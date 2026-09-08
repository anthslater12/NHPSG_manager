"""Activity schema migration, including the editable lifecycle foundation.

This module deliberately does not select or open a database. A caller must
provide the SQLite connection that should receive the schema.
"""

import os


LEGACY_COLUMNS = (
    "shift_activity_id",
    "shift_id",
    "recorded_by_user_id",
    "start_time",
    "end_time",
    "a_selected",
    "t_selected",
    "ls_selected",
    "activity_description",
    "created_at",
)

LIFECYCLE_COLUMNS = (
    "status",
    "completed_at_utc",
    "completed_by_user_id",
    "version_number",
)

LIFECYCLE_STATUSES = (
    "In Progress",
    "Completed",
    "Recorded",
)


CREATE_TABLE_SQL = """
CREATE TABLE shift_activities (
    shift_activity_id INTEGER PRIMARY KEY AUTOINCREMENT,

    shift_id INTEGER NOT NULL,
    recorded_by_user_id INTEGER NOT NULL,

    start_time TEXT NOT NULL,
    end_time TEXT,

    a_selected INTEGER NOT NULL DEFAULT 0 CHECK (
        a_selected IN (0, 1)
    ),
    t_selected INTEGER NOT NULL DEFAULT 0 CHECK (
        t_selected IN (0, 1)
    ),
    ls_selected INTEGER NOT NULL DEFAULT 0 CHECK (
        ls_selected IN (0, 1)
    ),

    activity_description TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    status TEXT NOT NULL DEFAULT 'Recorded' CHECK (
        status IN ('In Progress', 'Completed', 'Recorded')
    ),
    completed_at_utc TEXT CHECK (
        completed_at_utc IS NULL OR (
            typeof(completed_at_utc) = 'text'
            AND length(completed_at_utc) = 20
            AND completed_at_utc GLOB
                '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
            AND julianday(completed_at_utc) IS NOT NULL
            AND strftime('%Y-%m-%dT%H:%M:%SZ', julianday(completed_at_utc))
                = completed_at_utc
        )
    ),
    completed_by_user_id INTEGER REFERENCES users(user_id),
    version_number INTEGER NOT NULL DEFAULT 1 CHECK (version_number >= 1),

    FOREIGN KEY (shift_id)
        REFERENCES shifts(shift_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CONSTRAINT shift_activities_start_time CHECK (
        typeof(start_time) = 'text'
        AND length(start_time) = 5
        AND start_time GLOB '[0-9][0-9]:[0-9][0-9]'
        AND time(start_time) IS NOT NULL
        AND CAST(substr(start_time, 1, 2) AS INTEGER) BETWEEN 0 AND 23
        AND CAST(substr(start_time, 4, 2) AS INTEGER) BETWEEN 0 AND 59
    ),
    CONSTRAINT shift_activities_meaningful_in_progress CHECK (
        status <> 'In Progress'
        OR a_selected + t_selected + ls_selected >= 1
        OR length(trim(
            COALESCE(activity_description, ''),
            ' ' || char(9) || char(10) || char(11) || char(12) || char(13)
        )) > 0
    ),
    CONSTRAINT shift_activities_finalized_fields CHECK (
        status = 'In Progress'
        OR (
            length(trim(COALESCE(end_time, ''),
                ' ' || char(9) || char(10) || char(11) || char(12) || char(13)
            )) > 0
            AND time(start_time) IS NOT NULL
            AND time(end_time) IS NOT NULL
            AND time(end_time) > time(start_time)
            AND a_selected + t_selected + ls_selected >= 1
            AND length(trim(
                COALESCE(activity_description, ''),
                ' ' || char(9) || char(10) || char(11) || char(12) || char(13)
            )) > 0
        )
    ),
    CONSTRAINT shift_activities_completion_metadata CHECK (
        (status IN ('In Progress', 'Recorded')
         AND completed_at_utc IS NULL
         AND completed_by_user_id IS NULL)
        OR (status = 'Completed'
            AND completed_at_utc IS NOT NULL
            AND completed_by_user_id IS NOT NULL)
    )
)
"""


CREATE_SHIFT_CREATED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_shift_activities_shift_created
ON shift_activities (
    shift_id,
    created_at,
    shift_activity_id
)
"""


def _table_sql(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'shift_activities'"
    ).fetchone()
    return row[0] if row is not None else ""


def _needs_lifecycle_upgrade(conn, column_names):
    normalized_sql = " ".join(_table_sql(conn).lower().split())
    required_constraints = (
        "constraint shift_activities_start_time",
        "constraint shift_activities_meaningful_in_progress",
        "constraint shift_activities_finalized_fields",
        "constraint shift_activities_completion_metadata",
    )
    return (
        any(name not in column_names for name in LIFECYCLE_COLUMNS)
        or "end_time text not null" in normalized_sql
        or "activity_description text not null" in normalized_sql
        or not all(marker in normalized_sql for marker in required_constraints)
    )


def _create_table(conn):
    conn.execute(CREATE_TABLE_SQL)


def _rebuild_for_lifecycle(conn, existing_column_names):
    """Replace only the legacy Activities table inside the caller transaction."""
    legacy_table = "shift_activities_lifecycle_legacy"
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (legacy_table,),
    ).fetchone() is not None:
        raise RuntimeError(
            "An unfinished Activities lifecycle migration was found."
        )

    conn.execute(
        "ALTER TABLE shift_activities RENAME TO " + legacy_table
    )
    _create_table(conn)

    new_column_names = {
        row[1]
        for row in conn.execute("PRAGMA table_info(shift_activities)")
    }
    copy_columns = [
        name for name in existing_column_names if name in new_column_names
    ]
    column_list = ", ".join(copy_columns)
    conn.execute(
        "INSERT INTO shift_activities (" + column_list + ") "
        "SELECT " + column_list + " FROM " + legacy_table
    )
    conn.execute("DROP TABLE " + legacy_table)


def migrate(conn):
    """Idempotently create or upgrade the Activity table on ``conn``."""
    conn.execute("PRAGMA foreign_keys = ON")
    existing = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'shift_activities'"
    ).fetchone()

    caller_owned_transaction = conn.in_transaction
    savepoint = "shift_activities_lifecycle_migration"
    if caller_owned_transaction:
        conn.execute("SAVEPOINT " + savepoint)
    else:
        conn.execute("BEGIN")

    try:
        if existing is None:
            _create_table(conn)
        else:
            existing_column_names = tuple(
                row[1]
                for row in conn.execute("PRAGMA table_info(shift_activities)")
            )
            if _needs_lifecycle_upgrade(conn, set(existing_column_names)):
                _rebuild_for_lifecycle(conn, existing_column_names)

        conn.execute(CREATE_SHIFT_CREATED_INDEX_SQL)
    except Exception:
        if caller_owned_transaction:
            conn.execute("ROLLBACK TO SAVEPOINT " + savepoint)
            conn.execute("RELEASE SAVEPOINT " + savepoint)
        else:
            conn.rollback()
        raise
    else:
        if caller_owned_transaction:
            conn.execute("RELEASE SAVEPOINT " + savepoint)
        else:
            conn.commit()


def main():
    database_path = os.environ.get("NHPSG_DB_PATH", "nhpsg.db")
    import sqlite3

    conn = sqlite3.connect(database_path)
    try:
        migrate(conn)
    finally:
        conn.close()
    print("Shift activities table migration completed.")


if __name__ == "__main__":
    main()
