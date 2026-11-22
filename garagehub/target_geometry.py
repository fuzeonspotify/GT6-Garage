"""Read-only nominal geometry of an explicitly selected GT6 car configuration.

Physical TIRESIZE records and visual WHEEL/WHEEL_SP records are different
sources.  Resolving their numbers is not a native-profile compatibility check,
a measurement of rendered tyres, or a source-model/mount fit approval.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .errors import GarageError
from .specdb import _quote, _sha


SCHEMA_VERSION = 1
PART_TABLES = ("CHASSIS", "FRONTTIRE", "REARTIRE")
UNIT_EVIDENCE = {
    "primary_url": "https://nenkai.github.io/gt-modding-hub/concepts/specdb/#tiresize",
    "gt6_reference": "docs/evidence/v043/rx8_nominal_tyre_reference.json",
    "gt6_reference_sha256": "91c14684bc3e18f5d7a90d858040439d137dc81ce16a7e6ce6523efe1e348b02",
    "tiresize": {"diameter": "divide by 10 for rim inches",
                 "width": "multiply by 5 for section width in millimetres",
                 "flatness": "multiply by 5 for aspect percent"},
    "wheel": {"Diameter": "divide by 10 for rim inches",
              "Width": "section width in millimetres", "Aspect": "aspect percent"},
    "chassis": "wheelbase, treadF and treadR are nominal millimetres",
    "scope": "Ordinary metric tyre dimensions only. Special visual diameter/type controls are not inferred.",
}


def _safe(value):
    """Keep exceptional raw SQLite values representable in a JSON report."""
    if isinstance(value, bytes):
        return {"sqlite_type": "blob", "hex": value.hex()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"sqlite_type": "real", "value": repr(value)}
    if isinstance(value, dict):
        return {key: _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def _value(row, name):
    return next((value for key, value in row.items() if key.casefold() == name.casefold()), None)


def _has(row, name):
    return any(key.casefold() == name.casefold() for key in row)


def _id(value):
    return type(value) is int and 0 <= value < 0xFFFFFFFF


def _record(table, row):
    raw = _safe(row)
    encoded = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"table": table, "ids": {key: value for key, value in raw.items()
                                    if key.casefold() in {"id", "labelid", "carid", "carcode", "usecar", "wheelexid"}},
            "raw": raw, "canonical_record_sha256": hashlib.sha256(encoded).hexdigest()}


def _pending(path):
    return any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal"))


@contextmanager
def _snapshot(db_path):
    """A narrow snapshot reader; unrelated identity tables are not required."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise GarageError("Choose an existing decrypted GT6 database.", "specdb_missing")
    if _pending(path):
        raise GarageError("Use a closed database snapshot; a SQLite transaction sidecar is present.", "specdb_validation")
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\0":
            raise GarageError("Choose a decrypted SQLite copy of the GT6 database.", "specdb_encrypted")
    before = _sha(path)
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("BEGIN")
        yield connection, path, before
        if _pending(path) or _sha(path) != before:
            raise GarageError("Database changed during geometry inspection. Refresh the target report.", "specdb_stale")
    except sqlite3.DatabaseError as exc:
        raise GarageError("The selected database could not be read as a GT6 geometry snapshot.",
                          "target_geometry_database") from exc
    finally:
        if connection is not None:
            connection.close()


class _Reader:
    def __init__(self, connection):
        self.connection = connection
        self.issues = []
        self.tables = {row["name"].casefold(): dict(row) for row in connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','view')")}
        self.columns = {}

    def issue(self, code, message, scope, *, severity="error", **detail):
        item = {"code": code, "message": message, "scope": scope,
                "severity": severity, **_safe(detail)}
        if item not in self.issues:
            self.issues.append(item)

    def rows(self, table, key, value, scope):
        structure = self.tables.get(table.casefold())
        if structure is None:
            self.issue("missing_table", f"{table} is missing; {scope} remains incomplete.", scope, table=table)
            return None
        sql = (structure["sql"] or "").upper()
        if structure["type"] != "table" or "VIRTUAL TABLE" in sql:
            self.issue("unsupported_table", f"{table} must be an ordinary SQLite table for this inspection.",
                       scope, table=table)
            return None
        actual = structure["name"]
        if actual not in self.columns:
            self.columns[actual] = {row["name"].casefold(): row["name"] for row in self.connection.execute(
                "PRAGMA table_info(" + _quote(actual) + ")")}
        column = self.columns[actual].get(key.casefold())
        if column is None:
            self.issue("missing_column", f"{table}.{key} is missing; its linked records cannot be resolved.",
                       scope, table=table, column=key)
            return None
        rows = [dict(row) for row in self.connection.execute(
            "SELECT * FROM " + _quote(actual) + " WHERE " + _quote(column) + "=?", (value,))]
        if any(not _id(_value(row, key)) or _value(row, key) != value for row in rows):
            self.issue("invalid_identity", f"{table}.{key} matched through SQLite type conversion; "
                       "exact integer record identities are required.", scope,
                       table=table, key=key, value=value,
                       matching_records=[_record(table, row) for row in rows])
            return None
        return rows

    def one(self, table, key, value, scope):
        rows = self.rows(table, key, value, scope)
        if rows is None:
            return None
        if len(rows) != 1:
            code = "missing_row" if not rows else "ambiguous_row"
            self.issue(code, f"{table}.{key}={value} resolves to {len(rows)} rows; one exact record is required.",
                       scope, table=table, key=key, value=value, match_count=len(rows))
            return None
        return rows[0]

    def number(self, row, field, scope, *, integer=False):
        value = _value(row, field)
        if not _has(row, field):
            self.issue("missing_column", f"{field} is missing; {scope} cannot be calculated.", scope, column=field)
            return None
        if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0
                or (integer and int(value) != value)):
            self.issue("invalid_dimension", f"{field} must be a finite positive {'integer code' if integer else 'number'}; "
                       f"{scope} remains unknown.", scope, column=field, value=value)
            return None
        return float(value)


