import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

import pytest

from p95bench.cli import main
from p95bench.demo import demo_app
from p95bench.graders import choice, extract_jmx, jmeter_exec, numeric, run_jmeter
from p95bench.providers import OpenAICompatible, Price, ProviderConfig, Response
from p95bench.runner import run, score_run, summarize
from p95bench.tasks import Task, load_tasks

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text,passed", [("10", True), ("10.001", True), ("11", False),
                                          ("", False), ("nan", False), ("10 or 11", False)])
def test_numeric(text, passed):
    assert numeric(text, {"answer": 10, "tolerance": .01}, None).passed is passed


@pytest.mark.parametrize("text,passed", [(" b\n", True), ("A", False), ("", False),
                                          ("B because", False)])
def test_choice(text, passed):
    assert choice(text, {"answer": "B"}, None).passed is passed


def test_seed_answers():
    tasks = {task.id: task for task in load_tasks(ROOT / "tasks")}
    assert len(tasks) == 15
    expected = {"little-law": 40 * .25, "pacing": 100 / 20 - 2,
                "arrival": 18000 / 300, "concurrency": 75 * .8}
    with (ROOT / "tasks/analysis/samples.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    expected["mean"] = sum(int(r["elapsed"]) for r in rows) / len(rows)
    expected["errors"] = 100 * sum(r["success"] == "false" for r in rows) / len(rows)
    for name, answer in expected.items():
        assert tasks[name].params["answer"] == answer
    assert tasks["percentile"].params["answer"] == "B"
    assert tasks["closed"].params["answer"] == "A"
    assert tasks["correlation"].params["answer"] == "C"


def test_task_validation(tmp_path):
    with pytest.raises(ValueError, match="No YAML"):
        load_tasks(tmp_path)
    source = (ROOT / "tasks/workload/arrival.yaml").read_text()
    (tmp_path / "a.yaml").write_text(source)
    (tmp_path / "b.yaml").write_text(source)
    with pytest.raises(ValueError, match="Duplicate"):
        load_tasks(tmp_path)
    (tmp_path / "b.yaml").unlink()
    (tmp_path / "a.yaml").write_text(source + "fixtures: [../secret]\n")
    with pytest.raises(ValueError, match="escapes"):
        load_tasks(tmp_path)
    (tmp_path / "a.yaml").write_text("id: broken")
    with pytest.raises(ValueError, match="Missing"):
        load_tasks(tmp_path)


class FakeProvider:
    def __init__(self):
        self.calls = []

    def send(self, model, messages):
        self.calls.append((model, messages))
        return Response("10", 100, 20, 10, .5)


def test_run_and_rescore(tmp_path):
    provider = FakeProvider()
    task = Task("test", "workload", "none", "Compute", "numeric", {"answer": 10}, {})
    directory, summary = run(provider, "fake", "model", Price(1, .5, 2), [task], tmp_path, 2)
    assert summary["overall_score"] == 100
    assert summary["input_tokens"] == 200
    assert summary["output_tokens"] == 40
    assert summary["latency_seconds"] == 1
    assert summary["estimated_cost_usd"] == pytest.approx(.00027)
    assert summary == score_run(directory)
    assert len(provider.calls) == 2
    assert provider.calls[0][1] == provider.calls[1][1]
    attempt = json.loads((directory / "attempts.jsonl").read_text().splitlines()[0])
    assert attempt["response"]["text"] == "10"
    assert main(["score", str(directory)]) == 0
    with pytest.raises(ValueError, match="positive"):
        run(provider, "fake", "model", Price(1, 1, 1), [task], tmp_path, 0)
    task.grader = "unknown"
    with pytest.raises(ValueError, match="Unknown grader"):
        run(provider, "fake", "model", Price(1, 1, 1), [task], tmp_path)


def test_scoring_weights():
    def attempt(category, passed):
        return {"category": category, "grade": {"passed": passed}, "cost_usd": 0,
                "response": {"input_tokens": 0, "output_tokens": 0, "latency_seconds": 0}}
    assert summarize([])["overall_score"] == 0
    summary = summarize([attempt("authoring", True), attempt("concepts", False)])
    assert summary["overall_score"] == pytest.approx(100 * 25 / 30)


def test_adapter(monkeypatch):
    config = ProviderConfig("https://example.invalid", "TEST_BENCH_KEY", {})
    provider = OpenAICompatible(config)
    with pytest.raises(ValueError, match="Set TEST_BENCH_KEY"):
        provider.models()
    monkeypatch.setenv("TEST_BENCH_KEY", "fake-key")

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "answer"}}],
                               "usage": {"prompt_tokens": 20, "completion_tokens": 3,
                                         "prompt_tokens_details": {"cached_tokens": 4}}}).encode()

    def request(req, timeout):
        assert req.full_url == "https://example.invalid/chat/completions"
        assert json.loads(req.data)["temperature"] == 0
        assert timeout == 180
        return Reply()

    monkeypatch.setattr("urllib.request.urlopen", request)
    response = provider.send("model", [{"role": "user", "content": "hello"}])
    assert (response.text, response.input_tokens, response.output_tokens,
            response.cached_tokens) == ("answer", 20, 3, 4)
    monkeypatch.setattr(provider, "_request", lambda path: {"data": [{"id": "real-model"}]})
    assert provider.models() == ["real-model"]


