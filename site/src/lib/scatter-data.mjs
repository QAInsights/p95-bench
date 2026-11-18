export const scatterAxes = ['cost', 'tokens', 'latency'];
export const scatterLabels = { cost: 'Cost (USD)', tokens: 'Output tokens', latency: 'Latency (s)' };
export function scatterValue(model, axis) {
  if (!scatterAxes.includes(axis)) throw new RangeError(`Unknown scatter axis: ${axis}`);
  return axis === 'cost' ? model.cost : axis === 'tokens' ? model.tokens?.output : model.latency;
}

export function scatterScale(models, axis) {
  if (!scatterAxes.includes(axis)) throw new RangeError(`Unknown scatter axis: ${axis}`);
  const values = models.map(model => scatterValue(model, axis)).filter(value => Number.isFinite(value) && value >= 0);
  const low = values.length ? Math.min(...values) : 0, high = values.length ? Math.max(...values) : 1;
  const log = low > 0 && high / low > 10;
  const min = log ? low * .65 : Math.max(0, low - (high - low || 1) * .08);
  const max = log ? high * 1.22 : high + (high - low || 1) * .08;
  const transform = value => log ? Math.log10(value) : value;
  const start = transform(min), range = transform(max) - start;
  return { min, max, log, transform,
    position: value => (transform(value) - start) / range,
    value: ratio => log ? 10 ** (start + ratio * range) : start + ratio * range };
}

export function leastSquares(points) {
  const values = points.filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (values.length < 2) return null;
  const n = values.length, mx = values.reduce((sum, p) => sum + p.x, 0) / n,
    my = values.reduce((sum, p) => sum + p.y, 0) / n;
  const xx = values.reduce((sum, p) => sum + (p.x - mx) ** 2, 0);
  if (xx === 0) return null;
  const slope = values.reduce((sum, p) => sum + (p.x - mx) * (p.y - my), 0) / xx;
  const intercept = my - slope * mx;
  const yy = values.reduce((sum, p) => sum + (p.y - my) ** 2, 0);
  const error = values.reduce((sum, p) => sum + (p.y - slope * p.x - intercept) ** 2, 0);
  return { n, slope, intercept, r2: yy === 0 ? null : Math.max(0, Math.min(1, 1 - error / yy)) };
}