def _nominal(width_mm, aspect_percent, rim_inches):
    width = width_mm / 1000
    rim = rim_inches * .0254
    diameter = rim + 2 * width * aspect_percent / 100
    radius = diameter / 2
    if not all(math.isfinite(value) and value > 0 for value in (width, rim, diameter, radius)):
        return None
    return {"section_width_metres": width, "aspect_percent": aspect_percent,
            "rim_diameter_inches": rim_inches, "rim_diameter_metres": rim,
            "outer_diameter_metres": diameter, "outer_radius_metres": radius,
            "evidence_kind": "nominal_specification", "rendered_or_loaded_measurement": False}


def _physical(reader, table, selection, car_id):
    scope = "physical." + table
    result = {"status": "incomplete", "part_id": selection["part_id"],
              "selection_source": selection["source"], "record": None, "nominal": None}
    if not _id(selection["part_id"]):
        return result
    row = reader.one(table, "ID", selection["part_id"], scope)
    if row is None:
        return result
    result["record"] = _record(table, row)
    owner = _value(row, "UseCar")
    if owner is not None and owner != car_id:
        reader.issue("part_owner_differs", f"{table} row {selection['part_id']} belongs to car {owner}; "
                     "its use with the selected car has not been verified.", scope, severity="review",
                     part_id=selection["part_id"], owner_car_id=owner)
    if table == "CHASSIS":
        fields = {"wheelbase_metres": "wheelbase", "front_track_metres": "treadF", "rear_track_metres": "treadR"}
        numbers = {key: reader.number(row, field, scope) for key, field in fields.items()}
        if all(value is not None for value in numbers.values()):
            nominal = {key: value / 1000 for key, value in numbers.items()}
            if all(math.isfinite(value) and value > 0 for value in nominal.values()):
                result.update(status="complete", nominal=nominal)
            else:
                reader.issue("invalid_dimension", "The chassis dimensions exceed the positive finite metre calculation range.", scope)
        return result
    size_id = _value(row, "tiresize")
    result.update(size_id=size_id, size_record=None)
    if not _id(size_id):
        reader.issue("invalid_reference", f"{table} row {selection['part_id']} has no valid TIRESIZE reference.",
                     scope, column="tiresize", value=size_id)
        return result
    size = reader.one("TIRESIZE", "ID", size_id, scope)
    if size is None:
        return result
    result["size_record"] = _record("TIRESIZE", size)
    diameter, width, flatness = (reader.number(size, key, scope, integer=True)
                                 for key in ("diameter", "width", "flatness"))
    if all(value is not None for value in (diameter, width, flatness)):
        nominal = _nominal(width * 5, flatness * 5, diameter / 10)
        if nominal is None:
            reader.issue("invalid_dimension", "The tyre codes exceed the positive finite nominal calculation range.", scope)
        else:
            result.update(status="complete", nominal=nominal)
    return result


