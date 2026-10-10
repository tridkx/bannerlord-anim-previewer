import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image

from baker import bake, mbtool
from baker.geometry import read_meshpack
from baker.cloth import upgrade_cache


class MultiTpacTests(unittest.TestCase):
    def test_texture_guid_cli_output(self):
        with patch.object(mbtool, 'run', return_value=(
                'Material test\n    [  0] AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA\n'
                '    [  2] bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb\n')):
            self.assertEqual(mbtool.material_texture_guids(Path('a.tpac'), 'test'), {
                '0': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                '2': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'})

    def test_bake_all_packages_and_cross_package_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = root / 'Collection'
            packs = mod / 'AssetPackages'
            packs.mkdir(parents=True)
            for name in ('a.tpac', 'b.tpac', 'c.tpac'):
                (packs / name).touch()
            environment = SimpleNamespace(data_dir=root / 'data', native_data=root)
            environment.data_path = lambda path: environment.data_dir / path
            exports = []

            def export(pack, out_dir, **kwargs):
                exports.append((pack.name, out_dir, kwargs))
                (out_dir / 'geo').mkdir(parents=True)
                (out_dir / 'tex').mkdir()
                # Both mesh packages use the same intermediate filenames.
                (out_dir / 'geo' / 'same.gdmb').touch()
                color = bytes([255, 0, 0, 255] if pack.name == 'a.tpac' else [0, 255, 0, 255])
                (out_dir / 'tex' / 'same.bin').write_bytes(color)
                if pack.name == 'c.tpac':
                    return dict(meshes=[], textures=[], materials=[dict(
                        name='silk', guid='material-guid', textures={'0': ''})])
                name = 'dress' if pack.name == 'a.tpac' else 'boots'
                return dict(meshes=[dict(name=name, file='geo/same.gdmb')],
                            materials=[], textures=[dict(name=name, guid=name + '-guid',
                                file='tex/same.bin', width=1, height=1, format='R8G8B8A8_UNORM')])

            def parse(path):
                x = 1 if path.parent.parent.name == 'pack0' else 2
                return [dict(name='sub', lod=0, material='', material_guid='material-guid',
                    pos=np.array([[0, 0, x], [1, 0, x], [0, 1, x]], dtype=np.float32),
                    nrm=None, uv=np.zeros((3, 2), dtype=np.float32), col=None,
                    bone_idx=np.zeros((3, 4), dtype=np.uint8),
                    bone_w=np.tile(np.array([255, 0, 0, 0], dtype=np.uint8), (3, 1)),
                    tri=np.array([[0, 1, 2]]), bbox=[0, 0, x, 1, 1, x])]

            def cloth(pack):
                name = 'dress' if pack.name == 'a.tpac' else 'boots'
                return {name: [dict(name='sub', lod=0, cloth=dict(
                    enabled=pack.name == 'b.tpac', source='tpac'))]}, []

            skin = dict(name='man', gender='male', maturity='adult', parts={})
            with patch.object(bake.C, 'env', return_value=environment), \
                 patch.object(bake.C, 'ensure_dirs'), \
                 patch.object(bake, 'ensure_skeleton', return_value={}), \
                 patch.object(bake, 'ensure_vanilla', return_value={}), \
                 patch.object(bake.EQ, 'parse_skins', return_value={'man': skin}), \
                 patch.object(bake.EQ, 'load_module_items', return_value=[]), \
                 patch.object(bake.MB, 'exportmod', side_effect=export), \
                 patch.object(bake.MB, 'material_texture_guids', return_value={'0': 'dress-guid'}), \
                 patch.object(bake.CLOTH, 'read_settings', side_effect=cloth), \
                 patch.object(bake.GEO, 'parse_gdmb', side_effect=parse):
                result = bake.bake_mod(str(mod), skip_anims=True)
                self.assertEqual(result['sourcePacks'], ['a.tpac', 'b.tpac', 'c.tpac'])
                self.assertEqual(len(result['meshes']), 2)
                self.assertEqual(result['stats'], dict(vertices=6, triangles=2))
                self.assertEqual(result['materials']['silk']['textures']['albedo'], 'dress')
                for index, mesh in enumerate(result['meshes']):
                    subs = read_meshpack(environment.data_path(mesh['file']))
                    self.assertEqual(subs[0]['material'], 'silk')
                    self.assertEqual(float(subs[0]['pos'][0, 2]), index + 1)
                    self.assertEqual(subs[0]['cloth']['enabled'], bool(index))
                for name, color in [('dress', (255, 0, 0, 255)), ('boots', (0, 255, 0, 255))]:
                    with Image.open(environment.data_path(result['textures'][name]['file'])) as image:
                        self.assertEqual(image.getpixel((0, 0)), color)
                self.assertEqual(upgrade_cache(environment.data_dir / 'mods' / mod.name), (0, []))
                self.assertFalse((environment.data_dir / 'mods' / mod.name / '_export').exists())
                self.assertEqual(len({str(out) for _, out, _ in exports}), 3)
                self.assertTrue(all(kwargs == {'all_mips': False} for _, _, kwargs in exports))

    def test_duplicate_names_choose_later_package_and_matching_cloth(self):
        with tempfile.TemporaryDirectory() as tmp:
            packs = [Path('a.tpac'), Path('b.tpac')]
            with patch.object(bake.MB, 'exportmod', side_effect=[
                    dict(meshes=[dict(name='same', file='geo/a.gdmb')]),
                    dict(meshes=[dict(name='same', file='geo/b.gdmb')])]), \
                 patch.object(bake.CLOTH, 'read_settings', side_effect=[
                    ({'same': ['first']}, []), ({'same': ['second']}, [])]), \
                 patch.object(bake, '_log') as log:
                result = bake.export_packages(packs, Path(tmp))
                self.assertEqual(result['meshes'], [dict(name='same', file='pack1/geo/b.gdmb',
                    sourcePack='b.tpac', clothSettings=['second'])])
                self.assertIn('a.tpac → b.tpac', log.call_args.args[0])

    def test_single_package_keeps_existing_texture_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(bake.MB, 'exportmod', return_value=dict(
                    materials=[dict(name='skin', textures={'0': 'skin_d', '2': ''})])), \
                 patch.object(bake.CLOTH, 'read_settings', return_value=({}, [])), \
                 patch.object(bake.MB, 'material_texture_guids') as guids:
                result = bake.export_packages([Path('only.tpac')], Path(tmp))
                self.assertEqual(result['materials'][0]['textures'], {'0': 'skin_d', '2': ''})
                guids.assert_not_called()


if __name__ == '__main__':
    unittest.main()
