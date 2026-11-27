import {
  CHAMBER_CAMERAS,
  chamberGeometry,
  clamp,
  formatTaskCost,
  formatTaskLatency,
  project3D,
} from './pareto3d-math.mjs';

const PALETTE = {
  lime: '#d4ff3a',
  ink: '#f4f1ea',
  sub: 'rgba(244, 241, 234, 0.44)',
  hair: 'rgba(255, 255, 255, 0.075)',
  edge: 'rgba(255, 255, 255, 0.16)',
  deck: 'rgba(20, 19, 28, 0.72)',
  paretoDot: '#f4f1ea',
  dimDot: '#646677',
  bgStroke: 'rgba(12, 11, 16, 0.92)',
};

function poly(ctx, pts, fill, stroke, lw = 1) {
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.stroke(); }
}

function seg3D(ctx, a, b, cam, w, h, color, lw = 1, dash = []) {
  const pa = project3D(a, cam, w, h);
  const pb = project3D(b, cam, w, h);
  ctx.save();
  ctx.beginPath();
  ctx.setLineDash(dash);
  ctx.moveTo(pa.x, pa.y);
  ctx.lineTo(pb.x, pb.y);
  ctx.strokeStyle = color;
  ctx.lineWidth = lw;
  ctx.stroke();
  ctx.restore();
  return { pa, pb };
}

function streak3D(ctx, a, b, cam, w, h, tailColor, tipColor) {
  const pa = project3D(a, cam, w, h);
  const pb = project3D(b, cam, w, h);
  const grad = ctx.createLinearGradient(pa.x, pa.y, pb.x, pb.y);
  grad.addColorStop(0, 'rgba(255, 255, 255, 0.04)');
  grad.addColorStop(0.55, tailColor);
  grad.addColorStop(1, tipColor);
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(pa.x, pa.y);
  ctx.lineTo(pb.x, pb.y);
  ctx.strokeStyle = grad;
  ctx.lineWidth = 4.5;
  ctx.globalAlpha = 0.22;
  ctx.lineCap = 'round';
  ctx.stroke();
  ctx.lineWidth = 1.4;
  ctx.globalAlpha = 0.85;
  ctx.stroke();
  ctx.restore();
}

function drawTag(ctx, x, y, text, color, align = 'center') {
  ctx.save();
  ctx.font = '600 10.5px "JetBrains Mono", ui-monospace, monospace';
  ctx.textAlign = align;
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 3.5;
  ctx.strokeStyle = PALETTE.bgStroke;
  ctx.strokeText(text, x, y);
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
  ctx.restore();
}

