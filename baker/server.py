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
import traceback
import webbrowser
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

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


# --------------------------------------------------------------------------- 自动烘焙
# 界面上选中一个还没烘焙过的 mod 时，服务端就地把它烘出来 ——
# 否则用户在列表里选了却只看到 404，得回到命令行手动 bake。
# 用后台线程 + 轮询状态，而不是让一个 HTTP 请求挂几分钟。

_BAKE_LOCK = threading.Lock()
_BAKE_STATE: dict = {"running": False, "mod": None, "phase": "", "error": None,
                     "done": False, "log": []}


def bake_state() -> dict:
    with _BAKE_LOCK:
        return dict(_BAKE_STATE)


def _set_bake(**kw) -> None:
    with _BAKE_LOCK:
        _BAKE_STATE.update(kw)


def _append_log(line: str) -> None:
    with _BAKE_LOCK:
        _BAKE_STATE["log"] = (_BAKE_STATE["log"] + [line])[-40:]


def start_bake(mod: str, force: bool = False) -> tuple[bool, str]:
    st = bake_state()
    if st["running"]:
        return False, f"已有烘焙在进行中：{st['mod']}"
    _set_bake(running=True, mod=mod, phase="准备中", error=None, done=False, log=[])

    def worker():
        from . import bake as B
        try:
            def prog(i, n, name):
                _set_bake(phase=f"烘焙动画 {i}/{n}：{name}")
                _append_log(name)
            _set_bake(phase="导出几何 / 材质 / 贴图…")
            # force=True 会先清空 geo/ 与 tex/（彻底重建），用于清掉
            # mod 改名或删除网格后残留、manifest 已不再引用的旧产物。
            B.bake_mod(mod, force=force, progress=prog)
            _set_bake(running=False, done=True, phase="完成")
        except Exception as e:
            traceback.print_exc()
            _set_bake(running=False, done=False, error=f"{type(e).__name__}: {e}",
                      phase="失败")

    threading.Thread(target=worker, daemon=True).start()
    return True, "已开始"


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
        if self.path.startswith("/api/bake/status"):
            return self._json(bake_state())
        if self.path.startswith("/api/bake"):
            return self._api_bake()
        if self.path == "/" or self.path.startswith("/?"):
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/bake"):
            return self._api_bake()
        self.send_error(404)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_bake(self):
        q = parse_qs(urlparse(self.path).query)
        mod = (q.get("mod") or [""])[0]
        if not mod:
            return self._json({"ok": False, "error": "缺少 mod 参数"}, 400)
        force = (q.get("force") or ["0"])[0] in ("1", "true", "yes")
        ok, msg = start_bake(mod, force=force)
        return self._json({"ok": ok, "message": msg, "state": bake_state()},
                          200 if ok else 409)

    def _api_mods(self):
        from . import bake as B
        try:
            mods = [m for m in B.list_mods() if m["hasItems"] or m.get('hasRaces') or
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
            # ★ 必须 unquote：mod 名可能含空格（实测 "LVBU and DIAOCHAN"），
            #   浏览器会把空格发成 %20，不解码就永远 404 —— 而 404 又会触发
            #   「未烘焙 → 自动烘焙」，表现为打开页面卡两分钟。
            p = unquote(path.split("?", 1)[0].split("#", 1)[0])
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
            p = unquote(path.split("?", 1)[0].split("#", 1)[0])   # 见上：必须解码
            if p.startswith("/data/"):
                return str(DATA_DIR / p[len("/data/"):])
            rel = p.lstrip("/") or "index.html"
            return str(viewer / rel)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{port}/"
