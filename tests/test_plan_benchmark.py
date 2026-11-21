import json
from dataclasses import replace

import pytest

from p95bench.providers import PROVIDERS, Price
from scripts import plan_benchmark


def test_plan_benchmark_builds_model_and_tool_shards(monkeypatch, capsys):
    monkeypatch.setenv(
        "MODELS",
        "# requested providers, comma-separated lines are also supported\n"
        "deepseek deepseek-flash, qwen Qwen-Ambassador/Qwen3.8-Max\n",
    )
    monkeypatch.setenv("CAP_USD_PER_MODEL", "40.1234567")
    monkeypatch.setenv("SCENARIOS", "a01, w07")

    assert plan_benchmark.main([]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    models = json.loads(lines[0].removeprefix("models="))
    shards = json.loads(lines[1].removeprefix("shards="))
    assert models == [
        {"provider": "deepseek", "model": "deepseek-flash", "slug": "deepseek-flash",
         "key_env": "DEEPSEEK_API_KEY"},
        {"provider": "qwen", "model": "Qwen-Ambassador/Qwen3.8-Max",
         "slug": "Qwen-Ambassador-Qwen3.8-Max", "key_env": "MODELSCOPE_API_KEY"},
    ]
    assert len(shards) == 8
    assert [shard["tool"] for shard in shards[:4]] == [
        "jmeter", "k6", "locust", "gatling",
    ]
    assert all(shard["cap_usd"] == "10.030864" for shard in shards)
    assert shards[0] == {**models[0], "tool": "jmeter", "cap_usd": "10.030864"}


@pytest.mark.parametrize(
    "models,cap,error",
    [
        ("not-a-provider model", "40", "Unknown provider"),
        ("deepseek unknown-model", "40", "Unknown model"),
        ("deepseek deepseek-flash\ndeepseek deepseek-flash", "40", "Duplicate model"),
        ("", "40", "at least one"),
        ("deepseek deepseek-flash", "not-a-number", "CAP_USD_PER_MODEL"),
        ("deepseek deepseek-flash", "NaN", "CAP_USD_PER_MODEL"),
        ("deepseek deepseek-flash", "0", "CAP_USD_PER_MODEL"),
        ("deepseek deepseek-flash", "401", "at most $100"),
    ],
)
def test_plan_benchmark_rejects_invalid_inputs(monkeypatch, capsys, models, cap, error):
    monkeypatch.setenv("MODELS", models)
    monkeypatch.setenv("CAP_USD_PER_MODEL", cap)
    monkeypatch.delenv("SCENARIOS", raising=False)

    assert plan_benchmark.main([]) == 1
    assert error in capsys.readouterr().err


def test_plan_benchmark_rejects_slug_collisions(monkeypatch, capsys):
    config = replace(
        PROVIDERS["deepseek"],
        prices={"x/y": Price(1, 1, 1), "x-y": Price(1, 1, 1)},
    )
    monkeypatch.setitem(plan_benchmark.PROVIDERS, "deepseek", config)
    monkeypatch.setenv("MODELS", "deepseek x/y\ndeepseek x-y")
    monkeypatch.setenv("CAP_USD_PER_MODEL", "40")

    assert plan_benchmark.main([]) == 1
    assert "Model slug collision: x-y" in capsys.readouterr().err


def test_plan_benchmark_rejects_unknown_scenario(monkeypatch, capsys):
    monkeypatch.setenv("MODELS", "deepseek deepseek-flash")
    monkeypatch.setenv("CAP_USD_PER_MODEL", "40")
    monkeypatch.setenv("SCENARIOS", "not-a-scenario")

    assert plan_benchmark.main([]) == 1
    assert "Unknown Core scenario code" in capsys.readouterr().err


def test_plan_benchmark_summary_uses_stored_overall_and_task_scores(tmp_path, capsys):
    run_dir = tmp_path / "synthetic"
    run_dir.mkdir()
    (run_dir / "metadata.json").write_text(json.dumps({"model": "synthetic-model"}))
    (run_dir / "pack.json").write_text(json.dumps({
        "tasks": [{"id": "j1", "tool": "jmeter"}, {"id": "k1", "tool": "k6"}],
    }))
    (run_dir / "summary.json").write_text(json.dumps({
        "overall_score": 70.25,
        "after_repair": {"overall_score": 82.5},
        "estimated_cost_usd": 0.123456,
        "output_tokens": 1234,
    }))
    attempts = [
        {"task_id": "j1", "grade": {"score": .75},
         "repair": {"triggered": True,
                    "grade": {"status": "graded", "score": .95}}},
        {"task_id": "k1", "grade": {"score": .85}},
    ]
    (run_dir / "attempts.jsonl").write_text(
        "".join(json.dumps(attempt) + "\n" for attempt in attempts)
    )

    assert plan_benchmark.main(["--summary", str(run_dir)]) == 0
    table = capsys.readouterr().out
    assert "| synthetic-model | 70.2 | 82.5 |" in table
    assert "jmeter 75.0 → 95.0" in table
    assert "k6 85.0 → 85.0" in table
    assert "$0.123456" in table
    assert "1,234" in table
