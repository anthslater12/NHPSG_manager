import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_sleep_events_table
import app


class SleepEventsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "sleep.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.addCleanup(self.cleanup)
        self.create_database()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def create_database(self):
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, full_name TEXT, role TEXT, active INTEGER);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER);
            CREATE TABLE shifts (shift_id INTEGER PRIMARY KEY, client_id INTEGER, shift_date TEXT, shift_type TEXT, status TEXT);
            CREATE TABLE shift_staff (shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER, user_id INTEGER, active INTEGER);
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT, activity_datetime TEXT,
                activity_class TEXT, activity_type TEXT, user_id INTEGER, client_id INTEGER,
                shift_id INTEGER, related_table TEXT, related_id INTEGER, summary TEXT,
                details TEXT, success INTEGER NOT NULL DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO users VALUES (1, 'Assigned', 'Support Worker', 1), (2, 'Unassigned', 'Support Worker', 1), (3, 'Manager', 'Admin', 1);
            INSERT INTO clients VALUES (1, 'One', 1), (2, 'Two', 1);
            INSERT INTO shifts VALUES (10, 2, '2026-08-02', 'Day', 'Open'), (20, 2, '2026-08-02', 'Day', 'Closed'), (30, 2, '2026-08-02', 'Day', 'Cancelled');
            INSERT INTO shift_staff VALUES (100, 10, 1, 1), (200, 10, 2, 0);
        """)
        add_sleep_events_table.migrate(conn)
        conn.commit()
        conn.close()

    def login(self, user_id, role="Support Worker"):
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=role, full_name="Test User")

    def rows(self, sql):
        conn = sqlite3.connect(self.path)
        result = conn.execute(sql).fetchall()
        conn.close()
        return result

    def post(self, event_type, shift_id=10, event_local="2026-08-02T08:00"):
        return self.client.post(
            f"/shift/{shift_id}/sleep",
            data={"event_type": event_type, "event_local": event_local}
        )

    def test_fresh_schema_has_only_required_sleep_fields_and_constraint(self):
        columns = self.rows("PRAGMA table_info(sleep_events)")
        self.assertEqual([row[1] for row in columns], [
            "sleep_event_id", "client_id", "shift_id", "event_type",
            "event_datetime", "recorded_by_user_id", "created_at"
        ])
        with self.assertRaises(sqlite3.IntegrityError):
            conn = sqlite3.connect(self.path)
            conn.execute("INSERT INTO sleep_events (client_id, shift_id, event_type, event_datetime, recorded_by_user_id) VALUES (2, 10, 'invalid', 'x', 1)")
            conn.commit()

    def test_migration_is_idempotent_and_uses_one_table(self):
        conn = sqlite3.connect(self.path)
        add_sleep_events_table.migrate(conn)
        count = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='sleep_events'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_assigned_worker_records_fell_asleep_with_one_visible_audit(self):
        self.login(1)
        response = self.post("fell_asleep")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT event_type, client_id, shift_id, recorded_by_user_id FROM sleep_events"), [("fell_asleep", 2, 10, 1)])
        self.assertEqual(self.rows("SELECT activity_type, summary, client_id, shift_id, user_id, related_table, related_id, storyline_visible FROM activity_log"), [("sleep_fell_asleep", "Client fell asleep", 2, 10, 1, "sleep_events", 1, 1)])

    def test_assigned_worker_records_woke_up_without_prior_event(self):
        self.login(1)
        response = self.post("woke_up")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT event_type FROM sleep_events"), [("woke_up",)])
        self.assertEqual(self.rows("SELECT activity_type, storyline_visible FROM activity_log"), [("sleep_woke_up", 1)])

    def test_duplicate_events_are_append_only(self):
        self.login(1)
        self.assertEqual(self.post("fell_asleep").status_code, 302)
        self.assertEqual(self.post("fell_asleep", event_local="2026-08-02T08:01").status_code, 302)
        self.assertEqual(self.rows("SELECT event_type FROM sleep_events"), [("fell_asleep",), ("fell_asleep",)])

    def test_unassigned_signed_off_closed_cancelled_and_invalid_submissions_write_nothing(self):
        for user_id, shift_id, event_type in ((2, 10, "fell_asleep"), (1, 20, "woke_up"), (1, 30, "woke_up"), (1, 10, "invalid")):
            self.login(user_id)
            response = self.post(event_type, shift_id)
            self.assertIn(response.status_code, (200, 400, 403))
        self.assertEqual(self.rows("SELECT count(*) FROM sleep_events"), [(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM activity_log WHERE storyline_visible = 1"), [(0,)])

    def test_submitted_client_or_shift_cannot_override_authoritative_context(self):
        self.login(1)
        response = self.client.post("/shift/10/sleep", data={
            "event_type": "fell_asleep", "event_local": "2026-08-02T08:00",
            "client_id": "1", "shift_id": "999"
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT client_id, shift_id FROM sleep_events"), [(2, 10)])

    def test_recent_history_is_newest_first(self):
        self.login(1)
        self.post("fell_asleep", event_local="2026-08-02T06:00")
        self.post("woke_up", event_local="2026-08-02T07:00")
        response = self.client.get("/shift/10/sleep")
        self.assertEqual(response.status_code, 200)
        self.assertLess(response.data.find(b"Woke up"), response.data.find(b"Fell asleep"))


if __name__ == "__main__":
    unittest.main()
