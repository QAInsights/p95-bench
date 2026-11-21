import json
from pathlib import Path

import pytest
import yaml

from p95bench.grades import Checkpoint, CheckpointGrade
from p95bench.packs import digest, pack_config, snapshot
from p95bench.tasks import load_tasks
from scripts.build_core_pack import CORE_SCENARIOS, build_core_pack
from scripts.derive_core_run import derive_core_run

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PACK = ROOT / "packs/p95-matrix"
CORE_PACK = ROOT / "packs/p95-core"
TOOLS = {"jmeter", "k6", "locust", "gatling"}


def _task_files(pack):
    result = {}
    for path in (pack / "tasks").rglob("*.yaml"):
        result[yaml.safe_load(path.read_text())["id"]] = path
    return result


def _files(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*")
            if path.is_file()}


def test_core_pack_generation_is_deterministic_and_matches_committed_pack(tmp_path):
    generated = tmp_path / "p95-core"
    build_core_pack(MATRIX_PACK, generated)
    first = _files(generated)
    build_core_pack(MATRIX_PACK, generated)

    assert _files(generated) == first
    assert first == _files(CORE_PACK)


def test_core_is_the_byte_identical_26_by_4_matrix_subset():
    matrix_files = _task_files(MATRIX_PACK)
    core_files = _task_files(CORE_PACK)
    matrix_tasks = load_tasks(MATRIX_PACK / "tasks", pack_config("p95-matrix")["weights"])
    core_tasks = load_tasks(CORE_PACK / "tasks", pack_config("p95-core")["weights"])
    matrix_by_id = {task.id: task for task in matrix_tasks}
    core_by_id = {task.id: task for task in core_tasks}
    expected_ids = {
        task.id for task in matrix_tasks
        if task.scenario.split("-", 2)[1] in CORE_SCENARIOS
    }

    assert len(CORE_SCENARIOS) == 26
    assert len(core_tasks) == 104
    assert pack_config("p95-core") == pack_config("p95-matrix")
    assert set(core_by_id) == expected_ids
    assert set(core_files) == expected_ids
    assert {task.scenario.split("-", 2)[1] for task in core_tasks} == set(CORE_SCENARIOS)
    for code in CORE_SCENARIOS:
        tasks = [task for task in core_tasks if task.scenario.split("-", 2)[1] == code]
        assert len(tasks) == 4
        assert {task.tool for task in tasks} == TOOLS

    for task_id, task in core_by_id.items():
        assert task == matrix_by_id[task_id]
        relative = matrix_files[task_id].relative_to(MATRIX_PACK)
        assert (CORE_PACK / relative).read_bytes() == (MATRIX_PACK / relative).read_bytes()
        for fixture in task.fixtures:
            source = matrix_files[task_id].parent / fixture
            target = CORE_PACK / relative.parent / fixture
            assert target.read_bytes() == source.read_bytes()
            overlay = MATRIX_PACK / "overlays" / task.tool / fixture
            if overlay.is_file():
                assert (CORE_PACK / "overlays" / task.tool / fixture).read_bytes() == overlay.read_bytes()

    matrix_budgets = json.loads((MATRIX_PACK / "efficiency-budgets.json").read_text())
    core_budgets = json.loads((CORE_PACK / "efficiency-budgets.json").read_text())
    assert core_budgets == {task_id: matrix_budgets[task_id] for task_id in sorted(expected_ids)}

    matrix_manifest = snapshot(
        "p95-matrix", matrix_tasks, budgets=matrix_budgets)
    core_manifest = snapshot("p95-core", core_tasks, budgets=core_budgets)
    matrix_inventory = {item["id"]: item for item in matrix_manifest["tasks"]}
    core_inventory = {item["id"]: item for item in core_manifest["tasks"]}
    for task_id in expected_ids:
        assert core_inventory[task_id]["task_digest"] == matrix_inventory[task_id]["task_digest"]
        assert core_manifest["efficiency_budgets"][task_id] == matrix_manifest["efficiency_budgets"][task_id]


