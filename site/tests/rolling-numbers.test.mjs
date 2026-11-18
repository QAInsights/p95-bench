import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatNumber, interpolateNumber, numberDuration } from '../src/lib/number-format.mjs';
test('600ms cubic ease-out interpolation has exact endpoints and clamps progress', () => {
  assert.equal(numberDuration,600);
  assert.equal(interpolateNumber(0,100,0),0);assert.equal(interpolateNumber(0,100,.5),87.5);
  assert.equal(interpolateNumber(0,100,1),100);assert.equal(interpolateNumber(0,100,2),100);
  assert.equal(interpolateNumber(10,0,.5),1.25);assert.equal(interpolateNumber(10,0,-1),10);
  assert.equal(interpolateNumber(4,4,.5),4);assert.equal(interpolateNumber(-4,4,.5),3);
});
test('formatting retains score precision, integer counts and optional grouping without negative zero', () => {
  assert.equal(formatNumber(100,{digits:2}),'100.00');assert.equal(formatNumber(87.777,{digits:2}),'87.78');
  assert.equal(formatNumber(255.4),'255');assert.equal(formatNumber(0),'0');
  assert.equal(formatNumber(1234,{grouping:true}),'1,234');assert.equal(formatNumber(-.001,{digits:2}),'0.00');
});
test('invalid interpolation and formatting inputs fail explicitly', () => {
  assert.throws(()=>interpolateNumber(NaN,1,.5),RangeError);assert.throws(()=>interpolateNumber(0,Infinity,1),RangeError);
  assert.throws(()=>formatNumber(Infinity),RangeError);assert.throws(()=>formatNumber(1,{digits:-1}),RangeError);
  assert.throws(()=>formatNumber(1,{digits:1.5}),RangeError);
});
