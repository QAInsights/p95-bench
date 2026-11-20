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
});
