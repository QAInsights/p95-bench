import copy
import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from p95bench import analysis_v2
from p95bench.analysis_v2 import truth
from p95bench.budget import BudgetLedger
from p95bench.checkpoint_graders import award, rate_fraction
from p95bench.graders import grade
from p95bench.packs import pack_config
from p95bench.providers import Price, Response
from p95bench.reference_plans import reference
from p95bench.runner import run
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
    def corrupt(value):
        if isinstance(value, dict):
            return {key: corrupt(item) for key, item in value.items()}
        if isinstance(value, list):
            return [corrupt(item) for item in value]
        if isinstance(value, bool):
            return not value
        return value + 1 if isinstance(value, (int, float)) else value + "-wrong"

    for index, key in enumerate(("observations", "diagnosis", "evidence"), 1):
        mutant = copy.deepcopy(expected)
        mutant[key] = corrupt(mutant[key])
        result = grade(task, json.dumps(mutant), tmp_path, 2)
        vector = [item["points"] for item in task.params["rubric"]]
        vector[index] = 0
        assert [item.earned for item in result.checkpoints] == vector
    for malformed in ("", "{", "null", "{}"):
        assert grade(task, malformed, tmp_path, 2).score == 0


@pytest.mark.parametrize("text", ["", "{", "[1,]", "NaN", '\ud800', None, [], "x" * 65537,
                                  '{"n":' + "9" * 5000 + '}'])
def test_analysis_invalid_model_json_fails_with_detail(text, tmp_path):
    result = grade(ANALYSIS[0], text, tmp_path, 2)
    assert result.score == 0 and not result.passed
    assert all(checkpoint.earned == 0 for checkpoint in result.checkpoints)
    assert result.detail.startswith("Invalid model answer (")


def test_analysis_nested_model_json_does_not_escape_runner_dispatch(tmp_path):
    text = '{"x":' + '[' * 10000 + '0' + ']' * 10000 + '}'
    assert len(text.encode()) == 20007
    result = grade(ANALYSIS[0], text, tmp_path, 2)
    assert result.score == 0 and not result.passed
    assert all(checkpoint.earned == 0 for checkpoint in result.checkpoints)
    assert "Invalid model answer (RecursionError)" in result.detail


@pytest.mark.parametrize("error", [ValueError, TypeError, UnicodeError, RecursionError, OverflowError])
def test_analysis_model_decode_errors_fail_closed(error, monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise error("decode failed")
    monkeypatch.setattr(analysis_v2.json, "loads", fail)
    result = grade(ANALYSIS[0], "{}", tmp_path, 2)
    assert result.score == 0 and not result.passed
    assert all(checkpoint.earned == 0 for checkpoint in result.checkpoints)
    assert result.detail == f"Invalid model answer ({error.__name__}): decode failed"


@pytest.mark.parametrize("task", ANALYSIS, ids=lambda task: task.id)
@pytest.mark.parametrize("number", [10**400, -(10**400)])
def test_analysis_out_of_float_range_model_numbers_fail_closed(task, number, tmp_path):
    aliases = task.params["fixture_aliases"]
    answer = truth(task.params["analysis"], {aliases.get(name, name): value for name, value in task.fixtures.items()})
    def numeric_paths(value, path=()):
        if isinstance(value, dict):
            return [found for key, item in value.items() for found in numeric_paths(item, (*path, key))]
        if isinstance(value, list):
            return [found for key, item in enumerate(value) for found in numeric_paths(item, (*path, key))]
        return [path] if type(value) in (int, float) else []
    paths = numeric_paths(answer)
    assert paths
    for path in paths:
        mutant = copy.deepcopy(answer)
        target = mutant
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = number
        result = grade(task, json.dumps(mutant), tmp_path, 2)
        assert result.score == 0 and all(item.earned == 0 for item in result.checkpoints)
        assert result.detail.startswith("Invalid model answer (OverflowError):")


@pytest.mark.parametrize("comparison", ["same_shape", "equal"])
@pytest.mark.parametrize("error", [OverflowError, ValueError, TypeError, KeyError, RecursionError, ZeroDivisionError])
def test_analysis_comparison_errors_are_model_failures(comparison, error, monkeypatch, tmp_path):
    task = ANALYSIS[0]
    aliases = task.params["fixture_aliases"]
    answer = truth(task.params["analysis"], {aliases.get(name, name): value for name, value in task.fixtures.items()})
    def fail(*args):
        raise error("invalid comparison")
    monkeypatch.setattr(analysis_v2, comparison, fail)
    result = grade(task, json.dumps(answer), tmp_path, 2)
    assert result.score == 0 and all(item.earned == 0 for item in result.checkpoints)
    assert result.detail == f"Invalid model answer ({error.__name__}): {error('invalid comparison')}"


def test_bad_analysis_number_is_graded_and_does_not_stop_provider_calls(tmp_path):
    task = ANALYSIS[0]
    aliases = task.params["fixture_aliases"]
    answer = truth(task.params["analysis"], {aliases.get(name, name): value for name, value in task.fixtures.items()})
    invalid = copy.deepcopy(answer)
    invalid["observations"]["included"] = 10**400
    class Provider:
        calls = 0
        def send(self, model, messages):
            self.calls += 1
            return Response(json.dumps(invalid if self.calls == 1 else answer), 100, 20, 0, 1)
    provider = Provider()
    directory, summary = run(provider, "synthetic", "model", Price(1, 1, 1), [task], tmp_path,
        repeats=2, pack_id="p95-v2.0.0", budget_ledger=BudgetLedger(tmp_path / "ledger.json"),
        efficiency_budgets={task.id: {"tokens": 100, "latency_seconds": 10}})
    attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()]
    assert provider.calls == 2
    assert [attempt["grade"]["score"] for attempt in attempts] == [0, 1]
    assert all("grading_error" not in attempt for attempt in attempts)
    assert summary["complete"] and "stop_reason" not in summary
    assert not (directory / "not-run.jsonl").exists()


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
