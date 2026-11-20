import os
import subprocess
from unittest.mock import MagicMock, Mock

import pytest

from p95bench.worker import PROBE_IMAGE, Execution, Worker, WorkerUnavailable, execution_trial


def test_worker_pins_runtime_and_never_falls_back_to_host(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="pinned"):
        Worker(tmp_path, tmp_path / "public.sock", "python:latest")
    monkeypatch.setattr("p95bench.worker.shutil.which", lambda _: None)
    with pytest.raises(WorkerUnavailable, match="no host"):
        Worker(tmp_path, tmp_path / "public.sock", PROBE_IMAGE)


@pytest.mark.parametrize("runner_destination", ["/locust_runner.py", "/gatling_runner.py"])
def test_worker_policy_and_cleanup_on_infrastructure_failure(tmp_path, runner_destination):
    worker = object.__new__(Worker)
    worker.directory = tmp_path
    worker.public_socket = tmp_path / "public.sock"
    worker.image = PROBE_IMAGE
    worker._docker = Mock(return_value=subprocess.CompletedProcess([], 0, b"", b""))
    worker._attached = Mock(side_effect=WorkerUnavailable("attachment"))
    runner = tmp_path / "locust_runner.py"
    runner.write_text("pass")
    with pytest.raises(WorkerUnavailable, match="attachment"):
        worker.execute(["python3", "-c", "print('synthetic')"], {"input.txt": b"fixture"},
                       runner=runner, runner_destination=runner_destination)
    command = worker._docker.call_args_list[0].args
    for pair in (("--network", "none"), ("--user", "65534:65534"),
                 ("--pids-limit", "64"), ("--memory", "256m"), ("--cpus", "1"),
                 ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges")):
        index = command.index(pair[0])
        assert command[index:index + 2] == pair
    assert "--read-only" in command
    assert any("dst=/fixtures/input.txt,readonly" in arg for arg in command)
    assert any(f"src={runner.resolve()},dst={runner_destination},readonly" in arg for arg in command)
    assert not any("docker.sock" in arg or "dst=/home" in arg for arg in command)
    assert worker._docker.call_args_list[-1].args[:2] == ("rm", "--force")


@pytest.mark.parametrize("exports", [None, {"proof": 10}])
def test_kill_exception_reaps_attachment_before_container_removal(tmp_path, monkeypatch, exports):
    worker = object.__new__(Worker)
    worker.directory, worker.public_socket = tmp_path, tmp_path / "public.sock"
    worker.image, worker.docker, worker.environment = PROBE_IMAGE, "docker", {}
    process = MagicMock()
    process.__enter__.return_value = process
    process.wait.side_effect = [subprocess.TimeoutExpired("attachment", 1),
                               subprocess.TimeoutExpired("attachment", 1), 0]
    monkeypatch.setattr("p95bench.worker.subprocess.Popen", Mock(return_value=process))
    monkeypatch.setattr("p95bench.worker.selectors.DefaultSelector", MagicMock())
    monkeypatch.setattr("p95bench.worker.time.monotonic", Mock(side_effect=[0, *([2] * 10)]))
    def docker(*args):
        if args[0] == "kill":
            raise WorkerUnavailable("kill failed")
        if args[0] == "rm":
            assert process.wait.call_count == 3
        if args[0] == "exec" and args[-1] == "status":
            return subprocess.CompletedProcess([], 0, b'{"state":"done","returncode":0}', b"")
        if args[0] == "exec" and "files" in args:
            return subprocess.CompletedProcess([], 0, b'{"files":{},"errors":{"proof":"missing"}}', b"")
        return subprocess.CompletedProcess([], 0, b"", b"")
    worker._docker = Mock(side_effect=docker)
    with pytest.raises(WorkerUnavailable, match="kill failed"):
        worker.execute(["python3", "-c", "pass"], timeout=1, exports=exports)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert all(call.kwargs == {"timeout": 1} for call in process.wait.call_args_list)
    process.stdout.close.assert_called_once()
    process.__exit__.assert_not_called()
    assert worker._docker.call_args_list[-1].args[:2] == ("rm", "--force")


@pytest.mark.parametrize("volumes, security, error", [
    (b"null", b'["name=seccomp,profile=builtin"]', None),
    (b'{"/unbounded": {}}', b"[]", "volumes"),
    (b"null", b"[]", "seccomp")])
def test_worker_uses_empty_docker_configuration_and_checks_runtime(
        tmp_path, monkeypatch, volumes, security, error):
    monkeypatch.setattr("p95bench.worker.shutil.which", lambda _: "/usr/bin/docker")
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0, volumes, b""),
                            subprocess.CompletedProcess([], 0, security, b"")])
    monkeypatch.setattr("p95bench.worker.subprocess.run", run)
    if error:
        with pytest.raises(WorkerUnavailable, match=error):
            Worker(tmp_path, tmp_path / "public.sock", PROBE_IMAGE)
    else:
        worker = Worker(tmp_path, tmp_path / "public.sock", PROBE_IMAGE)
        assert set(worker.environment) == {"PATH", "DOCKER_CONFIG"}
        assert worker.environment["DOCKER_CONFIG"] == str(tmp_path / "docker-config")
        assert list((tmp_path / "docker-config").iterdir()) == []


