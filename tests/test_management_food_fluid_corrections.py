import sqlite3
import unittest

import app
from tests import test_food_fluid_corrections as worker_corrections


class ManagementFoodFluidCorrectionTests(unittest.TestCase):
    def setUp(self):
        worker_corrections.FoodFluidCorrectionTests.setUp(self)
        conn = self.connect()
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
            conn.execute(
                "ALTER TABLE activity_log ADD COLUMN event_datetime TEXT"
            )
            conn.commit()
        finally:
            conn.close()

    tearDown = worker_corrections.FoodFluidCorrectionTests.tearDown
    connect = worker_corrections.FoodFluidCorrectionTests.connect
    login = worker_corrections.FoodFluidCorrectionTests.login

    def row(self, entry_id):
        conn = self.connect()
        try:
            row = conn.execute(
                "SELECT * FROM food_fluid_entries "
                "WHERE food_fluid_entry_id = ?",
                (entry_id,),
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def rows(self, sql, parameters=()):
        conn = self.connect()
        try:
            return [dict(row) for row in conn.execute(sql, parameters)]
        finally:
            conn.close()

    def payload(self, **overrides):
        payload = {
            "event_local": "2024-01-15T09:30",
            "repeated_hour_choice": "",
            "interaction_type": "Requested",
            "item_description": "Management corrected meal",
            "outcome": "Partially consumed",
            "physically_thrown": "1",
            "additional_details": "Management corrected details",
        }
        payload.update(overrides)
        return payload

    def edit_url(self, entry_id=1):
        return f"/manager-review/food-fluid/{entry_id}/correct"

    def test_management_roles_can_get_and_post_correction(self):
        for user_id, role in ((1, "Admin"), (2, "Program Manager"), (3, "Director")):
            with self.subTest(role=role):
                self.login(user_id, session_role=role)
                self.assertEqual(self.client.get(self.edit_url()).status_code, 200)
                response = self.client.post(
                    self.edit_url(),
                    data=self.payload(item_description=f"{role} meal"),
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    response.location,
                    "/manager-review/food-fluid/1",
                )

    def test_detail_correct_entry_is_management_only(self):
        self.login(2, session_role="Program Manager")
        manager_page = self.client.get("/manager-review/food-fluid/1")
        self.assertEqual(manager_page.status_code, 200)
        self.assertIn(b"Correct Entry", manager_page.data)
        self.assertIn(b"/manager-review/food-fluid/1/correct", manager_page.data)

        self.login(5, session_role="Behaviour Consultant")
        consultant_page = self.client.get("/manager-review/food-fluid/1")
        self.assertEqual(consultant_page.status_code, 200)
        self.assertNotIn(b"Correct Entry", consultant_page.data)

    def test_non_management_users_inactive_users_and_session_spoof_are_denied(self):
        for user_id, session_role in (
            (5, "Behaviour Consultant"),
            (4, "Support Worker"),
            (6, "Admin"),
            (4, "Admin"),
        ):
            with self.subTest(user_id=user_id, session_role=session_role):
                self.login(user_id, session_role=session_role)
                self.assertEqual(self.client.get(self.edit_url()).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        self.edit_url(), data=self.payload()
                    ).status_code,
                    403,
                )

    def test_database_management_role_overrides_spoofed_session_role(self):
        self.login(2, session_role="Support Worker")
        self.assertEqual(self.client.get(self.edit_url()).status_code, 200)
        response = self.client.post(
            self.edit_url(),
            data=self.payload(item_description="Database role correction"),
        )
        self.assertEqual(response.status_code, 302)

    def test_historical_signed_off_inactive_assignment_and_closed_shift_are_allowed(self):
        conn = self.connect()
        conn.execute(
            "UPDATE shift_staff SET active = 0, actual_end_at_utc = ? "
            "WHERE shift_id = 10 AND user_id = 4",
            ("2024-01-15T18:00:00Z",),
        )
        conn.execute("UPDATE shifts SET status = 'Closed' WHERE shift_id = 10")
        conn.commit()
        conn.close()

        self.login(2, session_role="Program Manager")
        response = self.client.post(
            self.edit_url(),
            data=self.payload(additional_details="Closed shift correction"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row(1)["item_description"], "Management corrected meal")

    def test_cancelled_shift_and_voided_entry_are_denied(self):
        conn = self.connect()
        conn.execute("UPDATE shifts SET status = 'Cancelled' WHERE shift_id = 10")
        conn.commit()
        conn.close()
        self.login(2, session_role="Program Manager")
        self.assertEqual(self.client.get(self.edit_url(1)).status_code, 403)
        self.assertEqual(
            self.client.post(self.edit_url(1), data=self.payload()).status_code,
            403,
        )

        conn = self.connect()
        conn.execute("UPDATE shifts SET status = 'Open' WHERE shift_id = 10")
        conn.commit()
        conn.close()
        self.assertEqual(self.client.get(self.edit_url(3)).status_code, 403)
        self.assertEqual(
            self.client.post(self.edit_url(3), data=self.payload()).status_code,
            403,
        )

    def test_mismatched_source_client_is_denied(self):
        conn = self.connect()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO clients (client_id, client_name, active) "
            "VALUES (2, 'Other Client', 1)"
        )
        conn.execute(
            "UPDATE food_fluid_entries SET client_id = 2 "
            "WHERE food_fluid_entry_id = 1"
        )
        conn.commit()
        conn.close()

        self.login(2, session_role="Program Manager")
        self.assertEqual(self.client.get(self.edit_url()).status_code, 403)
        self.assertEqual(
            self.client.post(self.edit_url(), data=self.payload()).status_code,
            403,
        )

    def test_missing_source_client_is_denied(self):
        conn = self.connect()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "UPDATE food_fluid_entries SET client_id = 99 "
            "WHERE food_fluid_entry_id = 1"
        )
        conn.commit()
        conn.close()

        self.login(2, session_role="Program Manager")
        self.assertEqual(self.client.get(self.edit_url()).status_code, 403)
        self.assertEqual(
            self.client.post(self.edit_url(), data=self.payload()).status_code,
            403,
        )

    def test_management_correction_preserves_source_invalidates_review_and_audits(self):
        conn = self.connect()
        conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id, acknowledged_at,
                acknowledgement_type, active
            ) VALUES ('food_fluid_entries', 1, 2,
                      '2024-01-15T20:00:00Z', 'Review', 1)
        """)
        conn.commit()
        conn.close()
        before = self.row(1)

        self.login(2, session_role="Program Manager")
        response = self.client.post(
            self.edit_url(), data=self.payload()
        )
        self.assertEqual(response.status_code, 302)
        after = self.row(1)
        self.assertEqual(after["food_fluid_entry_id"], before["food_fluid_entry_id"])
        self.assertEqual(after["client_id"], before["client_id"])
        self.assertEqual(after["shift_id"], before["shift_id"])
        self.assertEqual(after["recorded_by_user_id"], before["recorded_by_user_id"])
        self.assertEqual(after["submitted_at_utc"], before["submitted_at_utc"])
        self.assertEqual(after["submission_token"], before["submission_token"])
        self.assertEqual(after["event_at_utc"], "2024-01-15T17:30:00Z")

        review = self.rows(
            "SELECT * FROM acknowledgements "
            "WHERE source_table = 'food_fluid_entries' AND source_id = 1"
        )[0]
        self.assertEqual(review["active"], 0)
        self.assertEqual(review["invalidated_by_user_id"], 2)
        self.assertEqual(
            review["invalidation_reason"],
            "Food & Fluid entry corrected by management",
        )
        audit = self.rows(
            "SELECT * FROM activity_log "
            "WHERE activity_type = 'management_food_fluid_event_updated'"
        )[0]
        self.assertEqual(audit["user_id"], 2)
        self.assertEqual(audit["related_table"], "food_fluid_entries")
        self.assertEqual(audit["related_id"], 1)
        self.assertEqual(audit["storyline_visible"], 0)
        self.assertIn("Original recorder user ID: 4", audit["details"])
        self.assertIn("Previous values:", audit["details"])
        self.assertIn("New values:", audit["details"])
        self.assertIn("Invalidated Review acknowledgement IDs:", audit["details"])

        detail = self.client.get("/manager-review/food-fluid/1")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Not yet reviewed.", detail.data)
        self.assertIn(b"Mark Reviewed", detail.data)

    def test_noop_preserves_review_source_and_writes_no_management_audit(self):
        conn = self.connect()
        conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id, acknowledged_at,
                acknowledgement_type, active
            ) VALUES ('food_fluid_entries', 1, 2,
                      '2024-01-15T20:00:00Z', 'Review', 1)
        """)
        conn.commit()
        conn.close()
        before = self.row(1)

        self.login(2, session_role="Program Manager")
        response = self.client.post(
            self.edit_url(),
            data={
                "event_local": "2024-01-15T08:00",
                "repeated_hour_choice": "",
                "interaction_type": "Offered",
                "item_description": "<script>alert(1)</script>",
                "outcome": "All consumed",
                "additional_details": "<b>private detail</b>",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            b"No changes were detected. The Food &amp; Fluid entry already "
            b"matches the information entered.",
            response.data,
        )
        self.assertEqual(self.row(1), before)
        self.assertEqual(self.rows(
            "SELECT * FROM activity_log "
            "WHERE activity_type = 'management_food_fluid_event_updated'"
        ), [])
        self.assertEqual(
            self.rows(
                "SELECT active FROM acknowledgements "
                "WHERE source_table = 'food_fluid_entries' AND source_id = 1"
            )[0]["active"],
            1,
        )

    def test_retroactive_same_shift_time_and_repeated_dst_hour_are_preserved(self):
        conn = self.connect()
        conn.execute(
            "UPDATE shift_staff SET actual_start_time = '10:00' "
            "WHERE shift_id = 10 AND user_id = 4"
        )
        conn.commit()
        conn.close()
        self.login(2, session_role="Program Manager")
        response = self.client.post(
            self.edit_url(),
            data=self.payload(event_local="2024-01-15T09:30"),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row(1)["event_at_utc"], "2024-01-15T17:30:00Z")

        conn = self.connect()
        conn.execute(
            "UPDATE shifts SET shift_date = '2024-11-02', "
            "shift_type = 'Overnight' WHERE shift_id = 10"
        )
        conn.execute(
            "UPDATE shift_staff SET actual_start_time = '00:00' "
            "WHERE shift_id = 10 AND user_id = 4"
        )
        conn.execute(
            "UPDATE food_fluid_entries SET event_at_utc = '2024-11-03T09:30:00Z' "
            "WHERE food_fluid_entry_id = 1"
        )
        conn.commit()
        conn.close()

        form = self.client.get(self.edit_url())
        self.assertEqual(form.status_code, 200)
        self.assertIn(b'value="2024-11-03T01:30"', form.data)
        self.assertIn(b'value="second"', form.data)
        response = self.client.post(
            self.edit_url(),
            data=self.payload(
                event_local="2024-11-03T01:30",
                repeated_hour_choice="second",
                additional_details="DST management correction",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row(1)["event_at_utc"], "2024-11-03T09:30:00Z")

    def test_management_audit_is_hidden_from_storyline_and_current_views_reflect_source(self):
        conn = self.connect()
        conn.execute("""
            INSERT INTO activity_log (
                activity_datetime, activity_class, activity_type, user_id,
                client_id, shift_id, related_table, related_id, summary,
                details, success, storyline_visible, event_datetime
            ) VALUES (
                '2024-01-15 08:00:00', 'FOOD_FLUID',
                'food_fluid_entry_created', 4, 1, 10,
                'food_fluid_entries', 1, 'Offered - <script>alert(1)</script>',
                'Outcome: All consumed', 1, 1, '2024-01-15T16:00:00Z'
            )
        """)
        conn.commit()
        conn.close()

        self.login(2, session_role="Program Manager")
        response = self.client.post(
            self.edit_url(),
            data=self.payload(item_description="Current source item"),
        )
        self.assertEqual(response.status_code, 302)

        conn = self.connect()
        try:
            entries = app.get_food_fluid_management_entries(conn)
        finally:
            conn.close()
        current = next(
            entry for entry in entries
            if entry["food_fluid_entry_id"] == 1
        )
        self.assertEqual(current["item_description"], "Current source item")
        self.assertEqual(current["event_local_display"], "2024-01-15 09:30")

        storyline = self.client.get(
            "/client/1/storyline?filter=Food+%26+Fluid&date=2024-01-15"
        )
        self.assertEqual(storyline.status_code, 200)
        self.assertEqual(storyline.data.count(b'class="storyline-event"'), 1)
        self.assertIn(b"Current source item", storyline.data)
        self.assertIn(b"09:30", storyline.data)
        self.assertIn(b"Outcome: Partially consumed", storyline.data)
        self.assertIn(b"Additional details: Management corrected details", storyline.data)
        self.assertNotIn(b"alert(1)", storyline.data)
        self.assertNotIn(b"All consumed", storyline.data)
        self.assertNotIn(b"private detail", storyline.data)
        self.assertNotIn(b"management_food_fluid_event_updated", storyline.data)
        self.assertEqual(
            self.rows(
                "SELECT storyline_visible FROM activity_log "
                "WHERE activity_type = 'management_food_fluid_event_updated'"
            )[0]["storyline_visible"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
