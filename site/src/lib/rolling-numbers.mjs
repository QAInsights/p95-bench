import { formatNumber, interpolateNumber, numberDuration } from './number-format.mjs';

const states = new Map(), active = new Set(), messages = new Map();
let frame, timer, motion;
function paint(state) { state.visual.textContent = formatNumber(state.value, state.options); }
function flush() {
  const region = document.getElementById('rolling-status');
  if (region && messages.size) region.textContent = [...messages.values()].join(' ');
  messages.clear();
}
function preference() {
  if (!motion) {
    motion = matchMedia('(prefers-reduced-motion: reduce)');
    motion.addEventListener('change', () => {
      if (!motion.matches) return;
      active.forEach(state => { state.value = state.target; paint(state); state.node.removeAttribute('data-rolling'); });
      active.clear(); cancelAnimationFrame(frame); frame = null;
      clearTimeout(timer); flush();
    });
  }
  return motion.matches;
}
function tick(now) {
  frame = null;
  active.forEach(state => {
    if (!state.node.isConnected) { state.value = state.target; active.delete(state); return; }
    const progress = (now - state.start) / numberDuration;
    state.value = interpolateNumber(state.from, state.target, progress);
    paint(state);
    if (progress >= 1) { active.delete(state); state.node.removeAttribute('data-rolling'); }
  });
  if (active.size) frame = requestAnimationFrame(tick);
}
export function rollNumber(node, value, { key = node.id || node, animate = true, from = value, max = value, ...options } = {}) {
  const final = formatNumber(value, options), reduced = preference();
  let state = states.get(key);
  if (!state) { state = { value: from, target: from }; states.set(key, state); }
  if (state.node !== node) {
    state.node?.removeAttribute('data-rolling');
    state.node = node;
    state.visual = document.createElement('span'); state.visual.className = 'rolling-visual'; state.visual.setAttribute('aria-hidden', 'true');
    state.accessible = document.createElement('span'); state.accessible.className = 'sr';
    node.replaceChildren(state.visual, state.accessible);
  }
  state.options = options;
  node.classList.add('rolling-number');
  state.chars = Math.max(state.chars ?? 0, formatNumber(max, options).length, final.length);
  node.style.setProperty('--number-chars', state.chars);
  node.dataset.rollingTarget = final;
  if (state.accessible.textContent !== final) state.accessible.textContent = final;
  if (!animate || reduced) {
    state.value = state.target = value; active.delete(state); node.removeAttribute('data-rolling');
  } else if (state.target !== value) {
    state.from = state.value; state.target = value; state.start = performance.now();
    if (state.value !== value) { active.add(state); node.setAttribute('data-rolling', ''); }
    else { active.delete(state); node.removeAttribute('data-rolling'); }
  }
  paint(state);
  if (active.size && !frame) frame = requestAnimationFrame(tick);
}
export function rollText(node, text, { key = node.id || node, animate = false, max = 999 } = {}) {
  const pieces = text.split(/(-?\d+(?:\.\d+)?)/g);
  const existing = [...node.querySelectorAll('.rolling-number')].map(number => Number(number.dataset.rollingTarget));
  const previous = existing.length ? existing : (node.textContent.match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);
  node.replaceChildren();
  pieces.forEach((piece, index) => {
    if (index % 2 === 0) node.append(document.createTextNode(piece));
    else {
      const span = document.createElement('span'); node.append(span);
      rollNumber(span, Number(piece), { key: `${key}:${index}`, animate, from: previous[(index - 1) / 2] ?? 0,
        digits: piece.includes('.') ? piece.split('.')[1].length : 0, max });
    }
  });
}
export function announceRolling(message, key = 'context') {
  messages.set(key, message);
  clearTimeout(timer);
  timer = setTimeout(() => preference() ? flush() : requestAnimationFrame(flush), preference() ? 0 : numberDuration);
}
