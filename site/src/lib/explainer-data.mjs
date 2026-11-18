import { scoreAttempts } from './scores.mjs';

export function executionShapes(tasks) {
  return tasks.filter(task => task.grader === 'k6_exec').map(task => {
    const vus = task.params.expected_checkout_vus ?? task.params.min_checkout_vus ?? 1;
    return { id: task.id, title: task.title, vus,
      iterations: task.params.min_checkout_iterations ?? task.params.completions / vus,
      completions: task.params.completions };
  });
}

export function setTaskState(attempts, task, state) {
  if (!['pass', 'fail', 'not_run'].includes(state)) throw new Error(`Unknown task state: ${state}`);
  const other = attempts.filter(attempt => attempt.task_id !== task.id);
  if (state === 'not_run') return other;
  const current = attempts.filter(attempt => attempt.task_id === task.id);
  return [...other, ...(current.length ? current : [{ task_id: task.id, category: task.category }])
    .map(attempt => ({ ...attempt, passed: state === 'pass' }))];
}

export function simulatorRows(tasks, attempts, weights) {
  const summary = scoreAttempts(attempts, weights);
  return { ...summary, rows: tasks.map(task => {
    const own = attempts.filter(attempt => attempt.task_id === task.id);
    const category = attempts.filter(attempt => attempt.category === task.category);
    const passed = own.filter(attempt => attempt.passed).length;
    const perAttempt = category.length && summary.measured_weight
      ? 100 * weights[task.category] / summary.measured_weight / category.length : 0;
    return { id: task.id, state: !own.length ? 'not_run'
      : passed === own.length ? 'pass' : !passed ? 'fail' : 'mixed',
      points: passed * perAttempt, maximum: own.length * perAttempt };
  }) };
}

export function attemptCost(attempt) {
  const cached = Math.max(0, Math.min(attempt.cached_tokens ?? 0, attempt.input_tokens));
  const parts = [
    { label: 'Uncached input', tokens: attempt.input_tokens - cached, rate: attempt.price_per_million.input },
    { label: 'Cached input', tokens: cached, rate: attempt.price_per_million.cached },
    { label: 'Output', tokens: attempt.output_tokens, rate: attempt.price_per_million.output },
  ].map(part => ({ ...part, cost: part.tokens * part.rate / 1_000_000 }));
  return { parts, total: parts.reduce((sum, part) => sum + part.cost, 0) };
}
