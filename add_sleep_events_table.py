import sqlite3


DB_NAME = "nhpsg.db"

SLEEP_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sleep_events (
    sleep_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    shift_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('fell_asleep', 'woke_up')),
    event_datetime TEXT NOT NULL,
    recorded_by_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def migrate(conn):
    conn.execute(SLEEP_EVENTS_TABLE_SQL)


def main():
    conn = sqlite3.connect(DB_NAME)
    try:
        with conn:
            migrate(conn)
    finally:
        conn.close()
    print("Sleep events table created.")


if __name__ == "__main__":
    main()
