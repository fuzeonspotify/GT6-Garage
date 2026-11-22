"""Explicit developer CLI for offline GT6 chassis diagnostics.

This tool does not install, launch, schedule, edit saves, or drive an emulator.
Build-package and verify-package invoke the pinned packer through their package
APIs; verification independently decodes encrypted databases in temporary work.
Tyre commands author explicit nominal database sizes only. They do not resize
native wheels or establish coherent visual/physical fit.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garagehub import chassis_probe as probe
from garagehub import runtime_comparison as archive
from garagehub.errors import GarageError


def _sha_argument(value):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise argparse.ArgumentTypeError("supply an explicitly recorded 64-digit SHA-256")
    return value.lower()


def _experiment(parser, *, changed_only=False):
    parser.add_argument("--car-id", type=int, required=True, help="Exact selected car ID")
    choices = tuple(probe.TRIALS) if changed_only else ("baseline", *probe.TRIALS)
    parser.add_argument("--trial", choices=choices, required=True, help="One explicit diagnostic trial")
    parser.add_argument("--delta-mm", type=int, required=True, help="Explicit integer delta; baseline requires 0")


def _tyre_selection(parser):
    parser.add_argument("--car-id", type=int, required=True, help="Exact owner car ID")
    parser.add_argument("--tyre", action="append", nargs=5, required=True,
                        metavar=("TABLE", "PART_ID", "WIDTH_MM", "ASPECT_PERCENT", "RIM_INCHES"),
                        help="Explicit FRONTTIRE/REARTIRE row and exact metric size; repeat for each selected row")


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    commands = command.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan-db", help="Pin a read-only closed SQLite experiment plan")
    plan.add_argument("--source-db", type=Path, required=True)
    _experiment(plan)

    build = commands.add_parser("build-db", help="Create an isolated one-field SQLite copy and receipt")
    build.add_argument("--source-db", type=Path, required=True)
    build.add_argument("--plan", type=Path, required=True)
    build.add_argument("--plan-sha256", type=_sha_argument, required=True)
    build.add_argument("--database-output", type=Path, required=True, help="Fresh SQLite output path")
    _experiment(build)

    verify = commands.add_parser("verify-db", help="Recheck every table and the exact allowed delta")
    verify.add_argument("--source-db", type=Path, required=True)
    verify.add_argument("--probe-db", type=Path, required=True)
    verify.add_argument("--plan", type=Path, required=True)
    verify.add_argument("--plan-sha256", type=_sha_argument, required=True)
    _experiment(verify)

    package = commands.add_parser("build-package", help="Build a private DB-only diagnostic package from approved v029")
    package.add_argument("--baseline-package", type=Path, required=True)
    package.add_argument("--baseline-receipt-sha256", type=_sha_argument, required=True)
    package.add_argument("--packer", type=Path, required=True)
    package.add_argument("--package-output", type=Path, required=True, help="Fresh isolated package directory")
    _experiment(package, changed_only=True)

    package_verify = commands.add_parser("verify-package", help="Recheck the pinned diagnostic package proof")
    package_verify.add_argument("--manifest", type=Path, required=True)
    package_verify.add_argument("--manifest-sha256", type=_sha_argument, required=True)
    _experiment(package_verify, changed_only=True)

    wheels = commands.add_parser("audit-wheels", help="Inspect authenticated native HQ/race wheel ownership")
    wheels.add_argument("--hq-wheel", type=Path, required=True)
    wheels.add_argument("--race-wheel", type=Path, required=True)

    tyre_scope = "Developer-only nominal tyre database authoring. Native wheel scaling and in-game fit remain unverified."
    tyre_plan = commands.add_parser("plan-tyres", help="Developer-only: plan private nominal tyre sizes for explicit owned rows",
                                    description=tyre_scope)
    tyre_plan.add_argument("--source-db", type=Path, required=True)
    _tyre_selection(tyre_plan)
    tyre_build = commands.add_parser("build-tyres", help="Developer-only: copy-on-write nominal tyre database; no native wheel changes",
                                     description=tyre_scope)
    tyre_build.add_argument("--source-db", type=Path, required=True)
    tyre_build.add_argument("--plan", type=Path, required=True)
    tyre_build.add_argument("--plan-sha256", type=_sha_argument, required=True)
    tyre_build.add_argument("--database-output", type=Path, required=True, help="Fresh isolated SQLite output path")
    _tyre_selection(tyre_build)
    tyre_verify = commands.add_parser("verify-tyres", help="Developer-only: independently verify every row and exact tyre-reference changes",
                                      description=tyre_scope)
    tyre_verify.add_argument("--source-db", type=Path, required=True)
    tyre_verify.add_argument("--probe-db", type=Path, required=True, help="Independent closed tyre candidate to verify")
    tyre_verify.add_argument("--plan", type=Path, required=True)
    tyre_verify.add_argument("--plan-sha256", type=_sha_argument, required=True)
    _tyre_selection(tyre_verify)
    for subcommand in (plan, build, verify, package, package_verify, wheels, tyre_plan, tyre_build, tyre_verify):
        subcommand.add_argument("--output", type=Path,
                                help="Optional fresh JSON evidence file; existing paths are never overwritten")
    return command


def _request(args):
    return probe._request(args.car_id, args.trial, args.delta_mm)


def _checked_plan(args):
    plan = archive._pinned_json(args.plan, args.plan_sha256)
    if probe._plan_request(plan) != _request(args):
        raise GarageError("The pinned plan does not match the explicit car, trial and delta.", "chassis_diagnostics")
    return plan


def _tyre_dimension(token, *, rim=False):
    from garagehub import tyre_size_plan as tyres
    try:
        value = json.loads(token, parse_int=Decimal, parse_float=Decimal)
    except InvalidOperation as error:
        raise GarageError("Tyre dimension is not representable.", "tyre_size_quantization") from error
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise GarageError("Tyre dimensions must be finite positive JSON numbers.", "chassis_diagnostics")
    # Shift the exponent directly: Decimal arithmetic must not round a long input
    # before its exact integer lattice has been checked.
    sign, digits, exponent = value.as_tuple()
    try:
        scaled = Decimal((sign, digits, exponent + (1 if rim else 0)))
    except InvalidOperation as error:
        raise GarageError("Tyre dimension is not representable.", "tyre_size_quantization") from error
    multiple = 1 if rim else 5
    if scaled != scaled.to_integral_value() or not 0 < scaled <= tyres.MAX_INTEGER * multiple or int(scaled) % multiple:
        raise GarageError("Tyre dimensions must be exact multiples of 5 mm, 5 percent and 0.1 rim inch; no rounding is applied.",
                          "tyre_size_quantization")
    return int(scaled) / 10 if rim else int(scaled)


def _tyre_request(args):
    from garagehub import tyre_size_plan as tyres
    selections = [{"table": table, "part_id": json.loads(part),
                   "size": {"section_width_mm": _tyre_dimension(width), "aspect_percent": _tyre_dimension(aspect),
                            "rim_diameter_inches": _tyre_dimension(rim, rim=True)}}
                  for table, part, width, aspect, rim in args.tyre]
    return tyres._request(args.car_id, selections)


def _checked_tyre_plan(args):
    from garagehub import tyre_size_plan as tyres
    plan = archive._pinned_json(args.plan, args.plan_sha256)
    if tyres._plan_request(plan) != _tyre_request(args):
        raise GarageError("The pinned tyre plan does not match the explicit car, selected rows and exact sizes.",
                          "chassis_diagnostics")
    return plan


def _check_report_path(args):
    if args.output is None:
        return None
    path = probe._fresh_path(args.output)
    for name in ("source_db", "probe_db", "database_output"):
        value = getattr(args, name, None)
        if value is not None:
            database = probe._absolute(value)
            if path in {database, *(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal"))}:
                raise GarageError("JSON evidence must not replace a database path or create one of its SQLite sidecars.",
                                  "chassis_diagnostics")
    if args.command in ("build-db", "build-tyres"):
        database = probe._absolute(args.database_output)
        suffix = ".tyres.json" if args.command == "build-tyres" else ".probe.json"
        if path in (database, Path(str(database) + suffix)):
            raise GarageError("--output must be separate from the database and its automatic receipt.",
                              "chassis_diagnostics")
    elif args.command == "build-package":
        package = probe._absolute(args.package_output)
        baseline = probe._absolute(args.baseline_package)
        for protected in (package, baseline):
            if path == protected or protected in path.parents:
                raise GarageError("Optional CLI evidence must be outside both baseline and diagnostic package directories.",
                                  "chassis_diagnostics")
    elif args.command == "verify-package":
        manifest_path = archive._plain_path(args.manifest)
        package = manifest_path.parent
        if path == package or package in path.parents:
            raise GarageError("JSON evidence must be outside the package being verified.", "chassis_diagnostics")
        manifest = archive._pinned_json(manifest_path, args.manifest_sha256)
        baseline = manifest.get("baseline")
        if type(baseline) is dict and type(baseline.get("path")) is str:
            baseline_path = probe._absolute(baseline["path"])
            if path == baseline_path or baseline_path in path.parents:
                raise GarageError("JSON evidence must be outside the approved baseline package.", "chassis_diagnostics")
    return path


def _write_report(path, report):
    if path is None:
        return None
    encoded = (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    probe._fresh_path(path)
    owned = []
    try:
        with path.open("xb") as stream:
            owned.append((path, probe._identity(path)))
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        probe._remove_owned(owned)
        raise
    return {"path": str(path), "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _change_summary(plan):
    change = plan["allowed_change"]
    return {"car_id": plan["request"]["car_id"], "trial": plan["request"]["trial"],
            "delta_mm": plan["request"]["delta_mm"], "allowed_change": change,
            "source_sha256": plan["source"]["sha256"], "plan_sha256": plan["plan_sha256"]}


def execute(args):
    """Call only the reviewed offline APIs; return complete evidence and summary."""
    common = {"command": args.command, "developer_only": True, "runtime_verified": False,
              "player_installable": False, "game_launched": False, "game_installation_changed": False}
    if args.command in ("plan-tyres", "build-tyres", "verify-tyres"):
        from garagehub import tyre_size_plan as tyres
        if args.command == "plan-tyres":
            report = plan = tyres.plan_tyre_sizes(args.source_db, **_tyre_request(args))
            status = "nominal_tyre_plan_created"
        else:
            plan = _checked_tyre_plan(args)
            if args.command == "build-tyres":
                report = tyres.build_tyre_sizes(args.source_db, args.database_output, plan)
                status = "isolated_nominal_tyre_database_created"
            else:
                report = tyres.verify_tyre_sizes(args.source_db, args.probe_db, plan)
                status = "nominal_tyre_database_delta_verified"
        summary = {**common, "status": status, "car_id": plan["request"]["car_id"],
                   "selected_rows": len(plan["selected_parts"]), "inserted_size_rows": len(plan["insertions"]),
                   "changed_reference_cells": len(plan["redirects"]), "no_op": plan["no_op"],
                   "source_sha256": plan["source"]["sha256"], "plan_sha256": plan["plan_sha256"],
                   "native_assets_changed": False, "wheel_metadata_changed": False,
                   "visual_physical_consistency_established": False}
        if args.command == "build-tyres":
            summary.update(output=report["output"], receipt_path=report["receipt_path"])
        elif args.command == "verify-tyres":
            summary["output_sha256"] = report["output_sha256"]
        return report, summary
    if args.command == "plan-db":
        request = _request(args)
        report = probe.plan_chassis_probe(args.source_db, **request)
        return report, {**common, "status": "plan_created", **_change_summary(report)}
    if args.command == "build-db":
        plan = _checked_plan(args)
        report = probe.build_chassis_probe(args.source_db, args.database_output, plan)
        return report, {**common, "status": "isolated_database_created", **_change_summary(plan),
                        "output": report["output"], "receipt_path": report["receipt_path"],
                        "changed_cell_count": report["validation"]["changed_cell_count"]}
    if args.command == "verify-db":
        plan = _checked_plan(args)
        report = probe.verify_chassis_probe(args.source_db, args.probe_db, plan)
        return report, {**common, "status": "database_delta_verified", **_change_summary(plan),
                        "output_sha256": report["output_sha256"],
                        "changed_cell_count": report["validation"]["changed_cell_count"]}
    if args.command == "build-package":
        from garagehub.chassis_probe_package import build_chassis_probe_package, MANIFEST_NAME
        request = _request(args)
        identity = archive.reviewed_identity()
        if request["car_id"] != identity["car_id"]:
            raise GarageError("This package adapter supports only the explicitly reviewed baseline car ID "
                              + str(identity["car_id"]) + ".", "chassis_diagnostics")
        report = build_chassis_probe_package(
            args.baseline_package, args.packer, args.package_output,
            baseline_receipt_sha256=args.baseline_receipt_sha256,
            trial=request["trial"], delta_mm=request["delta_mm"])
        manifest = probe._absolute(args.package_output) / MANIFEST_NAME
        return report, {**common, "status": "database_only_package_verified", **request,
                        "manifest": str(manifest), "manifest_sha256": probe._sha(manifest),
                        "allowed_change": report["allowed_database_change"],
                        "candidate_header_sha256": report["candidate_header_sha256"],
                        "native_assets_byte_identical": report["native_assets_byte_identical"]}
    if args.command == "verify-package":
        from garagehub.chassis_probe_package import verify_chassis_probe_manifest
        request = _request(args)
        manifest_path = archive._plain_path(args.manifest)
        manifest = archive._pinned_json(manifest_path, args.manifest_sha256)
        identity = manifest.get("identity")
        if type(identity) is not dict or type(identity.get("car_id")) is not int or identity["car_id"] != request["car_id"]:
            raise GarageError("The pinned package manifest targets a different car.", "chassis_diagnostics")
        plan = archive._pinned_json(manifest_path.parent / "DatabaseWork/plan.json",
                                    manifest["evidence"]["DatabaseWork/plan.json"])
        if probe._plan_request(plan) != request:
            raise GarageError("The pinned package plan differs from the explicit trial and delta.", "chassis_diagnostics")
        report = verify_chassis_probe_manifest(manifest_path, args.manifest_sha256)
        return report, {**common, "status": "database_only_package_verified", **request,
                        "manifest": str(manifest_path), "manifest_sha256": args.manifest_sha256,
                        "allowed_change": report["allowed_database_change"],
                        "native_assets_byte_identical": report["native_assets_byte_identical"]}
    if args.command == "audit-wheels":
        from garagehub.native_wheel_contract import audit_native_wheel_contract
        paths = [archive._plain_path(value) for value in (args.hq_wheel, args.race_wheel)]
        if any(path.stat().st_size > 128 * 1024**2 for path in paths):
            raise GarageError("Wheel input exceeds this inspection's byte budget.", "chassis_diagnostics")
        data = [path.read_bytes() for path in paths]
        report = audit_native_wheel_contract(*data)
        for path, raw in zip(paths, data):
            if probe._sha(path) != hashlib.sha256(raw).hexdigest():
                raise GarageError("Wheel source changed during its read-only audit.", "chassis_diagnostics")
        return report, {**common, "status": "native_wheel_inventory_verified", "profile_id": report["profile_id"],
                        "hq_shapes": len(report["assets"]["hq"]["shapes"]),
                        "race_shapes": len(report["assets"]["race"]["shapes"]),
                        "race_physical_tyre_owner_identified": report["capabilities"]["race_physical_tyre_owner_identified"],
                        "native_axle_mount_owner_identified": report["capabilities"]["native_axle_mount_owner_identified"]}
    raise GarageError("Unknown chassis diagnostic command.", "chassis_diagnostics")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        output = _check_report_path(args)
        report, summary = execute(args)
        evidence = _write_report(output, report)
        if evidence is not None:
            summary["json_evidence"] = evidence
        print(json.dumps(summary, sort_keys=True, allow_nan=False))
        return 0
    except (GarageError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print("chassis diagnostics: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
