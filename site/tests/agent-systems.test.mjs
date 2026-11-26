import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { transform } from '@astrojs/compiler-rs';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import { agentComparison, agentComparisons, loadAgentSystem, parseAgentSystem, validateAgentSystem } from '../src/lib/agent-systems.mjs';
import { boardModels, boardTasks, scatter, taskScore } from '../src/lib/board.mjs';
import { costFrontier, toolScore } from '../src/lib/scores.mjs';
import { buildData } from '../scripts/build-data.mjs';

const board = JSON.parse(await readFile(new URL('../src/data/leaderboard-p95-core.json', import.meta.url)));
const entry = await loadAgentSystem(board);
const terraEntry = await loadAgentSystem(board, new URL('../../results/agent-systems/p95-core/feather-wand-401-pr278-terra/', import.meta.url));
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-8, `${a} != ${b}`);

test('agent system has complete JMeter-only coverage, reconciled usage and public provenance', () => {
  assert.equal(entry.metadata.system, 'Feather Wand v4.0.1-RC1 (JMeter GUI Agent mode)');
  assert.equal(entry.metadata.model, 'claude-sonnet-5-5');
  assert.equal(entry.metadata.reasoning_mode, 'thinking-high');
  assert.match(entry.metadata.grader_commit, /^[a-f0-9]{40}$/);
  assert.deepEqual(entry.metadata.pricing_basis, { currency: 'USD', unit: 'million tokens',
    cache_write_ttl: '5 minutes', input: 2, cached: 0.1, cache_write: 2.5, output: 10 });
  assert.equal(entry.attempts.length, 26);
  assert.equal(entry.attempts.filter(a => a.repair.triggered).length, 8);
  assert.equal(entry.summary.turns, 581);
  assert.equal(entry.summary.output_tokens, 570217);
  assert.equal(entry.summary.input_tokens, 19246443);
  assert.equal(entry.summary.cached_tokens, 18261093);
  assert.equal(entry.summary.cache_write_tokens, 984120);
  assert.equal(entry.summary.fresh_input_tokens, 1230);
  near(entry.summary.estimated_cost_usd, 9.9910393);
  near(entry.summary.tool_scores.jmeter.first_attempt, 65.97222222211539);
  near(entry.summary.tool_scores.jmeter.after_repair, 66.04166666653846);
  assert.match(entry.metadata.cost_note, /\$3\.67/);
  assert.match(entry.metadata.repair_policy, /historical Core trigger/);
  assert.doesNotMatch(JSON.stringify(entry), /\/home\/|\/Users\/|sk-ant-[\w-]{20,}/);
  assert.ok(entry.attempts.every(a => a.grade.status === 'graded' && a.response.source_sha256));
});

test('agent comparison uses the current direct JMeter board data without mutating any ranking surface', () => {
  const snapshot = () => ({ board: structuredClone(board), models: boardModels(board),
    frontier: costFrontier(board.models), plot: scatter(boardModels(board)),
    heatmap: boardTasks(board).map(t => boardModels(board).map(m => taskScore(m, t.id, 'jmeter'))) });
  const before = snapshot();
  const rows = agentComparison(entry, board);
  const direct = board.models.find(m => m.model === 'claude-sonnet-5-5');
  near(rows[1].first, toolScore(direct, 'jmeter'));
  near(rows[1].after, toolScore(direct, 'jmeter', 'repair'));
  near(rows[1].first, 56.73076923076923);
  near(rows[1].after, 65.76923076923077);
  near(rows[1].cost, 10.735006);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].turns, 581);
  assert.deepEqual(snapshot(), before);
  assert.deepEqual(boardModels(board).map(m => m.name),
    ['GPT-6.1 Terra', 'Claude Sonnet 5.5', 'GPT-6.2 Sol', 'Qwen4 Max', 'GPT-6 Luna', 'DeepSeek Flash', 'Claude Haiku 5.5', 'Inkling 2 Small', 'DeepSeek V4 Pro', 'Inkling 2 Mini']);
  assert.deepEqual(boardModels(board).map(m => m.score),
    [84.92248315531599, 84.54197789514849, 78.23680860887244, 77.31725653059549, 68.82272260190327, 64.44586406147408, 60.89215401307633, 49.01639208143699, 48.24482914686975, 15.778785789240843]);
});

test('Terra agent system has complete JMeter-only coverage and honest cost provenance', () => {
  assert.equal(terraEntry.metadata.system, 'Feather Wand v4.0.1 + jmeter-ai PR #278 (JMeter GUI Agent mode)');
  assert.equal(terraEntry.metadata.model, 'gpt-6.1-terra');
  assert.equal(terraEntry.metadata.reasoning_mode, 'thinking-high');
  assert.match(terraEntry.metadata.grader_commit, /^[a-f0-9]{40}$/);
  assert.deepEqual(terraEntry.metadata.pricing_basis, { currency: 'USD', unit: 'million tokens',
    cache_write_ttl: '30 minutes', input: 1, cached: 0.05, cache_write: 1.25, output: 5 });
  assert.match(terraEntry.metadata.execution, /Destructive action confirmations were disabled/);
  assert.equal(terraEntry.metadata.excluded_spend_usd, null);
  assert.equal(terraEntry.attempts.length, 26);
  assert.equal(terraEntry.attempts.filter(a => a.repair.triggered).length, 0);
  assert.equal(terraEntry.summary.turns, 3263);
  assert.equal(terraEntry.summary.output_tokens, 312473);
  assert.equal(terraEntry.summary.input_tokens, 76801711);
  assert.equal(terraEntry.summary.cached_tokens, 75962653);
  assert.equal(terraEntry.summary.cache_write_tokens, 782299);
  assert.equal(terraEntry.summary.fresh_input_tokens, 56759);
  near(terraEntry.summary.estimated_cost_usd, 6.3951304);
  near(terraEntry.summary.tool_scores.jmeter.first_attempt, 69.03846153846153);
  near(terraEntry.summary.tool_scores.jmeter.after_repair, 69.03846153846153);
  assert.match(terraEntry.metadata.cost_note, /unknown spend/);
  assert.doesNotMatch(JSON.stringify(terraEntry), /\/home\/|\/Users\/|sk-[\w-]{20,}/);
  assert.ok(terraEntry.attempts.every(a => a.grade.status === 'graded' && a.response.source_sha256));
});

