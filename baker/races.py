"""Registered adult race skins and compatible human rigs for offline preview."""
from pathlib import Path
import math
import xml.etree.ElementTree as ET

from . import equipment as EQ, mbtool as MB, skeleton as SK

RACE_VERSION = 1

# Exact catalog keys avoid accidentally selecting animal or scene animations.
PREVIEW_ANIMS = (
    'inventory_idle', 'walk_forward_unarmed', 'run_forward_unarmed',
    'jumps_forward', 'slashright_onehanded_balance', 'overswing_onehanded_new',
    'mainmap_attack_2h', 'defend_forward_onehanded', 'bow_ready_continue',
    'rider_idle_lance_1', 'rider_walk_bow', 'rider_gallop_bow',
)


def default_anims(catalog: dict, limit: int) -> list[dict]:
    from . import actions
    by_key = {entry['key']: entry for entry in catalog['items']}
    selected = [by_key[key] for key in PREVIEW_ANIMS if key in by_key and by_key[key].get('tEnd', 0) > 0][:limit]
    seen = {entry['key'] for entry in selected}
    for entry in actions.default_selection(catalog, limit=limit):
        if len(selected) >= limit:
            break
        if entry['key'] not in seen and entry.get('tEnd', 0) > 0:
            selected.append(entry)
            seen.add(entry['key'])
    return selected


def skin_files(module: Path) -> list[Path]:
    project = module / 'ModuleData' / 'project.mbproj'
    if project.exists():
        files = []
        for entry in ET.parse(project).getroot().iter('file'):
            if entry.get('type') == 'skin' and entry.get('name'):
                path = (module / entry.get('name')).resolve()
                path.relative_to(module.resolve())
                files.append(path)
        return files
    path = module / 'ModuleData' / 'skins.xml'
    return [path] if path.exists() else []


def adult_skins(module: Path) -> dict:
    skins = {}
    for path in skin_files(module):
        skins.update(EQ.parse_skins(path))
    return {key: skin for key, skin in skins.items() if skin['maturity'] == 'adult'}


def validate_rig(rig: dict, native: dict) -> None:
    names = lambda xs: [name.removeprefix('bip01_') for name in xs]
    if (rig['boneCount'] != 28 or names(rig['names']) != names(native['names'])
            or list(rig['parent']) != list(native['parent'])):
        raise ValueError(f"骨架 {rig['name']} 与原版 28 骨人形顺序/层级不兼容，第一版无法播放原版动画")


def bake_skins(module: Path, packs: list[Path], output: Path,
               native: dict, meshes: list[dict]) -> dict:
    skins = adult_skins(module)
    if not skins:
        return {}
    assets = {}
    for pack in packs:
        for entry in MB.list_assets(pack):
            if entry['type'] == 'Skeleton':
                assets[entry['name']] = (pack, entry['guid'])
    available = {entry['mesh'] for entry in meshes}
    rigs = {}
    for key, skin in skins.items():
        name = skin['skeleton']
        if name not in rigs:
            if name in assets:
                pack, guid = assets[name]
                raw = MB.skeljson(pack, guid, output / 'rigs' / f'{guid}.json')
                rig = SK.to_viewer(SK.parse_skeljson(raw))
            elif name == 'human_skeleton':
                rig = native
            else:
                raise ValueError(f"种族皮肤 {key} 的骨架 {name!r} 未在当前 Mod 资源包中找到")
            validate_rig(rig, native)
            rigs[name] = rig
        missing = set(skin['parts'].values()) - available
        if missing:
            raise ValueError(f"种族皮肤 {key} 缺少身体资源: {', '.join(sorted(missing))}；第一版要求身体部件位于当前 Mod 内")
        if not skin['parts'].get('body'):
            raise ValueError(f'种族皮肤 {key} 未定义 body_meta_mesh')
        scale = skin['min_scale']
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError(f'种族皮肤 {key} 的 min_scale 无效')
        skin.update(label=f"{skin['race']} · {'女性' if skin['gender'] else '男性'}",
                    skeleton=rigs[name], scale=scale)
    return skins


def select_skin(skins: dict, prefer: str) -> dict:
    if prefer in skins:
        return skins[prefer]
    gender = 1 if prefer == 'woman' else 0
    return next((skin for skin in skins.values() if skin['gender'] == gender), next(iter(skins.values())))


def rig_from_viewer(data: dict) -> dict:
    import numpy as np
    return dict(name=data['name'], boneCount=data['boneCount'], names=data['names'],
                parent=np.array(data['parent']),
                restLocal=np.array(data['restLocal']).reshape(-1, 4, 4),
                restWorld=np.array(data['bindWorld']).reshape(-1, 4, 4))
