/* =============================================================================
   主应用：装配场景、播放动画、驱动 UI。

   给 AI 用的入口是 URL 参数（headless 浏览器可直接截图，无需点界面）：
     ?mod=PitaoYingOutfits&anim=inventory_idle&frame=120&view=side
     &equip=all|none|id1,id2&vanilla=1&bones=1&grid=1&debug=1&light=day&w=900&h=1200
   ========================================================================== */

import { Loader } from './loader.js';
import { Renderer } from './renderer.js';
import { Rig } from './pose.js';
import { M4, V3, createProgram } from './math.js';

const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

/* --------------------------------------------------------------------------- 状态 */

const params = new URLSearchParams(location.search);

const state = {
  mod: params.get('mod') || null,
  manifest: null,
  rig: null,
  anim: null,
  animKey: params.get('anim') || null,
  frame: parseFloat(params.get('frame') || '0'),
  playing: true,
  looping: true,
  speed: (() => {
    const v = parseFloat(localStorage.getItem('mbpreview.speed') || (params.get('speed') || '1'));
    return Number.isFinite(v) && v > 0 ? v : 1;
  })(),
  time: 0,
  // 相机
  cam: { az: 0, el: 8, dist: 2.6, target: [0, 0, 0.95] },
  // 显示
  debugMode: parseInt(params.get('debug') || '0', 10),
  lightName: params.get('light') || 'item',
  background: 'dark',
  showBones: params.get('bones') === '1',
  showGrid: params.get('grid') !== '0',
  showVanilla: params.get('vanilla') !== '0',
  skinName: params.get('skin') || null,   // 原版体型对照（man/woman）
  only: params.get('only') || null,        // 只显示名字/材质匹配的网格（诊断用）
  noAlphaTest: params.get('noalphatest') === '1',   // 诊断：关掉镂空看被丢弃的部分
  // 装备可见性
  equipped: new Set(),
  hiddenMeshes: new Set(),
  scene: [],          // {key, name, subs, gpu, material, kind:'mod'|'vanilla', group}
  animList: [],
  catFilter: '',
  query: '',
};

// 每个预设都把「环境 + 直射」的总量配平到 ≈1.0（见 renderer.js 里的说明）
const LIGHTS = {
  item:   { dir: [0.35, 0.55, 0.76], color: [0.75, 0.72, 0.68], ambient: [0.40, 0.43, 0.48], bg: [0.10, 0.11, 0.13] },
  day:    { dir: [0.42, 0.36, 0.83], color: [0.95, 0.92, 0.86], ambient: [0.36, 0.42, 0.52], bg: [0.36, 0.45, 0.60] },
  night:  { dir: [-0.30, 0.42, 0.85], color: [0.34, 0.40, 0.60], ambient: [0.16, 0.19, 0.28], bg: [0.05, 0.06, 0.10] },
  studio: { dir: [0.30, 0.50, 0.81], color: [0.82, 0.82, 0.82], ambient: [0.44, 0.44, 0.46], bg: [0.22, 0.22, 0.24] },
};
const BGS = { dark: [0.10, 0.11, 0.13], light: [0.62, 0.63, 0.65], green: [0.05, 0.45, 0.15] };

let loader, renderer, canvas, gl;
let lastT = 0;
let acc = 0;

/* --------------------------------------------------------------------------- 工具 */

function toast(msg, ms = 2600) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), ms);
}

function fatal(err) {
  console.error(err);
  let d = document.getElementById('err');
  if (!d) {
    d = document.createElement('div');
    d.id = 'err';
    $('#stage').appendChild(d);
  }
  // ★ 以前这里是 appendChild 一个新 div：一旦反复出错就会叠满整屏且无法关闭，
  //   表现就是"角色被盖住、忽隐忽现"。现在复用同一个面板，并给一个关闭按钮。
  d.innerHTML = '';
  const bar = document.createElement('div');
  bar.className = 'err-bar';
  const title = document.createElement('b');
  title.textContent = '预览器出错（按 Esc 或点右侧关闭）';
  const close = document.createElement('button');
  close.textContent = '✕ 关闭';
  close.onclick = () => d.remove();
  bar.append(title, close);
  const pre = document.createElement('pre');
  pre.textContent = String(err && err.stack ? err.stack : err);
  d.append(bar, pre);
  d.scrollTop = 0;
}

function setLoading(on, text) {
  const el = $('#loading');
  el.classList.toggle('hidden', !on);
  if (text) $('#loading-text').textContent = text;
}

/* --------------------------------------------------------------------------- 相机 */

// 约定：Bannerlord 模型 +Y 是角色正面，所以 az=0（相机在 +Y 侧）看到的是正面
const VIEWS = {
  front: { az: 0, el: 6 }, back: { az: 180, el: 6 },
  left: { az: -90, el: 6 }, right: { az: 90, el: 6 },
  top: { az: 180, el: 78 }, face: { az: 0, el: 4, focus: 'face' },
  feet: { az: 0, el: 18, focus: 'feet' },
};

