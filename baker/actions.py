# -*- coding: utf-8 -*-
"""动作集：解析 action_sets.xml，建立「动作类型 → 动画」映射，并合成动画目录。

为什么要它：直接给用户看 4052 个动画名（`anim_command_follow_2h_left_stance`）没有意义，
   用户想找的是「装备页待机」「走路」「挥砍」。action_sets.xml 正是游戏自己的这份索引
   —— `act_inventory_idle` → `inventory_idle`，且用 base_set 继承层层叠加。

播放速率：动画的 t 轴不是秒。AnimationClip 声明了秒数，于是 速率 = t_end / duration。
   实测 inventory_idle = 1267/15.0 = 84.5、walk_barmaid = 40/1.3 = 30.8 —— 每个动画都不同，
   不能拍脑袋定 fps，否则预览的节奏和游戏对不上。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

# 动作类型前缀 → 人类可读分类（顺序敏感：先匹配长的）
CATEGORIES: list[tuple[str, str]] = [
    ("inventory", "装备页/物品栏"), ("character_developer", "开发者调试"),
    ("conversation", "对话"), ("cutscene", "过场"), ("greeting", "打招呼"),
    ("talk_to", "交谈"), ("gossip", "闲聊"), ("argue", "争执"), ("taunt", "挑衅"),
    ("death", "死亡"), ("fall", "倒地"), ("jump", "跳跃"), ("crouch", "蹲伏"),
    ("defend", "防御"), ("blocked", "受击/格挡"), ("strike", "攻击"), ("release", "射击释放"),
    ("ready", "预备/持械"), ("quick", "快速动作"), ("usage", "使用物品"),
    ("pickup", "拾取"), ("rider", "骑乘动作"), ("mount", "上下马"), ("horse", "马术"),
    ("camel", "骆驼"), ("walk", "行走"), ("run", "奔跑"), ("idle", "待机"),
    ("stand", "站立"), ("guard", "守卫"), ("main", "主线姿态"), ("cheer", "欢呼"),
    ("dance", "舞蹈"), ("drink", "饮酒"), ("eat", "进食"), ("sit", "坐"),
    ("sleep", "睡眠"), ("work", "劳作"), ("die", "死亡"), ("spawn", "出生"),
]

# 常用「先烘焙」清单：装装备时最常看的那几个（用户明确要先看角色待机）
CORE_ACTIONS = [
    "act_inventory_idle_start", "act_inventory_idle", "act_inventory_cloth_equip",
    "act_inventory_glove_equip", "act_character_developer_idle",
    "act_walk_idle_unarmed", "act_walk_forward_unarmed", "act_run_forward_unarmed",
    "act_jump", "act_stand_1", "act_stand_2", "act_greeting_front_1",
]


def parse_action_sets(path: Path) -> dict:
    root = ET.parse(path).getroot()
    sets: dict[str, dict] = {}
    for s in root.findall("action_set"):
        sid = s.get("id")
        sets[sid] = dict(id=sid, base=s.get("base_set"), skeleton=s.get("skeleton"),
                         movement=s.get("movement_system"),
                         acts={a.get("type"): a.get("animation") for a in s.findall("action") if a.get("type")})

    cache: dict[str, dict] = {}

    def resolve(sid: str | None, seen: frozenset[str] = frozenset()) -> dict:
        if not sid or sid not in sets:
            return {}
        if sid in cache:
            return cache[sid]
        if sid in seen:
            return {}
        base = resolve(sets[sid]["base"], seen | {sid})
        merged = dict(base)
        merged.update(sets[sid]["acts"])
        cache[sid] = merged
        return merged

    for sid in sets:
        resolve(sid)
    return dict(sets={k: {kk: vv for kk, vv in v.items() if kk != "acts"} for k, v in sets.items()},
                resolved=cache)


def category_of(action_type: str) -> str:
    t = action_type[4:] if action_type.startswith("act_") else action_type
    for pref, label in CATEGORIES:
        if t.startswith(pref):
            return label
    return "其他"


# 名字兜底分类（大部分动画没被 as_human_warrior 引用，只能按名字猜）
NAME_HINTS: list[tuple[str, str]] = [
    ("inventory", "装备页/物品栏"), ("idle", "待机"), ("walk", "行走"), ("run", "奔跑"),
    ("jump", "跳跃"), ("crouch", "蹲伏"), ("defend", "防御"), ("block", "防御"),
    ("strike", "攻击"), ("attack", "攻击"), ("swing", "攻击"), ("thrust", "攻击"),
    ("death", "死亡"), ("die", "死亡"), ("fall", "倒地"), ("kick", "踢"),
    ("horse", "马术"), ("rider", "骑乘动作"), ("mount", "上下马"), ("camel", "骆驼"),
    ("talk", "交谈"), ("dialog", "对话"), ("dlg", "对话"), ("conversation", "对话"),
    ("greet", "打招呼"), ("cheer", "欢呼"), ("taunt", "挑衅"), ("dance", "舞蹈"),
    ("drink", "饮酒"), ("eat", "进食"), ("sit", "坐"), ("sleep", "睡眠"),
    ("pickup", "拾取"), ("carry", "搬运"), ("work", "劳作"), ("craft", "劳作"),
    ("draw", "预备/持械"), ("ready", "预备/持械"), ("release", "射击释放"),
    ("shoot", "射击释放"), ("reload", "装填"), ("equip", "装备页/物品栏"),
    ("spawn", "出生"), ("pose", "姿态"), ("stand", "站立"),
]


def category_of_name(key: str) -> str:
    k = key.lower()
    for hint, label in NAME_HINTS:
        if hint in k:
            return label
    return "其他"


def _norm(name: str) -> str:
    """动作集里的 animation 名与动画资产名之间差一个 'anim_' 前缀（5 个字符，不是 4 个）。"""
    return name[len("anim_"):] if name.startswith("anim_") else name


def build_catalog(action_sets_xml: Path, animlist: list[dict], cliplist: list[dict]) -> dict:
    """合成动画目录：一条 = 一个可播放动画，带真实时长、速率、分类、以及引用它的动作类型。"""
    parsed = parse_action_sets(action_sets_xml)
    warrior = parsed["resolved"].get("as_human_warrior", {})

    by_guid = {a["guid"]: a for a in animlist if a.get("guid")}
    by_name = {_norm(a["name"]): a for a in animlist}

    # ★ 同一个动画常被多个 clip 引用，而它们的 duration 未必相同（实测 550 个动画如此）。
    #   盲取第一个会让播放速度差整数倍。打分规则：clip 名与动画名一致 > 带 cyclic >
    #   名字是前缀关系；分数相同再比时长。
    def clip_score(clip, key):
        nm = _norm(clip["name"])
        sc = 0
        if nm == key:
            sc += 100
        elif nm.startswith(key) or key.startswith(nm):
            sc += 30
        if "cyclic" in (clip.get("flags") or []):
            sc += 20
        if clip.get("dur"):
            sc += 5
        return sc

    entries: dict[str, dict] = {}
    for clip in cliplist:
        guid = clip.get("anim")
        a = by_guid.get(guid) or by_name.get(clip["name"])
        if a is None:
            continue
        key = _norm(a["name"])
        dur = clip.get("dur")
        t_end = a.get("dur") or 0
        sc = clip_score(clip, key)
        e = entries.get(key)
        if e is None:
            e = entries[key] = dict(
                key=key, anim=a["name"], guid=a["guid"], tEnd=t_end,
                duration=dur, rate=(t_end / dur) if (dur and t_end) else None,
                cyclic=("cyclic" in (clip.get("flags") or [])),
                clips=[], actions=[], category="其他", _score=sc)
        elif sc > e["_score"] and dur:
            # 找到更可信的 clip，换掉时长
            e["_score"] = sc
            e["duration"] = dur
            e["rate"] = (t_end / dur) if t_end else None
            e["cyclic"] = ("cyclic" in (clip.get("flags") or []))
        if clip["name"] not in e["clips"]:
            e["clips"].append(clip["name"])

    # 补上没有 clip 的动画（仍可播放，只是没有权威时长）
    for a in animlist:
        key = _norm(a["name"])
        if key not in entries:
            entries[key] = dict(key=key, anim=a["name"], guid=a["guid"], tEnd=a.get("dur") or 0,
                                duration=None, rate=None, cyclic=False, clips=[], actions=[],
                                category="其他")

    # 动作类型挂到动画上
    for atype, aname in warrior.items():
        if not aname:
            continue
        e = entries.get(_norm(aname))
        if e is None:
            continue
        if atype not in e["actions"]:
            e["actions"].append(atype)

    for e in entries.values():
        e.pop("_score", None)
        cats = {category_of(a) for a in e["actions"]} or set()
        named = cats - {"其他"}
        # 有动作类型就用动作类型定类；否则退回按动画名猜（否则 3600+ 条全是"其他"）
        e["category"] = sorted(named)[0] if named else category_of_name(e["key"])
        e["actionCount"] = len(e["actions"])

    # 排序：先按「有动作类型引用」的多少，再按名字
    items = sorted(entries.values(), key=lambda e: (-e["actionCount"], e["key"]))
    return dict(count=len(items), actionCount=len(warrior), items=items,
                core=[c for c in CORE_ACTIONS if c in warrior])


# 中文口语 → 标准分类名（用户会搜"走路"，而分类叫"行走"）
CN_ALIAS = {
    "走路": "行走", "步行": "行走", "行走": "行走", "移动": "行走",
    "跑步": "奔跑", "跑": "奔跑", "奔跑": "奔跑",
    "站立": "站立", "站": "站立", "待机": "待机", "闲置": "待机", "idle": "待机",
    "攻击": "攻击", "砍": "攻击", "挥砍": "攻击", "劈": "攻击",
    "防御": "防御", "格挡": "防御", "挡": "防御", "盾": "防御",
    "跳": "跳跃", "跳跃": "跳跃", "蹲": "蹲伏", "蹲伏": "蹲伏",
    "死亡": "死亡", "死": "死亡", "倒地": "倒地", "摔倒": "倒地",
    "骑马": "马术", "马": "马术", "骑乘": "骑乘动作", "上下马": "上下马",
    "装备": "装备页/物品栏", "物品栏": "装备页/物品栏", "换装": "装备页/物品栏",
    "对话": "对话", "交谈": "交谈", "聊天": "闲聊", "打招呼": "打招呼",
    "欢呼": "欢呼", "庆祝": "欢呼", "挑衅": "挑衅", "嘲讽": "挑衅",
    "坐": "坐", "坐下": "坐", "睡": "睡眠", "躺": "睡眠",
    "喝酒": "饮酒", "吃": "进食", "拾取": "拾取", "捡": "拾取",
    "搬运": "搬运", "劳作": "劳作", "工作": "劳作", "持械": "预备/持械",
    "射箭": "射击释放", "射击": "射击释放", "装填": "装填",
    "踢": "踢", "舞蹈": "舞蹈", "跳舞": "舞蹈", "出生": "出生", "姿态": "姿态",
}


def resolve_anims(catalog: dict, requested: list[str]) -> list[dict]:
    """把用户给的动作类型/动画名/关键词解析成动画条目列表。"""
    items = catalog["items"]
    by_key = {e["key"]: e for e in items}
    by_anim = {e["anim"]: e for e in items}
    out: list[dict] = []
    seen = set()

    def add(e):
        if e and e["key"] not in seen:
            seen.add(e["key"])
            out.append(e)

    for req in requested:
        r = req.strip()
        if not r:
            continue
        if r in by_key:
            add(by_key[r]); continue
        if r in by_anim:
            add(by_anim[r]); continue
        if r.startswith("act_"):
            for e in items:
                if r in e["actions"]:
                    add(e); break
            continue
        # 中文别名 → 标准分类
        if r in CN_ALIAS:
            for e in items:
                if e["category"] == CN_ALIAS[r]:
                    add(e)
            if out:
                continue
        # 关键词模糊匹配（名字 / 动作类型 / 分类）
        rl = r.lower()
        for e in items:
            if (rl in e["key"].lower() or rl in (e["category"] or "")
                    or any(rl in a.lower() for a in e["actions"])):
                add(e)
    return out


def default_selection(catalog: dict, limit: int = 24) -> list[dict]:
    """默认烘焙集：按「装装备时最常看什么」配额度，而不是按引用数排序。

    按引用数排会选出一堆 camel_rider_* / 某个 NPC 的过场动画 —— 那些对皮套预览毫无价值。
    这里改成：核心动作显式点名 → 再按类别配额补 → 最后才按引用数兜底。
    """
    items = catalog["items"]
    by_key = {e["key"]: e for e in items}
    out: list[dict] = []
    seen: set[str] = set()

    def add(e):
        if e and e["key"] not in seen:
            seen.add(e["key"])
            out.append(e)

    # 1) 核心动作显式点名（装备页待机是用户第一优先）
    for atype in CORE_ACTIONS:
        if len(out) >= limit:
            break
        for e in items:
            if atype in e["actions"] and e["key"] not in seen:
                add(e)
                break

    # 2) 按类别配额补（类别顺序即优先级）
    #    同一类里优先「被玩家动作集引用过」且名字通用（短、不含场景道具词）的
    SCENE_WORDS = ("rudder", "boat", "ship", "oar", "arena", "prison", "stocks",
                   "pillory", "gallows", "chair_", "throne", "banner_", "tavern_")

    def pref(e):
        pen = 1 if any(s in e["key"] for s in SCENE_WORDS) else 0
        return (pen, -e["actionCount"], len(e["key"]))

    quota = [("装备页/物品栏", 5), ("待机", 5), ("站立", 4), ("行走", 4), ("奔跑", 2),
             ("跳跃", 3), ("蹲伏", 2), ("防御", 2), ("攻击", 2), ("使用物品", 1),
             ("拾取", 1), ("欢呼", 1), ("对话", 2), ("坐", 1)]
    for cat, n in quota:
        if len(out) >= limit:
            break
        pool = sorted((e for e in items if e["category"] == cat and e["key"] not in seen),
                      key=pref)
        for e in pool[:n]:
            if len(out) >= limit:
                break
            add(e)

    # 3) 兜底：优先有动作引用、且不是明显无关的（骆驼/马/动物）
    if len(out) < limit:
        skip = ("camel", "horse_", "rider", "sheep", "cow_", "dog_", "goose", "chicken", "hog")
        for e in items:
            if len(out) >= limit:
                break
            if e["key"] in seen or e["actionCount"] == 0:
                continue
            if any(s in e["key"] for s in skip):
                continue
            add(e)
    return out[:limit]
