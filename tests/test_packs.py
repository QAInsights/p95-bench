import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from p95bench.budget import BudgetLedger
from p95bench.cli import main
from p95bench.execution import AuditOverflow
from p95bench.graders import CHECKPOINT_GRADERS, GRADERS, Grade
from p95bench.grades import Checkpoint, CheckpointGrade
from p95bench.packs import efficiency, pack_config, snapshot, summarize_v2
from p95bench.providers import (
    PROVIDERS,
    CacheWritePrice,
    DeepSeekResponse,
    OpenAICompatible,
    Price,
    ProviderConfig,
    Response,
    SpendLimitExceeded,
)
from p95bench.runner import (
    _grade_response,
    _repair_trigger,
    harness_revision,
    run,
    score_run,
    summarize,
)
from p95bench.tasks import CATEGORIES, Task, load_tasks
from p95bench.worker import WorkerUnavailable

ROOT = Path(__file__).resolve().parents[1]
V2 = "p95-v2.0.0"


def task(category="repair"):
    return Task(category, category, "none", "Synthetic test input", "test_checkpoint", {}, {})


def result(points=80, detail=""):
    return CheckpointGrade((Checkpoint("behavior", points, 100),), detail)


@pytest.mark.parametrize("points", [0, 1.25, 71, 100])
def test_checkpoint_roundtrip(points):
    grade = result(points)
    assert grade.score == points / 100
    assert grade.passed is (points == 100)
    assert CheckpointGrade.from_dict(grade.to_dict()) == grade


def test_checkpoint_multiple_and_invalid():
    grade = CheckpointGrade([Checkpoint("load", 10, 10), Checkpoint("execute", 45, 90)])
    assert grade.to_dict()["points_earned"] == 55
    for values in [("", 0, 100), ("a", -1, 100), ("a", 101, 100),
                   ("a", 0, 0), ("a", float("nan"), 100), ("a", 0, float("inf"))]:
        with pytest.raises(ValueError):
            Checkpoint(*values)
    for checkpoints in [(), (Checkpoint("a", 0, 99),), (Checkpoint("a", 0, 100.0000000001),),
                        (Checkpoint("a", 10, 50), Checkpoint("a", 20, 50))]:
        with pytest.raises(ValueError):
            CheckpointGrade(checkpoints)
    for value in [{}, {**grade.to_dict(), "score": 1},
                  {**grade.to_dict(), "checkpoints": []}]:
        with pytest.raises(ValueError):
            CheckpointGrade.from_dict(value)


def test_efficiency_quality_gate_and_cache():
    response = {"input_tokens": 50, "output_tokens": 150,
                "cached_tokens": 50, "response_latency_seconds": 2}
    budget = {"tokens": 100, "latency_seconds": 4}
    assert efficiency(.8, response, budget) == pytest.approx(.7)
    assert efficiency(0, response, budget) == 0
    assert efficiency(1, response, {"tokens": 200, "latency_seconds": 2}) == 1
    assert efficiency(1, {**response, "input_tokens": 0, "output_tokens": 0,
                          "response_latency_seconds": 0}, budget) == 1
    for invalid in [{}, {**response, "output_tokens": -1},
                    {**response, "response_latency_seconds": float("nan")}]:
        with pytest.raises(ValueError):
            efficiency(.5, invalid, budget)
    with pytest.raises(ValueError):
        efficiency(.5, response, {"tokens": 0, "latency_seconds": 1})


@pytest.mark.parametrize("multiple,expected", [(0, 1), (.5, 1), (1, 1), (2, .5),
                                             (3, 0), (4, 0)])
@pytest.mark.parametrize("component", ["output_tokens", "response_latency_seconds", "both"])
def test_efficiency_linear_component_curve(multiple, expected, component):
    response = {"input_tokens": 1000, "output_tokens": 100, "response_latency_seconds": 10}
    budget = {"tokens": 100, "latency_seconds": 10}
    if component in ("output_tokens", "both"):
        response["output_tokens"] *= multiple
    if component in ("response_latency_seconds", "both"):
        response["response_latency_seconds"] *= multiple
    assert efficiency(1, response, budget) == pytest.approx(
        expected if component == "both" else .5 + .5 * expected)


def test_efficiency_independent_of_input_and_cached_tokens():
    response = {"input_tokens": 0, "output_tokens": 200, "response_latency_seconds": 20}
    budget = {"tokens": 100, "latency_seconds": 10}
    for input_tokens, cached_tokens in [(0, 0), (100, 0), (10000, 10000)]:
        assert efficiency(1, {**response, "input_tokens": input_tokens,
                              "cached_tokens": cached_tokens}, budget) == .5


def test_pack_defaults_validation():
    assert pack_config(V2)["default_repeats"] == 1
    assert pack_config(V2)["efficiency_weight"] == .05
    for repeat in [0, -1, 1.5, True]:
        with pytest.raises(ValueError, match="positive"):
            snapshot(V2, [task()], repeat)
    with pytest.raises(ValueError, match="Unknown pack"):
        pack_config("future")
    with pytest.raises(ValueError, match="unique"):
        snapshot(V2, [task(), task()])
    with pytest.raises(ValueError, match="category"):
        snapshot(V2, [replace(task(), category="concepts")])
    with pytest.raises(ValueError, match="budgets"):
        snapshot(V2, [task()])
    with pytest.raises(ValueError, match="positive"):
        snapshot(V2, [task()], budgets={"repair": {"tokens": -1, "latency_seconds": 1}})
    tasks = [task("repair"), task("analysis")]
    budgets = {t.id: {"tokens": 1000, "latency_seconds": 10} for t in tasks}
    original = snapshot(V2, tasks, budgets=budgets)
    assert original == snapshot(V2, tasks[::-1], budgets=budgets)
    changed = [replace(tasks[0], prompt="Different synthetic input"), tasks[1]]
    assert original["digest"] != snapshot(V2, changed, budgets=budgets)["digest"]


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def send(self, model, messages):
        self.calls += 1
        return Response("Synthetic response", 100, 20, 80, 2)