function applyView(name) {
  const v = VIEWS[name];
  if (!v) return;
  state.cam.az = v.az;
  state.cam.el = v.el;
  const rig = state.rig;
  if (v.focus && rig) {
    const j = rig.joints;
    const head = j[13] || [0, 0, 1.57];
    const toe = j[4] || [0, 0, 0];
    if (v.focus === 'face') { state.cam.target = [head[0], head[1], head[2] - 0.02]; state.cam.dist = 0.5; }
    else { state.cam.target = [toe[0] * 0.5, toe[1], 0.12]; state.cam.dist = 0.7; }
  } else if (rig) {
    const lo = rig.bbox.lo, hi = rig.bbox.hi;
    const h = hi[2] - lo[2];
    state.cam.target = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2] + h * 0.52];
    // 38° 垂直视场下要容纳整个人，距离约 高度/2/tan(19°) 再留 15% 边距
    state.cam.dist = Math.max(1.2, (h / 2) / Math.tan(19 * Math.PI / 180) * 1.15);
  }
  $$('#viewbar .vb[data-view]').forEach(b => b.classList.toggle('on', b.dataset.view === name));
}

function camEye() {
  const { az, el, dist, target } = state.cam;
  const a = az * Math.PI / 180, e = el * Math.PI / 180;
  return [
    target[0] + dist * Math.cos(e) * Math.sin(a),
    target[1] + dist * Math.cos(e) * Math.cos(a),
    target[2] + dist * Math.sin(e),
  ];
}

/* --------------------------------------------------------------------------- 装配场景 */

function materialOf(name) {
  const m = state.manifest.materials[name];
  if (!m) return { name, blendMode: 'no_alpha_blend', alphaTest: 0, twoSided: true,
                   textures: {}, skinning: true, alphaTestOn: false, useVertexColor: false };
  // 贴图名 → {file} 映射（渲染器只关心相对路径）
  const tex = {};
  for (const [role, tname] of Object.entries(m.textures || {})) {
    const t = state.manifest.textures[tname];
    if (t) tex[role] = { name: tname, file: t.file };
  }
  return { ...m, textures: tex };
}

async function buildScene() {
  const mf = state.manifest;
  let scene = [];

  // ---- mod 的装备件 ----
  const itemByMesh = new Map();
  for (const it of mf.items || []) itemByMesh.set(it.mesh, it);

  for (const m of mf.meshes || []) {
    const item = itemByMesh.get(m.mesh);
    const subs = await loader.loadMesh(m.file);
    // 每个子网格单独一条（材质可能不同）
    for (const s of subs) {
      scene.push({
        key: `${m.mesh}/${s.meta.name}`, mesh: m.mesh, name: s.meta.name,
        kind: 'mod', group: item ? item.id : m.mesh,
        item: item || null, subs: [s], material: materialOf(s.meta.material),
        visible: !item || state.equipped.has(item.id),
      });
    }
  }

  // ---- 原版身体部件 ----
  // ★ 只加载「当前体型」用到的那几个网格。全部加载会让男女两具身体同时出现。
  // ★ partKey 必须是语义名（body/hands/legs/face…），因为 covers_* 就是按语义遮的；
  //   直接拿网格名当 key，隐藏集合永远匹配不上，"原版身体露出来"就永远看不见。
  const van = mf.vanilla || { parts: {} };
  const skinParts = activeSkin().parts || {};
  const meshToSemantic = {};
  for (const [sem, meshName] of Object.entries(skinParts)) meshToSemantic[meshName] = sem;
  const wantMeshes = new Set(Object.values(skinParts));

  for (const [meshName, info] of Object.entries(van.parts || {})) {
    if (!wantMeshes.has(meshName)) continue;
    const semantic = meshToSemantic[meshName] || meshName;
    try {
      const subs = await loader.loadMesh(info.file);
      for (const s of subs) {
        scene.push({
          key: `vanilla/${semantic}/${s.meta.name}`, mesh: meshName, name: s.meta.name,
          kind: 'vanilla', group: semantic, partKey: semantic, item: null, subs: [s],
          material: materialOf(s.meta.material), visible: state.showVanilla,
        });
      }
    } catch (e) { console.warn('原版部件加载失败', meshName, e); }
  }

  if (state.only) {
    // 支持逗号分隔的多个关键词（诊断时用来做「只显示这几层」的对照）
    const terms = state.only.toLowerCase().split(',').map(x => x.trim()).filter(Boolean);
    scene = scene.filter(n => terms.some(f =>
      n.name.toLowerCase().includes(f)
      || (n.material.name || '').toLowerCase().includes(f)
      || n.mesh.toLowerCase().includes(f)));
  }
  state.scene = scene;
  // 上传 GPU
  for (const n of scene) n.gpu = renderer.upload(n.subs);
  // 贴图
  const rels = new Set();
  for (const n of scene) for (const t of Object.values(n.material.textures || {})) rels.add(t.file);
  await Promise.all(Array.from(rels).map(async rel => {
    const img = await loader.loadTexture(rel);
    renderer.texture(rel, img);
  }));

  applyEquipmentVisibility();
}

