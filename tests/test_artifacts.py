import os

import pytest

from p95bench.images import tool_image
from p95bench.worker import Resources, Worker, execution_trial


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in Docker tests")
def test_supervised_export_is_bounded_and_not_actor_forgeable(tmp_path):
    program = """
import os, pathlib, subprocess
assert os.getuid() == 65534
assert 'CapEff:\\t0000000000000000' in pathlib.Path('/proc/self/status').read_text()
try:
    pathlib.Path('/control/state.json').write_text('{"state":"done","returncode":0}')
except PermissionError:
    pass
else:
    raise AssertionError('Actor can forge supervisor state')
pathlib.Path('/output/ok.txt').write_text('verified')
pathlib.Path('/output/large.txt').write_text('x' * 1000)
pathlib.Path('/output/link.txt').symlink_to('/etc/passwd')
os.mkfifo('/output/fifo.txt')
"""
    with execution_trial(tmp_path) as (_, worker):
        result = worker.execute(["python3", "-c", program], exports={
            "ok.txt": 100, "large.txt": 10, "link.txt": 100, "fifo.txt": 100})
        assert result.status == "completed" and result.returncode == 0, result.output
        assert result.artifacts == {"ok.txt": b"verified"}
        assert set(result.artifact_errors) == {"large.txt", "link.txt", "fifo.txt"}
        timeout = worker.execute(["python3", "-c",
            "import pathlib,time; pathlib.Path('/output/partial').write_text('retained'); time.sleep(60)"],
            timeout=2, exports={"partial": 100})
        assert timeout.status == "timeout"
        assert timeout.artifacts == {"partial": b"retained"}
        directory = worker.execute(["python3", "-c", "import os; os.mkdir('/output/directory')"],
                                   exports={"directory": 10})
        assert not directory.artifacts and set(directory.artifact_errors) == {"directory"}


@pytest.mark.skipif(os.environ.get("P95_TEST_DOCKER") != "1", reason="Opt-in Docker tests")
@pytest.mark.parametrize("tool, command, version", [
    ("k6", ["k6", "version"], "k6 v1.0.0"),
    ("jmeter", ["java", "-Xmx128m", "-Djava.awt.headless=true", "-Duser.home=/tmp", "-jar",
                "/opt/apache-jmeter-5.6.3/bin/ApacheJMeter.jar", "--version"], "The Apache Software Foundation")])
def test_pinned_native_worker_versions(tmp_path, tool, command, version):
    with execution_trial(tmp_path, image=tool_image(tool), resources=Resources(768, 256, 512)) as (_, worker):
        result = worker.execute(command, timeout=20)
        assert result.status == "completed" and result.returncode == 0, result.output
        assert version in result.output
        assert "ClassNotFoundException" not in result.output
        assert ("5.6.3" if tool == "jmeter" else "v1.0.0") in result.output


def test_export_contract_rejected_before_runtime():
    worker = object.__new__(Worker)
    for exports in ({"../escape": 10}, {"a": 0}, {"a": True}, {"a": 9 * 1048576}):
        with pytest.raises(ValueError, match="export"):
            worker.execute(["unused"], exports=exports)
