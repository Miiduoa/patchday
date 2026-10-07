"""Copy with SQLite's backup API, execute one transaction, inspect, discard."""
from __future__ import annotations

import math
from pathlib import Path
import sqlite3
import time
from typing import Iterable


class InputError(ValueError):
    """The input cannot be rehearsed."""


def statements(sql: str) -> Iterable[str]:
    # complete_statement understands quoted semicolons and CREATE TRIGGER bodies.
    start = 0
    for end, char in enumerate(sql):
        if char == ";" and sqlite3.complete_statement(sql[start:end + 1]):
            yield sql[start:end + 1]
            start = end + 1
    tail = sql[start:].strip()
    if tail:
        # execute() accepts a final statement without a semicolon and comments.
        yield tail


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def schema(db: sqlite3.Connection) -> dict:
    objects = {}
    for kind, name, table, sql in db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ):
        item = {"type": kind, "name": name, "table": table, "sql": sql}
        if kind == "table":
            item["rows"] = db.execute(f"SELECT count(*) FROM {quote(name)}").fetchone()[0]
            # table_info omits generated/hidden columns; DDL text is not safe to
            # parse for identifiers (quotes, comments and constraints vary).
            item["columns"] = [row[1] for row in db.execute(f"PRAGMA main.table_xinfo({quote(name)})")]
        objects[f"{kind}:{name}"] = item
    return objects


def changes(before: dict, after: dict) -> list[dict]:
    result = []
    for key in sorted(before.keys() | after.keys()):
        a, b = before.get(key), after.get(key)
        if a == b:
            continue
        status = "added" if a is None else "removed" if b is None else "changed"
        item = b or a
        result.append({"object": key, "type": item["type"], "name": item["name"],
                       "status": status, "before": a, "after": b})
    return result


def restrictions(action, arg1, arg2, _database, _trigger):
    forbidden = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION,
                 sqlite3.SQLITE_SAVEPOINT, sqlite3.SQLITE_CREATE_VTABLE,
                 sqlite3.SQLITE_DROP_VTABLE}
    if action in forbidden:
        return sqlite3.SQLITE_DENY
    # SQLite itself invokes quick_check while adding a CHECK-constrained column.
    # It only reads the snapshot; all other PRAGMAs remain disallowed.
    if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() != "quick_check":
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in {"load_extension", "readfile", "writefile"}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def checks(db: sqlite3.Connection) -> dict:
    integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
    foreign_keys = [list(row) for row in db.execute("PRAGMA foreign_key_check")]
    return {"integrity": integrity, "foreign_key_violations": foreign_keys,
            "passed": integrity == ["ok"] and not foreign_keys}


def rehearse(database: Path | str, migrations: list[Path | str], *, timeout: float = 10.0) -> dict:
    """Rehearse files in the supplied order; never open the source for writing.

    A rehearsal is a single transaction on a private in-memory backup. This is
    a guard against accidental writes, not a sandbox for hostile SQL.
    """
    database = Path(database).resolve()
    if not database.is_file():
        raise InputError(f"Database does not exist: {database.name}")
    if not math.isfinite(timeout) or timeout <= 0:
        raise InputError("Timeout must be a finite positive number.")
    if not migrations:
        raise InputError("Provide at least one SQL migration.")
    scripts = []
    seen = set()
    for path in migrations:
        path = Path(path).resolve()
        if path in seen:
            raise InputError(f"Migration listed twice: {path.name}")
        if path == database or not path.is_file():
            raise InputError(f"Invalid migration file: {path.name}")
        seen.add(path)
        try:
            sql = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise InputError(f"Cannot read migration: {path.name}") from exc
        if not sql.strip():
            raise InputError(f"Migration is empty: {path.name}")
        scripts.append((path.name, sql))
    started = time.monotonic()
    deadline = started + timeout

    def expired():
        return time.monotonic() > deadline

    def backup_progress(_status, _remaining, _total):
        if expired():
            raise TimeoutError("Snapshot exceeded the rehearsal timeout.")

    db = sqlite3.connect(":memory:", isolation_level=None)
    source = None
    try:
        source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=min(timeout, 1.0))
        source.backup(db, pages=256, progress=backup_progress, sleep=0.01)
        source.close()
        source = None
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: int(expired()), 1000)
        before = schema(db)
        baseline = checks(db)
        result = {"format": "patchday-report-v1", "database": database.name,
                  "sqlite_version": sqlite3.sqlite_version, "status": "passed",
                  "snapshot_only": True, "rolled_back": False, "baseline": baseline,
                  "checks": None, "steps": [], "changes": [], "risks": [], "error": None}
        if not baseline["passed"]:
            result.update(status="failed", error="Source snapshot already fails integrity or foreign-key checks.")
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            return result
        db.execute("BEGIN")
        step = None
        try:
            for name, sql in scripts:
                step = {"file": name, "status": "running", "statements": 0, "changed_rows": 0, "elapsed_ms": 0}
                result["steps"].append(step)
                step_start = time.monotonic()
                total = db.total_changes
                db.set_authorizer(restrictions)
                try:
                    for index, statement in enumerate(statements(sql), 1):
                        step["statement_index"] = index
                        if expired():
                            raise TimeoutError("Rehearsal exceeded its time budget.")
                        db.execute(statement).close()
                        step["statements"] += 1
                finally:
                    db.set_authorizer(None)
                    step["elapsed_ms"] = round((time.monotonic() - step_start) * 1000, 2)
                step.update(status="rehearsed", changed_rows=db.total_changes - total)
            result["checks"] = checks(db)
            if not result["checks"]["passed"]:
                raise sqlite3.IntegrityError("Snapshot fails integrity or foreign-key checks after migration.")
            after = schema(db)
            result["changes"] = changes(before, after)
            for change in result["changes"]:
                old, new = change["before"], change["after"]
                if old and old["type"] == "table":
                    if new is None:
                        result["risks"].append(f"Table {old['name']} removed ({old['rows']} rows in the snapshot).")
                    elif new["rows"] < old["rows"]:
                        result["risks"].append(f"Table {old['name']} has fewer rows: {old['rows']} → {new['rows']}.")
                    if new is not None:
                        # SQLite folds ASCII identifier case only. Do not treat
                        # distinct Unicode names as equal via str.casefold().
                        fold = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
                        current = {name.translate(fold) for name in new["columns"]}
                        missing = [name for name in old["columns"] if name.translate(fold) not in current]
                        if missing:
                            names = ", ".join(quote(name) for name in missing)
                            result["risks"].append(
                                f"Table {old['name']} no longer has columns {names} "
                                f"(removed or renamed; {old['rows']} rows before migration)."
                            )
            if result["risks"]:
                result["status"] = "review"
            # Commit exercises deferred constraints. This commits only the disposable copy.
            db.execute("COMMIT")
        except (sqlite3.Error, TimeoutError) as exc:
            db.set_authorizer(None)
            db.set_progress_handler(None, 0)
            if db.in_transaction:
                db.execute("ROLLBACK")
            result.update(status="failed", rolled_back=True, changes=[], risks=[], error=str(exc))
            if step and step["status"] == "running":
                step["status"] = "failed"
            for done in result["steps"]:
                if done["status"] == "rehearsed":
                    done["status"] = "rolled_back"
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
        return result
    except (sqlite3.Error, TimeoutError) as exc:
        raise InputError(f"Cannot inspect database snapshot: {exc}") from exc
    finally:
        if source is not None:
            source.close()
        db.close()
