"""Fail-closed container boundary for future v2 executable checkpoint graders."""

import json
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from p95bench.demo_v2 import demo_app_v2

PROBE_IMAGE = ("python@sha256:"
               "5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b")


class WorkerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class Execution:
    status: str
    returncode: int | None
    output: str
    duration_seconds: float


class Worker:
    def __init__(self, directory, public_socket, image):
        if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
            raise ValueError("Worker image must be pinned by digest")
        self.directory, self.public_socket, self.image = directory, public_socket, image
        self.docker = shutil.which("docker")
        if not self.docker:
            raise WorkerUnavailable("Docker is required; no host execution fallback")
        configuration = directory / "docker-config"
        configuration.mkdir(mode=0o700)
        self.environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                            "DOCKER_CONFIG": str(configuration)}
        result = self._docker("image", "inspect", "--format", "{{json .Config.Volumes}}", image)
        if result.returncode:
            raise WorkerUnavailable("Pinned worker image must be preloaded")
        if json.loads(result.stdout) not in (None, {}):
            raise WorkerUnavailable("Worker images cannot declare unbounded writable volumes")
        security = self._docker("info", "--format", "{{json .SecurityOptions}}")
        if security.returncode or "name=seccomp,profile=builtin" not in json.loads(security.stdout):
            raise WorkerUnavailable("Worker requires the Docker built-in seccomp policy")

    def _docker(self, *args):
        try:
            return subprocess.run([self.docker, *args], env=self.environment,
                                  capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise WorkerUnavailable("Container runtime unavailable") from error

    def execute(self, command, fixtures=None, *, timeout=30, output_limit=1048576):
        if (not isinstance(command, (list, tuple)) or not command or len(command) > 64
                or any(not isinstance(arg, str) or not arg or len(arg) > 4096 for arg in command)):
            raise ValueError("Invalid execution command")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not 0 < timeout <= 300 or type(output_limit) is not int
                or not 1 <= output_limit <= 8 * 1048576):
            raise ValueError("Invalid execution limits")
        fixtures = {} if fixtures is None else fixtures
        if (not isinstance(fixtures, dict) or len(fixtures) > 32
                or any(not isinstance(name, str)
                       or not re.fullmatch(r"[\w.-]+", name, flags=re.ASCII)
                       or name in (".", "..") or not isinstance(data, bytes)
                       for name, data in fixtures.items())
                or sum(map(len, fixtures.values())) > 2 * 1048576):
            raise ValueError("Invalid read-only fixtures")
        name = "p95-" + uuid4().hex
        with tempfile.TemporaryDirectory(dir=self.directory) as folder:
            mounts = []
            for filename, data in fixtures.items():
                file = Path(folder) / filename
                file.write_bytes(data)
                file.chmod(0o444)
                mounts.extend(["--mount", f"type=bind,src={file},dst=/fixtures/{filename},readonly"])
            bootstrap = Path(__file__).with_name("worker_bootstrap.py").resolve()
            options = ["create", "--name", name, "--pull", "never", "--network", "none",
                       "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                       "--user", "65534:65534", "--cpus", "1", "--memory", "256m",
                       "--memory-swap", "256m", "--pids-limit", "64", "--ipc", "private",
                       "--ulimit", "nofile=128:128", "--ulimit", "fsize=8388608:8388608",
                       "--log-driver", "none", "--no-healthcheck", "--workdir", "/output",
                       "--tmpfs", "/output:rw,noexec,nosuid,nodev,size=64m,mode=1777",
                       "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                       "--mount", f"type=bind,src={bootstrap},dst=/bootstrap.py,readonly",
                       "--mount", f"type=bind,src={self.public_socket},dst=/public.sock,readonly",
                       *mounts, "--entrypoint", "python3", self.image, "/bootstrap.py", *command]
            created = False
            try:
                result = self._docker(*options)
                if result.returncode:
                    raise WorkerUnavailable("Container creation failed: " + result.stderr.decode()[:500])
                created = True
                return self._attached(name, timeout, output_limit)
            finally:
                removed = self._docker("rm", "--force", name)
                if created and removed.returncode:
                    raise WorkerUnavailable("Container cleanup failed; stop scheduling trials")

    def _attached(self, name, timeout, output_limit):
        started = time.monotonic()
        output = bytearray()
        status = "completed"
        process = subprocess.Popen([self.docker, "start", "--attach", name], env=self.environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        status = "timeout"
                        break
                    if not selector.select(min(remaining, .1)):
                        continue
                    chunk = os.read(process.stdout.fileno(), min(65536, output_limit + 1))
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > output_limit:
                        del output[output_limit:]
                        status = "output_limit"
                        break
        finally:
            kill_error = None
            try:
                if status != "completed":
                    self._docker("kill", name)
            except BaseException as error:
                kill_error = error
            try:
                self._reap_attachment(process)
            except BaseException as error:
                if kill_error is None:
                    raise
                kill_error.add_note("Attachment cleanup also failed: " + str(error))
            finally:
                process.stdout.close()
            if kill_error is not None:
                raise kill_error
        code = None
        if status == "completed":
            state = self._docker("inspect", "--format", "{{json .State}}", name)
            if state.returncode:
                raise WorkerUnavailable("Missing container exit evidence")
            evidence = json.loads(state.stdout)
            if evidence["Status"] != "exited" or evidence.get("Error"):
                raise WorkerUnavailable("Container did not execute successfully")
            code = evidence["ExitCode"]
            if evidence["OOMKilled"]:
                status = "resource_limit"
        return Execution(status, code, output.decode(errors="replace"), time.monotonic() - started)

    @staticmethod
    def _reap_attachment(process):
        for action in (None, process.terminate, process.kill):
            if action:
                try:
                    action()
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=1)
                return
            except subprocess.TimeoutExpired:
                pass
        raise WorkerUnavailable("Container attachment failed to terminate")


@contextmanager
def execution_trial(workdir, *, profile=None, seed="public-development", image=PROBE_IMAGE):
    """Only a fresh public protocol socket crosses into the untrusted container."""
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if any(ch in str(workdir) for ch in (",", "\n")):
        raise ValueError("Unsupported mount path")
    with (tempfile.TemporaryDirectory(prefix="p95-trial-", dir=workdir) as directory,
          tempfile.TemporaryDirectory(prefix="p95-ipc-") as ipc):
        socket_path = Path(ipc) / "public.sock"
        with demo_app_v2(profile, seed, socket_path=socket_path) as (shop, _):
            yield shop, Worker(Path(directory), socket_path, image)