def _write_source_run(directory, tasks, budgets, *, omit=None, mismatch_digest=None):
    directory.mkdir()
    manifest = snapshot("p95-matrix", tasks, budgets={task.id: budgets[task.id] for task in tasks})
    if mismatch_digest:
        next(item for item in manifest["tasks"] if item["id"] == mismatch_digest)[
            "task_digest"
        ] = "0" * 64
        contents = dict(manifest)
        contents.pop("digest")
        manifest["digest"] = digest(contents)
    metadata = {
        "pack_id": "p95-matrix",
        "pack_digest": manifest["digest"],
        "visibility": "public",
        "model": "synthetic",
        "provider": "test",
        "harness_commit": "0123456789abcdef",
        "harness_dirty": False,
        "repair_rounds": None,
        "reasoning_mode": None,
        "max_tokens": 16384,
    }
    (directory / "pack.json").write_text(json.dumps(manifest))
    (directory / "metadata.json").write_text(json.dumps(metadata))
    grade = CheckpointGrade((Checkpoint("correct", 100, 100),)).to_dict()
    attempts = []
    for task in tasks:
        if task.id == omit:
            continue
        attempts.append({
            "task_id": task.id,
            "category": task.category,
            "repeat": 0,
            "provider": "test",
            "model": "synthetic",
            "pack_id": "p95-matrix",
            "pack_digest": manifest["digest"],
            "grade": grade,
            "response": {
                "input_tokens": 10,
                "output_tokens": 10,
                "cached_tokens": 0,
                "latency_seconds": 1,
                "response_latency_seconds": 1,
            },
            "cost_usd": 0,
        })
    (directory / "attempts.jsonl").write_text(
        "".join(json.dumps(attempt) + "\n" for attempt in attempts)
    )
    return directory


def test_core_run_derivation_merges_partial_matrix_sources(tmp_path):
    tasks = load_tasks(MATRIX_PACK / "tasks", pack_config("p95-matrix")["weights"])
    budgets = json.loads((MATRIX_PACK / "efficiency-budgets.json").read_text())
    core_ids = {
        task.id for task in tasks
        if task.scenario.split("-", 2)[1] in CORE_SCENARIOS
    }
    core_tasks = [task for task in tasks if task.id in core_ids]
    left = [task for index, task in enumerate(core_tasks) if index % 2 == 0]
    right = [task for index, task in enumerate(core_tasks) if index % 2 == 1]
    source_a = _write_source_run(tmp_path / "source-a", left, budgets)
    source_b = _write_source_run(tmp_path / "source-b", right, budgets)
    output = tmp_path / "derived"

    summary = derive_core_run([source_a, source_b], output)

    assert summary["pack_id"] == "p95-core"
    assert summary["attempts"] == 104
    assert summary["complete"] is True
    metadata = json.loads((output / "metadata.json").read_text())
    attempts = [json.loads(line) for line in (output / "attempts.jsonl").read_text().splitlines()]
    assert [source["run"] for source in metadata["derived_from"]] == ["source-a", "source-b"]
    assert {attempt["derived_from"]["run"] for attempt in attempts} == {"source-a", "source-b"}
    assert not (source_a / "summary.json").exists()


def test_core_run_derivation_rejects_digest_mismatch_and_missing_task(tmp_path):
    tasks = load_tasks(MATRIX_PACK / "tasks", pack_config("p95-matrix")["weights"])
    budgets = json.loads((MATRIX_PACK / "efficiency-budgets.json").read_text())
    core_tasks = [task for task in tasks
                  if task.scenario.split("-", 2)[1] in CORE_SCENARIOS]

    mismatch = _write_source_run(
        tmp_path / "mismatch", core_tasks, budgets, mismatch_digest=core_tasks[0].id)
    with pytest.raises(ValueError, match="Task digest mismatch"):
        derive_core_run([mismatch], tmp_path / "mismatch-output")

    missing = _write_source_run(
        tmp_path / "missing", core_tasks, budgets, omit=core_tasks[0].id)
    with pytest.raises(ValueError, match="Missing Core task attempts"):
        derive_core_run([missing], tmp_path / "missing-output")
