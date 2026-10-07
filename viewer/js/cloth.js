// Browser PBD approximation of Bannerlord direct cloth. Positions remain Z-up.
// Alpha is a distance, never opacity. Native TCC/TCM cooking is not reproduced.
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const value = (x, fallback) => Number.isFinite(x) ? x : fallback;

export function skinPositions(s, matrices, out = new Float32Array(s.position.length)) {
  for (let i = 0; i < s.position.length / 3; i++) {
    const o = i * 3, b = i * 4;
    if (!s.boneIndex || !matrices?.length) {
      out.set(s.position.subarray(o, o + 3), o); continue;
    }
    let sum = 0;
    for (let j = 0; j < 4; j++) sum += s.boneWeight[b + j];
    out[o] = out[o + 1] = out[o + 2] = 0;
    for (let j = 0; j < 4; j++) {
      const w = sum ? s.boneWeight[b + j] / sum : (j === 0 ? 1 : 0);
      const m = matrices[clamp(s.boneIndex[b + j], 0, matrices.length - 1)];
      for (let k = 0; k < 3; k++) out[o + k] += w * (
        m[k] * s.position[o] + m[4 + k] * s.position[o + 1] +
        m[8 + k] * s.position[o + 2] + m[12 + k]);
    }
  }
  return out;
}

// Anatomical proxy capsules, not the game's authored collision body.
export function bodyCapsules(rig, animated = true) {
  const joints = animated ? rig.currentJoints() : rig.joints;
  const names = new Map(rig.names.map((n, i) => [n.replace('bip01_', ''), i]));
  const defs = [['pelvis', 'spine1', .13], ['spine1', 'neck', .15],
    ['neck', 'head', .085]];
  for (const side of ['l', 'r']) defs.push(
    [side + '_thigh', side + '_calf', .075],
    [side + '_calf', side + '_foot', .055],
    [side + '_foot', side + '_toe0', .045],
    [side + '_upperarm_twist', side + '_foretwist', .045],
    [side + '_foretwist', side + '_hand', .035]);
  return defs.filter(([a, b]) => names.has(a) && names.has(b))
    .map(([a, b, radius]) => ({a: joints[names.get(a)], b: joints[names.get(b)], radius}));
}

export class ClothMesh {
  constructor(sub) {
    this.sub = sub;
    this.settings = sub.meta.cloth || {};
    this.material = this.settings.material || {};
    this.reason = '';
    if (!this.settings.enabled) this.reason = this.settings.source === 'unavailable' || !sub.meta.cloth
      ? '缺少布料元数据，请重烘焙' : '资源未启用布料';
    else if (this.settings.simulationMesh && !/^0{8}-0{4}-0{4}-0{4}-0{12}$/.test(this.settings.simulationMesh))
      this.reason = '映射布料尚未支持（保留原始蒙皮）';
    else if (!sub.color) this.reason = '缺少顶点 Alpha';
    else if (sub.position.length / 3 > 16000) this.reason = '直接布料超过 16000 顶点预算';
    this.active = !this.reason;
    if (!this.active) return;
    this.position = new Float32Array(sub.position.length);
    this.normal = new Float32Array(sub.position.length);
    this.renderTarget = new Float32Array(sub.position.length);
    // Weld only coincident vertices with identical skinning and activity radius.
    // UV/normal seams may share particles; separate layers must not be merged.
    const map = new Map(), reps = [], radii = [];
    this.vertexParticle = new Uint32Array(sub.position.length / 3);
    for (let i = 0; i < this.vertexParticle.length; i++) {
      const o = i * 3, b = i * 4;
      const key = Array.from(sub.position.subarray(o, o + 3)).map(x => Math.round(x * 1e6)).join(',')
        + ':' + sub.color[b + 3] + ':' + (sub.boneIndex ?
          Array.from(sub.boneIndex.subarray(b, b + 4)).join(',') + ':' +
          Array.from(sub.boneWeight.subarray(b, b + 4)).join(',') : '');
      if (!map.has(key)) {
        map.set(key, reps.length); reps.push(i);
        radii.push(sub.color[b + 3] / 255 * value(this.settings.maxDistance, 1));
      }
      this.vertexParticle[i] = map.get(key);
    }
    this.reps = reps;
    this.radius = new Float32Array(radii);
    this.movable = radii.filter(r => r > 0).length;
    if (!this.movable) { this.active = false; this.reason = 'Alpha 全零，全部固定'; return; }
    this.p = new Float32Array(reps.length * 3);
    this.previous = new Float32Array(this.p.length);
    this.target = new Float32Array(this.p.length);
    this.oldTarget = new Float32Array(this.p.length);
    this.anchor = new Float32Array(this.p.length);
    this.index = sub.index || Uint32Array.from({length: this.vertexParticle.length}, (_, i) => i);
    const edges = new Map();
    const constraints = [];
    const add = (a, b, stiffness) => {
      if (a === b || (!this.radius[a] && !this.radius[b])) return;
      const oa = reps[a] * 3, ob = reps[b] * 3;
      const length = Math.hypot(...[0, 1, 2].map(k => sub.position[oa + k] - sub.position[ob + k]));
      if (length > 1e-7) constraints.push({a, b, length, stiffness: clamp(stiffness, 0, 1)});
    };
    for (let t = 0; t + 2 < this.index.length; t += 3) {
      const ids = [0, 1, 2].map(k => this.vertexParticle[this.index[t + k]]);
      for (let j = 0; j < 3; j++) {
        const a = ids[j], b = ids[(j + 1) % 3], opposite = ids[(j + 2) % 3];
        const key = Math.min(a, b) + ':' + Math.max(a, b);
        if (!edges.has(key)) {
          edges.set(key, opposite); add(a, b, value(this.material.stretching, .9));
        } else add(edges.get(key), opposite, value(this.material.bending, .3) * .5);
      }
    }
    this.constraints = constraints;
    this.initialized = false;
    this.accumulator = 0;
  }

