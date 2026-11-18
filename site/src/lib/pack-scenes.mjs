import { mountUnitMatrix } from './unit-matrix.mjs';
import { mountHeatmap } from './heatmap.mjs';
import { mountExplainer } from './explainer.mjs';

let controller;
export function mountPackScenes(pack, animate = false) {
  controller?.abort();
  controller = new AbortController();
  const template = document.getElementById(`pack-template-${pack}`);
  for (const id of ['unit-matrix', 'heatmap', 'scenarios', 'explainer']) {
    const root = document.getElementById(id), source = template.content.querySelector('#' + id);
    root.replaceChildren(...source.cloneNode(true).childNodes);
    root.classList.toggle('pack-v2', pack === 'v2');
  }
  mountUnitMatrix(document.getElementById('unit-matrix'), { signal: controller.signal, animate });
  mountHeatmap(document.getElementById('heatmap'), { signal: controller.signal, animate });
  if (pack === 'v1') mountExplainer();
}
