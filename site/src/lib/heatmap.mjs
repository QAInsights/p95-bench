import { metricExtent, heatColor, heatValue } from './heatmap-data.mjs';
import { rollNumber, rollText, announceRolling } from './rolling-numbers.mjs';

export function mountHeatmap(root, { signal, animate = false } = {}) {
  const data = JSON.parse(root.querySelector('#heatmap-records').textContent);
  const grid = root.querySelector('#heatmap-grid'), tip = root.querySelector('#heatmap-tooltip');
  let metric = root.dataset.heatmapMetric ?? 'result', active = null, hideTimer;
  const visibleRows = () => [...grid.querySelectorAll('tbody tr:not([hidden])')];
  function hideTip() { clearTimeout(hideTimer); tip.hidden = true; }
  function inspect(cell) {
    if (!cell) return;
    clearTimeout(hideTimer);
    root.querySelector('[data-inspected]')?.removeAttribute('data-inspected');
    active = cell;
    cell.setAttribute('data-inspected', '');
    grid.setAttribute('aria-activedescendant', cell.id);
    tip.textContent = cell.getAttribute('aria-label');
    tip.hidden = false;
    const rect = cell.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(rect.left, innerWidth - tip.offsetWidth - 8)) + 'px';
    tip.style.top = Math.max(8, Math.min(rect.bottom + 8, innerHeight - tip.offsetHeight - 8)) + 'px';
  }
  grid.addEventListener('pointerover', event => {
    const cell = event.target.closest('[data-heat-cell]');
    if (cell) inspect(cell);
  });
  grid.addEventListener('pointerout', () => { hideTimer = setTimeout(hideTip, 120); });
  tip.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  tip.addEventListener('pointerleave', hideTip);
  grid.addEventListener('focus', () => inspect(active ?? visibleRows()[0]?.querySelector('[data-heat-cell]')));
  grid.addEventListener('blur', hideTip);
  grid.addEventListener('scroll', () => { if (document.activeElement === grid && active && !tip.hidden) inspect(active); else hideTip(); });
  root.addEventListener('keydown', event => { if (event.key === 'Escape') hideTip(); }, { signal });
  grid.addEventListener('keydown', event => {
    if (event.altKey || event.metaKey || event.shiftKey || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    const rows = visibleRows(), row = active?.parentElement, ri = Math.max(0, rows.indexOf(row));
    const cells = [...rows[ri]?.querySelectorAll('[data-heat-cell]') ?? []];
    const ci = Math.max(0, cells.indexOf(active));
    let next;
    if (event.key === 'Home') next = (event.ctrlKey ? rows[0] : rows[ri])?.querySelector('[data-heat-cell]');
    else if (event.key === 'End') next = (event.ctrlKey ? rows.at(-1) : rows[ri])?.querySelector('[data-heat-cell]:last-child');
    else if (event.ctrlKey) return;
    else if (event.key === 'ArrowLeft') next = cells[ci - 1];
    else if (event.key === 'ArrowRight') next = cells[ci + 1];
    else next = rows[ri + (event.key === 'ArrowDown' ? 1 : -1)]?.querySelectorAll('[data-heat-cell]')[ci];
    if (!next || next === active) return;
    event.preventDefault();
    next.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
    const rect = next.getBoundingClientRect(), left = grid.getBoundingClientRect().left + grid.querySelector('.heat-model').getBoundingClientRect().width;
    if (rect.left < left) grid.scrollLeft -= left - rect.left;
    inspect(next);
  });
  function render(animate = false) {
    root.dataset.heatmapMetric = metric;
    hideTip();
    const showEarlier = document.getElementById('show-versions').checked;
    const rows = data.rows.filter(row => showEarlier || row.latest), extent = metricExtent(rows, metric);
    root.querySelectorAll('[data-heat-row],[data-heat-data-row]').forEach(node => {
      node.hidden = !showEarlier && !data.rows[Number(node.dataset.heatRow ?? node.dataset.heatDataRow)].latest;
    });
    grid.querySelectorAll('[data-heat-cell]').forEach(node => {
      const row = data.rows[Number(node.dataset.row)], cell = row.cells[Number(node.dataset.column)];
      const color = heatColor(cell[metric], metric, extent, cell.partial_credit);
      node.style.setProperty('--heat-color', color ?? 'repeating-linear-gradient(-45deg, transparent 0 .1875rem, #99919f .1875rem .25rem)');
      node.setAttribute('aria-label', `${row.model} · ${row.provider} · ${cell.task} · ${heatValue(cell, metric)} · ${cell.measured}/${cell.attempts} measured attempts (mean)`);
      node.firstElementChild.textContent = metric === 'result' && cell.result === 0 ? '×' : cell[metric] === null ? '—' : metric === 'result' && cell.result !== 1 ? '%' : '';
    });
    active = null;
    grid.removeAttribute('aria-activedescendant');
    root.querySelector('[data-inspected]')?.removeAttribute('data-inspected');
    const legend = root.querySelector('#heatmap-legend');
    legend.replaceChildren();
    const swatch = (text, color) => {
      const box = document.createElement('i'); box.className = 'heat-swatch'; box.style.background = color; box.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span'); label.textContent = text; legend.append(box, label);
    };
    if (metric === 'result') {
      swatch('Pass', '#d4ff3a'); swatch('Fail ×', '#c4416a'); swatch('Not run', 'repeating-linear-gradient(-45deg, transparent 0 .1875rem, #99919f .1875rem .25rem)');
      const note = document.createElement('span'); note.textContent = 'Partial credit and mixed repetitions use lime/red gradients'; legend.append(note);
    } else if (extent.min === null) legend.textContent = 'No measured values';
    else {
      const low = document.createElement('span'), high = document.createElement('span'), ramp = document.createElement('i');
      low.textContent = 'Min ' + heatValue({ [metric]: extent.min }, metric);
      high.textContent = 'Max ' + heatValue({ [metric]: extent.max }, metric);
      ramp.className = 'heat-ramp'; ramp.setAttribute('aria-hidden', 'true'); legend.append(low, ramp, high);
      swatch('No measurement', 'repeating-linear-gradient(-45deg, transparent 0 .1875rem, #99919f .1875rem .25rem)');
    }
    const count = rows.flatMap(row => row.cells).reduce((sum, cell) => sum + cell.attempts, 0);
    rollNumber(root.querySelector('#heatmap-source'), count, { key: 'heatmap:source', animate, max: data.rows.flatMap(row => row.cells).reduce((sum, cell) => sum + cell.attempts, 0) });
    rollText(root.querySelector('#heatmap-summary'), `${rows.length} models · ${data.columns.length} tasks. Repeated attempts show means; Not run is excluded.`, { key: 'heatmap:summary', animate, max: Math.max(data.rows.length, data.columns.length) });
    const legendLabel = [...legend.querySelectorAll('span')].map(node => node.textContent).join('. ') || legend.textContent;
    root.querySelector('#heatmap-status').textContent = `${metric} metric. ${rows.length} models. ${legendLabel}`;
    if (animate) announceRolling(`Heatmap ${metric} metric. ${rows.length} models, ${data.columns.length} tasks, ${count} published attempts. ${legendLabel}`, 'heatmap');
    root.querySelectorAll('[data-heat-metric]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.heatMetric === metric)));
    if (document.activeElement === grid) inspect(visibleRows()[0]?.querySelector('[data-heat-cell]'));
  }
  root.querySelectorAll('[data-heat-metric]').forEach(button => button.addEventListener('click', () => { metric = button.dataset.heatMetric; render(true); }));
  document.addEventListener('modelversionschange', () => render(true), { signal });
  signal?.addEventListener('abort', hideTip, { once: true });
  root.querySelector('[data-heat-figure]').textContent = `FIG. ${String([...document.querySelectorAll('.scene, .reading-section')].indexOf(root) + 1).padStart(2, '0')}`;
  render(animate);
}
