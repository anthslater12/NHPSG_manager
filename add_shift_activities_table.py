"""Activity Version 1 schema migration.

This module deliberately does not select or open a database. A caller must
provide the SQLite connection that should receive the schema.
"""


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shift_activities (
    shift_activity_id INTEGER PRIMARY KEY AUTOINCREMENT,

    shift_id INTEGER NOT NULL,
    recorded_by_user_id INTEGER NOT NULL,

    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,

    a_selected INTEGER NOT NULL DEFAULT 0 CHECK (
        a_selected IN (0, 1)
    ),
    t_selected INTEGER NOT NULL DEFAULT 0 CHECK (
        t_selected IN (0, 1)
    ),
    ls_selected INTEGER NOT NULL DEFAULT 0 CHECK (
        ls_selected IN (0, 1)
    ),

    activity_description TEXT NOT NULL CHECK (
        length(trim(
            activity_description,
            ' ' || char(9) || char(10) || char(11) || char(12) || char(13)
        )) > 0
    ),

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (shift_id)
        REFERENCES shifts(shift_id)
        ON DELETE RESTRICT,
    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT,

    CHECK (a_selected + t_selected + ls_selected >= 1)
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


def migrate(conn):
    """Create the Activity V1 table and index on ``conn``."""
    conn.execute("PRAGMA foreign_keys = ON")

    with conn:
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_SHIFT_CREATED_INDEX_SQL)
