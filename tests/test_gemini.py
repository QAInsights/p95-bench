import io
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from p95bench.providers import (
    PROVIDERS,
    Gemini,
    Price,
    ProviderUnavailable,
    Response,
    SpendLimitExceeded,
    gemini_prices,
    get_provider,
)
from p95bench.runner import run
from p95bench.tasks import Task


def reply(parts=None, thinking=40, finish='STOP'):
    return {'candidates': [{'content': {'parts': parts if parts is not None else [
        {'text': 'private reasoning', 'thought': True}, {'text': '1'}, {'text': '0'}]},
        'finishReason': finish}], 'usageMetadata': {'promptTokenCount': 100,
        'candidatesTokenCount': 2, 'thoughtsTokenCount': thinking,
        'cachedContentTokenCount': 60}}


@pytest.mark.parametrize('model,rates', [
    ('gemini-4-carbon', (2, .1, 10)), ('gemini-4-argon', (2, .1, 10)),
    ('gemini-4-hydrogen', (1.5, .075, 7.5)), ('gemini-4-neon', (3, .15, 15)),
    ('gemini-3.8-flash', (.75, .075, 3.75)),
])
def test_native_payload_usage_and_prices(monkeypatch, model, rates):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    provider = get_provider('gemini')
    assert type(provider) is Gemini

    def request(req, timeout):
        assert req.full_url == f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
        assert req.get_header('X-goog-api-key') == 'test-key'
        assert req.get_header('Authorization') is None
        assert timeout == 600
        generation = {'maxOutputTokens': 65536}
        if model == 'gemini-4-neon':
            generation['thinkingConfig'] = {'thinkingLevel': 'HIGH'}
        assert json.loads(req.data) == {'systemInstruction': {'parts': [{'text': 'system'}]},
            'contents': [{'role': 'user', 'parts': [{'text': 'prompt'}]},
                         {'role': 'model', 'parts': [{'text': 'previous'}]}],
            'generationConfig': generation}
        return io.BytesIO(json.dumps(reply()).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    response = provider.send(model, [{'role': 'system', 'content': 'system'},
        {'role': 'user', 'content': 'prompt'}, {'role': 'assistant', 'content': 'previous'}])
    assert response.text == '10'
    assert response.output_tokens == 42
    assert response.thinking_tokens == 40
    assert response.cached_tokens == 60
    assert response.thinking_exhausted is False
    price = PROVIDERS['gemini'].prices[model]
    assert (price.input, price.cached, price.output) == rates
    assert price.cost(response) == pytest.approx((40 * rates[0] + 60 * rates[1]
                                                 + 42 * rates[2]) / 1_000_000)


@pytest.mark.parametrize('parts', [[], None])
def test_thinking_only_cap_remains_empty_and_is_billed(monkeypatch, parts):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = reply([], 8192, 'MAX_TOKENS')
    data['usageMetadata']['candidatesTokenCount'] = 0
    data['usageMetadata']['cachedContentTokenCount'] = None
    data['candidates'][0]['content']['parts'] = parts
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    response = get_provider('gemini').send('gemini-4-carbon', [])
    assert response.text == ''
    assert response.output_tokens == 8192
    assert response.thinking_exhausted
    assert PROVIDERS['gemini'].prices['gemini-4-carbon'].cost(response) == pytest.approx(.08212)


def test_blocked_response_is_not_a_thinking_cap(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = {'usageMetadata': {'promptTokenCount': 100}, 'promptFeedback': {'blockReason': 'SAFETY'}}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    response = get_provider('gemini').send('gemini-4-carbon', [])
    assert response.text == ''
    assert response.input_tokens == 100
    assert response.output_tokens == 0
    assert response.finish_reason == 'BLOCKED_SAFETY'
    assert not response.thinking_exhausted


def test_blocked_response_without_usage_is_zero_cost(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = {'usageMetadata': {'serviceTier': 'standard'},
            'promptFeedback': {'blockReason': 'OTHER'}}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    response = get_provider('gemini').send('gemini-4-neon', [])
    assert response.text == ''
    assert response.input_tokens == 0
    assert response.output_tokens == 0
    assert response.finish_reason == 'BLOCKED_OTHER'
    assert PROVIDERS['gemini'].prices['gemini-4-neon'].cost(response) == 0


def test_blocked_empty_candidate_text_is_a_blocked_response(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = {'candidates': [{'content': {'parts': [{'text': ' \n\t'}]}}],
            'usageMetadata': {'serviceTier': 'standard'},
            'promptFeedback': {'blockReason': 'OTHER'}}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    response = get_provider('gemini').send('gemini-4-neon', [])
    assert response.finish_reason == 'BLOCKED_OTHER'
    assert response.input_tokens == response.output_tokens == 0


def test_spend_limited_blocked_response_without_usage_is_returned(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = {'usageMetadata': {'serviceTier': 'standard'},
            'promptFeedback': {'blockReason': 'OTHER'}}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    response = Gemini(PROVIDERS['gemini'], spend_limit=3).send('gemini-4-neon', [])
    assert response.finish_reason == 'BLOCKED_OTHER'
    assert response.input_tokens == response.output_tokens == 0


def test_usage_prompt_tokens_are_derived_from_total_when_omitted(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = reply()
    usage = data['usageMetadata']
    usage['totalTokenCount'] = (usage.pop('promptTokenCount') + usage['candidatesTokenCount']
                                + usage['thoughtsTokenCount'])
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    response = get_provider('gemini').send('gemini-4-neon', [])
    assert response.input_tokens == 100
    assert response.output_tokens == 42


def test_missing_prompt_usage_is_a_provider_error(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = {'candidates': [], 'usageMetadata': {}}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    with pytest.raises(ProviderUnavailable, match='omitted valid prompt token usage'):
        get_provider('gemini').send('gemini-4-neon', [])


def test_contradictory_total_usage_is_a_provider_error(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = reply()
    usage = data['usageMetadata']
    usage.pop('promptTokenCount')
    usage['totalTokenCount'] = usage['candidatesTokenCount'] + usage['thoughtsTokenCount']
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    with pytest.raises(ProviderUnavailable, match='omitted valid prompt token usage'):
        get_provider('gemini').send('gemini-4-neon', [])


def test_models_pagination_and_key(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    with pytest.raises(ValueError, match='Set GEMINI_API_KEY'):
        get_provider('gemini').models()
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    calls = []

    def request(req, timeout):
        calls.append(req.full_url)
        data = {'models': [{'name': 'models/gemini-4-carbon'}], 'nextPageToken': 'a/b'} if len(calls) == 1 else {
            'models': [{'name': 'models/gemini-4-argon'}]}
        return io.BytesIO(json.dumps(data).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    assert get_provider('gemini').models() == ['gemini-4-carbon', 'gemini-4-argon']
    assert calls[-1].endswith('pageToken=a%2Fb')


@pytest.mark.parametrize('error', [503, URLError('network'), IncompleteRead(b'partial')])
def test_retry_recovery_and_budget_reservations(monkeypatch, error):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    sleeps, calls = [], []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)
    provider = Gemini(PROVIDERS['gemini'], spend_limit=3)

    def request(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise error if isinstance(error, Exception) else HTTPError(req.full_url, error, 'busy', {}, None)
        return io.BytesIO(json.dumps(reply()).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    response = provider.send('gemini-4-carbon', [])
    assert len(calls) == 2
    assert sleeps == [1]
    assert provider.spent_upper_bound > PROVIDERS['gemini'].prices['gemini-4-carbon'].cost(response)
    assert provider.spent_upper_bound < 3


def test_budget_stops_before_network_call(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('budget must stop first'))
    with pytest.raises(SpendLimitExceeded):
        Gemini(PROVIDERS['gemini'], spend_limit=.001).send('gemini-4-carbon', [])
    with pytest.raises(ValueError):
        Gemini(PROVIDERS['gemini'], spend_limit=0)


def test_exhausted_requests_are_not_run(monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    sleeps = []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)

    def request(req, timeout):
        raise URLError('unavailable')

    monkeypatch.setattr('urllib.request.urlopen', request)
    provider = Gemini(replace(PROVIDERS['gemini'], max_retries=1), spend_limit=5)
    task = Task('test', 'workload', 'none', 'prompt', 'numeric', {'answer': 10}, {})
    directory, summary = run(provider, 'gemini', 'gemini-4-carbon',
                             provider.config.prices['gemini-4-carbon'], [task], tmp_path)
    assert summary['attempts'] == 0
    assert json.loads((directory / 'not-run.jsonl').read_text())['status'] == 'not_run'
    assert sleeps == [1, 2]
    with pytest.raises(ProviderUnavailable):
        provider.send('gemini-4-carbon', [])


def test_auth_errors_do_not_retry(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr('p95bench.providers.time.sleep', lambda _: pytest.fail('must not retry'))

    def request(req, timeout):
        raise HTTPError(req.full_url, 401, 'Unauthorized', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(HTTPError):
        get_provider('gemini').send('gemini-4-carbon', [])


@pytest.mark.parametrize('completed', [0, 1])
def test_budget_exhaustion_writes_summary_and_skips_remaining_repeats(tmp_path, completed):
    class Budgeted:
        calls = 0

        def send(self, model, messages):
            self.calls += 1
            if self.calls > completed:
                raise SpendLimitExceeded('budget exhausted')
            return Response('10', 10, 1)

    provider = Budgeted()
    tasks = [Task(str(i), 'workload', 'none', 'prompt', 'numeric', {'answer': 10}, {})
             for i in range(3)]
    directory, summary = run(provider, 'gemini', 'gemini-4-carbon',
                             PROVIDERS['gemini'].prices['gemini-4-carbon'], tasks, tmp_path, repeats=2)
    assert provider.calls == completed + 1
    assert summary['attempts'] == completed
    assert json.loads((directory / 'summary.json').read_text()) == summary
    skipped = [json.loads(line) for line in (directory / 'not-run.jsonl').read_text().splitlines()]
    assert [(s['task_id'], s['repeat']) for s in skipped] == [
        (str(i), repeat) for i in range(3) for repeat in range(2)][completed:]
    assert all(s['status'] == 'not_run' and s['error'] == 'budget exhausted' for s in skipped)


@pytest.mark.parametrize('model,day,rates', [
    ('gemini-4-carbon', '2026-11-30T23:59:59+00:00', (2, .1, 10)),
    ('gemini-4-carbon', '2026-12-01T00:00:00+00:00', (4, .2, 20)),
    ('gemini-4-argon', '2026-11-30T23:59:59+00:00', (2, .1, 10)),
    ('gemini-4-argon', '2026-12-01T00:00:00+00:00', (4, .2, 20)),
    ('gemini-3.8-flash', '2026-12-31T23:59:59+00:00', (.75, .075, 3.75)),
    ('gemini-3.8-flash', '2027-01-01T00:00:00+00:00', (1.5, .15, 7.5)),
])
def test_run_date_selects_prices_and_records_snapshot(monkeypatch, tmp_path, model, day, rates):
    when = datetime.fromisoformat(day)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    monkeypatch.setattr('p95bench.runner.datetime', Clock)
    provider = Gemini(PROVIDERS['gemini'])
    monkeypatch.setattr(provider, '_request', lambda *args: reply())
    task = Task('test', 'workload', 'none', 'prompt', 'numeric', {'answer': 10}, {})
    directory, summary = run(provider, 'gemini', model, None,
                             [task], tmp_path)
    metadata = json.loads((directory / 'metadata.json').read_text())
    expected = dict(zip(['input', 'cached', 'output'], rates))
    assert metadata['price_per_million'] == expected
    assert metadata['pricing_date_utc'] == when.date().isoformat()
    assert metadata['run_date'] == when.isoformat()
    attempt = json.loads((directory / 'attempts.jsonl').read_text())
    assert attempt['price_per_million'] == expected
    assert summary['estimated_cost_usd'] == pytest.approx((40 * rates[0] + 60 * rates[1]
                                                         + 42 * rates[2]) / 1_000_000)
    assert provider.config.prices[model] == gemini_prices(when)[model]


def test_model_extra_rejects_output_cap_overrides():
    with pytest.raises(ValueError, match='must not set output cap field'):
        replace(PROVIDERS['gemini'], model_extra={'gemini-custom': {'maxOutputTokens': 1000}})


def test_neon_uses_explicit_high_reasoning_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(reply()).encode()))
    task = Task('test', 'workload', 'none', 'prompt', 'numeric', {'answer': 10}, {})
    directory, _ = run(get_provider('gemini'), 'gemini', 'gemini-4-neon', None, [task], tmp_path)
    metadata = json.loads((directory / 'metadata.json').read_text())
    assert metadata['reasoning_mode'] == 'thinking-high'
    assert metadata['thinkingConfig'] == {'thinkingLevel': 'HIGH'}
    assert metadata['maxOutputTokens'] == 65536


def test_schedule_uses_utc_not_local_calendar_day():
    before = datetime(2026, 12, 1, tzinfo=timezone(timedelta(hours=1)))
    after = datetime(2026, 11, 30, 23, tzinfo=timezone(timedelta(hours=-1)))
    assert gemini_prices(before)['gemini-4-carbon'].input == 2
    assert gemini_prices(after)['gemini-4-carbon'].input == 4
    assert gemini_prices(after)['gemini-4-hydrogen'].input == 1.5
    with pytest.raises(ValueError, match='timezone-aware'):
        gemini_prices(datetime(2026, 12, 1))


@pytest.mark.parametrize('model', ['gemini-custom', 'gemini-4-carbon'])
def test_explicit_gemini_price_is_preserved_for_builtin_and_custom_models(monkeypatch, tmp_path, model):
    provider = Gemini(PROVIDERS['gemini'])
    monkeypatch.setattr(provider, '_request', lambda *args: reply())
    price = Price(7, .5, 30)
    task = Task('test', 'workload', 'none', 'prompt', 'numeric', {'answer': 10}, {})
    directory, summary = run(provider, 'gemini', model, price, [task], tmp_path)
    assert summary['estimated_cost_usd'] == pytest.approx((40 * 7 + 60 * .5 + 42 * 30) / 1_000_000)
    assert provider.config.prices[model] == price
    assert json.loads((directory / 'metadata.json').read_text())['price_per_million'] == {
        'input': 7, 'cached': .5, 'output': 30}


def test_custom_gemini_model_without_price_has_clear_error(tmp_path):
    with pytest.raises(ValueError, match='No verified price for gemini-custom; supply an explicit price'):
        run(Gemini(PROVIDERS['gemini']), 'gemini', 'gemini-custom', None, [], tmp_path)


def test_gemini_max_tokens_skips_repair_even_below_output_cap(monkeypatch, tmp_path):
    from p95bench.budget import BudgetLedger
    from p95bench.graders import CHECKPOINT_GRADERS
    from p95bench.grades import Checkpoint, CheckpointGrade

    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    data = reply([], 8192, 'MAX_TOKENS')
    data['usageMetadata']['candidatesTokenCount'] = 0
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    monkeypatch.setitem(CHECKPOINT_GRADERS, 'test_trunc', lambda *a: CheckpointGrade(
        (Checkpoint('loads', 0, 100),), 'Invalid model answer (JSONDecodeError): empty'))
    task = Task('t1', 'analysis', 'k6', 'prompt', 'test_trunc', {}, {})
    directory, _ = run(
        get_provider('gemini'), 'gemini', 'gemini-4-carbon', Price(2, .1, 10), [task], tmp_path,
        pack_id='p95-core', efficiency_budgets={'t1': {'tokens': 1000, 'latency_seconds': 90}},
        budget_ledger=BudgetLedger(tmp_path / 'ledger.json'),
    )
    attempt = json.loads((directory / 'attempts.jsonl').read_text())
    assert attempt['repair'] == {'triggered': False, 'skipped': 'truncated_at_cap'}
