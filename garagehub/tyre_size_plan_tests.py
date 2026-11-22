"""Synthetic exact-delta/ownership checks; no native or game sizing claims."""
from __future__ import annotations

from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from . import tyre_size_plan as tyres
from .errors import GarageError
from .specdb import _sha
from .specdb_tests import fixture


def size(width=245, aspect=45, rim=18):
    return {"section_width_mm": width, "aspect_percent": aspect, "rim_diameter_inches": rim}


def select(table="FRONTTIRE", part_id=102, dimensions=None):
    return {"table": table, "part_id": part_id, "size": size() if dimensions is None else dimensions}


class TyreSizePlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.sqlite"
        fixture(self.source)
        with closing(sqlite3.connect(self.source)) as db:
            db.executescript("""
                CREATE TABLE TIRESIZE(Label TEXT,flatness INTEGER,ID INTEGER PRIMARY KEY,
                    LabelID INTEGER,diameter INTEGER,width INTEGER);
                INSERT INTO TIRESIZE VALUES('shared_front',9,12,100,170,47);
                INSERT INTO TIRESIZE VALUES('shared_rear',8,13,200,170,51);
                INSERT INTO TIRESIZE VALUES('special_group',13,14,300,150,41);
                INSERT INTO FRONTTIRE VALUES(108,'supra_snow',108,82,211,14);
                INSERT INTO FRONTTIRE VALUES(109,'supra_racing',109,82,7,12);
                INSERT INTO REARTIRE VALUES(110,'supra_snow_rear',110,82,212,14);
                CREATE TABLE TYPED_ROWS(key,value);
                INSERT INTO TYPED_ROWS VALUES('same',1),('same',1.0),('same',X'00FF'),('same',NULL),('same',1.0);
                CREATE TABLE KEEP_IDS(ID INTEGER PRIMARY KEY AUTOINCREMENT,value TEXT);
                INSERT INTO KEEP_IDS(value) VALUES('unchanged');
                CREATE INDEX keep_index ON UNRELATED(tag);
                CREATE VIEW keep_view AS SELECT * FROM TYPED_ROWS;
            """)
            db.commit()
        self.source_hash = _sha(self.source)

    def tearDown(self):
        self.temporary.cleanup()

    def plan(self, selections=None, car_id=82):
        return tyres.plan_tyre_sizes(self.source, car_id=car_id,
                                    selections=[select()] if selections is None else selections)

    def rows(self, path, sql, values=()):
        with closing(sqlite3.connect(path)) as db:
            return db.execute(sql, values).fetchall()

    def change(self, path, sql, values=()):
        with closing(sqlite3.connect(path)) as db:
            db.execute(sql, values)
            db.commit()

    def build(self, plan=None):
        output = self.root / "candidate.sqlite"
        return output, tyres.build_tyre_sizes(self.source, output, self.plan() if plan is None else plan)

    def cli(self):
        path = Path(__file__).resolve().parents[1] / "tools/chassis_diagnostics.py"
        spec = importlib.util.spec_from_file_location("_tyre_size_test_cli", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def cli_request(self):
        return ["--source-db", str(self.source), "--car-id", "82",
                "--tyre", "FRONTTIRE", "102", "245", "45", "18"]

    def test_cli_explicit_nominal_plan_build_verify_reports_developer_scope(self):
        cli = self.cli()
        plan_path, output = self.root / "cli_plan.json", self.root / "cli_candidate.sqlite"
        common = [*self.cli_request(), "--tyre", "REARTIRE", "103", "245", "45", "18"]
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(cli.main(["plan-tyres", *common, "--output", str(plan_path)]), 0)
        summary = json.loads(stdout.getvalue())
        self.assertTrue(summary["developer_only"])
        self.assertFalse(summary["runtime_verified"])
        self.assertFalse(summary["visual_physical_consistency_established"])
        self.assertEqual(summary["selected_rows"], 2)
        self.assertEqual(summary["inserted_size_rows"], 1)
        self.assertEqual(summary["json_evidence"]["sha256"], _sha(plan_path))
        pinned = ["--plan", str(plan_path), "--plan-sha256", _sha(plan_path)]
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(cli.main(["build-tyres", *common, *pinned, "--database-output", str(output)]), 0)
        summary = json.loads(stdout.getvalue())
        self.assertFalse(summary["player_installable"])
        self.assertFalse(summary["native_assets_changed"])
        self.assertEqual(summary["changed_reference_cells"], 2)
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(cli.main(["verify-tyres", *common, *pinned, "--probe-db", str(output)]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["output_sha256"], _sha(output))
        self.assertEqual(_sha(self.source), self.source_hash)

    def test_cli_requires_pinned_plan_and_exact_selected_request_before_build(self):
        cli = self.cli()
        plan_path, output = self.root / "cli_plan.json", self.root / "cli_candidate.sqlite"
        plan_path.write_text(json.dumps(self.plan()), encoding="utf-8")
        pinned = ["--plan", str(plan_path), "--plan-sha256", _sha(plan_path)]
        changed = self.cli_request(); changed[-3] = "250"
        invalid_size = self.cli_request(); invalid_size[-3] = "243"
        for request, evidence in ((changed, pinned), (invalid_size, pinned),
                                  (self.cli_request(), [*pinned[:-1], "0" * 64])):
            with self.subTest(request=request, evidence=evidence), patch.object(tyres, "build_tyre_sizes") as build:
                with redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(cli.main(["build-tyres", *request, *evidence,
                                               "--database-output", str(output)]), 2)
                self.assertIn("chassis diagnostics:", stderr.getvalue())
                build.assert_not_called()
        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + ".tyres.json").exists())

    def test_cli_report_cannot_create_sidecars_or_overwrite_automatic_tyre_receipt(self):
        cli = self.cli()
        output = self.root / "cli_candidate.sqlite"
        protected = [Path(str(self.source) + suffix) for suffix in ("-wal", "-shm", "-journal")]
        protected.append(Path(str(output) + ".tyres.json"))
        for path in protected:
            args = ["build-tyres", *self.cli_request(), "--plan", str(self.root / "missing_plan.json"),
                    "--plan-sha256", "0" * 64, "--database-output", str(output), "--output", str(path)]
            with self.subTest(path=path), patch.object(cli, "execute") as execute, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(args), 2)
                execute.assert_not_called()
            self.assertFalse(path.exists())
        self.assertFalse(output.exists())
        self.assertEqual(_sha(self.source), self.source_hash)

    def test_cli_checks_exact_decimal_text_before_binary_float_conversion(self):
        cli = self.cli()
        for index, token in ((-3, "245.0000000000000001"), (-1, "18.0000000000000001"),
                             (-2, "45.0000000000000000000000000000000000001"),
                             (-3, "true"), (-1, "NaN"), (-1, "Infinity"), (-1, "1e-1000000")):
            args = self.cli_request(); args[index] = token
            with self.subTest(token=token), patch.object(tyres, "plan_tyre_sizes") as plan, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["plan-tyres", *args]), 2)
                plan.assert_not_called()
        args = self.cli_request(); args[-1] = "17.1"
        plan_path = self.root / "exact_decimal_plan.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["plan-tyres", *args, "--output", str(plan_path)]), 0)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["selected_parts"][0]["target"]["codes"], {"width": 49, "flatness": 9, "diameter": 171})
        self.assertFalse(plan["selected_parts"][0]["target"]["rounding_applied"])

    def test_exact_lattice_uses_decimal_not_tolerance_or_silent_rounding(self):
        result = tyres.normalize_tyre_size(size(235, 45, 17.1))
        self.assertEqual(result["codes"], {"width": 47, "flatness": 9, "diameter": 171})
        self.assertEqual(result["requested"], result["authored"])
        self.assertFalse(result["rounding_applied"])
        self.assertAlmostEqual(result["nominal_outer_diameter_metres"], .64584)
        self.assertFalse(result["rendered_or_loaded_measurement"])
        for request in (size(233), size(aspect=42), size(rim=17.01), size(rim=17.1000000001),
                        size(width=0), size(width=True), size(rim=float("inf")), size(aspect=float("nan"))):
            with self.subTest(request=request), self.assertRaises(GarageError):
                tyres.normalize_tyre_size(request)

    def test_explicit_row_selection_is_required_and_not_a_category_wildcard(self):
        for selection in ([], [select(), select()], [dict(select(), category=0)],
                          [select("TIRESIZE")], [select(part_id=True)], [select(part_id="102")]):
            with self.subTest(selection=selection), self.assertRaises(GarageError):
                self.plan(selection)
        with self.assertRaises(GarageError):
            self.plan(car_id=True)

    def test_shared_sizes_get_private_rows_and_other_cars_remain_unchanged(self):
        plan = self.plan()
        dependencies = plan["size_dependencies"][0]
        self.assertEqual(dependencies["size_row"]["ID"], 12)
        self.assertEqual(dependencies["reference_count"], 4)
        self.assertEqual(dependencies["other_car_reference_count"], 2)
        self.assertEqual(dependencies["other_car_ids"], [2144, 2145])
        output, receipt = self.build(plan)
        new = plan["insertions"][0]
        self.assertEqual(new["ID"], 15)
        self.assertEqual(new["LabelID"], 301)
        self.assertEqual(self.rows(output, "SELECT tiresize FROM FRONTTIRE WHERE ID=102"), [(15,)])
        self.assertEqual(self.rows(output, "SELECT ID,tiresize FROM FRONTTIRE WHERE ID<>102 ORDER BY ID"),
                         self.rows(self.source, "SELECT ID,tiresize FROM FRONTTIRE WHERE ID<>102 ORDER BY ID"))
        self.assertEqual(self.rows(output, "SELECT * FROM TIRESIZE WHERE ID<=14"),
                         self.rows(self.source, "SELECT * FROM TIRESIZE"))
        self.assertEqual(_sha(self.source), self.source_hash)
        self.assertEqual(receipt["validation"]["changed_reference_cells"], 1)
        for flag in ("installable", "product_car", "runtime_verified", "native_assets_changed", "wheel_metadata_changed"):
            self.assertFalse(receipt[flag])
        self.assertEqual(json.loads(Path(str(output) + ".tyres.json").read_text()), receipt)

    def test_equal_targets_share_one_new_row_across_explicit_compound_and_axle_selections(self):
        selections = [select(), select(part_id=109), select("REARTIRE", 103)]
        plan = self.plan(selections)
        self.assertEqual(len(plan["insertions"]), 1)
        output, receipt = self.build(plan)
        self.assertEqual(receipt["validation"]["changed_reference_cells"], 3)
        for table, part_id in (("FRONTTIRE", 102), ("FRONTTIRE", 109), ("REARTIRE", 103)):
            self.assertEqual(self.rows(output, "SELECT tiresize FROM " + table + " WHERE ID=?", (part_id,)), [(15,)])
        self.assertEqual(self.rows(output, "SELECT category,tiresize FROM FRONTTIRE WHERE ID=108"), [(211, 14)])
        self.assertEqual(self.rows(output, "SELECT category,tiresize FROM REARTIRE WHERE ID=110"), [(212, 14)])

    def test_distinct_targets_allocate_distinct_rows_and_selection_order_is_canonical(self):
        selections = [select(), select("REARTIRE", 103, size(275, 40, 19))]
        first, second = self.plan(selections), self.plan(list(reversed(selections)))
        self.assertEqual(first, second)
        self.assertEqual(len(first["insertions"]), 2)
        self.assertNotEqual(first["redirects"][0]["after"], first["redirects"][1]["after"])

    def test_no_op_request_keeps_original_reference_and_exact_database_bytes(self):
        plan = self.plan([select(dimensions=size(235, 45, 17))])
        self.assertTrue(plan["no_op"])
        self.assertEqual(plan["insertions"], [])
        self.assertEqual(plan["redirects"], [])
        output, receipt = self.build(plan)
        self.assertEqual(_sha(output), self.source_hash)
        self.assertTrue(receipt["validation"]["no_op_byte_identical"])

    def test_existing_matching_global_target_is_not_reused_for_changed_owned_rows(self):
        plan = self.plan([select(dimensions=size(205, 65, 15))])
        self.assertEqual(plan["insertions"][0]["ID"], 15)
        self.assertNotEqual(plan["redirects"][0]["after"], 14)

    def test_no_op_selection_is_not_redirected_when_another_selection_has_the_same_target(self):
        plan = self.plan([select(dimensions=size(235, 45, 17)), select("REARTIRE", 103, size(235, 45, 17))])
        self.assertEqual([(r["table"], r["part_id"]) for r in plan["redirects"]], [("REARTIRE", 103)])
        output, _ = self.build(plan)
        self.assertEqual(self.rows(output, "SELECT tiresize FROM FRONTTIRE WHERE ID=102"), [(12,)])
        self.assertEqual(self.rows(output, "SELECT tiresize FROM REARTIRE WHERE ID=103"), [(15,)])

    def test_wrong_owner_and_another_cars_default_reference_are_rejected(self):
        with self.assertRaises(GarageError):
            self.plan([select(part_id=202)])
        self.change(self.source, "UPDATE DEFAULT_PARTS SET FRONTTIRE=102 WHERE ID=200")
        with self.assertRaisesRegex(GarageError, "shared"):
            self.plan()

    def test_special_default_g_reference_is_included_and_cannot_hide_shared_ownership(self):
        self.change(self.source, "ALTER TABLE DEFAULT_PARTS ADD COLUMN FRONTTIRE_G INTEGER")
        self.change(self.source, "UPDATE DEFAULT_PARTS SET FRONTTIRE_G=108 WHERE ID=100")
        plan = self.plan([select(part_id=108)])
        self.assertEqual(plan["selected_parts"][0]["default_references"],
                         [{"default_parts_id": 100, "column": "FRONTTIRE_G", "car_ids": [82]}])
        self.change(self.source, "UPDATE DEFAULT_PARTS SET FRONTTIRE_G=108 WHERE ID=200")
        with self.assertRaises(GarageError):
            self.plan([select(part_id=108)])

    def test_unknown_size_schema_or_dependency_requires_review(self):
        self.change(self.source, "CREATE TABLE OTHER_SIZE_USER(ID INTEGER,tiresize INTEGER)")
        with self.assertRaisesRegex(GarageError, "dependency"):
            self.plan()
        self.change(self.source, "DROP TABLE OTHER_SIZE_USER")
        self.change(self.source, "ALTER TABLE TIRESIZE ADD COLUMN unknown INTEGER")
        with self.assertRaisesRegex(GarageError, "six-column"):
            self.plan()

    def test_missing_size_and_ambiguous_label_ids_cannot_be_cloned(self):
        self.change(self.source, "UPDATE FRONTTIRE SET tiresize=999 WHERE ID=102")
        with self.assertRaises(GarageError):
            self.plan()
        self.change(self.source, "UPDATE FRONTTIRE SET tiresize=12 WHERE ID=102")
        self.change(self.source, "UPDATE TIRESIZE SET LabelID=100 WHERE ID=13")
        with self.assertRaisesRegex(GarageError, "ambiguous"):
            self.plan()

    def test_unknown_foreign_key_dependency_is_not_hidden_by_an_unfamiliar_column_name(self):
        self.change(self.source, "CREATE TABLE OTHER_REFERENCE(ID INTEGER,chosen INTEGER REFERENCES TIRESIZE(ID))")
        with self.assertRaisesRegex(GarageError, "declared TIRESIZE dependency"):
            self.plan()

    def test_outgoing_size_foreign_key_requires_review_before_allocating_new_label_id(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.executescript("""
                CREATE TABLE KNOWN_LABELS(ID INTEGER PRIMARY KEY);
                INSERT INTO KNOWN_LABELS VALUES(100),(200),(300);
                CREATE TABLE new_size(Label TEXT,flatness INTEGER,ID INTEGER PRIMARY KEY,
                    LabelID INTEGER REFERENCES KNOWN_LABELS(ID),diameter INTEGER,width INTEGER);
                INSERT INTO new_size SELECT * FROM TIRESIZE;
                DROP TABLE TIRESIZE;
                ALTER TABLE new_size RENAME TO TIRESIZE;
            """)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            db.commit()
        for dimensions in (size(), size(235, 45, 17)):
            with self.subTest(dimensions=dimensions), self.assertRaisesRegex(GarageError, "outgoing foreign key"):
                self.plan([select(dimensions=dimensions)])
        self.assertFalse((self.root / "candidate.sqlite").exists())

    def test_generated_label_collision_allocates_unique_label_without_touching_original(self):
        self.change(self.source, "UPDATE TIRESIZE SET Label='gh_tyre_82_15' WHERE ID=14")
        plan = self.plan()
        self.assertEqual(plan["insertions"][0]["Label"], "gh_tyre_82_15_1")
        output, _ = self.build(plan)
        self.assertEqual(self.rows(output, "SELECT Label FROM TIRESIZE WHERE ID=14"), [("gh_tyre_82_15",)])

    def test_independent_verification_preserves_types_duplicates_all_options_and_visual_metadata(self):
        plan = self.plan()
        output, _ = self.build(plan)
        verification = tyres.verify_tyre_sizes(self.source, output, json.loads(json.dumps(plan)))
        self.assertTrue(verification["verified"])
        for table in ("WHEEL", "WHEEL_SP", "CHASSIS", "DEFAULT_PARTS", "ENGINE", "TYPED_ROWS", "sqlite_sequence"):
            self.assertEqual(self.rows(output, "SELECT * FROM " + table), self.rows(self.source, "SELECT * FROM " + table))
        self.assertIn("sqlite_sequence", verification["validation"]["row_counts"])
        self.change(output, "UPDATE TYPED_ROWS SET value=1 WHERE typeof(value)='real'")
        with self.assertRaisesRegex(GarageError, "TYPED_ROWS"):
            tyres.verify_tyre_sizes(self.source, output, plan)

    def test_independent_verifier_rejects_changed_global_row_or_unselected_option(self):
        plan = self.plan()
        output, _ = self.build(plan)
        self.change(output, "UPDATE TIRESIZE SET width=49 WHERE ID=12")
        with self.assertRaisesRegex(GarageError, "TIRESIZE"):
            tyres.verify_tyre_sizes(self.source, output, plan)
        self.change(output, "UPDATE TIRESIZE SET width=47 WHERE ID=12")
        self.change(output, "UPDATE FRONTTIRE SET tiresize=15 WHERE ID=108")
        with self.assertRaisesRegex(GarageError, "FRONTTIRE"):
            tyres.verify_tyre_sizes(self.source, output, plan)

    def test_stale_source_and_tampered_plan_fail_before_output_publication(self):
        plan = self.plan()
        altered = deepcopy(plan)
        altered["redirects"][0]["part_id"] = 202
        with self.assertRaises(GarageError):
            self.build(altered)
        self.change(self.source, "UPDATE UNRELATED SET tag='new' WHERE tag='keep'")
        with self.assertRaises(GarageError):
            self.build(plan)
        self.assertFalse((self.root / "candidate.sqlite").exists())
        self.assertFalse((self.root / "candidate.sqlite.tyres.json").exists())
        self.assertEqual(list(self.root.glob(".tyre-size-*")), [])

    def test_existing_output_or_receipt_is_not_clobbered(self):
        output = self.root / "candidate.sqlite"
        receipt = Path(str(output) + ".tyres.json")
        receipt.write_bytes(b"keep receipt")
        with self.assertRaises(GarageError):
            self.build()
        self.assertFalse(output.exists())
        self.assertEqual(receipt.read_bytes(), b"keep receipt")
        receipt.unlink()
        output.write_bytes(b"keep output")
        with self.assertRaises(GarageError):
            self.build()
        self.assertEqual(output.read_bytes(), b"keep output")

    def test_closed_snapshot_and_output_sidecar_rules_are_reused(self):
        plan = self.plan()
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.source) + suffix)
            with self.subTest(suffix=suffix), self.assertRaises(GarageError):
                tyres.build_tyre_sizes(self.source, sidecar, plan)
            self.assertFalse(sidecar.exists())
            sidecar.write_bytes(b"pending")
            with self.assertRaises(GarageError):
                self.plan()
            sidecar.unlink()
        self.assertEqual(_sha(self.source), self.source_hash)


if __name__ == "__main__":
    unittest.main()
