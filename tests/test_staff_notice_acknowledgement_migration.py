import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import add_staff_notice_acknowledgement_invalidation as migration
from add_staff_notice_acknowledgement_invalidation import (
    ACTIVE_UNIQUE_INDEX_NAME,
    FINAL_COLUMNS,
    MIGRATION_TABLE_NAME,
    migrate,
    schema_is_current,
    verify_database,
)


LEGACY_ACKNOWLEDGEMENTS_SQL = """
CREATE TABLE acknowledgements (
    acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
    comment TEXT,
    acknowledgement_type TEXT DEFAULT 'Read',
    active INTEGER DEFAULT 1,
    UNIQUE(source_table, source_id, user_id)
)
"""


def final_table_sql(
    acknowledgement_id_definition=(
        "INTEGER PRIMARY KEY AUTOINCREMENT"
    ),
    source_table_definition="TEXT NOT NULL",
    source_id_definition="INTEGER NOT NULL",
    active_definition="INTEGER NOT NULL DEFAULT 1",
    active_check="CHECK (active IN (0, 1))",
    foreign_key_delete="RESTRICT",
    include_foreign_key=True,
    additional_column="",
):
    foreign_key_sql = ""

    if include_foreign_key:
        foreign_key_sql = f"""
        , FOREIGN KEY (invalidated_by_user_id)
            REFERENCES users(user_id)
            ON DELETE {foreign_key_delete}
        """

    additional_column_sql = (
        f", {additional_column}"
        if additional_column
        else ""
    )

    return f"""
    CREATE TABLE acknowledgements (
        acknowledgement_id {acknowledgement_id_definition},
        source_table {source_table_definition},
        source_id {source_id_definition},
        user_id INTEGER NOT NULL,
        acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
        comment TEXT,
        acknowledgement_type TEXT DEFAULT 'Read',
        active {active_definition}
            {active_check},
        invalidated_at_utc TEXT,
        invalidated_by_user_id INTEGER,
        invalidation_reason TEXT
        {additional_column_sql}
        {foreign_key_sql}
    )
    """


