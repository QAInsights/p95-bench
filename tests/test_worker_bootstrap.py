from unittest.mock import Mock

import pytest

from p95bench.worker_bootstrap import RelayServer, connect_upstream


def test_relay_server_uses_a_larger_accept_queue():
    assert RelayServer.request_queue_size == 128


def test_connect_upstream_retries_temporary_queue_saturation(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.attempts = 0
            self.path = None
            self.timeout = None

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, path):
            self.attempts += 1
            self.path = path
            if self.attempts < 3:
                raise BlockingIOError

        def close(self):
            pass

    upstream = FakeSocket()
    monkeypatch.setattr("p95bench.worker_bootstrap.socket.socket", lambda *_: upstream)
    monkeypatch.setattr("p95bench.worker_bootstrap.time.sleep", Mock())

    assert connect_upstream("/test.sock") is upstream
    assert upstream.attempts == 3
    assert upstream.path == "/test.sock"
    assert upstream.timeout == 3


def test_connect_upstream_bounds_retries(monkeypatch):
    upstream = Mock()
    upstream.connect.side_effect = BlockingIOError
    monkeypatch.setattr("p95bench.worker_bootstrap.socket.socket", lambda *_: upstream)
    monkeypatch.setattr("p95bench.worker_bootstrap.time.monotonic",
                        Mock(side_effect=[0, 0, .21]))
    monkeypatch.setattr("p95bench.worker_bootstrap.time.sleep", Mock())

    with pytest.raises(BlockingIOError):
        connect_upstream("/test.sock")
    assert upstream.connect.call_count == 2
    upstream.close.assert_called_once()
