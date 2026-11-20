import json
import os
import shutil
from pathlib import Path

import pytest

from p95bench.graders import grade
from p95bench.packs import pack_config
from p95bench.reference_plans import reference
from p95bench.tasks import load_tasks
from scripts.build_matrix_pack import (
    MIGRATION_PROMPTS,
    REPAIR_PROMPTS,
    _text_for_tool,
    build_matrix_pack,
)
from scripts.build_matrix_repair_overlays import build_repair_overlays

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK = ROOT / "packs/p95-v2.0.0"
MATRIX_PACK = ROOT / "packs/p95-matrix"
TOOLS = ("jmeter", "k6", "locust", "gatling")
SOURCE_TASKS = {
    task.id: task for task in load_tasks(
        SOURCE_PACK / "tasks", pack_config("p95-v2.0.0")["weights"])
    if task.category in {"correlation", "workload", "repair", "migration"}
}
MATRIX_TASKS = load_tasks(
    MATRIX_PACK / "tasks", pack_config("p95-matrix")["weights"])
PHASE_A_TASKS = [task for task in MATRIX_TASKS if task.category in {"correlation", "workload"}]
REPAIR_TASKS = [task for task in MATRIX_TASKS if task.category == "repair"]
MIGRATION_TASKS = [task for task in MATRIX_TASKS if task.category == "migration"]
REPAIR_ORIGINAL_TOOLS = {
    "r01": "jmeter", "r02": "k6", "r04": "k6", "r05": "jmeter",
    "r06": "k6", "r07": "jmeter", "r08": "k6", "r10": "k6",
    "r11": "jmeter", "r12": "k6", "r13": "jmeter",
}
REPAIR_TARGETS = {
    "r01": "behavior", "r02": "behavior", "r04": "behavior", "r05": "behavior",
    "r06": "behavior", "r07": "behavior", "r08": "behavior", "r10": "behavior",
    "r11": "behavior", "r12": "behavior", "r13": "safety",
}
REPAIR_SCORES_PATH = Path.home() / "notes/phase-b-broken-scores.json"
HEADERS = {
    "k6": (
        "Return one complete k6 1.0.0 JS artifact, no explanation.",
        "Target only http://127.0.0.1:8000 (__ENV.BASE_URL).",
    ),
    "jmeter": (
        "Return one complete JMeter 5.6.3 JMX artifact, no explanation.",
        "Target only http://127.0.0.1:8000 (host/port properties or literals).",
    ),
    "locust": (
        "Return one complete Locust 2.47 Python locustfile, no explanation.",
        'Target only http://127.0.0.1:8000 (os.environ["BASE_URL"] is the host).',
    ),
    "gatling": (
        "Return one complete Gatling 3.15 Java DSL simulation, no explanation: "
        + "public class ModelSimulation extends Simulation, default package.",
        "Target only http://127.0.0.1:8000 (System.getenv(\"BASE_URL\")).",
    ),
}
def tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_matrix_pack_generation_is_deterministic_and_matches_committed_pack(tmp_path):
    generated = tmp_path / "p95-matrix"
    build_repair_overlays(SOURCE_PACK, generated)
    build_matrix_pack(SOURCE_PACK, generated)
    for category in ("correlation", "workload", "repair", "migration"):
        assert tree_bytes(generated / "tasks" / category) == tree_bytes(
            MATRIX_PACK / "tasks" / category)
    existing = tmp_path / "existing"
    shutil.copytree(MATRIX_PACK, existing)
    before = tree_bytes(existing)
    build_matrix_pack(SOURCE_PACK, existing)
    assert tree_bytes(existing) == before


def test_repair_overlay_generation_is_deterministic(tmp_path):
    generated = tmp_path / "p95-matrix"
    build_repair_overlays(SOURCE_PACK, generated)
    build_matrix_pack(SOURCE_PACK, generated)
    assert tree_bytes(generated / "overlays") == tree_bytes(MATRIX_PACK / "overlays")