def test_cli_errors(tmp_path):
    assert main(["run", "--provider", "deepseek", "--model", "missing"]) == 1
    assert main(["run", "--provider", "deepseek", "--model", "deepseek-flash",
                 "--repeats", "0"]) == 1
    assert main(["score", str(tmp_path)]) == 1


def test_demo_flow():
    with demo_app() as (url, admin):
        client = build_opener(HTTPCookieProcessor())

        def request(path, body=None, headers=None):
            req = Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers or {})
            with client.open(req) as response:
                return json.load(response)

        with pytest.raises(HTTPError) as error:
            request("/products")
        assert error.value.code == 401
        token = request("/login", {"username": "bench", "password": "bench"})["csrf"]
        with pytest.raises(HTTPError) as error:
            request("/checkout", {})
        assert error.value.code == 403
        headers = {"X-CSRF-Token": token}
        with pytest.raises(HTTPError) as error:
            request("/checkout", {}, headers)
        assert error.value.code == 409
        request("/products")
        request("/cart", {"product_id": "sku-1"}, headers)
        assert request("/checkout", {}, headers)["completed"]
        with pytest.raises(HTTPError):
            request("/checkout", {}, headers)
        with pytest.raises(HTTPError):
            request("/internal/stats")
        assert request("/internal/stats", headers={"X-Admin-Key": admin}) == {"completions": 1}
        new_token = request("/login", {"username": "bench", "password": "bench"})["csrf"]
        assert new_token != token
        with pytest.raises(HTTPError):
            request("/cart", {"product_id": "sku-1"}, headers)


@pytest.mark.parametrize("text", ["", "not xml", "<jmeterTestPlan><bad></jmeterTestPlan>"])
def test_invalid_jmx(text, tmp_path):
    with patch("p95bench.graders.jmeter_executable", return_value=Path("jmeter")):
        assert not jmeter_exec(text, {}, tmp_path).passed


def test_exec_empty_errors_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("p95bench.graders.jmeter_executable", lambda: Path("jmeter"))
    assert extract_jmx("```xml\n<jmeterTestPlan></jmeterTestPlan>\n```")
    xml = "<jmeterTestPlan></jmeterTestPlan>"

    def fake_run(command, *args):
        result = Path(command[command.index("-l") + 1])
        result.write_text("success\nfalse\n")
        return subprocess.CompletedProcess(command, 0)

    with patch("p95bench.graders.run_jmeter", fake_run):
        assert not jmeter_exec(xml, {}, tmp_path / "failed").passed
    def empty_run(command, *args):
        Path(command[command.index("-l") + 1]).write_text("success\n")
        return subprocess.CompletedProcess(command, 0)

    with patch("p95bench.graders.run_jmeter", empty_run):
        assert not jmeter_exec(xml, {}, tmp_path / "empty").passed

    def successful_run(command, *args):
        Path(command[command.index("-l") + 1]).write_text("success\ntrue\n")
        return subprocess.CompletedProcess(command, 0)

    with patch("p95bench.graders.run_jmeter", successful_run):
        assert not jmeter_exec(xml, {}, tmp_path / "no-flow").passed
        with patch("json.load", return_value={"completions": 1}):
            assert jmeter_exec(xml, {}, tmp_path / "success").passed
    with patch("p95bench.graders.run_jmeter", side_effect=subprocess.TimeoutExpired("jmeter", 1)):
        assert "timed out" in jmeter_exec(xml, {}, tmp_path / "timeout").detail


