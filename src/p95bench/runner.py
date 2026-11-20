"""Benchmark orchestration and reproducible scoring from saved attempts."""

import json
import subprocess
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from p95bench.budget import BudgetLedger
from p95bench.capacity import prepare_workloads
from p95bench.checkpoint_graders import award
from p95bench.execution import AuditOverflow
from p95bench.graders import (
    CHECKPOINT_GRADERS,
    GRADERS,
    Grade,
    grade,
    jmeter_executable,
    k6_executable,
)
from p95bench.grades import Checkpoint, CheckpointGrade
from p95bench.packs import snapshot, summarize_v2, validate_snapshot
from p95bench.providers import Gemini, ProviderUnavailable, SpendLimitExceeded, gemini_prices
from p95bench.tasks import CATEGORIES
from p95bench.worker import WorkerUnavailable

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


def harness_revision(root=None):
    root = (root or Path(__file__).resolve().parents[2]).resolve()
    unknown = {"harness_commit": None, "harness_dirty": None}
    try:
        toplevel = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        if Path(toplevel).resolve() != root:
            return unknown
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                                     "--untracked-files=no"], capture_output=True, text=True,
                                    check=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return unknown
    return {"harness_commit": commit, "harness_dirty": dirty}


def score_run(directory: Path):
    attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()
                if line.strip()]
    manifest_path = directory / "pack.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if manifest:
        validate_snapshot(manifest)
    if any(a.get("pack_id", "p95-v1") != (manifest["pack_id"] if manifest else "p95-v1")
           for a in attempts):
        raise ValueError("Mixed pack input or missing pack snapshot")
    summary = (summarize_v2(attempts, manifest)
               if manifest and manifest["grade_schema_version"] == 2 else summarize(attempts))
    skipped = directory / "not-run.jsonl"
    not_run = [json.loads(line) for line in skipped.read_text().splitlines()
               if line.strip()] if skipped.exists() else []
    if not_run or manifest and manifest["grade_schema_version"] == 2:
        summary["not_run"] = len(not_run)
    for entry in not_run:
        summary["estimated_cost_usd"] += entry.get("cost_usd", 0)
        for key in ("input_tokens", "output_tokens", "latency_seconds"):
            summary[key] += entry.get("response", {}).get(key, 0)
        if entry.get("grading_error") and "stop_reason" not in summary:
            summary["stop_reason"] = entry.get("error", entry["grading_error"]["message"])
            fallback = entry["grading_error"].get("fallback_error")
            if fallback:
                summary["stop_reason"] += f"; fallback error ({fallback['type']}): {fallback['message']}"
    (directory / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    return summary


def _grade_response(task, text, workdir, v2):
    try:
        result = grade(task, text, workdir, 2) if v2 else grade(task, text, workdir)
        return result.to_dict(), None
    except Exception as error:  # noqa: BLE001
        detail = f"Grader error ({type(error).__name__}): {error}"
        metadata = {"type": type(error).__name__, "message": str(error)}
        if (isinstance(error, (WorkerUnavailable, OSError, subprocess.SubprocessError))
                and not isinstance(error, AuditOverflow)):
            return {"status": "not_run", "detail": detail}, metadata
        if not isinstance(error, AuditOverflow):
            metadata["stop_run"] = True
        try:
            result = (CheckpointGrade(award(task.params.get("rubric", [
                {"id": "grading_error", "points": 100}]), {}), detail)
                if v2 else Grade(False, detail))
        except Exception as fallback_error:  # noqa: BLE001
            result = CheckpointGrade((Checkpoint("grading_error", 0, 100),), detail)
            metadata.update(stop_run=True, fallback_error={
                "type": type(fallback_error).__name__, "message": str(fallback_error)})
        return result.to_dict(), metadata


def run(provider, provider_name, model, price, tasks, out: Path, repeats=1, *,
        pack_id="p95-v1", efficiency_budgets=None, budget_ledger=None):
    manifest = snapshot(pack_id, tasks, repeats, efficiency_budgets)
    v2 = manifest["grade_schema_version"] == 2
    registry = CHECKPOINT_GRADERS if v2 else GRADERS
    for task in tasks:
        if task.grader not in registry:
            raise ValueError(f"Unknown grader: {task.grader}")
    if any(task.grader == "jmeter_exec" for task in tasks):
        jmeter_executable()
    if any(task.grader == "k6_exec" for task in tasks):
        k6_executable()
    started = datetime.now(UTC)
    if provider_name == "gemini":
        if price is None:
            price = gemini_prices(started).get(model)
        if price is None:
            raise ValueError(f"No verified price for {model}; supply an explicit price")
        if isinstance(provider, Gemini):
            provider.configure_run(started, model=model, price=price)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    directory = ((out / pack_id if v2 else out) / run_id).resolve()
    directory.mkdir(parents=True)
    if v2:
        prepare_workloads(tasks, directory / "capacity")
    (directory / "pack.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    config = getattr(provider, "config", None)
    ledger = (budget_ledger or BudgetLedger()) if v2 else None
    provider.measure_transport = v2
    if v2 or (config and config.cost_note):
        metadata = {"run_date": started.isoformat(), "pack_id": pack_id,
                    "pack_digest": manifest["digest"], "visibility": manifest["visibility"],
                    "provider": provider_name, "model": model,
                    "base_url": getattr(config, "base_url", None),
                    "cost_note": getattr(config, "cost_note", None),
                    "pricing_source": getattr(config, "pricing_source", None),
                    "temperature": getattr(config, "temperature", None),
                    **(config.extra if config else {})}
        task_scenarios = {task.id: task.scenario for task in tasks if task.scenario is not None}
        if task_scenarios:
            metadata["task_scenarios"] = dict(sorted(task_scenarios.items()))
        if provider_name == "gemini":
            metadata.update(pricing_date_utc=started.date().isoformat(),
                            price_per_million=asdict(price))
        if v2:
            metadata["spend_cap_usd"] = ledger.limit / 1_000_000
            metadata.update(harness_revision())
        (directory / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n")
    budget_error = None
    grading_stop = None
    with (directory / "attempts.jsonl").open("w") as stream:
        for index, task in enumerate(tasks):
            for repeat in range(repeats):
                reservation = None
                system = ("You are a performance test engineer. Follow the task's output contract."
                          if v2 else SYSTEM_PROMPT)
                messages = [{"role": "system", "content": system},
                            {"role": "user", "content": task.user_prompt()}]
                try:
                    if grading_stop is not None:
                        raise ProviderUnavailable(grading_stop)
                    if budget_error is not None:
                        raise SpendLimitExceeded(budget_error)
                    if ledger:
                        extra = config.extra if config else {}
                        output_cap = next((extra[k] for k in
                                           ("max_tokens", "max_completion_tokens", "maxOutputTokens")
                                           if k in extra), 16384)
                        maximum = ((len(json.dumps(messages).encode()) + 4096) * price.input
                                   + output_cap * price.output) / 1_000_000
                        reservation = ledger.reserve(maximum * (1 + (config.max_retries
                                                                     if config else 0)))
                        provider.transport_attempts = 0
                        provider.response_latency_seconds = None
                    response = provider.send(model, messages)
                    if ledger:
                        requests = getattr(provider, "transport_attempts", 0) or (
                            1 + (config.max_retries if config else 0))
                        ledger.settle(reservation, price.cost(response) + maximum * (requests - 1))
                except (ProviderUnavailable, SpendLimitExceeded) as error:
                    if isinstance(error, SpendLimitExceeded):
                        budget_error = str(error)
                    with (directory / "not-run.jsonl").open("a") as skipped:
                        entry = {"task_id": task.id, "repeat": repeat,
                                 "provider": provider_name, "model": model,
                                 "status": "not_run", "error": str(error)}
                        if task.scenario is not None:
                            entry["scenario"] = task.scenario
                        if v2:
                            entry.update(pack_id=pack_id, budget_reservation=reservation)
                        skipped.write(json.dumps(entry) + "\n")
                    continue
                workdir = directory / f"task-{index}-{repeat}"
                result, grading_error = _grade_response(task, response.text, workdir, v2)
                attempt = {"task_id": task.id, "category": task.category, "repeat": repeat,
                           "provider": provider_name, "model": model,
                           "messages": messages, "grader": task.grader, "params": task.params,
                           "price_per_million": asdict(price), "response": asdict(response),
                           "grade": result, "cost_usd": price.cost(response)}
                if task.scenario is not None:
                    attempt["scenario"] = task.scenario
                if grading_error is not None:
                    attempt["grading_error"] = grading_error
                if v2:
                    attempt.update(pack_id=pack_id, pack_digest=manifest["digest"],
                                   budget_reservation=reservation)
                    latency = getattr(provider, "response_latency_seconds", None)
                    attempt["response"].update(
                        response_latency_seconds=(response.latency_seconds if latency is None
                                                  else latency),
                        transport_attempts=requests,
                        backoff_seconds=getattr(provider, "backoff_seconds", 0))
                if result.get("status") == "not_run" or grading_error and grading_error.get("stop_run"):
                    attempt.update(status="not_run", error=result["detail"])
                    with (directory / "not-run.jsonl").open("a") as skipped:
                        skipped.write(json.dumps(attempt) + "\n")
                    grading_stop = "Grading stopped: " + result["detail"]
                    continue
                stream.write(json.dumps(attempt) + "\n")
                stream.flush()
    return directory, score_run(directory)
