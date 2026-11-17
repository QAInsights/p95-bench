import io
import json

import pytest

from p95bench.cli import main
from p95bench.providers import PROVIDERS, OpenAICompatible, Response, get_provider
from p95bench.runner import SYSTEM_PROMPT


@pytest.mark.parametrize('model,rates', [
    ('gpt-6.2-sol', (2, 10)), ('gpt-6.2-astra', (8, 40)),
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
        assert timeout == 180
        assert json.loads(req.data) == {
            'model': model, 'messages': messages, 'max_completion_tokens': 8192,
        }
        return io.BytesIO(json.dumps({
            'choices': [{'message': {'content': content}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                      'completion_tokens_details': {'reasoning_tokens': 10},
                      'prompt_tokens_details': {'cached_tokens': 40}},
        }).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    clock = iter([1., 1.5])
    monkeypatch.setattr('p95bench.providers.time.perf_counter', lambda: next(clock))
    response = provider.send(model, messages)
    assert response == Response(content or '', 100, 20, 40, .5)
    price = PROVIDERS['openai'].prices[model]
    assert (price.input, price.cached, price.output) == (rates[0], rates[0], rates[1])
    assert price.cost(response) == pytest.approx((100 * rates[0] + 20 * rates[1]) / 1_000_000)


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
