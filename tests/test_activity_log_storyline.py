import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_activity_log_storyline_visibility as storyline_migration
import add_activity_log_event_datetime as event_datetime_migration
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
    storyline_visible INTEGER NOT NULL DEFAULT 0,
    event_datetime TEXT NULL
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
        columns = sqlite3.connect(self.path).execute(
            "PRAGMA table_info(activity_log)"
        ).fetchall()
        column = next(row for row in columns if row[1] == "storyline_visible")
        self.assertEqual(column[1:], ("storyline_visible", "INTEGER", 1, "0", 0))
        event_column = next(row for row in columns if row[1] == "event_datetime")
        self.assertEqual(event_column[1:], ("event_datetime", "TEXT", 0, None, 0))

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

    def test_event_datetime_migration_is_idempotent_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE activity_log (activity_id INTEGER PRIMARY KEY, activity_datetime TEXT, summary TEXT)")
            conn.execute("INSERT INTO activity_log VALUES (1, '2026-08-04 10:00:00', 'Legacy')")
            conn.commit()
            self.assertTrue(event_datetime_migration.migrate(conn))
            self.assertFalse(event_datetime_migration.migrate(conn))
            self.assertEqual(
                conn.execute("SELECT activity_datetime, summary, event_datetime FROM activity_log").fetchone(),
                ("2026-08-04 10:00:00", "Legacy", None)
            )
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

    def test_log_activity_preserves_write_time_and_stores_canonical_event_time(self):
        conn = sqlite3.connect(self.path)
        app.log_activity(
            conn, "SLEEP", "sleep_woke_up", "Client woke up", user_id=7,
            client_id=2, event_datetime="2026-08-03T13:00:00Z"
        )
        row = conn.execute(
            "SELECT activity_datetime, event_datetime FROM activity_log"
        ).fetchone()
        conn.close()
        self.assertRegex(row[0], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertEqual(row[1], "2026-08-03T13:00:00Z")

    def test_incident_uses_active_shift_client_and_preserves_linkage(self):
        with self.client.session_transaction() as session:
            session.update(user_id=7, role="Support Worker", full_name="Worker")
        response = self.client.post("/incident/new", data={
            "incident_date": "2026-08-02", "incident_time": "12:00",
            "location": "Home", "incident_type": "Medical",
            "description": "Client two incident", "actions_taken": "Called nurse",
            "injury_details": "Bruise <arm>", "injury": "on",
            "follow_up_required": "on", "police_notified": "on",
        })
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.path)
        row = conn.execute("""
            SELECT al.client_id, al.user_id, al.related_table, al.related_id, al.storyline_visible,
                   al.summary, al.details, al.success,
                   ir.client_id, ir.injuries, ir.injury_details
            FROM activity_log al JOIN incident_reports ir ON ir.incident_id = al.related_id
            WHERE al.activity_type = 'incident_created'
        """).fetchone()
        conn.close()
        self.assertEqual(row, (
            2, 7, "incident_reports", 1, 1,
            "Incident created: Medical",
            "Location: Home\nInjury: Yes\n"
            "Injury details: Bruise <arm>\nActions taken: Called nurse\n"
            "Description: Client two incident\nFollow-up required: Yes",
            1, 2, 1, "Bruise <arm>"
        ))

    def test_incident_details_omit_blank_optional_fields_and_write_one_log_row(self):
        with self.client.session_transaction() as session:
            session.update(user_id=7, role="Support Worker", full_name="Worker")
        response = self.client.post("/incident/new", data={
            "incident_date": "2026-08-02", "incident_time": "12:00",
            "location": "Garden", "incident_type": "Fall",
            "description": "No injury", "actions_taken": "",
            "injury_details": "", "injury": "on",
        })
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.path)
        rows = conn.execute("""
            SELECT details FROM activity_log WHERE activity_type = 'incident_created'
        """).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], (
            "Location: Garden\nInjury: Yes\n"
            "Description: No injury\nFollow-up required: No"
        ))

    def test_unchecked_injury_saves_zero(self):
        with self.client.session_transaction() as session:
            session.update(user_id=7, role="Support Worker", full_name="Worker")
        response = self.client.post("/incident/new", data={
            "incident_date": "2026-08-02", "incident_time": "12:00",
            "location": "Garden", "incident_type": "Fall",
            "description": "No injury reported", "actions_taken": "",
            "injury_details": "",
        })
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.path)
        row = conn.execute(
            "SELECT injuries FROM incident_reports"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 0)

    def test_future_incident_is_rejected_before_writes_and_next_request_succeeds(self):
        with self.client.session_transaction() as session:
            session.update(user_id=7, role="Support Worker", full_name="Worker")

        future = self.client.post("/incident/new", data={
            "incident_date": "2099-01-01", "incident_time": "12:00",
            "location": "Home", "incident_type": "Fall",
            "description": "Future incident", "actions_taken": "",
            "injury_details": "",
        })
        self.assertEqual(future.status_code, 400)
        self.assertIn(b"Incident date and time cannot be in the future.", future.data)
        self.assertIn(b"2099-01-01", future.data)

        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM incident_reports").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0], 0)
        conn.close()

        valid = self.client.post("/incident/new", data={
            "incident_date": "2026-08-02", "incident_time": "12:00",
            "location": "Home", "incident_type": "Fall",
            "description": "Past incident", "actions_taken": "Observed",
            "injury_details": "",
        })
        self.assertEqual(valid.status_code, 302)
        conn = sqlite3.connect(self.path)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM incident_reports").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0], 1)
        conn.close()

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