def test_missing_jmeter_preflight(tmp_path, monkeypatch):
    monkeypatch.setenv("JMETER_HOME", str(tmp_path / "absent"))
    provider = FakeProvider()
    task = Task("exec", "authoring", "jmeter", "test", "jmeter_exec", {}, {})
    with pytest.raises(ValueError, match="set JMETER_HOME"):
        run(provider, "fake", "model", Price(0, 0, 0), [task], tmp_path / "results")
    assert not provider.calls
    assert not (tmp_path / "results").exists()
    with pytest.raises(ValueError, match="set JMETER_HOME"):
        jmeter_exec("<jmeterTestPlan></jmeterTestPlan>", {}, tmp_path)
    task.grader = "numeric"
    task.params = {"answer": 10}
    run(provider, "fake", "model", Price(0, 0, 0), [task], tmp_path / "results")
    assert len(provider.calls) == 1


@pytest.mark.skipif(os.name != "posix", reason="JMeter launcher process groups require POSIX")
def test_timeout_kills_launcher_and_child(tmp_path):
    pidfile = tmp_path / "child.pid"
    code = ("import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(90)']); "
            "open(sys.argv[1],'w').write(str(p.pid)); time.sleep(90)")
    with (tmp_path / "stdout").open("w") as log, pytest.raises(subprocess.TimeoutExpired):
        run_jmeter([sys.executable, "-c", code, str(pidfile)], log, 1)
    child = int(pidfile.read_text())
    for _ in range(100):
        stat = Path(f"/proc/{child}/stat")
        if not stat.exists() or stat.read_text().split()[2] == "Z":
            break
        time.sleep(.01)
    else:
        pytest.fail("Child survived launcher timeout")


@pytest.mark.parametrize("threads,passed", [([], False), (["thread-1", "thread-1"], False),
                                            (["thread-1", "thread-2"], True)])
def test_isolation_requires_distinct_checkout_threads(tmp_path, threads, passed):
    def execute(command, *args):
        result = Path(command[command.index("-l") + 1])
        result.write_text("success,threadName,URL\ntrue,other,http://localhost/products\n" +
                          "".join(f"true,{thread},http://localhost/checkout\n" for thread in threads))
        return subprocess.CompletedProcess(command, 0)

    with patch("p95bench.graders.jmeter_executable", return_value=Path("jmeter")), \
            patch("p95bench.graders.run_jmeter", execute), \
            patch("json.load", return_value={"completions": 2}):
        assert jmeter_exec("<jmeterTestPlan></jmeterTestPlan>",
                           {"completions": 2, "min_checkout_threads": 2}, tmp_path).passed is passed


def test_isolation_and_refresh_have_distinct_requirements():
    tasks = {t.id: t for t in load_tasks(ROOT / "tasks")}
    assert tasks["session-isolation"].params["min_checkout_threads"] == 2
    assert "min_checkout_threads" not in tasks["token-refresh"].params


def test_jmeter_prompts_contain_valid_json_examples():
    for task in load_tasks(ROOT / "tasks"):
        if task.grader == "jmeter_exec":
            assert '\\"' not in task.prompt
            assert '{"username":"bench","password":"bench"}' in task.prompt
            assert '{"product_id":"sku-1"}' in task.prompt
