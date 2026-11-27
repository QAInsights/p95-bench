import data from '../data/leaderboard-p95-core.json';
import { SITE_URL } from '../lib/seo.mjs';

export function GET() {
  const latest = data.models.map(m => m.run_date).filter(Boolean).sort().at(-1);
  const lastmod = latest ? new Date(latest).toISOString().slice(0, 10) : new Date().toISOString().slice(0, 10);
  const urls = [
    { loc: `${SITE_URL}/`, priority: '1.0', changefreq: 'weekly' },
    { loc: `${SITE_URL}/tasks`, priority: '0.8', changefreq: 'monthly' },
  ];
  const xml = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...urls.map(u => `  <url><loc>${u.loc}</loc><lastmod>${lastmod}</lastmod><changefreq>${u.changefreq}</changefreq><priority>${u.priority}</priority></url>`),
    '</urlset>',
  ].join('\n');

  return new Response(xml, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
}
