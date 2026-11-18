"use strict";
import DATA from "../data/leaderboard.json";
import { observeScenes } from "./scene-observer.mjs";
import { scrollWithinSection } from "./section-scroll.mjs";
import {
  name as modelName,
  modelSlug,
  contributions,
  costFrontier,
  bestWithin,
  replayScore,
  replayToolLayout,
  replayToolCounts,
  replaySampleLabel,
  budgetScale,
  selectModelVersions,
} from "./scores.mjs";
const weights = DATA.weights;
let showingEarlier = new URLSearchParams(location.search).get("versions") === "all";
let models = selectModelVersions(
  DATA.models,
  showingEarlier,
);
const $ = (id) => document.getElementById(id),
  reduced = matchMedia("(prefers-reduced-motion: reduce)");
let categories = Object.keys(weights).filter((c) =>
  models.some((m) => m.category_scores[c] !== undefined),
);
const colors = {
  authoring: "#6b2a7a",
  correlation: "#c4416a",
  workload: "#ff7a3d",
  analysis: "#ffc94a",
  concepts: "#a587e8",
  repair: "#2a1a4a",
  migration: "#75688b",
};
const name = (m) => modelName(m.model),
  cap = (s) => s[0].toUpperCase() + s.slice(1),
  score = (n) => (Number.isInteger(n) ? String(n) : n.toFixed(2));
const money = (n) => "$" + n.toFixed(6),
  fmt = (n) => n.toLocaleString("en-US");
let activeWeight = categories.reduce((s, c) => s + weights[c], 0);
const escape = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const versionMarkup = (m) =>
  '<span class="version-badge">' +
  m.version_status +
  "</span>" +
  (m.newer_model
    ? '<small class="newer-tag">newer: ' +
      escape(modelName(m.newer_model)) +
      "</small>"
    : "") +
  (m.discontinued ? ' · <span class="version-badge">Discontinued</span>' : "");
const scenes = [...document.querySelectorAll(".scene, .reading-section")],
  sceneNames = [
    "LIVE RUN",
    "FLAME STACK",
    "VALUE FRONTIER",
    "MODEL FOCUS",
    "SCENARIOS",
    "TOOLS",
    "METHODOLOGY",
  ];
let currentScene = 0,
  selected = 0,
  zoom = null,
  transition = null;
const announce = (s) => {
  $("status").textContent = s;
  clearTimeout(announce.timer);
  announce.timer = setTimeout(() => ($("status").textContent = ""), 3500);
};
function change(update, after = () => {}) {
  if (transition) transition.skipTransition();
  if (!reduced.matches && document.startViewTransition) {
    const t = document.startViewTransition(() => {
      if (transition === t) update();
    });
    t.ready.catch(() => {});
    transition = t;
    t.finished
      .catch(() => {})
      .finally(() => {
        if (transition === t) {
          transition = null;
          after();
        }
      });
  } else {
    update();
    after();
  }
}
function goScene(index, focus = true, instant = false) {
  index = (index + scenes.length) % scenes.length;
  if (index !== currentScene) {
    clearTimeout(announce.timer);
    $("status").textContent = "";
  }
  currentScene = index;
  scenes[index].scrollIntoView({
    behavior: instant || reduced.matches ? "instant" : "smooth",
    block: "start",
  });
  if (focus)
    scenes[index].querySelector("h1,h2").focus({ preventScroll: true });
}
document
  .querySelectorAll("[data-scene]")
  .forEach((b) => (b.onclick = () => goScene(Number(b.dataset.scene))));
