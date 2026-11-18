import io
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

from p95bench.cli import main
from p95bench.graders import k6_exec, k6_executable
from p95bench.providers import Price
from p95bench.runner import run
from p95bench.tasks import Task, load_tasks


def point(vu="1", status="200", expected="true", path="/checkout", iteration="0"):
    return {"type": "Point", "metric": "http_reqs", "data": {"tags": {
        "status": status, "expected_response": expected,
        "url": "http://127.0.0.1:1234" + path}, "metadata": {"vu": vu, "iter": iteration}}}


@pytest.fixture
def stub(tmp_path, monkeypatch):
    monkeypatch.setattr("p95bench.graders.demo_app", lambda: nullcontext((
        "http://127.0.0.1:1234", "admin")))
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k:
                        io.BytesIO(b'{"completions":2}'))

    def install(points, exit_code=0, delay=0, requests=None):
        executable = tmp_path / "k6"
        executable.write_text(f"#!{sys.executable}\n" +
                              "import json,os,sys,time\nfrom pathlib import Path\n" +
                              "assert 'FAKE_API_KEY' not in os.environ\n" +
                              "assert '--no-usage-report' in sys.argv\n" +
                              "assert '--http-debug' in sys.argv and '--log-format=json' in sys.argv\n" +
                              "assert 'BASE_URL=http://127.0.0.1:1234' in sys.argv\n" +
                              "assert sys.argv[-2].endswith(',vu,iter')\n" +
                              f"time.sleep({delay})\n" +
                              f"points={points!r}\n" +
                              "target=sys.argv[sys.argv.index('--out')+1].split('=',1)[1]\n" +
                              "Path(target).write_text(''.join(json.dumps(p)+'\\n' for p in points))\n" +
                              f"requests={requests or []!r}\n" +
                              "for record in requests: print(json.dumps(record))\n" +
                              f"print('stub k6')\nsys.exit({exit_code})\n")
        executable.chmod(0o755)
        monkeypatch.setenv("K6_PATH", str(executable))
        monkeypatch.setenv("FAKE_API_KEY", "not-a-real-key")
    return install


@pytest.mark.parametrize("language", ["js", "javascript", "JavaScript"])
def test_pass_and_artifacts(stub, tmp_path, language):
    stub([point("1"), point("2")])
    result = k6_exec(f"```{language}\nexport default function() {{}}\n```",
                     {"completions": 2, "min_checkout_vus": 2}, tmp_path / "run")
    assert result.passed
    assert result.detail == "Verified completions: 2/2"
    assert "export default" in (tmp_path / "run/script.js").read_text()
    assert "stub k6" in (tmp_path / "run/stdout.log").read_text()


@pytest.mark.parametrize("points,params,reason", [
    ([], {}, "no http_reqs"),
    ([{"type": "Metric", "metric": "http_reqs"}], {}, "no http_reqs"),
    ([point(status="403", expected="false")], {}, "Failed HTTP"),
    ([point(status="403")], {}, "Failed HTTP"),
    ([point(expected="false")], {}, "Failed HTTP"),
    ([point("1"), point("1")], {"min_checkout_vus": 2}, "Checkout VUs: 1/2"),
    ([point("1"), point("2", path="/products")], {"min_checkout_vus": 2}, "Checkout VUs: 1/2"),
    ([point("")], {"min_checkout_vus": 2}, "Checkout VUs: 0/2"),
    ([point()], {"completions": 1}, "Verified completions: 2/1"),
    ([{"type": "Point", "metric": "http_reqs"}], {}, "Execution failed"),
])
def test_failed_output(stub, tmp_path, points, params, reason):
    stub(points)
    result = k6_exec("```js\nexport default function() {}\n```", params, tmp_path / "run")
    assert not result.passed
    assert reason in result.detail


@pytest.mark.parametrize("text", ["", "export default function() {}", "```python\npass\n```",
                                        "```javascript\n\n```"])
def test_missing_code_block(text, tmp_path):
    result = k6_exec(text, {}, tmp_path)
    assert not result.passed
    assert result.detail == "No javascript code block found"


def test_nonzero_and_timeout(stub, tmp_path):
    stub([point()], exit_code=1)
    assert k6_exec("```js\nx\n```", {}, tmp_path / "exit").detail == "k6 exit code 1"
    stub([], delay=90)
    assert k6_exec("```js\nx\n```", {"timeout": .1}, tmp_path / "timeout").detail == "k6 timed out"


@pytest.mark.parametrize("vus,passed", [([1, 2], True), ([1, 1], False)])
def test_named_requests_use_real_debug_paths(stub, tmp_path, vus, passed):
    points = [point("1"), point("2")]
    for sample in points:
        sample["data"]["tags"].update({"url": "checkout", "name": "checkout"})
    requests = [{"source": "http-debug", "vu": vu,
                 "msg": "Request:\nPOST /checkout HTTP/1.1\r\nHost: 127.0.0.1:1234\r\n"}
                for vu in vus]
    requests.append({"source": "http-debug", "vu": 3,
                     "msg": "Request:\nGET /products HTTP/1.1\r\n"})
    stub(points, requests=requests)
    result = k6_exec("```js\nx\n```", {"completions": 2, "min_checkout_vus": 2}, tmp_path / "run")
    assert result.passed is passed


