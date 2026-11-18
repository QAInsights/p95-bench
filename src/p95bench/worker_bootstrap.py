"""Container-local public HTTP relay; no evaluator control or credentials."""

import os
import selectors
import socket
import socketserver
import subprocess
import sys
import threading


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.socket(socket.AF_UNIX) as upstream:
            upstream.settimeout(3)
            upstream.connect("/public.sock")
            with selectors.DefaultSelector() as selector:
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


if __name__ == "__main__":
    with socketserver.ThreadingTCPServer(("127.0.0.1", 8000), Relay) as server:
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        result = subprocess.run(sys.argv[1:], check=False, cwd="/output", env={
            "PATH": os.environ["PATH"], "HOME": "/tmp", "BASE_URL": "http://127.0.0.1:8000"})
        sys.exit(result.returncode if result.returncode >= 0 else 128 - result.returncode)
