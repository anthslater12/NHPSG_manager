import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import app


class PostShiftDocumentationPhase4ACareTests(unittest.TestCase):

    ACTIVE_NOW = datetime(2026, 8, 6, 19, 0, 0, tzinfo=timezone.utc)
    AFTER_WINDOW = datetime(2026, 8, 6, 19, 0, 1, tzinfo=timezone.utc)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp.name, "phase-4a-care.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.database_path
        app.app.config.update(TESTING=True)
        self.create_database()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def create_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL,
                scheduled_end_time TEXT,
                actual_end_at_utc TEXT,
                closed_at TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT,
                actual_end_at_utc TEXT,
                sign_on_at TEXT,
                sign_off_at TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                start_checklist_completed INTEGER NOT NULL DEFAULT 0,
                end_checklist_completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE care_tasks (
                care_task_id INTEGER PRIMARY KEY,
                task_name TEXT NOT NULL,
                instructions TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                occurs TEXT NOT NULL,
                comment_required_attempted INTEGER NOT NULL DEFAULT 0,
                comment_required_not_completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE shift_care_task_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                care_task_id INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                comment TEXT,
                completed_by_user_id INTEGER NOT NULL,
                completed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE schedule_shifts (
                schedule_shift_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                default_start_time TEXT,
                default_end_time TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE schedule_staff (
                schedule_staff_id INTEGER PRIMARY KEY,
                schedule_shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                planned_start_time TEXT,
                planned_end_time TEXT,
                assignment_note TEXT
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT,
                activity_class TEXT,
                activity_type TEXT,
                user_id INTEGER,
                client_id INTEGER,
                shift_id INTEGER,
                related_table TEXT,
                related_id INTEGER,
                summary TEXT,
                details TEXT,
                success INTEGER,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT
            );
        """)
        conn.executemany(
            "INSERT INTO users (user_id, role) VALUES (?, ?)",
            ((1, "Support Worker"), (2, "Support Worker"), (3, "Program Manager"))
        )
        conn.executemany(
            "INSERT INTO clients (client_id, client_name) VALUES (?, ?)",
            ((1, "Client One"), (2, "Client Two"))
        )
        conn.executemany("""
            INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status,
                 scheduled_end_time, actual_end_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            (10, 1, "2026-08-06", "Day", "Open", "15:00", None),
            (11, 2, "2026-08-06", "Day", "Closed", "15:00",
             "2026-08-06T15:00:00Z"),
            (12, 1, "2026-08-06", "Day", "Cancelled", "15:00",
             "2026-08-06T15:00:00Z"),
            (13, 1, "2026-08-06", "Day", "Closed", "15:00",
             "2026-08-06T14:59:59Z"),
            (14, 1, "2026-08-06", "Afternoon", "Open", "23:00", None),
            (15, 2, "2026-08-06", "Day", "Closed", "15:00",
             "2026-08-06T15:00:00Z"),
            (16, 1, "2026-08-06", "Day", "Open", "15:00", None),
            (17, 1, "2026-08-06", "Day", "Closed", "15:00",
             "2026-08-06T15:00:00Z"),
        ))
        conn.executemany("""
            INSERT INTO shift_staff
                (shift_staff_id, shift_id, user_id, actual_start_time,
                 actual_end_at_utc, sign_on_at, sign_off_at, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            (10, 10, 1, "07:00", None, "2026-08-06T07:00:00Z", None, 1),
            (11, 11, 1, "07:00", "2026-08-06T15:00:00Z",
             "2026-08-06T07:00:00Z", "2026-08-06T15:00:00Z", 0),
            (12, 12, 1, "07:00", "2026-08-06T15:00:00Z",
             "2026-08-06T07:00:00Z", "2026-08-06T15:00:00Z", 0),
            (13, 13, 1, "07:00", "2026-08-06T14:59:59Z",
             "2026-08-06T07:00:00Z", "2026-08-06T14:59:59Z", 0),
            (14, 11, 2, "07:00", "2026-08-06T15:00:00Z",
             "2026-08-06T07:00:00Z", "2026-08-06T15:00:00Z", 0),
            (15, 14, 1, "15:00", None, "2026-08-06T15:00:00Z", None, 1),
            (16, 15, 2, "07:00", "2026-08-06T15:00:00Z",
             "2026-08-06T07:00:00Z", "2026-08-06T15:00:00Z", 0),
            (17, 16, 1, "07:00", None, None, None, 1),
            (18, 17, 1, "07:00", None, None, None, 0),
        ))
        conn.executemany("""
            INSERT INTO schedule_shifts
                (schedule_shift_id, shift_id, default_start_time,
                 default_end_time, status)
            VALUES (?, ?, ?, ?, ?)
        """, (
            (101, 10, "07:00", "15:00", "Draft"),
            (102, 11, "07:00", "15:00", "Published"),
        ))
        conn.executemany("""
            INSERT INTO schedule_staff
                (schedule_staff_id, schedule_shift_id, user_id,
                 planned_start_time, planned_end_time, assignment_note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            (201, 101, 1, "07:15", "15:15", "Keep unchanged"),
            (202, 102, 1, "07:30", "15:30", "Previous unchanged"),
        ))
        conn.executemany("""
            INSERT INTO care_tasks
                (care_task_id, task_name, instructions, active, occurs,
                 comment_required_attempted, comment_required_not_completed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            (1, "Medication Prompt", "Offer the prompt.", 1, "Day", 1, 1),
            (2, "Afternoon Routine", "Complete the routine.", 1, "Afternoon", 0, 0),
            (3, "Inactive Routine", "Do not show.", 0, "Day", 0, 0),
        ))
        conn.commit()
        conn.close()

    def login(self, user_id=1, role="Support Worker", shift_id=None):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            if shift_id is None:
                session.pop(app.DOCUMENTATION_CONTEXT_SESSION_KEY, None)
            else:
                session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = shift_id

    def now(self, value=None):
        return mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=value or self.ACTIVE_NOW
        )

    def post(self, shift_id=10, task_id=1, **data):
        values = {"status": "Completed", "comment": "Observed"}
        values.update(data)
        return self.client.post(
            f"/shift/{shift_id}/care-task/{task_id}/record",
            data=values
        )

    def rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, parameters)]
        finally:
            conn.close()

    def lifecycle_snapshot(self, shift_id):
        return {
            "shift": self.rows(
                "SELECT * FROM shifts WHERE shift_id = ?", (shift_id,)
            ),
            "assignment": self.rows(
                "SELECT * FROM shift_staff WHERE shift_id = ?", (shift_id,)
            ),
            "schedule_shift": self.rows(
                "SELECT * FROM schedule_shifts WHERE shift_id = ?", (shift_id,)
            ),
            "schedule_staff": self.rows(
                """
                SELECT ss.*
                FROM schedule_staff ss
                JOIN schedule_shifts sh
                  ON sh.schedule_shift_id = ss.schedule_shift_id
                WHERE sh.shift_id = ?
                """,
                (shift_id,)
            ),
        }

    def test_active_context_get_shows_current_context_and_form(self):
        self.login(shift_id=10)
        with self.now():
            response = self.client.get("/shift/10/care-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Current Day Shift", response.data)
        self.assertIn(b"Client One", response.data)
        self.assertIn(b"Record Care Task", response.data)

    def test_missing_context_allows_only_exact_authorized_active_route(self):
        self.login(shift_id=None)
        with self.now():
            response = self.client.get("/shift/10/care-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Current Day Shift", response.data)
        self.assertIn(b"Client One", response.data)

    def test_previous_context_get_shows_late_documentation_message(self):
        self.login(shift_id=11)
        with self.now():
            response = self.client.get("/shift/11/care-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Previous Day Shift", response.data)
        self.assertIn(b"late documentation for care that occurred", response.data)
        self.assertIn(b"The shift is not being reopened", response.data)

    def test_active_save_uses_exact_shift_authoritative_client_and_worker(self):
        self.login(shift_id=10)
        with self.now():
            response = self.post(10, client_id="999")
        self.assertEqual(response.status_code, 302)
        entry = self.rows("SELECT * FROM shift_care_task_entries")[0]
        log = self.rows("SELECT * FROM activity_log")[0]
        self.assertEqual(entry["shift_id"], 10)
        self.assertEqual(entry["care_task_id"], 1)
        self.assertEqual(entry["completed_by_user_id"], 1)
        self.assertEqual(log["shift_id"], 10)
        self.assertEqual(log["client_id"], 1)
        self.assertEqual(log["user_id"], 1)
        self.assertEqual(log["related_table"], "shift_care_task_entries")
        self.assertEqual(log["related_id"], entry["entry_id"])
        self.assertEqual(log["activity_type"], "care_task_completed")
        self.assertEqual(log["activity_class"], "CARE")
        self.assertEqual(log["summary"], "Medication Prompt - Completed")
        self.assertEqual(log["details"], "Observed")
        self.assertEqual(log["success"], 1)
        self.assertEqual(log["storyline_visible"], 1)

    def test_previous_save_uses_exact_selected_shift_and_actual_submission_time(self):
        before_lifecycle = self.lifecycle_snapshot(11)
        database_time_before = self.rows(
            "SELECT datetime('now') AS now"
        )[0]["now"]
        self.login(shift_id=11)
        with self.now():
            response = self.post(11, 1, status="Attempted", comment="Late note")
        self.assertEqual(response.status_code, 302)
        entry = self.rows("SELECT * FROM shift_care_task_entries")[0]
        database_time_after = self.rows(
            "SELECT datetime('now') AS now"
        )[0]["now"]
        self.assertEqual(entry["shift_id"], 11)
        self.assertEqual(entry["completed_by_user_id"], 1)
        self.assertIsNotNone(entry["completed_at"])
        self.assertGreaterEqual(entry["completed_at"], database_time_before)
        self.assertLessEqual(entry["completed_at"], database_time_after)
        self.assertNotEqual(entry["completed_at"], "2026-08-06 15:00:00")
        self.assertEqual(
            self.rows("SELECT shift_id, client_id FROM activity_log"),
            [{"shift_id": 11, "client_id": 2}]
        )
        self.assertEqual(self.lifecycle_snapshot(11), before_lifecycle)

    def test_nonexistent_shift_is_rejected_without_care_or_success_log(self):
        self.login(shift_id=999)
        with self.now():
            response = self.post(999, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_other_worker_only_shift_is_rejected_without_writes(self):
        self.login(shift_id=15)
        with self.now():
            response = self.post(15, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_missing_authoritative_sign_on_is_rejected_without_writes(self):
        self.login(shift_id=16)
        with self.now():
            response = self.post(16, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_cancelled_assignment_is_rejected_with_valid_parent_shift(self):
        self.login(shift_id=17)
        with self.now():
            response = self.post(17, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_previous_save_does_not_change_consecutive_active_shift_or_lifecycle(self):
        before_active = self.lifecycle_snapshot(10)
        before_previous = self.lifecycle_snapshot(11)
        self.login(shift_id=11)
        with self.now():
            response = self.post(11, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.lifecycle_snapshot(10), before_active)
        self.assertEqual(self.lifecycle_snapshot(11), before_previous)

    def test_retry_cannot_create_duplicate_care_entry_or_success_log(self):
        self.login(shift_id=10)
        with self.now():
            first = self.post(10, 1)
            retry = self.post(10, 1)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(retry.status_code, 302)
        self.assertIn("/shift/10/care-task-entry/", retry.location)
        self.assertEqual(
            self.rows("SELECT COUNT(*) AS count FROM shift_care_task_entries")[0]["count"],
            1
        )
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) AS count FROM activity_log WHERE success = 1"
            )[0]["count"],
            1
        )

    def test_exact_boundary_is_eligible_and_one_second_beyond_is_rejected(self):
        self.login(shift_id=11)
        with self.now(self.ACTIVE_NOW):
            eligible = self.post(11, 1)
        self.assertEqual(eligible.status_code, 302)

        conn = sqlite3.connect(self.database_path)
        conn.execute("DELETE FROM shift_care_task_entries")
        conn.execute("DELETE FROM activity_log")
        conn.commit()
        conn.close()
        self.login(shift_id=11)
        with self.now(self.AFTER_WINDOW):
            expired = self.post(11, 1)
        self.assertEqual(expired.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_missing_previous_context_does_not_fallback_or_write(self):
        self.login(shift_id=None)
        with self.now():
            response = self.post(11, 1)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_rejected_request_leaves_lifecycle_and_schedule_unchanged(self):
        before_lifecycle = self.lifecycle_snapshot(11)
        self.login(shift_id=11)
        with self.now(self.AFTER_WINDOW):
            response = self.post(11, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.lifecycle_snapshot(11), before_lifecycle)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_malformed_session_context_fails_without_writes(self):
        self.login(shift_id=None)
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = "forged"
        with self.now():
            response = self.post(11, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_other_worker_cancelled_and_expired_contexts_are_rejected(self):
        for shift_id in (12, 13):
            self.login(shift_id=shift_id)
            with self.now():
                response = self.post(shift_id, 1)
            self.assertEqual(response.status_code, 302)
        self.login(shift_id=11)
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = 11
        with self.now(self.AFTER_WINDOW):
            response = self.post(11, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])

    def test_management_role_without_worker_assignment_cannot_bypass(self):
        self.login(user_id=3, role="Program Manager", shift_id=11)
        with self.now():
            response = self.post(11, 1)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])

    def test_switching_context_rejects_stale_form(self):
        self.login(shift_id=11)
        with self.now():
            self.assertEqual(
                self.client.get("/shift/11/care-task/1/record").status_code,
                200
            )
        self.login(shift_id=10)
        with self.now():
            response = self.post(11, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_forged_route_shift_cannot_redirect_save(self):
        self.login(shift_id=11)
        with self.now():
            response = self.post(10, 1)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])

    def test_inactive_and_non_applicable_tasks_are_rejected(self):
        self.login(shift_id=10)
        with self.now():
            inactive = self.post(10, 3)
            wrong_shift_type = self.post(10, 2)
        self.assertEqual(inactive.status_code, 404)
        self.assertEqual(wrong_shift_type.status_code, 404)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])

    def test_validation_failure_writes_nothing(self):
        self.login(shift_id=10)
        with self.now():
            response = self.post(10, 1, status="Attempted", comment="")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"comment is required", response.data)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_existing_previous_result_is_not_edited_or_redirected_to_edit(self):
        conn = sqlite3.connect(self.database_path)
        conn.execute("""
            INSERT INTO shift_care_task_entries
                (shift_id, care_task_id, outcome, comment, completed_by_user_id)
            VALUES (11, 1, 'Completed', 'Original', 2)
        """)
        conn.commit()
        conn.close()
        self.login(shift_id=11)
        with self.now():
            response = self.post(11, 1, status="Attempted", comment="Replace")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.rows(
                "SELECT outcome, comment, completed_by_user_id "
                "FROM shift_care_task_entries"
            ),
            [{
                "outcome": "Completed",
                "comment": "Original",
                "completed_by_user_id": 2
            }]
        )
        self.assertEqual(
            self.rows("SELECT COUNT(*) AS count FROM shift_care_task_entries")[0]["count"],
            1
        )
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_form_opening_creates_no_care_activity_log_entry(self):
        before_lifecycle = self.lifecycle_snapshot(10)
        self.login(shift_id=10)
        with self.now():
            response = self.client.get("/shift/10/care-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.lifecycle_snapshot(10), before_lifecycle)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_active_existing_result_preserves_existing_edit_redirect(self):
        conn = sqlite3.connect(self.database_path)
        conn.execute("""
            INSERT INTO shift_care_task_entries
                (shift_id, care_task_id, outcome, comment, completed_by_user_id)
            VALUES (10, 1, 'Completed', 'Original', 1)
        """)
        conn.commit()
        conn.close()
        self.login(shift_id=10)
        with self.now():
            response = self.post(10, 1)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/10/care-task-entry/", response.location)

    def test_activity_log_failure_rolls_back_source_write(self):
        self.login(shift_id=10)
        with mock.patch.object(app, "log_activity", side_effect=RuntimeError("log failure")):
            with self.now():
                response = self.post(10, 1)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_source_write_failure_leaves_no_success_activity_log(self):
        conn = sqlite3.connect(self.database_path)
        conn.execute("""
            CREATE TRIGGER fail_care_insert
            BEFORE INSERT ON shift_care_task_entries
            BEGIN
                SELECT RAISE(ABORT, 'source failure');
            END
        """)
        conn.commit()
        conn.close()
        self.login(shift_id=10)
        with self.now():
            response = self.post(10, 1)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_care_save_does_not_change_lifecycle_fields(self):
        before_shift = self.rows(
            "SELECT status, actual_end_at_utc, closed_at FROM shifts WHERE shift_id = 10"
        )
        before_assignment = self.rows(
            "SELECT active, actual_start_time, actual_end_at_utc, "
            "sign_on_at, sign_off_at, start_checklist_completed, "
            "end_checklist_completed FROM shift_staff WHERE shift_id = 10"
        )
        self.login(shift_id=10)
        with self.now():
            self.assertEqual(self.post(10, 1).status_code, 302)
        self.assertEqual(
            self.rows(
                "SELECT status, actual_end_at_utc, closed_at FROM shifts WHERE shift_id = 10"
            ),
            before_shift
        )
        self.assertEqual(
            self.rows(
                "SELECT active, actual_start_time, actual_end_at_utc, "
                "sign_on_at, sign_off_at, start_checklist_completed, "
                "end_checklist_completed FROM shift_staff WHERE shift_id = 10"
            ),
            before_assignment
        )


if __name__ == "__main__":
    unittest.main()
