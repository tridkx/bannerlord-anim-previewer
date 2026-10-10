# -*- coding: utf-8 -*-
"""骨架：解析 mbtool skeljson 的产物，算好 bind 局部/世界矩阵给查看器。

约定（已在真实数据上逐项验证）：
  * rest 是 4x4 **列主序**，平移列是「相对父骨的偏移」，且表达在**父骨局部坐标系**里。
  * 世界矩阵 = 父世界 @ 本骨 rest（标准层次相乘）。
  * 用该式算出的 bind pose：脚趾 z≈0（正好踩地）、头 z≈1.57、左右对称 —— 可作为自检判据。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def parse_skeljson(data: dict) -> dict:
    bones = sorted(data["bones"], key=lambda b: b["i"])
    n = len(bones)
    rest_local = np.stack([
        np.array(b["rest"], np.float64).reshape(4, 4).T for b in bones   # 列主序 → 行主序
    ])
    # Engine frames store a 3x4 affine transform; the fourth row may contain
    # padding/flags (custom TPACs can export w=0). Only rotation/translation apply.
    rest_local[:, 3, :] = [0, 0, 0, 1]
    parent = np.array([b["parent"] for b in bones], np.int32)
    names = [b["name"] for b in bones]

    world = np.zeros((n, 4, 4), np.float64)
    for i in range(n):
        p = parent[i]
        world[i] = rest_local[i] if p < 0 else world[p] @ rest_local[i]

    return dict(name=data.get("name", ""), guid=data.get("guid", ""), boneCount=n,
                names=names, parent=parent, restLocal=rest_local, restWorld=world)


def check(rig: dict) -> dict:
    """几何自检：脚底/头高/左右对称。数值不对说明矩阵约定或骨架选错了。"""
    w = rig["restWorld"][:, :3, 3]
    idx = {nm: i for i, nm in enumerate(rig["names"])}
    res: dict = {"warnings": []}

    def pos(name):
        return w[idx[name]] if name in idx else None

    toe_l, toe_r = pos("bip01_l_toe0"), pos("bip01_r_toe0")
    head, pelvis = pos("bip01_head"), pos("bip01_pelvis")
    if toe_l is not None and toe_r is not None:
        res["toe_z"] = [float(toe_l[2]), float(toe_r[2])]
        if max(abs(toe_l[2]), abs(toe_r[2])) > 0.08:
            res["warnings"].append(
                f"脚趾离地 {toe_l[2]:.3f}/{toe_r[2]:.3f} —— 应为 ~0（踩地），矩阵约定可能有误")
    if head is not None:
        res["head_z"] = float(head[2])
        if not (1.3 < head[2] < 1.9):
            res["warnings"].append(f"头高 {head[2]:.3f} 不在人形范围 [1.3, 1.9]")
    if pelvis is not None:
        res["pelvis_z"] = float(pelvis[2])
    # 左右对称（腿/手）
    for ln, rn in (("bip01_l_thigh", "bip01_r_thigh"), ("bip01_l_hand", "bip01_r_hand")):
        a, b = pos(ln), pos(rn)
        if a is not None and b is not None:
            asym = float(abs(abs(a[0]) - abs(b[0])))
            if asym > 0.05:
                res["warnings"].append(f"{ln}/{rn} 左右不对称，|x| 差 {asym:.3f}m")
    return res


def to_viewer(rig: dict) -> dict:
    def mflat(m):
        return [float(x) for x in np.asarray(m, np.float64).reshape(-1)]

    return dict(
        name=rig["name"], guid=rig["guid"], boneCount=rig["boneCount"],
        names=rig["names"], parent=[int(p) for p in rig["parent"]],
        restLocal=[mflat(rig["restLocal"][i]) for i in range(rig["boneCount"])],
        bindWorld=[mflat(rig["restWorld"][i]) for i in range(rig["boneCount"])],
    )


def load(path: Path) -> dict:
    return parse_skeljson(json.loads(Path(path).read_text(encoding="utf-8")))
