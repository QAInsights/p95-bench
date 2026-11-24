# P95 Bench

## V2 public pack

The legacy pack is `p95-v1`; its boolean scores and published artifacts are unchanged.
`p95-v2.0.0` introduces validated `CheckpointGrade` results: unique checkpoints total
100 possible points, awarded points determine quality, and full credit alone sets
`passed`. The 40 public tasks under `packs/p95-v2.0.0/tasks` use registered native
`v2_exec` and structured `v2_analysis` graders: 11 repair, 6 migration, 10 correlation,
8 workload and 5 analysis. Selecting v2 with a legacy grader fails before requests.
See [the pack contract](packs/p95-v2.0.0/README.md) for isolation, capacity qualification,
checkpoint gating and offline golden/mutation verification. The earlier `p95-v2.0.0`
pack remains available; the current benchmark uses the separate Core pack below.

## Core benchmark

The current benchmark and site board use `p95-core`: 26 scenarios × 4 tools = 104
tasks. Its locked scenario set is `r01 r02 r04 r05 r06 r08 r10 r11 r12 c01 c03
c04 c06 c08 c09 m01 m04 m05 m06 w01 w02 w07 a01 a04 a05 a06`. The full
`p95-matrix` pack remains available as the 40-scenario superset for historical
runs; it is not the current board.

First publication uses **one repeat by default**, configurable with `--repeats`.
Each task's repeat mean contributes equally within its category. V2 weights are
repair 25, migration 15, correlation 25, workload 20, analysis 15. Missing slots or
categories contribute zero to a provisional lower bound, not a renormalized score;
incomplete runs cannot enter the site data builder. An empty returned answer is a
graded attempt; transport/budget exhaustion is Not run.

V2 composite is `100 * (.95 * Q + .05 * E)`, where Q is weighted quality (0–1).
For each trial, `e = q * (.5 * credit(T/T_budget) + .5 * credit(L/L_budget))`,
where `credit(r) = clamp((3 - r)/2, 0, 1)`; E aggregates e exactly like quality.
Each component earns full credit at or below budget, declines linearly to 0 at
three times budget, and stays at 0 above that. T counts provider-reported output
tokens only, including reasoning tokens once as already counted by the provider
adapter; input and cached tokens do not affect efficiency.
L is accepted-response provider time, excluding retry backoff and script execution.
Frozen task-specific efficiency budgets must be supplied with `--efficiency-budgets`
as JSON mapping IDs to positive `tokens` and `latency_seconds` values. Token budgets
are three times the reference solution/answer token count using one recorded fixed
tokenizer across the pack. Latency budgets are 90 seconds for analysis tasks and
180 seconds for script tasks. The published v2 budgets are checked in at
`packs/p95-v2.0.0/efficiency-budgets.json` (o200k_base tokenizer). V1 scoring and
summaries are unchanged.

V2 runs are stored under `OUT/p95-v2.0.0/RUN_ID`; v1 preserves `OUT/RUN_ID` for
compatibility. Both get a hashed `pack.json` snapshot
containing scoring rules, task inventory, efficiency budgets and repeat count.
V2 `metadata.json` records `harness_commit` and `harness_dirty` (tracked changes present).
Offline `p95bench score DIRECTORY` dispatches by that snapshot. Old directories
without a snapshot are v1. Mixed pack inputs and altered snapshots are rejected.
Site data generation preserves the unchanged v1 `leaderboard.json`; the public
v2 builder uses `npm run data -- --pack p95-v2.0.0 --tasks PUBLIC_TASK_ROOT RUN_DIR...`
and writes `leaderboard-p95-v2.0.0.json`, never overwriting v1. The site lands on v2
by default; `?pack=v1` selects the legacy board.

