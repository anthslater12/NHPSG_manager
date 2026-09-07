import sqlite3
import unittest

from tests.test_behaviour_lifecycle_phase_2 import (
    BehaviourLifecyclePhaseTwoTests,
)


class BehaviourLifecyclePhaseThreeTests(BehaviourLifecyclePhaseTwoTests):
    def complete_abc_record(self):
        occurrence = self.create_in_progress()
        update = self.abc_payload(
            expected_version="1",
            occurrence_local=self.local_time(occurrence),
        )
        update.pop("submission_token")
        update.pop("lifecycle_action", None)
        response = self.client.post(
            f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit",
            data=update
        )
        self.assertEqual(response.status_code, 302)
        return self.row(occurrence["behaviour_occurrence_id"])

    def local_time(self, occurrence):
        import app
        return app.behaviour_utc_to_vancouver(
            occurrence["occurred_at_utc"]
        ).strftime("%Y-%m-%dT%H:%M")

    def complete(self, occurrence):
        return self.client.post(
            f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/complete",
            data={"expected_version": str(occurrence["version_number"])}
        )

    def test_creator_completes_complete_abc_and_worker_edit_locks(self):
        occurrence = self.complete_abc_record()
        self.login(1)
        response = self.complete(occurrence)
        self.assertEqual(response.status_code, 302)
        completed = self.row(occurrence["behaviour_occurrence_id"])
        self.assertEqual(completed["status"], "Completed")
        self.assertRegex(
            completed["completed_at_utc"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
        )
        self.assertEqual(completed["completed_by_user_id"], 1)
        self.assertEqual(completed["version_number"], 3)
        self.assertEqual(
            self.activity_types().count("behaviour_occurrence_completed"), 1
        )
        self.assertEqual(
            self.client.get(
                "/shift/10/behaviour/1/edit"
            ).status_code,
            403
        )
        import app
        week = app.get_behaviour_operational_week_start(
            app.behaviour_utc_to_vancouver(completed["occurred_at_utc"])
        )
        weekly = self.client.get(f"/behaviour/week/{week.isoformat()}")
        self.assertIn(b"Completed", weekly.data)
        self.assertNotIn(b"Edit / Continue", weekly.data)

        conn = sqlite3.connect(self.path)
        details = conn.execute(
            "SELECT details FROM activity_log "
            "WHERE activity_type = 'behaviour_occurrence_completed'"
        ).fetchone()[0]
        conn.close()
        self.assertIn("Occurrence ID: 1", details)
        self.assertIn("Client ID: 1", details)
        self.assertIn("Shift ID: 10", details)
        self.assertIn("Status: In Progress -> Completed", details)
        self.assertIn("Resulting version: 3", details)
        self.assertIn(completed["completed_at_utc"], details)

    def test_completion_authority_is_creator_only_and_database_backed(self):
        self.create_in_progress()
        complete_url = "/shift/10/behaviour/1/complete"
        for user_id in (2, 3, 4, 5, 6, 7, 8):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.post(
                    complete_url, data={"expected_version": "1"}
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.row()["status"], "In Progress")
        self.assertIsNone(self.row()["completed_at_utc"])
        self.assertEqual(self.activity_types(), ["behaviour_occurrence_created"])

        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE shifts SET status = 'Closed' WHERE shift_id = 10")
        conn.commit()
        conn.close()
        self.login(1)
        self.assertEqual(
            self.client.post(complete_url, data={"expected_version": "1"}).status_code,
            403
        )
        self.assertEqual(self.row()["status"], "In Progress")

    def test_incomplete_abc_and_v1_records_cannot_complete(self):
        self.create_in_progress()
        self.login(1)
        response = self.complete(self.row())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.row()["status"], "In Progress")
        self.assertIsNone(self.row()["completed_at_utc"])
        self.assertEqual(self.row()["version_number"], 1)
        self.assertNotIn("behaviour_occurrence_completed", self.activity_types())

        payload = {
            "occurrence_local": "2026-08-03T07:00",
            "submission_token": "V" * 43,
            "notes": "Needs category before completion",
            "lifecycle_action": "in_progress",
            "confirm_distinct_episode": "1",
        }
        response = self.client.post("/shift/10/behaviour", data=payload)
        self.assertEqual(response.status_code, 302)
        v1 = self.row()
        self.assertEqual(v1["record_format"], "V1")
        response = self.complete(v1)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.row(v1["behaviour_occurrence_id"])["status"], "In Progress")
        self.assertIsNone(self.row(v1["behaviour_occurrence_id"])["completed_at_utc"])

    def test_v1_category_without_notes_cannot_complete(self):
        self.login(1)
        payload = {
            "occurrence_local": "2026-08-03T07:00",
            "submission_token": "N" * 43,
            "self_harm": "1",
            "notes": "",
            "lifecycle_action": "in_progress",
            "confirm_distinct_episode": "1",
        }
        self.assertEqual(
            self.client.post("/shift/10/behaviour", data=payload).status_code,
            302
        )
        occurrence = self.row()
        response = self.complete(occurrence)
        self.assertEqual(response.status_code, 400)
        unchanged = self.row()
        self.assertEqual(unchanged["status"], "In Progress")
        self.assertIsNone(unchanged["completed_at_utc"])
        self.assertIsNone(unchanged["completed_by_user_id"])
        self.assertEqual(unchanged["version_number"], 1)
        self.assertNotIn("behaviour_occurrence_completed", self.activity_types())

    def test_complete_v1_record(self):
        self.login(1)
        payload = {
            "occurrence_local": "2026-08-03T07:00",
            "submission_token": "W" * 43,
            "self_harm": "1",
            "notes": "V1 event details",
            "lifecycle_action": "in_progress",
            "confirm_distinct_episode": "1",
        }
        self.assertEqual(
            self.client.post("/shift/10/behaviour", data=payload).status_code,
            302
        )
        occurrence = self.row()
        response = self.complete(occurrence)
        self.assertEqual(response.status_code, 302)
        completed = self.row()
        self.assertEqual(completed["record_format"], "V1")
        self.assertEqual(completed["status"], "Completed")
        self.assertEqual(completed["completed_by_user_id"], 1)

    def test_current_and_stale_completion_versions(self):
        occurrence = self.complete_abc_record()
        self.login(1)
        stale = self.client.post(
            "/shift/10/behaviour/1/complete",
            data={"expected_version": "1"}
        )
        self.assertEqual(stale.status_code, 409)
        unchanged = self.row()
        self.assertEqual(unchanged["status"], "In Progress")
        self.assertIsNone(unchanged["completed_at_utc"])
        self.assertEqual(unchanged["version_number"], 2)
        self.assertEqual(
            self.activity_types().count("behaviour_occurrence_completed"), 0
        )
        self.assertEqual(self.complete(unchanged).status_code, 302)
        self.assertEqual(self.row()["status"], "Completed")

    def test_completed_and_recorded_review_using_acknowledgements(self):
        completed = self.complete_abc_record()
        self.login(1)
        self.assertEqual(self.complete(completed).status_code, 302)
        self.login(3, shift_id=None)
        detail = self.client.get("/manager-review/behaviour/1")
        self.assertEqual(detail.status_code, 200)
        self.assertNotIn(b"has not been finalized", detail.data)
        self.assertIn(b"Mark as Reviewed", detail.data)
        self.assertEqual(
            self.client.post("/manager-review/behaviour/1/review").status_code,
            302
        )
        self.assertEqual(self.acknowledgement_count(), 1)

        self.login(1)
        recorded = self.abc_payload(
            token="X" * 43,
            occurrence_local="2026-08-03T08:00"
        )
        recorded["confirm_distinct_episode"] = "1"
        self.assertEqual(
            self.client.post("/shift/10/behaviour", data=recorded).status_code,
            302
        )
        self.login(3, shift_id=None)
        self.assertEqual(
            self.client.post("/manager-review/behaviour/2/review").status_code,
            302
        )
        self.assertEqual(self.acknowledgement_count(), 2)

    def test_behaviour_consultant_can_read_completed_but_not_complete(self):
        completed = self.complete_abc_record()
        self.login(1)
        self.assertEqual(self.complete(completed).status_code, 302)
        self.login(6, shift_id=None)
        self.assertEqual(
            self.client.get("/manager-review/behaviour/1").status_code,
            200
        )
        self.assertEqual(
            self.client.post(
                "/shift/10/behaviour/1/complete",
                data={"expected_version": "3"}
            ).status_code,
            403
        )

    def test_completed_void_is_rejected_and_recorded_void_still_works(self):
        completed = self.complete_abc_record()
        self.login(1)
        self.assertEqual(self.complete(completed).status_code, 302)
        self.login(3, shift_id=None)
        response = self.client.post(
            "/behaviour/occurrences/1/void",
            data={"void_reason": "Should remain completed"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.row()["status"], "Completed")

        self.login(1)
        recorded = self.abc_payload(
            token="Y" * 43,
            occurrence_local="2026-08-03T09:00"
        )
        recorded["confirm_distinct_episode"] = "1"
        self.assertEqual(
            self.client.post("/shift/10/behaviour", data=recorded).status_code,
            302
        )
        self.login(3, shift_id=None)
        self.assertEqual(
            self.client.post(
                "/behaviour/occurrences/2/void",
                data={"void_reason": "Legacy correction"}
            ).status_code,
            302
        )
        self.assertEqual(self.row(2)["status"], "Voided")


if __name__ == "__main__":
    unittest.main()
