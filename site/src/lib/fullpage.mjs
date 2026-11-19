"use strict";
import { resolvePack, creditGradient } from './packs.mjs';
import { mountPackScenes } from './pack-scenes.mjs';
import { renderToolRankings } from './tool-rankings.mjs';
import { taskCategoryGroups, taskHeading, replayTaskColumns } from './task-headings.mjs';
import { buildScenarios } from './scenarios.mjs';
import { observeScenes } from "./scene-observer.mjs";
import { scrollWithinSection, regionNeedsScrollKey } from "./section-scroll.mjs";
import { sectionMetadata, sectionShortcut, sectionNumber } from "./section-navigation.mjs";
import { leastSquares, scatterScale, scatterValue, scatterLabels } from "./scatter-data.mjs";
import { providerName } from "./provider-names.mjs";
import { rollNumber, rollText, announceRolling } from "./rolling-numbers.mjs";
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
  selectModelVersions,
  attemptFraction,
  shortTaskLabel,
} from "./scores.mjs";
const PACKS = JSON.parse(document.getElementById('pack-data').textContent);
let pack = resolvePack(new URLSearchParams(location.search).get('pack'), PACKS);
let DATA = PACKS[pack], weights = DATA.weights;
let showingEarlier = new URLSearchParams(location.search).get("versions") === "all";
let models = selectModelVersions(
  DATA.models,
  showingEarlier,
);
const $ = (id) => document.getElementById(id),
  reduced = matchMedia("(prefers-reduced-motion: reduce)");
$('show-versions').checked = showingEarlier;
mountPackScenes(pack);
const initialURL = new URL(location.href);
if (initialURL.searchParams.has('pack')) {
  initialURL.searchParams.set('pack', pack);
  history.replaceState(null, '', initialURL);
}
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
  score = (n) => Number.isFinite(n) ? (Number.isInteger(n) ? String(n) : n.toFixed(2)) : '—';
const money = (n) => Number.isFinite(n) ? "$" + n.toFixed(6) : '—',
  fmt = (n) => Number.isFinite(n) ? n.toLocaleString("en-US") : '—';
let activeWeight = categories.reduce((s, c) => s + weights[c], 0);
const escape = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const versionMarkup = (m, showCoverage = true) =>
  '<span class="version-badge">' +
  m.version_status +
  "</span>" +
  (m.newer_model
    ? '<small class="newer-tag">newer: ' +
      escape(modelName(m.newer_model)) +
      "</small>"
    : "") +
  (m.discontinued ? ' · <span class="version-badge">Discontinued</span>' : "") +
  (showCoverage && m.coverage?.partial ? ' · <span class="partial-run">Partial · ' + escape(m.coverage.label) + '</span>' : "") +
  (m.not_run ? `<span class="pack-not-run">Not run on ${pack}</span>` : pack === 'v2'
    ? `<span class="pack-quality">Quality ${score(m.quality_score)} · Composite ${score(m.overall_score)} (95/5)</span>` : '');
const scenes = [...document.querySelectorAll(".scene, .reading-section")],
  sections = sectionMetadata(scenes),
  sceneNames = sections.map(section => section.label.toUpperCase());
