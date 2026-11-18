import { name, selectModelVersions, toolScore } from './scores.mjs';

export function toolRanking(models, tool, pack, showEarlier = false) {
  return selectModelVersions(models, true).map(model => {
    const value = toolScore(model, tool);
    return {
      ...model,
      tool_score: value,
      tool_label: value === null ? `Not run on ${pack}` : `${Number(value.toFixed(1))}%`,
      hidden: !showEarlier && model.newer_model !== null,
    };
  }).sort((a, b) => (b.tool_score ?? -1) - (a.tool_score ?? -1));
}

export function renderToolRankings(root, models, pack, showEarlier) {
  for (const list of root.querySelectorAll('[data-tool-ranking]')) {
    const rows = new Map([...list.children].map(row => [JSON.stringify([row.dataset.toolProvider, row.dataset.toolModel]), row]));
    list.previousElementSibling.textContent = pack === 'v2' ? 'Mean checkpoint credit' : 'Executable task pass rate';
    for (const model of toolRanking(models, list.dataset.toolRanking, pack, showEarlier)) {
      const key = JSON.stringify([model.provider, model.model]);
      let row = rows.get(key);
      if (!row) {
        row = document.createElement('li');
        row.dataset.toolModel = model.model;
        row.dataset.toolProvider = model.provider;
        const label = document.createElement('span'), version = document.createElement('span');
        version.className = 'model-version';
        label.append(name(model.model), version);
        row.append(label, document.createElement('b'));
      }
      const version = row.querySelector('.model-version');
      version.replaceChildren(model.version_status);
      if (model.newer_model) {
        const newer = document.createElement('small');
        newer.className = 'newer-tag';
        newer.textContent = `newer: ${name(model.newer_model)}`;
        version.append(newer);
      }
      if (model.discontinued) version.append(' · Discontinued');
      row.hidden = model.hidden;
      row.classList.toggle('is-discontinued', model.discontinued);
      const value = row.querySelector('b');
      value.textContent = model.tool_label;
      value.classList.toggle('tool-unrun', model.tool_score === null);
      if (list.moveBefore && row.parentNode === list) list.moveBefore(row, null);
      else list.append(row);
    }
  }
}
