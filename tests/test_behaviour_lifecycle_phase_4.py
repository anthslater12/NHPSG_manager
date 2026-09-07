import sqlite3
import unittest

import app

from tests.test_behaviour_lifecycle_phase_2 import (
    BehaviourLifecyclePhaseTwoTests,
)


class BehaviourLifecyclePhaseFourTests(BehaviourLifecyclePhaseTwoTests):
    def local_time(self, occurrence):
        return app.behaviour_utc_to_vancouver(
            occurrence["occurred_at_utc"]
        ).strftime("%Y-%m-%dT%H:%M")

    def edit_payload(self, occurrence, action="complete", expected_version=None):
        payload = {
            "action": action,
            "expected_version": str(
                occurrence["version_number"]
                if expected_version is None else expected_version
            ),
            "record_format": occurrence["record_format"],
            "occurrence_local": self.local_time(occurrence),
        }
        if occurrence["record_format"] == "ABC":
            for field in app.ABC_BOOLEAN_FIELDS:
                if occurrence[field]:
                    payload[field] = "1"
            for field in app.ABC_TEXT_FIELDS:
                if occurrence[field] is not None:
                    payload[field] = occurrence[field]
            if occurrence["duration_until_calm_minutes"] is not None:
                payload["duration_until_calm_minutes"] = str(
                    occurrence["duration_until_calm_minutes"]
                )
        else:
            for field in app.BEHAVIOUR_CATEGORY_FIELDS:
                if occurrence[field]:
                    payload[field] = "1"
            payload["notes"] = occurrence["notes"] or ""
        return payload

    def create_v1_in_progress(self):
        self.login()
        response = self.client.post(
            "/shift/10/behaviour",
            data={
                "occurrence_local": "2026-08-03T07:00",
                "submission_token": "V" * 43,
                "self_harm": "1",
                "notes": "Draft notes",
                "lifecycle_action": "in_progress",
                "confirm_distinct_episode": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        return self.row()

    def activity_rows(self):
        conn = sqlite3.connect(self.path)
        rows = conn.execute(
            "SELECT activity_type, details FROM activity_log ORDER BY activity_id"
        ).fetchall()
        conn.close()
        return rows

    def test_edit_form_has_explicit_save_and_complete_actions(self):
        occurrence = self.create_in_progress()
        page = self.client.get(
            f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'name="action" value="save"', page.data)
        self.assertIn(b'name="action" value="complete"', page.data)
        self.assertIn(b"Save &amp; Complete", page.data)
        self.assertIn(b"stops normal worker editing", page.data)

        edit_url = f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit"
        missing = self.edit_payload(occurrence)
        missing.pop("action")
        self.assertEqual(self.client.post(edit_url, data=missing).status_code, 400)
        unknown = self.edit_payload(occurrence, action="finish")
        self.assertEqual(self.client.post(edit_url, data=unknown).status_code, 400)
        multiple = self.edit_payload(occurrence)
        multiple["action"] = ["save", "complete"]
        self.assertEqual(self.client.post(edit_url, data=multiple).status_code, 400)
        self.assertEqual(self.row()["version_number"], 1)
        self.assertEqual(
            [row[0] for row in self.activity_rows()],
            ["behaviour_occurrence_created"],
        )
        self.assertEqual(
            self.client.post(
                "/shift/10/behaviour/1/complete",
                data={"expected_version": "1"},
            ).status_code,
            404,
        )

    def test_save_and_complete_persists_final_v1_values_in_one_post(self):
        occurrence = self.create_v1_in_progress()
        self.login()
        payload = self.edit_payload(occurrence)
        payload["notes"] = "Final unsaved notes"
        payload["aggression_towards_others"] = "1"
        response = self.client.post(
            f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit",
            data=payload,
        )
        self.assertEqual(response.status_code, 302)
        completed = self.row()
        self.assertEqual(completed["status"], "Completed")
        self.assertEqual(completed["notes"], "Final unsaved notes")
        self.assertEqual(completed["aggression_towards_others"], 1)
        self.assertEqual(completed["completed_by_user_id"], 1)
        self.assertEqual(completed["version_number"], 2)
        self.assertRegex(
            completed["completed_at_utc"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )
        rows = self.activity_rows()
        self.assertEqual(
            [row[0] for row in rows],
            ["behaviour_occurrence_created", "behaviour_occurrence_completed"],
        )
        self.assertIn("notes", rows[1][1])
        self.assertIn("Previous status: In Progress", rows[1][1])
        self.assertIn("New status: Completed", rows[1][1])
        self.assertIn("Completion actor user ID: 1", rows[1][1])
        self.assertIn("Resulting version: 2", rows[1][1])

    def test_save_and_complete_abc_requires_final_sections_and_duration(self):
        occurrence = self.create_in_progress()
        self.login()
        edit_url = f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit"
        invalid = self.edit_payload(occurrence)
        response = self.client.post(edit_url, data=invalid)
        self.assertEqual(response.status_code, 400)
        unchanged = self.row()
        self.assertEqual(unchanged["status"], "In Progress")
        self.assertEqual(unchanged["version_number"], 1)
        self.assertIsNone(unchanged["completed_at_utc"])
        self.assertEqual(
            [row[0] for row in self.activity_rows()],
            ["behaviour_occurrence_created"],
        )

        final = self.abc_payload(
            expected_version="1",
            occurrence_local=self.local_time(occurrence),
            action="complete",
            antecedent_transition_activities=None,
            antecedent_denied_access="1",
            behaviour_physical_aggression="1",
            response_blocked_behaviour="1",
            duration_until_calm_minutes="4",
        )
        final.pop("submission_token")
        final.pop("lifecycle_action", None)
        response = self.client.post(edit_url, data=final)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row()["status"], "Completed")
        self.assertEqual(self.row()["duration_until_calm_minutes"], 4)
        self.assertEqual(self.row()["version_number"], 2)

    def test_stale_save_and_complete_changes_nothing(self):
        occurrence = self.create_in_progress()
        self.login()
        edit_url = f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit"
        saved = self.edit_payload(occurrence, action="save")
        saved["additional_notes"] = "Saved first"
        self.assertEqual(self.client.post(edit_url, data=saved).status_code, 302)
        current = self.row()
        stale = self.edit_payload(occurrence, action="complete", expected_version=1)
        stale["additional_notes"] = "Stale final value"
        response = self.client.post(edit_url, data=stale)
        self.assertEqual(response.status_code, 409)
        unchanged = self.row()
        self.assertEqual(unchanged["status"], "In Progress")
        self.assertEqual(unchanged["additional_notes"], current["additional_notes"])
        self.assertEqual(unchanged["version_number"], 2)
        self.assertIsNone(unchanged["completed_at_utc"])
        self.assertEqual(
            [row[0] for row in self.activity_rows()],
            ["behaviour_occurrence_created", "behaviour_occurrence_updated"],
        )


if __name__ == "__main__":
    unittest.main()
