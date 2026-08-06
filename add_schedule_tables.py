import sqlite3


DB_NAME = "nhpsg.db"


SCHEDULE_SHIFTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schedule_shifts (
    schedule_shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    shift_date TEXT NOT NULL CHECK (
        length(shift_date) = 10
        AND shift_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
    ),
    shift_type TEXT NOT NULL CHECK (
        shift_type IN ('Day', 'Afternoon', 'Overnight')
    ),
    planned_start_time TEXT NOT NULL CHECK (
        length(planned_start_time) = 5
        AND planned_start_time GLOB '[0-9][0-9]:[0-9][0-9]'
        AND CAST(substr(planned_start_time, 1, 2) AS INTEGER) BETWEEN 0 AND 23
        AND CAST(substr(planned_start_time, 4, 2) AS INTEGER) BETWEEN 0 AND 59
    ),
    planned_end_time TEXT NOT NULL CHECK (
        length(planned_end_time) = 5
        AND planned_end_time GLOB '[0-9][0-9]:[0-9][0-9]'
        AND CAST(substr(planned_end_time, 1, 2) AS INTEGER) BETWEEN 0 AND 23
        AND CAST(substr(planned_end_time, 4, 2) AS INTEGER) BETWEEN 0 AND 59
    ),
    status TEXT NOT NULL DEFAULT 'Draft' CHECK (
        status IN ('Draft', 'Published', 'Closed', 'Cancelled')
    ),
    notes TEXT,
    created_by INTEGER NOT NULL,
    created_at_utc TEXT NOT NULL CHECK (
        length(created_at_utc) = 20
        AND created_at_utc GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T'
            || '[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
    ),
    updated_by INTEGER NOT NULL,
    updated_at_utc TEXT NOT NULL CHECK (
        length(updated_at_utc) = 20
        AND updated_at_utc GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T'
            || '[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
    ),
    FOREIGN KEY (client_id) REFERENCES clients(client_id),
    FOREIGN KEY (created_by) REFERENCES users(user_id),
    FOREIGN KEY (updated_by) REFERENCES users(user_id),
    UNIQUE (client_id, shift_date, shift_type)
)
"""

SCHEDULE_STAFF_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schedule_staff (
    schedule_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_shift_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    planned_start_time TEXT CHECK (
        planned_start_time IS NULL
        OR (
            length(planned_start_time) = 5
            AND planned_start_time GLOB '[0-9][0-9]:[0-9][0-9]'
            AND CAST(substr(planned_start_time, 1, 2) AS INTEGER) BETWEEN 0 AND 23
            AND CAST(substr(planned_start_time, 4, 2) AS INTEGER) BETWEEN 0 AND 59
        )
    ),
    planned_end_time TEXT CHECK (
        planned_end_time IS NULL
        OR (
            length(planned_end_time) = 5
            AND planned_end_time GLOB '[0-9][0-9]:[0-9][0-9]'
            AND CAST(substr(planned_end_time, 1, 2) AS INTEGER) BETWEEN 0 AND 23
            AND CAST(substr(planned_end_time, 4, 2) AS INTEGER) BETWEEN 0 AND 59
        )
    ),
    assignment_note TEXT,
    assigned_by INTEGER NOT NULL,
    assigned_at_utc TEXT NOT NULL CHECK (
        length(assigned_at_utc) = 20
        AND assigned_at_utc GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T'
            || '[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
    ),
    FOREIGN KEY (schedule_shift_id)
        REFERENCES schedule_shifts(schedule_shift_id)
        ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (assigned_by) REFERENCES users(user_id),
    UNIQUE (schedule_shift_id, user_id)
)
"""

INDEXES = (
    ("idx_schedule_shifts_shift_date", "schedule_shifts(shift_date)"),
    (
        "idx_schedule_shifts_client_date",
        "schedule_shifts(client_id, shift_date)"
    ),
    (
        "idx_schedule_shifts_client_date_type",
        "schedule_shifts(client_id, shift_date, shift_type)"
    ),
    ("idx_schedule_shifts_status", "schedule_shifts(status)"),
    ("idx_schedule_staff_shift", "schedule_staff(schedule_shift_id)"),
    ("idx_schedule_staff_user", "schedule_staff(user_id)"),
)


def migrate(conn):
    """Create the Schedule V1 schema without modifying existing tables."""
    if conn.in_transaction:
        conn.commit()

    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(SCHEDULE_SHIFTS_TABLE_SQL)
        conn.execute(SCHEDULE_STAFF_TABLE_SQL)
        for index_name, indexed_tables in INDEXES:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {indexed_tables}"
            )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def main():
    conn = sqlite3.connect(DB_NAME)
    try:
        migrate(conn)
    finally:
        conn.close()
    print("Schedule tables created.")


if __name__ == "__main__":
    main()
