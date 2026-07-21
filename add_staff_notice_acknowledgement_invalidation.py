import re
import sqlite3


DB_NAME = "nhpsg.db"

TABLE_NAME = "acknowledgements"
MIGRATION_TABLE_NAME = "acknowledgements_staff_notice_migration"
ACTIVE_UNIQUE_INDEX_NAME = (
    "ux_acknowledgements_active_source_user"
)

BASE_COLUMNS = (
    "acknowledgement_id",
    "source_table",
    "source_id",
    "user_id",
    "acknowledged_at",
    "comment",
)

FINAL_COLUMNS = BASE_COLUMNS + (
    "acknowledgement_type",
    "active",
    "invalidated_at_utc",
    "invalidated_by_user_id",
    "invalidation_reason",
)

EXPECTED_COLUMN_DEFINITIONS = (
    ("acknowledgement_id", "INTEGER", 0, None, 1),
    ("source_table", "TEXT", 1, None, 0),
    ("source_id", "INTEGER", 1, None, 0),
    ("user_id", "INTEGER", 1, None, 0),
    (
        "acknowledged_at",
        "TEXT",
        0,
        "CURRENT_TIMESTAMP",
        0,
    ),
    ("comment", "TEXT", 0, None, 0),
    ("acknowledgement_type", "TEXT", 0, "'Read'", 0),
    ("active", "INTEGER", 1, "1", 0),
    ("invalidated_at_utc", "TEXT", 0, None, 0),
    ("invalidated_by_user_id", "INTEGER", 0, None, 0),
    ("invalidation_reason", "TEXT", 0, None, 0),
)

EXPECTED_FOREIGN_KEYS = (
    (
        "users",
        "invalidated_by_user_id",
        "user_id",
        "NO ACTION",
        "RESTRICT",
        "NONE",
    ),
)


def quote_identifier(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def create_table_sql(table_name):
    quoted_table_name = quote_identifier(table_name)

    return f"""
        CREATE TABLE {quoted_table_name} (
            acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,

            source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,

            acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
            comment TEXT,
            acknowledgement_type TEXT DEFAULT 'Read',

            active INTEGER NOT NULL DEFAULT 1
                CHECK (active IN (0, 1)),

            invalidated_at_utc TEXT,
            invalidated_by_user_id INTEGER,
            invalidation_reason TEXT,

            FOREIGN KEY (invalidated_by_user_id)
                REFERENCES users(user_id)
                ON DELETE RESTRICT
        )
    """


CREATE_ACTIVE_UNIQUE_INDEX_SQL = f"""
    CREATE UNIQUE INDEX {ACTIVE_UNIQUE_INDEX_NAME}
    ON {TABLE_NAME} (
        source_table,
        source_id,
        user_id
    )
    WHERE active = 1
"""


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


def get_table_sql(conn, table_name):
    row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,)
    ).fetchone()

    return row[0] if row else None


def get_column_info(conn, table_name):
    quoted_table_name = quote_identifier(table_name)

    return conn.execute(
        f"PRAGMA table_info({quoted_table_name})"
    ).fetchall()


def get_foreign_key_info(conn, table_name):
    quoted_table_name = quote_identifier(table_name)

    return conn.execute(
        f"PRAGMA foreign_key_list({quoted_table_name})"
    ).fetchall()


