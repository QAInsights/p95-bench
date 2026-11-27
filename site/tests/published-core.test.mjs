import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { test } from 'node:test';
import { buildData, loadTaskData } from '../scripts/build-data.mjs';
import { boardModels } from '../src/lib/board.mjs';

const data = JSON.parse(await readFile(new URL('../src/data/leaderboard-p95-core.json', import.meta.url)));
const runs = new URL('../../results/p95-core/', import.meta.url);
const budget = await readFile(new URL('../../packs/p95-core/efficiency-budgets.json', import.meta.url));
const budgetHash = createHash('sha256').update(budget).digest('hex');
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

test('Core regrades only fix-affected JMeter phases without inventing repairs', async () => {
  assert.match(data.publication_note, /beda2d5/);
  assert.match(data.publication_note, /only fix-affected/);
  for (const [directory, phases] of Object.entries({
    'claude-sonnet-5-5': 4, 'Qwen-Ambassador-Qwen4-Max': 0,
    'deepseek-v4-pro': 0, 'deepseek-flash': 0,
  })) {
    const metadata = JSON.parse(await readFile(new URL(`${directory}/metadata.json`, runs)));
    const note = metadata.regraded.at(-1);
    assert.match(note.harness_commit, /^beda2d5/);
    assert.equal(note.grader, 'native_numeric_affected_phases');
    assert.equal(note.initial_attempts_scanned, 104);
    assert.equal(note.initial_attempts_regraded, phases);
    assert.equal(note.recorded_repairs_regraded, phases);
    const attempts = (await readFile(new URL(`${directory}/attempts.jsonl`, runs), 'utf8'))
      .trim().split('\n').map(JSON.parse);
    const affected = attempts.filter(attempt => attempt.offline_regrades?.length);
    assert.equal(affected.length, phases);
    assert.ok(affected.every(attempt => attempt.task_id.endsWith('-jmeter')
      && attempt.offline_regrades.at(-1).initial_regraded
      && attempt.offline_regrades.at(-1).recorded_repair_regraded));
  }
});

test('Core publishes complete public Sonnet, DeepSeek, Qwen and OpenAI runs with current provenance', async () => {
  assert.equal(data.pack_id, 'p95-core');
  assert.equal(data.pack_digest, 'f07f902e5bf1542afa91818eb962abbdb4873c8999983f17ff7a983a685fc9de');
  assert.equal(data.budget_file_sha256, budgetHash);
  assert.match(data.publication_note, /45066297860/);
  assert.match(data.publication_note, /final answer after the one allowed repair/);
  assert.doesNotMatch(JSON.stringify(data), /5a5d088256624100/);
  assert.deepEqual((await readdir(runs)).sort(), ['Qwen-Ambassador-Qwen4-Max', 'claude-haiku-5-5', 'claude-sonnet-5-5', 'deepseek-flash', 'deepseek-v4-pro', 'gemini-4-neon', 'gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.2-sol', 'inkling-2-mini', 'inkling-2-small']);
  assert.deepEqual(data.models.map(model => model.model).sort(), ['Qwen-Ambassador/Qwen4-Max', 'claude-haiku-5-5', 'claude-sonnet-5-5', 'deepseek-flash', 'deepseek-v4-pro', 'gemini-4-neon', 'gpt-6-luna', 'gpt-6.1-terra', 'gpt-6.2-sol', 'thinkingmachines/Inkling-2-Mini', 'thinkingmachines/Inkling-2-Small']);
  const model = data.models.find(model => model.provider === 'anthropic');
  assert.equal(model.provider, 'anthropic');
  assert.equal(model.output_cap, 64000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.harness_commit === model.harness_commit));
  assert.match(model.cost_note, /list-price estimate/);
  assert.match(model.cost_note, /45066297860/);
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.cost, 19.925888);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 24);
  near(model.overall_score, 68.05146140886016);
  near(model.after_repair.overall_score, 84.54197789514849);
  near(model.overall_score, .95 * model.quality_score + .05 * model.efficiency_score);
  const displayed = boardModels(data).find(model => model.key === 'anthropic/claude-sonnet-5-5');
  assert.equal(displayed.score.toFixed(1), '84.5');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  assert.equal(displayed.score, model.after_repair.overall_score);
});

