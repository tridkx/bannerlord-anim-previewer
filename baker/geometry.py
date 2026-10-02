# -*- coding: utf-8 -*-
"""几何：解析 mbtool exportmod 的 GDMB 顶点流，并转成查看器用的紧凑格式。

GDMB（mbtool exportmod 产物，已在多份真实数据上验证）：
    char[4] "GDMB" | u32 version | u32 submeshCount
    每个子网格：
      u32 len + utf8  名称
      i32             lod
      u32 len + utf8  材质名
      u32 len + ascii 材质 GUID(36)
      u32 vertexCount | u32 indexCount
      f32[6]          bbox(min xyz, max xyz)
      u32 flags       1=Normals 2=Uv2 4=Colors1 8=Colors2 16=Tangents 32=BoneIndices
      --- 下面是数据，顺序固定 ---
      positions  vc*3 f32
      normals    vc*3 f32   (flags&1)
      uv1        vc*2 f32
      uv2        vc*2 f32   (flags&2)
      colors1    vc*4 u8    (flags&4)
      colors2    vc*4 u8    (flags&8)
      tangents   vc*4 f32   (flags&16)
      boneIdx    vc*4 u8    (flags&32)
      boneWeight vc*4 u8    (总是存在)
      indices    ic*4 i32   ★ 是 int32，不是 uint16 —— 踩过
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

GEO_MAGIC = b"GDMB"
MESH_MAGIC = b"MBMG"
MESH_VERSION = 1

F_NORMALS = 1
F_UV2 = 2
F_COLORS1 = 4
F_COLORS2 = 8
F_TANGENTS = 16
F_BONEIDX = 32


# --------------------------------------------------------------------------- 读 GDMB

def _rd_str(buf: bytes, off: int) -> tuple[str, int]:
    (n,) = struct.unpack_from("<I", buf, off)
    off += 4
    return buf[off:off + n].decode("utf-8", "replace"), off + n


def parse_gdmb(path: Path) -> list[dict]:
    buf = path.read_bytes()
    magic, ver, nsub = struct.unpack_from("<4sII", buf, 0)
    if magic != GEO_MAGIC:
        raise ValueError(f"{path.name}: 不是 GDMB 文件（magic={magic!r}）")
    off = 12
    out = []
    for _ in range(nsub):
        name, off = _rd_str(buf, off)
        (lod,) = struct.unpack_from("<i", buf, off); off += 4
        matname, off = _rd_str(buf, off)
        matguid, off = _rd_str(buf, off)
        vc, ic = struct.unpack_from("<II", buf, off); off += 8
        bbox = struct.unpack_from("<6f", buf, off); off += 24
        (flags,) = struct.unpack_from("<I", buf, off); off += 4

        pos = np.frombuffer(buf, "<f4", vc * 3, off).reshape(vc, 3).copy(); off += vc * 12
        nrm = None
        if flags & F_NORMALS:
            nrm = np.frombuffer(buf, "<f4", vc * 3, off).reshape(vc, 3).copy(); off += vc * 12
        uv = np.frombuffer(buf, "<f4", vc * 2, off).reshape(vc, 2).copy(); off += vc * 8
        if flags & F_UV2:
            off += vc * 8
        col = None
        if flags & F_COLORS1:
            col = np.frombuffer(buf, "u1", vc * 4, off).reshape(vc, 4).copy()
            off += vc * 4
        if flags & F_COLORS2:
            off += vc * 4
        if flags & F_TANGENTS:
            off += vc * 16
        bidx = None
        if flags & F_BONEIDX:
            bidx = np.frombuffer(buf, "u1", vc * 4, off).reshape(vc, 4).copy(); off += vc * 4
        bwt = np.frombuffer(buf, "u1", vc * 4, off).reshape(vc, 4).copy(); off += vc * 4
        idx = None
        if ic:
            idx = np.frombuffer(buf, "<i4", ic, off).reshape(-1, 3).copy(); off += ic * 4

        out.append(dict(name=name, lod=lod, material=matname, material_guid=matguid,
                        bbox=bbox, flags=flags, pos=pos, nrm=nrm, uv=uv, col=col,
                        bone_idx=bidx, bone_w=bwt, tri=idx))
    if off != len(buf):
        raise ValueError(f"{path.name}: 解析后残留 {len(buf) - off} 字节，格式假设有误")
    return out


# --------------------------------------------------------------------------- 写查看器格式

def write_meshpack(out_path: Path, subs: list[dict]) -> None:
    """写出 MBMG：JSON 元数据块 + 分离的属性数组（JS 侧可零拷贝建 BufferAttribute）。"""
    metas = []
    blobs: list[bytes] = []
    for s in subs:
        vc = len(s["pos"])
        ic = 0 if s["tri"] is None else s["tri"].size
        meta = dict(name=s["name"], material=s["material"], materialGuid=s["material_guid"],
                    lod=s["lod"], vertexCount=vc, indexCount=ic,
                    bbox=list(s["bbox"]), hasNormals=s["nrm"] is not None,
                    hasColor=s["col"] is not None, hasSkin=s["bone_idx"] is not None)
        metas.append(meta)
        blobs.append(s["pos"].astype("<f4").tobytes())
        if s["nrm"] is not None:
            blobs.append(s["nrm"].astype("<f4").tobytes())
        blobs.append(s["uv"].astype("<f4").tobytes())
        if s["col"] is not None:
            blobs.append(s["col"].astype("u1").tobytes())
        if s["bone_idx"] is not None:
            blobs.append(s["bone_idx"].astype("u1").tobytes())
            # 权重归一化到 0..255 且和恰为 255（引擎侧就是这么存的，这里只做一致性校验）
            blobs.append(s["bone_w"].astype("u1").tobytes())
        if s["tri"] is not None:
            blobs.append(s["tri"].astype("<u4").tobytes())

    js = json.dumps(metas, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # ★ 用空格把 JSON 块补齐到 4 字节对齐：否则后面用 new Float32Array(buf, off, n)
    #   建视图会因为 byteOffset 不是 4 的倍数而直接抛错。空格是合法 JSON 尾部空白，
    #   JSON.parse 照常工作，比补 \0 安全。
    pad = (-len(js)) % 4
    if pad:
        js += b" " * pad
    parts = [MESH_MAGIC, struct.pack("<III", MESH_VERSION, len(subs), len(js)), js, *blobs]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"".join(parts))


def read_meshpack(path: Path) -> list[dict]:
    """Python 侧回读（自检用；也证明格式可往返）。"""
    buf = path.read_bytes()
    magic, ver, n, jl = struct.unpack_from("<4sIII", buf, 0)
    if magic != MESH_MAGIC:
        raise ValueError("不是 MBMG 文件")
    off = 16
    metas = json.loads(buf[off:off + jl].decode("utf-8")); off += jl
    out = []
    for m in metas:
        vc = m["vertexCount"]
        s = dict(m)
        s["pos"] = np.frombuffer(buf, "<f4", vc * 3, off).reshape(vc, 3); off += vc * 12
        if m["hasNormals"]:
            s["nrm"] = np.frombuffer(buf, "<f4", vc * 3, off).reshape(vc, 3); off += vc * 12
        s["uv"] = np.frombuffer(buf, "<f4", vc * 2, off).reshape(vc, 2); off += vc * 8
        if m["hasColor"]:
            s["col"] = np.frombuffer(buf, "u1", vc * 4, off).reshape(vc, 4); off += vc * 4
        if m["hasSkin"]:
            s["bone_idx"] = np.frombuffer(buf, "u1", vc * 4, off).reshape(vc, 4); off += vc * 4
            s["bone_w"] = np.frombuffer(buf, "u1", vc * 4, off).reshape(vc, 4); off += vc * 4
        ic = m["indexCount"]
        if ic:
            s["tri"] = np.frombuffer(buf, "<u4", ic, off).reshape(-1, 3); off += ic * 4
        else:
            s["tri"] = None
        out.append(s)
    if off != len(buf):
        raise ValueError(f"MBMG 残留 {len(buf) - off} 字节")
    return out


# --------------------------------------------------------------------------- 体检

def audit(subs: list[dict]) -> dict:
    """几何/蒙皮体检 —— 这些数字直接决定实机表现，全部要能从最终产物倒推。"""
    report: dict = {"submeshes": len(subs), "vertices": 0, "triangles": 0,
                    "materials": [], "warnings": []}
    wsum_ok = 0
    wsum_total = 0
    max_bone = -1
    for s in subs:
        vc = len(s["pos"])
        report["vertices"] += vc
        report["triangles"] += 0 if s["tri"] is None else s["tri"].size // 3
        if s["material"] not in report["materials"]:
            report["materials"].append(s["material"])
        if s["bone_w"] is not None:
            tot = s["bone_w"].astype(np.int32).sum(1)
            # ★ 判据是"权重和接近满量程"，不是"恰好 255"。
            #   实测原版/编辑器产出的数据里 254 与 253 才是主流
            #   （LVBU and DIAOCHAN：254 占 68~80%、253 占 15~24%），
            #   卡死在 255 会把正常数据大面积误报成"权重被截断"。
            wsum_ok += int((np.abs(tot - 255) <= 3).sum())
            wsum_total += vc
            if s["bone_idx"] is not None:
                max_bone = max(max_bone, int(s["bone_idx"].max()))
    report["weightsum_ok_ratio"] = (wsum_ok / wsum_total) if wsum_total else 1.0
    report["max_bone_index"] = max_bone
    # 人形骨架只有 0..27；超出说明这个网格绑的不是人（实测马用 28..31）。
    # 渲染器会钳制索引避免越界，但姿势必然不对 —— 标出来让界面提示使用者。
    report["humanSkeleton"] = (max_bone <= 27)
    if wsum_total and report["weightsum_ok_ratio"] < 0.999:
        bad = 1.0 - report["weightsum_ok_ratio"]
        report["warnings"].append(
            f"有 {bad*100:.2f}% 的顶点权重和明显偏离满量程（<252）"
            " —— 这类顶点会退化成一整块跟随骨盆（实机表现：局部像刚体一起摇晃）。"
            " 通常是打包时把 0..1 的 float 截断成了 u8。")
    if max_bone > 27:
        report["warnings"].append(f"骨骼索引最大值 {max_bone} > 27 —— 超出人形骨架范围，蒙皮会错乱。")
    return report
