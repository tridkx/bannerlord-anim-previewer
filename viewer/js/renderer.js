/* =============================================================================
   WebGL2 渲染器：GPU 蒙皮 + 按游戏材质语义出图。

   刻意复刻的几条游戏侧语义（不照做就会出现"看着像、实机不一样"）：
     * UV 的 V 轴不翻 —— 本源是 top-origin，glTexImage2D 第一行正落在 t=0
     * alphaTest 用材质自己的阈值（原版常见 0.2745），低于阈值的片元 discard
     * two_sided 材质要对背面翻转法线，否则背面全是黑的
     * blendMode 决定是否写深度、是否混合；不透明/镂空写深度，混合不写
     * 顶点色只有在 shaderMatFlags 含 use_vertex_colors 时才参与调制
     * 光照量配平到 ≈1.0 —— 环境+直射加起来 >1 会把深色贴图抬成灰白，看着像"贴图错了"
   ========================================================================== */

import { M4, V3, createProgram, createTexture, createWhiteTexture } from './math.js';

// 人形骨架固定 28 根。数组开太大会吃掉 vertex uniform 配额
// （mat4[64] = 256 个 vec4，正好顶到 WebGL2 的最低保证值，弱显卡上会链接失败），
// 链接失败时 gl.getUniformLocation 返回 null，uniform 上传被静默忽略 —— 表现就是"骨骼不动/模型消失"。
const MAX_BONES = 28;

const VS = `#version 300 es
in vec3 aPos;
in vec3 aNormal;
in vec2 aUv;
in uvec4 aBoneIndex;
in vec4 aBoneWeight;
in vec4 aColor;

uniform mat4 uProj;
uniform mat4 uView;
uniform mat4 uModel;
uniform mat4 uBones[${MAX_BONES}];
uniform int  uBoneCount;     // 实际骨架骨数，用来钳制越界索引
uniform bool uSkinned;
uniform bool uHasColor;

out vec3 vWorldPos;
out vec3 vNormal;
out vec2 vUv;
out vec4 vColor;

void main() {
  vec4 p = vec4(aPos, 1.0);
  vec3 n = aNormal;
  if (uSkinned) {
    // ★ 权重必须归一化：部分 mod 的每顶点权重和不是 255（实测 LVBU and DIAOCHAN
    //   低到 58%），直接当系数用会把模型整体缩放。
    vec4 w = aBoneWeight;
    float ws = w.x + w.y + w.z + w.w;
    w = ws > 0.0001 ? w / ws : vec4(1.0, 0.0, 0.0, 0.0);
    // ★ 索引必须钳制：该 mod 里有网格用到索引 31（马骨架），而 uBones 只有 28 项，
    //   越界读取 uniform 数组在 WebGL 里是未定义行为（常见结果是顶点塌到原点）。
    ivec4 bi = clamp(ivec4(aBoneIndex), ivec4(0), ivec4(uBoneCount - 1));
    mat4 skin = w.x * uBones[bi.x] + w.y * uBones[bi.y]
              + w.z * uBones[bi.z] + w.w * uBones[bi.w];
    p = skin * p;
    n = mat3(skin) * n;
  }
  vec4 wp = uModel * p;
  vWorldPos = wp.xyz;
  vNormal = mat3(uModel) * n;
  vUv = aUv;
  vColor = uHasColor ? aColor : vec4(1.0);
  gl_Position = uProj * uView * wp;
}`;