By default, local V2/Core runs have **no spend limit**: an unset or blank
`P95_BUDGET_LIMIT_USD` is unlimited, and no tasks are skipped for budget exhaustion.
Set it to a positive USD amount (at most $100) to opt into a shared spend cap.
The ledger at `~/.local/state/p95-bench/p95-v2.0.0-budget.json` still records charges;
`P95_BUDGET_LEDGER` overrides its path. Removing the limit also removes a previous
cap from that ledger without deleting recorded charges. Use a separate ledger
when opting back into a cap, or when changing an existing cap. Do not delete/reset
the shared local ledger between models. File-locked, persistent maximum-cost
reservations happen before generation and include every possible transport retry. Returned usage releases
unused reserve; requests without usage retain their conservative bounds, including
interrupted processes. Bounds use uncached input at serialized-message bytes plus
4,096 framing tokens, the configured output cap and recorded prices. This is a
conservative list-price guard, not a provider invoice guarantee; unexpected usage
above a reservation stops the run for audit. Ledger corruption/cap mismatch fails
closed. With an explicit cap, budget exhaustion skips remaining slots and still
writes a provisional summary. Uncapped runs retain the same per-attempt costs,
token totals and `estimated_cost_usd`; `metadata.json` records `spend_cap_usd: null`.
New V2/Core summaries include `cost_by_tool`, including repair and charged not-run
responses. Core rankings show each tool's usage-priced cost alongside its score;
the total model cost includes repairs too. API keys are not needed for the
synthetic tests; no paid runs ship here.

v2 is public-only: all 40 tasks are checked in and there are no held-out tasks.
Runs are always marked public, and the site builder rejects any private run.

## Run on GitHub Actions

The workflow file is not included in this repository; a maintainer must first add
`.github/workflows/benchmark.yml` by copying `ci/benchmark.yml` there.