/** 按「已装备 + covers 遮盖」重算可见性 —— 这是"原版身体露出来"能被看见的关键 */
function applyEquipmentVisibility() {
  const mf = state.manifest;
  const equippedItems = (mf.items || []).filter(it => state.equipped.has(it.id));
  const hidden = new Set();
  for (const it of equippedItems) {
    const c = it.covers || {};
    if (c.body) { hidden.add('body'); hidden.add('shoulders'); hidden.add('underwear_top'); }
    if (c.hands) hidden.add('hands');
    if (c.legs) { hidden.add('legs'); hidden.add('underwear_bottom'); }
    if (c.head) hidden.add('face');
    if ((it.hairCover || '').toLowerCase() === 'all') hidden.add('face');
  }
  state.hiddenMeshes = hidden;
  for (const n of state.scene) {
    if (n.kind === 'mod') n.visible = !n.item || state.equipped.has(n.item.id);
    else n.visible = state.showVanilla && !hidden.has(n.partKey);
  }
  renderSkinList(equippedItems, hidden);
}

/* --------------------------------------------------------------------------- 动画 */

function rebuildAnimList() {
  const q = state.query.trim().toLowerCase();
  const cat = state.catFilter;
  let list = state.animList;
  if (cat) list = list.filter(a => a.category === cat);
  if (q) {
    list = list.filter(a =>
      a.key.toLowerCase().includes(q) ||
      (a.category || '').includes(q) ||
      (a.actions || []).some(x => x.toLowerCase().includes(q)));
  }
  const el = $('#anim-list');
  el.innerHTML = '';
  const LIMIT = 400;
  for (const a of list.slice(0, LIMIT)) {
    const d = document.createElement('div');
    d.className = 'item' + (a.key === state.animKey ? ' active' : '');
    const star = a.cyclic ? '<span class="star" title="循环">↻</span> ' : '';
    d.innerHTML = `<span class="nm">${star}${a.key}</span>
      <span class="tag">${a.category || ''} ${a.duration ? a.duration.toFixed(1) + 's' : ''}</span>`;
    d.onclick = () => selectAnim(a.key);
    el.appendChild(d);
  }
  if (list.length > LIMIT) {
    const d = document.createElement('div');
    d.className = 'item dim';
    d.textContent = `…还有 ${list.length - LIMIT} 条，请用搜索或分类筛选`;
    el.appendChild(d);
  }
  if (!list.length) el.innerHTML = '<div class="item dim">没有匹配的动画</div>';
}

function renderCatChips() {
  const counts = new Map();
  for (const a of state.animList) counts.set(a.category || '其他', (counts.get(a.category || '其他') || 0) + 1);
  const top = Array.from(counts.entries()).sort((x, y) => y[1] - x[1]).slice(0, 14);
  const el = $('#cat-chips');
  el.innerHTML = '';
  const mk = (label, val) => {
    const b = document.createElement('button');
    b.className = 'chip' + (state.catFilter === val ? ' active' : '');
    b.textContent = label;
    b.onclick = () => { state.catFilter = state.catFilter === val ? '' : val; renderCatChips(); rebuildAnimList(); };
    el.appendChild(b);
  };
  mk('全部', '');
  for (const [c, n] of top) mk(`${c} ${n}`, c);
}

async function selectAnim(key) {
  try {
    setLoading(true, `加载动画 ${key}…`);
    state.animKey = key;
    state.anim = await loader.loadAnim(key);
    state.frame = 0; state.time = 0;
    const a = state.anim;
    const s0 = a.start || 0;
    $('#timeline').min = String(s0);
    $('#timeline').max = String(Math.max(s0 + 1, a.frames - 1));
    $('#timeline').value = String(s0);
    const meta = a.meta || {};
    const secs = meta.duration || (meta.rate ? (a.frames - 1 - s0) / meta.rate : 0);
    $('#anim-info').textContent =
      `${a.frames} 帧${secs ? ' / ' + secs.toFixed(2) + 's' : ''}${meta.rate ? ' @' + meta.rate.toFixed(1) + 't/s' : ''}`;
    rebuildAnimList();
    setLoading(false);
  } catch (e) {
    setLoading(false);
    // 单个动画取不到不该把整个画面废掉：提示一下、退回上一个能用的动画
    console.error(e);
    toast(`动画 ${key} 加载失败：${e && e.message ? e.message : e}`, 6000);
    state.animKey = null;
  }
}

function tickAnim(dt) {
  if (!state.anim || !state.playing) return;
  const meta = state.anim.meta || {};
  const rate = meta.rate || 60;                    // t/秒（来自 AnimationClip.duration）
  const start = state.anim.start || 0;             // 跳过开头那帧绑定姿势
  const last = state.anim.frames - 1;
  const span = Math.max(1, last - start);
  state.time += dt * state.speed;
  let f = start + state.time * rate;
  if (state.looping) {
    f = start + ((f - start) % span);
  } else if (f >= last) {
    f = last; state.playing = false; $('#btn-play').textContent = '播放';
  }
  if (!Number.isFinite(f)) { state.time = 0; f = start; }
  state.frame = Math.max(start, Math.min(last, f));
  $('#timeline').value = String(state.frame);
  const sec = (state.frame - start) / rate;
  $('#time-label').textContent = `${sec.toFixed(2)}s`;
}

/* --------------------------------------------------------------------------- 渲染 */