for (const expected of [
  { model: 'deepseek-v4-pro', first: 44.15576676321813, after: 48.24482914686975,
    cost: 14.505640776, repairs: 29 },
  { model: 'deepseek-flash', first: 53.14515366802733, after: 64.44586406147408,
    cost: 5.054729706, repairs: 27 },
]) test(`Core ${expected.model} preserves matching manifest, public coverage, usage costs and after-repair display`, async () => {
  const model = data.models.find(model => model.model === expected.model);
  assert.equal(model.provider, 'deepseek');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.output_cap, 64000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.match(data.publication_note, /45082823742/);
  assert.match(model.cost_note, /45082823742/);
  assert.ok(model.cost_note.includes(`${expected.repairs} repair responses`));
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.harness_commit === model.harness_commit));
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, expected.first);
  near(model.after_repair.overall_score, expected.after);
  near(model.cost, expected.cost);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, expected.repairs);
  const displayed = boardModels(data).find(model => model.key === `deepseek/${expected.model}`);
  assert.equal(displayed.score.toFixed(1), expected.after.toFixed(1));
  assert.equal(displayed.score, model.after_repair.overall_score);
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
});

test('Flash provenance identifies the rerun Gatling shard without adding failed-run usage', () => {
  const model = data.models.find(model => model.model === 'deepseek-flash');
  assert.match(model.cost_note, /attempt 2 after an IncompleteRead transport failure in attempt 1/);
  assert.match(data.publication_note, /Flash's Gatling shard uses attempt 2/);
  assert.ok(model.derived_from.some(source => source.run === '20261123T020612Z-9edc7cba'));
});

test('Qwen Core keeps public list pricing, complete coverage and after-repair scoring', async () => {
  const model = data.models.find(model => model.model === 'Qwen-Ambassador/Qwen4-Max');
  assert.equal(model.provider, 'qwen');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.output_cap, 64000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.harness_commit, '2d6e43f309d0ef51698fee94e503439cdb4b09a6');
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.spend_cap_usd === null && source.harness_commit === model.harness_commit));
  assert.match(model.cost_note, /ModelScope free tier/);
  assert.match(model.cost_note, /45113245263/);
  assert.match(data.publication_note, /Qwen ran uncapped/);
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, 69.41250924142483);
  near(model.after_repair.overall_score, 77.31725653059549);
  near(model.cost, 12.0328038);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 16);
  const displayed = boardModels(data).find(model => model.key === 'qwen/Qwen-Ambassador/Qwen4-Max');
  assert.equal(displayed.score.toFixed(1), '77.3');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
  near(displayed.toolCosts.jmeter, 4.8591948);
  near(displayed.toolCosts.k6, 1.7839137);
  near(displayed.toolCosts.locust, 1.7608776);
  near(displayed.toolCosts.gatling, 3.6288177);
});

test('OpenAI Core keeps high reasoning, complete coverage, output headroom and after-repair scoring', async () => {
  const model = data.models.find(model => model.model === 'gpt-6.2-sol');
  assert.equal(model.provider, 'openai');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.output_cap, 128000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.harness_commit, '9a664f881e7c78dfea26e54b71b0e0d8fc6fa514');
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.spend_cap_usd === 12.5 && source.harness_commit === model.harness_commit));
  assert.match(model.cost_note, /High reasoning with a 128,000-token output cap/);
  assert.match(model.cost_note, /no first try or repair ended at the output cap/);
  assert.match(model.cost_note, /45511510673/);
  assert.match(data.publication_note, /OpenAI and Tinker use a 128,000-token output cap/);
  assert.match(data.publication_note, /3 for GPT-6\.2 Sol/);
  assert.match(data.publication_note, /17 for GPT-6\.1 Terra/);
  assert.match(data.publication_note, /28 for GPT-6 Luna/);
  assert.match(data.publication_note, /14 for Claude Haiku 5\.5/);
  assert.match(data.publication_note, /Twenty-four Haiku first tries ended at the 64,000-token output cap/);
  const rawAttempts = await readFile(new URL('gpt-6.2-sol/attempts.jsonl', runs), 'utf8');
  assert.doesNotMatch(rawAttempts, /sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|OPENAI_API_KEY/);
  const responses = rawAttempts.trim().split('\n').map(JSON.parse)
    .flatMap(attempt => [attempt.response, attempt.repair?.response].filter(Boolean));
  assert.equal(responses.length, 107);
  assert.ok(responses.every(response => response.finish_reason === 'stop'));
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, 76.50256330828586);
  near(model.after_repair.overall_score, 78.23680860887244);
  near(model.cost, 9.463684);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 3);
  assert.ok(model.cost_attempts.every(attempt => !attempt.output_tokens || attempt.output_tokens < 128000));
  const displayed = boardModels(data).find(model => model.key === 'openai/gpt-6.2-sol');
  assert.equal(displayed.score.toFixed(1), '78.2');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
  near(displayed.toolCosts.jmeter, 4.185608);
  near(displayed.toolCosts.k6, 1.428906);
  near(displayed.toolCosts.locust, 1.717984);
  near(displayed.toolCosts.gatling, 2.131186);
});