const FS = `#version 300 es
precision highp float;

in vec3 vWorldPos;
in vec3 vNormal;
in vec2 vUv;
in vec4 vColor;

uniform sampler2D uAlbedo;
uniform sampler2D uNormalTex;
uniform sampler2D uSpecTex;
uniform bool uHasAlbedo;
uniform bool uHasNormalTex;
uniform bool uHasSpecTex;
uniform bool uAlphaTestOn;
uniform float uAlphaTest;
uniform bool uTwoSided;
uniform bool uUseVertexColor;
uniform bool uVertexColorAlpha;
uniform float uSpecStrength;
uniform float uGloss;
uniform vec3 uLightDir;      // 指向光源（已归一化，世界空间）
uniform vec3 uLightColor;
uniform vec3 uAmbientColor;
uniform vec3 uCameraPos;
uniform int  uDebugMode;     // 0=正常 1=仅反照率 2=法线 3=UV 4=仅光照
uniform bool uCutout;        // alphaTest 材质：discard 之后按不透明输出
uniform vec3 uTint;

out vec4 fragColor;

void main() {
  if (uDebugMode == 3) { fragColor = vec4(fract(vUv), 0.0, 1.0); return; }

  vec3 N = normalize(vNormal);
  if (uTwoSided && !gl_FrontFacing) N = -N;

  vec4 base = uHasAlbedo ? texture(uAlbedo, vUv) : vec4(1.0);
  base.rgb *= uTint;
  if (uUseVertexColor) {
    base.rgb *= vColor.rgb;
    if (uVertexColorAlpha) base.a *= vColor.a;
  }

  // 镂空：低于阈值整片丢弃（发丝/睫毛/网格布靠它出形状）
  if (uAlphaTestOn && base.a < uAlphaTest) discard;

  if (uDebugMode == 1) { fragColor = base; return; }
  if (uDebugMode == 2) { fragColor = vec4(N * 0.5 + 0.5, 1.0); return; }

  vec3 V = normalize(uCameraPos - vWorldPos);
  float ndl = max(dot(N, uLightDir), 0.0);

  // 高光（Game 用 use_specular；无高光贴图时按 gloss 给一个常数）
  vec3 specCol = uHasSpecTex ? texture(uSpecTex, vUv).rgb : vec3(1.0);
  vec3 H = normalize(uLightDir + V);
  float spec = pow(max(dot(N, H), 0.0), mix(8.0, 96.0, clamp(uGloss, 0.0, 1.0)));
  vec3 specular = specCol * spec * uSpecStrength * ndl;

  // 半球环境光：天空色在上、地面反射在下，比纯常数环境光更接近游戏观感
  vec3 sky = uAmbientColor;
  vec3 ground = uAmbientColor * 0.35;
  vec3 amb = mix(ground, sky, clamp(N.z * 0.5 + 0.5, 0.0, 1.0));

  vec3 lit = base.rgb * (amb + uLightColor * ndl) + specular;
  if (uDebugMode == 4) lit = amb + uLightColor * ndl;

  // Cutout：留下的像素当实心，避免"头发发虚"
  fragColor = vec4(lit, uCutout ? 1.0 : base.a);
}`;

