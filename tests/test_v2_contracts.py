import base64
import json
import os
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from p95bench.budget import BudgetLedger
from p95bench.capacity import capacity_preflight, capacity_rate
from p95bench.checkpoint_graders import executable
from p95bench.demo_v2 import Profile, Shop
from p95bench.execution import AuditOverflow, Trial, artifact_source, k6_evidence, run_artifact
from p95bench.graders import grade
from p95bench.images import tool_image
from p95bench.packs import pack_config
from p95bench.providers import Price, Response
from p95bench.reference_plans import reference
from p95bench.runner import run
from p95bench.tasks import load_tasks
from p95bench.worker import PROBE_IMAGE, Execution, Worker, WorkerUnavailable, execution_trial

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


@pytest.mark.parametrize("kind", ["doubleProp", "FloatProperty"])
@pytest.mark.parametrize("value", ["300.0", "60.0", "-1.25", "0.0"])
def test_jmx_native_floating_properties_preserved(kind, value):
    source = (f'<jmeterTestPlan><{kind}><name>throughput</name><value>{value}</value>'
              f'<savedValue>0.0</savedValue></{kind}></jmeterTestPlan>')
    assert artifact_source("jmeter", source) == source


@pytest.mark.parametrize("kind", ["doubleProp", "FloatProperty"])
@pytest.mark.parametrize("field", ["value", "savedValue"])
@pytest.mark.parametrize("number", ["1_000", "1.0_0", "١.٢", "\u00a01.0"])
def test_jmx_native_numeric_fields_reject_non_java_literals(kind, field, number):
    values = {"value": "1", "savedValue": "0", field: number}
    source = (f'<jmeterTestPlan><{kind}><name>n</name><value>{values["value"]}</value>'
              f'<savedValue>{values["savedValue"]}</savedValue></{kind}></jmeterTestPlan>')
    with pytest.raises(ValueError, match="Invalid JMX numeric property"):
        artifact_source("jmeter", source)


@pytest.mark.parametrize("field", ["value", "savedValue"])
@pytest.mark.parametrize("number", ["1e39", "-1e39", "3.4028236e38", "0x1p128"])
def test_jmx_float32_properties_reject_overflow(field, number):
    values = {"value": "1", "savedValue": "0", field: number}
    source = (f'<jmeterTestPlan><FloatProperty><name>n</name><value>{values["value"]}</value>'
              f'<savedValue>{values["savedValue"]}</savedValue></FloatProperty></jmeterTestPlan>')
    with pytest.raises(ValueError, match="Invalid JMX numeric property"):
        artifact_source("jmeter", source)


@pytest.mark.parametrize("number", ["3.4028235e38", "-3.4028235e38", "0x1.fffffep127",
                                    "1e-50", "+1.25f", "1e2D", " \t1.25\n"])
def test_jmx_finite_float32_properties_preserved(number):
    source = (f'<jmeterTestPlan><FloatProperty><name>n</name><value>{number}</value>'
              '<savedValue>0</savedValue></FloatProperty></jmeterTestPlan>')
    assert artifact_source("jmeter", source) == source


def test_jmx_double_property_allows_values_beyond_float32_range():
    source = ('<jmeterTestPlan><doubleProp><name>n</name><value>1e39</value>'
              '<savedValue>0</savedValue></doubleProp></jmeterTestPlan>')
    assert artifact_source("jmeter", source) == source


@pytest.mark.parametrize("kind,value", [("intProp", "7"), ("longProp", "2147483648")])
def test_jmx_native_integer_properties_preserved(kind, value):
    source = f'<jmeterTestPlan><{kind} name="n">{value}</{kind}></jmeterTestPlan>'
    assert artifact_source("jmeter", source) == source


