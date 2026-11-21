"""Re-grade saved Core analysis answers with the current analysis grader.

Replays the protocol: if the re-graded first answer is no longer an invalid
answer, the repair round would not have been triggered, so the saved repair is
kept only as a superseded record and its spend is left out of the run cost.
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from p95bench.packs import pack_config
from p95bench.runner import _grade_response, _repair_trigger, score_run
from p95bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]


def regrade(run_dir, commit):
    run_dir = Path(run_dir)
    tasks = {t.id: t for t in load_tasks(ROOT / "packs/p95-core/tasks",
                                         pack_config("p95-core")["weights"])}
    lines = (run_dir / "attempts.jsonl").read_text().splitlines()
    attempts = [json.loads(line) for line in lines if line.strip()]
    changed, superseded_cost = [], 0.0
    with tempfile.TemporaryDirectory() as tmp:
        for index, attempt in enumerate(attempts):
            task = tasks[attempt["task_id"]]
            if task.grader != "v2_analysis":
                continue
            old_grade = attempt["grade"]
            old_repair = attempt.get("repair") or {"triggered": False}
            grade, error = _grade_response(task, attempt["response"]["text"],
                                           Path(tmp) / f"{index}", True)
            if error is not None:
                raise RuntimeError(f"Grader error on {task.id}: {error}")
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
    (run_dir / "attempts.jsonl").write_text("".join(json.dumps(a) + "\n" for a in attempts))
    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["regraded"] = {
        "harness_commit": commit,
        "grader": "v2_analysis",
        "reason": "Answer-list length is no longer part of the analysis answer schema.",
        "tasks": changed,
        "superseded_repair_cost_usd": superseded_cost,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    summary = score_run(run_dir)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
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
