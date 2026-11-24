import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from p95bench.analysis_v2 import analysis_reference
from p95bench.graders import grade
from p95bench.grades import Checkpoint, CheckpointGrade
from p95bench.packs import digest, pack_config, snapshot
from p95bench.runner import score_run
from p95bench.tasks import load_tasks
from scripts import regrade_core
from scripts.regrade_core_analysis import regrade

ROOT = Path(__file__).resolve().parents[1]
CORE_PACK = ROOT / "packs/p95-core"
A01_TASK = "mx-a01-jtl-endpoints-jmeter"
_UNSET = object()


def test_selective_regrade_preserves_unaffected_grades_repairs_usage_and_skips(tmp_path, monkeypatch):
    run_dir = _copy_run(tmp_path)
    attempts = _read_attempts(run_dir)
    zero = CheckpointGrade((Checkpoint("correct", 0, 100),),
                           regrade_core.SUBSET_REJECTION).to_dict()
    perfect = CheckpointGrade((Checkpoint("correct", 100, 100),)).to_dict()
    target = next(attempt for attempt in attempts
                  if attempt["task_id"] == "mx-m05-pacing-semantics-jmeter")
    target["grade"] = zero
    target["response"]["text"] = (
        '<jmeterTestPlan><doubleProp><name>x</name><value>1</value>'
        '<savedValue>0</savedValue></doubleProp></jmeterTestPlan>')
    target["repair"] = {
        "triggered": True, "reason": "invalid_model_artifact", "feedback": "recorded feedback",
        "response": {**target["response"]},
        "grade": zero, "cost_usd": 0.002,
    }
    attempts[1]["repair"] = {"triggered": True, "skipped": "truncated_at_cap"}
    attempts[2]["repair"] = {
        "triggered": True, "error": "insufficient reserved budget", "response": None,
        "grade": None, "cost_usd": 0,
    }
    _write_attempts(run_dir, attempts)
    before = score_run(run_dir)
    original = tmp_path / "original"
    original.mkdir()
    for name in ("attempts.jsonl", "metadata.json", "pack.json"):
        (original / name).write_bytes((run_dir / name).read_bytes())
    before_lines = (run_dir / "attempts.jsonl").read_text().splitlines()
    metadata = json.loads((run_dir / "metadata.json").read_text())
    calls = []

    def grade_saved(task, text, workdir, v2):
        calls.append((task.tool, text))
        return perfect, None

    monkeypatch.setattr(regrade_core, "_grade_response", grade_saved)
    note, summary = regrade_core.regrade(run_dir, "grader-commit", tmp_path / "artifacts")
    after = _read_attempts(run_dir)
    assert len(calls) == 2
    assert {tool for tool, _ in calls} == {"jmeter"}
    assert note["initial_attempts_scanned"] == 104
    assert note["initial_attempts_regraded"] == 1
    assert note["recorded_repairs_regraded"] == 1
    assert note["score_changes"] == [{"task_id": target["task_id"], "repeat": 0,
                                      "changes": [{"phase": "initial", "before": 0, "after": 1},
                                                  {"phase": "repair", "before": 0, "after": 1}]}]
    after_lines = (run_dir / "attempts.jsonl").read_text().splitlines()
    for old_line, new_line in zip(before_lines, after_lines, strict=True):
        if json.loads(old_line)["task_id"] != target["task_id"]:
            assert old_line == new_line
    for old, new in zip(attempts, after, strict=True):
        assert old["response"] == new["response"]
        assert old["cost_usd"] == new["cost_usd"]
        assert {key: value for key, value in old["repair"].items() if key != "grade"} == {
            key: value for key, value in new["repair"].items() if key != "grade"}
    assert all(summary.get(field) == before.get(field) for field in regrade_core.USAGE_FIELDS)
    new_metadata = json.loads((run_dir / "metadata.json").read_text())
    assert new_metadata.pop("regraded") == [note]
    assert new_metadata == metadata
    monkeypatch.setattr(regrade_core, "_grade_response", lambda *args: pytest.fail("cache missed"))
    with pytest.raises(RuntimeError, match="pre-regrade"):
        regrade_core.regrade(run_dir, "grader-commit", tmp_path / "artifacts")
    regrade_core.regrade(run_dir, "grader-commit", tmp_path / "artifacts",
                          original_run_dir=original)


@pytest.mark.parametrize("tool,detail,text,expected", [
    ("jmeter", regrade_core.SUBSET_REJECTION, "<jmeterTestPlan></jmeterTestPlan>", True),
    ("jmeter", "Other rejection", "<jmeterTestPlan></jmeterTestPlan>", False),
    ("jmeter", regrade_core.SUBSET_REJECTION, "<jmeterTestPlan><unknown/></jmeterTestPlan>", False),
    ("jmeter", regrade_core.SUBSET_REJECTION, "", False),
    ("k6", regrade_core.SUBSET_REJECTION, "<jmeterTestPlan></jmeterTestPlan>", False),
    ("locust", regrade_core.SUBSET_REJECTION, "<jmeterTestPlan></jmeterTestPlan>", False),
    ("gatling", regrade_core.SUBSET_REJECTION, "<jmeterTestPlan></jmeterTestPlan>", False),
])
def test_selective_regrade_requires_original_subset_rejection_and_patched_acceptance(
        tool, detail, text, expected):
    record = {"grade": {"detail": detail}, "response": {"text": text}}
    assert regrade_core.affected_phase(SimpleNamespace(tool=tool), record) is expected


