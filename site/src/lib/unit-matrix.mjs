import { unitGroups, unitSummary, passRateLabel } from './unit-matrix-data.mjs';
import { rollNumber, rollText, announceRolling } from './rolling-numbers.mjs';

export function mountUnitMatrix(root) {
  const records = JSON.parse(root.querySelector('#matrix-records').textContent);
  const board = root.querySelector('#matrix-board');
  const squares = new Map([...board.querySelectorAll('[data-unit-id]')].map(node => [node.dataset.unitId, node]));
  const rows = new Map([...root.querySelectorAll('[data-unit-row]')].map(node => [node.dataset.unitRow, node]));
  const tbody = root.querySelector('tbody');
  const tooltip = root.querySelector('#matrix-tooltip');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let mode = 'all', animations = [], described = null, hideTimer;
  const move = (parent, node) => parent.moveBefore && node.isConnected
    ? parent.moveBefore(node, null) : parent.append(node);
  const hideTip = () => {
    clearTimeout(hideTimer);
    tooltip.hidden = true;
    described?.removeAttribute('aria-describedby');
    described = null;
  };
  function showTip(node) {
    hideTip();
    described = node;
    tooltip.textContent = node.getAttribute('aria-label');
    tooltip.hidden = false;
    node.setAttribute('aria-describedby', tooltip.id);
    const rect = node.getBoundingClientRect();
    tooltip.style.left = Math.max(8, Math.min(rect.left, innerWidth - tooltip.offsetWidth - 8)) + 'px';
    tooltip.style.top = Math.max(8, Math.min(rect.bottom + 8, innerHeight - tooltip.offsetHeight - 8)) + 'px';
  }
  const delayHide = () => { hideTimer = setTimeout(hideTip, 120); };
  board.addEventListener('pointerover', event => {
    const node = event.target.closest('[data-unit-id]');
    if (node) showTip(node);
  });
  board.addEventListener('pointerout', delayHide);
  board.addEventListener('focusin', event => {
    if (event.target.matches('[data-unit-id]')) showTip(event.target);
  });
  board.addEventListener('focusout', delayHide);
  board.addEventListener('scroll', () => {
    if (document.activeElement.matches('[data-unit-id]')) showTip(document.activeElement);
    else hideTip();
  });
  tooltip.addEventListener('pointerenter', () => clearTimeout(hideTimer));
  tooltip.addEventListener('pointerleave', hideTip);
  root.addEventListener('keydown', event => { if (event.key === 'Escape') hideTip(); });
  board.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const nodes = [...board.querySelectorAll('[data-unit-id]')];
    if (!nodes.length) return;
    const current = nodes.indexOf(document.activeElement);
    const index = event.key === 'Home' ? 0 : event.key === 'End' ? nodes.length - 1
      : Math.max(0, Math.min(nodes.length - 1, current + (event.key === 'ArrowRight' ? 1 : -1)));
    nodes[index].focus({ preventScroll: true });
    nodes[index].scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
  function render(animate = false) {
    hideTip();
    const visible = records.filter(record => document.getElementById('show-versions').checked || record.latest);
    const before = new Map(animate && !reduced.matches
      ? [...board.querySelectorAll('[data-unit-id]')].map(node => [node, node.getBoundingClientRect()]) : []);
    animations.forEach(animation => animation.cancel());
    animations = [];
    const old = [...board.children];
    const groups = unitGroups(visible, mode);
    board.style.setProperty('--matrix-columns', Math.max(1, groups.length));
    for (const [index, group] of groups.entries()) {
      const section = document.createElement('div'), heading = document.createElement('h3');
      section.className = 'matrix-group';
      section.setAttribute('role', 'group');
      heading.id = `matrix-group-${index}`;
      heading.textContent = group.label;
      const stats = document.createElement('span');
      rollText(stats, `${group.count} squares · ${passRateLabel(group.passRate)}`, { key: `matrix:${mode}:${group.label}`, animate, max: records.length });
      heading.append(stats);
      section.setAttribute('aria-labelledby', heading.id);
      const grid = document.createElement('div');
      grid.className = 'matrix-squares';
      section.append(heading, grid);
      board.append(section);
      for (const record of group.records) {
        const node = squares.get(record.id), row = rows.get(record.id);
        node.hidden = false;
        move(grid, node);
        row.hidden = false;
        row.querySelector('[data-unit-group]').textContent = group.label;
        move(tbody, row);
      }
    }
    const ids = new Set(visible.map(record => record.id));
    for (const [id, node] of squares) if (!ids.has(id)) { node.remove(); rows.get(id).hidden = true; }
    old.forEach(node => node.remove());
    if (!groups.length) board.textContent = 'No published attempts in this view.';
    const after = [...board.querySelectorAll('[data-unit-id]')].map(node => [node, node.getBoundingClientRect()]);
    if (!reduced.matches) for (const [node, rect] of after) {
      const from = before.get(node);
      if (from && node.animate) animations.push(node.animate([
        { transform: `translate(${from.left - rect.left}px, ${from.top - rect.top}px)` },
        { transform: 'none' },
      ], { duration: 420, easing: 'cubic-bezier(.2,.8,.2,1)' }));
    }
    const summary = unitSummary(visible);
    rollNumber(root.querySelector('#matrix-total'), summary.measured, { animate, max: records.length });
    rollNumber(root.querySelector('#matrix-source-count'), summary.measured, { animate, max: records.length });
    rollText(root.querySelector('#matrix-summary'), `${summary.passed} passed · ${summary.failed} failed · ${summary.notRun} not run. ${passRateLabel(summary.passRate)}.`, { animate, max: records.length });
    root.querySelector('#matrix-status').textContent = `Grouped by ${mode}. ${summary.count} squares in ${groups.length} groups. ${passRateLabel(summary.passRate)}.`;
    if (animate) announceRolling(`Matrix grouped by ${mode}. ${summary.count} squares in ${groups.length} groups; ${summary.passed} passed, ${summary.failed} failed, ${summary.notRun} not run. ${passRateLabel(summary.passRate)}.`, 'matrix');
    root.querySelectorAll('[data-matrix-mode]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.matrixMode === mode)));
  }
  root.querySelectorAll('[data-matrix-mode]').forEach(button => button.addEventListener('click', () => {
    mode = button.dataset.matrixMode;
    render(true);
  }));
  document.addEventListener('modelversionschange', () => render(true));
  reduced.addEventListener('change', () => { if (reduced.matches) animations.forEach(animation => animation.cancel()); });
  const number = [...document.querySelectorAll('.scene, .reading-section')].indexOf(root) + 1;
  root.querySelector('[data-matrix-figure]').textContent = `FIG. ${String(number).padStart(2, '0')}`;
  render();
}
