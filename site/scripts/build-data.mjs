import { readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { parseArgs } from 'node:util';
import { parse } from 'yaml';
import { createHash } from 'node:crypto';
import { taskCoverage } from '../src/lib/scores.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const readJSON = async (file) => JSON.parse(await readFile(file, 'utf8'));

export function snapshotDigest(value) {
  const canonical = item => {
    if (item === null) return ['null'];
    if (typeof item === 'boolean') return ['boolean', item];
    if (typeof item === 'number') {
      if (!Number.isFinite(item)) throw new Error('Nonfinite digest number');
      const bytes = Buffer.alloc(8);
      bytes.writeDoubleBE(item);
      return ['number', bytes.toString('hex')];
    }
    if (typeof item === 'string') return ['string', item];
    if (Array.isArray(item)) return ['array', item.map(canonical)];
    return ['object', Object.entries(item)
      .sort(([a], [b]) => Buffer.compare(Buffer.from(a), Buffer.from(b)))
      .map(([key, val]) => [key, canonical(val)])];
  };
  return createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}

export async function loadTaskData({ packId = 'p95-v1', tasksRoot } = {}) {
  const packs = await readJSON(path.join(root, 'src/p95bench/packs.json'));
  if (!Object.hasOwn(packs, packId)) throw new Error('Unknown pack: ' + packId);
  const weights = packs[packId].weights ?? await readJSON(path.join(root, 'src/p95bench/category-weights.json'));
  tasksRoot ??= path.join(root, packId === 'p95-v1' ? 'tasks' : `packs/${packId}/tasks`);
  const taskMap = new Map();
  for (const category of Object.keys(weights)) {
    const folder = path.join(tasksRoot, category);
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

export async function buildData(directories, options = {}) {
  if (!directories.length) throw new Error('Pass at least one run directory');
  const packId = options.packId ?? 'p95-v1';
  const v2 = packId !== 'p95-v1';
  const { weights, tasks } = await loadTaskData(options);
  const taskMap = new Map(tasks.map(task => [task.id, task]));
  const models = [];
  const digests = new Set();
  for (const directory of directories) {
    let metadata = {}, manifest = {};
    for (const file of ['metadata.json', 'pack.json']) {
      try {
        const value = await readJSON(path.join(directory, file));
        if (file === 'pack.json') manifest = value;
        else metadata = value;
      } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    if ([metadata, manifest].some(value => value.visibility === 'private')) {
      throw new Error('Private runs require a separate allowlist exporter');
    }
    if ([metadata, manifest].some(value => (value.pack_id ?? 'p95-v1') !== packId)) {
      throw new Error('Mixed pack input');
    }
    const summary = await readJSON(path.join(directory, 'summary.json'));
    const attempts = (await readFile(path.join(directory, 'attempts.jsonl'), 'utf8'))
      .split('\n').filter((line) => line.trim()).map(JSON.parse);
    if (!attempts.length) throw new Error(`Empty attempts: ${directory}`);
    const first = attempts[0];
    if (attempts.some(a => (a.pack_id ?? 'p95-v1') !== packId)
      || (summary.pack_id ?? 'p95-v1') !== packId) throw new Error('Mixed pack input');
    if (v2) {
      const { digest, ...contents } = manifest;
      if (digest !== snapshotDigest(contents)) throw new Error('Pack snapshot digest mismatch');
      if (summary.complete !== true || !summary.pack_digest
        || attempts.some(a => a.pack_digest !== summary.pack_digest
          || a.grade.schema_version !== 2 || !Number.isFinite(a.grade.score)
          || a.grade.score < 0 || a.grade.score > 1)
        || manifest.digest !== summary.pack_digest
        || metadata.pack_digest !== summary.pack_digest) throw new Error('Incomplete or mismatched v2 run');
      const inventory = manifest.tasks;
      const repeats = manifest.repeats;
      if (!tasks.length || !Array.isArray(inventory) || inventory.length !== tasks.length
        || new Set(inventory.map(t => t.id)).size !== tasks.length
        || inventory.some(t => taskMap.get(t.id)?.category !== t.category)
        || !Number.isInteger(repeats) || repeats < 1 || summary.repeats !== repeats
        || attempts.length !== tasks.length * repeats
        || attempts.some(a => !taskMap.has(a.task_id)
          || a.category !== taskMap.get(a.task_id).category
          || !Number.isInteger(a.repeat) || a.repeat < 0 || a.repeat >= repeats)
        || new Set(attempts.map(a => JSON.stringify([a.task_id, a.repeat]))).size !== attempts.length) {
        throw new Error('Public task inventory or repeat coverage mismatch');
      }
      digests.add(summary.pack_digest);
    }
    if (attempts.some((a) => a.model !== first.model || a.provider !== first.provider)) {
      throw new Error(`Mixed model/provider run: ${directory}`);
    }
    if (attempts.length !== summary.attempts) throw new Error(`Attempt count mismatch: ${directory}`);
    const skipped = await readFile(path.join(directory, 'not-run.jsonl'), 'utf8').catch(error => {
      if (error.code !== 'ENOENT') throw error;
      return '';
    });
    const charged = skipped.split('\n').filter(line => line.trim()).map(JSON.parse)
      .filter(attempt => attempt.response != null);
    if (charged.some(a => a.status !== 'not_run' || a.model !== first.model
      || a.provider !== first.provider || (a.pack_id ?? 'p95-v1') !== packId
      || !taskMap.has(a.task_id) || !Number.isFinite(a.cost_usd) || a.cost_usd < 0
      || ![a.response.input_tokens, a.response.output_tokens].every(n => Number.isFinite(n) && n >= 0))) {
      throw new Error('Invalid charged not-run response');
    }
    const costAttempts = [...attempts, ...charged].map(attempt => ({ task_id: attempt.task_id,
      input_tokens: attempt.response.input_tokens,
      cached_tokens: attempt.response.cached_tokens ?? 0,
      output_tokens: attempt.response.output_tokens,
      price_per_million: attempt.price_per_million, cost_usd: attempt.cost_usd,
      ...(attempt.status === 'not_run' ? { status: 'not_run' } : {}) }));
    if (charged.length && (!Number.isFinite(summary.estimated_cost_usd)
      || Math.abs(costAttempts.reduce((total, a) => total + a.cost_usd, 0)
        - summary.estimated_cost_usd) > 1e-9)) throw new Error('Cost breakdown total mismatch');
    const stamp = path.basename(directory).match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/);
    const runDate = metadata.run_date ?? (stamp
      ? `${stamp[1]}-${stamp[2]}-${stamp[3]}T${stamp[4]}:${stamp[5]}:${stamp[6]}Z` : null);
    models.push({
      provider: first.provider, model: first.model, overall_score: summary.overall_score,
      ...(v2 ? { pack_id: packId, pack_digest: summary.pack_digest,
        quality_score: summary.quality_score, efficiency_score: summary.efficiency_score,
        repeats: summary.repeats } : {}),
      category_scores: summary.category_scores, cost: summary.estimated_cost_usd,
      tokens: { input: summary.input_tokens, output: summary.output_tokens },
      latency: summary.latency_seconds, run_date: runDate,
      harness_commit: metadata.harness_commit ?? first.harness_commit ?? null,
      ...(metadata.cost_note ? { cost_note: metadata.cost_note,
        pricing_source: metadata.pricing_source ?? null } : {}),
      output_cap: metadata.max_completion_tokens ?? metadata.max_tokens ?? metadata.maxOutputTokens ?? null,
      cost_attempts: costAttempts,
      coverage: taskCoverage({ per_task_results: attempts }, tasks),
      per_task_results: attempts.map((a) => {
        const task = taskMap.get(a.task_id);
        if (!task) throw new Error(`Unknown task: ${a.task_id}`);
        return { task_id: a.task_id, category: a.category, tool: task.tool,
          ...(v2 ? { score: a.grade.score, repeat: a.repeat,
            output_tokens: a.response.output_tokens, latency_seconds: a.response.latency_seconds } : {}),
          passed: a.grade.passed, detail: String(a.grade.detail).slice(0, 180) };
      }),
    });
  }
  if (digests.size > 1) throw new Error('Mixed pack manifests or repeat policies');
  if (new Set(models.map((m) => `${m.provider}/${m.model}`)).size !== models.length) {
    throw new Error('Duplicate model runs; select one run per model');
  }
  return { ...(v2 ? { pack_id: packId, pack_digest: [...digests][0] } : {}),
    weights, models: models.sort((a, b) => b.overall_score - a.overall_score
    || a.cost - b.cost || a.model.localeCompare(b.model) || a.provider.localeCompare(b.provider)) };
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const { values, positionals } = parseArgs({ options: { pack: { type: 'string', default: 'p95-v1' },
    tasks: { type: 'string' } }, allowPositionals: true });
  const data = await buildData(positionals, { packId: values.pack, tasksRoot: values.tasks });
  const filename = values.pack === 'p95-v1' ? 'leaderboard.json' : `leaderboard-${values.pack}.json`;
  const target = new URL('../src/data/' + filename, import.meta.url);
  await writeFile(target, JSON.stringify(data, null, 2) + '\n');
  console.log(`Wrote ${data.models.length} real model runs to ${fileURLToPath(target)}`);
}
