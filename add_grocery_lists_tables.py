"""Create and validate the Grocery Lists current and snapshot schema."""

import sqlite3


DB_NAME = "nhpsg.db"

TABLE_SQL = {
    "grocery_lists": """
        CREATE TABLE IF NOT EXISTS grocery_lists (
            grocery_list_id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            title TEXT NOT NULL CHECK (length(trim(title)) > 0),
            created_by_user_id INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY (client_id)
                REFERENCES clients(client_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (created_by_user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT,
            UNIQUE (client_id)
        )
    """,
    "grocery_list_sections": """
        CREATE TABLE IF NOT EXISTS grocery_list_sections (
            section_id INTEGER PRIMARY KEY AUTOINCREMENT,
            grocery_list_id INTEGER NOT NULL,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            display_order INTEGER NOT NULL DEFAULT 0
                CHECK (display_order >= 0),
            FOREIGN KEY (grocery_list_id)
                REFERENCES grocery_lists(grocery_list_id)
                ON DELETE CASCADE,
            UNIQUE (grocery_list_id, name)
        )
    """,
    "grocery_list_items": """
        CREATE TABLE IF NOT EXISTS grocery_list_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            section_id INTEGER NOT NULL,
            item_name TEXT NOT NULL CHECK (length(trim(item_name)) > 0),
            stock_text TEXT NULL,
            needed_text TEXT NULL,
            purchased INTEGER NOT NULL DEFAULT 0
                CHECK (purchased IN (0, 1)),
            display_order INTEGER NOT NULL DEFAULT 0
                CHECK (display_order >= 0),
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            updated_by_user_id INTEGER NOT NULL,
            FOREIGN KEY (section_id)
                REFERENCES grocery_list_sections(section_id)
                ON DELETE CASCADE,
            FOREIGN KEY (updated_by_user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT
        )
    """,
    "grocery_list_shares": """
        CREATE TABLE IF NOT EXISTS grocery_list_shares (
            share_id INTEGER PRIMARY KEY AUTOINCREMENT,
            grocery_list_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            permission TEXT NOT NULL
                CHECK (permission IN ('VIEW', 'EDIT')),
            shared_by_user_id INTEGER NOT NULL,
            shared_at_utc TEXT NOT NULL,
            FOREIGN KEY (grocery_list_id)
                REFERENCES grocery_lists(grocery_list_id)
                ON DELETE CASCADE,
            FOREIGN KEY (user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (shared_by_user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT,
            UNIQUE (grocery_list_id, user_id)
        )
    """,
    "grocery_list_email_recipients": """
        CREATE TABLE IF NOT EXISTS grocery_list_email_recipients (
            recipient_id INTEGER PRIMARY KEY AUTOINCREMENT,
            grocery_list_id INTEGER NOT NULL,
            display_name TEXT NULL,
            email_address TEXT NOT NULL
                CHECK (email_address = trim(email_address))
                CHECK (length(email_address) > 0)
                CHECK (length(email_address) <= 254),
            created_by_user_id INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_by_user_id INTEGER NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY (grocery_list_id)
                REFERENCES grocery_lists(grocery_list_id)
                ON DELETE CASCADE,
            FOREIGN KEY (created_by_user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (updated_by_user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT,
            UNIQUE (grocery_list_id, email_address)
        )
    """,
    "grocery_list_snapshots": """
        CREATE TABLE IF NOT EXISTS grocery_list_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            grocery_list_id INTEGER NOT NULL,
            snapshot_kind TEXT NOT NULL
                CHECK (snapshot_kind IN ('WEEKLY', 'EMAIL')),
            week_start TEXT NULL,
            captured_by_user_id INTEGER NOT NULL,
            captured_at_utc TEXT NOT NULL,
            CHECK (
                (
                    snapshot_kind = 'WEEKLY'
                    AND week_start IS NOT NULL
                    AND week_start = date(week_start)
                )
                OR (
                    snapshot_kind = 'EMAIL'
                    AND week_start IS NULL
                )
            ),
            FOREIGN KEY (grocery_list_id)
                REFERENCES grocery_lists(grocery_list_id)
                ON DELETE RESTRICT,
            FOREIGN KEY (captured_by_user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT
        )
    """,
    "grocery_list_snapshot_sections": """
        CREATE TABLE IF NOT EXISTS grocery_list_snapshot_sections (
            snapshot_section_id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            name TEXT NOT NULL CHECK (length(trim(name)) > 0),
            display_order INTEGER NOT NULL
                CHECK (display_order >= 0),
            FOREIGN KEY (snapshot_id)
                REFERENCES grocery_list_snapshots(snapshot_id)
                ON DELETE CASCADE
        )
    """,
    "grocery_list_snapshot_items": """
        CREATE TABLE IF NOT EXISTS grocery_list_snapshot_items (
            snapshot_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_section_id INTEGER NOT NULL,
            item_name TEXT NOT NULL CHECK (length(trim(item_name)) > 0),
            stock_text TEXT NULL,
            needed_text TEXT NULL,
            purchased INTEGER NOT NULL
                CHECK (purchased IN (0, 1)),
            display_order INTEGER NOT NULL
                CHECK (display_order >= 0),
            FOREIGN KEY (snapshot_section_id)
                REFERENCES grocery_list_snapshot_sections(snapshot_section_id)
                ON DELETE CASCADE
        )
    """,
}

