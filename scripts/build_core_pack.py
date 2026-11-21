"""Generate the locked Core subset of the Matrix pack."""

import argparse
import json
from pathlib import Path

import yaml

CORE_SCENARIOS = (
    "r01", "r02", "r04", "r05", "r06", "r08", "r10", "r11", "r12",
    "c01", "c03", "c04", "c06", "c08", "c09",
    "m01", "m04", "m05", "m06",
    "w01", "w02", "w07",
    "a01", "a04", "a05", "a06",
)
TOOLS = ("jmeter", "k6", "locust", "gatling")


def _scenario_code(task):
    scenario = task.get("scenario")
    if not isinstance(scenario, str):
        raise TypeError(f"Missing scenario in Core source task: {task.get('id')}")
    parts = scenario.split("-", 2)
    if len(parts) != 3 or parts[0] != "mx":
        raise ValueError(f"Unexpected Matrix scenario: {scenario}")
    return parts[1]


def _add_file(outputs, target_name, source):
    source = Path(source)
    if not source.is_file():
        raise ValueError(f"Missing Core source file: {source}")
    target_name = Path(target_name)
    content = source.read_bytes()
    if target_name in outputs and outputs[target_name] != content:
        raise ValueError(f"Conflicting Core source file: {target_name}")
    outputs[target_name] = content


def _write_outputs(target_pack, outputs):
    target_pack.mkdir(parents=True, exist_ok=True)
    for root_name in ("tasks", "overlays"):
        root = target_pack / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*"), reverse=True):
            relative = path.relative_to(target_pack)
            if path.is_file() or path.is_symlink():
                if relative not in outputs:
                    path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    for relative, content in sorted(outputs.items()):
        target = target_pack / relative
        if not target.resolve().is_relative_to(target_pack.resolve()):
            raise ValueError(f"Core output escapes pack directory: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != content:
            target.write_bytes(content)


def build_core_pack(source_pack, target_pack):
    source_pack, target_pack = Path(source_pack), Path(target_pack)
    source_tasks = source_pack / "tasks"
    if source_tasks.resolve() == (target_pack / "tasks").resolve():
        raise ValueError("Core output must not overwrite the Matrix tasks")

    selected = []
    for task_file in sorted(source_tasks.rglob("*.yaml")):
        task = yaml.safe_load(task_file.read_text())
        if _scenario_code(task) in CORE_SCENARIOS:
            selected.append((task_file, task))

    ids = [task["id"] for _, task in selected]
    if len(ids) != 104 or len(set(ids)) != 104:
        raise ValueError(f"Expected 104 unique Core tasks, found {len(ids)}")
    scenarios = {_scenario_code(task) for _, task in selected}
    if scenarios != set(CORE_SCENARIOS):
        raise ValueError(f"Core scenario mismatch: {sorted(scenarios ^ set(CORE_SCENARIOS))}")
    for code in CORE_SCENARIOS:
        tasks = [task for _, task in selected if _scenario_code(task) == code]
        if {task["tool"] for task in tasks} != set(TOOLS) or len(tasks) != len(TOOLS):
            raise ValueError(f"Scenario {code} must have exactly one task for each tool")

    budgets = json.loads((source_pack / "efficiency-budgets.json").read_text())
    if not set(ids) <= set(budgets):
        raise ValueError("Matrix efficiency budgets are missing Core tasks")
    core_budgets = {task_id: budgets[task_id] for task_id in sorted(ids)}
    outputs = {
        Path("efficiency-budgets.json"): (
            json.dumps(core_budgets, indent=2, sort_keys=True) + "\n"
        ).encode(),
    }

    for task_file, task in selected:
        task_relative = task_file.relative_to(source_pack)
        _add_file(outputs, task_relative, task_file)
        task_dir = task_file.parent.resolve()
        for fixture in task.get("fixtures", []):
            fixture_path = (task_dir / fixture).resolve()
            if not fixture_path.is_relative_to(task_dir):
                raise ValueError(f"Fixture escapes Matrix task directory: {fixture}")
            fixture_relative = task_relative.parent / fixture
            _add_file(outputs, fixture_relative, fixture_path)

            overlay = source_pack / "overlays" / task["tool"] / fixture
            if overlay.is_file():
                _add_file(outputs, Path("overlays") / task["tool"] / fixture, overlay)

        for name in task.get("params", {}).get("native_files", {}).values():
            overlay = source_pack / "overlays" / task["tool"] / name
            if overlay.is_file():
                _add_file(outputs, Path("overlays") / task["tool"] / name, overlay)

    _write_outputs(target_pack, outputs)
    return {task["id"] for _, task in selected}


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=root / "packs/p95-matrix")
    parser.add_argument("--output", type=Path, default=root / "packs/p95-core")
    args = parser.parse_args(argv)
    build_core_pack(args.source, args.output)


if __name__ == "__main__":
    main()
