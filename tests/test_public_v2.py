import copy
import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from p95bench.analysis_v2 import truth
from p95bench.checkpoint_graders import award, rate_fraction
from p95bench.graders import grade
from p95bench.packs import pack_config
from p95bench.reference_plans import reference
from p95bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1] / "packs/p95-v2.0.0/tasks"
TASKS = load_tasks(ROOT, pack_config("p95-v2.0.0")["weights"])
EXECUTABLE = [task for task in TASKS if task.grader == "v2_exec"]
ANALYSIS = [task for task in TASKS if task.grader == "v2_analysis"]


def test_complete_public_inventory_and_gates():
    assert len(TASKS) == 40 and len(EXECUTABLE) == 35 and len(ANALYSIS) == 5
    for task in TASKS:
        previous = set()
        assert sum(item["points"] for item in task.params["rubric"]) == 100
        for item in task.params["rubric"]:
            assert set(item["requires"]) <= previous
            previous.add(item["id"])
        assert len(task.user_prompt()) > len(task.prompt)


def test_rate_curve_and_fraction_gates():
    assert rate_fraction(10, 10) == 1
    assert rate_fraction(11, 10) == 1
    assert rate_fraction(12.5, 10) == pytest.approx(.5)
    assert rate_fraction(14, 10) == 0
    rubric = TASKS[0].params["rubric"]
    assert [item.earned for item in award(rubric, {})] == [0, 0, 0, 0]


@pytest.mark.parametrize("count,target,window,expected", [
    (4, 1, 5, 1), (5, 1, 5, 1), (6, 1, 5, 1), (3, 1, 5, 0),
    (0, 1, 5, 0), (11, 2, 5, 1), (12, 2, 5, 2 / 3),
    (55, 10, 5, 1), (60, 10, 5, 2 / 3), (27, 1, 30, 1),
])
def test_rate_window_tolerance_includes_one_arrival(count, target, window, expected):
    assert rate_fraction(count / window, target, window) == pytest.approx(expected)


@pytest.mark.parametrize("operation", ["work", "journey"])
def test_jmeter_reference_covers_startup_before_scored_window(operation):
    config = {"workload": True, "rate": 5, "operation": operation}
    root = ET.fromstring(reference("jmeter", config))
    assert root.findtext(".//stringProp[@name='ThreadGroup.duration']") == "38"
    root = ET.fromstring(reference("jmeter", dict(config, duration=12)))
    assert root.findtext(".//stringProp[@name='ThreadGroup.duration']") == "12"


@pytest.mark.parametrize("task", ANALYSIS, ids=lambda task: task.id)
def test_analysis_goldens_and_independent_mutations(task, tmp_path):
    aliases = task.params["fixture_aliases"]
    expected = truth(task.params["analysis"], {aliases.get(name, name): value for name, value in task.fixtures.items()})
    assert grade(task, json.dumps(expected), tmp_path, 2).score == 1
    for index, key in enumerate(("observations", "diagnosis", "evidence"), 1):
        mutant = copy.deepcopy(expected)
        mutant[key] = {"wrong": "injected fault"}
        result = grade(task, json.dumps(mutant), tmp_path, 2)
        vector = [item["points"] for item in task.params["rubric"]]
        vector[index] = 0
        assert [item.earned for item in result.checkpoints] == vector
    for malformed in ("", "{", "null", "{}"):
        assert grade(task, malformed, tmp_path, 2).score == 0


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task,repeat", [
    pytest.param(task, repeat, id=f"{task.id}-run{repeat}")
    for task in EXECUTABLE for repeat in (range(1, 4) if task.category == "workload" else [1])
])
def test_native_public_reference_scores_100(task, repeat, tmp_path):
    result = grade(task, reference(task.tool, task.params["contract"]), tmp_path, 2)
    print(f"NATIVE GOLDEN {task.id} run={repeat} score={result.score * 100:.8f}", flush=True)
    assert result.score == 1, result.to_dict()


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
@pytest.mark.parametrize("tool", ("jmeter", "k6"))
def test_native_csrf_mutation_has_explicit_prerequisite_vector(tool, tmp_path):
    task = next(task for task in EXECUTABLE if task.tool == tool and task.params["contract"]["mode"] == "flow")
    source = reference(tool, task.params["contract"])
    mutant = source.replace("$.csrf", "$.missing_csrf") if tool == "jmeter" else source.replace("csrf=b.csrf", "csrf='MISSING'")
    result = grade(task, mutant, tmp_path, 2)
    assert [item.earned for item in result.checkpoints] == [10, 0, 0, 0]


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", EXECUTABLE, ids=lambda task: task.id)
def test_empty_artifact_zeroes_every_execution_gate(task, tmp_path):
    assert [item.earned for item in grade(task, "", tmp_path, 2).checkpoints] == [0, 0, 0, 0]