$("next-scene").onclick = () => goScene(currentScene + 1);
document.querySelector(".wordmark").onclick = (e) => {
  e.preventDefault();
  goScene(0);
};
document.querySelectorAll('a[href="#method"],a[href="#tools"],a[href="#scenarios"]').forEach(
  (a) =>
    (a.onclick = (e) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      if (a.href !== location.href) history.replaceState(null, "", a.href);
      goScene(scenes.findIndex(scene => '#' + scene.id === a.hash));
    }),
);
const scenarioButtons = [...document.querySelectorAll('[data-scenario-filter]')];
scenarioButtons.forEach(button => {
  button.onclick = () => {
    scenarioButtons.forEach(filter => filter.setAttribute('aria-pressed', String(filter === button)));
    const cards = [...document.querySelectorAll('[data-scenario-tool]')];
    cards.forEach(card => {
      card.hidden = button.dataset.scenarioFilter !== 'all' && card.dataset.scenarioTool !== button.dataset.scenarioFilter;
    });
    const visible = cards.filter(card => !card.hidden).length;
    $('scenario-count').textContent = `Showing ${visible} of ${cards.length} scenarios`;
    $('scenario-empty').hidden = visible > 0;
    announce(`${visible} scenarios shown. ${button.textContent.trim()} filter.`);
  };
});
observeScenes(scenes, (scene) => {
  currentScene = scenes.indexOf(scene);
  document
    .querySelectorAll("[data-scene]")
    .forEach((b, i) =>
      b.setAttribute("aria-current", String(i === currentScene)),
    );
  $("scene-label").textContent =
    "0" + (currentScene + 1) + " / " + sceneNames[currentScene];
  document.title = "P95 Bench / " + cap(sceneNames[currentScene].toLowerCase());
  if (!scene.classList.contains("in-view")) {
    scene.classList.add("points-entering");
    setTimeout(() => scene.classList.remove("points-entering"), 1800);
  }
  scene.classList.add("in-view");
});
if (
  !CSS.supports("animation-timeline: view()") ||
  !CSS.supports("animation-range: 0% 100%")
)
  document.documentElement.classList.add("scroll-fallback");
function dismissFullscreenFailure() {
  clearTimeout(fullscreenFailure.timer);
  if ($("fullscreen-message").contains(document.activeElement))
    $("fullscreen").focus({ preventScroll: true });
  $("fullscreen-message").hidden = true;
}
$("dismiss-fullscreen-message").onclick = dismissFullscreenFailure;
function fullscreenFailure(message) {
  clearTimeout(fullscreenFailure.timer);
  $("fullscreen-message-text").textContent = message;
  $("fullscreen-message").hidden = false;
  announce(message);
  fullscreenFailure.timer = setTimeout(dismissFullscreenFailure, 4000);
}
async function fullscreen() {
  dismissFullscreenFailure();
  if (!document.fullscreenEnabled) {
    fullscreenFailure(
      "Fullscreen is unavailable in this browser. The scenes still fill the window.",
    );
    return;
  }
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch {
    fullscreenFailure(
      "Fullscreen was blocked by the browser. Try the fullscreen button.",
    );
  }
}
$("fullscreen").onclick = fullscreen;
document.addEventListener("fullscreenchange", () => {
  $("fullscreen").setAttribute(
    "aria-label",
    document.fullscreenElement ? "Exit fullscreen" : "Enter fullscreen",
  );
  $("fullscreen").querySelector(".fullscreen-label").textContent =
    document.fullscreenElement ? " Exit fullscreen" : " Fullscreen";
});
document.addEventListener("keydown", (e) => {
  if (
    e.altKey ||
    e.ctrlKey ||
    e.metaKey ||
    e.isComposing ||
    e.target.closest("input,select,textarea,[contenteditable=true]")
  )
    return;
  if (/^[1-4]$/.test(e.key)) {
    e.preventDefault();
    goScene(Number(e.key) - 1);
  } else if (e.key.toLowerCase() === "f") {
    e.preventDefault();
    fullscreen();
  } else if (e.key === "PageDown" || e.key === "PageUp") {
    if (scrollWithinSection(scenes[currentScene].getBoundingClientRect(), window.innerHeight,
      e.key === "PageUp")) return;
    e.preventDefault();
    goScene(currentScene + (e.key === "PageDown" ? 1 : -1));
  } else if (
    currentScene === 0 &&
    ["ArrowLeft", "ArrowRight"].includes(e.key)
  ) {
    e.preventDefault();
    const row = e.target.closest(".lane"),
      i = row ? Number(row.dataset.index) : selected;
    openModel(
      (i + (e.key === "ArrowRight" ? 1 : -1) + models.length) % models.length,
      row,
    );
  } else if (
    currentScene === 3 &&
    ["ArrowLeft", "ArrowRight"].includes(e.key)
  ) {
    e.preventDefault();
    openModel(
      (selected + (e.key === "ArrowRight" ? 1 : -1) + models.length) %
        models.length,
    );
  }
});
const laneNodes = new Map(),
  arrived = [],
  schedule = [];
let frame = 0,
  paused = false,
  finished = false,
  elapsed = 0,
  lastTime = null,
  replayAnimations = [];
