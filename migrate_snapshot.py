"""Transfer snapshot state while both writers are stopped."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from snapshot_state import PostgresSnapshotStore, SnapshotStore, build_board_snapshot


def validate(state):
    if (
        not isinstance(state, dict)
        or state.get("version") != 1
        or type(state.get("revision")) is not int
        or state["revision"] < 1
        or not isinstance(state.get("cache"), dict)
        or not state["cache"].get("gold_bar_data")
        or state.get("snapshot") != build_board_snapshot(state["cache"])
    ):
        raise ValueError("Invalid or inconsistent snapshot state")
    for key in ("updated_at", "last_checked_at"):
        if datetime.fromisoformat(state[key]).utcoffset() is None:
            raise ValueError(f"{key} must include a timezone")
    return state


def import_file(store, path):
    from psycopg.types.json import Jsonb

    state = validate(json.loads(path.read_text(encoding="utf-8")))
    store.initialize()
    with store._connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(74123001)")
        inserted = conn.execute(
            "INSERT INTO gold_snapshot (id, state) VALUES (1, %s) "
            "ON CONFLICT (id) DO NOTHING RETURNING id",
            (Jsonb(state),),
        ).fetchone()
        if not inserted:
            raise ValueError("Target database already has a snapshot")
    return state


def export_file(store, path):
    state = validate(store.load())
    target = SnapshotStore(path)
    with target._exclusive_lock():
        if path.exists():
            raise ValueError("Export requires a new file path")
        target._atomic_write(state)
    return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("import", "export"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    dsn = os.getenv("SNAPSHOT_DATABASE_URL")
    if not dsn:
        parser.error("Set SNAPSHOT_DATABASE_URL")
    operation = import_file if args.operation == "import" else export_file
    try:
        state = operation(PostgresSnapshotStore(dsn), args.path)
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, f"Migration failed: {error}\n")
    print(f"{args.operation}: revision {state['revision']}")
