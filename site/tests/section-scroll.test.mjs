import assert from 'node:assert/strict';
import { test } from 'node:test';
import { scrollWithinSection, regionNeedsScrollKey } from '../src/lib/section-scroll.mjs';

test('long sections retain native paging until the directional edge', () => {
  assert.equal(scrollWithinSection({ height: 3000, top: 0, bottom: 3000 }, 900, false), true);
  assert.equal(scrollWithinSection({ height: 3000, top: 0, bottom: 3000 }, 900, true), false);
  assert.equal(scrollWithinSection({ height: 3000, top: -500, bottom: 2500 }, 900, true), true);
  assert.equal(scrollWithinSection({ height: 3000, top: -2100, bottom: 900 }, 900, false), false);
  assert.equal(scrollWithinSection({ height: 900, top: 0, bottom: 900 }, 900, false), false);
});

test('regions only reserve native scrolling keys when their content overflows', () => {
  for (const key of ['PageDown', 'PageUp', ' ', 'ArrowDown', 'ArrowUp', 'Home', 'End']) {
    assert.equal(regionNeedsScrollKey({ clientHeight: 100, scrollHeight: 300 }, key), true);
    assert.equal(regionNeedsScrollKey({ clientHeight: 300, scrollHeight: 300 }, key), false);
    assert.equal(regionNeedsScrollKey(null, key), false);
  }
  for (const key of ['1', '8', 'f', 'F', 'ArrowLeft', 'ArrowRight']) {
    assert.equal(regionNeedsScrollKey({ clientHeight: 100, scrollHeight: 300 }, key), false);
  }
});
