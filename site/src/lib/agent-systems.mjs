import { readFile } from 'node:fs/promises';
import { snapshotDigest } from '../../scripts/build-data.mjs';
import { toolScore } from './scores.mjs';

const directory = new URL('../../../results/agent-systems/p95-core/feather-wand-rc1-sonnet-5-5/', import.meta.url);
const usageFields = ['input_tokens', 'fresh_input_tokens', 'cached_tokens', 'cache_write_tokens',
  'output_tokens', 'reasoning_tokens', 'turns'];
const near = (a, b) => Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) < 1e-8;

export function validateAgentSystem(entry, board) {
  const { metadata, summary, manifest, attempts } = entry;
  const { digest, ...contents } = manifest;
  if (board.pack_id !== 'p95-core' || digest !== snapshotDigest(contents)
    || [metadata, summary, manifest].some(value => value.pack_id !== board.pack_id)
    || [metadata, summary].some(value => value.pack_digest !== board.pack_digest)
    || digest !== board.pack_digest) throw new Error('Agent system Core manifest mismatch');
  if (metadata.entry_type !== 'agent_system' || metadata.visibility !== 'public'
    || metadata.rank_eligible !== false || summary.rank_eligible !== false
    || summary.complete !== false || summary.complete_tool_coverage !== true
    || JSON.stringify(metadata.tools) !== '["jmeter"]'
    || JSON.stringify(summary.tools) !== '["jmeter"]') throw new Error('Invalid agent system scope');
  const inventory = manifest.tasks.filter(task => task.id.endsWith('-jmeter'));
  const ids = new Set(inventory.map(task => task.id));
  if (!attempts.length || attempts.length !== ids.size || summary.attempts !== ids.size
    || summary.expected_attempts !== ids.size || summary.per_task_results.length !== ids.size
    || new Set(attempts.map(a => a.task_id)).size !== ids.size
    || new Set(summary.per_task_results.map(a => a.task_id)).size !== ids.size
    || attempts.some(a => !ids.has(a.task_id) || a.repeat !== 0 || a.tool !== 'jmeter'
      || a.pack_digest !== digest || a.pack_id !== board.pack_id
      || a.model !== metadata.model || a.provider !== metadata.provider)) {
    throw new Error('Incomplete agent system task coverage');
  }
  const records = attempts.flatMap(a => a.repair?.triggered ? [a, a.repair] : [a]);
  if (records.some(a => a.grade?.status !== 'graded' || !Number.isFinite(a.grade.score)
    || a.grade.score < 0 || a.grade.score > 1 || !Number.isFinite(a.cost_usd) || a.cost_usd < 0
    || usageFields.some(key => !Number.isInteger(a.response?.[key]) || a.response[key] < 0)
    || a.response.turns < 1)) throw new Error('Invalid agent system grade or usage');
  for (const attempt of attempts) {
    const result = summary.per_task_results.find(a => a.task_id === attempt.task_id);
    if (!result || result.tool !== 'jmeter' || !near(result.score, attempt.grade.score)
      || !near(result.after_repair_score, (attempt.repair?.grade ?? attempt.grade).score)) {
      throw new Error('Agent system summary grade mismatch');
    }
  }
  for (const key of usageFields) {
    const total = records.reduce((sum, record) => sum + record.response[key], 0);
    if (summary[key] !== total || metadata.usage[key] !== total) {
      throw new Error('Agent system usage mismatch');
    }
  }
  if (!near(summary.estimated_cost_usd, records.reduce((sum, a) => sum + a.cost_usd, 0))
    || !near(summary.tool_scores.jmeter.first_attempt, toolScore(summary, 'jmeter'))
    || !near(summary.tool_scores.jmeter.after_repair, toolScore(summary, 'jmeter', 'repair'))) {
    throw new Error('Agent system score or cost mismatch');
  }
  return entry;
}

export async function loadAgentSystem(board, source = directory) {
  const names = ['metadata.json', 'summary.json', 'pack.json', 'attempts.jsonl'];
  const files = await Promise.all(names.map(async name =>
    [name, await readFile(new URL(name, source), 'utf8')]));
  return parseAgentSystem(Object.fromEntries(files), board);
}

export function parseAgentSystem(sources, board) {
  const entries = parseAgentSystems(sources, board);
  if (entries.length !== 1) throw new Error('Missing or duplicate agent system files');
  return entries[0];
}

export function parseAgentSystems(sources, board) {
  const groups = new Map();
  for (const [key, value] of Object.entries(sources)) {
    const directory = key.split('/').slice(0, -1).join('/') || '.';
    if (!groups.has(directory)) groups.set(directory, {});
    groups.get(directory)[key] = value;
  }
  return [...groups.entries()].map(([directory, group]) => {
    const raw = name => {
      const matches = Object.entries(group).filter(([key]) => key.split('/').at(-1) === name);
      if (matches.length !== 1 || typeof matches[0][1] !== 'string') {
        throw new Error('Missing or duplicate agent system file: ' + name);
      }
      return matches[0][1];
    };
    const entry = validateAgentSystem({ metadata: JSON.parse(raw('metadata.json')),
      summary: JSON.parse(raw('summary.json')), manifest: JSON.parse(raw('pack.json')),
      attempts: raw('attempts.jsonl').trim().split('\n').filter(Boolean).map(JSON.parse) }, board);
    entry.directory = directory.split('/').at(-1);
    return entry;
  });
}

export function agentDisplay(entry) {
  if (entry.metadata.provider === 'anthropic' && entry.metadata.model === 'claude-sonnet-5-5') {
    return { systemName: 'Feather Wand RC1 + Sonnet 5.5',
      systemDetail: 'v4.0.1-RC1 GUI Agent mode · high thinking',
      directName: 'Sonnet 5.5 direct', directDetail: 'Provider API · high thinking' };
  }
  if (entry.metadata.provider === 'openai' && entry.metadata.model === 'gpt-6.1-terra') {
    return { systemName: 'Feather Wand latest + Terra',
      systemDetail: 'main 0745c8a + Responses transport GUI Agent mode · high reasoning',
      directName: 'Terra direct', directDetail: 'Provider API · high reasoning' };
  }
  return { systemName: entry.metadata.system, systemDetail: entry.metadata.reasoning_mode,
    directName: `${entry.metadata.model} direct`, directDetail: 'Provider API' };
}

export function agentComparison(entry, board) {
  validateAgentSystem(entry, board);
  const direct = board.models.find(m => m.model === entry.metadata.model
    && m.provider === entry.metadata.provider);
  if (!direct) throw new Error('Agent system direct-model comparison missing');
  const ids = new Set(direct.per_task_results.filter(a => a.tool === 'jmeter').map(a => a.task_id));
  const costs = direct.cost_attempts.filter(a => ids.has(a.task_id));
  const display = agentDisplay(entry);
  return [
    { name: display.systemName, detail: display.systemDetail,
      first: toolScore(entry.summary, 'jmeter'), after: toolScore(entry.summary, 'jmeter', 'repair'),
      cost: entry.summary.estimated_cost_usd, output: entry.summary.output_tokens,
      turns: entry.summary.turns, system: true },
    { name: display.directName, detail: display.directDetail,
      first: toolScore(direct, 'jmeter'), after: toolScore(direct, 'jmeter', 'repair'),
      cost: costs.reduce((sum, a) => sum + a.cost_usd, 0),
      output: costs.reduce((sum, a) => sum + a.output_tokens, 0), turns: costs.length, system: false },
  ];
}

export function agentComparisons(entries, board) {
  return entries.flatMap(entry => agentComparison(entry, board));
}
