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


class ScheduleClientContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "schedule.db")
        self.future = datetime.now(app.VANCOUVER_TIMEZONE).date() + timedelta(days=2)
        conn = sqlite3.connect(app.DB_NAME)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT, password_hash TEXT, full_name TEXT,
                role TEXT, active INTEGER NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT, activity_class TEXT,
                activity_type TEXT, user_id INTEGER, client_id INTEGER,
                shift_id INTEGER, related_table TEXT, related_id INTEGER,
                summary TEXT, details TEXT, success INTEGER NOT NULL DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT
            );
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Admin', 'Admin', 1),
                (2, 'worker', 'hash', 'Worker', 'Support Worker', 1);
            INSERT INTO clients VALUES
                (10, 'Client Ten', 1),
                (20, 'Client Twenty', 1),
                (30, 'Inactive Client', 0);
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        self.insert_shift(conn, 10, "Client Ten")
        self.insert_shift(conn, 20, "Client Twenty")
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

    def insert_shift(self, conn, client_id, client_name):
        shift_id = conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (?, ?, 'Day', '08:15', '16:45', 'Published', ?, 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, (client_id, self.future.isoformat(), f"{client_name} notes")).lastrowid
        conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, assignment_note, assigned_by, assigned_at_utc)
            VALUES (?, 2, ?, 1, '2026-08-01T15:00:00Z')
        """, (shift_id, f"{client_name} assignment"))
        conn.commit()
        return shift_id

    def test_multiple_clients_show_selection_and_only_active_clients(self):
        self.login()
        response = self.client.get("/schedule")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client Ten", response.data)
        self.assertIn(b"Client Twenty", response.data)
        self.assertNotIn(b"Inactive Client", response.data)
        self.assertIn(b"/schedule/client/10/week/", response.data)
        self.assertIn(b"/schedule/client/20/week/", response.data)

    def test_one_or_zero_active_clients_are_handled_without_guessing(self):
        self.login()
        with sqlite3.connect(app.DB_NAME) as conn:
            conn.execute("UPDATE clients SET active = 0 WHERE client_id = 20")
            conn.commit()
        response = self.client.get("/schedule")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/schedule/client/10/week/", response.location)
        with sqlite3.connect(app.DB_NAME) as conn:
            conn.execute("UPDATE clients SET active = 0 WHERE client_id = 10")
            conn.commit()
        response = self.client.get("/schedule")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No active clients", response.data)

    def test_client_week_filters_rows_staff_and_preserves_navigation(self):
        self.login()
        monday = self.future - timedelta(days=self.future.weekday())
        response = self.client.get(f"/schedule/client/10/week/{monday}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client: Client Ten", response.data)
        self.assertIn(b"Client Ten notes", response.data)
        self.assertNotIn(b"Client Twenty", response.data)
        self.assertNotIn(b"Client Twenty notes", response.data)
        self.assertIn(f"/schedule/client/10/week/{monday - timedelta(days=7)}".encode(), response.data)
        self.assertIn(f"/schedule/client/10/week/{monday + timedelta(days=7)}".encode(), response.data)
        self.assertIn(b"/schedule/client/10/week\"", response.data)
        self.assertIn(f"/schedule/client/10/shift/new?".encode(), response.data)

    def test_invalid_or_inactive_client_is_rejected_and_legacy_route_does_not_guess(self):
        self.login()
        monday = self.future - timedelta(days=self.future.weekday())
        self.assertEqual(self.client.get(f"/schedule/client/999/week/{monday}").status_code, 404)
        self.assertEqual(self.client.get(f"/schedule/client/30/week/{monday}").status_code, 404)
        legacy = self.client.get(f"/schedule/week/{monday}")
        self.assertEqual(legacy.status_code, 302)
        self.assertEqual(legacy.location, "/schedule")

    def test_route_client_is_authoritative_for_create_and_edit(self):
        self.login()
        data = {
            "client_id": "20",
            "shift_date": (self.future + timedelta(days=1)).isoformat(),
            "shift_type": "Afternoon",
            "planned_start_time": "15:00",
            "planned_end_time": "22:00",
            "status": "Draft",
            "notes": "Route client wins",
        }
        response = self.client.post("/schedule/client/10/shift/new", data=data)
        self.assertEqual(response.status_code, 302)
        with sqlite3.connect(app.DB_NAME) as conn:
            row = conn.execute("""
                SELECT schedule_shift_id, client_id FROM schedule_shifts
                WHERE notes = 'Route client wins'
            """).fetchone()
        self.assertEqual(row[1], 10)
        response = self.client.post(
            f"/schedule/shift/{row[0]}/edit",
            data={
                "client_id": "20",
                "shift_date": (self.future + timedelta(days=1)).isoformat(),
                "shift_type": "Afternoon",
                "planned_start_time": "16:00",
                "planned_end_time": "23:00",
                "status": "Published",
                "notes": "Edited without client change",
            },
        )
        self.assertEqual(response.status_code, 302)
        with sqlite3.connect(app.DB_NAME) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT client_id FROM schedule_shifts WHERE schedule_shift_id = ?",
                    (row[0],)
                ).fetchone()[0],
                10,
            )


if __name__ == "__main__":
    unittest.main()
