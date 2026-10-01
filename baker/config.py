# -*- coding: utf-8 -*-
"""路径与配置解析。

设计原则（满足「项目可搬移」）：
  * 一律以**项目根**为基准解析路径，绝不把 C:\\ / D:\\ 之类写进源码。
  * 项目根由本文件位置反推，与 cwd 无关。
  * 游戏目录 / mbtool 走「环境变量 > 项目配置 > 自动探测」三级回退。
  * 探测不到时给出**可操作**的报错，而不是抛一个空引用。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"

GAME_DIRNAME = "Mount & Blade II Bannerlord"


# --------------------------------------------------------------------------- 配置读写

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:  # 配置坏了不该让整个工具挂掉
            print(f"[config] 警告：{CONFIG_PATH.name} 解析失败（{e}），改用默认值", file=sys.stderr)
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- Steam 库发现

def _steam_roots() -> list[Path]:
    """列出可能的 Steam 安装根目录（注册表 + 常见路径）。"""
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg  # type: ignore
            for hive, key in [
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            ]:
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for value in ("SteamPath", "InstallPath"):
                            try:
                                p = winreg.QueryValueEx(k, value)[0]
                                if p:
                                    roots.append(Path(p))
                            except OSError:
                                pass
                except OSError:
                    pass
        except Exception:
            pass
    # 常见位置兜底（换了盘符也能靠这里找到）
    for base in ("C:/Program Files (x86)/Steam", "C:/Program Files/Steam",
                 "D:/Steam", "D:/SteamLibrary", "E:/Steam", "E:/SteamLibrary"):
        roots.append(Path(base))
    seen, out = set(), []
    for r in roots:
        try:
            rp = r.resolve()
        except OSError:
            continue
        if rp not in seen and rp.exists():
            seen.add(rp)
            out.append(rp)
    return out


def _steam_libraries() -> list[Path]:
    """解析 libraryfolders.vdf，拿到所有 Steam 库目录。"""
    libs: list[Path] = []
    for root in _steam_roots():
        libs.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.exists():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # 只认 "path" 字段，够用且不依赖 VDF 解析库
        for line in text.splitlines():
            line = line.strip()
            if not line.lower().startswith('"path"'):
                continue
            parts = line.split('"')
            if len(parts) >= 4:
                p = parts[3].replace("\\\\", "\\")
                try:
                    lp = Path(p)
                    if lp.exists():
                        libs.append(lp)
                except OSError:
                    pass
    seen, out = set(), []
    for p in libs:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def find_game_dir() -> Path | None:
    for env in ("BANNERLORD_DIR", "MB_GAME_DIR"):
        v = os.environ.get(env)
        if v and (Path(v) / "Modules").is_dir():
            return Path(v).resolve()
    cfg = load_config().get("game_dir")
    if cfg and (Path(cfg) / "Modules").is_dir():
        return Path(cfg).resolve()
    for lib in _steam_libraries():
        cand = lib / "steamapps" / "common" / GAME_DIRNAME
        if (cand / "Modules").is_dir():
            return cand.resolve()
    return None


# --------------------------------------------------------------------------- mbtool

def find_mbtool() -> Path | None:
    for env in ("MBTOOL", "MBTOOL_EXE"):
        v = os.environ.get(env)
        if v and Path(v).is_file():
            return Path(v).resolve()
    cfg = load_config().get("mbtool")
    if cfg and Path(cfg).is_file():
        return Path(cfg).resolve()
    exe = "mbtool.exe" if sys.platform == "win32" else "mbtool"
    found = shutil.which(exe)
    if found:
        return Path(found).resolve()
    # 兄弟目录：<项目根>/../mb-tools/...（默认布局，但不写死盘符）
    for rel in (Path("..") / "mb-tools" / "mbtool" / "bin" / "Release" / "net9.0" / exe,
                Path("..") / "mb-tools" / "mbtool" / "bin" / "Debug" / "net9.0" / exe,
                Path("tools") / "mbtool" / exe):
        cand = (PROJECT_ROOT / rel).resolve()
        if cand.is_file():
            return cand
    return None


# --------------------------------------------------------------------------- 统一入口

class Env:
    """解析好的运行环境；所有路径都来自这里，模块里不再出现字面路径。"""

    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT
        self.data_dir = DATA_DIR
        self.cache_dir = CACHE_DIR
        self.game_dir = find_game_dir()
        self.mbtool = find_mbtool()

    # -- 游戏内资产 --------------------------------------------------------
    @property
    def modules_dir(self) -> Path:
        return self._need_game() / "Modules"

    @property
    def native_packages(self) -> Path:
        return self.modules_dir / "Native" / "AssetPackages"

    @property
    def native_data(self) -> Path:
        return self.modules_dir / "Native" / "ModuleData"

    @property
    def native_em_packages(self) -> Path:
        return self.modules_dir / "Native" / "EmAssetPackages"

    @property
    def sandboxcore_data(self) -> Path:
        return self.modules_dir / "SandBoxCore" / "ModuleData"

    def module_dir(self, name: str) -> Path:
        return self.modules_dir / name

    def _need_game(self) -> Path:
        if self.game_dir is None:
            raise RuntimeError(
                "找不到《骑马与砍杀2：霸主》安装目录。\n"
                "  请任选一种方式指定：\n"
                "    1) 设置环境变量 BANNERLORD_DIR=<游戏根目录>\n"
                f"    2) 在 {CONFIG_PATH} 里写 {{\"game_dir\": \"<游戏根目录>\"}}\n"
                "  游戏根目录应包含 Modules/ 子目录。"
            )
        return self.game_dir

    # -- 常用资产包 --------------------------------------------------------
    def pack(self, which: str) -> Path:
        """按逻辑名取原生资产包路径。"""
        g = self._need_game()
        table = {
            "skeletons": self.native_packages / "skeletons.tpac",
            "animations": self.native_packages / "animations.tpac",
            "animation_clips": self.native_packages / "animation_clips.tpac",
            "materials": self.native_packages / "materials.tpac",
            "human": self.native_em_packages / "human" / "human.tpac",
            "human_underwear": self.native_em_packages / "human_underwear" / "human_underwear.tpac",
            "body_materials": self.native_em_packages / "mat1" / "body_materials" / "body_materials.tpac",
        }
        if which not in table:
            raise KeyError(f"未知资产包: {which}")
        p = table[which]
        if not p.exists():
            raise FileNotFoundError(f"资产包不存在: {p}")
        return p

    def data_path(self, rel: str | Path) -> Path:
        """manifest 里的 file 字段一律相对 data/，统一从这里解析，避免二次拼接。"""
        r = Path(rel)
        return r if r.is_absolute() else (self.data_dir / r)

    def describe(self) -> str:
        lines = [f"项目根   : {self.project_root}",
                 f"数据目录 : {self.data_dir}"]
        lines.append(f"游戏目录 : {self.game_dir or '【未找到】'}")
        lines.append(f"mbtool   : {self.mbtool or '【未找到】'}")
        return "\n".join(lines)


_ENV: Env | None = None


def env(refresh: bool = False) -> Env:
    global _ENV
    if _ENV is None or refresh:
        _ENV = Env()
    return _ENV


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, DATA_DIR / "mods"):
        d.mkdir(parents=True, exist_ok=True)