class RepairProvider:
    def __init__(self):
        self.calls = 0
        self.messages = []

    def send(self, model, messages):
        self.messages.append(messages)
        text = "broken artifact" if self.calls == 0 else "corrected artifact" if self.calls == 1 else "answer"
        response = Response(text, 100, 20, 0, 2)
        if self.calls == 0:
            response.reasoning_content = "private reasoning"
        self.calls += 1
        return response


def run_v2(tmp_path, monkeypatch, repeats=1, points=80, price=None):
    price = Price(1, .5, 2) if price is None else price
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint", lambda *args: result(points))
    tasks = [task(c) for c in pack_config(V2)["weights"]]
    budgets = {t.id: {"tokens": 60, "latency_seconds": 2} for t in tasks}
    provider = FakeProvider()
    directory, summary = run(provider, "fake", "model", price, tasks, tmp_path,
                             repeats, pack_id=V2, efficiency_budgets=budgets,
                             budget_ledger=BudgetLedger(tmp_path / "ledger.json"))
    return provider, directory, summary


@pytest.mark.parametrize("v2", [False, True])
@pytest.mark.parametrize("error", [AuditOverflow])
def test_script_audit_overflow_saves_graded_zero_and_continues(v2, error, tmp_path, monkeypatch):
    rubric = [{"id": "loads", "points": 10}, {"id": "setup", "points": 30},
              {"id": "behavior", "points": 40}, {"id": "safety", "points": 20}]
    tasks = [Task(name, "analysis", "none", "Synthetic task", "test_checkpoint", {"rubric": rubric}, {})
             for name in ("first", "second")]
    successful = result(100) if v2 else Grade(True, "ok")
    monkeypatch.setitem(CHECKPOINT_GRADERS if v2 else GRADERS, "test_checkpoint",
                        Mock(side_effect=[error("grader failed"), successful]))
    provider, price = FakeProvider(), Price(1, .5, 2)
    ledger = BudgetLedger(tmp_path / "ledger.json")
    options = {"pack_id": V2, "budget_ledger": ledger, "efficiency_budgets": {
        task.id: {"tokens": 100, "latency_seconds": 10} for task in tasks}} if v2 else {}
    directory, summary = run(provider, "fake", "model", price, tasks, tmp_path, **options)
    attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()]
    assert provider.calls == len(attempts) == 2
    assert attempts[0]["response"]["text"] == "Synthetic response"
    assert attempts[0]["grading_error"] == {"type": error.__name__, "message": "grader failed"}
    assert attempts[0]["grade"]["detail"] == f"Grader error ({error.__name__}): grader failed"
    assert not attempts[0]["grade"]["passed"] and attempts[1]["grade"]["passed"]
    assert "grading_error" not in attempts[1]
    assert summary["category_scores"]["analysis"] == 50
    assert summary["overall_score"] == (7.5 if v2 else 50)
    assert summary["estimated_cost_usd"] == pytest.approx(sum(a["cost_usd"] for a in attempts))
    assert summary == score_run(directory)
    if v2:
        assert [c["earned"] for c in attempts[0]["grade"]["checkpoints"]] == [0, 0, 0, 0]
        assert attempts[0]["grade"]["status"] == "graded"
        assert CheckpointGrade.from_dict(attempts[0]["grade"]).score == 0
        charges = json.loads(ledger.path.read_text())["charges"]
        assert set(charges) == {attempt["budget_reservation"] for attempt in attempts}
        assert not (directory / "not-run.jsonl").exists()


@pytest.mark.parametrize("v2", [False, True])
@pytest.mark.parametrize("error", [WorkerUnavailable("Docker cleanup failed"),
    OSError("Docker unavailable"), subprocess.CalledProcessError(1, "docker"),
    subprocess.TimeoutExpired("docker", 1)])
def test_grading_infrastructure_preserves_response_without_scoring_or_more_calls(v2, error, tmp_path, monkeypatch):
    tasks = [replace(task("analysis"), id=name) for name in ("first", "second")]
    monkeypatch.setitem(CHECKPOINT_GRADERS if v2 else GRADERS, "test_checkpoint",
                        Mock(side_effect=error))
    provider, price = FakeProvider(), Price(1, .5, 2)
    ledger = BudgetLedger(tmp_path / "ledger.json")
    options = {"pack_id": V2, "budget_ledger": ledger, "efficiency_budgets": {
        task.id: {"tokens": 100, "latency_seconds": 10} for task in tasks}} if v2 else {}
    directory, summary = run(provider, "fake", "model", price, tasks, tmp_path, repeats=2, **options)
    entries = [json.loads(line) for line in (directory / "not-run.jsonl").read_text().splitlines()]
    assert provider.calls == 1
    assert summary["attempts"] == 0 and summary["not_run"] == len(entries) == 4
    assert summary["passed"] == 0
    assert (directory / "attempts.jsonl").read_text() == ""
    assert all(entry["status"] == "not_run" for entry in entries)
    assert entries[0]["response"]["text"] == "Synthetic response"
    assert entries[0]["grading_error"]["type"] == type(error).__name__
    assert entries[0]["grade"]["status"] == "not_run"
    assert all("response" not in entry for entry in entries[1:])
    assert summary["estimated_cost_usd"] == entries[0]["cost_usd"] > 0
    assert summary == score_run(directory)
    if v2:
        assert not summary["complete"]
        assert sum(summary["cost_by_tool"].values()) == summary["estimated_cost_usd"]
        assert list(json.loads(ledger.path.read_text())["charges"]) == [entries[0]["budget_reservation"]]


