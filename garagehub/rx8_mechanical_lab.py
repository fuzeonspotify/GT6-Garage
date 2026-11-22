"""Reviewed RX8 rotor/caliper routes for an isolated native geometry lab.

Accepts any prepared source with explicit brake roles and tyre-derived corners.
It does not choose a source by name/hash, change native suspension mounts,
replace physics, or make a complete/importable car. Original body/stream assets
remain donors; selected brake calls receive separately appended direct meshes.
"""
from __future__ import annotations

import copy
import numpy as np

from .errors import GarageError
from .native import MDL3
from .native_editor import Editor, need
from .donor_profiles import RX8_ASSETS
from .recognition import CORNERS
from .assembly_fit import assembly_fit_report
from .rx8_body_materials import RX8BodyMaterials
from .rx8_wheel_adapter import REFERENCE_MOUNTS, REFERENCE_EVIDENCE
from .wheel_mounts import rigid_axle_mount
from .mechanical_native import append_dynamic_components, ROLES
from .util import sha256


EVIDENCE = {
    'original_native_probe_sha256': 'b03121259839c45238436a8c0d814abee3e4eccb273a6774940cc2117bd65c7c',
    'rotor_report': 'reports/vertex_encoding/native_rotor_geometry_spin_evidence_v001.json',
    'rotor_report_sha256': 'aa102131ad881228e1805bcbdbcf87d4883151567062346880c75826b570524c',
    'garage_capture_sha256': 'efe1431203fd772aa8b530ea43f07463ecde3a3d27e24d32255fef202dbf68e8',
    'free_run_capture_sha256': '8b995bf55b28e246ca8fdb7b920dd6e9cc3f23551b1ac713ae7295b801f0ec72',
    'scope': 'Original native paths/programs and two historical position-transform captures; no current candidate runtime acceptance.',
}

PROGRAM_HASHES = {
    'hq': {'0': 'e54016171a9a6933544a9b77ff2d9aa3dd3be51cc56fb55db150092ad44b8397',
           '5': 'f82545a2b46c8baee242cf04c245e6b53c3b4c6e050de039a755e37ec2f25e6f'},
    'race': {'0': 'd3d3e70b09d78d8cf337e8943f2ed0c9bd3cd98a0a2a5993138f2eb27e456be1',
             '5': 'f82545a2b46c8baee242cf04c245e6b53c3b4c6e050de039a755e37ec2f25e6f'},
}

# W^-1 D from the independently identified historical garage wheel and rotor
# position matrices. These are observed frame relationships, not tyre sizes.
# Small rear rotational terms and rounding residuals remain visible here; they
# are not silently replaced with an exact identity or nominal DB dimensions.
WHEEL_TO_ROTOR_REFERENCE = {
    'FL': [[.9999999657890974, -4.336808689942018e-19, 3.74313763328547e-17, .07879377937925325],
           [2.4955183631353295e-09, 1., -2.058885631868988e-19, 1.2231118162731036e-07],
           [-4.882525740565016e-08, -4.360069008767009e-19, 1., -9.553165678521935e-07]],
    'FR': [[1.0000000488234027, -3.038228591021458e-09, 6.870661946526837e-19, .07879438842318254],
           [4.2607133446392886e-10, 1.000000059580394, 7.614528410795641e-19, 5.708488087785568e-08],
           [-3.4187821555321484e-08, -2.7844878710718648e-09, 1., 9.528170592520979e-07]],
    'RL': [[.999999770269021, 1.4304943561655598e-06, 3.601019296679849e-06, .07917349997146994],
           [-1.4323176003652738e-06, .9999999026595843, -.0002741175093025259, 1.3869880416406133e-07],
           [-3.5476992717445466e-06, .00027411748642694044, .999999845582153, -1.4269508121778074e-06]],
    'RR': [[.9999999144659474, 1.4323651853168778e-06, -3.4406228393812835e-06, .07917571073156005],
           [-1.436033389356009e-06, .9999999623327502, .0002741289156262805, 2.4560534633844e-07],
           [3.4076388790294347e-06, -.0002741172279614533, .999999895076086, 2.021872234303501e-06]],
}


def rotor_to_caliper_rest_basis(corner):
    """Observed C^-1 D rest basis: right calipers are not rim-oriented."""
    need(corner in CORNERS, 'Unknown mechanical corner.')
    return np.eye(3) if corner[1] == 'L' else np.diag([-1., 1., -1.])


def reviewed_rx8_routes(donor, lod):
    need(lod in PROGRAM_HASHES, 'Unknown RX8 mechanical LOD.')
    need(sha256(donor) == RX8_ASSETS[lod+'/body'], 'Mechanical lab needs the exact original RX8 body asset.')
    routes = []
    # In each 42-byte corner section the caliper call is at +20; its rotor
    # call is at +35, inside the separately nested 0e 06 (15+corner) frame.
    starts = {0: 204 if lod == 'hq' else 215, 5: 207}
    for ci, corner in enumerate(CORNERS):
        for role, delta in (('brake_caliper', 0), ('brake_rotor', 1)):
            routes.append({'role': role, 'corner': corner, 'original_shape': 40+2*ci+delta,
                           'calls': [{'model': mid, 'offset': starts[mid]+42*ci+20+15*delta,
                                      'context': 1+2*ci+delta+(17 if mid == 5 else 0)} for mid in (0, 5)]})
    return {'asset_sha256': sha256(donor), 'programs': copy.deepcopy(PROGRAM_HASHES[lod]),
            'components': routes, 'evidence': copy.deepcopy(EVIDENCE)}