def test_selective_regrade_can_change_only_the_recorded_repair(tmp_path, monkeypatch):
    run_dir = _copy_run(tmp_path)
    attempts = _read_attempts(run_dir)
    target = next(attempt for attempt in attempts if attempt["task_id"].endswith("jmeter"))
    original_grade = target["grade"]
    target["repair"] = {
        "triggered": True, "grade": {**original_grade, "detail": regrade_core.SUBSET_REJECTION},
        "response": {**target["response"], "text": "<jmeterTestPlan></jmeterTestPlan>"},
        "cost_usd": 0,
    }
    _write_attempts(run_dir, attempts)
    monkeypatch.setattr(regrade_core, "_grade_response", lambda *args: (original_grade, None))
    note, _ = regrade_core.regrade(run_dir, "grader-commit", tmp_path / "artifacts")
    updated = next(attempt for attempt in _read_attempts(run_dir)
                   if attempt["task_id"] == target["task_id"])
    assert updated["grade"] == original_grade
    assert note["initial_attempts_regraded"] == 0
    assert note["recorded_repairs_regraded"] == 1
    assert updated["offline_regrades"][-1]["initial_regraded"] is False


def test_full_regrade_empty_run_makes_no_grader_calls(tmp_path, monkeypatch):
    run_dir = _copy_run(tmp_path)
    _write_attempts(run_dir, [])
    monkeypatch.setattr(regrade_core, "_grade_response", lambda *args: pytest.fail("empty run"))
    note, summary = regrade_core.regrade(run_dir, "grader-commit", tmp_path / "artifacts")
    assert note["initial_attempts_regraded"] == 0
    assert note["recorded_repairs_regraded"] == 0
    assert summary["attempts"] == 0


@pytest.mark.parametrize("failure", ["task_digest", "worker_error"])
def test_full_regrade_failure_does_not_rewrite_run(tmp_path, monkeypatch, failure):
    run_dir = _copy_run(tmp_path)
    if failure == "task_digest":
        manifest_path = run_dir / "pack.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["tasks"][0]["task_digest"] = "0" * 64
        manifest["digest"] = digest({key: value for key, value in manifest.items()
                                     if key != "digest"})
        manifest_path.write_text(json.dumps(manifest))
        attempts = _read_attempts(run_dir)
        for attempt in attempts:
            attempt["pack_digest"] = manifest["digest"]
        _write_attempts(run_dir, attempts)
    else:
        attempts = _read_attempts(run_dir)
        target = next(attempt for attempt in attempts if attempt["task_id"].endswith("jmeter"))
        target["grade"]["detail"] = regrade_core.SUBSET_REJECTION
        target["response"]["text"] = "<jmeterTestPlan></jmeterTestPlan>"
        _write_attempts(run_dir, attempts)
    monkeypatch.setattr(regrade_core, "_grade_response", lambda *args: (
        {"status": "not_run"}, {"type": "WorkerUnavailable", "message": "worker down"}))
    before = _files(run_dir)
    with pytest.raises(RuntimeError):
        regrade_core.regrade(run_dir, "grader-commit", tmp_path / "artifacts")
    assert _files(run_dir) == before


@pytest.mark.parametrize("response,expected", [
    (None, False), ({"text": "", "input_tokens": 0, "output_tokens": 0}, False),
    ({"text": "", "input_tokens": 10, "output_tokens": 0}, True),
    ({"text": "saved answer"}, True),
])
def test_full_regrade_distinguishes_unsent_repairs_from_empty_responses(response, expected):
    assert regrade_core.recorded_repair({"repair": {"response": response}}) is expected


def _copy_run(tmp_path):
    run_dir = tmp_path / "synthetic-core"
    run_dir.mkdir()
    tasks = load_tasks(CORE_PACK / "tasks", pack_config("p95-core")["weights"])
    budgets = json.loads((CORE_PACK / "efficiency-budgets.json").read_text())
    manifest = snapshot("p95-core", tasks, repeats=1, budgets=budgets)
    metadata = {
        "pack_id": "p95-core",
        "pack_digest": manifest["digest"],
        "visibility": "public",
        "model": "deepseek-flash",
        "provider": "deepseek",
        "harness_commit": "0123456789abcdef0123456789abcdef01234567",
        "harness_dirty": False,
        "repair_rounds": 1,
        "reasoning_mode": "thinking-high",
        "output_cap": 65536,
        "cost_note": (
            "USD list-price estimate from reported token usage at frozen registry prices for all "
            "104 Core first tries and 0 repair responses; Core usage cost $0.104000. Not an invoice."
        ),
    }
    (run_dir / "pack.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    exec_grade = CheckpointGrade((Checkpoint("correct", 100, 100),)).to_dict()
    attempts = []
    for task in tasks:
        if task.grader == "v2_analysis":
            text = json.dumps(analysis_reference(task.params, task.fixtures))
            task_grade = grade(task, text, tmp_path / "grade-init" / task.id, 2).to_dict()
        else:
            text = "ok"
            task_grade = exec_grade
        attempts.append({
            "task_id": task.id,
            "category": task.category,
            "scenario": task.scenario,
            "repeat": 0,
            "provider": "deepseek",
            "model": "deepseek-flash",
            "pack_id": "p95-core",
            "pack_digest": manifest["digest"],
            "grade": task_grade,
            "response": {
                "text": text,
                "input_tokens": 100,
                "output_tokens": 50,
                "cached_tokens": 0,
                "latency_seconds": 1.0,
                "response_latency_seconds": 1.0,
            },
            "repair": {"triggered": False},
            "cost_usd": 0.001,
        })
    (run_dir / "attempts.jsonl").write_text(
        "".join(json.dumps(attempt) + "\n" for attempt in attempts)
    )
    score_run(run_dir)
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
