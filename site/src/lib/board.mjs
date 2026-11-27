import { attemptFraction, name } from './scores.mjs';
import { providerName } from './provider-names.mjs';

export const palette = ['#d4ff3a', '#7b8cff', '#ff7a3d', '#e86fb0', '#4fd1e8', '#3fbf8f', '#b48cf2', '#f2c14e'];

export const categoryLabels = {
  repair: 'Repair', migration: 'Migration', correlation: 'Correlation',
  workload: 'Workload', analysis: 'Analysis',
};

export function boardModels(data) {
  return data.models.filter(m => Number.isFinite(m.overall_score))
    .map(m => {
      const final = Number.isFinite(m.after_repair?.overall_score) ? m.after_repair : m;
      const taskTools = new Map(m.per_task_results.map(task => [task.task_id, task.tool]));
      const toolCosts = {};
      for (const attempt of m.cost_attempts ?? []) {
        const tool = taskTools.get(attempt.task_id);
        if (tool) toolCosts[tool] = (toolCosts[tool] ?? 0) + attempt.cost_usd;
      }
      return {
        key: `${m.provider}/${m.model}`, name: name(m.model), provider: m.provider,
        providerLabel: providerName(m.provider),
        score: final.overall_score, categories: final.category_scores,
        reasoningMode: m.reasoning_mode ?? null, derivedFrom: m.derived_from ?? null,
        cost: m.cost, toolCosts, tasks: m.per_task_results,
      };
    })
    .sort((a, b) => b.score - a.score || a.cost - b.cost || a.name.localeCompare(b.name))
    .map((m, i) => ({ ...m, color: palette[i % palette.length] }));
}

export function reasoningLabel(mode) {
  if (!mode) return null;
  if (mode === 'thinking-high') return 'High reasoning';
  return mode.replaceAll('-', ' ');
}

export function sharedReasoningLabel(models) {
  const labels = [...new Set(models.map(model => reasoningLabel(model.reasoningMode)).filter(Boolean))];
  return labels.length === 1 ? labels[0] : null;
}

export function boardTasks(data) {
  const order = Object.keys(data.weights);
  const seen = new Map();
  for (const m of data.models) for (const t of m.per_task_results) seen.set(scenarioId(t), { ...t, task_id: scenarioId(t), tool: 'all' });
  return [...seen.values()]
    .map(t => ({ id: t.task_id, category: t.category, tool: t.tool, label: taskLabel(t.task_id) }))
    .sort((a, b) => order.indexOf(a.category) - order.indexOf(b.category) || a.id.localeCompare(b.id));
}

export const taskLabel = id => id.replace(/^(?:v2|mx)-[a-z]\d+-/i, '').replaceAll('-', ' ');
export const taskCode = id => id.match(/^(?:v2|mx)-([a-z]\d+)/i)?.[1].toUpperCase() ?? id;

export const TOOLS = ['jmeter', 'k6', 'locust', 'gatling'];
const scenarioId = t => t.scenario ?? t.task_id;
export function taskScore(model, taskId, tool = null) {
  const values = model.tasks.filter(t => scenarioId(t) === taskId && (!tool || tool === 'all' || t.tool === tool))
    .map(attempt => attemptFraction(attempt, 'repair')).filter(v => v !== null);
  return values.length ? Math.round(100 * values.reduce((s, v) => s + v, 0) / values.length) : null;
}

const costTicks = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100];

function layoutScatterLabels(points, { width, height, top, bottom }) {
  const labels = points.map(p => ({
    key: p.key,
    x: p.x + 11,
    y: p.y + 4,
    width: Math.max(44, p.name.length * 7.2),
  }));
  const rects = label => ({ left: label.x, right: label.x + label.width,
    top: label.y - 11, bottom: label.y + 4 });
  const minY = top + 10, maxY = height - bottom - 2;
  for (let iteration = 0; iteration < 80; iteration++) {
    let moved = false;
    labels.sort((a, b) => a.y - b.y);
    for (let i = 0; i < labels.length; i++) {
      for (let j = i + 1; j < labels.length; j++) {
        const a = rects(labels[i]), b = rects(labels[j]);
        const horizontal = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const vertical = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (horizontal <= 0 || vertical <= 0) continue;
        const push = vertical / 2 + 0.5;
        labels[i].y -= push;
        labels[j].y += push;
        moved = true;
      }
    }
    for (const label of labels) label.y = Math.max(minY, Math.min(maxY, label.y));
    if (!moved) break;
  }

  const overlapping = () => labels.some((first, i) => labels.slice(i + 1).some(second => {
    const a = rects(first), b = rects(second);
    return Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0
      && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0;
  }));
  if (overlapping()) spreadLabelGroups(labels, minY, maxY);
  return new Map(labels.map(label => [label.key, label]));
}

function spreadLabelGroups(labels, minY, maxY) {
  const horizontalOverlap = (first, second) => Math.min(first.x + first.width, second.x + second.width)
    - Math.max(first.x, second.x);
  const unseen = new Set(labels);
  const groups = [];
  for (const label of labels) {
    if (!unseen.has(label)) continue;
    const group = [];
    const queue = [label];
    unseen.delete(label);
    while (queue.length) {
      const current = queue.pop();
      group.push(current);
      for (const candidate of [...unseen]) {
        if (horizontalOverlap(current, candidate) > 0) {
          unseen.delete(candidate);
          queue.push(candidate);
        }
      }
    }
    groups.push(group);
  }

  for (const group of groups) {
    group.sort((a, b) => a.y - b.y);
    const spacing = 15.5;
    const needed = (group.length - 1) * spacing;
    const center = group.reduce((sum, label) => sum + label.y, 0) / group.length;
    const start = Math.max(minY, Math.min(maxY - needed, center - needed / 2));
    group.forEach((label, index) => { label.y = start + index * spacing; });
  }
}

export function scatter(models, { width = 960, height = 340, left = 44, right = 150, top = 22, bottom = 42 } = {}) {
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
  const points = priced.map((m, rank) => ({ ...m, rank, x: x(m.cost), y: y(m.score) }));
  const labels = layoutScatterLabels(points, { width, height, top, bottom });
  return {
    width, height, left, right, top, bottom,
    points: points.map(p => {
      const label = labels.get(p.key);
      return { ...p, labelX: label.x, labelY: label.y, labelWidth: label.width,
        labelDy: label.y - (p.y + 4) };
    }),
    xTicks: costTicks.filter(c => Math.log10(c) >= lo && Math.log10(c) <= hi)
      .map(c => ({ value: c, x: x(c), label: `$${c < 1 ? c.toFixed(c < 0.1 ? 2 : 1) : c}` })),
    yTicks,
  };
}

export const tone = value => Number.isFinite(value) ? Math.round(Math.max(0, Math.min(100, value))) : null;

export const formatCost = value => !Number.isFinite(value) ? '—' : `$${value.toFixed(2)}`;

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

export function toolScores(model, tasks) {
  const tools = [...new Set(tasks.map(t => t.tool))].sort();
  return Object.fromEntries(tools.map(tool => {
    const values = tasks.filter(t => t.tool === tool)
      .map(t => taskScore(model, t.id, null)).filter(v => v !== null);
    return [tool, values.length ? values.reduce((s, v) => s + v, 0) / values.length : null];
  }));
}
