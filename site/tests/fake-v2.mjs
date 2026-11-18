export function fakeV2() {
  const weights = { repair: 25, migration: 15, correlation: 25, workload: 20, analysis: 15 };
  const tasks = [
    ['v2-r01-extractor-scope', 'repair', 'jmeter'], ['v2-m01-basic-flow', 'migration', 'k6'],
    ['v2-c01-html-csrf', 'correlation', 'jmeter'], ['v2-w01-steady-starts', 'workload', 'jmeter'],
    ['v2-a01-jtl-endpoints', 'analysis', 'jmeter'],
  ];
  return { test_fixture: true, pack_id: 'p95-v2.0.0', weights,
    models: [['anthropic', 'claude-opus-5-5', 1], ['gemini', 'gemini-4-carbon', .8],
      ['qwen', 'Qwen-Ambassador/Qwen3.7-Max', .6]].map(([provider, model, factor]) => {
      const category_scores = Object.fromEntries(Object.keys(weights).map((category, index) =>
        [category, [60, 80, 100, 40, 50][index] * factor]));
      const quality_score = Object.entries(weights).reduce((sum, [category, weight]) =>
        sum + category_scores[category] * weight / 100, 0);
      const efficiency_score = quality_score * .8;
      return { provider, model, pack_id: 'p95-v2.0.0', repeats: 1, category_scores,
        quality_score, efficiency_score, overall_score: .95 * quality_score + .05 * efficiency_score,
        cost: .01 / factor, tokens: { input: 500, output: 400 }, latency: 10 / factor,
        run_date: '2000-01-01T00:00:00Z', harness_commit: 'TEST-FIXTURE-NOT-REAL', cost_attempts: [],
        per_task_results: tasks.map(([task_id, category, tool]) => ({ task_id, category, tool,
          score: category_scores[category] / 100, passed: category_scores[category] === 100,
          repeat: 0, output_tokens: 80, latency_seconds: 2 / factor,
          detail: 'TEST FIXTURE ONLY — not a benchmark result' })),
      };
    }),
  };
}
