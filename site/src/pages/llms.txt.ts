import data from '../data/leaderboard-p95-core.json';
import { boardModels, boardTasks, TOOLS } from '../lib/board.mjs';
import { REPO_URL, SITE_URL } from '../lib/seo.mjs';

export function GET() {
  const models = boardModels(data);
  const tasks = boardTasks(data);
  const taskTotal = tasks.length * TOOLS.length;
  const rows = models.map((m, i) => {
    const avgSec = m.latency ? `${Math.round(m.latency / m.taskCount)}s/task` : 'n/a';
    const avgCost = `$${(m.cost / m.taskCount).toFixed(3)}/task`;
    return `${i + 1}. **${m.name}** (${m.providerLabel}) — Score: ${m.score.toFixed(1)}/100 | Quality: ${m.quality.toFixed(1)}/100 | Speed: ${avgSec} | Cost: $${m.cost.toFixed(2)} (${avgCost})`;
  });

  const body = [
    '# P95 Bench',
    '',
    `> Open-source benchmark measuring how well AI language models write, fix, and analyse performance tests in JMeter, k6, Locust, and Gatling (${tasks.length} scenarios × ${TOOLS.length} tools = ${taskTotal} executable tasks).`,
    '',
    '## Key Pages',
    `- [Leaderboard & 3D Pareto Chamber](${SITE_URL}/): Live model rankings, per-tool scores, and Speed/Cost/Quality tradeoffs.`,
    `- [Public Task Catalog](${SITE_URL}/tasks): All ${tasks.length} public performance engineering scenarios and grading rubrics.`,
    `- [Source & Raw Runs on GitHub](${REPO_URL}): Complete harness, graders, and reproducible run logs.`,
    '',
    '## Scoring & Metrics',
    '- **Overall Score**: 95% weighted checkpoint quality (Repair 25%, Correlation 25%, Workload 20%, Migration 15%, Analysis 15%) + 5% efficiency (output tokens and provider API latency).',
    '- **Speed**: Average accepted-response provider API latency per task (excluding retry backoff and container execution time).',
    '- **Cost**: List-price USD estimate from provider-reported token usage across all 104 tasks plus recorded repair turns.',
    '',
    `## Current Core Rankings (${data.pack_id}, ${models.length} models)`,
    ...rows,
    '',
  ].join('\n');

  return new Response(body, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
}
