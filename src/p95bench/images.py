"""Reproducible local image builds; runtime execution always uses immutable IDs."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.request import urlopen

from p95bench.worker import WorkerUnavailable

STATE = Path.home() / ".local/state/p95-bench/tool-images.json"
VERSIONS = {"jmeter": "5.6.3", "k6": "1.0.0"}
ARCHIVE = "apache-jmeter-5.6.3.tgz"
ARCHIVE_SHA512 = ("5978a1a35edb5a7d428e270564ff49d2b1b257a65e17a759d259a9283fc17093e"
                  "522fe46f474a043864aea6910683486340706d745fcdf3db1505fd71e689083")


def image_state(state=None):
    return Path(state if state is not None else os.environ.get("P95_TOOL_IMAGES", STATE)).expanduser().resolve()


def tool_image(tool, state=None):
    path = image_state(state)
    try:
        data = json.loads(path.read_text())
        record = data["images"][tool]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise WorkerUnavailable("Pinned tool images must be built and recorded before execution") from error
    if (not isinstance(record, dict) or data.get("schema_version") != 1
            or record.get("version") != VERSIONS.get(tool)):
        raise WorkerUnavailable("Worker image version mismatch; rebuild pinned tool images")
    image = record.get("id")
    if (not isinstance(image, str) or not image.startswith("sha256:") or len(image) != 71
            or any(ch not in "0123456789abcdef" for ch in image[7:])):
        raise WorkerUnavailable("Expected an immutable local worker image ID")
    return image


def build_images(state=None):
    state = image_state(state)
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = state.parent / ARCHIVE
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        try:
            with urlopen("https://archive.apache.org/dist/jmeter/binaries/" + ARCHIVE,
                         timeout=60) as response, temporary.open("wb") as output:
                size = 0
                while chunk := response.read(1048576):
                    size += len(chunk)
                    if size > 128 * 1048576:
                        raise ValueError("JMeter archive exceeds download limit")
                    output.write(chunk)
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if hashlib.sha512(archive.read_bytes()).hexdigest() != ARCHIVE_SHA512:
        raise ValueError("JMeter archive checksum mismatch")
    dockerfile = Path(__file__).resolve().parents[2] / "workers/Dockerfile"
    recipe = dockerfile.read_bytes()
    records = {}
    with tempfile.TemporaryDirectory(dir=state.parent) as folder:
        context = Path(folder)
        (context / "Dockerfile").write_bytes(recipe)
        shutil.copyfile(archive, context / ARCHIVE)
        config = context / "docker-config"
        config.mkdir()
        environment = {"PATH": os.environ["PATH"], "DOCKER_CONFIG": str(config)}
        for tool, version in VERSIONS.items():
            image_file = context / (tool + ".iid")
            subprocess.run(["docker", "build", "--network=none", "--platform=linux/amd64",
                            "--target", tool, "--iidfile", str(image_file), str(context)],
                           check=True, timeout=600, env=environment)
            records[tool] = {"id": image_file.read_text().strip(), "version": version,
                             "recipe_sha256": hashlib.sha256(recipe).hexdigest()}
    temporary = state.with_suffix(".pending")
    temporary.write_text(json.dumps({"schema_version": 1, "images": records}, indent=2) + "\n")
    temporary.replace(state)
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_images(args.state), indent=2))
