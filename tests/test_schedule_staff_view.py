import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_staff_order
import add_schedule_tables
import app


class ScheduleStaffViewTests(unittest.TestCase):
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
                (2, 'anne', 'hash', 'Anne Worker', 'Support Worker', 1),
                (3, 'zara', 'hash', 'Zara Worker', 'Support Worker', 1),
                (4, 'historical', 'hash', 'Historical Worker', 'Support Worker', 0),
                (5, 'unused', 'hash', 'Unused Worker', 'Support Worker', 0),
                (6, 'director', 'hash', 'Dana Director', 'Director', 1),
                (7, 'manager', 'hash', 'Morgan Manager', 'Program Manager', 1),
                (8, 'worker', 'hash', 'Sam Worker', 'Support Worker', 1);
            INSERT INTO clients VALUES
                (10, 'Client Ten', 1),
                (20, 'Client Twenty', 1);
        """)
        conn.commit()
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

    def add_shift(self, client_id=10, shift_date="2026-08-03",
                  shift_type="Day", start="08:00", end="16:00"):
        conn = sqlite3.connect(app.DB_NAME)
        shift_id = conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, 'Published', NULL, 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, (client_id, shift_date, shift_type, start, end)).lastrowid
        conn.commit()
        conn.close()
        return shift_id

    def add_assignment(self, shift_id, user_id, start=None, end=None):
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, planned_start_time,
             planned_end_time, assigned_by, assigned_at_utc)
            VALUES (?, ?, ?, ?, 1, '2026-08-01T15:00:00Z')
        """, (shift_id, user_id, start, end))
        conn.commit()
        conn.close()

    def set_order(self, client_id, user_id, display_order):
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("""
            INSERT INTO schedule_staff_order
            (client_id, user_id, display_order, updated_by, updated_at_utc)
            VALUES (?, ?, ?, 1, '2026-08-01T15:00:00Z')
        """, (client_id, user_id, display_order))
        conn.commit()
        conn.close()

    def page(self, user_id=1, query=""):
        self.login(user_id)
        return self.client.get(
            f"/schedule/client/10/week/2026-08-03{query}"
        )

    def test_management_view_selection_and_summary(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"schedule-day-card", response.data)
        self.assertNotIn(b"id=\"schedule-staff-view-heading\"", response.data)
        self.assertIn(b"Weekly Staff Summary", response.data)
        self.assertIn(b"view=staff", response.data)

        response = self.page(query="?view=shift")
        self.assertIn(b"schedule-day-card", response.data)
        response = self.page(query="?view=staff")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"id=\"schedule-staff-view-heading\"", response.data)
        self.assertIn(b"Weekly Staff Summary", response.data)

    def test_all_management_roles_can_use_staff_view(self):
        for user_id in (1, 6, 7):
            response = self.page(user_id, "?view=staff")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Staff View", response.data)

    def test_support_worker_cannot_select_staff_view(self):
        response = self.page(8, "?view=staff")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"schedule-day-card", response.data)
        self.assertNotIn(b"schedule-staff-view-heading", response.data)
        self.assertNotIn(b"Planning View:", response.data)
        self.assertNotIn(b"Weekly Staff Summary", response.data)

    def test_matrix_columns_worker_inclusion_and_client_scoping(self):
        shift = self.add_shift()
        self.add_assignment(shift, 4, "09:00", "17:00")
        response = self.page(query="?view=staff")
        page = response.data.decode()
        matrix = page.split('class="schedule-staff-matrix"', 1)[1].split(
            "</table>", 1
        )[0]
        self.assertEqual(matrix.count('<th scope="col">'), 8)
        self.assertIn("Anne Worker", page)
        self.assertIn("Zara Worker", page)
        self.assertIn("Historical Worker", page)
        self.assertNotIn("Unused Worker", page)
        self.assertIn("Sam Worker", page)

        other_shift = self.add_shift(client_id=20)
        self.add_assignment(other_shift, 4, "09:00", "17:00")
        self.set_order(20, 4, 1)
        response = self.page(query="?view=staff")
        self.assertNotIn("Client Twenty", response.data.decode())

    def test_ordering_times_fallback_multiple_assignments_and_overnight(self):
        self.set_order(10, 3, 1)
        self.set_order(10, 2, 2)
        day = self.add_shift(start="08:00", end="16:00")
        afternoon = self.add_shift(shift_type="Afternoon", start="16:00", end="23:00")
        overnight = self.add_shift(
            shift_date="2026-08-04", shift_type="Overnight",
            start="23:00", end="07:30"
        )
        self.add_assignment(day, 2, "09:00", "11:00")
        self.add_assignment(afternoon, 2, "17:00", "19:00")
        self.add_assignment(overnight, 3, None, None)

        page = self.page(query="?view=staff").data.decode()
        self.assertLess(page.index("Zara Worker"), page.index("Anne Worker"))
        self.assertIn("9:00AM&ndash;11:00AM", page)
        self.assertIn("5:00PM&ndash;7:00PM", page)
        self.assertIn("11:00PM&ndash;7:30AM ND", page)
        self.assertNotIn("Add", page)
        self.assertNotIn("Move Up", page)
        self.assertNotIn("Move Down", page)

    def test_staff_view_keeps_existing_pdf_link_and_shift_edit_controls(self):
        shift = self.add_shift(shift_date="2026-08-09")
        response = self.page(query="?view=staff")
        self.assertIn(b"Export Weekly Schedule PDF", response.data)
        self.assertIn(b"Export Staff Matrix PDF", response.data)
        self.assertNotIn(b"schedule_shift_edit", response.data)

        self.add_assignment(shift, 2)
        response = self.page()
        self.assertIn(b"/schedule/shift/", response.data)


if __name__ == "__main__":
    unittest.main()
