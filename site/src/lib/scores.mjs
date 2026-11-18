import registry from '../data/model-registry.json' with { type: 'json' };

export function selectModelVersions(models, showEarlier = false, versions = registry) {
  const familyKey = (model) => Object.hasOwn(versions, model.model)
    ? `registered:${versions[model.model].family}` : `unknown:${model.model}`;
  const latest = new Map();
  for (const model of models) {
    const key = familyKey(model), order = versions[model.model]?.release_order ?? 0;
    if (!latest.has(key) || order > latest.get(key).order) latest.set(key, { model: model.model, order });
  }
  return models.map((model) => {
    const entry = versions[model.model];
    const newer = latest.get(familyKey(model)).model;
    return { ...model, family: entry?.family ?? model.model,
      release_order: entry?.release_order ?? 0, discontinued: entry?.discontinued === true,
      version_status: newer === model.model ? 'Latest' : 'Earlier version',
      newer_model: newer === model.model ? null : newer };
  }).filter((model) => showEarlier || model.newer_model === null)
    .sort((a, b) => Number(Boolean(a.not_run)) - Number(Boolean(b.not_run))
      || b.overall_score - a.overall_score || a.cost - b.cost || a.model.localeCompare(b.model));
}

export const score = (value) => value.toFixed(1);
export const money = (value) => `$${value.toFixed(4)}`;
export const name = (model) => model === 'deepseek-v4-pro' ? 'DeepSeek V4 Pro'
  : model === 'deepseek-flash' ? 'DeepSeek Flash'
  : model === 'claude-sonnet-5-5' ? 'Claude Sonnet 5.5'
  : model === 'claude-opus-5-5' ? 'Claude Opus 5.5'
  : model === 'thinkingmachines/Inkling' ? 'Inkling'
  : model === 'thinkingmachines/Inkling-Small' ? 'Inkling-Small'
  : model === 'thinkingmachines/Inkling-2-Small' ? 'Inkling 2 Small'
  : model === 'Qwen-Ambassador/Qwen3.8-Max' ? 'Qwen3.8 Max'
  : model === 'Qwen-Ambassador/Qwen3.7-Max' ? 'Qwen3.7 Max'
  : model === 'Qwen-Ambassador/Qwen3.8-Flash-Next' ? 'Qwen3.8 Flash Next'
  : model === 'gemini-4-carbon' ? 'Gemini 4 Carbon'
  : model === 'gemini-4-argon' ? 'Gemini 4 Argon'
  : model === 'gemini-4-hydrogen' ? 'Gemini 4 Hydrogen'
  : model === 'gemini-3.8-flash' ? 'Gemini 3.8 Flash'
  : model === 'muse-wonder-1.0' ? 'Muse Wonder 1.0'
  : model === 'muse-spark-1.3' ? 'Muse Spark 1.3'
  : model === 'muse-spark-1.2' ? 'Muse Spark 1.2'
  : model === 'muse-ember-1.0' ? 'Muse Ember 1.0'
  : model === 'gpt-6.2-sol' ? 'GPT-6.2 Sol'
  : model === 'gpt-6.2-astra' ? 'GPT-6.2 Astra' : model;