def get_index_info(conn, table_name):
    indexes = []
    quoted_table_name = quote_identifier(table_name)

    for row in conn.execute(
        f"PRAGMA index_list({quoted_table_name})"
    ).fetchall():
        index_name = row[1]
        quoted_index_name = quote_identifier(index_name)
        key_columns = tuple(
            (
                index_row[2],
                index_row[3],
                index_row[4],
            )
            for index_row in conn.execute(
                f"PRAGMA index_xinfo({quoted_index_name})"
            ).fetchall()
            if index_row[5] == 1
        )
        sql_row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'index'
              AND name = ?
            """,
            (index_name,)
        ).fetchone()

        indexes.append({
            "name": index_name,
            "unique": row[2],
            "origin": row[3],
            "partial": row[4],
            "key_columns": key_columns,
            "sql": sql_row[0] if sql_row else None
        })

    return indexes


def strip_sql_comments(sql):
    result = []
    position = 0
    quote = None

    while position < len(sql):
        character = sql[position]
        following = (
            sql[position + 1]
            if position + 1 < len(sql)
            else ""
        )

        if quote is not None:
            result.append(character)

            if quote == "[":
                if character == "]":
                    if following == "]":
                        result.append(following)
                        position += 1
                    else:
                        quote = None
            elif character == quote:
                if following == quote:
                    result.append(following)
                    position += 1
                else:
                    quote = None

            position += 1
            continue

        if character in {"'", '"', "`", "["}:
            quote = character
            result.append(character)
            position += 1
            continue

        if character == "-" and following == "-":
            position += 2

            while (
                position < len(sql)
                and sql[position] not in {"\r", "\n"}
            ):
                position += 1

            result.append(" ")
            continue

        if character == "/" and following == "*":
            comment_end = sql.find("*/", position + 2)

            if comment_end == -1:
                return None

            result.append(" ")
            position = comment_end + 2
            continue

        result.append(character)
        position += 1

    if quote is not None:
        return None

    return "".join(result)


SQL_TOKEN_PATTERN = re.compile(
    r"\s+"
    r"|'(?:''|[^'])*'"
    r'|"(?:""|[^"])*"'
    r"|`(?:``|[^`])*`"
    r"|\[(?:\]\]|[^\]])*\]"
    r"|[A-Za-z_][A-Za-z0-9_$]*"
    r"|\d+(?:\.\d+)?"
    r"|<>|!=|<=|>=|==|\|\|"
    r"|[(),.;+*/%<>=~-]"
)


def sql_token_signature(sql):
    comment_free_sql = strip_sql_comments(sql)

    if comment_free_sql is None:
        return None

    tokens = []
    position = 0

    while position < len(comment_free_sql):
        match = SQL_TOKEN_PATTERN.match(comment_free_sql, position)

        if match is None:
            return None

        token = match.group(0)
        position = match.end()

        if token.isspace():
            continue

        if token.startswith("'"):
            tokens.append(("string", token[1:-1].replace("''", "'")))
        elif token.startswith('"'):
            tokens.append((
                "word",
                token[1:-1].replace('""', '"').casefold(),
            ))
        elif token.startswith("`"):
            tokens.append((
                "word",
                token[1:-1].replace("``", "`").casefold(),
            ))
        elif token.startswith("["):
            tokens.append((
                "word",
                token[1:-1].replace("]]", "]").casefold(),
            ))
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", token):
            tokens.append(("word", token.casefold()))
        else:
            tokens.append(("symbol", token))

    if tokens and tokens[-1] == ("symbol", ";"):
        tokens.pop()

    return tuple(tokens)


def normalize_default(value):
    if value is None:
        return None

    normalized = value.strip()

    while (
        normalized.startswith("(")
        and normalized.endswith(")")
    ):
        normalized = normalized[1:-1].strip()

    if normalized.upper() == "CURRENT_TIMESTAMP":
        return "CURRENT_TIMESTAMP"

    return normalized


def table_has_approved_structure(conn, table_name):
    table_sql = get_table_sql(conn, table_name)

    if table_sql is None:
        return False

    columns = get_column_info(conn, table_name)
    actual_definitions = tuple(
        (
            row[1],
            re.sub(r"\s+", " ", row[2].strip()).upper(),
            row[3],
            normalize_default(row[4]),
            row[5],
        )
        for row in columns
    )

    if actual_definitions != EXPECTED_COLUMN_DEFINITIONS:
        return False

    if sql_token_signature(table_sql) != sql_token_signature(
        create_table_sql(table_name)
    ):
        return False

    foreign_keys = tuple(
        (
            row[2],
            row[3],
            row[4],
            row[5].upper(),
            row[6].upper(),
            row[7].upper(),
        )
        for row in get_foreign_key_info(conn, table_name)
    )

    return foreign_keys == EXPECTED_FOREIGN_KEYS


def has_approved_active_unique_index(conn):
    indexes = get_index_info(conn, TABLE_NAME)

    if len(indexes) != 1:
        return False

    index = indexes[0]

    return (
        index["name"] == ACTIVE_UNIQUE_INDEX_NAME
        and index["unique"] == 1
        and index["origin"] == "c"
        and index["partial"] == 1
        and index["key_columns"] == (
            ("source_table", 0, "BINARY"),
            ("source_id", 0, "BINARY"),
            ("user_id", 0, "BINARY"),
        )
        and sql_token_signature(index["sql"])
        == sql_token_signature(CREATE_ACTIVE_UNIQUE_INDEX_SQL)
    )


def has_unconditional_source_user_unique_index(conn):
    expected_columns = (
        "source_table",
        "source_id",
        "user_id"
    )

    return any(
        index["unique"] == 1
        and index["partial"] == 0
        and tuple(
            column[0]
            for column in index["key_columns"]
        ) == expected_columns
        for index in get_index_info(conn, TABLE_NAME)
    )


def schema_is_current(conn):
    if not table_exists(conn, TABLE_NAME):
        return False

    return (
        table_has_approved_structure(conn, TABLE_NAME)
        and has_approved_active_unique_index(conn)
        and not has_unconditional_source_user_unique_index(conn)
    )


def validate_source_schema(conn):
    columns = get_column_info(conn, TABLE_NAME)
    column_names = {row[1] for row in columns}
    missing_columns = set(BASE_COLUMNS) - column_names

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise RuntimeError(
            "The acknowledgements table is missing required "
            f"column(s): {missing}."
        )

    unexpected_columns = column_names - set(FINAL_COLUMNS)

    if unexpected_columns:
        unexpected = ", ".join(sorted(unexpected_columns))
        raise RuntimeError(
            "The acknowledgements table contains unexpected "
            f"column(s): {unexpected}. The migration stopped "
            "to avoid discarding data."
        )

    if "active" in column_names:
        invalid_active = conn.execute(
            """
            SELECT acknowledgement_id
            FROM acknowledgements
            WHERE active IS NULL
               OR typeof(active) <> 'integer'
               OR active NOT IN (0, 1)
            LIMIT 1
            """
        ).fetchone()

        if invalid_active:
            raise RuntimeError(
                "The acknowledgements table contains an invalid "
                "active value. No changes were made."
            )

        duplicate_active = conn.execute(
            """
            SELECT source_table, source_id, user_id
            FROM acknowledgements
            WHERE active = 1
            GROUP BY source_table, source_id, user_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()

        if duplicate_active:
            raise RuntimeError(
                "The acknowledgements table contains duplicate "
                "active source/user records. No changes were made."
            )


