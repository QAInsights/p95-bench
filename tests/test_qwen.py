import io
import json
from dataclasses import replace
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from p95bench.providers import (
    PROVIDERS,
    OpenAICompatible,
    ProviderUnavailable,
    Response,
    get_provider,
)
from p95bench.runner import run
from p95bench.tasks import Task


@pytest.mark.parametrize('model,rates', [
    ('Qwen-Ambassador/Qwen3.8-Max', (2, .2, 6)),
    ('Qwen-Ambassador/Qwen3.8-Flash-Next', (.15, .015, .47)),
    ('Qwen-Ambassador/Qwen3.7-Max', (2.5, .25, 7.5)),
])
@pytest.mark.parametrize('content', ['10', '', None])
def test_transport_and_estimated_prices(monkeypatch, model, rates, content):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    monkeypatch.delenv('QWEN_BASE_URL', raising=False)
    provider = get_provider('qwen')
    assert type(provider) is OpenAICompatible

    def request(req, timeout):
        assert req.full_url == 'https://api-inference.modelscope.ai/v1/chat/completions'
        assert req.get_header('Authorization') == 'Bearer test-key'
        assert timeout == 180
        assert json.loads(req.data) == {'model': model, 'messages': [],
                                        'enable_thinking': False, 'max_tokens': 16384,
                                        'temperature': 0}
        return io.BytesIO(json.dumps({
            'choices': [{'message': {'content': content, 'reasoning_content': 'not the answer'}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                      'prompt_tokens_details': {'cached_tokens': 40}},
        }).encode())

    monkeypatch.setattr('urllib.request.urlopen', request)
    response = provider.send(model, [])
    assert response.text == (content or '')
    price = PROVIDERS['qwen'].prices[model]
    assert (price.input, price.cached, price.output) == rates
    assert price.cost(response) == pytest.approx((60 * rates[0] + 40 * rates[1]
                                                + 20 * rates[2]) / 1_000_000)


def test_key_and_base_url_override(monkeypatch):
    monkeypatch.delenv('MODELSCOPE_API_KEY', raising=False)
    monkeypatch.setenv('QWEN_BASE_URL', 'https://example.test/v1')
    provider = get_provider('qwen')
    assert provider.config.base_url == 'https://example.test/v1'
    with pytest.raises(ValueError, match='Set MODELSCOPE_API_KEY'):
        provider.send('Qwen-Ambassador/Qwen3.8-Max', [])
    assert PROVIDERS['qwen'].base_url == 'https://api-inference.modelscope.ai/v1'


@pytest.mark.parametrize('retry_after,expected', [(None, 1), ('3', 3), ('bad', 1), ('120', 60)])
def test_server_error_backoff_then_success(monkeypatch, retry_after, expected):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    sleeps, calls = [], []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)

    def request(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            raise HTTPError(req.full_url, 503, 'server unavailable',
                            {'Retry-After': retry_after} if retry_after else {}, None)
        return io.BytesIO(b'{"data":[{"id":"Qwen-Ambassador/Qwen3.8-Max"}]}')

    monkeypatch.setattr('urllib.request.urlopen', request)
    assert get_provider('qwen').models() == ['Qwen-Ambassador/Qwen3.8-Max']
    assert sleeps == [expected]
    assert len(calls) == 2


@pytest.mark.parametrize('error', [503, 'network', 'timeout',
                                  RemoteDisconnected('closed'), IncompleteRead(b'partial'),
                                  ConnectionResetError('reset')])
def test_exhausted_transport_is_not_a_model_failure(monkeypatch, error):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    sleeps = []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)

    def request(req, timeout):
        if isinstance(error, Exception):
            raise error
        if error == 'network':
            raise URLError('unavailable')
        if error == 'timeout':
            raise TimeoutError('unavailable')
        raise HTTPError(req.full_url, error, 'unavailable', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', request)
    attempts = 6 if error == 503 else 3
    expected_sleeps = [1, 2, 4, 8, 16] if error == 503 else [1, 2]
    with pytest.raises(ProviderUnavailable, match=f'No response after {attempts} requests'):
        get_provider('qwen').send('Qwen-Ambassador/Qwen3.8-Max', [])
    assert sleeps == expected_sleeps


@pytest.mark.parametrize('error', [RemoteDisconnected('closed'), IncompleteRead(b'partial'),
                                  ConnectionResetError('reset')])
@pytest.mark.parametrize('recover', [True, False])
def test_response_read_disconnects_retry(monkeypatch, error, recover):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    sleeps, calls = [], []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)

    class InterruptedBody(io.BytesIO):
        def read(self, *args):
            raise error

    def request(req, timeout):
        calls.append(req)
        if not recover or len(calls) == 1:
            return InterruptedBody()
        return io.BytesIO(b'{"data":[{"id":"model"}]}')

    monkeypatch.setattr('urllib.request.urlopen', request)
    if recover:
        assert get_provider('qwen').models() == ['model']
        assert len(calls) == 2
        assert sleeps == [1]
    else:
        with pytest.raises(ProviderUnavailable, match='No response after 3 requests'):
            get_provider('qwen').models()
        assert len(calls) == 3
        assert sleeps == [1, 2]