def historical_frame_contract(inspection):
    """Use native runtime routes, with only source-centred rigid local geometry.

The caliper frame is the observed pre-spin frame in two captured poses. The
engine's complete steering/suspension construction is not reverse-engineered.
The garage rest relation fixes right/left local bases without inverting a full
captured camber or steering pose into the source vertices. Reference matrices
are used for axial placement and residual reporting only.
"""
    frames = {}
    for corner in CORNERS:
        wheel = np.eye(4)
        wheel[:3] = REFERENCE_MOUNTS[corner]
        relative = np.eye(4)
        relative[:3] = WHEEL_TO_ROTOR_REFERENCE[corner]
        rotor = wheel@relative
        rest = rotor_to_caliper_rest_basis(corner)
        caliper = rotor.copy()
        caliper[:3, :3] = rotor[:3, :3]@rest.T
        for role, mount in (('brake_rotor', rotor[:3]), ('brake_caliper', caliper[:3])):
            center, basis, offset, fit = rigid_axle_mount(inspection, corner, mount)
            if role == 'brake_caliper':
                basis = rest@basis
            evidence = copy.deepcopy(EVIDENCE)
            evidence['wheel_body_reference'] = copy.deepcopy(REFERENCE_EVIDENCE)
            evidence['frame_construction'] = 'Historical body-relative wheel reference composed with captured wheel-to-rotor; caliper uses the observed rotor-to-caliper rest basis.'
            frames[role+':'+corner] = {
                'units': 'metres', 'pivot_kind': 'tyre_hub_center', 'motion_intent': ROLES[role],
                'source_center_metres': center.tolist(), 'source_to_local_rotation': basis.tolist(),
                'axial_offset_metres': float(offset[0]), 'radial_offset_metres': [0., 0.],
                'reference_local_to_body': mount.tolist(),
                'source_center_in_body_frame': fit['source_center_in_native_body_frame'],
                'fitted_center_in_reference_pose': fit['fitted_center_in_reference_body_pose'],
                'source_body_minus_fitted_center_metres': fit['source_body_minus_fitted_center_metres'],
                'rotor_to_caliper_rest_basis': rest.tolist(),
                'neutral_basis_scope': 'The observed garage rest basis is the authoring reference. Source phase/orientation and every runtime state still need visual verification.',
                'captured_camber_or_steering_baked_into_source': False,
                'native_mount_or_physics_changed': False, 'evidence': evidence,
            }
    return frames


def _reviewed_parts(scene, inspection):
    try:
        records = {row['id']: row for row in inspection['parts']}
        need(len(records) == len(inspection['parts']) and set(records) == {part.id for part in scene.parts},
             'Mechanical inspection does not match the current prepared source inventory.')
        centers = np.array([inspection['wheel_centers'][corner] for corner in CORNERS], float)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise GarageError('Review the current source inventory and all four tyre/hub centres.', 'mechanical_native') from exc
    need(centers.shape == (4, 3) and np.isfinite(centers).all(), 'Four finite tyre/hub centres are required.')
    need(not any(row.get('role_review_required') for row in records.values()),
         'Resolve all proposed mechanical roles before this lab export, including mixed groups still assigned to body.')
    assembly_fit_report(inspection, {'kind': 'explicit_centres', 'forward': inspection.get('forward'),
                                    'wheel_centers_metres': inspection['wheel_centers']})
    for ci, corner in enumerate(CORNERS):
        tyres = [part for part in scene.parts if records[part.id]['role'] == 'tyre' and records[part.id]['corner'] == corner]
        need(bool(tyres), 'Mechanical export requires an assigned source tyre group at every corner.')
        # Match recognition's actual bounds/mean arithmetic. GLB attributes
        # are normally float32; promoting the extrema before averaging would
        # disagree with a freshly inferred centre by one float32 rounding step.
        bounds = np.array([np.min([p.positions.min(0) for p in tyres], axis=0),
                           np.max([p.positions.max(0) for p in tyres], axis=0)])
        measured = bounds.mean(0)
        numeric = 256*np.finfo(float).eps*max(1., float(np.max(np.abs(bounds))))
        need(np.isfinite(measured).all() and np.allclose(measured, centers[ci], rtol=0, atol=numeric),
             'Saved mechanical hub centres no longer match the current source tyre geometry.')
    selected = []
    for part in scene.parts:
        row = records[part.id]
        if row['role'] not in ROLES:
            continue
        need(not row.get('role_review_required'), 'Confirm each proposed brake role before native mechanical export.')
        need(row.get('corner') in CORNERS, 'Partition or assign one corner to each source brake component.')
        selected.append((part, row))
    need(bool(selected), 'Assign at least one explicit rotor or caliper group before mechanical export.')
    return selected


