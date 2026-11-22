"""Ownership regressions; owned native assets are optional external fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from . import native_wheel_contract as contract
from .errors import GarageError
from .native import MDL3, corners
from .stock_race_wheel import STOCK_SHA256, restore_stock_race_wheel
from .util import sha256


def packed_fixture():
    """Small independently authored shape, never a copy of game bytes."""
    data = bytearray(0x1000)
    data[:4] = b'MDL3'
    struct.pack_into('>I', data, 4, len(data))
    struct.pack_into('>H', data, 12, 14)
    struct.pack_into('>I', data, 0x6c, len(data))
    struct.pack_into('>I', data, 0xd0, 0x200)
    data[0x200:0x204] = b'PMSH'
    struct.pack_into('>4H', data, 0x210, 1, 0, 1, 1)
    struct.pack_into('>4I', data, 0x218, 0x300, 0, 0x450, 0x400)
    struct.pack_into('>hhHBB', data, 0x300, 0, 0, 0xffff, 1, 1)
    struct.pack_into('>2I', data, 0x310, 0x800, 0x900)
    data[0x400:0x404] = bytes((1, 3, 16, 12))
    struct.pack_into('>2I', data, 0x408, 0x420, 0x440)
    fields = ((0, 0, 3, 2), (1, 0, 1, 6), (2, 4, 4, 4), (3, 8, 2, 3))
    for i, (field, name) in enumerate(zip(fields, ('position', 'normal', 'colorSet1', 'map12'))):
        data[0x420 + 4 * i:0x424 + 4 * i] = bytes(field)
        name_at = 0xe00 + 24 * i
        value = name.encode() + b'\0'
        data[name_at:name_at + len(value)] = value
        struct.pack_into('>I', data, 0x440 + 4 * i, name_at)
    struct.pack_into('>2I', data, 0x450, 1, 0x700)
    struct.pack_into('>Hhh', data, 0x500, 0x1001, -1, 0)
    struct.pack_into('>2I', data, 0x528, 0xa00, 0x600)
    # +0x30 is opaque, +0x34 is the mesh index. Do not use the older overview.
    struct.pack_into('>2I', data, 0x630, 0xdeadbeef, 0)
    struct.pack_into('>I8B2H', data, 0x700, 48, 16, 16, 16, 0, 16, 32, 48, 48, 0, 0xffff)
    struct.pack_into('>8f', data, 0x710, .1, .2, .3, float('-inf'), -.3, -.6, -.9, float('inf'))
    struct.pack_into('>8I', data, 0x800, 0xb00, 0, 0, 0xc00, 0, 0xd00, 0xd80, 0)
    struct.pack_into('>7H2B', data, 0x820, 0, 3, 0, 3, 0, 1, 1, 11, 0)
    box = corners(((-.1, -.2, -.3), (.4, .5, .6)))
    data[0xa00:0xa60] = box
    words = struct.unpack('>24I', box)
    transposed = [words[(block * 4 + vertex) * 3 + axis]
                  for block in range(2) for axis in range(3) for vertex in range(4)]
    data[0x900:0x960] = struct.pack('>24I', *transposed)
    return data


def race_model_fixture():
    data = bytearray(0x1600)
    data[:4] = b'MDL3'
    struct.pack_into('>I', data, 4, len(data))
    struct.pack_into('>H', data, 12, 14)
    struct.pack_into('>I', data, 0x6c, len(data))
    struct.pack_into('>I', data, 0x30, 0x300)
    shapes = []
    for sid in range(16):
        shader = 'Shadow' if sid >= 12 else ('WheelBlurShadow', 'Flex', 'WheelBlur')[sid % 3]
        shapes.append({'role': contract._role('race', sid, shader),
                       'geometry': {'storage': 'ordinary_inline' if sid >= 12 or sid % 3 == 1
                                    else 'packed_inline'}})
    for mid in range(8):
        at, bp, cp = 0x300 + 48 * mid, 0x600 + 96 * mid, 0xa00 + 32 * mid
        struct.pack_into('>4f', data, at, 1, 0, 0, 0)
        struct.pack_into('>H', data, at + 18, 8)
        data[bp:bp + 96] = corners(((-1, -1, -1), (1, 1, 1)))
        if mid < 4:
            # Original branch order: common shadow, switch/default skip, rim, skip, blur, end.
            sid, context = mid * 3, mid * 2
            program = bytes((59, sid, context, 5, 0, 0, 2, 0, 4, 0, 7,
                             9, 8, 59, sid + 1, context + 1, 9, 3,
                             59, sid + 2, context + 1, 0))
        else:
            program = bytes((59, mid + 8, mid + 4, 0))
        data[cp:cp + len(program)] = program
        struct.pack_into('>3I', data, at + 20, bp, cp, len(program))
    return data, shapes


class NativeWheelContractTests(unittest.TestCase):
    def test_unknown_assets_are_rejected_before_parsing(self):
        with patch.object(contract, '_asset') as parse:
            with self.assertRaises(GarageError) as caught:
                contract.audit_native_wheel_contract(b'unknown hq', b'unknown race')
        self.assertEqual(caught.exception.code, 'template_identity')
        parse.assert_not_called()

    def test_both_hashes_are_authenticated_before_either_is_parsed(self):
        hq, race = b'independent hq fixture', b'independent race fixture'
        identities = {'hq/wheel': sha256(hq), 'race/wheel': sha256(race)}
        with patch.dict(contract.ASSETS, identities), patch.object(contract, '_asset') as parse:
            with self.assertRaises(GarageError):
                contract.audit_native_wheel_contract(hq, race + b'changed')
        parse.assert_not_called()

    def test_mutable_inputs_are_rejected_without_modification(self):
        original = bytearray(b'not a native file')
        with self.assertRaises(GarageError):
            contract.audit_native_wheel_contract(original, b'other')
        self.assertEqual(original, b'not a native file')

    def test_race_shadow_binding_cannot_be_misidentified_as_a_tyre(self):
        for sid in (0, 3, 6, 9):
            self.assertEqual(contract._role('race', sid, 'WheelBlurShadow'), 'rim_blur_shadow')
            with self.assertRaises(GarageError):
                contract._role('race', sid, 'Flex')

    def test_packed_mesh_index_uses_0x34_and_xyz_ignores_unused_w_sentinels(self):
        data = packed_fixture()
        result = contract._packed(MDL3(bytes(data)), 0x500, 0, [])
        self.assertEqual(result['mesh_index'], 0)
        self.assertEqual(result['mesh_index_field_offset'], 0x634)
        self.assertEqual(result['packed_vertex_count'], 3)
        self.assertTrue(result['component_bounds_match'])
        json.dumps(result, allow_nan=False)
        struct.pack_into('>I', data, 0x634, 1)
        with self.assertRaises(GarageError):
            contract._packed(MDL3(bytes(data)), 0x500, 0, [])

    def test_zero_or_out_of_metadata_packed_payload_is_not_read_as_a_header(self):
        for pointer in (0, 0xfff):
            with self.subTest(pointer=pointer):
                data = packed_fixture()
                struct.pack_into('>I', data, 0x800, pointer)
                with self.assertRaises(GarageError):
                    contract._packed(MDL3(bytes(data)), 0x500, 0, [])

    def test_packed_component_bounds_require_two_four_vertex_soa_blocks(self):
        data = packed_fixture()
        data[0x900:0x960] = data[0xa00:0xa60]
        with self.assertRaises(GarageError):
            contract._packed(MDL3(bytes(data)), 0x500, 0, [])

    def test_unknown_packed_extension_is_not_treated_as_ordinary_payload(self):
        data = packed_fixture()
        struct.pack_into('>I', data, 0x804, 0xe80)
        with self.assertRaises(GarageError):
            contract._packed(MDL3(bytes(data)), 0x500, 0, [])

    def test_aliases_keep_typed_owners_and_lengths_without_reclaim_claim(self):
        refs = [{'shape': 0, 'kind': 'packed_positions', 'offset': 300, 'bytes': 18},
                {'shape': 3, 'kind': 'packed_positions', 'offset': 300, 'bytes': 24},
                {'shape': 2, 'kind': 'ordinary_indices', 'offset': 300, 'bytes': 18}]
        self.assertEqual(contract._aliases(refs), [
            {'kind': 'packed_positions', 'offset': 300, 'shapes': [0, 3],
             'referenced_byte_lengths': [18, 24]}])

    def test_race_model_inventory_covers_common_rim_blur_and_shadow_paths(self):
        data, shapes = race_model_fixture()
        result = contract._models(MDL3(bytes(data)), 'race', shapes)
        self.assertEqual([draw['role'] for draw in result[0]['draws']],
                         ['rim_blur_shadow', 'rim_and_valve', 'rim_blur'])
        self.assertEqual([draw['command_offset'] for draw in result[0]['draws']], [0, 13, 18])
        self.assertEqual(result[7]['draws'][0]['shape'], 15)
        self.assertFalse(result[0]['runtime_branch_threshold_verified'])

    def test_changed_race_jump_is_rejected_instead_of_relabelled_as_valid(self):
        data, shapes = race_model_fixture()
        data[0xa00 + 12] = 7
        with self.assertRaises(GarageError):
            contract._models(MDL3(bytes(data)), 'race', shapes)


class OwnedWheelContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get('GT6_GARAGE_WHEEL_CONTRACT_ASSETS')
        root = Path(configured) if configured else Path(__file__).resolve().parents[2] / 'assembly_ui_native_reference'
        if not (root / 'hq/wheel').is_file() or not (root / 'race/wheel').is_file():
            raise unittest.SkipTest('Owned native wheel fixtures are not bundled; set GT6_GARAGE_WHEEL_CONTRACT_ASSETS.')
        cls.hq, cls.race = (root / 'hq/wheel').read_bytes(), (root / 'race/wheel').read_bytes()
        cls.report = contract.audit_native_wheel_contract(cls.hq, cls.race)

    def test_factory_showroom_export_and_its_four_packed_rims(self):
        from .stock_race_wheel import STOCK_SHOWROOM_SHA256, restore_stock_showroom_wheel
        from .template_shell_preview import decode_template_wheels
        from .running_gear import resolve_running_gear
        restored=restore_stock_showroom_wheel(self.hq)
        self.assertEqual(sha256(restored),STOCK_SHOWROOM_SHA256)
        self.assertEqual(restore_stock_showroom_wheel(restored),restored)
        damaged=self.hq[:-1]
        with self.assertRaises(GarageError):
            restore_stock_showroom_wheel(damaged)
        gear=resolve_running_gear({'mode':'checked_stock','wheel_set':'supra_factory'})
        components,proof=decode_template_wheels(self.hq,gear)
        self.assertEqual(proof['sha256'],STOCK_SHOWROOM_SHA256)
        rims=[row for row in components if row['role']=='rim']
        self.assertEqual(len(rims),4)
        self.assertTrue(all(len(row['positions'])==701 and len(row['faces'])==700 for row in rims))
        self.assertEqual(sum(bool(row['tyre_guide']) for row in components),4)

    def test_supplied_race_wheel_recovers_original_packed_asset_and_rejects_other_wheels(self):
        restored = restore_stock_race_wheel(self.race)
        self.assertEqual(sha256(restored), STOCK_SHA256)
        self.assertEqual(len(restored), 218496)
        self.assertEqual(restore_stock_race_wheel(restored), restored)
        for wrong in (self.hq, self.race[:-1], restored[:-1]):
            with self.assertRaises(GarageError):
                restore_stock_race_wheel(wrong)

    def test_actual_inventory_is_serializable_read_only_and_keeps_runtime_limits(self):
        self.assertEqual(sha256(self.hq), contract.ASSETS['hq/wheel'])
        self.assertEqual(sha256(self.race), contract.ASSETS['race/wheel'])
        self.assertTrue(self.report['read_only'])
        self.assertFalse(self.report['runtime_verified'])
        self.assertFalse(self.report['capabilities']['complete_wheel_size_authoring'])
        self.assertFalse(self.report['capabilities']['race_physical_tyre_owner_identified'])
        json.dumps(self.report, allow_nan=False)

    def test_actual_race_common_draws_are_blur_shadows_with_shared_packed_data(self):
        race = self.report['assets']['race']
        for sid in (0, 3, 6, 9):
            shape = race['shapes'][sid]
            self.assertEqual(shape['binding']['shader'], 'WheelBlurShadow')
            self.assertEqual(shape['role'], 'rim_blur_shadow')
            self.assertFalse(shape['physical_tyre_measurement'])
            self.assertEqual(shape['geometry']['storage'], 'packed_inline')
        normal_alias = next(row for row in race['shared_payloads']
                            if row['kind'] == 'raw_normals_colour_uv' and row['offset'] == 16736)
        self.assertEqual(normal_alias['shapes'], [0, 3, 6, 9])
        layout_alias = next(row for row in race['shared_payloads'] if row['kind'] == 'packed_position_layout')
        self.assertEqual(layout_alias['shapes'], [0, 2, 3, 5, 6, 8, 9, 11])

    def test_actual_hq_mesh_envelopes_are_not_promoted_to_physical_mount_proof(self):
        hq = self.report['assets']['hq']
        # Raw vertex radial maxima differ slightly from nominal tyre radii and
        # from half the axis-aligned diameter. Neither is a live physics result.
        measured = (.3218012777941803, .3218227947530377, .31804950454350994, .3180707557778693)
        for sid, radial_maximum in enumerate(measured):
            shape = hq['shapes'][sid]
            self.assertEqual(shape['role'], 'profile_hq_tyre_mesh')
            self.assertAlmostEqual(shape['geometry']['local_radial_envelope_metres'], radial_maximum, delta=1e-7)
            self.assertFalse(shape['physical_tyre_measurement'])
        shadows = next(row for row in hq['shared_payloads'] if row['kind'] == 'ordinary_vertices')
        self.assertEqual(shadows['shapes'], [4, 5, 6, 7])
        self.assertFalse(self.report['capabilities']['native_axle_mount_owner_identified'])


if __name__ == '__main__':
    unittest.main()
