export const numberDuration = 600;
const formatters = new Map();
export function interpolateNumber(from, to, progress) {
  if (![from, to, progress].every(Number.isFinite)) throw new RangeError('Interpolation requires finite numbers');
  const t = Math.max(0, Math.min(1, progress));
  return t === 1 ? to : from + (to - from) * (1 - (1 - t) ** 3);
}
export function formatNumber(value, { digits = 0, grouping = false } = {}) {
  if (!Number.isFinite(value) || !Number.isInteger(digits) || digits < 0 || digits > 6) throw new RangeError('Invalid number format');
  const rounded = Number(value.toFixed(digits));
  const key = `${digits}:${Boolean(grouping)}`;
  if (!formatters.has(key)) formatters.set(key, new Intl.NumberFormat('en-US', {
    minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: grouping,
  }));
  return formatters.get(key).format(Object.is(rounded, -0) ? 0 : rounded);
}