document.querySelector('.rail').replaceChildren(...sections.map(section => {
  const link = document.createElement('a');
  link.href = '#' + section.id;
  link.dataset.scene = section.index;
  link.setAttribute('aria-label', section.number + ': ' + section.label);
  link.setAttribute('aria-current', String(section.index === 0));
  const number = document.createElement('span'), marker = document.createElement('i');
  number.textContent = section.number;
  marker.setAttribute('aria-hidden', 'true');
  link.append(number, marker);
  const eyebrow = scenes[section.index].querySelector('.eyebrow b, .eyebrow');
  const label = eyebrow.querySelector('b') ?? eyebrow;
  label.textContent = label.textContent.replace(/^\s*\d+\s*\//, section.number + ' /');
  return link;
}));
document.querySelector('[data-section-shortcuts]').textContent = sections.length >= 10 ? '1–9 · 0 = 10' : '1 to ' + sections.length;
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
  .forEach((b) => (b.onclick = (e) => {
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    if (b.href !== location.href) history.replaceState(null, "", b.href);
    goScene(Number(b.dataset.scene));
  }));
$("next-scene").onclick = () => goScene(currentScene + 1);
document.querySelectorAll('a[href="#method"],a[href="#tools"],a[href="#scenarios"],a[href="#explainer"]').forEach(
  (a) =>
    (a.onclick = (e) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      if (a.href !== location.href) history.replaceState(null, "", a.href);
      goScene(scenes.findIndex(scene => '#' + scene.id === a.hash));
    }),
);
function setupScenarioControls() {
const scenarioButtons = [...document.querySelectorAll('[data-scenario-filter]')];
scenarioButtons.forEach(button => {
  button.onclick = () => {
    scenarioButtons.forEach(filter => filter.setAttribute('aria-pressed', String(filter === button)));
    const cards = [...document.querySelectorAll('[data-scenario-tool]')];
    cards.forEach(card => {
      card.hidden = button.dataset.scenarioFilter !== 'all' && card.dataset.scenarioTool !== button.dataset.scenarioFilter;
    });
    const visible = cards.filter(card => !card.hidden).length;
    rollText($('scenario-count'), `Showing ${visible} of ${cards.length} scenarios`, { animate: true, max: cards.length });
    $('scenario-empty').hidden = visible > 0;
    announceRolling(`${visible} scenarios shown. ${button.textContent.trim()} filter.`, 'scenarios');
  };
});
}
setupScenarioControls();
function renderScenarioResults() {
  for (const scenario of buildScenarios(DATA.tasks, models, true)) {
    const card = [...document.querySelectorAll('[data-scenario-id]')].find(node => node.dataset.scenarioId === scenario.id);
    if (!card) continue;
    rollText(card.querySelector('strong'), scenario.passLabel, { key: 'scenario:' + scenario.id, animate: true, max: models.length * 100 });
    card.querySelector('[data-scenario-missing]').textContent = scenario.notRun + ' not run';
  }
}
observeScenes(scenes, (scene) => {
  currentScene = scenes.indexOf(scene);
  document
    .querySelectorAll("[data-scene]")
    .forEach((b, i) =>
      b.setAttribute("aria-current", String(i === currentScene)),
    );
  $("scene-label").textContent =
    sectionNumber(currentScene + 1) + " / " + sceneNames[currentScene];
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
    e.defaultPrevented ||
    e.altKey ||
    e.ctrlKey ||
    e.metaKey ||
    e.isComposing ||
    e.target.closest('input,select,textarea,[contenteditable=true]') ||
    regionNeedsScrollKey(e.target.closest('[role="region"][tabindex]'), e.key)
  )
    return;
  const shortcut = sectionShortcut(e.key, scenes.length);
  if (shortcut !== null) {
    e.preventDefault();
    goScene(shortcut);
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
    scenes[currentScene].id === 'scene-4' &&
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
  const tasksById = new Map(DATA.tasks.map(task => [task.id, task]));
  const heading = id => taskHeading(tasksById.get(id) ?? { id });
  const sampleCount = taskIds.length;
  $('scene-1').classList.toggle('replay-v2', pack === 'v2');
  $('scene-1').style.setProperty('--replay-columns', sampleCount);
  $('replay-task-description').hidden = pack !== 'v2';
  $('replay-task-description').textContent = 'Focus or select a numbered column to inspect its task. Scroll horizontally for all columns.';
  $("scene-1").style.setProperty("--sample-columns", groups
    .map(group => `repeat(${group.count}, minmax(0, 1fr))`).join(" var(--group-gap) ") || "none");
  $("tool-heads").innerHTML = groups.map(group =>
    '<span style="grid-column:' + group.column + '/span ' + group.count + '">' +
    '<span class="tool-label">' + escape(group.label) + '</span><span class="group-size"> · ' +
    group.count + '</span>' + (pack === 'v2' ? '<span class="task-category-list">' +
      taskCategoryGroups(taskIds.slice(group.offset, group.offset + group.count).map(id => tasksById.get(id) ?? { id }))
        .map(category => '<span>' + escape(category.label) + ' <b>' + category.count + '</b></span>').join('') +
      '</span>' : '') + '</span>').join("");
  $("task-heads").innerHTML = pack === 'v2' ? replayTaskColumns(taskIds, groups).map(column =>
    '<button type="button" class="replay-task-heading" style="grid-column:' + column.column +
    '" aria-controls="replay-task-description" aria-label="' +
    escape('Column ' + column.ordinal + ': ' + heading(column.id) + ' · ' + column.tool) +
    '" title="' + escape(heading(column.id) + ' · ' + column.tool) + '">' + column.ordinal + '</button>').join('') : groups.map(group => taskIds
    .slice(group.offset, group.offset + group.count).map((id, index) =>
      '<span style="grid-column:' + (group.column + index) + '" title="' +
      escape(heading(id) + ' · ' + group.label) + '">' +
      escape(shortTaskLabel(id)) + '</span>').join(""))
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
      return replaySampleLabel(task, id, group.label, heading(id));
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
      escape(providerName(m.provider)) +
      " · " +
      versionMarkup(m, false) +
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
      (m.coverage?.partial ? '<span class="partial-run">Partial · ' + escape(m.coverage.label) + '</span>' : '') +
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
const inspectReplayTask = event => {
  const heading = event.target.closest('.replay-task-heading');
  if (heading) $('replay-task-description').textContent = heading.title;
};
$('task-heads').addEventListener('focusin', inspectReplayTask);
$('task-heads').addEventListener('click', inspectReplayTask);
$('task-heads').addEventListener('pointerover', inspectReplayTask);
function partialScore(i) {
  return replayScore(models[i], arrived[i], weights);
}
function updateCounters() {
  let max = 0,
    count = 0;
  models.forEach((m, i) => {
    const n = partialScore(i);
    const node = laneNodes.get(i).querySelector('.score-number');
    if (m.not_run) node.textContent = '—';
    else rollNumber(node, n, { key: 'lane:' + m.model, from: 0, digits: 2, max: 100 });
    max = Math.max(max, n);
    count += arrived[i].size;
  });
  rollNumber($("leading-score"), max, { from: 0, digits: 2, max: 100 });
  rollNumber($("sample-count"), count, { from: 0, max: DATA.models.reduce((sum, model) => sum + model.per_task_results.length, 0) });
  const leader = models.find(model => !model.not_run);
  $('leading-score').hidden = !leader;
  $('leading-empty').hidden = Boolean(leader);
  $('leading-quality').hidden = pack !== 'v2';
  if (pack === 'v2' && leader) rollText($('leading-quality'), `Published quality ${leader.quality_score.toFixed(2)} · composite 95/5`, { key: 'hero:quality', animate: true, max: 100 });
  else if (pack === 'v2') $('leading-quality').replaceChildren('No published runs in this view');
}
function land(s) {
  if (s.done) return;
  s.done = true;
  arrived[s.i].add(s.j);
  const t = models[s.i].per_task_results[s.j],
    cell = laneNodes.get(s.i).querySelector('[data-task="' + s.j + '"]');
  const fraction = attemptFraction(t) ?? 0;
  cell.classList.add('landed', fraction === 1 ? 'pass' : fraction > 0 ? 'partial' : 'fail');
  if (fraction > 0 && fraction < 1) cell.style.background = creditGradient(fraction);
  if (fraction === 0 && !reduced.matches) cell.classList.add('glitch');
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
  $("run-state").textContent = models.some(model => !model.not_run)
    ? 'ALL SAMPLES LANDED · RANKED BY SCORE' : 'NO PUBLISHED RUNS IN THIS VIEW';
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
  $("stack-info-title").textContent = (points * (pack === 'v2' ? .95 : 1)).toFixed(2) + " points";
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
function renderFlames(animate = false) {
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
          : (parts.find((s) => s.category === c)?.points ?? 0) * (pack === 'v2' ? .95 : 1);
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
          ((parts.find((s) => s.category === c)?.points ?? 0) * (pack === 'v2' ? .95 : 1)).toFixed(2) +
          ' weighted points. Zoom category."><span>' +
          (v > 7 ? score(v) : "") +
          "</span></button>"
        );
      })
      .join("") + (!zoom && pack === 'v2' && !m.not_run
        ? `<span class="segment efficiency-segment" style="width:${m.efficiency_score * .05}%" role="img" aria-label="Quality-gated efficiency: ${(m.efficiency_score * .05).toFixed(2)} composite points"></span>` : '');
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
      '</div><span class="bar-value"><span data-flame-score="' + i + '">' +
      (m.not_run || zoom && m.category_scores[zoom] === undefined
        ? "Not run"
        : score(zoom ? m.category_scores[zoom] : m.overall_score)) +
      "</span><small> /100</small></span></div>"
    );
  });
  $("flames").innerHTML = rows.join("");
  $('flames').querySelectorAll('[data-flame-score]').forEach(node => {
    const model = models[Number(node.dataset.flameScore)], value = zoom ? model.category_scores[zoom] : model.overall_score;
    if (Number.isFinite(value)) rollNumber(node, value, { key: 'flame:' + model.model, from: 0, digits: 2, max: 100, animate });
  });
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
      renderFlames(true);
      announceRolling(`${c ? cap(c) : 'Overall'} Flame Stack scores updated for ${models.length} models. Highest score ${Math.max(...models.map(model => c ? model.category_scores[c] ?? 0 : model.overall_score)).toFixed(2)} out of 100.`, 'flames');
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
let chartAxis = 'cost';
let scale = scatterScale(models, 'cost');
const budgetFor = (v) => scale.value(Math.max(0, Math.min(1000, Number(v))) / 1000);
let budget = models[0]?.cost ?? scale.min;
$("budget").value = Math.ceil(scale.position(budget) * 1000);
budget = budgetFor($("budget").value);
let frontier = costFrontier(models);
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, text) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  if (text !== undefined) el.textContent = text;
  return el;
}
function renderChart(animate = false) {
  const domain = scatterScale(models, chartAxis);
  const costView = chartAxis === 'cost';
  const svg = $("scatter"),
    w = svg.clientWidth,
    h = svg.clientHeight,
    left = 47,
    right = w - 23,
    top = 27,
    bottom = h - 30;
  if (w < 80 || h < 80) return;
  const existing = new Map([...svg.querySelectorAll('.point')].map(node => [node.dataset.modelId, node]));
  const bounds = svg.getBoundingClientRect();
  const before = new Map(animate && !reduced.matches ? [...existing.values()].map(node => {
    const rect = node.querySelector('circle').getBoundingClientRect();
    return [node, { x: rect.left + rect.width / 2 - bounds.left, y: rect.top + rect.height / 2 - bounds.top }];
  }) : []);
  existing.forEach(node => node.getAnimations().forEach(animation => animation.cancel()));
  svg.setAttribute("viewBox", "0 0 " + w + " " + h);
  svg
    .querySelectorAll(":scope > :not(title):not(desc):not(.point)")
    .forEach((n) => n.remove());
  const x = (v) =>
      left + domain.position(v) * (right - left),
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
  const tickFormat = value => chartAxis === 'cost' ? '$' + Number(value.toPrecision(2)).toLocaleString('en-US')
    : Number(value.toPrecision(2)).toLocaleString('en-US');
  const ticks = w < 500 ? [0, .5, 1] : [0, .25, .5, .75, 1];
  for (const ratio of ticks) {
    const n = domain.value(ratio);
      svg.append(
        svgEl(
          "text",
          { x: x(n), y: bottom + 21, class: "axis", "text-anchor": "middle" },
          tickFormat(n),
        ),
      );
  }
  svg.append(svgEl("text", { x: left, y: 10, class: "axis" }, "SCORE ↑"));
  if (costView) svg.append(
    svgEl("polyline", {
      points: frontier
        .map((m) => x(m.cost) + "," + y(m.overall_score))
        .join(" "),
      class: "frontier-path",
      pathLength: 1,
    }),
  );
  if (costView) svg.append(
    svgEl("line", {
      x1: x(budget),
      x2: x(budget),
      y1: top - 7,
      y2: bottom + 4,
      class: "budget-line",
    }),
  );
  const samples = models.filter(model => Number.isFinite(scatterValue(model, chartAxis)) && scatterValue(model, chartAxis) >= 0 && Number.isFinite(model.overall_score));
  const trend = leastSquares(samples.map(model => ({ x: domain.transform(scatterValue(model, chartAxis)), y: model.overall_score })));
  const predict = value => trend ? trend.intercept + trend.slope * domain.transform(value) : null;
  if (trend) {
    const defs = svgEl('defs', {}), clip = svgEl('clipPath', { id: 'scatter-trend-clip' });
    clip.append(svgEl('rect', { x: left, y: top, width: right - left, height: bottom - top }));
    defs.append(clip); svg.append(defs);
    svg.append(svgEl('line', { x1: left, x2: right, y1: y(predict(domain.min)), y2: y(predict(domain.max)), class: 'regression-line', 'clip-path': 'url(#scatter-trend-clip)' }));
  }
  const axisLabel = scatterLabels[chartAxis] + (domain.log ? ' · log10 scale' : ' · linear scale');
  $('chart-title').textContent = 'Published score versus ' + axisLabel;
  $('chart-desc').textContent = `${axisLabel}. Higher score is better. Dashed Flame line is least squares against ${domain.log ? 'log10 of' : ''} the horizontal values. Diamonds mark models above the fitted trend. Select a numbered model to inspect it.`;
  $('scatter-fit').textContent = axisLabel + (trend ? ` · dashed OLS slope ${trend.slope.toPrecision(3)} score/${domain.log ? 'log10(x)' : 'x'} · R² ${trend.r2 === null ? 'n/a (constant scores)' : trend.r2.toFixed(3)}` : ' · slope/R² n/a (need two distinct x values)');
  rollNumber($('scatter-source'), samples.length, { max: DATA.models.length });
  $('scatter-figure').textContent = 'FIG. ' + sectionNumber(scenes.findIndex(scene => scene.id === 'scene-3') + 1);
  document.querySelector('#scene-3 .value-winner').hidden = !costView;
  document.querySelector('#scene-3 .budget-slider-wrap').hidden = !costView;
  document.querySelector('#scene-3 .budget-label').hidden = !costView;
  $('budget').setAttribute('aria-label', `Maximum estimated run cost in USD, ${scale.log ? 'logarithmic' : 'linear'} scale`);
  const best = bestWithin(models, budget);
  const kept = new Set();
  models.forEach((m, i) => {
    if (!samples.includes(m)) return;
    const value = scatterValue(m, chartAxis), px = x(value), py = y(m.overall_score);
    const beats = trend && m.overall_score > predict(value) + 1e-8;
    const attrs = {
      class:
        "point" +
        (costView && m.cost > budget ? " above" : "") +
        (m.discontinued ? " is-discontinued" : "") +
        (costView && m === best ? " winner" : ""),
      tabindex: 0,
      role: "button",
      "aria-label":
        name(m) +
        ', ' + providerName(m.provider) +
        ", " +
        m.version_status +
        (m.discontinued ? ", Discontinued" : "") +
        ", score " +
        score(m.overall_score) +
        ", estimated cost " +
        money(m.cost) +
        ', ' + scatterLabels[chartAxis] + ' ' + tickFormat(value) + (beats ? ', beats trend' : '') +
        ". Open model focus.",
      style: "--delay:" + i * 45 + "ms",
    };
    const g = existing.get(m.model) ?? svgEl('g', {});
    kept.add(g);
    for (const [key, val] of Object.entries(attrs)) g.setAttribute(key, val);
    g.dataset.modelId = m.model;
    g.dataset.beatsTrend = String(Boolean(beats));
    if (!g.firstElementChild) g.append(svgEl('circle', { cx: 0, cy: 0 }), svgEl('text', {}), svgEl('path', { d: 'M0 -15 l3 3 -3 3 -3 -3 Z', class: 'trend-marker' }));
    g.querySelector('circle').setAttribute('r', costView && m === best ? 8 : 6);
    const dx = domain.position(value) > .5 ? -11 : 11;
    const text = g.querySelector('text');
    text.setAttribute('x', dx); text.setAttribute('y', 3); text.setAttribute('text-anchor', dx < 0 ? 'end' : 'start');
    text.textContent = sectionNumber(i + 1);
    g.querySelector('.trend-marker').toggleAttribute('hidden', !beats);
    g.setAttribute('transform', `translate(${px} ${py})`);
    const from = before.get(g);
    if (from && !reduced.matches && g.animate) g.animate([{ transform: `translate(${from.x}px, ${from.y}px)` }, { transform: `translate(${px}px, ${py}px)` }], { duration: 450, easing: 'cubic-bezier(.2,.8,.2,1)' });
    g.onclick = () => openModel(i, g);
    g.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openModel(i, g);
      }
    };
    if (g.parentElement === svg && svg.moveBefore) svg.moveBefore(g, null);
    else {
      const focused = document.activeElement === g;
      svg.append(g);
      if (focused) g.focus({ preventScroll: true });
    }
  });
  existing.forEach(node => { if (!kept.has(node)) node.remove(); });
}
function updateBudget(animate = false) {
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
      b.classList.toggle("above", chartAxis === 'cost' && models[i].cost > budget);
      b.classList.toggle("winner", chartAxis === 'cost' && models[i] === best);
    });
  renderChart(animate);
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
        (m.not_run ? `<small class="pack-not-run">Not run on ${pack}</small>` : '') +
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
        '</td><td>' + escape(providerName(m.provider)) + '</td><td>' +
        fmt(m.tokens.output) + '</td><td>' + (Number.isFinite(m.latency) ? m.latency.toFixed(2) : '—') +
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
        (m.not_run ? ` · Not run on ${pack}` : '') +
        "</option>",
    )
    .join("");
}
renderModelControls();
document.querySelectorAll('[data-scatter-axis]').forEach(button => button.addEventListener('click', () => {
  chartAxis = button.dataset.scatterAxis;
  document.querySelectorAll('[data-scatter-axis]').forEach(control => control.setAttribute('aria-pressed', String(control === button)));
  updateBudget(true);
  announceRolling(scatterLabels[chartAxis] + ' axis. ' + models.filter(model => !model.not_run).length + ' published runs. ' + $('scatter-fit').textContent, 'scatter');
}));
$("budget").oninput = () => {
  budget = budgetFor($("budget").value);
  updateBudget();
};
$("budget").onchange = () =>
  announce($("winner-name").textContent + " is the current budget result.");