export const modelSlug = (model) => model.replace(/^thinkingmachines\//, '').toLowerCase()
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
export const shortTaskLabel = id => id.match(/^v2-([a-z]\d+)(?:-|$)/i)?.[1].toUpperCase()
  ?? id.replaceAll('-', '').slice(0, 4).toUpperCase();
export function taskCoverage(model, tasks) {
  const expected = new Set(tasks.map(task => task.id));
  const run = new Set(model.per_task_results.map(task => task.task_id)
    .filter(id => expected.has(id))).size;
  const total = expected.size;
  return { run, total, partial: run < total, label: `${run}/${total} tasks run` };
}
export function costFrontier(models) {
  const sorted = models.filter(model => Number.isFinite(model.cost) && Number.isFinite(model.overall_score))
    .sort((a, b) => a.cost - b.cost || b.overall_score - a.overall_score);
  return sorted.filter((model, index) => index === 0
    || model.overall_score > Math.max(...sorted.slice(0, index).map((m) => m.overall_score)));
}
export function contributions(model, weights) {
  const present = Object.keys(model.category_scores);
  const total = present.reduce((sum, category) => sum + weights[category], 0);
  return Object.entries(weights).filter(([category]) => present.includes(category))
    .map(([category, weight]) => ({ category, score: model.category_scores[category],
      points: model.category_scores[category] * weight / total }));
}
export function scoreAttempts(attempts, weights) {
  const groups = new Map();
  for (const attempt of attempts) {
    if (attempt.passed == null) continue;
    if (!Object.hasOwn(weights, attempt.category)) throw new Error(`Unknown category: ${attempt.category}`);
    if (!groups.has(attempt.category)) groups.set(attempt.category, []);
    groups.get(attempt.category).push(attempt);
  }
  const category_scores = Object.fromEntries([...groups].map(([category, values]) =>
    [category, 100 * values.filter(value => value.passed).length / values.length]));
  const measured_weight = [...groups.keys()].reduce((sum, category) => sum + weights[category], 0);
  const overall_score = contributions({ category_scores }, weights).reduce((sum, row) => sum + row.points, 0);
  return { overall_score, category_scores, measured_weight };
}
export const bestWithin = (models, budget) => [...models].filter((m) => Number.isFinite(m.cost)
  && Number.isFinite(m.overall_score) && m.cost <= budget)
  .sort((a, b) => b.overall_score - a.overall_score || a.cost - b.cost)[0] ?? null;
export function toolScore(model, tool) {
  const values = model.per_task_results.filter(t => t.tool === tool).map(attemptFraction).filter(value => value !== null);
  return values.length ? 100 * values.reduce((sum, value) => sum + value, 0) / values.length : null;
}
export function attemptFraction(attempt) {
  if (Number.isFinite(attempt?.score) && attempt.score >= 0 && attempt.score <= 1) return attempt.score;
  return typeof attempt?.passed === 'boolean' ? Number(attempt.passed) : null;
}
export function toolScoreLabel(model, tool) {
  const value = toolScore(model, tool);
  return value === null ? 'Not run' : score(value);
}
export function taskResult(model, taskId) {
  const attempts = model.per_task_results.filter((task) => task.task_id === taskId);
  if (!attempts.length) return null;
  const values = attempts.map(attemptFraction).filter(value => value !== null);
  if (!values.length) return null;
  const passed = values.filter(value => value === 1).length;
  if (attempts.some(attempt => Number.isFinite(attempt.score))) {
    const score = values.reduce((sum, value) => sum + value, 0) / values.length;
    return { passed: passed === values.length, score, label: `${(score * 100).toFixed(1)}% credit`,
      detail: attempts.map(attempt => attempt.detail).join('\n') };
  }
  return {
    passed: passed === attempts.length,
    label: attempts.length === 1 ? (passed ? 'Pass' : 'Fail') : `${passed}/${attempts.length} pass`,
    detail: attempts.length === 1 ? attempts[0].detail : attempts.map((attempt, index) =>
      `Attempt ${index + 1}: ${attempt.passed ? 'Pass' : 'Fail'} · ${attempt.detail}`).join('\n'),
  };
}

export function replayLayout(models) {
  const taskIds = [], columns = new Map();
  const rows = models.map(model => {
    const occurrences = new Map(), row = [];
    model.per_task_results.forEach((task, index) => {
      const occurrence = occurrences.get(task.task_id) ?? 0;
      occurrences.set(task.task_id, occurrence + 1);
      if (!columns.has(task.task_id)) columns.set(task.task_id, []);
      const slots = columns.get(task.task_id);
      if (slots[occurrence] === undefined) {
        slots[occurrence] = taskIds.length;
        taskIds.push(task.task_id);
      }
      row[slots[occurrence]] = index;
    });
    return row;
  });
  return { taskIds, rows: rows.map(row => taskIds.map((_, column) => row[column] ?? null)) };
}

const replayTool = (tool) => tool?.trim().toLowerCase() || 'none';
const toolLabels = { none: 'General', jmeter: 'JMeter', k6: 'k6', gatling: 'Gatling', locust: 'Locust' };
const toolOrder = ['none', 'jmeter', 'k6', 'gatling', 'locust'];

export function replayToolLayout(models) {
  const { taskIds, rows } = replayLayout(models);
  const toolsByTask = new Map(models.flatMap(model => model.per_task_results
    .map(task => [task.task_id, replayTool(task.tool)])));
  const tools = [...new Set(taskIds.map(id => toolsByTask.get(id)))].sort((a, b) => {
    const order = tool => toolOrder.includes(tool) ? toolOrder.indexOf(tool) : toolOrder.length;
    return order(a) - order(b) || a.localeCompare(b, 'en');
  });
  const columns = [], groups = [];
  for (const tool of tools) {
    const indices = taskIds.flatMap((id, column) => toolsByTask.get(id) === tool ? [column] : []);
    groups.push({ tool, label: Object.hasOwn(toolLabels, tool) ? toolLabels[tool] : tool, count: indices.length,
      offset: columns.length, column: columns.length + groups.length + 1 });
    columns.push(...indices);
  }
  return { taskIds: columns.map(column => taskIds[column]),
    rows: rows.map(row => columns.map(column => row[column])), groups };
}

export function replayToolCounts(model, groups) {
  return groups.map(({ tool, label, count }) => {
    const tasks = model.per_task_results.filter(task => replayTool(task.tool) === tool);
    const passed = tasks.filter(task => task.passed).length;
    return { tool, label, passed, attempts: tasks.length, total: count,
      text: `${label} ${tasks.length ? `${passed}/${count}` : 'Not run'}` };
  });
}

export function replaySampleLabel(task, taskId, toolLabel) {
  return `${task?.task_name ?? taskId} · ${toolLabel} · ${task == null ? 'Not run' : Number.isFinite(task.score) ? `${(task.score * 100).toFixed(1)}% credit` : task.passed ? 'Pass' : 'Fail'}`;
}

export function replayScore(model, landed, weights) {
  const quality = contributions(model, weights).reduce((total, { category, points }) => {
    const tasks = model.per_task_results.map((task, index) => ({ task, index }))
      .filter(({ task }) => task.category === category);
    const sum = tasks.reduce((sum, { task }) => sum + (attemptFraction(task) ?? 0), 0);
    const received = tasks.reduce((sum, { task, index }) => sum + (landed.has(index) ? attemptFraction(task) ?? 0 : 0), 0);
    return total + (sum ? points * received / sum : 0);
  }, 0);
  return model.pack_id === 'p95-v2.0.0' ? (model.quality_score ? quality * model.overall_score / model.quality_score : 0) : quality;
}

export function budgetScale(models) {
  const costs = models.map((model) => model.cost).filter((cost) => Number.isFinite(cost) && cost > 0);
  const min = costs.length ? Math.min(...costs) * .65 : .001;
  const max = costs.length ? Math.max(...costs) * 1.22 : 1;
  const start = Math.log10(min), range = Math.log10(max) - start;
  return {
    min, max,
    toCost: (position) => 10 ** (start + Math.max(0, Math.min(1000, position)) / 1000 * range),
    toPosition: (cost) => cost > 0 ? Math.max(0, Math.min(1000, (Math.log10(cost) - start) / range * 1000)) : 0,
  };
}