test('Terra comparison uses the direct Terra JMeter board data', () => {
  const rows = agentComparison(terraEntry, board);
  assert.equal(rows[0].name, 'Feather Wand 4.0.1+patch + Terra');
  assert.equal(rows[1].name, 'Terra direct');
  near(rows[0].first, 69.03846153846153);
  near(rows[0].after, 69.03846153846153);
  near(rows[1].first, 75.38461538461537);
  near(rows[1].after, 80.38461538461539);
  near(rows[1].cost, 1.5476762);
  assert.equal(rows[1].output, 277943);
  assert.equal(rows[1].turns, 28);
});

test('agent system rejects empty, private, mixed-manifest, mismatched-score and invalid usage data', () => {
  const changes = [
    e => { e.attempts = []; },
    e => { e.attempts[0].task_id = e.attempts[1].task_id; },
    e => { e.metadata.visibility = 'private'; },
    e => { e.metadata.rank_eligible = true; },
    e => { e.metadata.pack_digest = 'different'; },
    e => { e.manifest.repeats = 2; },
    e => { e.summary.per_task_results[0].score = 0.25; },
    e => { e.attempts[0].grade.score = NaN; },
    e => { e.summary.turns++; },
    e => { e.attempts[0].response.turns = -1; },
    e => { e.summary.estimated_cost_usd++; },
    e => { e.summary.tool_scores.jmeter.first_attempt++; },
  ];
  for (const change of changes) {
    const invalid = structuredClone(entry);
    change(invalid);
    assert.throws(() => validateAgentSystem(invalid, board));
  }
  assert.throws(() => agentComparison(entry, { ...board, models: [] }), /comparison missing/);
});

test('JMeter-only system cannot be passed to the ranked Core model exporter', async () => {
  const run = new URL('../../results/agent-systems/p95-core/feather-wand-rc1-sonnet-5-5/', import.meta.url);
  await assert.rejects(buildData([run.pathname], { packId: 'p95-core' }), /Incomplete or mismatched v2 run/);
});

test('build-time import refuses missing or ambiguous system files', () => {
  assert.throws(() => parseAgentSystem({}, board), /Missing or duplicate/);
  assert.throws(() => parseAgentSystem({ 'a/metadata.json': '{}', 'b/metadata.json': '{}' }, board),
    /Missing or duplicate/);
});

test('Core renders the real agent component separately, with both rows and escaped notes', async () => {
  const source = await readFile(new URL('../src/components/AgentSystems.astro', import.meta.url), 'utf8');
  const { code } = await transform(source, { filename: 'AgentSystems.astro',
    internalURL: import.meta.resolve('@astrojs/runtime/compiler-runtime'),
    resultScopedSlot: true, resolvePath: specifier => specifier });
  const { default: Component } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);
  const container = await AstroContainer.create();
  const html = await container.renderToString(Component, { props: {
    rows: agentComparisons([terraEntry, entry], board),
    notes: [
      { label: 'Feather Wand 4.0.1+patch + Terra', note: terraEntry.metadata.cost_note + '<script>unsafe()</script>', url: 'https://example.com/terra' },
      { label: 'Feather Wand RC1 + Sonnet 5.5', note: entry.metadata.cost_note, url: 'https://example.com/sonnet' },
    ],
  } });
  assert.match(html, /id="agent-systems"/);
  for (const text of ['Agent systems (JMeter only)', 'Not ranked', 'Feather Wand RC1 + Sonnet 5.5',
    'Sonnet 5.5 direct', 'Feather Wand 4.0.1+patch + Terra', 'Terra direct', 'First attempt', 'After repair', 'Output tokens', 'Turns', '$6.40', '$1.55', '$9.99', '$10.74', '312,473', '570,217']) {
    assert.ok(html.includes(text), text);
  }
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>|\u2014/);
  assert.equal((html.match(/>66\.0</g) ?? []).length, 2);
  assert.match(html, /aria-label="Run data and provenance for Feather Wand 4\.0\.1\+patch \+ Terra"/);
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  assert.match(page, /<AgentSystems rows=\{agentRows\} notes=\{agentNotes\}/);
  assert.ok(page.indexOf('<AgentSystems') > page.indexOf('plot.points.map'));
  assert.doesNotMatch(page, /data\.models\.push|models\.push/);
  const css = await readFile(new URL('../src/styles/board.css', import.meta.url), 'utf8');
  const styles = css.slice(css.indexOf('.agent-systems'), css.indexOf('.rank {'));
  assert.ok([...styles.matchAll(/font-size:\s*([\d.]+)px/g)].every(match => Number(match[1]) >= 12));
  assert.doesNotMatch(styles, /animation:|transition:/);
});