function render() {
  const gl = renderer.gl;
  const light = LIGHTS[state.lightName] || LIGHTS.item;
  renderer.light.dir = V3.norm(light.dir);
  renderer.light.color = light.color;
  renderer.light.ambient = light.ambient;
  const bg = BGS[state.background] || BGS.dark;
  gl.clearColor(bg[0], bg[1], bg[2], 1);

  const eye = camEye();
  const aspect = canvas.width / Math.max(1, canvas.height);
  // 近远裁剪面按缩放自适应，避免人物被裁掉
  // 深度精度：near/far 的比例直接决定 z-fighting（皮套的身体与衣服常常贴得很近）。
  //   near 取 dist 的 0.12 倍足够包住模型，far 收紧到 3 倍 —— 比例 ~25:1，24 位深度绰绰有余。
  const d = state.cam.dist;
  const proj = M4.perspective(38 * Math.PI / 180, aspect,
                              Math.max(0.05, d * 0.12), Math.max(2, d * 3 + 10));
  const view = M4.orbit(eye, state.cam.target);

  renderer.beginFrame(proj, view, eye);

  if (state.rig && state.anim) {
    renderer.setBones(state.rig.update(state.anim, state.frame));
  } else if (state.rig) {
    renderer.setBones(state.rig.skin.map((_, i) => {
      const m = new Float32Array(16); m[0] = m[5] = m[10] = m[15] = 1; return m;
    }));
  }

  // 不透明/镂空先画（写深度），混合的后画
  const opaque = [], blended = [];
  for (const n of state.scene) {
    if (!n.visible) continue;
    const b = n.material.blendMode && n.material.blendMode !== 'no_alpha_blend';
    (b ? blended : opaque).push(n);
  }
  for (const n of opaque) renderNode(n, { skinned: true });
  for (const n of blended) renderNode(n, { skinned: true });

  if (state.showGrid) drawGrid();
  if (state.showBones && state.rig) drawBones();
}

function renderNode(n, opts) {
  const mat = state.noAlphaTest ? { ...n.material, alphaTestOn: false } : n.material;
  for (const g of n.gpu) {
    renderer.drawSub(g, mat, { skinned: opts.skinned, debugMode: state.debugMode });
  }
}

/* --------------------------------------------------------------------------- 地面网格 / 骨骼线 */

let lineProg = null;

function lineProgram() {
  if (lineProg) return lineProg;
  const vs = `#version 300 es
  in vec3 aPos; in vec3 aCol;
  uniform mat4 uProj, uView;
  out vec3 vCol;
  void main(){ vCol = aCol; gl_Position = uProj*uView*vec4(aPos,1.0); }`;
  const fs = `#version 300 es
  precision highp float; in vec3 vCol; out vec4 o;
  void main(){ o = vec4(vCol, 1.0); }`;
  lineProg = createProgram(gl, vs, fs, 'line');
  lineProg._buf = gl.createBuffer();
  lineProg._col = gl.createBuffer();
  return lineProg;
}

function drawLines(verts, cols, proj, view) {
  const p = lineProgram();
  gl.useProgram(p);
  gl.uniformMatrix4fv(p.u('uProj'), false, proj);
  gl.uniformMatrix4fv(p.u('uView'), false, view);
  gl.bindBuffer(gl.ARRAY_BUFFER, p._buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(verts), gl.DYNAMIC_DRAW);
  gl.enableVertexAttribArray(p.a('aPos'));
  gl.vertexAttribPointer(p.a('aPos'), 3, gl.FLOAT, false, 0, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, p._col);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(cols), gl.DYNAMIC_DRAW);
  gl.enableVertexAttribArray(p.a('aCol'));
  gl.vertexAttribPointer(p.a('aCol'), 3, gl.FLOAT, false, 0, 0);
  gl.disable(gl.DEPTH_TEST);
  gl.drawArrays(gl.LINES, 0, verts.length / 3);
  gl.enable(gl.DEPTH_TEST);
}

function drawGrid() {
  const v = [], c = [];
  const N = 10, S = 0.25, y = 0;
  for (let i = -N; i <= N; i++) {
    const t = i * S;
    const col = (i === 0) ? [0.45, 0.5, 0.6] : [0.22, 0.24, 0.28];
    v.push(-N * S, t, 0, N * S, t, 0, t, -N * S, 0, t, N * S, 0);
    for (let k = 0; k < 4; k++) c.push(...col);
  }
  const eye = camEye();
  const proj = M4.perspective(38 * Math.PI / 180, canvas.width / Math.max(1, canvas.height),
                              Math.max(0.05, state.cam.dist * 0.12), Math.max(2, state.cam.dist * 3 + 10));
  drawLines(v, c, proj, M4.orbit(eye, state.cam.target));
}

function drawBones() {
  const j = state.rig.currentJoints();
  const par = state.rig.parent;
  const v = [], c = [];
  for (let i = 0; i < par.length; i++) {
    const p = par[i];
    if (p < 0) continue;
    v.push(j[p][0], j[p][1], j[p][2], j[i][0], j[i][1], j[i][2]);
    const col = i >= 14 && i <= 20 ? [0.4, 0.9, 0.5] : (i >= 21 ? [0.95, 0.6, 0.4] : [0.85, 0.85, 0.95]);
    c.push(...col, ...col);
  }
  const eye = camEye();
  const proj = M4.perspective(38 * Math.PI / 180, canvas.width / Math.max(1, canvas.height),
                              Math.max(0.05, state.cam.dist * 0.12), Math.max(2, state.cam.dist * 3 + 10));
  drawLines(v, c, proj, M4.orbit(eye, state.cam.target));
}

