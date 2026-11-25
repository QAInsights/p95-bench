"""Offline export of the completed Feather Wand GUI run, separate from model rankings."""

import argparse
import hashlib
import json
import math
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from p95bench.packs import pack_config, task_digest, validate_snapshot
from p95bench.runner import _grade_response
from p95bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = "Feather Wand v4.0.1-RC1 (JMeter GUI Agent mode)"
MODEL = "claude-sonnet-5-5"
PROVIDER = "anthropic"
EXCLUDED_SPEND = 3.6677725
COST_NOTE = None
PRICES = {"input": 2, "cached": 0.10, "cache_write": 2.50, "output": 10}
USAGE = {"input_tokens": "input_total", "fresh_input_tokens": "fresh_input",
         "cached_tokens": "cache_read", "cache_write_tokens": "cache_write",
         "output_tokens": "output", "reasoning_tokens": "reasoning"}


def read(path):
    return json.loads(path.read_text())


def public_text(text):
    if re.search(r"/home/|/Users/|[A-Za-z]:\\Users\\|sk-ant-[\w-]{20,}", text):
        raise ValueError("Refusing to publish a local path or credential")
    return text


def response_usage(rows):
    if not rows or any(row.get("status") != "ok" for row in rows):
        raise ValueError("Only successful, recorded model turns may be published")
    rows = [{**row, "input_total": row.get("input_total", row.get("fresh_input", 0)
             + row.get("cache_read", 0) + row.get("cache_write", 0))} for row in rows]
    result = {key: sum(row[field] for row in rows) for key, field in USAGE.items()}
    if any(not isinstance(value, int) or value < 0 for value in result.values()):
        raise ValueError("Invalid token usage")
    if result["input_tokens"] != sum(result[key] for key in (
            "fresh_input_tokens", "cached_tokens", "cache_write_tokens")):
        raise ValueError("Input token accounting does not reconcile")
    result["turns"] = len(rows)
    cost = sum(result[key] * PRICES[price] for key, price in (
        ("fresh_input_tokens", "input"), ("cached_tokens", "cached"),
        ("cache_write_tokens", "cache_write"), ("output_tokens", "output"))) / 1_000_000
    return result, cost


def grade_saved(job):
    task, folder, evidence, digest, commit = job
    result = read(folder / "result.json")
    if result["status"] not in {"completed", "reused"} or result["task"] != task.id:
        raise ValueError("Expected a valid completed task")
    reused = result["status"] == "reused"
    source = folder / "reused-c03" if reused else folder
    attempt = {"task_id": task.id, "scenario": task.scenario, "category": task.category,
               "repeat": 0, "tool": "jmeter", "provider": PROVIDER,
               "model": MODEL, "pack_id": "p95-core", "pack_digest": digest,
               "repair": {"triggered": False}}
    for phase in ("initial", "repair") if result.get("repair") else ("initial",):
        if reused:
            raw = [json.loads(line) for line in (source / "usage.jsonl").read_text().splitlines()]
            aliases = {"cached_tokens": "cache_read_input_tokens",
                       "cache_write_tokens": "cache_write_input_tokens"}
            rows = [{**row, **{field: row[aliases.get(key, key)] for key, field in USAGE.items()}}
                    for row in raw]
        else:
            rows = read(source / f"{phase}-usage.json")
        usage, cost = response_usage(rows)
        filename = f"{phase}-answer.txt" if task.grader == "v2_analysis" else f"{phase}-model.jmx"
        text = public_text((source / filename).read_text())
        key = hashlib.sha256(json.dumps([commit, task_digest(task), text]).encode()).hexdigest()
        workdir = evidence / task.id / phase / key
        cache = workdir / "grade.json"
        if cache.exists():
            grade = read(cache)
        else:
            grade, error = _grade_response(task, text, workdir, True)
            if error or grade.get("status") != "graded":
                raise RuntimeError(f"Offline grading failed for {task.id} {phase}")
            workdir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(grade) + "\n")
        record = {"response": {"text": text, **usage,
                               "source_sha256": hashlib.sha256(text.encode()).hexdigest()},
                  "grade": grade, "cost_usd": cost, "price_per_million": PRICES}
        if phase == "initial":
            attempt.update(record)
        else:
            trigger = read(source / "initial-repair-trigger.json")
            if not trigger:
                raise ValueError("Recorded repair has no historical Core trigger")
            expected = ("Your previous answer failed before it could be scored:\n\n" + trigger[1]
                        + "\n\nReturn the complete corrected answer under the same output contract.")
            if (source / "repair-prompt.txt").read_text() != expected:
                raise ValueError("GUI repair feedback differs from the Core trigger")
            attempt["repair"] = {"triggered": True, "trigger": trigger[0],
                                 "feedback": public_text(expected), **record}
    records = [attempt, attempt["repair"]] if result.get("repair") else [attempt]
    if (sum(record["response"]["turns"] for record in records) != result["turns"]
            or not math.isclose(sum(record["cost_usd"] for record in records),
                                result["fw_cost_usd"], abs_tol=1e-9)):
        raise ValueError("Published usage differs from the recorded task totals")
    print(f"{task.id}: {attempt['grade']['score'] * 100:.4f} -> "
          f"{(attempt['repair'].get('grade') or attempt['grade'])['score'] * 100:.4f}", flush=True)
    return attempt