@pytest.mark.parametrize("repeats", [1, 3])
def test_v2_run_and_offline_rescore(tmp_path, monkeypatch, repeats):
    provider, directory, summary = run_v2(tmp_path, monkeypatch, repeats)
    assert directory.parent.name == V2
    assert provider.calls == 5 * repeats
    assert summary["pack_id"] == V2 and summary["complete"]
    assert summary["quality_score"] == 80
    assert summary["efficiency_score"] == pytest.approx(80)
    assert summary["overall_score"] == pytest.approx(80)
    assert summary == score_run(directory)
    manifest = json.loads((directory / "pack.json").read_text())
    attempts = [json.loads(l) for l in (directory / "attempts.jsonl").read_text().splitlines()]
    for mutation in [[attempts[0], attempts[0]], [{**attempts[0], "pack_id": "p95-v1"}],
                     [{**attempts[0], "repeat": -1}],
                     [{**attempts[0], "category": "concepts"}]]:
        with pytest.raises(ValueError):
            summarize_v2(mutation, manifest)
    missing = summarize_v2(attempts[:-1], manifest)
    assert not missing["complete"] and missing["overall_score"] < summary["overall_score"]
    empty = summarize_v2([], manifest)
    assert not empty["complete"] and empty["overall_score"] == 0
    manifest["repeats"] = 99
    (directory / "pack.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="digest"):
        score_run(directory)


def test_repair_round_is_bounded_private_and_scored_separately(tmp_path, monkeypatch):
    tasks = [task(category) for category in pack_config(V2)["weights"]]
    tasks[0] = replace(
        tasks[0],
        params={"rubric": [{"id": "loads", "points": 100}], "expected_answer": "fixture-answer"},
        fixtures={"accounts.json": json.dumps({"password": "fixture-secret-123"})},
    )
    invalid = ("Invalid model artifact (ValueError): fixture-secret-123 " + "é" * 3000)
    grades = iter([result(0, invalid), result(100), *[result(50, "Loaded") for _ in range(4)]])
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint", lambda *args: next(grades))
    provider = RepairProvider()
    ledger = BudgetLedger(tmp_path / "repair-ledger.json")
    budgets = {item.id: {"tokens": 60, "latency_seconds": 2} for item in tasks}
    directory, summary = run(provider, "fake", "model", Price(1, .5, 2), tasks, tmp_path,
                             pack_id=V2, efficiency_budgets=budgets, budget_ledger=ledger)
    attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()]
    first, repair = attempts[0], attempts[0]["repair"]

    assert provider.calls == 6
    assert repair["triggered"] and repair["reason"] == "invalid_model_artifact"
    assert first["grade"]["detail"].startswith("Invalid model artifact")
    assert repair["grade"]["passed"] and repair["response"]["text"] == "corrected artifact"
    assert repair["messages_appended"][0] == {
        "role": "assistant", "content": "broken artifact",
    }
    assert repair["messages_appended"][1]["content"] == (
        "Your previous answer failed before it could be scored:\n\n"
        f"{repair['feedback']}\n\n"
        "Return the complete corrected answer under the same output contract.")
    assert provider.messages[1] == first["messages"] + repair["messages_appended"]
    assert repair["feedback"].startswith("Invalid model artifact")
    assert len(repair["feedback"].encode()) <= 4096
    repair["feedback"].encode().decode("utf-8")
    assert "fixture-secret-123" not in repair["feedback"]
    assert "fixture-answer" not in repair["feedback"]
    assert "rubric" not in repair["feedback"]
    assert "100" not in repair["feedback"]
    assert "private reasoning" not in json.dumps(repair["messages_appended"])
    assert "reasoning_content" not in json.dumps(repair["messages_appended"])
    assert len(json.loads(ledger.path.read_text())["charges"]) == 6
    assert summary["repairs_triggered"] == summary["repairs_succeeded"] == 1
    assert sum(summary["cost_by_tool"].values()) == pytest.approx(summary["estimated_cost_usd"])
    assert summary["after_repair"]["quality_score"] == 62.5
    assert summary["after_repair"]["efficiency_score"] == 56.25

    manifest = json.loads((directory / "pack.json").read_text())
    old_attempts = [{key: value for key, value in attempt.items() if key != "repair"}
                    for attempt in attempts]
    old_summary = summarize_v2(old_attempts, manifest)
    for field in ("overall_score", "quality_score", "efficiency_score", "category_scores"):
        assert summary[field] == old_summary[field]
    assert summary == score_run(directory)


def test_loaded_low_score_does_not_trigger_repair(tmp_path, monkeypatch):
    tasks = [task(category) for category in pack_config(V2)["weights"]]
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint",
                        lambda *args: result(1, "Loaded and scored"))
    provider = RepairProvider()
    ledger = BudgetLedger(tmp_path / "no-repair-ledger.json")
    budgets = {item.id: {"tokens": 60, "latency_seconds": 2} for item in tasks}
    directory, summary = run(
        provider, "fake", "model", Price(1, .5, 2), tasks, tmp_path, pack_id=V2,
        efficiency_budgets=budgets, budget_ledger=ledger)
    attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()]

    assert provider.calls == len(tasks)
    assert all(attempt["repair"] == {"triggered": False} for attempt in attempts)
    assert len(json.loads(ledger.path.read_text())["charges"]) == len(tasks)
    assert summary["repairs_triggered"] == summary["repairs_succeeded"] == 0
    assert summary["after_repair"]["overall_score"] == summary["overall_score"]
    assert summary["after_repair"]["quality_score"] == summary["quality_score"]


