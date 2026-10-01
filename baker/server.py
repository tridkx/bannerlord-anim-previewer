# -*- coding: utf-8 -*-
"""本地预览服务：把 viewer/ 与 data/ 挂到 HTTP 上。

为什么要服务而不是双击 HTML：浏览器对 file:// 的 fetch 有同源限制，
查看器需要按需拉取 manifest / 网格 / 动画 / 贴图，必须走 HTTP。

路径映射（全部相对项目根，不含任何绝对路径）：
    /            → viewer/index.html
    /css /js     → viewer/…
    /data        → data/
    /api/mods    → 可预览的 mod 列表（JSON）
"""
from __future__ import annotations

import json
import mimetypes
import socket
import threading
import time
import webbrowser
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from .config import PROJECT_ROOT, DATA_DIR

MIME_OVERRIDE = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".mbmg": "application/octet-stream",
    ".mban": "application/octet-stream",
    ".png": "image/png",
}


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, directory=None, **kw):
        super().__init__(*a, directory=directory, **kw)

    # 静音日志（默认每请求一行太吵）
    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            return

    def guess_type(self, path):
        ext = Path(str(path)).suffix.lower()
        if ext in MIME_OVERRIDE:
            return MIME_OVERRIDE[ext]
        return mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    def end_headers(self):
        # 开发期禁用缓存，改了烘焙产物刷新即见
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/mods"):
            return self._api_mods()
        if self.path == "/" or self.path.startswith("/?"):
            self.path = "/index.html"
        return super().do_GET()

    def _api_mods(self):
        from . import bake as B
        try:
            mods = [m for m in B.list_mods() if m["hasItems"] or
                    (DATA_DIR / "mods" / m["name"] / "manifest.json").exists()]
        except Exception:
            mods = []
        # 已烘焙的排在前面
        for m in mods:
            m["baked"] = (DATA_DIR / "mods" / m["name"] / "manifest.json").exists()
        mods.sort(key=lambda m: (not m["baked"], m["name"]))
        body = json.dumps(mods, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port(preferred: int) -> int:
    for p in [preferred, 0]:
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", p))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            continue
    raise RuntimeError("找不到可用端口")


def serve(port: int = 8777, open_browser: bool = True, quiet: bool = False) -> None:
    """起服务。查看器目录与数据目录分别映射，互不越界。"""
    viewer = PROJECT_ROOT / "viewer"
    if not (viewer / "index.html").exists():
        raise FileNotFoundError(f"找不到查看器页面: {viewer / 'index.html'}")

    port = _free_port(port)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    class H(Handler):
        def translate_path(self, path):
            p = path.split("?", 1)[0].split("#", 1)[0]
            if p.startswith("/data/"):
                rel = p[len("/data/"):]
                return str(DATA_DIR / rel)
            if p == "/api/mods":
                return str(viewer / "index.html")
            rel = p.lstrip("/") or "index.html"
            return str(viewer / rel)

    url = f"http://127.0.0.1:{port}/"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    if not quiet:
        print(f"预览服务已启动: {url}")
        print("  （Ctrl+C 停止）")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            print("\n已停止")
    finally:
        httpd.server_close()


def serve_background(port: int = 8777) -> tuple[ThreadingHTTPServer, str]:
    """给 CLI 的 shot / check 用：后台起服务，返回 (server, url)。"""
    viewer = PROJECT_ROOT / "viewer"
    port = _free_port(port)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    class H(Handler):
        def translate_path(self, path):
            p = path.split("?", 1)[0].split("#", 1)[0]
            if p.startswith("/data/"):
                return str(DATA_DIR / p[len("/data/"):])
            rel = p.lstrip("/") or "index.html"
            return str(viewer / rel)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{port}/"