test('Terra Core keeps high reasoning, complete coverage, cache-aware cost and output headroom', async () => {
  const model = data.models.find(model => model.model === 'gpt-6.1-terra');
  assert.equal(model.provider, 'openai');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.output_cap, 128000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.harness_commit, 'de0702215439bb0b7bf3f9dbe5ef383c57f18b82');
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.spend_cap_usd === 12.5 && source.harness_commit === model.harness_commit));
  assert.match(model.cost_note, /17 repair responses/);
  assert.match(model.cost_note, /cache reads use documented discounted rates/);
  assert.match(model.cost_note, /cache writes use documented 1\.25x total rates/);
  assert.match(model.cost_note, /45529880940/);
  assert.match(data.publication_note, /GPT-6\.1 Terra was published from Actions run/);
  const rawAttempts = await readFile(new URL('gpt-6.1-terra/attempts.jsonl', runs), 'utf8');
  assert.doesNotMatch(rawAttempts, /sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|OPENAI_API_KEY/);
  const responses = rawAttempts.trim().split('\n').map(JSON.parse)
    .flatMap(attempt => [attempt.response, attempt.repair?.response].filter(Boolean));
  assert.equal(responses.length, 121);
  assert.ok(responses.every(response => response.finish_reason === 'stop'));
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, 74.92892098595574);
  near(model.after_repair.overall_score, 84.92248315531599);
  near(model.cost, 4.20945735);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 17);
  assert.ok(model.cost_attempts.every(attempt => !attempt.output_tokens || attempt.output_tokens < 128000));
  assert.equal(model.tokens.cache_read, 53962);
  assert.equal(model.tokens.cache_write, 394841);
  const displayed = boardModels(data).find(model => model.key === 'openai/gpt-6.1-terra');
  assert.equal(displayed.score.toFixed(1), '84.9');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
  near(displayed.toolCosts.jmeter, 1.5476762);
  near(displayed.toolCosts.k6, .70154525);
  near(displayed.toolCosts.locust, .7782115);
  near(displayed.toolCosts.gatling, 1.1820244);
});

test('Luna Core keeps high reasoning, complete coverage, cache-aware cost and output headroom', async () => {
  const model = data.models.find(model => model.model === 'gpt-6-luna');
  assert.equal(model.provider, 'openai');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.output_cap, 128000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.harness_commit, 'eb48304999a6052305cb32ce6688523143afb4b5');
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.spend_cap_usd === 2.5 && source.harness_commit === model.harness_commit));
  assert.match(model.cost_note, /28 repair responses/);
  assert.match(model.cost_note, /cache reads use documented discounted rates/);
  assert.match(model.cost_note, /cache writes use documented 1\.25x total rates/);
  assert.match(model.cost_note, /45530991214/);
  assert.match(data.publication_note, /GPT-6 Luna was published from Actions run/);
  const rawAttempts = await readFile(new URL('gpt-6-luna/attempts.jsonl', runs), 'utf8');
  assert.doesNotMatch(rawAttempts, /sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|OPENAI_API_KEY/);
  const responses = rawAttempts.trim().split('\n').map(JSON.parse)
    .flatMap(attempt => [attempt.response, attempt.repair?.response].filter(Boolean));
  assert.equal(responses.length, 132);
  assert.ok(responses.every(response => response.finish_reason === 'stop'));
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, 54.871302019528684);
  near(model.after_repair.overall_score, 68.82272260190327);
  near(model.cost, 1.04221925);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 28);
  assert.ok(model.cost_attempts.every(attempt => !attempt.output_tokens || attempt.output_tokens < 128000));
  assert.equal(model.tokens.cache_read, 78610);
  assert.equal(model.tokens.cache_write, 511014);
  const displayed = boardModels(data).find(model => model.key === 'openai/gpt-6-luna');
  assert.equal(displayed.score.toFixed(1), '68.8');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
  near(displayed.toolCosts.jmeter, .383011925);
  near(displayed.toolCosts.k6, .161833235);
  near(displayed.toolCosts.locust, .249177315);
  near(displayed.toolCosts.gatling, .248196775);
});


