import { modelSlug } from './scores.mjs';

export const FOCUS_WEIGHTS = {
  balanced: [0.46, 0.27, 0.27],
  quality: [0.78, 0.11, 0.11],
  speed: [0.24, 0.62, 0.14],
  cost: [0.24, 0.14, 0.62],
};

export const CHAMBER_CAMERAS = [
  { id: 'iso', label: '3D', yaw: 0.66, pitch: 0.44, zoom: 0.96 },
  { id: 'tri', label: 'Triangle', yaw: 0.785, pitch: 0.615, zoom: 0.92 },
  { id: 'qc', label: 'Q × Cost', yaw: 0.0, pitch: 0.02, zoom: 0.96 },
];

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function chamberModels(models) {
  return models
    .filter(m => Number.isFinite(m.score) && Number.isFinite(m.cost) && m.cost > 0)
    .map(m => {
      const taskCount = Number.isFinite(m.taskCount) && m.taskCount > 0 ? m.taskCount : 1;
      const quality = Number.isFinite(m.quality) ? m.quality : m.score;
      const latencyPerTask = Number.isFinite(m.latency) && m.latency > 0 ? m.latency / taskCount : null;
      return {
        key: m.key,
        slug: modelSlug(m.key.split('/').slice(1).join('/')),
        name: m.name,
        providerLabel: m.providerLabel ?? m.provider ?? '',
        quality,
        score: m.score,
        latencyPerTask,
        costPerTask: m.cost / taskCount,
        totalCost: m.cost,
        taskCount,
      };
    });
}

export function computePareto(items) {
  return items.map((a, i) => !items.some((b, j) => {
    if (j === i) return false;
    const latA = a.latencyPerTask ?? Infinity;
    const latB = b.latencyPerTask ?? Infinity;
    return b.quality >= a.quality && latB <= latA && b.costPerTask <= a.costPerTask
      && (b.quality > a.quality || latB < latA || b.costPerTask < a.costPerTask);
  }));
}

function logDomain(values) {
  const valid = values.filter(v => Number.isFinite(v) && v > 0);
  if (!valid.length) return [0, 1];
  const loVal = Math.min(...valid);
  const hiVal = Math.max(...valid);
  if (Math.abs(hiVal - loVal) < 1e-12) {
    return [Math.log10(loVal * 0.4), Math.log10(hiVal * 2.5)];
  }
  return [Math.log10(loVal * 0.72), Math.log10(hiVal * 1.38)];
}

export function chamberGeometry(items, focus = 'balanced') {
  if (!items.length) return [];
  const [logL0, logL1] = logDomain(items.map(m => m.latencyPerTask));
  const [logC0, logC1] = logDomain(items.map(m => m.costPerTask));
  const qMin = items.length === 1 ? 0 : Math.max(0, Math.floor(Math.min(...items.map(m => m.quality)) / 10) * 10 - 10);
  const qMax = 100;
  const pareto = computePareto(items);
  const [wq, ws, wc] = FOCUS_WEIGHTS[focus] ?? FOCUS_WEIGHTS.balanced;

  return items.map((m, i) => {
    const q = clamp((m.quality - qMin) / (qMax - qMin || 1), 0.06, 1);
    const s = m.latencyPerTask && m.latencyPerTask > 0
      ? clamp(1 - (Math.log10(m.latencyPerTask) - logL0) / (logL1 - logL0 || 1), 0.06, 1)
      : 0.5;
    const c = clamp(1 - (Math.log10(m.costPerTask) - logC0) / (logC1 - logC0 || 1), 0.06, 1);
    const util = wq * q + ws * s + wc * c;
    return {
      ...m,
      index: i,
      pareto: pareto[i],
      norm: { q, s, c },
      util,
      pos: {
        x: (s * 2 - 1) * 1.02,
        y: 0.08 + q * 1.54,
        z: (c * 2 - 1) * 1.02,
      },
    };
  });
}

export function project3D(pt, cam, width, height) {
  const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
  const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
  const x1 = pt.x * cy - pt.z * sy;
  const z1 = pt.x * sy + pt.z * cy;
  const yc = pt.y - 0.62;
  const y2 = yc * cp - z1 * sp;
  const z2 = yc * sp + z1 * cp;
  const dist = 5.6 / cam.zoom;
  const persp = dist / (dist - z2);
  const sc = Math.min(width, height) * 0.305 * cam.zoom;
  return {
    x: width * 0.5 + x1 * sc * persp,
    y: height * 0.56 - y2 * sc * persp,
    z: z2,
    s: persp,
  };
}

export function formatTaskCost(value) {
  if (!Number.isFinite(value)) return '—';
  return `$${value.toFixed(value < 0.05 ? 3 : 2)}`;
}

export function formatTaskLatency(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  return seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`;
}
