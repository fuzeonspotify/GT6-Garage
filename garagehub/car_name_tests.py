"""Independent NDB0 fixture and malformed-section tests; no game files needed."""
from __future__ import annotations
import base64,hashlib,struct,unittest
from .car_names import NDB0,name_ids,append_car_names,MAX_NEW_NAME_BYTES
from .errors import GarageError

# This fixed binary comes from the previous proven NDB0 writer, not this
# implementation. Maps have different locale ranks and compressed brand names.
FIXTURE=base64.b64decode(
    'TkRCMBI0VngAAADQAAAAAgAAACAAAACAXl5eXl5eXl6YdlQyAAAAA15eXl5eXl5e'
    'AAAACgAAAAAAAABgAP///wAAAB4AAAACAAAAaAH///8AAAAyAAAAAQAAAHD/////'
    'QWxwaGEAAABCZXRhAAAAAEdhbW1hIM6pAAAAAF5eXl6YdlQyAAAAA15eXl5eXl5e'
    'AAAACgAAAAIAAADAAP///wAAAB4AAAABAAAAxAH///8AAAAyAAAAAAAAAMj/////'
    'QQAAAEIAAABHIM6pAAAAAAAAAAIAAADcAAAA5E1ha2VyIEEATWFrZXIgQgAAAAAA')


def changed_uint(offset,value):
    raw=bytearray(FIXTURE);struct.pack_into('>I',raw,offset,value);return bytes(raw)


