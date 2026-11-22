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


def test_plan_benchmark_summary_matches_published_tool_scores(capsys):
    run_dir = plan_benchmark.ROOT / "results/p95-core/claude-sonnet-5-5"
    board = json.loads(
        (plan_benchmark.ROOT / "site/src/data/leaderboard-p95-core.json").read_text()
    )
    model = next(item for item in board["models"] if item["model"] == "claude-sonnet-5-5")
    expected = {}
    for tool in plan_benchmark.TOOLS:
        attempts = [item for item in model["per_task_results"] if item["tool"] == tool]
        first = 100 * sum(item["score"] for item in attempts) / len(attempts)
        after = 100 * sum(
            item.get("after_repair_score", item["score"]) for item in attempts
        ) / len(attempts)
        expected[tool] = (f"{first:.1f}", f"{after:.1f}")

    assert plan_benchmark.main(["--summary", str(run_dir)]) == 0
    table = capsys.readouterr().out
    for tool, (first, after) in expected.items():
        assert f"{tool} {first} → {after}" in table


def test_plan_benchmark_summary_rejects_unknown_core_task(tmp_path):
    (tmp_path / "attempts.jsonl").write_text(
        json.dumps({"task_id": "not-a-core-task"}) + "\n"
    )

    with pytest.raises(ValueError, match="not-a-core-task"):
        plan_benchmark._tool_scores(tmp_path)