INDEXES = (
    (
        "idx_grocery_list_sections_list",
        "grocery_list_sections(grocery_list_id)",
    ),
    (
        "idx_grocery_list_items_section",
        "grocery_list_items(section_id)",
    ),
    (
        "idx_grocery_list_shares_list",
        "grocery_list_shares(grocery_list_id)",
    ),
    (
        "idx_grocery_list_shares_user",
        "grocery_list_shares(user_id)",
    ),
    (
        "idx_grocery_list_email_recipients_list",
        "grocery_list_email_recipients(grocery_list_id)",
    ),
    (
        "idx_grocery_list_snapshots_list_week",
        "grocery_list_snapshots(grocery_list_id, week_start)",
    ),
    (
        "idx_grocery_list_snapshot_sections_snapshot",
        "grocery_list_snapshot_sections(snapshot_id)",
    ),
    (
        "idx_grocery_list_snapshot_items_section",
        "grocery_list_snapshot_items(snapshot_section_id)",
    ),
)

PARTIAL_UNIQUE_INDEXES = (
    (
        "idx_grocery_list_snapshots_weekly_unique",
        "grocery_list_snapshots(grocery_list_id, week_start)",
        "snapshot_kind = 'WEEKLY'",
    ),
)

EXPECTED_COLUMNS = {
    "grocery_lists": {
        "grocery_list_id",
        "client_id",
        "title",
        "created_by_user_id",
        "created_at_utc",
        "updated_at_utc",
    },
    "grocery_list_sections": {
        "section_id",
        "grocery_list_id",
        "name",
        "display_order",
    },
    "grocery_list_items": {
        "item_id",
        "section_id",
        "item_name",
        "stock_text",
        "needed_text",
        "purchased",
        "display_order",
        "created_at_utc",
        "updated_at_utc",
        "updated_by_user_id",
    },
    "grocery_list_shares": {
        "share_id",
        "grocery_list_id",
        "user_id",
        "permission",
        "shared_by_user_id",
        "shared_at_utc",
    },
    "grocery_list_email_recipients": {
        "recipient_id",
        "grocery_list_id",
        "display_name",
        "email_address",
        "created_by_user_id",
        "created_at_utc",
        "updated_by_user_id",
        "updated_at_utc",
    },
    "grocery_list_snapshots": {
        "snapshot_id",
        "grocery_list_id",
        "snapshot_kind",
        "week_start",
        "captured_by_user_id",
        "captured_at_utc",
    },
    "grocery_list_snapshot_sections": {
        "snapshot_section_id",
        "snapshot_id",
        "name",
        "display_order",
    },
    "grocery_list_snapshot_items": {
        "snapshot_item_id",
        "snapshot_section_id",
        "item_name",
        "stock_text",
        "needed_text",
        "purchased",
        "display_order",
    },
}


def _table_sql(conn, table_name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return None if row is None else row[0]


def _index_columns(conn, index_name):
    return tuple(
        row[2]
        for row in conn.execute(
            "SELECT * FROM pragma_index_info(?) ORDER BY seqno",
            (index_name,),
        )
    )


def _has_unique_columns(conn, table_name, expected_columns):
    for index in conn.execute(
        f'PRAGMA index_list("{table_name}")'
    ):
        if index[2] == 1 and _index_columns(conn, index[1]) == expected_columns:
            return True
    return False


def _has_partial_unique_index(
    conn, index_name, expected_columns, expected_where
):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'index' AND name = ?",
        (index_name,),
    ).fetchone()
    if row is None:
        return False

    index_row = conn.execute(
        "SELECT * FROM pragma_index_list(?) WHERE name = ?",
        ("grocery_list_snapshots", index_name),
    ).fetchone()
    if (
        index_row is None
        or index_row[2] != 1
        or len(index_row) < 5
        or index_row[4] != 1
        or _index_columns(conn, index_name) != expected_columns
    ):
        return False

    sql = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'index' AND name = ?",
        (index_name,),
    ).fetchone()[0]
    normalized_sql = " ".join(sql.lower().split())
    return f"where {expected_where.lower()}" in normalized_sql


