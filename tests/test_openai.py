import io
import json

import pytest

from p95bench.cli import main
from p95bench.providers import (
    PROVIDERS,
    DeepSeekResponse,
    OpenAICompatible,
    Price,
    ProviderUnavailable,
    Response,
    get_provider,
)
from p95bench.runner import SYSTEM_PROMPT


@pytest.mark.parametrize('cached,billed', [(-20, 0), (0, 0), (40, 40), (100, 100), (150, 100)])
def test_cached_token_billing_bounds(cached, billed):
    response = Response('answer', 100, 20, cached)
    assert Price(2, .5, 10).cost(response) == pytest.approx(
        ((100 - billed) * 2 + billed * .5 + 20 * 10) / 1_000_000)
    assert response.cached_tokens == cached


def test_cached_tokens_without_input():
    assert Price(2, .5, 10).cost(Response('', 0, 0, 50)) == 0


@pytest.mark.parametrize('write,expected_write', [(-5, 0), (0, 0), (10, 10), (200, 60)])
def test_cache_write_billing_bounds(write, expected_write):
    price = PROVIDERS['openai'].prices['gpt-6.1-terra']
    response = Response('', 100, 20, 40, cache_write_tokens=write)
    expected = ((100 - 40) * 1 + 40 * .05 + expected_write * (1.25 - 1) + 20 * 5) / 1_000_000
    assert price.cost(response) == pytest.approx(expected)


@pytest.mark.parametrize('details,expected', [({}, 0), ({'prompt_tokens_details': None}, 0),
    ({'prompt_tokens_details': {}}, 0),
    ({'prompt_tokens_details': None, 'prompt_cache_hit_tokens': 40}, 40),
    ({'prompt_tokens_details': {'cached_tokens': 90}, 'prompt_cache_hit_tokens': 10}, 10)])
