import assert from 'node:assert/strict';
import { test } from 'node:test';
import { leastSquares, scatterScale, scatterValue } from '../src/lib/scatter-data.mjs';
test('least squares fits known positive/negative trends and exposes variance explained', () => {
  assert.deepEqual(leastSquares([{x:1,y:2},{x:2,y:4},{x:3,y:6}]), {n:3,slope:2,intercept:0,r2:1});
  const negative = leastSquares([{x:1,y:6},{x:2,y:4},{x:3,y:2}]);
  assert.equal(negative.slope,-2);assert.equal(negative.intercept,8);assert.equal(negative.r2,1);
  const noisy=leastSquares([{x:0,y:1},{x:1,y:3},{x:2,y:2}]);
  assert.equal(noisy.slope,.5);assert.equal(noisy.r2,.25);
});
test('regression handles insufficient samples, identical x, constant y and invalid values', () => {
  assert.equal(leastSquares([]),null);assert.equal(leastSquares([{x:1,y:2}]),null);
  assert.equal(leastSquares([{x:1,y:2},{x:1,y:3}]),null);
  assert.equal(leastSquares([{x:0,y:2},{x:1,y:2}]).r2,null);
  assert.equal(leastSquares([{x:NaN,y:2},{x:Infinity,y:3}]),null);
});
test('axes read published totals and use explicitly labelled log scales only beyond tenfold positive spans', () => {
  const m={cost:1,tokens:{output:50},latency:5};
  assert.equal(scatterValue(m,'tokens'),50);assert.equal(scatterValue(m,'latency'),5);
  const log=scatterScale([m,{...m,cost:100}],'cost');assert.equal(log.log,true);
  for(const value of [1,10,100])assert.ok(Math.abs(log.value(log.position(value))-value)<1e-8);
  assert.equal(scatterScale([m,{...m,cost:10}],'cost').log,false);
  assert.equal(scatterScale([m,{...m,cost:0}],'cost').log,false);
  assert.ok(Number.isFinite(scatterScale([m,m],'cost').position(1)));
  assert.ok(Number.isFinite(scatterScale([],'cost').position(0)));
  assert.throws(()=>scatterValue(m,'unknown'),/Unknown scatter axis/);
  assert.throws(()=>scatterScale([],'unknown'),/Unknown scatter axis/);
});
