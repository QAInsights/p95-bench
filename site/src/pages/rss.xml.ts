import data from '../data/leaderboard-p95-core.json';
import { boardModels } from '../lib/board.mjs';
import { modelSlug } from '../lib/scores.mjs';
import { SITE_URL } from '../lib/seo.mjs';

const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function GET() {
  const models = boardModels(data);
  const byKey = new Map(data.models.map(m => [`${m.provider}/${m.model}`, m]));
  const items = models
    .map(m => ({ ...m, runDate: byKey.get(m.key)?.run_date ?? null, slug: modelSlug(m.key.split('/').slice(1).join('/')) }))
    .sort((a, b) => String(b.runDate ?? '').localeCompare(String(a.runDate ?? '')));

  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    '  <channel>',
    '    <title>P95 Bench · Core Leaderboard Updates</title>',
    `    <link>${SITE_URL}/</link>`,
    `    <atom:link href="${SITE_URL}/rss.xml" rel="self" type="application/rss+xml" />`,
    '    <description>Latest AI performance testing benchmark runs across JMeter, k6, Locust, and Gatling.</description>',
    '    <language>en-us</language>',
    ...items.map(m => {
      const link = `${SITE_URL}/models/${m.slug}`;
      const pub = m.runDate ? new Date(m.runDate).toUTCString() : new Date().toUTCString();
      const desc = `${m.name} (${m.providerLabel}) scored ${m.score.toFixed(1)}/100 on P95 Bench Core at $${m.cost.toFixed(2)} total cost across ${m.taskCount} tasks.`;
      return [
        '    <item>',
        `      <title>${esc(`${m.name}: ${m.score.toFixed(1)}/100 on P95 Bench Core`)}</title>`,
        `      <link>${link}</link>`,
        `      <guid>${link}</guid>`,
        `      <pubDate>${pub}</pubDate>`,
        `      <description>${esc(desc)}</description>`,
        '    </item>',
      ].join('\n');
    }),
    '  </channel>',
    '</rss>',
  ].join('\n');

  return new Response(xml, { headers: { 'Content-Type': 'application/rss+xml; charset=utf-8' } });
}
