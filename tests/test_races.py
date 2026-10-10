import tempfile
import unittest
from pathlib import Path
import numpy as np

from baker import races, skeleton


class RaceTests(unittest.TestCase):
    def test_default_preview_avoids_empty_animation_definitions(self):
        from unittest.mock import patch
        empty = dict(key='jump', tEnd=0)
        idle = dict(key='inventory_idle', tEnd=30)
        run = dict(key='run_forward_unarmed', tEnd=25)
        with patch('baker.actions.default_selection', return_value=[empty, idle, run]):
            selected = races.default_anims(dict(items=[empty, idle, run]), 12)
        self.assertEqual([entry['key'] for entry in selected], ['inventory_idle', 'run_forward_unarmed'])

    def test_registered_skin_file_and_adult_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory)
            data = module / 'ModuleData'
            data.mkdir()
            (data / 'project.mbproj').write_text('<base><file type="skin" name="ModuleData/custom.xml"/></base>')
            (data / 'custom.xml').write_text('''<skins><race id="test">
                <skin name="female" gender="1" mesh_maturity_type="adult" skeleton="custom" body_meta_mesh="body" min_scale="1.1"/>
                <skin name="child" gender="0" mesh_maturity_type="child"/>
                </race></skins>''')
            skins = races.adult_skins(module)
            self.assertEqual(list(skins), ['female'])
            self.assertEqual(races.select_skin(skins, 'woman')['name'], 'female')
            self.assertEqual(skins['female']['skeleton'], 'custom')
            (data / 'project.mbproj').write_text('<base><file type="skin" name="../outside.xml"/></base>')
            with self.assertRaises(ValueError):
                races.skin_files(module)

    def test_engine_frame_padding_is_not_a_homogeneous_coordinate(self):
        matrix = np.eye(4)
        matrix[:3, 3] = [1, 2, 3]
        matrix[3] = [1e-42, 0, 1e-43, 0]
        rig = skeleton.parse_skeljson(dict(bones=[dict(i=0, name='pelvis', parent=-1,
                                                     rest=matrix.T.ravel().tolist())]))
        np.testing.assert_allclose(rig['restWorld'][0] @ np.linalg.inv(rig['restWorld'][0]), np.eye(4))
        np.testing.assert_allclose(rig['restWorld'][0, :3, 3], [1, 2, 3])

    def test_equal_bone_count_does_not_imply_compatible_rig(self):
        native = dict(name='native', boneCount=28, names=[f'bip01_b{i}' for i in range(28)], parent=[-1]+list(range(27)))
        custom = dict(native, name='custom', names=[f'b{i}' for i in range(28)])
        races.validate_rig(custom, native)
        custom['names'][4], custom['names'][5] = custom['names'][5], custom['names'][4]
        with self.assertRaises(ValueError):
            races.validate_rig(custom, native)


if __name__ == '__main__':
    unittest.main()
