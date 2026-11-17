export const score = (value) => value.toFixed(1);
export const money = (value) => `$${value.toFixed(4)}`;
export const name = (model) => model === 'deepseek-v4-pro' ? 'DeepSeek V4 Pro'
  : model === 'deepseek-flash' ? 'DeepSeek Flash' : model;
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
