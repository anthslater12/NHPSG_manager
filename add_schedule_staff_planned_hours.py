"""Add and backfill per-worker planned Schedule hours."""

import argparse
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path


PLANNED_TIME_COLUMNS = {
    "planned_start_time": "TEXT CHECK (\n"
    "    planned_start_time IS NULL OR (\n"
    "        length(planned_start_time) = 5\n"
    "        AND planned_start_time GLOB '[0-9][0-9]:[0-9][0-9]'\n"
    "        AND CAST(substr(planned_start_time, 1, 2) AS INTEGER) BETWEEN 0 AND 23\n"
    "        AND CAST(substr(planned_start_time, 4, 2) AS INTEGER) BETWEEN 0 AND 59\n"
    "    )\n"
    ")",
    "planned_end_time": "TEXT CHECK (\n"
    "    planned_end_time IS NULL OR (\n"
    "        length(planned_end_time) = 5\n"
    "        AND planned_end_time GLOB '[0-9][0-9]:[0-9][0-9]'\n"
    "        AND CAST(substr(planned_end_time, 1, 2) AS INTEGER) BETWEEN 0 AND 23\n"
    "        AND CAST(substr(planned_end_time, 4, 2) AS INTEGER) BETWEEN 0 AND 59\n"
    "    )\n"
    ")",
}

TIME_PATTERN = re.compile(r"^[0-9]{2}:[0-9]{2}$")


def _parse_time(value, schedule_staff_id, field_name):
    if not isinstance(value, str) or not TIME_PATTERN.fullmatch(value):
        raise ValueError(
            f"schedule_staff_id {schedule_staff_id}: "
            f"{field_name} must use HH:MM."
        )
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as error:
        raise ValueError(
            f"schedule_staff_id {schedule_staff_id}: "
            f"{field_name} must use a valid HH:MM time."
        ) from error


def _validate_assignments(conn):
    rows = conn.execute("""
        SELECT ss.schedule_staff_id,
               ss.planned_start_time,
               ss.planned_end_time,
               s.shift_type
        FROM schedule_staff AS ss
        LEFT JOIN schedule_shifts AS s
          ON s.schedule_shift_id = ss.schedule_shift_id
        ORDER BY ss.schedule_staff_id
    """).fetchall()

    for row in rows:
        assignment_id = row[0]
        if row[3] is None:
            raise ValueError(
                f"schedule_staff_id {assignment_id}: parent schedule shift is missing."
            )
        start = _parse_time(row[1], assignment_id, "planned_start_time")
        end = _parse_time(row[2], assignment_id, "planned_end_time")
        if row[3] in ("Day", "Afternoon") and end <= start:
            raise ValueError(
                f"schedule_staff_id {assignment_id}: "
                f"{row[3]} hours must end after they start."
            )
        if row[3] == "Overnight" and end == start:
            raise ValueError(
                f"schedule_staff_id {assignment_id}: "
                "Overnight hours cannot have equal start and end times."
            )


def _validate_parent_defaults(conn):
    rows = conn.execute("""
        SELECT ss.schedule_staff_id,
               s.planned_start_time,
               s.planned_end_time,
               s.shift_type
        FROM schedule_staff AS ss
        LEFT JOIN schedule_shifts AS s
          ON s.schedule_shift_id = ss.schedule_shift_id
        ORDER BY ss.schedule_staff_id
    """).fetchall()

    for row in rows:
        assignment_id = row[0]
        if row[3] is None:
            raise ValueError(
                f"schedule_staff_id {assignment_id}: parent schedule shift is missing."
            )
        start = _parse_time(row[1], assignment_id, "schedule shift planned_start_time")
        end = _parse_time(row[2], assignment_id, "schedule shift planned_end_time")
        if row[3] in ("Day", "Afternoon") and end <= start:
            raise ValueError(
                f"schedule_staff_id {assignment_id}: "
                f"{row[3]} hours must end after they start."
            )
        if row[3] == "Overnight" and end == start:
            raise ValueError(
                f"schedule_staff_id {assignment_id}: "
                "Overnight hours cannot have equal start and end times."
            )


def migrate(conn):
    """Add columns, backfill defaults, and validate the complete assignment set."""
    if conn.in_transaction:
        raise RuntimeError(
            "Schedule planned-hours migration requires no active transaction."
        )

    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")

    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(schedule_staff)")
    }
    missing_columns = [
        column for column in PLANNED_TIME_COLUMNS
        if column not in existing_columns
    ]

    try:
        conn.execute("BEGIN IMMEDIATE")
        for column in missing_columns:
            conn.execute(
                f"ALTER TABLE schedule_staff ADD COLUMN {column} "
                f"{PLANNED_TIME_COLUMNS[column]}"
            )

        _validate_parent_defaults(conn)
        for column in PLANNED_TIME_COLUMNS:
            conn.execute(f"""
                UPDATE schedule_staff
                SET {column} = (
                    SELECT s.{column}
                    FROM schedule_shifts AS s
                    WHERE s.schedule_shift_id = schedule_staff.schedule_shift_id
                )
                WHERE {column} IS NULL OR TRIM({column}) = ''
            """)

        _validate_assignments(conn)
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise

    return bool(missing_columns)


def _resolved_database_path(command_line_path=None):
    selected = command_line_path or os.environ.get("NHPSG_DB_PATH") or "nhpsg.db"
    return Path(selected).expanduser().resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Add and backfill Schedule per-worker planned hours."
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
    print("Schedule staff planned hours migration completed.")


if __name__ == "__main__":
    main()
