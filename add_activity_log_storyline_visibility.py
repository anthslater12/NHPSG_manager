import sqlite3


DB_NAME = "nhpsg.db"
TABLE_NAME = "activity_log"
COLUMN_NAME = "storyline_visible"


def column_info(conn):
    return {
        row[1]: row
        for row in conn.execute(
            'PRAGMA table_info("activity_log")'
        ).fetchall()
    }


def schema_is_current(conn):
    column = column_info(conn).get(COLUMN_NAME)
    return (
        column is not None
        and column[3] == 1
        and str(column[4]).strip() == "0"
    )


def migrate(conn):
    if conn.in_transaction:
        raise RuntimeError(
            "The Activity Log Storyline migration must start outside an "
            "existing transaction."
        )

    if not column_info(conn):
        raise RuntimeError('Required prerequisite table "activity_log" is missing.')
    if schema_is_current(conn):
        return False

    try:
        conn.execute("BEGIN IMMEDIATE")
        if COLUMN_NAME not in column_info(conn):
            conn.execute(
                'ALTER TABLE "activity_log" '
                'ADD COLUMN "storyline_visible" INTEGER NOT NULL DEFAULT 0'
            )
        if not schema_is_current(conn):
            raise RuntimeError(
                "Activity Log Storyline schema verification failed."
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
        print("Activity Log Storyline visibility migration applied.")


if __name__ == "__main__":
    main()
