"""Focused relational and preservation regressions for isolated GT6 SpecDB edits.

Run: python -m unittest garagehub.specdb_tests -v
These are synthetic database tests, not game/runtime certification.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from .errors import GarageError
from . import specdb


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture(path):
    db = sqlite3.connect(path)
    db.executescript('''
    CREATE TABLE GENERIC_CAR(ID INTEGER PRIMARY KEY,CarID INTEGER,CarLabel TEXT,ModelCode INTEGER,
      Defaultparts INTEGER,NumColor INTEGER,MainColor INTEGER,InteriorType INTEGER,PremiumCar INTEGER,
      OpenCar INTEGER,Year INTEGER,Arcade INTEGER,InDealer INTEGER,SpecifyFlags1 INTEGER);
    CREATE TABLE DEFAULT_PARTS(ID INTEGER PRIMARY KEY,Label TEXT,CHASSIS INTEGER,FRONTTIRE INTEGER,
      REARTIRE INTEGER,ENGINE INTEGER,AirCleaner INTEGER,WheelVariation INTEGER,NOS INTEGER);
    CREATE TABLE CHASSIS(ID INTEGER PRIMARY KEY,Label TEXT,LabelID INTEGER,UseCar INTEGER,mass INTEGER,wheelbase INTEGER);
    CREATE TABLE FRONTTIRE(ID INTEGER PRIMARY KEY,Label TEXT,LabelID INTEGER,UseCar INTEGER,category INTEGER,tiresize INTEGER);
    CREATE TABLE REARTIRE(ID INTEGER PRIMARY KEY,Label TEXT,LabelID INTEGER,UseCar INTEGER,category INTEGER,tiresize INTEGER);
    CREATE TABLE ENGINE(ID INTEGER PRIMARY KEY,Label TEXT,LabelID INTEGER,UseCar INTEGER,power INTEGER);
    CREATE TABLE AIR_CLEANER(ID INTEGER PRIMARY KEY,Label TEXT,CarLabel TEXT,LabelID INTEGER,UseCar INTEGER,category INTEGER);
    CREATE TABLE NOS(ID INTEGER PRIMARY KEY,Label TEXT,LabelID INTEGER,UseCar INTEGER);
    CREATE TABLE MODEL_INFO(ID INTEGER PRIMARY KEY,CarCode INTEGER,ModelCode INTEGER,DriverModel TEXT,ModelWidth REAL);
    CREATE TABLE WHEEL(Label TEXT,CarCode INTEGER,LabelID INTEGER,ModelCode INTEGER,FrontWidth REAL,RearWidth REAL);
    CREATE TABLE WHEEL_SP(ID INTEGER PRIMARY KEY,CarCode INTEGER,ModelCode INTEGER,WheelExID INTEGER);
    CREATE TABLE CAR_CUSTOM_INFO(Label TEXT,CarCode INTEGER,LabelID INTEGER,BitsA INTEGER,BitsB INTEGER);
    CREATE TABLE VARIATION(Label TEXT,LabelID INTEGER,VarOrder INTEGER,CarColorID INTEGER,NameEng TEXT);
    CREATE TABLE CAR_NAME_ALPHABET(Label TEXT,LabelID INTEGER,Name TEXT,Grade TEXT,ShortName TEXT);
    CREATE TABLE CAR_NAME_JAPAN(Label TEXT,LabelID INTEGER,Name TEXT,Grade TEXT,ShortName TEXT);
    CREATE TABLE CAR_VIEW(ID INTEGER PRIMARY KEY,Label TEXT,Tuner TEXT);
    CREATE TABLE CAR_SEARCH(ID INTEGER PRIMARY KEY,CarLabel TEXT,Year INTEGER);
    CREATE TABLE UC_WING(ID INTEGER PRIMARY KEY,CarLabel TEXT,Width REAL);
    CREATE TABLE UNRELATED(tag TEXT,value BLOB);
    ''')
    for car_id, label, model, part, mass, name in (
        (82, 'supra_rz_97', 40006, 100, 1500, 'Toyota Supra RZ 1997'),
        (2144, 'cinque_custom_09', 800009, 200, 1250, 'Pagani Cinque'),
        (2145, 'f50_custom_95', 2250028, 300, 1230, 'Ferrari F50')):
        db.execute('INSERT INTO GENERIC_CAR VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (car_id,car_id,label,model,part,2,0,1,0,0,1997,1,1,2208))
        db.execute('INSERT INTO DEFAULT_PARTS VALUES(?,?,?,?,?,?,?,?,?)',
                   (part,'default_'+label,part+1,part+2,part+3,part+4,part+5,-1,4294967295))
        db.execute('INSERT INTO CHASSIS VALUES(?,?,?,?,?,?)',(part+1,'chassis_'+label,part+1,car_id,mass,2500))
        db.execute('INSERT INTO FRONTTIRE VALUES(?,?,?,?,?,?)',(part+2,'front_'+label,part+2,car_id,0,12))
        db.execute('INSERT INTO REARTIRE VALUES(?,?,?,?,?,?)',(part+3,'rear_'+label,part+3,car_id,0,13))
        db.execute('INSERT INTO ENGINE VALUES(?,?,?,?,?)',(part+4,'engine_'+label,part+4,car_id,300))
        db.execute('INSERT INTO AIR_CLEANER VALUES(?,?,?,?,?,?)',(part+5,'air_'+label,str(car_id),part+5,car_id,0))
        # A second upgrade proves all UseCar rows are copied, not just defaults.
        db.execute('INSERT INTO AIR_CLEANER VALUES(?,?,?,?,?,?)',(part+6,'air_'+label+'_b',str(car_id),part+6,car_id,1))
        db.execute('INSERT INTO MODEL_INFO VALUES(?,?,?,?,?)',(part+7,car_id,model,'driver_a',1.8))
        db.execute('INSERT INTO WHEEL VALUES(?,?,?,?,?,?)',(label,car_id,car_id,model,245.0,275.0))
        db.execute('INSERT INTO CAR_CUSTOM_INFO VALUES(?,?,?,?,?)',(label,car_id,car_id,17,29))
        for order in (1,2):
            db.execute('INSERT INTO VARIATION VALUES(?,?,?,?,?)',(label,car_id,order,4000+order,'Red' if order==1 else 'Blue'))
        for table in ('CAR_NAME_ALPHABET','CAR_NAME_JAPAN'):
            db.execute('INSERT INTO '+table+' VALUES(?,?,?,?,?)',(label,car_id,name,name,name))
        db.execute('INSERT INTO CAR_VIEW VALUES(?,?,?)',(car_id,label,'original_maker'))
        db.execute('INSERT INTO CAR_SEARCH VALUES(?,?,?)',(car_id,label,1997))
        db.execute('INSERT INTO UC_WING VALUES(?,?,?)',(part+8,label,1.5))
    db.executemany('INSERT INTO UNRELATED VALUES(?,?)',[('duplicate',b'\x00\xff'),('duplicate',b'\x00\xff'),('keep',None)])
    db.commit()
    db.close()


class SpecDBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.sqlite'
        fixture(self.source)
        self.original_hash = digest(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, **kwargs):
        options = dict(mode='new_entry',donor_car_id=82,name='My test model')
        options.update(kwargs)
        return specdb.plan_install(self.source, **options)

    def query(self, path, sql, args=()):
        with closing(sqlite3.connect(path)) as db:
            return db.execute(sql,args).fetchall()

    def change(self, sql, args=()):
        with closing(sqlite3.connect(self.source)) as db:
            with db:
                db.execute(sql,args)

    def test_model_codes_are_decimal_not_hex(self):
        self.assertEqual(specdb.model_path(2250028),'car/0225/0028')
        self.assertEqual(specdb.model_path(40006),'car/0004/0006')

    def test_new_entry_clones_graph_and_preserves_other_custom_cars(self):
        plan = self.plan()
        output = self.root / 'added.sqlite'
        receipt = specdb.build_database_copy(self.source,output,plan)
        self.assertEqual(plan['car_id'],2146)
        self.assertEqual(receipt['car_count_after'],4)
        self.assertEqual(digest(self.source),self.original_hash)
        self.assertEqual(self.query(output,'SELECT Name FROM CAR_NAME_ALPHABET WHERE LabelID=?',(2146,)),[('My test model',)])
        for table in ('GENERIC_CAR','CHASSIS','DEFAULT_PARTS','VARIATION','UNRELATED'):
            original = self.query(self.source,'SELECT * FROM '+table)
            changed = self.query(output,'SELECT * FROM '+table)
            for row in original:
                self.assertGreaterEqual(changed.count(row),original.count(row))
        self.assertTrue(receipt['all_unrelated_rows_preserved'])
        self.assertNotEqual(self.query(output,'SELECT ID FROM MODEL_INFO WHERE CarCode=2146')[0][0],2146)
        self.assertEqual(self.query(output,'SELECT count(*) FROM AIR_CLEANER WHERE UseCar=2146'),[(2,)])
        self.assertEqual(self.query(output,'SELECT DISTINCT CarLabel FROM AIR_CLEANER WHERE UseCar=2146'),[('2146',)])
        new_default = self.query(output,'SELECT Defaultparts FROM GENERIC_CAR WHERE ID=2146')[0][0]
        new_air = self.query(output,'SELECT AirCleaner FROM DEFAULT_PARTS WHERE ID=?',(new_default,))[0][0]
        self.assertEqual(self.query(output,'SELECT UseCar FROM AIR_CLEANER WHERE ID=?',(new_air,)),[(2146,)])

    def test_allocator_reserves_name_ids_orphan_references_and_packed_namespaces(self):
        self.change('INSERT INTO NOS VALUES(?,?,?,?)',(999,'orphan_2148',999,2148))
        plan = self.plan(reserved_car_ids=[2147],reserved_model_paths=['car/0004/0010/hq/body'])
        self.assertEqual(plan['car_id'],2149)
        self.assertEqual(plan['model_path'],'car/0004/0011')
        self.assertEqual(plan['request']['reserved_model_paths'],['car/0004/0010'])

    def test_reservation_collections_are_explicit_and_compact(self):
        with self.assertRaisesRegex(GarageError,'collection'):
            self.plan(reserved_model_paths='car/0004/0007')
        with self.assertRaisesRegex(GarageError,'integers'):
            self.plan(reserved_car_ids=[True])
        plan = self.plan(reserved_model_paths=['car/0004/0007/hq/body','car/0004/0007/race/body','description/gt6/US.rt2'])
        self.assertEqual(plan['request']['reserved_model_paths'],['car/0004/0007'])

    def test_repeat_import_allocates_independent_entries(self):
        first = self.plan()
        one = self.root / 'one.sqlite'
        specdb.build_database_copy(self.source,one,first)
        second = specdb.plan_install(one,mode='new_entry',donor_car_id=82,name='My test model')
        two = self.root / 'two.sqlite'
        result = specdb.build_database_copy(one,two,second)
        self.assertEqual(second['car_id'],first['car_id']+1)
        self.assertNotEqual(second['label'],first['label'])
        self.assertNotEqual(second['model_path'],first['model_path'])
        self.assertEqual(result['car_count_after'],5)

    def test_existing_label_is_rejected_even_outside_generic_car(self):
        self.change('INSERT INTO CAR_NAME_JAPAN VALUES(?,?,?,?,?)',('reserved_label',9999,'Reserved','Reserved','Reserved'))
        with self.assertRaisesRegex(GarageError,'already present'):
            self.plan(label='reserved_label')

    def test_exact_target_replacement_preserves_physics_names_and_shared_old_namespace(self):
        self.change('UPDATE GENERIC_CAR SET ModelCode=40006 WHERE ID=2144')
        self.change('UPDATE MODEL_INFO SET ModelCode=40006 WHERE CarCode=2144')
        self.change('UPDATE WHEEL SET ModelCode=40006 WHERE CarCode=2144')
        before = digest(self.source)
        plan = self.plan(mode='replace',target_car_id=82,name='This does not rename the target')
        self.assertTrue(plan['ready'])
        self.assertEqual(plan['display_name'],'Toyota Supra RZ 1997')
        output = self.root / 'replace.sqlite'
        receipt = specdb.build_database_copy(self.source,output,plan)
        self.assertEqual(receipt['car_count_after'],3)
        self.assertEqual(receipt['added_rows'],{})
        self.assertEqual(digest(self.source),before)
        for table in ('DEFAULT_PARTS','CHASSIS','ENGINE','VARIATION','CAR_NAME_ALPHABET','CAR_CUSTOM_INFO','UNRELATED'):
            self.assertEqual(self.query(self.source,'SELECT * FROM '+table),self.query(output,'SELECT * FROM '+table))
        self.assertEqual(self.query(output,'SELECT ModelCode FROM GENERIC_CAR WHERE ID=2144'),[(40006,)])
        self.assertNotEqual(self.query(output,'SELECT ModelCode FROM GENERIC_CAR WHERE ID=82'),[(40006,)])

    def test_compatible_existing_donor_clone_can_be_replaced(self):
        first = self.plan()
        source = self.root/'clone.sqlite'
        specdb.build_database_copy(self.source,source,first)
        plan = specdb.plan_install(source,mode='replace',donor_car_id=82,target_car_id=first['car_id'],name='Replacement')
        self.assertTrue(plan['ready'],plan['blockers'])
        result = specdb.build_database_copy(source,self.root/'replaced_clone.sqlite',plan)
        self.assertEqual(result['car_count_after'],4)
        self.assertEqual(result['display_name'],'My test model')

    def test_different_chassis_target_is_blocked_without_output(self):
        plan = self.plan(mode='replace',target_car_id=2144)
        self.assertFalse(plan['ready'])
        self.assertTrue(any('CHASSIS' in item for item in plan['blockers']))
        output = self.root/'blocked.sqlite'
        with self.assertRaisesRegex(GarageError,'CHASSIS'):
            specdb.build_database_copy(self.source,output,plan)
        self.assertFalse(output.exists())
        self.assertEqual(digest(self.source),self.original_hash)

    def test_existing_destination_is_never_replaced(self):
        output = self.root/'keep.sqlite'
        output.write_bytes(b'keep this file')
        with self.assertRaisesRegex(GarageError,'already exists'):
            specdb.build_database_copy(self.source,output,self.plan())
        self.assertEqual(output.read_bytes(),b'keep this file')

    def test_source_path_is_never_a_destination(self):
        with self.assertRaisesRegex(GarageError,'separate'):
            specdb.build_database_copy(self.source,self.source,self.plan())
        self.assertEqual(digest(self.source),self.original_hash)

    def test_source_change_invalidates_plan(self):
        plan = self.plan()
        self.change('UPDATE UNRELATED SET tag=? WHERE tag=?',('changed','keep'))
        with self.assertRaisesRegex(GarageError,'stale'):
            specdb.build_database_copy(self.source,self.root/'stale.sqlite',plan)
        self.assertFalse((self.root/'stale.sqlite').exists())

    def test_tampered_identity_invalidates_plan(self):
        plan = deepcopy(self.plan())
        plan['model_path']='car/0004/0006'
        with self.assertRaisesRegex(GarageError,'stale'):
            specdb.build_database_copy(self.source,self.root/'tampered.sqlite',plan)

    def test_invalid_color_count_rejected_before_copy(self):
        self.change('UPDATE GENERIC_CAR SET NumColor=3 WHERE ID=82')
        with self.assertRaisesRegex(GarageError,'color count'):
            self.plan()

    def test_missing_required_part_rejected_before_copy(self):
        self.change('UPDATE DEFAULT_PARTS SET CHASSIS=555555 WHERE ID=100')
        with self.assertRaisesRegex(GarageError,'CHASSIS'):
            self.plan()

    def test_missing_model_row_rejected_before_copy(self):
        self.change('DELETE FROM MODEL_INFO WHERE CarCode=82')
        with self.assertRaisesRegex(GarageError,'MODEL_INFO'):
            self.plan()

    def test_unknown_label_mapping_rolls_back_and_removes_output(self):
        self.change('UPDATE CHASSIS SET Label=? WHERE UseCar=82',('unrelated_literal',))
        plan = self.plan()
        before = digest(self.source)
        output = self.root/'failed.sqlite'
        with self.assertRaisesRegex(GarageError,'Cannot remap'):
            specdb.build_database_copy(self.source,output,plan)
        self.assertFalse(output.exists())
        self.assertEqual(digest(self.source),before)
        self.assertFalse(list(self.root.glob('.specdb_*')))

    def test_unselected_mode_and_missing_replacement_target_are_rejected(self):
        with self.assertRaisesRegex(GarageError,'Choose Add'):
            self.plan(mode=None)
        with self.assertRaisesRegex(GarageError,'Select the existing car'):
            self.plan(mode='replace')

    def test_catalogue_reports_compatibility_without_writing(self):
        result = specdb.catalogue(self.source,donor_car_id=82)
        cars = {car['car_id']:car for car in result['cars']}
        self.assertTrue(cars[82]['replacement_compatible'])
        self.assertFalse(cars[2144]['replacement_compatible'])
        self.assertEqual(cars[2145]['display_name'],'Ferrari F50')
        self.assertEqual(digest(self.source),self.original_hash)

    def test_source_with_triggers_is_not_executed_or_rewritten(self):
        self.change('CREATE TRIGGER tamper AFTER INSERT ON CHASSIS BEGIN DELETE FROM UNRELATED; END')
        with self.assertRaisesRegex(GarageError,'triggers'):
            self.plan()
        self.assertEqual(self.query(self.source,'SELECT count(*) FROM UNRELATED'),[(3,)])


if __name__ == '__main__':
    unittest.main()
