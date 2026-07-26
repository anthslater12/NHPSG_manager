import sqlite3
import unittest

from tests import test_food_fluid_checkpoint_4 as checkpoint_4


class FoodFluidCheckpoint5Tests(unittest.TestCase):
    setUp = checkpoint_4.FoodFluidCheckpoint4Tests.setUp
    tearDown = checkpoint_4.FoodFluidCheckpoint4Tests.tearDown
    connect = checkpoint_4.FoodFluidCheckpoint4Tests.connect
    login = checkpoint_4.FoodFluidCheckpoint4Tests.login
    insert_view = checkpoint_4.FoodFluidCheckpoint4Tests.insert_view
    insert_review = checkpoint_4.FoodFluidCheckpoint4Tests.insert_review
    count_rows = checkpoint_4.FoodFluidCheckpoint4Tests.count_rows

    def insert_recorded_entry(self, item, token, event_at_utc):
        conn = self.connect()
        cursor = conn.execute("""
            INSERT INTO food_fluid_entries (
                shift_id,
                client_id,
                recorded_by_user_id,
                event_at_utc,
                interaction_type,
                item_description,
                outcome,
                physically_thrown,
                additional_details,
                submitted_at_utc,
                submission_token
            )
            VALUES (
                10,
                1,
                4,
                ?,
                'Offered',
                ?,
                'All consumed',
                0,
                'Original detail',
                ?,
                ?
            )
        """, (
            event_at_utc,
            item,
            event_at_utc,
            token,
        ))
        entry_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return entry_id

    def row(self, entry_id):
        conn = self.connect()
        row = conn.execute("""
            SELECT *
            FROM food_fluid_entries
            WHERE food_fluid_entry_id = ?
        """, (entry_id,)).fetchone()
        result = dict(row) if row is not None else None
        conn.close()
        return result

    def void(self, entry_id, reason="Incorrect entry", **kwargs):
        return self.client.post(
            f"/manager-review/food-fluid/{entry_id}/void",
            data={"void_reason": reason},
            **kwargs,
        )

    def test_admin_manager_and_director_are_authorized_from_database(self):
        entry_ids = (
            1,
            2,
            self.insert_recorded_entry(
                "Director item",
                "CHECKPOINT-FIVE-DIRECTOR",
                "2024-01-15T19:00:00Z",
            ),
        )
        for user_id, entry_id in zip((1, 2, 3), entry_ids):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Support Worker")
                self.assertEqual(
                    self.client.get(
                        f"/manager-review/food-fluid/{entry_id}/void"
                    ).status_code,
                    200,
                )
                response = self.void(entry_id)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    self.row(entry_id)["voided_by_user_id"],
                    user_id,
                )

    def test_unauthorized_inactive_missing_and_unknown_roles_are_rejected(self):
        conn = self.connect()
        conn.execute("""
            INSERT INTO users (
                user_id,
                username,
                password_hash,
                full_name,
                role,
                active
            )
            VALUES (7, 'unknown', 'x', 'Unknown User', 'Contractor', 1)
        """)
        conn.commit()
        conn.close()

        for user_id in (4, 5, 6, 7, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                self.assertEqual(
                    self.client.get(
                        "/manager-review/food-fluid/1/void"
                    ).status_code,
                    403,
                )
                self.assertEqual(self.void(1).status_code, 403)
                self.assertEqual(self.row(1)["status"], "Recorded")
                self.assertEqual(self.count_rows("activity_log"), 0)

    def test_confirmation_requires_login_is_read_only_and_escaped(self):
        self.assertEqual(
            self.client.get(
                "/manager-review/food-fluid/1/void"
            ).status_code,
            302,
        )

        self.login(1)
        response = self.client.get("/manager-review/food-fluid/1/void")
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.row(1)["status"], "Recorded")
        self.assertEqual(self.count_rows("activity_log"), 0)
        self.assertIn("Confirm Food &amp; Fluid Void", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn('name="void_reason"', text)
        self.assertIn("Confirm Void", text)

    def test_reason_is_mandatory_nonblank_and_trimmed(self):
        self.login(1)
        for data in ({}, {"void_reason": ""}, {"void_reason": " \t\r\n "}):
            with self.subTest(data=data):
                response = self.client.post(
                    "/manager-review/food-fluid/1/void",
                    data=data,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.row(1)["status"], "Recorded")
                self.assertEqual(self.count_rows("activity_log"), 0)

        response = self.void(1, "  Corrected reason  ")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.row(1)["void_reason"], "Corrected reason")

    def test_unexpected_duplicate_and_audit_fields_are_rejected(self):
        self.login(1)
        invalid_payloads = (
            {"void_reason": "Reason", "client_id": "999"},
            {"void_reason": "Reason", "shift_id": "999"},
            {"void_reason": "Reason", "status": "Voided"},
            {"void_reason": "Reason", "voided_by_user_id": "4"},
            {"void_reason": "Reason", "voided_at_utc": "1900-01-01"},
            {"void_reason": ["First", "Second"]},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post(
                    "/manager-review/food-fluid/1/void",
                    data=payload,
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.row(1)["status"], "Recorded")
                self.assertEqual(self.count_rows("activity_log"), 0)

    def test_void_metadata_timestamp_original_data_and_activity_are_exact(self):
        before = self.row(1)
        original_fields = (
            "shift_id",
            "client_id",
            "recorded_by_user_id",
            "event_at_utc",
            "interaction_type",
            "item_description",
            "outcome",
            "physically_thrown",
            "additional_details",
            "submitted_at_utc",
            "submission_token",
        )

        self.login(2, session_role="Director")
        response = self.void(1, "  Wrong client meal  ")
        self.assertEqual(response.status_code, 302)
        after = self.row(1)

        self.assertEqual(after["status"], "Voided")
        self.assertEqual(after["voided_by_user_id"], 2)
        self.assertRegex(
            after["voided_at_utc"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )
        self.assertEqual(after["void_reason"], "Wrong client meal")
        for field in original_fields:
            self.assertEqual(after[field], before[field], field)

        conn = self.connect()
        activity = conn.execute("""
            SELECT *
            FROM activity_log
            WHERE activity_type = 'food_fluid_entry_voided'
        """).fetchone()
        conn.close()
        self.assertEqual(activity["activity_class"], "FOOD_FLUID")
        self.assertEqual(activity["summary"], "Food & Fluid entry voided")
        self.assertEqual(activity["user_id"], 2)
        self.assertEqual(activity["client_id"], 1)
        self.assertEqual(activity["shift_id"], 10)
        self.assertEqual(activity["related_table"], "food_fluid_entries")
        self.assertEqual(activity["related_id"], 1)
        self.assertIn("Wrong client meal", activity["details"])

    def test_activity_failure_rolls_back_entry_and_log(self):
        conn = self.connect()
        conn.execute("""
            CREATE TRIGGER reject_food_fluid_void_log
            BEFORE INSERT ON activity_log
            WHEN NEW.activity_type = 'food_fluid_entry_voided'
            BEGIN
                SELECT RAISE(ABORT, 'void log failed');
            END
        """)
        conn.commit()
        conn.close()

        before = self.row(1)
        self.login(1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.void(1)
        self.assertEqual(self.row(1), before)
        self.assertEqual(self.count_rows("activity_log"), 0)

    def test_repeated_and_already_voided_requests_are_conflicts(self):
        self.login(1)
        self.assertEqual(self.void(1).status_code, 302)
        after_first = self.row(1)
        activity_count = self.count_rows("activity_log")

        self.assertEqual(self.void(1, "Second reason").status_code, 409)
        self.assertEqual(self.row(1), after_first)
        self.assertEqual(self.count_rows("activity_log"), activity_count)

        self.assertEqual(self.void(3).status_code, 409)
        self.assertEqual(self.row(3)["void_reason"], "Entered in error")
        self.assertEqual(self.count_rows("activity_log"), activity_count)

    def test_conditional_update_rejects_stale_state_and_rolls_back(self):
        conn = self.connect()
        conn.execute("""
            CREATE TRIGGER simulate_stale_food_fluid_void
            BEFORE UPDATE OF status ON food_fluid_entries
            WHEN OLD.food_fluid_entry_id = 1
            BEGIN
                UPDATE food_fluid_entries
                SET
                    status = 'Voided',
                    voided_by_user_id = 3,
                    voided_at_utc = '2026-07-25T20:00:00Z',
                    void_reason = 'Concurrent void'
                WHERE food_fluid_entry_id = OLD.food_fluid_entry_id;
                SELECT RAISE(IGNORE);
            END
        """)
        conn.commit()
        conn.close()

        before = self.row(1)
        self.login(1)
        self.assertEqual(self.void(1).status_code, 409)
        self.assertEqual(self.row(1), before)
        self.assertEqual(self.count_rows("activity_log"), 0)

    def test_worker_and_management_views_show_void_metadata_without_worker_control(self):
        self.login(1)
        self.assertEqual(self.void(1, "<reason> & corrected").status_code, 302)

        management = self.client.get(
            "/manager-review/food-fluid/1"
        ).get_data(as_text=True)
        self.assertIn("Voided", management)
        self.assertIn("Admin User", management)
        self.assertIn("&lt;reason&gt; &amp; corrected", management)
        self.assertNotIn("Void this entry", management)
        self.assertIn("2026-", management)

        self.login(4)
        worker = self.client.get(
            "/shift/10/food-fluid"
        ).get_data(as_text=True)
        self.assertIn("Voided", worker)
        self.assertIn("Admin User", worker)
        self.assertIn("&lt;reason&gt; &amp; corrected", worker)
        self.assertNotIn("/void", worker)
        self.assertNotIn("Confirm Void", worker)

    def test_existing_view_and_review_history_remain_intact(self):
        self.insert_view(1, user_id=1)
        self.insert_review(1, user_id=2)
        self.login(3)
        self.assertEqual(self.void(1).status_code, 302)

        conn = self.connect()
        views = conn.execute("""
            SELECT user_id
            FROM activity_log
            WHERE activity_type = 'food_fluid_entry_viewed'
        """).fetchall()
        reviews = conn.execute("""
            SELECT user_id
            FROM acknowledgements
            WHERE source_table = 'food_fluid_entries'
              AND source_id = 1
              AND acknowledgement_type = 'Review'
              AND active = 1
        """).fetchall()
        conn.close()
        self.assertEqual([row["user_id"] for row in views], [1])
        self.assertEqual([row["user_id"] for row in reviews], [2])

        detail = self.client.get(
            "/manager-review/food-fluid/1"
        ).get_data(as_text=True)
        self.assertIn("Admin User", detail)
        self.assertIn("Manager User", detail)
        self.assertIn("Reviewed", detail)

    def test_missing_entry_is_not_found_without_writes(self):
        self.login(1)
        self.assertEqual(
            self.client.get(
                "/manager-review/food-fluid/999/void"
            ).status_code,
            404,
        )
        self.assertEqual(self.void(999).status_code, 404)
        self.assertEqual(self.count_rows("activity_log"), 0)


if __name__ == "__main__":
    unittest.main()
