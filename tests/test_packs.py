import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from p95bench.budget import BudgetLedger
from p95bench.cli import main
from p95bench.graders import CHECKPOINT_GRADERS
from p95bench.grades import Checkpoint, CheckpointGrade
from p95bench.packs import efficiency, pack_config, snapshot, summarize_v2
from p95bench.providers import OpenAICompatible, Price, ProviderConfig, Response, SpendLimitExceeded
from p95bench.runner import run, score_run, summarize
from p95bench.tasks import CATEGORIES, Task, load_private_tasks, load_tasks, require_external

ROOT = Path(__file__).resolve().parents[1]
V2 = "p95-v2.0.0"


def task(category="repair"):
    return Task(category, category, "none", "Synthetic test input", "test_checkpoint", {}, {})


def result(points=80):
    return CheckpointGrade((Checkpoint("behavior", points, 100),))


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
    assert efficiency(.8, response, budget) == pytest.approx(.6)
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


@pytest.mark.parametrize("repeats", [1, 3])
def test_v2_run_and_offline_rescore(tmp_path, monkeypatch, repeats):
    provider, directory, summary = run_v2(tmp_path, monkeypatch, repeats)
    assert directory.parent.name == V2
    assert provider.calls == 5 * repeats
    assert summary["pack_id"] == V2 and summary["complete"]
    assert summary["quality_score"] == 80
    assert summary["efficiency_score"] == pytest.approx(60)
    assert summary["overall_score"] == pytest.approx(79)
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
    provider, directory, summary = run_v2(tmp_path, monkeypatch, price=Price(10000, 10000, 10000))
    assert provider.calls == 0
    assert summary["attempts"] == 0 and not summary["complete"]
    assert len((directory / "not-run.jsonl").read_text().splitlines()) == 5


def test_budget_shared_models_processes_and_fail_closed(tmp_path):
    path = tmp_path / "ledger.json"
    first, second = BudgetLedger(path), BudgetLedger(path)
    reservation = first.reserve(20)
    with pytest.raises(SpendLimitExceeded):
        second.reserve(11)
    first.settle(reservation, 1)
    second.reserve(29)
    with pytest.raises(SpendLimitExceeded):
        first.reserve(.000001)
    with pytest.raises(ValueError):
        BudgetLedger(path, limit=31)
    with pytest.raises(ValueError):
        BudgetLedger(path, limit=1).reserve(.1)
    path.write_text("corrupted")
    with pytest.raises(ValueError):
        first.reserve(1)


def test_concurrent_budget_reservations(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.json")

    def reserve(_):
        try:
            ledger.reserve(5)
            return True
        except SpendLimitExceeded:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 6


def test_external_private_loading_and_output_guard(tmp_path):
    root = tmp_path / "restricted"
    root.mkdir()
    (root / "sample.yaml").write_text(
        "id: sample\ncategory: repair\ntool: none\nprompt: Synthetic\ngrader: test_checkpoint\n")
    assert len(load_private_tasks(root, ROOT)) == 1
    with pytest.raises(ValueError, match="outside"):
        load_private_tasks(ROOT / "tasks", ROOT)
    alias = tmp_path / "alias"
    alias.symlink_to(ROOT / "tasks")
    with pytest.raises(ValueError, match="outside"):
        require_external(alias, ROOT)
    with pytest.raises(ValueError, match="outside"):
        run(FakeProvider(), "fake", "model", Price(1, 1, 1), [task()], ROOT / "results",
            private=True)


def test_private_pack_symlink_cannot_write_into_repository(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    repository.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    (output / V2).symlink_to(repository, target_is_directory=True)
    monkeypatch.setattr("p95bench.runner.require_external",
                        lambda path, _: require_external(path, repository))
    monkeypatch.setitem(CHECKPOINT_GRADERS, "test_checkpoint", lambda *args: result(80))
    provider = FakeProvider()
    with pytest.raises(ValueError, match="outside"):
        run(provider, "fake", "model", Price(1, 1, 1), [task()], output,
            pack_id=V2, private=True,
            efficiency_budgets={"repair": {"tokens": 100, "latency_seconds": 1}},
            budget_ledger=BudgetLedger(tmp_path / "ledger.json"))
    assert not list(repository.iterdir())
    assert provider.calls == 0


def test_budget_overrun_is_persisted_and_blocks_future_spend(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger.json")
    key = ledger.reserve(1)
    with pytest.raises(ValueError, match="reservation"):
        ledger.settle(key, 31)
    assert json.loads(ledger.path.read_text())["charges"][key] == 31_000_000
    with pytest.raises(SpendLimitExceeded):
        BudgetLedger(ledger.path).reserve(.000001)


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
