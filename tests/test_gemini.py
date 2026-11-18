import io
import json
from dataclasses import replace
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError

import pytest

from p95bench.providers import (
    PROVIDERS,
    Gemini,
    ProviderUnavailable,
    SpendLimitExceeded,
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
    ('gemini-4-hydrogen', (1.5, .075, 7.5)), ('gemini-3.8-flash', (.75, .075, 3.75)),
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
        assert json.loads(req.data) == {'systemInstruction': {'parts': [{'text': 'system'}]},
            'contents': [{'role': 'user', 'parts': [{'text': 'prompt'}]},
                         {'role': 'model', 'parts': [{'text': 'previous'}]}],
            'generationConfig': {'maxOutputTokens': 8192}}
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
    assert response.output_tokens == 0
    assert not response.thinking_exhausted


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


@pytest.mark.parametrize('error', [429, 503, URLError('network'), IncompleteRead(b'partial')])
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
    provider = Gemini(replace(PROVIDERS['gemini'], max_retries=1), spend_limit=3)
    task = Task('test', 'workload', 'none', 'prompt', 'numeric', {'answer': 10}, {})
    directory, summary = run(provider, 'gemini', 'gemini-4-carbon',
                             provider.config.prices['gemini-4-carbon'], [task], tmp_path)
    assert summary['attempts'] == 0
    assert json.loads((directory / 'not-run.jsonl').read_text())['status'] == 'not_run'
    assert sleeps == [1]
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
