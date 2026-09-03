import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_tables
import add_schedule_staff_order
import app


class SchedulePdfExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "schedule.db")
        self.monday = date(2026, 8, 3)
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
        add_schedule_staff_order.migrate(conn)
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
        conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, planned_start_time, planned_end_time,
             assigned_by, assigned_at_utc)
            VALUES (?, 2, '22:00', '05:00', 1, '2026-08-01T15:00:00Z')
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

    def matrix_pdf_url(self, client_id=10, monday=None):
        monday = monday or self.monday
        return f"/schedule/client/{client_id}/week/{monday}/staff-matrix-pdf"

    def add_shift(self, client_id=10, shift_date=None, shift_type="Day",
                  start="08:00", end="16:00"):
        shift_date = shift_date or self.monday.isoformat()
        conn = sqlite3.connect(app.DB_NAME)
        try:
            shift_id = conn.execute("""
                INSERT INTO schedule_shifts
                (client_id, shift_date, shift_type, planned_start_time,
                 planned_end_time, status, notes, created_by, created_at_utc,
                 updated_by, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, 'Published', NULL, 1,
                        '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
            """, (client_id, shift_date, shift_type, start, end)).lastrowid
            conn.commit()
            return shift_id
        finally:
            conn.close()

    def add_assignment(self, shift_id, user_id, start, end):
        conn = sqlite3.connect(app.DB_NAME)
        try:
            conn.execute("""
                INSERT INTO schedule_staff
                (schedule_shift_id, user_id, planned_start_time,
                 planned_end_time, assigned_by, assigned_at_utc)
                VALUES (?, ?, ?, ?, 1, '2026-08-01T15:00:00Z')
            """, (shift_id, user_id, start, end))
            conn.commit()
        finally:
            conn.close()

    def add_order(self, client_id, user_id, display_order):
        conn = sqlite3.connect(app.DB_NAME)
        try:
            conn.execute("""
                INSERT INTO schedule_staff_order
                (client_id, user_id, display_order, updated_by, updated_at_utc)
                VALUES (?, ?, ?, 1, '2026-08-01T15:00:00Z')
            """, (client_id, user_id, display_order))
            conn.commit()
        finally:
            conn.close()

    def test_management_roles_see_export_and_worker_does_not(self):
        for user_id, role in ((1, "Admin"), (2, "Director"), (3, "Program Manager")):
            self.login(user_id, role)
            page = self.client.get(f"/schedule/client/10/week/{self.monday}")
            self.assertIn(b"Export Weekly Schedule PDF", page.data)
            self.assertIn(b"Export Staff Matrix PDF", page.data)
            self.assertEqual(page.data.count(b'target="_blank"'), 2)
            self.assertEqual(page.data.count(b'rel="noopener"'), 2)
        self.login(4, "Support Worker")
        page = self.client.get(f"/schedule/client/10/week/{self.monday}")
        self.assertNotIn(b"Export Weekly Schedule PDF", page.data)
        self.assertNotIn(b"Export Staff Matrix PDF", page.data)
        self.assertEqual(self.client.get(self.pdf_url()).status_code, 403)
        self.assertEqual(self.client.get(self.matrix_pdf_url()).status_code, 403)
        self.login(5, "Program Manager")
        self.assertEqual(self.client.get(self.pdf_url()).status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get(self.pdf_url()).status_code, 302)

    def test_staff_matrix_pdf_contains_worker_rows_and_seven_day_columns(self):
        self.login()
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            response = self.client.get(self.matrix_pdf_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF-1.7"))
        self.assertIn("NHPSG Manager &mdash; Staff Schedule Matrix", self.last_html)
        self.assertIn("Client / Ten &lt;Test&gt;", self.last_html)
        self.assertEqual(self.last_html.count('<th scope="col">'), 8)
        self.assertIn('class="matrix-worker-name">Dana Director</td>', self.last_html)
        self.assertIn('class="matrix-worker-name">Sam Worker</td>', self.last_html)
        self.assertIn("Monday", self.last_html)
        self.assertIn("Sunday", self.last_html)
        self.assertIn("10:00PM&ndash;5:00AM ND", self.last_html)
        self.assertIn("11:00PM&ndash;6:30AM ND", self.last_html)
        self.assertIn("ND = next day", self.last_html)
        self.assertNotIn("Weekly Staff Summary", self.last_html)
        self.assertNotIn("Scheduled Hours", self.last_html)
        self.assertNotIn("Shifts", self.last_html)

    def test_staff_matrix_pdf_orders_multiple_assignments_and_leaves_unscheduled_blank(self):
        first = self.add_shift(
            shift_date=(self.monday + timedelta(days=1)).isoformat(),
            shift_type="Day", start="07:00", end="15:00"
        )
        second = self.add_shift(
            shift_date=(self.monday + timedelta(days=1)).isoformat(),
            shift_type="Afternoon", start="15:00", end="23:00"
        )
        self.add_assignment(first, 4, "07:30", "15:00")
        self.add_assignment(second, 4, "15:00", "23:00")
        self.login()
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            self.client.get(self.matrix_pdf_url())
        sam_row = re.search(
            r'<tr>\s*<td class="matrix-worker-name">Sam Worker</td>(.*?)</tr>',
            self.last_html,
            flags=re.DOTALL,
        ).group(1)
        self.assertLess(
            sam_row.index("7:30AM&ndash;3:00PM"),
            sam_row.index("3:00PM&ndash;11:00PM"),
        )
        self.assertEqual(sam_row.count("<span class=\"matrix-assignment\">"), 3)
        self.assertIn("<td>\n                    \n                </td>", sam_row)

    def test_staff_matrix_pdf_scopes_client_week_and_keeps_inactive_assigned_worker(self):
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute(
            "INSERT INTO users VALUES (6, 'inactive-worker', 'hash', 'Inactive Worker', 'Support Worker', 0)"
        )
        conn.commit()
        conn.close()
        historical = self.add_shift(
            shift_date=(self.monday + timedelta(days=4)).isoformat(),
            shift_type="Day", start="09:00", end="17:00"
        )
        self.add_assignment(historical, 6, "09:15", "16:45")
        other_client = self.add_shift(
            client_id=20, shift_date=self.monday.isoformat(),
            shift_type="Day", start="00:00", end="23:59"
        )
        self.add_assignment(other_client, 4, "00:00", "23:59")
        other_week = self.add_shift(
            shift_date=(self.monday + timedelta(days=7)).isoformat(),
            shift_type="Day", start="00:00", end="23:59"
        )
        self.add_assignment(other_week, 4, "00:00", "23:59")
        self.login()
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            self.client.get(self.matrix_pdf_url())
        self.assertIn("Inactive Worker", self.last_html)
        self.assertIn("9:15AM&ndash;4:45PM", self.last_html)
        self.assertNotIn("11:59PM", self.last_html)
        self.assertNotIn("Client Twenty", self.last_html)
        self.assertNotIn("00:00", self.last_html)

    def test_staff_matrix_pdf_uses_client_order_and_matches_staff_view(self):
        conn = sqlite3.connect(app.DB_NAME)
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, 'hash', ?, 'Support Worker', 1)",
            ((6, "anne", "Anne Worker"), (7, "martin", "Martin Worker")),
        )
        conn.commit()
        conn.close()
        self.add_order(10, 7, 1)
        self.add_order(10, 4, 2)

        self.login()
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            self.client.get(self.matrix_pdf_url())
        pdf_workers = re.findall(
            r'<td class="matrix-worker-name">(.*?)</td>', self.last_html
        )
        staff_html = self.client.get(
            f"/schedule/client/10/week/{self.monday}?view=staff"
        ).data.decode()
        staff_body = staff_html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        staff_workers = re.findall(
            r'class="schedule-staff-matrix-worker-name">([^<]+)', staff_body
        )
        self.assertEqual(
            pdf_workers,
            ["Martin Worker", "Sam Worker", "Anne Worker", "Dana Director"],
        )
        self.assertEqual(staff_workers, pdf_workers)

        self.add_order(20, 4, 1)
        self.add_order(20, 7, 2)
        self.add_order(20, 6, 3)
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            self.client.get(self.matrix_pdf_url(client_id=20))
        client_b_workers = re.findall(
            r'<td class="matrix-worker-name">(.*?)</td>', self.last_html
        )
        self.assertEqual(client_b_workers, ["Sam Worker", "Martin Worker", "Anne Worker"])

    def test_staff_matrix_order_rows_do_not_surface_unassigned_inactive_workers(self):
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute(
            "INSERT INTO users VALUES (6, 'inactive-worker', 'hash', 'Inactive Worker', 'Support Worker', 0)"
        )
        conn.commit()
        conn.close()
        self.add_order(10, 6, 1)
        self.login()
        with patch.object(app, "_generate_schedule_pdf", side_effect=self.fake_renderer):
            self.client.get(self.matrix_pdf_url())
        self.assertNotIn("Inactive Worker", self.last_html)

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
        self.assertIn("<h1>NHPSG Manager &mdash; Weekly Staff Schedule</h1>", self.last_html)
        self.assertIn('<div class="schedule-pdf-week">Week: 2026-08-03 to 2026-08-09</div>', self.last_html)
        self.assertIn("h1 { margin: 0 0 12pt; font-size: 17pt; }", self.last_html)
        self.assertIn(".schedule-pdf-week {", self.last_html)
        self.assertIn("font-size: 16pt; font-weight: bold; text-align: center", self.last_html)
        self.assertIn("Client / Ten &lt;Test&gt;", self.last_html)
        self.assertIn("<div class=\"schedule-pdf-meta\">", self.last_html)
        self.assertEqual(self.last_html.count('class="schedule-pdf-meta-item'), 2)
        self.assertIn("schedule-pdf-meta-client\"><strong>Client:", self.last_html)
        self.assertIn("schedule-pdf-meta-generated\"><strong>Generated:", self.last_html)
        self.assertNotIn("schedule-pdf-meta-item:nth-child", self.last_html)
        self.assertIn("margin-bottom: 14pt", self.last_html)
        self.assertIn('<table class="schedule-pdf-grid">', self.last_html)
        self.assertIn("table-layout: fixed", self.last_html)
        self.assertIn(".schedule-pdf-grid td { padding: 6pt;", self.last_html)
        self.assertIn("margin: 0 0 8pt; line-height: 1.25", self.last_html)
        self.assertIn(".schedule-pdf-notes { margin-top: 5pt", self.last_html)
        self.assertIn(".schedule-pdf-worker-time {", self.last_html)
        self.assertIn("font-weight: bold", self.last_html)
        self.assertEqual(self.last_html.count('<th scope="col">'), 7)
        shift_rows = re.findall(
            r'<tr class="schedule-pdf-shift-row">(.*?)</tr>',
            self.last_html,
            flags=re.DOTALL,
        )
        self.assertEqual(len(shift_rows), 3)
        self.assertTrue(all(row.count("<td>") == 7 for row in shift_rows))
        self.assertEqual(self.last_html.count('class="schedule-pdf-worker"'), 2)
        self.assertIn("2026-", self.last_html)
        self.assertIn("Monday", self.last_html)
        self.assertIn("Sunday", self.last_html)
        self.assertIn("Day", self.last_html)
        self.assertIn("Afternoon", self.last_html)
        self.assertIn("Overnight", self.last_html)
        self.assertIn('class="schedule-pdf-worker-time">10:00PM&ndash;5:00AM ND</span>', self.last_html)
        self.assertIn('class="schedule-pdf-worker-time">11:00PM&ndash;6:30AM ND</span>', self.last_html)
        self.assertNotIn(" next day</span>", self.last_html)
        self.assertIn("ND = next day", self.last_html)
        self.assertLess(self.last_html.index("</table>"), self.last_html.index("ND = next day"))
        self.assertIn("schedule-pdf-meta", self.last_html)
        self.assertIn("margin-bottom: 14pt", self.last_html)
        self.assertIn("margin: 0 auto", self.last_html)
        self.assertIn('class="schedule-pdf-worker-name">Dana Director</span>', self.last_html)
        self.assertIn('class="schedule-pdf-worker-name">Sam Worker</span>', self.last_html)
        self.assertIn('class="schedule-pdf-worker">', self.last_html)
        self.assertIn('class="schedule-pdf-worker-separator"> &mdash; </span><span class="schedule-pdf-worker-name">Dana Director', self.last_html)
        self.assertIn('class="schedule-pdf-worker-separator"> &mdash; </span><span class="schedule-pdf-worker-name">Sam Worker', self.last_html)
        self.assertNotIn("11:45 PM", self.last_html)
        self.assertNotIn("Default planned hours:", self.last_html)
        self.assertNotIn("<strong>Status:</strong>", self.last_html)
        self.assertNotIn("<strong>Staff:</strong>", self.last_html)
        self.assertNotIn("<ul>", self.last_html)
        self.assertNotIn("The live NHPSG Manager schedule is authoritative.", self.last_html)
        self.assertEqual(self.last_html.count("Client / Ten &lt;Test&gt;"), 1)
        self.assertIn('class="schedule-pdf-notes"><strong>Notes:</strong> Notes &lt;must&gt; stay escaped', self.last_html)
        self.assertNotIn("The live NHPSG Manager schedule is authoritative.", self.last_html)
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