export function mountParetoChamber(root, items) {
  if (!root || !items.length) return;
  const stage = root.querySelector('[data-stage]');
  const cv = root.querySelector('canvas');
  const rail = root.querySelector('[data-rail]');
  const ctx = cv.getContext('2d');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const params = new URLSearchParams(window.location.search);
  const initCam = CHAMBER_CAMERAS.find(c => c.id === params.get('cam')) ?? CHAMBER_CAMERAS[0];
  const initFocus = ['balanced', 'quality', 'speed', 'cost'].includes(params.get('focus')) ? params.get('focus') : 'balanced';
  const initSel = Math.max(0, items.findIndex(m => m.slug === params.get('model')));

  const state = {
    focus: initFocus,
    camId: initCam.id,
    onlyPareto: false,
    selected: initSel,
    hover: null,
    cam: { ...initCam },
    targetCam: { ...initCam },
    hitPts: [],
    pulse: 0,
  };

  let geo = chamberGeometry(items, state.focus);

  function syncInspector() {
    geo = chamberGeometry(items, state.focus);
    const activeIdx = state.hover !== null ? state.hover : state.selected;
    const item = geo[activeIdx] ?? geo[0];
    const summaryEl = root.querySelector('[data-stage-summary]');
    if (summaryEl) {
      summaryEl.innerHTML = `<strong>${item.name}</strong>${item.quality.toFixed(1)} pts · ${formatTaskLatency(item.latencyPerTask)}/task · ${formatTaskCost(item.costPerTask)}/task`;
    }
    root.querySelector('[data-m-name]').textContent = item.name;
    root.querySelector('[data-m-prov]').textContent = `${item.providerLabel} · $${item.totalCost.toFixed(2)} full run`;
    root.querySelector('[data-m-badge]').hidden = !item.pareto;
    root.querySelector('[data-m-idx]').textContent = (item.util * 100).toFixed(1);
    root.querySelector('[data-m-q]').textContent = item.quality.toFixed(1);
    root.querySelector('[data-m-s]').textContent = formatTaskLatency(item.latencyPerTask);
    root.querySelector('[data-m-c]').textContent = formatTaskCost(item.costPerTask);
    root.querySelector('[data-b-q]').style.width = `${item.norm.q * 100}%`;
    root.querySelector('[data-b-s]').style.width = `${item.norm.s * 100}%`;
    root.querySelector('[data-b-c]').style.width = `${item.norm.c * 100}%`;

    const ranked = [...geo]
      .filter(n => !state.onlyPareto || n.pareto)
      .sort((a, b) => b.util - a.util || b.quality - a.quality);

    rail.innerHTML = ranked.map((r, rank) => `
      <button type="button" class="chamber-row${r.index === activeIdx ? ' active' : ''}" data-i="${r.index}">
        <span class="cr-rank">${rank + 1}</span>
        <span class="cr-name">
          <span class="cr-dot${r.index === activeIdx ? ' is-act' : r.pareto ? ' is-p' : ''}"></span>
          <span>${r.name}</span>
        </span>
        <span class="cr-meta">${r.quality.toFixed(0)} · ${formatTaskLatency(r.latencyPerTask)} · ${formatTaskCost(r.costPerTask)}</span>
      </button>`).join('');

    rail.querySelectorAll('[data-i]').forEach(el => {
      const idx = Number(el.dataset.i);
      el.addEventListener('mouseenter', () => { state.hover = idx; syncInspector(); });
      el.addEventListener('mouseleave', () => { state.hover = null; syncInspector(); });
      el.addEventListener('click', () => { state.selected = idx; syncInspector(); });
    });
  }

  function drawChamber(w, h, activeNode) {
    const L = 1.06, H = 1.66;
    const floor = [[-L, 0, -L], [L, 0, -L], [L, 0, L], [-L, 0, L]]
      .map(([x, y, z]) => project3D({ x, y, z }, state.cam, w, h));
    poly(ctx, floor, PALETTE.deck, PALETTE.edge, 1);

    for (const f of [-L / 3, L / 3]) {
      seg3D(ctx, { x: f, y: 0, z: -L }, { x: f, y: 0, z: L }, state.cam, w, h, PALETTE.hair, 0.75);
      seg3D(ctx, { x: -L, y: 0, z: f }, { x: L, y: 0, z: f }, state.cam, w, h, PALETTE.hair, 0.75);
    }
    for (const y of [H * 0.5, H]) {
      seg3D(ctx, { x: -L, y, z: -L }, { x: L, y, z: -L }, state.cam, w, h, PALETTE.hair, 0.75);
      seg3D(ctx, { x: -L, y, z: -L }, { x: -L, y, z: L }, state.cam, w, h, PALETTE.hair, 0.75);
    }

    const O = { x: -L, y: 0, z: -L };
    streak3D(ctx, O, { x: -L, y: H + 0.12, z: -L }, state.cam, w, h, 'rgba(212, 255, 58, 0.28)', 'rgba(212, 255, 58, 0.92)');
    streak3D(ctx, O, { x: L + 0.14, y: 0, z: -L }, state.cam, w, h, 'rgba(125, 211, 252, 0.22)', 'rgba(125, 211, 252, 0.78)');
    streak3D(ctx, O, { x: -L, y: 0, z: L + 0.14 }, state.cam, w, h, 'rgba(110, 231, 183, 0.22)', 'rgba(110, 231, 183, 0.78)');

    [
      { pt: { x: -L, y: H + 0.22, z: -L }, txt: 'QUALITY ↑', col: 'rgba(212, 255, 58, 0.82)' },
      { pt: { x: L + 0.24, y: 0, z: -L }, txt: 'SPEED →', col: 'rgba(125, 211, 252, 0.78)' },
      { pt: { x: -L, y: 0, z: L + 0.24 }, txt: 'COST →', col: 'rgba(110, 231, 183, 0.78)' },
    ].forEach(a => {
      const p = project3D(a.pt, state.cam, w, h);
      drawTag(ctx, clamp(p.x, 34, w - 34), clamp(p.y, 28, h - 46), a.txt, a.col);
    });

    if (activeNode) {
      const { pos } = activeNode;
      const rY = seg3D(ctx, pos, { x: pos.x, y: 0, z: pos.z }, state.cam, w, h, PALETTE.lime, 1.2, [3, 4]);
      const rX = seg3D(ctx, pos, { x: -L, y: pos.y, z: pos.z }, state.cam, w, h, PALETTE.lime, 1.0, [3, 4]);
      const rZ = seg3D(ctx, pos, { x: pos.x, y: pos.y, z: -L }, state.cam, w, h, PALETTE.lime, 1.0, [3, 4]);
      drawTag(ctx, rY.pb.x, rY.pb.y + 12, `${activeNode.quality.toFixed(1)} pts`, PALETTE.lime);
      drawTag(ctx, rX.pb.x - 6, rX.pb.y, formatTaskLatency(activeNode.latencyPerTask), PALETTE.lime, 'right');
      drawTag(ctx, rZ.pb.x + 6, rZ.pb.y, formatTaskCost(activeNode.costPerTask), PALETTE.lime, 'left');
    }
  }

  function render() {
    const rect = stage.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = rect.width, h = rect.height;
    if (w > 0 && h > 0) {
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
        cv.width = Math.round(w * dpr);
        cv.height = Math.round(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const lerp = reducedMotion ? 1 : 0.12;
      state.cam.yaw += (state.targetCam.yaw - state.cam.yaw) * lerp;
      state.cam.pitch += (state.targetCam.pitch - state.cam.pitch) * lerp;
      state.cam.zoom += (state.targetCam.zoom - state.cam.zoom) * lerp;
      if (!reducedMotion) state.pulse += 0.04;

      const activeIdx = state.hover !== null ? state.hover : state.selected;
      const visible = geo.filter(n => !state.onlyPareto || n.pareto || n.index === activeIdx);
      const projected = visible.map(n => ({
        ...n,
        p: project3D(n.pos, state.cam, w, h),
        pb: project3D({ x: n.pos.x, y: 0, z: n.pos.z }, state.cam, w, h),
      }));

      const activeNode = projected.find(n => n.index === activeIdx) ?? projected[0];
      drawChamber(w, h, activeNode);

      const hitRadius = window.matchMedia('(pointer: coarse)').matches ? 24 : 16;
      const sorted = [...projected].sort((a, b) => a.p.z - b.p.z);
      state.hitPts = sorted.map(n => ({ index: n.index, x: n.p.x, y: n.p.y, r: hitRadius }));

      for (const n of sorted) {
        const isAct = n.index === activeIdx;
        const depthAlpha = clamp(0.68 + (n.p.z + 1.4) * 0.16, 0.45, 1);
        const alpha = isAct ? 1 : n.pareto ? depthAlpha : depthAlpha * 0.5;

        ctx.save();
        ctx.globalAlpha = isAct ? 0.55 : alpha * 0.28;
        ctx.beginPath();
        ctx.ellipse(n.pb.x, n.pb.y, isAct ? 6 : 4, isAct ? 2.6 : 1.8, 0, 0, Math.PI * 2);
        ctx.fillStyle = isAct ? PALETTE.lime : PALETTE.paretoDot;
        ctx.fill();
        ctx.restore();

        ctx.save();
        ctx.globalAlpha = isAct ? 0.9 : alpha * 0.25;
        ctx.strokeStyle = isAct ? PALETTE.lime : PALETTE.paretoDot;
        ctx.lineWidth = isAct ? 1.8 : 0.9;
        ctx.beginPath();
        ctx.moveTo(n.p.x, n.p.y);
        ctx.lineTo(n.pb.x, n.pb.y);
        ctx.stroke();
        ctx.restore();

        const rad = (isAct ? 7.8 : n.pareto ? 5.6 : 4.2) * clamp(n.p.s, 0.85, 1.25);
        if (isAct) {
          ctx.save();
          ctx.beginPath();
          ctx.arc(n.p.x, n.p.y, rad + 5 + Math.sin(state.pulse) * 1.1, 0, Math.PI * 2);
          ctx.strokeStyle = PALETTE.lime;
          ctx.globalAlpha = 0.55;
          ctx.lineWidth = 1.2;
          ctx.stroke();
          ctx.restore();
        }

        ctx.save();
        ctx.globalAlpha = alpha;
        const core = isAct ? PALETTE.lime : n.pareto ? PALETTE.paretoDot : PALETTE.dimDot;
        const g = ctx.createRadialGradient(n.p.x - rad * 0.3, n.p.y - rad * 0.35, rad * 0.1, n.p.x, n.p.y, rad);
        g.addColorStop(0, '#ffffff');
        g.addColorStop(0.45, core);
        g.addColorStop(1, '#09090d');
        ctx.beginPath();
        ctx.arc(n.p.x, n.p.y, rad, 0, Math.PI * 2);
        ctx.fillStyle = g;
        ctx.fill();
        ctx.restore();

        if (isAct) {
          ctx.save();
          ctx.font = '600 12px "Bricolage Grotesque", system-ui, sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'bottom';
          ctx.lineWidth = 4;
          ctx.strokeStyle = PALETTE.bgStroke;
          ctx.strokeText(n.name, n.p.x, n.p.y - rad - 8);
          ctx.fillStyle = PALETTE.ink;
          ctx.fillText(n.name, n.p.x, n.p.y - rad - 8);
          ctx.restore();
        }
      }
    }
    requestAnimationFrame(render);
  }

  let drag = null;
  stage.addEventListener('pointerdown', e => {
    if (e.target.closest('button')) return;
    drag = { x: e.clientX, y: e.clientY, moved: false };
    stage.setPointerCapture(e.pointerId);
  });
  stage.addEventListener('pointermove', e => {
    const r = stage.getBoundingClientRect();
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.hypot(dx, dy) > 3) drag.moved = true;
      state.targetCam.yaw += dx * 0.008;
      state.targetCam.pitch = clamp(state.targetCam.pitch + dy * 0.007, 0.02, 1.5);
      drag.x = e.clientX;
      drag.y = e.clientY;
      return;
    }
    const hit = [...state.hitPts].reverse()
      .find(p => Math.hypot(p.x - (e.clientX - r.left), p.y - (e.clientY - r.top)) < p.r);
    const next = hit ? hit.index : null;
    if (next !== state.hover) { state.hover = next; syncInspector(); }
    stage.style.cursor = hit ? 'pointer' : 'grab';
  });
  stage.addEventListener('pointerup', e => {
    if (drag && !drag.moved) {
      const r = stage.getBoundingClientRect();
      const hit = [...state.hitPts].reverse()
        .find(p => Math.hypot(p.x - (e.clientX - r.left), p.y - (e.clientY - r.top)) < p.r);
      if (hit) { state.selected = hit.index; syncInspector(); }
    }
    drag = null;
  });
  stage.addEventListener('wheel', e => {
    e.preventDefault();
    state.targetCam.zoom = clamp(state.targetCam.zoom - e.deltaY * 0.0008, 0.75, 1.5);
  }, { passive: false });

  root.querySelectorAll('[data-cam]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.cam === state.camId);
    btn.addEventListener('click', () => {
      const cam = CHAMBER_CAMERAS.find(c => c.id === btn.dataset.cam);
      if (!cam) return;
      state.camId = cam.id;
      state.targetCam = { ...cam };
      root.querySelectorAll('[data-cam]').forEach(b => b.classList.toggle('active', b === btn));
    });
  });

  root.querySelectorAll('[data-focus]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.focus === state.focus);
    btn.addEventListener('click', () => {
      state.focus = btn.dataset.focus;
      root.querySelectorAll('[data-focus]').forEach(b => b.classList.toggle('active', b === btn));
      syncInspector();
    });
  });

  const shareBtn = root.querySelector('[data-share]');
  if (shareBtn) {
    shareBtn.addEventListener('click', async () => {
      const item = geo[state.selected] ?? geo[0];
      const url = new URL(window.location.href);
      url.searchParams.set('model', item.slug);
      url.searchParams.set('cam', state.camId);
      url.searchParams.set('focus', state.focus);
      url.hash = 'tradeoffs';
      try { await navigator.clipboard.writeText(url.toString()); } catch {}
      shareBtn.textContent = 'Copied';
      setTimeout(() => { shareBtn.textContent = 'Share'; }, 1400);
    });
  }

  const paretoBtn = root.querySelector('[data-pareto-only]');
  if (paretoBtn) {
    paretoBtn.addEventListener('click', () => {
      state.onlyPareto = !state.onlyPareto;
      paretoBtn.classList.toggle('active', state.onlyPareto);
      syncInspector();
    });
  }

  const panelBtn = root.querySelector('[data-toggle-panel]');
  if (panelBtn) {
    panelBtn.addEventListener('click', () => {
      const open = root.classList.toggle('is-expanded');
      panelBtn.classList.toggle('active', open);
      panelBtn.setAttribute('aria-expanded', String(open));
    });
  }

  syncInspector();
  requestAnimationFrame(render);
}
