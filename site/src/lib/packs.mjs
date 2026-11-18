export const packIds = { v1: 'p95-v1', v2: 'p95-v2.0.0' };
export const packLabels = { v1: 'v1 · 15 tasks', v2: 'v2 · 50 tasks' };

export function resolvePack(requested, packs) {
  if (Object.hasOwn(packs, requested)) return requested;
  return packs.v2 ? 'v2' : 'v1';
}

export function packModels(selected, peers) {
  const identity = model => JSON.stringify([model.provider, model.model]);
  const runs = new Map(selected.models.map(model => [identity(model), model]));
  const known = new Map(peers.flatMap(pack => pack.models).map(model => [identity(model), model]));
  return [...known].map(([id, model]) => runs.get(id) ?? {
    provider: model.provider, model: model.model, pack_id: selected.pack_id ?? 'p95-v1',
    not_run: true, overall_score: null, quality_score: null, efficiency_score: null,
    category_scores: {}, per_task_results: [], cost_attempts: [], cost: null,
    tokens: { input: null, output: null }, latency: null, run_date: null,
  });
}

export function makePackCatalog(v1, v2, tasks, attempts) {
  const datasets = { v1 };
  if (v2?.pack_id === packIds.v2 && Array.isArray(v2.models) && v2.models.some(model =>
    Number.isFinite(model.overall_score) && Number.isFinite(model.quality_score))) datasets.v2 = v2;
  return Object.fromEntries(Object.entries(datasets).map(([key, data]) => [key, {
    ...data, pack_id: packIds[key], tasks: tasks[key],
    models: packModels(data, Object.values(datasets)),
    attempts: key === 'v1' ? attempts.map(attempt => ({
      provider: attempt.provider, model: attempt.model, task_id: attempt.task_id,
      grade: { passed: attempt.grade.passed }, response: {
        output_tokens: attempt.response.output_tokens, latency_seconds: attempt.response.latency_seconds },
    })) : data.models.flatMap(model => model.per_task_results.map(task => ({
      provider: model.provider, model: model.model, task_id: task.task_id,
      grade: { score: task.score, passed: task.passed },
      response: { output_tokens: task.output_tokens, latency_seconds: task.latency_seconds },
    }))),
  }]));
}

export function creditGradient(value) {
  if (!Number.isFinite(value)) return null;
  const percent = Math.max(0, Math.min(100, value * 100));
  return `linear-gradient(135deg, #c4416a, color-mix(in srgb, #d4ff3a ${percent}%, #c4416a))`;
}
