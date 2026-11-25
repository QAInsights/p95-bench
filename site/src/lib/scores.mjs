export const name = (model) => model === 'deepseek-v4-pro' ? 'DeepSeek V4 Pro'
  : model === 'deepseek-flash' ? 'DeepSeek Flash'
  : model === 'claude-sonnet-5-5' ? 'Claude Sonnet 5.5'
  : model === 'claude-opus-5-5' ? 'Claude Opus 5.5'
  : model === 'thinkingmachines/Inkling' ? 'Inkling'
  : model === 'thinkingmachines/Inkling-Small' ? 'Inkling-Small'
  : model === 'thinkingmachines/Inkling-2-Small' ? 'Inkling 2 Small'
  : model === 'Qwen-Ambassador/Qwen4-Max' ? 'Qwen4 Max'
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
  : model === 'gpt-6.2-astra' ? 'GPT-6.2 Astra'
  : model === 'gpt-6.1-terra' ? 'GPT-6.1 Terra'
  : model === 'gpt-6-luna' ? 'GPT-6 Luna' : model;

export const modelSlug = (model) => model.replace(/^thinkingmachines\//, '').toLowerCase()
  .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

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

export const bestWithin = (models, budget) => [...models].filter((m) => Number.isFinite(m.cost)
  && Number.isFinite(m.overall_score) && m.cost <= budget)
  .sort((a, b) => b.overall_score - a.overall_score || a.cost - b.cost)[0] ?? null;

export function attemptFraction(attempt, scoreMode = 'first') {
  const score = scoreMode === 'repair' ? attempt?.after_repair_score ?? attempt?.score : attempt?.score;
  if (Number.isFinite(score) && score >= 0 && score <= 1) return score;
  return typeof attempt?.passed === 'boolean' ? Number(attempt.passed) : null;
}

export function toolScore(model, tool, scoreMode = 'first') {
  const values = model.per_task_results.filter(t => t.tool === tool)
    .map(attempt => attemptFraction(attempt, scoreMode)).filter(value => value !== null);
  return values.length ? 100 * values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

export function toolScoreLabel(model, tool, scoreMode = 'first') {
  const value = toolScore(model, tool, scoreMode);
  return value === null ? 'Not run' : value.toFixed(1);
}

export function taskResult(model, taskId, scoreMode = 'first') {
  const attempts = model.per_task_results.filter((task) => task.task_id === taskId);
  if (!attempts.length) return null;
  const values = attempts.map(attempt => attemptFraction(attempt, scoreMode)).filter(value => value !== null);
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