test('Gemini 4 Neon Core discloses mixed-wave provenance, blocked refusal and cost accounting', async () => {
  const model = data.models.find(model => model.model === 'gemini-4-neon');
  assert.equal(model.provider, 'gemini');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.output_cap, 65536);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.harness_commit, 'f41ba293ae1c70121a2a16ad7a44f2ed75938a85');
  assert.equal(model.derived_from.length, 12);
  assert.deepEqual(model.derived_from.reduce((counts, source) => {
    counts[source.harness_commit] = (counts[source.harness_commit] ?? 0) + 1;
    return counts;
  }, {}), {
    'f41ba293ae1c70121a2a16ad7a44f2ed75938a85': 4,
    '41254217a577b37e067af6912361e3fa3d2c6586': 4,
    '0f9fff71653c3d35645d10a165cea9ea0ea135fb': 3,
    '428a7100e05383c1457959f656305c14b108980a': 1,
  });
  assert.match(model.cost_note, /33 returned usage-bearing responses/);
  assert.match(model.cost_note, /zero-score provider refusal/);
  assert.match(model.cost_note, /45920115434/);
  assert.match(data.publication_note, /33 for Gemini 4 Neon/);
  assert.match(data.publication_note, /Footnote: mx-c01-html-csrf-jmeter/);
  assert.match(data.publication_note, /45935870372 is not used/);
  const rows = (await readFile(new URL('gemini-4-neon/attempts.jsonl', runs), 'utf8'))
    .trim().split('\n').map(JSON.parse);
  assert.doesNotMatch(JSON.stringify(rows), /sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|GEMINI_API_KEY/);
  const initial = rows.map(attempt => attempt.response);
  const triggered = rows.filter(attempt => attempt.repair?.triggered);
  const repairs = triggered.map(attempt => attempt.repair.response).filter(Boolean);
  assert.equal(initial.length, 104);
  assert.equal(triggered.length, 37);
  assert.equal(repairs.length, 33);
  assert.equal(triggered.filter(attempt => !attempt.repair.response).length, 4);
  assert.ok(triggered.filter(attempt => !attempt.repair.response)
    .every(attempt => attempt.repair.error.includes('HTTP 429')));
  assert.equal(initial.filter(response => response.finish_reason === 'STOP').length, 70);
  assert.equal(initial.filter(response => response.finish_reason === '').length, 32);
  assert.equal(initial.filter(response => response.finish_reason === 'MAX_TOKENS').length, 1);
  assert.equal(initial.filter(response => response.finish_reason === 'BLOCKED_OTHER').length, 1);
  assert.equal(repairs.filter(response => response.finish_reason === 'STOP').length, 21);
  assert.equal(repairs.filter(response => response.finish_reason === '').length, 11);
  assert.equal(repairs.filter(response => response.finish_reason === 'BLOCKED_OTHER').length, 1);
  assert.equal(rows.find(attempt => attempt.task_id === 'mx-c01-html-csrf-jmeter').grade.score, 0);
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, 58.600205328021254);
  near(model.after_repair.overall_score, 72.2179825882825);
  near(model.cost, 36.728001);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 33);
  assert.equal(model.cost_attempts.filter(attempt => attempt.output_tokens === 65536).length, 1);
  assert.equal(model.tokens.cache_read, 0);
  assert.equal(model.tokens.cache_write, 0);
  const displayed = boardModels(data).find(model => model.key === 'gemini/gemini-4-neon');
  assert.equal(displayed.score.toFixed(1), '72.2');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
  near(displayed.toolCosts.jmeter, 10.834818);
  near(displayed.toolCosts.k6, 7.740528000000001);
  near(displayed.toolCosts.locust, 6.858561);
  near(displayed.toolCosts.gatling, 11.294093999999998);
});

