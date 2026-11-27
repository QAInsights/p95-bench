export const SITE_URL = (import.meta.env?.PUBLIC_SITE_URL || 'https://p95.qainsights.com').replace(/\/$/, '');
export const REPO_URL = 'https://github.com/QAInsights/p95-bench';

export function canonicalUrl(pathname = '/') {
  const clean = pathname.startsWith('/') ? pathname : `/${pathname}`;
  return `${SITE_URL}${clean}`;
}

export function homeJsonLd({ models, taskTotal, scenarioCount, runDate }) {
  const leader = models[0];
  const modified = runDate ? new Date(runDate).toISOString().slice(0, 10) : undefined;
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'WebSite',
        '@id': `${SITE_URL}/#website`,
        url: `${SITE_URL}/`,
        name: 'P95 Bench',
        description: 'Open-source benchmark measuring how well AI language models write, fix, and analyse performance tests in JMeter, k6, Locust, and Gatling.',
        publisher: { '@type': 'Organization', name: 'QAInsights', url: REPO_URL },
      },
      {
        '@type': 'Dataset',
        '@id': `${SITE_URL}/#dataset`,
        name: 'P95 Bench Core Leaderboard',
        description: `Executable benchmark across ${scenarioCount} scenarios × 4 performance testing tools (${taskTotal} tasks) evaluating ${models.length} language models on quality, cost, and provider API latency.`,
        url: `${SITE_URL}/`,
        sameAs: REPO_URL,
        license: 'https://opensource.org/licenses/MIT',
        dateModified: modified,
        isAccessibleForFree: true,
        keywords: ['LLM benchmark', 'performance testing', 'JMeter', 'k6', 'Locust', 'Gatling', 'AI coding benchmark', 'Pareto frontier'],
        variableMeasured: ['Overall Score (0-100)', 'Quality Score (0-100)', 'List-price USD Cost', 'Average Provider API Latency per Task'],
      },
      {
        '@type': 'ItemList',
        name: 'P95 Bench Core Model Rankings',
        numberOfItems: models.length,
        itemListElement: models.map((m, i) => ({
          '@type': 'ListItem',
          position: i + 1,
          name: `${m.name} (${m.providerLabel}) — ${m.score.toFixed(1)}/100 ($${m.cost.toFixed(2)})`,
        })),
      },
      {
        '@type': 'FAQPage',
        mainEntity: [
          {
            '@type': 'Question',
            name: 'What is P95 Bench?',
            acceptedAnswer: {
              '@type': 'Answer',
              text: `P95 Bench is an open-source benchmark evaluating how well AI language models write, fix, migrate, correlate, and analyse performance tests across JMeter, k6, Locust, and Gatling (${taskTotal} executable tasks in the Core pack).`,
            },
          },
          {
            '@type': 'Question',
            name: 'How are models scored on P95 Bench?',
            acceptedAnswer: {
              '@type': 'Answer',
              text: 'Overall score combines 95% weighted checkpoint quality across repair, migration, correlation, workload, and analysis tasks plus 5% efficiency (output tokens and provider API latency). Each model gets one allowed repair round if a script fails to compile or run.',
            },
          },
          {
            '@type': 'Question',
            name: 'How is speed measured on the 3D Pareto Chamber?',
            acceptedAnswer: {
              '@type': 'Answer',
              text: 'Speed is measured as average accepted-response provider API latency per task across all 104 Core tasks, excluding retry backoff and container execution time.',
            },
          },
          ...(leader ? [{
            '@type': 'Question',
            name: 'Which model currently leads P95 Bench Core?',
            acceptedAnswer: {
              '@type': 'Answer',
              text: `${leader.name} (${leader.providerLabel}) currently ranks #1 on P95 Bench Core with an overall score of ${leader.score.toFixed(1)} / 100 at $${leader.cost.toFixed(2)} list-price run cost.`,
            },
          }] : []),
        ],
      },
    ],
  };
}

export function tasksJsonLd({ scenarioCount, taskTotal }) {
  return {
    '@context': 'https://schema.org',
    '@type': 'CollectionPage',
    name: 'P95 Bench Core Tasks',
    url: canonicalUrl('/tasks'),
    description: `All ${scenarioCount} public performance testing scenarios across JMeter, k6, Locust, and Gatling (${taskTotal} executable tasks).`,
    isPartOf: { '@id': `${SITE_URL}/#website` },
  };
}
