import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_tables
import add_schedule_staff_order
import app


class SchedulePublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "schedule.db")
        self.monday = app.get_schedule_operational_week_start(
            __import__("datetime").datetime.now(app.VANCOUVER_TIMEZONE)
        )
        conn = sqlite3.connect(app.DB_NAME)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,
                full_name TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY, client_name TEXT NOT NULL,
                active INTEGER NOT NULL
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT, activity_class TEXT, activity_type TEXT,
                user_id INTEGER, client_id INTEGER, shift_id INTEGER,
                related_table TEXT, related_id INTEGER, summary TEXT, details TEXT,
                success INTEGER NOT NULL DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0, event_datetime TEXT
            );
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Admin User', 'Admin', 1),
                (2, 'director', 'hash', 'Director User', 'Director', 1),
                (3, 'manager', 'hash', 'Manager User', 'Program Manager', 1),
                (4, 'worker', 'hash', 'Worker User', 'Support Worker', 1);
            INSERT INTO clients VALUES (10, 'Client Ten', 1), (20, 'Client Twenty', 1);
        """)
        add_schedule_tables.migrate(conn)
        add_schedule_staff_order.migrate(conn)
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def add_shift(self, client_id=10, week=None, status="Draft"):
        week = week or self.monday
        conn = sqlite3.connect(app.DB_NAME)
        shift_id = conn.execute("""
            INSERT INTO schedule_shifts
                (client_id, shift_date, shift_type, planned_start_time,
                 planned_end_time, status, notes, created_by, created_at_utc,
                 updated_by, updated_at_utc)
            VALUES (?, ?, 'Day', '08:00', '16:00', ?, 'Note', 1,
                    '2026-01-01T00:00:00Z', 1, '2026-01-01T00:00:00Z')
        """, (client_id, week.isoformat(), status)).lastrowid
        conn.commit()
        conn.close()
        return shift_id

    def rows(self, sql, params=()):
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def post(self, client_id=10, week=None, user_id=1):
        self.login(user_id)
        week = week or self.monday
        return self.client.post(
            f"/schedule/client/{client_id}/week/{week.isoformat()}/publish"
        )

    def test_each_management_role_can_publish_all_draft_week(self):
        for index, user_id in enumerate((1, 2, 3)):
            self.add_shift()
            response = self.post(user_id=user_id)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(
                [row["status"] for row in self.rows(
                    "SELECT status FROM schedule_shifts ORDER BY schedule_shift_id"
                )][-1],
                "Published",
            )
            if index < 2:
                self.tearDown()
                self.setUp()

    def test_support_worker_and_get_cannot_publish(self):
        self.add_shift()
        self.assertEqual(self.post(user_id=4).status_code, 403)
        self.login(1)
        response = self.client.get(
            f"/schedule/client/10/week/{self.monday.isoformat()}/publish"
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.rows("SELECT status FROM schedule_shifts")[0][0], "Draft")

    def test_invalid_week_states_and_past_week_are_rejected(self):
        cases = [
            (None, "This week has no schedule rows"),
            ("Published", "already published"),
            ("Closed", "cannot be published"),
            ("Cancelled", "cannot be published"),
        ]
        for status, message in cases:
            if status:
                self.add_shift(status=status)
            response = self.post()
            self.assertEqual(response.status_code, 302)
            self.assertIn(message.encode(), self.client.get(response.location).data)
            self.tearDown()
            self.setUp()

        self.add_shift(status="Published")
        self.add_shift(week=self.monday + timedelta(days=1))
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"mixed statuses", self.client.get(response.location).data)
        self.tearDown()
        self.setUp()

        past = self.monday - timedelta(days=7)
        self.add_shift(week=past)
        response = self.post(week=past)
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"past schedule week cannot be published", self.client.get(response.location).data)

    def test_publish_scopes_client_and_week_and_logs_once(self):
        self.add_shift()
        self.add_shift(client_id=20)
        self.add_shift(week=self.monday + timedelta(days=7))
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            [row[0] for row in self.rows(
                "SELECT status FROM schedule_shifts ORDER BY schedule_shift_id"
            )],
            ["Published", "Draft", "Draft"],
        )
        events = self.rows(
            "SELECT activity_type, storyline_visible FROM activity_log"
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["activity_type"], "schedule_week_published")
        self.assertEqual(events[0]["storyline_visible"], 0)

    def test_support_worker_sees_week_after_successful_publish(self):
        self.add_shift()
        self.assertEqual(self.post().status_code, 302)
        self.login(4)
        page = self.client.get(
            f"/schedule/client/10/week/{self.monday.isoformat()}"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"schedule-day-card", page.data)
        self.assertNotIn(b"not yet been published", page.data)

    def test_publish_failure_rolls_back_rows_and_activity_log(self):
        self.add_shift()
        with patch.object(app, "log_activity", side_effect=RuntimeError("log failure")):
            response = self.post()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows("SELECT status FROM schedule_shifts")[0][0], "Draft")
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_publish_button_only_appears_for_eligible_management_draft_week(self):
        self.add_shift()
        self.login(1)
        page = self.client.get(f"/schedule/client/10/week/{self.monday.isoformat()}")
        self.assertIn(b"Publish Schedule", page.data)
        page = self.client.get(
            f"/schedule/client/10/week/{self.monday.isoformat()}?view=staff"
        )
        self.assertIn(b"Publish Schedule", page.data)
        self.add_shift(status="Published", week=self.monday + timedelta(days=1))
        page = self.client.get(f"/schedule/client/10/week/{self.monday.isoformat()}")
        self.assertNotIn(b"Publish Schedule", page.data)


if __name__ == "__main__":
    unittest.main()
