import inspect
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app
import add_behaviour_occurrences_table as behaviour_migration


class ClientStorylineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "storyline.db")
        self.old_db = app.DB_NAME
        self.old_testing = app.app.config.get("TESTING")
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
                details TEXT, success INTEGER DEFAULT 1, storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT NULL
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
            CREATE TABLE toileting_events (
                toileting_event_id INTEGER PRIMARY KEY, client_id INTEGER,
                shift_id INTEGER, event_type TEXT, event_datetime TEXT,
                recorded_by_user_id INTEGER, location TEXT, location_other TEXT,
                bm_size TEXT, bm_consistency TEXT,
                behaviour_before TEXT, behaviour_during TEXT,
                behaviour_after TEXT, behaviour_comments TEXT
            );
            CREATE TABLE food_fluid_entries (
                food_fluid_entry_id INTEGER PRIMARY KEY, client_id INTEGER,
                shift_id INTEGER
            );
            CREATE TABLE shift_activities (
                shift_activity_id INTEGER PRIMARY KEY, shift_id INTEGER
            );
            CREATE TABLE shift_notes (
                note_id INTEGER PRIMARY KEY, client_id INTEGER
            );
            CREATE TABLE shift_care_task_entries (
                entry_id INTEGER PRIMARY KEY, shift_id INTEGER
            );
            CREATE TABLE shift_housekeeping_task_entries (
                entry_id INTEGER PRIMARY KEY, shift_id INTEGER
            );
            CREATE TABLE incident_reports (
                incident_id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL,
                reported_by_user_id INTEGER NOT NULL,
                incident_date TEXT NOT NULL, incident_time TEXT NOT NULL,
                location TEXT NOT NULL, incident_type TEXT NOT NULL,
                severity TEXT DEFAULT 'Normal', description TEXT NOT NULL,
                actions_taken TEXT, follow_up_required INTEGER NOT NULL DEFAULT 0,
                witnesses TEXT, injuries INTEGER NOT NULL DEFAULT 0,
                injury_details TEXT, police_notified INTEGER NOT NULL DEFAULT 0,
                medical_treatment INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'Awaiting Review',
                reviewed_by_user_id INTEGER, reviewed_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            INSERT INTO users VALUES
                (1, 'Worker', 'Support Worker', 1),
                (2, 'Manager', 'Program Manager', 1),
                (3, 'Other', 'Support Worker', 1),
                (4, 'Second Manager', 'Program Manager', 1),
                (5, 'Admin', 'Admin', 1),
                (6, 'Director', 'Director', 1),
                (7, 'Inactive Worker', 'Support Worker', 0),
                (8, 'Inactive Manager', 'Program Manager', 0),
                (9, 'Consultant', 'Behaviour Consultant', 1),
                (10, 'Inactive Consultant', 'Behaviour Consultant', 0);
            INSERT INTO clients VALUES (1, 'Client One', 1), (2, 'Client Two', 1);
            INSERT INTO shifts VALUES (10, 1, 'Open'), (20, 2, 'Open');
            INSERT INTO shift_staff VALUES (100, 10, 1, 1), (200, 20, 3, 1), (300, 10, 3, 0);
        """)
        conn.commit()
        behaviour_migration.migrate(conn)
        conn.close()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        app.app.config.update(TESTING=self.old_testing)
        self.temp.cleanup()

    def login(self, user_id=1, role="Support Worker"):
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=role, full_name="Test User")

    def add_event(self, event_type, summary, client_id=1, visible=1, success=1, when="2026-08-02 10:00:00", user_id=1, details=None, event_datetime=None, related_table=None, related_id=None):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_type, user_id, client_id, summary, details,
             success, storyline_visible, event_datetime, related_table, related_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (when, event_type, user_id, client_id, summary, details, success,
               visible, event_datetime, related_table, related_id))
        conn.commit()
        conn.close()

    def add_incident(self, incident_id, client_id=1):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO incident_reports
            (incident_id, client_id, reported_by_user_id, incident_date,
             incident_time, location, incident_type, description)
            VALUES (?, ?, 1, '2026-08-02', '10:00', 'Home', 'Medical', 'Details')
        """, (incident_id, client_id))
        conn.commit()
        conn.close()

    def add_behaviour_occurrence(self, occurrence_id, client_id=1, status="Recorded"):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO behaviour_occurrences
            (behaviour_occurrence_id, client_id, occurred_at_utc,
             aggression_towards_others, notes, recorded_by_user_id,
             recorded_at_utc, submission_token, status,
             voided_by_user_id, voided_at_utc, void_reason)
            VALUES (?, ?, '2026-08-02T17:00:00Z', 1, 'Behaviour notes',
                    1, '2026-08-02T17:01:00Z', ?, ?,
                    CASE WHEN ? = 'Voided' THEN 2 ELSE NULL END,
                    CASE WHEN ? = 'Voided' THEN '2026-08-02T17:02:00Z' ELSE NULL END,
                    CASE WHEN ? = 'Voided' THEN 'Test void' ELSE NULL END)
        """, (occurrence_id, client_id, f"behaviour-{occurrence_id}", status,
               status, status, status))
        conn.commit()
        conn.close()

    def event_utc(self, day, hour, minute):
        local = datetime(
            day.year,
            day.month,
            day.day,
            hour,
            minute,
            tzinfo=app.VANCOUVER_TIMEZONE
        )
        return local.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

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

    def test_management_toileting_storyline_control_and_per_user_status(self):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO toileting_events
            (toileting_event_id, client_id, shift_id, event_type,
             event_datetime, recorded_by_user_id, location)
            VALUES (7, 1, 10, 'BM', '2026-08-02T10:00', 1, 'Bathroom')
        """)
        conn.commit()
        conn.close()
        self.add_event(
            "toileting_event_created", "Toileting record",
            related_table="toileting_events", related_id=7
        )
        self.login(2, "Program Manager")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"View details", page)
        self.assertIn(b"Review required", page)
        self.assertIn(b'id="storyline-event-', page)
        self.assertIn(b'class="storyline-detail-link"', page)
        self.assertIn(b"client-storyline-detail-position", page)
        self.assertIn(
            b"/manager-review/toileting/7?storyline_client_id=1",
            page
        )

        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO acknowledgements
            (source_table, source_id, user_id, acknowledgement_type, active)
            VALUES ('toileting_events', 7, 2, 'Review', 1)
        """)
        conn.commit()
        conn.close()
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"You have reviewed this", page)
        self.assertNotIn(b"Review required", page)

        self.login(3, "Support Worker")
        page = self.client.get("/client/1/storyline").data
        self.assertNotIn(b"View details", page)
        self.assertNotIn(b"You have reviewed this", page)

    def test_malformed_toileting_storyline_metadata_stays_informational(self):
        self.login(2, "Program Manager")
        self.add_event(
            "toileting_event_created", "Missing source",
            related_table="toileting_events", related_id=None
        )
        self.add_event(
            "toileting_event_created", "Wrong source",
            related_table="other_table", related_id=7
        )
        page = self.client.get("/client/1/storyline").data
        self.assertNotIn(b"View details", page)
        self.assertIn(b"Missing source", page)
        self.assertIn(b"Wrong source", page)

    def test_management_controls_map_supported_modules_to_exact_sources(self):
        conn = sqlite3.connect(self.path)
        conn.executemany(
            "INSERT INTO food_fluid_entries VALUES (?, ?, ?)",
            [(11, 1, 10), (111, 2, 20)]
        )
        conn.execute("INSERT INTO shift_activities VALUES (12, 10)")
        conn.execute("INSERT INTO shift_notes VALUES (13, 1)")
        conn.execute("INSERT INTO shift_care_task_entries VALUES (14, 10)")
        conn.execute("INSERT INTO shift_care_task_entries VALUES (16, 10)")
        conn.execute("INSERT INTO shift_care_task_entries VALUES (17, 10)")
        conn.execute(
            "INSERT INTO shift_housekeeping_task_entries VALUES (15, 10)"
        )
        conn.execute(
            "INSERT INTO shift_housekeeping_task_entries VALUES (18, 10)"
        )
        conn.execute(
            "INSERT INTO shift_housekeeping_task_entries VALUES (19, 10)"
        )
        conn.commit()
        conn.close()

        mappings = (
            ("food_fluid_entry_created", "food_fluid_entries", 11,
             "/manager-review/food-fluid/11"),
            ("shift_activity_created", "shift_activities", 12,
             "/manager-review/activities/12"),
            ("shift_note_updated", "shift_notes", 13,
             "/manager-review/shift-notes/13"),
            ("care_task_updated", "shift_care_task_entries", 14,
             "/manager-review/care/14"),
            ("care_task_attempted", "shift_care_task_entries", 16,
             "/manager-review/care/16"),
            ("care_task_not_completed", "shift_care_task_entries", 17,
             "/manager-review/care/17"),
            ("housekeeping_task_updated", "shift_housekeeping_task_entries", 15,
             "/manager-review/housekeeping/15"),
            ("housekeeping_task_attempted", "shift_housekeeping_task_entries", 18,
             "/manager-review/housekeeping/18"),
            ("housekeeping_task_not_completed", "shift_housekeeping_task_entries", 19,
             "/manager-review/housekeeping/19"),
        )
        for event_type, table_name, source_id, _ in mappings:
            self.add_event(
                event_type,
                f"{event_type} record",
                related_table=table_name,
                related_id=source_id
            )

        self.add_event(
            "food_fluid_entry_created", "Wrong client source",
            related_table="food_fluid_entries", related_id=111
        )
        self.login(2, "Program Manager")
        page = self.client.get("/client/1/storyline").data

        for _, _, _, detail_path in mappings:
            self.assertIn(detail_path.encode(), page)
        self.assertIn(b"Wrong client source", page)
        self.assertEqual(page.count(b"View details"), len(mappings))

        self.login(1, "Support Worker")
        worker_page = self.client.get("/client/1/storyline").data
        self.assertNotIn(b"View details", worker_page)

    def test_attempted_and_not_completed_controls_validate_source_and_review_state(self):
        conn = sqlite3.connect(self.path)
        conn.executemany(
            "INSERT INTO shift_care_task_entries VALUES (?, ?)",
            [(31, 10), (32, 10), (33, 20)],
        )
        conn.executemany(
            "INSERT INTO shift_housekeeping_task_entries VALUES (?, ?)",
            [(41, 10), (42, 10), (43, 20)],
        )
        conn.commit()
        conn.close()

        events = (
            ("care_task_attempted", "shift_care_task_entries", 31,
             "/manager-review/care/31"),
            ("care_task_not_completed", "shift_care_task_entries", 32,
             "/manager-review/care/32"),
            ("housekeeping_task_attempted", "shift_housekeeping_task_entries", 41,
             "/manager-review/housekeeping/41"),
            ("housekeeping_task_not_completed", "shift_housekeeping_task_entries", 42,
             "/manager-review/housekeeping/42"),
        )
        for event_type, table_name, source_id, _ in events:
            self.add_event(
                event_type,
                f"{event_type} record",
                related_table=table_name,
                related_id=source_id,
            )

        self.add_event(
            "care_task_attempted", "Wrong client care source",
            related_table="shift_care_task_entries", related_id=33,
        )
        self.add_event(
            "housekeeping_task_not_completed", "Wrong client housekeeping source",
            related_table="shift_housekeeping_task_entries", related_id=43,
        )
        self.add_event(
            "care_task_attempted", "Wrong related table",
            related_table="care_tasks", related_id=31,
        )

        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO acknowledgements
            (source_table, source_id, user_id, acknowledgement_type, active)
            VALUES ('shift_care_task_entries', 31, 2, 'Review', 1)
        """)
        conn.execute("""
            INSERT INTO acknowledgements
            (source_table, source_id, user_id, acknowledgement_type, active)
            VALUES ('shift_care_task_entries', 32, 2, 'Read', 1)
        """)
        conn.commit()
        conn.close()

        self.login(2, "Program Manager")
        page = self.client.get("/client/1/storyline").data
        self.assertEqual(page.count(b"View details"), 4)
        self.assertEqual(page.count(b"You have reviewed this"), 1)
        self.assertEqual(page.count(b"Review required"), 3)
        for _, _, _, detail_path in events:
            self.assertIn(detail_path.encode(), page)
        self.assertIn(b"Wrong client care source", page)
        self.assertIn(b"Wrong client housekeeping source", page)
        self.assertIn(b"Wrong related table", page)

        self.login(1, "Support Worker")
        worker_page = self.client.get("/client/1/storyline").data
        self.assertNotIn(b"View details", worker_page)
        self.assertNotIn(b"You have reviewed this", worker_page)

    def test_legacy_management_review_queries_require_review_type(self):
        query_functions = (
            app.shift_notes,
            app.shift_note_review_detail,
            app.toileting_review_list,
            app.toileting_review_detail,
            app.care_review_list,
            app.care_review_detail,
            app.housekeeping_review_list,
            app.housekeeping_review_detail,
        )
        for function in query_functions:
            source = inspect.getsource(function)
            self.assertIn(
                "acknowledgement_type = 'Review'",
                source,
                function.__name__,
            )

    def test_care_and_housekeeping_review_status_uses_operational_source(self):
        conn = sqlite3.connect(self.path)
        conn.execute("INSERT INTO shift_care_task_entries VALUES (21, 10)")
        conn.execute(
            "INSERT INTO shift_housekeeping_task_entries VALUES (22, 10)"
        )
        conn.commit()
        conn.close()

        self.add_event(
            "care_task_completed", "Care operational event",
            related_table="shift_care_task_entries", related_id=21
        )
        self.add_event(
            "housekeeping_task_completed", "Housekeeping operational event",
            related_table="shift_housekeeping_task_entries", related_id=22
        )
        self.add_event(
            "care_task_updated", "Administrative care event",
            related_table="care_tasks", related_id=21
        )
        self.add_event(
            "housekeeping_task_updated", "Administrative housekeeping event",
            related_table="housekeeping_tasks", related_id=22
        )

        self.login(2, "Program Manager")
        before = self.client.get("/client/1/storyline").data
        self.assertEqual(before.count(b"Review required"), 2)
        self.assertNotIn(b"Administrative care event" + b"View details", before)
        self.assertNotIn(
            b"Administrative housekeeping eventView details", before
        )

        self.login(4, "Program Manager")
        self.assertEqual(
            self.client.post("/manager-review/care/21/review").status_code,
            302
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/housekeeping/22/review"
            ).status_code,
            302
        )

        self.login(2, "Program Manager")
        other_manager = self.client.get("/client/1/storyline").data
        self.assertEqual(other_manager.count(b"Review required"), 2)
        self.assertNotIn(b"You have reviewed this", other_manager)

        self.assertEqual(
            self.client.post("/manager-review/care/21/review").status_code,
            302
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/housekeeping/22/review"
            ).status_code,
            302
        )
        reviewed = self.client.get("/client/1/storyline").data
        self.assertEqual(reviewed.count(b"You have reviewed this"), 2)
        self.assertNotIn(b"Review required", reviewed)

        conn = sqlite3.connect(self.path)
        conn.execute("""
            UPDATE acknowledgements
            SET active = 0
            WHERE user_id = 2 AND acknowledgement_type = 'Review'
        """)
        conn.commit()
        conn.close()
        invalidated = self.client.get("/client/1/storyline").data
        self.assertEqual(invalidated.count(b"Review required"), 2)
        self.assertNotIn(b"You have reviewed this", invalidated)

        self.login(1, "Support Worker")
        worker_page = self.client.get("/client/1/storyline").data
        self.assertNotIn(b"View details", worker_page)

        self.login(9, "Behaviour Consultant")
        self.assertEqual(
            self.client.post("/manager-review/care/21/review").status_code,
            302
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/housekeeping/22/review"
            ).status_code,
            302
        )

        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO toileting_events
            (toileting_event_id, client_id, shift_id, event_type,
             event_datetime, recorded_by_user_id, location)
            VALUES (23, 1, 10, 'BM', '2026-08-02T10:00', 1, 'Bathroom')
        """)
        conn.commit()
        conn.close()
        self.assertEqual(
            self.client.post("/manager-review/toileting/23/review").status_code,
            302
        )

    def test_supported_detail_review_controls_forward_storyline_context(self):
        templates = (
            "food_fluid_review_detail.html",
            "activity_review_detail.html",
            "shift_note_review_detail.html",
            "care_review_detail.html",
            "housekeeping_review_detail.html",
            "toileting_review_detail.html",
        )
        template_root = Path(app.__file__).parent / "templates"
        for template_name in templates:
            source = (template_root / template_name).read_text(
                encoding="utf-8"
            )
            for field_name in (
                "storyline_client_id",
                "storyline_filter",
                "storyline_page",
            ):
                self.assertIn(field_name, source, template_name)

    def test_incident_detail_review_is_post_only_and_preserves_storyline_context(self):
        self.add_incident(31)
        self.add_event(
            "incident_created", "Incident record",
            related_table="incident_reports", related_id=31
        )
        self.login(2, "Program Manager")
        incident_list = self.client.get("/incidents")
        self.assertIn(b"/manager-review/incidents/31", incident_list.data)
        self.assertNotIn(b"/incident/31/review", incident_list.data)

        detail = self.client.get(
            "/manager-review/incidents/31",
            query_string={
                "storyline_client_id": 1,
                "storyline_filter": "Incident",
                "storyline_page": 2,
            }
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Back to Client Storyline", detail.data)
        self.assertIn(b"Review required", detail.data)
        self.assertEqual(self._incident_review_count(31), 0)

        old_get = self.client.get("/incident/31/review")
        self.assertEqual(old_get.status_code, 302)
        self.assertIn(b"/manager-review/incidents/31", old_get.data)
        self.assertEqual(self._incident_review_count(31), 0)

        reviewed = self.client.post(
            "/manager-review/incidents/31/review",
            data={
                "storyline_client_id": "1",
                "storyline_filter": "Incident",
                "storyline_page": "2",
            }
        )
        self.assertEqual(reviewed.status_code, 302)
        self.assertIn(b"storyline_client_id=1", reviewed.data)
        self.assertIn(b"storyline_filter=Incident", reviewed.data)
        self.assertIn(b"storyline_page=2", reviewed.data)
        self.assertEqual(self._incident_review_count(31), 1)
        conn = sqlite3.connect(self.path)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM activity_log "
                "WHERE activity_type = 'incident_created'"
            ).fetchone()[0],
            1
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM activity_log "
                "WHERE activity_type = 'record_acknowledged' "
                "AND storyline_visible = 1"
            ).fetchone()[0],
            0
        )
        conn.close()

        reviewed_detail = self.client.get(reviewed.headers["Location"])
        self.assertIn(b"Back to Client Storyline", reviewed_detail.data)
        self.assertIn(b"You have reviewed this incident", reviewed_detail.data)
        storyline_after_review = self.client.get(
            "/client/1/storyline?filter=Incident&page=2"
        )
        self.assertIn(b"You have reviewed this", storyline_after_review.data)
        self.assertIn(b"client-storyline-detail-position", storyline_after_review.data)

        self.client.post("/manager-review/incidents/31/review")
        self.assertEqual(self._incident_review_count(31), 1)

        self.login(4, "Program Manager")
        other_manager = self.client.get("/manager-review/incidents/31")
        self.assertIn(b"Review required", other_manager.data)
        self.assertNotIn(b"You have reviewed this incident", other_manager.data)

        self.login(2, "Program Manager")
        conn = sqlite3.connect(self.path)
        conn.execute("""
            UPDATE acknowledgements
            SET active = 0
            WHERE source_table = 'incident_reports'
              AND source_id = 31 AND user_id = 2
        """)
        conn.commit()
        conn.close()
        invalidated = self.client.get("/manager-review/incidents/31")
        self.assertIn(b"Review required", invalidated.data)

    def test_all_management_roles_can_open_incident_detail(self):
        self.add_incident(32)
        for user_id, role in (
            (5, "Admin"),
            (6, "Director"),
            (2, "Program Manager"),
        ):
            self.login(user_id, role)
            self.assertEqual(
                self.client.get("/manager-review/incidents/32").status_code,
                200
            )

    def _incident_review_count(self, incident_id):
        conn = sqlite3.connect(self.path)
        count = conn.execute("""
            SELECT COUNT(*) FROM acknowledgements
            WHERE source_table = 'incident_reports'
              AND source_id = ?
              AND acknowledgement_type = 'Review'
              AND active = 1
        """, (incident_id,)).fetchone()[0]
        conn.close()
        return count

    def test_incident_storyline_controls_validate_source_and_role(self):
        self.add_incident(41, client_id=1)
        self.add_incident(42, client_id=2)
        self.add_event(
            "incident_created", "Valid incident",
            related_table="incident_reports", related_id=41
        )
        self.add_event(
            "incident_created", "Wrong table incident",
            related_table="other_table", related_id=41
        )
        self.add_event(
            "incident_created", "Missing id incident",
            related_table="incident_reports", related_id=None
        )
        self.add_event(
            "incident_created", "Mismatched client incident",
            related_table="incident_reports", related_id=42
        )
        self.add_event(
            "incident_created", "Missing source incident",
            related_table="incident_reports", related_id=999
        )

        self.login(2, "Program Manager")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Valid incident", page)
        self.assertIn(b"/manager-review/incidents/41", page)
        self.assertIn(b"Review required", page)
        for summary in (
            b"Wrong table incident", b"Missing id incident",
            b"Mismatched client incident", b"Missing source incident"
        ):
            start = page.find(summary)
            self.assertGreaterEqual(start, 0)
            self.assertNotIn(b"View details", page[start:start + 700])

        self.login(1, "Support Worker")
        worker_detail = self.client.get("/manager-review/incidents/41")
        self.assertEqual(worker_detail.status_code, 200)
        self.assertIn(b"Details", worker_detail.data)
        self.assertNotIn(b"Review required", worker_detail.data)
        self.assertNotIn(b"You have reviewed this", worker_detail.data)
        self.assertNotIn(b"Mark as Reviewed", worker_detail.data)
        self.assertNotIn(b"review_incident_post", worker_detail.data)

        worker_list = self.client.get("/incidents")
        self.assertEqual(worker_list.status_code, 200)
        self.assertIn(b"View details", worker_list.data)
        self.assertNotIn(b"Add Review", worker_list.data)
        self.assertNotIn(b">Review</a>", worker_list.data)

        self.assertEqual(
            self.client.post("/manager-review/incidents/41/review").status_code,
            403
        )

        self.login(2, "Program Manager")
        manager_list = self.client.get("/incidents")
        self.assertIn(b"View details", manager_list.data)
        self.assertGreater(
            manager_list.data.count(b"Review"),
            worker_list.data.count(b"Review"),
        )

    def test_support_worker_incident_storyline_detail_is_read_only(self):
        self.add_incident(44)
        self.add_event(
            "incident_created", "Support-visible incident",
            related_table="incident_reports", related_id=44
        )
        self.login(1, "Support Worker")
        worker_page = self.client.get("/client/1/storyline")
        self.assertEqual(worker_page.status_code, 200)
        self.assertIn(b"/manager-review/incidents/44", worker_page.data)
        self.assertIn(b"View details", worker_page.data)
        self.assertNotIn(b"Review required", worker_page.data)
        self.assertNotIn(b"You have reviewed this", worker_page.data)

        detail = self.client.get(
            "/manager-review/incidents/44?storyline_client_id=1&"
            "storyline_filter=Incident&storyline_page=1"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Back to Client Storyline", detail.data)
        self.assertNotIn(b"Mark as Reviewed", detail.data)
        self.assertEqual(self._incident_review_count(44), 0)

    def test_inactive_user_cannot_open_or_review_incident(self):
        self.add_incident(45)
        for user_id, role in (
            (7, "Support Worker"),
            (8, "Program Manager"),
        ):
            self.login(user_id, role)
            self.assertEqual(
                self.client.get("/manager-review/incidents/45").status_code,
                403
            )
            self.assertEqual(
                self.client.post("/manager-review/incidents/45/review").status_code,
                403
            )
            self.assertEqual(self.client.get("/incidents").status_code, 403)

    def test_missing_incident_detail_returns_404(self):
        self.login(2, "Program Manager")
        self.assertEqual(
            self.client.get("/manager-review/incidents/999").status_code,
            404
        )

    def test_legacy_incident_review_get_remains_non_mutating(self):
        self.add_incident(46)
        self.login(2, "Program Manager")
        response = self.client.get("/incident/46/review")
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"/manager-review/incidents/46", response.data)
        self.assertEqual(self._incident_review_count(46), 0)

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
        today = datetime.now().date()
        self.add_event("sleep_woke_up", "Client woke up", when=f"{today} 10:00:00")
        self.add_event("care_task_completed", "Bath - Completed", when=f"{today} 10:01:00")
        self.add_event("unknown_visible", "Unknown summary", when=f"{today - timedelta(days=2)} 10:02:00")
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

    def test_food_fluid_details_are_shown_but_other_details_are_hidden_and_escaped(self):
        self.login()
        self.add_event(
            "food_fluid_entry_created", "Offered <Toast>",
            details="Outcome: All consumed\nAdditional details: <b>Observed</b>"
        )
        self.add_event(
            "user_login", "Administrative summary",
            details="Internal behaviour detail"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Outcome: All consumed", page)
        self.assertIn(b"&lt;b&gt;Observed&lt;/b&gt;", page)
        self.assertNotIn(b"Internal behaviour detail", page)

    def test_behaviour_details_render_and_void_details_are_safe(self):
        self.login()
        created = (
            "Categories:\nAggression towards others\nSelf-Harm\n\n"
            "Notes:\nObserved <script>\nSecond line\n" + ("Long note " * 80)
        )
        self.add_event("behaviour_occurrence_created", "Behaviour occurrence recorded", details=created)
        self.add_event("behaviour_occurrence_voided", "Behaviour occurrence voided", details="Status: Voided")
        self.add_event("behaviour_occurrence_created", "Legacy Behaviour", details="")
        self.add_event("behaviour_occurrence_created", "Hidden Behaviour", details="hidden", visible=0)
        self.add_event("behaviour_occurrence_voided", "Failed Behaviour", details="Status: Voided", success=0)
        self.add_event("behaviour_occurrence_created", "Other Client Behaviour", details="other", client_id=2)
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Categories:", page)
        self.assertIn(b"Aggression towards others", page)
        self.assertIn(b"Self-Harm", page)
        self.assertIn(b"&lt;script&gt;", page)
        self.assertIn(b"Second line", page)
        self.assertIn(b"Status: Voided", page)
        self.assertIn(b"Legacy Behaviour", page)
        self.assertNotIn(b"Hidden Behaviour", page)
        self.assertNotIn(b"Failed Behaviour", page)
        self.assertNotIn(b"Other Client Behaviour", page)
        self.assertNotIn(b"Void reason", page)

    def test_abc_behaviour_details_use_nested_presentation_roles(self):
        self.login()
        details = (
            "Before the Behaviour (A):\n"
            "Asked to transition between activities\n"
            "Other\nOther: Was pacing\n\n"
            "Behaviour Observed (B):\nPhysical aggression\n\n"
            "Staff Response (C):\nBlocked behaviour\n\n"
            "Outcome:\nDuration until calm: 1 minute\n"
            "How the client calmed down:\nClient stopped pacing\nAnd smiled\n"
            "Additional notes:\n<test>"
        )
        self.add_event(
            "behaviour_occurrence_created", "Behaviour occurrence recorded",
            details=details
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"storyline-behaviour-section-heading", page)
        self.assertIn(b"storyline-behaviour-section-item", page)
        self.assertIn(b"storyline-behaviour-nested-detail", page)
        self.assertIn(b"storyline-behaviour-outcome-item", page)
        self.assertIn(b"storyline-behaviour-outcome-label", page)
        self.assertIn(b"storyline-behaviour-nested-text", page)
        self.assertIn(b"And smiled", page)
        self.assertIn(b"&lt;test&gt;", page)
        self.assertIn(b"storyline-divider", page)

    def test_incident_details_render_from_activity_log_only_and_escape_values(self):
        self.login()
        details = (
            "Location: Home\nSeverity: High\nInjury: Yes\n"
            "Injury details: <arm>\nActions taken: " + ("Call " * 80) +
            "\nDescription: <script>incident</script>\n"
            "Follow-up required: No\nPolice notified: Yes\nMedical treatment: No"
        )
        self.add_event("incident_created", "Incident created: Fall", details=details)
        self.add_event(
            "shift_note_updated", "Staff note", details="Severity: note\nKeep this"
        )
        self.add_event("incident_created", "Old generic incident", details="Event UTC: old")
        self.add_event("incident_created", "Hidden incident", details="hidden", visible=0)
        self.add_event("incident_created", "Failed incident", details="failed", success=0)
        self.add_event("incident_created", "Other client incident", details="other", client_id=2)
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Location: Home", page)
        self.assertIn(b"&lt;arm&gt;", page)
        self.assertIn(b"&lt;script&gt;incident&lt;/script&gt;", page)
        self.assertIn(b"Injury: Yes", page)
        self.assertIn(b"Follow-up required: No", page)
        self.assertNotIn(b"Severity: High", page)
        self.assertNotIn(b"Police notified: Yes", page)
        self.assertNotIn(b"Medical treatment: No", page)
        self.assertIn(b"Severity: note", page)
        self.assertIn(b"Event UTC: old", page)
        self.assertNotIn(b"Hidden incident", page)
        self.assertNotIn(b"Failed incident", page)
        self.assertNotIn(b"Other client incident", page)
        conn = sqlite3.connect(self.path)
        self.assertEqual(
            conn.execute(
                "SELECT details FROM activity_log WHERE activity_type = 'incident_created' AND summary = ?",
                ("Incident created: Fall",),
            ).fetchone()[0],
            details,
        )
        conn.close()

    def test_shift_note_details_render_from_activity_log_with_safe_history(self):
        self.login()
        first_note = "First line\nSecond <line>\n" + ("Long text " * 80)
        second_note = "Replacement note"
        self.add_event("shift_note_updated", "Updated staff notes for shift", details=first_note)
        self.add_event("shift_note_updated", "Updated staff notes for shift", details=second_note)
        self.add_event(
            "shift_note_updated", "Generic note", details="",
            visible=1
        )
        self.add_event(
            "shift_note_updated", "Hidden note", details="hidden",
            visible=0
        )
        self.add_event(
            "shift_note_updated", "Failed note", details="failed",
            success=0
        )
        self.add_event(
            "shift_note_updated", "Other client note", details="other",
            client_id=2
        )
        self.add_event(
            "shift_note_updated", "Note with follow-up", details="Worker note content"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"First line", page)
        self.assertIn(b"Second &lt;line&gt;", page)
        self.assertIn(b"Long text", page)
        self.assertIn(b"Replacement note", page)
        self.assertIn(b"Generic note", page)
        self.assertIn(b"Note with follow-up", page)
        self.assertNotIn(b"Hidden note", page)
        self.assertNotIn(b"Failed note", page)
        self.assertNotIn(b"Other client note", page)
        self.assertNotIn(b"Follow-up required", page)

    def test_storyline_time_omits_seconds_and_username_but_keeps_user_linkage(self):
        self.login()
        self.add_event(
            "food_fluid_entry_created", "Offered - Juice",
            when="2026-08-02 15:44:30", user_id=1
        )
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE users SET full_name = 'ActorUsername' WHERE user_id = 1")
        conn.commit()
        conn.close()
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"15:44", page)
        self.assertNotIn(b"15:44:30", page)
        self.assertNotIn(b"ActorUsername", page)
        conn = sqlite3.connect(self.path)
        self.assertEqual(
            conn.execute("SELECT user_id FROM activity_log").fetchone()[0], 1
        )
        conn.close()

    def test_storyline_uses_event_time_for_display_order_and_vancouver_date(self):
        self.login()
        today = datetime.now(app.VANCOUVER_TIMEZONE).date()
        yesterday = today - timedelta(days=1)
        event_datetime = self.event_utc(yesterday, 6, 0)
        self.add_event(
            "sleep_woke_up", "Wake event", when=f"{today} 14:20:00",
            event_datetime=event_datetime
        )
        self.add_event(
            "incident_created", "Legacy event", when=f"{yesterday} 13:30:00"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"06:00", page)
        self.assertIn(b"Yesterday", page)
        self.assertLess(page.find(b"Legacy event"), page.find(b"Wake event"))
        self.assertNotIn(event_datetime.encode(), page)

    def test_storyline_is_globally_ordered_by_one_canonical_datetime(self):
        self.login()
        today = datetime.now(app.VANCOUVER_TIMEZONE).date()
        yesterday = today - timedelta(days=1)
        older_date = today - timedelta(days=2)

        self.add_event(
            "shift_activity_created", "Older 23:00",
            when=f"{older_date} 23:00:00"
        )
        self.add_event(
            "shift_activity_created", "Yesterday 15:08",
            when=f"{today} 01:00:00",
            event_datetime=self.event_utc(yesterday, 15, 8)
        )
        self.add_event(
            "shift_activity_created", "Today 08:30",
            when=f"{today} 08:30:00"
        )
        self.add_event(
            "shift_activity_created", "Yesterday 15:11",
            when=f"{yesterday} 15:11:00",
            event_datetime=""
        )
        self.add_event(
            "shift_activity_created", "Yesterday 15:38",
            when=f"{today} 02:00:00",
            event_datetime=self.event_utc(yesterday, 15, 38)
        )
        self.add_event(
            "shift_activity_created", "Yesterday 15:09",
            when=f"{today} 03:00:00",
            event_datetime=self.event_utc(yesterday, 15, 9)
        )
        self.add_event(
            "shift_activity_created", "Yesterday 05:20",
            when=f"{yesterday} 05:20:00"
        )

        page = self.client.get("/client/1/storyline").data.decode()
        self.assertEqual(page.count("<h3>Today</h3>"), 1)
        self.assertEqual(page.count("<h3>Yesterday</h3>"), 1)
        self.assertEqual(
            page.count(
                f"<h3>{older_date.strftime('%A, %B')} "
                f"{older_date.day}, {older_date.year}</h3>"
            ),
            1
        )
        summaries = (
            "Today 08:30",
            "Yesterday 15:38",
            "Yesterday 15:11",
            "Yesterday 15:09",
            "Yesterday 15:08",
            "Yesterday 05:20",
            "Older 23:00",
        )
        positions = [page.find(summary) for summary in summaries]
        self.assertTrue(all(position >= 0 for position in positions))
        self.assertEqual(positions, sorted(positions, reverse=False))

    def test_storyline_same_event_time_uses_activity_id_tie_breaker_and_malformed_falls_back(self):
        self.login()
        self.add_event(
            "shift_activity_created", "First", when="2026-08-02 10:00:00",
            event_datetime="2026-08-02T17:00:00Z"
        )
        self.add_event(
            "shift_activity_created", "Second", when="2026-08-02 11:00:00",
            event_datetime="2026-08-02T17:00:00Z"
        )
        self.add_event(
            "shift_activity_created", "Malformed", when="2026-08-02 12:00:00",
            event_datetime="not-a-timestamp"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertLess(page.find(b"Second"), page.find(b"First"))
        self.assertIn(b"Malformed", page)

    def test_food_fluid_details_render_on_separate_lines_and_blank_details_are_omitted(self):
        self.login()
        self.add_event(
            "food_fluid_entry_created", "Offered - Juice",
            details="Outcome: Partially consumed\nAdditional details: Consumed about 80%"
        )
        self.add_event(
            "food_fluid_entry_created", "Drink - Water",
            details="Outcome: Fully consumed"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Outcome: Partially consumed</div>", page)
        self.assertIn(b"Additional details: Consumed about 80%</div>", page)
        self.assertIn(b"Outcome: Fully consumed</div>", page)
        self.assertNotIn(b"Additional details:</div>", page)

    def test_old_generic_food_fluid_details_render_without_raw_technical_data(self):
        self.login()
        self.add_event(
            "food_fluid_entry_created", "Food & Fluid entry recorded",
            details="Event UTC: 2024-01-01T10:00:00Z; Outcome: All consumed"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Food &amp; Fluid entry recorded", page)
        self.assertNotIn(b"Event UTC", page)

    def test_activity_summary_categories_and_escaped_long_description_render_safely(self):
        self.login()
        long_description = "Walk around neighbourhood " + ("x" * 300)
        self.add_event(
            "shift_activity_created", long_description,
            details="A, T, LS"
        )
        self.add_event(
            "shift_activity_created", "Trampoline",
            details="A"
        )
        self.add_event(
            "shift_activity_created", "<script>alert(1)</script>",
            details="A, T"
        )
        self.add_event(
            "shift_activity_created", "Blank details", details=""
        )
        self.add_event(
            "user_login", "Hidden raw details", details="Private detail"
        )
        self.add_event(
            "shift_activity_created", "Failed Activity", details="LS", success=0
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(long_description.encode(), page)
        self.assertIn(b"A, T, LS", page)
        self.assertIn(b"Trampoline", page)
        self.assertIn(b"A, T", page)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn(b"<script>alert(1)</script>", page)
        self.assertNotIn(b"Private detail", page)
        self.assertNotIn(b"Failed Activity", page)
        self.assertEqual(page.count(b"storyline-details"), 3)

    def test_storyline_events_have_compact_wrappers_and_dividers(self):
        self.login()
        self.add_event("shift_activity_created", "Trampoline", details="A, T")
        self.add_event("food_fluid_entry_created", "Offered - Juice", details="Outcome: Consumed")
        self.add_event("sleep_fell_asleep", "Fell asleep", details="Note: Settled after music")
        page = self.client.get("/client/1/storyline").data
        self.assertEqual(page.count(b'class="storyline-event"'), 3)
        self.assertEqual(page.count(b"storyline-divider"), 3)
        self.assertIn(b"Trampoline", page)
        self.assertIn(b"A, T", page)
        self.assertIn(b"Outcome: Consumed", page)
        self.assertIn(b"Note: Settled after music", page)
        self.assertIn(b"Fell asleep", page)

    def test_activity_details_use_compact_spacing_and_keep_divider(self):
        self.login()
        self.add_event("shift_activity_created", "Trampoline", details="A, T")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"storyline-activity-details", page)
        self.assertIn(b"margin-top: 4px; margin-bottom: 0", page)
        self.assertIn(b"class=\"storyline-event\" style=\"padding: 0.25rem 0 0;\"", page)
        self.assertIn(b"A, T", page)
        self.assertIn(b"storyline-activity-divider\" style=\"border: 0; border-top: 1px solid #d9d9d9; margin: 0.15rem 0 0.35rem;", page)
        self.assertNotIn(b"A, T</div>\n                        </div>", page)

    def test_non_activity_divider_spacing_remains_unchanged(self):
        self.login()
        self.add_event("sleep_fell_asleep", "Fell asleep")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(
            b"class=\"storyline-divider\" style=\"border: 0; border-top: 1px solid #d9d9d9; margin: 0.35rem 0 0.35rem;",
            page
        )

    def test_toileting_details_show_nonblank_fields_and_escape_values(self):
        self.login()
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO toileting_events
            (toileting_event_id, client_id, location, bm_size, bm_consistency,
             behaviour_before, behaviour_during, behaviour_after, behaviour_comments)
            VALUES (7, 1, 'Bathroom', 'Large', 'Soft',
                    '<Observed>', '', 'Calm', '')
        """)
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_type, user_id, client_id, related_id,
             summary, details, success, storyline_visible)
            VALUES ('2026-08-03 10:00:00', 'toileting_event_created', 1, 1, 7,
                    'Toileting event recorded: BM',
                    'Location: Bathroom\nSize: Large\nConsistency: Soft\nBehaviour: Before: <Observed>; After: Calm',
                    1, 1)
        """)
        conn.commit()
        conn.close()
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Location: Bathroom", page)
        self.assertIn(b"Size: Large", page)
        self.assertIn(b"Consistency: Soft", page)
        self.assertIn(b"Behaviour: Before: &lt;Observed&gt;; After: Calm", page)
        self.assertNotIn(b"During:", page)
        self.assertNotIn(b"Comments:", page)

    def test_manager_can_view_and_storyline_view_does_not_write(self):
        self.login(2, "Program Manager")
        before = sqlite3.connect(self.path).execute("SELECT count(*) FROM activity_log").fetchone()[0]
        self.assertEqual(self.client.get("/client/2/storyline").status_code, 200)
        after = sqlite3.connect(self.path).execute("SELECT count(*) FROM activity_log").fetchone()[0]
        self.assertEqual(before, after)

    def test_behaviour_storyline_maps_to_detail_and_support_worker_has_no_control(self):
        occurrence_id = 41
        self.add_behaviour_occurrence(occurrence_id)
        self.add_event(
            "behaviour_occurrence_created", "Behaviour recorded",
            details="Behaviour: Aggression toward others",
            related_table="behaviour_occurrences", related_id=occurrence_id
        )
        self.login(1, "Support Worker")
        worker_page = self.client.get("/client/1/storyline").data
        self.assertNotIn(b"View details", worker_page)
        self.login(2, "Program Manager")
        manager_page = self.client.get("/client/1/storyline").data
        self.assertIn(b'id="storyline-event-', manager_page)
        self.assertIn(b"View details", manager_page)
        self.assertIn(b"/manager-review/behaviour/41", manager_page)

    def test_behaviour_review_is_per_manager_and_preserves_storyline_context(self):
        occurrence_id = 42
        self.add_behaviour_occurrence(occurrence_id)
        self.login(2, "Program Manager")
        detail = self.client.get(
            "/manager-review/behaviour/42?storyline_client_id=1&"
            "storyline_filter=Behaviour&storyline_page=2"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Behaviour notes", detail.data)
        self.assertIn(b"filter=Behaviour", detail.data)
        reviewed = self.client.post(
            "/manager-review/behaviour/42/review",
            data={"storyline_client_id": "1", "storyline_filter": "Behaviour", "storyline_page": "2"}
        )
        self.assertEqual(reviewed.status_code, 302)
        self.assertIn("filter=Behaviour", reviewed.location)
        conn = sqlite3.connect(self.path)
        rows = conn.execute("""
            SELECT user_id, acknowledgement_type FROM acknowledgements
            WHERE source_table='behaviour_occurrences' AND source_id=42
        """).fetchall()
        conn.close()
        self.assertEqual(rows, [(2, "Review")])
        self.login(4, "Program Manager")
        self.assertIn(b"Review required", self.client.get("/manager-review/behaviour/42").data)

    def test_behaviour_consultant_can_review_and_add_management_note(self):
        occurrence_id = 44
        self.add_behaviour_occurrence(occurrence_id)
        self.login(9, "Support Worker")

        detail = self.client.get(
            "/manager-review/behaviour/44?storyline_client_id=1&"
            "storyline_filter=Behaviour&storyline_page=3"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Management Notes", detail.data)
        self.assertIn(b"Add Management Note", detail.data)

        reviewed = self.client.post(
            "/manager-review/behaviour/44/review",
            data={"storyline_client_id": "1", "storyline_filter": "Behaviour",
                  "storyline_page": "3"}
        )
        self.assertEqual(reviewed.status_code, 302)

        noted = self.client.post(
            "/manager-review/behaviour/44/management-note",
            data={"note_text": "  Consultant follow-up  ",
                  "storyline_client_id": "1", "storyline_filter": "Behaviour",
                  "storyline_page": "3"}
        )
        self.assertEqual(noted.status_code, 302)
        self.assertIn("filter=Behaviour", noted.location)
        conn = sqlite3.connect(self.path)
        note = conn.execute("""
            SELECT note_text, created_by_user_id, visibility
            FROM management_notes
            WHERE source_table='behaviour_occurrences' AND source_id=44
        """).fetchone()
        conn.close()
        self.assertEqual(note, ("Consultant follow-up", 9, "management_only"))

    def test_behaviour_consultant_matches_manager_storyline_access(self):
        occurrence_id = 45
        self.add_behaviour_occurrence(occurrence_id)
        self.add_incident(45)
        self.add_event(
            "behaviour_occurrence_created", "Behaviour recorded",
            related_table="behaviour_occurrences", related_id=occurrence_id
        )
        self.add_event(
            "incident_created", "Incident recorded",
            related_table="incident_reports", related_id=45
        )
        self.login(9, "Behaviour Consultant")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"/manager-review/behaviour/45", page)
        self.assertIn(b"/manager-review/incidents/45", page)
        self.assertIn(b"Incident recorded", page)
        self.assertNotIn(b">Edit<", page)

        self.assertEqual(
            self.client.get("/manager-review/behaviour/45").status_code, 200
        )
        self.assertEqual(self.client.get("/shift-task/new").status_code, 403)
        self.assertEqual(self.client.get("/care-task/new").status_code, 403)
        self.assertEqual(
            self.client.get("/housekeeping-task/new").status_code, 403
        )
        self.assertEqual(self.client.post("/action/1").status_code, 403)
        self.assertEqual(
            self.client.post("/behaviour/occurrences/45/void",
                             data={"void_reason": "not permitted"}).status_code,
            403
        )
        self.assertEqual(
            self.client.get("/manager-review/behaviour/45").status_code, 200
        )

    def test_behaviour_consultant_matches_manager_client_scope_and_history(self):
        self.add_event("sleep_fell_asleep", "Client One history", client_id=1)
        self.add_event("sleep_woke_up", "Client Two history", client_id=2)
        for user_id, role in ((2, "Program Manager"), (9, "Behaviour Consultant")):
            self.login(user_id, role)
            client_one = self.client.get("/client/1/storyline?filter=Sleep&page=1")
            client_two = self.client.get("/client/2/storyline?filter=Sleep&page=1")
            self.assertEqual(client_one.status_code, 200)
            self.assertEqual(client_two.status_code, 200)
            self.assertIn(b"Client One history", client_one.data)
            self.assertIn(b"Client Two history", client_two.data)

    def test_behaviour_consultant_sees_storyline_navigation_link(self):
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE clients SET active = 0 WHERE client_id = 2")
        conn.commit()
        conn.close()
        self.login(9, "Behaviour Consultant")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b'href="/client/1/storyline"', page)
        self.assertIn(b"Storyline", page)

    def test_inactive_behaviour_consultant_and_support_worker_remain_forbidden(self):
        occurrence_id = 46
        self.add_behaviour_occurrence(occurrence_id)
        for user_id in (3, 10):
            self.login(user_id, "Behaviour Consultant")
            self.assertEqual(
                self.client.get(f"/manager-review/behaviour/{occurrence_id}").status_code,
                403
            )
            self.assertEqual(
                self.client.post(f"/manager-review/behaviour/{occurrence_id}/review").status_code,
                403
            )

    def test_voided_behaviour_is_reviewable_and_support_worker_is_forbidden(self):
        occurrence_id = 43
        self.add_behaviour_occurrence(occurrence_id, status="Voided")
        self.login(3, "Support Worker")
        self.assertEqual(self.client.get("/manager-review/behaviour/43").status_code, 403)
        self.login(2, "Program Manager")
        page = self.client.get("/manager-review/behaviour/43").data
        self.assertIn(b"Voided", page)
        self.assertIn(b"Mark as Reviewed", page)


if __name__ == "__main__":
    unittest.main()
