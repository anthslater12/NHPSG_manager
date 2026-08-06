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


class ScheduleCopyWeekTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "schedule.db")
        self.current_monday = app.get_schedule_operational_week_start(
            datetime.now(app.VANCOUVER_TIMEZONE)
        )
        self.source_monday = self.current_monday - timedelta(days=7)
        self.future_monday = self.current_monday + timedelta(days=7)
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
                (5, 'inactive-worker', 'hash', 'Old Worker', 'Support Worker', 0),
                (6, 'inactive-manager', 'hash', 'Inactive Manager', 'Program Manager', 0);
            INSERT INTO clients VALUES
                (10, 'Client Ten', 1), (20, 'Client Twenty', 1),
                (30, 'Inactive Client', 0), (40, 'Empty Client', 1);
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        self.insert_source_data(conn)
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

    def insert_source_data(self, conn):
        self.insert_shift(
            conn, 10, self.source_monday, "Day", "08:15", "16:45",
            "Published", "Monday notes", [
                (4, "Day handover", "08:15", "16:45"),
                (5, "Old assignment", "08:45", "17:15"),
            ]
        )
        self.insert_shift(
            conn, 10, self.source_monday + timedelta(days=2), "Overnight",
            "23:30", "07:00", "Closed", "Overnight notes",
            [(4, "Night", "23:45", "07:30")]
        )
        self.insert_shift(
            conn, 20, self.source_monday, "Day", "06:00", "14:00",
            "Draft", "Other client", [(4, "Other", "06:00", "14:00")]
        )

    def insert_shift(self, conn, client_id, shift_date, shift_type, start, end,
                     status, notes, assignments):
        shift_id = conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, '2026-08-01T15:00:00Z',
                    1, '2026-08-01T15:00:00Z')
        """, (
            client_id, shift_date.isoformat(), shift_type, start, end,
            status, notes,
        )).lastrowid
        for assignment in assignments:
            user_id, note, start, end = assignment
            conn.execute("""
                INSERT INTO schedule_staff
                (schedule_shift_id, user_id, assignment_note,
                 planned_start_time, planned_end_time, assigned_by,
                 assigned_at_utc)
                VALUES (?, ?, ?, ?, ?, 1, '2026-08-01T15:00:00Z')
            """, (shift_id, user_id, note, start, end))
        conn.commit()
        return shift_id

    def post_copy(self, client_id=10, monday=None, user_id=1, role="Admin"):
        self.login(user_id, role)
        monday = monday or self.current_monday
        return self.client.post(
            f"/schedule/client/{client_id}/week/{monday}/copy-previous"
        )

    def rows(self, sql, params=()):
        with sqlite3.connect(app.DB_NAME) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(sql, params).fetchall()

    def test_management_roles_see_control_and_worker_cannot_post(self):
        for user_id, role in ((1, "Admin"), (2, "Director"), (3, "Program Manager")):
            self.login(user_id, role)
            page = self.client.get(
                f"/schedule/client/10/week/{self.current_monday}"
            )
            self.assertIn(b"Copy Previous Week", page.data)
        self.login(4, "Support Worker")
        page = self.client.get(f"/schedule/client/10/week/{self.current_monday}")
        self.assertNotIn(b"Copy Previous Week", page.data)
        self.assertEqual(self.post_copy(user_id=4, role="Support Worker").status_code, 403)
        self.assertEqual(self.post_copy(user_id=6, role="Program Manager").status_code, 403)
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.post_copy().status_code, 302)

    def test_complete_copy_preserves_content_and_uses_new_audit_values(self):
        source = self.rows("""
            SELECT * FROM schedule_shifts WHERE client_id = 10
            ORDER BY schedule_shift_id
        """)
        response = self.post_copy()
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/schedule/client/10/week/{self.current_monday}", response.location)
        destination = self.rows("""
            SELECT * FROM schedule_shifts WHERE client_id = 10
              AND shift_date BETWEEN ? AND ? ORDER BY schedule_shift_id
        """, (self.current_monday.isoformat(),
               (self.current_monday + timedelta(days=6)).isoformat()))
        self.assertEqual(len(destination), 2)
        self.assertTrue({row["schedule_shift_id"] for row in source}.isdisjoint(
            {row["schedule_shift_id"] for row in destination}
        ))
        for source_row, destination_row in zip(source, destination):
            self.assertEqual(
                destination_row["shift_date"],
                (datetime.fromisoformat(source_row["shift_date"]) + timedelta(days=7)).date().isoformat()
            )
            for field in ("shift_type", "planned_start_time", "planned_end_time", "status", "notes"):
                self.assertEqual(destination_row[field], source_row[field])
            self.assertEqual(destination_row["created_by"], 1)
            self.assertEqual(destination_row["updated_by"], 1)
            self.assertNotEqual(destination_row["created_at_utc"], source_row["created_at_utc"])

        assignments = self.rows("""
            SELECT ss.*, sh.shift_date FROM schedule_staff ss
            JOIN schedule_shifts sh ON sh.schedule_shift_id = ss.schedule_shift_id
            WHERE sh.client_id = 10 AND sh.shift_date BETWEEN ? AND ?
            ORDER BY sh.shift_date, ss.user_id
        """, (self.current_monday.isoformat(),
               (self.current_monday + timedelta(days=6)).isoformat()))
        self.assertEqual([
            (row["user_id"], row["assignment_note"],
             row["planned_start_time"], row["planned_end_time"])
            for row in assignments
        ], [
            (4, "Day handover", "08:15", "16:45"),
            (5, "Old assignment", "08:45", "17:15"),
            (4, "Night", "23:45", "07:30"),
        ])
        self.assertTrue(all(row["assigned_by"] == 1 for row in assignments))
        event = self.rows("SELECT * FROM activity_log")[0]
        self.assertEqual(event["activity_type"], "schedule_week_copied")
        self.assertEqual(event["client_id"], 10)
        self.assertIsNone(event["shift_id"])
        self.assertEqual(event["storyline_visible"], 0)
        self.assertEqual(event["related_table"], "schedule_shifts")
        self.assertIn("Source week", event["details"])
        self.assertIn("Destination week", event["details"])
        self.assertIn("Shifts copied: 2", event["details"])
        self.assertIn("Staff assignments copied: 3", event["details"])
        self.assertEqual(self.rows("SELECT * FROM shifts"), [])
        self.assertEqual(self.rows("SELECT * FROM shift_staff"), [])

    def test_other_client_rows_do_not_copy_or_block_destination(self):
        self.assertEqual(self.post_copy().status_code, 302)
        self.assertEqual(len(self.rows(
            "SELECT * FROM schedule_shifts WHERE client_id = 20"
        )), 1)
        self.assertEqual(len(self.rows(
            "SELECT * FROM schedule_shifts WHERE client_id = 10 AND shift_date >= ?",
            (self.current_monday.isoformat(),)
        )), 2)

    def test_null_source_worker_hours_copy_using_parent_defaults(self):
        with sqlite3.connect(app.DB_NAME) as conn:
            conn.execute("""
                UPDATE schedule_staff
                SET planned_start_time = NULL, planned_end_time = NULL
                WHERE schedule_shift_id = (
                    SELECT schedule_shift_id FROM schedule_shifts
                    WHERE client_id = 10 AND shift_type = 'Day'
                ) AND user_id = 4
            """)
            conn.commit()
        self.assertEqual(self.post_copy().status_code, 302)
        destination = self.rows("""
            SELECT ss.planned_start_time, ss.planned_end_time
            FROM schedule_staff AS ss
            JOIN schedule_shifts AS sh ON sh.schedule_shift_id = ss.schedule_shift_id
            WHERE sh.client_id = 10 AND sh.shift_date = ? AND sh.shift_type = 'Day'
              AND ss.user_id = 4
        """, (self.current_monday.isoformat(),))[0]
        self.assertEqual((destination[0], destination[1]), ("08:15", "16:45"))

    def test_destination_protection_is_atomic(self):
        conn = sqlite3.connect(app.DB_NAME)
        self.insert_shift(
            conn, 10, self.current_monday, "Day", "09:00", "10:00",
            "Draft", "Existing", []
        )
        conn.close()
        before = len(self.rows("SELECT * FROM schedule_shifts"))
        response = self.post_copy()
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"destination week already contains", self.client.get(response.location).data.lower())
        self.assertEqual(len(self.rows("SELECT * FROM schedule_shifts")), before)
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_empty_source_invalid_client_and_past_week_are_refused(self):
        self.assertEqual(self.post_copy(client_id=40).status_code, 302)
        self.assertIn(b"no previous schedule", self.client.get(
            f"/schedule/client/40/week/{self.current_monday}"
        ).data.lower())
        self.assertEqual(self.post_copy(client_id=999).status_code, 302)
        self.assertEqual(self.post_copy(client_id=30).status_code, 302)
        past = self.current_monday - timedelta(days=7)
        self.assertEqual(self.post_copy(monday=past).status_code, 302)
        self.assertIn(b"past schedule week", self.client.get(
            f"/schedule/client/10/week/{past}"
        ).data.lower())
        self.assertEqual(self.post_copy(monday="not-a-date").status_code, 302)
        non_monday = self.current_monday + timedelta(days=1)
        self.assertEqual(self.post_copy(monday=non_monday).status_code, 302)

    def test_future_destination_is_allowed(self):
        self.assertEqual(self.post_copy(monday=self.current_monday).status_code, 302)
        self.assertEqual(self.post_copy(monday=self.future_monday).status_code, 302)
        self.assertEqual(len(self.rows(
            "SELECT * FROM schedule_shifts WHERE client_id = 10 AND shift_date BETWEEN ? AND ?",
            (self.future_monday.isoformat(),
             (self.future_monday + timedelta(days=6)).isoformat())
        )), 2)

    def test_activity_log_failure_rolls_back_everything(self):
        original = app.log_activity
        before_shifts = self.rows("SELECT * FROM schedule_shifts")
        before_staff = self.rows("SELECT * FROM schedule_staff")

        def fail(*args, **kwargs):
            raise RuntimeError("forced failure")

        app.log_activity = fail
        try:
            response = self.post_copy()
        finally:
            app.log_activity = original
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.rows(
            "SELECT * FROM schedule_shifts WHERE client_id = 10 AND shift_date >= ?",
            (self.current_monday.isoformat(),)
        ), [])
        self.assertEqual(self.rows("SELECT * FROM schedule_shifts"), before_shifts)
        self.assertEqual(self.rows("SELECT * FROM schedule_staff"), before_staff)
        self.assertEqual(self.rows("SELECT * FROM activity_log"), [])


if __name__ == "__main__":
    unittest.main()