/* --------------------------------------------------------------------------- UI 绑定 */

function renderEquipList() {
  const mf = state.manifest;
  const el = $('#equip-list');
  el.innerHTML = '';
  const groups = new Map();
  for (const it of mf.items || []) {
    if (!groups.has(it.slot)) groups.set(it.slot, []);
    groups.get(it.slot).push(it);
  }
  const order = ['Head', 'Cape', 'Body', 'Gloves', 'Leg', 'Item0', 'Other'];
  const label = { Head: '头部', Cape: '披风', Body: '身体', Gloves: '手', Leg: '腿脚', Item0: '手持', Other: '其他' };
  for (const slot of order) {
    const arr = groups.get(slot);
    if (!arr) continue;
    const g = document.createElement('div');
    g.className = 'eq-group';
    g.innerHTML = `<div class="gl">${label[slot] || slot}</div>`;
    for (const it of arr) {
      const row = document.createElement('label');
      row.className = 'eq';
      const cov = Object.keys(it.covers || {});
      row.innerHTML = `<input type="checkbox" ${state.equipped.has(it.id) ? 'checked' : ''}>
        <span class="nm" title="${it.id}">${it.name || it.id}</span>
        <span class="covers">${cov.length ? cov.join('/') : ''}</span>`;
      row.querySelector('input').onchange = e => {
        if (e.target.checked) state.equipped.add(it.id);
        else state.equipped.delete(it.id);
        applyEquipmentVisibility();
      };
      g.appendChild(row);
    }
    el.appendChild(g);
  }
  if (!(mf.items || []).length) el.innerHTML = '<p class="hint">这个 mod 没有 items.xml 装备定义。</p>';
}

/** 只重建「原版身体」那一部分 —— 换体型时不必重载 mod 的网格 */
async function rebuildVanilla() {
  const mf = state.manifest;
  state.scene = state.scene.filter(n => n.kind !== 'vanilla');
  const van = mf.vanilla || { parts: {} };
  const skinParts = activeSkin().parts || {};
  const meshToSemantic = {};
  for (const [sem, meshName] of Object.entries(skinParts)) meshToSemantic[meshName] = sem;
  const wantMeshes = new Set(Object.values(skinParts));
  for (const [meshName, info] of Object.entries(van.parts || {})) {
    if (!wantMeshes.has(meshName)) continue;
    const semantic = meshToSemantic[meshName] || meshName;
    try {
      const subs = await loader.loadMesh(info.file);
      for (const s of subs) {
        const node = {
          key: `vanilla/${semantic}/${s.meta.name}`, mesh: meshName, name: s.meta.name,
          kind: 'vanilla', group: semantic, partKey: semantic, item: null, subs: [s],
          material: materialOf(s.meta.material), visible: state.showVanilla,
        };
        node.gpu = renderer.upload(node.subs);
        for (const t of Object.values(node.material.textures || {})) {
          renderer.texture(t.file, await loader.loadTexture(t.file));
        }
        state.scene.push(node);
      }
    } catch (e) { console.warn('原版部件加载失败', meshName, e); }
  }
  applyEquipmentVisibility();
}

function renderSkinSelect() {
  const sel = $('#skin-select');
  if (!sel) return;
  const all = state.manifest.skins || {};
  sel.innerHTML = '';
  for (const [k, v] of Object.entries(all)) {
    const o = document.createElement('option');
    o.value = k; o.textContent = `${v.label || k}（${Object.keys(v.parts || {}).length} 部件）`;
    sel.appendChild(o);
  }
  const cur = state.skinName || state.manifest.skin?.name;
  if (cur) sel.value = cur;
  sel.onchange = async () => {
    state.skinName = sel.value;
    setLoading(true, '切换体型…');
    await rebuildVanilla();
    setLoading(false);
  };
}

function renderSkinList(equippedItems, hidden) {
  const el = $('#skin-list');
  if (!el) return;
  const parts = (state.manifest.vanilla || {}).parts || {};
  const skinParts = activeSkin().parts || {};
  el.innerHTML = '';
  for (const [k, meshName] of Object.entries(skinParts)) {
    if (!parts[meshName]) continue;
    const isHidden = hidden.has(k);
    const row = document.createElement('div');
    row.className = 'skin-row';
    row.innerHTML = `<span class="nm">${SKIN_LABEL[k] || k}</span>
      <span class="badge ${isHidden ? 'hidden' : 'exposed'}">${isHidden ? '已遮住' : '露出'}</span>`;
    el.appendChild(row);
  }
  if (!el.children.length) el.innerHTML = '<p class="hint">未加载原版身体部件。</p>';
}

const SKIN_LABEL = { body: '原版躯干', shoulders: '原版肩', legs: '原版脚', hands: '原版手',
                     face: '原版头/脸', underwear_bottom: '原版内裤', underwear_top: '原版内衣上' };

