"""Derive a Core run by filtering one or more Matrix runs."""

import argparse
import json
from pathlib import Path

from p95bench.packs import pack_config, snapshot, validate_snapshot
from p95bench.runner import score_run
from p95bench.tasks import load_tasks

MATRIX_PACK = "p95-matrix"
CORE_PACK = "p95-core"
OUTPUT_CAP_KEYS = ("max_tokens", "max_completion_tokens", "maxOutputTokens")
CONSISTENT_METADATA = (
    "model", "provider", "harness_commit", "harness_dirty", "repair_rounds",
    "reasoning_mode",
)


def _output_cap(metadata):
    values = [metadata[key] for key in OUTPUT_CAP_KEYS
              if metadata.get(key) is not None]
    if len(set(values)) > 1:
        raise ValueError("Source metadata has conflicting output caps")
    if not values or not isinstance(values[0], int) or isinstance(values[0], bool):
        raise ValueError("Source metadata is missing a valid output cap")
    return values[0]


def _read_attempts(path):
    attempts = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            attempt = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid attempt JSON at {path}:{number}") from error
        if not isinstance(attempt, dict):
            raise TypeError(f"Expected an attempt object at {path}:{number}")
        attempts.append(attempt)
    return attempts


def _core_snapshot(core_pack_root):
    config = pack_config(CORE_PACK)
    tasks = load_tasks(core_pack_root / "tasks", config["weights"])
    budgets = json.loads((core_pack_root / "efficiency-budgets.json").read_text())
    return tasks, snapshot(CORE_PACK, tasks, repeats=1, budgets=budgets)


def derive_core_run(model_runs, out_dir, core_pack_root=None):
    root = Path(__file__).resolve().parents[1]
    core_pack_root = Path(core_pack_root or root / "packs/p95-core")
    model_runs = [Path(path).resolve() for path in model_runs]
    out_dir = Path(out_dir)
    if not model_runs:
        raise ValueError("At least one --model-run is required")
    if len(set(model_runs)) != len(model_runs):
        raise ValueError("Duplicate source run directory")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"Output directory is not empty: {out_dir}")

    core_tasks, core_manifest = _core_snapshot(core_pack_root)
    core_inventory = {item["id"]: item for item in core_manifest["tasks"]}
    core_budgets = core_manifest["efficiency_budgets"]
    metadata_by_run = []
    attempts_by_task = {}
    source_task_ids = set()
    common = None

    for run_dir in model_runs:
        manifest_path = run_dir / "pack.json"
        metadata_path = run_dir / "metadata.json"
        attempts_path = run_dir / "attempts.jsonl"
        manifest = json.loads(manifest_path.read_text())
        metadata = json.loads(metadata_path.read_text())
        validate_snapshot(manifest)
        if manifest.get("pack_id") != MATRIX_PACK:
            raise ValueError(f"Expected a {MATRIX_PACK} source: {run_dir}")
        if (metadata.get("pack_id") != MATRIX_PACK
                or metadata.get("pack_digest") != manifest["digest"]):
            raise ValueError(f"Source metadata and Matrix snapshot disagree: {run_dir}")
        if metadata.get("harness_dirty") is not False:
            raise ValueError(f"Source harness is not clean: {run_dir}")
        if any(not isinstance(metadata.get(key), str) or not metadata[key]
               for key in ("model", "provider", "harness_commit")):
            raise ValueError(f"Source metadata is missing model, provider, or harness commit: {run_dir}")
        if manifest.get("repeats") != 1:
            raise ValueError(f"Core derivation requires one source repeat: {run_dir}")

        cap = _output_cap(metadata)
        values = {key: metadata.get(key) for key in CONSISTENT_METADATA}
        values["output_cap"] = cap
        if common is None:
            common = values
        elif values != common:
            differing = [key for key in values if values[key] != common[key]]
            raise ValueError(f"Source run metadata differs: {', '.join(differing)}")

        matrix_inventory = {}
        for item in manifest.get("tasks", []):
            if item["id"] in matrix_inventory:
                raise ValueError(f"Duplicate task in source snapshot: {item['id']}")
            matrix_inventory[item["id"]] = item
        matrix_budgets = manifest.get("efficiency_budgets", {})
        if set(matrix_budgets) != set(matrix_inventory):
            raise ValueError(f"Source budget inventory mismatch: {run_dir}")
        for task_id in core_inventory.keys() & matrix_inventory.keys():
            if matrix_inventory[task_id]["task_digest"] != core_inventory[task_id]["task_digest"]:
                raise ValueError(f"Task digest mismatch for {task_id} in {run_dir}")
            if matrix_budgets[task_id] != core_budgets[task_id]:
                raise ValueError(f"Efficiency budget mismatch for {task_id} in {run_dir}")

        source_record = {
            "pack_id": MATRIX_PACK,
            "pack_digest": manifest["digest"],
            "run": run_dir.name,
            "harness_commit": metadata.get("harness_commit"),
        }
        metadata_by_run.append((metadata, source_record))
        for attempt in _read_attempts(attempts_path):
            task_id = attempt.get("task_id")
            matrix_task = matrix_inventory.get(task_id)
            if (matrix_task is None or attempt.get("pack_id") != MATRIX_PACK
                    or attempt.get("pack_digest") != manifest["digest"]
                    or attempt.get("category") != matrix_task["category"]):
                raise ValueError(f"Attempt does not match its Matrix snapshot: {task_id}")
            if (attempt.get("model") != values["model"]
                    or attempt.get("provider") != values["provider"]):
                raise ValueError(f"Attempt model/provider does not match metadata: {task_id}")
            if task_id not in core_inventory:
                continue
            if attempt.get("repeat") != 0:
                raise ValueError(f"Core source attempt must be the single repeat: {task_id}")
            grade = attempt.get("grade")
            if not isinstance(grade, dict) or grade.get("status") != "graded":
                raise ValueError(f"Core source attempt is not graded: {task_id}")
            if task_id in attempts_by_task:
                raise ValueError(f"Duplicate Core task attempt: {task_id}")
            derived_attempt = dict(attempt)
            derived_attempt.update(
                pack_id=CORE_PACK,
                pack_digest=core_manifest["digest"],
                derived_from=source_record,
            )
            attempts_by_task[task_id] = derived_attempt
            source_task_ids.add(task_id)

    missing = set(core_inventory) - source_task_ids
    if missing:
        raise ValueError(f"Missing Core task attempts: {', '.join(sorted(missing))}")

    out_dir.mkdir(parents=True, exist_ok=True)
    attempts = [attempts_by_task[task_id] for task_id in sorted(core_inventory)]
    metadata = dict(metadata_by_run[0][0])
    metadata.update(
        pack_id=CORE_PACK,
        pack_digest=core_manifest["digest"],
        derived_from=[record for _, record in metadata_by_run],
    )
    if "task_scenarios" in metadata:
        metadata["task_scenarios"] = {
            task.id: task.scenario for task in core_tasks if task.scenario is not None
        }
    (out_dir / "pack.json").write_text(json.dumps(core_manifest, indent=2) + "\n")
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (out_dir / "attempts.jsonl").write_text(
        "".join(json.dumps(attempt) + "\n" for attempt in attempts)
    )
    return score_run(out_dir)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-run", type=Path, action="append", required=True)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(derive_core_run(args.model_run, args.out_dir), indent=2))


if __name__ == "__main__":
    main()
