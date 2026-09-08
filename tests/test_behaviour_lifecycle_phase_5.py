import sqlite3
import unittest
from unittest.mock import patch

import app

from tests.test_behaviour_lifecycle_phase_4 import (
    BehaviourLifecyclePhaseFourTests,
)


class BehaviourLifecyclePhaseFiveTests(BehaviourLifecyclePhaseFourTests):
    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(self.path)
        conn.executescript("""
        CREATE TABLE shift_notes (
            shift_note_id INTEGER PRIMARY KEY, shift_date TEXT,
            shift_type TEXT, client_id INTEGER, user_id INTEGER,
            created_at TEXT, note_text TEXT
        );
        CREATE TABLE shift_care_task_entries (
            entry_id INTEGER PRIMARY KEY, care_task_id INTEGER,
            shift_id INTEGER, completed_by_user_id INTEGER,
            completed_at TEXT, outcome TEXT, comment TEXT
        );
        CREATE TABLE shift_housekeeping_task_entries (
            entry_id INTEGER PRIMARY KEY, housekeeping_task_id INTEGER,
            shift_id INTEGER, completed_by_user_id INTEGER,
            completed_at TEXT, outcome TEXT, comment TEXT
        );
        """)
        conn.commit()
        conn.close()

    def dashboard(self, user_id=1, role="Support Worker", shift_id=10):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = "Test User"
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = shift_id
        patches = (
            patch.object(app, "_get_authenticated_staff_notice_recipient", return_value={"user_id": user_id}),
            patch.object(app, "_get_staff_notice_recipient_collections", return_value={"dashboard": [], "outstanding_count": 0}),
            patch.object(app, "reconcile_staff_notice_non_shift_requirements_in_transaction"),
            patch.object(app, "get_active_food_fluid_shift_context", side_effect=PermissionError),
            patch.object(app, "get_active_sleep_shift_context", side_effect=PermissionError),
            patch.object(app, "get_food_fluid_shift_entries", return_value=[]),
            patch.object(app, "get_sleep_events", return_value=[]),
            patch.object(app, "get_applicable_care_tasks", return_value=[]),
            patch.object(app, "get_applicable_housekeeping_tasks", return_value=[]),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
            return self.client.get(f"/shift/{shift_id}")

    def test_save_returns_to_same_edit_page_persists_and_flashes_progress(self):
        occurrence = self.create_in_progress()
        self.login()
        payload = self.edit_payload(occurrence, action="save")
        payload["additional_notes"] = "Saved progress"
        response = self.client.post(
            f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit",
            data=payload,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/shift/10/behaviour/1/edit")
        self.assertEqual(self.row()["additional_notes"], "Saved progress")
        self.assertEqual(self.row()["version_number"], 2)
        self.assertEqual(
            self.activity_types(),
            ["behaviour_occurrence_created", "behaviour_occurrence_updated"],
        )
        with self.client.session_transaction() as session:
            self.assertIn(
                "Behaviour progress saved.",
                [message for _category, message in session.get("_flashes", [])]
            )

    def test_current_shift_shows_only_eligible_own_in_progress_records(self):
        own = self.create_in_progress(token="O" * 43)
        other = self.create_in_progress(token="W" * 43)
        conn = sqlite3.connect(self.path)
        conn.execute(
            "UPDATE behaviour_occurrences SET recorded_by_user_id = ? "
            "WHERE behaviour_occurrence_id = ?",
            (2, other["behaviour_occurrence_id"]),
        )
        conn.commit()
        conn.close()
        page = self.dashboard().data
        self.assertIn(b"Behaviour In Progress", page)
        self.assertIn(b"Continue Behaviour", page)
        self.assertIn(b"/shift/10/behaviour/1/edit", page)
        self.assertNotIn(b"/shift/10/behaviour/2/edit", page)

    def test_current_shift_excludes_finalized_and_voided_records(self):
        for status in ("Completed", "Recorded", "Voided"):
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
                    conn.execute(
                        "UPDATE behaviour_occurrences "
                        "SET status = 'Recorded', duration_until_calm_minutes = 1 "
                        "WHERE behaviour_occurrence_id = ?",
                        (occurrence["behaviour_occurrence_id"],),
                    )
                conn.commit()
                conn.close()
                self.assertNotIn(b"Behaviour In Progress", self.dashboard().data)

    def test_current_shift_excludes_unauthorized_worker_contexts(self):
        self.create_in_progress()
        for user_id, role in (
            (3, "Admin"),
            (6, "Behaviour Consultant"),
            (7, "Support Worker"),
        ):
            with self.subTest(user_id=user_id):
                self.assertNotIn(
                    b"Continue Behaviour",
                    self.dashboard(user_id=user_id, role=role).data,
                )
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE shifts SET status = 'Closed' WHERE shift_id = 10")
        conn.commit()
        conn.close()
        self.assertNotIn(b"Continue Behaviour", self.dashboard().data)

    def test_inactive_worker_cannot_resolve_resume_records(self):
        self.create_in_progress()
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        with self.assertRaises(PermissionError):
            app.get_behaviour_in_progress_resume_records(conn, 10, 8)
        conn.close()

    def test_completed_record_disappears_after_save_and_complete(self):
        occurrence = self.create_v1_in_progress()
        self.login()
        response = self.client.post(
            f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit",
            data=self.edit_payload(occurrence, action="complete"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row()["status"], "Completed")
        self.assertNotIn(b"Behaviour In Progress", self.dashboard().data)


if __name__ == "__main__":
    unittest.main()