@pytest.mark.parametrize(("finish_reason", "output_tokens"), [
    ("length", 100), ("stop", 64000),
])
def test_repair_is_skipped_for_truncated_responses(
        finish_reason, output_tokens, tmp_path, monkeypatch):
    task_item = task()
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint",
                        lambda *args: result(0, "Invalid model artifact: broken response"))

    class TruncatedProvider:
        config = PROVIDERS["deepseek"]

        def __init__(self):
            self.calls = 0

        def send(self, model, messages):
            self.calls += 1
            return DeepSeekResponse("broken response", 100, output_tokens, 0, 1,
                                    reasoning_tokens=50, finish_reason=finish_reason)

    provider = TruncatedProvider()
    ledger = BudgetLedger(tmp_path / "truncated-ledger.json")
    directory, _ = run(
        provider, "deepseek", "deepseek-flash", Price(1, .5, 2), [task_item], tmp_path,
        pack_id=V2, efficiency_budgets={
            task_item.id: {"tokens": 100, "latency_seconds": 10}},
        budget_ledger=ledger)
    attempt = json.loads((directory / "attempts.jsonl").read_text())

    assert provider.calls == 1
    assert attempt["repair"] == {"triggered": False, "skipped": "truncated_at_cap"}
    assert len(json.loads(ledger.path.read_text())["charges"]) == 1


@pytest.mark.parametrize('cap', [64000, 32000])
@pytest.mark.parametrize('price', [Price(1, .5, 2), CacheWritePrice(1, .5, 2, 1.25)])
def test_output_cap_is_reserved_for_both_calls(tmp_path, monkeypatch, cap, price):
    task_item = task()
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint",
                        Mock(side_effect=[
                            result(0, "Invalid model artifact: broken response"), result(100)]))
    provider = RepairProvider()
    provider.config = replace(PROVIDERS["deepseek"], model_output_limits={"deepseek-flash": cap})

    class RecordingLedger:
        limit = 100_000_000

        def __init__(self):
            self.reservations = []

        def reserve(self, maximum):
            self.reservations.append(maximum)
            return str(len(self.reservations))

        def settle(self, key, amount):
            return None

    ledger = RecordingLedger()
    directory, _ = run(
        provider, "deepseek", "deepseek-flash", price, [task_item], tmp_path,
        pack_id=V2, efficiency_budgets={
            task_item.id: {"tokens": 100, "latency_seconds": 10}},
        budget_ledger=ledger)
    attempt = json.loads((directory / "attempts.jsonl").read_text())
    repair_messages = attempt["messages"] + attempt["repair"]["messages_appended"]
    metadata = json.loads((directory / "metadata.json").read_text())
    assert metadata["output_cap"] == metadata["max_tokens"] == cap
    retries = provider.config.transport_retries

    def expected_reservation(messages):
        maximum = ((len(json.dumps(messages).encode()) + 4096) * price.input_upper_bound
                   + cap * price.output) / 1_000_000
        return maximum * (1 + retries)

    assert attempt["repair"]["triggered"]
    assert ledger.reservations == pytest.approx([
        expected_reservation(attempt["messages"]),
        expected_reservation(repair_messages),
    ])


def test_zero_loads_from_gatling_compile_failure_is_repairable(tmp_path):
    task = Task("synthetic-gatling", "repair", "gatling", "Synthetic", "v2_exec", {}, {})
    workdir = tmp_path / "task-0-0"
    case = workdir / "case-0"
    case.mkdir(parents=True)
    (case / "execution.json").write_text(json.dumps({
        "status": "completed", "returncode": 1,
        "output": "Gatling compilation error: type mismatch",
        "artifact_errors": {},
    }))
    (case / "audit.json").write_text("[]")
    grade = CheckpointGrade((
        Checkpoint("loads", 0, 10), Checkpoint("setup", 0, 30),
        Checkpoint("behavior", 0, 40), Checkpoint("safety", 0, 20),
    )).to_dict()

    reason, feedback = _repair_trigger(task, grade, workdir)

    assert reason == "gatling_compile_failure"
    assert "Gatling compilation error" in feedback
    assert len(feedback.encode()) <= 4096
    assert _repair_trigger(task, {"status": "not_run", "detail": "Worker unavailable"},
                           workdir) is None


def test_grader_serialization_error_without_rubric_is_a_graded_error(tmp_path, monkeypatch):
    invalid = Mock()
    invalid.to_dict.side_effect = TypeError("invalid result")
    monkeypatch.setattr("p95bench.runner.grade", Mock(return_value=invalid))
    saved, error = _grade_response(task(), "response", tmp_path, True)
    assert CheckpointGrade.from_dict(saved).score == 0
    assert saved["checkpoints"] == [{"id": "grading_error", "earned": 0, "possible": 100}]
    assert error == {"type": "TypeError", "message": "invalid result", "stop_run": True}


@pytest.mark.parametrize("rubric", [[{"id": "behavior", "points": 99}], [], None,
    [{"id": "same", "points": 50}, {"id": "same", "points": 50}], [{"id": "missing_points"}]])
