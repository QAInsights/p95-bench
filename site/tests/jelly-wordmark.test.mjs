import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { sampleDots, springStep, idlePointer } from '../src/lib/jelly-wordmark.mjs';

test('sampling respects alpha threshold, cell positions and the P95 color boundary', () => {
  const pixels = new Uint8ClampedArray(3 * 2 * 4);
  pixels[3] = 110;
  pixels[7] = 111;
  pixels[23] = 255;
  assert.deepEqual(sampleDots(pixels, 3, 2, 5, 2), [
    { hx: 5, hy: 0, x: 5, y: 0, vx: 0, vy: 0, lime: true },
    { hx: 10, hy: 5, x: 10, y: 5, vx: 0, vy: 0, lime: false },
  ]);
  assert.deepEqual(sampleDots(new Uint8ClampedArray(0), 0, 0, 5, 0), []);
  assert.deepEqual(sampleDots(new Uint8ClampedArray(4), 1, 1, 5, 0), []);
  assert.throws(() => sampleDots(pixels, 2, 2, 5, 2), /Invalid pixel buffer/);
});

test('a displaced pixel springs home, while a resting pixel and empty grid stay at rest', () => {
  const dot = { hx: 20, hy: 20, x: 30, y: 10, vx: 0, vy: 0 };
  assert.ok(springStep([dot], null, 5) > 0);
  assert.ok(dot.x < 30 && dot.y > 10);
  for (let index = 0; index < 300; index++) springStep([dot], null, 5);
  assert.ok(Math.abs(dot.x - dot.hx) < 1e-8 && Math.abs(dot.y - dot.hy) < 1e-8);
  assert.equal(springStep([], null, 5), 0);
  assert.equal(springStep([{ hx: 0, hy: 0, x: 0, y: 0, vx: 0, vy: 0 }], null, 5), 0);
});

test('pointer pushes nearby pixels and idle ripple is gentler with a bounded source', () => {
  const dot = { hx: 20, hy: 20, x: 20, y: 20, vx: 0, vy: 0 };
  const active = { ...dot }, idle = { ...dot }, far = { ...dot };
  const source = { x: 10, y: 10, vx: 1, vy: 1 };
  springStep([active], source, 5);
  springStep([idle], source, 5, true);
  springStep([far], { ...source, x: -1000, y: -1000 }, 5);
  assert.ok(active.x > idle.x && idle.x > dot.x);
  assert.ok(active.y > idle.y && idle.y > dot.y);
  assert.deepEqual(far, dot);
  for (const seconds of [0, 1, 100, 1000]) {
    const ghost = idlePointer(100, 50, seconds);
    assert.ok(ghost.x >= 2 && ghost.x <= 98 && ghost.y >= 7.5 && ghost.y <= 42.5);
    assert.ok(Number.isFinite(ghost.vx) && Number.isFinite(ghost.vy));
  }
});

test('wordmark is decorative, below legal text, locally font-backed and has no pause control', async () => {
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  const css = await readFile(new URL('../src/styles/jelly-wordmark.css', import.meta.url), 'utf8');
  assert.ok(page.indexOf('id="jelly-wordmark"') > page.indexOf('© 2026'));
  assert.match(page, /latin-800\.css/);
  assert.match(page, /<canvas aria-hidden="true"><\/canvas>/);
  assert.match(page, /<span class="sr">P95 Bench<\/span>/);
  assert.match(css, /touch-action: pan-y/);
  assert.doesNotMatch(page.slice(page.indexOf('id="jelly-wordmark"'), page.indexOf('id="jelly-wordmark"') + 200), /button|pause/i);
});