def test_optional_prompt_token_details(monkeypatch, details, expected):
    provider = get_provider('openai')
    monkeypatch.setattr(provider, '_request', lambda *args: {
        'choices': [{'message': {'content': 'answer'}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 20, **details}})
    assert provider.send('gpt-6.2-sol', []).cached_tokens == expected


def test_deepseek_thinking_payload_and_reasoning_tokens(monkeypatch):
    provider = get_provider('deepseek')
    assert provider.config.extra == {
        'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high', 'max_tokens': 64000}
    assert provider.config.reasoning_mode == 'thinking-high'
    assert provider.config.temperature is None
    assert PROVIDERS['qwen'].reasoning_mode == 'thinking-high'
    assert PROVIDERS['openai'].reasoning_mode == 'thinking-high'
    assert all(config.reasoning_mode == 'provider-default'
               for name, config in PROVIDERS.items()
               if name not in {'deepseek', 'qwen', 'anthropic', 'openai'})
    captured = {}

    def request(path, body):
        captured.update(body)
        return {
            'choices': [{'message': {'content': 'answer only',
                                     'reasoning_content': 'private reasoning'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 30, 'completion_tokens': 50,
                      'completion_tokens_details': {'reasoning_tokens': 18}},
        }

    monkeypatch.setattr(provider, '_request', request)
    response = provider.send('deepseek-flash', [{'role': 'user', 'content': 'Solve'}])

    assert captured['thinking'] == {'type': 'enabled'}
    assert captured['reasoning_effort'] == 'high'
    assert captured['max_tokens'] == 64000
    assert 'temperature' not in captured
    assert isinstance(response, DeepSeekResponse)
    assert response.text == 'answer only'
    assert response.output_tokens == 50
    assert response.reasoning_tokens == 18
    assert not hasattr(response, 'reasoning_content')
    assert not response.thinking_exhausted
    assert DeepSeekResponse('', 30, 50, reasoning_tokens=18,
                            finish_reason='length').thinking_exhausted


def sse_response(*chunks):
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    lines.append("data: [DONE]\n\n")
    return io.BytesIO("".join(lines).encode())


def test_qwen_stream_concatenates_content_reasoning_and_usage(monkeypatch):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    chunks = [
        {'choices': [{'delta': {'content': 'The '}}]},
        {'choices': [{'delta': {'reasoning_content': 'first '}}]},
        {'choices': [{'delta': {'content': 'answer', 'reasoning_content': 'second'},
                      'finish_reason': 'stop'}]},
        {'choices': [], 'usage': {
            'prompt_tokens': 100, 'completion_tokens': 25,
            'prompt_tokens_details': {'cached_tokens': 40},
            'completion_tokens_details': {'reasoning_tokens': 15},
        }},
    ]
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: sse_response(*chunks))

    response = get_provider('qwen').send('Qwen-Ambassador/Qwen3.8-Max', [])

    assert response.text == 'The answer'
    assert response.reasoning_tokens == 15
    assert response.input_tokens == 100
    assert response.output_tokens == 25
    assert response.cached_tokens == 40
    assert response.finish_reason == 'stop'


def test_qwen_stream_length_with_reasoning_only_is_thinking_exhausted(monkeypatch):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    chunks = [
        {'choices': [{'delta': {'reasoning_content': 'thinking'}, 'finish_reason': 'length'}]},
        {'choices': [], 'usage': {
            'prompt_tokens': 100, 'completion_tokens': 64000,
            'completion_tokens_details': {'reasoning_tokens': 64000},
        }},
    ]
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: sse_response(*chunks))

    response = get_provider('qwen').send('Qwen-Ambassador/Qwen3.8-Max', [])

    assert response.text == ''
    assert response.reasoning_tokens == 64000
    assert response.thinking_exhausted


def test_qwen_stream_without_usage_retries_as_transport_error(monkeypatch):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    provider = get_provider('qwen')
    calls = []

    def request(*args, **kwargs):
        calls.append(None)
        return sse_response({'choices': [{'delta': {'content': 'answer'}}]})

    monkeypatch.setattr('urllib.request.urlopen', request)
    monkeypatch.setattr('p95bench.providers.time.sleep', lambda _: None)
    expected_calls = 1 + provider.config.transport_retries

    with pytest.raises(ProviderUnavailable,
                       match=rf'No response after {expected_calls} requests \(StreamInterrupted\)'):
        provider.send('Qwen-Ambassador/Qwen3.8-Max', [])

    assert len(calls) == expected_calls


def test_qwen_malformed_stream_event_retries_as_transport_error(monkeypatch):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    provider = get_provider('qwen')
    calls = []
    truncated_stream = (
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        b'data: {"usage": {"prompt_tok')

    def request(*args, **kwargs):
        calls.append(None)
        return io.BytesIO(truncated_stream)

    monkeypatch.setattr('urllib.request.urlopen', request)
    monkeypatch.setattr('p95bench.providers.time.sleep', lambda _: None)
    expected_calls = 1 + provider.config.transport_retries

    with pytest.raises(ProviderUnavailable,
                       match=rf'No response after {expected_calls} requests \(StreamInterrupted\)'):
        provider.send('Qwen-Ambassador/Qwen3.8-Max', [])

    assert len(calls) == expected_calls


def test_qwen_interrupted_stream_recovers_on_retry(monkeypatch):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    calls = []
    truncated_stream = (
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        b'data: {"usage": {"prompt_tok')

    def request(*args, **kwargs):
        calls.append(None)
        if len(calls) == 1:
            return io.BytesIO(truncated_stream)
        return sse_response(
            {'choices': [{'delta': {'content': 'recovered'}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'prompt_tokens': 4, 'completion_tokens': 3}},
        )

    monkeypatch.setattr('urllib.request.urlopen', request)
    monkeypatch.setattr('p95bench.providers.time.sleep', lambda _: None)

    response = get_provider('qwen').send('Qwen-Ambassador/Qwen3.8-Max', [])

    assert response.text == 'recovered'
    assert response.input_tokens == 4
    assert response.output_tokens == 3
    assert response.finish_reason == 'stop'
    assert len(calls) == 2


def test_qwen_stream_error_is_unavailable(monkeypatch):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    calls = []

    def request(*args, **kwargs):
        calls.append(None)
        return sse_response({'error': {'message': 'stream rejected'}})

    monkeypatch.setattr('urllib.request.urlopen', request)

    with pytest.raises(ProviderUnavailable, match='Stream error: stream rejected'):
        get_provider('qwen').send('Qwen-Ambassador/Qwen3.8-Max', [])

    assert len(calls) == 1


def test_deepseek_non_stream_response_remains_plain_json(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    payload = {
        'choices': [{'message': {'content': 'answer'}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 30, 'completion_tokens': 50,
                  'completion_tokens_details': {'reasoning_tokens': 18}},
    }

    def request(req, timeout):
        assert 'stream' not in json.loads(req.data)
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)

    response = get_provider('deepseek').send('deepseek-flash', [])

    assert response.text == 'answer'
    assert response.reasoning_tokens == 18
    assert response.finish_reason == 'stop'


@pytest.mark.parametrize('model,rates', [
    ('gpt-6.2-sol', (2, .1, 2.5, 10)), ('gpt-6.2-astra', (8, .4, 10, 40)),
    ('gpt-6.1-terra', (1, .05, 1.25, 5)), ('gpt-6-luna', (.1, .01, .125, .5)),
])
@pytest.mark.parametrize('content', ['42', None])
def test_transport(monkeypatch, model, rates, content):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    provider = get_provider('openai')
    assert type(provider) is OpenAICompatible
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': 'Return 42'}]

    def request(req, timeout):
        assert req.full_url == 'https://api.openai.com/v1/chat/completions'
        assert req.get_method() == 'POST'
        assert req.get_header('Authorization') == 'Bearer test-key'
        assert timeout == 600
        assert json.loads(req.data) == {
            'model': model, 'messages': messages, 'max_completion_tokens': 128000,
            'reasoning_effort': 'high',
        }
        return io.BytesIO(json.dumps({
            'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                      'completion_tokens_details': {'reasoning_tokens': 10},
                      'prompt_tokens_details': {'cached_tokens': 40, 'cache_write_tokens': 10}},
        }).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    clock = iter([1., 1.5])
    monkeypatch.setattr('p95bench.providers.time.perf_counter', lambda: next(clock))
    response = provider.send(model, messages)
    assert isinstance(response, DeepSeekResponse)
    assert response.text == (content or '')
    assert response.input_tokens == 100
    assert response.output_tokens == 20
    assert response.cached_tokens == 40
    assert response.cache_write_tokens == 10
    assert response.latency_seconds == .5
    assert response.reasoning_tokens == 10
    assert response.finish_reason == 'stop'
    price = PROVIDERS['openai'].prices[model]
    assert (price.input, price.cached, price.cache_write, price.output) == rates
    expected = (50 * rates[0] + 40 * rates[1] + 10 * rates[2] + 20 * rates[3]) / 1_000_000
    assert price.cost(response) == pytest.approx(expected)
    assert price.input_upper_bound == max(rates[0], rates[2])


def test_openai_length_finish_reason_is_preserved(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    payload = {
        'choices': [{'message': {'content': None}, 'finish_reason': 'length'}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 128000,
                  'completion_tokens_details': {'reasoning_tokens': 127000}},
    }
    monkeypatch.setattr('urllib.request.urlopen',
                        lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode()))

    response = get_provider('openai').send('gpt-6.2-sol', [])

    assert response.finish_reason == 'length'
    assert response.reasoning_tokens == 127000
    assert response.thinking_exhausted


def test_unknown_price_and_missing_key(monkeypatch, tmp_path, capsys):
    def request(*args, **kwargs):
        pytest.fail('Unknown price or missing key must fail before network access')

    monkeypatch.setattr('urllib.request.urlopen', request)
    assert main(['run', '--provider', 'openai', '--model', 'unknown',
                 '--out', str(tmp_path / 'results')]) == 1
    assert 'Unknown model price' in capsys.readouterr().err
    assert not (tmp_path / 'results').exists()
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(ValueError, match='Set OPENAI_API_KEY'):
        get_provider('openai').send('gpt-6.2-sol', [])


def test_cli_selection(monkeypatch, tmp_path):
    def fake_run(provider, provider_name, model, price, tasks, out, repeats):
        assert type(provider) is OpenAICompatible
        assert provider.config is PROVIDERS['openai']
        assert (provider_name, model) == ('openai', 'gpt-6.2-sol')
        return tmp_path, {'overall_score': 100}

    monkeypatch.setattr('p95bench.cli.run', fake_run)
    assert main(['run', '--provider', 'openai', '--model', 'gpt-6.2-sol']) == 0
