import sqlite3
import unittest

from tests import test_food_fluid_checkpoint_4 as checkpoint_4


class FoodFluidCorrectionTests(unittest.TestCase):
    def setUp(self):
        checkpoint_4.FoodFluidCheckpoint4Tests.setUp(self)
        conn = self.connect()
        conn.execute(
            "UPDATE shift_staff SET actual_start_time = '07:00' "
            "WHERE shift_id = 10 AND user_id = 4"
        )
        conn.execute(
            "ALTER TABLE activity_log "
            "ADD COLUMN storyline_visible INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
        conn.close()

    tearDown = checkpoint_4.FoodFluidCheckpoint4Tests.tearDown
    connect = checkpoint_4.FoodFluidCheckpoint4Tests.connect
    login = checkpoint_4.FoodFluidCheckpoint4Tests.login

    def row(self, entry_id):
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM food_fluid_entries WHERE food_fluid_entry_id = ?",
            (entry_id,),
        ).fetchone()
        conn.close()
        return dict(row)

    def activity_rows(self):
        conn = self.connect()
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM activity_log ORDER BY activity_id"
        ).fetchall()]
        conn.close()
        return rows

    def add_second_worker_to_shift(self):
        conn = self.connect()
        conn.execute("""
            INSERT INTO users
                (user_id, username, password_hash, full_name, role, active)
            VALUES (7, 'second-worker', 'x', 'Second Worker', 'Support Worker', 1)
        """)
        conn.execute("""
            INSERT INTO shift_staff
                (shift_staff_id, shift_id, user_id, actual_start_time, active)
            VALUES (7, 10, 7, '2024-01-15 07:00:00', 1)
        """)
        conn.commit()
        conn.close()

    def correction_payload(self, **overrides):
        payload = {
            "event_local": "2024-01-15T10:00",
            "repeated_hour_choice": "",
            "interaction_type": "Requested",
            "item_description": "Corrected meal",
            "outcome": "Partially consumed",
            "physically_thrown": "1",
            "additional_details": "Corrected details",
        }
        payload.update(overrides)
        return payload

    def test_own_current_shift_entry_shows_correct_and_form(self):
        self.login(4)

        page = self.client.get("/shift/10/food-fluid")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Correct", page.data)
        self.assertIn(b"/shift/10/food-fluid/1/edit", page.data)

        form = self.client.get("/shift/10/food-fluid/1/edit")
        self.assertEqual(form.status_code, 200)
        self.assertIn(b"Correct Food &amp; Fluid Entry", form.data)
        self.assertIn(b"Save Correction", form.data)
        self.assertEqual(
            checkpoint_4.app._food_fluid_edit_values(self.row(1))[
                "repeated_hour_choice"
            ],
            "",
        )

    def test_other_worker_cannot_correct_entry(self):
        self.add_second_worker_to_shift()
        self.login(7)

        page = self.client.get("/shift/10/food-fluid")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"/shift/10/food-fluid/1/edit", page.data)
        self.assertEqual(
            self.client.get("/shift/10/food-fluid/1/edit").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/shift/10/food-fluid/1/edit",
                data=self.correction_payload(),
            ).status_code,
            403,
        )

    def test_reviewed_entry_is_not_correctable(self):
        conn = self.connect()
        conn.execute("""
            INSERT INTO acknowledgements (
                source_table, source_id, user_id, acknowledged_at,
                acknowledgement_type, active
            ) VALUES ('food_fluid_entries', 1, 1,
                      '2026-07-25 12:05:00', 'Review', 1)
        """)
        conn.commit()
        conn.close()
        self.login(4)

        page = self.client.get("/shift/10/food-fluid")
        self.assertNotIn(b"/shift/10/food-fluid/1/edit", page.data)
        self.assertEqual(
            self.client.get("/shift/10/food-fluid/1/edit").status_code,
            403,
        )

    def test_inactive_or_signed_off_worker_cannot_correct(self):
        conn = self.connect()
        conn.execute("UPDATE shift_staff SET active = 0 WHERE user_id = 4")
        conn.commit()
        conn.close()
        self.login(4)
        self.assertEqual(
            self.client.get("/shift/10/food-fluid/1/edit").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/shift/10/food-fluid/1/edit",
                data=self.correction_payload(),
            ).status_code,
            403,
        )

        conn = self.connect()
        conn.execute("UPDATE shift_staff SET active = 1 WHERE user_id = 4")
        conn.execute("UPDATE users SET active = 0 WHERE user_id = 4")
        conn.commit()
        conn.close()
        self.assertEqual(
            self.client.get("/shift/10/food-fluid/1/edit").status_code,
            403,
        )

    def test_manager_cannot_use_correction_route(self):
        self.login(2, session_role="Program Manager")
        self.assertEqual(
            self.client.get("/shift/10/food-fluid/1/edit").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/shift/10/food-fluid/1/edit",
                data=self.correction_payload(),
            ).status_code,
            403,
        )

    def test_voided_entry_cannot_be_corrected(self):
        self.login(4)
        self.assertEqual(
            self.client.get("/shift/10/food-fluid/3/edit").status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/shift/10/food-fluid/3/edit",
                data=self.correction_payload(),
            ).status_code,
            403,
        )

    def test_real_correction_updates_same_row_redirects_and_audits(self):
        self.login(4)
        before = self.row(1)

        response = self.client.post(
            "/shift/10/food-fluid/1/edit",
            data=self.correction_payload(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/shift/10/food-fluid")

        page = self.client.get(response.location)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Food &amp; Fluid correction saved successfully.", page.data)

        after = self.row(1)
        self.assertEqual(after["food_fluid_entry_id"], before["food_fluid_entry_id"])
        self.assertEqual(after["shift_id"], before["shift_id"])
        self.assertEqual(after["client_id"], before["client_id"])
        self.assertEqual(after["recorded_by_user_id"], before["recorded_by_user_id"])
        self.assertEqual(after["status"], "Recorded")
        self.assertEqual(after["event_at_utc"], "2024-01-15T18:00:00Z")
        self.assertEqual(after["interaction_type"], "Requested")
        self.assertEqual(after["item_description"], "Corrected meal")
        self.assertEqual(after["outcome"], "Partially consumed")
        self.assertEqual(after["physically_thrown"], 1)
        self.assertEqual(after["additional_details"], "Corrected details")
        self.assertEqual(after["submitted_at_utc"], before["submitted_at_utc"])
        self.assertEqual(after["submission_token"], before["submission_token"])

        activities = self.activity_rows()
        self.assertEqual(len(activities), 1)
        activity = activities[0]
        self.assertEqual(activity["activity_type"], "food_fluid_event_updated")
        self.assertEqual(activity["user_id"], 4)
        self.assertEqual(activity["client_id"], 1)
        self.assertEqual(activity["shift_id"], 10)
        self.assertEqual(activity["related_table"], "food_fluid_entries")
        self.assertEqual(activity["related_id"], 1)
        self.assertEqual(activity["storyline_visible"], 0)
        self.assertIn("Changed fields:", activity["details"])

    def test_correction_allows_retrospective_event_before_actual_start(self):
        conn = self.connect()
        conn.execute(
            "UPDATE shift_staff SET actual_start_time = '10:00' "
            "WHERE shift_id = 10 AND user_id = 4"
        )
        conn.commit()
        conn.close()

        self.login(4)
        response = self.client.post(
            "/shift/10/food-fluid/1/edit",
            data=self.correction_payload(
                event_local="2024-01-15T09:30",
                item_description="Retrospective correction",
            ),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.row(1)["event_at_utc"],
            "2024-01-15T17:30:00Z",
        )

    def test_ambiguous_event_preserves_fold_when_corrected(self):
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
            "UPDATE food_fluid_entries "
            "SET event_at_utc = '2024-11-03T09:30:00Z' "
            "WHERE food_fluid_entry_id = 1"
        )
        conn.commit()
        conn.close()

        self.login(4)
        form = self.client.get("/shift/10/food-fluid/1/edit")
        self.assertEqual(form.status_code, 200)
        self.assertIn(b'value="2024-11-03T01:30"', form.data)
        self.assertIn(b'value="second"', form.data)

        response = self.client.post(
            "/shift/10/food-fluid/1/edit",
            data={
                "event_local": "2024-11-03T01:30",
                "repeated_hour_choice": "second",
                "interaction_type": "Offered",
                "item_description": "<script>alert(1)</script>",
                "outcome": "All consumed",
                "additional_details": "DST correction",
            },
        )

        self.assertEqual(response.status_code, 302)
        corrected = self.row(1)
        self.assertEqual(
            corrected["event_at_utc"],
            "2024-11-03T09:30:00Z",
        )
        self.assertEqual(corrected["additional_details"], "DST correction")

    def test_noop_does_not_update_or_audit_and_shows_message(self):
        self.login(4)
        before = self.row(1)

        response = self.client.post(
            "/shift/10/food-fluid/1/edit",
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
        self.assertEqual(self.activity_rows(), [])

    def test_required_field_validation_remains_in_place(self):
        self.login(4)
        before = self.row(1)
        response = self.client.post(
            "/shift/10/food-fluid/1/edit",
            data=self.correction_payload(item_description="   "),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Food or beverage item is required.", response.data)
        self.assertEqual(self.row(1), before)
        self.assertEqual(self.activity_rows(), [])


if __name__ == "__main__":
    unittest.main()
