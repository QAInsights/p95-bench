import data from '../data/leaderboard.json';
import { bestWithin, money, name, score } from './scores.mjs';

export async function initialize() {
  const slider = document.querySelector<HTMLInputElement>('#budget')!;
  const value = document.querySelector<HTMLOutputElement>('#budget-value')!;
  const result = document.querySelector<HTMLElement>('#budget-result')!;
  let announcement: ReturnType<typeof setTimeout>;
  function update() {
    const budget = Number(slider.value);
    slider.style.setProperty('--slider-progress', `${budget / Number(slider.max) * 100}%`);
    value.textContent = money(budget);
    slider.setAttribute('aria-valuetext', `${money(budget)} per run`);
    const best = bestWithin(data.models, budget);
    document.querySelectorAll<SVGGElement>('.chart-model').forEach((point) => {
      const model = data.models.find((m) => m.model === point.dataset.model)!;
      point.classList.toggle('selected', model.model === best?.model);
      point.classList.toggle('over-budget', model.cost > budget);
    });
    clearTimeout(announcement);
    announcement = setTimeout(() => {
      const heading = document.createElement('p');
      heading.className = 'pick-name';
      heading.textContent = best ? name(best.model) : 'No model in budget.';
      const detail = document.createElement('p');
      detail.className = 'mono pick-detail';
      detail.textContent = best ? `${score(best.overall_score)} points · ${money(best.cost)} / run`
        : `Start at ${money(Math.min(...data.models.map((m) => m.cost)))} for a measured run.`;
      result.replaceChildren(heading, detail);
    }, 120);
  }
  slider.addEventListener('input', update);
  update();

  if (!Object.hasOwn(HTMLElement.prototype, 'popover')) {
    await import('@oddbird/popover-polyfill');
  }
  const probe = document.createElement('div');
  probe.popover = 'hint';
  if (probe.popover !== 'hint') {
    document.addEventListener('beforetoggle', (event) => {
      const target = event.target as HTMLElement;
      if ((event as ToggleEvent).newState === 'open' && target.getAttribute('popover') === 'hint') {
        target.popover = 'auto';
      }
    }, true);
  }
  document.addEventListener('toggle', (event) => {
    const target = event.target as HTMLElement;
    if (!target.matches('.hint')) return;
    document.querySelectorAll(`[popovertarget="${CSS.escape(target.id)}"]`).forEach((trigger) => {
      trigger.setAttribute('aria-expanded', String((event as ToggleEvent).newState === 'open'));
    });
  }, true);
  if (!('interestForElement' in HTMLButtonElement.prototype)) {
    await import('interestfor');
  }
}
