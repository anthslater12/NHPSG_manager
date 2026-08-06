import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_tables
import app


class SchedulePdfExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "schedule.db")
        self.monday = app.get_schedule_operational_week_start(
            datetime.now(app.VANCOUVER_TIMEZONE)
        )
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
            CREATE TABLE shifts (shift_id INTEGER PRIMARY KEY, client_id INTEGER);
            CREATE TABLE shift_staff (shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER, user_id INTEGER);
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Alex Admin', 'Admin', 1),
                (2, 'director', 'hash', 'Dana Director', 'Director', 1),
                (3, 'manager', 'hash', 'Morgan Manager', 'Program Manager', 1),
                (4, 'worker', 'hash', 'Sam Worker', 'Support Worker', 1),
                (5, 'inactive-manager', 'hash', 'Inactive Manager', 'Program Manager', 0);
            INSERT INTO clients VALUES
                (10, 'Client / Ten <Test>', 1),
                (20, 'Other Client', 1),
                (30, 'Inactive Client', 0);
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (10, ?, 'Overnight', '23:45', '07:15', 'Published',
                    'Notes <must> stay escaped', 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, ((self.monday + timedelta(days=2)).isoformat(),))
        shift_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, assignment_note, planned_start_time,
             planned_end_time, assigned_by, assigned_at_utc)
            VALUES (?, 4, 'assignment', '23:00', '06:30', 1,
                    '2026-08-01T15:00:00Z')
        """, (shift_id,))
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        self.last_html = None

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, role="Admin"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = "Test User"

    def fake_renderer(self, html):
        self.last_html = html
        return b"%PDF-1.7 fake test PDF"

    def pdf_url(self, client_id=10, monday=None):
        monday = monday or self.monday
        return f"/schedule/client/{client_id}/week/{monday}/pdf"

    def test_management_roles_see_export_and_worker_does_not(self):
        for user_id, role in ((1, "Admin"), (2, "Director"), (3, "Program Manager")):
            self.login(user_id, role)
            page = self.client.get(f"/schedule/client/10/week/{self.monday}")
            self.assertIn(b"Export PDF", page.data)
        self.login(4, "Support Worker")
        page = self.client.get(f"/schedule/client/10/week/{self.monday}")
        self.assertNotIn(b"Export PDF", page.data)
        self.assertEqual(self.client.get(self.pdf_url()).status_code, 403)
        self.login(5, "Program Manager")
        self.assertEqual(self.client.get(self.pdf_url()).status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get(self.pdf_url()).status_code, 302)

    def test_pdf_response_headers_filename_and_selected_client_html(self):
        self.login()
        before = self.rows("SELECT * FROM schedule_shifts")
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            response = self.client.get(self.pdf_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn("inline", response.headers["Content-Disposition"])
        self.assertIn("NHPSG_Schedule_Client_Ten_Test_", response.headers["Content-Disposition"])
        self.assertTrue(response.data.startswith(b"%PDF-1.7"))
        self.assertIn("Client / Ten &lt;Test&gt;", self.last_html)
        self.assertIn("2026-", self.last_html)
        self.assertIn("Monday", self.last_html)
        self.assertIn("Sunday", self.last_html)
        self.assertIn("Day", self.last_html)
        self.assertIn("Afternoon", self.last_html)
        self.assertIn("Overnight", self.last_html)
        self.assertIn("11:45 PM", self.last_html)
        self.assertIn("6:30 AM next day", self.last_html)
        self.assertIn("Sam Worker &mdash; 11:00 PM&ndash;6:30 AM next day", self.last_html)
        self.assertNotIn("Default planned hours: 11:45 PM&ndash;7:15 AM", self.last_html)
        self.assertIn("Notes &lt;must&gt; stay escaped", self.last_html)
        self.assertIn("Published", self.last_html)
        self.assertIn("The live NHPSG Manager schedule is authoritative.", self.last_html)
        self.assertEqual(before, self.rows("SELECT * FROM schedule_shifts"))
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_other_client_and_shift_staff_data_are_not_included(self):
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (20, ?, 'Day', '06:00', '14:00', 'Draft', 'Other notes', 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, (self.monday.isoformat(),))
        conn.execute("INSERT INTO shift_staff VALUES (1, 999, 4)")
        conn.commit()
        conn.close()
        self.login()
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            self.client.get(self.pdf_url())
        self.assertNotIn("Other Client", self.last_html)
        self.assertNotIn("Other notes", self.last_html)
        self.assertNotIn("999", self.last_html)

    def test_empty_week_exports_missing_slots(self):
        with sqlite3.connect(app.DB_NAME) as conn:
            conn.execute("DELETE FROM schedule_shifts")
            conn.commit()
        self.login()
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            response = self.client.get(self.pdf_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.last_html.count("Not scheduled"), 21)

    def test_invalid_client_or_monday_and_renderer_failure_are_safe(self):
        self.login()
        self.assertEqual(self.client.get(self.pdf_url(999)).status_code, 404)
        self.assertEqual(self.client.get(self.pdf_url(30)).status_code, 404)
        self.assertEqual(self.client.get(self.pdf_url(monday="not-a-date")).status_code, 404)
        non_monday = self.monday + timedelta(days=1)
        self.assertEqual(self.client.get(self.pdf_url(monday=non_monday)).status_code, 404)
        with patch.object(app, "_generate_schedule_pdf", side_effect=RuntimeError("failure")):
            response = self.client.get(self.pdf_url())
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b"Traceback", response.data)

    def test_dependency_and_deployment_documentation_are_present(self):
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as file:
            self.assertIn("WeasyPrint==62.3", file.read())
        with open(os.path.join(ROOT, "documentation", "SCHEDULE_PDF_DEPLOYMENT.md"), encoding="utf-8") as file:
            documentation = file.read()
        self.assertIn("WeasyPrint==62.3", documentation)
        self.assertIn("libpango", documentation)
        self.assertIn("restart", documentation)

    def rows(self, sql, params=()):
        with sqlite3.connect(app.DB_NAME) as conn:
            return conn.execute(sql, params).fetchall()


if __name__ == "__main__":
    unittest.main()
