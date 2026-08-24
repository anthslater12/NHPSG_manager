import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class IncidentManagementEngagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "incident.db")
        self.old_db = app.DB_NAME
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
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER,
                user_id INTEGER,
                active INTEGER
            );
            CREATE TABLE incident_reports (
                incident_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                reported_by_user_id INTEGER NOT NULL,
                incident_date TEXT NOT NULL,
                incident_time TEXT NOT NULL,
                location TEXT NOT NULL,
                incident_type TEXT NOT NULL,
                severity TEXT DEFAULT 'Normal',
                description TEXT NOT NULL,
                actions_taken TEXT,
                follow_up_required INTEGER NOT NULL DEFAULT 0,
                witnesses TEXT,
                injuries INTEGER NOT NULL DEFAULT 0,
                injury_details TEXT,
                police_notified INTEGER NOT NULL DEFAULT 0,
                medical_treatment INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'Awaiting Review',
                reviewed_by_user_id INTEGER,
                reviewed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                acknowledgement_type TEXT DEFAULT 'Read',
                comment TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE management_notes (
                management_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                note_text TEXT NOT NULL,
                visibility TEXT NOT NULL DEFAULT 'management_only',
                created_by_user_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                active INTEGER NOT NULL DEFAULT 1,
                shared_at TEXT,
                shared_by_user_id INTEGER
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
            CREATE TABLE action_comments (
                comment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                comment TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
                (5, 'Inactive Manager', 'Program Manager', 0),
                (6, 'Assigned Worker', 'Support Worker', 1);
            INSERT INTO clients VALUES (1, 'Client One', 1);
            INSERT INTO shifts VALUES (10, 1, '2026-08-02', 'Day', 'Open');
            INSERT INTO shift_staff VALUES (100, 10, 1, 1);
            INSERT INTO incident_reports
                (incident_id, client_id, reported_by_user_id,
                 incident_date, incident_time, location, incident_type,
                 severity, description, actions_taken, follow_up_required)
            VALUES
                (41, 1, 1, '2026-08-02', '10:00', 'Home', 'Medical',
                 'High', 'Incident details', 'Called nurse', 1);
            INSERT INTO activity_log
                (activity_type, activity_class, user_id, client_id,
                 related_table, related_id, summary, storyline_visible)
            VALUES
                ('incident_created', 'INCIDENT', 1, 1,
                 'incident_reports', 41, 'Incident created', 1);
        """)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id):
        roles = {
            1: "Support Worker",
            2: "Program Manager",
            3: "Director",
            4: "Admin",
            5: "Program Manager",
            6: "Support Worker",
        }
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=roles[user_id])

    def rows(self, sql, params=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        result = conn.execute(sql, params).fetchall()
        conn.close()
        return result

    def test_all_management_roles_can_add_and_view_incident_notes(self):
        for user_id, label in (
            (2, "PM note"),
            (3, "Director note"),
            (4, "Admin note"),
        ):
            self.login(user_id)
            response = self.client.post(
                "/manager-review/incidents/41/management-note",
                data={"note_text": label}
            )
            self.assertEqual(response.status_code, 302)

        self.login(2)
        detail = self.client.get("/manager-review/incidents/41")
        self.assertEqual(detail.status_code, 200)
        for label in ("PM note", "Director note", "Admin note"):
            self.assertIn(label.encode(), detail.data)
        self.assertIn(b"Program Manager", detail.data)
        self.assertIn(b"Management only", detail.data)

        notes = self.rows("""
            SELECT source_table, source_id, note_text, created_by_user_id,
                   visibility, active
            FROM management_notes
            ORDER BY management_note_id
        """)
        self.assertEqual(len(notes), 3)
        self.assertTrue(all(row["source_table"] == "incident_reports" for row in notes))
        self.assertTrue(all(row["source_id"] == 41 for row in notes))
        self.assertTrue(all(row["visibility"] == "management_only" for row in notes))
        self.assertTrue(all(row["active"] == 1 for row in notes))

    def test_support_worker_and_inactive_manager_cannot_add_notes_or_actions(self):
        self.login(1)
        detail = self.client.get("/manager-review/incidents/41")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn(b"Management Notes", detail.data)
        self.assertNotIn(b"Linked Actions", detail.data)
        self.assertNotIn(b"Create Action", detail.data)
        self.assertEqual(
            self.client.post(
                "/manager-review/incidents/41/management-note",
                data={"note_text": "not allowed"}
            ).status_code,
            403
        )
        self.assertEqual(
            self.client.get("/manager-review/incidents/41/action/new").status_code,
            403
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/incidents/41/action/new",
                data={"title": "not allowed"}
            ).status_code,
            403
        )

        self.login(5)
        self.assertEqual(
            self.client.post(
                "/manager-review/incidents/41/management-note",
                data={"note_text": "stale"}
            ).status_code,
            403
        )
        self.assertEqual(
            self.client.get("/manager-review/incidents/41/action/new").status_code,
            403
        )

    def test_blank_and_missing_incident_note_requests_fail_safely(self):
        self.login(2)
        blank = self.client.post(
            "/manager-review/incidents/41/management-note",
            data={
                "note_text": "   ",
                "storyline_client_id": "1",
                "storyline_filter": "Incident",
                "storyline_page": "1",
            }
        )
        self.assertEqual(blank.status_code, 302)
        self.assertIn(b"note_error=Management", blank.data)
        self.assertIn(b"storyline_client_id=1", blank.data)
        self.assertEqual(
            self.rows("SELECT * FROM management_notes"), []
        )

        self.assertEqual(
            self.client.post(
                "/manager-review/incidents/999/management-note",
                data={"note_text": "missing"}
            ).status_code,
            404
        )
        self.assertEqual(
            self.client.get("/manager-review/incidents/999/action/new").status_code,
            404
        )

    def test_note_activity_is_management_only_and_not_storyline_event(self):
        self.login(2)
        self.client.post(
            "/manager-review/incidents/41/management-note",
            data={"note_text": "Audit note"}
        )
        activity = self.rows("""
            SELECT activity_class, activity_type, related_table,
                   related_id, storyline_visible
            FROM activity_log
            WHERE activity_type = 'management_note_added'
        """)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0]["activity_class"], "MANAGEMENT_NOTE")
        self.assertEqual(activity[0]["related_table"], "management_notes")
        self.assertEqual(activity[0]["storyline_visible"], 0)
        self.assertEqual(
            self.rows("""
                SELECT COUNT(*) AS count
                FROM activity_log
                WHERE activity_type = 'incident_created'
                  AND related_id = 41
            """)[0]["count"],
            1
        )

        storyline = self.client.get(
            "/client/1/storyline?filter=Incident&page=1"
        )
        self.assertEqual(storyline.status_code, 200)
        self.assertEqual(storyline.data.count(b"Incident created"), 1)

    def test_storyline_context_survives_management_note_creation(self):
        self.login(2)
        response = self.client.post(
            "/manager-review/incidents/41/management-note",
            data={
                "note_text": "Context note",
                "storyline_client_id": "1",
                "storyline_filter": "Incident",
                "storyline_page": "2",
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"storyline_client_id=1", response.data)
        self.assertIn(b"storyline_filter=Incident", response.data)
        self.assertIn(b"storyline_page=2", response.data)

    def test_management_can_create_linked_action_and_return_to_storyline_context(self):
        self.login(2)
        form = self.client.get(
            "/manager-review/incidents/41/action/new?"
            "storyline_client_id=1&storyline_filter=Incident&storyline_page=1"
        )
        self.assertEqual(form.status_code, 200)
        self.assertIn(b"Create Incident Action", form.data)
        self.assertIn(b"storyline_client_id", form.data)

        response = self.client.post(
            "/manager-review/incidents/41/action/new",
            data={
                "title": "Call family",
                "description": "Discuss follow-up",
                "priority": "High",
                "assigned_to_user_id": "6",
                "due_date": "2026-08-10",
                "storyline_client_id": "1",
                "storyline_filter": "Incident",
                "storyline_page": "1",
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"storyline_client_id=1", response.data)
        self.assertIn(b"storyline_filter=Incident", response.data)

        action = self.rows("""
            SELECT title, source_table, source_id, priority,
                   assigned_to_user_id, due_date, created_by_user_id
            FROM action_items
        """)[0]
        self.assertEqual(action["title"], "Call family")
        self.assertEqual(action["source_table"], "incident_reports")
        self.assertEqual(action["source_id"], 41)
        self.assertEqual(action["priority"], "High")
        self.assertEqual(action["assigned_to_user_id"], 6)
        self.assertEqual(action["due_date"], "2026-08-10")
        self.assertEqual(action["created_by_user_id"], 2)

        detail = self.client.get(response.headers["Location"])
        self.assertIn(b"Linked Actions", detail.data)
        self.assertIn(b"Call family", detail.data)
        self.assertIn(b"Assigned Worker", detail.data)
        self.assertIn(b"2026-08-10", detail.data)

        action_id = self.rows("SELECT action_id FROM action_items")[0][0]
        action_detail = self.client.get(f"/action/{action_id}")
        self.assertEqual(action_detail.status_code, 200)
        self.assertIn(b"View Source Incident", action_detail.data)
        self.assertIn(b"/manager-review/incidents/41", action_detail.data)

        self.login(1)
        worker_detail = self.client.get("/manager-review/incidents/41")
        self.assertNotIn(b"Linked Actions", worker_detail.data)
        self.assertNotIn(b"Call family", worker_detail.data)
        self.assertNotIn(b"Create Action", worker_detail.data)

        activity = self.rows("""
            SELECT activity_class, activity_type, related_table,
                   storyline_visible
            FROM activity_log
            WHERE activity_type = 'action_created'
        """)
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0]["activity_class"], "ACTION")
        self.assertEqual(activity[0]["related_table"], "action_items")
        self.assertEqual(activity[0]["storyline_visible"], 0)

    def test_existing_incident_review_remains_available_to_management(self):
        self.login(2)
        response = self.client.post(
            "/manager-review/incidents/41/review",
            data={
                "storyline_client_id": "1",
                "storyline_filter": "Incident",
                "storyline_page": "1",
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"storyline_client_id=1", response.data)
        self.assertEqual(
            self.rows("""
                SELECT COUNT(*) AS count
                FROM acknowledgements
                WHERE source_table = 'incident_reports'
                  AND source_id = 41
                  AND acknowledgement_type = 'Review'
            """)[0]["count"],
            1
        )


if __name__ == "__main__":
    unittest.main()
