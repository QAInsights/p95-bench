"""Plan GitHub Actions benchmark shards and render a completed run summary."""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path

from p95bench.packs import pack_config
from p95bench.providers import PROVIDERS
from p95bench.tasks import load_tasks

TOOLS = ("jmeter", "k6", "locust", "gatling")
ROOT = Path(__file__).resolve().parents[1]


def _scenario_codes(value):
    requested = [code.strip() for code in value.replace("\n", ",").split(",") if code.strip()]
    if not requested:
        return
    tasks = load_tasks(ROOT / "packs/p95-core/tasks", pack_config("p95-core")["weights"])
    available = {
        task.scenario.split("-", 2)[1]
        for task in tasks
        if task.scenario and len(task.scenario.split("-", 2)) > 1
    }
    missing = sorted(set(requested) - available)
    if missing:
        raise ValueError(f"Unknown Core scenario code(s): {', '.join(missing)}")


def _task_ids(value):
    requested = [task_id.strip() for task_id in value.replace("\n", ",").split(",")
                 if task_id.strip()]
    if not requested:
        return None
    tasks = load_tasks(ROOT / "packs/p95-core/tasks", pack_config("p95-core")["weights"])
    available = {task.id for task in tasks}
    missing = sorted(set(requested) - available)
    if missing:
        raise ValueError(f"Unknown Core task id(s): {', '.join(missing)}")
    return set(requested)


def _models(value):
    entries = []
    seen_entries = set()
    seen_slugs = set()
    for line in value.replace("\r", "\n").splitlines():
        for entry_text in line.split("#", 1)[0].split(","):
            entry_text = entry_text.strip()
            if not entry_text:
                continue
            fields = entry_text.split()
            if len(fields) != 2:
                raise ValueError('Each MODELS entry must be "provider model"')
            provider, model = fields
            if provider not in PROVIDERS:
                raise ValueError(f"Unknown provider: {provider}")
            config = PROVIDERS[provider]
            if model not in config.prices:
                raise ValueError(f"Unknown model for {provider}: {model}")
            entry = (provider, model)
            if entry in seen_entries:
                raise ValueError(f"Duplicate model entry: {provider} {model}")
            seen_entries.add(entry)
            slug = re.sub(r"[^A-Za-z0-9._-]", "-", model)
            if slug in seen_slugs:
                raise ValueError(f"Model slug collision: {slug}")
            seen_slugs.add(slug)
            entries.append({
                "provider": provider,
                "model": model,
                "slug": slug,
                "key_env": config.key_env,
            })
    if not entries:
        raise ValueError("MODELS must contain at least one provider/model entry")
    return entries


def _shard_cap(value):
    if not value.strip():
        return ""
    try:
        cap = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("CAP_USD_PER_MODEL must be a finite decimal greater than zero") from error
    if not cap.is_finite() or cap <= 0:
        raise ValueError("CAP_USD_PER_MODEL must be a finite decimal greater than zero")
    if cap / 4 > 100:
        raise ValueError("CAP_USD_PER_MODEL divided by 4 must be at most $100")
    shard_cap = (cap / 4).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    if shard_cap <= 0:
        raise ValueError("CAP_USD_PER_MODEL divided by 4 rounds down to zero")
    return format(shard_cap, "f")


def build_plan(environment=None):
    environment = os.environ if environment is None else environment
    _scenario_codes(environment.get("SCENARIOS", ""))
    task_ids = _task_ids(environment.get("TASKS", ""))
    models = _models(environment.get("MODELS", ""))
    cap_usd = _shard_cap(environment.get("CAP_USD_PER_MODEL", ""))
    shard_tasks = {}
    if task_ids is None:
        tools = TOOLS
    else:
        tasks = load_tasks(ROOT / "packs/p95-core/tasks", pack_config("p95-core")["weights"])
        selected_tools = {task.tool for task in tasks if task.id in task_ids}
        tools = tuple(tool for tool in TOOLS if tool in selected_tools)
        shard_tasks = {
            tool: ",".join(sorted(task.id for task in tasks
                                  if task.id in task_ids and task.tool == tool))
            for tool in tools
        }
    shards = [
        {**model, "tool": tool, "cap_usd": cap_usd,
         **({"tasks": shard_tasks[tool]} if tool in shard_tasks else {})}
        for model in models
        for tool in tools
    ]
    return models, shards


def _tool_scores(run_dir):
    tasks = load_tasks(ROOT / "packs/p95-core/tasks", pack_config("p95-core")["weights"])
    task_tools = {task.id: task.tool for task in tasks}
    scores = defaultdict(lambda: [[], []])
    attempts_path = run_dir / "attempts.jsonl"
    for line in attempts_path.read_text().splitlines():
        if not line.strip():
            continue
        attempt = json.loads(line)
        tool = task_tools.get(attempt.get("task_id"))
        if tool is None:
            raise ValueError(f"Attempt task is not in the Core pack: {attempt.get('task_id')}")
        first_score = attempt["grade"]["score"]
        repair = attempt.get("repair") or {}
        repair_grade = repair.get("grade")
        after_score = (repair_grade["score"] if repair.get("triggered") is True
                       and isinstance(repair_grade, dict)
                       and repair_grade.get("status") == "graded" else first_score)
        scores[tool][0].append(first_score)
        scores[tool][1].append(after_score)
    return "; ".join(
        f"{tool} {100 * sum(scores[tool][0]) / len(scores[tool][0]):.1f} → "
        f"{100 * sum(scores[tool][1]) / len(scores[tool][1]):.1f}"
        for tool in TOOLS if scores[tool][0]
    )


def _summary_markdown(run_dir):
    run_dir = Path(run_dir)
    summary = json.loads((run_dir / "summary.json").read_text())
    metadata = json.loads((run_dir / "metadata.json").read_text())
    model = str(metadata.get("model") or run_dir.name).replace("|", "\\|")
    first = summary.get("overall_score")
    after = (summary.get("after_repair") or {}).get("overall_score")
    first_text = f"{first:.1f}" if isinstance(first, (int, float)) else "—"
    after_text = f"{after:.1f}" if isinstance(after, (int, float)) else "—"
    cost = summary.get("estimated_cost_usd")
    cost_text = f"${cost:.6f}" if isinstance(cost, (int, float)) else "—"
    token_text = " | ".join(
        f"{count:,}" if isinstance(count, int) else "—"
        for count in (summary.get(key) for key in (
            "input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens"))
    )
    per_tool = _tool_scores(run_dir)
    return "\n".join([
        ("| Model | Overall first try | Overall after repair | Per-tool score (first → after) |"
         " Cost | Input tokens | Cache reads | Cache writes | Output tokens |"),
        "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
        f"| {model} | {first_text} | {after_text} | {per_tool or '—'} | {cost_text} | {token_text} |",
    ])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.summary:
            print(_summary_markdown(args.summary))
        else:
            models, shards = build_plan()
            print("models=" + json.dumps(models, separators=(",", ":")))
            print("shards=" + json.dumps(shards, separators=(",", ":")))
    except (InvalidOperation, OSError, ValueError) as error:
        print(f"plan_benchmark.py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
