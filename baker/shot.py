# -*- coding: utf-8 -*-
"""出图：给 AI 用的无头截图。

自己起后台服务、开无头浏览器、等查看器就绪、截图、收工 —— 不依赖任何常驻进程。

    mbpreview shot --mod PitaoYingOutfits --anim inventory_idle --frame 200 --view left -o a.png
    mbpreview shot --mod X --anims inventory_idle,walk_forward_unarmed --frames 0,60,120 --grid

浏览器优先级：系统 Edge（channel=msedge）→ 打包的 Chromium。
用系统 Edge 是因为本机一定装了，且不必额外下载几百 MB。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .server import serve_background
from .config import PROJECT_ROOT


def _launch(pw, width: int, height: int, headless: bool):
    """优先系统 Edge；失败再退回 playwright 自带 Chromium。"""
    errors = []
    for kwargs in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return pw.chromium.launch(headless=headless, args=[
                "--use-angle=swiftshader",       # 无头环境下用软件渲染跑 WebGL
                "--enable-unsafe-swiftshader",
                "--disable-gpu-sandbox",
                "--no-sandbox",
            ], **kwargs)
        except Exception as e:
            errors.append(f"{kwargs or 'bundled'}: {e}")
    raise RuntimeError("启动浏览器失败：\n  " + "\n  ".join(errors) +
                       "\n  可试: python -m playwright install chromium")


def shot(mod: str, anim: str | None = None, frame: float | None = None,
         view: str = "front", out: Path | None = None,
         width: int = 900, height: int = 1200, equip: str = "all",
         vanilla: bool = True, bones: bool = False, grid: bool = True,
         debug: int = 0, light: str = "item", hide_ui: bool = False,
         only: str | None = None, no_alpha_test: bool = False, skin: str | None = None,
         wait_ms: int = 1200, timeout_ms: int = 40000,
         headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright

    httpd, base = serve_background()
    q = [f"mod={mod}", f"view={view}", f"equip={equip}",
         f"vanilla={1 if vanilla else 0}", f"bones={1 if bones else 0}",
         f"grid={1 if grid else 0}", f"debug={debug}", f"light={light}"]
    if anim:
        q.append(f"anim={anim}")
    if frame is not None:
        q.append(f"frame={frame}")
    if only:
        q.append(f"only={only}")
    if no_alpha_test:
        q.append("noalphatest=1")
    if skin:
        q.append(f"skin={skin}")
    url = base + "?" + "&".join(q)

    result = {"url": url, "console": [], "errors": [], "out": None}
    try:
        with sync_playwright() as pw:
            browser = _launch(pw, width, height, headless)
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("console", lambda m: result["console"].append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: result["errors"].append(str(e)))
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            # 查看器加载完会置 window.__ready
            try:
                page.wait_for_function("() => window.__ready === true", timeout=timeout_ms)
            except Exception as e:
                result["errors"].append(f"等待就绪超时: {e}")
            page.wait_for_timeout(wait_ms)
            if hide_ui:
                page.add_style_tag(content="#panel{display:none!important}")
                page.wait_for_timeout(200)
            if out is None:
                out = PROJECT_ROOT / "_out" / f"{mod}_{anim or 'default'}_{view}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            result["out"] = str(out)
            # 顺手把页面里的诊断信息带回来，便于批处理判读
            try:
                result["hud"] = page.inner_text("#hud-stats")
                result["animInfo"] = page.inner_text("#anim-info")
            except Exception:
                pass
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return result


def shot_batch(mod: str, anims: list[str], frames: list[float], views: list[str],
               outdir: Path, **kw) -> list[dict]:
    """批量出图（一次开浏览器、一个服务，比逐个起快得多）。"""
    from playwright.sync_api import sync_playwright

    httpd, base = serve_background()
    outdir.mkdir(parents=True, exist_ok=True)
    shots = []
    try:
        with sync_playwright() as pw:
            browser = _launch(pw, kw.get("width", 900), kw.get("height", 1200),
                              kw.get("headless", True))
            page = browser.new_page(viewport={"width": kw.get("width", 900),
                                              "height": kw.get("height", 1200)})
            errs: list[str] = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            for anim in anims:
                for frame in frames:
                    for view in views:
                        q = (f"mod={mod}&anim={anim}&frame={frame}&view={view}"
                             f"&equip={kw.get('equip','all')}&debug={kw.get('debug',0)}"
                             f"&light={kw.get('light','item')}"
                             f"&bones={1 if kw.get('bones') else 0}"
                             f"&grid={1 if kw.get('grid', True) else 0}")
                        page.goto(base + "?" + q, wait_until="domcontentloaded")
                        try:
                            page.wait_for_function("() => window.__ready === true", timeout=40000)
                        except Exception:
                            pass
                        page.wait_for_timeout(kw.get("wait_ms", 900))
                        name = f"{anim}__{view}__f{int(frame)}.png"
                        page.screenshot(path=str(outdir / name))
                        shots.append({"anim": anim, "frame": frame, "view": view,
                                      "file": str(outdir / name)})
            if errs:
                shots.append({"errors": errs[:20]})
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return shots
