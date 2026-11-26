import json

import pytest

from p95bench.packs import pack_config
from p95bench.tasks import load_tasks
from scripts import publish_feather_wand as publish

CONFIG = {"provider": "anthropic", "model": "claude-sonnet-5-5",
          "prices": {"input": 2, "cached": .1, "cache_write": 2.5, "output": 10}}


def job(task, path):
    return task, path, path / "evidence", "digest", "commit", CONFIG


def test_usage_prices_cache_creation_reads_and_output_separately():
    row = {"status": "ok", "input_total": 60, "fresh_input": 10, "cache_read": 20,
           "cache_write": 30, "output": 40, "reasoning": 5}
    usage, cost = publish.response_usage([row, row], CONFIG["prices"])
    assert usage["turns"] == 2
    assert usage["input_tokens"] == 120
    assert usage["output_tokens"] == 80
    assert cost == pytest.approx(2 * (10 * 2 + 20 * .1 + 30 * 2.5 + 40 * 10) / 1_000_000)


@pytest.mark.parametrize("rows", [[], [{"status": "error"}], [{
    "status": "ok", "input_total": 0, "fresh_input": 1, "cache_read": 0,
    "cache_write": 0, "output": 2, "reasoning": 0}]])
def test_usage_refuses_empty_failed_or_inconsistent_turns(rows):
    with pytest.raises(ValueError):
        publish.response_usage(rows, CONFIG["prices"])


@pytest.mark.parametrize("text", ["/home/person/run/file.jmx", "/Users/person/run", "C:\\Users\\person\\run",
                                  "sk-ant-" + "x" * 25])
def test_publication_refuses_local_paths_and_credentials(text):
    with pytest.raises(ValueError, match="local path or credential"):
        publish.public_text(text)


def test_saved_submission_regrade_preserves_only_recorded_gui_repair(tmp_path, monkeypatch):
    task = next(t for t in load_tasks(publish.ROOT / 'packs/p95-core/tasks',
                                     pack_config('p95-core')['weights']) if t.grader == 'v2_analysis'
                and t.tool == 'jmeter')
    result = {"task": task.id, "status": "completed", "repair": True, "turns": 2,
              "fw_cost_usd": .00004}
    (tmp_path / 'result.json').write_text(json.dumps(result))
    row = {"status": "ok", "input_total": 0, "fresh_input": 0, "cache_read": 0,
           "cache_write": 0, "output": 2, "reasoning": 0}
    trigger = ['invalid_model_answer', 'Invalid model answer']
    (tmp_path / 'initial-repair-trigger.json').write_text(json.dumps(trigger))
    feedback = ('Your previous answer failed before it could be scored:\n\n' + trigger[1]
                + '\n\nReturn the complete corrected answer under the same output contract.')
    (tmp_path / 'repair-prompt.txt').write_text(feedback)
    for phase in ('initial', 'repair'):
        (tmp_path / f'{phase}-usage.json').write_text(json.dumps([row]))
        (tmp_path / f'{phase}-answer.txt').write_text(phase)
    calls = []

    def grade(task, text, workdir, v2):
        calls.append(text)
        return {"status": "graded", "score": .5 if text == 'repair' else 1}, None

    monkeypatch.setattr(publish, '_grade_response', grade)
    attempt = publish.grade_saved(job(task, tmp_path))
    assert calls == ['initial', 'repair']
    assert attempt['grade']['score'] == 1
    assert attempt['repair']['grade']['score'] == .5
    assert attempt['repair']['feedback'] == feedback
    assert publish.grade_saved(job(task, tmp_path)) == attempt
    assert calls == ['initial', 'repair']


def test_credit_failed_task_is_not_graded(tmp_path, monkeypatch):
    task = next(t for t in load_tasks(publish.ROOT / 'packs/p95-core/tasks',
                                     pack_config('p95-core')['weights']) if t.tool == 'jmeter')
    (tmp_path / 'result.json').write_text(json.dumps({"task": task.id, "status": "not-run"}))
    monkeypatch.setattr(publish, '_grade_response', lambda *a: pytest.fail('paid failure graded'))
    with pytest.raises(ValueError, match='valid completed task'):
        publish.grade_saved(job(task, tmp_path))


def test_reused_c03_normalizes_native_usage_without_publishing_old_metadata(tmp_path, monkeypatch):
    task = next(t for t in load_tasks(publish.ROOT / 'packs/p95-core/tasks',
                                     pack_config('p95-core')['weights'])
                if t.id == 'mx-c03-json-parent-child-jmeter')
    result = {"task": task.id, "status": "reused", "repair": False, "turns": 1,
              "fw_cost_usd": .0000146, "source": "/home/private/old-run"}
    (tmp_path / 'result.json').write_text(json.dumps(result))
    source = tmp_path / 'reused-c03'
    source.mkdir()
    row = {"status": "ok", "input_tokens": 3, "fresh_input_tokens": 1,
           "cache_read_input_tokens": 1, "cache_write_input_tokens": 1,
           "output_tokens": 1, "reasoning_tokens": 0}
    (source / 'usage.jsonl').write_text(json.dumps(row) + '\n')
    (source / 'initial-model.jmx').write_text('<jmeterTestPlan></jmeterTestPlan>')
    monkeypatch.setattr(publish, '_grade_response', lambda *a: ({"status": "graded", "score": 1}, None))
    attempt = publish.grade_saved(job(task, tmp_path))
    assert attempt['response']['input_tokens'] == 3
    assert attempt['response']['cached_tokens'] == 1
    assert attempt['response']['cache_write_tokens'] == 1
    assert attempt['repair'] == {"triggered": False}
    assert '/home/' not in json.dumps(attempt)
