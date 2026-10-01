/* =============================================================================
   姿态求解：把骨架 + 动画算成 28 个蒙皮矩阵，与 baker/animation.py 的公式严格一致。

   已在真实数据上验证的约定：
     M_i(t) = M_parent(t) @ [ R(q_i(t)) | restLocal_i.translation ]
     根骨额外叠加 rootPosition 平移
     蒙皮矩阵 = M_pose @ inv(M_bind)      ★ 顺序写反在 bind pose 下会伪装成全绿
   ========================================================================== */

import { quatToMat3, slerp } from './math.js';

/** 行主序 16 数组 → 列主序 Float32Array（Python 侧输出的是行主序拍平） */
function rm2gl(a) {
  const o = new Float32Array(16);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) o[c * 4 + r] = a[r * 4 + c];
  return o;
}

/** 刚体变换的逆：[R|t]^-1 = [R^T | -R^T t]（骨架矩阵都是刚体，比通用求逆快且稳） */
function rigidInverse(m, out = new Float32Array(16)) {
  const r00 = m[0], r01 = m[4], r02 = m[8];
  const r10 = m[1], r11 = m[5], r12 = m[9];
  const r20 = m[2], r21 = m[6], r22 = m[10];
  const tx = m[12], ty = m[13], tz = m[14];
  // R^T
  out[0] = r00; out[1] = r01; out[2] = r02; out[3] = 0;
  out[4] = r10; out[5] = r11; out[6] = r12; out[7] = 0;
  out[8] = r20; out[9] = r21; out[10] = r22; out[11] = 0;
  // -R^T t
  out[12] = -(r00 * tx + r10 * ty + r20 * tz);
  out[13] = -(r01 * tx + r11 * ty + r21 * tz);
  out[14] = -(r02 * tx + r12 * ty + r22 * tz);
  out[15] = 1;
  return out;
}

/** out = a @ b（列主序，与 GL 一致） */
function mul(a, b, out = new Float32Array(16)) {
  for (let c = 0; c < 4; c++) {
    const b0 = b[c * 4], b1 = b[c * 4 + 1], b2 = b[c * 4 + 2], b3 = b[c * 4 + 3];
    out[c * 4 + 0] = a[0] * b0 + a[4] * b1 + a[8] * b2 + a[12] * b3;
    out[c * 4 + 1] = a[1] * b0 + a[5] * b1 + a[9] * b2 + a[13] * b3;
    out[c * 4 + 2] = a[2] * b0 + a[6] * b1 + a[10] * b2 + a[14] * b3;
    out[c * 4 + 3] = a[3] * b0 + a[7] * b1 + a[11] * b2 + a[15] * b3;
  }
  return out;
}

export class Rig {
  constructor(skeleton) {
    this.n = skeleton.boneCount;
    this.names = skeleton.names;
    this.parent = skeleton.parent;
    this.restLocal = skeleton.restLocal.map(rm2gl);
    this.bindWorld = skeleton.bindWorld.map(rm2gl);
    this.invBind = this.bindWorld.map(m => rigidInverse(m));
    // 关节位置（用于相机取景、骨骼可视化）
    this.joints = this.bindWorld.map(m => [m[12], m[13], m[14]]);
    this._pose = Array.from({ length: this.n }, () => new Float32Array(16));
    this._L = new Float32Array(16);
    this._R3 = new Float32Array(9);
    this._q = new Float32Array(4);
    this.skin = Array.from({ length: this.n }, () => new Float32Array(16));
    this.bbox = this._bindBBox();
  }

  _bindBBox() {
    const lo = [1e9, 1e9, 1e9], hi = [-1e9, -1e9, -1e9];
    for (const p of this.joints) {
      for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[k]); hi[k] = Math.max(hi[k], p[k]); }
    }
    return { lo, hi };
  }

  /** 求某一帧（可为小数，帧间做 slerp）的蒙皮矩阵，写进 this.skin */
  update(anim, frame) {
    // frame 可能是 NaN（URL 参数为空、时间轴异常）—— 一旦传下去整具骨架会变 NaN，
    // 表现就是"角色整个消失"。这里兜住，退回第 0 帧而不是崩掉。
    if (!Number.isFinite(frame)) frame = 0;
    const f0 = Math.max(0, Math.min(anim.frames - 1, Math.floor(frame)));
    const f1 = Math.min(anim.frames - 1, f0 + 1);
    const t = Math.max(0, Math.min(1, frame - f0));
    const bc = Math.min(this.n, anim.boneCount);

    for (let i = 0; i < this.n; i++) {
      const L = this._L;
      if (i < bc) {
        const o0 = (i * anim.frames + f0) * 4, o1 = (i * anim.frames + f1) * 4;
        this._q[0] = anim.quat[o0]; this._q[1] = anim.quat[o0 + 1];
        this._q[2] = anim.quat[o0 + 2]; this._q[3] = anim.quat[o0 + 3];
        if (t > 0) {
          const q1 = [anim.quat[o1], anim.quat[o1 + 1], anim.quat[o1 + 2], anim.quat[o1 + 3]];
          slerp(this._q, q1, t, this._q);
        }
        const R = quatToMat3(this._q, this._R3);
        L[0] = R[0]; L[1] = R[1]; L[2] = R[2]; L[3] = 0;
        L[4] = R[3]; L[5] = R[4]; L[6] = R[5]; L[7] = 0;
        L[8] = R[6]; L[9] = R[7]; L[10] = R[8]; L[11] = 0;
      } else {
        L.set(this.restLocal[i]);
      }
      // 平移取骨架 rest 的局部平移（动画只带旋转）
      const rl = this.restLocal[i];
      L[12] = rl[12]; L[13] = rl[13]; L[14] = rl[14]; L[15] = 1;

      const p = this.parent[i];
      const M = this._pose[i];
      if (p < 0) {
        const o = f0 * 3;
        L[12] += anim.rootPos[o];
        L[13] += anim.rootPos[o + 1];
        L[14] += anim.rootPos[o + 2];
        M.set(L);
      } else {
        mul(this._pose[p], L, M);
      }
    }
    for (let i = 0; i < this.n; i++) mul(this._pose[i], this.invBind[i], this.skin[i]);
    return this.skin;
  }

  /** 当前帧的关节世界位置（骨骼线框用） */
  currentJoints() {
    const out = [];
    for (let i = 0; i < this.n; i++) {
      const m = this._pose[i];
      out.push([m[12], m[13], m[14]]);
    }
    return out;
  }
}
