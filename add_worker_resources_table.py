"""Create the Worker Resource Library storage metadata table."""

import os
import re
import sqlite3
import sys


DB_NAME = "nhpsg.db"
TABLE_NAME = "worker_resources"

WORKER_RESOURCES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS worker_resources (
    resource_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL CHECK (category IN (
        'Training',
        'Policies and Procedures',
        'Forms and Documents',
        'Reference'
    )),
    resource_type TEXT NOT NULL CHECK (resource_type IN (
        'Video',
        'Document',
        'Image'
    )),
    stored_filename TEXT NOT NULL UNIQUE,
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes >= 0),
    display_order INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    uploaded_by_user_id INTEGER NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (uploaded_by_user_id)
        REFERENCES users(user_id)
        ON DELETE RESTRICT
)
"""

EXPECTED_COLUMNS = {
    "resource_id",
    "title",
    "description",
    "category",
    "resource_type",
    "stored_filename",
    "original_filename",
    "mime_type",
    "file_size_bytes",
    "display_order",
    "active",
    "uploaded_by_user_id",
    "created_at_utc",
    "updated_at_utc",
}

INDEXES = (
    (
        "idx_worker_resources_active_category_order",
        "worker_resources(active, category, display_order)",
    ),
    (
        "idx_worker_resources_uploaded_by_user_id",
        "worker_resources(uploaded_by_user_id)",
    ),
)


def _table_sql(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = ?",
        (TABLE_NAME,),
    ).fetchone()
    return None if row is None else row[0]


def _validate_existing_schema(conn):
    table_sql = _table_sql(conn)
    if table_sql is None:
        raise RuntimeError(
            "Existing worker_resources schema is incompatible: "
            "the object is not a table."
        )

    columns = {
        row[1]: row
        for row in conn.execute("PRAGMA table_info(worker_resources)")
    }
    missing = EXPECTED_COLUMNS - columns.keys()
    if missing:
        missing_columns = ", ".join(sorted(missing))
        raise RuntimeError(
            "Existing worker_resources schema is incompatible: "
            f"missing columns: {missing_columns}."
        )

    required_not_null = {
        "title",
        "category",
        "resource_type",
        "stored_filename",
        "original_filename",
        "mime_type",
        "file_size_bytes",
        "display_order",
        "active",
        "uploaded_by_user_id",
        "created_at_utc",
        "updated_at_utc",
    }
    incorrectly_nullable = sorted(
        name for name in required_not_null if columns[name][3] != 1
    )
    if incorrectly_nullable:
        raise RuntimeError(
            "Existing worker_resources schema is incompatible: "
            "required columns are nullable: "
            f"{', '.join(incorrectly_nullable)}."
        )

    if columns["resource_id"][5] != 1:
        raise RuntimeError(
            "Existing worker_resources schema is incompatible: "
            "resource_id is not the primary key."
        )

    has_stored_filename_unique_constraint = False
    for index in conn.execute("PRAGMA index_list(worker_resources)"):
        if index[2] != 1:
            continue
        index_columns = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM pragma_index_info(?)",
                (index[1],),
            )
        ]
        if index_columns == ["stored_filename"]:
            has_stored_filename_unique_constraint = True
            break
    if not has_stored_filename_unique_constraint:
        raise RuntimeError(
            "Existing worker_resources schema is incompatible: "
            "stored_filename must be unique."
        )

    foreign_keys = {
        (row[3], row[2], row[4], row[6])
        for row in conn.execute("PRAGMA foreign_key_list(worker_resources)")
    }
    if ("uploaded_by_user_id", "users", "user_id", "RESTRICT") not in foreign_keys:
        raise RuntimeError(
            "Existing worker_resources schema is incompatible: "
            "uploaded_by_user_id must reference users(user_id) with ON DELETE RESTRICT."
        )

    normalized_sql = re.sub(r"\s+", " ", table_sql).lower()
    normalized_sql = normalized_sql.replace("( ", "(").replace(" )", ")")
    required_sql_fragments = (
        "category in ('training', 'policies and procedures', 'forms and documents', 'reference')",
        "resource_type in ('video', 'document', 'image')",
        "file_size_bytes >= 0",
        "active in (0, 1)",
    )
    if any(fragment not in normalized_sql for fragment in required_sql_fragments):
        raise RuntimeError(
            "Existing worker_resources schema is incompatible: "
            "one or more controlled-value constraints are missing."
        )


def migrate(conn):
    """Create and validate the Worker Resource Library schema transactionally."""
    conn.execute("PRAGMA foreign_keys = ON")
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or type(foreign_keys[0]) is not int or foreign_keys[0] != 1:
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled.")

    started_transaction = not conn.in_transaction
    savepoint = "worker_resources_migration"
    if started_transaction:
        conn.execute("BEGIN IMMEDIATE")
    else:
        conn.execute(f"SAVEPOINT {savepoint}")

    try:
        conn.execute(WORKER_RESOURCES_TABLE_SQL)
        _validate_existing_schema(conn)
        for index_name, indexed_columns in INDEXES:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {indexed_columns}"
            )

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


def main(argv=None):
    """Run the migration against an explicit path or NHPSG_DB_PATH."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        raise SystemExit("usage: add_worker_resources_table.py [database-path]")

    database_name = args[0] if args else os.environ.get("NHPSG_DB_PATH", DB_NAME)
    conn = sqlite3.connect(database_name)
    try:
        migrate(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
