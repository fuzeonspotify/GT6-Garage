"""Developer-only composition of a new car graph and private nominal tyres.

This API writes closed SQLite snapshots, never a game package. It does not
connect nominal dimensions to native wheel geometry or enable player sizing.
The original -> clone edge is checked by a separate read-only row projection;
the clone -> nominal-tyre edge uses the existing typed tyre verifier. Neither
edge trusts a caller's or builder's receipt as evidence of database contents.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import specdb, tyre_size_plan as tyres
from .chassis_probe import (_source_path, _fresh_path, _identity, _assert_source,
    _schema_record, _row_token, _exact_row, _digest_json, _json, _remove_owned)
from .errors import GarageError


SCHEMA_VERSION = 1
_SCOPE = {"developer_only": True, "installable": False, "product_car": False,
          "native_assets_changed": False, "native_scaling_supported": False,
          "visual_physical_consistency_established": False,
          "runtime_verified": False}


def _need(condition, message, code="new_entry_tyres"):
    if not condition:
        raise GarageError(message, code)


def _installation(source, plan):
    _need(type(plan) is dict and type(plan.get("request")) is dict,
          "Provide the current ordinary new-entry installation plan.")
    request = plan["request"]
    _need(set(request) == {"mode", "donor_car_id", "name", "label", "target_car_id",
                          "reserved_model_paths", "reserved_car_ids"},
          "The installation request does not match the reviewed API.")
    _need(request["mode"] == "new_entry" and request["target_car_id"] is None,
          "Private cloned tyre composition supports new entries only; replacement is unchanged.")
    fresh = specdb.plan_install(source, **request)
    _need(_json(plan) == _json(fresh) and fresh["ready"] is True,
          "The installation plan is stale or altered.", "new_entry_tyres_stale")
    return fresh


def _selections(car_id, selections):
    _need(type(selections) is list and bool(selections),
          "Explicitly select donor tyre part IDs and nominal sizes.")
    rows = []
    for row in selections:
        _need(type(row) is dict and set(row) == {"table", "donor_part_id", "size"},
              "Each selection needs table, donor_part_id and size only.")
        rows.append({"table": row["table"], "part_id": row["donor_part_id"], "size": row["size"]})
    normalized = tyres._request(car_id, rows)["selections"]
    return [{"table": row["table"], "donor_part_id": row["part_id"], "size": row["size"]}
            for row in normalized]


def _clone_projection(source, schema, plan):
    """Derive exact append rows without executing the clone writer.

    Allocation follows the source reader's row order, as the existing writer
    does. Label and identity changes are enumerated here rather than calling
    _append_car, _replace_label or _new_value. All other cells retain their
    original typed values. The actual candidate is compared below.
    """
    old_id, new_id = plan["donor"]["car_id"], plan["car_id"]
    old_label, new_label = plan["donor"]["label"], plan["label"]
    additions, maps, maxima = {}, {}, {}

    def rows(table, column, value):
        return [dict(row) for row in source.execute(
            "SELECT * FROM " + specdb._quote(table) + " WHERE " + specdb._quote(column) + "=?", (value,))]

    def allocate(table, column):
        key = (table, column)
        if key not in maxima:
            value = source.execute("SELECT MAX(" + specdb._quote(column) + ") FROM "
                                   + specdb._quote(table)).fetchone()[0]
            _need(value is None or type(value) is int,
                  "Clone allocation requires exact integer identities: " + table + "." + column)
            maxima[key] = 0 if value is None else value
        maxima[key] += 1
        _need(0 <= maxima[key] < 0x7FFFFFFF, "Clone identity allocation is outside its integer range.")
        return maxima[key]

    def labels(row):
        result = dict(row)
        for key in ("Label", "CarLabel"):
            value = row.get(key)
            if isinstance(value, str):
                if key == "CarLabel" and value == str(old_id):
                    result[key] = str(new_id)
                else:
                    _need(old_label in value, "A clone label does not identify the donor.")
                    result[key] = value.replace(old_label, new_label)
        return result

    def add(table, row):
        # AUTOINCREMENT can modify sqlite_sequence as a separate side effect.
        # This bounded projection retains that internal table exactly instead.
        sql = source.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        _need("AUTOINCREMENT" not in (sql or "").upper(),
              "An appended AUTOINCREMENT table needs a separately reviewed sequence adapter.")
        for key in ("Label", "CarLabel"):
            if row.get(key) is not None:
                _need(not rows(table, key, row[key]), "A generated clone label collides with original data.")
        additions.setdefault(table, []).append(row)

    for table, columns in schema.items():
        if "UseCar" not in columns:
            continue
        _need(table not in specdb.CLONE_RULES and table not in ("GENERIC_CAR", "DEFAULT_PARTS"),
              "Overlapping clone ownership rules require a reviewed adapter.")
        for original in rows(table, "UseCar", old_id):
            _need(type(original["UseCar"]) is int and original["UseCar"] == old_id,
                  "The donor owner uses an ambiguous SQLite type.")
            identity = tyres._integer_id(original["ID"], table + " donor ID")
            _need(identity not in maps.get(table, {}), "The donor part identity is ambiguous.")
            result = labels(original)
            result.update(ID=allocate(table, "ID"), UseCar=new_id)
            if "LabelID" in result:
                result["LabelID"] = allocate(table, "LabelID")
            maps.setdefault(table, {})[identity] = result["ID"]
            add(table, result)

    donor = _exact_row(source, "GENERIC_CAR", "ID", old_id)
    default = labels(_exact_row(source, "DEFAULT_PARTS", "ID", donor["Defaultparts"]))
    default["ID"] = allocate("DEFAULT_PARTS", "ID")
    for field, value in list(default.items()):
        table = specdb.ALIASES.get(field, field)
        if table in maps and value in maps[table]:
            default[field] = maps[table][value]
    add("DEFAULT_PARTS", default)
    car = dict(donor, ID=new_id, CarID=new_id, CarLabel=new_label,
               ModelCode=plan["model_code"], Defaultparts=default["ID"])
    for field in ("Arcade", "InDealer"):
        if field in car:
            car[field] = 1
    add("GENERIC_CAR", car)

    for table, key in specdb.CLONE_RULES.items():
        if table not in schema:
            continue
        for original in rows(table, key, old_label if key == "CarLabel" else old_id):
            result = labels(original)
            for field in ("CarCode", "LabelID"):
                if field in result:
                    _need(type(original[field]) is int and original[field] == old_id,
                          "A clone identity alias does not match the donor.")
                    result[field] = new_id
            if "ID" in result:
                result["ID"] = new_id if table in ("CAR_VIEW", "CAR_SEARCH") else allocate(table, "ID")
            if "ModelCode" in result:
                _need(type(original["ModelCode"]) is int and original["ModelCode"] == donor["ModelCode"],
                      "An alternate model reference requires a reviewed clone adapter.")
                result["ModelCode"] = plan["model_code"]
            if table.startswith("CAR_NAME_"):
                result.update(Name=plan["display_name"], Grade=plan["display_name"], ShortName=plan["display_name"])
            add(table, result)
    return additions, maps


def _mapping_rows(maps):
    return [{"table": table, "donor_part_id": old, "cloned_part_id": new}
            for table, mapping in sorted(maps.items()) for old, new in sorted(mapping.items())]


def _projection_summary(schema, additions, maps):
    typed = {table: sorted(repr(_row_token(row[column] for column in schema[table])) for row in rows)
             for table, rows in additions.items()}
    return {"added_rows": {table: len(rows) for table, rows in sorted(additions.items())},
            "typed_additions_sha256": _digest_json(typed), "parts_id_maps": _mapping_rows(maps),
            "schema_cookie": 1, "schema_cookie_policy": "sqlite_backup_into_new_empty_database"}


def plan_new_entry_tyres(source_db, *, installation_plan, selections):
    """Read-only plan using explicit donor IDs; neither size nor category is inferred."""
    path = _source_path(source_db); identity = _identity(path)
    install = _installation(path, installation_plan)
    selected = _selections(install["donor"]["car_id"], selections)
    donor_plan = tyres.plan_tyre_sizes(path, car_id=install["donor"]["car_id"], selections=[
        {"table": row["table"], "part_id": row["donor_part_id"], "size": row["size"]} for row in selected])
    with specdb._source(path) as (source, schema, source_hash):
        source.execute("BEGIN")
        _need(source_hash == install["source_sha256"] == donor_plan["source"]["sha256"],
              "The source changed between identity and tyre planning.", "new_entry_tyres_stale")
        _need(not list(source.execute("PRAGMA foreign_key_check")), "The source has unresolved foreign keys.")
        additions, maps = _clone_projection(source, schema, install)
        mapping = []
        for selected_row, tyre_row in zip(selected, donor_plan["selected_parts"]):
            table, old = selected_row["table"], selected_row["donor_part_id"]
            _need(old in maps.get(table, {}), "A selected donor tyre was not included in the new-entry graph.")
            mapping.append({"table": table, "donor_part_id": old, "cloned_part_id": maps[table][old],
                "donor_car_id": install["donor"]["car_id"], "new_car_id": install["car_id"],
                "category": tyre_row["category"], "original_size_id": tyre_row["original_size_id"],
                "size": selected_row["size"]})
        result = {"schema_version": SCHEMA_VERSION, "kind": "gt6_new_entry_tyre_composition_plan", **_SCOPE,
            "source": {"path": str(path), "sha256": source_hash, "bytes": path.stat().st_size},
            "installation_plan": install, "selections": selected, "donor_tyre_plan": donor_plan,
            "schema_sha256": _digest_json(_schema_record(source)),
            "expected_clone": _projection_summary(schema, additions, maps), "selected_mappings": mapping,
            "scope": "New-entry clone followed by exact selected nominal tyre references only; no native or package output."}
        result["plan_sha256"] = _digest_json(result)
        _assert_source(path, identity, source_hash)
    return result


def _fresh_plan(source_db, plan):
    _need(type(plan) is dict and "installation_plan" in plan and "selections" in plan,
          "Provide a complete current new-entry tyre composition plan.")
    fresh = plan_new_entry_tyres(source_db, installation_plan=plan["installation_plan"], selections=plan["selections"])
    _need(_json(fresh) == _json(plan), "The composition plan is stale or altered.", "new_entry_tyres_stale")
    return fresh


def _verify_clone(source_path, clone_path, install):
    """Compare every actual clone row with an independent typed append projection."""
    source_identity, clone_identity = _identity(source_path), _identity(clone_path)
    with specdb._source(source_path) as (source, schema, source_hash):
        source.execute("BEGIN")
        additions, maps = _clone_projection(source, schema, install)
        definition = _schema_record(source)
        with specdb._source(clone_path) as (clone, _, clone_hash):
            clone.execute("BEGIN")
            # SQLite backup uses the original destination cookie + 1. The
            # existing writer starts with an empty new database (cookie 0).
            # Permit this exact metadata change, not arbitrary schema edits.
            # Primary source: https://sqlite.org/matrix/ev/src/backup.html
            expected_definition = dict(definition, pragmas=dict(definition["pragmas"], schema_version=1))
            _need(expected_definition == _schema_record(clone), "Cloning changed schema or settings.", "new_entry_tyres_preservation")
            counts = {}
            for table, info in definition["columns"].items():
                columns = [row[1] for row in info]
                sql = "SELECT * FROM " + specdb._quote(table)
                original = list(source.execute(sql)); actual = list(clone.execute(sql))
                expected = Counter(_row_token(row) for row in original)
                expected.update(_row_token(row[column] for column in columns) for row in additions.get(table, []))
                _need(Counter(_row_token(row) for row in actual) == expected,
                      "Clone data differs from the exact permitted append: " + table, "new_entry_tyres_preservation")
                counts[table] = {"before": len(original), "after": len(actual), "added": len(additions.get(table, []))}
            _need([row[0] for row in clone.execute("PRAGMA integrity_check")] == ["ok"], "The clone failed SQLite integrity.")
            _need(not list(clone.execute("PRAGMA foreign_key_check")), "The clone has unresolved foreign keys.")
            _assert_source(source_path, source_identity, source_hash)
            _assert_source(clone_path, clone_identity, clone_hash)
    return {"source_sha256": source_hash, "clone_sha256": clone_hash,
            "all_original_rows_types_and_multiplicities_preserved": True,
            "only_exact_projected_clone_rows_added": True, "schema_definition_unchanged": True,
            "other_pragma_settings_unchanged": True,
            "schema_cookie": {"source": definition["pragmas"]["schema_version"], "clone": 1,
                              "policy": "sqlite_backup_into_new_empty_database"},
            "row_counts": counts, "parts_id_maps": _mapping_rows(maps),
            "sqlite_integrity": "ok", "foreign_key_check": "ok"}


def _cloned_tyre_plan(clone, plan):
    rows = plan["selected_mappings"]
    with specdb._source(clone) as (db, _, _):
        for row in rows:
            actual = _exact_row(db, row["table"], "ID", row["cloned_part_id"])
            expected = (row["new_car_id"], row["category"], row["original_size_id"])
            _need(_row_token(actual[key] for key in ("UseCar", "category", "tiresize")) == _row_token(expected),
                  "The selected clone ownership/category/size differs from the donor mapping.", "new_entry_tyres_preservation")
    return tyres.plan_tyre_sizes(clone, car_id=plan["installation_plan"]["car_id"], selections=[
        {"table": row["table"], "part_id": row["cloned_part_id"], "size": row["size"]} for row in rows])


def verify_new_entry_tyres(source_db, clone_db, output_db, plan):
    """Independently verify two closed snapshots; build receipts are not inputs."""
    source, clone, output = map(_source_path, (source_db, clone_db, output_db))
    _need(len({source, clone, output}) == 3, "Source, clone and final snapshot must be separate files.")
    identities = {path: _identity(path) for path in (source, clone, output)}
    hashes = {path: specdb._sha(path) for path in (source, clone, output)}
    fresh = _fresh_plan(source, plan)
    clone_check = _verify_clone(source, clone, fresh["installation_plan"])
    tyre_plan = _cloned_tyre_plan(clone, fresh)
    tyre_check = tyres.verify_tyre_sizes(clone, output, tyre_plan)
    with specdb._source(output) as (db, _, _):
        _need(not list(db.execute("PRAGMA foreign_key_check")), "The final snapshot has unresolved foreign keys.")
    for path in (source, clone, output):
        _assert_source(path, identities[path], hashes[path])
    return {"schema_version": SCHEMA_VERSION, "kind": "gt6_new_entry_tyre_composition_verification", **_SCOPE,
        "verified": True, "plan_sha256": fresh["plan_sha256"], "source_sha256": hashes[source],
        "clone_sha256": hashes[clone], "output_sha256": hashes[output],
        "clone_validation": clone_check, "tyre_plan": tyre_plan, "tyre_validation": tyre_check,
        "all_three_input_snapshots_unchanged": True, "independent_of_build_receipts": True}


def _save_json(path, value, owned):
    _fresh_path(path)
    with path.open("xb") as stream:
        owned.append((path, _identity(path)))
        stream.write((_json(value) + "\n").encode("utf-8"))


def build_new_entry_tyres(source_db, output_dir, plan):
    """Create a fresh developer directory; retain the clone if the tyre stage fails.

    COMPOSITION.json is published only after independent two-edge checking.
    Failures keep the pinned plan, clone and FAILED.json, while removing this
    invocation's final candidate/receipt. Existing paths are never replaced.
    """
    source = _source_path(source_db); source_identity = _identity(source)
    fresh = _fresh_plan(source, plan)
    output = _fresh_path(output_dir)
    _need(output not in {source, *(Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal"))},
          "The composition directory cannot occupy a source SQLite sidecar path.")
    output.mkdir()
    clone, final = output / "clone.sqlite", output / "nominal_tyres.sqlite"
    final_owned = []
    try:
        _save_json(output / "PLAN.json", fresh, [])
        clone_receipt = specdb.build_database_copy(source, clone, fresh["installation_plan"])
        _save_json(output / "CLONE_BUILD.json", clone_receipt, [])
        clone_check = _verify_clone(source, _source_path(clone), fresh["installation_plan"])
        _need(_json(_mapping_rows(clone_receipt["parts_id_maps"])) == _json(clone_check["parts_id_maps"]),
              "The builder's clone map differs from the independently verified rows.", "new_entry_tyres_preservation")
        _save_json(output / "CLONE_VERIFIED.json", clone_check, [])
        tyre_plan = _cloned_tyre_plan(clone, fresh)
        _save_json(output / "TYRE_PLAN.json", tyre_plan, [])
        tyres.build_tyre_sizes(clone, final, tyre_plan)
        for path in (final, Path(str(final) + ".tyres.json")):
            final_owned.append((path, _identity(path)))
        verification = verify_new_entry_tyres(source, clone, final, fresh)
        _assert_source(source, source_identity, fresh["source"]["sha256"])
        receipt = {"schema_version": SCHEMA_VERSION, "kind": "gt6_new_entry_tyre_composition_candidate", **_SCOPE,
            "passed": True, "plan_sha256": fresh["plan_sha256"], "source": fresh["source"],
            "new_car_id": fresh["installation_plan"]["car_id"], "selected_mappings": fresh["selected_mappings"],
            "clone": {"path": str(clone), "sha256": verification["clone_sha256"]},
            "output": {"path": str(final), "sha256": verification["output_sha256"]},
            "verification": verification, "game_files_changed": False,
            "receipt_path": str(output / "COMPOSITION.json")}
        _save_json(output / "COMPOSITION.json", receipt, final_owned)
        _assert_source(source, source_identity, fresh["source"]["sha256"])
        return receipt
    except BaseException as error:
        _remove_owned(final_owned)
        _save_json(output / "FAILED.json", {"schema_version": SCHEMA_VERSION, **_SCOPE,
            "passed": False, "error_type": type(error).__name__, "error_code": getattr(error, "code", None),
            "plan_sha256": fresh["plan_sha256"], "clone_retained": clone.exists()}, [])
        raise