def validate_no_inbound_foreign_keys(conn):
    for row in conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND name <> ?
        """,
        (TABLE_NAME,)
    ).fetchall():
        table_name = row[0]

        for foreign_key in get_foreign_key_info(conn, table_name):
            if foreign_key[2].casefold() == TABLE_NAME.casefold():
                raise RuntimeError(
                    f'Table "{table_name}" has a foreign key to '
                    "acknowledgements. The migration stopped before "
                    "rebuilding the table."
                )


def source_expression(column_name, source_columns):
    if column_name in source_columns:
        return quote_identifier(column_name)

    if column_name == "acknowledgement_type":
        return "'Read'"

    if column_name == "active":
        return "1"

    return "NULL"


def get_sequence_value(conn, table_name):
    if not table_exists(conn, "sqlite_sequence"):
        return None

    row = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = ?",
        (table_name,)
    ).fetchone()

    if row is None:
        return None

    sequence_value = row[0]

    if (
        not isinstance(sequence_value, int)
        or isinstance(sequence_value, bool)
        or sequence_value < 0
    ):
        raise RuntimeError(
            "The acknowledgements AUTOINCREMENT sequence is invalid. "
            "No changes were made."
        )

    return sequence_value


def set_sequence_value(conn, table_name, sequence_value):
    conn.execute(
        "DELETE FROM sqlite_sequence WHERE name = ?",
        (table_name,)
    )

    if sequence_value > 0:
        conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
            (table_name, sequence_value)
        )


def select_final_rows(conn, table_name, select_expressions=None):
    quoted_table_name = quote_identifier(table_name)

    if select_expressions is None:
        select_expressions = ", ".join(
            quote_identifier(column_name)
            for column_name in FINAL_COLUMNS
        )

    return conn.execute(
        f"""
        SELECT {select_expressions}
        FROM {quoted_table_name}
        ORDER BY acknowledgement_id
        """
    ).fetchall()


def foreign_key_violations(conn, table_name=None):
    if table_name is None:
        return conn.execute("PRAGMA foreign_key_check").fetchall()

    quoted_table_name = quote_identifier(table_name)
    return conn.execute(
        f"PRAGMA foreign_key_check({quoted_table_name})"
    ).fetchall()


def run_database_checks(conn):
    integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()

    if integrity_rows != [("ok",)]:
        details = "; ".join(str(row[0]) for row in integrity_rows)
        raise RuntimeError(
            f"SQLite integrity_check failed: {details}"
        )

    foreign_key_errors = foreign_key_violations(conn)

    if foreign_key_errors:
        raise RuntimeError(
            "SQLite foreign_key_check reported violations: "
            f"{foreign_key_errors}"
        )


def create_final_schema(conn):
    conn.execute(create_table_sql(TABLE_NAME))
    conn.execute(CREATE_ACTIVE_UNIQUE_INDEX_SQL)


def rebuild_acknowledgements(conn):
    validate_source_schema(conn)
    validate_no_inbound_foreign_keys(conn)

    if table_exists(conn, MIGRATION_TABLE_NAME):
        raise RuntimeError(
            f'Temporary table "{MIGRATION_TABLE_NAME}" already '
            "exists. No changes were made."
        )

    source_columns = get_column_info(conn, TABLE_NAME)
    source_column_names = {row[1] for row in source_columns}
    select_expressions = ", ".join(
        source_expression(column_name, source_column_names)
        for column_name in FINAL_COLUMNS
    )
    legacy_rows = select_final_rows(
        conn,
        TABLE_NAME,
        select_expressions
    )
    original_sequence = get_sequence_value(conn, TABLE_NAME)
    maximum_preserved_id = max(
        (row[0] for row in legacy_rows),
        default=0
    )
    sequence_high_water = max(
        original_sequence or 0,
        maximum_preserved_id
    )

    conn.execute(create_table_sql(MIGRATION_TABLE_NAME))

    insert_columns = ", ".join(
        quote_identifier(column_name)
        for column_name in FINAL_COLUMNS
    )
    quoted_migration_table = quote_identifier(
        MIGRATION_TABLE_NAME
    )
    quoted_source_table = quote_identifier(TABLE_NAME)
    conn.execute(
        f"""
        INSERT INTO {quoted_migration_table} ({insert_columns})
        SELECT {select_expressions}
        FROM {quoted_source_table}
        """
    )

    migrated_rows_before_drop = select_final_rows(
        conn,
        MIGRATION_TABLE_NAME
    )

    if migrated_rows_before_drop != legacy_rows:
        raise RuntimeError(
            "Acknowledgement data verification failed before "
            "replacing the original table."
        )

    if not table_has_approved_structure(
        conn,
        MIGRATION_TABLE_NAME
    ):
        raise RuntimeError(
            "The temporary acknowledgement schema did not pass "
            "verification."
        )

    temporary_foreign_key_errors = foreign_key_violations(
        conn,
        MIGRATION_TABLE_NAME
    )

    if temporary_foreign_key_errors:
        raise RuntimeError(
            "The copied acknowledgements violate foreign keys: "
            f"{temporary_foreign_key_errors}"
        )

    conn.execute(f"DROP TABLE {quoted_source_table}")
    conn.execute(
        f"ALTER TABLE {quoted_migration_table} "
        f"RENAME TO {quoted_source_table}"
    )
    conn.execute(CREATE_ACTIVE_UNIQUE_INDEX_SQL)
    set_sequence_value(conn, TABLE_NAME, sequence_high_water)

    if select_final_rows(conn, TABLE_NAME) != legacy_rows:
        raise RuntimeError(
            "Acknowledgement data verification failed after "
            "replacing the original table."
        )

    final_sequence = get_sequence_value(conn, TABLE_NAME) or 0

    if final_sequence < sequence_high_water:
        raise RuntimeError(
            "Acknowledgement AUTOINCREMENT sequence verification "
            "failed."
        )


def migrate(conn):
    if conn.in_transaction:
        raise RuntimeError(
            "The acknowledgement migration must start outside an "
            "existing transaction."
        )

    if not table_exists(conn, "users"):
        raise RuntimeError(
            "The users table must exist before migrating "
            "acknowledgements."
        )

    incoming_foreign_keys = conn.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0]
    migration_error = None

    if schema_is_current(conn):
        return False

    try:
        conn.execute("BEGIN IMMEDIATE")

        if not table_exists(conn, TABLE_NAME):
            create_final_schema(conn)
        else:
            rebuild_acknowledgements(conn)

        if not schema_is_current(conn):
            raise RuntimeError(
                "The acknowledgement schema did not pass "
                "post-migration verification."
            )

        run_database_checks(conn)
        conn.commit()
    except BaseException as error:
        migration_error = error

        if conn.in_transaction:
            conn.rollback()

        raise
    finally:
        final_foreign_keys = conn.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0]

        if final_foreign_keys != incoming_foreign_keys:
            setting_error = RuntimeError(
                "The migration changed the connection's foreign-key "
                "setting unexpectedly."
            )

            conn.execute(
                f"PRAGMA foreign_keys = {incoming_foreign_keys}"
            )

            if migration_error is not None:
                migration_error.add_note(str(setting_error))
            else:
                raise setting_error

    return True


def verify_database(conn):
    run_database_checks(conn)


def main():
    conn = sqlite3.connect(DB_NAME)

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        changed = migrate(conn)
        verify_database(conn)
    finally:
        conn.close()

    if changed:
        print("Acknowledgement invalidation migration completed.")
    else:
        print("Acknowledgement schema is already current.")


if __name__ == "__main__":
    main()