  reset() { this.initialized = false; this.accumulator = 0; }

  update(matrices, dt, capsules = [], options = {}) {
    if (!this.active) return;
    skinPositions(this.sub, matrices, this.renderTarget);
    this.oldTarget.set(this.target);
    for (let i = 0; i < this.reps.length; i++) {
      const o = i * 3, r = this.reps[i] * 3;
      for (let k = 0; k < 3; k++) this.target[o + k] = this.renderTarget[r + k];
    }
    if (!this.initialized) {
      this.p.set(this.target); this.previous.set(this.target); this.oldTarget.set(this.target);
      this.initialized = true;
    }
    const h = 1 / clamp(value(this.settings.frequency, 120), 30, 240);
    this.accumulator += clamp(value(dt, 0), 0, .05);
    const steps = Math.min(12, Math.floor(this.accumulator / h));
    this.accumulator -= steps * h;
    for (let step = 0; step < steps; step++) {
      const blend = (step + 1) / steps;
      for (let o = 0; o < this.p.length; o++) {
        this.anchor[o] = this.oldTarget[o] + (this.target[o] - this.oldTarget[o]) * blend;
      }
      const damping = Math.pow(1 - clamp(value(this.material.damping, .1), 0, .99), h * 60);
      const inertia = clamp(value(this.material.linearInertia, 1), 0, 1);
      const wind = value(options.wind, 0) * value(this.material.wind, 1);
      const drag = Math.exp(-Math.max(0, value(this.material.airDrag, 0)) * h);
      for (let i = 0; i < this.reps.length; i++) {
        const o = i * 3;
        if (!this.radius[i]) {
          for (let k = 0; k < 3; k++) this.p[o + k] = this.previous[o + k] = this.anchor[o + k];
          continue;
        }
        for (let k = 0; k < 3; k++) {
          const shift = (this.target[o + k] - this.oldTarget[o + k]) / steps * (1 - inertia);
          const current = this.p[o + k];
          let velocity = (current - this.previous[o + k]) * damping * drag;
          const maxV = value(this.material.maxVelocity, -1);
          if (maxV > 0) velocity = clamp(velocity, -maxV * h, maxV * h);
          this.p[o + k] = current + velocity + shift + h * h *
            (k === 2 ? -value(this.material.gravity, 9.81) : k === 0 ? wind : 0);
          this.previous[o + k] = current + shift;
        }
      }
      for (let iteration = 0; iteration < 4; iteration++) {
        for (const c of this.constraints) {
          const a = c.a * 3, b = c.b * 3;
          const x = this.p[b] - this.p[a], y = this.p[b + 1] - this.p[a + 1], z = this.p[b + 2] - this.p[a + 2];
          const length = Math.hypot(x, y, z);
          if (length < 1e-8) continue;
          const wa = this.radius[c.a] > 0 ? 1 : 0, wb = this.radius[c.b] > 0 ? 1 : 0;
          const f = (length - c.length) / length * c.stiffness / (wa + wb);
          for (let k = 0; k < 3; k++) {
            const delta = (k === 0 ? x : k === 1 ? y : z) * f;
            this.p[a + k] += delta * wa; this.p[b + k] -= delta * wb;
          }
        }
        this.project(capsules, options, this.anchor);
      }
    }
    // Exact game distance bound also applies on frames without a solver step.
    this.project([], {...options, collisions: false}, this.target);
    for (let i = 0; i < this.vertexParticle.length; i++) {
      const o = i * 3, p = this.vertexParticle[i] * 3;
      for (let k = 0; k < 3; k++) this.position[o + k] = this.p[p + k];
    }
    this.normals();
  }

