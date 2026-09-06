import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class ActionCreationHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "actions.db")
        self.old_db = app.DB_NAME
        self.old_testing = app.app.config.get("TESTING")
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.addCleanup(self.cleanup)

        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT,
                shift_type TEXT,
                status TEXT
            );
            CREATE TABLE shift_notes (
                note_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                shift_date TEXT,
                shift_type TEXT,
                note_text TEXT,
                follow_up_required INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE care_tasks (
                care_task_id INTEGER PRIMARY KEY,
                task_name TEXT NOT NULL
            );
            CREATE TABLE shift_care_task_entries (
                entry_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                care_task_id INTEGER NOT NULL,
                outcome TEXT,
                comment TEXT
            );
            CREATE TABLE housekeeping_tasks (
                housekeeping_task_id INTEGER PRIMARY KEY,
                task_name TEXT NOT NULL
            );
            CREATE TABLE shift_housekeeping_task_entries (
                entry_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                housekeeping_task_id INTEGER NOT NULL,
                outcome TEXT,
                comment TEXT
            );
            CREATE TABLE toileting_events (
                toileting_event_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                event_type TEXT,
                event_datetime TEXT,
                location TEXT,
                location_other TEXT,
                general_comments TEXT
            );
            CREATE TABLE action_items (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'Open',
                priority TEXT DEFAULT 'Medium',
                source_table TEXT,
                source_id INTEGER,
                assigned_to_user_id INTEGER,
                created_by_user_id INTEGER,
                due_date TEXT,
                acknowledged_at TEXT,
                completed_at TEXT,
                closed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                shift_id INTEGER
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT DEFAULT CURRENT_TIMESTAMP,
                activity_class TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                user_id INTEGER,
                client_id INTEGER,
                shift_id INTEGER,
                related_table TEXT,
                related_id INTEGER,
                summary TEXT NOT NULL,
                details TEXT,
                success INTEGER DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT NULL
            );
            INSERT INTO users VALUES
                (1, 'Support Worker', 'Support Worker', 1),
                (2, 'Program Manager', 'Program Manager', 1),
                (3, 'Director', 'Director', 1),
                (4, 'Admin', 'Admin', 1),
                (5, 'Behaviour Consultant', 'Behaviour Consultant', 1),
                (6, 'Inactive Manager', 'Program Manager', 0),
                (7, 'Other Worker', 'Support Worker', 1);
            INSERT INTO clients VALUES (1, 'Client One', 1);
            INSERT INTO shifts VALUES (10, 1, '2026-08-02', 'Day', 'Open');
            INSERT INTO shift_notes VALUES
                (20, 1, 1, '2026-08-02', 'Day', 'Shift note', 1);
            INSERT INTO care_tasks VALUES (30, 'Personal care');
            INSERT INTO shift_care_task_entries VALUES
                (31, 10, 30, 'Completed', 'Care comment');
            INSERT INTO housekeeping_tasks VALUES (40, 'Kitchen');
            INSERT INTO shift_housekeeping_task_entries VALUES
                (41, 10, 40, 'Completed', 'Housekeeping comment');
            INSERT INTO toileting_events VALUES
                (50, 10, 'Urination', '2026-08-02T10:00:00Z',
                 'Bathroom', NULL, 'Toileting comment');
        """)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        app.app.config["TESTING"] = self.old_testing
        self.temp.cleanup()

    def login(self, user_id, session_role=None):
        roles = {
            1: "Support Worker",
            2: "Program Manager",
            3: "Director",
            4: "Admin",
            5: "Behaviour Consultant",
            6: "Program Manager",
            7: "Support Worker",
        }
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                role=session_role or roles[user_id]
            )

    def rows(self, sql, params=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        result = conn.execute(sql, params).fetchall()
        conn.close()
        return result

    def test_legacy_action_routes_use_active_database_authority(self):
        routes = (
            "/manager-review/shift-notes/20/action/new",
            "/manager-review/care/31/action/new",
            "/manager-review/toileting/50/action/new",
            "/manager-review/housekeeping/41/action/new",
        )
        for route in routes:
            for user_id in (2, 3, 4):
                self.login(user_id, session_role="Support Worker")
                self.assertEqual(
                    self.client.get(route).status_code,
                    200,
                    (route, user_id)
                )

            for user_id in (1, 5, 6):
                self.login(user_id, session_role="Admin")
                self.assertEqual(
                    self.client.get(route).status_code,
                    403,
                    (route, user_id)
                )

    def test_invalid_assignees_are_rejected_without_creating_actions(self):
        self.login(2)
        routes = (
            "/manager-review/shift-notes/20/action/new",
            "/manager-review/care/31/action/new",
            "/manager-review/toileting/50/action/new",
            "/manager-review/housekeeping/41/action/new",
        )
        for route in routes:
            for raw_assignee in ("not-an-int", "999", "6"):
                response = self.client.post(route, data={
                    "title": "Invalid assignee",
                    "description": "Description",
                    "priority": "High",
                    "assigned_to_user_id": raw_assignee,
                })
                self.assertEqual(response.status_code, 200, (route, raw_assignee))
                self.assertIn(b"Invalid assigned user.", response.data)
                self.assertEqual(
                    self.rows("SELECT COUNT(*) AS count FROM action_items")[0]["count"],
                    0
                )

    def test_valid_actions_preserve_source_shift_assignment_and_creator(self):
        self.login(2, session_role="Admin")
        cases = (
            (
                "/manager-review/shift-notes/20/action/new",
                "shift_notes",
                20,
                None,
            ),
            (
                "/manager-review/care/31/action/new",
                "shift_care_task_entries",
                31,
                10,
            ),
            (
                "/manager-review/toileting/50/action/new",
                "toileting_events",
                50,
                10,
            ),
            (
                "/manager-review/housekeeping/41/action/new",
                "shift_housekeeping_task_entries",
                41,
                10,
            ),
        )
        for route, source_table, source_id, shift_id in cases:
            response = self.client.post(route, data={
                "title": f"Follow-up {source_table}",
                "description": "Description",
                "priority": "High",
                "assigned_to_user_id": "1",
            })
            self.assertEqual(response.status_code, 302, route)
            action = self.rows("""
                SELECT title, priority, source_table, source_id, shift_id,
                       assigned_to_user_id, created_by_user_id
                FROM action_items
                WHERE source_table = ? AND source_id = ?
            """, (source_table, source_id))[0]
            self.assertEqual(action["priority"], "High")
            self.assertEqual(action["source_table"], source_table)
            self.assertEqual(action["source_id"], source_id)
            self.assertEqual(action["shift_id"], shift_id)
            self.assertEqual(action["assigned_to_user_id"], 1)
            self.assertEqual(action["created_by_user_id"], 2)


if __name__ == "__main__":
    unittest.main()