/** 当前选用的原版体型定义（默认取烘焙时定下的那个，可在 URL 里用 skin= 覆盖） */
function activeSkin() {
  const mf = state.manifest;
  const all = mf.skins || {};
  const want = state.skinName || mf.skin?.name;
  return all[want] || mf.skin || { name: '?', parts: {} };
}

function bindUI() {
  // 标签页
  $$('.tab').forEach(t => t.onclick = () => {
    $$('.tab').forEach(x => x.classList.toggle('active', x === t));
    $$('.tabpane').forEach(p => p.classList.toggle('active', p.id === 'pane-' + t.dataset.tab));
  });

  // 视角
  $$('#viewbar .vb[data-view]').forEach(b => b.onclick = () => applyView(b.dataset.view));
  $('#btn-bones').onclick = e => { state.showBones = !state.showBones; e.target.classList.toggle('on', state.showBones); };
  $('#btn-grid').onclick = e => { state.showGrid = !state.showGrid; e.target.classList.toggle('on', state.showGrid); };

  // 动画
  $('#anim-search').oninput = e => { state.query = e.target.value; rebuildAnimList(); };
  $('#btn-play').onclick = e => { state.playing = !state.playing; e.target.textContent = state.playing ? '暂停' : '播放'; };
  $('#btn-loop').onclick = e => { state.looping = !state.looping; e.target.classList.toggle('on', state.looping); };
  $('#timeline').oninput = e => {
    state.frame = parseFloat(e.target.value);
    const s0 = state.anim?.start || 0;
    state.time = state.anim?.meta?.rate ? (state.frame - s0) / state.anim.meta.rate : state.frame / 60;
    state.playing = false; $('#btn-play').textContent = '播放';
  };
  // 倍速：快捷按钮 + 数字框 + 滑块三种入口，统一走 setSpeed（并记住选择）。
  // 1.00× 的含义是"按游戏 AnimationClip 声明的时长播放" —— 但游戏内实际观感
  // 未必和这个数字一致，所以这里不纠结绝对正确，把调节权交给使用者。
  const sp = $('#speed'), spNum = $('#speed-num');
  window.setSpeed = (v, from) => {
    v = Math.min(10, Math.max(0.05, Number(v) || 1));
    state.speed = v;
    $('#speed-label').textContent = v.toFixed(2) + '×';
    if (from !== 'range') sp.value = String(Math.min(parseFloat(sp.max), v));
    if (from !== 'num') spNum.value = String(v);
    $$('#speed-chips .chip').forEach(b =>
      b.classList.toggle('active', Math.abs(parseFloat(b.dataset.speed) - v) < 1e-6));
    try { localStorage.setItem('mbpreview.speed', String(v)); } catch (_) {}
  };
  setSpeed(state.speed);
  sp.oninput = e => setSpeed(e.target.value, 'range');
  spNum.oninput = e => setSpeed(e.target.value, 'num');
  $$('#speed-chips .chip').forEach(b => b.onclick = () => setSpeed(b.dataset.speed));

  // 装备
  $('#show-vanilla').onchange = e => { state.showVanilla = e.target.checked; applyEquipmentVisibility(); };

  // 显示
  $$('#debug-chips .chip').forEach(b => b.onclick = () => {
    state.debugMode = parseInt(b.dataset.debug, 10);
    $$('#debug-chips .chip').forEach(x => x.classList.toggle('active', x === b));
  });
  $$('#light-chips .chip').forEach(b => b.onclick = () => {
    state.lightName = b.dataset.light;
    $$('#light-chips .chip').forEach(x => x.classList.toggle('active', x === b));
  });
  $$('#bg-chips .chip').forEach(b => b.onclick = () => {
    state.background = b.dataset.bg;
    $$('#bg-chips .chip').forEach(x => x.classList.toggle('active', x === b));
  });

  $('#btn-diag').onclick = runDiagnostics;

  // 鼠标
  let drag = null;
  canvas.addEventListener('pointerdown', e => {
    drag = { x: e.clientX, y: e.clientY, btn: e.button };
    canvas.classList.add('dragging');
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', e => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    drag.x = e.clientX; drag.y = e.clientY;
    if (drag.btn === 2 || e.shiftKey) {
      const s = state.cam.dist * 0.0016;
      const a = state.cam.az * Math.PI / 180;
      state.cam.target[0] -= (dx * Math.cos(a) - dy * Math.sin(a)) * s;
      state.cam.target[1] -= (dx * -Math.sin(a) - dy * Math.cos(a)) * s;
    } else {
      // 方向按使用者手感定：向右拖动 → 模型跟着往右转，向下拖动 → 视角往下走。
      // （这两个符号就是"拖拽方向是否跟随"的开关，反过来会让操作感觉很别扭。）
      state.cam.az += dx * 0.4;
      state.cam.el = Math.max(-85, Math.min(85, state.cam.el + dy * 0.35));
    }
  });
  canvas.addEventListener('pointerup', e => {
    drag = null; canvas.classList.remove('dragging');
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
  });
  canvas.addEventListener('contextmenu', e => e.preventDefault());
  canvas.addEventListener('wheel', e => {
    e.preventDefault();
    state.cam.dist = Math.max(0.15, Math.min(12, state.cam.dist * (1 + Math.sign(e.deltaY) * 0.11)));
  }, { passive: false });

  // 快捷键
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') { const d = document.getElementById('err'); if (d) d.remove(); return; }
    if (e.target.tagName === 'INPUT') return;
    const k = e.key.toLowerCase();
    if (k === ' ') { e.preventDefault(); $('#btn-play').click(); }
    else if (k === 'b') $('#btn-bones').click();
    else if (k === 'g') $('#btn-grid').click();
    else if (k === '1') applyView('front');
    else if (k === '2') applyView('left');
    else if (k === '3') applyView('back');
    else if (k === '4') applyView('face');
  });
}

