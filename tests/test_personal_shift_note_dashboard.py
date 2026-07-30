import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from flask import session

import app


class PersonalShiftNoteDashboardTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "dashboard.db"
        )
        self.original_database_name = app.DB_NAME
        self.addCleanup(self.restore_database_name)
        app.DB_NAME = self.database_path
        self.create_database()

    def restore_database_name(self):
        app.DB_NAME = self.original_database_name

    def create_database(self):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.executescript("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL
                );

                CREATE TABLE clients (
                    client_id INTEGER PRIMARY KEY,
                    client_name TEXT NOT NULL
                );

                CREATE TABLE shift_notes (
                    note_id INTEGER PRIMARY KEY,
                    client_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    shift_date TEXT NOT NULL,
                    shift_type TEXT NOT NULL
                );

                CREATE TABLE acknowledgements (
                    acknowledgement_id INTEGER PRIMARY KEY,
                    source_table TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    active INTEGER NOT NULL
                );

                CREATE TABLE action_items (
                    action_id INTEGER PRIMARY KEY,
                    title TEXT,
                    due_date TEXT,
                    priority TEXT,
                    status TEXT,
                    assigned_to_user_id INTEGER,
                    created_at TEXT
                );

                CREATE TABLE incident_reports (
                    incident_id INTEGER PRIMARY KEY,
                    incident_type TEXT,
                    incident_date TEXT,
                    incident_time TEXT,
                    client_id INTEGER
                );

                CREATE TABLE activity_log (
                    activity_id INTEGER PRIMARY KEY,
                    activity_datetime TEXT,
                    activity_type TEXT,
                    summary TEXT,
                    user_id INTEGER
                );

                INSERT INTO users (user_id, full_name)
                VALUES
                    (1, 'Admin One'),
                    (2, 'Manager Two'),
                    (3, 'Worker');

                INSERT INTO clients (client_id, client_name)
                VALUES (1, 'Client One');
            """)
            conn.commit()
        finally:
            conn.close()

    def execute(self, sql, parameters=()):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute(sql, parameters)
            conn.commit()
        finally:
            conn.close()

    def add_note(self, note_id, shift_date=None):
        if shift_date is None:
            shift_date = f"2026-08-{note_id:02d}"
        self.execute("""
            INSERT INTO shift_notes
                (note_id, client_id, user_id, shift_date, shift_type)
            VALUES (?, 1, 3, ?, 'Day')
        """, (note_id, shift_date))

    def add_review(self, note_id, user_id, active=1):
        self.execute("""
            INSERT INTO acknowledgements
                (source_table, source_id, user_id, active)
            VALUES ('shift_notes', ?, ?, ?)
        """, (note_id, user_id, active))

    def dashboard_results(self, user_id):
        stats = app.get_dashboard_stats(user_id)
        inbox = app.get_management_inbox(user_id)
        return stats["notes_to_review"], [
            row["note_id"]
            for row in inbox["notes_to_review_list"]
        ]

    def test_note_with_no_reviews_is_pending(self):
        self.add_note(1)

        self.assertEqual(self.dashboard_results(1), (1, [1]))

    def test_review_by_another_manager_does_not_remove_note(self):
        self.add_note(1)
        self.add_review(1, user_id=2)

        self.assertEqual(self.dashboard_results(1), (1, [1]))

    def test_current_users_active_review_removes_note(self):
        self.add_note(1)
        self.add_review(1, user_id=1)

        self.assertEqual(self.dashboard_results(1), (0, []))

    def test_current_users_inactive_review_does_not_remove_note(self):
        self.add_note(1)
        self.add_review(1, user_id=1, active=0)

        self.assertEqual(self.dashboard_results(1), (1, [1]))

    def test_count_can_exceed_preview_limit(self):
        for note_id in range(1, 8):
            self.add_note(note_id)

        count, preview_ids = self.dashboard_results(1)

        self.assertEqual(count, 7)
        self.assertEqual(preview_ids, [7, 6, 5, 4, 3])

    def test_management_users_receive_different_results(self):
        self.add_note(1)
        self.add_note(2)
        self.add_note(3)
        self.add_review(1, user_id=1)
        self.add_review(2, user_id=1)
        self.add_review(1, user_id=2)

        self.assertEqual(self.dashboard_results(1), (1, [3]))
        self.assertEqual(self.dashboard_results(2), (2, [3, 2]))

    def test_dashboard_passes_current_user_id_to_both_helpers(self):
        connection = mock.Mock()
        current_user = {
            "user_id": 2,
            "role": "Program Manager"
        }
        dashboard_stats = {
            "outstanding_action_count": 0,
            "outstanding_actions": [],
            "notes_to_review": 0,
            "open_incidents": 0,
            "recent_activity": 0
        }
        management_inbox = {
            "high_priority_actions": [],
            "notes_to_review_list": [],
            "recent_incidents": [],
            "recent_activity_list": []
        }

        with app.app.test_request_context("/dashboard"):
            session["user_id"] = 2
            with (
                mock.patch.object(app, "get_db", return_value=connection),
                mock.patch.object(
                    app,
                    "get_active_authenticated_user",
                    return_value=current_user
                ),
                mock.patch.object(
                    app,
                    "_load_management_staff_notice_dashboard",
                    return_value={"dashboard": [], "outstanding_count": 0}
                ),
                mock.patch.object(
                    app,
                    "get_dashboard_stats",
                    return_value=dashboard_stats
                ) as stats,
                mock.patch.object(
                    app,
                    "get_management_inbox",
                    return_value=management_inbox
                ) as inbox,
                mock.patch.object(
                    app,
                    "get_active_shift_staff",
                    return_value=[]
                ),
                mock.patch.object(app, "get_manager_alerts", return_value=[]),
                mock.patch.object(app, "render_template", return_value="ok")
            ):
                self.assertEqual(app.dashboard(), "ok")

        stats.assert_called_once_with(2)
        inbox.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
