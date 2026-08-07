import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import app


class PostShiftDocumentationPhase4BHousekeepingTests(unittest.TestCase):

    NOW = datetime(2026, 8, 6, 19, 0, 0, tzinfo=timezone.utc)
    AFTER_WINDOW = datetime(2026, 8, 6, 19, 0, 1, tzinfo=timezone.utc)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "phase-4b-housekeeping.db")
        self.assertNotEqual(os.path.abspath(self.path), os.path.abspath("nhpsg.db"))
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.create_database()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def create_database(self):
        conn = sqlite3.connect(self.path)
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
            CREATE TABLE housekeeping_tasks (
                housekeeping_task_id INTEGER PRIMARY KEY,
                task_name TEXT NOT NULL,
                instructions TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                occurs TEXT NOT NULL,
                comment_required_attempted INTEGER NOT NULL DEFAULT 0,
                comment_required_not_completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE shift_housekeeping_task_entries (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                housekeeping_task_id INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                comment TEXT,
                completed_by_user_id INTEGER NOT NULL,
                completed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE shift_care_task_entries (
                entry_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                care_task_id INTEGER NOT NULL,
                outcome TEXT NOT NULL
            );
            CREATE TABLE sleep_events (
                sleep_event_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                note TEXT
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
            ((1, "Support Worker"), (2, "Support Worker"),
             (3, "Program Manager"))
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
            INSERT INTO housekeeping_tasks
                (housekeeping_task_id, task_name, instructions, active, occurs,
                 comment_required_attempted, comment_required_not_completed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            (1, "Kitchen Reset", "Clean the kitchen.", 1, "Day", 1, 1),
            (2, "Afternoon Reset", "Clean later.", 1, "Afternoon", 0, 0),
            (3, "Inactive Reset", "Do not show.", 0, "Day", 0, 0),
            (4, "Bathroom Reset", "Clean the bathroom.", 1, "Day", 0, 0),
            (5, "Laundry Reset", "Complete laundry.", 1, "Day", 0, 0),
        ))
        conn.executemany("""
            INSERT INTO schedule_shifts
                (schedule_shift_id, shift_id, default_start_time,
                 default_end_time, status)
            VALUES (?, ?, ?, ?, ?)
        """, ((101, 10, "07:00", "15:00", "Draft"),
              (102, 11, "07:00", "15:00", "Published")))
        conn.executemany("""
            INSERT INTO schedule_staff
                (schedule_staff_id, schedule_shift_id, user_id,
                 planned_start_time, planned_end_time, assignment_note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ((201, 101, 1, "07:15", "15:15", "Keep unchanged"),
              (202, 102, 1, "07:30", "15:30", "Previous unchanged")))
        conn.execute("""
            INSERT INTO shift_care_task_entries VALUES (1, 10, 1, 'Completed')
        """)
        conn.execute(
            "INSERT INTO sleep_events (sleep_event_id, shift_id, event_type) "
            "VALUES (1, 10, 'woke_up')"
        )
        conn.commit()
        conn.close()

    def login(self, shift_id=None, user_id=1, role="Support Worker"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            if shift_id is None:
                session.pop(app.DOCUMENTATION_CONTEXT_SESSION_KEY, None)
            else:
                session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = shift_id

    def now(self, value=None):
        return mock.patch.object(
            app, "get_application_now_utc", return_value=value or self.NOW
        )

    def post(self, shift_id=10, task_id=1, **data):
        values = {"status": "Completed", "comment": "Observed"}
        values.update(data)
        return self.client.post(
            f"/shift/{shift_id}/housekeeping-task/{task_id}/record",
            data=values
        )

    def rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, parameters)]
        finally:
            conn.close()

    def count(self, table):
        return self.rows(f"SELECT COUNT(*) AS count FROM {table}")[0]["count"]

    def lifecycle_snapshot(self, shift_id):
        return {
            "shift": self.rows("SELECT * FROM shifts WHERE shift_id = ?", (shift_id,)),
            "assignment": self.rows("SELECT * FROM shift_staff WHERE shift_id = ?", (shift_id,)),
            "schedule": self.rows("SELECT * FROM schedule_shifts WHERE shift_id = ?", (shift_id,)),
            "planned_hours": self.rows("""
                SELECT ss.* FROM schedule_staff ss
                JOIN schedule_shifts sh ON sh.schedule_shift_id = ss.schedule_shift_id
                WHERE sh.shift_id = ?
            """, (shift_id,)),
        }

    def assert_no_housekeeping_write(self):
        self.assertEqual(self.count("shift_housekeeping_task_entries"), 0)
        self.assertEqual(self.count("activity_log"), 0)

    def trace_route_connection(self, statements):
        original_get_db = app.get_db

        def get_traced_db():
            conn = original_get_db()
            conn.set_trace_callback(
                lambda statement: statements.append(" ".join(statement.split()))
            )
            return conn

        return mock.patch.object(app, "get_db", side_effect=get_traced_db)

    def test_previous_housekeeping_form_opens_for_eligible_context(self):
        self.login(11)
        with self.now():
            response = self.client.get("/shift/11/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Record Housekeeping Task", response.data)
        self.assert_no_housekeeping_write()

    def test_current_context_banner_is_authoritative(self):
        self.login(10)
        with self.now():
            response = self.client.get("/shift/10/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Current Day Shift", response.data)
        self.assertIn(b"Client One", response.data)

    def test_get_nonexistent_shift_rejected_without_writes(self):
        before = self.lifecycle_snapshot(10)
        self.login(999)
        with self.now():
            response = self.client.get("/shift/999/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(10), before)

    def test_get_other_workers_shift_rejected_without_writes(self):
        before = self.lifecycle_snapshot(15)
        self.login(15)
        with self.now():
            response = self.client.get("/shift/15/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(15), before)

    def test_get_missing_sign_on_evidence_rejected_without_writes(self):
        before = self.lifecycle_snapshot(16)
        self.login(16)
        with self.now():
            response = self.client.get("/shift/16/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(16), before)

    def test_get_cancelled_assignment_rejected_without_writes(self):
        before = self.lifecycle_snapshot(17)
        self.login(17)
        with self.now():
            response = self.client.get("/shift/17/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(17), before)

    def test_get_cancelled_shift_rejected_without_writes(self):
        before = self.lifecycle_snapshot(12)
        self.login(12)
        with self.now():
            response = self.client.get("/shift/12/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(12), before)

    def test_get_expired_context_rejected_without_writes(self):
        before = self.lifecycle_snapshot(13)
        self.login(13)
        with self.now(self.AFTER_WINDOW):
            response = self.client.get("/shift/13/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(13), before)

    def test_get_missing_context_rejected_without_writes(self):
        before = self.lifecycle_snapshot(11)
        self.login()
        with self.now():
            response = self.client.get("/shift/11/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 403)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(11), before)

    def test_get_forged_context_rejected_without_writes(self):
        before = self.lifecycle_snapshot(11)
        self.login()
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = "forged"
        with self.now():
            response = self.client.get("/shift/11/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(11), before)

    def test_get_inapplicable_task_rejected_without_writes(self):
        before = self.lifecycle_snapshot(10)
        self.login(10)
        with self.now():
            response = self.client.get("/shift/10/housekeeping-task/2/record")
        self.assertEqual(response.status_code, 404)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(10), before)

    def test_get_inactive_task_rejected_without_writes(self):
        before = self.lifecycle_snapshot(10)
        self.login(10)
        with self.now():
            response = self.client.get("/shift/10/housekeeping-task/3/record")
        self.assertEqual(response.status_code, 404)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(10), before)

    def test_active_fallback_requires_exact_authorized_assignment(self):
        self.login()
        with self.now():
            response = self.client.get("/shift/10/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Current Day Shift", response.data)
        self.assert_no_housekeeping_write()

    def test_previous_context_banner_shows_late_documentation(self):
        self.login(11)
        with self.now():
            response = self.client.get("/shift/11/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Previous Day Shift", response.data)
        self.assertIn(b"late documentation for housekeeping", response.data)
        self.assertIn(b"reopened.", response.data)

    def test_previous_save_uses_exact_selected_shift(self):
        self.login(11)
        with self.now():
            response = self.post(11, client_id="999")
        self.assertEqual(response.status_code, 302)
        entry = self.rows("SELECT * FROM shift_housekeeping_task_entries")[0]
        self.assertEqual((entry["shift_id"], entry["completed_by_user_id"]), (11, 1))
        log = self.rows("SELECT * FROM activity_log")[0]
        self.assertEqual((log["shift_id"], log["client_id"], log["user_id"]), (11, 2, 1))

    def test_previous_save_leaves_consecutive_active_shift_unchanged(self):
        before_active = self.lifecycle_snapshot(10)
        before_previous = self.lifecycle_snapshot(11)
        self.login(11)
        with self.now():
            self.assertEqual(self.post(11).status_code, 302)
        self.assertEqual(self.lifecycle_snapshot(10), before_active)
        self.assertEqual(self.lifecycle_snapshot(11), before_previous)

    def test_nonexistent_shift_rejected_without_writes(self):
        self.login(999)
        with self.now():
            response = self.post(999)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_other_workers_shift_rejected_without_writes(self):
        self.login(15)
        with self.now():
            response = self.post(15)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_missing_sign_on_evidence_rejected_without_writes(self):
        self.login(16)
        with self.now():
            response = self.post(16)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_cancelled_assignment_rejected_with_valid_parent_shift(self):
        self.login(17)
        with self.now():
            response = self.post(17)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_cancelled_shift_rejected_without_writes(self):
        self.login(12)
        with self.now():
            response = self.post(12)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_expired_context_rejected_without_writes(self):
        self.login(13)
        with self.now():
            response = self.post(13)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_exact_boundary_is_eligible_and_one_second_beyond_is_rejected(self):
        self.login(11)
        with self.now(self.NOW):
            self.assertEqual(self.post(11).status_code, 302)
        self.assertEqual(self.count("shift_housekeeping_task_entries"), 1)

        conn = sqlite3.connect(self.path)
        conn.execute("DELETE FROM shift_housekeeping_task_entries")
        conn.execute("DELETE FROM activity_log")
        conn.commit()
        conn.close()
        self.login(11)
        with self.now(self.AFTER_WINDOW):
            self.assertEqual(self.post(11).status_code, 302)
        self.assert_no_housekeeping_write()

    def test_missing_context_rejected_without_writes(self):
        self.login()
        with self.now():
            response = self.post(11)
        self.assertEqual(response.status_code, 403)
        self.assert_no_housekeeping_write()

    def test_management_role_cannot_bypass_worker_eligibility(self):
        self.login(11, user_id=3, role="Program Manager")
        with self.now():
            response = self.post(11)
        self.assertEqual(response.status_code, 403)
        self.assert_no_housekeeping_write()

    def test_forged_context_rejected_without_writes(self):
        self.login()
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = "forged"
        with self.now():
            response = self.post(11)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_stale_form_rejected_after_context_switch(self):
        self.login(11)
        with self.now():
            self.assertEqual(self.client.get("/shift/11/housekeeping-task/1/record").status_code, 200)
        self.login(10)
        with self.now():
            response = self.post(11)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_forged_client_cannot_change_authoritative_linkage(self):
        self.login(10)
        with self.now():
            self.assertEqual(self.post(10, client_id="2").status_code, 302)
        log = self.rows("SELECT client_id, shift_id FROM activity_log")[0]
        self.assertEqual(log, {"client_id": 1, "shift_id": 10})

    def test_post_begins_immediate_before_final_authorization_and_duplicate_check(self):
        statements = []
        self.login(10)
        with self.trace_route_connection(statements):
            with self.now():
                response = self.post(10)
        self.assertEqual(response.status_code, 302)

        begin_index = statements.index("BEGIN IMMEDIATE")
        final_assignment_index = max(
            index for index, statement in enumerate(statements)
            if "FROM shift_staff ss" in statement
        )
        final_task_index = max(
            index for index, statement in enumerate(statements)
            if "FROM housekeeping_tasks" in statement
        )
        duplicate_index = max(
            index for index, statement in enumerate(statements)
            if (
                "SELECT entry_id FROM shift_housekeeping_task_entries" in statement
                and "WHERE shift_id =" in statement
            )
        )
        source_insert_index = next(
            index for index, statement in enumerate(statements)
            if "INSERT INTO shift_housekeeping_task_entries" in statement
        )
        activity_insert_index = next(
            index for index, statement in enumerate(statements)
            if "INSERT INTO activity_log" in statement
        )
        commit_index = statements.index("COMMIT")

        self.assertLess(begin_index, final_assignment_index)
        self.assertLess(begin_index, final_task_index)
        self.assertLess(begin_index, duplicate_index)
        self.assertLess(begin_index, source_insert_index)
        self.assertLess(source_insert_index, activity_insert_index)
        self.assertLess(activity_insert_index, commit_index)
        self.assertEqual(self.count("shift_housekeeping_task_entries"), 1)
        self.assertEqual(self.count("activity_log"), 1)

    def test_post_revalidates_authoritative_context_inside_transaction(self):
        original_loader = app.get_worker_documentation_module_context
        calls = []

        def load_then_revoke(*args, **kwargs):
            calls.append(None)
            context = original_loader(*args, **kwargs)
            if len(calls) == 1:
                conn = sqlite3.connect(self.path)
                try:
                    conn.execute(
                        "UPDATE shift_staff SET active = 0 WHERE shift_id = 10"
                    )
                    conn.commit()
                finally:
                    conn.close()
            return context

        before = self.lifecycle_snapshot(10)
        self.login(10)
        with mock.patch.object(
            app,
            "get_worker_documentation_module_context",
            side_effect=load_then_revoke
        ):
            with self.now():
                response = self.post(10)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(calls), 2)
        self.assert_no_housekeeping_write()
        after = self.lifecycle_snapshot(10)
        self.assertEqual(after["shift"], before["shift"])
        self.assertEqual(after["schedule"], before["schedule"])
        self.assertEqual(after["planned_hours"], before["planned_hours"])
        self.assertEqual(after["assignment"][0]["active"], 0)

    def test_inapplicable_task_rejected_without_writes(self):
        self.login(10)
        with self.now():
            response = self.post(10, 2)
        self.assertEqual(response.status_code, 404)
        self.assert_no_housekeeping_write()

    def test_inactive_task_rejected_without_writes(self):
        self.login(10)
        with self.now():
            response = self.post(10, 3)
        self.assertEqual(response.status_code, 404)
        self.assert_no_housekeeping_write()

    def test_retry_cannot_create_duplicate_entry_or_success_log(self):
        self.login(10)
        with self.now():
            first = self.post(10)
            retry = self.post(10)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(retry.status_code, 302)
        self.assertIn("/shift/10/housekeeping-task-entry/", retry.location)
        self.assertEqual(self.count("shift_housekeeping_task_entries"), 1)
        self.assertEqual(self.count("activity_log"), 1)

    def test_coworker_result_cannot_be_overwritten_or_reattributed(self):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO shift_housekeeping_task_entries
                (shift_id, housekeeping_task_id, outcome, comment, completed_by_user_id)
            VALUES (11, 1, 'Completed', 'Original', 2)
        """)
        conn.commit()
        conn.close()
        self.login(11)
        with self.now():
            response = self.post(11, status="Attempted", comment="Replace")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.rows("SELECT outcome, comment, completed_by_user_id FROM shift_housekeeping_task_entries"), [{"outcome": "Completed", "comment": "Original", "completed_by_user_id": 2}])
        self.assertEqual(self.count("activity_log"), 0)

    def test_previous_existing_result_does_not_redirect_to_edit(self):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO shift_housekeeping_task_entries
                (shift_id, housekeeping_task_id, outcome, completed_by_user_id)
            VALUES (11, 1, 'Completed', 2)
        """)
        conn.commit()
        conn.close()
        self.login(11)
        with self.now():
            response = self.post(11)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("/edit", response.get_data(as_text=True))

    def test_active_existing_result_keeps_existing_edit_redirect(self):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO shift_housekeeping_task_entries
                (shift_id, housekeeping_task_id, outcome, completed_by_user_id)
            VALUES (10, 1, 'Completed', 1)
        """)
        conn.commit()
        conn.close()
        self.login(10)
        with self.now():
            response = self.post(10)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/10/housekeeping-task-entry/", response.location)

    def test_existing_housekeeping_outcomes_remain_valid(self):
        self.login(10)
        with self.now():
            for task_id, status, comment in ((1, "Completed", "Done"), (4, "Attempted", "Tried"), (5, "Not Completed", "Blocked")):
                with self.subTest(status=status):
                    self.assertEqual(self.post(10, task_id, status=status, comment=comment).status_code, 302)
        self.assertEqual(self.count("shift_housekeeping_task_entries"), 3)

    def test_conditional_comment_validation_remains_intact(self):
        self.login(10)
        with self.now():
            for status in ("Attempted", "Not Completed"):
                with self.subTest(status=status):
                    response = self.post(10, 1, status=status, comment="")
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(b"comment is required", response.data)
        self.assert_no_housekeeping_write()

    def test_invalid_housekeeping_outcome_rejected_without_writes(self):
        before = self.lifecycle_snapshot(10)
        self.login(10)
        with self.now():
            response = self.post(
                10,
                status="Invalid Outcome",
                comment="Observed"
            )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(response.location)
        self.assertIn(b"Please select a valid outcome", response.data)
        self.assert_no_housekeeping_write()
        self.assertEqual(self.lifecycle_snapshot(10), before)

    def test_completed_at_comes_from_database_submission_time(self):
        before = self.rows("SELECT datetime('now') AS now")[0]["now"]
        self.login(11)
        with self.now():
            self.assertEqual(self.post(11).status_code, 302)
        after = self.rows("SELECT datetime('now') AS now")[0]["now"]
        completed_at = self.rows("SELECT completed_at FROM shift_housekeeping_task_entries")[0]["completed_at"]
        self.assertGreaterEqual(completed_at, before)
        self.assertLessEqual(completed_at, after)

    def test_previous_save_does_not_backdate_or_create_occurrence_time(self):
        self.login(11)
        with self.now():
            self.assertEqual(self.post(11).status_code, 302)
        entry = self.rows("SELECT * FROM shift_housekeeping_task_entries")[0]
        self.assertNotEqual(entry["completed_at"], "2026-08-06 15:00:00")
        self.assertNotIn("event_datetime", entry)

    def test_activity_log_fields_and_source_linkage_are_exact(self):
        self.login(10)
        with self.now():
            self.assertEqual(self.post(10, status="Attempted", comment="Observed").status_code, 302)
        entry = self.rows("SELECT * FROM shift_housekeeping_task_entries")[0]
        log = self.rows("SELECT * FROM activity_log")[0]
        self.assertEqual(log["activity_class"], "HOUSEKEEPING")
        self.assertEqual(log["activity_type"], "housekeeping_task_attempted")
        self.assertEqual(log["summary"], "Kitchen Reset - Attempted")
        self.assertEqual(log["details"], "Observed")
        self.assertEqual((log["user_id"], log["client_id"], log["shift_id"]), (1, 1, 10))
        self.assertEqual((log["related_table"], log["related_id"]), ("shift_housekeeping_task_entries", entry["entry_id"]))
        self.assertEqual((log["success"], log["storyline_visible"]), (1, 1))

    def test_activity_log_failure_rolls_back_housekeeping_insert(self):
        self.login(10)
        with mock.patch.object(app, "log_activity", side_effect=RuntimeError("log failure")):
            with self.now():
                response = self.post(10)
        self.assertEqual(response.status_code, 500)
        self.assert_no_housekeeping_write()

    def test_source_write_failure_leaves_no_success_log(self):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            CREATE TRIGGER fail_housekeeping_insert
            BEFORE INSERT ON shift_housekeeping_task_entries
            BEGIN
                SELECT RAISE(ABORT, 'source failure');
            END
        """)
        conn.commit()
        conn.close()
        self.login(10)
        with self.now():
            response = self.post(10)
        self.assertEqual(response.status_code, 500)
        self.assert_no_housekeeping_write()

    def test_rejected_requests_create_no_success_log(self):
        self.login(11)
        with self.now(self.AFTER_WINDOW):
            response = self.post(11)
        self.assertEqual(response.status_code, 302)
        self.assert_no_housekeeping_write()

    def test_get_creates_no_housekeeping_entry_or_log(self):
        before = self.lifecycle_snapshot(10)
        self.login(10)
        with self.now():
            response = self.client.get("/shift/10/housekeeping-task/1/record")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.lifecycle_snapshot(10), before)
        self.assert_no_housekeeping_write()

    def test_housekeeping_save_does_not_change_lifecycle_or_schedule_records(self):
        before_active = self.lifecycle_snapshot(10)
        before_previous = self.lifecycle_snapshot(11)
        self.login(11)
        with self.now():
            self.assertEqual(self.post(11).status_code, 302)
        self.assertEqual(self.lifecycle_snapshot(10), before_active)
        self.assertEqual(self.lifecycle_snapshot(11), before_previous)

    def test_care_and_other_module_records_remain_unchanged(self):
        before_care = self.rows("SELECT * FROM shift_care_task_entries")
        before_sleep = self.rows("SELECT * FROM sleep_events")
        self.login(10)
        with self.now():
            self.assertEqual(self.post(10).status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM shift_care_task_entries"), before_care)
        self.assertEqual(self.rows("SELECT * FROM sleep_events"), before_sleep)

    def test_fixture_uses_temporary_database_only(self):
        self.assertTrue(os.path.exists(self.path))
        self.assertNotEqual(os.path.abspath(self.path), os.path.abspath("nhpsg.db"))


if __name__ == "__main__":
    unittest.main()