def test_invalid_rubric_saves_minimal_error_grade_and_stops(rubric, tmp_path, monkeypatch):
    tasks = [replace(task("analysis"), id=name, params={"rubric": rubric})
             for name in ("first", "second")]
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint", Mock(side_effect=ValueError("grader failed")))
    provider, ledger = FakeProvider(), BudgetLedger(tmp_path / "ledger.json")
    directory, summary = run(provider, "fake", "model", Price(1, .5, 2), tasks, tmp_path,
        pack_id=V2, budget_ledger=ledger, efficiency_budgets={
            task.id: {"tokens": 100, "latency_seconds": 10} for task in tasks})
    entries = [json.loads(line) for line in (directory / "not-run.jsonl").read_text().splitlines()]
    assert provider.calls == 1
    assert entries[0]["status"] == "not_run"
    assert entries[0]["response"]["text"] == "Synthetic response"
    assert entries[0]["grading_error"]["stop_run"] is True
    assert "fallback_error" in entries[0]["grading_error"]
    grade = CheckpointGrade.from_dict(entries[0]["grade"])
    assert grade.score == 0 and grade.checkpoints == (Checkpoint("grading_error", 0, 100),)
    assert "grader failed" in grade.detail
    assert summary["attempts"] == 0 and summary["not_run"] == len(entries) == 2
    assert not summary["complete"]
    assert (directory / "attempts.jsonl").read_text() == ""
    assert summary["estimated_cost_usd"] == entries[0]["cost_usd"] > 0
    assert len(json.loads(ledger.path.read_text())["charges"]) == 1
    assert summary == score_run(directory)


@pytest.mark.parametrize("error", [KeyboardInterrupt, SystemExit])
def test_grading_does_not_swallow_process_cancellation(error, tmp_path, monkeypatch):
    monkeypatch.setattr("p95bench.runner.grade", Mock(side_effect=error))
    with pytest.raises(error):
        _grade_response(task(), "response", tmp_path, True)


def test_repeat_mean_not_pass_rate(tmp_path, monkeypatch):
    scores = iter([20, 80] * 5)
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint", lambda *args: result(next(scores)))
    tasks = [task(c) for c in pack_config(V2)["weights"]]
    _, summary = run(FakeProvider(), "fake", "model", Price(1, 1, 1), tasks, tmp_path, 2,
                     pack_id=V2, efficiency_budgets={
                         t.id: {"tokens": 1000, "latency_seconds": 10} for t in tasks},
                     budget_ledger=BudgetLedger(tmp_path / "ledger.json"))
    assert summary["overall_score"] == pytest.approx(50)
    assert summary["passed"] == 0


def test_no_ready_grader_means_no_spend(tmp_path):
    provider = FakeProvider()
    with pytest.raises(ValueError, match="Unknown grader"):
        run(provider, "fake", "model", Price(1, 1, 1), [task()], tmp_path,
            pack_id=V2, efficiency_budgets={"repair": {"tokens": 1, "latency_seconds": 1}})
    assert provider.calls == 0


def test_shared_cap_stops_requests_but_writes_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("P95_BUDGET_LIMIT_USD", "100")
    provider, directory, summary = run_v2(tmp_path, monkeypatch, price=Price(10000, 10000, 10000))
    assert provider.calls == 0
    assert summary["attempts"] == 0 and not summary["complete"]
    assert len((directory / "not-run.jsonl").read_text().splitlines()) == 5


def test_budget_shared_models_processes_and_fail_closed(tmp_path):
    path = tmp_path / "ledger.json"
    first, second = BudgetLedger(path, limit=100), BudgetLedger(path, limit=100)
    reservation = first.reserve(70)
    with pytest.raises(SpendLimitExceeded):
        second.reserve(31)
    first.settle(reservation, 1)
    second.reserve(99)
    with pytest.raises(SpendLimitExceeded):
        first.reserve(.000001)
    with pytest.raises(ValueError):
        BudgetLedger(path, limit=101)
    with pytest.raises(ValueError):
        BudgetLedger(path, limit=1).reserve(.1)
    path.write_text("corrupted")
    with pytest.raises(ValueError):
        first.reserve(1)


def test_budget_ledger_environment_overrides(tmp_path, monkeypatch):
    path = tmp_path / "environment-ledger.json"
    monkeypatch.setenv("P95_BUDGET_LEDGER", str(path))
    monkeypatch.setenv("P95_BUDGET_LIMIT_USD", "17.250001")

    ledger = BudgetLedger()

    assert ledger.path == path
    assert ledger.limit == 17_250_001


@pytest.mark.parametrize("limit", [None, "", "   "])
def test_uncapped_ledger_keeps_charges_without_a_spend_ceiling(tmp_path, monkeypatch, limit):
    if limit is None:
        monkeypatch.delenv("P95_BUDGET_LIMIT_USD", raising=False)
    else:
        monkeypatch.setenv("P95_BUDGET_LIMIT_USD", limit)
    ledger = BudgetLedger(tmp_path / "ledger.json")
    key = ledger.reserve(1000)
    ledger.settle(key, 200)
    ledger.reserve(2500)
    empty = ledger.reserve(0)
    ledger.settle(empty, 0)
    saved = json.loads(ledger.path.read_text())
    assert saved["limit"] is ledger.limit is None
    assert sum(saved["charges"].values()) == 2_700_000_000


