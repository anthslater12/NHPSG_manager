"""Create the per-client Schedule staff ordering table."""

import argparse
import os
import sqlite3
from pathlib import Path


SCHEDULE_STAFF_ORDER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schedule_staff_order (
    client_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    display_order INTEGER NOT NULL CHECK (display_order >= 1),
    updated_by INTEGER NOT NULL,
    updated_at_utc TEXT NOT NULL CHECK (
        length(updated_at_utc) = 20
        AND updated_at_utc GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T'
            || '[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
    ),
    PRIMARY KEY (client_id, user_id),
    UNIQUE (client_id, display_order),
    FOREIGN KEY (client_id) REFERENCES clients(client_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (updated_by) REFERENCES users(user_id)
)
"""


def migrate(conn):
    """Create the ordering table transactionally and idempotently."""
    if conn.in_transaction:
        conn.commit()

    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(SCHEDULE_STAFF_ORDER_TABLE_SQL)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def _resolved_database_path(command_line_path=None):
    selected = command_line_path or os.environ.get("NHPSG_DB_PATH") or "nhpsg.db"
    return Path(selected).expanduser().resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create the Schedule per-client staff ordering table."
    )
    parser.add_argument(
        "database",
        nargs="?",
        help="SQLite database path; overrides NHPSG_DB_PATH.",
    )
    args = parser.parse_args(argv)
    database_path = _resolved_database_path(args.database)
    print(f"Resolved database path: {database_path}")
    if not database_path.exists():
        raise SystemExit(f"Database does not exist: {database_path}")

    conn = sqlite3.connect(str(database_path))
    try:
        migrate(conn)
    finally:
        conn.close()
    print("Schedule staff ordering migration completed.")


if __name__ == "__main__":
    main()