export class Renderer {
  constructor(canvas) {
    const gl = canvas.getContext('webgl2', {
      alpha: false, antialias: true, depth: true, stencil: false,
      premultipliedAlpha: false,
      // 不要开 preserveDrawingBuffer：截图走 CDP 合成结果，用不着它，
      // 而它在部分驱动上会和抗锯齿一起导致画面闪烁/黑屏。
      preserveDrawingBuffer: false,
      powerPreference: 'high-performance',
    });
    if (!gl) throw new Error('这个浏览器/驱动不支持 WebGL2，无法运行预览器。');
    this.gl = gl;
    this.canvas = canvas;
    this.prog = createProgram(gl, VS, FS, 'mesh');
    this.white = createWhiteTexture(gl);
    this.gpu = new WeakMap();        // subs 数组 → GPU 资源
    this.texGpu = new Map();         // rel → GL texture
    this.stats = { drawCalls: 0, triangles: 0 };
    this.boneCount = MAX_BONES;
    this.light = {
      // 装备页大气（Modules/Native/Atmospheres/item_scene_atmosphere.xml）：
      //   sun_altitude=70  sun_intesity=0.700  sun_color=1.0,0.932,0.891
      //   global_ambient fog_ambient_color=0.517,0.708,1.000
      // ★ 关键：环境 + 直射的总量要配平到 ≈1.0。少了会把亮色压成灰蓝
      //   （浅肤色看起来像灰皮肤），多了会把深色抬成灰白 —— 两边都会让人误判"贴图错了"。
      dir: V3.norm([0.35, 0.55, 0.76]),
      color: [0.75, 0.72, 0.68],
      ambient: [0.40, 0.43, 0.48],
      ground: [0.18, 0.16, 0.15],
    };
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.clearColor(0.10, 0.11, 0.13, 1.0);

    // 上下文健康度（排查"看不见"时第一时间要看的东西）
    this.diag = { maxBones: MAX_BONES, uniformOk: !!this.prog.u('uBones') };
    try {
      const d = gl.getExtension('WEBGL_debug_renderer_info');
      this.diag.renderer = d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      const dbg = gl.getExtension('WEBGL_debug_shaders');
      this.diag.uniformVectors = gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS);
      this.diag.msaa = gl.getParameter(gl.SAMPLES);
    } catch (e) { this.diag.renderer = '?'; }
  }

  setCanvasSize(w, h, dpr = 1) {
    const gl = this.gl;
    this.canvas.width = Math.max(1, Math.round(w * dpr));
    this.canvas.height = Math.max(1, Math.round(h * dpr));
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
  }

  /** 上传一个子网格到 GPU（一次性） */
  upload(subs) {
    const gl = this.gl;
    if (this.gpu.has(subs)) return this.gpu.get(subs);
    const out = subs.map(s => {
      const g = {};
      const buf = (target, data, usage = gl.STATIC_DRAW) => {
        const b = gl.createBuffer();
        gl.bindBuffer(target, b);
        gl.bufferData(target, data, usage);
        return b;
      };
      g.pos = buf(gl.ARRAY_BUFFER, s.position);
      if (s.normal) g.nrm = buf(gl.ARRAY_BUFFER, s.normal);
      g.uv = buf(gl.ARRAY_BUFFER, s.uv);
      if (s.color) g.col = buf(gl.ARRAY_BUFFER, s.color);
      if (s.boneIndex) {
        g.bidx = buf(gl.ARRAY_BUFFER, s.boneIndex);
        g.bwt = buf(gl.ARRAY_BUFFER, s.boneWeight);
      }
      if (s.index) g.idx = buf(gl.ELEMENT_ARRAY_BUFFER, s.index);
      g.count = s.index ? s.index.length : s.position.length / 3;
      g.skinned = !!s.boneIndex;
      g.hasColor = !!s.color;
      return g;
    });
    this.gpu.set(subs, out);
    return out;
  }

  texture(rel, image) {
    if (this.texGpu.has(rel)) return this.texGpu.get(rel);
    const t = image ? createTexture(this.gl, image) : this.white;
    this.texGpu.set(rel, t);
    return t;
  }

  /** 画一个子网格 */
  drawSub(g, mat, opts) {
    const gl = this.gl, p = this.prog;
    const a = n => p.a(n);

    if (a('aPos') >= 0) {
      gl.bindBuffer(gl.ARRAY_BUFFER, g.pos);
      gl.enableVertexAttribArray(a('aPos'));
      gl.vertexAttribPointer(a('aPos'), 3, gl.FLOAT, false, 0, 0);
    }
    if (a('aNormal') >= 0) {
      if (g.nrm) {
        gl.bindBuffer(gl.ARRAY_BUFFER, g.nrm);
        gl.enableVertexAttribArray(a('aNormal'));
        gl.vertexAttribPointer(a('aNormal'), 3, gl.FLOAT, false, 0, 0);
      } else {
        gl.disableVertexAttribArray(a('aNormal'));
        gl.vertexAttrib3f(a('aNormal'), 0, 0, 1);
      }
    }
    if (a('aUv') >= 0) {
      gl.bindBuffer(gl.ARRAY_BUFFER, g.uv);
      gl.enableVertexAttribArray(a('aUv'));
      gl.vertexAttribPointer(a('aUv'), 2, gl.FLOAT, false, 0, 0);
    }
    if (a('aColor') >= 0) {
      if (g.col) {
        gl.bindBuffer(gl.ARRAY_BUFFER, g.col);
        gl.enableVertexAttribArray(a('aColor'));
        gl.vertexAttribPointer(a('aColor'), 4, gl.UNSIGNED_BYTE, true, 0, 0);
      } else {
        gl.disableVertexAttribArray(a('aColor'));
        gl.vertexAttrib4f(a('aColor'), 1, 1, 1, 1);
      }
    }
    // 骨骼索引必须是整数属性（用 IPointer），权重是归一化 ubyte
    if (a('aBoneIndex') >= 0) {
      if (g.bidx && opts.skinned) {
        gl.bindBuffer(gl.ARRAY_BUFFER, g.bidx);
        gl.enableVertexAttribArray(a('aBoneIndex'));
        gl.vertexAttribIPointer(a('aBoneIndex'), 4, gl.UNSIGNED_BYTE, 0, 0);
      } else {
        gl.disableVertexAttribArray(a('aBoneIndex'));
        gl.vertexAttribI4ui(a('aBoneIndex'), 0, 0, 0, 0);
      }
    }
    if (a('aBoneWeight') >= 0) {
      if (g.bwt && opts.skinned) {
        gl.bindBuffer(gl.ARRAY_BUFFER, g.bwt);
        gl.enableVertexAttribArray(a('aBoneWeight'));
        gl.vertexAttribPointer(a('aBoneWeight'), 4, gl.UNSIGNED_BYTE, true, 0, 0);
      } else {
        gl.disableVertexAttribArray(a('aBoneWeight'));
        gl.vertexAttrib4f(a('aBoneWeight'), 1, 0, 0, 0);
      }
    }

    // 贴图槽位：0=albedo 2=normal 4=specular
    const slots = mat.textures || {};
    const albedoRel = slots.albedo ? slots.albedo.file : null;
    const normRel = slots.normal ? slots.normal.file : null;
    const specRel = slots.specular ? slots.specular.file : null;
    const bind = (unit, tex, loc, hasLoc) => {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, tex);
      if (loc) gl.uniform1i(loc, unit);
      if (hasLoc) gl.uniform1i(hasLoc, tex === this.white ? 0 : 1);
    };
    bind(0, albedoRel ? (this.texGpu.get(albedoRel) || this.white) : this.white,
         p.u('uAlbedo'), p.u('uHasAlbedo'));
    bind(1, normRel ? (this.texGpu.get(normRel) || this.white) : this.white,
         p.u('uNormalTex'), p.u('uHasNormalTex'));
    bind(2, specRel ? (this.texGpu.get(specRel) || this.white) : this.white,
         p.u('uSpecTex'), p.u('uHasSpecTex'));

    // 混合模式 → GL 状态
    // ★ 有 alphaTest 的材质走 Cutout：alpha 只用来"挖形状"，留下的像素是实心的。
    //   实测 mod 的头发贴图里完全不透明的像素可能只有 2.3%（Valerie），
    //   其余按 SRC_ALPHA 混合会让整头头发发虚 —— 而 alpha_test 的语义本就是二值化。
    //   想看"如果按半透明混合会怎样"可以用 ?blendtest=1 对比。
    // ★ 有些材质定义了 alphaTest 值却没勾 alphaTestOn —— 实测 LVBU 里有 5 个
    //   （minyul 明玉护手、heizhenzhub 黑珍珠、minyuh、heizhenzhue、monvdejiemao）。
    //   原版 50 个材质里这两个字段**永远一致**，说明是打包工具漏勾了。
    //   只认 alphaTestOn 会让这些材质走半透明混合 —— 明玉护手整只发虚就是这么来的。
    //   注意 1.0 是危险值（会丢弃全部 alpha<1 的像素，睫毛会整个消失），
    //   所以只认 (0,1) 开区间里的阈值。
    const atVal = mat.alphaTest || 0;
    const useAlphaTest = !!mat.alphaTestOn || (atVal > 0.01 && atVal < 0.99);
    const cutout = useAlphaTest && !opts.forceBlend;
    // ★ mat.opaque：贴图 alpha 基本全 1 的材质（即便 blendMode 写着 factor）也按不透明走。
    //   否则不写深度，双面材质的内表面会盖住外表面 —— 实测曹操的脸就是这样，
    //   正面能看到后脑勺内壳。没有该字段时退回原来的行为。
    const blend = !cutout && mat.opaque !== true
                  && mat.blendMode && mat.blendMode !== 'no_alpha_blend';
    gl.depthMask(blend ? false : true);
    if (blend) {
      gl.enable(gl.BLEND);
      if (mat.blendMode === 'add' || mat.blendMode === 'add_alpha') gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      else if (mat.blendMode === 'modulate' || mat.blendMode === 'multiply') gl.blendFunc(gl.DST_COLOR, gl.ZERO);
      else gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    } else {
      gl.disable(gl.BLEND);
    }
    if (mat.twoSided) gl.disable(gl.CULL_FACE);
    else {
      gl.enable(gl.CULL_FACE);
      gl.cullFace(mat.cullFront ? gl.FRONT : gl.BACK);
    }

    // 镂空边缘的 alpha 在阈值附近时，mipmap 采样会让它逐帧时有时无（表现为边缘闪烁）。
    // 开 alpha-to-coverage 用覆盖率做软过渡；没开 MSAA 的上下文会自动忽略，无副作用。
    if (useAlphaTest) gl.enable(gl.SAMPLE_ALPHA_TO_COVERAGE);
    else gl.disable(gl.SAMPLE_ALPHA_TO_COVERAGE);

    gl.uniform1i(p.u('uBoneCount'), this.boneCount || MAX_BONES);
    gl.uniform1i(p.u('uSkinned'), (g.skinned && opts.skinned) ? 1 : 0);
    gl.uniform1i(p.u('uHasColor'), g.hasColor ? 1 : 0);
    gl.uniform1i(p.u('uTwoSided'), mat.twoSided ? 1 : 0);
    gl.uniform1i(p.u('uAlphaTestOn'), useAlphaTest ? 1 : 0);
    gl.uniform1f(p.u('uAlphaTest'), mat.alphaTest || 0);
    gl.uniform1i(p.u('uUseVertexColor'), mat.useVertexColor ? 1 : 0);
    gl.uniform1i(p.u('uVertexColorAlpha'), mat.vertexColorAlpha === false ? 0 : 1);
    gl.uniform1f(p.u('uSpecStrength'), mat.useSpecular ? (normRel || specRel ? 0.35 : 0.10) : 0.0);
    gl.uniform1f(p.u('uGloss'), mat.specFromDiffuse ? 0.5 : 0.75);
    gl.uniform1i(p.u('uDebugMode'), opts.debugMode | 0);
    gl.uniform1i(p.u('uCutout'), cutout ? 1 : 0);
    if (p.u('uTint')) gl.uniform3fv(p.u('uTint'), opts.tint || [1, 1, 1]);

    if (g.idx) {
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, g.idx);
      gl.drawElements(gl.TRIANGLES, g.count, gl.UNSIGNED_INT, 0);
    } else {
      gl.drawArrays(gl.TRIANGLES, 0, g.count);
    }
    this.stats.drawCalls++;
    this.stats.triangles += g.count / 3;
  }

  beginFrame(proj, view, cameraPos) {
    const gl = this.gl, p = this.prog;
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.useProgram(p);
    gl.uniformMatrix4fv(p.u('uProj'), false, proj);
    gl.uniformMatrix4fv(p.u('uView'), false, view);
    gl.uniform3fv(p.u('uCameraPos'), cameraPos);
    gl.uniform3fv(p.u('uLightDir'), this.light.dir);
    gl.uniform3fv(p.u('uLightColor'), this.light.color);
    gl.uniform3fv(p.u('uAmbientColor'), this.light.ambient);
    this.stats.drawCalls = 0;
    this.stats.triangles = 0;
    // 单位模型矩阵（所有几何已在世界空间）
    gl.uniformMatrix4fv(p.u('uModel'), false, M4.identity());
  }

  /** 上传 28 根骨的蒙皮矩阵（M_pose @ inv(M_bind)） */
  setBones(mats) {
    const gl = this.gl;
    const loc = this.prog.u('uBones');
    if (!loc) return;                       // shader 里被优化掉/链接失败，静默跳过而不是崩
    this.boneCount = Math.min(mats.length, MAX_BONES);
    const flat = new Float32Array(MAX_BONES * 16);
    for (let i = 0; i < MAX_BONES; i++) {   // 默认单位阵，避免未用到的槽位是 0 矩阵
      flat[i * 16] = flat[i * 16 + 5] = flat[i * 16 + 10] = flat[i * 16 + 15] = 1;
    }
    for (let i = 0; i < Math.min(mats.length, MAX_BONES); i++) {
      const m = mats[i];
      for (let k = 0; k < 16; k++) {
        const v = m[k];
        flat[i * 16 + k] = Number.isFinite(v) ? v : (k % 5 === 0 ? 1 : 0);   // NaN 兜底
      }
    }
    gl.uniformMatrix4fv(loc, false, flat);
  }
}
