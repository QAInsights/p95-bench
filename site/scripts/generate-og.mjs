import { readFile, unlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import os from 'node:os';
import path from 'node:path';
import { boardModels, boardTasks, TOOLS, formatCost } from '../src/lib/board.mjs';

const siteDir = path.resolve(fileURLToPath(new URL('..', import.meta.url)));

function findChrome() {
  if (process.env.CHROME_PATH && existsSync(process.env.CHROME_PATH)) return process.env.CHROME_PATH;
  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ];
  for (const p of candidates) if (existsSync(p)) return p;
  try { return execFileSync('which', ['google-chrome'], { encoding: 'utf8' }).trim(); } catch { return null; }
}

const data = JSON.parse(await readFile(path.join(siteDir, 'src/data/leaderboard-p95-core.json'), 'utf8'));
const models = boardModels(data);
const tasks = boardTasks(data);
const top3 = models.slice(0, 3);
const fontSq = (await readFile(path.join(siteDir, 'public/fonts/GeistPixel-Square.woff2'))).toString('base64');
const fontGr = (await readFile(path.join(siteDir, 'public/fonts/GeistPixel-Grid.woff2'))).toString('base64');

const rowsHtml = top3.map((m, i) => `
  <div class="row ${i === 0 ? 'lead' : ''}">
    <div class="rank">#${i + 1}</div>
    <div class="name">${m.name}</div>
    <div class="track"><div class="fill" style="width:${m.score.toFixed(1)}%"></div></div>
    <div class="score">${m.score.toFixed(1)}</div>
    <div class="cost">${formatCost(m.cost)}</div>
  </div>`).join('');

