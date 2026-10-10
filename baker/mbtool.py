# -*- coding: utf-8 -*-
"""mbtool 命令行封装。

只做三件事：定位可执行文件、拼参数、把输出拿回来。
所有对游戏资产的读取都从这里出，便于统一加缓存与错误提示。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import env


class MbToolError(RuntimeError):
    pass


def exe() -> Path:
    e = env().mbtool
    if e is None:
        raise MbToolError(
            "找不到 mbtool 可执行文件。\n"
            "  请任选一种方式指定：\n"
            "    1) 设置环境变量 MBTOOL=<mbtool 可执行文件完整路径>\n"
            "    2) 在 config.json 里写 {\"mbtool\": \"<路径>\"}\n"
            "    3) 把它放进 PATH\n"
            "  也可从其源码构建：dotnet build -c Release"
        )
    return e


def run(*args: str | Path, timeout: int = 900) -> str:
    cmd = [str(exe()), *[str(a) for a in args]]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MbToolError(f"mbtool 超时（{timeout}s）: {' '.join(cmd)}") from e
    if p.returncode != 0:
        raise MbToolError(
            f"mbtool 失败（退出码 {p.returncode}）\n  命令: {' '.join(cmd)}\n"
            f"  stderr: {(p.stderr or '').strip()[:2000]}")
    return p.stdout or ""


# --------------------------------------------------------------------------- 常用命令

def exportmod(pack: Path, out_dir: Path, lod: int = -1, all_mips: bool = True) -> dict:
    """导出整包（几何 + 材质 + 贴图）→ 返回 pack.json 内容。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    args: list[str | Path] = ["exportmod", pack, out_dir]
    if lod >= 0:
        args.append(str(lod))
    if all_mips:
        args.append("--all-mips")
    run(*args)
    pj = out_dir / "pack.json"
    if not pj.exists():
        raise MbToolError(f"exportmod 未产出 pack.json: {out_dir}")
    return json.loads(pj.read_text(encoding="utf-8"))


def material_texture_guids(pack: Path, name: str) -> dict[str, str]:
    """exportmod 只返回包内贴图名；用 mat 的槽位 GUID 补全跨包引用。"""
    output = run("mat", pack, name)
    return {slot: guid.lower() for slot, guid in re.findall(
        r"^\s*\[\s*(\d+)\]\s+([0-9a-fA-F-]{36})\s*$", output, re.MULTILINE)}


def skeljson(skeletons_pack: Path, guid: str, out_json: Path) -> dict:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    run("skeljson", skeletons_pack, guid, out_json)
    return json.loads(out_json.read_text(encoding="utf-8"))


def anim_json(animations_pack: Path, name_or_guid: str, out_json: Path,
              skeletons_pack: Path | None = None) -> dict:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    args: list[str | Path] = ["anim", animations_pack, name_or_guid, out_json]
    if skeletons_pack is not None:
        args.append(skeletons_pack)
    run(*args, timeout=1800)
    return json.loads(out_json.read_text(encoding="utf-8"))


def animlist(animations_pack: Path, filt: str = "") -> list[dict]:
    out = run("animlist", animations_pack, filt) if filt else run("animlist", animations_pack)
    rows = []
    for line in out.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 4 and parts[0] == "SkeletalAnimation":
            d = {"name": parts[1], "guid": parts[2]}
            for tok in parts[3:]:
                if tok.startswith("bones="):
                    d["bones"] = int(tok[6:])
                elif tok.startswith("dur="):
                    d["dur"] = int(tok[4:])
            rows.append(d)
    return rows


def cliplist(clips_pack: Path, filt: str = "") -> list[dict]:
    out = run("cliplist", clips_pack, filt) if filt else run("cliplist", clips_pack)
    rows = []
    for line in out.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "AnimationClip":
            d = {"name": parts[1], "dur": None, "anim": None, "flags": []}
            for tok in parts[2:]:
                if tok.startswith("dur="):
                    try:
                        d["dur"] = float(tok[4:])
                    except ValueError:
                        pass
                elif tok.startswith("anim="):
                    d["anim"] = tok[5:]
                elif tok.startswith("flags="):
                    d["flags"] = [x for x in tok[6:].strip("[]").split(",") if x]
            rows.append(d)
    return rows


def list_assets(pack: Path, filt: str = "") -> list[dict]:
    out = run("list", pack, filt) if filt else run("list", pack)
    rows = []
    for line in out.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            rows.append({"type": parts[0], "name": parts[1], "guid": parts[2]})
    return rows


def guidindex(modules_dir: Path, out_tsv: Path) -> Path:
    out_tsv.parent.mkdir(parents=True, exist_ok=True)
    run("guidindex", modules_dir, out_tsv, timeout=3600)
    return out_tsv


# --------------------------------------------------------------------------- 骨架 guid 解析

def skeleton_guid(skeletons_pack: Path, name: str = "bip01_notused") -> str:
    """skeletons.tpac 里有多个同名骨架（不同 guid）；取第一个即可 —— 实测 rest 完全一致。"""
    for a in list_assets(skeletons_pack, name):
        if a["type"] == "Skeleton" and a["name"] == name:
            return a["guid"]
    raise MbToolError(f"skeletons.tpac 里找不到骨架 {name!r}")


def selfcheck() -> str:
    """连通性自检：不依赖游戏，只验证 mbtool 可用。"""
    try:
        out = run("--help") if False else run()
    except MbToolError:
        # mbtool 无参时返回非 0 是正常的，这里只要求能启动
        try:
            p = subprocess.run([str(exe())], capture_output=True, text=True, timeout=30)
            return (p.stdout or p.stderr or "")[:200]
        except Exception as e:
            return f"启动失败: {e}"
    return out[:200]
