# -*- coding: utf-8 -*-
"""装备与原版角色组合 —— "皮套 mod 的实机情况"里最容易翻车的一环。

Bannerlord 的角色不是一个整体网格，而是「原版皮肤部件 + 若干装备件」按槽位拼起来的：

  原版部件（skins.xml 定义，全部在 EmAssetPackages/human/human.tpac）
      body / shoulders / legs / hands / face / underwear_*
  ★ 装备件通过 covers_* 决定隐藏哪些原版部件 —— 这些标志不会渲染，
    而是被反序列化成 MeshesMask（SkinMask 位掩码），由原生代码执行隐藏。
    预览器不复刻这套 mask，就**根本看不见"原版身体露出来"**，等于白做。

槽位规则（来自 items.xml 的 Type，实测原版 194+ 件防具）：
    BodyArmor→Body  HeadArmor→Head  LegArmor→Leg  ArmArmor→Gloves  Cape→Cape
  ★ 头部槽一次只能穿一件 —— 所以脸和头发必须在同一个 mesh 里，否则戴上脸就没头发。
  ★ covers_legs 原版只出现在 LegArmor 上；放 BodyArmor 上无效。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

# items.xml 的 Type → 装备槽位
TYPE_TO_SLOT = {
    "BodyArmor": "Body", "HeadArmor": "Head", "LegArmor": "Leg",
    "ArmArmor": "Gloves", "HandArmor": "Gloves",     # 实测 mod 里用 HandArmor
    "Cape": "Cape", "Horse": "Horse", "Shield": "Shield",
    "Bow": "Item0", "Crossbow": "Item0", "Arrows": "Item1", "Bolts": "Item1",
    "OneHandedWeapon": "Item0", "TwoHandedWeapon": "Item0", "Polearm": "Item0",
    "Goods": "Other", "Book": "Other", "Animal": "Horse",
}
SLOT_ORDER = ["Head", "Cape", "Body", "Gloves", "Leg", "Item0", "Horse"]
SLOT_LABEL = {"Head": "头部", "Cape": "披风", "Body": "身体", "Gloves": "手",
              "Leg": "腿脚", "Item0": "手持", "Horse": "坐骑"}

# 原版皮肤部件（skins.xml 的字段名 → 预览器内部名）
SKIN_FIELDS = {
    "body_meta_mesh": "body", "body_meta_mesh_shoulders": "shoulders",
    "legs_mesh": "legs", "hands_mesh": "hands", "face_meta_mesh": "face",
    "underwear_bottom_mesh": "underwear_bottom", "underwear_top_mesh": "underwear_top",
}
SKIN_LABEL = {"body": "原版躯干", "shoulders": "原版肩", "legs": "原版脚", "hands": "原版手",
              "face": "原版头/脸", "underwear_bottom": "原版内裤", "underwear_top": "原版内衣上"}


def parse_skins(path: Path) -> dict:
    """skins.xml → 每个 skin（性别/年龄段）用哪些原版网格。"""
    root = ET.parse(path).getroot()
    out = {}
    for race in root.findall("race"):
        for skin in race.findall("skin"):
            name = skin.get("name")
            if not name:
                continue
            parts = {}
            for field, key in SKIN_FIELDS.items():
                v = skin.get(field)
                if v:
                    parts[key] = v
            out[name] = dict(race=race.get("id"), gender=int(skin.get("gender") or 0),
                             name=name, maturity=skin.get("mesh_maturity_type"),
                             parts=parts, min_scale=float(skin.get("min_scale") or 1.0))
    return out


def parse_items(path: Path, module: str) -> list[dict]:
    """items.xml → 装备件列表（含遮盖标志）。"""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        # 最常见的坑：XML 注释里出现 "--"，.NET XmlReader 会拒绝整个文件
        raise RuntimeError(
            f"{path} 不是合法 XML（{e}）。\n"
            "  常见原因：注释里用了连续减号做分隔线 —— XML 规范禁止 '--'，"
            "写成一排减号会让整个文件被拒绝，表现为「mod 加载了但一件装备都没有」。"
            "  分隔线请用 '===='。") from e

    items = []
    for it in root.findall("Item"):
        iid = it.get("id")
        if not iid:
            continue
        armor = it.find("./ItemComponent/Armor")
        itype = it.get("Type") or ""
        covers = {}
        hair_cover = beard_cover = None
        if armor is not None:
            for k in ("covers_body", "covers_hands", "covers_legs", "covers_head"):
                if (armor.get(k) or "").lower() == "true":
                    covers[k[7:]] = True
            hair_cover = armor.get("hair_cover_type")
            beard_cover = armor.get("beard_cover_type")
        items.append(dict(
            id=iid, name=it.get("name") or iid, mesh=it.get("mesh") or "",
            type=itype, slot=TYPE_TO_SLOT.get(itype, "Other"), module=module,
            covers=covers, hairCover=hair_cover, beardCover=beard_cover,
            culture=it.get("culture") or "", weight=float(it.get("weight") or 0),
            armor=None if armor is None else {
                k: armor.get(k) for k in
                ("body_armor", "arm_armor", "leg_armor", "head_armor") if armor.get(k)},
        ))
    return items


def hidden_skin_parts(equipped: list[dict], skin_parts: dict) -> dict[str, str]:
    """按已装备的件算出「哪些原版皮肤部件该被隐藏」，并给出隐藏它的那件装备（便于排查）。"""
    hidden: dict[str, str] = {}
    for it in equipped:
        c = it.get("covers") or {}
        src = it["id"]
        if c.get("body"):
            hidden.setdefault("body", src)
            if "shoulders" in skin_parts:
                hidden.setdefault("shoulders", src)
            if "underwear_top" in skin_parts:
                hidden.setdefault("underwear_top", src)
        if c.get("hands"):
            hidden.setdefault("hands", src)
        if c.get("legs"):
            hidden.setdefault("legs", src)
            if "underwear_bottom" in skin_parts:
                hidden.setdefault("underwear_bottom", src)
        if c.get("head"):
            hidden.setdefault("face", src)
        if (it.get("hairCover") or "").lower() == "all":
            hidden.setdefault("face", src)
    return hidden


def preview_plan(equipped: list[dict], skin: dict) -> dict:
    """给预览器的一份「该显示什么」清单。"""
    parts = dict(skin.get("parts") or {})
    hidden = hidden_skin_parts(equipped, parts)
    shown = {k: v for k, v in parts.items() if k not in hidden}
    return dict(
        skinName=skin.get("name"), skinParts=parts,
        hiddenParts={k: {"mesh": parts[k], "hiddenBy": hidden[k]} for k in hidden if k in parts},
        visibleParts=shown,
        # 诊断：这些是"没被任何装备遮住、会原样露出来"的原版部件
        exposed=[k for k in shown],
    )


def load_module_items(module_dir: Path, module_name: str) -> list[dict]:
    """读一个 mod 的装备定义（支持 items.xml 与 items/ 目录两种布局）。"""
    out: list[dict] = []
    single = module_dir / "ModuleData" / "items.xml"
    if single.exists():
        out += parse_items(single, module_name)
    d = module_dir / "ModuleData" / "items"
    if d.is_dir():
        for f in sorted(d.glob("*.xml")):
            try:
                out += parse_items(f, module_name)
            except RuntimeError as e:
                print(f"  ! 跳过 {f.name}: {e}")
    return out


def vanilla_skin_catalog(skins_xml: Path, prefer: str = "man") -> dict:
    """挑一个默认的原版体型（默认成年男性；皮套多为女性角色时改用 woman）。"""
    all_skins = parse_skins(skins_xml)
    if prefer in all_skins:
        return all_skins[prefer]
    for k, v in all_skins.items():
        if v["maturity"] == "adult":
            return v
    return next(iter(all_skins.values()))


# --------------------------------------------------------------------------- 多套装备

def detect_sets(items: list[dict], min_size: int = 2) -> list[dict]:
    """把装备按命名前缀聚成「套」，供界面做「一键穿整套」。

    一个 mod 里常有多套角色（实测 XianJian7Outfits = 月清疏 + 白茉晴两套、
    LVBU and DIAOCHAN = 56 件多角色）。逐件勾选既慢又容易漏，
    而命名上通常自带分组线索：`xj7_yue_body` / `xj7_yue_head` / `xj7_yue_feet`。

    做法：取每个 id 的**下划线分段前缀**与**字符前缀**，保留能覆盖 >= min_size 件、
    且不被更具体前缀完全覆盖的那些。要求至少分出 2 组才认为"这个 mod 有多套"。
    """
    ids = [it["id"] for it in items]
    if len(ids) < min_size * 2:
        return []
    cover: dict[str, set] = {}
    for i in ids:
        parts = i.split("_")
        for k in range(1, len(parts)):            # xj7 / xj7_yue
            cover.setdefault("_".join(parts[:k]), set()).add(i)
        for k in range(4, len(i)):                # 没有下划线时的兜底：lvbu…
            cover.setdefault(i[:k], set()).add(i)

    cands = {p: g for p, g in cover.items() if min_size <= len(g) < len(ids)}
    # 只留"极大"前缀：若存在更长的前缀覆盖完全相同的一组，则短的那个没有信息量
    keep: dict[str, set] = {}
    for p, g in cands.items():
        if any(len(q) > len(p) and cands.get(q) == g for q in cands):
            continue
        keep[p] = g

    # 去掉被别的组完全包含的组（保留更具体的划分）
    out = []
    for p, g in sorted(keep.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if any(g < set(o["members"]) for o in out):
            continue
        out.append(dict(key=p, label=p, members=sorted(g)))
    return out if len(out) >= 2 else []
