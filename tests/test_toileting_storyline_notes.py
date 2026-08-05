import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class ToiletingStorylineNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "storyline.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, full_name TEXT,
                role TEXT, active INTEGER
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY, client_id INTEGER, status TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER,
                user_id INTEGER, active INTEGER
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT, activity_type TEXT, user_id INTEGER,
                client_id INTEGER, shift_id INTEGER, related_table TEXT,
                related_id INTEGER, summary TEXT, details TEXT,
                success INTEGER DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT
            );
        """)
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, 1)",
            [(1, "Worker", "Support Worker"), (2, "Manager", "Program Manager")],
        )
        conn.execute("INSERT INTO clients VALUES (1, 'Client One', 1)")
        conn.execute("INSERT INTO shifts VALUES (10, 1, 'Open')")
        conn.execute("INSERT INTO shift_staff VALUES (1, 10, 1, 1)")
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, role="Support Worker"):
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=role, full_name="Test User")

    def add_log(self, details, client_id=1, success=1, visible=1):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_type, user_id, client_id, shift_id,
             related_table, related_id, summary, details, success,
             storyline_visible, event_datetime)
            VALUES ('2026-08-04 10:00:00', 'toileting_event_created', 1, ?, 10,
                    'toileting_events', 7,
                    'Toileting event recorded: BM', ?, ?, ?,
                    '2026-08-04 18:00:00')
        """, (client_id, details, success, visible))
        conn.commit()
        conn.close()

    def test_formatter_preserves_existing_lines_and_multiline_notes(self):
        details = app.format_toileting_storyline_details(
            "Bathroom", "Medium", "Soft", "", "Agitated", "", "",
            "  Client reported stomach discomfort.\nReturned to the lounge afterward.  "
        )
        self.assertEqual(details[:4], [
            "Location: Bathroom",
            "Size: Medium",
            "Consistency: Soft",
            "Behaviour: During: Agitated",
        ])
        self.assertEqual(
            details[-1],
            "Additional notes:\nClient reported stomach discomfort.\n"
            "Returned to the lounge afterward."
        )

    def test_blank_and_whitespace_notes_are_omitted(self):
        for notes in (None, "", "   \n  "):
            details = app.format_toileting_storyline_details(
                "Bathroom", "Small", "Firm", "", "", "", "", notes
            )
            self.assertFalse(any("Additional notes:" in line for line in details))

    def test_storyline_shows_notes_to_worker_and_manager_with_escaping(self):
        self.add_log(
            "Location: Bathroom\nSize: Medium\nAdditional notes:\n"
            "Line one\n<b>not markup</b>"
        )
        for user_id, role in ((1, "Support Worker"), (2, "Program Manager")):
            self.login(user_id, role)
            page = self.client.get("/client/1/storyline")
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Additional notes:", page.data)
            self.assertIn(b"Line one", page.data)
            self.assertIn(b"&lt;b&gt;not markup&lt;/b&gt;", page.data)
            self.assertNotIn(b"<b>not markup</b>", page.data)

    def test_visibility_and_legacy_rows_remain_safe(self):
        self.add_log("Location: Bathroom")
        self.add_log("Legacy operational note", visible=1)
        self.add_log("Hidden note", visible=0)
        self.add_log("Other client", client_id=99)
        self.add_log("Failed note", success=0)
        self.login()
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Location: Bathroom", page)
        self.assertIn(b"Legacy operational note", page)
        self.assertNotIn(b"Hidden note", page)
        self.assertNotIn(b"Other client", page)
        self.assertNotIn(b"Failed note", page)


if __name__ == "__main__":
    unittest.main()
