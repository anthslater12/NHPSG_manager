import sqlite3


DB_NAME = "nhpsg.db"

SHIFTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shifts (
    shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    shift_date TEXT NOT NULL,
    shift_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Open',

    scheduled_start_time TEXT,
    scheduled_end_time TEXT,
    actual_end_at_utc TEXT NULL,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT
)
"""

SHIFT_STAFF_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shift_staff (
    shift_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,

    actual_start_time TEXT NOT NULL,
    actual_end_time TEXT,
    actual_end_at_utc TEXT NULL,

    sign_on_at TEXT DEFAULT CURRENT_TIMESTAMP,
    sign_off_at TEXT,

    active INTEGER NOT NULL DEFAULT 1
)
"""


def migrate(conn):
    conn.execute(SHIFTS_TABLE_SQL)
    conn.execute(SHIFT_STAFF_TABLE_SQL)


def main():
    conn = sqlite3.connect(DB_NAME)

    try:
        with conn:
            migrate(conn)
    finally:
        conn.close()

    print("Shift tables created.")


if __name__ == "__main__":
    main()
