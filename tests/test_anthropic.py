import io
import json
from urllib.error import HTTPError

import pytest

from p95bench.cli import main
from p95bench.providers import PROVIDERS, Anthropic, OpenAICompatible, get_provider
from p95bench.runner import SYSTEM_PROMPT


def test_provider_selection():
    assert isinstance(get_provider('anthropic'), Anthropic)
    assert type(get_provider('deepseek')) is OpenAICompatible


@pytest.mark.parametrize('content,expected', [
    ([{'type': 'text', 'text': 'hel'}, {'type': 'tool_use', 'name': 'ignored'},
      {'type': 'text', 'text': 'lo'}], 'hello'),
    ([], ''),
])
def test_messages_transport(monkeypatch, content, expected):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': 'Solve the task'}]

    def request(req, timeout):
        assert req.full_url == 'https://api.anthropic.com/v1/messages'
        assert req.get_method() == 'POST'
        headers = {k.lower(): v for k, v in req.header_items()}
        assert headers == {'x-api-key': 'test-key', 'anthropic-version': '2023-06-01',
                           'content-type': 'application/json'}
        assert timeout == 180
        assert json.loads(req.data) == {
            'model': 'claude-sonnet-5-5', 'system': SYSTEM_PROMPT,
            'messages': messages[1:], 'max_tokens': 8192, 'temperature': 0,
        }
        return io.BytesIO(json.dumps({'content': content, 'usage': {
            'input_tokens': 20, 'output_tokens': 3}}).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    clock = iter([10., 10.5])
    monkeypatch.setattr('p95bench.providers.time.perf_counter', lambda: next(clock))
    response = get_provider('anthropic').send('claude-sonnet-5-5', messages)
    assert response.text == expected
    assert (response.input_tokens, response.output_tokens, response.cached_tokens) == (20, 3, 0)
    assert response.latency_seconds == .5
    assert messages[0]['role'] == 'system'
    assert PROVIDERS['anthropic'].prices['claude-sonnet-5-5'].cost(response) == .00007


def test_discovery_pagination(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    urls = []

    def request(req, timeout):
        urls.append(req.full_url)
        assert req.get_method() == 'GET'
        assert req.get_header('X-api-key') == 'test-key'
        assert req.get_header('Anthropic-version') == '2023-06-01'
        data = ({'data': [{'id': 'first'}], 'has_more': True, 'last_id': 'first'}
                if len(urls) == 1 else {'data': [{'id': 'second'}], 'has_more': False})
        return io.BytesIO(json.dumps(data).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    assert get_provider('anthropic').models() == ['first', 'second']
    assert urls == ['https://api.anthropic.com/v1/models',
                    'https://api.anthropic.com/v1/models?after_id=first']


def test_missing_key_and_http_error(monkeypatch):
    provider = get_provider('anthropic')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    with pytest.raises(ValueError, match='Set ANTHROPIC_API_KEY'):
        provider.models()
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')

    def request(req, timeout):
        raise HTTPError(req.full_url, 401, 'Unauthorized', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(HTTPError, match='401'):
        provider.send('claude-sonnet-5-5', [{'role': 'user', 'content': 'hello'}])


def test_cli_rejects_unpriced_model_before_network(monkeypatch, tmp_path, capsys):
    def unexpected_request(*args, **kwargs):
        pytest.fail('Unknown price must fail before network access')

    monkeypatch.setattr('urllib.request.urlopen', unexpected_request)
    assert main(['run', '--provider', 'anthropic', '--model', 'unknown',
                 '--out', str(tmp_path / 'results')]) == 1
    assert 'Unknown model price' in capsys.readouterr().err
    assert not (tmp_path / 'results').exists()


def test_cli_uses_anthropic(monkeypatch, tmp_path):
    def fake_run(provider, provider_name, model, price, tasks, out, repeats):
        assert isinstance(provider, Anthropic)
        assert provider_name == 'anthropic'
        assert model == 'claude-opus-5-5'
        assert (price.input, price.output) == (4, 20)
        return tmp_path, {'overall_score': 100}

    monkeypatch.setattr('p95bench.cli.run', fake_run)
    assert main(['run', '--provider', 'anthropic', '--model', 'claude-opus-5-5']) == 0
