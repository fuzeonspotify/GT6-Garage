"""Developer-only, isolated one-field GT6 chassis A/B experiment builder.

This module is not used by player import, replacement, or installation paths.
It creates a closed decrypted SQLite diagnostic copy and an evidence receipt.
It never changes native assets, packages a car, launches a game, or installs.

Start with ``baseline`` and ``wheelbase_delta_mm`` on the SAME closed source.
``tread_front_delta_mm`` is a separate experiment, never combined implicitly.
The bounded deltas control an experiment; they are not physical safety limits.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import struct
import tempfile

from .errors import GarageError
from .specdb import _quote, _sha, _source


SCHEMA_VERSION = 1
TRIALS = {
    "wheelbase_delta_mm": {"column": "wheelbase", "delta_min": -500, "delta_max": 500,
                          "value_min": 1000, "value_max": 6000},
    "tread_front_delta_mm": {"column": "treadF", "delta_min": -200, "delta_max": 200,
                             "value_min": 600, "value_max": 3000},
}
PROTOCOL = (
    "Developer-only diagnostic; this is not an installable package or a product car.",
    "Use baseline and exactly one changed variant from the same pinned source database.",
    "Stage only in a separate controlled game validation environment; this builder does not stage or launch it.",
    "Keep native assets, selected car, installed parts, tune, camera and measurement setup identical.",
    "Measure baseline and changed wheel/contact positions before inferring a field-to-transform relationship.",
    "Do not use a currently protected emulator session or treat package/SQLite success as runtime validation.",
)


def _require(condition, message, code="chassis_probe_validation"):
    if not condition:
        raise GarageError(message, code)


def _absolute(path):
    return Path(os.path.abspath(os.fspath(path)))


def _check_components(path):
    """Do not resolve away a symlink/junction before deciding it is forbidden."""
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        _require(not stat.S_ISLNK(info.st_mode) and not reparse,
                 "Probe paths must not contain symbolic links or reparse points.", "chassis_probe_path")


def _identity(path):
    value = path.lstat()
    return (value.st_dev, value.st_ino)


def _source_path(path):
    path = _absolute(path)
    _check_components(path)
    _require(path.is_file(), "Choose an existing closed decrypted SQLite snapshot.", "chassis_probe_path")
    _require(path.stat().st_nlink == 1, "Probe source must not be a hard-linked file.", "chassis_probe_path")
    _require(not any(os.path.lexists(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")),
             "Use a closed snapshot without SQLite journal, WAL or SHM sidecars.", "chassis_probe_snapshot")
    with path.open("rb") as stream:
        header = stream.read(20)
    _require(header[:16] == b"SQLite format 3\0", "Probe source must be a decrypted SQLite snapshot.",
             "chassis_probe_snapshot")
    _require(len(header) == 20 and header[18:20] == b"\x01\x01",
             "Probe source must be a closed rollback-journal snapshot, not WAL mode.", "chassis_probe_snapshot")
    return path


def _fresh_path(path):
    path = _absolute(path)
    _check_components(path)
    _require(path.parent.is_dir(), "The probe output directory must already exist.", "chassis_probe_path")
    _require(not os.path.lexists(path), "Probe output already exists; choose a fresh output path.",
             "chassis_probe_exists")
    _require(not any(os.path.lexists(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")),
             "The fresh probe output has SQLite sidecars; choose another path.", "chassis_probe_exists")
    return path


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise GarageError("The probe plan must contain finite JSON data.", "chassis_probe_plan") from error


def _digest_json(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _value_token(value):
    """Preserve SQLite storage types, duplicate rows and exact float payloads."""
    if value is None:
        return ("null", None)
    if type(value) is int:
        return ("integer", value)
    if type(value) is float:
        return ("real", struct.pack(">d", value))
    if type(value) is str:
        return ("text", value)
    if type(value) is bytes:
        return ("blob", value)
    raise GarageError("Unsupported SQLite value in probe preservation check.", "chassis_probe_validation")


def _row_token(row):
    return tuple(_value_token(value) for value in row)


def _schema_record(connection):
    structures = [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name,sql")]
    tables = [row[1] for row in structures if row[0] == "table"]
    columns = {}
    for table in tables:
        details = [tuple(row) for row in connection.execute("PRAGMA table_xinfo(" + _quote(table) + ")")]
        _require(details and all(row[6] == 0 for row in details),
                 "Generated or hidden columns are outside the reviewed probe schema.")
        columns[table] = details
    pragmas = {name: connection.execute("PRAGMA " + name).fetchone()[0]
               for name in ("application_id", "user_version", "encoding", "page_size", "auto_vacuum", "schema_version")}
    return {"structures": structures, "columns": columns, "pragmas": pragmas}


def _exact_row(connection, table, key, identity):
    _require(type(identity) is int and 0 <= identity < 0xFFFFFFFF,
             f"{table}.{key} requires an exact nonnegative integer identity.")
    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM " + _quote(table) + " WHERE " + _quote(key) + "=?", (identity,))]
    _require(len(rows) == 1 and type(rows[0].get(key)) is int and rows[0][key] == identity,
             f"{table}.{key}={identity} must resolve to one exact integer record.")
    return rows[0]


def _request(car_id, trial, delta_mm):
    _require(type(car_id) is int and 0 <= car_id < 0xFFFFFFFF, "Choose an exact integer car ID.")
    _require(type(trial) is str and trial in {"baseline", *TRIALS}, "Choose an explicit supported probe trial.")
    if trial == "baseline":
        if delta_mm is None:
            delta_mm = 0
        _require(type(delta_mm) is int and delta_mm == 0, "The baseline trial must have zero delta.")
    else:
        limits = TRIALS[trial]
        _require(type(delta_mm) is int and delta_mm != 0
                 and limits["delta_min"] <= delta_mm <= limits["delta_max"],
                 f"{trial} requires a nonzero integer delta in [{limits['delta_min']}, {limits['delta_max']}].")
    return {"car_id": car_id, "trial": trial, "delta_mm": delta_mm}


def _make_plan(connection, schema, path, source_hash, request):
    car_id = request["car_id"]
    _require({"ID", "UseCar", "wheelbase", "treadF"}.issubset(schema.get("CHASSIS", [])),
             "Probe CHASSIS schema must contain exact identities, wheelbase and treadF.")
    car = _exact_row(connection, "GENERIC_CAR", "ID", car_id)
    _require(type(car.get("CarID")) is int and car["CarID"] == car_id, "Car ID aliases must agree exactly.")
    defaults = _exact_row(connection, "DEFAULT_PARTS", "ID", car.get("Defaultparts"))
    chassis = _exact_row(connection, "CHASSIS", "ID", defaults.get("CHASSIS"))
    _require(type(chassis.get("UseCar")) is int and chassis["UseCar"] == car_id,
             "The selected default CHASSIS must belong to this car.")
    references = [tuple(row) for row in connection.execute(
        'SELECT g.ID,d.ID FROM GENERIC_CAR AS g JOIN DEFAULT_PARTS AS d ON g.Defaultparts=d.ID WHERE d.CHASSIS=?',
        (chassis["ID"],))]
    _require(references == [(car_id, defaults["ID"])],
             "The selected CHASSIS is shared or its referencing car graph is ambiguous.")
    settings = _schema_record(connection)
    trial = request["trial"]
    change = None
    limits = None
    if trial != "baseline":
        limits = dict(TRIALS[trial])
        field = limits["column"]
        before = chassis[field]
        _require(type(before) is int and limits["value_min"] <= before <= limits["value_max"],
                 "The starting dimension is outside this bounded diagnostic experiment.")
        after = before + request["delta_mm"]
        _require(limits["value_min"] <= after <= limits["value_max"],
                 "The proposed dimension is outside this bounded diagnostic experiment.")
        change = {"table": "CHASSIS", "row_id": chassis["ID"], "owner_car_id": car_id,
                  "column": field, "before": before, "after": after, "delta_mm": request["delta_mm"]}
    # The entire row is pinned by a storage-type-preserving digest. Plans remain
    # finite JSON even if unrelated database columns contain unusual values.
    columns = schema["CHASSIS"]
    token = repr(_row_token([chassis[column] for column in columns])).encode("utf-8")
    plan = {
        "schema_version": SCHEMA_VERSION, "kind": "gt6_chassis_probe_plan",
        "developer_only": True, "installable": False, "product_car": False,
        "request": request, "source": {"path": str(path), "sha256": source_hash, "bytes": path.stat().st_size},
        "schema_sha256": _digest_json(settings),
        "selection": {"car_id": car_id, "default_parts_id": defaults["ID"], "chassis_id": chassis["ID"],
                      "chassis_storage_row_sha256": hashlib.sha256(token).hexdigest(),
                      "model_code": car.get("ModelCode")},
        "allowed_change": change, "experiment_bounds": limits,
        "bounds_scope": "Diagnostic controls only; these are not physical safety or handling limits.",
        "native_transforms_changed": False, "runtime_relationship_established": False,
        "protocol": list(PROTOCOL),
    }
    plan["plan_sha256"] = _digest_json(plan)
    return plan


def plan_chassis_probe(source_db, *, car_id, trial, delta_mm=None):
    """Read and pin one explicit baseline/wheelbase/front-track diagnostic plan."""
    request = _request(car_id, trial, delta_mm)
    path = _source_path(source_db)
    identity = _identity(path)
    with _source(path) as (connection, schema, source_hash):
        connection.execute("BEGIN")
        result = _make_plan(connection, schema, path, source_hash, request)
        _require(_source_path(path) == path and _identity(path) == identity,
                 "Probe source changed during planning.", "chassis_probe_stale")
    return result


def _apply_change(connection, change):
    if change is None:
        return
    cursor = connection.execute(
        "UPDATE CHASSIS SET " + _quote(change["column"]) + "=? WHERE ID=? AND UseCar=? AND "
        + _quote(change["column"]) + "=?",
        (change["after"], change["row_id"], change["owner_car_id"], change["before"]))
    _require(cursor.rowcount == 1, "The probe did not update exactly one owned CHASSIS row.")


def _verify_preservation(source, candidate, plan):
    source_schema = _schema_record(source)
    candidate_schema = _schema_record(candidate)
    _require(source_schema == candidate_schema, "Probe schema or database settings changed.", "chassis_probe_preservation")
    change = plan["allowed_change"]
    counts = {}
    for table in source_schema["columns"]:
        before = [tuple(row) for row in source.execute("SELECT * FROM " + _quote(table))]
        after = [tuple(row) for row in candidate.execute("SELECT * FROM " + _quote(table))]
        expected = Counter(_row_token(row) for row in before)
        if change is not None and table == "CHASSIS":
            columns = [row[1] for row in source_schema["columns"][table]]
            id_index, owner_index, field_index = (columns.index(key) for key in ("ID", "UseCar", change["column"]))
            selected = [row for row in before if type(row[id_index]) is int and row[id_index] == change["row_id"]]
            _require(len(selected) == 1 and type(selected[0][owner_index]) is int
                     and selected[0][owner_index] == change["owner_car_id"]
                     and type(selected[0][field_index]) is int and selected[0][field_index] == change["before"],
                     "Probe source row no longer matches the allowed delta.", "chassis_probe_preservation")
            changed = list(selected[0])
            changed[field_index] = change["after"]
            expected.subtract([_row_token(selected[0])])
            expected.update([_row_token(changed)])
            expected = +expected
        _require(Counter(_row_token(row) for row in after) == expected,
                 "Probe changed rows outside its exact allowed delta: " + table, "chassis_probe_preservation")
        counts[table] = {"before": len(before), "after": len(after)}
    integrity = [row[0] for row in candidate.execute("PRAGMA integrity_check")]
    _require(integrity == ["ok"], "Probe SQLite integrity check failed.", "chassis_probe_preservation")
    return {"all_other_rows_and_tables_unchanged": True, "schema_unchanged": True,
            "sqlite_integrity": "ok", "changed_cell_count": 0 if change is None else 1,
            "row_counts": counts}


def _assert_source(path, identity, source_hash):
    _require(_source_path(path) == path and _identity(path) == identity and _sha(path) == source_hash,
             "Probe source changed; discard this diagnostic and make a fresh plan.", "chassis_probe_stale")


def _plan_request(plan):
    _require(type(plan) is dict and type(plan.get("request")) is dict,
             "Provide a pinned chassis probe plan.", "chassis_probe_plan")
    request = plan["request"]
    _require(set(request) == {"car_id", "trial", "delta_mm"}, "Probe request fields do not match this API.",
             "chassis_probe_plan")
    return _request(request["car_id"], request["trial"], request["delta_mm"])


def verify_chassis_probe(source_db, output_db, plan):
    """Independently rederive complete preservation from two closed SQLite files.

    This read-only verifier trusts neither a build receipt nor the output path.
    It re-resolves the pinned source graph, checks every table with SQLite value
    types and duplicate multiplicities, and permits only the plan's single cell.
    Use this on the decoded database extracted from an experimental package.
    """
    request = _plan_request(plan)
    source_path = _source_path(source_db)
    output_path = _source_path(output_db)
    _require(source_path != output_path, "Verify a separate probe output, not the source itself.", "chassis_probe_path")
    source_identity, output_identity = _identity(source_path), _identity(output_path)
    with _source(source_path) as (source, schema, source_hash):
        source.execute("BEGIN")
        fresh = _make_plan(source, schema, source_path, source_hash, request)
        _require(_json(fresh) == _json(plan), "The probe plan is stale or was altered; make a fresh plan.",
                 "chassis_probe_stale")
        with _source(output_path) as (candidate, _, output_hash):
            candidate.execute("BEGIN")
            validation = _verify_preservation(source, candidate, fresh)
            baseline = request["trial"] == "baseline"
            _require(not baseline or source_hash == output_hash, "Baseline must be a byte-identical copy.",
                     "chassis_probe_preservation")
            _assert_source(source_path, source_identity, source_hash)
            _assert_source(output_path, output_identity, output_hash)
        result = {
            "schema_version": SCHEMA_VERSION, "kind": "gt6_chassis_probe_verification", "verified": True,
            "developer_only": True, "installable": False, "product_car": False, "runtime_verified": False,
            "plan_sha256": fresh["plan_sha256"], "source_sha256": source_hash, "output_sha256": output_hash,
            "validation": {**validation, "source_unchanged": True, "output_unchanged": True,
                           "baseline_byte_identical": baseline},
        }
    return result


def _publish_exclusive(path, payload_path, owned):
    _fresh_path(path)
    with path.open("xb") as destination:
        owned.append((path, _identity(path)))
        with payload_path.open("rb") as payload:
            shutil.copyfileobj(payload, destination, 1024 * 1024)
        destination.flush()
        os.fsync(destination.fileno())


def _remove_owned(owned):
    for path, identity in reversed(owned):
        try:
            if _identity(path) == identity and not path.is_symlink():
                path.unlink()
        except FileNotFoundError:
            pass


def build_chassis_probe(source_db, output_db, plan):
    """Build one fresh diagnostic DB and ``<output_db>.probe.json`` receipt.

    A rejected/tampered/stale plan creates neither result. Any build failure
    removes only files created by this invocation; existing paths are retained.
    All database mutations occur in a temporary isolated copy and a transaction.
    """
    request = _plan_request(plan)
    path = _source_path(source_db)
    identity = _identity(path)
    output = _fresh_path(output_db)
    receipt_path = _fresh_path(str(output) + ".probe.json")
    _require(output != path and receipt_path != path, "Probe output must be separate from its source.", "chassis_probe_path")
    owned = []
    try:
        with _source(path) as (source, schema, source_hash):
            source.execute("BEGIN")
            fresh = _make_plan(source, schema, path, source_hash, request)
            _require(_json(fresh) == _json(plan), "The probe plan is stale or was altered; make a fresh plan.",
                     "chassis_probe_stale")
            _assert_source(path, identity, source_hash)
            with tempfile.TemporaryDirectory(prefix=".chassis-probe-", dir=output.parent) as temporary:
                candidate_path = Path(temporary) / "candidate.sqlite"
                with path.open("rb") as original, candidate_path.open("xb") as candidate_file:
                    shutil.copyfileobj(original, candidate_file, 1024 * 1024)
                _require(_sha(candidate_path) == source_hash, "The probe copy did not match its source.", "chassis_probe_stale")
                candidate = sqlite3.connect(candidate_path)
                candidate.row_factory = sqlite3.Row
                try:
                    candidate.execute("PRAGMA trusted_schema=OFF")
                    if plan["allowed_change"] is not None:
                        candidate.execute("BEGIN IMMEDIATE")
                        _apply_change(candidate, plan["allowed_change"])
                    validation = _verify_preservation(source, candidate, plan)
                    candidate.commit()
                except BaseException:
                    candidate.rollback()
                    raise
                finally:
                    candidate.close()
                # Reopen the committed bytes and repeat the exact row check.
                with closing(sqlite3.connect(candidate_path.as_uri() + "?mode=ro", uri=True)) as persisted:
                    persisted.execute("PRAGMA query_only=ON")
                    persisted.execute("PRAGMA trusted_schema=OFF")
                    validation = _verify_preservation(source, persisted, plan)
                candidate_hash = _sha(candidate_path)
                baseline = request["trial"] == "baseline"
                _require(not baseline or candidate_hash == source_hash, "Baseline must be a byte-identical copy.")
                _assert_source(path, identity, source_hash)
                receipt = {
                    "schema_version": SCHEMA_VERSION, "kind": "gt6_isolated_chassis_probe",
                    "status": "diagnostic_candidate_only", "developer_only": True,
                    "purpose": "Deliberate isolated physics-versus-visual diagnostic; no runtime relationship is assumed.",
                    "installable": False, "product_car": False, "runtime_verified": False,
                    "game_launched": False, "game_files_changed": False, "native_assets_changed": False,
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                    "plan": fresh, "source": fresh["source"],
                    "output": {"path": str(output), "sha256": candidate_hash, "bytes": candidate_path.stat().st_size},
                    "receipt_path": str(receipt_path), "validation": {
                        **validation, "source_unchanged": True, "baseline_byte_identical": baseline},
                    "protocol": list(PROTOCOL),
                }
                receipt_temp = Path(temporary) / "receipt.json"
                receipt_temp.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
                _publish_exclusive(output, candidate_path, owned)
                _require(_sha(output) == candidate_hash and output.stat().st_nlink == 1,
                         "Published probe copy failed verification.", "chassis_probe_preservation")
                _publish_exclusive(receipt_path, receipt_temp, owned)
                _require(_sha(receipt_path) == _sha(receipt_temp) and receipt_path.stat().st_nlink == 1,
                         "Published probe receipt failed verification.", "chassis_probe_preservation")
                _assert_source(path, identity, source_hash)
        return receipt
    except BaseException:
        _remove_owned(owned)
        raise