/* --------------------------------------------------------------------------- 诊断 */

function runDiagnostics() {
  const mf = state.manifest;
  const out = [];
  const ok = s => out.push(`<span class="ok">✓</span> ${s}`);
  const bad = s => out.push(`<span class="bad">✗</span> ${s}`);
  const warn = s => out.push(`<span class="warn">!</span> ${s}`);

  out.push(`<b>${mf.mod}</b>  顶点 ${mf.stats.vertices} / 三角 ${mf.stats.triangles}`);
  out.push('');

  // 1) 几何与蒙皮
  const a = mf.audit || {};
  out.push('<b>— 蒙皮 —</b>');
  if (a.weightsum_ok_ratio >= 0.999) ok(`每顶点 4×u8 权重和 ==255：${(a.weightsum_ok_ratio * 100).toFixed(2)}%`);
  else bad(`权重和 ==255 仅 ${(a.weightsum_ok_ratio * 100).toFixed(2)}% —— 实机会退化成一整块跟随骨盆（模型像刚体一起晃）`);
  if ((a.max_bone_index ?? -1) <= 27) ok(`骨骼索引最大值 ${a.max_bone_index} ≤ 27`);
  else bad(`骨骼索引最大值 ${a.max_bone_index} > 27 —— 超出人形骨架，蒙皮会错乱`);

  // 2) 材质
  out.push('', '<b>— 材质 —</b>');
  for (const [name, m] of Object.entries(mf.materials)) {
    const bits = [`${m.blendMode}`, m.alphaTestOn ? `alphaTest=${m.alphaTest.toFixed(3)}` : '',
                  m.twoSided ? '双面' : ''].filter(Boolean).join(' ');
    if (!m.skinning) bad(`${name}: ${bits} —— vertexLayoutFlags 缺 skinning，网格不会跟骨骼动`);
    else ok(`${name}: ${bits}`);
  }

  // 3) 贴图
  out.push('', '<b>— 贴图 —</b>');
  for (const [name, t] of Object.entries(mf.textures)) {
    ok(`${name} ${t.width}×${t.height} ${t.format}${t.hasAlpha ? ' (has_alpha)' : ''}`);
  }

  // 4) 原版身体遮盖
  out.push('', '<b>— 原版身体遮盖 —</b>');
  const equippedItems = (mf.items || []).filter(it => state.equipped.has(it.id));
  const hidden = state.hiddenMeshes;
  const skinParts = activeSkin().parts || {};
  for (const [k, meshName] of Object.entries(skinParts)) {
    if (!(mf.vanilla?.parts || {})[meshName]) continue;
    if (hidden.has(k)) ok(`${SKIN_LABEL[k] || k}：已被遮住`);
    else warn(`${SKIN_LABEL[k] || k}：<b>会露出来</b>（没有任何装备的 covers_* 覆盖它）`);
  }
  out.push(`  已装备 ${equippedItems.length} 件：${equippedItems.map(i => i.id).join(', ') || '（无）'}`);

  // 5) 动画
  out.push('', '<b>— 动画 —</b>');
  ok(`已烘焙 ${mf.anims.length} 个，目录 ${mf.catalogSummary.count} 条 / 动作类型 ${mf.catalogSummary.actionCount}`);
  if (state.anim) {
    const meta = state.anim.meta || {};
    ok(`当前 ${state.animKey}：${state.anim.frames} 帧`
      + (meta.duration ? ` / ${meta.duration.toFixed(2)}s（速率 ${meta.rate.toFixed(1)} t/s）` : '（无权威时长，按 60t/s 播放）'));
  }

  // 6) 运行时统计
  out.push('', '<b>— 运行时 —</b>');
  const dg = renderer.diag || {};
  ok(`渲染器：${dg.renderer || '?'}`);
  ok(`顶点 uniform 上限 ${dg.uniformVectors ?? '?'} 个 vec4（本工具用 ${(dg.maxBones||28)*4}）`
     + (dg.uniformOk ? '' : ' <b class="bad">—— uBones 定位失败！</b>'));
  ok(`MSAA 采样数 ${dg.msaa ?? '?'}`);
  ok(`可见网格 ${state.scene.filter(n => n.visible).length}/${state.scene.length}`);
  ok(`本帧 ${renderer.stats.drawCalls} 次绘制 / ${(renderer.stats.triangles | 0).toLocaleString()} 三角`);

  $('#diag-out').innerHTML = out.join('<br>');
}

/* --------------------------------------------------------------------------- 启动 */

async function fetchMods() {
  const r = await fetch('api/mods');
  return r.json();
}

async function loadMod(name) {
  try {
    await loadModInner(name);
  } catch (e) {
    setLoading(false);           // ★ 无论怎么失败都不能把加载遮罩留在屏幕上
    fatal(e);
  }
}

