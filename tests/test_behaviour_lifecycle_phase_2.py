import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_behaviour_occurrences_table as migration
import app


class BehaviourLifecyclePhaseTwoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "behaviour.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,
            full_name TEXT, role TEXT, active INTEGER
        );
        CREATE TABLE clients (
            client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER
        );
        CREATE TABLE shifts (
            shift_id INTEGER PRIMARY KEY, client_id INTEGER, shift_date TEXT,
            shift_type TEXT, status TEXT, scheduled_end_time TEXT
        );
        CREATE TABLE shift_staff (
            shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER, user_id INTEGER,
            actual_start_time TEXT, actual_end_at_utc TEXT, sign_on_at TEXT,
            sign_off_at TEXT, active INTEGER
        );
        CREATE TABLE activity_log (
            activity_id INTEGER PRIMARY KEY, activity_datetime TEXT,
            activity_class TEXT NOT NULL, activity_type TEXT NOT NULL,
            user_id INTEGER, client_id INTEGER, shift_id INTEGER,
            related_table TEXT, related_id INTEGER, summary TEXT NOT NULL,
            details TEXT, storyline_visible INTEGER NOT NULL DEFAULT 0,
            success INTEGER, event_datetime TEXT
        );
        CREATE TABLE acknowledgements (
            acknowledgement_id INTEGER PRIMARY KEY, source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
            acknowledged_at TEXT NOT NULL, acknowledgement_type TEXT NOT NULL,
            comment TEXT, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE management_notes (
            management_note_id INTEGER PRIMARY KEY, source_table TEXT NOT NULL,
            source_id INTEGER NOT NULL, note_text TEXT NOT NULL,
            visibility TEXT NOT NULL, created_by_user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            shared_at TEXT, shared_by_user_id INTEGER
        );
        CREATE TABLE action_items (
            action_id INTEGER PRIMARY KEY, source_table TEXT, source_id INTEGER,
            title TEXT, status TEXT, priority TEXT, created_at TEXT,
            assigned_to_user_id INTEGER
        );
        INSERT INTO users VALUES
            (1, 'worker', 'x', 'Creator', 'Support Worker', 1),
            (2, 'other', 'x', 'Other Worker', 'Support Worker', 1),
            (3, 'admin', 'x', 'Admin', 'Admin', 1),
            (4, 'pm', 'x', 'Program Manager', 'Program Manager', 1),
            (5, 'director', 'x', 'Director', 'Director', 1),
            (6, 'consultant', 'x', 'Consultant', 'Behaviour Consultant', 1),
            (7, 'unassigned', 'x', 'Unassigned', 'Support Worker', 1),
            (8, 'inactive', 'x', 'Inactive', 'Support Worker', 0);
        INSERT INTO clients VALUES (1, 'Active Client', 1);
        INSERT INTO shifts VALUES
            (10, 1, '2026-08-03', 'Day', 'Open', '20:00'),
            (11, 1, '2026-08-03', 'Day', 'Closed', '20:00');
        INSERT INTO shift_staff VALUES
            (1, 10, 1, '08:00', NULL, '2026-08-03T15:00:00Z', NULL, 1),
            (2, 10, 2, '08:00', NULL, '2026-08-03T15:00:00Z', NULL, 1),
            (3, 11, 1, '08:00', NULL, '2026-08-03T15:00:00Z', NULL, 1);
        """)
        migration.migrate(conn)
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, shift_id=10):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["full_name"] = "Test User"
            session["role"] = "Support Worker"
            if shift_id is None:
                session.pop(app.DOCUMENTATION_CONTEXT_SESSION_KEY, None)
            else:
                session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = shift_id

    def abc_payload(self, token="A" * 43, **overrides):
        payload = {
            "record_format": "ABC",
            "occurrence_local": (
                datetime.now(app.VANCOUVER_TIMEZONE) - timedelta(minutes=2)
            ).strftime("%Y-%m-%dT%H:%M"),
            "submission_token": token,
            "antecedent_transition_activities": "1",
            "behaviour_physical_aggression": "1",
            "response_blocked_behaviour": "1",
            "duration_until_calm_minutes": "3",
            "calming_description": "Moved to a quiet area",
            "additional_notes": "Initial details",
        }
        payload.update(overrides)
        return payload

    def in_progress_payload(self, token="P" * 43, **overrides):
        payload = self.abc_payload(
            token=token,
            behaviour_physical_aggression=None,
            response_blocked_behaviour=None,
            duration_until_calm_minutes="",
            calming_description="",
            additional_notes="",
            lifecycle_action="in_progress",
        )
        payload.update(overrides)
        for field in (
            "behaviour_physical_aggression", "response_blocked_behaviour"
        ):
            if payload.get(field) is None:
                payload.pop(field, None)
        return payload

    def row(self, occurrence_id=None):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        if occurrence_id is None:
            row = conn.execute(
                "SELECT * FROM behaviour_occurrences "
                "ORDER BY behaviour_occurrence_id DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM behaviour_occurrences WHERE behaviour_occurrence_id = ?",
                (occurrence_id,)
            ).fetchone()
        conn.close()
        return row

    def activity_types(self):
        conn = sqlite3.connect(self.path)
        result = [row[0] for row in conn.execute(
            "SELECT activity_type FROM activity_log ORDER BY activity_id"
        )]
        conn.close()
        return result

    def acknowledgement_count(self):
        conn = sqlite3.connect(self.path)
        count = conn.execute("SELECT COUNT(*) FROM acknowledgements").fetchone()[0]
        conn.close()
        return count

    def create_in_progress(self, token="P" * 43):
        self.login()
        payload = self.in_progress_payload(token=token)
        payload["confirm_distinct_episode"] = "1"
        response = self.client.post(
            "/shift/10/behaviour", data=payload
        )
        self.assertEqual(response.status_code, 302)
        return self.row()

    def test_save_in_progress_edit_and_continue_link(self):
        self.login()
        response = self.client.post(
            "/shift/10/behaviour", data=self.in_progress_payload()
        )
        self.assertEqual(response.status_code, 302)
        occurrence = self.row()
        self.assertEqual(occurrence["status"], "In Progress")
        self.assertEqual(occurrence["recorded_by_user_id"], 1)
        self.assertEqual(occurrence["shift_id"], 10)
        self.assertEqual(occurrence["version_number"], 1)
        self.assertIsNone(occurrence["completed_at_utc"])

        edit_url = "/shift/10/behaviour/1/edit"
        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Continue Behaviour Record", page.data)
        self.assertIn(b"Save In Progress", page.data)
        self.assertIn(b'name="expected_version" value="1"', page.data)
        week = app.get_behaviour_operational_week_start(
            app.behaviour_utc_to_vancouver(occurrence["occurred_at_utc"])
        )
        weekly = self.client.get(f"/behaviour/week/{week.isoformat()}")
        self.assertEqual(weekly.status_code, 200)
        self.assertIn(b"In Progress", weekly.data)
        self.assertIn(b"Edit / Continue", weekly.data)

        update = self.in_progress_payload(
            token="not-accepted-by-edit-route",
            expected_version="1",
            occurrence_local=app.behaviour_utc_to_vancouver(
                occurrence["occurred_at_utc"]
            ).strftime("%Y-%m-%dT%H:%M"),
            behaviour_physical_aggression="1",
            additional_notes="Follow-up details",
        )
        update.pop("lifecycle_action")
        update.pop("submission_token")
        updated = self.client.post(edit_url, data=update)
        self.assertEqual(updated.status_code, 302)
        occurrence = self.row()
        self.assertEqual(occurrence["status"], "In Progress")
        self.assertEqual(occurrence["version_number"], 2)
        self.assertEqual(occurrence["additional_notes"], "Follow-up details")
        self.assertEqual(
            self.activity_types(),
            ["behaviour_occurrence_created", "behaviour_occurrence_updated"]
        )

        conn = sqlite3.connect(self.path)
        details = conn.execute(
            "SELECT details FROM activity_log WHERE activity_type = 'behaviour_occurrence_updated'"
        ).fetchone()[0]
        conn.close()
        self.assertIn("additional_notes", details)
        self.assertIn("None", details)
        self.assertIn("'Follow-up details'", details)

    def test_in_progress_requires_meaningful_data_and_recorded_remains_legacy_default(self):
        self.login()
        empty = self.in_progress_payload(
            token="E" * 43,
            antecedent_transition_activities=None,
        )
        empty.pop("antecedent_transition_activities", None)
        response = self.client.post("/shift/10/behaviour", data=empty)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.row())

        complete = self.abc_payload(token="R" * 43)
        complete.pop("lifecycle_action", None)
        response = self.client.post("/shift/10/behaviour", data=complete)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row()["status"], "Recorded")

    def test_edit_is_creator_only_active_open_same_shift_and_not_management_route(self):
        self.login()
        self.assertEqual(
            self.client.post(
                "/shift/10/behaviour", data=self.in_progress_payload()
            ).status_code,
            302
        )
        edit_url = "/shift/10/behaviour/1/edit"
        for user_id in (2, 3, 4, 5, 6, 7, 8):
            self.login(user_id)
            self.assertEqual(self.client.get(edit_url).status_code, 403, user_id)
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE shifts SET status = 'Closed' WHERE shift_id = 10")
        conn.commit()
        conn.close()
        self.login(1, shift_id=10)
        self.assertEqual(self.client.get(edit_url).status_code, 403)
        self.assertEqual(
            self.client.get("/shift/11/behaviour/1/edit").status_code, 403
        )

    def test_immutable_fields_invalid_partial_noop_and_stale_update_do_not_write(self):
        self.login()
        self.assertEqual(
            self.client.post(
                "/shift/10/behaviour", data=self.in_progress_payload()
            ).status_code,
            302
        )
        edit_url = "/shift/10/behaviour/1/edit"
        original = self.row()
        invalid = self.in_progress_payload(
            expected_version="1",
            antecedent_transition_activities=None,
        )
        invalid.pop("antecedent_transition_activities", None)
        invalid.pop("lifecycle_action")
        invalid.pop("submission_token")
        self.assertEqual(self.client.post(edit_url, data=invalid).status_code, 400)
        self.assertEqual(self.activity_types(), ["behaviour_occurrence_created"])

        noop = self.in_progress_payload(
            expected_version="1",
            occurrence_local=app.behaviour_utc_to_vancouver(
                original["occurred_at_utc"]
            ).strftime("%Y-%m-%dT%H:%M"),
        )
        noop.pop("lifecycle_action")
        noop.pop("submission_token")
        self.assertEqual(self.client.post(edit_url, data=noop).status_code, 400)
        self.assertEqual(self.activity_types(), ["behaviour_occurrence_created"])

        update = self.in_progress_payload(
            expected_version="1", additional_notes="Latest details"
        )
        update.pop("lifecycle_action")
        update.pop("submission_token")
        self.assertEqual(self.client.post(edit_url, data=update).status_code, 302)
        latest = self.row()
        stale = self.in_progress_payload(
            expected_version="1", additional_notes="Stale overwrite"
        )
        stale.pop("lifecycle_action")
        stale.pop("submission_token")
        self.assertEqual(self.client.post(edit_url, data=stale).status_code, 409)
        self.assertEqual(self.row()["additional_notes"], latest["additional_notes"])
        self.assertEqual(self.row()["version_number"], 2)
        self.assertEqual(
            self.activity_types(),
            ["behaviour_occurrence_created", "behaviour_occurrence_updated"]
        )

        crafted = self.in_progress_payload(
            expected_version="2", client_id="99", submission_token="bad",
            status="Completed", completed_by_user_id="3",
            completed_at_utc="2026-08-03T15:00:00Z",
        )
        crafted.pop("lifecycle_action")
        self.assertEqual(self.client.post(edit_url, data=crafted).status_code, 400)
        self.assertEqual(self.row()["status"], "In Progress")
        self.assertEqual(self.row()["version_number"], 2)

    def test_voiding_in_progress_is_rejected_without_changing_record(self):
        self.login()
        self.assertEqual(
            self.client.post(
                "/shift/10/behaviour", data=self.in_progress_payload()
            ).status_code,
            302
        )
        self.login(3, shift_id=None)
        response = self.client.post(
            "/behaviour/occurrences/1/void", data={"void_reason": "No longer needed"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.row()["status"], "In Progress")
        self.assertNotIn("behaviour_occurrence_voided", self.activity_types())

    def test_creator_cannot_edit_finalized_or_voided_statuses(self):
        for status in ("Recorded", "Completed", "Voided"):
            with self.subTest(status=status):
                occurrence = self.create_in_progress(token=status[0] * 43)
                conn = sqlite3.connect(self.path)
                if status == "Completed":
                    conn.execute("""
                        UPDATE behaviour_occurrences
                        SET status = 'Completed', duration_until_calm_minutes = 1,
                            completed_at_utc = '2026-08-03T15:00:00Z',
                            completed_by_user_id = 3
                        WHERE behaviour_occurrence_id = ?
                    """, (occurrence["behaviour_occurrence_id"],))
                elif status == "Voided":
                    conn.execute("""
                        UPDATE behaviour_occurrences
                        SET status = 'Voided', duration_until_calm_minutes = 1,
                            voided_by_user_id = 3,
                            voided_at_utc = '2026-08-03T15:00:00Z',
                            void_reason = 'Test void'
                        WHERE behaviour_occurrence_id = ?
                    """, (occurrence["behaviour_occurrence_id"],))
                else:
                    conn.execute("""
                        UPDATE behaviour_occurrences
                        SET status = 'Recorded', duration_until_calm_minutes = 1
                        WHERE behaviour_occurrence_id = ?
                    """, (occurrence["behaviour_occurrence_id"],))
                conn.commit()
                conn.close()
                self.login(1)
                edit_url = f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit"
                self.assertEqual(self.client.get(edit_url).status_code, 403)
                self.assertEqual(
                    self.client.post(edit_url, data={"expected_version": "1"}).status_code,
                    403
                )
                self.assertEqual(
                    self.row(occurrence["behaviour_occurrence_id"])["status"],
                    status
                )
                self.assertEqual(
                    self.activity_types(),
                    ["behaviour_occurrence_created"] * (
                        occurrence["behaviour_occurrence_id"]
                    )
                )

    def test_in_progress_is_viewable_but_cannot_be_formally_reviewed(self):
        self.create_in_progress()
        self.login(3, shift_id=None)
        detail = self.client.get("/manager-review/behaviour/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"In Progress", detail.data)
        self.assertIn(b"has not been finalized", detail.data)
        self.assertNotIn(b"Mark as Reviewed", detail.data)
        self.assertIn(b"Review is unavailable until this Behaviour record is finalized", detail.data)

        response = self.client.post(
            "/manager-review/behaviour/1/review",
            data={"crafted": "review"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.acknowledgement_count(), 0)
        self.assertEqual(self.activity_types(), ["behaviour_occurrence_created"])


if __name__ == "__main__":
    unittest.main()
