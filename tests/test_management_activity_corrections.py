import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import app

try:
    import tests.test_shift_activities as shift_activity_tests
except ImportError:
    import test_shift_activities as shift_activity_tests


class ManagementActivityCorrectionTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "management-activities.db"
        )
        self.original_database_name = app.DB_NAME
        self.addCleanup(self.restore_application_state)
        app.DB_NAME = self.database_path
        app.app.config.update(TESTING=True)
        shift_activity_tests.ShiftActivitiesTests.create_database(self)
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute(
                "ALTER TABLE activity_log ADD COLUMN storyline_visible "
                "INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute(
                "ALTER TABLE activity_log ADD COLUMN event_datetime TEXT"
            )
            conn.commit()
        finally:
            conn.close()
        self.client = app.app.test_client()

    def restore_application_state(self):
        app.DB_NAME = self.original_database_name

    def login(self, user_id, role="Support Worker"):
        shift_activity_tests.ShiftActivitiesTests.login(self, user_id, role)

    def rows(self, sql, parameters=()):
        return shift_activity_tests.ShiftActivitiesTests.rows(
            self, sql, parameters
        )

    def insert_activity(self, shift_id=10, user_id=1, description="Existing"):
        return shift_activity_tests.ShiftActivitiesTests.insert_activity(
            self, shift_id, user_id, description
        )

    def insert_completed_activity(self, shift_id=10, user_id=1):
        return shift_activity_tests.ShiftActivitiesTests.insert_completed_activity(
            self, shift_id, user_id
        )

    def activity_row(self, activity_id):
        return shift_activity_tests.ShiftActivitiesTests.activity_row(
            self, activity_id
        )

    def correction_payload(self, activity_id, version=1, **overrides):
        values = {
            "action": "save",
            "expected_version": str(version),
            "start_time": "09:15",
            "end_time": "10:15",
            "a_selected": "1",
            "activity_description": "Management correction",
        }
        values.update(overrides)
        return values

    def add_review(self, activity_id, user_id=7):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("""
                INSERT INTO acknowledgements
                (source_table, source_id, user_id, acknowledgement_type, active)
                VALUES ('shift_activities', ?, ?, 'Review', 1)
            """, (activity_id, user_id))
            conn.commit()
        finally:
            conn.close()

    def test_management_roles_can_get_and_post_correction(self):
        for user_id, role in ((6, "Admin"), (7, "Program Manager"), (8, "Director")):
            with self.subTest(role=role):
                activity_id = self.insert_activity(description=f"{role} source")
                self.login(user_id, role)
                edit_url = f"/manager-review/activities/{activity_id}/correct"
                self.assertEqual(self.client.get(edit_url).status_code, 200)
                response = self.client.post(
                    edit_url,
                    data=self.correction_payload(activity_id),
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Activity correction saved successfully.", response.data)
                self.assertEqual(
                    self.activity_row(activity_id)["activity_description"],
                    "Management correction",
                )

    def test_behaviour_consultant_support_worker_and_inactive_manager_are_denied(self):
        for user_id, role in (
            (9, "Behaviour Consultant"),
            (1, "Support Worker"),
            (10, "Admin"),
        ):
            with self.subTest(user_id=user_id, role=role):
                activity_id = self.insert_activity(description="Protected source")
                self.login(user_id, role)
                edit_url = f"/manager-review/activities/{activity_id}/correct"
                self.assertEqual(self.client.get(edit_url).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        edit_url,
                        data=self.correction_payload(activity_id),
                    ).status_code,
                    403,
                )

    def test_database_role_controls_access_not_session_role(self):
        activity_id = self.insert_activity()
        self.login(6, "Support Worker")
        self.assertEqual(
            self.client.get(
                f"/manager-review/activities/{activity_id}/correct"
            ).status_code,
            200,
        )

        activity_id = self.insert_activity(user_id=2)
        self.login(2, "Admin")
        self.assertEqual(
            self.client.get(
                f"/manager-review/activities/{activity_id}/correct"
            ).status_code,
            403,
        )

    def test_historical_closed_and_signed_off_activity_can_be_corrected(self):
        historical_id = self.insert_activity(
            shift_id=20, user_id=2, description="Historical source"
        )
        signed_off_id = self.insert_activity(
            shift_id=10, user_id=2, description="Signed off source"
        )
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute(
                "UPDATE shift_staff SET active = 0, sign_off_at = ? "
                "WHERE shift_id = 10 AND user_id = 2",
                ("2026-08-03T23:00:00Z",),
            )
            conn.commit()
        finally:
            conn.close()

        self.login(6, "Admin")
        for activity_id in (historical_id, signed_off_id):
            with self.subTest(activity_id=activity_id):
                response = self.client.post(
                    f"/manager-review/activities/{activity_id}/correct",
                    data=self.correction_payload(activity_id),
                )
                self.assertEqual(response.status_code, 302)

    def test_cancelled_shift_activity_is_denied(self):
        activity_id = self.insert_activity(
            shift_id=30, user_id=2, description="Cancelled source"
        )
        self.login(6, "Admin")
        edit_url = f"/manager-review/activities/{activity_id}/correct"
        self.assertEqual(self.client.get(edit_url).status_code, 403)
        self.assertEqual(
            self.client.post(
                edit_url,
                data=self.correction_payload(activity_id),
            ).status_code,
            403,
        )

    def test_same_id_recorder_completion_metadata_and_version_are_preserved(self):
        activity_id = self.insert_completed_activity(user_id=2)
        before = self.activity_row(activity_id)
        self.login(6, "Admin")
        response = self.client.post(
            f"/manager-review/activities/{activity_id}/correct",
            data=self.correction_payload(
                activity_id,
                version=2,
                start_time="16:20",
                end_time="16:30",
                activity_description="Historical correction",
            ),
        )
        self.assertEqual(response.status_code, 302)
        after = self.activity_row(activity_id)
        self.assertEqual(after["shift_activity_id"], before["shift_activity_id"])
        self.assertEqual(after["recorded_by_user_id"], before["recorded_by_user_id"])
        self.assertEqual(after["completed_by_user_id"], before["completed_by_user_id"])
        self.assertEqual(after["completed_at_utc"], before["completed_at_utc"])
        self.assertEqual(after["version_number"], before["version_number"] + 1)
        self.assertEqual(after["start_time"], "16:20")

        audit = self.rows("""
            SELECT * FROM activity_log
            WHERE activity_type = 'management_shift_activity_updated'
        """)[0]
        self.assertEqual(audit["user_id"], 6)
        self.assertEqual(audit["client_id"], 1)
        self.assertEqual(audit["shift_id"], 10)
        self.assertEqual(audit["related_id"], activity_id)
        self.assertEqual(audit["storyline_visible"], 0)
        self.assertIn("Original recorder user ID: 2", audit["details"])
        self.assertIn("09:00", audit["details"])
        self.assertIn("16:20", audit["details"])
        self.assertIn("Previous version: 2", audit["details"])
        self.assertIn("New version: 3", audit["details"])

    def test_stale_version_is_rejected(self):
        activity_id = self.insert_activity()
        self.login(6, "Admin")
        response = self.client.post(
            f"/manager-review/activities/{activity_id}/correct",
            data=self.correction_payload(activity_id, version=99),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.activity_row(activity_id)["version_number"], 1)

    def test_reviewed_activity_is_corrected_and_reviews_are_invalidated(self):
        activity_id = self.insert_activity(description="Reviewed source")
        self.add_review(activity_id, user_id=7)
        review_id = self.rows(
            "SELECT acknowledgement_id FROM acknowledgements"
        )[0]["acknowledgement_id"]
        self.login(6, "Admin")
        response = self.client.post(
            f"/manager-review/activities/{activity_id}/correct",
            data=self.correction_payload(activity_id),
        )
        self.assertEqual(response.status_code, 302)
        review = self.rows(
            "SELECT * FROM acknowledgements WHERE acknowledgement_id = ?",
            (review_id,),
        )[0]
        self.assertEqual(review["active"], 0)
        self.assertEqual(review["invalidated_by_user_id"], 6)
        self.assertIsNotNone(review["invalidated_at_utc"])
        audit = self.rows(
            "SELECT details FROM activity_log "
            "WHERE activity_type = 'management_shift_activity_updated'"
        )[0]
        self.assertIn(f"Invalidated Review acknowledgement IDs: {review_id}", audit["details"])
        self.assertIn("Invalidated Review count: 1", audit["details"])

        detail = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Mark as Reviewed", detail.data)
        self.assertIn(b"No management reviews recorded.", detail.data)

    def test_noop_does_not_change_version_audit_or_review(self):
        activity_id = self.insert_activity()
        self.add_review(activity_id)
        before = self.activity_row(activity_id)
        self.login(6, "Admin")
        response = self.client.post(
            f"/manager-review/activities/{activity_id}/correct",
            data=self.correction_payload(
                activity_id,
                start_time="09:00",
                end_time="10:00",
                a_selected="1",
                activity_description="Existing",
            ),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            b"No changes were detected. The Activity already matches the information entered.",
            response.data,
        )
        self.assertEqual(self.activity_row(activity_id), before)
        self.assertEqual(
            self.rows("SELECT active FROM acknowledgements")[0]["active"],
            1,
        )
        self.assertEqual(
            self.rows("SELECT activity_type FROM activity_log"),
            [],
        )

    def test_detail_control_is_management_only(self):
        activity_id = self.insert_activity()
        self.login(6, "Admin")
        manager_page = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertIn(b"Correct Entry", manager_page.data)
        self.assertIn(
            f"/manager-review/activities/{activity_id}/correct".encode(),
            manager_page.data,
        )

        self.login(9, "Behaviour Consultant")
        consultant_page = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertNotIn(b"Correct Entry", consultant_page.data)

    def test_storyline_rehydrates_corrected_activity_without_duplicate_event(self):
        activity_id = self.insert_activity(description="Original Storyline text")
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("""
                INSERT INTO activity_log
                (activity_datetime, activity_class, activity_type, user_id,
                 client_id, shift_id, related_table, related_id, summary,
                 details, success, storyline_visible, event_datetime)
                VALUES ('2026-08-03 16:00:00', 'ACTIVITY',
                        'shift_activity_created', 2, 1, 10,
                        'shift_activities', ?, 'Original Storyline text',
                        'Original Storyline text', 1, 1,
                        '2026-08-03T16:00:00Z')
            """, (activity_id,))
            conn.commit()
        finally:
            conn.close()

        self.login(6, "Admin")
        self.client.post(
            f"/manager-review/activities/{activity_id}/correct",
            data=self.correction_payload(
                activity_id,
                start_time="09:15",
                end_time="10:15",
                activity_description="Corrected Storyline text",
            ),
        )
        storyline = self.client.get(
            "/client/1/storyline?filter=Activity&date=2026-08-03"
        )
        self.assertEqual(storyline.status_code, 200)
        self.assertIn(b"Corrected Storyline text", storyline.data)
        self.assertEqual(storyline.data.count(b'class="storyline-event"'), 1)
        self.assertNotIn(b"management_shift_activity_updated", storyline.data)

    def test_activity_reporting_reads_corrected_current_row(self):
        activity_id = self.insert_activity(description="Report source")
        self.login(6, "Admin")
        self.client.post(
            f"/manager-review/activities/{activity_id}/correct",
            data=self.correction_payload(
                activity_id,
                start_time="09:15",
                end_time="10:15",
                activity_description="Corrected report source",
            ),
        )
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            occurrences = app._activity_report_occurrences(
                conn,
                1,
                date(2026, 8, 1),
                date(2026, 8, 31),
            )
        finally:
            conn.close()
        self.assertEqual(
            [item["activity_description"] for item in occurrences],
            ["Corrected report source"],
        )


if __name__ == "__main__":
    unittest.main()