class AcknowledgementInvalidationMigrationTests(
    unittest.TestCase
):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        database_path = (
            Path(self.temp_directory.name)
            / "acknowledgement_migration_test.db"
        )
        self.conn = sqlite3.connect(database_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL
            )
        """)
        self.conn.executemany(
            "INSERT INTO users (user_id, username) VALUES (?, ?)",
            (
                (1, "manager_one"),
                (2, "manager_two")
            )
        )
        self.conn.execute(LEGACY_ACKNOWLEDGEMENTS_SQL)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp_directory.cleanup()

    def recreate_acknowledgements(
        self,
        table_sql,
        index_predicate=None,
    ):
        self.conn.execute("DROP TABLE acknowledgements")
        self.conn.execute(table_sql)

        if index_predicate is not None:
            self.conn.execute(f"""
                CREATE UNIQUE INDEX
                    {ACTIVE_UNIQUE_INDEX_NAME}
                ON acknowledgements (
                    source_table,
                    source_id,
                    user_id
                )
                WHERE {index_predicate}
            """)

        self.conn.commit()

    def schema_snapshot(self):
        return self.conn.execute("""
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name = 'acknowledgements'
               OR tbl_name = 'acknowledgements'
            ORDER BY type, name
        """).fetchall()

    def acknowledgement_rows(self):
        return self.conn.execute("""
            SELECT *
            FROM acknowledgements
            ORDER BY acknowledgement_id
        """).fetchall()

    def temporary_table_exists(self):
        return self.conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = ?
            """,
            (MIGRATION_TABLE_NAME,)
        ).fetchone() is not None

    def approved_index_exists(self):
        return self.conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'index'
              AND name = ?
            """,
            (ACTIVE_UNIQUE_INDEX_NAME,)
        ).fetchone() is not None

    def create_custom_active_index(self, columns, predicate):
        self.conn.execute(f"""
            CREATE UNIQUE INDEX {ACTIVE_UNIQUE_INDEX_NAME}
            ON acknowledgements ({columns})
            WHERE {predicate}
        """)
        self.conn.commit()

    def assert_case_variant_inbound_foreign_key_rejected(
        self,
        referenced_table_name,
    ):
        self.insert_legacy_row(12)
        self.conn.execute(f"""
            CREATE TABLE child_acknowledgement (
                child_id INTEGER PRIMARY KEY,
                acknowledgement_id INTEGER NOT NULL,
                FOREIGN KEY (acknowledgement_id)
                    REFERENCES {referenced_table_name}(
                        acknowledgement_id
                    )
                    ON DELETE CASCADE
            )
        """)
        self.conn.execute("""
            INSERT INTO child_acknowledgement (
                child_id,
                acknowledgement_id
            )
            VALUES (1, 12)
        """)
        self.conn.commit()
        original_schema = self.schema_snapshot()
        original_rows = self.acknowledgement_rows()
        original_child_rows = self.conn.execute("""
            SELECT *
            FROM child_acknowledgement
        """).fetchall()
        original_sequence = self.conn.execute("""
            SELECT seq
            FROM sqlite_sequence
            WHERE name = 'acknowledgements'
        """).fetchone()

        with self.assertRaisesRegex(RuntimeError, "has a foreign key"):
            migrate(self.conn)

        self.assertEqual(self.schema_snapshot(), original_schema)
        self.assertEqual(self.acknowledgement_rows(), original_rows)
        self.assertEqual(
            self.conn.execute("""
                SELECT *
                FROM child_acknowledgement
            """).fetchall(),
            original_child_rows
        )
        self.assertEqual(
            self.conn.execute("""
                SELECT seq
                FROM sqlite_sequence
                WHERE name = 'acknowledgements'
            """).fetchone(),
            original_sequence
        )
        self.assertFalse(self.temporary_table_exists())
        self.assertFalse(self.approved_index_exists())
        self.assertFalse(self.conn.in_transaction)

    def assert_database_valid(self):
        self.assertEqual(
            self.conn.execute(
                "PRAGMA integrity_check"
            ).fetchall(),
            [("ok",)]
        )
        self.assertEqual(
            self.conn.execute(
                "PRAGMA foreign_key_check"
            ).fetchall(),
            []
        )
        verify_database(self.conn)

    def insert_legacy_row(self, acknowledgement_id=1):
        self.conn.execute("""
            INSERT INTO acknowledgements (
                acknowledgement_id,
                source_table,
                source_id,
                user_id,
                acknowledged_at,
                comment,
                acknowledgement_type,
                active
            )
            VALUES (?, 'shift_notes', 17, 1,
                    '2026-07-01 10:11:12',
                    'Original acknowledgement', 'Read', 1)
        """, (acknowledgement_id,))
        self.conn.commit()

    def test_migrates_empty_existing_schema(self):
        changed = migrate(self.conn)

        self.assertTrue(changed)
        self.assertTrue(schema_is_current(self.conn))
        self.assertFalse(self.conn.in_transaction)
        self.assertFalse(self.temporary_table_exists())
        self.assert_database_valid()

    def test_preserves_every_existing_acknowledgement_value(self):
        original_rows = (
            (
                4,
                "shift_notes",
                17,
                1,
                "2026-07-01 10:11:12",
                "Original read acknowledgement",
                "Read",
                1,
            ),
            (
                9,
                "unusual/source value",
                -22,
                2,
                None,
                None,
                None,
                0,
            ),
        )
        self.conn.executemany("""
            INSERT INTO acknowledgements (
                acknowledgement_id,
                source_table,
                source_id,
                user_id,
                acknowledged_at,
                comment,
                acknowledgement_type,
                active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, original_rows)
        self.conn.commit()

        migrate(self.conn)

        expected_rows = tuple(
            row + (None, None, None)
            for row in original_rows
        )
        self.assertEqual(
            tuple(self.acknowledgement_rows()),
            expected_rows
        )
        self.assert_database_valid()

    def test_preserves_partially_migrated_invalidation_values(self):
        self.recreate_acknowledgements(
            final_table_sql(include_foreign_key=False)
        )
        self.conn.execute("""
            CREATE UNIQUE INDEX legacy_acknowledgement_unique
            ON acknowledgements (source_table, source_id, user_id)
        """)
        expected_row = (
            6,
            "shift_notes",
            31,
            1,
            "2026-07-04 12:13:14",
            "Historical comment",
            "Review",
            0,
            "2026-07-05T10:00:00Z",
            2,
            "Entered in error",
        )
        self.conn.execute("""
            INSERT INTO acknowledgements
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, expected_row)
        self.conn.commit()

        self.assertTrue(migrate(self.conn))
        self.assertEqual(
            tuple(self.acknowledgement_rows()[0]),
            expected_row
        )
        self.assertTrue(schema_is_current(self.conn))

    def test_allows_history_and_only_one_active_acknowledgement(self):
        migrate(self.conn)

        cursor = self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table,
                source_id,
                user_id,
                acknowledgement_type
            )
            VALUES ('staff_notice_deliveries', 12, 1,
                    'Acknowledgement')
        """)
        original_id = cursor.lastrowid

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""
                INSERT INTO acknowledgements (
                    source_table,
                    source_id,
                    user_id,
                    acknowledgement_type
                )
                VALUES ('staff_notice_deliveries', 12, 1,
                        'Acknowledgement')
            """)

        self.conn.execute("""
            UPDATE acknowledgements
            SET active = 0,
                invalidated_at_utc = '2026-07-03T18:00:00Z',
                invalidated_by_user_id = 2,
                invalidation_reason = 'Entered in error'
            WHERE acknowledgement_id = ?
        """, (original_id,))
        replacement = self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table,
                source_id,
                user_id,
                acknowledgement_type
            )
            VALUES ('staff_notice_deliveries', 12, 1,
                    'Acknowledgement')
        """)

        self.assertNotEqual(replacement.lastrowid, original_id)
        self.assertEqual(
            self.conn.execute("""
                SELECT COUNT(*)
                FROM acknowledgements
                WHERE source_table = 'staff_notice_deliveries'
                  AND source_id = 12
                  AND user_id = 1
            """).fetchone()[0],
            2
        )
        self.assertEqual(
            self.conn.execute("""
                SELECT COUNT(*)
                FROM acknowledgements
                WHERE source_table = 'staff_notice_deliveries'
                  AND source_id = 12
                  AND user_id = 1
                  AND active = 1
            """).fetchone()[0],
            1
        )

    def test_active_constraint_rejects_values_other_than_zero_or_one(self):
        migrate(self.conn)

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""
                INSERT INTO acknowledgements (
                    source_table, source_id, user_id, active
                )
                VALUES ('example', 1, 1, 2)
            """)

    def test_preserves_sequence_greater_than_maximum_existing_id(self):
        self.conn.execute("""
            INSERT INTO acknowledgements (
                acknowledgement_id,
                source_table,
                source_id,
                user_id
            )
            VALUES (40, 'deleted', 1, 1)
        """)
        self.conn.execute(
            "DELETE FROM acknowledgements WHERE acknowledgement_id = 40"
        )
        self.conn.execute("""
            INSERT INTO acknowledgements (
                acknowledgement_id,
                source_table,
                source_id,
                user_id
            )
            VALUES (7, 'preserved', 2, 1)
        """)
        self.conn.commit()

        migrate(self.conn)

        cursor = self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id
            )
            VALUES ('new', 3, 1)
        """)
        self.assertEqual(cursor.lastrowid, 41)

    def test_preserves_empty_table_nonzero_sequence(self):
        self.insert_legacy_row(73)
        self.conn.execute("DELETE FROM acknowledgements")
        self.conn.commit()

        migrate(self.conn)

        cursor = self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id
            )
            VALUES ('new', 1, 1)
        """)
        self.assertEqual(cursor.lastrowid, 74)

    def test_missing_sequence_row_uses_maximum_preserved_id(self):
        self.insert_legacy_row(19)
        self.conn.execute("""
            DELETE FROM sqlite_sequence
            WHERE name = 'acknowledgements'
        """)
        self.conn.commit()

        migrate(self.conn)

        cursor = self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id
            )
            VALUES ('new', 2, 1)
        """)
        self.assertEqual(cursor.lastrowid, 20)

    def test_forced_copy_failure_rolls_back_without_residue(self):
        self.recreate_acknowledgements(
            final_table_sql(include_foreign_key=False)
        )
        self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table,
                source_id,
                user_id,
                active,
                invalidated_by_user_id
            )
            VALUES ('shift_notes', 1, 1, 0, 999)
        """)
        self.conn.commit()
        original_schema = self.schema_snapshot()
        original_rows = self.acknowledgement_rows()

        with self.assertRaises(sqlite3.IntegrityError):
            migrate(self.conn)

        self.assertEqual(self.schema_snapshot(), original_schema)
        self.assertEqual(self.acknowledgement_rows(), original_rows)
        self.assertFalse(self.temporary_table_exists())
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("SELECT 1").fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )

    def test_foreign_key_check_failure_rolls_back_when_enforcement_off(self):
        self.recreate_acknowledgements(
            final_table_sql(include_foreign_key=False)
        )
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table,
                source_id,
                user_id,
                active,
                invalidated_by_user_id
            )
            VALUES ('shift_notes', 1, 1, 0, 999)
        """)
        self.conn.commit()
        original_schema = self.schema_snapshot()
        original_rows = self.acknowledgement_rows()

        with self.assertRaisesRegex(
            RuntimeError,
            "violate foreign keys"
        ) as error_context:
            migrate(self.conn)

        self.assertIn(
            MIGRATION_TABLE_NAME,
            str(error_context.exception)
        )
        self.assertIn("users", str(error_context.exception))
        self.assertEqual(self.schema_snapshot(), original_schema)
        self.assertEqual(self.acknowledgement_rows(), original_rows)
        self.assertFalse(self.temporary_table_exists())
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
            0
        )

    def test_final_database_check_failure_prevents_commit(self):
        self.conn.execute("""
            INSERT INTO acknowledgements (
                acknowledgement_id,
                source_table,
                source_id,
                user_id
            )
            VALUES (50, 'deleted', 50, 1)
        """)
        self.conn.execute(
            "DELETE FROM acknowledgements WHERE acknowledgement_id = 50"
        )
        self.conn.commit()
        self.insert_legacy_row(3)
        original_schema = self.schema_snapshot()
        original_rows = self.acknowledgement_rows()
        original_sequence = self.conn.execute("""
            SELECT seq
            FROM sqlite_sequence
            WHERE name = 'acknowledgements'
        """).fetchone()

        with mock.patch.object(
            migration,
            "run_database_checks",
            side_effect=RuntimeError("forced integrity failure"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "forced integrity failure"
            ):
                migrate(self.conn)

        self.assertEqual(self.schema_snapshot(), original_schema)
        self.assertEqual(self.acknowledgement_rows(), original_rows)
        self.assertEqual(
            self.conn.execute("""
                SELECT seq
                FROM sqlite_sequence
                WHERE name = 'acknowledgements'
            """).fetchone(),
            original_sequence
        )
        self.assertFalse(self.temporary_table_exists())
        self.assertFalse(self.approved_index_exists())
        self.assertFalse(self.conn.in_transaction)

    def test_unknown_column_is_rejected_before_changes(self):
        self.conn.execute(
            "ALTER TABLE acknowledgements ADD COLUMN unknown_data TEXT"
        )
        self.conn.commit()
        original_schema = self.schema_snapshot()

        with self.assertRaisesRegex(RuntimeError, "unexpected column"):
            migrate(self.conn)

        self.assertEqual(self.schema_snapshot(), original_schema)
        self.assertFalse(self.temporary_table_exists())

    def test_inbound_foreign_key_is_rejected_before_changes(self):
        self.conn.execute("""
            CREATE TABLE "dependent""table" (
                dependent_id INTEGER PRIMARY KEY,
                acknowledgement_id INTEGER,
                FOREIGN KEY (acknowledgement_id)
                    REFERENCES acknowledgements(acknowledgement_id)
            )
        """)
        self.conn.commit()
        original_schema = self.schema_snapshot()

        with self.assertRaisesRegex(RuntimeError, "has a foreign key"):
            migrate(self.conn)

        self.assertEqual(self.schema_snapshot(), original_schema)
        self.assertFalse(self.temporary_table_exists())

    def test_uppercase_cascade_inbound_foreign_key_is_rejected(self):
        self.assert_case_variant_inbound_foreign_key_rejected(
            "ACKNOWLEDGEMENTS"
        )

    def test_mixed_case_cascade_inbound_foreign_key_is_rejected(self):
        self.assert_case_variant_inbound_foreign_key_rejected(
            "AcKnOwLeDgEmEnTs"
        )

    def test_duplicate_active_records_are_rejected(self):
        self.recreate_acknowledgements("""
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                comment TEXT,
                acknowledgement_type TEXT DEFAULT 'Read',
                active INTEGER DEFAULT 1
            )
        """)
        self.conn.executemany("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id, active
            )
            VALUES ('shift_notes', 1, 1, 1)
        """, ((), ()))
        self.conn.commit()

        with self.assertRaisesRegex(RuntimeError, "duplicate active"):
            migrate(self.conn)

        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM acknowledgements"
            ).fetchone()[0],
            2
        )
        self.assertFalse(self.temporary_table_exists())

    def test_invalid_existing_active_value_is_rejected(self):
        self.recreate_acknowledgements("""
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                comment TEXT,
                acknowledgement_type TEXT DEFAULT 'Read',
                active INTEGER DEFAULT 1
            )
        """)
        self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id, active
            )
            VALUES ('shift_notes', 1, 1, 2)
        """)
        self.conn.commit()

        with self.assertRaisesRegex(RuntimeError, "invalid active"):
            migrate(self.conn)

        self.assertEqual(
            self.conn.execute(
                "SELECT active FROM acknowledgements"
            ).fetchone()[0],
            2
        )

    def test_null_existing_active_value_is_rejected(self):
        self.recreate_acknowledgements("""
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                comment TEXT,
                acknowledgement_type TEXT DEFAULT 'Read',
                active INTEGER DEFAULT 1
            )
        """)
        self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id, active
            )
            VALUES ('shift_notes', 1, 1, NULL)
        """)
        self.conn.commit()

        with self.assertRaisesRegex(RuntimeError, "invalid active"):
            migrate(self.conn)

        self.assertIsNone(
            self.conn.execute(
                "SELECT active FROM acknowledgements"
            ).fetchone()[0]
        )

    def test_absent_acknowledgements_table_creates_final_schema(self):
        self.conn.execute("DROP TABLE acknowledgements")
        self.conn.commit()

        self.assertTrue(migrate(self.conn))
        self.assertTrue(schema_is_current(self.conn))
        self.assertEqual(self.acknowledgement_rows(), [])
        self.assert_database_valid()

    def test_foreign_key_setting_is_preserved_when_on(self):
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )

        migrate(self.conn)

        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )

    def test_foreign_key_setting_is_preserved_when_off(self):
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
            0
        )

        migrate(self.conn)

        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
            0
        )
        self.assertTrue(schema_is_current(self.conn))

    def test_verify_database_is_read_only_and_does_not_commit(self):
        migrate(self.conn)
        self.conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id
            )
            VALUES ('uncommitted', 1, 1)
        """)
        self.assertTrue(self.conn.in_transaction)

        verify_database(self.conn)

        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(
            self.conn.execute("""
                SELECT COUNT(*)
                FROM acknowledgements
                WHERE source_table = 'uncommitted'
            """).fetchone()[0],
            0
        )

    def test_missing_invalidator_foreign_key_is_not_current(self):
        self.recreate_acknowledgements(
            final_table_sql(include_foreign_key=False),
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_wrong_invalidator_delete_action_is_not_current(self):
        self.recreate_acknowledgements(
            final_table_sql(foreign_key_delete="CASCADE"),
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_non_equivalent_partial_index_predicate_is_not_current(self):
        self.recreate_acknowledgements(
            final_table_sql(),
            "active = 1 AND source_table <> ''",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_parenthesized_predicate_is_conservatively_not_current(self):
        self.recreate_acknowledgements(
            final_table_sql(),
            "((active = 1))",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_explicit_column_collation_is_not_current(self):
        self.recreate_acknowledgements(
            final_table_sql(
                source_table_definition=(
                    "TEXT NOT NULL COLLATE NOCASE"
                )
            ),
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_autoincrement_text_in_comment_does_not_satisfy_schema(self):
        self.recreate_acknowledgements(
            final_table_sql(
                acknowledgement_id_definition=(
                    "INTEGER PRIMARY KEY "
                    "/* acknowledgement_id INTEGER PRIMARY KEY "
                    "AUTOINCREMENT */"
                )
            ),
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_missing_active_check_in_comment_is_not_current(self):
        self.recreate_acknowledgements(
            final_table_sql(
                active_check=(
                    "/* CHECK (active IN (0, 1)) */"
                )
            ),
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_different_active_check_with_approved_comment_is_not_current(self):
        self.recreate_acknowledgements(
            final_table_sql(
                active_check=(
                    "CHECK (active BETWEEN 0 AND 1) "
                    "/* CHECK (active IN (0, 1)) */"
                )
            ),
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_index_column_collation_is_not_current(self):
        self.recreate_acknowledgements(final_table_sql())
        self.create_custom_active_index(
            "source_table COLLATE NOCASE, source_id, user_id",
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_descending_index_column_is_not_current(self):
        self.recreate_acknowledgements(final_table_sql())
        self.create_custom_active_index(
            "source_table DESC, source_id, user_id",
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_different_index_column_order_is_not_current(self):
        self.recreate_acknowledgements(final_table_sql())
        self.create_custom_active_index(
            "source_id, source_table, user_id",
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_index_predicate_text_in_comment_is_not_current(self):
        self.recreate_acknowledgements(final_table_sql())
        self.create_custom_active_index(
            "source_table, source_id, user_id",
            "1 = 1 /* active = 1 */",
        )

        self.assertFalse(schema_is_current(self.conn))

    def test_incorrect_column_definitions_are_not_current(self):
        incorrect_definitions = (
            {
                "source_id_definition": "TEXT NOT NULL",
            },
            {
                "source_table_definition": "TEXT",
            },
            {
                "active_definition": "INTEGER NOT NULL DEFAULT 0",
            },
            {
                "acknowledgement_id_definition": (
                    "INTEGER PRIMARY KEY"
                ),
            },
            {
                "acknowledgement_id_definition": "INTEGER",
            },
        )

        for definitions in incorrect_definitions:
            with self.subTest(definitions=definitions):
                self.recreate_acknowledgements(
                    final_table_sql(**definitions),
                    "active = 1",
                )
                self.assertFalse(schema_is_current(self.conn))

    def test_additional_column_is_not_current_and_is_rejected(self):
        self.recreate_acknowledgements(
            final_table_sql(additional_column="unexpected TEXT"),
            "active = 1",
        )

        self.assertFalse(schema_is_current(self.conn))

        with self.assertRaisesRegex(RuntimeError, "unexpected column"):
            migrate(self.conn)

    def test_second_migration_run_is_a_genuine_no_op(self):
        self.insert_legacy_row(8)
        self.assertTrue(migrate(self.conn))
        rows_after_first_run = self.acknowledgement_rows()
        schema_after_first_run = self.schema_snapshot()
        sequence_after_first_run = self.conn.execute("""
            SELECT seq
            FROM sqlite_sequence
            WHERE name = 'acknowledgements'
        """).fetchone()

        self.assertFalse(migrate(self.conn))

        self.assertEqual(
            self.acknowledgement_rows(),
            rows_after_first_run
        )
        self.assertEqual(
            self.schema_snapshot(),
            schema_after_first_run
        )
        self.assertEqual(
            self.conn.execute("""
                SELECT seq
                FROM sqlite_sequence
                WHERE name = 'acknowledgements'
            """).fetchone(),
            sequence_after_first_run
        )
        self.assertFalse(self.conn.in_transaction)


if __name__ == "__main__":
    unittest.main()
