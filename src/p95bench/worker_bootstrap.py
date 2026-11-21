"""Container-local public HTTP relay; no evaluator control or credentials."""

import json
import os
import selectors
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path


def stop_actors():
    if os.geteuid() != 0:
        raise PermissionError("Supervisor privileges required")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        active = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                status = dict(line.split(":", 1) for line in (entry / "status").read_text().splitlines()
                              if ":" in line)
                if int(status["Uid"].split()[0]) == 65534 and not status["State"].strip().startswith("Z"):
                    active = True
                    os.kill(int(entry.name), signal.SIGKILL)
            except (ProcessLookupError, FileNotFoundError):
                pass
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass
        if not active:
            return
        time.sleep(.01)
    raise RuntimeError("Generated processes did not stop")


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        with connect_upstream() as upstream, selectors.DefaultSelector() as selector:
            selector.register(self.request, selectors.EVENT_READ, upstream)
            selector.register(upstream, selectors.EVENT_READ, self.request)
            while True:
                ready = selector.select(3)
                if not ready:
                    return
                for key, _ in ready:
                    data = key.fileobj.recv(65536)
                    if not data:
                        return
                    key.data.sendall(data)


def connect_upstream(path="/public.sock"):
    upstream = socket.socket(socket.AF_UNIX)
    upstream.settimeout(3)
    deadline = time.monotonic() + .2
    try:
        while True:
            try:
                upstream.connect(path)
                return upstream
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(.01, remaining))
    except BaseException:
        upstream.close()
        raise


class RelayServer(socketserver.ThreadingTCPServer):
    request_queue_size = 128


if __name__ == "__main__":
    if sys.argv[1] == "--stop":
        stop_actors()
        sys.exit(0)
    supervised = sys.argv[1] == "--supervised"
    command = sys.argv[2:] if supervised else sys.argv[1:]
    with RelayServer(("127.0.0.1", 8000), Relay) as server:
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        identity = {"user": 65534, "group": 65534, "extra_groups": [], "umask": 0o022} if supervised else {}
        try:
            result = subprocess.run(command, check=False, cwd="/output", env={
                "PATH": os.environ["PATH"], "HOME": "/tmp", "BASE_URL": "http://127.0.0.1:8000",
                "JAVA_HOME": os.environ.get("JAVA_HOME", "")}, **identity)
            code = result.returncode
        except OSError:
            code = 127
        if supervised:
            stop_actors()
            pending = Path("/control/state.pending")
            pending.write_text(json.dumps({"state": "done", "returncode": code}))
            pending.replace("/control/state.json")
            threading.Event().wait()
        sys.exit(code if code >= 0 else 128 - code)
