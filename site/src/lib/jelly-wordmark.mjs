export function sampleDots(data, width, height, cell, limeWidth) {
  if (data.length !== width * height * 4) throw new RangeError('Invalid pixel buffer dimensions');
  const dots = [];
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (data[(y * width + x) * 4 + 3] > 110) {
      const hx = x * cell, hy = y * cell;
      dots.push({ hx, hy, x: hx, y: hy, vx: 0, vy: 0, lime: x < limeWidth });
    }
  }
  return dots;
}

export function springStep(dots, source, cell, idle = false) {
  const radius = cell * (idle ? 8 : 11), gain = idle ? .45 : 1;
  let energy = 0;
  for (const dot of dots) {
    const dx = dot.x - (source?.x ?? -1e4), dy = dot.y - (source?.y ?? -1e4);
    const distance = Math.hypot(dx, dy);
    if (source && distance < radius) {
      const force = (1 - distance / radius) ** 2 * gain;
      dot.vx += (dx * .06 + source.vx * .35) * force;
      dot.vy += (dy * .06 + source.vy * .35) * force;
    }
    dot.vx = (dot.vx + (dot.hx - dot.x) * .045) * .86;
    dot.vy = (dot.vy + (dot.hy - dot.y) * .045) * .86;
    dot.x += dot.vx;
    dot.y += dot.vy;
    energy += Math.abs(dot.vx) + Math.abs(dot.vy) + Math.abs(dot.hx - dot.x) + Math.abs(dot.hy - dot.y);
  }
  return energy;
}

export function idlePointer(width, height, seconds) {
  return {
    x: width * (.5 + .48 * Math.sin(seconds * .45)),
    y: height * (.5 + .35 * Math.sin(seconds * .9 + 1.3)),
    vx: width * .48 * .45 * Math.cos(seconds * .45) / 60,
    vy: height * .35 * .9 * Math.cos(seconds * .9 + 1.3) / 60,
  };
}

export function mountJellyWordmark(host) {
  const canvas = host.querySelector('canvas'), ctx = canvas.getContext('2d');
  if (!ctx) return () => {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const events = new AbortController();
  const on = (target, event, handler, options = {}) =>
    target.addEventListener(event, handler, { ...options, signal: events.signal });
  let dots = [], cell = 8, width = 0, height = 0, frame = 0;
  let visible = false, suspended = false, disposed = false, epoch = performance.now();
  let lime = '', ink = '';
  const pointer = { x: -1e4, y: -1e4, vx: 0, vy: 0, active: false };
  const running = () => !disposed && visible && !suspended && !document.hidden && !reduced.matches;
  function draw() {
    ctx.clearRect(0, 0, width, height);
    for (const isLime of [true, false]) {
      ctx.fillStyle = isLime ? lime : ink;
      for (const dot of dots) {
        if (dot.lime !== isLime) continue;
        const stretch = Math.min(Math.hypot(dot.x - dot.hx, dot.y - dot.hy) / 40, .6);
        const size = (cell - 1.5) * (1 - stretch * .5);
        ctx.fillRect(dot.x + (cell - size) / 2, dot.y + (cell - size) / 2, size, size);
      }
    }
  }
  function reset() {
    for (const dot of dots) {
      dot.x = dot.hx;
      dot.y = dot.hy;
      dot.vx = dot.vy = 0;
    }
    draw();
  }
  function animate(now) {
    frame = 0;
    if (!running()) return;
    springStep(dots, pointer.active ? pointer : idlePointer(width, height, (now - epoch) / 1000), cell, !pointer.active);
    pointer.vx *= .5;
    pointer.vy *= .5;
    draw();
    frame = requestAnimationFrame(animate);
  }
  function sync() {
    if (running()) {
      if (!frame && dots.length) frame = requestAnimationFrame(animate);
    } else {
      cancelAnimationFrame(frame);
      frame = 0;
      pointer.active = false;
      if (reduced.matches) reset();
    }
  }
  function build() {
    if (disposed) return;
    width = host.clientWidth;
    height = host.clientHeight;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const styles = getComputedStyle(host);
    lime = styles.getPropertyValue('--lime').trim();
    ink = styles.getPropertyValue('--ink').trim();
    cell = Math.max(5, Math.round(width / 150));
    if (!width || !height) { dots = []; return; }
    const offscreen = document.createElement('canvas');
    offscreen.width = Math.ceil(width / cell);
    offscreen.height = Math.ceil(height / cell);
    const sample = offscreen.getContext('2d');
    let size = offscreen.height * .95;
    const square = () => { sample.font = `400 ${size}px "Geist Pixel Square"`; };
    const grid = () => { sample.font = `400 ${size}px "Geist Pixel Grid"`; };
    square();
    let limeWidth = sample.measureText('P95').width;
    grid();
    const textWidth = limeWidth + sample.measureText(' Bench').width;
    if (textWidth > offscreen.width * .98) {
      size *= offscreen.width * .98 / textWidth;
    }
    square();
    limeWidth = sample.measureText('P95').width;
    grid();
    const gap = sample.measureText(' ').width;
    sample.textBaseline = 'middle';
    sample.fillStyle = '#fff';
    square();
    sample.fillText('P95', 0, offscreen.height / 2);
    grid();
    sample.fillText('Bench', limeWidth + gap, offscreen.height / 2);
    dots = sampleDots(sample.getImageData(0, 0, offscreen.width, offscreen.height).data,
      offscreen.width, offscreen.height, cell, limeWidth);
    draw();
    sync();
  }
  function leave() {
    pointer.active = false;
    pointer.x = pointer.y = -1e4;
    pointer.vx = pointer.vy = 0;
    epoch = performance.now();
    sync();
  }
  on(host, 'pointermove', event => {
    if (!running()) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    if (pointer.active) { pointer.vx = x - pointer.x; pointer.vy = y - pointer.y; }
    pointer.x = x;
    pointer.y = y;
    pointer.active = true;
    sync();
  }, { passive: true });
  on(host, 'pointerleave', leave);
  on(host, 'pointercancel', leave);
  on(host, 'pointerup', event => { if (event.pointerType !== 'mouse') leave(); });
  on(document, 'visibilitychange', sync);
  on(reduced, 'change', sync);
  on(window, 'pagehide', event => {
    if (event.persisted) { suspended = true; sync(); }
    else cleanup();
  });
  on(window, 'pageshow', () => { suspended = false; sync(); });
  on(document, 'astro:before-swap', () => cleanup());
  const intersection = new IntersectionObserver(entries => {
    visible = entries.some(entry => entry.isIntersecting);
    sync();
  });
  const resize = new ResizeObserver(build);
  intersection.observe(host);
  resize.observe(host);
  Promise.all([
    document.fonts.load('400 64px "Geist Pixel Square"'),
    document.fonts.load('400 64px "Geist Pixel Grid"'),
  ]).then(build).catch(build);
  function cleanup() {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(frame);
    frame = 0;
    events.abort();
    intersection.disconnect();
    resize.disconnect();
  }
  return cleanup;
}
