import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, readdir} from 'node:fs/promises';

test('self-hosted pixel styles preload both primary faces and retain existing prose typography', async () => {
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  const css = await readFile(new URL('../src/styles/geist.css', import.meta.url), 'utf8');
  for (const face of ['Square', 'Grid']) {
    assert.match(page, new RegExp(`rel="preload" href="/fonts/GeistPixel-${face}\\.woff2" as="font" type="font/woff2" crossorigin`));
    const bytes = await readFile(new URL(`../public/fonts/GeistPixel-${face}.woff2`, import.meta.url));
    assert.equal(bytes.subarray(0, 4).toString(), 'wOF2');
  }
  assert.equal([...css.matchAll(/font-display: swap/g)].length, 2);
  assert.match(page, /class="brand-p95">P95/);
  assert.match(page, /class="brand-bench">Bench/);
  assert.match(page, /@fontsource\/bricolage-grotesque/);
  assert.match(await readFile(new URL('../public/fonts/OFL.txt', import.meta.url), 'utf8'), /SIL OPEN FONT LICENSE Version 1.1/);
  assert.equal((await readdir(new URL('../public/fonts/', import.meta.url))).some(n => /serif/i.test(n)), false);
  assert.match(page, /id="task-grid" role="region" aria-label="Task results" tabindex="0"/);
  const client = await readFile(new URL('../src/lib/fullpage.mjs', import.meta.url), 'utf8');
  assert.match(client, /\[role="region"\]\[tabindex\]/);
});

test('jelly canvas samples each word with its own face and waits for both fonts', async () => {
  const source = await readFile(new URL('../src/lib/jelly-wordmark.mjs', import.meta.url), 'utf8');
  assert.match(source, /square\(\);\s*sample.fillText\('P95'/);
  assert.match(source, /grid\(\);\s*sample.fillText\('Bench', limeWidth \+ gap/);
  assert.match(source, /document.fonts.load\('400 64px "Geist Pixel Square"'\)/);
  assert.match(source, /document.fonts.load\('400 64px "Geist Pixel Grid"'\)/);
  assert.doesNotMatch(source, /Bricolage/);
  assert.match(source, /Promise\.allSettled\(\[[\s\S]*\]\)\.then\(build\)/);
});

test('short desktop focus scenes grow rather than hiding task results', async () => {
  const css = await readFile(new URL('../src/styles/geist.css', import.meta.url), 'utf8');
  assert.match(css, /max-height: 40rem[\s\S]*#scene-4\s*\{\s*block-size: auto/);
  assert.match(css, /#scene-4 \.profile\s*\{\s*flex: none;\s*grid-template-rows: auto/);
  assert.match(css, /#scene-4 \.task-grid\s*\{[\s\S]*min-block-size: 5\.5rem;\s*max-block-size: 16\.5rem/);
});
