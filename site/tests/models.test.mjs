import assert from 'node:assert/strict';
import { test } from 'node:test';
import { modelSlug, name } from '../src/lib/scores.mjs';

test('model display names and artifact slugs preserve exact API IDs separately', () => {
  for (const [model, display, slug] of [
    ['thinkingmachines/Inkling', 'Inkling', 'inkling'],
    ['thinkingmachines/Inkling-Small', 'Inkling-Small', 'inkling-small'],
    ['thinkingmachines/Inkling-2-Small', 'Inkling 2 Small', 'inkling-2-small'],
    ['gpt-6.2-sol', 'GPT-6.2 Sol', 'gpt-6-2-sol'],
    ['gpt-6.2-astra', 'GPT-6.2 Astra', 'gpt-6-2-astra'],
    ['deepseek-flash', 'DeepSeek Flash', 'deepseek-flash'],
    ['claude-opus-5-5', 'Claude Opus 5.5', 'claude-opus-5-5'],
  ]) {
    assert.equal(name(model), display);
    assert.equal(modelSlug(model), slug);
    assert.doesNotMatch(modelSlug(model), /[/\\]/);
  }
  assert.equal(name('vendor/unknown'), 'vendor/unknown');
  assert.equal(modelSlug('vendor/unknown'), 'vendor-unknown');
});
