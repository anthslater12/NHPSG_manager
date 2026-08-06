import sqlite3


def migrate(conn):
    """Add the optional Sleep note field to an existing installation."""
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sleep_events'"
    ).fetchone()
    if table is None:
        return

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(sleep_events)").fetchall()
    }
    if "note" not in columns:
        conn.execute("ALTER TABLE sleep_events ADD COLUMN note TEXT NULL")