test('Inkling 2 Small Core keeps complete uniform provenance, repair scoring and output-cap accounting', async () => {
  const model = data.models.find(model => model.model === 'thinkingmachines/Inkling-2-Small');
  assert.equal(model.provider, 'thinkingmachines');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.output_cap, 128000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.harness_commit, '8560bb6289ff6bf2f923f4593e9091b345de1c5d');
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.harness_commit === model.harness_commit && source.spend_cap_usd == null));
  assert.match(model.cost_note, /21 repair responses/);
  assert.match(model.cost_note, /21 first tries and 5 repair responses ended at the output cap/);
  assert.match(model.cost_note, /45733052845/);
  assert.match(data.publication_note, /21 for Inkling 2 Small/);
  const rows = (await readFile(new URL('inkling-2-small/attempts.jsonl', runs), 'utf8'))
    .trim().split('\n').map(JSON.parse);
  assert.doesNotMatch(JSON.stringify(rows), /sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|TINKER_API_KEY/);
  const initial = rows.map(attempt => attempt.response);
  const repairs = rows.filter(attempt => attempt.repair?.triggered).map(attempt => attempt.repair.response);
  assert.equal(initial.length, 104);
  assert.equal(repairs.length, 21);
  assert.equal(initial.filter(response => response.finish_reason === 'length').length, 21);
  assert.equal(initial.filter(response => response.finish_reason === 'stop').length, 83);
  assert.equal(repairs.filter(response => response.finish_reason === 'length').length, 5);
  assert.equal(repairs.filter(response => response.finish_reason === 'stop').length, 16);
  assert.ok(initial.concat(repairs).filter(response => response.finish_reason === 'length')
    .every(response => response.text === ''));
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, 46.09237679534985);
  near(model.after_repair.overall_score, 49.01639208143699);
  near(model.cost, 5.93598756);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 21);
  assert.equal(model.cost_attempts.filter(attempt => attempt.output_tokens === 128000).length, 26);
  assert.equal(model.tokens.cache_read, 143232);
  assert.equal(model.tokens.cache_write, 0);
  const displayed = boardModels(data).find(model => model.key === 'thinkingmachines/thinkingmachines/Inkling-2-Small');
  assert.equal(displayed.score.toFixed(1), '49.0');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
  near(displayed.toolCosts.jmeter, 2.17562228);
  near(displayed.toolCosts.k6, .8248155);
  near(displayed.toolCosts.locust, 1.25536728);
  near(displayed.toolCosts.gatling, 1.6801825);
});

test('Inkling 2 Mini Core discloses supplemented mixed-harness provenance and cap-dominated result', async () => {
  const model = data.models.find(model => model.model === 'thinkingmachines/Inkling-2-Mini');
  assert.equal(model.provider, 'thinkingmachines');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.output_cap, 128000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.harness_commit, '2383300f46e2929e43ee0740b349a675eb1bfcae');
  assert.equal(model.derived_from.length, 5);
  assert.deepEqual(model.derived_from.map(source => source.spend_cap_usd), [2.5, 2.5, 2.5, 2.5, 1]);
  assert.deepEqual(model.derived_from.map(source => source.harness_commit), [
    '2383300f46e2929e43ee0740b349a675eb1bfcae',
    '2383300f46e2929e43ee0740b349a675eb1bfcae',
    '2383300f46e2929e43ee0740b349a675eb1bfcae',
    '2383300f46e2929e43ee0740b349a675eb1bfcae',
    '8d9fe2996ec19d8a34ff19b3133f65e46b8cbb87',
  ]);
  assert.match(model.cost_note, /supplemental local execution/);
  assert.match(model.cost_note, /86 first tries ended at the output cap/);
  assert.match(model.cost_note, /45673110614/);
  assert.match(data.publication_note, /supplemental local execution/);
  assert.match(data.publication_note, /0 for Inkling 2 Mini/);
  const rawAttempts = await readFile(new URL('inkling-2-mini/attempts.jsonl', runs), 'utf8');
  assert.doesNotMatch(rawAttempts, /sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|TINKER_API_KEY/);
  const responses = rawAttempts.trim().split('\n').map(JSON.parse)
    .flatMap(attempt => [attempt.response, attempt.repair?.response].filter(Boolean));
  assert.equal(responses.length, 104);
  assert.equal(responses.filter(response => response.finish_reason === 'length').length, 86);
  assert.equal(responses.filter(response => response.finish_reason === 'stop').length, 18);
  assert.ok(responses.filter(response => response.finish_reason === 'length')
    .every(response => response.text === ''));
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, 15.778785789240843);
  near(model.after_repair.overall_score, 15.778785789240843);
  near(model.cost, 5.61112928);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 0);
  assert.equal(model.cost_attempts.filter(attempt => attempt.output_tokens === 128000).length, 86);
  assert.equal(model.tokens.cache_read, 90112);
  assert.equal(model.tokens.cache_write, 0);
  const displayed = boardModels(data).find(model => model.key === 'thinkingmachines/thinkingmachines/Inkling-2-Mini');
  assert.equal(displayed.score.toFixed(1), '15.8');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
  near(displayed.toolCosts.jmeter, 1.43202972);
  near(displayed.toolCosts.k6, 1.42604715);
  near(displayed.toolCosts.locust, 1.31451327);
  near(displayed.toolCosts.gatling, 1.43853914);
});

test('published Core data reproduces from stored runs apart from documented annotations', async () => {
  const rebuilt = await buildData((await readdir(runs)).map(model => new URL(`${model}/`, runs).pathname), { packId: 'p95-core' });
  const { budget_file_sha256, publication_note, models, ...rest } = data;
  const unannotated = { ...rest, models: models.map(({ budget_file_sha256, ...model }) => model) };
  assert.deepEqual(unannotated, rebuilt);
});
