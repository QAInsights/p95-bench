import { name } from './scores.mjs';
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
      cost: m.cost, outputTokens: m.tokens?.output ?? null, latency: m.latency,
      categories: m.category_scores, tasks: m.per_task_results,
    }))
    .sort((a, b) => b.score - a.score || a.cost - b.cost || a.name.localeCompare(b.name))
    .map((m, i) => ({ ...m, color: palette[i % palette.length] }));
}

export function boardTasks(data) {
  const order = Object.keys(data.weights);
  const seen = new Map();
  for (const m of data.models) for (const t of m.per_task_results) seen.set(t.task_id, t);
  return [...seen.values()]
    .map(t => ({ id: t.task_id, category: t.category, tool: t.tool, label: taskLabel(t.task_id) }))
    .sort((a, b) => order.indexOf(a.category) - order.indexOf(b.category) || a.id.localeCompare(b.id));
}

export const taskLabel = id => id.replace(/^v2-[a-z]\d+-/i, '').replaceAll('-', ' ');
export const taskCode = id => id.match(/^v2-([a-z]\d+)/i)?.[1].toUpperCase() ?? id;

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
    points: priced.map(m => ({ ...m, x: x(m.cost), y: y(m.score) })),
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
