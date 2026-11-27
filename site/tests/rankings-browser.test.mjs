import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { test } from 'node:test';
import { extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const siteDir = resolve(fileURLToPath(new URL('..', import.meta.url)));
const distDir = join(siteDir, 'dist');

const contentTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webmanifest': 'application/manifest+json',
  '.woff2': 'font/woff2',
};

function chromeExecutable() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  try {
    return execFileSync('which', ['google-chrome'], { encoding: 'utf8' }).trim();
  } catch {
    return null;
  }
}

async function serveStatic() {
  const server = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      let file = normalize(join(distDir, pathname === '/' ? 'index.html' : pathname));
      if (!file.startsWith(distDir)) throw new Error('outside dist');
      if ((await stat(file)).isDirectory()) file = join(file, 'index.html');
      const body = await readFile(file);
      response.writeHead(200, { 'content-type': contentTypes[extname(file)] ?? 'application/octet-stream' });
      response.end(body);
    } catch {
      response.writeHead(404);
      response.end('not found');
    }
  });
  await new Promise(resolveListen => server.listen(0, '127.0.0.1', resolveListen));
  return server;
}

test('rankings sorting and filtering work in a browser', async t => {
  const executablePath = chromeExecutable();
  if (!executablePath) return t.skip('Chrome is unavailable');

  execFileSync('npm', ['run', 'build'], { cwd: siteDir, stdio: 'pipe' });
  const server = await serveStatic();
  t.after(() => new Promise(resolveClose => server.close(resolveClose)));
  const browser = await chromium.launch({ executablePath, args: ['--no-sandbox'] });
  t.after(() => browser.close());

  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const visibleModels = () => page.$$eval('#rankings-table tbody tr', rows =>
    rows.filter(row => !row.hidden).map(row => row.cells[1].textContent.trim()));

  const totalRows = (await visibleModels()).length;

  await page.click('#rankings-table thead th:nth-child(8) button');
  assert.equal(await page.getAttribute('#rankings-table thead th:nth-child(8)', 'aria-sort'), 'ascending');
  assert.match((await visibleModels())[0], /^GPT-6 Luna/);

  await page.click('#rankings-table thead th:nth-child(5) button');
  assert.match((await visibleModels())[0], /^Inkling 2 Mini/);
  await page.click('#rankings-table thead th:nth-child(5) button');
  assert.equal(await page.getAttribute('#rankings-table thead th:nth-child(5)', 'aria-sort'), 'descending');
  assert.match((await visibleModels())[0], /^Claude Sonnet 5\.5/);

  await page.fill('#model-filter', 'neon');
  assert.deepEqual(await visibleModels(), ['Gemini 4 Neon Google']);
  await page.fill('#model-filter', '');
  assert.equal((await visibleModels()).length, totalRows);
});
