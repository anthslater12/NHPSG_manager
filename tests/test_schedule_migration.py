import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_schedule_tables as migration


class ScheduleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "schedule.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL
            );
            CREATE TABLE unrelated (value TEXT NOT NULL);
            INSERT INTO users VALUES (1, 'Manager'), (2, 'Worker');
            INSERT INTO clients VALUES (10, 'Client');
            INSERT INTO unrelated VALUES ('preserve me');
        """)
        self.conn.commit()
        migration.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def shift_values(self, **overrides):
        values = {
            "client_id": 10,
            "shift_date": "2026-08-05",
            "shift_type": "Day",
            "planned_start_time": "07:30",
            "planned_end_time": "15:30",
            "status": "Draft",
            "created_by": 1,
            "created_at_utc": "2026-08-05T15:00:00Z",
            "updated_by": 1,
            "updated_at_utc": "2026-08-05T15:00:00Z",
        }
        values.update(overrides)
        return values

    def insert_shift(self, **overrides):
        values = self.shift_values(**overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        return self.conn.execute(
            f"INSERT INTO schedule_shifts ({columns}) VALUES ({placeholders})",
            tuple(values.values())
        ).lastrowid

    def insert_staff(self, schedule_shift_id, user_id=2, assigned_by=1):
        return self.conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, assigned_by, assigned_at_utc)
            VALUES (?, ?, ?, '2026-08-05T15:00:00Z')
        """, (schedule_shift_id, user_id, assigned_by)).lastrowid

    def assert_rejected(self, **overrides):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_shift(**overrides)
        self.conn.rollback()

    def test_tables_columns_foreign_keys_and_indexes(self):
        self.assertEqual(
            set(row["name"] for row in self.conn.execute(
                "PRAGMA table_info(schedule_shifts)"
            )),
            {
                "schedule_shift_id", "client_id", "shift_date", "shift_type",
                "planned_start_time", "planned_end_time", "status", "notes",
                "created_by", "created_at_utc", "updated_by", "updated_at_utc",
            }
        )
        self.assertEqual(
            set(row["name"] for row in self.conn.execute(
                "PRAGMA table_info(schedule_staff)"
            )),
            {
                "schedule_staff_id", "schedule_shift_id", "user_id",
                "assignment_note", "assigned_by", "assigned_at_utc",
            }
        )
        self.assertEqual(
            {
                (row["from"], row["table"], row["to"], row["on_delete"])
                for row in self.conn.execute(
                    "PRAGMA foreign_key_list(schedule_shifts)"
                )
            },
            {
                ("client_id", "clients", "client_id", "NO ACTION"),
                ("created_by", "users", "user_id", "NO ACTION"),
                ("updated_by", "users", "user_id", "NO ACTION"),
            }
        )
        self.assertEqual(
            {
                (row["from"], row["table"], row["to"], row["on_delete"])
                for row in self.conn.execute(
                    "PRAGMA foreign_key_list(schedule_staff)"
                )
            },
            {
                ("schedule_shift_id", "schedule_shifts", "schedule_shift_id", "CASCADE"),
                ("user_id", "users", "user_id", "NO ACTION"),
                ("assigned_by", "users", "user_id", "NO ACTION"),
            }
        )
        indexes = {
            row["name"] for table in ("schedule_shifts", "schedule_staff")
            for row in self.conn.execute(f"PRAGMA index_list({table})")
        }
        self.assertTrue({
            "idx_schedule_shifts_shift_date",
            "idx_schedule_shifts_client_date",
            "idx_schedule_shifts_client_date_type",
            "idx_schedule_shifts_status",
            "idx_schedule_staff_shift",
            "idx_schedule_staff_user",
        }.issubset(indexes))

    def test_migration_is_idempotent_and_preserves_unrelated_data(self):
        migration.migrate(self.conn)
        migration.migrate(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT value FROM unrelated").fetchone()[0],
            "preserve me"
        )

    def test_foreign_keys_and_schedule_uniqueness_are_enforced(self):
        self.assert_rejected(client_id=999)
        self.assert_rejected(created_by=999)
        self.assert_rejected(updated_by=999)
        shift_id = self.insert_shift()
        self.conn.commit()
        self.assert_rejected()
        self.insert_staff(shift_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_staff(shift_id)
        self.conn.rollback()
        self.insert_shift(shift_type="Afternoon")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM schedule_shifts").fetchone()[0],
            2
        )

    def test_multiple_users_and_cascade_delete(self):
        shift_id = self.insert_shift()
        self.insert_staff(shift_id, user_id=1)
        self.insert_staff(shift_id, user_id=2)
        self.conn.commit()
        self.conn.execute(
            "DELETE FROM schedule_shifts WHERE schedule_shift_id = ?",
            (shift_id,)
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM schedule_staff WHERE schedule_shift_id = ?",
                (shift_id,)
            ).fetchone()[0],
            0
        )

    def test_same_user_can_have_day_and_afternoon_double_shift(self):
        day_id = self.insert_shift(shift_type="Day")
        afternoon_id = self.insert_shift(shift_type="Afternoon")
        self.insert_staff(day_id, user_id=2)
        self.insert_staff(afternoon_id, user_id=2)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM schedule_staff WHERE user_id = 2"
            ).fetchone()[0],
            2
        )

    def test_invalid_shift_type_status_and_time_formats_are_rejected(self):
        self.assert_rejected(shift_type="Evening")
        self.assert_rejected(status="Publishedish")
        self.assert_rejected(planned_start_time="7:30")
        self.assert_rejected(planned_end_time="25:00")
        self.assert_rejected(shift_date="05-08-2026")
        self.assert_rejected(created_at_utc="2026-08-05 15:00:00")

    def test_assignment_foreign_keys_and_missing_schedule_shift_are_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_staff(999)
        self.conn.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_staff(self.insert_shift(), user_id=999)
        self.conn.rollback()


if __name__ == "__main__":
    unittest.main()