def _visual_axle(reader, row, axle, scope):
    fields = [axle + name for name in ("Diameter", "Width", "Aspect")]
    diameter, width, aspect = (reader.number(row, key, scope) for key in fields)
    supported = True
    # Their units, precedence and exceptional forms have not been established
    # by the ordinary-metric reference. Keep the raw row; do not guess a tyre.
    for name in ("TireDiameter", "DiameterMM", "TireType"):
        field = axle + name
        value = _value(row, field)
        if not _has(row, field) or type(value) not in (int, float) or not math.isfinite(value):
            supported = False
            reader.issue("unknown_visual_control", f"{field} is missing or invalid; an ordinary visual tyre cannot be assumed.",
                         scope, column=field, value=value)
        elif value != 0:
            supported = False
            reader.issue("unsupported_visual_control", f"{field}={value} uses an unreviewed visual tyre convention; "
                         "its raw value is retained without a dimensional guess.", scope, severity="review",
                         column=field, value=value)
    if not supported or any(value is None for value in (diameter, width, aspect)):
        return {"status": "incomplete", "nominal": None}
    nominal = _nominal(width, aspect, diameter / 10)
    if nominal is None:
        reader.issue("invalid_dimension", "The visual tyre fields exceed the positive finite nominal calculation range.", scope)
    return {"status": "complete" if nominal is not None else "incomplete", "nominal": nominal}


def _visual(reader, table, car_id, model_code):
    scope = "visual." + table
    rows = reader.rows(table, "CarCode", car_id, scope)
    result = {"status": "incomplete", "rows": [], "row_count": 0, "selection": "not_selected"}
    if rows is None:
        return result
    result["row_count"] = len(rows)
    for index, row in enumerate(rows):
        row_scope = f"{scope}[{index}]"
        item = {"record": _record(table, row),
                "front": _visual_axle(reader, row, "Front", row_scope + ".front"),
                "rear": _visual_axle(reader, row, "Rear", row_scope + ".rear")}
        result["rows"].append(item)
        if model_code is not None and _value(row, "ModelCode") != model_code:
            reader.issue("visual_model_differs", f"{table} row {index} does not identify the selected car's model code; "
                         "native model compatibility remains unverified.", row_scope, severity="review",
                         target_model_code=model_code, row_model_code=_value(row, "ModelCode"))
    option_ids_valid = True
    if table == "WHEEL_SP":
        ids = [_value(row, "ID") for row in rows]
        option_ids_valid = all(_id(value) for value in ids)
        if not option_ids_valid:
            reader.issue("invalid_visual_option_id", "WHEEL_SP contains an option without an exact valid ID; "
                         "the option collection remains incomplete.", scope)
        elif len(set(ids)) != len(ids):
            option_ids_valid = False
            reader.issue("ambiguous_visual_option", "WHEEL_SP contains duplicate option IDs; no option was selected.", scope)
    if table == "WHEEL" and len(rows) != 1:
        reader.issue("missing_stock_wheel" if not rows else "ambiguous_stock_wheel",
                     f"WHEEL contains {len(rows)} rows for car {car_id}; a unique stock visual specification was not selected.", scope)
    elif option_ids_valid and all(item[axle]["status"] == "complete" for item in result["rows"] for axle in ("front", "rear")):
        result["status"] = "complete"
    return result


def _compare(physical, visual):
    if physical["nominal"] is None or visual["nominal"] is None:
        return {"status": "incomplete", "differences": None}
    p, v = physical["nominal"], visual["nominal"]
    # Compare the nominal source specification, not a made-up fit threshold.
    keys = ("section_width_metres", "aspect_percent", "rim_diameter_inches")
    differences = {key: v[key] - p[key] for key in keys}
    differences["outer_radius_metres"] = v["outer_radius_metres"] - p["outer_radius_metres"]
    return {"status": "agree" if all(v[key] == p[key] for key in keys) else "disagree",
            "differences": differences, "difference_direction": "visual_minus_physical_nominal"}


