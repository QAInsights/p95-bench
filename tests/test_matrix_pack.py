import os
import re
import shutil
from pathlib import Path

import pytest

from p95bench.graders import grade
from p95bench.packs import pack_config
from p95bench.reference_plans import reference
from p95bench.tasks import load_tasks
from scripts.build_matrix_pack import build_matrix_pack

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACK = ROOT / "packs/p95-v2.0.0"
MATRIX_PACK = ROOT / "packs/p95-matrix"
TOOLS = ("jmeter", "k6", "locust", "gatling")
SOURCE_TASKS = {
    task.id: task for task in load_tasks(
        SOURCE_PACK / "tasks", pack_config("p95-v2.0.0")["weights"])
    if task.category in {"correlation", "workload"}
}
MATRIX_TASKS = load_tasks(
    MATRIX_PACK / "tasks", pack_config("p95-matrix")["weights"])
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
VU_WORD = re.compile(r"\bVUs?\b")


def tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_matrix_pack_generation_is_deterministic_and_matches_committed_pack(tmp_path):
    generated = tmp_path / "p95-matrix"
    build_matrix_pack(SOURCE_PACK, generated)
    for category in ("correlation", "workload"):
        assert tree_bytes(generated / "tasks" / category) == tree_bytes(
            MATRIX_PACK / "tasks" / category)
    existing = tmp_path / "existing"
    shutil.copytree(MATRIX_PACK, existing)
    before = tree_bytes(existing)
    build_matrix_pack(SOURCE_PACK, existing)
    assert tree_bytes(existing) == before


def test_matrix_tasks_load_with_consistent_tool_scenario_and_protocol():
    matrix_config = pack_config("p95-matrix")
    v2_config = pack_config("p95-v2.0.0")
    assert matrix_config["weights"] == v2_config["weights"]
    assert matrix_config["efficiency_weight"] == v2_config["efficiency_weight"]
    assert len(MATRIX_TASKS) == 72
    assert {task.category for task in MATRIX_TASKS} == {"correlation", "workload"}
    assert sum(task.category == "correlation" for task in MATRIX_TASKS) == 40
    assert sum(task.category == "workload" for task in MATRIX_TASKS) == 32

    by_scenario = {}
    for task in MATRIX_TASKS:
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
            for key in ("contract", "rubric", "trials", "controls"):
                assert task.params[key] == source.params[key]
            expected_prompt = source.prompt if task.tool == "k6" else VU_WORD.sub(
                lambda match: "virtual users" if match.group() == "VUs" else "virtual user",
                source.prompt,
            )
            assert task.prompt == expected_prompt
            for name, content in source.fixtures.items():
                if name != "protocol.md":
                    assert task.fixtures[name] == content

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
