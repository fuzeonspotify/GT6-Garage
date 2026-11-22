"""Synthetic semantic-priority cases; no original model or game data required."""
from __future__ import annotations

import copy
import unittest

import numpy as np

from .errors import GarageError
from .glb import Part, Scene, unit_normals
from .recognition import CORNERS, infer


CENTRES = {corner: np.array([.8 if corner[1] == 'L' else -.8,
                            .34, 1.3 if corner[0] == 'F' else -1.3])
           for corner in CORNERS}


def circular_part(pid, centre, radius=.34, width=.22, segments=32, material=0, extras=None):
    angles=np.arange(segments)*2*np.pi/segments
    rings=[np.column_stack((np.full(segments,x),radius*np.sin(angles),radius*np.cos(angles)))
           for x in (-width/2,width/2)]
    points=np.vstack((*rings,[[-width/2,0,0],[width/2,0,0]]))+centre
    faces=[]
    for i in range(segments):
        j=(i+1)%segments
        faces.extend(((i,j,segments+i),(j,segments+j,segments+i),
                      (2*segments,j,i),(2*segments+1,segments+i,segments+j)))
    faces=np.asarray(faces,dtype=np.int64)
    return Part(pid,'Component '+pid,len(pid),0,material,points,unit_normals(points,faces),
                np.zeros((len(points),2)),faces,dict(extras or {}))


def scene_case(segments=32,tyre_color=.04):
    parts=[circular_part('disc_'+corner,CENTRES[corner],segments=segments) for corner in CORNERS]
    points=np.array([[-1,.2,-2.1],[1,.2,-2.1],[1,1.4,-2.1],[-1,1.4,-2.1],
                     [-1,.2,2.1],[1,.2,2.1],[1,1.4,2.1],[-1,1.4,2.1]])
    faces=np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                    [2,3,7],[2,7,6],[0,4,7],[0,7,3],[1,2,6],[1,6,5]],dtype=np.int64)
    parts.append(Part('shell','Component shell',4,0,1,points,unit_normals(points,faces),
                      np.zeros((8,2)),faces,{'gt6_role':'body'}))
    materials=[{'id':i,'name':'Material.'+str(i),'color':[color]*3+[1],
                'alpha_mode':'OPAQUE','transmission':0.,'metallic':0.,'roughness':.8,
                'base_texture':None,'double_sided':False,'emissive':[0,0,0],'normal_map':False}
               for i,color in enumerate((tyre_color,.65))]
    return Scene(parts,materials,{},b'unchanged source','0'*64,[])


def by_id(report):
    return {row['id']:row for row in report['parts']}


