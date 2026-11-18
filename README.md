# P95 Bench

An open-source benchmark for AI performance test scripting and engineering.
General purpose and independent of any AI assistant product. Compare model
quality, cost and latency before selecting a scripting assistant, detect model
regressions, or evaluate workload reasoning and dynamic-value correlation.

Python 3.11+ is required. The seed pack includes 15 tasks: four workload
calculations, three concepts questions, two CSV analysis questions, three
executable JMeter authoring/correlation tasks and three mirrored k6 tasks.
Gatling and Locust execution graders are future work. No OpenRouter: calls go
directly to providers.

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
Install k6 v1.0.0 as documented below, or use `--tool jmeter --tool none`
to run the original 12 tasks without k6.
Run from this checkout so the default task path resolves. All unit tests use a
fake provider or mocked transport, with no external API calls:

```sh
ruff check .
pytest
```

**Execution safety:** generated JMX and k6 scripts are executable code, not safe documents.
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
`PROVIDERS`. Anthropic uses a native Messages API adapter; Google is future work.
The runner supports dependency injection for testing and custom adapters.

For Anthropic, set `ANTHROPIC_API_KEY` and use `--provider anthropic` with
`--model claude-sonnet-5-5` or `--model claude-opus-5-5`. The adapter sends the
fixed prompt through the top-level `system` field with the same 8192-token output
cap. Temperature is omitted for Sonnet 5.5 and Opus 5.5 because their API rejects
the parameter as deprecated; published run metadata records this exception.
It uses `x-api-key` authentication and API version
`2023-06-01`, without an SDK. Discover IDs in Python with
`get_provider('anthropic').models()` from `p95bench.providers` (there is no
discovery CLI). Verified list input/output prices per million tokens are
$2/$10 for Sonnet and $4/$20 for Opus, from <https://claude.com/pricing>.
Prices live in `PROVIDERS['anthropic'].prices` in `src/p95bench/providers.py`,
using the same `Price(input, cached, output)` entries as DeepSeek. Prompt caching
is not enabled, so the cached rate is set to the ordinary input rate.

