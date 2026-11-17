import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

const publicFile = (name) => new URL(`../public/${name}`, import.meta.url);

test('manifest declares the site identity, matching description and available icons', async () => {
  const manifest = JSON.parse(await readFile(publicFile('site.webmanifest'), 'utf8'));
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  assert.equal(manifest.name, 'P95 Bench');
  assert.equal(manifest.short_name, 'P95 Bench');
  assert.equal(manifest.description, page.match(/<meta name="description" content="([^"]+)"/)[1]);
  assert.equal(manifest.start_url, '/');
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.background_color, '#0c0b10');
  assert.equal(manifest.theme_color, '#0c0b10');
  assert.deepEqual(manifest.icons.map((icon) => [icon.sizes, icon.type, icon.purpose]), [
    ['any', 'image/svg+xml', 'any'], ['192x192', 'image/png', 'any'],
    ['512x512', 'image/png', 'any'], ['512x512', 'image/png', 'maskable'],
  ]);
  for (const icon of manifest.icons) {
    const contents = await readFile(publicFile(icon.src.slice(1)));
    if (icon.type === 'image/png') {
      assert.equal(contents.subarray(1, 4).toString(), 'PNG');
      assert.equal(`${contents.readUInt32BE(16)}x${contents.readUInt32BE(20)}`, icon.sizes);
    } else {
      assert.match(contents.toString(), /<svg/);
    }
  }
  const apple = await readFile(publicFile('apple-touch-icon.png'));
  assert.equal(apple.readUInt32BE(16), 180);
  assert.equal(apple.readUInt32BE(20), 180);
});