def _foreign_keys(conn, table_name):
    return {
        (row[3], row[2], row[4], row[6])
        for row in conn.execute(f'PRAGMA foreign_key_list("{table_name}")')
    }


def _validate_existing_schema(conn):
    required_foreign_keys = {
        "grocery_lists": {
            ("client_id", "clients", "client_id", "RESTRICT"),
            ("created_by_user_id", "users", "user_id", "RESTRICT"),
        },
        "grocery_list_sections": {
            (
                "grocery_list_id",
                "grocery_lists",
                "grocery_list_id",
                "CASCADE",
            ),
        },
        "grocery_list_items": {
            (
                "section_id",
                "grocery_list_sections",
                "section_id",
                "CASCADE",
            ),
            ("updated_by_user_id", "users", "user_id", "RESTRICT"),
        },
        "grocery_list_shares": {
            (
                "grocery_list_id",
                "grocery_lists",
                "grocery_list_id",
                "CASCADE",
            ),
            ("user_id", "users", "user_id", "RESTRICT"),
            ("shared_by_user_id", "users", "user_id", "RESTRICT"),
        },
        "grocery_list_email_recipients": {
            (
                "grocery_list_id",
                "grocery_lists",
                "grocery_list_id",
                "CASCADE",
            ),
            (
                "created_by_user_id",
                "users",
                "user_id",
                "RESTRICT",
            ),
            (
                "updated_by_user_id",
                "users",
                "user_id",
                "RESTRICT",
            ),
        },
        "grocery_list_snapshots": {
            (
                "grocery_list_id",
                "grocery_lists",
                "grocery_list_id",
                "RESTRICT",
            ),
            (
                "captured_by_user_id",
                "users",
                "user_id",
                "RESTRICT",
            ),
        },
        "grocery_list_snapshot_sections": {
            (
                "snapshot_id",
                "grocery_list_snapshots",
                "snapshot_id",
                "CASCADE",
            ),
        },
        "grocery_list_snapshot_items": {
            (
                "snapshot_section_id",
                "grocery_list_snapshot_sections",
                "snapshot_section_id",
                "CASCADE",
            ),
        },
    }
    required_checks = {
        "grocery_lists": ("length(trim(title)) > 0",),
        "grocery_list_sections": (
            "length(trim(name)) > 0",
            "display_order >= 0",
        ),
        "grocery_list_items": (
            "length(trim(item_name)) > 0",
            "purchased in (0, 1)",
            "display_order >= 0",
        ),
        "grocery_list_shares": (
            "permission in ('view', 'edit')",
        ),
        "grocery_list_email_recipients": (
            "email_address = trim(email_address)",
            "length(email_address) > 0",
            "length(email_address) <= 254",
        ),
        "grocery_list_snapshots": (
            "snapshot_kind in ('weekly', 'email')",
            "snapshot_kind = 'weekly'",
            "week_start is not null",
            "snapshot_kind = 'email'",
            "week_start is null",
            "week_start = date(week_start)",
        ),
        "grocery_list_snapshot_sections": (
            "length(trim(name)) > 0",
            "display_order >= 0",
        ),
        "grocery_list_snapshot_items": (
            "length(trim(item_name)) > 0",
            "purchased in (0, 1)",
            "display_order >= 0",
        ),
    }
    unique_columns = {
        "grocery_lists": ("client_id",),
        "grocery_list_sections": ("grocery_list_id", "name"),
        "grocery_list_shares": ("grocery_list_id", "user_id"),
        "grocery_list_email_recipients": (
            "grocery_list_id",
            "email_address",
        ),
    }

    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        table_sql = _table_sql(conn, table_name)
        if table_sql is None:
            raise RuntimeError(
                f"Existing {table_name} schema is incompatible: "
                "the object is not a table."
            )

        columns = {
            row[1]: row
            for row in conn.execute(f'PRAGMA table_info("{table_name}")')
        }
        missing = expected_columns - columns.keys()
        if missing:
            raise RuntimeError(
                f"Existing {table_name} schema is incompatible: "
                f"missing columns: {', '.join(sorted(missing))}."
            )

        if table_name == "grocery_lists":
            primary_key = "grocery_list_id"
            required_not_null = {
                "client_id",
                "title",
                "created_by_user_id",
                "created_at_utc",
                "updated_at_utc",
            }
        elif table_name == "grocery_list_sections":
            primary_key = "section_id"
            required_not_null = {
                "grocery_list_id",
                "name",
                "display_order",
            }
        elif table_name == "grocery_list_items":
            primary_key = "item_id"
            required_not_null = {
                "section_id",
                "item_name",
                "purchased",
                "display_order",
                "created_at_utc",
                "updated_at_utc",
                "updated_by_user_id",
            }
        elif table_name == "grocery_list_shares":
            primary_key = "share_id"
            required_not_null = {
                "grocery_list_id",
                "user_id",
                "permission",
                "shared_by_user_id",
                "shared_at_utc",
            }
        elif table_name == "grocery_list_email_recipients":
            primary_key = "recipient_id"
            required_not_null = {
                "grocery_list_id",
                "email_address",
                "created_by_user_id",
                "created_at_utc",
                "updated_by_user_id",
                "updated_at_utc",
            }
        elif table_name == "grocery_list_snapshots":
            primary_key = "snapshot_id"
            required_not_null = {
                "grocery_list_id",
                "snapshot_kind",
                "captured_by_user_id",
                "captured_at_utc",
            }
        elif table_name == "grocery_list_snapshot_sections":
            primary_key = "snapshot_section_id"
            required_not_null = {
                "snapshot_id",
                "name",
                "display_order",
            }
        else:
            primary_key = "snapshot_item_id"
            required_not_null = {
                "snapshot_section_id",
                "item_name",
                "purchased",
                "display_order",
            }

        incorrectly_nullable = sorted(
            name for name in required_not_null if columns[name][3] != 1
        )
        if incorrectly_nullable:
            raise RuntimeError(
                f"Existing {table_name} schema is incompatible: "
                "required columns are nullable: "
                f"{', '.join(incorrectly_nullable)}."
            )

        if columns[primary_key][5] != 1:
            raise RuntimeError(
                f"Existing {table_name} schema is incompatible: "
                f"{primary_key} is not the primary key."
            )

        if not required_foreign_keys[table_name].issubset(
            _foreign_keys(conn, table_name)
        ):
            raise RuntimeError(
                f"Existing {table_name} schema is incompatible: "
                "required foreign keys are missing or incorrect."
            )

        normalized_sql = " ".join(table_sql.lower().split())
        if any(
            check_fragment not in normalized_sql
            for check_fragment in required_checks[table_name]
        ):
            raise RuntimeError(
                f"Existing {table_name} schema is incompatible: "
                "required CHECK constraints are missing."
            )

        if table_name in unique_columns and not _has_unique_columns(
            conn, table_name, unique_columns[table_name]
        ):
            raise RuntimeError(
                f"Existing {table_name} schema is incompatible: "
                "required uniqueness constraint is missing."
            )

    for index_name, _ in INDEXES:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "Existing Grocery Lists schema is incompatible: "
                f"missing index {index_name}."
            )

    for index_name, indexed_columns, where_clause in PARTIAL_UNIQUE_INDEXES:
        column_text = indexed_columns.partition("(")[2].rstrip(")")
        expected_columns = tuple(
            column.strip() for column in column_text.split(",")
        )
        if not _has_partial_unique_index(
            conn,
            index_name,
            expected_columns,
            where_clause,
        ):
            raise RuntimeError(
                "Existing Grocery Lists schema is incompatible: "
                f"missing or incorrect index {index_name}."
            )


