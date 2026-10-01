# -*- coding: utf-8 -*-
"""mbpreview —— 命令行入口（给人和 AI 共用）。

  doctor            环境自检（游戏目录 / mbtool / 资产包是否齐）
  mods              列出可预览的皮套 mod
  bake <mod>        烘焙一个 mod（几何+材质+贴图+装备+动画）
  anims [关键词]     搜索动画目录
  serve [--mod X]   启动本地预览服务（人类 UI 用）
  shot ...          出图（AI 判读用）
  inspect <mod>     数据体检：从最终产物倒推，报告会影响实机表现的指标
	check <mod>       动画形变巡检：跑多个动画帧，检测陷地/拉伸/权重异常
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baker import bake as B            # noqa: E402
from baker import config as C          # noqa: E402
from baker import equipment as EQ      # noqa: E402
from baker import geometry as GEO      # noqa: E402
from baker import mbtool as MB         # noqa: E402


def cmd_doctor(args) -> int:
    e = C.env()
    print("=== 环境自检 ===")
    print(e.describe())
    ok = True
    if e.game_dir is None:
        print("✗ 未找到游戏目录")
        ok = False
    if e.mbtool is None:
        print("✗ 未找到 mbtool")
        ok = False
    if ok:
        print("\n=== 关键资产包 ===")
        for k in ("skeletons", "animations", "animation_clips", "human", "body_materials"):
            try:
                p = e.pack(k)
                print(f"  ✓ {k:16s} {p.stat().st_size/1e6:8.1f} MB  {p}")
            except Exception as ex:
                print(f"  ✗ {k:16s} {ex}")
                ok = False
        try:
            out = MB.run()
            print(f"\n  mbtool 可执行 ✓")
        except Exception:
            pass
        print("\n=== 可预览的 mod ===")
        mods = B.list_mods()
        for m in mods:
            print(f"  {m['name']:24s} {len(m['packs'])} 包  items={'有' if m['hasItems'] else '无'}")
        if not mods:
            print("  （无 —— 需要 <游戏>/Modules/<mod>/AssetPackages/*.tpac）")
    print("\n结论:", "就绪 ✓" if ok else "有缺失 ✗")
    return 0 if ok else 1


def cmd_mods(args) -> int:
    for m in B.list_mods():
        print(f"{m['name']}\t{len(m['packs'])}包\t{'items' if m['hasItems'] else '-'}\t{m['path']}")
    return 0


def cmd_bake(args) -> int:
    def prog(i, n, name):
        print(f"    [{i}/{n}] {name}", flush=True)
    mf = B.bake_mod(args.mod, anims=args.anim, anim_limit=args.anim_limit,
                    force=args.force, skin_prefer=args.skin, skip_anims=args.no_anims,
                    progress=prog if not args.quiet else None)
    print(f"\n完成：{mf['mod']}")
    print(f"  网格 {len(mf['meshes'])} / 材质 {len(mf['materials'])} / 贴图 {len(mf['textures'])}")
    print(f"  装备 {len(mf['items'])} / 动画 {len(mf['anims'])}")
    print(f"  manifest: {C.DATA_DIR / 'mods' / mf['mod'] / 'manifest.json'}")
    return 0


def cmd_anims(args) -> int:
    cat = B.ensure_catalog()
    items = cat["items"]
    if args.keyword:
        from baker import actions as A
        items = A.resolve_anims(cat, [args.keyword])
    seen_cat = {}
    for e in items:
        seen_cat.setdefault(e["category"], 0)
        seen_cat[e["category"]] += 1
    if args.category:
        items = [e for e in items if e["category"] == args.category]
    print(f"共 {len(items)} 条  (目录总数 {cat['count']}，动作类型 {cat['actionCount']})")
    if not args.keyword and not args.category:
        print("\n分类统计：")
        for k, v in sorted(seen_cat.items(), key=lambda kv: -kv[1]):
            print(f"  {v:5d}  {k}")
    print()
    print(f"{'动画名':42s} {'分类':12s} {'秒':>7s} {'速率':>7s} {'动作数':>5s}  代表动作")
    for e in items[: args.limit]:
        rep = e["actions"][0] if e["actions"] else ""
        print(f"{e['key'][:42]:42s} {e['category'][:12]:12s} "
              f"{(e['duration'] or 0):7.2f} {(e['rate'] or 0):7.1f} {e['actionCount']:5d}  {rep}")
    return 0


def cmd_inspect(args) -> int:
    """从**最终产物**倒推体检 —— 不是查中间产物。"""
    e = C.env()
    mdir = e.data_dir / "mods" / args.mod
    mf_p = mdir / "manifest.json"
    if not mf_p.exists():
        print(f"未烘焙：{mdir}\n先跑：mbpreview bake {args.mod}")
        return 1
    mf = json.loads(mf_p.read_text(encoding="utf-8"))
    print(f"=== {mf['mod']} 体检（数据来自已烘焙产物）===")
    problems = []

    tot_v = tot_t = 0
    for m in mf["meshes"]:
        subs = GEO.read_meshpack(e.data_path(m["file"]))
        a = GEO.audit(subs)
        tot_v += a["vertices"]; tot_t += a["triangles"]
        print(f"  {m['mesh']:28s} {a['submeshes']:3d} 组  {a['vertices']:6d} 顶点 "
              f"{a['triangles']:6d} 三角  权重和OK {a['weightsum_ok_ratio']*100:6.2f}%  "
              f"骨索引≤{a['max_bone_index']}")
        problems += [f"{m['mesh']}: {w}" for w in a["warnings"]]
    print(f"  合计 {tot_v} 顶点 / {tot_t} 三角")
    if tot_v != mf["stats"]["vertices"]:
        problems.append(f"顶点数 {tot_v} ≠ 打包时的 {mf['stats']['vertices']}")

    print("\n--- 装备遮盖 ---")
    skin = mf["skin"]
    eq = mf["items"]
    plan = EQ.preview_plan(eq, dict(name=skin["name"], parts=skin["parts"]))
    for it in eq:
        cov = ",".join(sorted(it["covers"])) or "-"
        print(f"  [{it['slot']:6s}] {it['id']:28s} covers={cov:24s} {it['type']}")
    print(f"  体型 {skin['name']}：")
    for k, v in plan["skinParts"].items():
        if k in plan["hiddenParts"]:
            print(f"    ✓ 隐藏 {EQ.SKIN_LABEL.get(k,k):14s} (被 {plan['hiddenParts'][k]['hiddenBy']} 遮住)")
        else:
            print(f"    ! 露出 {EQ.SKIN_LABEL.get(k,k):14s} mesh={v}")

    print("\n--- 材质 ---")
    for name, m in mf["materials"].items():
        tex = ",".join(f"{k}={v}" for k, v in m["textures"].items()) or "(无贴图)"
        print(f"  {name:24s} {m['blendMode']:16s} alphaTest={m['alphaTest']:.3f} "
              f"{'双面' if m['twoSided'] else '单面'} {'蒙皮' if m['skinning'] else '★无skinning!'} {tex}")
        if not m["skinning"]:
            problems.append(f"材质 {name} 的 vertexLayoutFlags 里没有 skinning —— 实机表现是网格不跟骨骼动")
    print("\n--- 贴图 ---")
    for name, t in mf["textures"].items():
        print(f"  {name:28s} {t['width']}x{t['height']} {t['format']:5s} alpha标志={t['hasAlpha']}")

    if problems:
        print(f"\n=== 发现 {len(problems)} 个问题 ===")
        for p in problems:
            print(f"  ! {p}")
        return 2
    print("\n体检通过 ✓")
    return 0


def cmd_serve(args) -> int:
    from baker import server
    server.serve(port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_check(args) -> int:
    from baker import check as CK
    info = CK.check_mod(args.mod, anims=args.anim, frames=args.frames,
                        max_anims=args.max, verbose=True)
    return 2 if info["problems"] else 0


def cmd_shot(args) -> int:
    from baker import shot as SH
    if args.anims:
        anims = [a.strip() for a in args.anims.split(",") if a.strip()]
        frames = [float(x) for x in (args.frames or "0").split(",")]
        views = [v.strip() for v in (args.views or "front,left").split(",")]
        outdir = Path(args.out) if args.out else (C.PROJECT_ROOT / "_out" / args.mod)
        res = SH.shot_batch(args.mod, anims, frames, views, outdir,
                            equip=args.equip, debug=args.debug, light=args.light,
                            bones=args.bones, grid=not args.no_grid,
                            width=args.width, height=args.height)
        errs = [r for r in res if "errors" in r]
        shots = [r for r in res if "file" in r]
        print(f"出图 {len(shots)} 张 → {outdir}")
        for r in shots:
            print(f"  {Path(r['file']).name}")
        if errs:
            print("页面错误:")
            for e in errs[0]["errors"]:
                print("  !", e)
        return 0

    res = SH.shot(args.mod, anim=args.anim, frame=args.frame, view=args.view,
                  out=Path(args.out) if args.out else None,
                  width=args.width, height=args.height, equip=args.equip,
                  vanilla=not args.no_vanilla, bones=args.bones, grid=not args.no_grid,
                  debug=args.debug, light=args.light, hide_ui=args.hide_ui,
                  only=args.only, no_alpha_test=args.no_alpha_test, skin=args.skin,
                  wait_ms=args.wait)
    for c in res["console"][-12:]:
        print("  console:", c)
    for e in res["errors"]:
        print("  ! 页面错误:", e)
    if res.get("hud"):
        print("  HUD:", res["hud"])
    if res.get("animInfo"):
        print("  动画:", res["animInfo"])
    print("出图 →", res["out"])
    return 0 if not res["errors"] else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mbpreview", description="骑砍2 皮套 Mod 动画预览器")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)
    sub.add_parser("mods", help="列出可预览的 mod").set_defaults(func=cmd_mods)

    p = sub.add_parser("serve", help="启动本地预览服务（人类 UI）")
    p.add_argument("--port", type=int, default=8777)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("bake", help="烘焙一个 mod")
    p.add_argument("mod")
    p.add_argument("--anim", action="append", help="要烘焙的动画（动作类型/动画名/关键词），可多次")
    p.add_argument("--anim-limit", type=int, default=24, help="默认动画集大小")
    p.add_argument("--no-anims", action="store_true", help="跳过动画")
    p.add_argument("--skin", default="man", help="原版体型对照（man/woman）")
    p.add_argument("--force", action="store_true", help="忽略缓存全量重建")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_bake)

    p = sub.add_parser("anims", help="搜索动画")
    p.add_argument("keyword", nargs="?", default="")
    p.add_argument("--category", default="")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=cmd_anims)

    p = sub.add_parser("inspect", help="数据体检")
    p.add_argument("mod")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("check", help="动画形变巡检（自动发现问题）")
    p.add_argument("mod")
    p.add_argument("--anim", action="append", help="只检查指定动画，可多次")
    p.add_argument("--frames", type=int, default=5, help="每个动画采样多少帧")
    p.add_argument("--max", type=int, default=8, help="最多检查多少个动画")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("shot", help="无头出图（给 AI 判读）")
    p.add_argument("--mod", required=True)
    p.add_argument("--anim", help="动画 key")
    p.add_argument("--anims", help="批量：逗号分隔的动画 key 列表")
    p.add_argument("--frame", type=float)
    p.add_argument("--frames", help="批量：逗号分隔的帧号")
    p.add_argument("--view", default="front", help="front/back/left/right/top/face/feet")
    p.add_argument("--views", help="批量：逗号分隔的视角")
    p.add_argument("-o", "--out", help="输出文件或目录")
    p.add_argument("--equip", default="all", help="all / none / 逗号分隔的装备 id")
    p.add_argument("--debug", type=int, default=0, help="0正常 1仅贴图 2法线 3UV 4仅光照")
    p.add_argument("--light", default="item", help="item/day/night/studio")
    p.add_argument("--bones", action="store_true", help="显示骨骼")
    p.add_argument("--no-grid", action="store_true")
    p.add_argument("--no-vanilla", action="store_true", help="不显示原版身体")
    p.add_argument("--hide-ui", action="store_true", help="隐藏右侧面板（纯净出图）")
    p.add_argument("--only", help="只显示名字/材质匹配的网格（诊断）")
    p.add_argument("--no-alpha-test", action="store_true", help="关掉镂空（诊断被丢弃的部分）")
    p.add_argument("--skin", help="原版体型对照：man/woman")
    p.add_argument("--width", type=int, default=900)
    p.add_argument("--height", type=int, default=1200)
    p.add_argument("--wait", type=int, default=1200, help="就绪后再等的毫秒数")
    p.set_defaults(func=cmd_shot)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