async function loadModInner(name) {
  setLoading(true, `加载 ${name}…`);
  const mf = await loader.loadManifest(name);
  state.manifest = mf;
  state.rig = new Rig(mf.skeleton);
  state.animList = mf.anims || [];

  // 默认装备：全部 mod 装备
  const eqParam = params.get('equip');
  state.equipped = new Set();
  if (eqParam === 'none') { /* 空 */ }
  else if (eqParam && eqParam !== 'all') {
    for (const id of eqParam.split(',')) if (id.trim()) state.equipped.add(id.trim());
  } else {
    for (const it of mf.items || []) state.equipped.add(it.id);
  }

  await buildScene();
  renderEquipList();
  renderSkinSelect();
  renderCatChips();
  rebuildAnimList();

  const want = state.animKey || (state.animList.find(a => a.key === 'inventory_idle') || state.animList[0] || {}).key;
  if (want) await selectAnim(want);

  // URL 指定的初始帧（AI 出图用）—— 必须在 selectAnim 之后设，否则会被重置为 0
  const initFrame = parseFloat(params.get('frame') ?? 'NaN');
  if (!Number.isNaN(initFrame)) {
    const s0 = state.anim?.start || 0;
    state.frame = Math.max(s0, Math.min((state.anim?.frames || 1) - 1, initFrame));
    state.time = state.anim?.meta?.rate ? (state.frame - s0) / state.anim.meta.rate : state.frame / 60;
    state.playing = false;
    $('#btn-play').textContent = '播放';
    $('#timeline').value = String(state.frame);
  }
  applyView(params.get('view') || 'front');
  setLoading(false);
}

async function main() {
  canvas = $('#gl');
  try {
    renderer = new Renderer(canvas);
  } catch (e) { fatal(e); return; }
  gl = renderer.gl;
  loader = new Loader('data/');   // 所有 file 字段都相对 data/

  bindUI();

  window.addEventListener('resize', () => {
    const r = canvas.parentElement.getBoundingClientRect();
    // 面板动画/折叠的瞬间可能量到 0 尺寸；此时若把画布设成 1x1，画面就"消失"了
    if (r.width < 8 || r.height < 8) return;
    renderer.setCanvasSize(r.width, r.height, Math.min(2, window.devicePixelRatio || 1));
  });
  window.dispatchEvent(new Event('resize'));

  let mods = [];
  try { mods = await fetchMods(); } catch (e) { console.warn(e); }
  const sel = $('#mod-select');
  sel.innerHTML = '';
  for (const m of mods) {
    const o = document.createElement('option');
    o.value = m.name; o.textContent = m.name;
    sel.appendChild(o);
  }
  sel.onchange = () => { location.search = `?mod=${encodeURIComponent(sel.value)}`; };

  const first = state.mod || (mods[0] && mods[0].name);
  if (!first) { setLoading(false); toast('没有找到可预览的 mod。先跑：mbpreview bake <mod>'); return; }
  sel.value = first;
  await loadMod(first);

  // URL 指定的初始状态
  if (params.get('bones') === '1') $('#btn-bones').classList.add('on');
  if (params.get('debug')) {
    $$('#debug-chips .chip').forEach(x => x.classList.toggle('active', x.dataset.debug === params.get('debug')));
  }
  if (params.get('frame')) {
    state.playing = false; $('#btn-play').textContent = '播放';
  }
  document.title = `皮套预览器 · ${first}`;
  window.__ready = true;      // 供 headless 截图判断加载完成
  // 供 AI 查询/驱动的调试接口（headless 环境下用它验证相机、倍速、可见性等状态）
  window.__preview = {
    state,
    get cam() { return state.cam; },
    get frame() { return state.frame; },
    get speed() { return state.speed; },
    get visibleMeshes() { return state.scene.filter(n => n.visible).length; },
    setSpeed: (v) => window.setSpeed(v),
    view: (n) => applyView(n),
  };

  lastT = performance.now();
  requestAnimationFrame(loop);
}

let fpsAcc = 0, fpsN = 0, fpsT = 0;
function loop(now) {
  const dt = Math.min(0.1, (now - lastT) / 1000);
  lastT = now;
  try {
    tickAnim(dt);
    render();
    fpsAcc += dt; fpsN++;
    if (fpsAcc > 0.5) {
      $('#hud-fps').textContent = `${(fpsN / fpsAcc).toFixed(0)} fps`;
      const vis = state.scene.filter(n => n.visible).length;
      const bad = !Number.isFinite(state.frame) || vis === 0;
      $('#hud-stats').innerHTML =
        `${state.animKey || '—'}  帧 ${Number.isFinite(state.frame) ? state.frame.toFixed(0) : 'NaN'}  |  ` +
        `${renderer.stats.drawCalls} 绘制 / ${(renderer.stats.triangles | 0).toLocaleString()} 三角` +
        (bad ? '  <b style="color:#e06c75">⚠ 无可见网格</b>' : '');
      fpsAcc = 0; fpsN = 0;
    }
  } catch (e) {
    fatal(e);
    return;
  }
  requestAnimationFrame(loop);
}

main().catch(fatal);
