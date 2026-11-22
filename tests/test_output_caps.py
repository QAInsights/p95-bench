import io
import json
from dataclasses import replace

import pytest

from p95bench.providers import DEFAULT_OUTPUT_TOKENS, OUTPUT_LIMIT_FIELDS, PROVIDERS, get_provider
from p95bench.runner import _output_cap


@pytest.mark.parametrize('provider_name,model', [
    (name, model) for name, config in PROVIDERS.items() for model in config.prices
])
@pytest.mark.parametrize('model_limit', [None, 32000, 128000])
def test_request_and_reservation_use_same_cap(monkeypatch, provider_name, model, model_limit):
    provider = get_provider(provider_name)
    if model_limit is not None:
        provider.config = replace(provider.config, model_output_limits={model: model_limit})
    config = provider.config
    expected = min(DEFAULT_OUTPUT_TOKENS, model_limit or DEFAULT_OUTPUT_TOKENS)
    monkeypatch.setenv(config.key_env, 'test-key')

    def request(req, timeout):
        body = json.loads(req.data)
        options = body.get('generationConfig', body)
        field = next(key for key in OUTPUT_LIMIT_FIELDS if key in options)
        assert options[field] == expected == _output_cap(config, model)
        assert timeout == 600
        if provider_name == 'gemini':
            data = {'usageMetadata': {'promptTokenCount': 2, 'candidatesTokenCount': 1},
                    'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]}
        elif provider_name == 'anthropic':
            events = [
                {'type': 'message_start', 'message': {'usage': {'input_tokens': 2}}},
                {'type': 'content_block_start', 'index': 0,
                 'content_block': {'type': 'text', 'text': 'ok'}},
                {'type': 'message_delta', 'usage': {'output_tokens': 1},
                 'delta': {'stop_reason': 'end_turn'}},
                {'type': 'message_stop'},
            ]
            return io.BytesIO(''.join(f'data: {json.dumps(e)}\n\n' for e in events).encode())
        elif options.get('stream'):
            data = {'choices': [{'delta': {'content': 'ok'}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 2, 'completion_tokens': 1}}
            return io.BytesIO(f'data: {json.dumps(data)}\n\ndata: [DONE]\n\n'.encode())
        else:
            data = {'choices': [{'message': {'content': 'ok'}}],
                    'usage': {'prompt_tokens': 2, 'completion_tokens': 1}}
        return io.BytesIO(json.dumps(data).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    response = provider.send(model, [{'role': 'user', 'content': 'test'}])
    assert response.text == 'ok'
    assert response.output_tokens == 1
    assert config.extra == PROVIDERS[provider_name].extra


def test_shared_fallback_and_provider_defaults():
    assert DEFAULT_OUTPUT_TOKENS == 64000
    assert _output_cap(None) == 64000
    for config in PROVIDERS.values():
        assert config.output_cap() == 64000
        assert replace(config, extra={}).output_cap() == 64000