const html = `<!doctype html><html><head><meta charset="utf-8"><style>
  @font-face { font-family: "GP-Sq"; src: url("data:font/woff2;base64,${fontSq}") format("woff2"); }
  @font-face { font-family: "GP-Gr"; src: url("data:font/woff2;base64,${fontGr}") format("woff2"); }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    width: 1200px; height: 630px; overflow: hidden;
    background: radial-gradient(880px 440px at 18% 0%, rgba(212,255,58,0.12), transparent 65%),
                radial-gradient(600px 420px at 85% 65%, rgba(125,211,252,0.08), transparent 65%), #0c0b10;
    color: #f4f1ea; font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
    padding: 48px 54px; display: grid; grid-template-columns: 1.38fr 1fr; gap: 40px; align-items: center;
  }
  .left { display: flex; flex-direction: column; justify-content: space-between; height: 100%; }
  .brand { display: flex; align-items: center; gap: 14px; }
  .wordmark { font-size: 30px; letter-spacing: 0.01em; }
  .p95 { font-family: "GP-Sq", monospace; color: #d4ff3a; }
  .bench { font-family: "GP-Gr", monospace; color: #f4f1ea; }
  .pill {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
    color: #d4ff3a; border: 1px solid rgba(212,255,58,0.35); background: rgba(212,255,58,0.09);
    padding: 4px 12px; border-radius: 999px;
  }
  h1 { font-size: 46px; font-weight: 700; line-height: 1.12; letter-spacing: -0.025em; margin-top: 22px; }
  h1 span { color: #d4ff3a; }
  .sub { font-family: ui-monospace, monospace; font-size: 15px; color: #a39fb0; margin-top: 12px; }
  .board {
    margin-top: 28px; background: rgba(19,18,25,0.86); border: 1px solid #2b2835;
    border-radius: 14px; padding: 14px 18px; display: flex; flex-direction: column; gap: 12px;
  }
  .row { display: grid; grid-template-columns: 32px 175px 1fr 64px 68px; align-items: center; gap: 12px; }
  .rank { font-family: ui-monospace, monospace; font-size: 14px; color: #6e6a7c; }
  .row.lead .rank { color: #d4ff3a; font-weight: 700; }
  .name { font-size: 16px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .track { height: 10px; background: #1d1b25; border-radius: 99px; overflow: hidden; }
  .fill { height: 100%; border-radius: 99px; background: rgba(244,241,234,0.65); }
  .row.lead .fill { background: #d4ff3a; }
  .score { font-family: "GP-Gr", monospace; font-size: 22px; text-align: right; color: #f4f1ea; }
  .row.lead .score { color: #d4ff3a; }
  .cost { font-family: ui-monospace, monospace; font-size: 14px; text-align: right; color: #a39fb0; }
  .foot { display: flex; align-items: center; justify-content: space-between; margin-top: 20px; font-family: ui-monospace, monospace; font-size: 14px; color: #a39fb0; }
  .url { color: #f4f1ea; font-weight: 600; }
  .stage {
    height: 100%; background: radial-gradient(360px 260px at 50% 42%, rgba(212,255,58,0.08), transparent 72%), #0b0b0f;
    border: 1px solid #2b2835; border-radius: 18px; display: flex; align-items: center; justify-content: center; position: relative;
  }
  .stage-cap { position: absolute; top: 16px; left: 20px; font-family: ui-monospace, monospace; font-size: 12px; color: #6e6a7c; letter-spacing: 0.08em; }
</style></head><body>
  <div class="left">
    <div>
      <div class="brand">
        <div class="wordmark"><span class="p95">P95</span> <span class="bench">Bench</span></div>
        <span class="pill">${tasks.length * TOOLS.length} tasks · ${models.length} models</span>
      </div>
      <h1>AI <span>Performance Testing</span> Benchmark</h1>
      <div class="sub">JMeter · k6 · Locust · Gatling — Speed, Cost &amp; Quality</div>
      <div class="board">${rowsHtml}</div>
    </div>
    <div class="foot"><span class="url">p95.qainsights.com</span><span>Open-source · Reproducible runs</span></div>
  </div>
  <div class="stage">
    <div class="stage-cap">3D PARETO CHAMBER</div>
    <svg width="390" height="440" viewBox="0 0 390 440" fill="none">
      <defs>
        <linearGradient id="gq" x1="195" y1="315" x2="195" y2="62" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#d4ff3a" stop-opacity="0.08"/><stop offset="1" stop-color="#d4ff3a" stop-opacity="0.95"/></linearGradient>
        <linearGradient id="gs" x1="195" y1="315" x2="345" y2="380" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#7dd3fc" stop-opacity="0.08"/><stop offset="1" stop-color="#7dd3fc" stop-opacity="0.88"/></linearGradient>
        <linearGradient id="gc" x1="195" y1="315" x2="45" y2="380" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#6ee7b7" stop-opacity="0.08"/><stop offset="1" stop-color="#6ee7b7" stop-opacity="0.88"/></linearGradient>
      </defs>
      <polygon points="195,248 338,312 195,380 52,312" fill="rgba(22,22,32,0.75)" stroke="rgba(255,255,255,0.16)" stroke-width="1.2"/>
      <line x1="124" y1="280" x2="266" y2="346" stroke="rgba(255,255,255,0.07)"/><line x1="266" y1="280" x2="124" y2="346" stroke="rgba(255,255,255,0.07)"/>
      <line x1="195" y1="315" x2="195" y2="62" stroke="url(#gq)" stroke-width="2.4" stroke-linecap="round"/>
      <line x1="195" y1="315" x2="345" y2="380" stroke="url(#gs)" stroke-width="2.2" stroke-linecap="round"/>
      <line x1="195" y1="315" x2="45" y2="380" stroke="url(#gc)" stroke-width="2.2" stroke-linecap="round"/>
      <text x="195" y="46" fill="#d4ff3a" font-family="ui-monospace,monospace" font-size="12" font-weight="700" text-anchor="middle">QUALITY ↑</text>
      <text x="342" y="402" fill="#7dd3fc" font-family="ui-monospace,monospace" font-size="12" font-weight="700" text-anchor="middle">SPEED →</text>
      <text x="48" y="402" fill="#6ee7b7" font-family="ui-monospace,monospace" font-size="12" font-weight="700" text-anchor="middle">COST →</text>
      <!-- Background model spires -->
      <line x1="138" y1="296" x2="138" y2="175" stroke="rgba(244,241,234,0.22)"/><circle cx="138" cy="175" r="5.5" fill="#f4f1ea"/>
      <line x1="242" y1="302" x2="242" y2="162" stroke="rgba(244,241,234,0.22)"/><circle cx="242" cy="162" r="5.5" fill="#f4f1ea"/>
      <line x1="278" y1="322" x2="278" y2="232" stroke="rgba(244,241,234,0.16)"/><circle cx="278" cy="232" r="4.5" fill="#78798a"/>
      <line x1="112" y1="318" x2="112" y2="246" stroke="rgba(244,241,234,0.16)"/><circle cx="112" cy="246" r="4.5" fill="#78798a"/>
      <!-- Hero #1 Pareto leader node -->
      <line x1="195" y1="286" x2="195" y2="122" stroke="#d4ff3a" stroke-width="2" stroke-dasharray="4 4"/>
      <circle cx="195" cy="122" r="18" stroke="#d4ff3a" stroke-opacity="0.38" stroke-width="1.5"/>
      <circle cx="195" cy="122" r="9.5" fill="#d4ff3a"/>
      <text x="195" y="96" fill="#f4f1ea" font-family="system-ui,sans-serif" font-size="13" font-weight="700" text-anchor="middle">${top3[0]?.name ?? 'Top Model'}</text>
    </svg>
  </div>
</body></html>`;

const executablePath = findChrome();
if (!executablePath) throw new Error('Chrome/Edge executable not found');
const tmpHtml = path.join(os.tmpdir(), `p95-og-${process.pid}.html`);
const outPng = path.join(siteDir, 'public/og-image.png');
await writeFile(tmpHtml, html, 'utf8');
try {
  execFileSync(executablePath, [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--hide-scrollbars',
    '--window-size=1200,630',
    `--screenshot=${outPng}`,
    pathToFileURL(tmpHtml).href,
  ], { stdio: 'pipe' });
  console.log('Generated site/public/og-image.png (1200x630)');
} finally {
  await unlink(tmpHtml).catch(() => {});
}
