import data from '../data/leaderboard.json';
import { name } from './scores.mjs';
import { simulatorRows, setTaskState, attemptCost } from './explainer-data.mjs';

const root = document.getElementById('explainer');
const config = JSON.parse(document.getElementById('ex-data').textContent);
const $ = id => document.getElementById(id);
const usd = value => '$' + value.toFixed(6);
const number = value => value.toLocaleString('en-US');
const say = message => { $('status').textContent = message; };
const element = (tag, text) => {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
};
const stages = [...root.querySelectorAll('[data-ex-stage]')];
const panels = [...root.querySelectorAll('[data-ex-panel]')];
stages.forEach((button, index) => {
  button.onclick = () => {
    stages.forEach((other, i) => other.setAttribute('aria-pressed', String(index === i)));
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
  };
});
panels.forEach((panel, index) => { panel.hidden = index !== 0; });
root.querySelector('.ex-stage-buttons').hidden = false;

const pipeline = [...root.querySelectorAll('[data-pipeline]')];
pipeline.forEach((button, index) => {
  button.onclick = () => {
    pipeline.forEach(other => other.setAttribute('aria-pressed', String(other === button)));
    $('ex-pipeline-detail').textContent = config.pipeline[index];
    say(config.pipeline[index]);
  };
});
const shapes = [...root.querySelectorAll('[data-ex-shape]')];
shapes.forEach((button, index) => {
  button.onclick = () => {
    shapes.forEach(other => other.setAttribute('aria-pressed', String(other === button)));
    const shape = config.shapes[index];
    $('ex-shape-detail').textContent = `${shape.completions} verified checkout${shape.completions === 1 ? '' : 's'}. ${shape.vus} VU${shape.vus === 1 ? '' : 's'} across ${shape.iterations} iteration${shape.iterations === 1 ? '' : 's'}. Fresh session and token at each login.`;
    say($('ex-shape-detail').textContent);
  };
});

let attempts = data.models[0].per_task_results.map(attempt => ({ ...attempt }));
const taskNodes = new Map([...root.querySelectorAll('[data-ex-task]')]
  .map(node => [node.dataset.exTask, node]));
function renderSimulation(announce = false) {
  const summary = simulatorRows(config.tasks, attempts, data.weights);
  $('ex-score').textContent = summary.overall_score.toFixed(2);
  $('ex-weight').textContent = summary.measured_weight;
  $('ex-sim-meta').textContent = `${new Set(attempts.map(attempt => attempt.task_id)).size}/${config.tasks.length} tasks run · ${attempts.filter(attempt => attempt.passed).length}/${attempts.length} attempts pass. ${summary.measured_weight ? 'Only measured categories contribute.' : 'Nothing measured yet; zero is a placeholder, not a failed run.'}`;
  $('ex-categories').replaceChildren(...Object.entries(data.weights).map(([category, weight]) => {
    const values = attempts.filter(attempt => attempt.category === category);
    const passed = values.filter(attempt => attempt.passed).length;
    const points = values.length ? summary.category_scores[category] * weight / summary.measured_weight : 0;
    const row = document.createElement('div');
    row.append(element('dt', category), element('dd', `${weight} weight · ${values.length ? `${passed}/${values.length} pass · ${summary.category_scores[category].toFixed(2)}% · ${points.toFixed(2)} points` : 'Not run · excluded'}`));
    return row;
  }));
  summary.rows.forEach(row => {
    const node = taskNodes.get(row.id);
    node.querySelectorAll('[data-ex-state]').forEach(button =>
      button.setAttribute('aria-pressed', String(button.dataset.exState === row.state)));
    node.querySelector('[data-ex-points]').textContent = row.state === 'not_run'
      ? 'Not run · excluded from scoring'
      : `${row.points.toFixed(2)} points earned / ${row.maximum.toFixed(2)} possible${row.state === 'mixed' ? ' · mixed repeated results' : ''}`;
  });
  if (announce) say(`Simulated overall ${summary.overall_score.toFixed(2)}. ${summary.measured_weight} measured weight.`);
}
config.tasks.forEach(task => {
  taskNodes.get(task.id).querySelectorAll('[data-ex-state]').forEach(button => {
    button.onclick = () => {
      attempts = setTaskState(attempts, task, button.dataset.exState);
      renderSimulation(true);
    };
  });
});
$('ex-model').onchange = () => {
  attempts = data.models[Number($('ex-model').value)].per_task_results.map(attempt => ({ ...attempt }));
  renderSimulation(true);
};
root.querySelectorAll('[data-ex-preset]').forEach(button => {
  button.onclick = () => {
    attempts = config.tasks.reduce((values, task) => setTaskState(values, task, button.dataset.exPreset), []);
    renderSimulation(true);
  };
});
renderSimulation();

function renderCost() {
  const model = data.models[Number($('ex-cost-model').value)];
  const attempt = model.cost_attempts[Number($('ex-cost-attempt').value)];
  if (!attempt) {
    $('ex-cost-parts').replaceChildren();
    $('ex-cost-total').textContent = 'No published attempt usage.';
    return;
  }
  const cost = attemptCost(attempt);
  $('ex-cost-parts').replaceChildren(...cost.parts.map(part => {
    const article = document.createElement('article');
    article.append(element('h4', part.label), element('strong', `${number(part.tokens)} tokens`),
      element('p', `$${part.rate} / million`), element('p', usd(part.cost)));
    return article;
  }));
  $('ex-cost-total').textContent = `Attempt estimate: ${usd(cost.total)}`;
  $('ex-model-cost').textContent = `${name(model.model)} · whole published run: ${usd(model.cost)} · ${number(model.tokens.input)} input + ${number(model.tokens.output)} output tokens. ${model.cost_note ?? 'Individual attempts can have different price snapshots.'}`;
}
$('ex-cost-model').onchange = () => {
  const model = data.models[Number($('ex-cost-model').value)];
  $('ex-cost-attempt').replaceChildren(...model.cost_attempts.map((attempt, index) => {
    const option = element('option', `${index + 1}. ${attempt.task_id}`);
    option.value = index;
    return option;
  }));
  renderCost();
};
$('ex-cost-attempt').onchange = renderCost;
renderCost();