def test_removing_cap_preserves_existing_ledger_charges(tmp_path):
    capped = BudgetLedger(tmp_path / "ledger.json", limit=10)
    key = capped.reserve(2)
    capped.settle(key, 1)
    uncapped = BudgetLedger(capped.path, limit="")
    uncapped.reserve(1000)
    saved = json.loads(capped.path.read_text())
    assert saved["limit"] is None
    assert saved["charges"][key] == 1_000_000
    with pytest.raises(ValueError, match="mismatched"):
        capped.reserve(1)


@pytest.mark.parametrize("limit", [None, ""])
def test_cli_uncapped_run_records_all_costs_and_tokens(tmp_path, monkeypatch, limit):
    if limit is None:
        monkeypatch.delenv("P95_BUDGET_LIMIT_USD", raising=False)
    else:
        monkeypatch.setenv("P95_BUDGET_LIMIT_USD", limit)
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setenv("P95_BUDGET_LEDGER", str(ledger_path))
    tasks = [replace(task(category), tool=tool) for category, tool in zip(
        pack_config(V2)["weights"], ("jmeter", "k6", "locust", "gatling", "jmeter"), strict=True)]
    provider = FakeProvider()
    monkeypatch.setattr("p95bench.cli.get_provider", lambda _: provider)
    monkeypatch.setattr("p95bench.cli.load_tasks", lambda *args: tasks)
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint", lambda *args: result(100))
    config = replace(PROVIDERS["deepseek"], prices={"deepseek-flash": Price(1e6, 1e6, 1e6)})
    monkeypatch.setitem(PROVIDERS, "deepseek", config)
    budgets = tmp_path / "efficiency.json"
    budgets.write_text(json.dumps({item.id: {"tokens": 100, "latency_seconds": 10} for item in tasks}))
    assert main(["run", "--provider", "deepseek", "--model", "deepseek-flash", "--pack", V2,
                 "--efficiency-budgets", str(budgets), "--out", str(tmp_path)]) == 0
    directory = next((tmp_path / V2).iterdir())
    summary = json.loads((directory / "summary.json").read_text())
    attempts = [json.loads(line) for line in (directory / "attempts.jsonl").read_text().splitlines()]
    assert provider.calls == summary["attempts"] == 5
    assert summary["complete"] and summary["not_run"] == 0
    assert not (directory / "not-run.jsonl").exists()
    assert json.loads((directory / "metadata.json").read_text())["spend_cap_usd"] is None
    assert summary["estimated_cost_usd"] == sum(item["cost_usd"] for item in attempts) == 600
    assert summary["input_tokens"] == 500 and summary["output_tokens"] == 100
    assert summary["cost_by_tool"] == {"jmeter": 240, "k6": 120, "locust": 120, "gatling": 120}
    assert sum(json.loads(ledger_path.read_text())["charges"].values()) == 600_000_000
    assert summary == score_run(directory)