def export(run_dir, output_dir, evidence_dir, jobs=1):
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                            check=True, capture_output=True).stdout.strip()
    tasks = [task for task in load_tasks(ROOT / "packs/p95-core/tasks",
                                       pack_config("p95-core")["weights"]) if task.tool == "jmeter"]
    manifest = read(ROOT / "results/p95-core/claude-sonnet-5-5/pack.json")
    validate_snapshot(manifest)
    inventory = {item["id"]: item["task_digest"] for item in manifest["tasks"]}
    if len(tasks) != 26 or any(task_digest(task) != inventory.get(task.id) for task in tasks):
        raise ValueError("Core task inventory differs from the recorded manifest")
    work = [(task, Path(run_dir) / task.id, Path(evidence_dir), manifest["digest"], commit)
            for task in tasks]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        attempts = list(pool.map(grade_saved, work))
    records = [record for attempt in attempts for record in (
        [attempt, attempt["repair"]] if attempt["repair"]["triggered"] else [attempt])]
    totals = {key: sum(record["response"][key] for record in records) for key in (*USAGE, "turns")}
    total_cost = sum(record["cost_usd"] for record in records)
    per_task = [{"task_id": a["task_id"], "category": a["category"], "scenario": a["scenario"],
                 "tool": "jmeter", "repeat": 0, "score": a["grade"]["score"],
                 "after_repair_score": (a["repair"].get("grade") or a["grade"])["score"]}
                for a in attempts]
    scores = {"first_attempt": 100 * sum(a["score"] for a in per_task) / len(per_task),
              "after_repair": 100 * sum(a["after_repair_score"] for a in per_task) / len(per_task)}
    metadata = {"entry_type": "agent_system", "system": SYSTEM, "model": MODEL,
                "provider": PROVIDER, "reasoning_mode": "thinking-high", "tools": ["jmeter"],
                "pack_id": "p95-core", "pack_digest": manifest["digest"], "visibility": "public",
                "harness_commit": commit, "grader_commit": commit, "rank_eligible": False,
                "original_grader_commit": (read(Path(run_dir) / "summary.json")["main_grader"]
                                 if (Path(run_dir) / "summary.json").exists() else commit),
                "initial_attempts_regraded": len(attempts),
                "recorded_repairs_regraded": sum(a["repair"]["triggered"] for a in attempts),
                "execution": "JMeter 5.6.3 GUI only; fresh plan and conversation per task.",
                "repair_policy": "One GUI repair only when the historical Core trigger fired; "
                                 "exact feedback, saved responses only, no new repairs during regrade. "
                                 "Retain recorded repairs even if the merged grader accepts the initial.",
                "analysis_extraction": "Verbatim final assistant text from the native conversation.",
                "pricing_basis": {"currency": "USD", "unit": "million tokens",
                                  "cache_write_ttl": "5 minutes", **PRICES},
                "usage": totals, "excluded_spend_usd": EXCLUDED_SPEND,
                "cost_note": COST_NOTE or ("List-price estimate from native per-turn usage. Excludes $3.67 "
                             "extra spend on discarded starts and credit-failed partial attempts. "
                             "Unreported failed-turn usage is unpriced. Not an invoice.")}
    summary = {"pack_id": "p95-core", "pack_digest": manifest["digest"], "attempts": len(attempts),
               "expected_attempts": len(tasks), "complete": False, "complete_tool_coverage": True,
               "tools": ["jmeter"], "rank_eligible": False, "per_task_results": per_task,
               "tool_scores": {"jmeter": scores}, "estimated_cost_usd": total_cost, **totals}
    payloads = {}
    for name, value in (("metadata", metadata), ("summary", summary), ("pack", manifest)):
        payloads[f"{name}.json"] = public_text(json.dumps(value, indent=2)) + "\n"
    payloads["attempts.jsonl"] = public_text(
        "".join(json.dumps(attempt) + "\n" for attempt in attempts))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in payloads.items():
        (output_dir / name).write_text(text)
    print(json.dumps({"scores": scores, "cost_usd": total_cost, **totals}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--system", default=SYSTEM)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--provider", default=PROVIDER)
    parser.add_argument("--input-price", type=float, default=PRICES["input"])
    parser.add_argument("--cached-price", type=float, default=PRICES["cached"])
    parser.add_argument("--cache-write-price", type=float, default=PRICES["cache_write"])
    parser.add_argument("--output-price", type=float, default=PRICES["output"])
    parser.add_argument("--excluded-spend", type=float, default=EXCLUDED_SPEND)
    parser.add_argument("--cost-note", default=COST_NOTE)
    args = parser.parse_args()
    globals().update(SYSTEM=args.system, MODEL=args.model, PROVIDER=args.provider,
                     EXCLUDED_SPEND=args.excluded_spend, COST_NOTE=args.cost_note,
                     PRICES={"input": args.input_price, "cached": args.cached_price,
                             "cache_write": args.cache_write_price, "output": args.output_price})
    export(args.run_dir, args.output_dir, args.evidence_dir, args.jobs)


if __name__ == "__main__":
    main()
