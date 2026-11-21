import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from p95bench import images


@pytest.mark.parametrize("invocation", ["function", "cli"])
@pytest.mark.parametrize("explicit", [False, True])
def test_image_build_and_execution_share_state_override(tmp_path, monkeypatch, invocation, explicit):
    home = tmp_path / "home"
    default = home / ".local/state/p95-bench/tool-images.json"
    environment = tmp_path / "override/images.json"
    specified = tmp_path / "explicit state/images.json"
    for state in (default, environment, specified):
        state.parent.mkdir(parents=True)
        (state.parent / images.ARCHIVE).write_bytes(b"trusted archive fixture")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("P95_TOOL_IMAGES", str(environment))
    monkeypatch.setattr(hashlib, "sha512", lambda data: SimpleNamespace(hexdigest=lambda: images.ARCHIVE_SHA512))
    monkeypatch.setattr(images, "download_gatling_jars", lambda lock, cache: [])

    def build(command, **kwargs):
        if command[1:3] == ["-m", "pip"]:
            wheelhouse = Path(command[command.index("--dest") + 1])
            (wheelhouse / "locust-2.47.0-py3-none-any.whl").write_bytes(b"locust wheel fixture")
            return subprocess.CompletedProcess(command, 0)
        Path(command[command.index("--iidfile") + 1]).write_text("sha256:" + "a" * 64)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", build)
    if invocation == "cli":
        images.main(["build", *(["--state", str(specified)] if explicit else [])])
    else:
        images.build_images(*([specified] if explicit else []))
    expected = specified if explicit else environment
    assert expected.exists()
    assert not default.exists()
    assert images.tool_image("k6", specified if explicit else None) == "sha256:" + "a" * 64
    assert images.tool_image("locust", specified if explicit else None) == "sha256:" + "a" * 64
    assert images.tool_image("gatling", specified if explicit else None) == "sha256:" + "a" * 64
