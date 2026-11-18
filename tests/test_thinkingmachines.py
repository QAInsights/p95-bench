import io
import json
from urllib.error import HTTPError

import pytest

from p95bench.cli import main
from p95bench.providers import PROVIDERS, OpenAICompatible, Response, get_provider
from p95bench.runner import SYSTEM_PROMPT, run
from p95bench.tasks import Task


@pytest.mark.parametrize('model,rates', [
    ('thinkingmachines/Inkling', (1.87, .374, 4.68)),
    ('thinkingmachines/Inkling-Small', (.58, .116, 1.44)),
    ('thinkingmachines/Inkling-2-Small', (.30, .03, 1.00)),
])
@pytest.mark.parametrize('content', ['10', '', None])
def test_transport(monkeypatch, model, rates, content):
    monkeypatch.setenv('TINKER_API_KEY', 'test-key')
    provider = get_provider('thinkingmachines')
    assert type(provider) is OpenAICompatible
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': 'Return only 10'}]

    def request(req, timeout):
        assert req.full_url == (
            'https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1/chat/completions')
        assert req.get_method() == 'POST'
        assert req.get_header('Authorization') == 'Bearer test-key'
        assert req.get_header('User-agent') == 'p95bench/0.1.0'
        assert timeout == 600
        assert json.loads(req.data) == {
            'model': model, 'messages': messages, 'temperature': 0, 'max_tokens': 8192,
        }
        return io.BytesIO(json.dumps({
            'choices': [{'message': {'content': content, 'reasoning_content': 'Private reasoning'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                      'prompt_tokens_details': {'cached_tokens': 40}},
        }).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    clock = iter([10., 10.25])
    monkeypatch.setattr('p95bench.providers.time.perf_counter', lambda: next(clock))
    response = provider.send(model, messages)
    assert response == Response(content or '', 100, 20, 40, .25)
    price = PROVIDERS['thinkingmachines'].prices[model]
    assert (price.input, price.cached, price.output) == rates
    assert price.cost(response) == pytest.approx((60 * rates[0] + 40 * rates[1]
                                                + 20 * rates[2]) / 1_000_000)


def test_missing_key_and_http_error(monkeypatch):
    provider = get_provider('thinkingmachines')
    monkeypatch.delenv('TINKER_API_KEY', raising=False)
    with pytest.raises(ValueError, match='Set TINKER_API_KEY'):
        provider.send('thinkingmachines/Inkling', [])
    monkeypatch.setenv('TINKER_API_KEY', 'test-key')

    def request(req, timeout):
        raise HTTPError(req.full_url, 401, 'Unauthorized', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(HTTPError, match='401'):
        provider.send('thinkingmachines/Inkling', [])


def test_cli_rejects_unpriced_model(monkeypatch, tmp_path, capsys):
    def request(*args, **kwargs):
        pytest.fail('Unknown price must fail before network access')

    monkeypatch.setattr('urllib.request.urlopen', request)
    assert main(['run', '--provider', 'thinkingmachines', '--model', 'unknown',
                 '--out', str(tmp_path / 'results')]) == 1
    assert 'Unknown model price' in capsys.readouterr().err
    assert not (tmp_path / 'results').exists()


def test_namespaced_model_run_directory_and_cli(monkeypatch, tmp_path):
    model = 'thinkingmachines/Inkling-2-Small'

    class FakeProvider:
        def send(self, requested_model, messages):
            assert requested_model == model
            return Response('10', 10, 1)

    task = Task('test', 'workload', 'none', 'Return 10', 'numeric', {'answer': 10}, {})
    directory, summary = run(FakeProvider(), 'thinkingmachines', model,
                             PROVIDERS['thinkingmachines'].prices[model], [task], tmp_path)
    assert directory.parent == tmp_path
    assert summary['passed'] == 1
    attempt = json.loads((directory / 'attempts.jsonl').read_text())
    assert attempt['model'] == model

    def fake_run(provider, provider_name, requested_model, price, tasks, out, repeats):
        assert type(provider) is OpenAICompatible
        assert provider.config is PROVIDERS['thinkingmachines']
        assert (provider_name, requested_model) == ('thinkingmachines', model)
        return directory, summary

    monkeypatch.setattr('p95bench.cli.run', fake_run)
    assert main(['run', '--provider', 'thinkingmachines', '--model', model]) == 0