def migrate(conn):
    """Create and validate the current Grocery Lists schema transactionally."""
    conn.execute("PRAGMA foreign_keys = ON")
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
    if (
        foreign_keys is None
        or type(foreign_keys[0]) is not int
        or foreign_keys[0] != 1
    ):
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")

    already_complete = all(
        _table_sql(conn, table_name) is not None
        for table_name in TABLE_SQL
    )
    already_complete = already_complete and all(
        conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        is not None
        for index_name, _ in INDEXES
    )
    already_complete = already_complete and all(
        conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        is not None
        for index_name, _, _ in PARTIAL_UNIQUE_INDEXES
    )

    started_transaction = not conn.in_transaction
    savepoint = "grocery_lists_migration"
    if started_transaction:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute(f"SAVEPOINT {savepoint}")

    try:
        for sql in TABLE_SQL.values():
            conn.execute(sql)
        for index_name, indexed_columns in INDEXES:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {indexed_columns}"
            )
        for index_name, indexed_columns, where_clause in PARTIAL_UNIQUE_INDEXES:
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                f"ON {indexed_columns} WHERE {where_clause}"
            )
        _validate_existing_schema(conn)

        if started_transaction:
            conn.commit()
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except BaseException:
        if started_transaction:
            if conn.in_transaction:
                conn.rollback()
        else:
            try:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except sqlite3.Error:
                pass
        raise

    return not already_complete


def main():
    conn = sqlite3.connect(DB_NAME)
    try:
        migrate(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