def test_matrix_tasks_load_with_consistent_tool_scenario_and_protocol():
    matrix_config = pack_config("p95-matrix")
    v2_config = pack_config("p95-v2.0.0")
    assert matrix_config["weights"] == v2_config["weights"]
    assert matrix_config["efficiency_weight"] == v2_config["efficiency_weight"]
    assert len(MATRIX_TASKS) == 140
    assert {task.category for task in MATRIX_TASKS} == {
        "correlation", "workload", "repair", "migration",
    }
    assert sum(task.category == "correlation" for task in PHASE_A_TASKS) == 40
    assert sum(task.category == "workload" for task in PHASE_A_TASKS) == 32
    assert len(REPAIR_TASKS) == 44

    by_scenario = {}
    for task in PHASE_A_TASKS:
        by_scenario.setdefault(task.scenario, []).append(task)
        assert task.tool in TOOLS and task.params["tool"] == task.tool
        assert task.id == f"{task.scenario}-{task.tool}"
        assert set(task.fixtures) >= {f"protocol-{task.tool}.md"}
        protocol = task.fixtures[f"protocol-{task.tool}.md"]
        assert protocol.splitlines()[1:3] == list(HEADERS[task.tool])
        assert all(line in task.user_prompt() for line in HEADERS[task.tool])
        assert protocol.splitlines()[-1] == (
            "Fixtures are read-only /fixtures/<logical alias>. CPU1, RAM768MiB, "
            "PIDs256, FDs512, no egress."
        )
        if task.tool != "k6":
            assert (
                "Mark unexpected /v2/work failures as failed samples; expected expiry "
                "and scheduled 429 responses are not failures."
            ) in protocol
            assert (
                "Every raw /v2/work response is one native sample. Keep assertions truthful."
            ) in protocol

    assert len(by_scenario) == 18
    assert sum(scenario.startswith("mx-c") for scenario in by_scenario) == 10
    assert sum(scenario.startswith("mx-w") for scenario in by_scenario) == 8
    for scenario, variants in by_scenario.items():
        assert len(variants) == 4
        assert {task.tool for task in variants} == set(TOOLS)
        source_id = scenario.replace("mx-", "v2-", 1)
        source = SOURCE_TASKS[source_id]
        for task in variants:
            assert task.category == source.category
            assert task.grader == source.grader
            for key in ("contract", "trials", "controls"):
                assert task.params[key] == source.params[key]
            assert task.params["rubric"] == [
                {**rubric, "description": _text_for_tool(
                    rubric["description"], task.tool, task.scenario)}
                for rubric in source.params["rubric"]
            ]
            expected_prompt = _text_for_tool(source.prompt, task.tool, task.scenario)
            assert task.prompt == expected_prompt
            for name, content in source.fixtures.items():
                if name != "protocol.md":
                    assert task.fixtures[name] == content

    repair_by_scenario = {}
    for task in REPAIR_TASKS:
        repair_by_scenario.setdefault(task.scenario, []).append(task)
        code = task.scenario.split("-", 2)[1]
        source = SOURCE_TASKS[f"v2-{code}-" + task.scenario.split("-", 2)[2]]
        extension = {"jmeter": "jmx", "k6": "js", "locust": "py", "gatling": "java"}[task.tool]
        artifact_name = f"broken-{code}.{extension}"
        assert task.id == f"{task.scenario}-{task.tool}"
        assert task.tool in TOOLS and task.params["tool"] == task.tool
        expected_prompt = REPAIR_PROMPTS.get(
            (code, task.tool),
            _text_for_tool(source.prompt, task.tool, task.scenario).split("\n\n", 1)[0])
        assert task.prompt.split("\n\n", 1)[0] == expected_prompt
        assert task.prompt.split("\n\n", 1)[1].splitlines()[0] == "Repair the supplied artifact."
        assert set(task.fixtures) >= {artifact_name, f"protocol-{task.tool}.md"}
        assert task.fixtures[artifact_name]
        protocol = task.fixtures[f"protocol-{task.tool}.md"]
        assert protocol.splitlines()[1:3] == list(HEADERS[task.tool])
        assert all(line in task.user_prompt() for line in HEADERS[task.tool])
        for key in ("contract", "trials", "controls"):
            assert task.params[key] == source.params[key]
        assert task.params["rubric"] == [
            {**rubric, "description": _text_for_tool(
                rubric["description"], task.tool, task.scenario)}
            for rubric in source.params["rubric"]
        ]
        for name, content in source.fixtures.items():
            if name not in ("protocol.md",) and not name.startswith("broken-r"):
                assert task.fixtures[name] == content
        if code == "r10":
            assert set(task.fixtures) >= {
                "r10-accounts.json", "r10-account-1.csv", "r10-account-2.csv",
            }
        if task.tool == REPAIR_ORIGINAL_TOOLS[code]:
            original_name = next(name for name in source.fixtures if name.startswith("broken-r"))
            assert task.fixtures[artifact_name] == source.fixtures[original_name]

    assert len(repair_by_scenario) == 11
    for scenario, variants in repair_by_scenario.items():
        assert len(variants) == 4
        assert {task.tool for task in variants} == set(TOOLS)
        source_id = scenario.replace("mx-", "v2-", 1)
        source = SOURCE_TASKS[source_id]
        for task in variants:
            assert task.category == source.category
            assert task.grader == source.grader
            for key in ("contract", "trials", "controls"):
                assert task.params[key] == source.params[key]

    assert len(MIGRATION_TASKS) == 24
    migration_by_scenario = {}
    for task in MIGRATION_TASKS:
        migration_by_scenario.setdefault(task.scenario, []).append(task)
        code = task.scenario.split("-", 2)[1]
        source = next(
            candidate for candidate in SOURCE_TASKS.values()
            if candidate.category == "migration"
            and candidate.id.startswith(f"v2-{code}-")
        )
        source_jmx = next(name for name in source.fixtures if name.startswith("source-m"))
        assert task.id == f"{task.scenario}-{task.tool}"
        assert task.tool in TOOLS and task.params["tool"] == task.tool
        assert set(task.fixtures) >= {f"protocol-{task.tool}.md"}
        for key in ("contract", "trials", "controls", "rubric"):
            assert task.params[key] == source.params[key]
        if task.tool == "k6":
            assert task.prompt == source.prompt
            assert task.fixtures[source_jmx] == source.fixtures[source_jmx]
        else:
            line_two = (
                f"Translate the supplied JMX to {task.tool.title()}."
                if task.tool in ("locust", "gatling")
                else "Translate the supplied k6 script to JMeter."
            )
            assert task.prompt.splitlines()[0] == MIGRATION_PROMPTS[(code, task.tool)]
            assert task.prompt.splitlines()[2] == line_two
        if task.tool == "jmeter":
            source_js = Path(source_jmx).with_suffix(".js").name
            assert task.fixtures[source_js] == reference(
                "k6", source.params["contract"])
            assert task.fixtures[source_js] == (
                MATRIX_PACK / "overlays" / "jmeter" / source_js
            ).read_text()
        else:
            assert task.fixtures[source_jmx] == source.fixtures[source_jmx]
        if code == "m04":
            assert set(task.fixtures) >= {
                "m04-accounts.json", "m04-account-1.csv", "m04-account-2.csv",
            }

    assert len(migration_by_scenario) == 6
    for scenario, variants in migration_by_scenario.items():
        assert len(variants) == 4
        assert {task.tool for task in variants} == set(TOOLS)

    assert all((MATRIX_PACK / "overlays" / tool).is_dir() for tool in TOOLS)


