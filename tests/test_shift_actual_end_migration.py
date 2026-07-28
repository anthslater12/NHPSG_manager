import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_shift_actual_end_utc as migration
import add_shift_tables


class ShiftActualEndMigrationTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = (
            Path(self.temporary_directory.name) / "shift_end.db"
        )
        self.conn = sqlite3.connect(self.database_path)
        self.addCleanup(self.conn.close)

    def column(self, table_name, column_name):
        return next(
            (
                row
                for row in self.conn.execute(
                    f'PRAGMA table_info("{table_name}")'
                ).fetchall()
                if row[1] == column_name
            ),
            None
        )

    def create_legacy_shift_schema(self):
        self.conn.executescript("""
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                scheduled_start_time TEXT,
                scheduled_end_time TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT
            );

            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT NOT NULL,
                actual_end_time TEXT,
                sign_on_at TEXT DEFAULT CURRENT_TIMESTAMP,
                sign_off_at TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
        """)
        self.conn.commit()

    def test_fresh_schema_contains_nullable_authoritative_end_fields(self):
        with self.conn:
            add_shift_tables.migrate(self.conn)

        for table_name in ("shifts", "shift_staff"):
            column = self.column(
                table_name,
                "actual_end_at_utc"
            )
            self.assertIsNotNone(column)
            self.assertEqual(column[2].upper(), "TEXT")
            self.assertEqual(column[3], 0)
            self.assertIsNone(column[4])

    def test_migration_adds_fields_and_preserves_existing_rows(self):
        self.create_legacy_shift_schema()
        self.conn.execute("""
            INSERT INTO shifts
            (client_id, shift_date, shift_type)
            VALUES (1, '2026-08-03', 'Day')
        """)
        self.conn.execute("""
            INSERT INTO shift_staff
            (shift_id, user_id, actual_start_time)
            VALUES (1, 2, '08:00')
        """)
        self.conn.commit()

        changed = migration.migrate(self.conn)

        self.assertTrue(changed)
        self.assertTrue(migration.schema_is_current(self.conn))
        self.assertEqual(
            self.conn.execute(
                "SELECT client_id, shift_date, shift_type, "
                "actual_end_at_utc FROM shifts"
            ).fetchall(),
            [(1, "2026-08-03", "Day", None)]
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT shift_id, user_id, actual_start_time, "
                "actual_end_at_utc FROM shift_staff"
            ).fetchall(),
            [(1, 2, "08:00", None)]
        )

    def test_migration_is_idempotent(self):
        self.create_legacy_shift_schema()

        self.assertTrue(migration.migrate(self.conn))
        first_schema = {
            table_name: self.conn.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
            for table_name in ("shifts", "shift_staff")
        }
        self.assertFalse(migration.migrate(self.conn))
        second_schema = {
            table_name: self.conn.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
            for table_name in ("shifts", "shift_staff")
        }

        self.assertEqual(second_schema, first_schema)


if __name__ == "__main__":
    unittest.main()
