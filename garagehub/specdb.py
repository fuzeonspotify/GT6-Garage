"""GT6 1.22 car identity planning and isolated SQLite copy construction.

The append algorithm generalizes the verified Cinque additional-entry builder.
No function decrypts a game file, installs a package, or opens a source database
for writing. Callers provide a decrypted snapshot and package the result with
the corresponding model, NDB0 names and other presentation resources.

Replacement deliberately preserves the target's physics and identity. It routes
that compatible target to a fresh model namespace, leaving shared old model
files and every other car untouched. Different chassis/native configurations
are rejected rather than inheriting an untested donor setup silently.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import unicodedata

from .errors import GarageError

SCHEMA_VERSION = 1
DATABASE_VIRTUAL_PATH = "specdb/GT6/DB0106.dat"
ALIASES = {
    "IntakeManifold": "INTAKE_MANIFOLD", "ExhaustManifold": "EXHAUST_MANIFOLD",
    "Catalyst": "CATALYST", "AirCleaner": "AIR_CLEANER",
    "BoostController": "BOOST_CONTROLLER", "IndepThrottle": "INDEP_THROTTLE",
    "Supercharger": "SUPERCHARGER",
}
CLONE_RULES = {
    "MODEL_INFO": "CarCode", "WHEEL": "CarCode", "WHEEL_SP": "CarCode",
    "CAR_CUSTOM_INFO": "CarCode", "VARIATION": "LabelID",
    "CAR_NAME_ALPHABET": "LabelID", "CAR_NAME_JAPAN": "LabelID",
    "CAR_VIEW": "ID", "CAR_SEARCH": "ID", "UC_WING": "CarLabel",
}
REQUIRED_COLUMNS = {
    "GENERIC_CAR": {"ID", "CarID", "CarLabel", "ModelCode", "Defaultparts", "NumColor"},
    "DEFAULT_PARTS": {"ID", "Label", "CHASSIS", "FRONTTIRE", "REARTIRE"},
    "MODEL_INFO": {"ID", "CarCode", "ModelCode"},
    "WHEEL": {"Label", "CarCode", "ModelCode"},
    "CAR_CUSTOM_INFO": {"Label", "CarCode"},
    "VARIATION": {"Label", "LabelID", "VarOrder"},
    "CAR_NAME_ALPHABET": {"Label", "LabelID", "Name", "Grade", "ShortName"},
    "CAR_NAME_JAPAN": {"Label", "LabelID", "Name", "Grade", "ShortName"},
    "CAR_VIEW": {"ID", "Label"}, "CAR_SEARCH": {"ID", "CarLabel"},
    "CHASSIS": {"ID"}, "FRONTTIRE": {"ID"}, "REARTIRE": {"ID"},
}
IDENTITY_COLUMNS = {"ID", "Label", "LabelID", "UseCar", "CarCode", "CarID", "CarLabel"}
SENTINELS = {-1, 0xFFFFFFFF, None}
MANDATORY_PARTS = {"CHASSIS", "FRONTTIRE", "REARTIRE"}
RUNTIME_CAR_FIELDS = ("NumColor", "MainColor", "PremiumCar", "InteriorType", "OpenCar")
_MODEL_PATH = re.compile(r"^car/(\d{4})/(\d{4})(?:/|$)", re.ASCII)
_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,79}$", re.ASCII)


def _require(condition, message, code="specdb_validation"):
    if not condition:
        raise GarageError(message, code)


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(connection, table, column=None, value=None):
    sql = "SELECT * FROM " + _quote(table)
    args = ()
    if column is not None:
        sql += " WHERE " + _quote(column) + "=?"
        args = (value,)
    return [dict(row) for row in connection.execute(sql, args)]


def _one(connection, table, column, value):
    rows = _rows(connection, table, column, value)
    _require(len(rows) == 1, f"{table}.{column}={value} must resolve to exactly one row.")
    return rows[0]


def _schema(connection):
    structures = list(connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    _require(not any(row[0] == "trigger" for row in structures), "Database triggers are outside the reviewed GT6 schema.")
    _require(not any(row[0] == "table" and "VIRTUAL TABLE" in (row[2] or "").upper() for row in structures),
             "Virtual tables are outside the reviewed GT6 schema.")
    result = {}
    for row in structures:
        if row[0] == "table":
            result[row[1]] = [entry[1] for entry in connection.execute("PRAGMA table_info(" + _quote(row[1]) + ")")]
    for table, required in REQUIRED_COLUMNS.items():
        _require(table in result, "GT6 database is missing table " + table + ".")
        missing = required - set(result[table])
        _require(not missing, f"GT6 {table} is missing required columns: {', '.join(sorted(missing))}.")
    for table, columns in result.items():
        if "UseCar" in columns:
            _require("ID" in columns, f"Cannot safely clone {table}: UseCar rows have no ID.")
    return result


@contextmanager
def _source(path):
    path = Path(path).resolve()
    _require(path.is_file(), "Choose an existing decrypted GT6 database.", "specdb_missing")
    _require(not any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")),
             "Use a closed database snapshot; a pending SQLite transaction was found.")
    with path.open("rb") as stream:
        _require(stream.read(16) == b"SQLite format 3\0", "The GT6 database must be decrypted into an isolated SQLite copy.", "specdb_encrypted")
    before = _sha(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    try:
        schema = _schema(connection)
        yield connection, schema, before
        _require(_sha(path) == before, "Database changed during inspection. Refresh the installation plan.", "specdb_stale")
    finally:
        connection.close()


def model_path(model_code):
    """Model codes are eight *decimal* digits, e.g. 2250028 -> 0225/0028."""
    _require(type(model_code) is int and 0 <= model_code <= 99_999_999, "Unsupported GT6 model code.")
    digits = f"{model_code:08d}"
    return "car/" + digits[:4] + "/" + digits[4:]


def _info(connection, car):
    names = _rows(connection, "CAR_NAME_ALPHABET", "LabelID", car["ID"])
    return {"car_id": car["ID"], "label": car["CarLabel"],
            "display_name": names[0]["Name"] if len(names) == 1 else car["CarLabel"],
            "model_code": car["ModelCode"], "model_path": model_path(car["ModelCode"])}


def _validate_car(connection, schema, car_id):
    car = _one(connection, "GENERIC_CAR", "ID", car_id)
    _require(car["CarID"] == car_id, "GENERIC_CAR.ID and CarID disagree.")
    model_path(car["ModelCode"])
    default = _one(connection, "DEFAULT_PARTS", "ID", car["Defaultparts"])
    for column, value in default.items():
        table = ALIASES.get(column, column)
        if column in MANDATORY_PARTS:
            _require(value not in SENTINELS, f"The donor has no required {column} part.")
        if table in schema and "ID" in schema[table] and column not in ("FRONTTIRE_G", "REARTIRE_G") and value not in SENTINELS:
            _one(connection, table, "ID", value)
    for table in ("MODEL_INFO", "WHEEL", "CAR_CUSTOM_INFO"):
        linked = _rows(connection, table, "CarCode", car_id)
        _require(bool(linked), f"Car {car_id} has no {table} record.")
        if "ModelCode" in schema[table]:
            _require(all(row["ModelCode"] == car["ModelCode"] for row in linked),
                     f"Car {car_id} uses multiple or inconsistent {table} model codes; this mapping needs review.")
    if "WHEEL_SP" in schema:
        special = _rows(connection, "WHEEL_SP", "CarCode", car_id)
        _require(all(row["ModelCode"] == car["ModelCode"] for row in special),
                 f"Car {car_id} uses separate WHEEL_SP model references; this mapping needs review.")
    for table in ("CAR_NAME_ALPHABET", "CAR_NAME_JAPAN"):
        _one(connection, table, "LabelID", car_id)
    for table in ("CAR_VIEW", "CAR_SEARCH"):
        _one(connection, table, "ID", car_id)
    variations = _rows(connection, "VARIATION", "LabelID", car_id)
    _require(type(car["NumColor"]) is int and car["NumColor"] > 0 and len(variations) == car["NumColor"],
             f"Car {car_id} color count does not match VARIATION.")
    orders = [row["VarOrder"] for row in variations]
    _require(len(orders) == len(set(orders)), f"Car {car_id} has duplicate color variation order values.")
    return car, default


def _neutral(rows, *, model=False):
    ignored = IDENTITY_COLUMNS | ({"ModelCode"} if model else set())
    return sorted(json.dumps({k: v for k, v in row.items() if k not in ignored}, sort_keys=True, default=repr) for row in rows)


def _compatibility(connection, schema, donor_id, target_id):
    blockers = []
    try:
        donor, donor_parts = _validate_car(connection, schema, donor_id)
        target, target_parts = _validate_car(connection, schema, target_id)
    except GarageError as error:
        return [str(error)]
    for table in ("CHASSIS", "FRONTTIRE", "REARTIRE"):
        left = _one(connection, table, "ID", donor_parts[table])
        right = _one(connection, table, "ID", target_parts[table])
        if _neutral([left]) != _neutral([right]):
            blockers.append(f"{table} differs from the conversion donor; fit this target's native configuration before replacing it.")
    for table in ("MODEL_INFO", "WHEEL", "WHEEL_SP"):
        if table in schema:
            left = _rows(connection, table, "CarCode", donor_id)
            right = _rows(connection, table, "CarCode", target_id)
            if _neutral(left, model=True) != _neutral(right, model=True):
                blockers.append(f"{table} native configuration differs from the conversion donor.")
    differences = [field for field in RUNTIME_CAR_FIELDS if donor.get(field) != target.get(field)]
    if differences:
        blockers.append("Target rendering/color configuration differs: " + ", ".join(differences) + ".")
    return blockers


def catalogue(db_path, donor_car_id=None):
    """Read-only car picker with optional per-target native compatibility."""
    with _source(db_path) as (connection, schema, digest):
        if donor_car_id is not None:
            _require(type(donor_car_id) is int, "Choose a numeric donor car ID.")
            _validate_car(connection, schema, donor_car_id)
        cars = []
        for car in _rows(connection, "GENERIC_CAR"):
            info = _info(connection, car)
            if donor_car_id is not None:
                blockers = _compatibility(connection, schema, donor_car_id, car["ID"])
                info.update(replacement_compatible=not blockers, compatibility_blockers=blockers)
            cars.append(info)
        cars.sort(key=lambda item: (item["display_name"].casefold(), item["car_id"]))
        return {"schema_version": SCHEMA_VERSION, "source_sha256": digest, "car_count": len(cars), "cars": cars}


def _used_model_codes(connection, schema, reserved):
    result = set()
    for table, columns in schema.items():
        if "ModelCode" in columns:
            for row in connection.execute("SELECT DISTINCT ModelCode FROM " + _quote(table)):
                if type(row[0]) is int and 0 <= row[0] <= 99_999_999:
                    result.add(row[0])
    for path in reserved:
        _require(isinstance(path, str), "Reserved model paths must be text.")
        match = _MODEL_PATH.match(path.replace("\\", "/"))
        if match:
            result.add(int(match[1] + match[2]))
    return result


def _allocate_model(connection, schema, donor_model, reserved):
    used = _used_model_codes(connection, schema, reserved)
    maker = donor_model // 10000
    children = [code % 10000 for code in used if code // 10000 == maker]
    start = max(children, default=0) + 1
    candidates = list(range(start, 10000)) + list(range(1, start))
    for child in candidates:
        code = maker * 10000 + child
        if code not in used:
            return code
    raise GarageError("The donor manufacturer has no unused model namespace. Select a reviewed namespace policy.", "specdb_namespace_full")


def _allocate_car_id(connection, schema, reserved):
    used, identity_max = set(), 0
    for table, columns in schema.items():
        refs = [c for c in ("UseCar", "CarCode", "CarID") if c in columns]
        if table == "GENERIC_CAR" or table in ("CAR_VIEW", "CAR_SEARCH"):
            refs.append("ID")
        if table.startswith("CAR_NAME_") or table in ("VARIATION", "WHEEL", "CAR_CUSTOM_INFO"):
            if "LabelID" in columns:
                refs.append("LabelID")
        for column in set(refs):
            values = {r[0] for r in connection.execute("SELECT DISTINCT " + _quote(column) + " FROM " + _quote(table))
                      if type(r[0]) is int and 0 <= r[0] < 0x7FFFFFFF}
            used.update(values)
            if table == "GENERIC_CAR" or table.startswith("CAR_NAME_"):
                identity_max = max(identity_max, max(values, default=0))
    for value in reserved:
        _require(type(value) is int and 0 <= value < 0x7FFFFFFF, "Reserved car IDs must be valid nonnegative integers.")
        used.add(value)
        identity_max = max(identity_max, value)
    candidate = identity_max + 1
    while candidate in used:
        candidate += 1
    _require(candidate < 0x7FFFFFFF, "No representable new car ID is available.")
    return candidate


def _label_for(name, car_id):
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    stem = re.sub(r"[^a-z0-9]+", "_", ascii_name).strip("_")[:48] or "model"
    return f"custom_{stem}_{car_id}"


def _check_label(connection, schema, label):
    for table, columns in schema.items():
        for column in ("CarLabel", "Label"):
            if column in columns:
                count = connection.execute("SELECT count(*) FROM " + _quote(table) + " WHERE " + _quote(column) + "=?", (label,)).fetchone()[0]
                _require(count == 0, f"Label {label} is already present in {table}.{column}.", "specdb_identity_collision")


def plan_install(db_path, *, mode, donor_car_id, name, label=None,
                 target_car_id=None, reserved_model_paths=(), reserved_car_ids=()):
    """Return a serializable, hash-pinned plan without changing any database.

    Pass the current packed-file listing in reserved_model_paths and all IDs
    from both NDB0 name maps/locales in reserved_car_ids. Neither set is guessed.
    ready reports database readiness; model/package/runtime QA is separate.
    """
    _require(mode in ("new_entry", "replace", "replace_existing"), "Choose Add a new car or Replace an existing car.", "installation_mode")
    mode = "replace" if mode == "replace_existing" else mode
    _require(type(donor_car_id) is int, "Choose a numeric donor car ID.")
    _require(isinstance(name, str) and bool(name.strip()) and name.isprintable() and len(name) <= 160,
             "Enter a printable car name of 1–160 characters.")
    name = name.strip()
    _require(not isinstance(reserved_model_paths, (str, bytes)), "Pass a collection of reserved model paths, not one string.")
    _require(not isinstance(reserved_car_ids, (str, bytes)), "Pass a collection of reserved car IDs.")
    reserved_namespaces = set()
    for path in reserved_model_paths:
        _require(isinstance(path, str), "Reserved model paths must be text.")
        match = _MODEL_PATH.match(path.replace("\\", "/"))
        if match:
            reserved_namespaces.add("car/" + match[1] + "/" + match[2])
    reserved_paths = sorted(reserved_namespaces)
    reserved_id_values = set()
    for value in reserved_car_ids:
        _require(type(value) is int and 0 <= value < 0x7FFFFFFF, "Reserved car IDs must be valid nonnegative integers.")
        reserved_id_values.add(value)
    reserved_ids = sorted(reserved_id_values)
    request = {"mode": mode, "donor_car_id": donor_car_id, "name": name, "label": label,
               "target_car_id": target_car_id, "reserved_model_paths": reserved_paths, "reserved_car_ids": reserved_ids}
    with _source(db_path) as (connection, schema, digest):
        donor, _ = _validate_car(connection, schema, donor_car_id)
        donor_info = _info(connection, donor)
        new_model = _allocate_model(connection, schema, donor["ModelCode"], reserved_paths)
        blockers = []
        if mode == "new_entry":
            _require(target_car_id is None, "A new entry must not specify a replacement target.")
            car_id = _allocate_car_id(connection, schema, reserved_ids)
            new_label = label if label is not None else _label_for(name, car_id)
            _require(isinstance(new_label, str) and _LABEL.fullmatch(new_label), "Use a lowercase car label containing letters, digits and underscores.")
            _check_label(connection, schema, new_label)
            changed = {"GENERIC_CAR", "DEFAULT_PARTS"}
            for table, columns in schema.items():
                if "UseCar" in columns and _rows(connection, table, "UseCar", donor_car_id):
                    changed.add(table)
            for table, column in CLONE_RULES.items():
                if table in schema and _rows(connection, table, column, donor["CarLabel"] if column == "CarLabel" else donor_car_id):
                    changed.add(table)
            target = None
            display_name = name
            semantics = "append_donor_configuration"
        else:
            _require(type(target_car_id) is int, "Select the existing car to replace.", "replacement_target")
            _require(label is None, "Asset-only replacement preserves the selected target's label.")
            target_car = _one(connection, "GENERIC_CAR", "ID", target_car_id)
            target = _info(connection, target_car)
            car_id, new_label, display_name = target_car_id, target["label"], target["display_name"]
            blockers = _compatibility(connection, schema, donor_car_id, target_car_id)
            changed = {"GENERIC_CAR", "MODEL_INFO", "WHEEL"}
            if "WHEEL_SP" in schema and _rows(connection, "WHEEL_SP", "CarCode", target_car_id):
                changed.add("WHEEL_SP")
            semantics = "route_compatible_assets_preserve_target_physics_and_names"
        count = connection.execute("SELECT count(*) FROM GENERIC_CAR").fetchone()[0]
        plan = {"schema_version": SCHEMA_VERSION, "mode": mode, "ready": not blockers, "blockers": blockers,
                "source_sha256": digest, "database_virtual_path": DATABASE_VIRTUAL_PATH,
                "donor": donor_info, "target": target, "car_id": car_id, "label": new_label,
                "display_name": display_name, "requested_name": name,
                "model_code": new_model, "model_path": model_path(new_model),
                "changed_tables": sorted(changed), "car_count_before": count,
                "car_count_after": count + (mode == "new_entry"), "semantics": semantics,
                "request": request, "model_assets_verified": False, "runtime_tested": False}
        plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return plan


def _new_value(connection, table, column):
    value = connection.execute("SELECT COALESCE(MAX(" + _quote(column) + "),0)+1 FROM " + _quote(table)).fetchone()[0]
    _require(type(value) is int and 0 <= value < 0x7FFFFFFF, f"{table}.{column} cannot allocate another supported integer ID.")
    return value


def _replace_label(row, old_label, new_label, old_id, new_id):
    for column in ("Label", "CarLabel"):
        value = row.get(column)
        if isinstance(value, str):
            if column == "CarLabel" and value == str(old_id):
                row[column] = str(new_id)
            elif old_label in value:
                row[column] = value.replace(old_label, new_label)
            else:
                # Do not guess the meaning of an unrelated literal label.
                raise GarageError(f"Cannot remap {column} value {value!r}; it does not identify this donor.", "specdb_label_mapping")


def _append_car(source, dest, schema, plan):
    old_id, new_id = plan["donor"]["car_id"], plan["car_id"]
    old_label, new_label = plan["donor"]["label"], plan["label"]
    additions, maps = {}, {}

    def insert(table, row):
        for column in ("Label", "CarLabel"):
            if column in row and row[column] is not None:
                # The original database must not already own a generated label.
                # Multiple new VARIATION rows intentionally share their label.
                _require(not source.execute("SELECT 1 FROM " + _quote(table) + " WHERE " + _quote(column) + "=? LIMIT 1", (row[column],)).fetchone(),
                         f"Generated {table}.{column} collides with existing data.", "specdb_identity_collision")
        columns = list(row)
        dest.execute("INSERT INTO " + _quote(table) + " (" + ",".join(map(_quote, columns)) + ") VALUES (" + ",".join("?" for _ in columns) + ")", [row[c] for c in columns])
        additions[table] = additions.get(table, 0) + 1

    for table, columns in schema.items():
        if "UseCar" not in columns:
            continue
        for old in _rows(source, table, "UseCar", old_id):
            new = dict(old, UseCar=new_id, ID=_new_value(dest, table, "ID"))
            maps.setdefault(table, {})[old["ID"]] = new["ID"]
            if "LabelID" in new:
                new["LabelID"] = _new_value(dest, table, "LabelID")
            _replace_label(new, old_label, new_label, old_id, new_id)
            insert(table, new)
    donor = _one(source, "GENERIC_CAR", "ID", old_id)
    default = _one(source, "DEFAULT_PARTS", "ID", donor["Defaultparts"])
    default["ID"] = _new_value(dest, "DEFAULT_PARTS", "ID")
    _replace_label(default, old_label, new_label, old_id, new_id)
    for column, value in list(default.items()):
        part = ALIASES.get(column, column)
        if part in maps and value in maps[part]:
            default[column] = maps[part][value]
    insert("DEFAULT_PARTS", default)
    car = dict(donor, ID=new_id, CarID=new_id, CarLabel=new_label, ModelCode=plan["model_code"], Defaultparts=default["ID"])
    for column in ("Arcade", "InDealer"):
        if column in car:
            car[column] = 1
    insert("GENERIC_CAR", car)
    for table, column in CLONE_RULES.items():
        if table not in schema:
            continue
        for old in _rows(source, table, column, old_label if column == "CarLabel" else old_id):
            new = dict(old)
            _replace_label(new, old_label, new_label, old_id, new_id)
            for field in ("CarCode", "LabelID"):
                if field in new:
                    _require(new[field] == old_id, f"Unexpected {table}.{field} identity relationship.")
                    new[field] = new_id
            if "ID" in new:
                new["ID"] = new_id if table in ("CAR_VIEW", "CAR_SEARCH") else _new_value(dest, table, "ID")
            if "ModelCode" in new:
                _require(new["ModelCode"] == donor["ModelCode"], f"{table} has an alternate donor model code requiring review.")
                new["ModelCode"] = plan["model_code"]
            if table.startswith("CAR_NAME_"):
                new.update(Name=plan["display_name"], Grade=plan["display_name"], ShortName=plan["display_name"])
            insert(table, new)
    return additions, maps


def _replace_routing(source, dest, schema, plan):
    target_id = plan["car_id"]
    changes = {}
    for table in plan["changed_tables"]:
        column = "ID" if table == "GENERIC_CAR" else "CarCode"
        old = _rows(source, table, column, target_id)
        _require(bool(old), f"Missing replacement target records in {table}.")
        expected = plan["target"]["model_code"]
        _require(all(row["ModelCode"] == expected for row in old), f"{table} contains a separate wheel/model reference requiring review.")
        cursor = dest.execute("UPDATE " + _quote(table) + " SET ModelCode=? WHERE " + _quote(column) + "=?", (plan["model_code"], target_id))
        _require(cursor.rowcount == len(old), f"Unexpected replacement row count in {table}.")
        changes[table] = cursor.rowcount
    return changes


def _preservation(source, dest, schema, plan, additions):
    """Compare row multisets, preserving duplicates as well as row contents."""
    for table, columns in schema.items():
        before = _rows(source, table)
        after = _rows(dest, table)
        # A tuple of SQLite scalars is hashable; supported GT6 tables use only
        # integers/reals/text/blobs/nulls. Compare duplicate multiplicities too.
        before_counts = Counter(tuple(row[c] for c in columns) for row in before)
        after_counts = Counter(tuple(row[c] for c in columns) for row in after)
        if plan["mode"] == "new_entry":
            _require(not (before_counts - after_counts), "Original database rows changed in " + table + ".")
            _require(len(after) - len(before) == additions.get(table, 0), "Unexpected appended row count in " + table + ".")
        else:
            expected = []
            for row in before:
                row = dict(row)
                target_column = "ID" if table == "GENERIC_CAR" else "CarCode"
                if table in plan["changed_tables"] and row.get(target_column) == plan["car_id"]:
                    row["ModelCode"] = plan["model_code"]
                expected.append(tuple(row[c] for c in columns))
            _require(Counter(expected) == after_counts, "Replacement altered unrelated data in " + table + ".")


def build_database_copy(source_db, output_db, plan):
    """Build a *new* isolated SQLite file, using a freshly revalidated plan.

    The destination must not exist. A failure removes only this call's temporary
    output. The source is always opened mode=ro and its hash is rechecked.
    """
    _require(isinstance(plan, dict) and isinstance(plan.get("request"), dict), "Choose a current installation plan.")
    fresh = plan_install(source_db, **plan["request"])
    _require(all(plan.get(key) == value for key, value in fresh.items()), "Installation plan changed or is stale. Review it again.", "specdb_stale")
    _require(fresh["ready"], " ".join(fresh["blockers"]), "specdb_incompatible_target")
    source_path, output = Path(source_db).resolve(), Path(output_db).absolute()
    _require(source_path != output.resolve(), "Database output must be separate from its source.")
    _require(not os.path.lexists(output), "Database output already exists; choose a new candidate path.", "specdb_output_exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".specdb_", suffix=".sqlite", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    published = False
    try:
        with _source(source_path) as (source, schema, digest):
            _require(digest == fresh["source_sha256"], "Database changed before construction.", "specdb_stale")
            dest = sqlite3.connect(temporary)
            dest.row_factory = sqlite3.Row
            try:
                source.backup(dest)
                dest.execute("PRAGMA trusted_schema=OFF")
                dest.execute("BEGIN IMMEDIATE")
                if fresh["mode"] == "new_entry":
                    additions, maps = _append_car(source, dest, schema, fresh)
                    updated = {}
                else:
                    updated = _replace_routing(source, dest, schema, fresh)
                    additions, maps = {}, {}
                _validate_car(dest, schema, fresh["car_id"])
                _preservation(source, dest, schema, fresh, additions)
                _require(dest.execute("SELECT count(*) FROM GENERIC_CAR").fetchone()[0] == fresh["car_count_after"], "Unexpected final car count.")
                _require([r[0] for r in dest.execute("PRAGMA integrity_check")] == ["ok"], "Generated database failed SQLite integrity validation.")
                dest.commit()
            except Exception:
                dest.rollback()
                raise
            finally:
                dest.close()
        output_hash = _sha(temporary)
        # Exclusive create avoids replacing a concurrent job's completed file.
        with output.open("xb") as target:
            published = True
            with temporary.open("rb") as source_stream:
                shutil.copyfileobj(source_stream, target, 1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        _require(_sha(output) == output_hash, "Database output copy verification failed.")
        _require(_sha(source_path) == fresh["source_sha256"], "Source changed during construction; discard this candidate.", "specdb_stale")
        return {"passed": True, "schema_version": SCHEMA_VERSION, "mode": fresh["mode"],
                "semantics": fresh["semantics"], "source_sha256": fresh["source_sha256"],
                "database_sha256": output_hash, "plan_sha256": fresh["plan_sha256"],
                "car_id": fresh["car_id"], "label": fresh["label"], "display_name": fresh["display_name"],
                "model_code": fresh["model_code"], "model_path": fresh["model_path"],
                "car_count_before": fresh["car_count_before"], "car_count_after": fresh["car_count_after"],
                "added_rows": additions, "updated_rows": updated, "parts_id_maps": maps,
                "all_unrelated_rows_preserved": True, "source_unchanged": True,
                "physics": "donor configuration cloned" if fresh["mode"] == "new_entry" else "existing target configuration unchanged",
                "integrity": "ok", "runtime_tested": False, "physical_ps3_tested": False}
    except Exception:
        if published:
            output.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        for suffix in ("-journal", "-wal", "-shm"):
            Path(str(temporary) + suffix).unlink(missing_ok=True)
