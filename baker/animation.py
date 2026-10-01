# -*- coding: utf-8 -*-
"""动画：解析 mbtool anim 的产物 → 查看器用的规整二进制，并推导真实播放速率。

已验证的解码约定（本项目独立验证，判据见 tools/verify.py）：
    每骨只有旋转（除根骨），且 q 是**相对父骨的局部旋转**。
    M_i(t) = M_parent(t) @ [ R(q_i(t)) | restLocal_i.translation ]
    根骨额外叠加 rootPosition 平移。
    不需要任何额外的坐标轴变换矩阵。

播放速率：动画的 t 轴不是秒。唯一有据可查的换算来自 AnimationClip 声明的时长，
    速率 = t_end / clip.duration（不同动画不一样，实测有 30.8 / 60 / 84.5 t/s）。
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

ANIM_MAGIC = b"MBAN"
ANIM_VERSION = 2


def parse_animjson(data: dict) -> dict:
    tracks = {t["i"]: t for t in data["boneAnims"]}
    bone_count = int(data.get("boneNum") or len(tracks))
    # 统一的帧数 = 各骨 t 最大值 + 1（个别骨会少几帧，用最近邻补齐）
    t_end = 0
    for t in tracks.values():
        if t.get("rot"):
            t_end = max(t_end, max(f["t"] for f in t["rot"]))
    frames = t_end + 1

    quat = np.zeros((bone_count, frames, 4), np.float32)
    quat[..., 3] = 1.0
    have = np.zeros(bone_count, bool)
    for i in range(bone_count):
        tr = tracks.get(i)
        if not tr or not tr.get("rot"):
            continue
        ts = np.array([f["t"] for f in tr["rot"]], np.int64)
        qs = np.array([f["q"] for f in tr["rot"]], np.float32)
        # 最近邻重采样到规则网格（关键帧本身就是逐帧的，这里只补少数缺口）
        idx = np.clip(np.searchsorted(ts, np.arange(frames)), 0, len(ts) - 1)
        idx = np.where(np.abs(ts[np.clip(idx, 0, len(ts) - 1)] - np.arange(frames))
                       > np.abs(ts[np.clip(idx - 1, 0, len(ts) - 1)] - np.arange(frames)),
                       np.clip(idx - 1, 0, len(ts) - 1), idx)
        quat[i] = qs[idx]
        have[i] = True

    root_pos = np.zeros((frames, 3), np.float32)
    rp = data.get("rootPosition") or []
    if rp:
        ts = np.array([f["t"] for f in rp], np.int64)
        vs = np.array([f["v"][:3] for f in rp], np.float32)
        idx = np.clip(np.searchsorted(ts, np.arange(frames)), 0, len(ts) - 1)
        root_pos = vs[idx]

    # ★ 实测：游戏导出的动画**第 0 帧是绑定姿势**（A-pose），第 1 帧才是动画真正的起点
    #   （inventory_idle 0→1 跳 162°、walk 0→1 跳 67°）。把它当作动画内容播放，
    #   每循环一次角色就会闪一下 A-pose，节奏听起来像"变快了"。
    #   判据：0→1 的跳变远大于其余帧的平均变化。
    start = 0
    if frames >= 4 and bone_count:
        def step(a, b):
            d = np.abs((quat[:, a, :] * quat[:, b, :]).sum(1))
            return float(np.degrees(2 * np.arccos(np.clip(d, -1, 1))).max())
        jump = step(0, 1)
        rest = float(np.median([step(i, i + 1) for i in range(1, min(frames - 1, 60))])) if frames > 3 else 0.0
        if jump > 45.0 and jump > 3.0 * max(rest, 1e-3):
            start = 1

    return dict(name=data.get("name", ""), guid=data.get("guid", ""),
                boneCount=bone_count, frames=frames, tEnd=t_end, start=start,
                quat=quat, rootPos=root_pos, have=have)


def write_animbin(path: Path, anim: dict) -> None:
    """MBAN: 头 + 根位移 + 每骨四元数（xyzw, f32）。

    版本 2 起头部多一个 start 字段（动画真正的起始帧，跳过绑定姿势帧）。
    """
    bc, fr = anim["boneCount"], anim["frames"]
    hdr = struct.pack("<4sIIII", ANIM_MAGIC, ANIM_VERSION, bc, fr, int(anim.get("start", 0)))
    body = anim["rootPos"].astype("<f4").tobytes() + anim["quat"].astype("<f4").tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(hdr + body)


def read_animbin(path: Path) -> dict:
    buf = path.read_bytes()
    magic, ver, bc, fr = struct.unpack_from("<4sIII", buf, 0)
    if magic != ANIM_MAGIC:
        raise ValueError(f"{path.name}: 不是 MBAN 文件")
    off = 16
    start = 0
    if ver >= 2:
        (start,) = struct.unpack_from("<I", buf, 16); off = 20
    root = np.frombuffer(buf, "<f4", fr * 3, off).reshape(fr, 3); off += fr * 12
    quat = np.frombuffer(buf, "<f4", bc * fr * 4, off).reshape(bc, fr, 4); off += bc * fr * 16
    return dict(boneCount=bc, frames=fr, start=start, rootPos=root, quat=quat)


# --------------------------------------------------------------------------- 姿态求解（Python 侧）

def q2R(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, np.float64)
    q = q / max(float(np.linalg.norm(q)), 1e-12)
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], np.float64)


def pose(rig: dict, anim: dict, frame: int) -> np.ndarray:
    """返回每骨的世界矩阵 (n,4,4)。公式见模块 docstring。"""
    n = rig["boneCount"]
    fr = anim["frames"]
    f = int(np.clip(frame, 0, fr - 1))
    out = np.zeros((n, 4, 4), np.float64)
    for i in range(n):
        L = np.eye(4)
        L[:3, :3] = q2R(anim["quat"][i, f])
        L[:3, 3] = rig["restLocal"][i][:3, 3]
        p = rig["parent"][i]
        if p < 0:
            L[:3, 3] += anim["rootPos"][f]
            out[i] = L
        else:
            out[i] = out[p] @ L
    return out


def skin_matrices(rig: dict, anim: dict, frame: int) -> np.ndarray:
    """蒙皮矩阵 M_pose @ inv(M_bind)。顺序写反会在 bind pose 下伪装成全绿，务必保持此序。"""
    Mp = pose(rig, anim, frame)
    return np.einsum("nij,njk->nik", Mp, np.linalg.inv(rig["restWorld"]))


def lbs(positions: np.ndarray, bone_idx: np.ndarray, bone_w: np.ndarray,
        skin: np.ndarray) -> np.ndarray:
    w = bone_w.astype(np.float64) / 255.0
    w = w / np.maximum(w.sum(1, keepdims=True), 1e-9)
    ph = np.concatenate([positions, np.ones((len(positions), 1))], 1)
    out = np.zeros((len(positions), 3))
    for s in range(4):
        bi = bone_idx[:, s].astype(np.int64)
        out += w[:, s:s + 1] * np.einsum("nij,nj->ni", skin[bi], ph)[:, :3]
    return out
