import { selectModelVersions, taskResult } from './scores.mjs';

const labels = new Map([['none', 'General'], ['jmeter', 'JMeter'], ['k6', 'k6'],
  ['gatling', 'Gatling'], ['locust', 'Locust']]);
const toolOrder = [...labels.keys()];

export function scenarioGrading({ grader, params = {} }) {
  if (grader === 'v2_exec' || grader === 'v2_analysis') return params.rubric.map(checkpoint =>
    `${checkpoint.id}: ${checkpoint.points} points — ${checkpoint.description}; prerequisites: ${(checkpoint.requires ?? []).join(', ') || 'none'}`).join('. ');
  if (grader === 'numeric') return `Numeric answer: ${params.answer}, with absolute tolerance ${params.tolerance ?? 0}. Reply with only the bare finite number; markdown or prose fails.`;
  if (grader === 'choice') return `Exact choice: ${params.answer}. Reply with only the bare letter; markdown or prose fails. Letter case and surrounding whitespace are ignored.`;
  if (grader !== 'jmeter_exec' && grader !== 'k6_exec') return `Custom grader: ${grader}. See the task definition for its checks.`;
  const checks = [grader === 'jmeter_exec'
    ? 'Return one complete <jmeterTestPlan> XML block before execution. Executed against the local demo shop. Requires exit code 0, a nonempty sample log and all samples successful.'
    : 'Return the script in one js/javascript code block before execution. Executed against the local demo shop. Requires exit code 0, nonempty HTTP metrics, expected responses and positive HTTP statuses below 400.'];
  const completions = params.completions ?? 1;
  checks.push(`Exactly ${completions} completed checkout${completions === 1 ? '' : 's'}.`);
  if (grader === 'jmeter_exec') {
    if ((params.min_checkout_threads ?? 1) > 1) checks.push(`At least ${params.min_checkout_threads} distinct checkout threads.`);
    checks.push('Thread and iteration counts are not otherwise checked.');
  } else {
    if ((params.min_checkout_vus ?? 1) > 1) checks.push(`At least ${params.min_checkout_vus} distinct checkout VUs.`);
    if (params.expected_checkout_vus != null) checks.push(`Exactly ${params.expected_checkout_vus} checkout VU${params.expected_checkout_vus === 1 ? '' : 's'}.`);
    if ((params.min_checkout_iterations ?? 1) > 1) checks.push(`At least ${params.min_checkout_iterations} distinct checkout iterations.`);
  }
  checks.push(`Timeout: ${params.timeout ?? 60} seconds.`);
  return checks.join(' ');
}

export function buildScenarios(tasks, models, showEarlier = false) {
  const latest = selectModelVersions(models, showEarlier);
  return tasks.map(task => {
    const tool = task.tool || 'none';
    const results = latest.map(model => taskResult(model, task.id));
    const passed = results.filter(result => result?.passed).length;
    const attempted = results.filter(result => result !== null).length;
    return { id: task.id, title: task.title ?? task.id, category: task.category,
      tool, toolLabel: labels.get(tool) ?? tool, summary: task.summary ?? (task.grader.startsWith('v2_') ? task.prompt.split('\n')[0] : task.prompt),
      grading: scenarioGrading(task), passed, attempted, total: latest.length,
      notRun: latest.length - attempted,
      passLabel: latest.length ? task.grader.startsWith('v2_')
        ? attempted ? `${(100 * results.reduce((sum, result) => sum + (result?.score ?? 0), 0) / attempted).toFixed(1)}% mean quality · ${attempted}/${latest.length} models run`
          : `Not run on v2 · 0/${latest.length} models run`
        : `${passed}/${latest.length} models pass` : 'No published latest models' };
  }).sort((a, b) => a.category.localeCompare(b.category, 'en') || a.id.localeCompare(b.id, 'en'));
}

export function scenarioFilters(scenarios) {
  const tools = [...new Map(scenarios.map(scenario => [scenario.tool, scenario.toolLabel]))];
  const order = tool => toolOrder.includes(tool) ? toolOrder.indexOf(tool) : toolOrder.length;
  tools.sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b, 'en'));
  return [{ tool: 'all', label: 'All' }, ...tools.map(([tool, label]) => ({ tool, label }))];
}
