# -*- coding: utf-8 -*-
"""烘焙主流程：把游戏资产 + mod 资产转成查看器能直接吃的目录。

产物布局（data/ 下）：
    cache/                      跨 mod 共享，只重建一次
        skeleton.json           骨架 bind pose
        catalog.json            动画目录（含时长/速率/动作映射）
        vanilla/geo/*.mbmg      原版皮肤部件
        vanilla/materials.json
        anim/<key>.mban         按需烘焙的动画
    mods/<mod>/                 每个 mod 一份
        manifest.json           总清单（查看器入口）
        geo/*.mbmg  tex/*.png
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import numpy as np

from . import actions as A
from . import animation as AN
from . import config as C
from . import equipment as EQ
from . import geometry as GEO
from . import cloth as CLOTH
from . import material as MAT
from . import mbtool as MB
from . import skeleton as SK
from . import races as RACE
from PIL import Image


# 原版身体/手在 tpac 里不带贴图（游戏运行时按肤色程序生成）。预览时按类别给兜底色，
# 否则只能拿白图渲染 —— 看起来就像角色戴了一副白手套、眼睛是一片白。
VANILLA_FALLBACK = {
    "skin":      ((206, 160, 128, 255), "vanilla_skin_default"),
    "hair":      (( 58,  44,  36, 255), "vanilla_hair_default"),
    "beard":     (( 74,  58,  48, 255), "vanilla_beard_default"),
    "eye":       ((196, 192, 188, 255), "vanilla_eye_default"),
    "mouth":     ((168, 106,  98, 255), "vanilla_mouth_default"),
    "brow":      (( 86,  64,  52, 255), "vanilla_brow_default"),
    "underwear": ((214, 210, 204, 255), "vanilla_underwear_default"),
}


def vanilla_fallback(name: str) -> str | None:
    """按材质名猜它该用什么兜底色（tpac 里没有贴图的那批）。"""
    n = (name or "").lower()
    if not n:
        return None
    if n.startswith(("body_", "head_")) or n.endswith("_skin") or n == "beard_skin":
        return "skin"
    if "hair" in n:
        return "hair"
    if "beard" in n:
        return "beard"
    if "eye" in n:
        return "eye"
    if "brow" in n:
        return "brow"
    if "mouth" in n:
        return "mouth"
    if "underwear" in n:
        return "underwear"
    return None


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh(target: Path, *sources: Path) -> bool:
    """目标比所有来源都新才算新鲜（缓存键必须包含影响输出的全部来源）。"""
    if not target.exists():
        return False
    t = target.stat().st_mtime
    return all((not s.exists()) or s.stat().st_mtime <= t for s in sources)


# --------------------------------------------------------------------------- 共享：骨架

def ensure_skeleton(force: bool = False) -> dict:
    """返回**查看器格式**的骨架（restLocal / bindWorld / parent / names）。

    缓存里存 mbtool 的原始 skeljson（便于排查），每次再转成查看器格式给前端。
    """
    e = C.env()
    raw_path = e.cache_dir / "skeleton_raw.json"
    sk_pack = e.pack("skeletons")
    if force or not _fresh(raw_path, sk_pack):
        guid = MB.skeleton_guid(sk_pack, "bip01_notused")
        MB.skeljson(sk_pack, guid, raw_path)
    rig = SK.parse_skeljson(json.loads(raw_path.read_text(encoding="utf-8")))
    chk = SK.check(rig)
    if force or not getattr(ensure_skeleton, "_logged", False):
        _log(f"  骨架 {rig['boneCount']} 骨  头高={chk.get('head_z', 0):.3f}  "
             f"脚底 z={chk.get('toe_z')}")
        for w in chk["warnings"]:
            _log(f"  ! 骨架自检: {w}")
        ensure_skeleton._logged = True
    return SK.to_viewer(rig)


# --------------------------------------------------------------------------- 共享：动画目录

# 目录格式版本：改了 build_catalog / _norm 之类影响输出的逻辑就 +1，强制重建缓存
CATALOG_VERSION = 4


def ensure_catalog(force: bool = False) -> dict:
    e = C.env()
    out = e.cache_dir / "catalog.json"
    srcs = [e.pack("animations"), e.pack("animation_clips"),
            e.native_data / "action_sets.xml"]
    if not force and out.exists():
        try:
            old = json.loads(out.read_text(encoding="utf-8"))
            if old.get("version") != CATALOG_VERSION:
                force = True
        except Exception:
            force = True
    if force or not _fresh(out, *srcs):
        t0 = time.time()
        _log("  读取动画清单（首次较慢，之后走缓存）…")
        anims = MB.animlist(e.pack("animations"))
        _log(f"    骨骼动画 {len(anims)} 个  ({time.time()-t0:.1f}s)")
        clips = MB.cliplist(e.pack("animation_clips"))
        _log(f"    动画剪辑 {len(clips)} 个  ({time.time()-t0:.1f}s)")
        cat = A.build_catalog(e.native_data / "action_sets.xml", anims, clips)
        cat["builtAt"] = time.time()
        cat["version"] = CATALOG_VERSION
        linked = sum(1 for x in cat["items"] if x["actionCount"] > 0)
        _log(f"    动作类型 {cat['actionCount']} 个 → 目录条目 {cat['count']} 条"
             f"（其中 {linked} 条挂上了动作名）")
        out.write_text(json.dumps(cat, ensure_ascii=False), encoding="utf-8")
    return json.loads(out.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- 共享：动画烘焙

def bake_anim(key: str, entry: dict, force: bool = False) -> Path:
    e = C.env()
    out = e.cache_dir / "anim" / f"{key}.mban"
    if force or not out.exists():
        tmp = out.with_suffix(".json")
        data = MB.anim_json(e.pack("animations"), entry["anim"], tmp, e.pack("skeletons"))
        anim = AN.parse_animjson(data)
        AN.write_animbin(out, anim)
        try:
            tmp.unlink()
        except OSError:
            pass
    return out


def ensure_anims(entries: list[dict], force: bool = False, progress=None) -> list[dict]:
    got = []
    for i, ent in enumerate(entries):
        p = bake_anim(ent["key"], ent, force=force)
        a = AN.read_animbin(p)
        frames = int(a["frames"])
        start = int(a.get("start", 0))
        # ★ 播放速率 = 有效跨度 / clip 声明的秒数。两个坑：
        #   (1) 不能用 animlist 的 dur 字段当帧数 —— 它和实际关键帧范围不一致
        #       （实测 inventory_idle：字段 630、实际 1267 帧、clip 15s），拿它算会慢一倍；
        #   (2) 要扣掉开头那一帧绑定姿势（start），否则循环周期会多出一帧。
        t_end = max(0, frames - 1)
        span = max(1, t_end - start)
        dur = ent.get("duration")
        rate = (span / dur) if dur else None
        got.append(dict(key=ent["key"], anim=ent["anim"], guid=ent.get("guid"),
                        file=f"cache/anim/{ent['key']}.mban", frames=frames,
                        tEnd=t_end, start=start, span=span, duration=dur, rate=rate,
                        cyclic=bool(ent.get("cyclic")), category=ent.get("category", "其他"),
                        actions=ent.get("actions", [])[:24], actionCount=ent.get("actionCount", 0)))
        if progress:
            progress(i + 1, len(entries), ent["key"])
    return got


# --------------------------------------------------------------------------- 共享：原版皮肤部件

def ensure_vanilla(force: bool = False) -> dict:
    """导出原版皮肤部件。

    两个坑（都会让"没被装备盖住的原版部位"显示成**纯白**，看着像戴了白手套）：
      1. human.tpac 里的网格**材质名是空的** —— 真正的材质跨包放在
         `mat1/body_materials/`，本包内查不到。必须用 materialGuid 关联回来。
      2. 原版身体材质在 tpac 里**一个贴图都没有**（游戏运行时按肤色/体型程序生成），
         没有 albedo 就只能拿白图兜底。这里生成一张默认肤色给它。
    """
    e = C.env()
    vdir = e.cache_dir / "vanilla"
    out = vdir / "vanilla.json"
    human = e.pack("human")
    if force or not _fresh(out, human):
        if vdir.exists():
            shutil.rmtree(vdir, ignore_errors=True)
        exp = vdir / "_export"
        pj = MB.exportmod(human, exp, all_mips=False)

        # ---- 跨包材质：GUID → 材质名/参数 ----
        mats: dict = {}
        mat_by_guid: dict = {}
        try:
            bm = e.pack("body_materials")
            bexp = vdir / "_bm"
            bpj = MB.exportmod(bm, bexp, all_mips=False)
            for m in bpj.get("materials", []):
                mats[m["name"]] = MAT.simplify_material(m)
                mat_by_guid[m["guid"]] = m["name"]
            shutil.rmtree(bexp, ignore_errors=True)
        except Exception as ex:
            _log(f"  ! 原版身体材质读取失败（手/身体会显示成白色）: {ex}")

        # ---- 兜底贴图：原版材质一个 albedo 都没有，按类别各生成一张纯色 ----
        texdir = vdir / "tex"
        texdir.mkdir(parents=True, exist_ok=True)
        vtex = {}
        for _kind, (rgba, tname) in VANILLA_FALLBACK.items():
            Image.new("RGBA", (16, 16), rgba).save(texdir / f"{tname}.png")
            vtex[tname] = dict(name=tname, width=16, height=16, format="RGBA",
                               hasAlpha=False, file=f"cache/vanilla/tex/{tname}.png")

        geo_out = vdir / "geo"
        parts = {}
        for m in pj["meshes"]:
            src = exp / m["file"]
            subs = GEO.parse_gdmb(src)
            subs = [x for x in subs if x["lod"] == 0] or subs
            if not subs:
                continue
            # ★ 用 GUID 把跨包的材质名补回来，否则 material 是空串
            for x in subs:
                if not x.get("material") and x.get("material_guid"):
                    x["material"] = mat_by_guid.get(x["material_guid"], "")
            dst = geo_out / f"{m['name']}.mbmg"
            GEO.write_meshpack(dst, subs)
            parts[m["name"]] = dict(
                mesh=m["name"], file=f"cache/vanilla/geo/{m['name']}.mbmg",
                materials=sorted({x.get("material") or "" for x in subs}),
                triangles=sum(0 if x["tri"] is None else x["tri"].size // 3 for x in subs),
                vertices=sum(len(x["pos"]) for x in subs))
        shutil.rmtree(exp, ignore_errors=True)

        # 只给真正用在身体部件上的材质挂兜底色（并按类别选颜色，
        # 否则一刀切涂肤色会把眼睛也涂掉，看着像没长眼睛）
        used = {mn for p in parts.values() for mn in p["materials"] if mn}
        for name in used:
            mat = mats.get(name)
            if mat is None or mat["textures"].get("albedo"):
                continue
            kind = vanilla_fallback(name)
            if kind:
                mat["textures"]["albedo"] = VANILLA_FALLBACK[kind][1]
                mat["_fallback"] = kind
        out.write_text(json.dumps(
            dict(parts=parts, materials=mats,
                 textures=vtex),
            ensure_ascii=False), encoding="utf-8")
    return json.loads(out.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- mod 烘焙

PACKAGE_VERSION = 1


def export_packages(packs: list[Path], exp: Path) -> dict:
    """各包独立导出，按文件名顺序合并；同名资产取后包并提示。"""
    merged = {kind: {} for kind in ("meshes", "materials", "textures")}
    for index, pack in enumerate(packs):
        prefix = f"pack{index}"
        package_dir = exp / prefix
        pj = MB.exportmod(pack, package_dir, all_mips=False)
        settings, warnings = CLOTH.read_settings(pack)
        for warning in warnings:
            _log(f"  ! {pack.name}: {warning}")
        for kind, assets in merged.items():
            for entry in pj.get(kind, []):
                entry = dict(entry, sourcePack=pack.name)
                if entry.get("file"):
                    entry["file"] = f"{prefix}/{entry['file']}"
                if kind == "meshes":
                    entry["clothSettings"] = settings.get(entry["name"], [])
                if entry["name"] in assets:
                    _log(f"  ! 同名 {kind} {entry['name']}: "
                         f"{assets[entry['name']]['sourcePack']} → {pack.name}")
                assets[entry["name"]] = entry
    textures_by_guid = {t["guid"].lower(): t["name"]
                        for t in merged["textures"].values() if t.get("guid")}
    pack_by_name = {p.name: p for p in packs}
    for mat in merged["materials"].values():
        slots = mat.get("textures") or {}
        if len(packs) > 1 and any(not value for value in slots.values()):
            guids = MB.material_texture_guids(pack_by_name[mat["sourcePack"]], mat["name"])
            mat["textures"] = {slot: value or textures_by_guid.get(guids.get(str(slot)), "")
                               for slot, value in slots.items()}
    return {kind: list(assets.values()) for kind, assets in merged.items()}


def find_mod(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.is_dir():
        return p.resolve()
    e = C.env()
    cand = e.module_dir(name_or_path)
    if cand.is_dir():
        return cand.resolve()
    raise FileNotFoundError(
        f"找不到 mod: {name_or_path}\n  可给 mod 名（在 <游戏>/Modules/ 下）或直接给目录路径。")


def list_mods() -> list[dict]:
    """列出已安装的、带 AssetPackages 的 mod（即"可预览的皮套 mod"）。"""
    e = C.env()
    out = []
    try:
        mods_dir = e.modules_dir
    except RuntimeError:
        return out
    for d in sorted(mods_dir.iterdir()):
        if not d.is_dir():
            continue
        packs = sorted((d / "AssetPackages").glob("*.tpac")) if (d / "AssetPackages").is_dir() else []
        if not packs:
            continue
        items = list((d / "ModuleData").glob("items*.xml")) + \
            list((d / "ModuleData" / "items").glob("*.xml")) if (d / "ModuleData").is_dir() else []
        out.append(dict(name=d.name, path=str(d), packs=[str(p) for p in packs],
                        hasItems=bool(items), hasRaces=d.name != 'Native' and bool(RACE.skin_files(d))))
    return out


def bake_mod(mod: str, anims: list[str] | None = None, anim_limit: int = 24,
             force: bool = False, skin_prefer: str = "man",
             skip_anims: bool = False, progress=None,
             max_tex: int | None = None) -> dict:
    e = C.env()
    C.ensure_dirs()
    mod_dir = find_mod(mod)
    mod_name = mod_dir.name
    out_dir = e.data_dir / "mods" / mod_name
    packs = sorted((mod_dir / "AssetPackages").glob("*.tpac"))
    if not packs:
        raise FileNotFoundError(f"{mod_dir} 下没有 AssetPackages/*.tpac")

    _log(f"烘焙 mod: {mod_name}")
    _log(f"  包: {', '.join(p.name for p in packs)}")
    rig_json = ensure_skeleton(force)

    # ---- 几何 / 材质 / 贴图 ----
    exp = out_dir / "_export"
    # 只要 mip0：解码与渲染都只用第一级，导出整条 mip 链纯属白做功（还更慢、更占磁盘）
    if exp.exists():
        exp.resolve().relative_to(e.data_dir.resolve())
        shutil.rmtree(exp)
    pj = export_packages(packs, exp)
    material_by_guid = {m["guid"].lower(): m["name"]
                        for m in pj["materials"] if m.get("guid")}

    geo_dir = out_dir / "geo"
    tex_dir = out_dir / "tex"
    if force:
        for d in (geo_dir, tex_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

    meshes = []
    audit_all = dict(submeshes=0, vertices=0, triangles=0, materials=[], warnings=[])
    for m in pj["meshes"]:
        src = exp / m["file"]
        if not src.exists():
            continue
        subs = GEO.parse_gdmb(src)
        CLOTH.attach(subs, m["clothSettings"])
        for sub in subs:
            if sub.get("material_guid"):
                sub["material"] = material_by_guid.get(sub["material_guid"].lower(), sub["material"])
        lod0 = [s for s in subs if s["lod"] == 0]
        subs = lod0 or subs
        a = GEO.audit(subs)
        audit_all["submeshes"] += a["submeshes"]
        audit_all["vertices"] += a["vertices"]
        audit_all["triangles"] += a["triangles"]
        for mm in a["materials"]:
            if mm not in audit_all["materials"]:
                audit_all["materials"].append(mm)
        audit_all["warnings"] += [f"{m['name']}: {w}" for w in a["warnings"]]
        dst = geo_dir / f"{m['name']}.mbmg"
        GEO.write_meshpack(dst, subs)
        meshes.append(dict(mesh=m["name"], file=f"mods/{mod_name}/geo/{m['name']}.mbmg",
                           sourcePack=m["sourcePack"],
                           lod=m.get("lod", 0),
                           humanSkeleton=a["humanSkeleton"],
                           maxBoneIndex=a["max_bone_index"],
                           submeshes=[dict(name=s["name"], material=s["material"],
                                           vertices=len(s["pos"]),
                                           triangles=0 if s["tri"] is None else s["tri"].size // 3,
                                           bbox=list(s["bbox"])) for s in subs],
                           vertices=a["vertices"], triangles=a["triangles"]))

    _log(f"  导出: {len(meshes)} 网格 / {audit_all['submeshes']} 子网格 / "
         f"{audit_all['vertices']} 顶点 / {audit_all['triangles']} 三角")

    # ---- 材质 ----
    materials = {}
    for m in pj.get("materials", []):
        materials[m["name"]] = MAT.simplify_material(m)

    # ---- 贴图 ----
    textures = {}
    for t in pj.get("textures", []):
        try:
            tex_src = (exp / t["file"]).parent
            meta = MAT.export_texture(t, tex_src, tex_dir, max_size=max_tex)
            meta["file"] = f"mods/{mod_name}/tex/{meta['name']}.png"
            textures[meta["name"]] = meta
        except Exception as ex:
            _log(f"  ! 贴图 {t.get('name')} 解码失败: {ex}")

    shutil.rmtree(exp, ignore_errors=True)

    # ★ 判断材质「实际是否不透明」。blendMode=factor 的材质里有很多贴图 alpha 基本全 1
    #   （实测曹操的脸），它们该照常写深度；否则双面材质的内表面会盖住外表面。
    #   真正半透明的（纱、头发边缘）alpha 会有大量中低值，仍按混合处理。
    for _name, _mat in materials.items():
        _alb = (_mat.get("textures") or {}).get("albedo")
        _t = textures.get(_alb) if _alb else None
        if _t and "alphaMedian" in _t:
            _mat["opaque"] = bool(_t["alphaMedian"] > 0.9 and _t["alphaLow"] < 0.05)
        else:
            _mat["opaque"] = _mat.get("blendMode") == "no_alpha_blend"

    # ---- 装备定义 ----
    items = EQ.load_module_items(mod_dir, mod_name)
    items = [it for it in items if it["mesh"]]
    sets = EQ.detect_sets(items)
    _log(f"  装备件: {len(items)}" + (f"（识别出 {len(sets)} 套）" if sets else ""))

    # ---- 原版皮肤部件 ----
    race_skins = RACE.bake_skins(mod_dir, packs, out_dir, rig_json, meshes)
    vanilla = dict(parts={}, materials={}, textures={}) if race_skins else ensure_vanilla(force)
    skins = race_skins or EQ.parse_skins(e.native_data / "skins.xml")
    skin = RACE.select_skin(skins, skin_prefer) if race_skins else (skins.get(skin_prefer) or EQ.vanilla_skin_catalog(e.native_data / "skins.xml"))
    # 把可用体型一并给前端；前端只加载选中的那一套，否则男女两具身体会同时出现
    skins_view = {k: dict(label=SK_LABELS.get(k, k), gender=v["gender"],
                          maturity=v["maturity"], parts=v["parts"])
                  for k, v in skins.items() if v["maturity"] == "adult"}
    if race_skins:
        skins_view = race_skins
        rig_json = skin['skeleton']

    # ---- 动画 ----
    catalog = ensure_catalog(force) if not skip_anims else dict(items=[], core=[], count=0, actionCount=0)
    anim_entries = []
    if not skip_anims:
        if anims:
            sel = A.resolve_anims(catalog, anims)
            _log(f"  选定动画 {len(sel)} 个（来自请求: {', '.join(anims[:6])}{'…' if len(anims) > 6 else ''}）")
        else:
            sel = A.default_selection(catalog, limit=anim_limit)
            _log(f"  默认动画集 {len(sel)} 个")
        anim_entries = ensure_anims(sel, force=force, progress=progress)

    manifest = dict(
        format=1, mod=mod_name, modPath=str(mod_dir), builtAt=time.time(),
        packageVersion=PACKAGE_VERSION, sourcePacks=[p.name for p in packs],
        meshes=meshes, materials=materials, textures=textures,
        items=items, sets=sets, skeleton=rig_json, vanilla=vanilla,
        skin=dict(name=skin["name"], parts=skin["parts"],
                  label=SK_LABELS.get(skin["name"], skin["name"])),
        skins=skins_view,
        raceVersion=RACE.RACE_VERSION, previewType='race' if race_skins else 'equipment',
        anims=anim_entries,
        catalogSummary=dict(count=catalog.get("count", 0), actionCount=catalog.get("actionCount", 0)),
        audit=audit_all,
        stats=dict(vertices=audit_all["vertices"], triangles=audit_all["triangles"]),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    # 完整目录单独放（可能很大），查看器需要时再取
    if not skip_anims:
        catfile = out_dir / "catalog.json"
        catfile.write_text(json.dumps(
            dict(items=[dict(key=x["key"], anim=x["anim"], tEnd=x["tEnd"], duration=x["duration"],
                             rate=x["rate"], cyclic=x["cyclic"], category=x["category"],
                             actionCount=x["actionCount"], actions=x["actions"][:6])
                        for x in catalog["items"]]), ensure_ascii=False), encoding="utf-8")

    for w in audit_all["warnings"]:
        _log(f"  ! {w}")
    _log(f"  完成 → {out_dir}")
    return manifest


SK_LABELS = {"man": "成年男性", "woman": "成年女性"}
