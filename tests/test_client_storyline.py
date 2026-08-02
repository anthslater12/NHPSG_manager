import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import app


class ClientStorylineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "storyline.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.addCleanup(self.cleanup)
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, full_name TEXT, role TEXT, active INTEGER);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER);
            CREATE TABLE shifts (shift_id INTEGER PRIMARY KEY, client_id INTEGER, status TEXT);
            CREATE TABLE shift_staff (shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER, user_id INTEGER, active INTEGER);
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT, activity_datetime TEXT,
                activity_class TEXT, activity_type TEXT, user_id INTEGER, client_id INTEGER,
                shift_id INTEGER, related_table TEXT, related_id INTEGER, summary TEXT,
                details TEXT, success INTEGER DEFAULT 1, storyline_visible INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO users VALUES (1, 'Worker', 'Support Worker', 1), (2, 'Manager', 'Program Manager', 1), (3, 'Other', 'Support Worker', 1);
            INSERT INTO clients VALUES (1, 'Client One', 1), (2, 'Client Two', 1);
            INSERT INTO shifts VALUES (10, 1, 'Open'), (20, 2, 'Open');
            INSERT INTO shift_staff VALUES (100, 10, 1, 1), (200, 20, 3, 1), (300, 10, 3, 0);
        """)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, role="Support Worker"):
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=role, full_name="Test User")

    def add_event(self, event_type, summary, client_id=1, visible=1, success=1, when="2026-08-02 10:00:00", user_id=1):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_type, user_id, client_id, summary, success, storyline_visible)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (when, event_type, user_id, client_id, summary, success, visible))
        conn.commit()
        conn.close()

    def test_authorized_worker_sees_only_visible_events_for_client(self):
        self.login()
        self.add_event("sleep_fell_asleep", "Client fell asleep")
        self.add_event("user_login", "Login", visible=0)
        self.add_event("incident_created", "Other client", client_id=2)
        response = self.client.get("/client/1/storyline")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client fell asleep", response.data)
        self.assertNotIn(b"Login", response.data)
        self.assertNotIn(b"Other client", response.data)

    def test_worker_cannot_view_unrelated_client(self):
        self.login()
        self.assertEqual(self.client.get("/client/2/storyline").status_code, 403)

    def test_unauthenticated_and_unassigned_workers_are_rejected(self):
        self.assertEqual(self.client.get("/client/1/storyline").status_code, 302)
        self.login(3)
        self.assertEqual(self.client.get("/client/1/storyline").status_code, 403)

    def test_active_assignment_is_sufficient_before_sign_on_and_inactive_is_denied(self):
        self.login(1)
        self.assertEqual(self.client.get("/client/1/storyline").status_code, 200)
        self.login(3)
        self.assertEqual(self.client.get("/client/1/storyline").status_code, 403)

    def test_labels_filters_unknown_events_and_date_heading(self):
        self.login()
        self.add_event("sleep_woke_up", "Client woke up")
        self.add_event("care_task_completed", "Bath - Completed")
        self.add_event("unknown_visible", "Unknown summary")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Sleep", page)
        self.assertIn(b"Care", page)
        self.assertIn(b"Client activity", page)
        self.assertIn(b"Today", page)
        filtered = self.client.get("/client/1/storyline?filter=Sleep").data
        self.assertIn(b"Client woke up", filtered)
        self.assertNotIn(b"Bath - Completed", filtered)

    def test_all_labels_and_success_filtering(self):
        self.login()
        events = (
            ("food_fluid_entry_created", "Food record"),
            ("behaviour_occurrence_created", "Behaviour record"),
            ("toileting_event_created", "Toileting record"),
            ("shift_activity_created", "Activity record"),
            ("incident_created", "Incident record"),
            ("shift_note_updated", "Note record"),
            ("start_shift_completed", "Start record"),
            ("end_shift_completed", "End record"),
            ("care_task_completed", "Bath - Completed"),
            ("housekeeping_task_completed", "Housekeeping record"),
        )
        for event_type, summary in events:
            self.add_event(event_type, summary)
        self.add_event("incident_created", "Failed", success=0)
        page = self.client.get("/client/1/storyline").data
        for _, summary in events:
            self.assertIn(summary.encode(), page)
        self.assertNotIn(b"Failed", page)
        for category, included, excluded in (
            ("Food & Fluid", b"Food record", b"Behaviour record"),
            ("Behaviour", b"Behaviour record", b"Food record"),
            ("Toileting", b"Toileting record", b"Food record"),
            ("Activity", b"Activity record", b"Food record"),
            ("Incident", b"Incident record", b"Food record"),
            ("Shift Notes", b"Note record", b"Food record"),
            ("Shift", b"Start record", b"Food record"),
            ("Care", b"Bath - Completed", b"Food record"),
            ("Housekeeping", b"Housekeeping record", b"Food record"),
        ):
            filtered = self.client.get(
                "/client/1/storyline", query_string={"filter": category}
            ).data
            self.assertIn(included, filtered)
            self.assertNotIn(excluded, filtered)

    def test_invalid_filter_and_pagination_are_safe(self):
        self.login()
        for index in range(30):
            self.add_event("shift_activity_created", f"Activity {index}", when=f"2026-08-02 10:{index % 60:02d}:00")
        response = self.client.get("/client/1/storyline?filter=invalid&page=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Newer Events", response.data)
        older = self.client.get("/client/1/storyline?filter=Activity&page=2").data
        self.assertIn(b"Newer Events", older)
        self.assertNotIn(b"Client activity", older)

    def test_pagination_is_limited_and_links_preserve_filter(self):
        self.login()
        for index in range(26):
            self.add_event("shift_activity_created", f"Paged {index}", when=f"2026-08-02 09:{index:02d}:00")
        first = self.client.get("/client/1/storyline?filter=Activity&page=1").data
        self.assertIn(b"Older Events", first)
        self.assertIn(b"filter=Activity", first)
        self.assertLess(first.find(b"Paged 25"), first.find(b"Paged 1"))
        second = self.client.get("/client/1/storyline?filter=Activity&page=2").data
        self.assertIn(b"Newer Events", second)
        self.assertIn(b"Paged 0", second)

    def test_empty_and_filtered_empty_states_render(self):
        self.login()
        empty = self.client.get("/client/1/storyline").data
        self.assertIn(b"No Storyline events have been recorded", empty)
        self.add_event("sleep_fell_asleep", "Sleep event")
        filtered_empty = self.client.get("/client/1/storyline?filter=Incident").data
        self.assertIn(b"No Incident events match this filter", filtered_empty)

    def test_manager_can_view_and_storyline_view_does_not_write(self):
        self.login(2, "Program Manager")
        before = sqlite3.connect(self.path).execute("SELECT count(*) FROM activity_log").fetchone()[0]
        self.assertEqual(self.client.get("/client/2/storyline").status_code, 200)
        after = sqlite3.connect(self.path).execute("SELECT count(*) FROM activity_log").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