@pytest.mark.parametrize("limit", ["invalid", "NaN", "0"])
def test_budget_ledger_rejects_invalid_environment_limit(tmp_path, monkeypatch, limit):
    monkeypatch.setenv("P95_BUDGET_LEDGER", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("P95_BUDGET_LIMIT_USD", limit)

    with pytest.raises(ValueError):
        BudgetLedger()


def test_budget_ledger_rejects_negative_reservation(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = BudgetLedger(path)

    with pytest.raises(ValueError, match="Negative budget amount"):
        ledger.reserve(-0.0000001)

    assert not path.exists()


def test_budget_ledger_rejects_negative_settlement_without_corruption(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = BudgetLedger(path)
    reservation = ledger.reserve(2)
    original = json.loads(path.read_text())

    with pytest.raises(ValueError, match="Negative budget amount"):
        ledger.settle(reservation, -0.0000001)

    assert json.loads(path.read_text()) == original
    with ledger.transaction() as data:
        assert data["charges"] == {reservation: 2_000_000}


def test_concurrent_budget_reservations(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.json", limit=100)

    def reserve(_):
        try:
            ledger.reserve(20)
            return True
        except SpendLimitExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 5


def test_v2_metadata_records_harness_revision(tmp_path, monkeypatch):
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint", lambda *args: result(80))
    directory, _ = run(FakeProvider(), "fake", "model", Price(1, 1, 1), [task()], tmp_path,
                       pack_id=V2, efficiency_budgets={"repair": {"tokens": 100, "latency_seconds": 1}},
                       budget_ledger=BudgetLedger(tmp_path / "ledger.json"))
    metadata = json.loads((directory / "metadata.json").read_text())
    assert metadata["repair_rounds"] == 1
    assert metadata["reasoning_mode"] is None
    assert metadata["output_cap"] == 64000
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=False).stdout.strip()
    assert metadata["harness_commit"] == (head or None)
    assert isinstance(metadata["harness_dirty"], bool) or not head


def test_matrix_scenario_is_recorded_in_manifest_attempts_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint", lambda *args: result(80))
    scenario = "mx-c02-html-csrf"
    task_id = scenario + "-k6"
    matrix_task = Task(task_id, "correlation", "k6", "Synthetic", "test_checkpoint", {}, {},
                       scenario=scenario)
    directory, _ = run(
        FakeProvider(), "fake", "model", Price(1, .5, 2), [matrix_task], tmp_path,
        pack_id="p95-matrix",
        efficiency_budgets={task_id: {"tokens": 100, "latency_seconds": 2}},
        budget_ledger=BudgetLedger(tmp_path / "matrix-ledger.json"),
    )
    manifest = json.loads((directory / "pack.json").read_text())
    metadata = json.loads((directory / "metadata.json").read_text())
    attempt = json.loads((directory / "attempts.jsonl").read_text())
    assert manifest["tasks"][0]["scenario"] == scenario
    assert attempt["scenario"] == scenario
    assert metadata["task_scenarios"] == {task_id: scenario}


@pytest.mark.parametrize(
    "filters,expected",
    [
        (["--tool", "k6"], {"mx-a01-analysis-k6", "mx-w07-analysis-k6"}),
        (["--scenario", "a01"], {
            "mx-a01-analysis-jmeter", "mx-a01-analysis-k6",
            "mx-a01-analysis-locust", "mx-a01-analysis-gatling",
        }),
        (["--tool", "k6", "--scenario", "a01"], {"mx-a01-analysis-k6"}),
    ],
)
def test_cli_filters_scenarios_and_subsets_budgets(tmp_path, monkeypatch, filters, expected):
    tasks = [
        Task(f"mx-{scenario}-analysis-{tool}", "analysis", tool, "Synthetic",
             "test_checkpoint", {}, {}, scenario=f"mx-{scenario}-analysis")
        for scenario in ("a01", "w07")
        for tool in ("jmeter", "k6", "locust", "gatling")
    ]
    all_budgets = {
        item.id: {"tokens": 100, "latency_seconds": 10}
        for item in tasks
    }
    budget_file = tmp_path / "budgets.json"
    budget_file.write_text(json.dumps(all_budgets))
    captured = {}

    def fake_run(_provider, _provider_name, _model, _price, selected, _out, _repeats, **options):
        captured["task_ids"] = {item.id for item in selected}
        captured["budgets"] = options["efficiency_budgets"]
        return tmp_path / "run", {"complete": True}

    monkeypatch.setattr("p95bench.cli.load_tasks", lambda *_: tasks)
    monkeypatch.setattr("p95bench.cli.get_provider", lambda _: FakeProvider())
    monkeypatch.setattr("p95bench.cli.run", fake_run)

    assert main([
        "run", "--provider", "deepseek", "--model", "deepseek-flash",
        "--pack", "p95-core", "--efficiency-budgets", str(budget_file),
        *filters,
    ]) == 0
    assert captured["task_ids"] == expected
    assert set(captured["budgets"]) == expected


@pytest.mark.parametrize(
    "tasks,scenario,error",
    [
        ([task()], "a01", "Task has no scenario"),
        ([replace(task(), scenario="mx-w07-burst")], "a01", "No tasks match scenario"),
    ],
)
def test_cli_rejects_unfilterable_or_unknown_scenarios(
    tmp_path, monkeypatch, capsys, tasks, scenario, error,
):
    monkeypatch.setattr("p95bench.cli.load_tasks", lambda *_: tasks)
    monkeypatch.setattr("p95bench.cli.get_provider", lambda _: FakeProvider())

    assert main([
        "run", "--provider", "deepseek", "--model", "deepseek-flash",
        "--pack", "p95-core", "--scenario", scenario, "--out", str(tmp_path),
    ]) == 1
    assert error in capsys.readouterr().err


def test_cli_rejects_selected_task_missing_efficiency_budget(tmp_path, monkeypatch, capsys):
    selected = replace(task(), id="mx-a01-analysis-k6", tool="k6", scenario="mx-a01-analysis")
    other = replace(task(), id="mx-w07-analysis-k6", tool="k6", scenario="mx-w07-analysis")
    budget_file = tmp_path / "budgets.json"
    budget_file.write_text(json.dumps({
        other.id: {"tokens": 100, "latency_seconds": 10},
    }))
    monkeypatch.setattr("p95bench.cli.load_tasks", lambda *_: [selected, other])
    monkeypatch.setattr("p95bench.cli.get_provider", lambda _: FakeProvider())

    assert main([
        "run", "--provider", "deepseek", "--model", "deepseek-flash",
        "--pack", "p95-core", "--tool", "k6", "--scenario", "a01",
        "--efficiency-budgets", str(budget_file), "--out", str(tmp_path),
    ]) == 1
    assert selected.id in capsys.readouterr().err


def test_harness_revision_ignores_enclosing_repository(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    installed = tmp_path / ".venv" / "lib" / "python3" / "site-packages"
    installed.mkdir(parents=True)
    assert harness_revision(installed) == {"harness_commit": None, "harness_dirty": None}


def test_budget_overrun_is_persisted_and_blocks_future_spend(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.json", limit=100)
    key = ledger.reserve(1)
    with pytest.raises(ValueError, match="reservation"):
        ledger.settle(key, 101)
    assert json.loads(ledger.path.read_text())["charges"][key] == 101_000_000
    with pytest.raises(SpendLimitExceeded):
        BudgetLedger(ledger.path, limit=100).reserve(.000001)


def test_legacy_published_scores_bit_identical():
    for directory in (ROOT / "results/published").iterdir():
        attempts = [json.loads(l) for l in (directory / "attempts.jsonl").read_text().splitlines()]
        expected = json.loads((directory / "summary.json").read_text())
        actual = summarize(attempts)
        # Python 3.12 changed sum precision; freeze v1 arithmetic on each runtime.
        categories = list(dict.fromkeys(a["category"] for a in attempts))
        legacy_score = sum(expected["category_scores"][c] * CATEGORIES[c]
                           for c in categories) / sum(CATEGORIES[c] for c in categories)
        assert legacy_score == pytest.approx(expected["overall_score"], rel=1e-14)
        expected["overall_score"] = legacy_score
        for field in ("estimated_cost_usd", "latency_seconds"):
            assert actual.pop(field) == pytest.approx(expected.pop(field), rel=1e-14, abs=1e-14)
        assert actual == expected
        assert actual["overall_score"].hex() == float(expected["overall_score"]).hex()
        for category, score in actual["category_scores"].items():
            assert score.hex() == float(expected["category_scores"][category]).hex()


def test_legacy_run_preserves_binary_score_and_directory_layout(tmp_path):
    sample = load_tasks(ROOT / "tasks")[0]
    sample = replace(sample, grader="numeric", params={"answer": 10})
    provider = Mock(send=Mock(return_value=Response("10", 1, 2, 0, 1)), config=None)
    directory, summary = run(provider, "fake", "model", Price(1, 1, 1), [sample], tmp_path)
    assert directory.parent == tmp_path
    assert json.loads((directory / "pack.json").read_text())["pack_id"] == "p95-v1"
    assert summary["overall_score"] == 100
    assert "pack_id" not in summary
    assert score_run(directory) == summary


def test_transport_measurement_excludes_backoff(monkeypatch):
    provider = OpenAICompatible(ProviderConfig("https://example.invalid", "TEST_KEY", {},
                                             max_retries=1))
    provider.measure_transport = True
    monkeypatch.setenv("TEST_KEY", "synthetic")
    monkeypatch.setattr(provider, "_request_once", Mock(side_effect=[TimeoutError(), {"ok": True}]))
    clock = iter([0, 10, 12])
    monkeypatch.setattr("p95bench.providers.time.perf_counter", lambda: next(clock))
    monkeypatch.setattr("p95bench.providers.time.sleep", lambda _: None)
    assert provider._request("/test") == {"ok": True}
    assert provider.transport_attempts == 2
    assert provider.backoff_seconds == 1
    assert provider.response_latency_seconds == 2


def test_cli_invalid_pack_and_repeat_no_calls(capsys):
    assert main(["run", "--provider", "deepseek", "--model", "deepseek-flash",
                 "--repeats", "0"]) == 1
    assert "positive" in capsys.readouterr().err


def test_cli_formats_capacity_audit_overflow_as_worker_failure(tmp_path, monkeypatch, capsys):
    provider = FakeProvider()
    tasks = load_tasks(ROOT / "packs" / V2 / "tasks", pack_config(V2)["weights"])
    budgets = tmp_path / "efficiency.json"
    budgets.write_text(json.dumps({task.id: {"tokens": 100, "latency_seconds": 10} for task in tasks}))
    monkeypatch.setattr("p95bench.cli.get_provider", lambda _: provider)
    monkeypatch.setattr("p95bench.capacity.QUALIFIED", {})
    monkeypatch.setattr("p95bench.capacity.tool_image", lambda _: "sha256:" + "0" * 64)
    monkeypatch.setattr("p95bench.capacity.run_artifact", Mock(side_effect=AuditOverflow("capacity audit overflow")))
    assert main(["run", "--provider", "deepseek", "--model", "deepseek-flash",
                 "--pack", V2, "--out", str(tmp_path), "--efficiency-budgets", str(budgets)]) == 1
    assert "p95bench: capacity audit overflow" in capsys.readouterr().err
    assert provider.calls == 0


@pytest.mark.parametrize("v2, failure", [(False, WorkerUnavailable), (True, WorkerUnavailable),
    (False, RuntimeError), (True, RuntimeError), (True, RecursionError), (True, ValueError),
    (True, "rubric"), (False, AuditOverflow), (True, AuditOverflow), (False, None), (True, None)])
def test_cli_grading_stops_exit_nonzero_after_saving_summary(v2, failure, tmp_path, monkeypatch, capsys):
    rubric = [{"id": "behavior", "points": 99 if failure == "rubric" else 100}]
    tasks = [replace(task("analysis"), id=name, params={"rubric": rubric}) for name in ("first", "second")]
    provider = FakeProvider()
    monkeypatch.setattr("p95bench.cli.get_provider", lambda _: provider)
    monkeypatch.setattr("p95bench.cli.load_tasks", lambda *args: tasks)
    monkeypatch.setattr("p95bench.runner.BudgetLedger", lambda: BudgetLedger(tmp_path / "ledger.json"))
    error = ValueError if failure == "rubric" else failure
    grader = (Mock(side_effect=error("synthetic grading failure")) if error else
              lambda *args: result(100) if v2 else Grade(True, "valid"))
    monkeypatch.setitem(CHECKPOINT_GRADERS if v2 else GRADERS, "test_checkpoint", grader)
    stopped = failure is not None and failure is not AuditOverflow
    options = []
    if v2:
        budgets = tmp_path / "efficiency.json"
        budgets.write_text(json.dumps({task.id: {"tokens": 100, "latency_seconds": 10} for task in tasks}))
        options = ["--efficiency-budgets", str(budgets)]
    assert main(["run", "--provider", "deepseek", "--model", "deepseek-flash", "--out", str(tmp_path),
                 "--pack", V2 if v2 else "p95-v1", *options]) == int(stopped)
    output = capsys.readouterr()
    summary_file = next(tmp_path.rglob("summary.json"))
    summary = json.loads(summary_file.read_text())
    assert ("stop_reason" in summary) is stopped
    assert provider.calls == (1 if stopped else 2)
    assert "Run:" in output.out
    if stopped:
        assert "p95bench: Run stopped: Grader error" in output.err
        assert "synthetic grading failure" in summary["stop_reason"]
        assert summary["attempts"] == 0 and summary["not_run"] == 2
        assert summary["estimated_cost_usd"] > 0
    else:
        assert output.err == ""
        assert summary["attempts"] == 2
    assert main(["score", str(summary_file.parent)]) == int(stopped)
    assert score_run(summary_file.parent) == summary
