import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class BehaviourConsultantReviewPermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "review_permissions.db")
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
                follow_up_required INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT,
                acknowledgement_type TEXT DEFAULT 'Read',
                comment TEXT,
                active INTEGER NOT NULL DEFAULT 1
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
                success INTEGER NOT NULL DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT NULL
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
            CREATE TABLE care_tasks (
                care_task_id INTEGER PRIMARY KEY,
                task_name TEXT NOT NULL
            );
            CREATE TABLE shift_care_task_entries (
                entry_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                care_task_id INTEGER NOT NULL,
                outcome TEXT,
                comment TEXT,
                completed_by_user_id INTEGER,
                completed_at TEXT DEFAULT CURRENT_TIMESTAMP
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
                comment TEXT,
                completed_by_user_id INTEGER,
                completed_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE toileting_events (
                toileting_event_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_id INTEGER NOT NULL,
                event_type TEXT,
                event_datetime TEXT,
                recorded_by_user_id INTEGER,
                location TEXT,
                location_other TEXT,
                bm_size TEXT,
                bm_consistency TEXT,
                bm_unusual_details TEXT,
                urine_volume TEXT,
                urine_unusual_details TEXT,
                behaviour_before TEXT,
                behaviour_during TEXT,
                behaviour_after TEXT,
                behaviour_comments TEXT,
                general_comments TEXT
            );
            INSERT INTO users VALUES
                (1, 'Support Worker', 'Support Worker', 1),
                (2, 'Behaviour Consultant', 'Behaviour Consultant', 1),
                (3, 'Admin User', 'Admin', 1),
                (4, 'Inactive Consultant', 'Behaviour Consultant', 0);
            INSERT INTO clients VALUES (1, 'Client One', 1);
            INSERT INTO shifts VALUES (10, 1, '2026-08-02', 'Day', 'Open');
            INSERT INTO shift_notes
                (note_id, client_id, user_id, shift_date, shift_type,
                 note_text, created_at)
            VALUES (20, 1, 1, '2026-08-02', 'Day',
                    'Shift note for review', '2026-08-02 10:00:00');
            INSERT INTO incident_reports
                (incident_id, client_id, reported_by_user_id, incident_date,
                 incident_time, location, incident_type, description)
            VALUES (30, 1, 1, '2026-08-02', '10:00', 'Home',
                    'Medical', 'Incident for review');
            INSERT INTO care_tasks VALUES (40, 'Personal care');
            INSERT INTO shift_care_task_entries
                (entry_id, shift_id, care_task_id, outcome, comment,
                 completed_by_user_id, completed_at)
            VALUES (41, 10, 40, 'Completed', 'Care comment', 1,
                    '2026-08-02 11:00:00');
            INSERT INTO housekeeping_tasks VALUES (50, 'Kitchen');
            INSERT INTO shift_housekeeping_task_entries
                (entry_id, shift_id, housekeeping_task_id, outcome, comment,
                 completed_by_user_id, completed_at)
            VALUES (51, 10, 50, 'Completed', 'Housekeeping comment', 1,
                    '2026-08-02 11:00:00');
            INSERT INTO toileting_events
                (toileting_event_id, client_id, shift_id, event_type,
                 event_datetime, recorded_by_user_id, location)
            VALUES (61, 1, 10, 'Urination', '2026-08-02T10:00:00Z',
                    1, 'Bathroom');
        """)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        app.app.config.update(TESTING=self.old_testing)
        self.temp.cleanup()

    def login(self, user_id, session_role=None):
        roles = {
            1: "Support Worker",
            2: "Behaviour Consultant",
            3: "Admin",
            4: "Behaviour Consultant",
        }
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                role=session_role or roles[user_id],
                full_name="Test User",
            )

    def rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, parameters)]
        finally:
            conn.close()

    def test_behaviour_consultant_can_enter_hub_and_review_lists(self):
        self.login(2)

        detail = self.client.get("/manager-review/shift-notes/20")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Mark as Reviewed", detail.data)
        self.assertIn(b"Add Management Note", detail.data)
        self.assertIn(b"Linked Actions", detail.data)
        self.assertIn(b"Create Action", detail.data)

        hub = self.client.get("/manager-review")
        self.assertEqual(hub.status_code, 200)
        self.assertIn(b"Open Care Review", hub.data)
        self.assertNotIn(b"Open Leave Requests", hub.data)

        for path in (
            "/manager-review/care",
            "/manager-review/housekeeping",
            "/manager-review/toileting",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Review", response.data)

        incidents = self.client.get("/incidents")
        self.assertEqual(incidents.status_code, 200)
        self.assertRegex(incidents.data, rb">\s*Review\s*</a>")

        notes = self.client.get("/shift-notes")
        self.assertEqual(notes.status_code, 200)
        self.assertIn(b"Review", notes.data)
        self.assertIn(b"/shift-note/20/acknowledge", notes.data)

    def test_support_worker_cannot_use_review_hub_or_review_controls(self):
        self.login(1)
        self.assertEqual(self.client.get("/manager-review").status_code, 403)
        for path in (
            "/manager-review/care",
            "/manager-review/housekeeping",
            "/manager-review/toileting",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

        notes = self.client.get("/shift-notes")
        self.assertEqual(notes.status_code, 200)
        self.assertNotIn(b"/shift-note/20/acknowledge", notes.data)

    def test_shift_note_review_uses_database_authority(self):
        self.login(2, session_role="Support Worker")
        response = self.client.post("/shift-note/20/acknowledge")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.rows(
                "SELECT user_id, acknowledgement_type "
                "FROM acknowledgements WHERE source_table = 'shift_notes'"
            ),
            [{"user_id": 2, "acknowledgement_type": "Review"}],
        )

        self.login(1, session_role="Admin")
        denied = self.client.post("/shift-note/20/acknowledge")
        self.assertEqual(denied.status_code, 403)

        self.login(4, session_role="Admin")
        inactive = self.client.post("/shift-note/20/acknowledge")
        self.assertEqual(inactive.status_code, 403)

    def test_behaviour_consultant_can_use_legacy_incident_review_redirect(self):
        self.login(2)
        response = self.client.get("/incident/30/review")
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"/manager-review/incidents/30", response.data)


if __name__ == "__main__":
    unittest.main()