@pytest.mark.parametrize("body", [
    '<doubleProp><name>x</name><value>1</value><savedValue>0</savedValue><unknown/></doubleProp>',
    '<doubleProp><name>x</name><value>1</value><value>2</value></doubleProp>',
    '<doubleProp><name>x</name><value>1</value></doubleProp>',
    '<doubleProp name="other"><name>x</name><value>1</value><savedValue>0</savedValue></doubleProp>',
    '<doubleProp><name>x</name><value class="evil">1</value><savedValue>0</savedValue></doubleProp>',
    '<doubleProp><name>x</name><value><stringProp>1</stringProp></value><savedValue>0</savedValue></doubleProp>',
    '<doubleProp><name>x</name><value>${__groovy(1)}</value><savedValue>0</savedValue></doubleProp>',
    '<doubleProp><name>x</name><value>NaN</value><savedValue>0</savedValue></doubleProp>',
    '<doubleProp><name>x</name><value>1</value><savedValue>inf</savedValue></doubleProp>',
    '<doubleProp><name/><value>1</value><savedValue>0</savedValue></doubleProp>',
    '<intProp><name>x</name><value>1</value><savedValue>0</savedValue></intProp>',
    '<longProp><name>x</name><value>1</value><savedValue>0</savedValue></longProp>',
    '<intProp name="n"><stringProp name="x">1</stringProp></intProp>',
    '<longProp name="n"><ResponseAssertion/></longProp>',
    '<FloatProperty><name>x</name><value>1</value><savedValue>0</savedValue><unknown/></FloatProperty>',
    '<FloatProperty name="x">1</FloatProperty>',
    '<floatProp><name>x</name><value>1</value><savedValue>0</savedValue></floatProp>',
    '<stringProp name="x"><name>x</name><value>1</value><savedValue>0</savedValue></stringProp>',
    '<name>x</name>', '<value>1</value>', '<savedValue>0</savedValue>',
    '<doubleProp><name>HTTPSampler.implementation</name><value>1</value><savedValue>0</savedValue></doubleProp>',
])
def test_jmx_numeric_children_are_contextual_and_strict(body):
    with pytest.raises(ValueError):
        artifact_source("jmeter", f'<jmeterTestPlan>{body}</jmeterTestPlan>')


