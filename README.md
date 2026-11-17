# P95 Bench

An open-source benchmark for AI performance test scripting and engineering.
General purpose and independent of any AI assistant product. Compare model
quality, cost and latency before selecting a scripting assistant, detect model
regressions, or evaluate workload reasoning and dynamic-value correlation.

Python 3.11+ is required. The first release includes 12 tasks: four workload
calculations, three concepts questions, two CSV analysis questions and three
executable JMeter authoring/correlation tasks. k6, Gatling and Locust execution
graders are future work. No OpenRouter: calls go directly to providers.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
export DEEPSEEK_API_KEY='your-key'
export JMETER_HOME="$HOME/apache-jmeter-5.6.3"
p95bench run --provider deepseek --model deepseek-flash --tasks tasks/ --out results/ --repeats 1
p95bench score results/<run>
```

Install Apache JMeter 5.6.3 and a compatible Java runtime separately. The default
JMeter location is `~/apache-jmeter-5.6.3`; override with `JMETER_HOME`.
Run from this checkout so the default task path resolves. All unit tests use a
fake provider or mocked transport, with no external API calls:

```sh
ruff check .
pytest
```

**Execution safety:** generated JMX is executable code, not a safe document.
Run execution benchmarks only in a disposable, isolated environment without
valuable files or credentials, and restrict outbound network access. This
release uses a subprocess timeout, not a security sandbox. JMeter plugins and
scripts can access the local machine. Do not run untrusted task packs on a
shared host. The demo app binds only to loopback and stops after each grade.
The provider key is used only for API requests, never included in prompts or
result files. Execution subprocesses do not need provider credentials.

## Providers and prices

`src/p95bench/providers.py` defines a minimal `Provider.send(model, messages)`
protocol returning text, prompt/completion/cache-hit tokens and elapsed seconds.
`OpenAICompatible` accepts a base URL, key environment-variable name, prices,
temperature (use `None` when unsupported), and optional request fields.
Add another OpenAI-compatible provider by adding a `ProviderConfig` entry to
`PROVIDERS`. Anthropic and Google need separate adapters in later releases.
The runner supports dependency injection for testing and custom adapters.

DeepSeek `/models` was queried during implementation and returned
`deepseek-flash` and `deepseek-v4-pro`. Flash is the cheapest listed model.
Use `OpenAICompatible(PROVIDERS['deepseek']).models()` in Python to refresh IDs.
Model IDs and prices can change: update the table deliberately before running
an unknown model. Unknown prices fail early rather than silently reporting $0.

Prices are USD per million tokens, taken from
<https://api-docs.deepseek.com/quick_start/pricing.md>. The table uses **peak**
rates: Flash input $0.30, cached input $0.006, output $1.20; Pro input $1.32,
cached input $0.044, output $3.96. DeepSeek advertises half-price off-peak rates;
reported costs are conservative estimates, not invoices. Cache-hit input tokens
are not charged twice. Each attempt records its price snapshot. Calls use
temperature 0, a fixed system prompt and identical task/fixture messages across
models. DeepSeek thinking is disabled for this initial chat benchmark, with an
8192-token output cap. Provider failures abort the run rather than count as
incorrect answers; already-written attempt lines remain available to score.

## Add a task

Place one YAML file in `tasks/<category>/`. Example:

```yaml
id: my-workload
category: workload
tool: none
prompt: "At 10 requests/s and 2 seconds mean latency, what is concurrency?"
grader: numeric
params:
  answer: 20
  tolerance: 0.001
```

Categories are `authoring`, `correlation`, `workload`, `analysis`, `repair`,
`migration`, `concepts`. Tools are `jmeter`, `k6`, `gatling`, `locust`, `none`.
Optional `fixtures: [samples.csv]` loads UTF-8 files relative to the YAML file
and appends their contents to the prompt. Fixture paths cannot escape that
directory. IDs must be unique. Answers/params are never sent to models.

`numeric` requires a bare finite number and checks absolute tolerance.
`choice` requires a bare choice letter (case insensitive).
`jmeter_exec` extracts a complete `jmeterTestPlan`, runs JMeter non-GUI with
`host` and `port` properties, requires nonempty successful samples, and checks
exact `params.completions` (default 1) via the demo app's protected internal
stats API. `params.timeout` defaults to 60 seconds. A zero process exit code
alone does not pass. Add future execution graders to `graders.GRADERS`; the
runner and task schema do not need tool-specific changes.

The shop contract is in each execution prompt: login returns a fresh CSRF token
and session cookie, products must be listed, cart requires the session token,
and checkout requires that same token and populated cart. A session can complete
only once, so static tokens or repeated checkout requests cannot fake success.
The verifier's random admin key is not given to the model or JMeter.

Seed numeric answers are computed in `test_seed_answers`: Little's Law
40 x 0.25 = 10; pacing 100 / 20 - 2 = 3 seconds; arrival rate
18000 / 300 = 60/s; concurrency 75 x 0.8 = 60; fixture mean
(100 + 200 + 300 + 400) / 4 = 250 ms; error percentage 1 / 4 x 100 = 25.

## Scoring and artifacts

Each attempt passes (1) or fails (0). Category score is its mean x 100, including
all repeats. Overall is a weighted mean: authoring 25, correlation 15, workload
15, analysis 15, repair 15, migration 10, concepts 5. Weights are renormalized
over categories actually present, not treated as failures for missing categories.
The seed pack's active weight sum is 75. These small public seeds are a baseline,
not a statistically comprehensive leaderboard or a contamination-resistant test.

Each unique run directory contains `attempts.jsonl` with raw model text, exact
messages, model/provider, grader parameters, token usage, latency, estimated
cost and grade. `summary.json` aggregates category/overall scores and totals.
Execution subdirectories retain JMX, JTL, JMeter log and stdout for inspection.
`score` recomputes aggregates from saved grades without calling APIs or rerunning
JMeter. It can summarize partial runs; check attempt counts before comparing.
Results are ignored by Git. No credentials should be included in task fixtures.

## License

MIT. Copyright 2026 NaveenKumar Namachivayam.
