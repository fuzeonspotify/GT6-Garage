"""Portable regression tests for private chassis-session recovery boundaries.

Only function definitions are loaded from the Windows command-line helper.
The command-line bootstrap, process APIs, launcher, input and game are never
executed. Filesystem recovery uses a fresh temporary directory for each test.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import ctypes
from ctypes import wintypes as T
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "chassis_probe_session.py"


def load_functions():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))
    definitions = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)], type_ignores=[])
    namespace = dict(Path=Path, argparse=argparse, contextlib=contextlib,
                     ctypes=ctypes, T=T,
                     datetime=datetime, hashlib=hashlib, io=io, json=json,
                     os=os, shutil=shutil, subprocess=subprocess, sys=sys, uuid=uuid)
    exec(compile(definitions, str(SCRIPT), "exec"), namespace)
    return namespace


def digest(data):
    return hashlib.sha256(data).hexdigest()


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def put_json(path, data):
    put(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


class ChassisSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="chassis-session-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case = self.root / "case"
        self.run = self.root / "hub" / "autorun_runs" / "test"
        self.trial = self.run / "runtime_candidate"
        self.trial.mkdir(parents=True)
        self.engine = self.case / "runtime" / "rpcs3.exe"
        put(self.engine, b"inert test executable; never run")
        self.target = self.case / "title/BCES01893/USRDIR/PDIPFS"
        self.home = self.case / "runtime/portable/dev_hdd0/home/00000001"
        self.baseline_bytes = {"A/B": b"retained native payload", "K/4D": b"approved header"}
        self.candidate_bytes = {**self.baseline_bytes, "K/4D": b"diagnostic header", "C/D": b"new database payload"}
        self.approved = digest(self.baseline_bytes["K/4D"])
        self.current = digest(self.candidate_bytes["K/4D"])
        self.baseline = self.variant("approved", self.baseline_bytes)
        self.candidate = self.variant("diagnostic", self.candidate_bytes)
        self.config = {
            "engine_sha256": digest(self.engine.read_bytes()),
            "baseline": self.baseline,
            "candidate": self.candidate,
            "identity": {"car_id": 82, "model_path": "car/0082/0000", "title_id": "BCES01893"},
            "comparison_manifest_sha256": "a" * 64,
        }
        self.ns = load_functions()
        self.ns.update(CASE=self.case, ENGINE=self.engine, RUN=self.run,
                       HUB=self.run.parent.parent, CONFIG=self.config,
                       SESSION={"owner": "test-owner"}, CONFIG_SHA256="b" * 64,
                       APPROVED=self.approved, CURRENT=self.current)
        self.ns["stopped"] = Mock()
        self.ns["lease"] = Mock()
        self.ns["verify_setup"] = Mock(return_value={"configuration": "unchanged"})
        self.ns["control_preflight"] = Mock(return_value={
            "takeover_latched": False, "held_keys_present": False, "controller_active": False})
        self.ns["processes"] = Mock(return_value=[{"ProcessId": 20748, "ExecutablePath": str(self.root / "unrelated/rpcs3.exe")}])
        put_json(self.case / "isolation.json", {
            "runtime": str(self.engine.parent), "title": str(self.target.parent.parent),
            "executable_sha256": self.config["engine_sha256"],
        })
        put_json(self.case / "active_package.json", self.receipt(self.baseline))
        put_json(self.trial / "active_package_before.json", self.receipt(self.baseline))
        put(self.case / "profiles/SharedControlPurchaseSafe.yml", b"inert profile\n")
        put_json(self.run.parent.parent / "AUTORUN_STATE.json", {"current_worker": "test-owner"})
        for rel, data in self.candidate_bytes.items():
            put(self.target / rel, data)
        for rel, data in self.baseline_bytes.items():
            put(self.trial / "package_backup/before" / rel, data)
        self.journal = {
            "state": "installed", "target": str(self.target), "expected_header": self.approved,
            "entries": [
                {"path": rel, "existed": rel in self.baseline_bytes,
                 "sha256": digest(self.baseline_bytes[rel]) if rel in self.baseline_bytes else None}
                for rel in self.candidate_bytes
            ],
        }
        self.journal_path = self.trial / "package_backup/journal.json"
        put_json(self.journal_path, self.journal)
        put(self.home / "save.dat", b"progress during private test")
        put(self.trial / "private_save_before/save.dat", b"approved saved state")
        self.saved = self.ns["home_manifest"](self.trial / "private_save_before")
        self.state = {
            "stage": "candidate", "target": str(self.target), "test_header": self.current,
            "install_requested": True, "installed": False, "restored": False,
            "save_start_hashes": self.saved,
        }
        put_json(self.trial / "state.json", self.state)

    def variant(self, name, contents):
        package = self.root / name
        result = {
            "candidate_id": name, "header_sha256": digest(contents["K/4D"]),
            "package": str(package),
            "physical_files": {rel: {"sha256": digest(data), "bytes": len(data)} for rel, data in contents.items()},
        }
        put_json(package / "package_receipt.json", self.receipt(result))
        return result

    @staticmethod
    def receipt(variant):
        return {
            "passed": True, "source_roundtrip_match": True,
            "candidate_id": variant["candidate_id"], "header_sha256": variant["header_sha256"],
            "files": [{"relative_path": rel, **row} for rel, row in variant["physical_files"].items()],
        }

    def restore(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.ns["restore"](self.trial, self.state)

    def current_payloads(self):
        return {p.relative_to(self.target).as_posix(): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}

    def hub_record(self, worker):
        path = self.run.parent.parent / "AUTORUN_STATE.json"
        record = {"current_worker": worker, "retained_metadata": {"keep": True}}
        put_json(path, record)
        return path, record

    def baseline_ready(self):
        for path in self.target.rglob("*"):
            if path.is_file():
                path.unlink()
        for rel, data in self.baseline_bytes.items():
            put(self.target / rel, data)
        put_json(self.case / "active_package.json", self.receipt(self.baseline))

    def test_hub_owned_state_accepts_both_formats_without_rewriting_metadata(self):
        for worker in ("test-owner", {"owner": "test-owner", "run": str(self.run)}):
            with self.subTest(worker=worker):
                path, record = self.hub_record(worker)
                before = path.read_bytes()
                self.assertEqual(self.ns["hub_owned_state"](), record)
                self.assertEqual(path.read_bytes(), before)

    def test_hub_owned_state_rejects_wrong_owner_run_and_missing_run(self):
        invalid_workers = (
            "another-owner",
            {"owner": "another-owner", "run": str(self.run)},
            {"owner": "test-owner", "run": str(self.run.parent / "another-run")},
            {"owner": "test-owner"},
            {"owner": "test-owner", "run": "relative/run"},
            {"owner": "test-owner", "run": str(self.run / ".." / self.run.name)},
            {"owner": "test-owner", "run": None},
            {"run": str(self.run)},
            None,
            True,
        )
        for worker in invalid_workers:
            with self.subTest(worker=worker):
                path, _ = self.hub_record(worker)
                before = path.read_bytes()
                with self.assertRaisesRegex(AssertionError, "ownership"):
                    self.ns["hub_owned_state"]()
                self.assertEqual(path.read_bytes(), before)

    def test_preflight_rejects_unowned_state_before_validation_or_external_calls(self):
        self.hub_record({"owner": "test-owner", "run": str(self.run.parent / "another-run")})
        self.ns["package_check"] = Mock()
        with patch.object(subprocess, "run") as external, self.assertRaisesRegex(AssertionError, "ownership"):
            self.ns["preflight"]()
        external.assert_not_called()
        self.ns["stopped"].assert_not_called()
        self.ns["verify_setup"].assert_not_called()
        self.ns["package_check"].assert_not_called()
        self.ns["control_preflight"].assert_not_called()
        self.assertFalse((self.run / "runtime_preflight.json").exists())

    def test_preflight_accepts_both_owned_formats_without_launching(self):
        self.baseline_ready()
        for worker in ("test-owner", {"owner": "test-owner", "run": str(self.run)}):
            with self.subTest(worker=worker):
                path, record = self.hub_record(worker)
                with patch.object(subprocess, "run") as external, contextlib.redirect_stdout(io.StringIO()):
                    result = self.ns["preflight"]()
                external.assert_not_called()
                self.assertTrue(result["ready"])
                self.assertFalse(result["game_launched"])
                self.assertEqual(json.loads(path.read_text()), record)

    def test_launch_rejects_unowned_state_before_trial_or_launcher(self):
        self.hub_record({"owner": "test-owner"})
        new_trial = self.run / "unstarted_baseline"
        self.ns["install_files"] = Mock()
        with patch.object(subprocess, "run") as external, self.assertRaisesRegex(AssertionError, "ownership"):
            self.ns["launch"](new_trial, "baseline")
        external.assert_not_called()
        self.ns["install_files"].assert_not_called()
        self.ns["stopped"].assert_not_called()
        self.ns["verify_setup"].assert_not_called()
        self.assertFalse(new_trial.exists())

    def test_launch_rechecks_ownership_before_requesting_external_launch(self):
        self.baseline_ready()
        self.hub_record({"owner": "test-owner", "run": str(self.run)})
        new_trial = self.run / "ownership_changed_before_launch"

        def change_owner_during_validation():
            self.hub_record({"owner": "another-owner", "run": str(self.run)})
            return {"configuration": "unchanged"}

        self.ns["verify_setup"].side_effect = change_owner_during_validation
        with patch.object(subprocess, "run") as external, self.assertRaisesRegex(AssertionError, "ownership"):
            self.ns["launch"](new_trial, "baseline")
        external.assert_not_called()
        self.assertFalse(json.loads((new_trial / "state.json").read_text())["launch_requested"])
        self.assertEqual(self.current_payloads(), self.baseline_bytes)

    def test_successful_mock_launch_updates_hub_for_both_owner_formats(self):
        self.baseline_ready()
        for ordinal, worker in enumerate(("test-owner", {"owner": "test-owner", "run": str(self.run)})):
            with self.subTest(worker=worker):
                hub_path, record = self.hub_record(worker)
                new_trial = self.run / ("mock_baseline_" + str(ordinal))
                process = {"pid": 4242, "path": str(self.engine), "created_filetime": 134080740000000000}
                self.ns["identity"] = Mock(return_value=process)

                def fake_launcher(argv, **kwargs):
                    self.assertIn(str(self.case / "Start-Case.ps1"), argv)
                    put_json(self.case / "last_launch.json", {
                        "ProcessId": process["pid"], "Executable": str(self.engine),
                        "HeaderSHA256": self.approved, "StartedUtc": self.ns["now"](),
                    })
                    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

                with patch.object(subprocess, "run", side_effect=fake_launcher) as external, contextlib.redirect_stdout(io.StringIO()):
                    self.ns["launch"](new_trial, "baseline")
                self.assertEqual(external.call_count, 1)
                actual = json.loads(hub_path.read_text())
                self.assertEqual(actual["current_worker"], record["current_worker"])
                self.assertEqual(actual["retained_metadata"], record["retained_metadata"])
                self.assertEqual(actual["game_launch_count_this_run"], 1)
                self.assertTrue(actual["private_runtime_running"])
                self.assertEqual(actual["latest_runtime_attempt"], str(new_trial / "state.json"))
                state = json.loads((new_trial / "state.json").read_text())
                self.assertEqual(state["process"], process)
                self.assertEqual(state["launch_count"], 1)
                self.assertFalse(state["automatic_relaunch"])

    def test_restore_reconciles_successful_install_before_active_receipt_or_installed_flag(self):
        self.assertFalse(self.state["installed"])
        self.restore()
        self.assertEqual(self.current_payloads(), self.baseline_bytes)
        self.assertTrue(self.state["restored"])
        self.assertTrue(self.state["package_restored"])
        self.assertEqual(self.ns["home_manifest"](self.home), self.saved)
        self.assertEqual((self.trial / "private_save_after/save.dat").read_bytes(), b"progress during private test")

    def test_restore_rejects_foreign_current_payload_before_any_mutation(self):
        put(self.target / "C/D", b"another writer owns this")
        before = self.current_payloads()
        with self.assertRaisesRegex(AssertionError, "another writer"):
            self.restore()
        self.assertEqual(self.current_payloads(), before)
        self.assertEqual((self.home / "save.dat").read_bytes(), b"progress during private test")
        self.assertFalse(self.state["restored"])

    def test_restore_rejects_corrupt_backup_before_any_mutation(self):
        put(self.trial / "package_backup/before/A/B", b"corrupt backup")
        before = self.current_payloads()
        with self.assertRaises(AssertionError):
            self.restore()
        self.assertEqual(self.current_payloads(), before)

    def test_restore_rejects_wrong_journal_target(self):
        self.journal["target"] = str(self.root / "unrelated-title")
        put_json(self.journal_path, self.journal)
        before = self.current_payloads()
        with self.assertRaises(AssertionError):
            self.restore()
        self.assertEqual(self.current_payloads(), before)

    def test_restore_rejects_incomplete_journal_before_any_mutation(self):
        self.journal["entries"] = [entry for entry in self.journal["entries"] if entry["path"] != "C/D"]
        put_json(self.journal_path, self.journal)
        before = self.current_payloads()
        with self.assertRaises(AssertionError):
            self.restore()
        self.assertEqual(self.current_payloads(), before)
        self.assertFalse(self.state["restored"])

    def test_restore_rejects_redirected_payload_parent(self):
        outside = self.root / "outside-the-private-title"
        outside.mkdir()
        shutil.move(str(self.target / "A/B"), outside / "B")
        (self.target / "A").rmdir()
        redirected = self.target / "A"
        try:
            redirected.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as symbolic_link_error:
            if os.name != "nt":
                self.skipTest("This host cannot create directory symbolic links")
            # Windows directory junctions exercise the same reparse-point guard
            # without requiring the symbolic-link privilege. Both endpoints are
            # inside this test's owned temporary directory.
            try:
                result = subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(redirected), str(outside)],
                    capture_output=True, text=True, timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired) as junction_error:
                self.skipTest("This host cannot create a directory link: " + str(junction_error))
            if result.returncode != 0:
                self.skipTest("Neither directory symlink nor junction is available: "
                              + str(symbolic_link_error) + "; " + (result.stderr or result.stdout).strip())
            self.assertTrue(redirected.exists(), "The junction command did not create its test link")
            # Remove the junction itself before TemporaryDirectory cleanup.
            # os.rmdir does not remove the directory at its target.
            self.addCleanup(lambda: os.rmdir(redirected) if os.path.lexists(redirected) else None)
        original = (outside / "B").read_bytes()
        with self.assertRaises((AssertionError, RuntimeError, ValueError)):
            self.restore()
        self.assertEqual((outside / "B").read_bytes(), original)
        self.assertEqual((self.target / "K/4D").read_bytes(), self.candidate_bytes["K/4D"])
        self.assertFalse(self.state["restored"])

    def test_takeover_restores_package_but_retains_current_save_and_snapshot(self):
        put_json(self.trial / "control/USER_CONTROL_ACTIVE.json", {"user_takeover": True})
        with self.assertRaisesRegex(RuntimeError, "Physical user takeover"):
            self.restore()
        self.assertEqual(self.current_payloads(), self.baseline_bytes)
        self.assertEqual((self.home / "save.dat").read_bytes(), b"progress during private test")
        self.assertEqual(self.ns["home_manifest"](self.trial / "private_save_before"), self.saved)
        self.assertTrue(self.state["package_restored"])
        self.assertTrue(self.state["save_restore_pending"])
        self.assertFalse(self.state["restored"])

    def test_header_is_restored_after_all_other_existing_payloads(self):
        calls = []
        original_replace = os.replace

        def record(source, destination):
            if self.target in Path(destination).parents:
                calls.append(Path(destination).relative_to(self.target).as_posix())
            return original_replace(source, destination)

        with patch.object(os, "replace", record):
            self.restore()
        self.assertEqual(calls, ["A/B", "K/4D"])

    def recovery_fixture(self):
        requested = datetime.datetime(2026, 11, 19, 1, 0, tzinfo=datetime.timezone.utc)
        started = requested + datetime.timedelta(seconds=2)
        process = {"pid": 4242, "path": str(self.engine),
                   "created_filetime": int(started.timestamp() * 10000000) + 116444736000000000}
        launch = {"ProcessId": process["pid"], "Executable": str(self.engine),
                  "HeaderSHA256": self.current, "StartedUtc": started.isoformat()}
        put_json(self.case / "last_launch.json", launch)
        self.state.update(launch_requested=True, launch_requested_utc=requested.isoformat(),
                          launch_count=0, other_processes=self.ns["processes"]())
        self.ns["identity"] = Mock(return_value=process)
        return launch, process

    def test_recover_launch_records_exact_new_process_without_launching(self):
        _, expected = self.recovery_fixture()
        with patch.object(subprocess, "run", side_effect=AssertionError("No launcher or process mutation is permitted")):
            result = self.ns["recover_launch"](self.trial, self.state)
        self.assertEqual(result, expected)
        self.assertEqual(self.state["launch_count"], 1)
        self.assertTrue(self.state["launch_record_recovered"])
        self.assertEqual(json.loads((self.trial / "state.json").read_text())["process"], expected)

    def test_recover_launch_rejects_preexisting_protected_pid(self):
        launch, process = self.recovery_fixture()
        launch["ProcessId"] = process["pid"] = 20748
        put_json(self.case / "last_launch.json", launch)
        with self.assertRaises(AssertionError):
            self.ns["recover_launch"](self.trial, self.state)
        self.ns["identity"].assert_not_called()
        self.assertNotIn("process", self.state)

    def test_recover_launch_rejects_stale_request_wrong_image_header_and_creation(self):
        for fault in ("stale", "image", "header", "creation"):
            with self.subTest(fault=fault):
                launch, process = self.recovery_fixture()
                if fault == "stale":
                    launch["StartedUtc"] = "2026-11-19T01:01:01+00:00"
                elif fault == "image":
                    launch["Executable"] = str(self.root / "unrelated/rpcs3.exe")
                elif fault == "header":
                    launch["HeaderSHA256"] = self.approved
                else:
                    process["created_filetime"] -= 31 * 10000000
                put_json(self.case / "last_launch.json", launch)
                with self.assertRaises(AssertionError):
                    self.ns["recover_launch"](self.trial, self.state)
                self.assertNotIn("process", self.state)

    def test_owned_rejects_pid_reuse_before_any_close_or_input(self):
        _, process = self.recovery_fixture()
        self.state["process"] = dict(process)
        self.ns["identity"].return_value = {**process, "created_filetime": process["created_filetime"] + 1}
        with self.assertRaisesRegex(AssertionError, "ownership changed"):
            self.ns["owned"](self.state)

    def test_stopped_does_not_adopt_or_close_unrelated_runtime(self):
        namespace = load_functions()
        namespace.update(ENGINE=self.engine, processes=self.ns["processes"])
        with patch.object(subprocess, "run", side_effect=AssertionError("No process mutation is permitted")):
            namespace["stopped"]()
            namespace["processes"].return_value.append({"ProcessId": 4242, "ExecutablePath": str(self.engine)})
            with self.assertRaisesRegex(AssertionError, "still running"):
                namespace["stopped"]()

    def control_fixture(self, foreground=100):
        from PIL import Image
        _, process = self.recovery_fixture()
        self.state["process"] = dict(process)
        self.snapshot = {
            "hwnd": 100, "process": dict(process), "title": "FPS: 59 | GT6 [BCES01893]",
            "client_rect": [0, 0, 80, 60], "client_screen_rect": [100, 100, 180, 160],
            "window_screen_rect": [99, 97, 181, 161],
            "foreground_hwnd": foreground,
        }
        self.ns["window_snapshot"] = Mock(return_value=self.snapshot)
        self.ns["grab_client_image"] = Mock(return_value=Image.new("RGB", (80, 60), (12, 80, 30)))
        self.ns["grab_window_paint"] = Mock(return_value=(
            Image.new("RGB", (82, 64), (255, 255, 255)),
            {"printwindow_return": True, "window_rect": [99, 97, 181, 161]},
        ))
        self.ctl = SimpleNamespace(
            LATCH=self.trial / "control/USER_CONTROL_ACTIVE.json",
            u=SimpleNamespace(SetForegroundWindow=Mock(return_value=False)),
            input_state=Mock(return_value={"held_keys": (), "idle_ms": 1000}),
            active=Mock(return_value=False), changed=Mock(return_value=None),
            take_over=Mock(),
        )
        self.shared_latch = self.case / "input_control_v001/USER_CONTROL_ACTIVE.json"
        return self.ctl

    def foreground_request(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ns["request_foreground"](self.trial, self.state, self.ctl, 100, self.shared_latch)

    def capture(self, name="reference"):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ns["capture_owned_window"](
                self.trial, self.state, argparse.Namespace(name=name), self.ctl, 100)

    def observation_args(self, capture, observed_state="stationary_side", camera="same-side"):
        return argparse.Namespace(
            capture_sha256=capture["sha256"], capture_event=capture["event_id"],
            observed_state=observed_state, body_visible="yes", rims_visible="yes",
            note="Controlled fixture observation, not a game test", setup_confirmed="yes", camera_key=camera,
        )

    def observe(self, capture, observed_state="stationary_side", camera="same-side"):
        with contextlib.redirect_stdout(io.StringIO()):
            self.ns["observe_capture"](self.trial, self.state, self.observation_args(capture, observed_state, camera))

    def test_foreground_refusal_makes_one_owned_request_without_fallback(self):
        self.control_fixture(foreground=200)
        result = self.foreground_request()
        self.ctl.u.SetForegroundWindow.assert_called_once_with(100)
        self.assertFalse(result["activated"])
        self.assertEqual(result["reason"], "ordinary_activation_refused")
        self.assertFalse(result["focus_forced"])
        self.assertFalse(result["input_sent"])

    def test_foreground_success_requires_observed_owned_foreground(self):
        self.control_fixture(foreground=200)
        self.ctl.u.SetForegroundWindow.return_value = True
        self.ns["window_snapshot"].side_effect = [self.snapshot, self.snapshot, {**self.snapshot, "foreground_hwnd": 100}]
        result = self.foreground_request()
        self.assertTrue(result["activated"])
        self.assertEqual(result["foreground_after"], 100)
        self.ctl.u.SetForegroundWindow.assert_called_once_with(100)

    def test_foreground_already_active_needs_no_activation_call(self):
        self.control_fixture()
        result = self.foreground_request()
        self.assertTrue(result["activated"])
        self.assertFalse(result["request_made"])
        self.ctl.u.SetForegroundWindow.assert_not_called()

    def test_foreground_respects_both_takeover_latches_and_physical_input(self):
        for blocker in ("local", "shared", "key", "controller", "change"):
            with self.subTest(blocker=blocker):
                self.control_fixture(foreground=200)
                for path in (self.ctl.LATCH, self.shared_latch):
                    if path.exists():
                        path.unlink()
                if blocker in ("local", "shared"):
                    put_json(self.ctl.LATCH if blocker == "local" else self.shared_latch, {"takeover": True})
                elif blocker == "key":
                    self.ctl.input_state.return_value = {"held_keys": (65,)}
                elif blocker == "controller":
                    self.ctl.active.return_value = True
                else:
                    self.ctl.changed.return_value = "keyboard_or_mouse"
                result = self.foreground_request()
                self.assertFalse(result["activated"])
                self.assertFalse(result["request_made"])
                self.ctl.u.SetForegroundWindow.assert_not_called()

    def test_foreground_rechecks_project_ownership_before_activation(self):
        self.control_fixture(foreground=200)
        self.ns["hub_owned_state"] = Mock(side_effect=[{}, AssertionError("ownership changed")])
        with self.assertRaisesRegex(AssertionError, "ownership"):
            self.foreground_request()
        self.ctl.u.SetForegroundWindow.assert_not_called()

    def test_foreground_change_during_preflight_blocks_activation(self):
        self.control_fixture(foreground=200)
        self.ns["window_snapshot"].side_effect = [self.snapshot, {**self.snapshot, "foreground_hwnd": 300}]
        result = self.foreground_request()
        self.assertEqual(result["reason"], "window_or_focus_changed_before_request")
        self.ctl.u.SetForegroundWindow.assert_not_called()

    def test_foreground_user_activity_after_request_latches_takeover(self):
        self.control_fixture(foreground=200)
        self.ctl.changed.side_effect = [None, "controller"]
        self.ns["window_snapshot"].side_effect = [self.snapshot, self.snapshot, {**self.snapshot, "foreground_hwnd": 100}]
        result = self.foreground_request()
        self.assertFalse(result["activated"])
        self.ctl.take_over.assert_called_once_with("controller")
        self.ctl.u.SetForegroundWindow.assert_called_once_with(100)

    def test_window_snapshot_rejects_wrong_owner_title_and_minimized_window(self):
        _, process = self.recovery_fixture()
        self.state["process"] = dict(process)
        def owner(hwnd, pointer):
            pointer._obj.value = process["pid"]
            return 1
        def rect(hwnd, pointer):
            pointer._obj.left = pointer._obj.top = 0
            pointer._obj.right = 1280
            pointer._obj.bottom = 720
            return 1
        def position(hwnd, pointer):
            pointer._obj.x = 50
            pointer._obj.y = 100
            return 1
        ctl = SimpleNamespace(u=SimpleNamespace(
            IsWindowVisible=Mock(return_value=True), IsIconic=Mock(return_value=False),
            GetWindowThreadProcessId=Mock(side_effect=owner), GetClientRect=Mock(side_effect=rect),
            GetWindowRect=Mock(side_effect=rect),
            ClientToScreen=Mock(side_effect=position), GetForegroundWindow=Mock(return_value=100),
        ), title=Mock(return_value="FPS: 59 | GT6 [BCES01893]"))
        result = self.ns["window_snapshot"](ctl, self.state, 100)
        self.assertEqual(result["client_screen_rect"], [50, 100, 1330, 820])
        ctl.title.return_value = "Another emulator dialog"
        with self.assertRaisesRegex(AssertionError, "configured game"):
            self.ns["window_snapshot"](ctl, self.state, 100)
        ctl.title.return_value = "GT6 [BCES01893]"
        ctl.u.IsIconic.return_value = True
        with self.assertRaisesRegex(AssertionError, "minimized"):
            self.ns["window_snapshot"](ctl, self.state, 100)
        ctl.u.IsIconic.return_value = False
        ctl.u.GetWindowThreadProcessId.side_effect = lambda h, p: setattr(p._obj, "value", 20748) or 1
        with self.assertRaisesRegex(AssertionError, "ownership"):
            self.ns["window_snapshot"](ctl, self.state, 100)

    def test_foreground_capture_binds_pixels_geometry_process_and_package(self):
        self.control_fixture()
        row = self.capture()
        self.ns["grab_client_image"].assert_called_once_with([100, 100, 180, 160])
        self.ns["grab_window_paint"].assert_not_called()
        self.assertTrue(row["measurement_eligible"])
        self.assertEqual(row["capture_method"], "foreground_client_pixels")
        self.assertEqual(row["process"], self.state["process"])
        self.assertEqual(row["package_header_sha256"], self.current)
        self.assertEqual(row["sha256"], digest(Path(row["path"]).read_bytes()))
        self.assertEqual(Path(row["path"]).parent, self.trial)
        self.assertTrue(self.ns["measurable_capture"](self.trial, self.state, row))

    def test_capture_discards_focus_geometry_and_dimension_mismatch_before_saving(self):
        from PIL import Image
        for fault in ("focus", "geometry", "dimensions"):
            with self.subTest(fault=fault):
                self.control_fixture()
                after = dict(self.snapshot)
                if fault == "focus":
                    after["foreground_hwnd"] = 20748
                elif fault == "geometry":
                    after["client_screen_rect"] = [200, 100, 280, 160]
                else:
                    self.ns["grab_client_image"].return_value = Image.new("RGB", (81, 60))
                self.ns["window_snapshot"].side_effect = [self.snapshot, after]
                row = self.capture()
                self.assertEqual(row["event_kind"], "capture_discarded")
                self.assertFalse(row["measurement_eligible"])
                self.assertEqual(list(self.trial.glob("*.png")), [])

    def test_capture_does_not_save_when_window_identity_changes(self):
        self.control_fixture()
        self.ns["window_snapshot"].side_effect = [self.snapshot, AssertionError("Window ownership changed")]
        with self.assertRaisesRegex(AssertionError, "ownership"):
            self.capture()
        self.assertEqual(list(self.trial.glob("*.png")), [])

    def test_capture_rejects_path_names_before_capturing(self):
        self.control_fixture()
        for name in ("../outside", "a/b", "", "_", "名", "a" * 50):
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, "capture name"):
                self.capture(name)
        self.ns["grab_client_image"].assert_not_called()
        self.ns["grab_window_paint"].assert_not_called()

    def test_background_printwindow_is_diagnostic_and_cannot_approve_reference(self):
        self.control_fixture(foreground=200)
        row = self.capture()
        self.ns["grab_window_paint"].assert_called_once_with(self.ctl, 100, [99, 97, 181, 161])
        self.ns["grab_client_image"].assert_not_called()
        self.assertFalse(row["measurement_eligible"])
        self.assertEqual(row["capture_method"], "background_printwindow_diagnostic")
        before = (self.trial / "events.jsonl").read_bytes()
        with self.assertRaisesRegex(AssertionError, "eligible owned foreground"):
            self.observe(row)
        self.assertEqual((self.trial / "events.jsonl").read_bytes(), before)
        self.observe(row, "other")
        self.assertFalse(self.state["sequence_completed"])

    def test_background_window_or_focus_change_discards_diagnostic(self):
        self.control_fixture(foreground=200)
        self.ns["window_snapshot"].side_effect = [self.snapshot, {**self.snapshot, "foreground_hwnd": 100}]
        row = self.capture()
        self.assertEqual(row["event_kind"], "capture_discarded")
        self.assertEqual(list(self.trial.glob("*.png")), [])

    def test_failed_background_api_does_not_create_an_image(self):
        self.control_fixture(foreground=200)
        frame, raw = self.ns["grab_window_paint"].return_value
        self.ns["grab_window_paint"].return_value = (frame, {**raw, "printwindow_return": False})
        row = self.capture()
        self.assertEqual(row["reason"], "window_diagnostic_api_refused")
        self.assertEqual(list(self.trial.glob("*.png")), [])

    def test_capture_publication_preserves_collision_and_removes_partial_output(self):
        from PIL import Image
        path = self.trial / "existing.png"
        put(path, b"foreign existing evidence")
        with self.assertRaises(FileExistsError):
            self.ns["save_capture_image"](path, Image.new("RGB", (2, 2)))
        self.assertEqual(path.read_bytes(), b"foreign existing evidence")
        def fail(output, **kwargs):
            output.write(b"partial")
            raise ValueError("encoder failed")
        broken = self.trial / "broken.png"
        with self.assertRaisesRegex(ValueError, "encoder"):
            self.ns["save_capture_image"](broken, SimpleNamespace(save=fail))
        self.assertFalse(broken.exists())

    def test_observation_rejects_changed_image_wrong_process_or_package(self):
        self.control_fixture()
        row = self.capture()
        before = (self.trial / "events.jsonl").read_bytes()
        for fault in ("image", "process", "package"):
            with self.subTest(fault=fault):
                original_image = Path(row["path"]).read_bytes()
                self.state["process"] = dict(self.snapshot["process"])
                self.state["test_header"] = self.current
                if fault == "image":
                    Path(row["path"]).write_bytes(b"different image")
                elif fault == "process":
                    self.state["process"]["created_filetime"] += 1
                else:
                    self.state["test_header"] = self.approved
                with self.assertRaises(AssertionError):
                    self.observe(row, "other")
                self.assertEqual((self.trial / "events.jsonl").read_bytes(), before)
                Path(row["path"]).write_bytes(original_image)

    def make_measurement_sequence(self, changed_camera=False):
        self.control_fixture()
        captures = []
        states = ["garage_reference", "stationary_side", "stationary_side_repeat", "stationary_three_quarter"]
        for observed in states:
            capture = self.capture(observed)
            captures.append(capture)
            camera = "other-side" if changed_camera and observed == "stationary_side_repeat" else "same-side"
            self.observe(capture, observed, camera)
        return captures

    def test_measurement_sequence_requires_eligible_frames_and_same_side_camera(self):
        captures = self.make_measurement_sequence()
        self.assertTrue(self.state["sequence_completed"])
        self.assertFalse(self.state["runtime_relationship_established"])
        self.assertTrue(self.ns["measurement_sequence_complete"](self.trial, self.state))
        Path(captures[0]["path"]).write_bytes(b"changed after review")
        with self.assertRaisesRegex(AssertionError, "Capture image changed"):
            self.ns["measurement_sequence_complete"](self.trial, self.state)

    def test_different_repeat_camera_does_not_complete_measurement_sequence(self):
        self.make_measurement_sequence(changed_camera=True)
        self.assertFalse(self.state["sequence_completed"])

    def test_candidate_launch_rechecks_baseline_capture_evidence_before_side_effects(self):
        self.baseline_ready()
        put_json(self.run / "runtime_baseline/state.json", {
            "restored": True, "save_start_hashes": self.ns["home_manifest"](self.home),
            "configuration_sha256": {"configuration": "unchanged"}, "sequence_completed": True,
        })
        destination = self.run / "unstarted_measurement_candidate"
        self.ns["install_files"] = Mock()
        with patch.object(subprocess, "run") as external, self.assertRaisesRegex(AssertionError, "capture evidence"):
            self.ns["launch"](destination, "candidate")
        external.assert_not_called()
        self.ns["install_files"].assert_not_called()
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