new ResizeObserver(() => renderChart([...$("scatter").querySelectorAll('.point')].some(node => node.getAnimations().some(animation => animation.playState === 'running')))).observe($("scatter"));
reduced.addEventListener('change', () => { if (reduced.matches) renderChart(); });
updateBudget();
function renderProfile() {
  const m = models[selected];
  $("model-picker").value = selected;
  $("focus-name").textContent = name(m);
  $("focus-provider").textContent = providerName(m.provider) + " / " + m.model;
  $("focus-version").innerHTML =
    "Rank " + (selected + 1) + " · " + versionMarkup(m);
  $("scene-4").classList.toggle("is-discontinued", m.discontinued);
  $("focus-score").textContent = score(m.overall_score);
  $("focus-passed").textContent = m.not_run ? `Not run on ${pack}` : pack === 'v2'
    ? `Quality ${score(m.quality_score)} · Composite ${score(m.overall_score)} (95/5)` :
    m.per_task_results.filter((t) => t.passed).length +
    " / " +
    m.per_task_results.length +
    " PASS";
  $("task-grid").innerHTML = m.per_task_results
    .map(
      (t, i) =>
        '<article class="task ' +
        (attemptFraction(t) === 1 ? 'pass' : attemptFraction(t) > 0 ? 'partial' : 'fail') +
        '"><div class="task-top"><span>' +
        String(i + 1).padStart(2, "0") +
        " / " +
        escape(t.tool === "none" ? t.category : t.tool) +
        '</span><span class="task-state">' +
        (Number.isFinite(t.score) ? `${(t.score * 100).toFixed(1)}% CREDIT` : t.passed ? "✓ PASS" : "× FAIL") +
        "</span></div><strong>" +
        escape(t.task_id) +
        "</strong><p>" +
        escape(t.detail) +
        "</p></article>",
    )
    .join("");
  if (m.not_run) $('task-grid').innerHTML = `<p class="pack-not-run">Not run on ${pack}. No task scores, usage or artifacts are available.</p>`;
  $("task-grid").scrollTop = 0;
  $("focus-cost").textContent = money(m.cost);
  $("focus-cost-note").textContent = m.cost_note ?? "USD · list-price estimate";
  $("focus-tokens").textContent = m.not_run ? '—' : fmt(m.tokens.input + m.tokens.output);
  $("focus-token-detail").textContent =
    fmt(m.tokens.input) + " in / " + fmt(m.tokens.output) + " out";
  $("focus-latency").textContent = Number.isFinite(m.latency) ? m.latency.toFixed(2) + 's' : '—';
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
  if (m.not_run) $('focus-provenance').replaceChildren(`Not run on ${pack}. No published artifacts.`);
  else if (pack === 'v2') {
    link.href = 'https://github.com/QAInsights/p95-bench/tree/main/results/published/';
    link.textContent = 'Browse published artifacts · ' + DATA.pack_id + ' ↗';
  }
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
    pack === 'v2' && !m.not_run ? `Quality ${score(m.quality_score)} across ${parts.length} categories. Composite ${score(m.overall_score)} includes quality-gated efficiency.`
      : parts.length + " measured categories. Every point traceable.";
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
    goScene(scenes.findIndex(scene => scene.id === 'scene-4'), false, true);
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
function updateVisibleCounts(animate = false) {
  const tasks = new Set(
    models.flatMap((m) => m.per_task_results.map((t) => t.task_id)),
  ).size;
  const samples = models.reduce((sum, m) => sum + m.per_task_results.length, 0);
  const runs = models.filter(model => !model.not_run).length;
  document.querySelectorAll('[data-run-count]').forEach(el => rollNumber(el, runs, { animate, max: DATA.models.length }));
  document.querySelectorAll('[data-pack]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.pack === pack)));
  document.querySelectorAll('[data-pack-model]').forEach(row => { row.hidden = !models.some(model => model.model === row.dataset.packModel); });
  $('leading-label').textContent = pack === 'v2' ? 'LEADING COMPOSITE · 95/5' : 'LEADING WEIGHTED SCORE';
  $('method-pack').textContent = pack === 'v2'
    ? `Showing ${DATA.pack_id} · 50-task design, ${DATA.tasks.length} public tasks. V2 is the default landing view; select v1 for the legacy board. Composite = 95% quality + 5% quality-gated efficiency. Checkpoints total 100 possible points per task; prerequisites gate dependent credit. Repetitions are averaged; first publication defaults to one. Pinned workers, independent server audit evidence and workload capacity preflights verify execution. Private task details are never published.`
    : 'Showing p95-v1 · 15 tasks · binary pass/fail. Weighted category pass rates retain their original scoring. V2 is the default landing view.';
  document.querySelectorAll('[data-v1-method]').forEach(node => { node.hidden = pack !== 'v1'; });
  $('method-weights').innerHTML = Object.entries(weights).map(([category, weight]) =>
    `<span><i style="background:${colors[category]}"></i>${escape(category)}<b>${weight}</b></span>`).join('');
  document
    .querySelectorAll("[data-model-count]")
    .forEach((el) => rollNumber(el, models.length, { animate, max: DATA.models.length }));
  document
    .querySelectorAll("[data-task-count]")
    .forEach((el) => rollNumber(el, pack === 'v2' ? DATA.tasks.length : tasks, { animate, max: DATA.tasks.length }));
  document
    .querySelectorAll("[data-sample-total]")
    .forEach((el) => rollNumber(el, samples, { animate, max: DATA.models.reduce((sum, model) => sum + model.per_task_results.length, 0) }));
  document
    .querySelectorAll("[data-active-weight]")
    .forEach((el) => rollNumber(el, activeWeight, { animate, max: 100 }));
  document
    .querySelectorAll("[data-active-count]")
    .forEach((el) => rollNumber(el, categories.length, { animate, max: Object.keys(weights).length }));
  renderToolRankings(document.getElementById('tools'), DATA.models, pack, showingEarlier);
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
  const nextPack = resolvePack(new URLSearchParams(location.search).get('pack'), PACKS);
  const packChanged = nextPack !== pack;
  pack = nextPack;
  DATA = PACKS[pack];
  weights = DATA.weights;
  models = selectModelVersions(DATA.models, all);
  if (packChanged) {
    mountPackScenes(pack, true);
    setupScenarioControls();
  }
  selected = Math.max(
    0,
    models.findIndex((m) => m.model === previous),
  );
  categories = Object.keys(weights).filter((c) =>
    models.some((m) => c in m.category_scores),
  );
  activeWeight = categories.reduce((sum, c) => sum + weights[c], 0);
  zoom = null;
  scale = scatterScale(models, 'cost');
  frontier = costFrontier(models);
  budget = Math.max(scale.min, Math.min(scale.max, budget));
  $("budget").value = Math.ceil(scale.position(budget) * 1000);
  budget = budgetFor($("budget").value);
  buildLanes();
  renderFlames(true);
  renderCategoryLegend();
  $("stack-info-title").textContent = activeWeight + " active points";
  $("stack-info-detail").textContent =
    "Scores are normalized over measured categories. Unmeasured categories never count as zero.";
  renderModelControls();
  renderProfile();
  updateBudget();
  updateVisibleCounts(true);
  if (!packChanged) document.dispatchEvent(new CustomEvent('modelversionschange', { detail: { showEarlier: all } }));
  renderScenarioResults();
  startReplay();
  if (wasFinished) settle(false);
  else if (wasPaused) $("pause").click();
  scenes[currentScene].scrollIntoView({ behavior: "instant", block: "start" });
  announceRolling(
    pack + '. ' + models.filter(model => !model.not_run).length +
      " published runs shown. " +
      (all ? "Earlier versions included." : "Latest versions only.") +
      ` ${models.reduce((sum, model) => sum + model.per_task_results.length, 0)} published attempts. ` +
      (models.some(model => !model.not_run) ? `Highest overall score ${Math.max(...models.map(model => model.overall_score)).toFixed(2)} out of 100.` : 'No published runs in this view.'), 'versions',
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
  const nextPack = resolvePack(new URLSearchParams(location.search).get('pack'), PACKS);
  if (all !== showingEarlier || nextPack !== pack) applyVersions();
});
document.querySelectorAll('[data-pack]').forEach(button => {
  button.onclick = () => {
    const url = new URL(location.href);
    url.searchParams.set('pack', button.dataset.pack);
    history.pushState(null, '', url);
    applyVersions();
  };
});
if (!("switch" in HTMLInputElement.prototype))
  $("show-versions").classList.add("switch-fallback");
updateVisibleCounts();
renderScenarioResults();
startReplay();
const initialSection = scenes.findIndex(scene => '#' + scene.id === location.hash);
if (initialSection >= 0)
  setTimeout(() => goScene(initialSection, false, true), 50);
