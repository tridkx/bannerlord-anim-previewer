"""Read cloth settings from TPAC metadata without loading vertex/texture segments.

Layout follows the same Mesh/ClothingMaterial reader as mbtool (subversions 0–2).
Unknown versions fail closed: never infer cloth from alpha or a material name.
"""
from __future__ import annotations

import io
import math
import struct
import uuid
from pathlib import Path


class Reader:
    def __init__(self, stream):
        self.stream = stream

    def take(self, n):
        if n < 0 or n > 64 * 1024 * 1024:
            raise ValueError("invalid TPAC metadata length")
        b = self.stream.read(n)
        if len(b) != n:
            raise ValueError("truncated TPAC metadata")
        return b

    def value(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def count(self):
        n = self.value('i')
        if not 0 <= n <= 100000:
            raise ValueError("invalid TPAC count")
        return n

    def string(self):
        return self.take(self.count()).decode('utf-8')

    def strings(self):
        return [self.string() for _ in range(self.count())]

    def guid(self):
        return str(uuid.UUID(bytes_le=self.take(16)))


def parse_metamesh(data):
    r = Reader(io.BytesIO(data))
    version = r.value('I')
    if version not in (0, 1):
        raise ValueError(f"unsupported metamesh version {version}")
    r.take(20)
    r.string()
    proxy = r.guid()
    body = ''
    if version >= 1:
        r.take(4)
        if r.value('I'):
            body = r.string()
    out = []
    for _ in range(r.count()):
        r.take(1)
        lod = r.value('i')
        r.take(20)
        subversion = r.value('I')
        if subversion not in (0, 1, 2):
            raise ValueError(f"unsupported mesh subversion {subversion}")
        r.take(16)
        name = r.string()
        r.take(4)
        flags = r.strings()
        r.take(16 + 64 + 24 + 52 + 4)
        r.strings()
        distance = r.value('f')
        material = {'name': r.string()}
        for key in ('bending', 'shearing', 'stretching', 'anchor', 'damping',
                    'gravity', 'linearInertia', 'airDrag', 'wind'):
            material[key] = r.value('f')
        frequency = r.value('i')
        precise, dummy = r.value('B'), r.value('B')
        if subversion >= 1:
            material['maxVelocity'] = r.value('f')
            material['velocityMultiplier'] = r.value('f')
            r.take(1)
        if not all(math.isfinite(v) for v in material.values() if isinstance(v, float)) or not math.isfinite(distance):
            raise ValueError('non-finite cloth settings')
        out.append(dict(name=name, lod=lod, cloth=dict(
            enabled=bool({'uses_cloth_simulation', 'force_enable_cloth'} & set(flags)),
            source='tpac', maxDistance=max(0, distance), material=material,
            frequency=max(30, min(240, frequency)), precise=bool(precise),
            dummyParticles=bool(dummy), simulationMesh=proxy, collisionBody=body)))
    r.take(16)
    r.take(r.count() * 16)
    r.take(2)
    if r.stream.read(1):
        raise ValueError('unconsumed metamesh metadata')
    return out


def read_settings(path: Path):
    settings, warnings = {}, []
    with path.open('rb') as stream:
        r = Reader(stream)
        if r.value('I') != 0x43415054:
            raise ValueError('not a TPAC file')
        version = r.value('I')
        if version not in (1, 2):
            raise ValueError(f'unsupported TPAC version {version}')
        r.take(16)
        count = r.count()
        r.take(8)
        for _ in range(count):
            kind = r.guid()
            r.take(16 + (4 if version > 1 else 0))
            name = r.string()
            metadata = r.take(r.value('Q'))
            r.take(8)
            r.take(r.count() * 69)
            r.take(r.count() * 48)
            if kind.startswith('a08f8b97'):  # Metamesh GUID, little-endian decoded
                try:
                    settings[name] = parse_metamesh(metadata)
                except (ValueError, struct.error, UnicodeError) as ex:
                    warnings.append(f'{name}: cloth metadata unavailable: {ex}')
    return settings, warnings


def attach(subs, entries):
    by_key = {(s['name'], s['lod']): s['cloth'] for s in entries}
    for s in subs:
        s['cloth'] = by_key.get((s['name'], s['lod']),
                                dict(enabled=False, source='unavailable'))


def upgrade_cache(mod_dir: Path):
    """Refresh only metadata; leave cached vertex/texture/animation bytes intact."""
    import json
    from .config import env
    manifest = json.loads((mod_dir / 'manifest.json').read_text(encoding='utf-8'))
    packs = sorted((Path(manifest['modPath']) / 'AssetPackages').glob('*.tpac'))
    if not packs:
        raise FileNotFoundError(f"no source TPAC for {manifest['mod']}")
    settings, warnings = {}, []
    for pack in packs:
        pack_settings, pack_warnings = read_settings(pack)
        settings[pack.name] = pack_settings
        warnings.extend(f'{pack.name}: {warning}' for warning in pack_warnings)
    # 旧清单只烘焙第一个包；新清单明确记录每个网格来源。
    changed = 0
    for mesh in manifest['meshes']:
        path = env().data_path(mesh['file'])
        buf = path.read_bytes()
        magic, version, count, length = struct.unpack_from('<4sIII', buf)
        if magic != b'MBMG' or version != 1:
            raise ValueError(f'unsupported meshpack: {path}')
        metas = json.loads(buf[16:16 + length])
        source = mesh.get('sourcePack', packs[0].name)
        entries = {(e['name'], e['lod']): e['cloth']
                   for e in settings.get(source, {}).get(mesh['mesh'], [])}
        for meta in metas:
            meta['cloth'] = entries.get((meta['name'], meta['lod']),
                                       dict(enabled=False, source='unavailable'))
        encoded = json.dumps(metas, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        encoded += b' ' * (-len(encoded) % 4)
        updated = struct.pack('<4sIII', magic, version, count, len(encoded)) + encoded + buf[16 + length:]
        if updated != buf:
            tmp = path.with_suffix('.mbmg.tmp')
            tmp.write_bytes(updated)
            tmp.replace(path)
            changed += 1
    return changed, warnings


if __name__ == '__main__':
    import argparse
    from .config import env
    parser = argparse.ArgumentParser(description='Refresh cloth metadata in existing preview caches')
    parser.add_argument('mod', nargs='?', help='mod name; omit to refresh all baked mods')
    args = parser.parse_args()
    root = env().data_dir / 'mods'
    targets = [root / args.mod] if args.mod else sorted(root.iterdir())
    for target in targets:
        if not (target / 'manifest.json').exists():
            continue
        try:
            changed, warnings = upgrade_cache(target)
        except FileNotFoundError as ex:
            print(f'{target.name}: skipped ({ex})')
            continue
        print(f'{target.name}: {changed} meshpacks updated; {len(warnings)} warnings')
        for warning in warnings:
            print(warning)
