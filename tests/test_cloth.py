import io
import json
import struct
import tempfile
import unittest
import uuid
from pathlib import Path

import numpy as np

from baker.cloth import parse_metamesh, attach, read_settings, upgrade_cache
from baker.geometry import write_meshpack, read_meshpack


def fixture(subversion=2, enabled=True):
    b = io.BytesIO()
    def put(fmt, *v): b.write(struct.pack('<' + fmt, *v))
    def string(s):
        encoded = s.encode(); put('i', len(encoded)); b.write(encoded)
    def guid(): b.write(uuid.UUID(int=0).bytes_le)
    put('I', 1); guid(); put('f', 1); string('body'); guid()
    put('IIi', 0, 0, 1)
    put('BiI', 1, 0, 2); guid(); put('I', subversion); guid(); string('skirt')
    put('Ii', 0, int(enabled))
    if enabled: string('uses_cloth_simulation')
    guid(); b.write(bytes(64 + 24 + 52 + 4)); put('i', 0); put('f', .75)
    string('silk'); put('9f', .3, .4, .8, 1, .2, 9.81, .5, .1, 1)
    put('iBB', 120, 1, 0)
    if subversion >= 1: put('ffB', 8, 1, 0)
    guid(); put('iBB', 0, 0, 0)
    return b.getvalue()


class ClothMetadataTests(unittest.TestCase):
    def test_supported_versions_and_values(self):
        for version in (0, 1, 2):
            entry = parse_metamesh(fixture(version))[0]
            self.assertTrue(entry['cloth']['enabled'])
            self.assertEqual(entry['cloth']['frequency'], 120)
            self.assertAlmostEqual(entry['cloth']['maxDistance'], .75)
            self.assertAlmostEqual(entry['cloth']['material']['gravity'], 9.81, places=5)

    def test_flag_required(self):
        self.assertFalse(parse_metamesh(fixture(enabled=False))[0]['cloth']['enabled'])

    def test_fail_closed(self):
        for data in (fixture()[:-1], fixture() + b'x', fixture(3)):
            with self.assertRaises(ValueError): parse_metamesh(data)

    def test_lod_mapping(self):
        subs = [dict(name='skirt', lod=0), dict(name='skirt', lod=1)]
        attach(subs, parse_metamesh(fixture()))
        self.assertTrue(subs[0]['cloth']['enabled'])
        self.assertEqual(subs[1]['cloth']['source'], 'unavailable')

    def test_tpac_and_meshpack_payload_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packs = root / 'AssetPackages'
            packs.mkdir()
            data = fixture()
            name = b'dress'
            asset = (uuid.UUID('a08f8b97-197c-4bea-b95b-53846cae834e').bytes_le + bytes(16)
                     + struct.pack('<Ii', 1, len(name)) + name + struct.pack('<Q', len(data))
                     + data + struct.pack('<qii', 0, 0, 0))
            pack = packs / 'test.tpac'
            pack.write_bytes(struct.pack('<II', 0x43415054, 2) + bytes(16)
                             + struct.pack('<III', 1, 36 + len(asset), 0) + asset)
            settings, warnings = read_settings(pack)
            self.assertEqual(warnings, [])
            self.assertTrue(settings['dress'][0]['cloth']['enabled'])
            mesh = root / 'dress.mbmg'
            sub = dict(name='skirt', material='silk', material_guid='', lod=0,
                       pos=np.array([[0, 0, 1], [1, 0, 1], [0, 1, 1]], dtype=np.float32),
                       nrm=None, uv=np.zeros((3, 2)), col=np.full((3, 4), 127, dtype=np.uint8),
                       bone_idx=np.zeros((3, 4), dtype=np.uint8),
                       bone_w=np.tile([255, 0, 0, 0], (3, 1)), tri=np.array([[0, 1, 2]]),
                       bbox=[0, 0, 1, 1, 1, 1])
            write_meshpack(mesh, [sub])
            before = mesh.read_bytes()
            (root / 'manifest.json').write_text(json.dumps(dict(mod='test', modPath=str(root),
                meshes=[dict(mesh='dress', file=str(mesh))])), encoding='utf-8')
            self.assertEqual(upgrade_cache(root), (1, []))
            after = mesh.read_bytes()
            length = lambda buf: struct.unpack_from('<I', buf, 12)[0]
            self.assertEqual(before[16 + length(before):], after[16 + length(after):])
            self.assertTrue(read_meshpack(mesh)[0]['cloth']['enabled'])
            self.assertEqual(upgrade_cache(root), (0, []), 'metadata refresh is idempotent')


if __name__ == '__main__': unittest.main()