def build_mechanical_lod(scene, inspection, donor, lod):
    """Return (body_bytes, receipt); no source, donor or filesystem writes.

The caller must retain the original body_s unchanged. This lab keeps the donor
body visible and replaces only selected brake draw operands; it is not a full
source body assembly and is not suitable for installation.
"""
    routes = reviewed_rx8_routes(donor, lod)
    selected = _reviewed_parts(scene, inspection)
    frames = historical_frame_contract(inspection)
    editor = Editor(donor)
    material_builder = RX8BodyMaterials(scene, editor, lod)
    sourceparts = []
    for part, row in selected:
        material = scene.materials[part.material]
        need(material.get('alpha_mode', 'OPAQUE') == 'OPAQUE' and material.get('transmission', 0.) <= .1,
             'Transparent or cutout mechanical surfaces need a separately reviewed native material recipe.')
        recipe = material_builder.recipe(part.material, 'body')
        item = {'id': part.id, 'role': row['role'], 'corner': row['corner'],
                'positions': part.positions, 'normals': part.normals, 'uv': part.uv, 'faces': part.indices,
                'native_material': recipe['native_material'], 'native_fvf': recipe['fvf'],
                'transparent': recipe['transparent']}
        if recipe['constant_uv'] is not None:
            item['native_uv'] = np.tile(recipe['constant_uv'], (len(part.positions), 1))
        sourceparts.append(item)
    receipt = append_dynamic_components(editor, sourceparts, routes, frames)
    result, native_receipt = editor.finish()
    original, parsed = MDL3(donor), MDL3(result)
    validation = parsed.validate()
    need(result[parsed.cut:parsed.cut+len(donor)-original.cut] == donor[original.cut:],
         'Mechanical lab changed the original GPU payload.')
    for sid in range(original.u16(0x14)):
        need(original.span(original.u32(0x38)+48*sid, 48) == parsed.span(parsed.u32(0x38)+48*sid, 48),
             'Mechanical lab changed an original packed shape descriptor.')
    changed_models = {row['model'] for row in receipt['programs']}
    for row in receipt['programs']:
        before = original.u32(0x30)+48*row['model']
        after = parsed.u32(0x30)+48*row['model']
        old_program = original.span(original.u32(before+24), original.u32(before+28))
        new_program = parsed.span(parsed.u32(after+24), parsed.u32(after+28))
        allowed = {operand['offset'] for operand in row['shape_operands']}
        need(len(old_program) == len(new_program) and sha256(new_program) == row['after_sha256'] and
             all(a == b or i in allowed for i, (a, b) in enumerate(zip(old_program, new_program))),
             'Serialized mechanical program changed state, contexts or branch layout.')
        need(original.span(before+32, 16) == parsed.span(after+32, 16),
             'Serialized mechanical model changed native VM entry state.')
    for mid in range(original.u16(0x10)):
        if mid in changed_models:
            continue
        before, after = original.u32(0x30)+48*mid, parsed.u32(0x30)+48*mid
        need(original.span(original.u32(before+24), original.u32(before+28)) ==
             parsed.span(parsed.u32(after+24), parsed.u32(after+28)), 'An unrelated native model program changed.')
    for row in receipt['shapes']:
        at = parsed.u32(0x38)+48*row['shape']
        need(parsed.s16(at+2) >= 0 and parsed.u32(at+44) == 0, 'Mechanical output unexpectedly uses a packed binding.')
        need(sha256(parsed.span(parsed.u32(at+12), row['vertices']*24)) == row['vertex_sha256'] and
             sha256(parsed.span(parsed.u32(at+24), row['triangles']*6)) == row['index_sha256'],
             'Serialized mechanical source geometry changed.')
    need(original.u32(0x60) == parsed.u32(0x60) and
         original.span(original.u32(0x64), original.u32(0x60)) ==
         parsed.span(parsed.u32(0x64), parsed.u32(0x60)), 'Native VM bytecode changed.')
    receipt.update(lod=lod, source_sha256=scene.digest, native_sha256=sha256(result),
                   native_validation=validation, native_resources=native_receipt,
                   material_recipes=material_builder.report,
                   original_gpu_payload_preserved=True, original_body_stream_required_unchanged=True,
                   full_body_replaced=False, full_car_import_accepted=False,
                   limitations=[
                       'The donor body remains visible. This lab routes only explicitly assigned brake geometry.',
                       'Original packed resources are retained. Appended direct-shape loading requires runtime verification.',
                       'Source dimensions and authored off-axis geometry remain intact; body/axle residuals remain disclosed.',
                       'The native caliper pre-spin and rotor spin relation is supported by two historical captures, not all steering, compression, braking or LOD states.',
                       'Tyre fit, source phase/orientation, material appearance and full assembly clearances remain unverified.',
                   ])
    return result, receipt