def target_geometry_report(db_path, car_id, selected_parts=None):
    """Resolve selected/default nominal geometry without writing any database.

selected_parts is an optional mapping of CHASSIS, FRONTTIRE and/or REARTIRE
to exact part IDs supplied by the caller. Unspecified parts use DEFAULT_PARTS.
It does not read a save or establish which parts a player currently has fitted.
Missing/ambiguous data returns incomplete components with plain-language
issues. Invalid API arguments or an unreadable/stale snapshot raise GarageError.
"""
    if not _id(car_id):
        raise GarageError("Choose a nonnegative integer car ID.", "target_geometry_input")
    if selected_parts is None:
        selected_parts = {}
    if (not isinstance(selected_parts, dict) or set(selected_parts) - set(PART_TABLES)
            or any(not _id(value) for value in selected_parts.values())):
        raise GarageError("Selected parts must map CHASSIS, FRONTTIRE or REARTIRE to exact nonnegative integer IDs.",
                          "target_geometry_input")
    selected_parts = dict(selected_parts)
    with _snapshot(db_path) as (connection, path, digest):
        reader = _Reader(connection)
        car = reader.one("GENERIC_CAR", "ID", car_id, "target")
        target = {"car_id": car_id, "label": None, "model_code": None,
                  "record": None, "default_parts_record": None}
        default = None
        if car is not None:
            target.update(label=_value(car, "CarLabel"), model_code=_value(car, "ModelCode"),
                          record=_record("GENERIC_CAR", car))
            if _has(car, "CarID") and _value(car, "CarID") != car_id:
                reader.issue("car_identity_mismatch", "GENERIC_CAR.ID and CarID disagree; target identity remains unreviewed.", "target")
            default_id = _value(car, "Defaultparts")
            if _id(default_id):
                default = reader.one("DEFAULT_PARTS", "ID", default_id, "default_parts")
                if default is not None:
                    target["default_parts_record"] = _record("DEFAULT_PARTS", default)
            else:
                reader.issue("invalid_reference", "The selected car has no valid DEFAULT_PARTS reference.",
                             "default_parts", value=default_id)
        selection = {}
        physical = {}
        names = {"CHASSIS": "chassis", "FRONTTIRE": "front_tyre", "REARTIRE": "rear_tyre"}
        for table in PART_TABLES:
            default_id = _value(default, table) if default is not None else None
            explicit = table in selected_parts
            part_id = selected_parts[table] if explicit else default_id
            selection[table] = {"source": "selected" if explicit else "default", "part_id": part_id,
                                "default_part_id": default_id, "current_installed_state_verified": False}
            if not _id(part_id):
                reader.issue("invalid_reference", f"No exact {table} part ID is available for the requested configuration.",
                             "selection." + table, value=part_id)
            # Do not attach an arbitrary part graph to an unresolved car.
            physical[names[table]] = (_physical(reader, table, selection[table], car_id) if car is not None
                                     else {"status": "incomplete", "part_id": part_id,
                                           "selection_source": selection[table]["source"], "record": None, "nominal": None})
        visual = {"wheel": _visual(reader, "WHEEL", car_id, target["model_code"]),
                  "wheel_sp": _visual(reader, "WHEEL_SP", car_id, target["model_code"])}
        comparisons = {"stock_wheel": {"status": "incomplete", "front": None, "rear": None}, "wheel_sp": []}
        if car is not None and len(visual["wheel"]["rows"]) == 1:
            row = visual["wheel"]["rows"][0]
            compare = {axle: _compare(physical[axle + "_tyre"], row[axle]) for axle in ("front", "rear")}
            statuses = {item["status"] for item in compare.values()}
            status = "incomplete" if "incomplete" in statuses else "disagree" if "disagree" in statuses else "agree"
            comparisons["stock_wheel"] = {"status": status, **compare}
            for axle in ("front", "rear"):
                if compare[axle]["status"] == "disagree":
                    reader.issue("visual_physical_disagreement", f"The stock visual {axle} tyre dimensions differ from "
                                 "the requested physical tyre specification; both are retained separately.",
                                 "comparison.stock_wheel." + axle, severity="review")
        if car is not None:
            for row in visual["wheel_sp"]["rows"]:
                comparisons["wheel_sp"].append({"record": row["record"], "selection": "not_selected",
                    **{axle: _compare(physical[axle + "_tyre"], row[axle]) for axle in ("front", "rear")}})
        identity_resolved = (car is not None
                             and not any(issue["code"] == "car_identity_mismatch" for issue in reader.issues))
        nominal_complete = identity_resolved and all(item["status"] == "complete" for item in physical.values())
        visual_complete = identity_resolved and all(item["status"] == "complete" for item in visual.values())
        report = {"schema_version": SCHEMA_VERSION, "kind": "selected_target_nominal_geometry",
                  "database": {"path": str(path), "sha256": digest, "unchanged": True},
                  "target": target, "selection": selection, "requested_selected_parts": selected_parts,
                  "physical": physical, "visual": visual, "comparisons": comparisons,
                  "nominal_data_complete": nominal_complete,
                  "nominal_data_complete_scope": "Resolved selected/default chassis and physical tyre nominal dimensions only.",
                  "visual_data_complete": visual_complete,
                  "visual_data_complete_scope": "Resolved stock and unselected option records in the supported ordinary metric convention only.",
                  "issues": reader.issues, "issue_messages": [item["message"] for item in reader.issues],
                  "unit_evidence": UNIT_EVIDENCE,
                  "limits": [
                      "Default or caller-supplied part IDs do not establish the player's currently installed configuration.",
                      "Nominal specifications do not include loaded tyre shape, suspension state, or the effects of chassis GrowInch and TireWidthRate controls.",
                      "WHEEL_SP records are unselected options; stock and option records do not establish the currently fitted visual wheel.",
                      "Dimension availability does not establish a native profile, mounting frame, clearance or runtime fit.",
                  ],
                  "native_profile_compatible": None, "mount_coordinates": None,
                  "actual_rendered_tyre_dimensions": None, "loaded_contact_radius_metres": None,
                  "runtime_fit_verified": False, "source_geometry_inspected": False,
                  "physics_changed": False, "read_only": True}
    return _safe(report)
