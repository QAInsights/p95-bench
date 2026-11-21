import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from p95bench.checkpoint_graders import flow_fractions
from p95bench.execution import Trial, artifact_source, locust_evidence
from p95bench.graders import grade
from p95bench.packs import pack_config
from p95bench.reference_plans import locust_reference, reference
from p95bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
TASKS = load_tasks(ROOT / "packs/p95-v2.0.0/tasks", pack_config("p95-v2.0.0")["weights"])
EXECUTABLE = [task for task in TASKS if task.grader == "v2_exec"]
@pytest.mark.parametrize("task", EXECUTABLE, ids=lambda task: task.id)
def test_locust_reference_sources_are_python(task):
    source = reference("locust", task.params["contract"])
    assert "class MatrixUser(HttpUser)" in source
    assert "class MatrixShape(LoadTestShape)" in source
    compile(source, "locustfile.py", "exec")


@pytest.mark.parametrize("fence", ["python", "py", ""])
def test_locust_artifact_source_unfences_python(fence):
    text = f"```{fence}\nprint('ok')\n```"
    assert artifact_source("locust", text) == "print('ok')"


def test_locust_artifact_source_limits_size():
    with pytest.raises(ValueError, match="1 MiB"):
        artifact_source("locust", "x" * (1024 * 1024 + 1))


def test_locust_evidence_parses_request_records():
    record = {"request": "request-" + "a" * 20, "worker": "user-2", "name": "checkout",
              "method": "POST", "path": "/v2/orders", "success": True,
              "assertion_failures": 0, "response_time": 251.5, "active_users": 2}
    samples, points = locust_evidence({"requests.jsonl": (json.dumps(record) + "\n").encode()})
    assert samples == [record]
    assert points == []


@pytest.mark.parametrize("record", [
    {"request": "bad"},
    {"request": "request-" + "a" * 20, "worker": "user-1", "method": "POST",
     "path": "/v2/orders", "success": True, "assertion_failures": 2,
     "response_time": 1, "active_users": 1},
])
def test_locust_evidence_rejects_invalid_records(record):
    with pytest.raises(ValueError, match="Invalid Locust"):
        locust_evidence({"requests.jsonl": (json.dumps(record) + "\n").encode()})


@pytest.mark.parametrize("points,expected", [
    ([{"metric": "http_req_duration", "tags": {"name": "checkout"}}] * 2, 0.5),
    ([], 0.25),
])
def test_threshold_uses_native_duration_samples_when_points_are_absent(points, expected):
    samples = [
        {"request": "request-" + str(index) * 20, "worker": "user-1", "name": "checkout",
         "method": "POST", "path": "/v2/orders", "success": True,
         "assertion_failures": 0, **({"response_time": 21.5} if index == 1 else {})}
        for index in range(1, 3)
    ]
    trial = Trial(True, "completed", 0, [], samples, points)
    fractions = flow_fractions(trial, [], {"mode": "threshold", "users": 2, "iterations": 2})
    assert fractions["setup"] == expected


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", EXECUTABLE, ids=lambda task: task.id)
def test_native_locust_reference_scores_100(task, tmp_path):
    source = locust_reference(task.params["contract"])
    locust_task = replace(task, tool="locust",
                          params={**task.params, "tool": "locust"})
    result = grade(locust_task, source, tmp_path, 2)
    assert result.score == 1, result.to_dict()


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", EXECUTABLE, ids=lambda task: task.id)
def test_empty_locust_artifact_zeroes_every_gate(task, tmp_path):
    locust_task = replace(task, tool="locust",
                          params={**task.params, "tool": "locust"})
    assert [item.earned for item in grade(locust_task, "", tmp_path, 2).checkpoints] == [0, 0, 0, 0]
