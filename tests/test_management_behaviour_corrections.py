import sqlite3
import unittest

import app

from tests import test_behaviour_lifecycle_phase_2 as behaviour_phase_two
from tests import test_behaviour_lifecycle_phase_4 as behaviour_phase_four


class ManagementBehaviourCorrectionTests(unittest.TestCase):
    """Management-only correction coverage for Behaviour occurrences."""

    def setUp(self):
        behaviour_phase_two.BehaviourLifecyclePhaseTwoTests.setUp(self)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "ALTER TABLE acknowledgements ADD COLUMN invalidated_at_utc TEXT"
            )
            conn.execute(
                "ALTER TABLE acknowledgements ADD COLUMN invalidated_by_user_id INTEGER"
            )
            conn.execute(
                "ALTER TABLE acknowledgements ADD COLUMN invalidation_reason TEXT"
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        behaviour_phase_two.BehaviourLifecyclePhaseTwoTests.tearDown(self)

    def login(self, user_id=1, role="Support Worker"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["full_name"] = "Test User"
            session["role"] = role
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = 10

    def rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, parameters).fetchall()
        finally:
            conn.close()

    def execute(self, sql, parameters=()):
        conn = sqlite3.connect(self.path)
        try:
            cursor = conn.execute(sql, parameters)
            result = cursor.fetchall() if cursor.description else []
            conn.commit()
            return result
        finally:
            conn.close()

    def abc_payload(self, **overrides):
        return behaviour_phase_two.BehaviourLifecyclePhaseTwoTests.abc_payload(
            self, **overrides
        )

    def local_time(self, occurrence):
        return app.behaviour_utc_to_vancouver(
            occurrence["occurred_at_utc"]
        ).strftime("%Y-%m-%dT%H:%M")

    def create_completed(self, user_id=1, token="M" * 43):
        self.login(user_id)
        payload = {
            "occurrence_local": "2026-08-03T07:00",
            "submission_token": token,
            "self_harm": "1",
            "notes": "Recorded notes",
            "lifecycle_action": "in_progress",
            "confirm_distinct_episode": "1",
        }
        created = self.client.post("/shift/10/behaviour", data=payload)
        self.assertEqual(created.status_code, 302)
        occurrence = self.rows(
            "SELECT * FROM behaviour_occurrences "
            "ORDER BY behaviour_occurrence_id DESC LIMIT 1"
        )[0]
        self.login(user_id)
        completion = behaviour_phase_four.BehaviourLifecyclePhaseFourTests.edit_payload(
            self, occurrence, action="complete"
        )
        completed = self.client.post(
            f"/shift/10/behaviour/{occurrence['behaviour_occurrence_id']}/edit",
            data=completion,
        )
        self.assertEqual(completed.status_code, 302, completed.data.decode())
        return self.rows(
            "SELECT * FROM behaviour_occurrences WHERE behaviour_occurrence_id = ?",
            (occurrence["behaviour_occurrence_id"],),
        )[0]

    def create_recorded(self, token="V" * 43):
        self.login(1)
        response = self.client.post(
            "/shift/10/behaviour",
            data={
                "occurrence_local": "2026-08-03T07:00",
                "submission_token": token,
                "self_harm": "1",
                "notes": "Recorded source",
                "lifecycle_action": "recorded",
                "confirm_distinct_episode": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        return self.rows(
            "SELECT * FROM behaviour_occurrences "
            "ORDER BY behaviour_occurrence_id DESC LIMIT 1"
        )[0]

    def correction_payload(self, occurrence, **overrides):
        values = behaviour_phase_four.BehaviourLifecyclePhaseFourTests.edit_payload(
            self, occurrence, action="save"
        )
        values.update({
            "occurrence_local": "2026-08-03T08:00",
            "notes": "Management correction",
        })
        values.update(overrides)
        return values

    def management_url(self, occurrence_id):
        return f"/manager-review/behaviour/{occurrence_id}/correct"

    def test_management_roles_can_correct_other_workers_historical_record(self):
        occurrence = self.create_completed(user_id=2)
        occurrence_id = occurrence["behaviour_occurrence_id"]
        self.execute("UPDATE shifts SET status = 'Closed' WHERE shift_id = 10")

        self.login(3, "Admin")
        detail = self.client.get(
            f"/manager-review/behaviour/{occurrence_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Correct Entry", detail.data)
        self.assertIn(self.management_url(occurrence_id).encode(), detail.data)

        before = occurrence
        response = self.client.post(
            self.management_url(occurrence_id),
            data=self.correction_payload(occurrence),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.location,
            f"/manager-review/behaviour/{occurrence_id}",
        )
        corrected_detail = self.client.get(response.location)
        self.assertEqual(corrected_detail.status_code, 200)
        self.assertIn(b"Behaviour correction saved successfully.", corrected_detail.data)
        after = self.rows(
            "SELECT * FROM behaviour_occurrences WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        )[0]
        self.assertEqual(after["behaviour_occurrence_id"], before["behaviour_occurrence_id"])
        self.assertEqual(after["recorded_by_user_id"], 2)
        self.assertEqual(after["status"], "Completed")
        self.assertEqual(after["completed_at_utc"], before["completed_at_utc"])
        self.assertEqual(after["completed_by_user_id"], before["completed_by_user_id"])
        self.assertEqual(after["notes"], "Management correction")
        self.assertEqual(after["version_number"], before["version_number"] + 1)

        audit = self.rows(
            "SELECT * FROM activity_log WHERE activity_type = ?",
            ("management_behaviour_occurrence_updated",),
        )[0]
        self.assertEqual(audit["user_id"], 3)
        self.assertEqual(audit["related_id"], occurrence_id)
        self.assertEqual(audit["storyline_visible"], 0)
        self.assertIn("Original recorder user ID: 2", audit["details"])
        self.assertIn("Previous version: 2", audit["details"])
        self.assertIn("New version: 3", audit["details"])

    def test_all_management_roles_are_allowed_and_session_role_is_ignored(self):
        for user_id, role, token in (
            (3, "Admin", "A" * 43),
            (4, "Program Manager", "B" * 43),
            (5, "Director", "C" * 43),
        ):
            with self.subTest(role=role):
                occurrence = self.create_completed(token=token)
                self.login(user_id, "Support Worker")
                self.assertEqual(
                    self.client.get(
                        self.management_url(
                            occurrence["behaviour_occurrence_id"]
                        )
                    ).status_code,
                    200,
                )
                response = self.client.post(
                    self.management_url(
                        occurrence["behaviour_occurrence_id"]
                    ),
                    data=self.correction_payload(
                        occurrence, notes=f"Correction by {role}"
                    ),
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    response.location,
                    f"/manager-review/behaviour/"
                    f"{occurrence['behaviour_occurrence_id']}",
                )

        occurrence = self.create_completed(token="D" * 43)
        self.login(1, "Admin")
        self.assertEqual(
            self.client.get(
                self.management_url(occurrence["behaviour_occurrence_id"])
            ).status_code,
            403,
        )

    def test_cancelled_shift_is_denied(self):
        occurrence = self.create_completed(token="E" * 43)
        self.execute("UPDATE shifts SET status = 'Cancelled' WHERE shift_id = 10")
        self.login(3, "Admin")
        self.assertEqual(
            self.client.get(
                self.management_url(occurrence["behaviour_occurrence_id"])
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.management_url(occurrence["behaviour_occurrence_id"]),
                data=self.correction_payload(occurrence),
            ).status_code,
            403,
        )

    def test_mismatched_occurrence_and_shift_clients_are_denied(self):
        occurrence = self.create_completed(token="X" * 43)
        occurrence_id = occurrence["behaviour_occurrence_id"]
        self.execute(
            "INSERT INTO clients (client_id, client_name, active) "
            "VALUES (2, 'Different Client', 1)"
        )
        self.execute(
            "UPDATE behaviour_occurrences SET client_id = ? "
            "WHERE behaviour_occurrence_id = ?",
            (2, occurrence_id),
        )
        self.login(3, "Admin")
        self.assertEqual(
            self.client.get(self.management_url(occurrence_id)).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.management_url(occurrence_id),
                data=self.correction_payload(occurrence, notes="Should fail"),
            ).status_code,
            403,
        )
        self.assertEqual(
            self.rows(
                "SELECT version_number FROM behaviour_occurrences "
                "WHERE behaviour_occurrence_id = ?",
                (occurrence_id,),
            )[0][0],
            occurrence["version_number"],
        )

    def test_inactive_management_user_is_denied_get_and_post(self):
        occurrence = self.create_completed(token="Y" * 43)
        occurrence_id = occurrence["behaviour_occurrence_id"]
        self.execute(
            "INSERT INTO users "
            "(user_id, username, password_hash, full_name, role, active) "
            "VALUES (9, 'inactive-admin', 'x', 'Inactive Admin', 'Admin', 0)"
        )
        self.login(9, "Admin")
        self.assertEqual(
            self.client.get(self.management_url(occurrence_id)).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self.management_url(occurrence_id),
                data=self.correction_payload(occurrence),
            ).status_code,
            403,
        )

    def test_management_correction_keeps_one_current_storyline_behaviour_event(self):
        occurrence = self.create_completed()
        occurrence_id = occurrence["behaviour_occurrence_id"]
        self.login(3, "Admin")
        response = self.client.post(
            self.management_url(occurrence_id),
            data=self.correction_payload(occurrence, notes="Storyline correction"),
        )
        self.assertEqual(response.status_code, 302)

        storyline = self.client.get(
            "/client/1/storyline?filter=Behaviour&date=2026-08-03"
        )
        self.assertEqual(storyline.status_code, 200)
        self.assertIn(b"Storyline correction", storyline.data)
        self.assertEqual(storyline.data.count(b'class="storyline-event"'), 1)
        self.assertNotIn(b"management_behaviour_occurrence_updated", storyline.data)

    def test_management_correction_invalidates_review_and_requires_review_again(self):
        occurrence = self.create_completed()
        occurrence_id = occurrence["behaviour_occurrence_id"]
        review_id = self.execute(
            "INSERT INTO acknowledgements "
            "(source_table, source_id, user_id, acknowledged_at, "
            "acknowledgement_type, active) VALUES "
            "('behaviour_occurrences', ?, 3, '2026-08-03T16:00:00Z', 'Review', 1) "
            "RETURNING acknowledgement_id",
            (occurrence_id,),
        )[0][0]
        self.login(3, "Admin")
        response = self.client.post(
            self.management_url(occurrence_id),
            data=self.correction_payload(occurrence, notes="Needs review again"),
        )
        self.assertEqual(response.status_code, 302)
        acknowledgement = self.rows(
            "SELECT * FROM acknowledgements WHERE acknowledgement_id = ?",
            (review_id,),
        )[0]
        self.assertEqual(acknowledgement["active"], 0)
        self.assertEqual(acknowledgement["invalidated_by_user_id"], 3)
        self.assertIsNotNone(acknowledgement["invalidated_at_utc"])
        self.assertEqual(
            acknowledgement["invalidation_reason"],
            "Behaviour corrected by management",
        )

        detail = self.client.get(
            f"/manager-review/behaviour/{occurrence_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Review required", detail.data)
        self.assertIn(b"Mark as Reviewed", detail.data)
        self.assertIn(b"No management reviews recorded.", detail.data)

    def test_noop_keeps_review_version_and_audit_unchanged(self):
        occurrence = self.create_completed()
        occurrence_id = occurrence["behaviour_occurrence_id"]
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "INSERT INTO acknowledgements "
                "(source_table, source_id, user_id, acknowledged_at, "
                "acknowledgement_type, active) VALUES "
                "('behaviour_occurrences', ?, 3, ?, 'Review', 1)",
                (occurrence_id, "2026-08-03T16:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

        self.login(3, "Admin")
        response = self.client.post(
            self.management_url(occurrence_id),
            data=self.correction_payload(
                occurrence,
                occurrence_local=app.behaviour_utc_to_vancouver(
                    occurrence["occurred_at_utc"]
                ).strftime("%Y-%m-%dT%H:%M"),
                notes=occurrence["notes"] or "",
            ),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            b"No changes were detected. The Behaviour occurrence already matches",
            response.data,
        )
        self.assertEqual(
            self.rows(
                "SELECT version_number FROM behaviour_occurrences "
                "WHERE behaviour_occurrence_id = ?", (occurrence_id,)
            )[0][0],
            occurrence["version_number"],
        )
        self.assertEqual(
            self.rows(
                "SELECT active FROM acknowledgements WHERE source_id = ?",
                (occurrence_id,),
            )[0][0],
            1,
        )
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) FROM activity_log "
                "WHERE activity_type = 'management_behaviour_occurrence_updated'"
            )[0][0],
            0,
        )

    def test_management_route_denies_behaviour_consultant_support_worker_inactive_and_voided(self):
        occurrence = self.create_completed()
        occurrence_id = occurrence["behaviour_occurrence_id"]
        for user_id, role in ((6, "Behaviour Consultant"), (1, "Support Worker"), (8, "Support Worker")):
            with self.subTest(user_id=user_id):
                self.login(user_id, role)
                self.assertEqual(self.client.get(self.management_url(occurrence_id)).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        self.management_url(occurrence_id),
                        data=self.correction_payload(occurrence),
                    ).status_code,
                    403,
                )

        voided = self.create_recorded(token="W" * 43)
        self.login(3, "Admin")
        void_response = self.client.post(
            f"/behaviour/occurrences/{voided['behaviour_occurrence_id']}/void",
            data={"void_reason": "Incorrect record"},
        )
        self.assertEqual(void_response.status_code, 302)
        self.assertEqual(
            self.client.get(
                self.management_url(voided["behaviour_occurrence_id"])
            ).status_code,
            403,
        )

    def test_stale_management_correction_is_rejected(self):
        occurrence = self.create_completed()
        occurrence_id = occurrence["behaviour_occurrence_id"]
        self.login(3, "Admin")
        response = self.client.post(
            self.management_url(occurrence_id),
            data=self.correction_payload(occurrence, expected_version="99"),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.rows(
                "SELECT version_number FROM behaviour_occurrences "
                "WHERE behaviour_occurrence_id = ?", (occurrence_id,)
            )[0][0],
            occurrence["version_number"],
        )


if __name__ == "__main__":
    unittest.main()