function buildLanes() {
  const { taskIds, rows, groups } = replayToolLayout(models);
  const sampleCount = taskIds.length;
  $("scene-1").style.setProperty("--sample-columns", groups
    .map(group => `repeat(${group.count}, minmax(0, 1fr))`).join(" var(--group-gap) ") || "none");
  $("tool-heads").innerHTML = groups.map(group =>
    '<span style="grid-column:' + group.column + '/span ' + group.count + '">' +
    '<span class="tool-label">' + escape(group.label) + '</span><span class="group-size"> · ' +
    group.count + '</span></span>').join("");
  $("task-heads").innerHTML = groups.map(group => taskIds
    .slice(group.offset, group.offset + group.count).map((id, index) =>
      '<span style="grid-column:' + (group.column + index) + '" title="' +
      escape(id + ' · ' + group.label) + '">' +
      escape(id.replaceAll("-", "").slice(0, 4).toUpperCase()) + '</span>').join(""))
    .join("");
  $("lanes").replaceChildren();
  $("lane-descriptions").replaceChildren();
  laneNodes.clear();
  arrived.length = 0;
  schedule.length = 0;
  models.forEach((m, i) => {
    arrived.push(new Set());
    const row = document.createElement("div");
    row.className = "lane" + (m.discontinued ? " is-discontinued" : "");
    row.dataset.index = i;
    row.style.viewTransitionName = "lane-" + i;
    const counts = replayToolCounts(m, groups);
    const labels = taskIds.map((id, column) => {
      const task = rows[i][column] === null ? null : m.per_task_results[rows[i][column]];
      const group = groups.find(group => column >= group.offset && column < group.offset + group.count);
      return replaySampleLabel(task, id, group.label);
    });
    const description = document.createElement("p");
    description.id = "lane-description-" + i;
    description.textContent = counts.map(count => count.text).join(" · ") + ". " + labels.join("; ");
    $("lane-descriptions").append(description);
    row.innerHTML =
      '<span class="lane-index">' +
      String(i + 1).padStart(2, "0") +
      '</span><button class="lane-open" aria-label="' + escape(name(m) +
        ', inspect all ' + m.per_task_results.length + ' published task results' +
        (m.per_task_results.length < sampleCount ? ', remaining tasks not run' : '')) +
      '" aria-describedby="' + description.id + '"><span class="lane-title">' +
      escape(name(m)) +
      '</span><span class="lane-provider">' +
      escape(m.provider) +
      " · " +
      versionMarkup(m) +
      '</span></button><span class="track">' +
      groups.map((group, groupIndex) => '<span class="sample-group" role="img" aria-label="' +
        escape(counts[groupIndex].text + ': ' + labels.slice(group.offset, group.offset + group.count).join('; ')) +
        '" style="grid-column:' + group.column + '/span ' + group.count + ';--group-count:' + group.count + '">' +
        rows[i].slice(group.offset, group.offset + group.count).map((j, index) => {
          const label = labels[group.offset + index];
          if (j === null) return '<span class="sample unrun" title="' + escape(label) + '"></span>';
          return '<span class="sample" data-task="' + j + '" title="' +
            escape(label + ' · ' + m.per_task_results[j].detail) + '"></span>';
        }).join("") + '</span>').join("") +
      '</span><span class="lane-result"><span class="lane-tool-counts">' +
      counts.filter(count => count.tool !== 'none').map(count =>
        '<span' + (count.attempts ? '' : ' class="tool-unrun"') + '>' + escape(count.text) + '</span>')
        .join('<span aria-hidden="true"> · </span>') +
      '</span><span class="lane-score"><span class="score-number">0</span> <small>/100</small></span></span>';
    row.onclick = () => openModel(i, row);
    laneNodes.set(i, row);
    m.per_task_results.forEach((t, j) =>
      schedule.push({
        i,
        j,
        start: 300 + j * 420 + i * 30,
        land: 700 + j * 420 + i * 30,
        sent: false,
        done: false,
      }),
    );
  });
  [...models.keys()]
    .sort((a, b) => name(models[a]).localeCompare(name(models[b])))
    .forEach((i) => $("lanes").append(laneNodes.get(i)));
}
buildLanes();
function partialScore(i) {
  return replayScore(models[i], arrived[i], weights);
}
function updateCounters() {
  let max = 0,
    count = 0;
  models.forEach((m, i) => {
    const n = partialScore(i);
    laneNodes.get(i).querySelector(".score-number").textContent = score(n);
    max = Math.max(max, n);
    count += arrived[i].size;
  });
  $("leading-score").textContent = max.toFixed(2);
  $("sample-count").textContent = count;
}
function land(s) {
  if (s.done) return;
  s.done = true;
  arrived[s.i].add(s.j);
  const t = models[s.i].per_task_results[s.j],
    cell = laneNodes.get(s.i).querySelector('[data-task="' + s.j + '"]');
  cell.classList.add("landed", t.passed ? "pass" : "fail");
  if (!t.passed && !reduced.matches) cell.classList.add("glitch");
}
function send(s) {
  s.sent = true;
  const row = laneNodes.get(s.i),
    track = row.querySelector(".track"),
    target = track.querySelector('[data-task="' + s.j + '"]');
  const p = document.createElement("span");
  p.className =
    "puck" + (models[s.i].per_task_results[s.j].passed ? "" : " fail");
  p.setAttribute("aria-hidden", "true");
  track.append(p);
  const a = p.animate(
    [
      { transform: "translateX(0)", opacity: 0 },
      { opacity: 1, offset: 0.15 },
      { transform: "translateX(" + target.offsetLeft + "px)", opacity: 1 },
    ],
    { duration: 400, easing: "cubic-bezier(.2,.7,.2,1)" },
  );
  a.onfinish = () => p.remove();
  a.oncancel = () => p.remove();
  replayAnimations.push(a);
}
function settle(animate = true) {
  if (finished) return;
  finished = true;
  cancelAnimationFrame(frame);
  schedule.forEach(land);
  replayAnimations.forEach((a) => a.cancel());
  replayAnimations = [];
  updateCounters();
  const update = () =>
    models.forEach((m, i) => {
      $("lanes").append(laneNodes.get(i));
      laneNodes.get(i).querySelector(".lane-index").textContent = String(
        i + 1,
      ).padStart(2, "0");
    });
  if (animate) change(update);
  else update();
  $("run-state").textContent = "ALL SAMPLES LANDED · RANKED BY SCORE";
  $("pause").textContent = "Replay";
  $("finish").disabled = true;
}
function tick(now) {
  if (paused || finished) return;
  if (lastTime !== null) elapsed += Math.min(now - lastTime, 100);
  lastTime = now;
  schedule.forEach((s) => {
    if (elapsed >= s.start && !s.sent) send(s);
    if (elapsed >= s.land && !s.done) land(s);
  });
  updateCounters();
  if (schedule.every((s) => s.done)) settle();
  else frame = requestAnimationFrame(tick);
}
function startReplay() {
  finished = false;
  paused = false;
  elapsed = 0;
  lastTime = null;
  schedule.forEach((s) => {
    s.sent = false;
    s.done = false;
  });
  arrived.forEach((s) => s.clear());
  laneNodes.forEach((row) =>
    row.querySelectorAll(".sample[data-task]").forEach((c) => (c.className = "sample")),
  );
  $("pause").textContent = "Pause";
  $("finish").disabled = false;
  $("run-state").textContent = "REPLAYING PUBLISHED SAMPLES";
  updateCounters();
  if (reduced.matches) settle(false);
  else frame = requestAnimationFrame(tick);
}
$("pause").onclick = () => {
  if (finished) {
    startReplay();
    return;
  }
  paused = !paused;
  $("pause").textContent = paused ? "Resume" : "Pause";
  $("run-state").textContent = paused
    ? "REPLAY PAUSED"
    : "REPLAYING PUBLISHED SAMPLES";
  replayAnimations
    .filter((a) => a.playState === "running" || a.playState === "paused")
    .forEach((a) => (paused ? a.pause() : a.play()));
  if (!paused) {
    lastTime = null;
    frame = requestAnimationFrame(tick);
  } else cancelAnimationFrame(frame);
};
$("finish").onclick = () => settle();
reduced.addEventListener("change", () => {
  if (reduced.matches) settle(false);
});
function stackInfo(m, c) {
  const points =
    contributions(m, weights).find((s) => s.category === c)?.points ?? 0;
  $("stack-info-title").textContent = points.toFixed(2) + " points";
  $("stack-info-detail").textContent =
    name(m) +
    " / " +
    cap(c) +
    " · " +
    score(m.category_scores[c]) +
    "/100 · weight " +
    weights[c] +
    ". Click to isolate this category.";
}
function renderFlames() {
  $("zoom-path").textContent = zoom
    ? "/ " + cap(zoom)
    : "/ Weighted contribution";
  const rows = models.map((m, i) => {
    const cats = zoom ? [zoom] : categories;
    const parts = contributions(m, weights);
    const segments = cats
      .map((c) => {
        const v = zoom
          ? (m.category_scores[c] ?? 0)
          : (parts.find((s) => s.category === c)?.points ?? 0);
        if (v === 0) return "";
        return (
          '<button class="segment" data-model="' +
          i +
          '" data-cat="' +
          c +
          '" style="width:calc(' +
          v +
          "% - 2px);background:" +
          colors[c] +
          '" aria-label="' +
          escape(name(m)) +
          " / " +
          cap(c) +
          " / " +
          score(m.category_scores[c]) +
          " out of 100 / " +
          (parts.find((s) => s.category === c)?.points ?? 0).toFixed(2) +
          ' weighted points. Zoom category."><span>' +
          (v > 7 ? score(v) : "") +
          "</span></button>"
        );
      })
      .join("");
    return (
      '<div class="flame-row' +
      (m.discontinued ? " is-discontinued" : "") +
      '"><button class="model-link" data-open="' +
      i +
      '">' +
      escape(name(m)) +
      ' <span style="color:var(--mute)">↗</span><span class="model-version">' +
      versionMarkup(m) +
      '</span></button><div class="flame-track">' +
      segments +
      '</div><span class="bar-value">' +
      (zoom && m.category_scores[zoom] === undefined
        ? "Not run"
        : score(zoom ? m.category_scores[zoom] : m.overall_score)) +
      "<small> /100</small></span></div>"
    );
  });
  $("flames").innerHTML = rows.join("");
  $("flames")
    .querySelectorAll("[data-open]")
    .forEach((b) => (b.onclick = () => openModel(Number(b.dataset.open), b)));
  $("flames")
    .querySelectorAll("[data-cat]")
    .forEach((b) => {
      b.onpointerenter = () =>
        stackInfo(models[Number(b.dataset.model)], b.dataset.cat);
      b.onfocus = () =>
        stackInfo(models[Number(b.dataset.model)], b.dataset.cat);
      b.onclick = () => zoomCategory(b.dataset.cat);
    });
}
function zoomCategory(c) {
  change(
    () => {
      zoom = c;
      renderFlames();
      $("stack-info-title").textContent = c
        ? cap(c)
        : activeWeight + " active points";
      $("stack-info-detail").textContent = c
        ? "Category view. All models share a 100-point scale. Select a model name to inspect its tasks."
        : "Scores are normalized over measured categories. Unmeasured categories never count as zero.";
    },
    () => $("zoom-out").focus({ preventScroll: true }),
  );
}
$("zoom-out").onclick = () => zoomCategory(null);
function renderCategoryLegend() {
  $("category-legend").innerHTML = categories
    .map(
      (c) =>
        '<button data-category="' +
        c +
        '"><i style="background:' +
        colors[c] +
        '"></i>' +
        cap(c) +
        " · " +
        weights[c] +
        "</button>",
    )
    .join("");
  $("category-legend")
    .querySelectorAll("button")
    .forEach((b) => (b.onclick = () => zoomCategory(b.dataset.category)));
}
renderCategoryLegend();
renderFlames();
let scale = budgetScale(models);
const budgetFor = (v) => scale.toCost(Number(v));
let budget = models[0]?.cost ?? scale.min;
$("budget").value = Math.ceil(scale.toPosition(budget));
budget = budgetFor($("budget").value);
let frontier = costFrontier(models);
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, text) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  if (text !== undefined) el.textContent = text;
  return el;
}
function renderChart() {
  const minCost = scale.min,
    maxCost = scale.max;
  const logMin = Math.log10(minCost),
    logMax = Math.log10(maxCost);
  const svg = $("scatter"),
    w = svg.clientWidth,
    h = svg.clientHeight,
    left = 47,
    right = w - 23,
    top = 27,
    bottom = h - 30;
  svg.setAttribute("viewBox", "0 0 " + w + " " + h);
  svg
    .querySelectorAll(":scope > :not(title):not(desc)")
    .forEach((n) => n.remove());
  const x = (v) =>
      left + ((Math.log10(v) - logMin) / (logMax - logMin)) * (right - left),
    y = (v) => bottom - (v / 100) * (bottom - top);
  for (const n of [0, 25, 50, 75, 100]) {
    svg.append(
      svgEl("line", {
        x1: left,
        y1: y(n),
        x2: right,
        y2: y(n),
        class: "gridline",
      }),
    );
    svg.append(
      svgEl(
        "text",
        { x: left - 12, y: y(n) + 3, class: "axis", "text-anchor": "end" },
        n,
      ),
    );
  }
  const ticks = w < 500 ? [0.01, 0.05, 0.2] : [0.01, 0.02, 0.05, 0.1, 0.2];
  for (const n of ticks)
    if (n >= minCost && n <= maxCost)
      svg.append(
        svgEl(
          "text",
          { x: x(n), y: bottom + 21, class: "axis", "text-anchor": "middle" },
          "$" + n.toFixed(2),
        ),
      );
  svg.append(svgEl("text", { x: left, y: 10, class: "axis" }, "SCORE ↑"));
  svg.append(
    svgEl("polyline", {
      points: frontier
        .map((m) => x(m.cost) + "," + y(m.overall_score))
        .join(" "),
      class: "frontier-path",
      pathLength: 1,
    }),
  );
  svg.append(
    svgEl("line", {
      x1: x(budget),
      x2: x(budget),
      y1: top - 7,
      y2: bottom + 4,
      class: "budget-line",
    }),
  );
  const best = bestWithin(models, budget);
  models.forEach((m, i) => {
    const g = svgEl("g", {
      class:
        "point" +
        (m.cost > budget ? " above" : "") +
        (m.discontinued ? " is-discontinued" : "") +
        (m === best ? " winner" : ""),
      tabindex: 0,
      role: "button",
      "aria-label":
        name(m) +
        ", " +
        m.version_status +
        (m.discontinued ? ", Discontinued" : "") +
        ", score " +
        score(m.overall_score) +
        ", estimated cost " +
        money(m.cost) +
        ". Open model focus.",
      style: "--delay:" + i * 45 + "ms",
    });
    g.append(
      svgEl("circle", {
        cx: x(m.cost),
        cy: y(m.overall_score),
        r: m === best ? 8 : 6,
      }),
    );
    const dx = m.cost > Math.sqrt(minCost * maxCost) ? -11 : 11;
    g.append(
      svgEl(
        "text",
        {
          x: x(m.cost) + dx,
          y: y(m.overall_score) + 3,
          "text-anchor": dx < 0 ? "end" : "start",
        },
        String(i + 1).padStart(2, "0"),
      ),
    );
    g.onclick = () => openModel(i, g);
    g.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openModel(i, g);
      }
    };
    svg.append(g);
  });
}
function updateBudget() {
  const best = bestWithin(models, budget);
  $("budget-value").textContent = "$" + budget.toFixed(3);
  $("budget").setAttribute(
    "aria-valuetext",
    budget.toFixed(3) +
      " US dollars; " +
      (best
        ? "best within budget " +
          name(best) +
          ", score " +
          score(best.overall_score)
        : "no published model within budget"),
  );
  $("winner-name").textContent = best ? name(best) : "No run within budget";
  $("winner-meta").textContent = best
    ? score(best.overall_score) +
      " / 100  ·  " +
      money(best.cost) +
      " per run  ·  " +
      best.per_task_results.filter((t) => t.passed).length +
      "/" +
      best.per_task_results.length +
      " pass"
    : "Increase the budget to include a published run.";
  $("chart-models")
    .querySelectorAll("button")
    .forEach((b, i) => {
      b.classList.toggle("above", models[i].cost > budget);
      b.classList.toggle("winner", models[i] === best);
    });
  renderChart();
}
function renderModelControls() {
  $("chart-models").innerHTML = models
    .map(
      (m, i) =>
        '<button class="chart-chip' +
        (m.discontinued ? " is-discontinued" : "") +
        '" data-model="' +
        i +
        '"><span>' +
        String(i + 1).padStart(2, "0") +
        "</span>" +
        escape(name(m)) +
        "</button>",
    )
    .join("");
  $("chart-models")
    .querySelectorAll("button")
    .forEach((b) => (b.onclick = () => openModel(Number(b.dataset.model), b)));
  $("chart-table").innerHTML = models
    .map(
      (m, i) =>
        "<tr" +
        (m.discontinued ? ' class="is-discontinued"' : "") +
        "><td>" +
        (i + 1) +
        '</td><th scope="row">' +
        escape(name(m)) +
        '<span class="model-version">' +
        versionMarkup(m) +
        "</span></th><td>" +
        score(m.overall_score) +
        "</td><td>" +
        money(m.cost) +
        "</td></tr>",
    )
    .join("");
  $("model-picker").innerHTML = models
    .map(
      (m, i) =>
        '<option value="' +
        i +
        '">' +
        escape(name(m)) +
        " · " +
        m.version_status +
        (m.newer_model ? " · newer: " + escape(modelName(m.newer_model)) : "") +
        (m.discontinued ? " · Discontinued" : "") +
        "</option>",
    )
    .join("");
}
renderModelControls();
$("budget").oninput = () => {
  budget = budgetFor($("budget").value);
  updateBudget();
};
$("budget").onchange = () =>
  announce($("winner-name").textContent + " is the current budget result.");
