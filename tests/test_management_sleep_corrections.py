import sqlite3
import unittest

import app

try:
    from tests import test_sleep_storyline_review as sleep_review
except ImportError:
    import test_sleep_storyline_review as sleep_review


class ManagementSleepCorrectionTests(unittest.TestCase):

    def setUp(self):
        sleep_review.SleepStorylineReviewTests.setUp(self)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "ALTER TABLE acknowledgements ADD COLUMN "
                "invalidated_at_utc TEXT"
            )
            conn.execute(
                "ALTER TABLE acknowledgements ADD COLUMN "
                "invalidated_by_user_id INTEGER"
            )
            conn.execute(
                "ALTER TABLE acknowledgements ADD COLUMN "
                "invalidation_reason TEXT"
            )
            conn.commit()
        finally:
            conn.close()

    def cleanup(self):
        sleep_review.SleepStorylineReviewTests.cleanup(self)

    def login(self, user_id, role):
        sleep_review.SleepStorylineReviewTests.login(self, user_id, role)

    def rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, parameters).fetchall()
        finally:
            conn.close()

    def row(self, sql, parameters=()):
        rows = self.rows(sql, parameters)
        return rows[0] if rows else None

    def add_sleep_event(self, sleep_event_id, **kwargs):
        sleep_review.SleepStorylineReviewTests.add_sleep_event(
            self, sleep_event_id, **kwargs
        )

    def add_acknowledgement(
        self,
        sleep_event_id,
        user_id=2,
        acknowledgement_type="Review",
        active=1,
    ):
        sleep_review.SleepStorylineReviewTests.add_acknowledgement(
            self,
            sleep_event_id,
            user_id,
            acknowledgement_type=acknowledgement_type,
            active=active,
        )

    def edit_url(self, sleep_event_id):
        return f"/manager-review/sleep/{sleep_event_id}/correct"

    def correction_data(self, event_local="2026-08-02T09:30", note="Corrected note"):
        return {"event_local": event_local, "note": note}

    def test_management_roles_can_get_and_post_correction(self):
        for event_id, user_id, role in (
            (1, 2, "Admin"),
            (2, 3, "Director"),
            (3, 4, "Program Manager"),
        ):
            with self.subTest(role=role):
                self.add_sleep_event(event_id)
                self.login(user_id, role)
                response = self.client.get(self.edit_url(event_id))
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Correct Sleep Entry", response.data)
                response = self.client.post(
                    self.edit_url(event_id),
                    data=self.correction_data(note=f"{role} correction")
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    response.location,
                    f"/manager-review/sleep/{event_id}"
                )

        notes = self.rows(
            "SELECT note FROM sleep_events ORDER BY sleep_event_id"
        )
        self.assertEqual(
            [row["note"] for row in notes],
            ["Admin correction", "Director correction", "Program Manager correction"],
        )

    def test_database_role_controls_management_correction(self):
        self.add_sleep_event(1)
        self.login(2, "Support Worker")
        self.assertEqual(self.client.get(self.edit_url(1)).status_code, 200)

        self.login(1, "Admin")
        self.assertEqual(self.client.get(self.edit_url(1)).status_code, 403)

        self.login(6, "Admin")
        self.assertEqual(self.client.get(self.edit_url(1)).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.edit_url(1), data=self.correction_data()
            ).status_code,
            403,
        )

    def test_behaviour_consultant_and_support_worker_are_denied(self):
        self.add_sleep_event(1)
        for user_id, role in ((7, "Behaviour Consultant"), (1, "Support Worker")):
            with self.subTest(role=role):
                self.login(user_id, role)
                self.assertEqual(self.client.get(self.edit_url(1)).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        self.edit_url(1), data=self.correction_data()
                    ).status_code,
                    403,
                )

    def test_detail_shows_management_correct_entry_only_to_management(self):
        self.add_sleep_event(1)
        self.login(2, "Admin")
        manager_page = self.client.get("/manager-review/sleep/1")
        self.assertEqual(manager_page.status_code, 200)
        self.assertIn(b"Correct Entry", manager_page.data)
        self.assertIn(b"/manager-review/sleep/1/correct", manager_page.data)
        table_end = manager_page.data.index(b"</table>")
        correct = manager_page.data.index(b"Correct Entry")
        reviews = manager_page.data.index(b"<h3>Reviews</h3>")
        self.assertLess(table_end, correct)
        self.assertLess(correct, reviews)

        self.login(7, "Behaviour Consultant")
        consultant_page = self.client.get("/manager-review/sleep/1")
        self.assertEqual(consultant_page.status_code, 200)
        self.assertNotIn(b"Correct Entry", consultant_page.data)

    def test_other_worker_signed_off_and_historical_rows_can_be_corrected(self):
        self.add_sleep_event(1)
        self.add_sleep_event(2, shift_id=20, client_id=2)
        self.login(2, "Admin")

        for event_id in (1, 2):
            with self.subTest(event_id=event_id):
                response = self.client.post(
                    self.edit_url(event_id),
                    data=self.correction_data(note=f"Historical {event_id}")
                )
                self.assertEqual(response.status_code, 302)

        conn = sqlite3.connect(self.path)
        try:
            conn.execute("UPDATE shift_staff SET active = 0 WHERE shift_id = 10")
            conn.execute("UPDATE shifts SET status = 'Closed' WHERE shift_id = 10")
            conn.commit()
        finally:
            conn.close()

        self.add_sleep_event(3)
        response = self.client.post(
            self.edit_url(3),
            data=self.correction_data(note="Closed shift correction")
        )
        self.assertEqual(response.status_code, 302)

    def test_cancelled_shift_is_denied(self):
        self.add_sleep_event(1)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("UPDATE shifts SET status = 'Cancelled' WHERE shift_id = 10")
            conn.commit()
        finally:
            conn.close()

        self.login(2, "Admin")
        self.assertEqual(self.client.get(self.edit_url(1)).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.edit_url(1), data=self.correction_data()
            ).status_code,
            403,
        )

    def test_correction_preserves_source_identity_and_writes_hidden_audit(self):
        self.add_sleep_event(1)
        before = self.row(
            "SELECT * FROM sleep_events WHERE sleep_event_id = 1"
        )
        self.login(2, "Admin")
        response = self.client.post(
            self.edit_url(1),
            data=self.correction_data(
                event_local="2026-08-02T09:00",
                note="Management corrected note"
            )
        )
        self.assertEqual(response.status_code, 302)
        after = self.row(
            "SELECT * FROM sleep_events WHERE sleep_event_id = 1"
        )
        self.assertEqual(after["sleep_event_id"], before["sleep_event_id"])
        self.assertEqual(after["client_id"], before["client_id"])
        self.assertEqual(after["shift_id"], before["shift_id"])
        self.assertEqual(after["event_type"], before["event_type"])
        self.assertEqual(after["recorded_by_user_id"], before["recorded_by_user_id"])
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual(after["event_datetime"], "2026-08-02T16:00:00Z")
        self.assertEqual(after["note"], "Management corrected note")

        audit = self.row(
            "SELECT * FROM activity_log "
            "WHERE activity_type = 'management_sleep_event_updated'"
        )
        self.assertEqual(audit["user_id"], 2)
        self.assertEqual(audit["client_id"], 1)
        self.assertEqual(audit["shift_id"], 10)
        self.assertEqual(audit["related_table"], "sleep_events")
        self.assertEqual(audit["related_id"], 1)
        self.assertEqual(audit["storyline_visible"], 0)
        self.assertIn("Changed fields: event_datetime, note", audit["details"])
        self.assertIn("Settled after music", audit["details"])
        self.assertIn("Management corrected note", audit["details"])

    def test_reviewed_correction_invalidates_review_but_preserves_row(self):
        self.add_sleep_event(1)
        self.add_acknowledgement(1, user_id=7)
        review = self.row(
            "SELECT * FROM acknowledgements WHERE source_id = 1"
        )
        self.login(2, "Admin")
        response = self.client.post(
            self.edit_url(1), data=self.correction_data()
        )
        self.assertEqual(response.status_code, 302)
        invalidated = self.row(
            "SELECT * FROM acknowledgements WHERE acknowledgement_id = ?",
            (review["acknowledgement_id"],)
        )
        self.assertEqual(invalidated["active"], 0)
        self.assertEqual(invalidated["invalidated_by_user_id"], 2)
        self.assertIsNotNone(invalidated["invalidated_at_utc"])
        self.assertEqual(
            invalidated["invalidation_reason"],
            "Sleep corrected by management"
        )

        detail = self.client.get("/manager-review/sleep/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Review required", detail.data)
        self.assertIn(b"Mark as Reviewed", detail.data)

    def test_noop_preserves_source_review_and_writes_no_audit(self):
        self.add_sleep_event(1)
        self.add_acknowledgement(1)
        before = self.row(
            "SELECT * FROM sleep_events WHERE sleep_event_id = 1"
        )
        self.login(2, "Admin")
        response = self.client.post(
            self.edit_url(1),
            data=self.correction_data(
                event_local="2026-08-02T08:30",
                note="Settled after music"
            )
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            b"No changes were detected. The Sleep entry already matches the information entered.",
            response.data,
        )
        self.assertEqual(
            self.row("SELECT * FROM sleep_events WHERE sleep_event_id = 1"),
            before,
        )
        self.assertEqual(
            self.row("SELECT active FROM acknowledgements WHERE source_id = 1")["active"],
            1,
        )
        self.assertEqual(
            self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'management_sleep_event_updated'"
            ),
            [],
        )

    def test_required_note_validation_does_not_write(self):
        self.add_sleep_event(1)
        before = self.row(
            "SELECT * FROM sleep_events WHERE sleep_event_id = 1"
        )
        self.login(2, "Admin")
        response = self.client.post(
            self.edit_url(1),
            data=self.correction_data(note="   ")
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Notes are required.", response.data)
        self.assertEqual(
            self.row("SELECT * FROM sleep_events WHERE sleep_event_id = 1"),
            before,
        )

    def test_mismatched_source_client_is_denied(self):
        self.add_sleep_event(1, client_id=2)
        self.login(2, "Admin")
        self.assertEqual(self.client.get(self.edit_url(1)).status_code, 403)
        self.assertEqual(
            self.client.post(
                self.edit_url(1), data=self.correction_data()
            ).status_code,
            403,
        )

    def test_dst_conversion_uses_vancouver_rules(self):
        self.add_sleep_event(1)
        self.login(2, "Admin")
        response = self.client.post(
            self.edit_url(1),
            data=self.correction_data(
                event_local="2026-01-15T09:00", note="Winter correction"
            )
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.row(
                "SELECT event_datetime FROM sleep_events WHERE sleep_event_id = 1"
            )["event_datetime"],
            "2026-01-15T17:00:00Z",
        )

    def test_correction_does_not_create_visible_storyline_duplicate(self):
        self.add_sleep_event(1)
        sleep_review.SleepStorylineReviewTests.add_storyline_event(
            self, "sleep_fell_asleep", 1
        )
        self.login(2, "Admin")
        response = self.client.post(
            self.edit_url(1),
            data=self.correction_data(
                event_local="2026-08-02T09:30",
                note="Corrected sleep note",
            ),
        )
        self.assertEqual(response.status_code, 302)
        # Source-value rehydration is a separate Storyline follow-up.
        storyline = self.client.get(
            "/client/1/storyline?filter=Sleep&date=2026-08-02"
        )
        self.assertEqual(storyline.status_code, 200)
        self.assertEqual(storyline.data.count(b'class="storyline-event"'), 1)
        self.assertNotIn(b"management_sleep_event_updated", storyline.data)
        self.assertEqual(
            self.row(
                "SELECT storyline_visible FROM activity_log "
                "WHERE activity_type = 'management_sleep_event_updated'"
            )["storyline_visible"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