Thinking Machines (Tinker) uses the existing OpenAI-compatible adapter. Set
`TINKER_API_KEY` and use `--provider thinkingmachines`. The beta endpoint is
`https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1`, with bearer
authentication and `/chat/completions`. Temperature is 0 and the output cap is
8192 tokens, including reasoning. If reasoning exhausts that cap, null answer
content is graded as empty text; reasoning is not substituted for the answer.
No explicit reasoning effort is set. Any future `reasoning_effort` override must
be a top-level request field, not nested under `extra_body`, whose fields the
server silently ignores. See the [API overview](https://tinker-docs.thinkingmachines.ai/inference/openai/overview/).

Verified [Tinker prices](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/)
are USD per million tokens: Prefill is input and Sample is output.

| Model ID | Input | Cached input | Output | Published slug |
| --- | ---: | ---: | ---: | --- |
| `thinkingmachines/Inkling` | 1.87 | 0.374 | 4.68 | `inkling` |
| `thinkingmachines/Inkling-Small` | 0.58 | 0.116 | 1.44 | `inkling-small` |
| `thinkingmachines/Inkling-2-Small` | 0.30 | 0.03 | 1.00 | `inkling-2-small` |

Keep exact model IDs in API calls and saved attempts. Run directories use
timestamp IDs, independent of model names; published copies use the flat slugs
above under `results/published/`. Site artifact links use those same slugs.

OpenAI also uses the OpenAI-compatible adapter at `https://api.openai.com/v1`.
Set `OPENAI_API_KEY` and select `--provider openai` with `gpt-6.2-sol` or
`gpt-6.2-astra`. Verified Standard input/output list prices per million tokens
are $2/$10 for Sol and $8/$40 for Astra, from <https://openai.com/api/pricing/>.
Cached input is conservatively priced at the ordinary input rate pending a
verified cache discount. Both models reject `max_tokens` and temperature 0:
requests use `max_completion_tokens: 8192` and omit temperature, leaving the
API default. Published metadata records these exceptions. Reasoning tokens
count toward the completion cap and reported output usage.

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
temperature 0 where supported, a fixed system prompt and identical task/fixture messages across
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

`k6_exec` extracts a `js` or `javascript` code block and runs pinned k6 v1.0.0
against the same demo shop. It requires exit 0, nonempty `http_reqs` points,
successful HTTP statuses and `expected_response` tags, and exact verified
completions. `min_checkout_vus` requires distinct checkout VUs; k6 JSON output
stores VU/iteration system tags in point metadata. Isolation checks use actual
request paths and VUs from `--http-debug --log-format=json`, since custom
request names can replace the metric URL tag; URL tags are a fallback.
The k6 refresh seed sets `expected_checkout_vus: 1` and
`min_checkout_iterations: 2`, requiring checkout requests from exactly one VU
in at least two distinct iterations. Missing worker/iteration metadata fails
this check. The existing JMeter refresh grader verifies successful samples and
two session completions but does not separately verify thread/iteration shape;
its published grading is unchanged.
Timeout handling kills the
whole process group, as for JMeter. Set `K6_PATH` or put `k6` on PATH.

Install the official Linux amd64 release:

```sh
curl -fL https://github.com/grafana/k6/releases/download/v1.0.0/k6-v1.0.0-linux-amd64.tar.gz -o /home/ubuntu/Downloads/k6-v1.0.0-linux-amd64.tar.gz
tar -xzf /home/ubuntu/Downloads/k6-v1.0.0-linux-amd64.tar.gz -C /home/ubuntu/Downloads
install -m 0755 /home/ubuntu/Downloads/k6-v1.0.0-linux-amd64/k6 /home/ubuntu/.local/bin/k6
k6 version
```

`p95bench execute --provider deepseek --model deepseek-flash --tool k6`
runs only the three k6 tasks. `execute` aliases `run`; repeat `--tool` to select
multiple engines. Without a filter, the seed pack has 15 tasks. k6 prompts
require built-in modules and a JavaScript code block; the shared system prompt
is unchanged. Execution artifacts are trusted local code, not sandboxed code.
Run generated scripts only in an isolated environment without credentials or
access to sensitive services. The execution environment strips secret-named
variables, but this is not a filesystem or network sandbox.

`min_checkout_threads` optionally requires successful checkout samples from that
many distinct JMeter threads, in addition to the app's verified session completions.
The isolation seed requires two checkout threads; the refresh seed permits one
thread to complete two iterations with fresh sessions and tokens.

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
Execution subdirectories retain JMX, JTL, JMeter log and stdout, or k6
`script.js`, `points.json` and `stdout.log`, for inspection.
`score` recomputes aggregates from saved grades without calling APIs or rerunning
execution engines. It can summarize partial runs; check attempt counts before comparing.
Results are ignored by Git except for the explicitly published runs under
`results/published/`. No credentials should be included in task fixtures.

## Site

Live at https://p95.qainsights.com.
Deploys on push to main.

The static Astro showcase lives in `site/` and requires Node 22.23.4 or newer.
Fonts are self-hosted. To regenerate the leaderboard and build:

```sh
cd site
npm ci
npm run data -- ../results/published/*/
npm test
npm run build
npm run preview
```

The data command also accepts original run directories containing `summary.json`
and `attempts.jsonl`. It reads task metadata and category weights from the harness
and emits only public scoring fields, never prompts or raw responses, into
`site/src/data/leaderboard.json`. Optional `metadata.json` records the run date
and harness commit when known. Published metadata sidecars document the
execution timestamps, harness commits and any temperature exception. The published JSONL
files are full reproduction artifacts and were checked for credentials before
publication. Do not add secrets to prompts or fixtures.

Published k6 tasks were executed separately from the original 12-task runs,
then appended and rescored together using the same category weights. Metadata
records the separate k6 run date, version, harness commit and execution note.
Tool cards report each engine's own executable task pass rate.

Tinker's client timeout is 600 seconds rather than the default 180 seconds.
Earlier k6 requests timed out without responses; this gives those models more
time without changing their prompts, temperature or 8192-token output cap.

Model families and release order live in `site/src/data/model-registry.json`.
The site defaults to the newest benchmarked release per family; enable
"Show earlier versions" (`?versions=all`) to include every published result at
its full rank. Add increasing `release_order` values within a `family` and set
`discontinued: true` only when the provider no longer offers a model. Unknown
model IDs get their own latest family automatically.

## License

MIT. Copyright 2026 NaveenKumar Namachivayam.
