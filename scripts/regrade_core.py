"""Offline replay of every saved Core response, preserving historical repair selection."""

import argparse
import hashlib
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from p95bench.packs import pack_config, task_digest, validate_snapshot
from p95bench.runner import _grade_response
from p95bench.tasks import load_tasks
from scripts.regrade_core_analysis import _summary_for_attempts

ROOT = Path(__file__).resolve().parents[1]
USAGE_FIELDS = ("estimated_cost_usd", "input_tokens", "output_tokens", "latency_seconds",
                "cached_tokens", "cache_write_tokens")


def recorded_repair(attempt):
    repair = attempt.get("repair") or {}
    response = repair.get("response")
    return response is not None and (repair.get("grade") is not None
                                    or bool(response.get("text"))
                                    or bool(response.get("input_tokens"))
                                    or bool(response.get("output_tokens")))


def _grade_saved(job):
    task, attempt, directory, commit = job
    results = {}
    for phase in ("initial", "repair") if recorded_repair(attempt) else ("initial",):
        record = attempt if phase == "initial" else attempt["repair"]
        text = record["response"]["text"]
        key = hashlib.sha256(json.dumps(
            [commit, task_digest(task), text], sort_keys=True).encode()).hexdigest()
        workdir = directory / phase / key
        workdir.mkdir(parents=True, exist_ok=True)
        cache = workdir / "offline-grade.json"
        if cache.exists():
            result = json.loads(cache.read_text())
        else:
            grade, error = _grade_response(task, text, workdir / "execution", True)
            if error and error["type"] != "AuditOverflow":
                raise RuntimeError(f"Grader error on {task.id} {phase}: {error}")
            if grade.get("status") != "graded":
                raise RuntimeError(f"Ungraded response on {task.id} {phase}: {grade}")
            result = {"grade": grade, "grading_error": error}
            cache.write_text(json.dumps(result, indent=2) + "\n")
        results[phase] = result
    print(f"Graded {attempt['model']} {task.id} repeat={attempt['repeat']} "
          + " ".join(f"{phase}={result['grade']['score']}"
                     for phase, result in results.items()), flush=True)
    return results


def regrade(run_dir, commit, artifacts_dir, jobs=1):
    run_dir, artifacts_dir = Path(run_dir), Path(artifacts_dir)
    tasks = {task.id: task for task in load_tasks(ROOT / "packs/p95-core/tasks",
                                                pack_config("p95-core")["weights"])}
    manifest = json.loads((run_dir / "pack.json").read_text())
    validate_snapshot(manifest)
    if manifest["pack_id"] != "p95-core":
        raise ValueError("Expected a Core run")
    inventory = {item["id"]: item["task_digest"] for item in manifest["tasks"]}
    attempts = [json.loads(line) for line in (run_dir / "attempts.jsonl").read_text().splitlines()
                if line.strip()]
    metadata = json.loads((run_dir / "metadata.json").read_text())
    before = _summary_for_attempts(run_dir, attempts)
    work = []
    for attempt in attempts:
        task = tasks.get(attempt["task_id"])
        if task is None or task_digest(task) != inventory.get(task.id):
            raise RuntimeError(f"Task digest mismatch for {attempt['task_id']}")
        if attempt.get("pack_digest") != manifest["digest"]:
            raise RuntimeError("Attempt pack digest mismatch")
        work.append((task, attempt, artifacts_dir / task.id / str(attempt["repeat"]), commit))

    if jobs < 1:
        raise ValueError("jobs must be positive")
    if jobs == 1:
        results = list(map(_grade_saved, work))
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(_grade_saved, work))

    changed = []
    for attempt, result in zip(attempts, results, strict=True):
        changes = []
        for phase, replacement in result.items():
            record = attempt if phase == "initial" else attempt["repair"]
            old_score, new_score = record["grade"]["score"], replacement["grade"]["score"]
            if old_score != new_score:
                changes.append({"phase": phase, "before": old_score, "after": new_score})
            record["grade"] = replacement["grade"]
            if replacement["grading_error"] is not None:
                record["grading_error"] = replacement["grading_error"]
            else:
                record.pop("grading_error", None)
        if changes:
            changed.append({"task_id": attempt["task_id"], "repeat": attempt["repeat"],
                            "changes": changes})

    summary = _summary_for_attempts(run_dir, attempts)
    if any(summary.get(field) != before.get(field) for field in USAGE_FIELDS):
        raise RuntimeError("Offline regrade changed cost, token or latency totals")
    repairs = sum(recorded_repair(attempt) for attempt in attempts)
    note = {"harness_commit": commit, "grader": "all_core_saved_outputs",
            "reason": "Accept JMeter native numeric properties; replay all four tools offline.",
            "repair_policy": "Regrade recorded responses only; preserve historical repair "
                             "selection, feedback, skipped rounds, cost, tokens and latency.",
            "initial_attempts_regraded": len(attempts), "recorded_repairs_regraded": repairs,
            "repairs_not_attempted": len(attempts) - repairs, "score_changes": changed}
    history = metadata.get("regraded", [])
    if isinstance(history, dict):
        history = [history]
    metadata["regraded"] = [*history, note]
    for attempt in attempts:
        attempt.setdefault("offline_regrades", []).append({
            "harness_commit": commit, "initial_regraded": True,
            "recorded_repair_regraded": recorded_repair(attempt),
        })
    (run_dir / "attempts.jsonl").write_text(
        "".join(json.dumps(attempt) + "\n" for attempt in attempts))
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return note, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", nargs="+", type=Path)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args(argv)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.strip()
    for run_dir in args.run_dir:
        note, summary = regrade(run_dir, commit, args.artifacts_dir / run_dir.name, args.jobs)
        print(json.dumps({"run": str(run_dir), **note,
                          "overall_score": summary["overall_score"],
                          "after_repair": summary["after_repair"]["overall_score"]}, indent=2),
              flush=True)


if __name__ == "__main__":
    main()
