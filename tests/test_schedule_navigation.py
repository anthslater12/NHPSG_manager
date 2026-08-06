import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_tables
import app


class ScheduleNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "schedule.db")
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
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Alex Manager', 'Admin', 1),
                (2, 'worker', 'hash', 'Sam Worker', 'Support Worker', 1),
                (3, 'inactive', 'hash', 'Inactive Worker', 'Support Worker', 0),
                (4, 'director', 'hash', 'Dana Director', 'Director', 1),
                (5, 'manager', 'hash', 'Morgan Manager', 'Program Manager', 1);
            INSERT INTO clients VALUES (10, 'Client Ten', 1);
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        self.insert_schedule_data(conn)
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def insert_schedule_data(self, conn):
        values = {
            "client_id": 10,
            "shift_date": "2026-08-03",
            "planned_start_time": "07:30",
            "planned_end_time": "15:30",
            "status": "Published",
            "created_by": 1,
            "created_at_utc": "2026-08-01T15:00:00Z",
            "updated_by": 1,
            "updated_at_utc": "2026-08-01T15:00:00Z",
        }
        ids = {}
        for shift_type, start, end, notes in (
            ("Day", "07:30", "15:30", "Bring the communication book."),
            ("Afternoon", "15:30", "23:00", None),
            ("Overnight", "23:00", "07:30", "Overnight handover."),
        ):
            row = dict(values, shift_type=shift_type,
                       planned_start_time=start, planned_end_time=end,
                       notes=notes)
            columns = ", ".join(row)
            ids[shift_type] = conn.execute(
                f"INSERT INTO schedule_shifts ({columns}) VALUES ({', '.join('?' for _ in row)})",
                tuple(row.values())
            ).lastrowid
        conn.executemany("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, planned_start_time,
             planned_end_time, assigned_by, assigned_at_utc)
            VALUES (?, ?, ?, ?, 1, '2026-08-01T15:00:00Z')
        """, (
            (ids["Day"], 2, "07:00", "15:00"),
            (ids["Day"], 4, "08:00", "16:00"),
            (ids["Afternoon"], 4, "15:30", "23:00"),
        ))
        # This operational assignment must not leak into the planned schedule.
        conn.execute("""
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER,
                user_id INTEGER
            )
        """)
        conn.execute("INSERT INTO shift_staff VALUES (1, 99, 3)")
        conn.commit()

    def login(self, user_id=1, role="Admin"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = "Test User"

    def test_schedule_index_uses_current_vancouver_week(self):
        self.login()
        expected = app.get_schedule_operational_week_start(
            datetime.now(app.VANCOUVER_TIMEZONE)
        ).isoformat()
        response = self.client.get("/schedule")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/schedule/client/10/week/{expected}", response.location)

    def test_week_view_shows_seven_days_and_navigation(self):
        self.login()
        response = self.client.get("/schedule/client/10/week/2026-08-03")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b"schedule-day-card"), 7)
        self.assertIn(b"/schedule/client/10/week/2026-07-27", response.data)
        self.assertIn(b"/schedule/client/10/week/2026-08-10", response.data)
        self.assertNotIn(b"<strong>Client:</strong>", response.data)
        self.assertNotIn(b"<strong>Status:</strong>", response.data)
        self.assertNotIn(b"<strong>Staff:</strong>", response.data)
        self.assertIn(b"7:00AM&ndash;3:00PM &mdash; Sam Worker", response.data)
        self.assertIn(b"8:00AM&ndash;4:00PM &mdash; Dana Director", response.data)
        self.assertNotIn(b"<ul>", response.data)
        self.assertNotIn(b"7:30 AM&ndash;3:30 PM", response.data)
        self.assertNotIn(b"11:00 PM&ndash;7:30 AM", response.data)
        self.assertNotIn(b"Default planned hours: 7:30 AM&ndash;3:30 PM", response.data)
        self.assertNotIn(b"Default planned hours:", response.data)
        self.assertIn(b"Sam Worker", response.data)
        self.assertIn(b"Dana Director", response.data)
        self.assertIn(b"Bring the communication book.", response.data)
        self.assertIn(b"Not scheduled", response.data)

    def test_workers_are_ordered_by_individual_start_and_overnight_next_day_is_worker_specific(self):
        self.login()
        response = self.client.get("/schedule/client/10/week/2026-08-03")
        page = response.data.decode()
        self.assertLess(page.index("7:00AM&ndash;3:00PM &mdash; Sam Worker"), page.index("8:00AM&ndash;4:00PM &mdash; Dana Director"))
        conn = sqlite3.connect(app.DB_NAME)
        overnight_id = conn.execute("""
            SELECT schedule_shift_id FROM schedule_shifts
            WHERE shift_type = 'Overnight'
        """).fetchone()[0]
        conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, planned_start_time, planned_end_time,
             assigned_by, assigned_at_utc)
            VALUES (?, 2, '22:00', '06:00', 1, '2026-08-01T15:00:00Z')
        """, (overnight_id,))
        conn.commit()
        conn.close()
        response = self.client.get("/schedule/client/10/week/2026-08-03")
        self.assertIn(b"10:00PM&ndash;6:00AM next day &mdash; Sam Worker", response.data)

    def test_null_worker_times_fall_back_to_parent_for_display(self):
        self.login()
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("""
            UPDATE schedule_staff
            SET planned_start_time = NULL, planned_end_time = NULL
            WHERE user_id = 2
        """)
        conn.commit()
        conn.close()
        response = self.client.get("/schedule/client/10/week/2026-08-03")
        self.assertIn(b"7:30AM&ndash;3:30PM &mdash; Sam Worker", response.data)

    def test_shift_order_and_no_operational_assignment_or_write_controls(self):
        self.login()
        response = self.client.get("/schedule/client/10/week/2026-08-03")
        page = response.data.decode()
        self.assertLess(page.index("<h4>Day</h4>"), page.index("<h4>Afternoon</h4>"))
        self.assertLess(page.index("<h4>Afternoon</h4>"), page.index("<h4>Overnight</h4>"))
        self.assertNotIn("Inactive Worker", page)
        self.assertNotIn("Save", page)
        self.assertNotIn("Create", page)
        self.assertIn('method="post"', page)

    def test_all_active_roles_can_view_and_inactive_or_anonymous_cannot(self):
        for user_id, role in ((1, "Admin"), (2, "Support Worker"),
                              (4, "Director"), (5, "Program Manager")):
            self.login(user_id, role)
            self.assertEqual(
                self.client.get("/schedule/client/10/week/2026-08-03").status_code, 200
            )
        self.login(3, "Support Worker")
        self.assertEqual(
            self.client.get("/schedule/client/10/week/2026-08-03").status_code, 403
        )
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(
            self.client.get("/schedule/client/10/week/2026-08-03").status_code, 302
        )

    def test_invalid_week_is_rejected(self):
        self.login()
        self.assertEqual(
            self.client.get("/schedule/client/10/week/2026-08-04").status_code, 404
        )
        self.assertEqual(
            self.client.get("/schedule/client/10/week/not-a-date").status_code, 404
        )

    def test_schedule_navigation_is_in_shared_menu_for_active_roles(self):
        for user_id, role in ((1, "Admin"), (2, "Support Worker"),
                              (4, "Director"), (5, "Program Manager")):
            self.login(user_id, role)
            response = self.client.get("/schedule/client/10/week/2026-08-03")
            self.assertIn(b'href="/schedule"', response.data)

    def test_read_only_view_does_not_change_schedule_rows(self):
        self.login()
        with sqlite3.connect(app.DB_NAME) as conn:
            before = tuple(conn.execute("""
                SELECT schedule_shift_id, shift_date, shift_type, notes
                FROM schedule_shifts ORDER BY schedule_shift_id
            """).fetchall())
        self.client.get("/schedule/client/10/week/2026-08-03")
        with sqlite3.connect(app.DB_NAME) as conn:
            after = tuple(conn.execute("""
                SELECT schedule_shift_id, shift_date, shift_type, notes
                FROM schedule_shifts ORDER BY schedule_shift_id
            """).fetchall())
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
