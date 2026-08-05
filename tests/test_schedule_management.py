import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_tables
import app


class ScheduleManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "schedule.db")
        self.future = datetime.now(app.VANCOUVER_TIMEZONE).date() + timedelta(days=5)
        conn = sqlite3.connect(app.DB_NAME)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
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
                success INTEGER NOT NULL DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT
            );
            CREATE TABLE shifts (shift_id INTEGER PRIMARY KEY, client_id INTEGER);
            CREATE TABLE shift_staff (shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER, user_id INTEGER);
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Alex Admin', 'Admin', 1),
                (2, 'director', 'hash', 'Dana Director', 'Director', 1),
                (3, 'manager', 'hash', 'Morgan Manager', 'Program Manager', 1),
                (4, 'worker1', 'hash', 'Sam Worker', 'Support Worker', 1),
                (5, 'worker2', 'hash', 'Taylor Worker', 'Support Worker', 1),
                (6, 'inactive-manager', 'hash', 'Inactive Manager', 'Program Manager', 0),
                (7, 'inactive-worker', 'hash', 'Inactive Worker', 'Support Worker', 0);
            INSERT INTO clients VALUES
                (10, 'Client Ten', 1), (11, 'Inactive Client', 0);
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, role="Admin"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = "Test User"

    def form(self, **overrides):
        values = {
            "client_id": "10",
            "shift_date": self.future.isoformat(),
            "shift_type": "Day",
            "planned_start_time": "07:30",
            "planned_end_time": "15:30",
            "status": "Draft",
            "notes": "Worker-visible handover",
        }
        values.update(overrides)
        return values

    def create(self, **overrides):
        data = self.form(**overrides)
        worker_ids = data.pop("worker_ids", [])
        response = self.client.post(
            "/schedule/client/10/shift/new",
            data={**data, "worker_ids": worker_ids},
            follow_redirects=False,
        )
        return response

    def rows(self, sql, params=()):
        with sqlite3.connect(app.DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql, params).fetchall()

    def test_management_roles_can_open_form_and_worker_cannot_mutate(self):
        for user_id, role in ((1, "Admin"), (2, "Director"), (3, "Program Manager")):
            self.login(user_id, role)
            response = self.client.get("/schedule/client/10/shift/new")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Create Scheduled Shift", response.data)
        self.login(4, "Support Worker")
        self.assertEqual(self.client.get("/schedule/client/10/shift/new").status_code, 403)
        self.assertEqual(self.create().status_code, 403)
        self.login(6, "Program Manager")
        self.assertEqual(self.client.get("/schedule/client/10/shift/new").status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get("/schedule/client/10/shift/new").status_code, 302)

    def test_valid_creation_allows_zero_or_multiple_workers_and_logs_hidden_events(self):
        self.login()
        response = self.create(worker_ids=["4", "5"])
        self.assertEqual(response.status_code, 302)
        self.assertIn("/schedule/client/10/week/", response.location)
        weekly = self.client.get(response.location)
        self.assertIn(b"Scheduled shift created.", weekly.data)
        shifts = self.rows("SELECT * FROM schedule_shifts")
        assignments = self.rows("SELECT * FROM schedule_staff ORDER BY user_id")
        self.assertEqual(len(shifts), 1)
        self.assertEqual([row["user_id"] for row in assignments], [4, 5])
        self.assertEqual(self.rows("SELECT * FROM shifts"), [])
        self.assertEqual(self.rows("SELECT * FROM shift_staff"), [])
        events = self.rows("SELECT * FROM activity_log ORDER BY activity_id")
        self.assertEqual([row["activity_type"] for row in events], [
            "schedule_shift_created", "schedule_staff_assigned",
            "schedule_staff_assigned",
        ])
        self.assertTrue(all(row["storyline_visible"] == 0 for row in events))
        self.assertTrue(all(row["shift_id"] is None for row in events))
        self.assertTrue(all(row["client_id"] == 10 for row in events))

        response = self.create(shift_type="Afternoon", worker_ids=[])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.rows("SELECT * FROM schedule_shifts")), 2)

    def test_validation_rejects_bad_values_and_duplicate_schedule(self):
        self.login()
        invalid = (
            {"shift_type": "Evening"},
            {"status": "Publishedish"},
            {"shift_date": "not-a-date"},
            {"planned_start_time": "7:30"},
            {"planned_end_time": "06:00", "shift_type": "Day"},
            {"worker_ids": ["4", "4"]},
        )
        for overrides in invalid:
            response = self.create(**overrides)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.post(
                "/schedule/client/999/shift/new", data=self.form()
            ).status_code,
            404,
        )
        self.assertEqual(len(self.rows("SELECT * FROM schedule_shifts")), 0)
        self.assertEqual(len(self.rows("SELECT * FROM activity_log")), 0)

        self.assertEqual(self.create().status_code, 302)
        duplicate = self.create()
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(b"already exists", duplicate.data)
        self.assertEqual(len(self.rows("SELECT * FROM schedule_shifts")), 1)

    def test_variable_day_and_overnight_times_are_accepted(self):
        self.login()
        self.assertEqual(self.create(
            planned_start_time="10:15", planned_end_time="12:45",
            worker_ids=[]
        ).status_code, 302)
        self.assertEqual(self.create(
            shift_type="Overnight", planned_start_time="23:00",
            planned_end_time="07:00", worker_ids=[]
        ).status_code, 302)

    def test_edit_updates_fields_and_changes_only_assignment_delta(self):
        self.login()
        self.assertEqual(self.create(worker_ids=["4", "5"]).status_code, 302)
        shift = self.rows("SELECT * FROM schedule_shifts")[0]
        before = self.rows(
            "SELECT * FROM schedule_staff WHERE schedule_shift_id = ? AND user_id = 4",
            (shift["schedule_shift_id"],)
        )[0]
        response = self.client.post(
            f"/schedule/shift/{shift['schedule_shift_id']}/edit",
            data=self.form(
                planned_start_time="08:15", planned_end_time="16:45",
                status="Published", notes="Updated notes", worker_ids=["4"]
            ),
        )
        self.assertEqual(response.status_code, 302)
        updated = self.rows("SELECT * FROM schedule_shifts")[0]
        self.assertEqual(updated["planned_start_time"], "08:15")
        self.assertEqual(updated["status"], "Published")
        self.assertEqual(updated["notes"], "Updated notes")
        after = self.rows(
            "SELECT * FROM schedule_staff WHERE schedule_shift_id = ? AND user_id = 4",
            (shift["schedule_shift_id"],)
        )[0]
        self.assertEqual(before["schedule_staff_id"], after["schedule_staff_id"])
        self.assertEqual(self.rows(
            "SELECT user_id FROM schedule_staff WHERE schedule_shift_id = ?",
            (shift["schedule_shift_id"],)
        )[0]["user_id"], 4)
        types = [row["activity_type"] for row in self.rows(
            "SELECT * FROM activity_log ORDER BY activity_id"
        )]
        self.assertIn("schedule_shift_updated", types)
        self.assertIn("schedule_staff_removed", types)

    def test_past_closed_and_cancelled_schedules_are_read_only(self):
        conn = sqlite3.connect(app.DB_NAME)
        now = datetime.now(app.VANCOUVER_TIMEZONE).date()
        conn.execute("""
            INSERT INTO schedule_shifts
            (schedule_shift_id, client_id, shift_date, shift_type,
             planned_start_time, planned_end_time, status, created_by,
             created_at_utc, updated_by, updated_at_utc)
            VALUES (1, 10, ?, 'Day', '07:00', '15:00', 'Draft', 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, ((now - timedelta(days=1)).isoformat(),))
        conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (10, ?, 'Overnight', '23:00', '07:00', 'Closed', 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, ((now + timedelta(days=1)).isoformat(),))
        conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (10, ?, 'Day', '07:00', '15:00', 'Cancelled', 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, ((now + timedelta(days=2)).isoformat(),))
        conn.commit()
        conn.close()
        self.login()
        for shift in self.rows("SELECT schedule_shift_id FROM schedule_shifts"):
            self.assertEqual(
                self.client.get(
                    f"/schedule/shift/{shift['schedule_shift_id']}/edit"
                ).status_code,
                403,
            )

    def test_activity_log_failure_rolls_back_schedule_and_assignments(self):
        self.login()
        original = app.log_activity

        def fail(*args, **kwargs):
            raise RuntimeError("forced activity log failure")

        app.log_activity = fail
        try:
            response = self.create(worker_ids=["4"])
        finally:
            app.log_activity = original
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows("SELECT * FROM schedule_shifts"), [])
        self.assertEqual(self.rows("SELECT * FROM schedule_staff"), [])
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_management_sees_controls_and_worker_does_not(self):
        self.login()
        self.assertEqual(self.create(worker_ids=[]).status_code, 302)
        self.login(1, "Admin")
        management_page = self.client.get(
            f"/schedule/client/10/week/{self.future - timedelta(days=self.future.weekday())}"
        )
        self.assertIn(b"Edit", management_page.data)
        self.login(4, "Support Worker")
        worker_page = self.client.get(
            f"/schedule/client/10/week/{self.future - timedelta(days=self.future.weekday())}"
        )
        self.assertNotIn(b">Edit</a>", worker_page.data)
        self.assertNotIn(b">Add</a>", worker_page.data)


if __name__ == "__main__":
    unittest.main()
