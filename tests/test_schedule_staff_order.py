import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_staff_order as migration
import add_schedule_tables
import app


class ScheduleStaffOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "schedule.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Admin User', 'Admin', 1),
                (2, 'anne', 'hash', 'Anne Slater', 'Support Worker', 1),
                (3, 'martin', 'hash', 'Martin Lapensee', 'Support Worker', 1),
                (4, 'kaitlynn', 'hash', 'Kaitlynn Gresham', 'Support Worker', 1),
                (5, 'same-one', 'hash', 'Same Name', 'Support Worker', 1),
                (6, 'same-two', 'hash', 'Same Name', 'Support Worker', 1),
                (7, 'inactive-ordered', 'hash', 'Inactive Ordered', 'Support Worker', 0),
                (8, 'inactive-unordered', 'hash', 'Inactive Unordered', 'Support Worker', 0),
                (9, 'new-worker', 'hash', 'New Worker', 'Support Worker', 1);
            INSERT INTO clients VALUES
                (10, 'Client Ten', 1),
                (20, 'Client Twenty', 1);
        """)
        self.conn.commit()
        add_schedule_tables.migrate(self.conn)
        self.shift_id = self.conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (10, '2026-08-10', 'Day', '08:00', '16:00', 'Published',
                    'Existing note', 1, '2026-08-01T15:00:00Z',
                    1, '2026-08-01T15:00:00Z')
        """).lastrowid
        self.conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, planned_start_time,
             planned_end_time, assigned_by, assigned_at_utc)
            VALUES (?, 2, '08:00', '16:00', 1, '2026-08-01T15:00:00Z')
        """, (self.shift_id,))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def migrate_order(self):
        migration.migrate(self.conn)

    def order_row(self, client_id, user_id, display_order, updated_by=1):
        self.conn.execute("""
            INSERT INTO schedule_staff_order
            (client_id, user_id, display_order, updated_by, updated_at_utc)
            VALUES (?, ?, ?, ?, '2026-08-01T15:00:00Z')
        """, (client_id, user_id, display_order, updated_by))
        self.conn.commit()

    def workers(self, user_ids):
        placeholders = ",".join("?" for _ in user_ids)
        return self.conn.execute(
            f"SELECT user_id, full_name FROM users WHERE user_id IN ({placeholders})",
            tuple(user_ids),
        ).fetchall()

    def test_migration_creates_expected_table_constraints_and_is_idempotent(self):
        before_shifts = [tuple(row) for row in self.conn.execute(
            "SELECT * FROM schedule_shifts"
        )]
        before_staff = [tuple(row) for row in self.conn.execute(
            "SELECT * FROM schedule_staff"
        )]

        self.migrate_order()
        self.migrate_order()

        table = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'schedule_staff_order'"
        ).fetchone()[0]
        self.assertIn("PRIMARY KEY (client_id, user_id)", table)
        self.assertIn("UNIQUE (client_id, display_order)", table)
        self.assertIn("CHECK (display_order >= 1)", table)
        self.assertEqual(
            [row[1] for row in self.conn.execute(
                "PRAGMA table_info(schedule_staff_order)"
            )],
            ["client_id", "user_id", "display_order", "updated_by", "updated_at_utc"],
        )
        self.assertEqual(
            [tuple(row) for row in self.conn.execute(
                "SELECT * FROM schedule_staff_order"
            )],
            [],
        )
        self.assertEqual(before_shifts, [tuple(row) for row in self.conn.execute(
            "SELECT * FROM schedule_shifts"
        )])
        self.assertEqual(before_staff, [tuple(row) for row in self.conn.execute(
            "SELECT * FROM schedule_staff"
        )])

    def test_migration_explicit_missing_path_does_not_create_database(self):
        missing = Path(self.temp.name) / "missing.db"
        with self.assertRaises(SystemExit):
            migration.main([str(missing)])
        self.assertFalse(missing.exists())

    def test_foreign_keys_and_constraints_are_enforced(self):
        self.migrate_order()
        self.order_row(10, 2, 1)

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO schedule_staff_order VALUES (10, 2, 2, 1, '2026-08-01T15:00:00Z')"
            )
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO schedule_staff_order VALUES (10, 3, 1, 1, '2026-08-01T15:00:00Z')"
            )
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO schedule_staff_order VALUES (10, 3, 0, 1, '2026-08-01T15:00:00Z')"
            )
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO schedule_staff_order VALUES (999, 3, 2, 1, '2026-08-01T15:00:00Z')"
            )
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO schedule_staff_order VALUES (10, 3, 2, 999, '2026-08-01T15:00:00Z')"
            )
        self.conn.rollback()

        foreign_keys = [
            (row[2], row[3], row[4])
            for row in self.conn.execute("PRAGMA foreign_key_list(schedule_staff_order)")
        ]
        self.assertIn(("users", "updated_by", "user_id"), foreign_keys)
        self.assertIn(("users", "user_id", "user_id"), foreign_keys)
        self.assertIn(("clients", "client_id", "client_id"), foreign_keys)

    def test_empty_ordering_falls_back_alphabetically_with_user_id_tie_breaker(self):
        self.migrate_order()
        result = app._schedule_effective_staff_order(
            self.conn, 10, self.workers((2, 3, 4, 5, 6, 9))
        )
        self.assertEqual(
            [row["user_id"] for row in result],
            [2, 4, 3, 9, 5, 6],
        )

    def test_explicit_ordered_workers_precede_unordered_workers(self):
        self.migrate_order()
        self.order_row(10, 3, 2)
        self.order_row(10, 2, 1)
        result = app._schedule_effective_staff_order(
            self.conn, 10, self.workers((2, 3, 4, 9))
        )
        self.assertEqual([row["user_id"] for row in result], [2, 3, 4, 9])
        self.assertEqual([row["display_order"] for row in result[:2]], [1, 2])

    def test_same_worker_has_independent_order_per_client(self):
        self.migrate_order()
        self.order_row(10, 2, 1)
        self.order_row(10, 3, 2)
        self.order_row(20, 2, 2)
        self.order_row(20, 3, 1)
        client_a = app._schedule_effective_staff_order(
            self.conn, 10, self.workers((2, 3))
        )
        client_b = app._schedule_effective_staff_order(
            self.conn, 20, self.workers((2, 3))
        )
        self.assertEqual([row["user_id"] for row in client_a], [2, 3])
        self.assertEqual([row["user_id"] for row in client_b], [3, 2])

    def test_new_active_worker_is_included_without_an_order_row(self):
        self.migrate_order()
        self.order_row(10, 2, 1)
        result = app._schedule_effective_staff_order(
            self.conn, 10, self.workers((2, 9))
        )
        self.assertEqual([row["user_id"] for row in result], [2, 9])

    def test_inactive_order_rows_persist_but_do_not_expand_supplied_worker_set(self):
        self.migrate_order()
        self.order_row(10, 7, 1)
        active_result = app._schedule_effective_staff_order(
            self.conn, 10, self.workers((2, 9))
        )
        self.assertEqual([row["user_id"] for row in active_result], [2, 9])
        historical_result = app._schedule_effective_staff_order(
            self.conn, 10, self.workers((2, 7, 9))
        )
        self.assertEqual([row["user_id"] for row in historical_result], [7, 2, 9])
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM schedule_staff_order WHERE client_id = 10 AND user_id = 7"
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
