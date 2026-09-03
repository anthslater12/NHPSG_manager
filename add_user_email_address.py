"""Add an optional email address to NHPSG user accounts."""

import argparse
import os
import sqlite3
from pathlib import Path


EMAIL_COLUMN = "email_address"


def migrate(conn):
    """Add the nullable users.email_address column when it is missing."""
    if conn.in_transaction:
        raise RuntimeError(
            "User email-address migration requires no active transaction."
        )

    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(users)")
    }

    if not existing_columns:
        raise RuntimeError("The users table does not exist.")

    if EMAIL_COLUMN in existing_columns:
        return False

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "ALTER TABLE users ADD COLUMN email_address TEXT"
        )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise

    return True


def _resolved_database_path(command_line_path=None):
    selected = (
        command_line_path
        or os.environ.get("NHPSG_DB_PATH")
        or "nhpsg.db"
    )
    return Path(selected).expanduser().resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Add an optional email address to NHPSG user accounts."
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
        changed = migrate(conn)
    finally:
        conn.close()

    if changed:
        print("User email-address migration completed.")
    else:
        print("User email-address migration already applied.")


if __name__ == "__main__":
    main()