@pytest.mark.parametrize("command, fixtures, limits", [([], {}, {}), (["echo"], {"../bad": b"x"}, {}),
    (["echo"], {"input": "not bytes"}, {}), (["echo"], {}, {"timeout": 0}),
    (["echo"], {}, {"output_limit": 0})])
def test_worker_rejects_invalid_inputs_before_execution(command, fixtures, limits):
    worker = object.__new__(Worker)
    with pytest.raises(ValueError):
        worker.execute(command, fixtures, **limits)


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1",
                    reason="Opt-in: requires Docker and the preloaded pinned probe image")
def test_real_container_isolation_public_access_limits_and_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_PROVIDER_API_KEY", "not-mounted")
    probe = """
import http.cookiejar, json, os, pathlib, resource, socket, urllib.request
assert os.getuid() == 65534
assert 'SYNTHETIC_PROVIDER_API_KEY' not in os.environ
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/home/ubuntu').exists()
assert pathlib.Path('/fixtures/input.txt').read_text() == 'fixture'
for filename in ('/etc/forbidden', '/fixtures/input.txt'):
    try:
        open(filename, 'w')
    except OSError:
        pass
    else:
        raise AssertionError('Writable protected filesystem')
pathlib.Path('/output/result').write_text('bounded scratch')
assert pathlib.Path('/sys/fs/cgroup/pids.max').read_text().strip() == '64'
assert pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip() == '268435456'
assert resource.getrlimit(resource.RLIMIT_FSIZE)[0] == 8388608
status = pathlib.Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in status
assert 'NoNewPrivs:\\t1' in status
with socket.socket() as connection:
    connection.settimeout(.2)
    try:
        connection.connect(('1.1.1.1', 443))
    except OSError:
        pass
    else:
        raise AssertionError('External network reachable')
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def request(path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(os.environ['BASE_URL'] + path, data=data, headers=headers or {})
    with opener.open(req, timeout=2) as response:
        return json.load(response)
auth = request('/v2/login', {'username':'bench','password':'bench'})
product = request('/v2/catalog')['data']['products'][0]
headers = {'X-CSRF-Token': auth['csrf'], 'Idempotency-Key':'one'}
cart = request('/v2/carts', {'product_id':product['id'],
    'variant_id':product['variants'][0]['id']}, headers)
order = request('/v2/orders', {'cart_id':cart['id']}, headers)
assert order['accepted']
assert request('/v2/orders', {'cart_id':cart['id']}, headers)['id'] == order['id']
print('PASS: filesystem, credentials, network, capabilities, quotas and public app')
"""
    with execution_trial(tmp_path) as (shop, worker):
        names = []
        docker = worker._docker

        def record(*args):
            if args[0] == "create":
                names.append(args[args.index("--name") + 1])
            return docker(*args)

        monkeypatch.setattr(worker, "_docker", record)
        result = worker.execute(["python3", "-c", probe], {"input.txt": b"fixture"}, timeout=10)
        assert isinstance(result, Execution)
        assert result.status == "completed" and result.returncode == 0, result.output
        assert "PASS:" in result.output
        assert any(e["kind"] == "login" for e in shop.audit())
        assert len([e for e in shop.audit() if e["kind"] == "commit"]) == 1
        timeout = worker.execute(["python3", "-c",
            ("import subprocess,time; subprocess.Popen(['python3','-c','import time; time.sleep(60)']); "
             "time.sleep(60)")], timeout=1)
        assert timeout.status == "timeout"
        flooded = worker.execute(["python3", "-c", "print('x' * 10000)"], output_limit=128)
        assert flooded.status == "output_limit" and len(flooded.output.encode()) <= 128
        for name in names:
            assert docker("inspect", name).returncode != 0
