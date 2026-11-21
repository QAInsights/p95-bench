import json
import shutil
from pathlib import Path

import pytest

from p95bench.packs import digest
from scripts.regrade_core_analysis import regrade

ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUN = ROOT / "results/p95-core/deepseek-flash"
A01_TASK = "mx-a01-jtl-endpoints-jmeter"
_UNSET = object()


def _copy_run(tmp_path):
    run_dir = tmp_path / "deepseek-flash"
    shutil.copytree(SOURCE_RUN, run_dir)
    return run_dir


def _files(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*")
            if path.is_file()}


def _read_attempts(run_dir):
    return [json.loads(line) for line in (run_dir / "attempts.jsonl").read_text().splitlines()
            if line.strip()]


def _write_attempts(run_dir, attempts):
    (run_dir / "attempts.jsonl").write_text(
        "".join(json.dumps(attempt) + "\n" for attempt in attempts))


def _make_a01_invalid(run_dir, cost_note=_UNSET):
    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["repair_rounds"] = 0
    if cost_note is not _UNSET:
        metadata["cost_note"] = cost_note
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    attempts = _read_attempts(run_dir)
    attempt = next(item for item in attempts if item["task_id"] == A01_TASK)
    attempt["response"]["text"] = "not JSON"
    attempt["repair"] = {"triggered": False}
    _write_attempts(run_dir, attempts)


def test_regrade_rejects_tampered_task_digest_without_writing(tmp_path):
    run_dir = _copy_run(tmp_path)
    manifest_path = run_dir / "pack.json"
    manifest = json.loads(manifest_path.read_text())
    item = next(task for task in manifest["tasks"] if task["id"] == A01_TASK)
    item["task_digest"] = "0" * 64
    contents = dict(manifest)
    contents.pop("digest")
    manifest["digest"] = digest(contents)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    before = _files(run_dir)

    with pytest.raises(RuntimeError, match="Task digest mismatch"):
        regrade(run_dir, "test-commit")

    assert _files(run_dir) == before


def test_regrade_of_already_regraded_run_reconciles_summary_only(tmp_path):
    run_dir = _copy_run(tmp_path)
    attempts_path = run_dir / "attempts.jsonl"
    metadata_path = run_dir / "metadata.json"
    summary_path = run_dir / "summary.json"
    attempts_before = attempts_path.read_bytes()
    metadata_before = metadata_path.read_bytes()
    summary_before = summary_path.read_bytes()
    previous_summary = json.loads(summary_before)

    changed, _, summary = regrade(run_dir, "second-pass")

    assert changed == []
    assert attempts_path.read_bytes() == attempts_before
    assert metadata_path.read_bytes() == metadata_before
    assert json.loads(summary_path.read_text()) == summary
    if previous_summary == summary:
        assert summary_path.read_bytes() == summary_before
    else:
        assert summary_path.read_bytes() == (json.dumps(summary, indent=2) + "\n").encode()


def test_regrade_repairs_stale_summary_without_rewriting_attempts_or_metadata(tmp_path):
    run_dir = _copy_run(tmp_path)
    attempts_path = run_dir / "attempts.jsonl"
    metadata_path = run_dir / "metadata.json"
    attempts_before = attempts_path.read_bytes()
    metadata_before = metadata_path.read_bytes()
    (run_dir / "summary.json").write_text('{"stale": true}\n')

    changed, _, summary = regrade(run_dir, "second-pass")

    assert changed == []
    assert json.loads((run_dir / "summary.json").read_text()) == summary
    assert attempts_path.read_bytes() == attempts_before
    assert metadata_path.read_bytes() == metadata_before


def test_zero_repair_rounds_do_not_raise_for_invalid_first_answer(tmp_path):
    run_dir = _copy_run(tmp_path)
    _make_a01_invalid(run_dir)

    changed, _, _ = regrade(run_dir, "test-commit")

    assert A01_TASK in changed
    updated = _read_attempts(run_dir)
    updated_attempt = next(item for item in updated if item["task_id"] == A01_TASK)
    assert updated_attempt["repair"]["triggered"] is False


def test_changing_regrade_preserves_null_cost_note(tmp_path):
    run_dir = _copy_run(tmp_path)
    _make_a01_invalid(run_dir, cost_note=None)

    changed, _, _ = regrade(run_dir, "test-commit")

    assert A01_TASK in changed
    metadata = json.loads((run_dir / "metadata.json").read_text())
    assert metadata["cost_note"] is None


def test_changing_regrade_rejects_embedded_cost_total_without_writing(tmp_path):
    run_dir = _copy_run(tmp_path)
    _make_a01_invalid(run_dir, cost_note="Manual cost estimate: $1.23")
    before = _files(run_dir)

    with pytest.raises(RuntimeError, match="embedded total"):
        regrade(run_dir, "test-commit")

    assert _files(run_dir) == before