@pytest.mark.skipif(not os.environ.get("JMETER_HOME"), reason="Set JMETER_HOME for native SaveService test")
def test_jmx_native_saveservice_numeric_roundtrip():
    home = Path(os.environ["JMETER_HOME"])
    result = subprocess.run(
        ["java", "--class-path", f"{home}/lib/*{os.pathsep}{home}/lib/ext/*",
         str(Path(__file__).with_name("NativeNumericProperties.java")), str(home)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    serialized = [line.split("\t") for line in result.stdout.splitlines() if line.startswith("NATIVE\t")]
    assert len(serialized) == 4
    for _, encoded in serialized:
        element = base64.b64decode(encoded).decode().partition("?>")[2].lstrip()
        source = '<jmeterTestPlan>' + element + '</jmeterTestPlan>'
        assert artifact_source("jmeter", source) == source


def test_k6_scenario_artifact_preserves_source_verbatim():
    source = "export const options = { scenarios: {} };\noptions.iterations=1;"
    assert artifact_source("k6", source) == source


def test_console_cannot_forge_http_debug_provenance():
    forged = {"source": "console", "msg": "Response:\nX-P95-Request-ID: request-" + "a" * 20,
              "vu": 1, "iter": 0, "scenario": "fake"}
    assert k6_evidence({"http.log": json.dumps(forged).encode()}) == ([], [])


def test_malformed_xml_zeroes_every_execution_checkpoint(tmp_path):
    task = next(task for task in TASKS if task.grader == "v2_exec" and task.tool == "jmeter"
                and not task.params["contract"].get("workload"))
    result = grade(task, "<jmeterTestPlan><hashTree>", tmp_path, 2)
    assert [item.earned for item in result.checkpoints] == [0, 0, 0, 0]


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
def test_repair_round_reruns_reference_gatling_answer(tmp_path):
    tasks = load_tasks(ROOT / "packs/p95-matrix/tasks", pack_config("p95-matrix")["weights"])
    task = next(item for item in tasks if item.tool == "gatling" and item.category == "correlation"
                and not item.params["contract"].get("workload"))
    correct = reference(task.tool, task.params["contract"])

    class StubProvider:
        def __init__(self):
            self.calls = 0

        def send(self, model, messages):
            self.calls += 1
            text = "<broken Gatling artifact>" if self.calls == 1 else correct
            return Response(text, 100, 20, 0, .01)

    provider = StubProvider()
    directory, summary = run(
        provider, "stub", "stub-model", Price(1, .5, 2), [task], tmp_path,
        pack_id="p95-matrix",
        efficiency_budgets={task.id: {"tokens": 16384, "latency_seconds": 30}},
        budget_ledger=BudgetLedger(tmp_path / "repair-docker-ledger.json"),
    )
    attempt = json.loads((directory / "attempts.jsonl").read_text())

    assert provider.calls == 2
    assert attempt["grade"]["score"] == 0
    assert attempt["repair"]["triggered"]
    assert attempt["repair"]["grade"]["score"] == 1
    assert summary["after_repair"]["category_scores"][task.category] == 100


@pytest.mark.parametrize("error", [OverflowError, RecursionError])
def test_native_model_evidence_conversion_errors_are_graded_zero(error, monkeypatch, tmp_path):
    task = next(task for task in TASKS if task.grader == "v2_exec" and not task.params["contract"].get("workload"))
    monkeypatch.setattr("p95bench.checkpoint_graders.run_artifact", Mock(side_effect=error("invalid model evidence")))
    result = grade(task, "model artifact", tmp_path, 2)
    assert result.score == 0 and all(item.earned == 0 for item in result.checkpoints)
    assert result.detail == f"Invalid model artifact ({error.__name__}): invalid model evidence"


@pytest.mark.parametrize("overflow", [False, True])
def test_execution_audit_overflow_preserves_bounded_native_evidence(overflow, monkeypatch, tmp_path):
    shop = Shop(Profile(), "synthetic")
    shop.audit_overflow = overflow
    worker = Mock()
    artifacts = {"http.log": b'{"source":"console","msg":"native diagnostic"}\n', "points.json": b""}
    worker.execute.return_value = Execution("completed", 0, "native output", 1, artifacts,
                                             {"optional": "missing"})
    monkeypatch.setattr("p95bench.execution.tool_image", lambda tool: PROBE_IMAGE)
    monkeypatch.setattr("p95bench.execution.execution_trial", lambda *a, **k: nullcontext((shop, worker)))
    if overflow:
        with pytest.raises(AuditOverflow, match="script exceeded the request evidence limit"):
            run_artifact("k6", "export default function() {}", tmp_path)
    else:
        trial = run_artifact("k6", "export default function() {}", tmp_path)
        assert trial.events == [] and not trial.loaded
        assert json.loads((tmp_path / "audit.json").read_text()) == []
    worker.execute.assert_called_once()
    options = worker.execute.call_args.kwargs
    assert options["output_limit"] == 1048576
    assert options["exports"] == {"points.json": 8 * 1048576, "http.log": 8 * 1048576}
    assert all((tmp_path / name).read_bytes() == value for name, value in artifacts.items())
    metadata = json.loads((tmp_path / "execution.json").read_text())
    assert metadata["output"] == "native output"
    assert metadata["status"] == "completed" and metadata["returncode"] == 0
    assert metadata["artifact_errors"] == {"optional": "missing"}
    assert metadata["audit_overflow"] is overflow
    assert json.loads((tmp_path / "audit.json").read_text()) == []


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in native Docker tests")
def test_native_audit_overflow_retains_engine_output(monkeypatch, tmp_path):
    @contextmanager
    def overflow_trial(*args, **kwargs):
        with execution_trial(*args, **kwargs) as (shop, worker):
            shop.audit_overflow = True
            yield shop, worker
    monkeypatch.setattr("p95bench.execution.execution_trial", overflow_trial)
    with pytest.raises(AuditOverflow, match="script exceeded"):
        run_artifact("k6", "export default function() { console.log('native diagnostic'); }", tmp_path)
    metadata = json.loads((tmp_path / "execution.json").read_text())
    assert metadata["audit_overflow"] is True
    assert metadata["status"] == "completed" and metadata["returncode"] == 0
    assert "native diagnostic" in (tmp_path / "http.log").read_text()
    for name in ("points.json", "http.log"):
        assert (tmp_path / name).stat().st_size <= 8 * 1048576
    assert len(metadata["output"].encode()) <= 3 * 1048576


def test_execution_nonoverflow_audit_error_is_infrastructure(monkeypatch, tmp_path):
    shop, worker = Mock(audit_overflow=False), Mock()
    shop.audit.side_effect = RuntimeError("Demo requests did not drain; audit is not final")
    worker.execute.return_value = Execution("completed", 0, "", 1)
    monkeypatch.setattr("p95bench.execution.tool_image", lambda tool: PROBE_IMAGE)
    monkeypatch.setattr("p95bench.execution.execution_trial", lambda *a, **k: nullcontext((shop, worker)))
    with pytest.raises(WorkerUnavailable, match="requests did not drain"):
        run_artifact("k6", "export default function() {}", tmp_path)


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


@pytest.mark.parametrize("rate,completion,fail_every,expected", [
    (8, False, 5, 26), (8, True, 0, 26), (8, True, 5, 32),
])
def test_capacity_rate_accounts_for_scheduled_failed_completions(
    rate, completion, fail_every, expected
):
    assert capacity_rate(rate, completion, fail_every) == expected


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


@pytest.mark.parametrize("tool,startup,passes", [
    ("gatling", 15, True), ("gatling", 60, True), ("gatling", 60.1, False),
    ("jmeter", 10, True), ("k6", 10, True), ("locust", 10, True),
    ("jmeter", 10.1, False), ("k6", 10.1, False), ("locust", 10.1, False),
])
@pytest.mark.parametrize("fault", [None, "empty", "rate", "loaded", "timeout", "exit"])
def test_capacity_startup_headroom_preserves_gates(tmp_path, monkeypatch, tool, startup, passes, fault):
    monkeypatch.setattr("p95bench.capacity.QUALIFIED", {})
    monkeypatch.setattr("p95bench.capacity.tool_image", lambda tool: "sha256:" + "2" * 64)
    configs = []
    monkeypatch.setattr("p95bench.capacity.reference", lambda tool, config: configs.append(config) or "trusted")

    def trusted_trial(tool, source, directory, **kwargs):
        assert kwargs["timeout"] == (90 if tool == "gatling" else 40)
        directory.mkdir(parents=True)
        evidence = []
        rate = 2 if fault == "rate" else 4
        for index in range(0 if fault == "empty" else 12 * rate):
            timestamp = 100 + startup + index / rate
            evidence.extend([
                {"kind": "start", "request": index, "path": "/v2/work", "method": "POST", "time": timestamp},
                {"kind": "work", "request": index, "fault": None},
                {"kind": "end", "request": index, "outcome": 200, "time": timestamp},
            ])
        return Trial(fault != "loaded", "timeout" if fault == "timeout" else "completed",
                     1 if fault == "exit" else 0, evidence, [], [], 100)

    monkeypatch.setattr("p95bench.capacity.run_artifact", trusted_trial)
    if passes and fault is None:
        result = capacity_preflight(tool, {"rate": 1}, {}, tmp_path)
        assert result["observed_rates"] == {"work": 4}
        assert result["startup_seconds"] == pytest.approx({"work": startup})
        assert result["startup_allowance_seconds"] == (60 if tool == "gatling" else 10)
        assert (result["duration_seconds"], result["warmup_seconds"], result["measurement_seconds"]) == (12, 5, 3)
    else:
        with pytest.raises(WorkerUnavailable, match="Capacity preflight failed"):
            capacity_preflight(tool, {"rate": 1}, {}, tmp_path)
    assert configs[0]["duration"] == 12
    assert configs[0]["rate"] == 4


@pytest.mark.parametrize("tool", ["gatling", "jmeter", "k6", "locust"])
@pytest.mark.parametrize("workload", [True, False])
def test_only_gatling_workloads_gain_grading_startup_headroom(tmp_path, monkeypatch, tool, workload):
    trial = Trial(True, "completed", 0, [], [], [])
    engine = Mock(return_value=trial)
    fractions = Mock(return_value={"loads": 1})
    monkeypatch.setattr("p95bench.checkpoint_graders.capacity_preflight", Mock())
    monkeypatch.setattr("p95bench.checkpoint_graders.run_artifact", engine)
    monkeypatch.setattr("p95bench.checkpoint_graders.workload_fractions", fractions)
    monkeypatch.setattr("p95bench.checkpoint_graders.flow_fractions", fractions)
    contract = {"workload": workload}
    params = {"tool": tool, "contract": contract, "trials": [{"profile": {}}],
              "rubric": [{"id": "loads", "points": 100}]}
    assert executable("trusted", params, tmp_path, {}).score == 1
    assert engine.call_args.kwargs["timeout"] == (110 if tool == "gatling" and workload else 60)
    if workload:
        fractions.assert_called_once_with(trial, {**contract, "tool": tool})


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