new ResizeObserver(() => renderChart()).observe($("scatter"));
updateBudget();
function renderProfile() {
  const m = models[selected];
  $("model-picker").value = selected;
  $("focus-name").textContent = name(m);
  $("focus-provider").textContent = m.provider.toUpperCase() + " / " + m.model;
  $("focus-version").innerHTML =
    "Rank " + (selected + 1) + " · " + versionMarkup(m);
  $("scene-4").classList.toggle("is-discontinued", m.discontinued);
  $("focus-score").textContent = score(m.overall_score);
  $("focus-passed").textContent =
    m.per_task_results.filter((t) => t.passed).length +
    " / " +
    m.per_task_results.length +
    " PASS";
  $("task-grid").innerHTML = m.per_task_results
    .map(
      (t, i) =>
        '<article class="task ' +
        (t.passed ? "pass" : "fail") +
        '"><div class="task-top"><span>' +
        String(i + 1).padStart(2, "0") +
        " / " +
        escape(t.tool === "none" ? t.category : t.tool) +
        '</span><span class="task-state">' +
        (t.passed ? "✓ PASS" : "× FAIL") +
        "</span></div><strong>" +
        escape(t.task_id) +
        "</strong><p>" +
        escape(t.detail) +
        "</p></article>",
    )
    .join("");
  $("focus-cost").textContent = money(m.cost);
  $("focus-tokens").textContent = fmt(m.tokens.input + m.tokens.output);
  $("focus-token-detail").textContent =
    fmt(m.tokens.input) + " in / " + fmt(m.tokens.output) + " out";
  $("focus-latency").textContent = m.latency.toFixed(2) + "s";
  $("focus-latency-detail").textContent =
    "Total across " + m.per_task_results.length + " requests";
  $("focus-provenance").replaceChildren();
  const slug = modelSlug(m.model);
  const link = document.createElement("a");
  link.href =
    "https://github.com/QAInsights/p95-bench/tree/main/results/published/" +
    slug;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = "Inspect published artifacts ↗";
  $("focus-provenance").append(
    document.createTextNode(
      "RUN " +
        (m.run_date ?? "not recorded") +
        " · HARNESS " +
        (m.harness_commit?.slice(0, 12) ?? "not recorded") +
        " · PUBLISHED ATTEMPTS · ",
    ),
    link,
  );
  $("cat-keys").innerHTML = Object.keys(weights)
    .map(
      (c) =>
        '<div class="cat-key"><i style="background:' +
        colors[c] +
        (m.category_scores[c] === undefined ? ";opacity:.4" : "") +
        '"></i>' +
        cap(c) +
        "<b>" +
        (m.category_scores[c] === undefined
          ? "Not run"
          : score(m.category_scores[c])) +
        "</b></div>",
    )
    .join("");
  const ring = $("ring");
  ring.replaceChildren();
  const r = 103,
    circ = 2 * Math.PI * r;
  let offset = 0;
  const parts = contributions(m, weights);
  $("ring-caption").textContent =
    parts.length + " measured categories. Every point traceable.";
  const measured = parts.reduce((s, p) => s + weights[p.category], 0);
  ring.append(
    svgEl("circle", {
      cx: 130,
      cy: 130,
      r,
      fill: "none",
      stroke: "#282331",
      "stroke-width": 16,
    }),
  );
  parts.forEach(({ category: c, score: value }) => {
    const slot = (weights[c] / measured) * circ,
      len = Math.max(0, (slot * value) / 100 - 5);
    const circle = svgEl("circle", {
      cx: 130,
      cy: 130,
      r,
      fill: "none",
      stroke: colors[c],
      "stroke-width": 16,
      "stroke-dasharray": len + " " + (circ - len),
      "stroke-dashoffset": -offset,
    });
    circle.append(
      svgEl(
        "title",
        {},
        cap(c) + ": " + score(m.category_scores[c]) + " out of 100",
      ),
    );
    ring.append(circle);
    offset += slot;
  });
  ring.setAttribute(
    "aria-label",
    name(m) +
      " category scores. " +
      parts
        .map(({ category, score: value }) => cap(category) + " " + score(value))
        .join(", ") +
      ". Unmeasured categories are listed as not run.",
  );
}
function openModel(i, source) {
  const heading = $("focus-name");
  if (source) {
    settle(false);
    source.style.viewTransitionName = "model-focus";
    heading.style.viewTransitionName = "none";
  }
  const update = () => {
    selected = i;
    renderProfile();
    if (source) {
      source.style.viewTransitionName = "none";
      heading.style.viewTransitionName = "model-focus";
    }
    goScene(3, false, true);
  };
  change(update, () => {
    heading.focus({ preventScroll: true });
    heading.style.viewTransitionName = "none";
    if (source)
      source.style.viewTransitionName = source.classList.contains("lane")
        ? "lane-" + source.dataset.index
        : "none";
  });
}
$("previous-model").onclick = () =>
  openModel((selected - 1 + models.length) % models.length);
