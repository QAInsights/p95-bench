import assert from 'node:assert/strict';
import { test } from 'node:test';
import { scrollWithinSection } from '../src/lib/section-scroll.mjs';

test('long sections retain native paging until the directional edge', () => {
  assert.equal(scrollWithinSection({ height: 3000, top: 0, bottom: 3000 }, 900, false), true);
  assert.equal(scrollWithinSection({ height: 3000, top: 0, bottom: 3000 }, 900, true), false);
  assert.equal(scrollWithinSection({ height: 3000, top: -500, bottom: 2500 }, 900, true), true);
  assert.equal(scrollWithinSection({ height: 3000, top: -2100, bottom: 900 }, 900, false), false);
  assert.equal(scrollWithinSection({ height: 900, top: 0, bottom: 900 }, 900, false), false);
});
