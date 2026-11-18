import assert from 'node:assert/strict';
import { test } from 'node:test';
import { observeScenes } from '../src/lib/scene-observer.mjs';

function fixture(height) {
  const observers = [], listeners = new Map();
  const viewport = {
    innerHeight: height,
    IntersectionObserver: class {
      targets = [];
      disconnected = false;
      constructor(callback, options) {
        Object.assign(this, { callback, options });
        observers.push(this);
      }
      observe(target) { this.targets.push(target); }
      disconnect() { this.disconnected = true; }
    },
    addEventListener: (name, callback) => listeners.set(name, callback),
    removeEventListener: (name) => listeners.delete(name),
  };
  return { viewport, observers, listeners };
}

test('scene activation uses a one-pixel viewport midline independent of section ratio', () => {
  for (const height of [400, 812, 900]) {
    const { viewport, observers } = fixture(height);
    const scenes = [{ id: 'short' }, { id: 'tall' }], active = [];
    observeScenes(scenes, scene => active.push(scene.id), viewport);
    const observer = observers[0], middle = Math.floor(height / 2);
    assert.deepEqual(observer.options, {
      root: null, rootMargin: `-${middle}px 0px -${height - middle - 1}px 0px`, threshold: 0,
    });
    assert.deepEqual(observer.targets, scenes);
    observer.callback([{ target: scenes[0], isIntersecting: false, intersectionRatio: 0 }]);
    assert.deepEqual(active, []);
    observer.callback([
      { target: scenes[0], isIntersecting: false, intersectionRatio: 0 },
      { target: scenes[1], isIntersecting: true, intersectionRatio: .0001 },
    ]);
    assert.deepEqual(active, ['tall']);
  }
});

test('observer rebuilds pixel margins on resize and disconnects on cleanup', () => {
  const { viewport, observers, listeners } = fixture(900);
  const scenes = [{}];
  const stop = observeScenes(scenes, () => {}, viewport);
  viewport.innerHeight = 667;
  listeners.get('resize')();
  assert.equal(observers[0].disconnected, true);
  assert.equal(observers[1].options.rootMargin, '-333px 0px -333px 0px');
  assert.deepEqual(observers[1].targets, scenes);
  stop();
  assert.equal(observers[1].disconnected, true);
  assert.equal(listeners.has('resize'), false);
});

test('empty sections and empty observer entries do not activate a scene', () => {
  const { viewport, observers } = fixture(812);
  const stop = observeScenes([], () => assert.fail('No section to activate'), viewport);
  observers[0].callback([]);
  assert.deepEqual(observers[0].targets, []);
  stop();
});