  project(capsules, options, anchors) {
    for (let i = 0; i < this.reps.length; i++) {
      const o = i * 3, radius = this.radius[i] * value(options.distanceScale, 1);
      if (!radius) {
        for (let k = 0; k < 3; k++) this.p[o + k] = anchors[o + k];
        continue;
      }
      if (options.collisions !== false) {
        this.p[o + 2] = Math.max(.003, this.p[o + 2]);
        for (const c of capsules) {
          const ax = c.b[0] - c.a[0], ay = c.b[1] - c.a[1], az = c.b[2] - c.a[2];
          const px = this.p[o] - c.a[0], py = this.p[o + 1] - c.a[1], pz = this.p[o + 2] - c.a[2];
          const len2 = ax * ax + ay * ay + az * az;
          const t = len2 > 1e-12 ? clamp((ax * px + ay * py + az * pz) / len2, 0, 1) : 0;
          const dx = px - ax * t, dy = py - ay * t, dz = pz - az * t;
          const length = Math.hypot(dx, dy, dz), r = c.radius + .003;
          if (length < r) {
            // Defined direction even if the particle lies on the capsule axis.
            if (length < 1e-8) this.p[o] += r;
            else {
              const f = r / length - 1;
              this.p[o] += dx * f; this.p[o + 1] += dy * f; this.p[o + 2] += dz * f;
            }
          }
        }
      }
      const dx = this.p[o] - anchors[o], dy = this.p[o + 1] - anchors[o + 1], dz = this.p[o + 2] - anchors[o + 2];
      const length = Math.hypot(dx, dy, dz);
      if (!Number.isFinite(length)) {
        for (let k = 0; k < 3; k++) this.p[o + k] = this.previous[o + k] = anchors[o + k];
      } else if (length > radius) {
        const f = radius / length;
        this.p[o] = anchors[o] + dx * f; this.p[o + 1] = anchors[o + 1] + dy * f; this.p[o + 2] = anchors[o + 2] + dz * f;
      }
    }
  }

  normals() {
    const n = this.normal, p = this.position;
    n.fill(0);
    for (let t = 0; t + 2 < this.index.length; t += 3) {
      const a = this.index[t] * 3, b = this.index[t + 1] * 3, c = this.index[t + 2] * 3;
      const ux = p[b] - p[a], uy = p[b + 1] - p[a + 1], uz = p[b + 2] - p[a + 2];
      const vx = p[c] - p[a], vy = p[c + 1] - p[a + 1], vz = p[c + 2] - p[a + 2];
      for (const o of [a, b, c]) {
        n[o] += uy * vz - uz * vy; n[o + 1] += uz * vx - ux * vz; n[o + 2] += ux * vy - uy * vx;
      }
    }
    for (let o = 0; o < n.length; o += 3) {
      const length = Math.hypot(n[o], n[o + 1], n[o + 2]);
      if (length > 1e-10) for (let k = 0; k < 3; k++) n[o + k] /= length;
      else n[o + 2] = 1;
    }
  }
}