def test_http_429_is_not_retried(monkeypatch):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    calls, sleeps = [], []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)

    def request(req, timeout):
        calls.append(req)
        raise HTTPError(req.full_url, 429, 'rate limited', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(HTTPError, match='429'):
        get_provider('qwen').models()
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize('error', [IncompleteRead(b'partial'),
                                  ConnectionResetError('reset'), TimeoutError('timed out')])
def test_deepseek_transport_failures_retry_three_attempts(monkeypatch, error):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    calls, sleeps = [], []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)
    provider = get_provider('deepseek')
    provider.measure_transport = True

    class InterruptedBody(io.BytesIO):
        def read(self, *args):
            raise error

    def request(req, timeout):
        calls.append(req)
        if len(calls) == 1:
            if isinstance(error, IncompleteRead):
                return InterruptedBody()
            raise error
        return io.BytesIO(b'{"data":[{"id":"model"}]}')

    monkeypatch.setattr('urllib.request.urlopen', request)
    assert provider.models() == ['model']
    assert len(calls) == 2
    assert sleeps == [1]
    assert provider.transport_attempts == 2


def test_deepseek_transport_retry_limit(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    calls, sleeps = [], []
    monkeypatch.setattr('p95bench.providers.time.sleep', sleeps.append)

    def request(req, timeout):
        calls.append(req)
        raise TimeoutError('timed out')

    monkeypatch.setattr('urllib.request.urlopen', request)
    provider = get_provider('deepseek')
    provider.measure_transport = True
    with pytest.raises(ProviderUnavailable, match='No response after 3 requests'):
        provider.models()
    assert len(calls) == 3
    assert sleeps == [1, 2]
    assert provider.transport_attempts == 3


def test_transport_retries_reuse_one_budget_reservation(monkeypatch, tmp_path):
    from p95bench.analysis_v2 import analysis_reference
    from p95bench.packs import pack_config
    from p95bench.runner import run
    from p95bench.tasks import load_tasks

    task = next(task for task in load_tasks(
        Path('packs/p95-v2.0.0/tasks'), pack_config('p95-v2.0.0')['weights'])
        if task.grader == 'v2_analysis')
    answer = json.dumps(analysis_reference(task.params, task.fixtures))
    config = replace(PROVIDERS['deepseek'], extra={'max_tokens': 64})

    class Ledger:
        limit = 60_000_000

        def __init__(self):
            self.reservations = []
            self.settlements = []

        def reserve(self, maximum):
            self.reservations.append(maximum)
            return 'one-reservation'

        def settle(self, key, amount):
            self.settlements.append((key, amount))

    class StubProvider:
        def __init__(self):
            self.config = config

        def send(self, model, messages):
            self.transport_attempts = 3
            self.response_latency_seconds = .1
            self.backoff_seconds = 3
            return Response(answer, 10, 10)

    ledger = Ledger()
    monkeypatch.setattr('p95bench.runner.BudgetLedger', lambda: ledger)
    monkeypatch.setattr('p95bench.runner.prepare_workloads', lambda *_: None)
    budgets = {task.id: {'tokens': 1000, 'latency_seconds': 90}}
    run(StubProvider(), 'deepseek', 'deepseek-flash', PROVIDERS['deepseek'].prices['deepseek-flash'],
        [task], tmp_path, pack_id='p95-v2.0.0', efficiency_budgets=budgets)

    assert len(ledger.reservations) == 1
    assert len(ledger.settlements) == 1
    assert ledger.settlements[0][0] == 'one-reservation'
    assert ledger.settlements[0][1] <= ledger.reservations[0]


def test_auth_error_does_not_retry(monkeypatch):
    monkeypatch.setenv('MODELSCOPE_API_KEY', 'test-key')
    monkeypatch.setattr('p95bench.providers.time.sleep', lambda _: pytest.fail('must not retry'))

    def request(req, timeout):
        raise HTTPError(req.full_url, 401, 'Unauthorized', {}, None)

    monkeypatch.setattr('urllib.request.urlopen', request)
    with pytest.raises(HTTPError, match='401'):
        get_provider('qwen').models()


def test_run_excludes_no_response_but_grades_empty_responses(tmp_path):
    model = 'Qwen-Ambassador/Qwen3.8-Max'

    class FakeProvider:
        config = PROVIDERS['qwen']

        def send(self, requested_model, messages):
            assert requested_model == model
            if messages[-1]['content'] == 'unavailable':
                raise ProviderUnavailable('No response after 6 requests (HTTP 429)')
            return Response('10' if messages[-1]['content'] == 'correct' else '', 10, 2)

    tasks = [Task(text, 'workload', 'none', text, 'numeric', {'answer': 10}, {})
             for text in ['unavailable', 'empty', 'correct']]
    directory, summary = run(FakeProvider(), 'qwen', model,
                             PROVIDERS['qwen'].prices[model], tasks, tmp_path)
    assert summary['attempts'] == 2
    assert summary['passed'] == 1
    assert summary['overall_score'] == 50
    assert json.loads((directory / 'not-run.jsonl').read_text())['status'] == 'not_run'
    assert json.loads((directory / 'metadata.json').read_text())['cost_note'] == (
        'List-price estimate, run via ModelScope free tier.')
    assert 'unavailable' not in [json.loads(line)['task_id']
                                 for line in (directory / 'attempts.jsonl').read_text().splitlines()]


def test_all_unavailable_has_no_scored_attempts(tmp_path):
    class FakeProvider:
        config = replace(PROVIDERS['qwen'])

        def send(self, model, messages):
            raise ProviderUnavailable('unavailable')

    task = Task('test', 'workload', 'none', 'test', 'numeric', {'answer': 10}, {})
    directory, summary = run(FakeProvider(), 'qwen', 'model',
                             PROVIDERS['qwen'].prices['Qwen-Ambassador/Qwen3.8-Max'],
                             [task], tmp_path)
    assert summary['attempts'] == 0
    assert summary['category_scores'] == {}
    assert (directory / 'attempts.jsonl').read_text() == ''
