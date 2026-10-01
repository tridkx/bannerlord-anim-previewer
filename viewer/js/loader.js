/* =============================================================================
   加载器：读 manifest 与各类二进制资源。

   格式与 baker 侧一一对应（改了任何一边都要同步）：
     MBMG  网格   : magic/ver/count/jsonLen + json + [pos][nrm][uv][col][boneIdx][boneW][tri]
     MBAN  动画   : magic/ver/boneCount/frames + rootPos(f32 x3) + quat(f32 x4 x bones)
   ========================================================================== */

// 注意：按小端 u32 读，'MBMG' 的字节序是 4D 42 4D 47 → 0x474D424D
const MESH_MAGIC = 0x474D424D;
const ANIM_MAGIC = 0x4E41424D;  // 'MBAN' → 4D 42 41 4E

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`加载失败 ${url}: HTTP ${r.status}`);
  return r.json();
}

async function fetchBuffer(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`加载失败 ${url}: HTTP ${r.status}`);
  return r.arrayBuffer();
}

/** 解析 MBMG → 若干子网格（每个含 typed array，可直接喂 GL） */
export function parseMeshPack(buf) {
  const dv = new DataView(buf);
  const magic = dv.getUint32(0, true);
  if (magic !== MESH_MAGIC) throw new Error('不是 MBMG 文件');
  const version = dv.getUint32(4, true);
  const count = dv.getUint32(8, true);
  const jsonLen = dv.getUint32(12, true);
  const metas = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 16, jsonLen)));
  let off = 16 + jsonLen;

  const take = (Type, n) => {
    const a = new Type(buf, off, n);
    off += a.byteLength;
    return a;
  };

  const subs = [];
  for (const m of metas) {
    const vc = m.vertexCount, ic = m.indexCount;
    const s = { meta: m };
    s.position = take(Float32Array, vc * 3);
    if (m.hasNormals) s.normal = take(Float32Array, vc * 3);
    s.uv = take(Float32Array, vc * 2);
    if (m.hasColor) s.color = take(Uint8Array, vc * 4);
    if (m.hasSkin) {
      s.boneIndex = take(Uint8Array, vc * 4);
      s.boneWeight = take(Uint8Array, vc * 4);
    }
    if (ic) s.index = take(Uint32Array, ic);
    subs.push(s);
  }
  if (off !== buf.byteLength) {
    console.warn(`MBMG 解析残留 ${buf.byteLength - off} 字节（格式可能变了）`);
  }
  return subs;
}

/** 解析 MBAN → {boneCount, frames, rootPos: Float32Array, quat: Float32Array} */
export function parseAnimPack(buf) {
  const dv = new DataView(buf);
  const magic = dv.getUint32(0, true);
  if (magic !== ANIM_MAGIC) throw new Error('不是 MBAN 文件');
  const version = dv.getUint32(4, true);
  const boneCount = dv.getUint32(8, true);
  const frames = dv.getUint32(12, true);
  // v2 起多一个 start 字段：动画真正的起始帧（跳过开头那一帧绑定姿势）
  const start = version >= 2 ? dv.getUint32(16, true) : 0;
  const head = version >= 2 ? 20 : 16;
  const rootPos = new Float32Array(buf, head, frames * 3);
  const quat = new Float32Array(buf, head + frames * 12, boneCount * frames * 4);
  return { boneCount, frames, start, rootPos, quat };
}

export class Loader {
  /**
   * 所有 file 字段都是**相对 data/ 的路径**（baker 侧统一过），
   * 于是 mod 资源、共享动画、原版部件用同一套拼接规则，不需要按类型分支。
   */
  constructor(base = 'data/') {
    this.base = base;
    this.manifest = null;
    this.meshCache = new Map();
    this.animCache = new Map();
    this.texCache = new Map();
    this.animMeta = new Map();
  }

  url(rel) { return this.base + rel; }

  async loadManifest(mod) {
    this.manifest = await fetchJSON(`data/mods/${encodeURIComponent(mod)}/manifest.json`);
    for (const a of this.manifest.anims || []) this.animMeta.set(a.key, a);
    return this.manifest;
  }

  async loadMesh(rel) {
    if (!this.meshCache.has(rel)) {
      this.meshCache.set(rel, (async () =>
        parseMeshPack(await fetchBuffer(this.url(rel))))());
    }
    return this.meshCache.get(rel);
  }

  async loadAnim(key) {
    if (!this.animCache.has(key)) {
      this.animCache.set(key, (async () => {
        const meta = this.animMeta.get(key);
        const rel = meta && meta.file ? meta.file : `cache/anim/${key}.mban`;
        const a = parseAnimPack(await fetchBuffer(this.url(rel)));
        a.meta = meta || { key, frames: a.frames };
        return a;
      })());
    }
    return this.animCache.get(key);
  }

  /** 贴图用浏览器原生解码（PNG），失败时返回 null 由渲染器用白图兜底 */
  loadTexture(rel) {
    if (this.texCache.has(rel)) return this.texCache.get(rel);
    const p = new Promise((resolve) => {
      const img = new Image();
      let done = false;
      const fin = (v) => { if (!done) { done = true; resolve(v); } };
      img.onload = () => fin(img);
      img.onerror = () => { console.warn('贴图加载失败', rel); fin(null); };
      // 超时兜底：某些情况下 onload/onerror 都不触发，会把整个加载流程卡死
      setTimeout(() => { if (!done) { console.warn('贴图加载超时', rel); fin(null); } }, 15000);
      img.src = this.url(rel);
    });
    this.texCache.set(rel, p);
    return p;
  }
}
