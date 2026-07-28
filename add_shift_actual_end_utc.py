import sqlite3


DB_NAME = "nhpsg.db"

REQUIRED_COLUMNS = {
    "shifts": "actual_end_at_utc",
    "shift_staff": "actual_end_at_utc",
}


def table_exists(conn, table_name):
    return conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,)
    ).fetchone() is not None


def column_names(conn, table_name):
    return {
        row[1]
        for row in conn.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }


def schema_is_current(conn):
    return all(
        table_exists(conn, table_name)
        and column_name in column_names(conn, table_name)
        for table_name, column_name in REQUIRED_COLUMNS.items()
    )


def migrate(conn):
    if conn.in_transaction:
        raise RuntimeError(
            "The shift actual-end migration must start outside an "
            "existing transaction."
        )
    missing_tables = [
        table_name
        for table_name in REQUIRED_COLUMNS
        if not table_exists(conn, table_name)
    ]
    if missing_tables:
        raise RuntimeError(
            "Missing required shift table(s): "
            + ", ".join(sorted(missing_tables))
            + "."
        )
    if schema_is_current(conn):
        return False

    try:
        conn.execute("BEGIN IMMEDIATE")

        for table_name, column_name in REQUIRED_COLUMNS.items():
            if column_name not in column_names(conn, table_name):
                conn.execute(
                    f'ALTER TABLE "{table_name}" '
                    f'ADD COLUMN "{column_name}" TEXT NULL'
                )

        if not schema_is_current(conn):
            raise RuntimeError(
                "Shift actual-end schema verification failed."
            )

        integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
        if integrity_rows != [("ok",)]:
            raise RuntimeError(
                "SQLite integrity_check failed after shift actual-end "
                "migration."
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
        print("Shift actual-end migration completed.")
    else:
        print("Shift actual-end schema is already current.")


if __name__ == "__main__":
    main()
