import { attemptFraction, name } from './scores.mjs';
import { providerName } from './provider-names.mjs';

export const palette = ['#d4ff3a', '#7b8cff', '#ff7a3d', '#e86fb0', '#4fd1e8', '#3fbf8f', '#b48cf2', '#f2c14e'];

export const categoryLabels = {
  repair: 'Repair', migration: 'Migration', correlation: 'Correlation',
  workload: 'Workload', analysis: 'Analysis',
};

export function boardModels(data) {
  return data.models.filter(m => Number.isFinite(m.overall_score))
    .map(m => ({
      key: `${m.provider}/${m.model}`, name: name(m.model), provider: m.provider,
      providerLabel: providerName(m.provider),
      score: m.overall_score, quality: m.quality_score, efficiency: m.efficiency_score,
      afterRepair: m.after_repair ? {
        score: m.after_repair.overall_score, quality: m.after_repair.quality_score,
        efficiency: m.after_repair.efficiency_score, categories: m.after_repair.category_scores,
      } : null,
      repairRounds: m.repair_rounds ?? null, reasoningMode: m.reasoning_mode ?? null,
      derivedFrom: m.derived_from ?? null,
      cost: m.cost, outputTokens: m.tokens?.output ?? null, latency: m.latency,
      categories: m.category_scores, tasks: m.per_task_results,
    }))
    .sort((a, b) => b.score - a.score || a.cost - b.cost || a.name.localeCompare(b.name))
    .map((m, i) => ({ ...m, color: palette[i % palette.length] }));
}

export function boardTasks(data) {
  const order = Object.keys(data.weights);
  const seen = new Map();
  for (const m of data.models) for (const t of m.per_task_results) seen.set(scenarioId(t), { ...t, task_id: scenarioId(t), tool: 'all' });
  return [...seen.values()]
    .map(t => ({ id: t.task_id, category: t.category, tool: t.tool, label: taskLabel(t.task_id) }))
    .sort((a, b) => order.indexOf(a.category) - order.indexOf(b.category) || a.id.localeCompare(b.id));
}

export const hasRepairScores = models => models.some(m =>
  m.repairRounds > 0 && Number.isFinite(m.afterRepair?.score));

export const taskLabel = id => id.replace(/^(?:v2|mx)-[a-z]\d+-/i, '').replaceAll('-', ' ');
export const taskCode = id => id.match(/^(?:v2|mx)-([a-z]\d+)/i)?.[1].toUpperCase() ?? id;

export const TOOLS = ['jmeter', 'k6', 'locust', 'gatling'];
const scenarioId = t => t.scenario ?? t.task_id;
export function taskScore(model, taskId, tool = null, scoreMode = 'first') {
  const values = model.tasks.filter(t => scenarioId(t) === taskId && (!tool || tool === 'all' || t.tool === tool))
    .map(attempt => attemptFraction(attempt, scoreMode)).filter(v => v !== null);
  return values.length ? Math.round(100 * values.reduce((s, v) => s + v, 0) / values.length) : null;
}

const costTicks = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100];

export function scatter(models, { width = 880, height = 280, left = 44, right = 24, top = 20, bottom = 36 } = {}) {
  const priced = models.filter(m => Number.isFinite(m.cost) && m.cost > 0);
  if (!priced.length) return { points: [], xTicks: [], yTicks: [], width, height };
  const costs = priced.map(m => m.cost), scores = priced.map(m => m.score);
  const lo = Math.log10(Math.min(...costs) / 1.8), hi = Math.log10(Math.max(...costs) * 1.8);
  let yMin = Math.floor(Math.min(...scores) / 10) * 10, yMax = Math.ceil(Math.max(...scores) / 10) * 10;
  if (yMax - yMin < 20) { yMin = Math.max(0, yMin - 10); yMax = Math.min(100, Math.max(yMax, yMin + 20)); }
  const x = c => left + (Math.log10(c) - lo) / (hi - lo) * (width - left - right);
  const y = s => top + (yMax - s) / (yMax - yMin) * (height - top - bottom);
  const yTicks = [];
  for (let s = yMin; s <= yMax; s += 10) yTicks.push({ value: s, y: y(s) });
  return {
    width, height, left, right, top, bottom,
    points: priced.map((m, rank) => ({ ...m, rank, x: x(m.cost), y: y(m.score) })),
    xTicks: costTicks.filter(c => Math.log10(c) >= lo && Math.log10(c) <= hi)
      .map(c => ({ value: c, x: x(c), label: `$${c < 1 ? c.toFixed(c < 0.1 ? 2 : 1) : c}` })),
    yTicks,
  };
}

export const tone = value => Number.isFinite(value) ? Math.round(Math.max(0, Math.min(100, value))) : null;

export const formatCost = value => !Number.isFinite(value) ? '—' : `$${value.toFixed(2)}`;
export const formatTokens = value => !Number.isFinite(value) ? '—'
  : value >= 1000 ? `${Math.round(value / 1000)}k` : String(value);
export const formatMinutes = seconds => !Number.isFinite(seconds) ? '—' : `${(seconds / 60).toFixed(1)} min`;

export const categoryBlurbs = {
  repair: 'Fix a broken script so it runs the journey correctly.',
  migration: 'Convert an existing test plan to the target tool without changing its behaviour.',
  correlation: 'Capture dynamic values such as tokens, IDs and ETags, and reuse them correctly.',
  workload: 'Generate an exact load shape: arrival rates, phases, pacing and isolated pools.',
  analysis: 'Read result files and report the requested numbers as JSON.',
};

export const toolLabel = tool => tool === 'k6' ? 'k6' : tool === 'jmeter' ? 'JMeter'
  : tool === 'locust' ? 'Locust' : tool === 'gatling' ? 'Gatling' : tool;

export const taskSummary = prompt => String(prompt ?? '').split('\n')[0].trim();

export function taskDetails(definitions, tasks) {
  const byId = new Map();
  for (const d of definitions) {
    const key = d.scenario ?? d.id;
    if (!byId.has(key) || d.tool === 'k6') byId.set(key, d);
  }
  return tasks.map(t => {
    const d = byId.get(t.id) ?? {};
    const summary = taskSummary(d.prompt);
    return { ...t, summary: t.category === 'migration'
      ? summary.replace(/\bto k6\b/, 'to the target tool').replace(/\b(?:a )?JMX\b/, 'a source').replace(/\bJMeter\b/, 'source').replace(/\bk6 /, '')
      : summary,
      checks: (d.params?.rubric ?? []).map(item => item.id) };
  });
}

export function toolScores(model, tasks, scoreMode = 'first') {
  const tools = [...new Set(tasks.map(t => t.tool))].sort();
  return Object.fromEntries(tools.map(tool => {
    const values = tasks.filter(t => t.tool === tool)
      .map(t => taskScore(model, t.id, null, scoreMode)).filter(v => v !== null);
    return [tool, values.length ? values.reduce((s, v) => s + v, 0) / values.length : null];
  }));
}
