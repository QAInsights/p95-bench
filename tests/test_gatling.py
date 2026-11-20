import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from p95bench.checkpoint_graders import flow_fractions
from p95bench.execution import (
    Trial,
    artifact_source,
    gatling_evidence,
    native_command,
    run_artifact,
)
from p95bench.gatling_runner import SESSION_USER
from p95bench.graders import grade
from p95bench.images import VERSIONS
from p95bench.packs import pack_config
from p95bench.reference_plans import gatling_reference, reference
from p95bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
TASKS = load_tasks(ROOT / "packs/p95-v2.0.0/tasks", pack_config("p95-v2.0.0")["weights"])
EXECUTABLE = [task for task in TASKS if task.grader == "v2_exec"]
GATLING_EVIDENCE_XFAIL = {
    "v2-r12-error-metric": "Gatling exports no business_failure metric points",
}


@pytest.mark.parametrize("task", EXECUTABLE, ids=lambda task: task.id)
def test_gatling_reference_sources_are_java(task):
    source = reference("gatling", task.params["contract"])
    assert "public class ModelSimulation extends Simulation" in source
    assert "io.gatling.javaapi.http" in source


@pytest.mark.parametrize("fence", ["java", ""])
def test_gatling_artifact_source_unfences_java(fence):
    text = f"```{fence}\npublic class ModelSimulation {{}}\n```"
    assert artifact_source("gatling", text) == "public class ModelSimulation {}"


def test_gatling_artifact_source_limits_size():
    with pytest.raises(ValueError, match="1 MiB"):
        artifact_source("gatling", "x" * (1024 * 1024 + 1))


def test_gatling_command_and_version_are_pinned():
    assert native_command("gatling") == [
        "python3", "/gatling_runner.py", "/fixtures/ModelSimulation.java"
    ]
    assert VERSIONS["gatling"] == "3.15.1"


def test_gatling_evidence_parses_request_records():
    record = {"request": "request-" + "a" * 20, "worker": "user-2", "name": "checkout",
              "method": "POST", "path": "/v2/orders", "success": True,
              "assertion_failures": 0, "response_time": 251, "active_users": 2}
    samples, points = gatling_evidence({"requests.jsonl": (json.dumps(record) + "\n").encode()})
    assert samples == [record]
    assert points == []


def test_gatling_trace_session_user_id():
    match = SESSION_USER.search("Session:\nSession(matrix,1,HashMap())")
    assert match and match[1] == "1"


@pytest.mark.parametrize("record", [
    {"request": "bad"},
    {"request": "request-" + "a" * 20, "worker": "user-1", "method": "POST",
     "path": "/v2/orders", "success": True, "assertion_failures": 2,
     "response_time": 1, "active_users": 1},
])
def test_gatling_evidence_rejects_invalid_records(record):
    with pytest.raises(ValueError, match="Invalid Gatling"):
        gatling_evidence({"requests.jsonl": (json.dumps(record) + "\n").encode()})


@pytest.mark.parametrize("points,expected", [
    ([{"metric": "http_req_duration", "tags": {"name": "checkout"}}] * 2, 0.5),
    ([], 0.25),
])
def test_threshold_uses_native_duration_samples_when_points_are_absent(points, expected):
    samples = [
        {"request": "request-" + str(index) * 20, "worker": "user-1", "name": "checkout",
         "method": "POST", "path": "/v2/orders", "success": True,
         "assertion_failures": 0, **({"response_time": 21} if index == 1 else {})}
        for index in range(1, 3)
    ]
    trial = Trial(True, "completed", 0, [], samples, points)
    fractions = flow_fractions(trial, [], {"mode": "threshold", "users": 2, "iterations": 2})
    assert fractions["setup"] == expected


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", [
    pytest.param(task, marks=pytest.mark.xfail(strict=True, reason=GATLING_EVIDENCE_XFAIL[task.id]))
    if task.id in GATLING_EVIDENCE_XFAIL else pytest.param(task)
    for task in EXECUTABLE
], ids=lambda task: task.id)
def test_native_gatling_reference_scores_100(task, tmp_path):
    source = gatling_reference(task.params["contract"])
    gatling_task = replace(task, tool="gatling",
                           params={**task.params, "tool": "gatling"})
    result = grade(gatling_task, source, tmp_path, 2)
    assert result.score == 1, result.to_dict()


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
@pytest.mark.parametrize("task", EXECUTABLE, ids=lambda task: task.id)
def test_empty_gatling_artifact_zeroes_every_gate(task, tmp_path):
    gatling_task = replace(task, tool="gatling",
                           params={**task.params, "tool": "gatling"})
    assert [item.earned for item in grade(gatling_task, "", tmp_path, 2).checkpoints] == [0, 0, 0, 0]


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
def test_gatling_compiles_within_trial_timeout(tmp_path):
    trial = run_artifact(
        "gatling", gatling_reference({"users": 1, "iterations": 1}), tmp_path,
        profile={}, seed="gatling-compile-probe", timeout=60,
    )
    execution = json.loads((tmp_path / "execution.json").read_text())
    compile_output = next((
        line.split("=", 1)[1]
        for line in execution["output"].splitlines()
        if line.startswith("GATLING_COMPILE_SECONDS=")
    ), None)
    assert trial.loaded and trial.status == "completed" and trial.returncode == 0
    if compile_output is not None:
        compile_seconds = float(compile_output)
        assert compile_seconds < 60
        print(f"GATLING_COMPILE_SECONDS={compile_seconds:.3f}")
    assert execution["duration_seconds"] < 60
    print(f"GATLING_TRIAL_SECONDS={execution['duration_seconds']:.3f}")
