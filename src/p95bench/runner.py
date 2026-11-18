"""Benchmark orchestration and reproducible scoring from saved attempts."""

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from p95bench.graders import GRADERS, grade, jmeter_executable, k6_executable
from p95bench.providers import ProviderUnavailable, SpendLimitExceeded, gemini_prices
from p95bench.tasks import CATEGORIES

SYSTEM_PROMPT = (
    "You are a performance test engineer. Solve the task exactly as specified. "
    "For numeric questions return only the number, for multiple choice only the letter. "
    "For JMeter tasks return a complete runnable JMX in a single XML code block."
)


def summarize(attempts):
    categories = defaultdict(list)
    for attempt in attempts:
        categories[attempt["category"]].append(float(attempt["grade"]["passed"]))
    scores = {key: 100 * sum(values) / len(values) for key, values in categories.items()}
    weight = sum(CATEGORIES[key] for key in scores)
    return {
        "overall_score": sum(scores[k] * CATEGORIES[k] for k in scores) / weight if weight else 0,
        "category_scores": scores,
        "attempts": len(attempts),
        "passed": sum(a["grade"]["passed"] for a in attempts),
        "estimated_cost_usd": sum(a["cost_usd"] for a in attempts),
        "input_tokens": sum(a["response"]["input_tokens"] for a in attempts),
        "output_tokens": sum(a["response"]["output_tokens"] for a in attempts),
        "latency_seconds": sum(a["response"]["latency_seconds"] for a in attempts),
    }


def score_run(directory: Path):
    attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()
                if line.strip()]
    summary = summarize(attempts)
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def run(provider, provider_name, model, price, tasks, out: Path, repeats=1):
    if repeats < 1:
        raise ValueError("repeats must be positive")
    for task in tasks:
        if task.grader not in GRADERS:
            raise ValueError(f"Unknown grader: {task.grader}")
    if any(task.grader == "jmeter_exec" for task in tasks):
        jmeter_executable()
    if any(task.grader == "k6_exec" for task in tasks):
        k6_executable()
    started = datetime.now(UTC)
    if provider_name == "gemini":
        price = gemini_prices(started)[model]
        configure = getattr(provider, "configure_run", None)
        if configure:
            configure(started)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = (out / run_id).resolve()
    directory.mkdir(parents=True)
    config = getattr(provider, "config", None)
    if config and config.cost_note:
        metadata = {"run_date": started.isoformat(),
                    "provider": provider_name, "model": model, "base_url": config.base_url,
                    "cost_note": config.cost_note, "pricing_source": config.pricing_source,
                    "temperature": config.temperature, **config.extra}
        if provider_name == "gemini":
            metadata.update(pricing_date_utc=started.date().isoformat(),
                            price_per_million=asdict(price))
        (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    budget_error = None
    with (directory / "attempts.jsonl").open("w") as stream:
        for index, task in enumerate(tasks):
            for repeat in range(repeats):
                messages = [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": task.user_prompt()}]
                try:
                    if budget_error is not None:
                        raise SpendLimitExceeded(budget_error)
                    response = provider.send(model, messages)
                except (ProviderUnavailable, SpendLimitExceeded) as error:
                    if isinstance(error, SpendLimitExceeded):
                        budget_error = str(error)
                    with (directory / "not-run.jsonl").open("a") as skipped:
                        skipped.write(json.dumps({"task_id": task.id, "repeat": repeat,
                                                  "provider": provider_name, "model": model,
                                                  "status": "not_run", "error": str(error)}) + "\n")
                    continue
                result = grade(task, response.text, directory / f"task-{index}-{repeat}")
                attempt = {"task_id": task.id, "category": task.category, "repeat": repeat,
                           "provider": provider_name, "model": model,
                           "messages": messages, "grader": task.grader, "params": task.params,
                           "price_per_million": asdict(price), "response": asdict(response),
                           "grade": result.to_dict(), "cost_usd": price.cost(response)}
                stream.write(json.dumps(attempt) + "\n")
                stream.flush()
    return directory, score_run(directory)
