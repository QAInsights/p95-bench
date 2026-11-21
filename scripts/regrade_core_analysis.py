"""Re-grade saved Core analysis answers with the current analysis grader.

Replays the protocol: if the re-graded first answer is no longer an invalid
answer, the repair round would not have been triggered, so the saved repair is
kept only as a superseded record and its spend is left out of the run cost.
"""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

from p95bench.packs import pack_config, task_digest
from p95bench.runner import _grade_response, _repair_trigger, score_run
from p95bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
COST_NOTE_PATTERN = re.compile(
    r"USD list-price estimate from reported token usage at frozen registry prices for all "
    r"\d+ Core first tries and \d+ repair responses; Core usage cost \$\d+\.\d{6}\. "
    r"Not an invoice\."
)


def _summary_for_attempts(run_dir, attempts):
    with tempfile.TemporaryDirectory() as tmp:
        summary_dir = Path(tmp)
        for name in ("pack.json", "not-run.jsonl"):
            source = run_dir / name
            if source.exists():
                (summary_dir / name).write_bytes(source.read_bytes())
        (summary_dir / "attempts.jsonl").write_text(
            "".join(json.dumps(attempt) + "\n" for attempt in attempts))
        return score_run(summary_dir)


def regrade(run_dir, commit):
    run_dir = Path(run_dir)
    tasks = {t.id: t for t in load_tasks(ROOT / "packs/p95-core/tasks",
                                         pack_config("p95-core")["weights"])}
    manifest = json.loads((run_dir / "pack.json").read_text())
    inventory = {item["id"]: item.get("task_digest")
                 for item in manifest.get("tasks", [])}
    lines = (run_dir / "attempts.jsonl").read_text().splitlines()
    attempts = [json.loads(line) for line in lines if line.strip()]
    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    analysis_tasks = {}
    for attempt in attempts:
        task_id = attempt["task_id"]
        task = tasks.get(task_id)
        if task is None:
            raise RuntimeError(f"Task not found in Core pack: {task_id}")
        if task.grader != "v2_analysis":
            continue
        if task_id not in inventory:
            raise RuntimeError(f"Task missing from run pack manifest: {task_id}")
        if task_digest(task) != inventory[task_id]:
            raise RuntimeError(f"Task digest mismatch for {task_id}")
        analysis_tasks[task_id] = task

    repair_rounds = metadata.get("repair_rounds", 0)
    changed, superseded_cost = [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        for index, attempt in enumerate(attempts):
            task = analysis_tasks.get(attempt["task_id"])
            if task is None:
                continue
            old_grade = attempt["grade"]
            old_repair = attempt.get("repair") or {"triggered": False}
            grade, error = _grade_response(task, attempt["response"]["text"],
                                           Path(tmp) / f"{index}", True)
            if error is not None:
                raise RuntimeError(f"Grader error on {task.id}: {error}")
            trigger = None
            if (repair_rounds and old_repair.get("skipped") != "truncated_at_cap"):
                trigger = _repair_trigger(task, grade, Path(tmp) / f"{index}")
            repair = old_repair
            if old_repair.get("triggered") and trigger is None:
                repair = {"triggered": False, "superseded_repair": old_repair}
                superseded_cost += old_repair.get("cost_usd", 0)
            elif old_repair.get("triggered") and old_repair.get("response"):
                repair_grade, repair_error = _grade_response(
                    task, old_repair["response"]["text"], Path(tmp) / f"{index}-repair", True)
                if repair_error is not None:
                    raise RuntimeError(f"Grader error on {task.id} repair: {repair_error}")
                repair = {**old_repair, "grade": repair_grade}
            elif not old_repair.get("triggered") and trigger is not None:
                raise RuntimeError(f"{task.id}: re-grade would now trigger a repair")
            if grade != old_grade or repair != old_repair:
                changed.append(task.id)
                attempt["grade"] = grade
                attempt["repair"] = repair
                attempt["regraded"] = {"harness_commit": commit,
                                       "previous_score": old_grade["score"]}

    if not changed:
        summary = _summary_for_attempts(run_dir, attempts)
        summary_path = run_dir / "summary.json"
        try:
            current_summary = json.loads(summary_path.read_text())
        except FileNotFoundError:
            current_summary = None
        except json.JSONDecodeError:
            current_summary = None
        if current_summary != summary:
            summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        return changed, superseded_cost, summary

    cost_note = metadata.get("cost_note")
    rewrite_cost_note = False
    if isinstance(cost_note, str):
        if COST_NOTE_PATTERN.fullmatch(cost_note):
            rewrite_cost_note = True
        elif "$" in cost_note:
            raise RuntimeError("Existing cost_note contains an embedded total")
    summary = _summary_for_attempts(run_dir, attempts)
    repairs = sum(
        bool((repair := attempt.get("repair") or {}).get("triggered"))
        and repair.get("response") is not None
        for attempt in attempts
    )
    if rewrite_cost_note:
        metadata["cost_note"] = (
            f"USD list-price estimate from reported token usage at frozen registry prices for all "
            f"{len(attempts)} Core first tries and {repairs} repair responses; Core usage cost "
            f"${summary['estimated_cost_usd']:.6f}. Not an invoice."
        )
    regraded = metadata.get("regraded", [])
    if isinstance(regraded, dict):
        regraded = [regraded]
    if not isinstance(regraded, list):
        raise TypeError("metadata regraded value must be an object or a list")
    metadata["regraded"] = [*regraded, {
        "harness_commit": commit,
        "grader": "v2_analysis",
        "reason": "Answer-list length is no longer part of the analysis answer schema.",
        "tasks": changed,
        "superseded_repair_cost_usd": superseded_cost,
    }]
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    (run_dir / "attempts.jsonl").write_text(
        "".join(json.dumps(attempt) + "\n" for attempt in attempts))
    return changed, superseded_cost, summary


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="+", type=Path)
    args = parser.parse_args(argv)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                            capture_output=True, text=True).stdout.strip()
    for run_dir in args.run_dir:
        changed, cost, summary = regrade(run_dir, commit)
        print(json.dumps({"run": str(run_dir), "changed": changed,
                          "superseded_repair_cost_usd": cost,
                          "overall_score": summary["overall_score"],
                          "after_repair": summary["after_repair"]["overall_score"],
                          "estimated_cost_usd": summary["estimated_cost_usd"]}, indent=2))


if __name__ == "__main__":
    main()
