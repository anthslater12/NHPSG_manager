import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import add_schedule_staff_planned_hours as migration
import add_schedule_tables


class ScheduleStaffPlannedHoursMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "legacy.db"
        self.conn = self.create_legacy_database()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.conn.close()
        self.temp.cleanup()

    def create_legacy_database(self, foreign_keys=True):
        conn = sqlite3.connect(self.path)
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL
            );
            CREATE TABLE schedule_shifts (
                schedule_shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                planned_start_time TEXT NOT NULL,
                planned_end_time TEXT NOT NULL,
                status TEXT NOT NULL,
                notes TEXT,
                created_by INTEGER NOT NULL,
                created_at_utc TEXT NOT NULL,
                updated_by INTEGER NOT NULL,
                updated_at_utc TEXT NOT NULL
            );
            CREATE TABLE schedule_staff (
                schedule_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                assignment_note TEXT,
                assigned_by INTEGER NOT NULL,
                assigned_at_utc TEXT NOT NULL,
                FOREIGN KEY (schedule_shift_id)
                    REFERENCES schedule_shifts(schedule_shift_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (assigned_by) REFERENCES users(user_id),
                UNIQUE (schedule_shift_id, user_id)
            );
            CREATE INDEX idx_schedule_staff_shift
                ON schedule_staff(schedule_shift_id);
            CREATE INDEX idx_schedule_staff_user
                ON schedule_staff(user_id);
            INSERT INTO users VALUES (1, 'Manager'), (2, 'Worker A'), (3, 'Worker B');
            INSERT INTO clients VALUES (10, 'Client');
        """)
        self.insert_shift(conn, 1, "Day", "07:00", "15:00")
        self.insert_shift(conn, 2, "Overnight", "23:00", "07:00")
        self.insert_shift(conn, 3, "Afternoon", "14:00", "22:00")
        conn.executemany("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, assignment_note, assigned_by, assigned_at_utc)
            VALUES (?, ?, ?, 1, '2026-08-05T15:00:00Z')
        """, (
            (1, 2, "Defaulted"),
            (1, 3, "Preserve"),
            (2, 2, "Partial"),
            (3, 3, "Afternoon"),
        ))
        conn.execute("""
            CREATE UNIQUE INDEX idx_schedule_staff_test_unique
            ON schedule_staff(schedule_shift_id, user_id)
        """)
        conn.commit()
        return conn

    @staticmethod
    def insert_shift(conn, shift_id, shift_type, start, end):
        conn.execute("""
            INSERT INTO schedule_shifts
            (schedule_shift_id, client_id, shift_date, shift_type,
             planned_start_time, planned_end_time, status, notes,
             created_by, created_at_utc, updated_by, updated_at_utc)
            VALUES (?, 10, '2026-08-05', ?, ?, ?, 'Draft', NULL,
                    1, '2026-08-05T15:00:00Z', 1, '2026-08-05T15:00:00Z')
        """, (shift_id, shift_type, start, end))

    def test_backfill_preserves_rows_and_populated_values(self):
        self.conn.execute("""
            ALTER TABLE schedule_staff ADD COLUMN planned_start_time TEXT
        """)
        self.conn.execute("""
            ALTER TABLE schedule_staff ADD COLUMN planned_end_time TEXT
        """)
        self.conn.execute("""
            UPDATE schedule_staff
            SET planned_start_time = '08:00', planned_end_time = '16:00'
            WHERE schedule_staff_id = 2
        """)
        self.conn.execute("""
            UPDATE schedule_staff SET planned_end_time = '08:00'
            WHERE schedule_staff_id = 3
        """)
        self.conn.commit()

        before = self.conn.execute("""
            SELECT schedule_staff_id, schedule_shift_id, user_id,
                   assignment_note, assigned_by, assigned_at_utc
            FROM schedule_staff ORDER BY schedule_staff_id
        """).fetchall()
        self.assertFalse(migration.migrate(self.conn))
        after = self.conn.execute("""
            SELECT schedule_staff_id, schedule_shift_id, user_id,
                   assignment_note, assigned_by, assigned_at_utc
            FROM schedule_staff ORDER BY schedule_staff_id
        """).fetchall()
        self.assertEqual(before, after)
        self.assertEqual(
            self.conn.execute("""
                SELECT planned_start_time, planned_end_time
                FROM schedule_staff ORDER BY schedule_staff_id
            """).fetchall(),
            [
                ("07:00", "15:00"),
                ("08:00", "16:00"),
                ("23:00", "08:00"),
                ("14:00", "22:00"),
            ],
        )

    def test_idempotent_and_existing_worker_hours_are_not_overwritten(self):
        self.assertTrue(migration.migrate(self.conn))
        self.conn.execute("""
            UPDATE schedule_staff
            SET planned_start_time = '09:00', planned_end_time = '17:00'
            WHERE schedule_staff_id = 1
        """)
        self.conn.commit()
        self.assertFalse(migration.migrate(self.conn))
        self.assertEqual(
            self.conn.execute("""
                SELECT planned_start_time, planned_end_time
                FROM schedule_staff WHERE schedule_staff_id = 1
            """).fetchone(),
            ("09:00", "17:00"),
        )

    def assert_migration_rejected_and_rolled_back(self, mutate):
        mutate(self.conn)
        self.conn.commit()
        with self.assertRaises(ValueError) as caught:
            migration.migrate(self.conn)
        self.assertIn("schedule_staff_id", str(caught.exception))
        columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(schedule_staff)")
        }
        self.assertNotIn("planned_start_time", columns)
        self.assertNotIn("planned_end_time", columns)

    def test_equal_day_hours_are_rejected_and_rolled_back(self):
        self.assert_migration_rejected_and_rolled_back(
            lambda conn: conn.execute("""
                UPDATE schedule_shifts
                SET planned_start_time = '10:00', planned_end_time = '10:00'
                WHERE schedule_shift_id = 3
            """)
        )

    def test_equal_overnight_hours_are_rejected(self):
        self.assert_migration_rejected_and_rolled_back(
            lambda conn: conn.execute("""
                UPDATE schedule_shifts
                SET planned_start_time = '10:00', planned_end_time = '10:00'
                WHERE schedule_shift_id = 2
            """)
        )

    def test_invalid_time_and_missing_parent_are_rejected(self):
        self.assert_migration_rejected_and_rolled_back(
            lambda conn: conn.execute("""
                UPDATE schedule_shifts
                SET planned_start_time = '7:00'
                WHERE schedule_shift_id = 3
            """)
        )

        self.conn.close()
        self.path.unlink()
        self.conn = self.create_legacy_database(foreign_keys=False)
        self.conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, assigned_by, assigned_at_utc)
            VALUES (999, 2, 1, '2026-08-05T15:00:00Z')
        """)
        self.conn.commit()
        with self.assertRaises(ValueError) as caught:
            migration.migrate(self.conn)
        self.assertIn("parent schedule shift is missing", str(caught.exception))

    def test_fresh_schema_has_nullable_checked_columns_and_preserves_constraints(self):
        path = Path(self.temp.name) / "fresh.db"
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT NOT NULL);
            INSERT INTO users VALUES (1, 'Manager'), (2, 'Worker');
            INSERT INTO clients VALUES (10, 'Client');
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        columns = {
            row[1]: row for row in conn.execute("PRAGMA table_info(schedule_staff)")
        }
        self.assertEqual(columns["planned_start_time"][3], 0)
        self.assertEqual(columns["planned_end_time"][3], 0)
        conn.close()

    def test_standalone_path_precedence_and_missing_path_safety(self):
        explicit = Path(self.temp.name) / "explicit.db"
        environment = Path(self.temp.name) / "environment.db"
        for path in (explicit, environment):
            conn = self.create_legacy_database_for_path(path)
            conn.close()

        with patch.dict(os.environ, {"NHPSG_DB_PATH": str(environment)}):
            migration.main([str(explicit)])
        self.assertIn(
            "planned_start_time",
            {row[1] for row in sqlite3.connect(explicit).execute(
                "PRAGMA table_info(schedule_staff)"
            )},
        )
        self.assertNotIn(
            "planned_start_time",
            {row[1] for row in sqlite3.connect(environment).execute(
                "PRAGMA table_info(schedule_staff)"
            )},
        )

        with patch.dict(os.environ, {"NHPSG_DB_PATH": str(environment)}):
            migration.main([])
        self.assertIn(
            "planned_start_time",
            {row[1] for row in sqlite3.connect(environment).execute(
                "PRAGMA table_info(schedule_staff)"
            )},
        )

        missing = Path(self.temp.name) / "missing.db"
        with patch.dict(os.environ, {}, clear=False):
            with self.assertRaises(SystemExit):
                migration.main([str(missing)])
        self.assertFalse(missing.exists())

    @staticmethod
    def create_legacy_database_for_path(path):
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, full_name TEXT NOT NULL);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT NOT NULL);
            CREATE TABLE schedule_shifts (
                schedule_shift_id INTEGER PRIMARY KEY,
                client_id INTEGER, shift_date TEXT, shift_type TEXT,
                planned_start_time TEXT, planned_end_time TEXT
            );
            CREATE TABLE schedule_staff (
                schedule_staff_id INTEGER PRIMARY KEY,
                schedule_shift_id INTEGER, user_id INTEGER,
                assigned_by INTEGER, assigned_at_utc TEXT
            );
        """)
        conn.commit()
        return conn


if __name__ == "__main__":
    unittest.main()