class RecognitionTests(unittest.TestCase):
    def test_mirrored_light_clusters_require_a_unique_pair_and_respect_tags(self):
        from .template_shell_independent_tests import wheel_free_glb
        from .glb import GLB
        scene=GLB(wheel_free_glb()).scene()
        pair=scene.parts[1:3]
        for part in pair:
            part.extras={};part.name='LampCluster'
        self.assertTrue(all(by_id(infer(scene))[part.id].get('paired_lens') for part in pair))
        pair[1].extras={'gt6_role':'glass'}
        self.assertFalse(any(row.get('paired_lens') for row in infer(scene)['parts']))
        pair[1].extras={};pair[1].name='PoliceLightBar'
        self.assertFalse(any(row.get('paired_lens') for row in infer(scene)['parts']))

    def test_dcc_part_labels_and_stationary_hardware(self):
        from .recognition import component_name_role
        cases=[('HeadLamp_L.002','Lens','headlight'),('Lamp_Rear_R','Lens','taillight'),
               ('CHMSL','Red','brakelight'),('wheelArch_L','tyre_rubber','body'),
               ('HeadlightBezel','Chrome','body'),('LightBar','headlight','body'),
               ('FrontIndicator','glass','body'),('SteeringWheel','rubber','interior'),
               ('rim_FL','Alloy','rim'),('wiper_L.001','rubber','body')]
        for name, material, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(component_name_role(name,material),expected)
    def test_authored_non_tyres_never_seed_axle_centres(self):
        for role in ('ignore','body','rim','valve','paint','interior'):
            with self.subTest(role=role):
                scene=scene_case()
                for i,part in enumerate(scene.parts[:4]):
                    part.extras={'gt6_role':role}
                    # Vary the envelopes while retaining plausible wheel shapes.
                    part.positions=CENTRES[CORNERS[i]]+(part.positions-CENTRES[CORNERS[i]])*(.9+i*.05)
                report=infer(scene,forward='+Z')
                rows=by_id(report)
                self.assertEqual(report['wheel_centers'],{})
                self.assertEqual(report['wheel_radii'],{})
                self.assertIsNone(report['wheelbase'])
                for corner in CORNERS:
                    row=rows['disc_'+corner]
                    self.assertEqual(row['role'],role)
                    self.assertIsNone(row['corner'])
                    self.assertEqual(row['reason'],'Blender/GLB role tag')

    def test_untagged_four_tyres_remain_geometrically_recognized(self):
        report=infer(scene_case(),forward='+Z');rows=by_id(report)
        self.assertEqual(set(report['wheel_centers']),set(CORNERS))
        for corner in CORNERS:
            self.assertEqual(rows['disc_'+corner]['role'],'tyre')
            self.assertEqual(rows['disc_'+corner]['corner'],corner)
            np.testing.assert_allclose(report['wheel_centers'][corner],CENTRES[corner],atol=1e-15)
            self.assertAlmostEqual(report['wheel_radii'][corner],.34)
        self.assertAlmostEqual(report['wheelbase'],2.6)
        self.assertAlmostEqual(report['track'],1.6)

    def test_authored_roles_survive_concentric_rim_and_valve_inference(self):
        for role in ('body','paint','interior','ignore','rim','valve'):
            with self.subTest(role=role):
                scene=scene_case()
                # This bright circular part qualifies as a geometric rim but
                # does not enter the dark-tyre candidate pass.
                scene.parts.append(circular_part('detail',CENTRES['FL'],radius=.23,width=.12,
                                                  material=1,extras={'gt6_role':role}))
                report=infer(scene,forward='+Z');row=by_id(report)['detail']
                self.assertEqual(row['role'],role)
                self.assertEqual(row['corner'],'FL' if role in ('rim','valve') else None)
                np.testing.assert_allclose(report['wheel_centers']['FL'],CENTRES['FL'],atol=1e-15)

    def test_authored_rim_cannot_be_reclassified_as_a_small_valve(self):
        scene=scene_case()
        centre=CENTRES['FL']+np.array([0,.2,0])
        scene.parts.append(circular_part('small_detail',centre,radius=.04,width=.035,
                                         material=1,extras={'gt6_role':'rim'}))
        row=by_id(infer(scene,forward='+Z'))['small_detail']
        self.assertEqual((row['role'],row['corner']),('rim','FL'))

    def test_saved_non_tyres_do_not_seed_concentric_predictions(self):
        for role in ('ignore','body','rim'):
            with self.subTest(role=role):
                scene=scene_case()
                for corner,part in zip(CORNERS,scene.parts[:4]):
                    part.extras={'gt6_role':'tyre','gt6_corner':corner}
                scene.parts.append(circular_part('unassigned_detail',CENTRES['FL'],radius=.23,
                                                  width=.12,material=1))
                overrides={'disc_'+corner:{'role':role,'corner':corner} for corner in CORNERS}
                report=infer(scene,forward='+Z',overrides=overrides);rows=by_id(report)
                self.assertEqual(report['wheel_centers'],{})
                self.assertEqual(rows['unassigned_detail']['role'],'body')
                self.assertIsNone(rows['unassigned_detail']['corner'])
                for corner in CORNERS:
                    self.assertEqual(rows['disc_'+corner]['role'],role)
                    self.assertEqual(rows['disc_'+corner]['reason'],'Your saved assignment')
                    self.assertEqual(rows['disc_'+corner]['corner'],corner if role=='rim' else None)

    def test_saved_role_and_corner_override_authored_tags(self):
        scene=scene_case(segments=8,tyre_color=.65)
        remap=dict(zip(CORNERS,reversed(CORNERS)))
        for corner,part in zip(CORNERS,scene.parts[:4]):
            part.extras={'gt6_role':'rim','gt6_corner':corner}
        overrides={'disc_'+corner:{'role':'tyre','corner':remap[corner]} for corner in CORNERS}
        report=infer(scene,forward='+Z',overrides=overrides);rows=by_id(report)
        for corner in CORNERS:
            row=rows['disc_'+corner]
            self.assertEqual((row['role'],row['corner']),('tyre',remap[corner]))
            self.assertEqual(row['reason'],'Your saved assignment')
            np.testing.assert_allclose(report['wheel_centers'][remap[corner]],CENTRES[corner],atol=1e-15)

    def test_authored_tyre_corners_are_not_relabelled_by_quadrants(self):
        scene=scene_case();remap=dict(zip(CORNERS,reversed(CORNERS)))
        for corner,part in zip(CORNERS,scene.parts[:4]):
            part.extras={'gt6_role':'tyre','gt6_corner':remap[corner]}
        report=infer(scene,forward='+Z');rows=by_id(report)
        for corner in CORNERS:
            self.assertEqual(rows['disc_'+corner]['corner'],remap[corner])
            np.testing.assert_allclose(report['wheel_centers'][remap[corner]],CENTRES[corner],atol=1e-15)

    def test_authored_tyres_do_not_require_heuristic_mesh_density_or_dark_colour(self):
        scene=scene_case(segments=8,tyre_color=.65)
        for corner,part in zip(CORNERS,scene.parts[:4]):
            part.extras={'gt6_role':'tyre','gt6_corner':corner}
        report=infer(scene,forward='+Z');rows=by_id(report)
        self.assertEqual(set(report['wheel_centers']),set(CORNERS))
        for corner in CORNERS:
            row=rows['disc_'+corner]
            self.assertEqual((row['role'],row['corner']),('tyre',corner))
            self.assertEqual(row['reason'],'Blender/GLB role tag')
            self.assertEqual(row['confidence'],.99)

    def test_authored_tyre_without_corner_can_receive_spatial_proposal(self):
        scene=scene_case(segments=8,tyre_color=.65)
        for part in scene.parts[:4]:part.extras={'gt6_role':'tyre'}
        rows=by_id(infer(scene,forward='+Z'))
        for corner in CORNERS:
            row=rows['disc_'+corner]
            self.assertEqual((row['role'],row['corner']),('tyre',corner))
            self.assertIn('corner proposed from position',row['reason'])

    def test_saved_unassigned_corner_is_not_inferred_or_inherited(self):
        scene=scene_case()
        for corner,part in zip(CORNERS,scene.parts[:4]):
            part.extras={'gt6_role':'tyre','gt6_corner':corner}
        scene.parts.append(circular_part('rim_detail',CENTRES['FR'],radius=.23,width=.12,
                                          material=1,extras={'gt6_role':'rim','gt6_corner':'FR'}))
        overrides={'disc_FL':{'role':'tyre','corner':None},'rim_detail':{'role':'rim','corner':None}}
        report=infer(scene,forward='+Z',overrides=overrides);rows=by_id(report)
        self.assertIsNone(rows['disc_FL']['corner'])
        self.assertNotIn('FL',report['wheel_centers'])
        self.assertIsNone(rows['rim_detail']['corner'])
        self.assertEqual(rows['rim_detail']['role'],'rim')

    def test_nonwheel_authored_corner_does_not_assign_an_axle(self):
        scene=scene_case()
        for corner,part in zip(CORNERS,scene.parts[:4]):
            part.extras={'gt6_role':'body','gt6_corner':corner}
        report=infer(scene,forward='+Z')
        self.assertEqual(report['wheel_centers'],{})
        self.assertTrue(all(row['corner'] is None for row in report['parts']))

    def test_split_authored_tyre_group_has_one_consistent_measured_centre(self):
        scene=scene_case()
        original=scene.parts.pop(0)
        for i,offset in enumerate((-.08,.08)):
            scene.parts.append(circular_part('split_'+str(i),CENTRES['FL']+[offset,0,0],
                                              width=.06,segments=8,
                                              extras={'gt6_role':'tyre','gt6_corner':'FL'}))
        report=infer(scene,forward='+Z')
        np.testing.assert_allclose(report['wheel_centers']['FL'],original.center,atol=1e-15)
        self.assertAlmostEqual(report['wheel_radii']['FL'],.34)
        self.assertEqual({row['id'] for row in report['parts'] if row['role']=='tyre' and row['corner']=='FL'},
                         {'split_0','split_1'})

    def test_invalid_saved_role_or_corner_is_rejected(self):
        for value in ({'role':'unknown'},{'role':'tyre','corner':'front'},None):
            with self.subTest(value=value),self.assertRaises(GarageError):
                infer(scene_case(),forward='+Z',overrides={'disc_FL':value})

    def test_invalid_authored_role_does_not_disable_ordinary_geometry_recognition(self):
        scene=scene_case()
        for part in scene.parts[:4]:part.extras={'gt6_role':'unknown','gt6_corner':'rear'}
        report=infer(scene,forward='+Z')
        self.assertEqual(set(report['wheel_centers']),set(CORNERS))
        self.assertTrue(all(by_id(report)['disc_'+corner]['role']=='tyre' for corner in CORNERS))

    def test_inference_preserves_source_geometry_metadata_and_saved_assignments(self):
        scene=scene_case();scene.parts[0].extras={'gt6_role':'tyre','gt6_corner':'FL'}
        overrides={'disc_FR':{'role':'tyre','corner':'FR'}}
        before=copy.deepcopy(scene);saved=copy.deepcopy(overrides)
        infer(scene,forward='+Z',overrides=overrides)
        self.assertEqual(overrides,saved)
        self.assertEqual(scene.materials,before.materials)
        self.assertEqual(scene.document,before.document)
        self.assertEqual((scene.raw,scene.digest,scene.warnings),(before.raw,before.digest,before.warnings))
        for part,old in zip(scene.parts,before.parts):
            self.assertEqual((part.id,part.name,part.extras,part.warnings),(old.id,old.name,old.extras,old.warnings))
            for name in ('positions','normals','indices','uv'):
                np.testing.assert_array_equal(getattr(part,name),getattr(old,name))


if __name__=='__main__':
    unittest.main()
