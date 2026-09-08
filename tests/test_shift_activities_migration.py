import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import add_shift_activities_table as migration
import app


class ShiftActivitiesMigrationTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        database_path = (
            Path(self.temporary_directory.name) / "activities-schema.db"
        )
        self.conn = sqlite3.connect(database_path)
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY
            );
            INSERT INTO users VALUES (1, 'Admin', 1), (2, 'Support Worker', 1);
            INSERT INTO shifts VALUES (10);
        """)

    def migrate(self):
        migration.migrate(self.conn)

    def insert_activity(self, **overrides):
        values = {
            "shift_id": 10,
            "recorded_by_user_id": 1,
            "start_time": "09:00",
            "end_time": "10:00",
            "a_selected": 1,
            "t_selected": 0,
            "ls_selected": 0,
            "activity_description": "Community walk",
            "status": "Recorded",
            "completed_at_utc": None,
            "completed_by_user_id": None,
            "version_number": 1,
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        return self.conn.execute(
            f"INSERT INTO shift_activities ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        ).lastrowid

    def assert_rejected(self, **overrides):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_activity(**overrides)
        self.conn.rollback()

    def insert_legacy_schema(self):
        self.conn.executescript("""
            CREATE TABLE shift_activities (
                shift_activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                recorded_by_user_id INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                a_selected INTEGER NOT NULL DEFAULT 0 CHECK (a_selected IN (0, 1)),
                t_selected INTEGER NOT NULL DEFAULT 0 CHECK (t_selected IN (0, 1)),
                ls_selected INTEGER NOT NULL DEFAULT 0 CHECK (ls_selected IN (0, 1)),
                activity_description TEXT NOT NULL CHECK (
                    length(trim(
                        activity_description,
                        ' ' || char(9) || char(10) || char(11) || char(12) || char(13)
                    )) > 0
                ),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shift_id) REFERENCES shifts(shift_id) ON DELETE RESTRICT,
                FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
                CHECK (a_selected + t_selected + ls_selected >= 1)
            );
            CREATE INDEX idx_shift_activities_shift_created
                ON shift_activities(shift_id, created_at, shift_activity_id);
        """)
        self.conn.execute("""
            INSERT INTO shift_activities (
                shift_id, recorded_by_user_id, start_time, end_time,
                a_selected, t_selected, ls_selected, activity_description
            ) VALUES (10, 2, '09:00', '10:00', 1, 0, 0, 'Legacy walk')
        """)
        self.conn.commit()

    def test_clean_creation_has_lifecycle_schema(self):
        self.migrate()

        columns = {
            row["name"]: row
            for row in self.conn.execute("PRAGMA table_info(shift_activities)")
        }
        self.assertEqual(set(columns), {
            "shift_activity_id", "shift_id", "recorded_by_user_id",
            "start_time", "end_time", "a_selected", "t_selected",
            "ls_selected", "activity_description", "created_at", "status",
            "completed_at_utc", "completed_by_user_id", "version_number",
        })
        self.assertEqual(columns["status"]["dflt_value"], "'Recorded'")
        self.assertEqual(columns["completed_at_utc"]["notnull"], 0)
        self.assertEqual(columns["completed_by_user_id"]["notnull"], 0)
        self.assertEqual(columns["version_number"]["dflt_value"], "1")

    def test_upgrade_preserves_legacy_rows_ids_and_data(self):
        self.insert_legacy_schema()
        before = self.conn.execute(
            "SELECT shift_activity_id, shift_id, recorded_by_user_id, "
            "start_time, end_time, a_selected, t_selected, ls_selected, "
            "activity_description, created_at FROM shift_activities"
        ).fetchone()

        self.migrate()

        after = self.conn.execute(
            "SELECT shift_activity_id, shift_id, recorded_by_user_id, "
            "start_time, end_time, a_selected, t_selected, ls_selected, "
            "activity_description, created_at, status, completed_at_utc, "
            "completed_by_user_id, version_number FROM shift_activities"
        ).fetchone()
        self.assertEqual(tuple(after[:10]), tuple(before))
        self.assertEqual(tuple(after[10:]), ("Recorded", None, None, 1))

    def test_legacy_foreign_keys_and_expected_index_are_preserved(self):
        self.insert_legacy_schema()
        self.migrate()

        foreign_keys = {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in self.conn.execute(
                "PRAGMA foreign_key_list(shift_activities)"
            )
        }
        self.assertEqual(foreign_keys, {
            ("shift_id", "shifts", "shift_id", "RESTRICT"),
            ("recorded_by_user_id", "users", "user_id", "RESTRICT"),
            ("completed_by_user_id", "users", "user_id", "NO ACTION"),
        })
        indexes = {
            row["name"]
            for row in self.conn.execute("PRAGMA index_list(shift_activities)")
        }
        self.assertIn("idx_shift_activities_shift_created", indexes)

    def test_recorded_and_completed_finalized_rows_are_valid(self):
        self.migrate()
        self.insert_activity(status="Recorded")
        self.insert_activity(
            status="Completed",
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1,
        )
        self.assertEqual(
            [row[0] for row in self.conn.execute(
                "SELECT status FROM shift_activities ORDER BY shift_activity_id"
            ).fetchall()],
            ["Recorded", "Completed"],
        )

    def test_category_constraints_and_foreign_keys_remain_enforced(self):
        self.migrate()
        for combination in ((1, 0, 0), (0, 1, 1), (1, 1, 1)):
            self.insert_activity(
                a_selected=combination[0],
                t_selected=combination[1],
                ls_selected=combination[2],
            )
        self.assert_rejected(a_selected=2)
        self.assert_rejected(shift_id=999)
        self.assert_rejected(recorded_by_user_id=999)

    def test_in_progress_accepts_partial_record_with_no_end_time(self):
        self.migrate()
        self.insert_activity(
            status="In Progress",
            end_time=None,
            activity_description=None,
        )
        row = self.conn.execute(
            "SELECT status, end_time, activity_description, version_number "
            "FROM shift_activities"
        ).fetchone()
        self.assertEqual(tuple(row), ("In Progress", None, None, 1))

    def test_in_progress_accepts_category_with_blank_description(self):
        self.migrate()
        self.insert_activity(
            status="In Progress",
            end_time="",
            activity_description=" \t\r\n",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM shift_activities"
            ).fetchone()[0],
            "In Progress",
        )

    def test_in_progress_rejects_empty_whitespace_and_malformed_start_time(self):
        self.migrate()
        for start_time in ("", " \t\r\n", "not-a-time", "9:00", "24:00"):
            with self.subTest(start_time=repr(start_time)):
                self.assert_rejected(
                    status="In Progress",
                    start_time=start_time,
                    end_time=None,
                    activity_description=None,
                )

    def test_in_progress_rejects_meaningless_blank_record(self):
        self.migrate()
        self.assert_rejected(
            status="In Progress",
            end_time=None,
            a_selected=0,
            t_selected=0,
            ls_selected=0,
            activity_description=" \t\r\n",
        )

    def test_in_progress_rejects_completion_metadata(self):
        self.migrate()
        self.assert_rejected(
            status="In Progress",
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1,
        )

    def test_completed_requires_finalized_fields_and_metadata(self):
        self.migrate()
        self.insert_activity(
            status="Completed",
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1,
        )
        self.assert_rejected(
            status="Completed",
            completed_at_utc="2026-01-04T20:02:00Z",
        )
        self.assert_rejected(
            status="Completed",
            completed_by_user_id=1,
        )
        self.assert_rejected(
            status="Completed",
            end_time=None,
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1,
        )
        self.assert_rejected(
            status="Completed",
            a_selected=0,
            t_selected=0,
            ls_selected=0,
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1,
        )
        self.assert_rejected(
            status="Completed",
            activity_description=None,
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1,
        )

    def test_recorded_rejects_completion_metadata_and_invalid_finalized_fields(self):
        self.migrate()
        self.assert_rejected(
            status="Recorded",
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1,
        )
        self.assert_rejected(status="Recorded", end_time=None)
        self.assert_rejected(
            status="Recorded",
            a_selected=0,
            t_selected=0,
            ls_selected=0,
        )
        self.assert_rejected(status="Recorded", activity_description=None)
        self.assert_rejected(
            status="Recorded", start_time="10:00", end_time="09:00"
        )

    def test_lifecycle_status_version_and_completion_timestamp_allowlists(self):
        self.migrate()
        self.assert_rejected(status="Draft")
        self.assert_rejected(status="Recorded", version_number=0)
        self.assert_rejected(
            status="Completed",
            completed_at_utc="2026-01-04T20:02:00+00:00",
            completed_by_user_id=1,
        )
        self.assert_rejected(
            status="Completed",
            completed_at_utc="2026-01-04T20:02:00Z ",
            completed_by_user_id=1,
        )

    def test_migration_is_idempotent(self):
        self.migrate()
        self.insert_activity()
        first_rows = [
            tuple(row)
            for row in self.conn.execute(
                "SELECT * FROM shift_activities"
            ).fetchall()
        ]
        first_schema = self.conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'shift_activities'"
        ).fetchone()[0]

        self.migrate()

        self.assertEqual(
            [tuple(row) for row in self.conn.execute(
                "SELECT * FROM shift_activities"
            ).fetchall()],
            first_rows,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'shift_activities'"
            ).fetchone()[0],
            first_schema,
        )

    def test_failed_rebuild_rolls_back_without_stranding_legacy_table(self):
        self.insert_legacy_schema()
        original_create_table = migration._create_table

        def fail_create_table(conn):
            raise RuntimeError("forced lifecycle rebuild failure")

        migration._create_table = fail_create_table
        try:
            with self.assertRaises(RuntimeError):
                migration.migrate(self.conn)
        finally:
            migration._create_table = original_create_table

        table_names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("shift_activities", table_names)
        self.assertNotIn("shift_activities_lifecycle_legacy", table_names)
        row = self.conn.execute(
            "SELECT activity_description FROM shift_activities"
        ).fetchone()
        self.assertEqual(row[0], "Legacy walk")

    def test_migration_works_inside_caller_transaction(self):
        self.insert_legacy_schema()
        self.conn.execute("INSERT INTO shifts (shift_id) VALUES (11)")
        self.assertTrue(self.conn.in_transaction)

        self.migrate()

        self.assertTrue(self.conn.in_transaction)
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM shifts WHERE shift_id = 11"
            ).fetchone()
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM shift_activities"
            ).fetchone()[0],
            "Recorded",
        )

    def test_main_uses_nhpsg_db_path(self):
        database_path = Path(self.temporary_directory.name) / "configured.db"
        configured = sqlite3.connect(database_path)
        configured.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY);
            CREATE TABLE shifts (shift_id INTEGER PRIMARY KEY);
        """)
        configured.commit()
        configured.close()

        with patch.dict(os.environ, {"NHPSG_DB_PATH": str(database_path)}):
            migration.main()

        configured = sqlite3.connect(database_path)
        try:
            self.assertIsNotNone(
                configured.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'shift_activities'"
                ).fetchone()
            )
        finally:
            configured.close()

    def test_application_lifecycle_helpers_classify_statuses(self):
        self.assertEqual(
            app.SHIFT_ACTIVITY_STATUSES,
            {"In Progress", "Completed", "Recorded"},
        )
        self.assertTrue(app.is_shift_activity_editable("In Progress"))
        self.assertFalse(app.is_shift_activity_editable("Recorded"))
        self.assertTrue(app.is_shift_activity_finalized("Completed"))
        self.assertTrue(app.is_shift_activity_finalized("Recorded"))
        self.assertFalse(app.is_shift_activity_finalized("In Progress"))


if __name__ == "__main__":
    unittest.main()