Dispatch the `Benchmark` workflow with one `provider model` entry per line or
comma-separated in **models**, for example `deepseek deepseek-flash`. Add the
matching repository secret for every selected provider: `DEEPSEEK_API_KEY`,
`MODELSCOPE_API_KEY`, `MODEL_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
`TINKER_API_KEY`, or `OPENAI_API_KEY`. Only secrets for selected providers are
needed.

Leave **cap_usd_per_model** blank for unlimited spend, or set it for an optional
total spend limit of each model, and choose
**repair_rounds** (`1` by default, or `0`). The cap is per model and is split
evenly across its four tool shards; each shard checks a conservative reservation
against its ledger before every provider call. **scenarios** optionally selects
comma-separated Core scenario codes for a smoke run. A scenario-filtered run
skips the Core merge.

The workflow plans a JMeter, k6, Locust and Gatling shard per model and uploads
each as `shard-<slug>-<tool>`. For a full run, the merge job validates and
combines those sources into a `core-<slug>` artifact containing one Core run.
For publication, download each `core-<slug>` artifact, commit its run files
under `results/p95-core/<model>/`, then regenerate the site data from the
published run directories:

```sh
cd site
npm run data -- --pack p95-core ../results/p95-core/*/
```

This command regenerates the whole board, so always pass every published Core
run directory. The builder doesn't write `budget_file_sha256` or
`publication_note`, so carry them over from the committed JSON when regenerating.

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
legacy (`p95-v1`) path uses a subprocess timeout, not a security sandbox. JMeter plugins and
scripts can access the local machine. Do not run untrusted task packs on a
shared host. The demo app binds only to loopback and stops after each grade.
The provider key is used only for API requests, never included in prompts or
result files. Execution subprocesses do not need provider credentials.

## Providers and prices

All providers use `DEFAULT_OUTPUT_TOKENS = 64_000` in `providers.py` for their
native output-limit field. `ProviderConfig.output_cap(model)` and
`request_extra(model)` share the effective cap with initial/repair reservations,
truncation checks and run metadata. Only documented lower output maxima belong
in `model_output_limits`; no such lower maximum was found for the registry below.
Unknown maxima retain the requested shared default, not a claim of API support.

Output-limit documentation audit (2026-11-22; all effective caps are **64,000**):

| Provider/models | Documented maximum output | Source |
| --- | --- | --- |
| Anthropic Sonnet 5.5 / Opus 5.5 | 128K each | [Models](https://platform.claude.com/docs/en/about-claude/models/overview) |
| OpenAI GPT-6.2 Sol / Astra | 128,000 each | [Sol](https://developers.openai.com/api/docs/models/gpt-6.2-sol), [Astra](https://developers.openai.com/api/docs/models/gpt-6.2-astra) |
| Gemini 4 Carbon / Argon / Hydrogen | 65,536 synchronous each | [Carbon](https://ai.google.dev/gemini-api/docs/models/gemini-4-carbon), [Argon](https://ai.google.dev/gemini-api/docs/models/gemini-4-argon), [Hydrogen](https://ai.google.dev/gemini-api/docs/models/gemini-4-hydrogen) |
| Gemini 3.8 Flash | 65,536 | [Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) |
| DeepSeek Flash / V4 Pro | 384K each | [Models](https://api-docs.deepseek.com/quick_start/pricing) |
| Qwen4-Max | 262,144 thinking + answer; 131,072 non-thinking | [Model](https://www.alibabacloud.com/help/en/model-studio/qwen4-max) |
| Qwen3.8-Max / Qwen3.7-Max | 131,072 each | [3.8](https://www.alibabacloud.com/help/en/model-studio/qwen3-8-max), [3.7](https://www.alibabacloud.com/help/en/model-studio/qwen3-7-max) |
| Qwen3.8-Flash-Next | Unknown; no hosted output maximum verified | [Vendor announcement](https://www.alibabacloud.com/blog/603501) |
| Muse Spark 1.3 | 131,072 in official agent configuration | [Official configuration](https://dev.meta.ai/docs/quickstart) |
| Muse Ember 1.0 | 524,288 | [Model](https://dev.meta.ai/docs/muse-ember) |
| Muse Wonder 1.0 / Spark 1.2 | Unknown; model pages specify context, not maximum output | [Wonder](https://dev.meta.ai/docs/muse-wonder), [Spark](https://dev.meta.ai/docs/muse-spark) |
| Inkling / Inkling-Small / Inkling-2-Small | Unknown; context is not a separate output maximum | [Models](https://tinker-docs.thinkingmachines.ai/tinker/models/models_and_pricing/), [Chat API](https://tinker-docs.thinkingmachines.ai/inference/openai/chat-completions/) |

Qwen limits above are the vendor's model specifications; ModelScope's hosted
alias limits have not been independently verified. Tinker lists 64K contexts for
base Inkling/Inkling-Small (1M sampling for Inkling-2-Small); a 64,000 output
request may not fit alongside a long prompt. No output limit was inferred from
those context lengths. Verify these unknown/host-specific limits before paid runs.

Requests allow 600 seconds of socket inactivity. Anthropic and Meta stream long
generations (Qwen already streams), avoiding the non-streaming timeout path;
this is not a 600-second total generation deadline. Anthropic's native SSE
accumulator requires final usage and `message_stop`, excludes thinking text,
and retries incomplete streams as uncertain, potentially billable attempts.
See [Anthropic streaming](https://platform.claude.com/docs/en/api/streaming) and
[Meta streaming](https://dev.meta.ai/docs/protocols/chat-completions).

For a fixed prompt, the output portion of each worst-case reservation changes
by `(effective_cap - old_cap) * output_price / 1_000_000`, multiplied by
`1 + transport_retries` (currently 3) for the shared v2 ledger. Moving from
16,384 to 64,000 multiplies that portion by 3.90625; moving from 65,536 to
64,000 reduces it by 2.34375%. Initial and repair calls use the same formula.

### Qwen via ModelScope

Use `--provider qwen` with `MODELSCOPE_API_KEY`. The OpenAI-compatible endpoint
is `https://api-inference.modelscope.ai/v1` (override with `QWEN_BASE_URL`).
Models: `Qwen-Ambassador/Qwen4-Max`, `Qwen-Ambassador/Qwen3.8-Max`,
`Qwen-Ambassador/Qwen3.8-Flash-Next`, and `Qwen-Ambassador/Qwen3.7-Max`.
Thinking is enabled (`enable_thinking: true`) at `reasoning_effort: high`
(Qwen4-Max's default; no `thinking_budget`). Responses are streamed, since some
ModelScope models accept thinking only on streamed calls.
Temperature is left at the provider default, and the output cap is 64,000 tokens
via `max_completion_tokens`, which covers reasoning and reply, matching DeepSeek.
The [Qwen API reference](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-dashscope)
documents thinking control for the 3.8 and 3.7 series and a temperature range
that includes 0. ModelScope compatibility is checked by the real runs.

ModelScope inference uses a free tier with daily limits. Reported costs are
**Model Studio list-price estimates, not charges from ModelScope**. Singapore
[list prices](https://www.alibabacloud.com/help/en/model-studio/model-pricing)
per million input/output tokens are $1.50/$4.50 for 4-Max (cache hits $0.075),
$2/$6 for 3.8-Max, $0.15/$0.47 for 3.8-Flash (used as the estimate for
Flash-Next), and $2.50/$7.50 for 3.7-Max.
Cache hits use 10% of the input rate (5% for 4-Max). Run metadata preserves the
pricing source and free-tier note, which also appear in Model focus and the cost
explainer.

Rate limits and transient transport/server errors retry five times, using
exponential backoff or a numeric `Retry-After`, capped at 60 seconds per wait.
A streamed response that ends mid-event or without usage counts as a transport
error and is retried; its tokens are unknown, so it is left out of the
published cost, while the spend ledger keeps its full reserved estimate.
Exhausted requests are recorded in `not-run.jsonl`, not graded as failures.
Authentication and other non-retryable errors abort the run. Published coverage
counts only actual responses; an empty response is still a real, graded attempt.

`src/p95bench/providers.py` defines a minimal `Provider.send(model, messages)`
protocol returning text, token usage, and elapsed seconds.
`OpenAICompatible` accepts a base URL, key environment-variable name, prices,
temperature (use `None` when unsupported), and optional request fields.
Add another OpenAI-compatible provider by adding a `ProviderConfig` entry to
`PROVIDERS`. Anthropic uses a native Messages API adapter; Google is future work.
The runner supports dependency injection for testing and custom adapters.

For Anthropic, set `ANTHROPIC_API_KEY` and use `--provider anthropic` with
`--model claude-sonnet-5-5` or `--model claude-opus-5-5`. The adapter sends the
fixed prompt through the top-level `system` field with the shared 64,000-token output
cap, `stream: true`, `thinking: {type: adaptive}` and
`output_config: {effort: high}`. Metadata records `reasoning_mode: thinking-high`.
See Anthropic's [thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
and [effort](https://platform.claude.com/docs/en/build-with-claude/effort) documentation.
Temperature is omitted for Sonnet 5.5 and Opus 5.5 because their API rejects
the parameter as deprecated; published run metadata records this exception.
It uses `x-api-key` authentication and API version
`2023-06-01`, without an SDK. Discover IDs in Python with
`get_provider('anthropic').models()` from `p95bench.providers` (there is no
discovery CLI). Prices live in `PROVIDERS['anthropic'].prices` in
`src/p95bench/providers.py` and follow the [Anthropic API pricing table](https://platform.claude.com/docs/en/about-claude/pricing).
Sonnet 5.5 costs $2/$10 per million fresh input/output tokens, and Opus 5.5
costs $4/$20. Both charge 0.05× the input rate for cache reads and 1.25× for
five-minute cache writes. The direct adapter does not request prompt caching;
it accounts for cache usage when the API reports it.

Thinking Machines (Tinker) uses the existing OpenAI-compatible adapter. Set
`TINKER_API_KEY` and use `--provider thinkingmachines`. The beta endpoint is
`https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1`, with bearer
authentication and `/chat/completions`. Temperature is 0 and the output cap is
64,000 tokens, including reasoning. If reasoning exhausts that cap, null answer
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
requests use `max_completion_tokens: 64000` and omit temperature. Chat
Completions requests set `reasoning_effort: high`, following OpenAI's
[GPT-6.2 guidance](https://developers.openai.com/api/docs/guides/latest-model);
new run metadata records `reasoning_mode: thinking-high`. Reasoning tokens
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
models. DeepSeek thinking uses high reasoning effort with a 64,000-token output cap.
Temperature is omitted because it is ignored in thinking mode; the cap includes
reasoning and visible output. Provider failures abort the run rather than count as
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
Reported `input_tokens` include cache reads (`cached_tokens`) and writes
(`cache_write_tokens`); those two fields are subsets of input, not extra tokens.
Old results without cache counts still score the same.
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
Fonts are self-hosted.

Number keys 1–9 select the first nine sections in DOM order; 0 selects section 10. The rail exposes
every section; use it or Page Up / Page Down if more sections are added.
The Unit matrix is built from published attempt records, retaining repeats and
adding hatched placeholders only for missing tasks. Group pass rates exclude
Not run. Use Left / Right, Home / End within the matrix to inspect a square,
or open its semantic data table. Reduced motion disables regrouping animation.

The board shows Core only, with no pack/version switcher. Generate
`leaderboard-p95-core.json` with `npm run data -- --pack p95-core
<public-run-directories>`. Core contains 26 public scenarios across four tools
(104 tasks); category counts are derived from its task inventory. Quality,
quality-gated efficiency and the 95/5 composite stay separate.
Models missing a selected-pack run remain visible without invented scores.
Synthetic fixtures in `site/tests/` are test-only and must never be published as
a leaderboard or copied into production data.

To regenerate the leaderboard and build:

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
Leaderboard and replay labels mark partial runs with their distinct-task coverage.
Repeated attempts do not fill missing tasks, and not-run tasks are not failures.

Model families and release order live in `site/src/data/model-registry.json`.
The site defaults to the newest benchmarked release per family; enable
"Show earlier versions" (`?versions=all`) to include every published result at
its full rank. Add increasing `release_order` values within a `family` and set
`discontinued: true` only when the provider no longer offers a model. Unknown
model IDs get their own latest family automatically.

## Google Gemini

The `gemini` provider calls the native Gemini API `generateContent` endpoint at
`https://generativelanguage.googleapis.com/v1beta` using `GEMINI_API_KEY` in the
`x-goog-api-key` header. Models are `gemini-4-carbon`, `gemini-4-argon`,
`gemini-4-hydrogen`, and `gemini-3.8-flash`. Example:

```sh
p95bench run --provider gemini --model gemini-4-carbon --out results
```

Temperature is omitted, retaining the API's default of 1. Default thinking is
unchanged. `maxOutputTokens` is 64000, shared by thinking and answer tokens.
Published response output tokens include both `candidatesTokenCount` and
`thoughtsTokenCount`; `thinking_tokens` and `finish_reason` are also recorded.
Thinking text is never substituted for an answer. A thinking-only `MAX_TOKENS`
response is an actual empty response, not a transport failure; check
`GeminiResponse.thinking_exhausted` and stop for review before changing settings.

[Standard list prices](https://ai.google.dev/gemini-api/docs/pricing), USD per
million tokens, verified November 18, 2026:

| Model | Input | Cached input | Output including thinking | Valid through |
| --- | ---: | ---: | ---: | --- |
| Carbon | 2.00 | 0.10 | 10.00 | November 30, 2026 |
| Argon | 2.00 | 0.10 | 10.00 | November 30, 2026 |
| Hydrogen | 1.50 | 0.075 | 7.50 | No expiry listed |
| 3.8 Flash | 0.75 | 0.075 | 3.75 | December 31, 2026 |

Runs choose the date-effective schedule using their UTC start time, recording
the pricing date and snapshot in metadata and attempts. Carbon/Argon rates
double December 1; Flash rates double January 1. Published snapshots are never
rewritten. No explicit caches, storage or grounding tools are created.
Library callers pass `price=None` to select the dated schedule for a built-in
Gemini model. An explicit `Price` is honored for built-in and custom models;
custom models without a supplied price fail before any requests are sent.
Transient requests use bounded backoff; exhaustion is Not run. A client timeout
of 600 seconds accommodates thinking without changing the output cap.
`Gemini(config, spend_limit=3)` optionally bounds total estimated spend across
models and retries. Each network attempt reserves full capped output and a
conservative text-input allowance (request bytes plus 1024 framing tokens).
Successful responses reconcile to returned usage; unknown outcomes retain their
full reservation. Insufficient remaining budget stops before another request.

## Meta Muse

The `meta` provider uses OpenAI-compatible Chat Completions at
`https://api.meta.ai/v1`, authenticating with `MODEL_API_KEY`. Models are
`muse-wonder-1.0`, `muse-spark-1.3`, `muse-spark-1.2`, and `muse-ember-1.0`.
Contributor variants and Spark 1.1 are not included.

Temperature is omitted per Meta's recommendation (default 1). Reasoning effort
is omitted: Wonder and Ember default to max, Spark to high. The 64000
`max_completion_tokens` cap includes reasoning and visible output. Returned
`completion_tokens` already includes reasoning; the harness does not add
`completion_tokens_details.reasoning_tokens` again. It records the latter
separately with the finish reason, never substituting reasoning for an answer.
An empty reasoning-only `length` response requires review before cap changes.

[Verified list prices](https://dev.meta.ai/products/meta-model-api), USD per
million input / cached input / output tokens: Wonder 4 / 0.20 / 20;
Spark 1.3 and 1.2 1.25 / 0.15 / 4.25; Ember 0.10 / 0.004 / 0.50 below 200k
context. The Ember adapter conservatively rejects larger text contexts before
sending, rather than silently underbilling the higher tier. Prompts, caps and
grading are otherwise unchanged. Transient requests retry with bounded backoff;
exhaustion is Not run. The client timeout is 600 seconds.
`Muse(config, spend_limit=3)` shares the conservative per-network-attempt budget
policy documented above, including retained reserves for unknown outcomes.

## V2 execution and demo foundations

`p95bench.demo_v2.demo_app_v2(Profile(...), seed=...)` is separate from the frozen
v1 `demo_app()`. Its public protocol includes:

- `GET /v2/login-form`, `POST /v2/login` (`bench` or `bench-1` through `bench-16`,
  password `bench`), optional form CSRF, exclusive sessions and rotating CSRF.
- `GET /v2/catalog`, `/v2/catalog/page?cursor=TOKEN`, `/v2/catalog-html`: seeded dynamic
  product/variant IDs, pagination and stock. JSON products live at `data.products`.
- `POST /v2/carts` with `product_id` and `variant_id`; `GET/PATCH /v2/carts/{id}`.
  PATCH takes `quantity` and an `If-Match` ETag. Cart creation returns ETag `"1"`
  by default; `dynamic_etag` profiles return a fresh seeded revision to correlate.
- `POST /v2/session/renew` resets the deterministic operation-count expiry and
  returns fresh CSRF. After re-login, `POST /v2/carts/{id}/recover` rebinds an
  account-owned cart. Mutations require `X-CSRF-Token`.
- `POST /v2/orders` takes `cart_id`, an `Idempotency-Key`, optionally a reservation
  and, for ETag profiles, `If-Match`. Controlled business declines return HTTP 200
  with `accepted: false`. A lost response after commit can be retried exactly once
  without a duplicate business commit.
- `POST /v2/reservations` takes `cart_id` and returns a dynamic `status_url`;
  `GET /v2/jobs/{id}` transitions through bounded polls, with configurable polling
  limits and ready/failed outcomes. Ready jobs supply a one-use `reservation_id`.
- `/v2/browse`, `/v2/search`, `/v2/work`: controlled delay phases and deterministic
  rate faults with `Retry-After`. Delays occur outside state locks.
- Optional one-use login redirects, `/v2/challenge-form`, `/v2/challenges/{id}`
  and `/v2/claim/{token}` bridge HTML challenges to session-owned JSON cart grants.
  Profiles select both hidden-input layouts, eligibility/order variants, challenge
  expiry, one scheduled ETag conflict, and separate checkout slowdown phases.
  `/v2/health` stays fast and is never business-completion evidence.
  Timed phases use the owner's monotonic clock, anchored on the first operation
  of the relevant pool; deterministic operation-count phases are also available.

The evaluator receives `shop.audit()`: a defensive copy of append-only server-owned
start/end, authentication, cart, commit, job, rate/fault and phase events using a
monotonic clock. There is **no public audit/reset/control endpoint**. Every trial
gets fresh state; audit overflow and bounded state capacity fail closed.
Final audit snapshots wait for in-flight requests to drain with a bounded deadline;
`shop.audit(wait=False)` offers a live snapshot while a trial is running.

`p95bench.worker.execution_trial(workdir, profile=..., seed=..., image=...)` creates
an isolated worker for the v2 graders. Images must be preloaded and pinned by
digest, and include Python 3 for the public-protocol relay. The default pinned
Python image is a safety probe, **not a JMeter/k6 runtime**. Native tool images are built
with `python -m p95bench.images build`; v1 still uses its existing trusted launcher.

Actors run unprivileged with a read-only root, no capabilities, no-new-privileges,
one CPU, bounded stdout/deadlines, read-only explicit byte
fixtures, and size-limited ephemeral scratch. No home, provider environment or
Docker socket is mounted. Docker networking is disabled entirely: a container-local
loopback relay forwards only to a fresh Unix socket serving the public shop API.
Audit/control stays in the owner process, outside the container. Timeout/output
limits remove the entire container, including descendants. Missing runtime/image
or failed cleanup is an infrastructure error; there is no host execution fallback.
The probe defaults to 256 MiB RAM/64 PIDs/128 file descriptors. Native tools use
768 MiB/256 PIDs/512 file descriptors; the same policy applies to capacity references.
For bounded export, a root supervisor retains only SETUID/SETGID/KILL; generated
processes are UID65534 with zero capabilities and cannot modify supervisor state.
Returned stdout is untrusted tool evidence; business credit must use the owner audit.

Docker integration tests are opt-in and never pull images automatically:
`docker pull python@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b`,
then `P95_TEST_DOCKER=1 .venv/bin/python -m pytest tests/test_worker.py`.
The full native suite additionally requires the two built tool images:
`P95_TEST_DOCKER=1 .venv/bin/python -m pytest -q`. It makes no provider calls.
Before workload generation/grading, a trusted native reference qualifies the evaluator;
an insufficient evaluator aborts as infrastructure-unavailable, not model score zero.
Capacity references keep their 12-second duration configuration and measure the three seconds
after five seconds of business-operation warmup at a minimum of 3× the task rate.
Gatling allows 60 seconds from worker launch to the first business operation for
cold JVM startup and Java compilation, with a 90-second preflight deadline.
Its workload grader uses the same startup allowance and 50 seconds of additional
wall-clock headroom; scored workload windows and drain limits are unchanged.
JMeter, k6 and Locust retain their 10-second startup allowance and existing deadlines.
`capacity.json` records per-pool measured startup, allowance and timeout.

## License

MIT. Copyright 2026 NaveenKumar Namachivayam.
