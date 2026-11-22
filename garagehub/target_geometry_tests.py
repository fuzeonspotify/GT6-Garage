"""Synthetic SQLite geometry graphs; no model whitelist or game assets."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from .errors import GarageError
from .target_geometry import target_geometry_report


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture(path):
    """Two unrelated cars plus explicitly selectable alternate part rows.

No primary-key constraints are used so duplicate/ambiguous data can be tested
as actual SQLite rows rather than mocked reader return values.
"""
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
        CREATE TABLE GENERIC_CAR(ID INTEGER,CarID INTEGER,CarLabel TEXT,ModelCode INTEGER,Defaultparts INTEGER);
        CREATE TABLE DEFAULT_PARTS(ID INTEGER,CHASSIS INTEGER,FRONTTIRE INTEGER,REARTIRE INTEGER);
        CREATE TABLE CHASSIS(ID INTEGER,UseCar INTEGER,wheelbase INTEGER,treadF INTEGER,treadR INTEGER,
          original_rideHeight_F INTEGER,width INTEGER);
        CREATE TABLE FRONTTIRE(ID INTEGER,UseCar INTEGER,category INTEGER,tiresize INTEGER);
        CREATE TABLE REARTIRE(ID INTEGER,UseCar INTEGER,category INTEGER,tiresize INTEGER);
        CREATE TABLE TIRESIZE(ID INTEGER,Label TEXT,diameter INTEGER,width INTEGER,flatness INTEGER);
        CREATE TABLE WHEEL(CarCode INTEGER,LabelID INTEGER,ModelCode INTEGER,
          FrontDiameter REAL,FrontWidth REAL,FrontAspect REAL,
          RearDiameter REAL,RearWidth REAL,RearAspect REAL,
          FrontTireDiameter REAL,RearTireDiameter REAL,FrontDiameterMM INTEGER,RearDiameterMM INTEGER,
          FrontTireType INTEGER,RearTireType INTEGER);
        CREATE TABLE WHEEL_SP(ID INTEGER,WheelExID INTEGER,CarCode INTEGER,ModelCode INTEGER,
          FrontDiameter REAL,FrontWidth REAL,FrontAspect REAL,
          RearDiameter REAL,RearWidth REAL,RearAspect REAL,
          FrontTireDiameter REAL,RearTireDiameter REAL,FrontDiameterMM INTEGER,RearDiameterMM INTEGER,
          FrontTireType INTEGER,RearTireType INTEGER);
        CREATE TABLE UNRELATED(note TEXT,payload BLOB);
        ''')
        for car, label, model, base, geometry, front, rear in (
            (11, 'arbitrary_coupe', 10001, 100, (2700, 1500, 1510), (180, 45, 9), (180, 51, 8)),
            (27, 'unlisted_prototype', 98765432, 200, (2580, 1620, 1602), (190, 49, 7), (200, 67, 6))):
            db.execute('INSERT INTO GENERIC_CAR VALUES(?,?,?,?,?)', (car, car, label, model, base))
            db.execute('INSERT INTO DEFAULT_PARTS VALUES(?,?,?,?)', (base, base+1, base+2, base+3))
            db.execute('INSERT INTO CHASSIS VALUES(?,?,?,?,?,?,?)', (base+1, car, *geometry, 125, 1800))
            db.execute('INSERT INTO FRONTTIRE VALUES(?,?,?,?)', (base+2, car, 5, base*10))
            db.execute('INSERT INTO REARTIRE VALUES(?,?,?,?)', (base+3, car, 5, base*10+1))
            for ident, dims in ((base*10, front), (base*10+1, rear)):
                db.execute('INSERT INTO TIRESIZE VALUES(?,?,?,?,?)', (ident, 'size_'+str(ident), *dims))
            db.execute('INSERT INTO WHEEL VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (car, car, model, front[0], front[1]*5, front[2]*5,
                        rear[0], rear[1]*5, rear[2]*5, 0, 0, 0, 0, 0, 0))
        db.execute('INSERT INTO CHASSIS VALUES(?,?,?,?,?,?,?)', (106, 11, 2550, 1450, 1460, 90, 1790))
        db.execute('INSERT INTO FRONTTIRE VALUES(?,?,?,?)', (104, 11, 7, 1002))
        db.execute('INSERT INTO REARTIRE VALUES(?,?,?,?)', (105, 11, 7, 1003))
        db.executemany('INSERT INTO TIRESIZE VALUES(?,?,?,?,?)',
                       [(1002, 'alternate_front', 180, 47, 8), (1003, 'alternate_rear', 190, 53, 7)])
        db.execute('INSERT INTO UNRELATED VALUES(?,?)', ('unchanged', b'\x00\xff'))
        db.commit()


class TargetGeometryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root/'geometry.sqlite'
        fixture(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def change(self, sql, params=()):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(sql, params)
            db.commit()

    def report(self, car=11, **kwargs):
        return target_geometry_report(self.db, car, **kwargs)

    def codes(self, report):
        return {issue['code'] for issue in report['issues']}

    def option(self, ident, diameter=150, width=205, aspect=65):
        self.change('INSERT INTO WHEEL_SP VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident, 33, 11, 10001, diameter, width, aspect, diameter, width, aspect, 0, 0, 0, 0, 0, 0))

    def test_default_graph_units_ids_and_nominal_scope(self):
        before = digest(self.db)
        result = self.report()
        self.assertTrue(result['nominal_data_complete'])
        self.assertTrue(result['visual_data_complete'])
        self.assertEqual(result['database']['sha256'], before)
        self.assertEqual(result['selection']['CHASSIS'],
                         {'source':'default','part_id':101,'default_part_id':101,'current_installed_state_verified':False})
        front = result['physical']['front_tyre']
        self.assertEqual((front['part_id'], front['size_id']), (102, 1000))
        self.assertEqual(front['record']['ids']['UseCar'], 11)
        self.assertEqual(front['size_record']['raw']['width'], 45)
        self.assertEqual(front['nominal']['section_width_metres'], .225)
        self.assertEqual(front['nominal']['rim_diameter_inches'], 18)
        self.assertEqual(front['nominal']['aspect_percent'], 45)
        self.assertAlmostEqual(front['nominal']['outer_radius_metres'], .32985)
        self.assertAlmostEqual(result['physical']['rear_tyre']['nominal']['outer_radius_metres'], .3306)
        self.assertEqual(result['physical']['chassis']['nominal'],
                         {'wheelbase_metres':2.7,'front_track_metres':1.5,'rear_track_metres':1.51})
        self.assertEqual(result['comparisons']['stock_wheel']['status'], 'agree')
        self.assertEqual(result['issues'], [])
        self.assertIsNone(result['mount_coordinates'])
        self.assertIsNone(result['native_profile_compatible'])
        self.assertIsNone(result['actual_rendered_tyre_dimensions'])
        self.assertIsNone(result['loaded_contact_radius_metres'])
        self.assertFalse(result['runtime_fit_verified'])
        self.assertFalse(result['source_geometry_inspected'])
        self.assertFalse(result['physics_changed'])
        self.assertEqual(digest(self.db), before)

    def test_unlisted_car_with_different_asymmetric_dimensions(self):
        result = self.report(27)
        self.assertTrue(result['nominal_data_complete'])
        self.assertEqual(result['target']['label'], 'unlisted_prototype')
        self.assertEqual(result['target']['model_code'], 98765432)
        self.assertEqual(result['physical']['front_tyre']['size_id'], 2000)
        self.assertEqual(result['physical']['rear_tyre']['size_id'], 2001)
        self.assertAlmostEqual(result['physical']['front_tyre']['nominal']['outer_radius_metres'], .32705)
        self.assertAlmostEqual(result['physical']['rear_tyre']['nominal']['outer_radius_metres'], .3545)
        self.assertEqual(result['comparisons']['stock_wheel']['status'], 'agree')

    def test_explicit_selection_uses_exact_rows_and_defaults_unspecified_parts(self):
        selected = {'CHASSIS':106,'FRONTTIRE':104}
        before = digest(self.db)
        result = self.report(selected_parts=selected)
        self.assertEqual(selected, {'CHASSIS':106,'FRONTTIRE':104})
        self.assertEqual(result['requested_selected_parts'], selected)
        self.assertEqual(result['selection']['CHASSIS']['source'], 'selected')
        self.assertEqual(result['selection']['CHASSIS']['default_part_id'], 101)
        self.assertEqual(result['physical']['chassis']['nominal']['wheelbase_metres'], 2.55)
        self.assertEqual(result['physical']['front_tyre']['size_id'], 1002)
        self.assertEqual(result['physical']['rear_tyre']['part_id'], 103)
        self.assertEqual(result['selection']['REARTIRE']['source'], 'default')
        self.assertAlmostEqual(result['physical']['front_tyre']['nominal']['outer_radius_metres'], .3226)
        self.assertEqual(result['comparisons']['stock_wheel']['front']['status'], 'disagree')
        self.assertEqual(result['comparisons']['stock_wheel']['rear']['status'], 'agree')
        self.assertEqual(digest(self.db), before)

    def test_explicit_foreign_part_is_disclosed_without_claiming_compatibility(self):
        result = self.report(selected_parts={'REARTIRE':203})
        self.assertEqual(result['physical']['rear_tyre']['size_id'], 2001)
        self.assertIn('part_owner_differs', self.codes(result))
        self.assertFalse(result['selection']['REARTIRE']['current_installed_state_verified'])
        self.assertIsNone(result['native_profile_compatible'])

    def test_visual_physical_disagreement_is_preserved_without_fit_threshold(self):
        self.change('UPDATE WHEEL SET FrontDiameter=170,FrontWidth=235 WHERE CarCode=11')
        before = digest(self.db)
        result = self.report()
        self.assertTrue(result['nominal_data_complete'])
        self.assertEqual(result['physical']['front_tyre']['nominal']['section_width_metres'], .225)
        self.assertEqual(result['visual']['wheel']['rows'][0]['front']['nominal']['section_width_metres'], .235)
        delta = result['comparisons']['stock_wheel']['front']
        self.assertEqual(delta['status'], 'disagree')
        self.assertAlmostEqual(delta['differences']['section_width_metres'], .01)
        self.assertEqual(delta['differences']['rim_diameter_inches'], -1)
        self.assertIn('visual_physical_disagreement', self.codes(result))
        self.assertEqual(result['issue_messages'], [item['message'] for item in result['issues']])
        self.assertEqual(digest(self.db), before)

    def test_special_visual_controls_are_not_guessed(self):
        for field, value in (('FrontTireDiameter', 660), ('FrontDiameterMM', 457), ('FrontTireType', 1)):
            with self.subTest(field=field):
                self.change('UPDATE WHEEL SET '+field+'=? WHERE CarCode=11', (value,))
                result = self.report()
                self.assertTrue(result['nominal_data_complete'])
                row = result['visual']['wheel']['rows'][0]
                self.assertIsNone(row['front']['nominal'])
                self.assertEqual(row['record']['raw'][field], value)
                self.assertIn('unsupported_visual_control', self.codes(result))
                self.assertEqual(result['comparisons']['stock_wheel']['status'], 'incomplete')
                self.change('UPDATE WHEEL SET '+field+'=0 WHERE CarCode=11')

    def test_missing_visual_control_does_not_assume_zero(self):
        self.change('ALTER TABLE WHEEL DROP COLUMN FrontDiameterMM')
        result = self.report()
        self.assertTrue(result['nominal_data_complete'])
        self.assertFalse(result['visual_data_complete'])
        self.assertIsNone(result['visual']['wheel']['rows'][0]['front']['nominal'])
        self.assertIn('unknown_visual_control', self.codes(result))

    def test_missing_physical_table_remains_incomplete(self):
        self.change('DROP TABLE TIRESIZE')
        result = self.report()
        self.assertFalse(result['nominal_data_complete'])
        self.assertEqual(result['physical']['front_tyre']['part_id'], 102)
        self.assertIsNone(result['physical']['front_tyre']['nominal'])
        self.assertEqual(result['visual']['wheel']['rows'][0]['front']['status'], 'complete')
        self.assertIn('missing_table', self.codes(result))

    def test_missing_stock_or_option_table_is_explicit_without_losing_physical_data(self):
        self.change('DROP TABLE WHEEL_SP')
        result = self.report()
        self.assertTrue(result['nominal_data_complete'])
        self.assertFalse(result['visual_data_complete'])
        self.assertEqual(result['visual']['wheel_sp']['status'], 'incomplete')
        self.assertIn('missing_table', self.codes(result))
        self.change('DROP TABLE WHEEL')
        result = self.report()
        self.assertTrue(result['nominal_data_complete'])
        self.assertEqual(result['comparisons']['stock_wheel']['status'], 'incomplete')

    def test_missing_track_column_never_uses_body_width_as_track(self):
        self.change('ALTER TABLE CHASSIS DROP COLUMN treadF')
        result = self.report()
        self.assertFalse(result['nominal_data_complete'])
        self.assertEqual(result['physical']['chassis']['record']['raw']['width'], 1800)
        self.assertIsNone(result['physical']['chassis']['nominal'])
        self.assertIn('missing_column', self.codes(result))

    def test_missing_tire_reference_is_not_filled_from_visual_wheel(self):
        self.change('UPDATE FRONTTIRE SET tiresize=99999 WHERE ID=102')
        result = self.report()
        self.assertIsNone(result['physical']['front_tyre']['size_record'])
        self.assertIsNone(result['physical']['front_tyre']['nominal'])
        self.assertIn('missing_row', self.codes(result))
        self.assertEqual(result['comparisons']['stock_wheel']['front']['status'], 'incomplete')

    def test_duplicate_car_cannot_acquire_an_arbitrary_part_graph(self):
        self.change('INSERT INTO GENERIC_CAR SELECT * FROM GENERIC_CAR WHERE ID=11')
        result = self.report(selected_parts={'CHASSIS':106})
        self.assertFalse(result['nominal_data_complete'])
        self.assertFalse(result['visual_data_complete'])
        self.assertIsNone(result['target']['record'])
        self.assertIsNone(result['physical']['chassis']['record'])
        self.assertIn('ambiguous_row', self.codes(result))

    def test_duplicate_chassis_and_size_ids_are_incomplete(self):
        self.change('INSERT INTO CHASSIS SELECT * FROM CHASSIS WHERE ID=101')
        self.change('INSERT INTO TIRESIZE SELECT * FROM TIRESIZE WHERE ID=1000')
        result = self.report()
        self.assertIsNone(result['physical']['chassis']['nominal'])
        self.assertIsNone(result['physical']['front_tyre']['nominal'])
        self.assertEqual(result['physical']['rear_tyre']['status'], 'complete')
        self.assertIn('ambiguous_row', self.codes(result))

    def test_sqlite_affinity_cannot_turn_text_car_id_into_exact_identity(self):
        self.change('ALTER TABLE GENERIC_CAR RENAME TO GENERIC_CAR_DATA')
        self.change('CREATE TABLE GENERIC_CAR(ID TEXT,CarID INTEGER,CarLabel TEXT,ModelCode INTEGER,Defaultparts INTEGER)')
        self.change('INSERT INTO GENERIC_CAR SELECT * FROM GENERIC_CAR_DATA')
        result = self.report()
        self.assertFalse(result['nominal_data_complete'])
        self.assertFalse(result['visual_data_complete'])
        self.assertIsNone(result['target']['record'])
        issue = next(item for item in result['issues'] if item['code']=='invalid_identity')
        self.assertEqual(issue['matching_records'][0]['raw']['ID'], '11')

    def test_sqlite_affinity_cannot_attach_visual_rows_with_text_car_code(self):
        self.change('ALTER TABLE WHEEL RENAME TO WHEEL_DATA')
        self.change('CREATE TABLE WHEEL(CarCode TEXT,FrontWidth INTEGER)')
        self.change('INSERT INTO WHEEL VALUES(?,?)', ('11', 225))
        result = self.report()
        self.assertTrue(result['nominal_data_complete'])
        self.assertFalse(result['visual_data_complete'])
        self.assertEqual(result['comparisons']['stock_wheel']['status'], 'incomplete')
        self.assertIn('invalid_identity', self.codes(result))

    def test_ambiguous_stock_rows_are_all_retained_and_neither_is_selected(self):
        self.change('INSERT INTO WHEEL SELECT * FROM WHEEL WHERE CarCode=11')
        self.change('UPDATE WHEEL SET FrontWidth=245 WHERE rowid=(SELECT max(rowid) FROM WHEEL)')
        result = self.report()
        self.assertEqual(result['visual']['wheel']['row_count'], 2)
        self.assertEqual(len(result['visual']['wheel']['rows']), 2)
        self.assertEqual(result['visual']['wheel']['selection'], 'not_selected')
        self.assertFalse(result['visual_data_complete'])
        self.assertEqual(result['comparisons']['stock_wheel']['status'], 'incomplete')
        self.assertIsNone(result['comparisons']['stock_wheel']['front'])
        self.assertIn('ambiguous_stock_wheel', self.codes(result))

    def test_alternative_visual_wheels_are_unselected_option_rows(self):
        self.option(501)
        self.option(502, 160, 215, 55)
        result = self.report()
        self.assertTrue(result['visual_data_complete'])
        self.assertEqual(result['comparisons']['stock_wheel']['status'], 'agree')
        self.assertEqual([row['record']['ids']['ID'] for row in result['visual']['wheel_sp']['rows']], [501,502])
        self.assertEqual(len(result['comparisons']['wheel_sp']), 2)
        self.assertTrue(all(row['selection']=='not_selected' for row in result['comparisons']['wheel_sp']))
        self.assertEqual(result['comparisons']['wheel_sp'][0]['front']['status'], 'disagree')
        self.assertNotIn('visual_physical_disagreement', self.codes(result))
        self.assertEqual(result['physical']['front_tyre']['size_id'], 1000)

    def test_duplicate_visual_option_identity_is_explicit(self):
        self.option(501)
        self.option(501, 160, 215, 55)
        result = self.report()
        self.assertEqual(len(result['visual']['wheel_sp']['rows']), 2)
        self.assertEqual(result['visual']['wheel_sp']['status'], 'incomplete')
        self.assertIn('ambiguous_visual_option', self.codes(result))

    def test_invalid_size_codes_are_unknown_and_json_safe(self):
        for value in (0, -1, 45.5, 'unknown', float('inf'), float('nan')):
            with self.subTest(value=value):
                self.change('UPDATE TIRESIZE SET width=? WHERE ID=1000', (value,))
                result = self.report()
                self.assertFalse(result['nominal_data_complete'])
                self.assertIsNone(result['physical']['front_tyre']['nominal'])
                self.assertIn('invalid_dimension', self.codes(result))
                json.dumps(result, allow_nan=False)

    def test_zero_visual_aspect_or_nonfinite_width_does_not_become_rim_only_tyre(self):
        self.change('UPDATE WHEEL SET FrontAspect=0,RearWidth=? WHERE CarCode=11', (float('inf'),))
        result = self.report()
        row = result['visual']['wheel']['rows'][0]
        self.assertIsNone(row['front']['nominal'])
        self.assertIsNone(row['rear']['nominal'])
        self.assertTrue(result['nominal_data_complete'])
        self.assertEqual(result['comparisons']['stock_wheel']['status'], 'incomplete')
        json.dumps(result, allow_nan=False)

    def test_positive_raw_dimensions_cannot_complete_with_underflowed_metres(self):
        self.change('UPDATE CHASSIS SET wheelbase=? WHERE ID=101', (5e-324,))
        self.change('UPDATE WHEEL SET FrontDiameter=?,FrontWidth=?,FrontAspect=? WHERE CarCode=11',
                    (1e-321, 5e-321, 1e-320))
        result = self.report()
        self.assertFalse(result['nominal_data_complete'])
        self.assertIsNone(result['physical']['chassis']['nominal'])
        self.assertIsNone(result['visual']['wheel']['rows'][0]['front']['nominal'])
        self.assertFalse(result['visual_data_complete'])
        self.assertIn('invalid_dimension', self.codes(result))
        self.assertGreater(result['physical']['chassis']['record']['raw']['wheelbase'], 0)
        json.dumps(result, allow_nan=False)

    def test_missing_car_and_missing_default_parts_are_explicit(self):
        result = self.report(999)
        self.assertFalse(result['nominal_data_complete'])
        self.assertFalse(result['visual_data_complete'])
        self.assertIsNone(result['target']['record'])
        self.assertIn('missing_row', self.codes(result))
        self.change('DELETE FROM DEFAULT_PARTS WHERE ID=100')
        result = self.report()
        self.assertFalse(result['nominal_data_complete'])
        self.assertEqual(result['selection']['FRONTTIRE']['part_id'], None)
        self.assertIn('invalid_reference', self.codes(result))

    def test_explicit_selected_parts_do_not_repair_ambiguous_default_data(self):
        self.change('INSERT INTO DEFAULT_PARTS SELECT * FROM DEFAULT_PARTS WHERE ID=100')
        result = self.report(selected_parts={'CHASSIS':106,'FRONTTIRE':104,'REARTIRE':105})
        self.assertIsNone(result['target']['default_parts_record'])
        self.assertIn('ambiguous_row', self.codes(result))
        self.assertEqual(result['physical']['front_tyre']['size_id'], 1002)
        self.assertEqual(result['selection']['REARTIRE']['default_part_id'], None)
        self.assertIsNone(result['native_profile_compatible'])

    def test_invalid_api_arguments_do_not_create_or_change_database(self):
        before = digest(self.db)
        for car in (True, -1, '11', 0xFFFFFFFF):
            with self.subTest(car=car), self.assertRaises(GarageError):
                self.report(car)
        for selected in ([], {'SUSPENSION':1}, {'FRONTTIRE':True}, {'CHASSIS':-1}, {'REARTIRE':'105'}):
            with self.subTest(selected=selected), self.assertRaises(GarageError):
                self.report(selected_parts=selected)
        self.assertEqual(digest(self.db), before)
        self.assertEqual(list(self.root.iterdir()), [self.db])

    def test_missing_encrypted_or_transaction_snapshot_is_rejected(self):
        missing = self.root/'missing.sqlite'
        with self.assertRaises(GarageError):
            target_geometry_report(missing, 11)
        self.assertFalse(missing.exists())
        encrypted = self.root/'encrypted.dat'
        encrypted.write_bytes(b'not a decrypted database')
        with self.assertRaises(GarageError) as error:
            target_geometry_report(encrypted, 11)
        self.assertEqual(error.exception.code, 'specdb_encrypted')
        before = digest(self.db)
        sidecar = Path(str(self.db)+'-wal')
        sidecar.write_bytes(b'pending')
        with self.assertRaises(GarageError):
            self.report()
        self.assertEqual(sidecar.read_bytes(), b'pending')
        self.assertEqual(digest(self.db), before)

    def test_view_is_not_executed_as_an_ordinary_part_table(self):
        self.change('ALTER TABLE CHASSIS RENAME TO CHASSIS_DATA')
        self.change('CREATE VIEW CHASSIS AS SELECT ID,unavailable_function(UseCar) AS UseCar FROM CHASSIS_DATA')
        result = self.report()
        self.assertIsNone(result['physical']['chassis']['record'])
        self.assertIn('unsupported_table', self.codes(result))

    def test_sqlite_identifier_case_is_respected_without_losing_raw_provenance(self):
        self.change('ALTER TABLE TIRESIZE RENAME COLUMN flatness TO profile_temp')
        self.change('ALTER TABLE TIRESIZE RENAME COLUMN profile_temp TO FLATNESS')
        self.change('ALTER TABLE TIRESIZE RENAME TO sizes_temp')
        self.change('ALTER TABLE sizes_temp RENAME TO tiresize')
        result = self.report()
        self.assertTrue(result['nominal_data_complete'])
        raw = result['physical']['front_tyre']['size_record']['raw']
        self.assertEqual(raw['FLATNESS'], 9)
        self.assertEqual(result['physical']['front_tyre']['nominal']['aspect_percent'], 45)

    def test_success_and_incomplete_results_preserve_all_bytes_and_create_no_sidecars(self):
        before = digest(self.db)
        self.report()
        self.report(selected_parts={'FRONTTIRE':987654})
        self.assertEqual(digest(self.db), before)
        self.assertEqual(list(self.root.iterdir()), [self.db])
        with closing(sqlite3.connect(self.db.as_uri()+'?mode=ro', uri=True)) as db:
            self.assertEqual(db.execute('SELECT * FROM UNRELATED').fetchall(), [('unchanged', b'\x00\xff')])

    def test_changed_snapshot_cannot_return_an_unchanged_receipt(self):
        before = digest(self.db)
        with patch('garagehub.target_geometry._sha', side_effect=[before, 'different']):
            with self.assertRaises(GarageError) as error:
                self.report()
        self.assertEqual(error.exception.code, 'specdb_stale')
        self.assertEqual(digest(self.db), before)

    def test_unknown_raw_blob_and_nonfinite_values_remain_traceable(self):
        self.change('ALTER TABLE TIRESIZE ADD COLUMN extra BLOB')
        self.change('UPDATE TIRESIZE SET extra=?,width=? WHERE ID=1000', (b'\x00\xff', float('inf')))
        result = self.report()
        row = result['physical']['front_tyre']['size_record']
        self.assertEqual(row['raw']['extra'], {'sqlite_type':'blob','hex':'00ff'})
        self.assertEqual(row['raw']['width'], {'sqlite_type':'real','value':'inf'})
        self.assertEqual(len(row['canonical_record_sha256']), 64)
        json.dumps(result, allow_nan=False)

    def test_mismatched_car_identity_never_claims_complete_target(self):
        self.change('UPDATE GENERIC_CAR SET CarID=27 WHERE ID=11')
        result = self.report()
        self.assertFalse(result['nominal_data_complete'])
        self.assertFalse(result['visual_data_complete'])
        self.assertIn('car_identity_mismatch', self.codes(result))
        self.assertFalse(result['runtime_fit_verified'])

