import { readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { parse } from 'yaml';
import { taskCoverage } from '../src/lib/scores.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const readJSON = async (file) => JSON.parse(await readFile(file, 'utf8'));

export async function loadTaskData() {
  const weights = await readJSON(path.join(root, 'src/p95bench/category-weights.json'));
  const taskMap = new Map();
  for (const category of Object.keys(weights)) {
    const folder = path.join(root, 'tasks', category);
    const files = await readdir(folder).catch((error) => {
      if (error.code === 'ENOENT') return [];
      throw error;
    });
    for (const file of files.filter((name) => name.endsWith('.yaml')).sort()) {
      const task = parse(await readFile(path.join(folder, file), 'utf8'));
      if (taskMap.has(task.id)) throw new Error(`Duplicate task: ${task.id}`);
      taskMap.set(task.id, task);
    }
  }
  return { weights, tasks: [...taskMap.values()] };
}

export async function buildData(directories) {
  if (!directories.length) throw new Error('Pass at least one run directory');
  const { weights, tasks } = await loadTaskData();
  const taskMap = new Map(tasks.map(task => [task.id, task]));
  const models = [];
  for (const directory of directories) {
    const summary = await readJSON(path.join(directory, 'summary.json'));
    const attempts = (await readFile(path.join(directory, 'attempts.jsonl'), 'utf8'))
      .split('\n').filter((line) => line.trim()).map(JSON.parse);
    if (!attempts.length) throw new Error(`Empty attempts: ${directory}`);
    const first = attempts[0];
    if (attempts.some((a) => a.model !== first.model || a.provider !== first.provider)) {
      throw new Error(`Mixed model/provider run: ${directory}`);
    }
    if (attempts.length !== summary.attempts) throw new Error(`Attempt count mismatch: ${directory}`);
    let metadata = {};
    try { metadata = await readJSON(path.join(directory, 'metadata.json')); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    const stamp = path.basename(directory).match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/);
    const runDate = metadata.run_date ?? (stamp
      ? `${stamp[1]}-${stamp[2]}-${stamp[3]}T${stamp[4]}:${stamp[5]}:${stamp[6]}Z` : null);
    models.push({
      provider: first.provider, model: first.model, overall_score: summary.overall_score,
      category_scores: summary.category_scores, cost: summary.estimated_cost_usd,
      tokens: { input: summary.input_tokens, output: summary.output_tokens },
      latency: summary.latency_seconds, run_date: runDate,
      harness_commit: metadata.harness_commit ?? first.harness_commit ?? null,
      coverage: taskCoverage({ per_task_results: attempts }, tasks),
      per_task_results: attempts.map((a) => {
        const task = taskMap.get(a.task_id);
        if (!task) throw new Error(`Unknown task: ${a.task_id}`);
        return { task_id: a.task_id, category: a.category, tool: task.tool,
          passed: a.grade.passed, detail: String(a.grade.detail).slice(0, 180) };
      }),
    });
  }
  if (new Set(models.map((m) => `${m.provider}/${m.model}`)).size !== models.length) {
    throw new Error('Duplicate model runs; select one run per model');
  }
  return { weights, models: models.sort((a, b) => b.overall_score - a.overall_score
    || a.cost - b.cost || a.model.localeCompare(b.model) || a.provider.localeCompare(b.provider)) };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const data = await buildData(process.argv.slice(2));
  const target = new URL('../src/data/leaderboard.json', import.meta.url);
  await writeFile(target, JSON.stringify(data, null, 2) + '\n');
  console.log(`Wrote ${data.models.length} real model runs to ${fileURLToPath(target)}`);
}
