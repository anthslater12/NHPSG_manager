"""Add the nullable authoritative event timestamp to Activity Log."""

import sqlite3


DB_NAME = "nhpsg.db"
COLUMN_NAME = "event_datetime"


def column_info(conn):
    return {
        row[1]: row
        for row in conn.execute('PRAGMA table_info("activity_log")').fetchall()
    }


def migrate(conn):
    if conn.in_transaction:
        raise RuntimeError(
            "The Activity Log event datetime migration must start outside an "
            "existing transaction."
        )
    if not column_info(conn):
        raise RuntimeError('Required prerequisite table "activity_log" is missing.')
    if COLUMN_NAME in column_info(conn):
        return False

    try:
        conn.execute("BEGIN IMMEDIATE")
        if COLUMN_NAME not in column_info(conn):
            conn.execute(
                'ALTER TABLE "activity_log" '
                'ADD COLUMN "event_datetime" TEXT NULL'
            )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    return True


def main():
    conn = sqlite3.connect(DB_NAME)
    try:
        changed = migrate(conn)
    finally:
        conn.close()
    if changed:
        print("Activity Log event datetime migration applied.")


if __name__ == "__main__":
    main()
