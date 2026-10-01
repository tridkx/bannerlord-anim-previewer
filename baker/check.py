# -*- coding: utf-8 -*-
"""动画形变巡检 —— 给 AI 用的"自动发现问题"。

不开浏览器、不靠人眼：直接从**已烘焙的产物**（MBMG 网格 + MBAN 动画 + 骨架）
做 LBS，用客观判据找出会影响实机表现的问题。

判据（都经过实测校准，避免把正常现象报成问题）：
  1. 边长拉伸   —— 蒙皮约定正确时 p99 应 ≈1.1~1.3；p999 上百说明某根骨的变换炸了
  2. 脚底 z     —— 站立类动画应 ≈0（踩地）；持续 >5cm 是陷地/浮空
  3. 顶点位移   —— 离群值（>1.5m）说明权重或坐标错了
  4. 权重和     —— 每顶点 4×u8 之和必须 ==255
  5. 骨索引范围 —— 必须 ≤27

★ 判据的坑（踩过）：t=0 时姿势≈bind pose，LBS 退化成恒等变换，
  所有自检都会全绿 —— 所以**必须用 t>0 的帧**，并且边长统计只看 >1cm 的边
  （毫米级短边在权重过渡区本来就会被相对拉伸，用比值判会误报）。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import animation as AN
from . import config as C
from . import geometry as GEO
from . import skeleton as SK


def _load_rig() -> dict:
    e = C.env()
    raw = json.loads((e.cache_dir / "skeleton_raw.json").read_text(encoding="utf-8"))
    return SK.parse_skeljson(raw)


def _load_meshes(manifest: dict) -> list[tuple[str, dict]]:
    e = C.env()
    out = []
    for m in manifest["meshes"]:
        for s in GEO.read_meshpack(e.data_path(m["file"])):
            if s.get("bone_idx") is None:
                continue
            out.append((f"{m['mesh']}/{s['name']}", s))
    return out


def check_mod(mod: str, anims: list[str] | None = None, frames: int = 5,
              max_anims: int = 8, verbose: bool = False) -> dict:
    e = C.env()
    mod_dir = e.data_dir / "mods" / mod
    mf_p = mod_dir / "manifest.json"
    if not mf_p.exists():
        raise FileNotFoundError(f"未烘焙：{mod_dir}（先跑 mbpreview bake {mod}）")
    mf = json.loads(mf_p.read_text(encoding="utf-8"))
    rig = _load_rig()
    Mb = rig["restWorld"]
    meshes = _load_meshes(mf)

    problems: list[dict] = []
    info: dict = {"mod": mod, "meshes": len(meshes),
                  "vertices": int(sum(len(s["pos"]) for _, s in meshes))}

    # ---- 静态检查：权重 ----
    wsum_bad = 0
    maxbone = -1
    for name, s in meshes:
        tot = s["bone_w"].astype(np.int32).sum(1)
        wsum_bad += int((np.abs(tot - 255) > 1).sum())
        maxbone = max(maxbone, int(s["bone_idx"].max()))
    info["weightsum_bad"] = wsum_bad
    info["max_bone_index"] = maxbone
    if wsum_bad:
        problems.append(dict(kind="weightsum", severity="high",
                             msg=f"{wsum_bad} 个顶点的 4×u8 权重和 ≠255 —— "
                                 "引擎会退化成一整块跟随骨盆（实机表现：整个模型像刚体一起摇晃）"))
    if maxbone > 27:
        problems.append(dict(kind="bone_index", severity="high",
                             msg=f"骨骼索引最大值 {maxbone} > 27，超出人形骨架范围"))

    # ---- 动画检查 ----
    avail = mf.get("anims", [])
    if anims:
        want = set(anims)
        avail = [a for a in avail if a["key"] in want or a.get("anim") in want]
    avail = avail[:max_anims]
    per_anim = []
    for a in avail:
        p = e.cache_dir / "anim" / f"{a['key']}.mban"
        if not p.exists():
            continue
        anim = AN.read_animbin(p)
        n_fr = anim["frames"]
        rec = dict(key=a["key"], frames=n_fr, duration=a.get("duration"),
                   cyclic=a.get("cyclic"), issues=[])
        ratios, minz, maxz = [], 1e9, -1e9
        disps = []
        for fi in range(frames):
            t = int(round(fi * (n_fr - 1) / max(1, frames - 1)))
            skin = AN.skin_matrices(rig, anim, t)
            for name, s in meshes:
                p2 = AN.lbs(s["pos"], s["bone_idx"], s["bone_w"], skin)
                disps.append(np.linalg.norm(p2 - s["pos"], axis=1))
                minz = min(minz, float(p2[:, 2].min()))
                maxz = max(maxz, float(p2[:, 2].max()))
                tri = s.get("tri")
                if tri is None:
                    continue
                e0 = np.linalg.norm(s["pos"][tri[:, 0]] - s["pos"][tri[:, 1]], axis=1)
                e1 = np.linalg.norm(p2[tri[:, 0]] - p2[tri[:, 1]], axis=1)
                m = e0 > 0.01                      # ★ 只看 >1cm 的边
                if m.any():
                    ratios.append(e1[m] / e0[m])
        maxdisp = disp_p99 = 0.0
        if disps:
            dd = np.concatenate(disps)
            maxdisp = float(dd.max())
            disp_p99 = float(np.percentile(dd, 99))
        if ratios:
            r = np.concatenate(ratios)
            rec.update(stretch_p50=float(np.percentile(r, 50)),
                       stretch_p99=float(np.percentile(r, 99)),
                       stretch_p999=float(np.percentile(r, 99.9)),
                       stretch_max=float(r.max()))
            if rec["stretch_p999"] > 4.0:
                rec["issues"].append(
                    f"边长拉伸 p999={rec['stretch_p999']:.1f}（正常应 <2）"
                    " —— 某根骨的蒙皮变换异常，常见于权重映射错到别的骨")
        rec.update(z_min=minz, z_max=maxz, max_disp=maxdisp, disp_p99=disp_p99)
        if minz < -0.06:
            rec["issues"].append(f"顶点最低到 z={minz:.3f} —— 陷进地面 {abs(minz)*100:.0f}cm")
        if maxdisp > 1.0 and maxdisp > 6.0 * max(disp_p99, 1e-6):
            rec["issues"].append(
                f"顶点位移离群：最大 {maxdisp:.2f}m 而 p99 只有 {disp_p99:.2f}m"
                " —— 个别顶点被某根骨甩飞，通常是权重映射错")
        per_anim.append(rec)
        if rec["issues"]:
            for msg in rec["issues"]:
                problems.append(dict(kind="anim", severity="medium",
                                     anim=a["key"], msg=msg))

    info["anims"] = per_anim
    info["problems"] = problems
    if verbose:
        print(f"=== {mod} 动画巡检 ===")
        print(f"  网格 {info['meshes']} 组 / 顶点 {info['vertices']}")
        print(f"  权重和异常 {wsum_bad}  骨索引上限 {maxbone}")
        print(f"{'动画':34s} {'帧':>5s} {'拉伸p99':>8s} {'拉伸p999':>9s} {'z最低':>8s} "
              f"{'位移p99':>8s} {'位移max':>8s}")
        for r in per_anim:
            print(f"  {r['key'][:34]:34s} {r['frames']:5d} {r.get('stretch_p99',0):8.2f} "
                  f"{r.get('stretch_p999',0):9.2f} {r.get('z_min',0):8.3f} "
                  f"{r.get('disp_p99',0):8.2f} {r.get('max_disp',0):8.2f}")
        print()
        if problems:
            print(f"发现 {len(problems)} 个问题：")
            for p in problems:
                loc = f"[{p.get('anim','静态')}] " if p.get("anim") else ""
                print(f"  ! {loc}{p['msg']}")
        else:
            print("未发现问题 ✓")
    return info