REFERENCE_TASK_IDS = {
    f"mx-{code}-{slug}-{tool}"
    for code, slug in (("c02", "html-csrf"), ("w01", "steady-starts"))
    for tool in TOOLS
}
REFERENCE_TASKS = [task for task in MATRIX_TASKS if task.id in REFERENCE_TASK_IDS]


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1",
                    reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", REFERENCE_TASKS, ids=lambda task: task.id)
def test_matrix_native_reference_scores_100(task, tmp_path):
    result = grade(task, reference(task.tool, task.params["contract"]), tmp_path, 2)
    assert result.score == 1, result.to_dict()


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1",
                    reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", MIGRATION_TASKS, ids=lambda task: task.id)
def test_matrix_migration_reference_scores_100(task, tmp_path):
    result = grade(task, reference(task.tool, task.params["contract"]), tmp_path, 2)
    assert result.score == 1, result.to_dict()


JMeter_MIGRATION_TASKS = [task for task in MIGRATION_TASKS if task.tool == "jmeter"]


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1",
                    reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", JMeter_MIGRATION_TASKS, ids=lambda task: task.id)
def test_jmeter_migration_source_scores_as_k6(task, tmp_path):
    code = task.scenario.split("-", 2)[1]
    source_task = next(
        candidate for candidate in SOURCE_TASKS.values()
        if candidate.category == "migration" and candidate.id.startswith(f"v2-{code}-")
    )
    source_name = next(name for name in task.fixtures if name.endswith(".js"))
    result = grade(source_task, task.fixtures[source_name], tmp_path, 2)
    assert result.score == 1, result.to_dict()


@pytest.fixture(scope="session", autouse=True)
def reset_repair_scores():
    if os.environ.get("P95_TEST_DOCKER") == "1":
        REPAIR_SCORES_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPAIR_SCORES_PATH.write_text("[]\n")


def write_repair_score(record):
    records = json.loads(REPAIR_SCORES_PATH.read_text())
    records = [item for item in records if item["task_id"] != record["task_id"]]
    records.append(record)
    REPAIR_SCORES_PATH.write_text(json.dumps(records, indent=2) + "\n")


def repair_target(task):
    code = task.scenario.split("-", 2)[1]
    if code == "r11" and task.tool == "gatling":
        return "safety"
    return REPAIR_TARGETS[code]


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1",
                    reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", REPAIR_TASKS, ids=lambda task: task.id)
def test_matrix_repair_reference_and_broken_checkpoint(task, tmp_path):
    code = task.scenario.split("-", 2)[1]
    artifact_name = next(name for name in task.fixtures
                         if name.startswith(f"broken-{code}."))
    target_id = repair_target(task)
    record = {"task_id": task.id, "target_checkpoint": target_id}
    try:
        correct = grade(task, reference(task.tool, task.params["contract"]),
                        tmp_path / "reference", 2)
        broken = grade(task, task.fixtures[artifact_name], tmp_path / "broken", 2)
        target = next(item for item in broken.checkpoints if item.id == target_id)
        record.update({
            "reference_score": correct.score,
            "broken_score": broken.score,
            "broken_checkpoints": {
                item.id: {"earned": item.earned, "possible": item.possible}
                for item in broken.checkpoints
            },
            "target_checkpoint": {
                "id": target.id, "earned": target.earned, "possible": target.possible,
            },
            "reference_detail": correct.detail,
            "broken_detail": broken.detail,
        })
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
        write_repair_score(record)
        raise
    write_repair_score(record)
    assert correct.score == 1, correct.to_dict()
    assert broken.score < 1, broken.to_dict()
    assert broken.checkpoints[0].earned == broken.checkpoints[0].possible
    code = task.scenario.split("-", 2)[1]
    target = next(item for item in broken.checkpoints if item.id == repair_target(task))
    assert target.earned < target.possible, broken.to_dict()
