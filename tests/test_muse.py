import io
import json
from dataclasses import replace
from http.client import IncompleteRead, RemoteDisconnected
from urllib.error import HTTPError, URLError

import pytest

from p95bench.providers import (
    PROVIDERS,
    Muse,
    ProviderUnavailable,
    SpendLimitExceeded,
    get_provider,
)


def reply(text='10', total=22, reasoning=20, finish='stop'):
    return {'choices': [{'message': {'content': text}, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': total,
                      'prompt_tokens_details': {'cached_tokens': 60},
                      'completion_tokens_details': {'reasoning_tokens': reasoning}}}


@pytest.mark.parametrize('model,rates', [
    ('muse-wonder-1.0', (4, .2, 20)), ('muse-spark-1.3', (1.25, .15, 4.25)),
    ('muse-spark-1.2', (1.25, .15, 4.25)), ('muse-ember-1.0', (.1, .004, .5)),
])
def test_muse_payload_and_reasoning_inclusive_price(monkeypatch, model, rates):
    monkeypatch.setenv('MODEL_API_KEY', 'test-key')
    provider = Muse(PROVIDERS['meta'], spend_limit=3)
    messages = [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'prompt'}]

    def request(req, timeout):
        assert req.full_url == 'https://api.meta.ai/v1/chat/completions'
        assert req.get_header('Authorization') == 'Bearer test-key'
        assert json.loads(req.data) == {'model': model, 'messages': messages, 'max_completion_tokens': 8192}
        assert timeout == 600
        return io.BytesIO(json.dumps(reply()).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    response = provider.send(model, messages)
    assert response.text == '10'
    assert response.output_tokens == 22
    assert response.reasoning_tokens == 20
    assert not response.thinking_exhausted
    price = PROVIDERS['meta'].prices[model]
    assert (price.input, price.cached, price.output) == rates
    assert price.cost(response) == pytest.approx((40 * rates[0] + 60 * rates[1]
                                                 + 22 * rates[2]) / 1_000_000)
    assert provider.spent_upper_bound == pytest.approx(price.cost(response))


@pytest.mark.parametrize('text', [None, '', ' '])
def test_reasoning_only_cap_is_empty_and_still_billed(monkeypatch, text):
    monkeypatch.setenv('MODEL_API_KEY', 'test-key')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(
        json.dumps(reply(text, 8192, 8192, 'length')).encode()))
    response = get_provider('meta').send('muse-wonder-1.0', [])
    assert response.thinking_exhausted
    assert response.output_tokens == 8192
    assert PROVIDERS['meta'].prices['muse-wonder-1.0'].cost(response) == pytest.approx(.164012)


def test_null_usage_details_and_non_reasoning_empty_response(monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY', 'test-key')
    data = reply(None, 0, 0, 'content_filter')
    data['usage'].update(prompt_tokens_details=None, completion_tokens_details=None)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(json.dumps(data).encode()))
    response = get_provider('meta').send('muse-wonder-1.0', [])
    assert response.text == ''
    assert response.cached_tokens == 0
    assert response.reasoning_tokens == 0
    assert not response.thinking_exhausted


@pytest.mark.parametrize('error', [429, 503, URLError('network'), RemoteDisconnected('closed'),
                                 IncompleteRead(b'partial'), ConnectionResetError('reset')])
@pytest.mark.parametrize('recover', [True, False])
def test_transient_retry_and_exhaustion(monkeypatch, error, recover):
    monkeypatch.setenv('MODEL_API_KEY', 'test-key')
    calls, sleeps = [], []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)

    def request(req, timeout):
        calls.append(req)
        if len(calls) == 1 or not recover:
            raise error if isinstance(error, Exception) else HTTPError(req.full_url, error, 'busy', {}, None)
        return io.BytesIO(json.dumps(reply()).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    provider = Muse(replace(PROVIDERS['meta'], max_retries=1), spend_limit=3)
    if recover:
        assert provider.send('muse-wonder-1.0', []).text == '10'
    else:
        with pytest.raises(ProviderUnavailable):
            provider.send('muse-wonder-1.0', [])
    assert len(calls) == 2
    assert sleeps == [1]
    assert 0 < provider.spent_upper_bound < 3


def test_missing_key_auth_budget_and_ember_context_guards(monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    assert isinstance(get_provider('meta'), Muse)
    assert set(PROVIDERS['meta'].prices) == {'muse-wonder-1.0', 'muse-spark-1.3',
                                           'muse-spark-1.2', 'muse-ember-1.0'}
    with pytest.raises(ValueError, match='Set MODEL_API_KEY'):
        get_provider('meta').models()
    monkeypatch.setenv('MODEL_API_KEY', 'test-key')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('must stop before request'))
    with pytest.raises(SpendLimitExceeded):
        Muse(PROVIDERS['meta'], spend_limit=.001).send('muse-wonder-1.0', [])
    with pytest.raises(ValueError, match='below 200k'):
        get_provider('meta').send('muse-ember-1.0', [{'role': 'user', 'content': 'x' * 200000}])
    with pytest.raises(ValueError, match='positive'):
        Muse(PROVIDERS['meta'], spend_limit=0)


def test_auth_errors_do_not_retry(monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY', 'test-key')
    monkeypatch.setattr('p95bench.providers.time.sleep', lambda _: pytest.fail('must not retry'))

    def request(req, timeout):
        raise HTTPError(req.full_url, 401, 'Unauthorized', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(HTTPError):
        get_provider('meta').send('muse-wonder-1.0', [])