def test_k6_preflight_before_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("K6_PATH", str(tmp_path / "absent"))
    with pytest.raises(ValueError, match="set K6_PATH"):
        k6_executable()
    task = Task("k6", "authoring", "k6", "test", "k6_exec", {}, {})
    with pytest.raises(ValueError, match="set K6_PATH"):
        run(None, "fake", "model", Price(0, 0, 0), [task], tmp_path / "results")
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize("metadata", [True, False])
@pytest.mark.parametrize("debug_iterations,metric_samples,passed", [
    ([None, None], [("1", "0"), ("1", "1")], True),
    (["0", None], [("1", "0"), ("1", "1")], True),
    ([None, None], [("1", "0"), ("1", "0")], False),
    ([None, None], [("1", "0"), ("2", "1")], False),
    ([None, None], [("1", None), ("1", None)], False),
    (["0", "0"], [("1", "0"), ("1", "1")], False),
])
def test_debug_iteration_fallback(stub, tmp_path, metadata, debug_iterations, metric_samples, passed):
    points = [point(vu, iteration=iteration) for vu, iteration in metric_samples]
    if not metadata:
        for sample in points:
            sample["data"]["tags"].update(sample["data"].pop("metadata"))
    requests = [{"source": "http-debug", "vu": 1,
                 "msg": "Request:\nPOST /checkout HTTP/1.1\r\n",
                 **({"iter": iteration} if iteration is not None else {})}
                for iteration in debug_iterations]
    stub(points, requests=requests)
    grade = k6_exec("```js\nx\n```", {"completions": 2, "expected_checkout_vus": 1,
                    "min_checkout_iterations": 2}, tmp_path / "run")
    assert grade.passed is passed


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("samples,reason", [
    ([("1", "0"), ("1", "1")], "Verified completions: 2/2"),
    ([("1", "0"), ("2", "0")], "Checkout VUs: 2/1 expected"),
    ([("1", "0"), ("1", "0")], "Checkout iterations: 1/2"),
    ([("1", "0"), ("1", None)], "Missing checkout iteration metadata"),
    ([("1", "0"), (None, "1")], "Missing checkout VU metadata"),
    ([], "Empty run: no http_reqs points"),
])
def test_token_refresh_run_shape(stub, tmp_path, debug, samples, reason):
    points = [point(vu, iteration=iteration) for vu, iteration in samples]
    requests = None
    if debug:
        for sample in points:
            sample["data"]["tags"]["url"] = "checkout"
        requests = [{"source": "http-debug", "vu": vu, "iter": iteration,
                     "msg": "Request:\nPOST /checkout HTTP/1.1\r\n"}
                    for vu, iteration in samples]
        requests.append({"source": "http-debug", "vu": 3, "iter": 9,
                         "msg": "Request:\nGET /products HTTP/1.1\r\n"})
    else:
        for sample in points:
            sample["data"]["tags"].update(sample["data"].pop("metadata"))
    stub(points, requests=requests)
    params = {"completions": 2, "expected_checkout_vus": 1, "min_checkout_iterations": 2}
    result = k6_exec("```js\nx\n```", params, tmp_path / "run")
    assert result.detail == reason
    assert result.passed is reason.startswith("Verified")


def test_tool_filter_and_alias(tmp_path, monkeypatch):
    selected = []

    def fake_run(provider, provider_name, model, price, tasks, out, repeats):
        selected.extend(tasks)
        return tmp_path, {}

    monkeypatch.setattr("p95bench.cli.run", fake_run)
    assert main(["execute", "--provider", "deepseek", "--model", "deepseek-flash",
                 "--tool", "k6"]) == 0
    assert len(selected) == 3 and all(t.tool == "k6" for t in selected)
    selected.clear()
    assert main(["run", "--provider", "deepseek", "--model", "deepseek-flash",
                 "--tool", "k6", "--tool", "jmeter"]) == 0
    assert len(selected) == 6
    assert main(["run", "--provider", "deepseek", "--model", "deepseek-flash",
                 "--tool", "locust"]) == 1


def test_mirrored_params_and_prompts():
    tasks = {t.id: t for t in load_tasks(Path(__file__).resolve().parents[1] / "tasks")}
    for suffix in ["shop-flow", "session-isolation", "token-refresh"]:
        k6, jmeter = tasks["k6-" + suffix], tasks[suffix]
        expected = dict(jmeter.params)
        if "min_checkout_threads" in expected:
            expected["min_checkout_vus"] = expected.pop("min_checkout_threads")
        if suffix == "token-refresh":
            expected.update(expected_checkout_vus=1, min_checkout_iterations=2)
        assert k6.params == expected
        assert k6.category == jmeter.category
        assert "__ENV.BASE_URL" in k6.prompt
        assert "single javascript code block" in k6.prompt
        assert "k6 v1.0.0" in k6.prompt
