# p95-v2.0.0 public evaluation contract

Exactly **40 public tasks**: repair11, migration6, correlation10, workload8, analysis5.
Thirty-five execute native scripts; five compare structured analysis with deterministic
CSV/JSON evidence. There are no model API calls in the reference or mutation tests.
The pack is public-only: every scored task is checked in and there are no held-out tasks.

## Prepare the evaluator

From the checkout, with Docker available and the Python development dependencies installed:

```sh
.venv/bin/python -m p95bench.images build
P95_TEST_DOCKER=1 .venv/bin/python -m pytest -q
.venv/bin/ruff check .
cd site && npm test && npm run build
```

`workers/Dockerfile` pins k6 1.0.0, Java17 and the Python relay base by registry
digest. Both the builder and recipe verify the official JMeter5.6.3 archive SHA512.
The builder records immutable local image IDs and recipe hashes in
`~/.local/state/p95-bench/tool-images.json` (override with `P95_TOOL_IMAGES`).
Build/download is an explicit preparation step; execution never pulls an image and
has no host-engine fallback. Native-version tests check actual engine output.

The CLI selects this task root by default when `--pack p95-v2.0.0` is chosen. Real
provider execution still requires frozen per-task efficiency budgets
(`efficiency-budgets.json` in this directory) and uses the
shared $30 ledger; do not reset it between models. Repeats default to1. No cost or
60–70 calibration claim is made without an authorized model cohort.

Composite scoring is 95% quality and 5% quality-gated efficiency. Each efficiency
component uses `clamp((3 - usage/budget)/2, 0, 1)`: full credit at or below budget,
half at twice budget, and zero at three times budget or above. Token usage is
provider-reported output tokens only, including reasoning tokens once as counted
by the adapter, independent of input/cache tokens. Latency is model API response
time, excluding retry backoff and script execution. The two component credits are
averaged and multiplied by task quality. Frozen token budgets are three times the
reference answer's token count using one recorded tokenizer for all tasks;
latency budgets are 90 seconds for analysis and 180 seconds for script tasks.

## Native evidence and bounds

One CPU, 768MiB RAM, 256 PIDs, 512 file descriptors, no network, no home/environment
credentials/Docker socket. The read-only runtime exposes only a loopback relay to the
public shop. Actors are UID65534 without capabilities; an independent owner holds
the audit and fault configuration. A narrowly privileged supervisor owns `/control`,
terminates/reaps all generated descendants (including detached ones), then exports.
Cleanup/control/runtime failures abort evaluation as infrastructure failures.

Exports allow at most4 flat ASCII filenames, at most8MiB/file and16MiB total; native
tool contracts are narrower. Reads use NOFOLLOW/NONBLOCK and regular-file checks;
directories, FIFOs, links, missing and oversized files produce bounded artifact errors.
Timeouts retain stopped-process partial artifacts and owner audit, never fake success.
Temporary output and scratch are bounded independently. Generated code cannot create
the supervisor's completion record. k6 custom `handleSummary` is disabled.

Each case retains native JTL or NDJSON/HTTP-debug output, `audit.json`, `execution.json`
(image ID, recorded fresh seed, profile, return code and artifact errors). Unpredictable
server request IDs in response headers join native worker identities to evaluator-owned
authentication, mutation authorization, cart ownership and business commits. Client
counters or stdout alone cannot establish a business rate or a checkout.

JMX accepts a documented declarative subset only: no DTD/entities, script/process
components, custom implementations/classpaths, result listeners or executable functions.
The full API and safe subset appear in each executable task's protocol fixture. Per-task
fixture aliases mount bounded inputs read-only, without exposing owner audit/configuration.

## Rubrics and prerequisite gates

Every YAML declares four checkpoint IDs, points totaling100, descriptions, and earlier
checkpoint prerequisites. Gates operate per fixture case: a checkpoint fraction is
capped by every prerequisite fraction; cases are averaged, not cherry-picked. This
preserves partial credit without multiplying the same failure twice.

- Malformed JMX/empty artifacts have explicit all-zero vectors.
- A broken CSRF extractor loses extraction and its gated checkout/safety credit.
- Negative assertion controls require a passing positive control. Slow-checkout and
  slow-catalog variants distinguish the intended threshold scope from global/fast-route
  thresholds. Declines, genuine denials and failed reservations must be detected rather
  than accepted as successful HTTP responses.
- Native tests assert reference100 for every public execution task and exact vectors
  for the injected repair faults. Independent analysis mutations lose exactly their
  section's points. An extra workload health route loses only the20-point limits
  checkpoint while preserving the valid rate and uniformity checkpoints.
  An independent valid-but-wrong ETag retry payload loses only the25-point preservation
  checkpoint. Fresh seeded ETags prevent hard-coded initial/retry revisions passing.
- Analysis answers use explicit field types/enums, numeric tolerance1e-6 and fixture
  evidence identifiers. Percentiles use nearest rank, with stated warmup/parent filtering;
  missing response-time distributions are never invented.

## Workloads and capacity

Before any workload provider request, and independently at the grader entry point,
run a trusted reference in the **same pinned engine and resource policy** at >=3x the
largest requested rate. Pools run concurrently. Probe the slowest configured service
profile, not just the fast phase:12s native execution, three seconds independently measured
after five warmup seconds from the first owner operation (starts, or successful completions
for the completion task). Record
the qualifying rate, resource policy and evidence directory in `capacity.json`. Reuse a
qualification only in the current evaluator process for the same image/profile/pools.
Starting another provider run clears the qualification cache and retains fresh evidence.
Failure raises infrastructure-unavailable before provider generation, never score zero.
This short headroom probe is not a claim of long-duration production capacity.

The first valid authenticated operation anchors T0 and must start within10s of engine
launch. Warmup5s, score30s in six5s windows, drain<=5s. Phase changes follow the declared
three10s scored plateaus. Full rate credit allows an absolute count error of
max(10% of expected arrivals,1 arrival) in every window. Thus at1/s a five-second
window accepts4–6 arrivals. Beyond that tolerance, credit declines linearly to zero
at40% relative error. The same quantization allowance applies to the full-window
mean-rate cap. Uniformity checks per-second bursts and window minimums.
Window credit is capped by the full scored-window mean rate; isolated correct bursts
cannot hide an overall rate miss.
Only declared POST routes and independently committed journeys count, never health,
setup requests, duplicate commits or model counters. Completion-mode retries respect
Retry-After per native worker, even across session renewal; raw requests have a ceiling.
Unexpected errors/dropped work are bounded at1%, VUs at the declared cap. Scheduled429
responses are expected, but never count as successful completions. Reference JMeter pacing
uses1% headroom, within the public tolerance, to avoid exact low-rate bin-edge jitter.
Trusted JMeter references run38s so session setup does not truncate the owner-anchored
35s warmup/scoring period; subsequent traffic must still fit the same5s drain bound.

V1's tasks, launcher, scoring and published results remain separate and unchanged.
