#!/usr/bin/env bash
set -euo pipefail

# Run inside a Docker Sandbox microVM (Linux amd64) to prepare the p95-bench v2 evaluator.
# Makes zero model API calls.

REPO_DIR="${1:-$(pwd)}"
VERIFY="${VERIFY:-0}"
PROBE_IMAGE="python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b"

cd "$REPO_DIR"

echo "==> Checking Python 3.11+ and Docker..."
python3 - <<'PY'
import sys
assert sys.version_info >= (3, 11), f"Python 3.11+ required, got {sys.version}"
print(f"Python {sys.version.split()[0]} OK")
PY

docker info --format '{{json .SecurityOptions}}' | grep -q 'name=seccomp,profile=builtin' || {
  echo "ERROR: Docker built-in seccomp profile is required for p95-bench v2 workers." >&2
  exit 1
}
echo "Docker + built-in seccomp OK"

echo "==> Setting up virtualenv and installing p95bench..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'

echo "==> Pulling pinned probe image and building native JMeter/k6 worker images..."
docker pull "$PROBE_IMAGE"
.venv/bin/python -m p95bench.images build

if [ "$VERIFY" = "1" ]; then
  echo "==> Running offline Docker integration suite (no provider API calls)..."
  P95_TEST_DOCKER=1 .venv/bin/python -m pytest -q
fi

echo "==> Evaluator setup complete."
