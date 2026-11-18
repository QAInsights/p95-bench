import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from p95bench.capacity import capacity_preflight
from p95bench.execution import Trial, artifact_source, k6_evidence
from p95bench.graders import grade
from p95bench.images import tool_image
from p95bench.packs import pack_config
from p95bench.reference_plans import reference
from p95bench.runner import run
from p95bench.tasks import load_tasks
from p95bench.worker import PROBE_IMAGE, Worker, WorkerUnavailable

ROOT = Path(__file__).resolve().parents[1]
TASKS = load_tasks(ROOT / "packs/p95-v2.0.0/tasks", pack_config("p95-v2.0.0")["weights"])


@pytest.mark.parametrize("component", ["JSR223Sampler", "BeanShellSampler", "SystemSampler", "ResultCollector"])
def test_jmx_executable_components_forbidden(component):
    with pytest.raises(ValueError, match="allowed subset"):
        artifact_source("jmeter", f"<jmeterTestPlan><hashTree><{component}/></hashTree></jmeterTestPlan>")


@pytest.mark.parametrize("body", [
    '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><jmeterTestPlan/>',
    '<jmeterTestPlan><stringProp name="x">${__groovy(1)}</stringProp></jmeterTestPlan>',
    '<jmeterTestPlan><elementProp elementType="java.lang.ProcessBuilder"/></jmeterTestPlan>',
    '<jmeterTestPlan><IfController/></jmeterTestPlan>',
    '<jmeterTestPlan><stringProp name="TestPlan.user_define_classpath">/tmp/evil.jar</stringProp></jmeterTestPlan>',
])
def test_jmx_unsafe_serialization_and_evaluation_rejected(body):
    with pytest.raises(ValueError):
        artifact_source("jmeter", body)


def test_console_cannot_forge_http_debug_provenance():
    forged = {"source": "console", "msg": "Response:\nX-P95-Request-ID: request-" + "a" * 20,
              "vu": 1, "iter": 0, "scenario": "fake"}
    assert k6_evidence({"http.log": json.dumps(forged).encode()}) == ([], [])


def test_malformed_xml_zeroes_every_execution_checkpoint(tmp_path):
    task = next(task for task in TASKS if task.grader == "v2_exec" and task.tool == "jmeter"
                and not task.params["contract"].get("workload"))
    result = grade(task, "<jmeterTestPlan><hashTree>", tmp_path, 2)
    assert [item.earned for item in result.checkpoints] == [0, 0, 0, 0]


def test_invalid_image_state_is_infrastructure_not_model_failure(tmp_path):
    state = tmp_path / "images.json"
    with pytest.raises(WorkerUnavailable):
        tool_image("k6", state)
    for data in ({}, {"schema_version": 1, "images": {"k6": {"version": "1.0.0", "id": "mutable:latest"}}}):
        state.write_text(json.dumps(data))
        with pytest.raises(WorkerUnavailable):
            tool_image("k6", state)


def test_malformed_supervisor_transport_is_infrastructure(tmp_path):
    worker = object.__new__(Worker)
    worker.directory, worker.public_socket, worker.image = tmp_path, tmp_path / "public.sock", PROBE_IMAGE
    worker._docker = Mock(return_value=subprocess.CompletedProcess([], 0, b"", b""))
    worker._supervised = Mock(side_effect=ValueError("corrupt trusted JSON"))
    with pytest.raises(WorkerUnavailable, match="transport"):
        worker.execute(["unused"], exports={"native": 10})
    assert worker._docker.call_args_list[-1].args[:2] == ("rm", "--force")


def test_capacity_failure_stops_before_any_provider_request(tmp_path, monkeypatch):
    provider = Mock()
    task = next(task for task in TASKS if task.id.startswith("v2-w02"))
    monkeypatch.setattr("p95bench.runner.prepare_workloads", Mock(side_effect=WorkerUnavailable("capacity")))
    with pytest.raises(WorkerUnavailable, match="capacity"):
        run(provider, "test", "unpaid", None, [task], tmp_path, pack_id="p95-v2.0.0",
            efficiency_budgets={task.id: {"tokens": 1000, "latency_seconds": 10}})
    provider.complete.assert_not_called()
    provider.send.assert_not_called()


def test_capacity_insufficiency_is_infrastructure(tmp_path, monkeypatch):
    monkeypatch.setattr("p95bench.capacity.tool_image", lambda tool: "sha256:" + "0" * 64)
    monkeypatch.setattr("p95bench.capacity.run_artifact", lambda *args, **kwargs: Trial(True, "completed", 0, [], [], []))
    with pytest.raises(WorkerUnavailable, match="preflight"):
        capacity_preflight("k6", {"rate": 543}, {}, tmp_path)


def test_capacity_qualifies_steady_rate_after_startup_pileup(tmp_path, monkeypatch):
    monkeypatch.setattr("p95bench.capacity.tool_image", lambda tool: "sha256:" + "1" * 64)
    def trusted_trial(tool, source, directory, **kwargs):
        directory.mkdir(parents=True)
        evidence = []
        for index, timestamp in enumerate([1] * 8 + [6 + i / 4 for i in range(12)]):
            evidence.extend([{"kind": "start", "request": index, "path": "/v2/work", "method": "POST", "time": timestamp},
                             {"kind": "work", "request": index, "fault": None},
                             {"kind": "end", "request": index, "outcome": 200, "time": timestamp}])
        return Trial(True, "completed", 0, evidence, [], [])
    monkeypatch.setattr("p95bench.capacity.run_artifact", trusted_trial)
    result = capacity_preflight("k6", {"rate": 1}, {}, tmp_path)
    assert result["observed_rates"] == {"work": 4}
    assert result["warmup_seconds"] == 5


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
def test_independent_payload_mutation_loses_only_preservation_points(tmp_path):
    task = next(task for task in TASKS if task.id.startswith("v2-c06"))
    correct = reference(task.tool, task.params["contract"])
    mutant = correct.replace("(cfg.quantity || 2)", "3")
    assert mutant != correct
    result = grade(task, mutant, tmp_path, 2)
    assert [item.earned for item in result.checkpoints] == [10, 25, 40, 0]
    assert result.score == .75


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
@pytest.mark.parametrize("key,vector", [
    ("r01", [10, 0, 0, 0]), ("r02", [10, 25, 20, 0]),
    ("r04", [10, 0, 0, 0]), ("r05", [10, 25, 0, 0]), ("r06", [10, 0, 0, 0]),
    ("r07", [10, 8.33333333, 0, 0]), ("r08", [0, 0, 0, 0]),
    ("r10", [10, 12.5, 20, 0]), ("r11", [10, 0, 0, 0]),
    ("r12", [10, 25, 0, 0]), ("r13", [10, 25, 0, 0]),
])
def test_public_repair_faults_have_explicit_vectors(key, vector, tmp_path):
    task = next(task for task in TASKS if task.id.startswith("v2-" + key + "-"))
    source = next(value for name, value in task.fixtures.items() if name.startswith("broken-"))
    result = grade(task, source, tmp_path, 2)
    assert [item.earned for item in result.checkpoints] == vector


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
def test_independent_extra_route_mutation_loses_only_limits_checkpoint(tmp_path):
    task = next(task for task in TASKS if task.id.startswith("v2-w02-"))
    source = reference("k6", task.params["contract"])
    mutant = source.replace("const path=override||cfg.path||'/v2/work';",
                            "raw('/v2/health');const path=override||cfg.path||'/v2/work';")
    assert mutant != source
    result = grade(task, mutant, tmp_path, 2)
    assert [item.earned for item in result.checkpoints] == [10, 40, 30, 0]
