"""Differential tests for developer-only isolated chassis diagnostics.

Run: python -m unittest garagehub.chassis_probe_tests -v
Synthetic SQLite fixtures prove isolation and exact deltas, not game behaviour.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from . import chassis_probe as probe
from .errors import GarageError
from .specdb_tests import fixture as identity_fixture


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ChassisProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.sqlite"
        identity_fixture(self.source)
        with closing(sqlite3.connect(self.source)) as database:
            database.executescript("""
                ALTER TABLE CHASSIS ADD COLUMN treadF INTEGER DEFAULT 1520;
                ALTER TABLE CHASSIS ADD COLUMN treadR INTEGER DEFAULT 1525;
                ALTER TABLE CHASSIS ADD COLUMN original_rideHeight_F INTEGER DEFAULT 130;
                UPDATE CHASSIS SET wheelbase=2550 WHERE UseCar=82;
                CREATE TABLE TIRESIZE(ID INTEGER,Label TEXT,diameter INTEGER,width INTEGER,flatness INTEGER);
                INSERT INTO TIRESIZE VALUES(12,'shared_front',170,47,9),(13,'shared_rear',170,51,8);
                CREATE TABLE GENERATED_IDS(ID INTEGER PRIMARY KEY AUTOINCREMENT,note TEXT);
                INSERT INTO GENERATED_IDS(note) VALUES('retain sqlite_sequence');
                CREATE TABLE STORAGE_CLASSES(key,value);
                INSERT INTO STORAGE_CLASSES VALUES('integer',1),('real',1.0),('blob',X'0000FF'),('null',NULL);
                CREATE INDEX retain_index ON UNRELATED(tag);
                CREATE VIEW retain_view AS SELECT tag FROM UNRELATED;
            """)
            database.commit()
        self.initial_hash = digest(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, trial="wheelbase_delta_mm", delta_mm=300, car_id=82):
        return probe.plan_chassis_probe(self.source, car_id=car_id, trial=trial, delta_mm=delta_mm)

    def build(self, plan=None, name="trial.sqlite"):
        output = self.root / name
        return output, probe.build_chassis_probe(self.source, output, plan or self.plan())

    def query(self, path, sql, parameters=()):
        with closing(sqlite3.connect(path)) as database:
            return database.execute(sql, parameters).fetchall()

    def change(self, sql, parameters=()):
        with closing(sqlite3.connect(self.source)) as database:
            database.execute(sql, parameters)
            database.commit()

    def assert_no_output(self, name="trial.sqlite"):
        self.assertFalse((self.root / name).exists())
        self.assertFalse((self.root / (name + ".probe.json")).exists())
        self.assertEqual(list(self.root.glob(".chassis-probe-*")), [])

    def cli(self):
        path = Path(__file__).resolve().parents[1] / "tools/chassis_diagnostics.py"
        spec = importlib.util.spec_from_file_location("_chassis_diagnostics_test_cli", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cli_requires_the_pinned_plan_and_matching_explicit_experiment(self):
        cli = self.cli()
        plan_path = self.root / "cli_plan.json"
        output = self.root / "cli_probe.sqlite"
        common = ["--source-db", str(self.source), "--car-id", "82", "--trial", "wheelbase_delta_mm", "--delta-mm", "300"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["plan-db", *common, "--output", str(plan_path)]), 0)
        pinned = ["--plan", str(plan_path), "--plan-sha256", digest(plan_path)]
        bad = list(common)
        bad[-1] = "450"
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["build-db", *bad, *pinned, "--database-output", str(output)]), 2)
        self.assertFalse(output.exists())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["build-db", *common, *pinned, "--database-output", str(output)]), 0)
            self.assertEqual(cli.main(["verify-db", *common, *pinned, "--probe-db", str(output)]), 0)
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_cli_rejects_unreviewed_package_car_before_calling_package_builder(self):
        cli = self.cli()
        args = ["build-package", "--baseline-package", str(self.root / "baseline"),
                "--baseline-receipt-sha256", "a" * 64, "--packer", str(self.root / "packer.exe"),
                "--package-output", str(self.root / "package"), "--car-id", "2144",
                "--trial", "wheelbase_delta_mm", "--delta-mm", "300"]
        with patch("garagehub.chassis_probe_package.build_chassis_probe_package") as build, redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(args), 2)
            build.assert_not_called()
        self.assertFalse((self.root / "package").exists())

    def test_cli_existing_evidence_stops_before_database_build(self):
        cli = self.cli()
        evidence = self.root / "keep.json"
        evidence.write_bytes(b"retain this evidence")
        args = ["build-db", "--source-db", str(self.source), "--plan", str(self.root / "plan.json"),
                "--plan-sha256", "a" * 64, "--database-output", str(self.root / "trial.sqlite"),
                "--car-id", "82", "--trial", "wheelbase_delta_mm", "--delta-mm", "300", "--output", str(evidence)]
        with patch.object(probe, "build_chassis_probe") as build, redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(args), 2)
            build.assert_not_called()
        self.assertEqual(evidence.read_bytes(), b"retain this evidence")
        self.assert_no_output()

    def test_cli_report_cannot_poison_database_snapshot_sidecars(self):
        cli = self.cli()
        for suffix in ("-wal", "-shm", "-journal"):
            path = Path(str(self.source) + suffix)
            args = ["plan-db", "--source-db", str(self.source), "--car-id", "82", "--trial", "baseline",
                    "--delta-mm", "0", "--output", str(path)]
            with self.subTest(suffix=suffix), patch.object(probe, "plan_chassis_probe") as plan, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(args), 2)
                plan.assert_not_called()
            self.assertFalse(path.exists())
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_cli_report_cannot_invalidate_verified_package_or_baseline_tree(self):
        cli = self.cli()
        package = self.root / "diagnostic_package"
        baseline = self.root / "baseline_package"
        (package / "Verified").mkdir(parents=True)
        (baseline / "Input").mkdir(parents=True)
        manifest = package / "chassis_probe_manifest.json"
        manifest.write_text(json.dumps({"baseline": {"path": str(baseline)}}), encoding="utf-8")
        for output in (package / "Verified/review.json", baseline / "Input/review.json"):
            args = ["verify-package", "--manifest", str(manifest), "--manifest-sha256", digest(manifest),
                    "--car-id", "82", "--trial", "wheelbase_delta_mm", "--delta-mm", "300", "--output", str(output)]
            with self.subTest(output=output), patch("garagehub.chassis_probe_package.verify_chassis_probe_manifest") as verify, redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(args), 2)
                verify.assert_not_called()
            self.assertFalse(output.exists())
        self.assertEqual(list((package / "Verified").iterdir()), [])
        self.assertEqual(list((baseline / "Input").iterdir()), [])

    def test_baseline_is_byte_identical_and_receipt_is_deliberately_not_installable(self):
        plan = self.plan(trial="baseline", delta_mm=0)
        output, receipt = self.build(json.loads(json.dumps(plan)))
        self.assertEqual(digest(output), self.initial_hash)
        self.assertEqual(digest(self.source), self.initial_hash)
        self.assertEqual(receipt["validation"]["changed_cell_count"], 0)
        self.assertTrue(receipt["validation"]["baseline_byte_identical"])
        self.assertTrue(receipt["developer_only"])
        for field in ("installable", "product_car", "runtime_verified", "game_launched", "game_files_changed", "native_assets_changed"):
            self.assertFalse(receipt[field])
        self.assertEqual(json.loads(Path(str(output) + ".probe.json").read_text()), receipt)
        self.assertEqual(output.stat().st_nlink, 1)

    def test_wheelbase_probe_changes_exactly_one_cell_and_preserves_every_other_table(self):
        output, receipt = self.build()
        self.assertEqual(self.query(output, "SELECT wheelbase,treadF,treadR,original_rideHeight_F FROM CHASSIS WHERE ID=101"),
                         [(2850, 1520, 1525, 130)])
        with closing(sqlite3.connect(self.source)) as source, closing(sqlite3.connect(output)) as candidate:
            tables = [row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                rows = [list(row) for row in source.execute('SELECT * FROM "' + table + '"')]
                if table == "CHASSIS":
                    columns = [row[1] for row in source.execute('PRAGMA table_info("CHASSIS")')]
                    for row in rows:
                        if row[columns.index("ID")] == 101:
                            row[columns.index("wheelbase")] = 2850
                expected = Counter(probe._row_token(row) for row in rows)
                actual = Counter(probe._row_token(row) for row in candidate.execute('SELECT * FROM "' + table + '"'))
                self.assertEqual(actual, expected, table)
        self.assertEqual(receipt["validation"]["changed_cell_count"], 1)
        self.assertTrue(receipt["validation"]["all_other_rows_and_tables_unchanged"])
        self.assertIn("sqlite_sequence", receipt["validation"]["row_counts"])
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_front_track_is_a_separate_explicit_trial(self):
        output, receipt = self.build(self.plan(trial="tread_front_delta_mm", delta_mm=100))
        self.assertEqual(self.query(output, "SELECT wheelbase,treadF,treadR FROM CHASSIS WHERE ID=101"), [(2550, 1620, 1525)])
        self.assertEqual(receipt["plan"]["allowed_change"]["column"], "treadF")
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_independent_verification_accepts_decoded_copy_without_trusting_receipt(self):
        plan = self.plan()
        output, receipt = self.build(plan)
        Path(str(output) + ".probe.json").write_text('{"verified":false}', encoding="utf-8")
        decoded = self.root / "independently_decoded.sqlite"
        decoded.write_bytes(output.read_bytes())
        result = probe.verify_chassis_probe(self.source, decoded, json.loads(json.dumps(plan)))
        self.assertTrue(result["verified"])
        self.assertEqual(result["output_sha256"], receipt["output"]["sha256"])
        self.assertEqual(result["validation"]["changed_cell_count"], 1)
        self.assertFalse(result["installable"])
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_independent_verifier_rejects_an_extra_output_delta(self):
        plan = self.plan()
        output, _ = self.build(plan)
        with closing(sqlite3.connect(output)) as database:
            database.execute("UPDATE CHASSIS SET treadR=treadR+1 WHERE ID=101")
            database.commit()
        output_hash = digest(output)
        with self.assertRaisesRegex(GarageError, "allowed delta"):
            probe.verify_chassis_probe(self.source, output, plan)
        self.assertEqual(digest(output), output_hash)
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_independent_verifier_rejects_stale_plan_and_source_alias(self):
        plan = self.plan()
        output, _ = self.build(plan)
        with self.assertRaisesRegex(GarageError, "separate"):
            probe.verify_chassis_probe(self.source, self.source, plan)
        self.change("UPDATE UNRELATED SET tag='new snapshot' WHERE tag='keep'")
        after_edit = digest(self.source)
        with self.assertRaisesRegex(GarageError, "stale"):
            probe.verify_chassis_probe(self.source, output, plan)
        self.assertEqual(digest(self.source), after_edit)

    def test_baseline_verification_requires_exact_bytes_even_when_rows_match(self):
        plan = self.plan(trial="baseline", delta_mm=0)
        output, _ = self.build(plan)
        with output.open("ab") as stream:
            stream.write(b"unapproved trailing byte")
        with self.assertRaisesRegex(GarageError, "byte-identical"):
            probe.verify_chassis_probe(self.source, output, plan)
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_arbitrary_selected_car_is_supported_without_donor_whitelist(self):
        output, receipt = self.build(self.plan(car_id=2144, delta_mm=450))
        self.assertEqual(self.query(output, "SELECT wheelbase FROM CHASSIS WHERE UseCar=2144"), [(2950,)])
        self.assertEqual(self.query(output, "SELECT wheelbase FROM CHASSIS WHERE UseCar=82"), [(2550,)])
        self.assertEqual(receipt["plan"]["selection"]["car_id"], 2144)

    def test_source_change_rejects_stale_plan_before_creating_output(self):
        plan = self.plan()
        self.change("UPDATE UNRELATED SET tag='source changed' WHERE tag='keep'")
        after_edit = digest(self.source)
        with self.assertRaisesRegex(GarageError, "stale"):
            self.build(plan)
        self.assert_no_output()
        self.assertEqual(digest(self.source), after_edit)

    def test_exact_plan_fields_cannot_be_tampered_or_boolean_coerced(self):
        original = self.plan()
        mutations = (
            lambda plan: plan["allowed_change"].update(after=3000),
            lambda plan: plan["request"].update(delta_mm=450),
            lambda plan: plan.update(schema_version=True),
            lambda plan: plan.update(installable=True),
            lambda plan: plan["selection"].update(chassis_id=201),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                plan = deepcopy(original)
                mutation(plan)
                with self.assertRaisesRegex(GarageError, "stale"):
                    self.build(plan)
                self.assert_no_output()
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_delta_types_and_experiment_bounds_are_explicit(self):
        for delta in (None, True, 300.0, "300", 0, 501, -501):
            with self.subTest(delta=delta), self.assertRaises(GarageError):
                self.plan(delta_mm=delta)
        for trial, delta in (("baseline", 1), ("baseline", False), ("unknown", 300), ("tread_front_delta_mm", 201)):
            with self.subTest(trial=trial, delta=delta), self.assertRaises(GarageError):
                self.plan(trial=trial, delta_mm=delta)
        for car_id in (True, "82", 82.0, -1, 0xFFFFFFFF):
            with self.subTest(car_id=car_id), self.assertRaises(GarageError):
                self.plan(car_id=car_id)
        self.change("UPDATE CHASSIS SET wheelbase=5900 WHERE ID=101")
        with self.assertRaisesRegex(GarageError, "bounded"):
            self.plan(delta_mm=300)
        self.assert_no_output()

    def test_default_part_must_be_owned_and_not_shared(self):
        self.change("UPDATE CHASSIS SET UseCar=2144 WHERE ID=101")
        with self.assertRaisesRegex(GarageError, "belong"):
            self.plan()
        self.change("UPDATE CHASSIS SET UseCar=82 WHERE ID=101")
        self.change("UPDATE DEFAULT_PARTS SET CHASSIS=101 WHERE ID=200")
        with self.assertRaisesRegex(GarageError, "shared"):
            self.plan()
        self.assert_no_output()

    def test_duplicate_row_identity_and_noninteger_car_alias_are_rejected(self):
        self.change("UPDATE GENERIC_CAR SET CarID='ambiguous' WHERE ID=82")
        with self.assertRaisesRegex(GarageError, "aliases"):
            self.plan()
        self.change("UPDATE GENERIC_CAR SET CarID=82 WHERE ID=82")
        with closing(sqlite3.connect(self.source)) as database:
            database.executescript("""
                ALTER TABLE CHASSIS RENAME TO CHASSIS_OLD;
                CREATE TABLE CHASSIS AS SELECT * FROM CHASSIS_OLD;
                INSERT INTO CHASSIS SELECT * FROM CHASSIS_OLD WHERE ID=101;
                DROP TABLE CHASSIS_OLD;
            """)
            database.commit()
        with self.assertRaisesRegex(GarageError, "one exact"):
            self.plan()
        self.assert_no_output()

    def test_existing_database_and_existing_receipt_are_never_overwritten(self):
        plan = self.plan()
        output = self.root / "trial.sqlite"
        output.write_bytes(b"retain existing database")
        with self.assertRaisesRegex(GarageError, "already exists"):
            self.build(plan)
        self.assertEqual(output.read_bytes(), b"retain existing database")
        output.unlink()
        receipt = Path(str(output) + ".probe.json")
        receipt.write_bytes(b"retain existing receipt")
        with self.assertRaisesRegex(GarageError, "already exists"):
            self.build(plan)
        self.assertFalse(output.exists())
        self.assertEqual(receipt.read_bytes(), b"retain existing receipt")
        with self.assertRaises(GarageError):
            probe.build_chassis_probe(self.source, self.source, plan)
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_input_and_output_link_paths_are_rejected(self):
        source_link = self.root / "linked.sqlite"
        try:
            source_link.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("This host cannot create symbolic links for the path check")
        with self.assertRaisesRegex(GarageError, "links"):
            probe.plan_chassis_probe(source_link, car_id=82, trial="baseline")
        broken_output = self.root / "broken.sqlite"
        broken_output.symlink_to(self.root / "missing.sqlite")
        with self.assertRaisesRegex(GarageError, "links"):
            probe.build_chassis_probe(self.source, broken_output, self.plan())
        linked_parent = self.root / "linked_directory"
        linked_parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(GarageError, "links"):
            probe.build_chassis_probe(self.source, linked_parent / "trial.sqlite", self.plan())
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_hard_linked_source_is_rejected(self):
        link = self.root / "hardlinked.sqlite"
        try:
            os.link(self.source, link)
        except (OSError, NotImplementedError):
            self.skipTest("This host cannot create hard links for the path check")
        with self.assertRaisesRegex(GarageError, "hard-linked"):
            self.plan()
        self.assertEqual(digest(link), self.initial_hash)

    def test_transaction_sidecars_and_wal_header_are_rejected(self):
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.source) + suffix)
            sidecar.write_bytes(b"closed snapshot required")
            with self.subTest(suffix=suffix), self.assertRaisesRegex(GarageError, "sidecars"):
                self.plan()
            sidecar.unlink()
        data = bytearray(self.source.read_bytes())
        data[18:20] = b"\x02\x02"
        self.source.write_bytes(data)
        with self.assertRaisesRegex(GarageError, "WAL mode"):
            self.plan()
        self.assert_no_output()

    def test_triggers_are_rejected_instead_of_executed(self):
        self.change("CREATE TRIGGER unexpected_change AFTER UPDATE ON CHASSIS BEGIN UPDATE UNRELATED SET tag='changed'; END")
        with self.assertRaisesRegex(GarageError, "triggers"):
            self.plan()
        self.assertEqual(self.query(self.source, "SELECT COUNT(*) FROM UNRELATED WHERE tag='changed'"), [(0,)])
        self.assert_no_output()

    def test_extra_data_mutation_rolls_back_and_discards_candidate(self):
        plan = self.plan()
        apply = probe._apply_change

        def corrupt(connection, change):
            apply(connection, change)
            connection.execute("DELETE FROM UNRELATED WHERE rowid=(SELECT MIN(rowid) FROM UNRELATED)")

        with patch.object(probe, "_apply_change", corrupt), self.assertRaisesRegex(GarageError, "allowed delta"):
            self.build(plan)
        self.assert_no_output()
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_extra_schema_mutation_is_rejected(self):
        plan = self.plan()
        apply = probe._apply_change

        def corrupt(connection, change):
            apply(connection, change)
            connection.execute("CREATE INDEX unapproved ON CHASSIS(wheelbase)")

        with patch.object(probe, "_apply_change", corrupt), self.assertRaisesRegex(GarageError, "schema"):
            self.build(plan)
        self.assert_no_output()
        self.assertEqual(digest(self.source), self.initial_hash)

    def test_receipt_publication_race_retains_foreign_file_and_removes_only_owned_output(self):
        plan = self.plan()
        publish = probe._publish_exclusive
        receipt = self.root / "trial.sqlite.probe.json"

        def collide(path, payload, owned):
            if path == receipt:
                path.write_bytes(b"another writer owns this")
            publish(path, payload, owned)

        with patch.object(probe, "_publish_exclusive", collide), self.assertRaisesRegex(GarageError, "already exists"):
            self.build(plan)
        self.assertFalse((self.root / "trial.sqlite").exists())
        self.assertEqual(receipt.read_bytes(), b"another writer owns this")
        self.assertEqual(digest(self.source), self.initial_hash)
        self.assertEqual(list(self.root.glob(".chassis-probe-*")), [])

    def test_independent_source_change_during_publication_discards_owned_outputs(self):
        plan = self.plan()
        publish = probe._publish_exclusive
        independently_changed = self.source.read_bytes() + b"other writer changed source"

        def source_race(path, payload, owned):
            publish(path, payload, owned)
            if path.suffix == ".json":
                self.source.write_bytes(independently_changed)

        with patch.object(probe, "_publish_exclusive", source_race), self.assertRaisesRegex(GarageError, "source changed"):
            self.build(plan)
        self.assert_no_output()
        self.assertEqual(self.source.read_bytes(), independently_changed)


if __name__ == "__main__":
    unittest.main()