class CarNameTests(unittest.TestCase):
    def test_reviewed_fixture_ids_and_exact_noop_roundtrip(self):
        self.assertEqual(name_ids(FIXTURE),{10,30,50})
        parsed=NDB0.parse(FIXTURE)
        self.assertEqual(parsed.serialize(),FIXTURE)
        self.assertEqual(parsed.trees[0].entries[0].full_name(parsed.prefixes),'Maker A Alpha')
        self.assertEqual(parsed.trees[0].entries[2].full_name(parsed.prefixes),'Gamma Ω')

    def test_append_preserves_all_original_names_prefixes_and_relative_sort(self):
        old=NDB0.parse(FIXTURE)
        output,receipt=append_car_names(FIXTURE,20,'Prototype Roadster','Prototype',after_id=30)
        new=NDB0.parse(output)
        self.assertEqual(name_ids(output),{10,20,30,50})
        self.assertEqual(receipt['original_counts'],[3,3]);self.assertEqual(receipt['new_counts'],[4,4])
        self.assertEqual(receipt['inserted_ranks'],[3,2])
        self.assertTrue(receipt['reverse_append_roundtrip_exact'])
        self.assertEqual(new.header[:8],old.header[:8]);self.assertEqual(new.prefixes,old.prefixes)
        for before,after in zip(old.trees,new.trees):
            expected={e.car_id:(e.name,e.prefixes) for e in before.entries}
            actual={e.car_id:(e.name,e.prefixes) for e in after.entries if e.car_id!=20}
            self.assertEqual(actual,expected)
            old_order=[e.car_id for e in sorted(before.entries,key=lambda e:e.rank)]
            new_order=[e.car_id for e in sorted(after.entries,key=lambda e:e.rank) if e.car_id!=20]
            self.assertEqual(new_order,old_order)
        # Reverse-append evidence is also checked in the production function.
        self.assertEqual(output,NDB0.parse(output).serialize())

    def test_append_matches_previous_reviewed_writer_bytes(self):
        output,_=append_car_names(FIXTURE,20,'Prototype Roadster','Prototype',after_id=30)
        self.assertEqual(len(output),304)
        self.assertEqual(hashlib.sha256(output).hexdigest(),'b0fab83eeb121fda567f9d0f948afa2862395167fea5d5ab2fa40004e32c7e4b')

    def test_non_ascii_new_names_roundtrip_without_normalizing_existing_locale(self):
        output,_=append_car_names(FIXTURE,40,'試作車 Ω','試作',after_id=10)
        parsed=NDB0.parse(output)
        self.assertEqual(next(e.name.decode() for e in parsed.trees[0].entries if e.car_id==40),'試作車 Ω')
        self.assertEqual(next(e.name.decode() for e in parsed.trees[1].entries if e.car_id==40),'試作')

    def test_repeat_append_preserves_first_added_identity(self):
        one,_=append_car_names(FIXTURE,20,'First Custom','First',after_id=30)
        two,_=append_car_names(one,40,'Second Custom','Second',after_id=20)
        self.assertEqual(name_ids(two),{10,20,30,40,50})
        for tree in NDB0.parse(two).trees:
            self.assertIn(next(e.name for e in tree.entries if e.car_id==20),(b'First Custom',b'First'))

    def test_collision_never_replaces_existing_name(self):
        with self.assertRaisesRegex(GarageError,'already has a name'):
            append_car_names(FIXTURE,30,'Wrong','Wrong',after_id=10)
        self.assertEqual(name_ids(FIXTURE),{10,30,50})

    def test_anchor_required_and_must_exist(self):
        with self.assertRaisesRegex(GarageError,'anchor.*missing'):
            append_car_names(FIXTURE,40,'New','New',after_id=99)
        with self.assertRaises(TypeError):append_car_names(FIXTURE,40,'New','New')

    def test_id_and_anchor_types_and_reserved_id_refused(self):
        for value in (True,-1,0xFFFFFFFF,'40'):
            with self.subTest(value=value),self.assertRaises(GarageError):
                append_car_names(FIXTURE,value,'New','New',after_id=10)
        with self.assertRaises(GarageError):append_car_names(FIXTURE,40,'New','New',after_id=True)

    def test_mismatched_long_short_ids_refused(self):
        # Second map third record: keep the map sorted but change its car ID.
        raw=changed_uint(128+16+2*16,51)
        with self.assertRaisesRegex(GarageError,'different car IDs'):name_ids(raw)

    def test_unterminated_name_cannot_read_into_next_map(self):
        raw=bytearray(FIXTURE);struct.pack_into('>I',raw,32+16+8,124)
        raw[124:128]=b'ABCD'
        with self.assertRaisesRegex(GarageError,'unterminated'):name_ids(bytes(raw))

    def test_prefix_cannot_point_into_headers_or_records(self):
        with self.assertRaisesRegex(GarageError,'own section'):name_ids(changed_uint(208+4,16))

    def test_name_pointer_cannot_escape_its_map(self):
        for offset in (16,128,0xFFFFFFFF):
            with self.subTest(offset=offset),self.assertRaises(GarageError):
                name_ids(changed_uint(32+16+8,offset))

    def test_excessive_counts_refused_before_allocating_record_lists(self):
        for offset in (32+4,128+4,208):
            with self.subTest(offset=offset),self.assertRaises(GarageError):name_ids(changed_uint(offset,0xFFFFFFFF))

    def test_section_and_prefix_tables_must_fit_file(self):
        for offset in (8,16,20):
            with self.subTest(offset=offset),self.assertRaises(GarageError):name_ids(changed_uint(offset,0xFFFFFFF0))
        with self.assertRaises(GarageError):name_ids(FIXTURE[:219])

    def test_duplicate_ids_and_ranks_refused(self):
        with self.assertRaisesRegex(GarageError,'unique'):name_ids(changed_uint(32+16+16,10))
        with self.assertRaisesRegex(GarageError,'permutation'):name_ids(changed_uint(32+16+16+4,0))

    def test_noop_gate_rejects_unrecognized_padding_without_rewriting(self):
        raw=bytearray(FIXTURE);raw[124]=0
        with self.assertRaisesRegex(GarageError,'lossless'):name_ids(bytes(raw))
        with self.assertRaises(GarageError):append_car_names(bytes(raw),40,'New','New',after_id=10)

    def test_invalid_utf8_is_an_actionable_model_error(self):
        raw=bytearray(FIXTURE);raw[96]=255
        with self.assertRaisesRegex(GarageError,'UTF-8'):name_ids(bytes(raw))

    def test_new_names_are_bounded_printable_utf8(self):
        for value in ('','   ','bad\0name','bad\nname','a'*(MAX_NEW_NAME_BYTES+1),None):
            with self.subTest(value=repr(value)[:40]),self.assertRaises(GarageError):
                append_car_names(FIXTURE,40,value,'New',after_id=10)


if __name__=='__main__':unittest.main()
