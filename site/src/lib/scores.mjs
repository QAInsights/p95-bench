export const score = (value) => value.toFixed(1);
export const money = (value) => `$${value.toFixed(4)}`;
export const name = (model) => model === 'deepseek-v4-pro' ? 'DeepSeek V4 Pro'
  : model === 'deepseek-flash' ? 'DeepSeek Flash'
  : model === 'claude-sonnet-5-5' ? 'Claude Sonnet 5.5'
  : model === 'claude-opus-5-5' ? 'Claude Opus 5.5' : model;
export function costFrontier(models) {
  const sorted = [...models].sort((a, b) => a.cost - b.cost || b.overall_score - a.overall_score);
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
export const bestWithin = (models, budget) => [...models].filter((m) => m.cost <= budget)
  .sort((a, b) => b.overall_score - a.overall_score || a.cost - b.cost)[0] ?? null;
export function toolScore(model, tool) {
  const tasks = model.per_task_results.filter((t) => t.tool === tool);
  return tasks.length ? 100 * tasks.filter((t) => t.passed).length / tasks.length : null;
}
export function toolScoreLabel(model, tool) {
  const value = toolScore(model, tool);
  return value === null ? 'Not run' : score(value);
}
export function taskResult(model, taskId) {
  const attempts = model.per_task_results.filter((task) => task.task_id === taskId);
  if (!attempts.length) return null;
  const passed = attempts.filter((attempt) => attempt.passed).length;
  return {
    passed: passed === attempts.length,
    label: attempts.length === 1 ? (passed ? 'Pass' : 'Fail') : `${passed}/${attempts.length} pass`,
    detail: attempts.length === 1 ? attempts[0].detail : attempts.map((attempt, index) =>
      `Attempt ${index + 1}: ${attempt.passed ? 'Pass' : 'Fail'} · ${attempt.detail}`).join('\n'),
  };
}
