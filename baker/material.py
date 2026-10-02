# -*- coding: utf-8 -*-
"""贴图与材质。

贴图来源是 exportmod 产出的 tex/*.bin —— **原始 BC 压缩字节 + mip 表**，不是 PNG。
本模块负责解码成 RGBA，并做两件影响观感的事：

  1. **alpha bleed（透明区填色）**：镂空贴图（发丝/睫毛/网格布）的透明像素 RGB 往往是白或黑。
     若不处理，浏览器生成 mipmap 时会把白/黑平均进来，实机表现是「发丝发白/发灰」或「边缘黑边」。
     做法：把透明像素的 RGB 用最近的不透明像素中位色迭代扩散填充，只改 RGB 不动 A。
  2. **V 轴不翻**：本源 UV 是 top-origin（v=0 = 贴图顶行），而 WebGL 的 glTexImage2D
     第一行数据正好落在 t=0 —— 原样上传即正确。翻图会导致五官整体上下错位。

材质参数的语义（从 1862 个原版材质统计得出，渲染器据此决策）：
  blendMode   no_alpha_blend / factor / modulate / add / ...
  alphaTest   >0 且 shaderMatFlags 含 alpha_test ⇒ 镂空
  flags       two_sided（双面）/ cull_front_faces / no_depth_test / ...
  vertexLayoutFlags 含 skinning 才跟骨骼动
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

# --------------------------------------------------------------------------- BC 解码

def _rgb565(c: np.ndarray) -> np.ndarray:
    r = ((c >> 11) & 0x1F).astype(np.uint16)
    g = ((c >> 5) & 0x3F).astype(np.uint16)
    b = (c & 0x1F).astype(np.uint16)
    out = np.empty((len(c), 3), np.uint8)
    out[:, 0] = (r << 3) | (r >> 2)
    out[:, 1] = (g << 2) | (g >> 4)
    out[:, 2] = (b << 3) | (b >> 2)
    return out


def _bc1_colors(block: np.ndarray) -> np.ndarray:
    """block: (n,8) uint8 → (n,4,3) uint8 调色板（含 BC1 的 3/4 色模式）。"""
    c0 = block[:, 0].astype(np.uint16) | (block[:, 1].astype(np.uint16) << 8)
    c1 = block[:, 2].astype(np.uint16) | (block[:, 3].astype(np.uint16) << 8)
    pal = np.zeros((len(block), 4, 3), np.float32)
    pal[:, 0] = _rgb565(c0)
    pal[:, 1] = _rgb565(c1)
    opaque = c0 > c1
    pal[:, 2] = np.where(opaque[:, None], (2 * pal[:, 0] + pal[:, 1]) / 3,
                         (pal[:, 0] + pal[:, 1]) / 2)
    pal[:, 3] = np.where(opaque[:, None], (pal[:, 0] + 2 * pal[:, 1]) / 3, 0.0)
    return np.clip(pal + 0.5, 0, 255).astype(np.uint8), opaque


def _expand_indices(bits: np.ndarray) -> np.ndarray:
    """把 4 字节小端 bit 串展开成每像素 2 bit 的索引 (n,16)。"""
    n = len(bits)
    b = bits.astype(np.uint32)
    v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16) | (b[:, 3] << 24))
    shifts = (np.arange(16, dtype=np.uint32) * 2)[None, :]
    return ((v[:, None] >> shifts) & 0x3).astype(np.uint8)


def _bc4_channel(block: np.ndarray) -> np.ndarray:
    """block: (n,8) uint8 → (n,16) uint8 插值后的单通道。"""
    r0 = block[:, 0].astype(np.int32)
    r1 = block[:, 1].astype(np.int32)
    bits = block[:, 2:8]
    n = len(block)
    idx = np.zeros((n, 16), np.int32)
    b = bits.astype(np.uint32)
    v0 = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    v1 = b[:, 3] | (b[:, 4] << 8) | (b[:, 5] << 16)
    for k in range(8):
        idx[:, k] = (v0 >> (3 * k)) & 0x7
        idx[:, 8 + k] = (v1 >> (3 * k)) & 0x7
    pal = np.zeros((n, 8), np.int32)
    pal[:, 0] = r0
    pal[:, 1] = r1
    small = r0 <= r1
    for i in range(2, 8):
        f = i - 1
        big = ((8 - f) * r0 + f * r1 + 3) // 7
        sm = ((6 - f) * r0 + f * r1 + 2) // 5 if f <= 5 else 0
        pal[:, i] = np.where(small, sm, big)
    pal[:, 6] = np.where(small, 0, pal[:, 6])
    pal[:, 7] = np.where(small, 255, pal[:, 7])
    return pal[np.arange(n)[:, None], idx].astype(np.uint8)   # n 已在上面由 len(block) 定义


def _blocks_to_img(vals: np.ndarray, w: int, h: int, channels: int) -> np.ndarray:
    """vals: (nblock, 16, channels) → (h,w,channels)；按 4x4 块铺回。"""
    bw, bh = (w + 3) // 4, (h + 3) // 4
    arr = vals.reshape(bh, bw, 4, 4, channels)
    img = arr.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, channels)
    return img[:h, :w]


def decode_bc(data: bytes, w: int, h: int, fmt: str) -> np.ndarray:
    """返回 (h,w,4) uint8 RGBA。"""
    f = fmt.upper()
    buf = np.frombuffer(data, np.uint8)
    if f in ("DXT1", "BC1"):
        blk = buf[: (len(buf) // 8) * 8].reshape(-1, 8)
        pal, opaque = _bc1_colors(blk)                 # (n,4,3)
        idx = _expand_indices(blk[:, 4:8])             # (n,16)
        rgb = pal[np.arange(len(blk))[:, None], idx]   # (n,16,3)
        # c0<=c1 时是 3 色 + 透明模式，索引 3 表示透明；否则全不透明
        alpha = np.where(opaque[:, None], 255, np.where(idx == 3, 0, 255)).astype(np.uint8)
        img = _blocks_to_img(rgb, w, h, 3)
        aimg = _blocks_to_img(alpha[:, :, None], w, h, 1)
        return np.concatenate([img, aimg], 2)
    if f in ("DXT5", "BC3"):
        blk = buf[: (len(buf) // 16) * 16].reshape(-1, 16)
        alpha = _bc4_channel(blk[:, :8])               # (n,16)
        pal, _ = _bc1_colors(blk[:, 8:16])
        idx = _expand_indices(blk[:, 12:16])
        rgb = pal[np.arange(len(blk))[:, None], idx]
        img = _blocks_to_img(rgb, w, h, 3)
        aimg = _blocks_to_img(alpha[:, :, None], w, h, 1)
        return np.concatenate([img, aimg], 2)
    if f in ("ATI2", "BC5"):
        blk = buf[: (len(buf) // 16) * 16].reshape(-1, 16)
        r = _bc4_channel(blk[:, :8])
        g = _bc4_channel(blk[:, 8:16])
        img = _blocks_to_img(np.stack([r, g], -1), w, h, 2)
        return np.concatenate([img, np.full(img.shape[:2] + (1,), 255, np.uint8),
                               np.full(img.shape[:2] + (1,), 255, np.uint8)], 2)
    if f in ("BC4", "ATI1"):
        blk = buf[: (len(buf) // 8) * 8].reshape(-1, 8)
        r = _bc4_channel(blk)
        img = _blocks_to_img(r[:, :, None], w, h, 1)
        return np.concatenate([img, img, img, np.full(img.shape[:2] + (1,), 255, np.uint8)], 2)
    # 未压缩格式（LVBU and DIAOCHAN 里有 R8G8B8A8_UNORM）
    RAW = {"R8G8B8A8_UNORM": 4, "R8G8B8A8_UNORM_SRGB": 4, "B8G8R8A8_UNORM": 4,
           "R8G8B8_UNORM": 3, "B8G8R8_UNORM": 3, "R8_UNORM": 1, "A8_UNORM": 1,
           "R16G16B16A16_FLOAT": 8, "R16G16B16A16_UNORM": 8, "R32_FLOAT": 4}
    if f in RAW:
        ch = RAW[f]
        need = w * h * ch
        if len(buf) < need:
            raise ValueError(f"{fmt}: 数据不足（需要 {need} 字节，只有 {len(buf)}）")
        a = buf[:need].reshape(h, w, ch)
        if ch == 4:
            if f.startswith("B8G8R8A8"):
                a = a[..., [2, 1, 0, 3]]
            return np.ascontiguousarray(a)
        if ch == 3:
            if f.startswith("B8G8R8"):
                a = a[..., [2, 1, 0]]
            return np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], 2)
        if ch == 1:
            return np.concatenate([a, a, a, np.full(a.shape[:2] + (1,), 255, np.uint8)], 2)
        # 16/32 位浮点：线性映射到 8 位（够预览用）
        v = a.astype(np.float32)
        if f.endswith("_FLOAT"):
            v = np.clip(v, 0.0, 1.0) * 255.0
        else:
            v = np.clip(v / 257.0, 0, 255)
        v = v.astype(np.uint8)
        if v.shape[2] == 3:
            v = np.concatenate([v, np.full(v.shape[:2] + (1,), 255, np.uint8)], 2)
        return v
    raise ValueError(f"暂不支持的贴图格式: {fmt}")


# --------------------------------------------------------------------------- alpha bleed

def alpha_bleed(rgba: np.ndarray, rounds: int = 8) -> np.ndarray:
    """把透明像素的 RGB 用邻近不透明像素填充（只改 RGB，不动 A）。

    不做这件事，缩小时的 mipmap 会把透明区的白/黑平均进边缘 —— 实机表现是发丝发白或黑边，
    而近距离看 mip0 完全正常，极易误判。
    """
    a = rgba[..., 3]
    if a.min() == 255 or (a == 0).sum() == 0:
        return rgba
    out = rgba.copy()
    filled = a > 0
    rgb = out[..., :3].astype(np.float32)
    for _ in range(rounds):
        if filled.all():
            break
        # 用已填充像素的 3x3 均值去填未填充的
        acc = np.zeros_like(rgb)
        cnt = np.zeros(filled.shape, np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                s = np.roll(np.roll(rgb, dy, 0), dx, 1)
                m = np.roll(np.roll(filled, dy, 0), dx, 1)
                acc += s * m[..., None]
                cnt += m
        new = (~filled) & (cnt > 0)
        if not new.any():
            break
        rgb[new] = acc[new] / cnt[new][..., None]
        filled = filled | new
    out[..., :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
    return out


# --------------------------------------------------------------------------- 贴图导出

# 预览用的贴图尺寸上限。4096² 的 RGBA 光解压就 64MB，而预览窗口通常只有几百像素宽 ——
# 降到 2048 后解码与 GPU 上传都快 4 倍，肉眼几乎看不出差别。需要看细节时可调高
# （环境变量 MB_PREVIEW_MAX_TEX 或 bake(max_tex=...)）。
MAX_TEX = int(__import__("os").environ.get("MB_PREVIEW_MAX_TEX", "2048"))


def export_texture(entry: dict, tex_dir: Path, out_dir: Path,
                   max_size: int | None = None) -> dict:
    """把一条 texture 记录解出来写成 PNG，返回给查看器用的元数据。"""
    src = tex_dir / Path(entry["file"]).name
    raw = src.read_bytes()
    w, h = int(entry["width"]), int(entry["height"])
    fmt = entry.get("format", "DXT1")
    off = 0
    if entry.get("mipOffset"):
        off = int(entry["mipOffset"][0])
    size = int(entry["mipSize"][0]) if entry.get("mipSize") else len(raw) - off
    rgba = decode_bc(raw[off:off + size], w, h, fmt)
    rgba = alpha_bleed(rgba)          # ★ 必须在缩放之前做，否则透明区的白/黑会被平均进边缘
    limit = MAX_TEX if max_size is None else max_size
    ow, oh = w, h
    if limit and max(w, h) > limit:
        scale = limit / float(max(w, h))
        nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        rgba = np.asarray(Image.fromarray(rgba, "RGBA").resize((nw, nh), Image.LANCZOS))
        w, h = nw, nh
    name = entry["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(out_dir / f"{name}.png", optimize=False)
    # ★ alpha 分布：给「这个材质到底算不算半透明」提供依据。
    #   实测曹操的脸（caocaoc）贴图 alpha 全是 1.0 却因为 blendMode=factor
    #   被当成半透明 → 不写深度 → 双面材质的内表面盖住外表面（看到后脑勺内壳）。
    a = rgba[:, :, 3].astype(np.float32) / 255.0
    return dict(name=name, width=w, height=h, srcWidth=ow, srcHeight=oh, format=fmt,
                hasAlpha=("has_alpha" in (entry.get("systemFlags") or [])),
                srcMips=int(entry.get("mipCount") or 1),
                alphaMedian=float(np.median(a)),
                alphaLow=float((a < 0.5).mean()))


# --------------------------------------------------------------------------- 材质

SLOT_ROLE = {0: "albedo", 1: "second", 2: "normal", 3: "detail", 4: "specular"}


def simplify_material(m: dict) -> dict:
    """把材质参数翻译成渲染器能直接用的语义标志。"""
    flags = set(m.get("flags") or [])
    sflags = set(m.get("shaderMatFlags") or [])
    layers = set(m.get("vertexLayoutFlags") or [])
    blend = m.get("blendMode") or "no_alpha_blend"
    alpha_test = float(m.get("alphaTest") or 0.0)

    slots = {SLOT_ROLE.get(int(k), f"slot{k}"): v
             for k, v in (m.get("textures") or {}).items() if v}
    # 0=反照率 1=第二色/遮罩 2=法线 4=高光
    return dict(
        name=m["name"], guid=m.get("guid", ""), blendMode=blend, alphaTest=alpha_test,
        twoSided=("two_sided" in flags),
        cullFront=("cull_front_faces" in flags),
        depthWrite=("no_modify_depth_buffer" not in flags) and blend == "no_alpha_blend",
        noDepthTest=("no_depth_test" in flags),
        castShadow=("dont_cast_shadow" not in flags),
        alphaTestOn=("alpha_test" in sflags),
        useAlphaOfAlbedo=("use_albedo_alpha" in sflags),
        useVertexColor=("use_vertex_colors" in sflags),
        vertexColorAlpha=("disable_vertex_color_alpha" not in sflags),
        useSpecular=("use_specular" in sflags),
        specFromDiffuse=("use_specular_from_diffuse" in sflags),
        doubleColorMask=("use_double_colormap_with_mask_texture" in sflags),
        skinning=("skinning" in layers),
        bumpmap=("bumpmap" in layers),
        textures=slots,
        raw=dict(flags=sorted(flags), shaderMatFlags=sorted(sflags),
                 vertexLayoutFlags=sorted(layers)),
    )


def load_packjson(export_dir: Path) -> dict:
    return json.loads((export_dir / "pack.json").read_text(encoding="utf-8"))
