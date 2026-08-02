import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_activity_log_storyline_visibility as storyline_migration
import app


ACTIVITY_LOG_COLUMNS = """
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
    storyline_visible INTEGER NOT NULL DEFAULT 0
"""


class ActivityLogStorylineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "storyline.db")
        self.old_db_name = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.addCleanup(self.cleanup)
        self.create_database()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db_name
        self.temp.cleanup()

    def create_database(self):
        conn = sqlite3.connect(self.path)
        conn.executescript(f"""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                password_hash TEXT,
                full_name TEXT NOT NULL,
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
                status TEXT NOT NULL
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE incident_reports (
                incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                reported_by_user_id INTEGER NOT NULL,
                incident_date TEXT NOT NULL,
                incident_time TEXT NOT NULL,
                location TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                description TEXT NOT NULL,
                actions_taken TEXT,
                follow_up_required INTEGER NOT NULL DEFAULT 0,
                witnesses TEXT,
                injuries INTEGER NOT NULL DEFAULT 0,
                injury_details TEXT,
                police_notified INTEGER NOT NULL DEFAULT 0,
                medical_treatment INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE activity_log ({ACTIVITY_LOG_COLUMNS});
            INSERT INTO users VALUES (7, 'worker', 'x', 'Worker', 'Support Worker', 1);
            INSERT INTO clients VALUES (1, 'One', 1), (2, 'Two', 1);
            INSERT INTO shifts VALUES (20, 2, '2026-08-02', 'Day', 'Open');
            INSERT INTO shift_staff VALUES (30, 20, 7, 1);
        """)
        conn.commit()
        conn.close()

    def test_schema_is_not_null_and_defaults_false(self):
        column = sqlite3.connect(self.path).execute(
            "PRAGMA table_info(activity_log)"
        ).fetchall()[-1]
        self.assertEqual(column[1:], ("storyline_visible", "INTEGER", 1, "0", 0))

    def test_legacy_schema_migration_defaults_existing_rows_false(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE activity_log (activity_id INTEGER PRIMARY KEY, success INTEGER)")
            conn.execute("INSERT INTO activity_log VALUES (1, 1)")
            conn.commit()
            self.assertTrue(storyline_migration.migrate(conn))
            self.assertEqual(conn.execute("SELECT storyline_visible FROM activity_log").fetchone()[0], 0)
            conn.close()

    def test_log_activity_defaults_hidden_and_explicit_operational_event_visible(self):
        conn = sqlite3.connect(self.path)
        app.log_activity(conn, "ADMIN", "configuration_changed", "Config", user_id=7)
        app.log_activity(
            conn, "INCIDENT", "incident_created", "Incident", user_id=7,
            client_id=2, shift_id=20, related_table="incident_reports",
            related_id=9, storyline_visible=True
        )
        app.log_activity(conn, "FOOD_FLUID", "food_fluid_entry_viewed", "Viewed", user_id=7, client_id=2)
        rows = conn.execute("SELECT activity_type, storyline_visible FROM activity_log ORDER BY activity_id").fetchall()
        conn.close()
        self.assertEqual(rows, [("configuration_changed", 0), ("incident_created", 1), ("food_fluid_entry_viewed", 0)])

    def test_incident_uses_active_shift_client_and_preserves_linkage(self):
        with self.client.session_transaction() as session:
            session.update(user_id=7, role="Support Worker", full_name="Worker")
        response = self.client.post("/incident/new", data={
            "incident_date": "2026-08-02", "incident_time": "12:00",
            "location": "Home", "incident_type": "Medical",
            "description": "Client two incident",
        })
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.path)
        row = conn.execute("""
            SELECT al.client_id, al.user_id, al.related_table, al.related_id, al.storyline_visible,
                   ir.client_id
            FROM activity_log al JOIN incident_reports ir ON ir.incident_id = al.related_id
            WHERE al.activity_type = 'incident_created'
        """).fetchone()
        conn.close()
        self.assertEqual(row, (2, 7, "incident_reports", 1, 1, 2))

    def test_activity_log_page_does_not_filter_storyline_visibility(self):
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO activity_log (activity_class, activity_type, summary, storyline_visible) VALUES ('ADMIN', 'audit_only', 'Audit', 0)")
        conn.commit()
        conn.close()
        with self.client.session_transaction() as session:
            session.update(user_id=7, role="Admin", full_name="Worker")
        response = self.client.get("/activity-log")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Audit", response.data)


if __name__ == "__main__":
    unittest.main()
