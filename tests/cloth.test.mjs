import assert from 'node:assert/strict';
import {ClothMesh, skinPositions} from '../viewer/js/cloth.js';

const identity = () => new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]);
const sub = () => ({meta: {cloth: {enabled: true, source: 'tpac', maxDistance: .2,
  frequency: 120, material: {gravity: 9.81, damping: .1, stretching: .9, wind: 1}}},
  position: new Float32Array([0,0,1, .2,0,1, 0,0,.8, .2,0,.8]),
  color: new Uint8Array([255,255,255,0, 255,255,255,0, 255,255,255,255, 255,255,255,255]),
  index: new Uint32Array([0,2,1, 1,2,3]),
  boneIndex: new Uint8Array(16), boneWeight: new Uint8Array(16).fill(63)});

const s = sub(), c = new ClothMesh(s), m = identity();
for (let frame = 0; frame < 240; frame++) {
  m[12] = Math.sin(frame / 30) * .1;
  c.update([m], 1/120, [], {wind: 3});
  const anchors = skinPositions(s, [m]);
  for (let i = 0; i < 4; i++) {
    const distance = Math.hypot(...[0,1,2].map(k => c.position[i*3+k] - anchors[i*3+k]));
    assert.ok(distance <= (i < 2 ? 1e-6 : .200001));
  }
  assert.ok(c.position.every(Number.isFinite));
}
assert.ok(Math.abs(c.position[6] - m[12]) > .00001, 'free cloth responds to wind');
const frozen = c.position.slice(); c.update([m], 0, [], {wind: 20});
assert.deepEqual(c.position, frozen, 'pause freezes simulation');
c.reset(); c.update([m], 0);
assert.deepEqual(c.position, skinPositions(s, [m]), 'reset returns exactly to skinning');
const a = new ClothMesh(sub()), b = new ClothMesh(sub());
for (let i=0; i<60; i++) { a.update([identity()], 1/60); b.update([identity()], 1/60); }
assert.deepEqual(a.position, b.position, 'fixed-step sequence is reproducible');
const off = sub(); off.meta.cloth.enabled = false;
assert.equal(new ClothMesh(off).active, false);
const mapped = sub(); mapped.meta.cloth.simulationMesh = '11111111-0000-0000-0000-000000000000';
assert.match(new ClothMesh(mapped).reason, /映射/);
// Render seams can exceed the old vertex cap while requiring few physics points.
const repeated = sub();
repeated.position = new Float32Array(18000 * 3);
repeated.color = new Uint8Array(18000 * 4);
repeated.boneIndex = new Uint8Array(18000 * 4);
repeated.boneWeight = new Uint8Array(18000 * 4).fill(63);
for (let i=0; i<18000; i++) {
  repeated.position.set(s.position.subarray((i%4)*3, (i%4)*3+3), i*3);
  repeated.color.set(s.color.subarray((i%4)*4, (i%4)*4+4), i*4);
}
const welded = new ClothMesh(repeated);
assert.equal(welded.active, true);
assert.equal(welded.reps.length, 4);
const unique = sub();
unique.position = Float32Array.from({length: 17000*3}, (_, i) => i*.001);
unique.color = new Uint8Array(17000*4).fill(255);
unique.boneIndex = new Uint8Array(17000*4);
unique.boneWeight = new Uint8Array(17000*4).fill(63);
assert.match(new ClothMesh(unique).reason, /物理点预算/);
const contact = sub(); contact.position.fill(0); contact.meta.cloth.maxDistance=1;
const collider = new ClothMesh(contact);
collider.update([identity()], 1/60, [{a:[0,0,-1], b:[0,0,1], radius:.1}]);
assert.ok(Math.hypot(collider.position[6], collider.position[7]) >= .099);
assert.ok(collider.position.every(Number.isFinite), 'capsule axis contact remains finite');
console.log('cloth solver: anchors, distance limits, wind, pause, reset, determinism, mapping and collision passed');
