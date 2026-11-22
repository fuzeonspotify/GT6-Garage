"""Isolated copy-on-write authoring of selected owned GT6 nominal tyre sizes.

This developer API does not alter WHEEL, native geometry, chassis, suspension,
grip or tyre-category selection. It is not connected to player import/install.
Requested dimensions must fit the ordinary TIRESIZE integer lattice exactly.
Only explicitly selected FRONTTIRE/REARTIRE rows can acquire private size rows.
Database preservation is not evidence of native scaling or driving behaviour.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .chassis_probe import (_source_path, _fresh_path, _identity, _assert_source,
    _schema_record, _row_token, _exact_row, _digest_json, _json,
    _publish_exclusive, _remove_owned)
from .errors import GarageError
from .specdb import _quote, _sha, _source


SCHEMA_VERSION = 1
PART_TABLES = ("FRONTTIRE", "REARTIRE")
SIZE_COLUMNS = {"ID", "LabelID", "Label", "diameter", "width", "flatness"}
MAX_INTEGER = 0x7FFFFFFE


def _need(condition, message, code="tyre_size_plan"):
    if not condition:
        raise GarageError(message, code)


def _integer_id(value, label):
    _need(type(value) is int and 0 <= value <= MAX_INTEGER,
          label + " must be an exact supported nonnegative integer.")
    return value


def normalize_tyre_size(size):
    """Return exact requested/authored metric dimensions and integer codes.

    No tolerance or nearest-size rounding is used. Decimal conversion of JSON
    numbers avoids rejecting ordinary values such as 17.1 due to binary-float
    multiplication, while 17.1000000001 remains a different, invalid request.
    The integer bound is a storage bound, not a fit/handling capability claim.
    """
    fields = {"section_width_mm": (Decimal(5), "width"),
              "aspect_percent": (Decimal(5), "flatness"),
              "rim_diameter_inches": (Decimal("0.1"), "diameter")}
    _need(type(size) is dict and set(size) == set(fields),
          "Specify section_width_mm, aspect_percent and rim_diameter_inches exactly.")
    codes = {}
    for key, (step, code) in fields.items():
        value = size[key]
        _need(type(value) in (int, float), key + " must be a finite positive JSON number.")
        try:
            number = Decimal(str(value))
            encoded = number / step
        except (InvalidOperation, ValueError, OverflowError) as error:
            raise GarageError(key + " is not representable.", "tyre_size_quantization") from error
        _need(number.is_finite() and number > 0 and encoded.is_finite()
              and encoded == encoded.to_integral_value() and 0 < encoded <= MAX_INTEGER,
              key + " must be an exact positive multiple of " + str(step)
              + "; choose an explicit representable size instead of rounding.", "tyre_size_quantization")
        codes[code] = int(encoded)
    authored = {"section_width_mm": codes["width"] * 5,
                "aspect_percent": codes["flatness"] * 5,
                "rim_diameter_inches": codes["diameter"] / 10}
    diameter = (Decimal(codes["diameter"]) / 10 * Decimal("0.0254")
                + 2 * Decimal(codes["width"] * 5) / 1000
                * Decimal(codes["flatness"] * 5) / 100)
    return {"requested": dict(size), "authored": authored, "codes": codes,
            "exactly_representable": True, "rounding_applied": False,
            "nominal_outer_diameter_metres": float(diameter),
            "nominal_outer_radius_metres": float(diameter / 2),
            "rendered_or_loaded_measurement": False}


def _request(car_id, selections):
    _integer_id(car_id, "Car ID")
    _need(type(selections) is list and bool(selections), "Explicitly select at least one owned tyre row.")
    result, seen = [], set()
    for item in selections:
        _need(type(item) is dict and set(item) == {"table", "part_id", "size"}
              and item["table"] in PART_TABLES, "Select FRONTTIRE or REARTIRE with an exact part_id and size.")
        _integer_id(item["part_id"], "Tyre part ID")
        identity = (item["table"], item["part_id"])
        _need(identity not in seen, "A tyre row may be selected only once.")
        seen.add(identity)
        size = normalize_tyre_size(item["size"])
        result.append({"table": item["table"], "part_id": item["part_id"], "size": size["requested"]})
    return {"car_id": car_id, "selections": sorted(result, key=lambda r: (r["table"], r["part_id"]))}


def _storage_hash(row, columns):
    return hashlib.sha256(repr(_row_token(row[column] for column in columns)).encode()).hexdigest()


def _default_references(connection, schema, table, part_id, car_id):
    """Record stock/G references; reject rows consumed by another car's default.

    Alternate compound rows need not be the current default. Their explicit ID
    is the request; category names or presumed stock/racing groups never expand it.
    """
    result = []
    for field in (table, table + "_G"):
        if field not in schema["DEFAULT_PARTS"]:
            continue
        for row in connection.execute("SELECT * FROM DEFAULT_PARTS WHERE " + _quote(field) + "=?", (part_id,)):
            _need(type(row[field]) is int and row[field] == part_id,
                  "A default-part reference uses an ambiguous SQLite storage type.")
            default_id = _integer_id(row["ID"], "Default-parts ID")
            consumers = list(connection.execute("SELECT ID,CarID,Defaultparts FROM GENERIC_CAR WHERE Defaultparts=?",
                                                (default_id,)))
            _need(consumers and all(type(r["ID"]) is int and r["ID"] == car_id
                  and type(r["CarID"]) is int and r["CarID"] == car_id
                  and type(r["Defaultparts"]) is int and r["Defaultparts"] == default_id for r in consumers),
                  "A selected tyre row is shared through another car or an unresolved default-parts record.",
                  "tyre_size_ownership")
            result.append({"default_parts_id": default_id, "column": field,
                           "car_ids": [r["ID"] for r in consumers]})
    return result


def _make_plan(connection, schema, path, source_hash, request):
    _need(set(schema.get("TIRESIZE", [])) == SIZE_COLUMNS,
          "TIRESIZE must match the reviewed six-column ordinary metric schema.")
    for table in PART_TABLES:
        _need({"ID", "UseCar", "category", "tiresize"} <= set(schema.get(table, [])),
              table + " needs exact identities, ownership, category and tiresize columns.")
    # This bounded adapter knows both complete named TIRESIZE dependency tables.
    # Extra reference kinds must be reviewed instead of silently omitted.
    references = {(table, column) for table, columns in schema.items() for column in columns
                  if column.casefold().replace("_", "") in ("tiresize", "tiresizeid")}
    _need(references == {(table, "tiresize") for table in PART_TABLES},
          "An additional or differently named TIRESIZE dependency requires a reviewed adapter.")
    for table in schema:
        for fk in connection.execute("PRAGMA foreign_key_list(" + _quote(table) + ")"):
            _need(table.upper() != "TIRESIZE",
                  "TIRESIZE has an outgoing foreign key that requires a reviewed allocation adapter.")
            target = fk[2].upper()
            if target == "TIRESIZE":
                _need(table in PART_TABLES and fk[3] == "tiresize" and fk[4] == "ID",
                      "An additional declared TIRESIZE dependency requires a reviewed adapter.")
            elif target in PART_TABLES:
                _need(table == "DEFAULT_PARTS" and fk[3] in (target, target + "_G") and fk[4] == "ID",
                      "An additional declared tyre-part dependency requires a reviewed adapter.")
    settings = _schema_record(connection)
    _need(all(row[2].upper() == ("TEXT" if row[1] == "Label" else "INTEGER")
              for row in settings["columns"]["TIRESIZE"]), "TIRESIZE column types do not match the reviewed schema.")
    car_id = request["car_id"]
    car = _exact_row(connection, "GENERIC_CAR", "ID", car_id)
    _need(type(car["CarID"]) is int and car["CarID"] == car_id, "Car identity aliases must agree exactly.")
    _exact_row(connection, "DEFAULT_PARTS", "ID", car["Defaultparts"])
    sizes = [dict(row) for row in connection.execute("SELECT * FROM TIRESIZE")]
    for key in ("ID", "LabelID"):
        values = [_integer_id(row[key], "TIRESIZE." + key) for row in sizes]
        _need(len(set(values)) == len(values), "TIRESIZE." + key + " contains ambiguous identities.")
    labels = [row["Label"] for row in sizes]
    _need(all(type(label) is str and label for label in labels) and len(set(labels)) == len(labels),
          "TIRESIZE labels must be unique nonempty text.")
    next_id = max((row["ID"] for row in sizes), default=0) + 1
    next_label_id = max((row["LabelID"] for row in sizes), default=0) + 1
    selected, insertions, redirects, groups, dependencies = [], [], [], {}, {}
    for item in request["selections"]:
        table, part_id = item["table"], item["part_id"]
        row = _exact_row(connection, table, "ID", part_id)
        _need(type(row["UseCar"]) is int and row["UseCar"] == car_id,
              "Every selected tyre row must be owned by the selected car.", "tyre_size_ownership")
        _integer_id(row["category"], "Tyre category")
        default_refs = _default_references(connection, schema, table, part_id, car_id)
        original = _exact_row(connection, "TIRESIZE", "ID", row["tiresize"])
        _need(all(type(original[k]) is int and 0 < original[k] <= MAX_INTEGER for k in ("diameter", "width", "flatness")),
              "The source tyre size does not use the reviewed positive integer metric codes.")
        target = normalize_tyre_size(item["size"])
        changed = any(original[key] != target["codes"][key] for key in target["codes"])
        selected.append({"table": table, "part_id": part_id, "owner_car_id": car_id,
            "category": row["category"], "original_size_id": original["ID"], "changed": changed,
            "part_storage_sha256": _storage_hash(row, schema[table]), "default_references": default_refs,
            "target": target})
        if original["ID"] not in dependencies:
            deps = []
            for dependent in PART_TABLES:
                for ref in connection.execute("SELECT * FROM " + dependent + " WHERE tiresize=? ORDER BY ID", (original["ID"],)):
                    _need(type(ref["tiresize"]) is int and ref["tiresize"] == original["ID"],
                          "TIRESIZE has a dependency with an ambiguous reference type.")
                    _integer_id(ref["ID"], "Dependent tyre ID")
                    _integer_id(ref["UseCar"], "Dependent car ID")
                    _integer_id(ref["category"], "Dependent tyre category")
                    deps.append({"table": dependent, "part_id": ref["ID"], "owner_car_id": ref["UseCar"],
                        "category": ref["category"], "selected": any(s["table"] == dependent and s["part_id"] == ref["ID"]
                                                                 for s in request["selections"]),
                        "row_storage_sha256": _storage_hash(dict(ref), schema[dependent])})
            dependencies[original["ID"]] = {"size_row": original,
                "references": deps, "reference_count": len(deps),
                "other_car_reference_count": sum(r["owner_car_id"] != car_id for r in deps),
                "other_car_ids": sorted({r["owner_car_id"] for r in deps if r["owner_car_id"] != car_id})}
        if not changed:
            continue
        key = tuple(target["codes"][k] for k in ("diameter", "width", "flatness"))
        if key not in groups:
            _integer_id(next_id, "New TIRESIZE ID")
            _integer_id(next_label_id, "New TIRESIZE LabelID")
            label = "gh_tyre_" + str(car_id) + "_" + str(next_id)
            suffix = 0
            while label in labels:
                suffix += 1
                label = "gh_tyre_" + str(car_id) + "_" + str(next_id) + "_" + str(suffix)
            new = {"ID": next_id, "LabelID": next_label_id, "Label": label, **target["codes"]}
            groups[key] = next_id
            insertions.append(new)
            labels.append(label)
            next_id += 1
            next_label_id += 1
        redirects.append({"table": table, "part_id": part_id, "owner_car_id": car_id,
                          "before": original["ID"], "after": groups[key]})
    plan = {"schema_version": SCHEMA_VERSION, "kind": "gt6_private_tyre_size_plan",
        "developer_only": True, "installable": False, "product_car": False, "runtime_verified": False,
        "request": request, "source": {"path": str(path), "sha256": source_hash, "bytes": path.stat().st_size},
        "schema_sha256": _digest_json(settings), "selected_parts": selected,
        "size_dependencies": [dependencies[key] for key in sorted(dependencies)],
        "dependency_scope": "Every FRONTTIRE/REARTIRE row referencing a selected source size; additional named size dependencies are rejected.",
        "insertions": insertions, "redirects": redirects, "no_op": not redirects,
        "scope": "Exact selected nominal tyre references only; WHEEL, native geometry, chassis, suspension and all other tyre fields remain unchanged.",
        "native_scaling_supported": False, "visual_physical_consistency_established": False}
    plan["plan_sha256"] = _digest_json(plan)
    return plan


def plan_tyre_sizes(source_db, *, car_id, selections):
    request = _request(car_id, selections)
    path = _source_path(source_db)
    identity = _identity(path)
    with _source(path) as (connection, schema, source_hash):
        connection.execute("BEGIN")
        result = _make_plan(connection, schema, path, source_hash, request)
        _assert_source(path, identity, source_hash)
    return result


def _plan_request(plan):
    _need(type(plan) is dict and type(plan.get("request")) is dict
          and set(plan["request"]) == {"car_id", "selections"}, "Provide a complete pinned tyre-size plan.")
    return _request(**plan["request"])


def _verify(source, candidate, plan):
    schema = _schema_record(source)
    _need(schema == _schema_record(candidate), "Tyre authoring changed database schema or settings.", "tyre_size_preservation")
    counts = {}
    for table, columns_info in schema["columns"].items():
        columns = [row[1] for row in columns_info]
        before = list(source.execute("SELECT * FROM " + _quote(table)))
        expected = Counter(_row_token(row) for row in before)
        if table == "TIRESIZE":
            expected.update(_row_token(row[key] for key in columns) for row in plan["insertions"])
        for change in (r for r in plan["redirects"] if r["table"] == table):
            ii, oi, si = (columns.index(key) for key in ("ID", "UseCar", "tiresize"))
            selected = [row for row in before if type(row[ii]) is int and row[ii] == change["part_id"]]
            _need(len(selected) == 1 and type(selected[0][oi]) is int and selected[0][oi] == change["owner_car_id"]
                  and type(selected[0][si]) is int and selected[0][si] == change["before"],
                  "The original selected tyre row no longer matches its allowed change.", "tyre_size_preservation")
            altered = list(selected[0]); altered[si] = change["after"]
            expected.subtract([_row_token(selected[0])]); expected.update([_row_token(altered)])
        after = list(candidate.execute("SELECT * FROM " + _quote(table)))
        _need(Counter(_row_token(row) for row in after) == +expected,
              "Tyre authoring changed data beyond its exact insertions/references: " + table, "tyre_size_preservation")
        counts[table] = {"before": len(before), "after": len(after)}
    _need([row[0] for row in candidate.execute("PRAGMA integrity_check")] == ["ok"], "Tyre candidate failed SQLite integrity.")
    return {"all_original_size_rows_unchanged": True, "all_other_rows_and_values_unchanged": True,
            "schema_unchanged": True, "sqlite_integrity": "ok", "inserted_size_rows": len(plan["insertions"]),
            "changed_reference_cells": len(plan["redirects"]), "row_counts": counts}


def verify_tyre_sizes(source_db, output_db, plan):
    """Re-derive the request and compare every typed row, independent of receipts."""
    request = _plan_request(plan)
    path, output = _source_path(source_db), _source_path(output_db)
    _need(path != output, "Verify an independent candidate, not the source itself.")
    source_id, output_id = _identity(path), _identity(output)
    with _source(path) as (source, schema, source_hash):
        source.execute("BEGIN")
        fresh = _make_plan(source, schema, path, source_hash, request)
        _need(_json(fresh) == _json(plan), "The tyre plan is stale or altered.", "tyre_size_stale")
        with _source(output) as (candidate, _, output_hash):
            candidate.execute("BEGIN")
            validation = _verify(source, candidate, fresh)
            _need(not fresh["no_op"] or source_hash == output_hash, "A no-op tyre candidate must be byte-identical.")
            _assert_source(path, source_id, source_hash); _assert_source(output, output_id, output_hash)
    return {"schema_version": SCHEMA_VERSION, "kind": "gt6_private_tyre_size_verification", "verified": True,
            "developer_only": True, "installable": False, "runtime_verified": False,
            "plan_sha256": fresh["plan_sha256"], "source_sha256": source_hash, "output_sha256": output_hash,
            "validation": {**validation, "source_unchanged": True, "output_unchanged": True, "no_op_byte_identical": fresh["no_op"]}}


def build_tyre_sizes(source_db, output_db, plan):
    """Publish a new closed SQLite snapshot and <output>.tyres.json receipt.

    Existing files are never overwritten. Shared source size rows remain intact;
    selected owned tyre references alone are redirected to new private sizes.
    """
    request = _plan_request(plan)
    path = _source_path(source_db); identity = _identity(path)
    output = _fresh_path(output_db); receipt_path = _fresh_path(str(output) + ".tyres.json")
    forbidden = {path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))}
    _need(output not in forbidden and receipt_path not in forbidden, "Outputs cannot be the source or its SQLite sidecars.")
    owned = []
    try:
        with _source(path) as (source, schema, source_hash):
            source.execute("BEGIN")
            fresh = _make_plan(source, schema, path, source_hash, request)
            _need(_json(fresh) == _json(plan), "The tyre plan is stale or altered.", "tyre_size_stale")
            _assert_source(path, identity, source_hash)
            with tempfile.TemporaryDirectory(prefix=".tyre-size-", dir=output.parent) as temporary:
                candidate_path = Path(temporary) / "candidate.sqlite"
                shutil.copyfile(path, candidate_path)
                _need(_sha(candidate_path) == source_hash, "Tyre candidate copy differs from its source.")
                with closing(sqlite3.connect(candidate_path)) as candidate:
                    candidate.execute("PRAGMA trusted_schema=OFF")
                    with candidate:
                        if not fresh["no_op"]:
                            candidate.execute("BEGIN IMMEDIATE")
                        for row in fresh["insertions"]:
                            columns = schema["TIRESIZE"]
                            candidate.execute("INSERT INTO TIRESIZE (" + ",".join(map(_quote, columns)) + ") VALUES ("
                                              + ",".join("?" for _ in columns) + ")", [row[k] for k in columns])
                        for change in fresh["redirects"]:
                            cursor = candidate.execute("UPDATE " + change["table"]
                                + " SET tiresize=? WHERE ID=? AND UseCar=? AND tiresize=?",
                                (change["after"], change["part_id"], change["owner_car_id"], change["before"]))
                            _need(cursor.rowcount == 1, "A tyre redirect did not change exactly one owned row.")
                        _verify(source, candidate, fresh)
                validation = verify_tyre_sizes(path, candidate_path, fresh)
                candidate_hash = validation["output_sha256"]
                receipt = {"schema_version": SCHEMA_VERSION, "kind": "gt6_private_tyre_size_candidate",
                    "developer_only": True, "installable": False, "product_car": False, "runtime_verified": False,
                    "game_files_changed": False, "native_assets_changed": False, "wheel_metadata_changed": False,
                    "plan": fresh, "output": {"path": str(output), "sha256": candidate_hash, "bytes": candidate_path.stat().st_size},
                    "validation": validation["validation"], "receipt_path": str(receipt_path)}
                receipt_temp = Path(temporary) / "receipt.json"
                receipt_temp.write_text(json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
                _assert_source(path, identity, source_hash)
                _publish_exclusive(output, candidate_path, owned)
                _need(_sha(output) == candidate_hash and output.stat().st_nlink == 1, "Published tyre candidate differs.")
                _publish_exclusive(receipt_path, receipt_temp, owned)
                _need(_sha(receipt_path) == _sha(receipt_temp) and receipt_path.stat().st_nlink == 1, "Published tyre receipt differs.")
                _assert_source(path, identity, source_hash)
        return receipt
    except BaseException:
        _remove_owned(owned)
        raise