$("next-model").onclick = () => openModel((selected + 1) % models.length);
$("model-picker").onchange = () => openModel(Number($("model-picker").value));
renderProfile();
function updateVisibleCounts() {
  const tasks = new Set(
    models.flatMap((m) => m.per_task_results.map((t) => t.task_id)),
  ).size;
  const samples = models.reduce((sum, m) => sum + m.per_task_results.length, 0);
  document
    .querySelectorAll("[data-model-count]")
    .forEach((el) => (el.textContent = models.length));
  document
    .querySelectorAll("[data-task-count]")
    .forEach((el) => (el.textContent = tasks));
  document
    .querySelectorAll("[data-sample-total]")
    .forEach((el) => (el.textContent = samples));
  document
    .querySelectorAll("[data-active-weight]")
    .forEach((el) => (el.textContent = activeWeight));
  document
    .querySelectorAll("[data-active-count]")
    .forEach((el) => (el.textContent = categories.length));
  const ids = new Set(models.map((m) => m.model));
  document
    .querySelectorAll("[data-tool-model]")
    .forEach((el) => (el.hidden = !ids.has(el.dataset.toolModel)));
  const date = models
    .map((m) => m.run_date)
    .filter(Boolean)
    .sort()
    .at(-1);
  $("published-date").textContent = date
    ? new Date(date)
        .toLocaleDateString("en-GB", {
          day: "2-digit",
          month: "short",
          year: "numeric",
          timeZone: "UTC",
        })
        .toUpperCase()
    : "PUBLISHED RUNS";
  $("not-run").innerHTML = Object.keys(weights)
    .filter((c) => !categories.includes(c))
    .map(
      (c) =>
        '<div class="empty-frame"><b>' +
        cap(c) +
        " / weight " +
        weights[c] +
        "</b><span>Not yet benchmarked</span></div>",
    )
    .join("");
}
function applyVersions() {
  const previous = models[selected]?.model,
    wasFinished = finished,
    wasPaused = paused;
  cancelAnimationFrame(frame);
  replayAnimations.forEach((a) => a.cancel());
  replayAnimations = [];
  transition?.skipTransition();
  transition = null;
  const all = new URLSearchParams(location.search).get("versions") === "all";
  showingEarlier = all;
  $("show-versions").checked = all;
  models = selectModelVersions(DATA.models, all);
  selected = Math.max(
    0,
    models.findIndex((m) => m.model === previous),
  );
  categories = Object.keys(weights).filter((c) =>
    models.some((m) => c in m.category_scores),
  );
  activeWeight = categories.reduce((sum, c) => sum + weights[c], 0);
  zoom = null;
  scale = budgetScale(models);
  frontier = costFrontier(models);
  budget = Math.max(scale.min, Math.min(scale.max, budget));
  $("budget").value = Math.ceil(scale.toPosition(budget));
  budget = budgetFor($("budget").value);
  buildLanes();
  renderFlames();
  renderCategoryLegend();
  $("stack-info-title").textContent = activeWeight + " active points";
  $("stack-info-detail").textContent =
    "Scores are normalized over measured categories. Unmeasured categories never count as zero.";
  renderModelControls();
  renderProfile();
  updateBudget();
  updateVisibleCounts();
  startReplay();
  if (wasFinished) settle(false);
  else if (wasPaused) $("pause").click();
  scenes[currentScene].scrollIntoView({ behavior: "instant", block: "start" });
  announce(
    models.length +
      " published runs shown. " +
      (all ? "Earlier versions included." : "Latest versions only."),
  );
}
$("show-versions").checked =
  new URLSearchParams(location.search).get("versions") === "all";
$("show-versions").onchange = () => {
  const url = new URL(location.href);
  if ($("show-versions").checked) url.searchParams.set("versions", "all");
  else url.searchParams.delete("versions");
  history.pushState(null, "", url);
  applyVersions();
};
addEventListener("popstate", () => {
  const all = new URLSearchParams(location.search).get("versions") === "all";
  if (all !== showingEarlier) applyVersions();
});
if (!("switch" in HTMLInputElement.prototype))
  $("show-versions").classList.add("switch-fallback");
updateVisibleCounts();
startReplay();
if (location.hash.match(/^#scene-[1-4]$/))
  setTimeout(() => goScene(Number(location.hash.at(-1)) - 1, false, true), 50);
