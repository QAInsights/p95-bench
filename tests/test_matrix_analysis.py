import copy
import csv
import json
import os
from io import StringIO
from pathlib import Path

import pytest

from p95bench.analysis_v2 import (
    TOOL_ANSWER_STRINGS,
    adapt_native,
    analysis_reference,
)
from p95bench.graders import grade
from p95bench.packs import pack_config
from p95bench.reference_plans import reference
from p95bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TASKS = {
    task.id: task for task in load_tasks(
        ROOT / "packs/p95-v2.0.0/tasks", pack_config("p95-v2.0.0")["weights"])
}
MATRIX_TASKS = load_tasks(
    ROOT / "packs/p95-matrix/tasks", pack_config("p95-matrix")["weights"])
MATRIX_ANALYSIS = [task for task in MATRIX_TASKS if task.category == "analysis"]


def _parsed(name, text):
    if name.endswith(".csv"):
        return list(csv.DictReader(StringIO(text)))
    return json.loads(text)


def _corrupt(value):
    if isinstance(value, dict):
        return {key: _corrupt(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_corrupt(item) for item in value]
    if isinstance(value, bool):
        return not value
    return value + 1 if isinstance(value, (int, float)) else value + "-wrong"


@pytest.mark.parametrize("task", MATRIX_ANALYSIS, ids=lambda task: task.id)
def test_matrix_analysis_adapter_matches_canonical_v2_data(task):
    source = SOURCE_TASKS[task.scenario.replace("mx-", "v2-", 1)]
    aliases = source.params.get("fixture_aliases", {})
    canonical = {
        aliases.get(name, name): value for name, value in source.fixtures.items()
    }
    adapted = adapt_native(
        task.params["analysis"], task.tool, task.fixtures, task.params["native_files"])
    assert set(adapted) == set(canonical)
    assert {name: _parsed(name, value) for name, value in adapted.items()} == {
        name: _parsed(name, value) for name, value in canonical.items()
    }


def test_matrix_a01_locust_transaction_rows_match_events_request_prompt():
    task = next(
        task for task in MATRIX_ANALYSIS
        if task.id == "mx-a01-jtl-endpoints-locust"
    )
    native = _parsed(task.params["native_files"]["data"], task.fixtures[
        task.params["native_files"]["data"]
    ])
    parent_rows = [row for row in native if row["row_id"] == "parent"]

    assert len(parent_rows) == 1
    assert parent_rows[0]["request_type"] == "Transaction"
    assert task.prompt.splitlines()[0] == (
        "Analyze a Locust per-request CSV exported by an events.request listener; "
        "the test also fires transaction timings through events.request "
        "(locust-plugins TransactionManager style). Identify the slow endpoint "
        "after removing warmup and transaction parent samples."
    )
    assert (
        "request_type=Transaction identifies the transaction rows fired through "
        "events.request."
    ) in task.user_prompt()


@pytest.mark.parametrize("task", MATRIX_ANALYSIS, ids=lambda task: task.id)
def test_matrix_analysis_goldens_and_independent_mutations(task, tmp_path):
    expected = analysis_reference(task.params, task.fixtures)
    assert grade(task, json.dumps(expected), tmp_path, 2).score == 1

    for index, key in enumerate(("observations", "diagnosis", "evidence"), 1):
        mutant = copy.deepcopy(expected)
        mutant[key] = _corrupt(mutant[key])
        result = grade(task, json.dumps(mutant), tmp_path, 2)
        assert [item.earned for item in result.checkpoints] == [
            10 if position == 0 else 0 if position == index else item["points"]
            for position, item in enumerate(task.params["rubric"])
        ]


def test_matrix_analysis_tool_strings_and_numeric_parity():
    by_scenario = {}
    for task in MATRIX_ANALYSIS:
        by_scenario.setdefault(task.scenario, []).append(task)
    for tasks in by_scenario.values():
        answers = {
            task.tool: analysis_reference(task.params, task.fixtures)
            for task in tasks
        }
        assert len({json.dumps(answer["observations"], sort_keys=True)
                    for answer in answers.values()}) == 1
        kind = tasks[0].params["analysis"]
        if kind in TOOL_ANSWER_STRINGS:
            for tool, (metric, keys, fix) in TOOL_ANSWER_STRINGS[kind].items():
                assert answers[tool]["diagnosis"]["failed_metric"] == metric
                assert answers[tool]["evidence"]["keys"] == keys
                assert answers[tool]["evidence"]["fix"] == fix


def test_matrix_a06_spans_sidecar_is_identical_across_tools():
    tasks = [task for task in MATRIX_ANALYSIS
             if task.params["analysis"] == "decomposition"]
    assert len({task.fixtures["spans.json"] for task in tasks}) == 1
    assert all("spans.json" in task.user_prompt() for task in tasks)


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1",
                    reason="Opt-in full Matrix reference gate")
@pytest.mark.parametrize(
    "task",
    [
        pytest.param(
            task,
            marks=pytest.mark.xfail(
                strict=True,
                reason="Known JMeter W04 reference gap; best serialized CTT score 0.9188888889",
            ),
        ) if task.id == "mx-w04-three-phases-jmeter" else task
        for task in MATRIX_TASKS
    ],
    ids=lambda task: task.id,
)
def test_matrix_full_reference_gate(task, tmp_path):
    artifact = (
        json.dumps(analysis_reference(task.params, task.fixtures))
        if task.grader == "v2_analysis"
        else reference(task.tool, task.params["contract"])
    )
    result = grade(task, artifact, tmp_path, 2)
    assert result.score == 1, result.to_dict()
