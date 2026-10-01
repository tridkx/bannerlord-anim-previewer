/* =============================================================================
   mbpreview 查看器 —— 数学与 GL 小工具
   不依赖任何第三方库：矩阵/四元数按需实现，够用即可。
   ========================================================================== */

export const M4 = {
  identity() { return new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]); },

  /** 列主序相乘：out = a @ b（与 GL 一致） */
  mul(a, b, out = new Float32Array(16)) {
    for (let c = 0; c < 4; c++) {
      const b0 = b[c*4], b1 = b[c*4+1], b2 = b[c*4+2], b3 = b[c*4+3];
      out[c*4+0] = a[0]*b0 + a[4]*b1 + a[8]*b2  + a[12]*b3;
      out[c*4+1] = a[1]*b0 + a[5]*b1 + a[9]*b2  + a[13]*b3;
      out[c*4+2] = a[2]*b0 + a[6]*b1 + a[10]*b2 + a[14]*b3;
      out[c*4+3] = a[3]*b0 + a[7]*b1 + a[11]*b2 + a[15]*b3;
    }
    return out;
  },

  perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return new Float32Array([
      f/aspect,0,0,0, 0,f,0,0, 0,0,(far+near)*nf,-1, 0,0,2*far*near*nf,0]);
  },

  /** 由「目标点 + 方位角/仰角/距离」构造视图矩阵（右-handed，看向 -Z） */
  orbit(eye, target, up = [0,0,1]) {
    const z = norm(sub(eye, target));
    const x = norm(cross(up, z));
    const y = cross(z, x);
    return new Float32Array([
      x[0],y[0],z[0],0,
      x[1],y[1],z[1],0,
      x[2],y[2],z[2],0,
      -dot(x,eye), -dot(y,eye), -dot(z,eye), 1]);
  },

  /** 从 4x4（列主序，16 个 float 的普通数组）取左上 3x3 塞进 mat3（列主序） */
  toMat3(m, out = new Float32Array(9)) {
    out[0]=m[0]; out[1]=m[1]; out[2]=m[2];
    out[3]=m[4]; out[4]=m[5]; out[5]=m[6];
    out[6]=m[8]; out[7]=m[9]; out[8]=m[10];
    return out;
  },
};

export const V3 = {
  sub: (a,b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]],
  add: (a,b) => [a[0]+b[0], a[1]+b[1], a[2]+b[2]],
  scale: (a,s) => [a[0]*s, a[1]*s, a[2]*s],
  dot: (a,b) => a[0]*b[0]+a[1]*b[1]+a[2]*b[2],
  cross: (a,b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]],
  len: a => Math.hypot(a[0],a[1],a[2]),
  norm(a) { const l = Math.hypot(a[0],a[1],a[2]) || 1; return [a[0]/l, a[1]/l, a[2]/l]; },
};
const { sub, cross, dot, norm } = V3;

/** 四元数 (x,y,z,w) → 3x3（列主序），与 baker/animation.py 的 q2R 必须一致 */
export function quatToMat3(q, out = new Float32Array(9)) {
  let [x,y,z,w] = q;
  const n = Math.hypot(x,y,z,w) || 1; x/=n; y/=n; z/=n; w/=n;
  const xx=x*x, yy=y*y, zz=z*z, xy=x*y, xz=x*z, yz=y*z, wx=w*x, wy=w*y, wz=w*z;
  // 行主序公式 → 列主序写入
  out[0]=1-2*(yy+zz); out[1]=2*(xy+wz);   out[2]=2*(xz-wy);
  out[3]=2*(xy-wz);   out[4]=1-2*(xx+zz); out[5]=2*(yz+wx);
  out[6]=2*(xz+wy);   out[7]=2*(yz-wx);   out[8]=1-2*(xx+yy);
  return out;
}

/** 球面插值（动画帧间过渡；逐帧关键帧时 t 通常落在两帧之间） */
export function slerp(a, b, t, out = new Float32Array(4)) {
  let [ax,ay,az,aw] = a, [bx,by,bz,bw] = b;
  let d = ax*bx + ay*by + az*bz + aw*bw;
  if (d < 0) { bx=-bx; by=-by; bz=-bz; bw=-bw; d=-d; }
  if (d > 0.9995) {
    out[0]=ax+(bx-ax)*t; out[1]=ay+(by-ay)*t; out[2]=az+(bz-az)*t; out[3]=aw+(bw-aw)*t;
    const n = Math.hypot(out[0],out[1],out[2],out[3])||1;
    out[0]/=n; out[1]/=n; out[2]/=n; out[3]/=n;
    return out;
  }
  const th = Math.acos(d), s = Math.sin(th);
  const wa = Math.sin((1-t)*th)/s, wb = Math.sin(t*th)/s;
  out[0]=ax*wa+bx*wb; out[1]=ay*wa+by*wb; out[2]=az*wa+bz*wb; out[3]=aw*wa+bw*wb;
  return out;
}

/* --------------------------------------------------------------------------- GL 小工具 */

export function createProgram(gl, vsSrc, fsSrc, name = "program") {
  const compile = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      const log = gl.getShaderInfoLog(s);
      throw new Error(`[${name}] 着色器编译失败:\n${log}\n---- 源码 ----\n${src}`);
    }
    return s;
  };
  const p = gl.createProgram();
  gl.attachShader(p, compile(gl.VERTEX_SHADER, vsSrc));
  gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fsSrc));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    throw new Error(`[${name}] 链接失败: ${gl.getProgramInfoLog(p)}`);
  }
  // 缓存 uniform / attribute 位置
  p._u = {};
  p._a = {};
  const nu = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
  for (let i = 0; i < nu; i++) {
    const info = gl.getActiveUniform(p, i);
    const nm = info.name.replace(/\[0\]$/, "");
    p._u[nm] = gl.getUniformLocation(p, nm);
  }
  const na = gl.getProgramParameter(p, gl.ACTIVE_ATTRIBUTES);
  for (let i = 0; i < na; i++) {
    const info = gl.getActiveAttrib(p, i);
    p._a[info.name] = gl.getAttribLocation(p, info.name);
  }
  p.u = n => p._u[n];
  p.a = n => p._a[n];
  return p;
}

export function createTexture(gl, image, { mipmap = true, srgb = false } = {}) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  // ★ V 轴不翻：本源 UV 是 top-origin，而 glTexImage2D 的第一行数据正好落在 t=0。
  //   （baker 侧同样不翻，两边结论一致；翻图会让五官整体上下错位）
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
  const filter = mipmap ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR;
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, mipmap ? filter : gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
  if (mipmap) gl.generateMipmap(gl.TEXTURE_2D);
  return t;
}

export function createWhiteTexture(gl) {
  const t = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
                new Uint8Array([255,255,255,255]));
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  return t;
}

/** 把 4x4 普通数组（16 个数的 JS 数组）转成 Float32Array 并转置为列主序 */
export function rowMajorArrayToGL(a) {
  const o = new Float32Array(16);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) o[c*4+r] = a[r*4+c];
  return o;
}
