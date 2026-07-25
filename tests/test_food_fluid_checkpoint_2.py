import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import add_food_fluid_entries_table as migration
import app


class FoodFluidCheckpointTwoTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(
            self.temporary_directory.name,
            "food_fluid_checkpoint_two.db"
        )
        self.original_database = app.DB_NAME
        app.DB_NAME = self.database_path
        app.app.config.update(TESTING=True)

        conn = sqlite3.connect(self.database_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                scheduled_start_time TEXT,
                scheduled_end_time TEXT,
                created_at TEXT,
                closed_at TEXT
            );

            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT NOT NULL,
                actual_end_time TEXT,
                sign_on_at TEXT,
                sign_off_at TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                start_checklist_completed INTEGER DEFAULT 0,
                end_checklist_completed INTEGER DEFAULT 0
            );

            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT,
                activity_class TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                user_id INTEGER,
                client_id INTEGER,
                shift_id INTEGER,
                related_table TEXT,
                related_id INTEGER,
                summary TEXT NOT NULL,
                details TEXT,
                created_at TEXT,
                success INTEGER DEFAULT 1
            );

            INSERT INTO users
                (user_id, username, password_hash, full_name, role, active)
            VALUES
                (1, 'worker', 'x', 'Worker One', 'Support Worker', 1),
                (2, 'manager', 'x', 'Manager', 'Program Manager', 1),
                (3, 'inactive', 'x', 'Inactive Worker', 'Support Worker', 0),
                (4, 'consultant', 'x', 'Consultant', 'Behaviour Consultant', 1),
                (5, 'no_shift', 'x', 'No Shift Worker', 'Support Worker', 1),
                (6, 'signed_off', 'x', 'Signed Off Worker', 'Support Worker', 1),
                (7, 'unknown', 'x', 'Unknown Role', 'Contractor', 1);

            INSERT INTO clients
                (client_id, client_name, active)
            VALUES
                (1, 'Active Client', 1),
                (2, 'Inactive Client', 0),
                (3, 'Other Active Client', 1);

            INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status)
            VALUES
                (10, 1, '2024-01-15', 'Day', 'Open'),
                (11, 1, '2024-01-15', 'Overnight', 'Open'),
                (12, 1, '2024-01-15', 'Day', 'Closed'),
                (13, 2, '2024-01-15', 'Day', 'Open'),
                (14, 3, '2024-01-15', 'Afternoon', 'Open'),
                (15, 1, '2024-11-02', 'Overnight', 'Open');

            INSERT INTO shift_staff
                (shift_staff_id, shift_id, user_id, actual_start_time, active)
            VALUES
                (100, 10, 1, '07:00', 1),
                (101, 11, 1, '23:00', 1),
                (102, 12, 1, '07:00', 1),
                (103, 13, 1, '07:00', 1),
                (104, 14, 1, '15:00', 1),
                (105, 10, 2, '07:00', 1),
                (106, 10, 3, '07:00', 1),
                (107, 10, 4, '07:00', 1),
                (108, 10, 6, '07:00', 0),
                (109, 10, 7, '07:00', 1),
                (110, 15, 1, '23:00', 1);
        """)
        migration.migrate(conn)
        conn.close()

        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.original_database
        self.temporary_directory.cleanup()

    def login(self, user_id, session_role="Untrusted Session Role"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = session_role
            session["full_name"] = "Untrusted Session Name"

    def payload(self, **overrides):
        values = {
            "event_local": "2024-01-15T09:00",
            "interaction_type": "Offered",
            "item_description": "Toast and water",
            "outcome": "All consumed",
            "additional_details": "",
        }
        values.update(overrides)
        return values

    def post(self, shift_id=10, **overrides):
        return self.client.post(
            f"/shift/{shift_id}/food-fluid/new",
            data=self.payload(**overrides),
            follow_redirects=False
        )

    def rows(self, table):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {table} ORDER BY 1"
        ).fetchall()
        conn.close()
        return rows

    def counts(self):
        return (
            len(self.rows("food_fluid_entries")),
            len(self.rows("activity_log")),
        )

    def test_valid_daytime_submission_and_exact_activity_log(self):
        self.login(1, session_role="Director")

        response = self.post()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.location,
            "/shift/10/food-fluid/new?created=1"
        )
        entry = self.rows("food_fluid_entries")[0]
        self.assertEqual(entry["shift_id"], 10)
        self.assertEqual(entry["client_id"], 1)
        self.assertEqual(entry["recorded_by_user_id"], 1)
        self.assertEqual(entry["event_at_utc"], "2024-01-15T17:00:00Z")
        self.assertEqual(entry["interaction_type"], "Offered")
        self.assertEqual(entry["item_description"], "Toast and water")
        self.assertEqual(entry["outcome"], "All consumed")
        self.assertEqual(entry["physically_thrown"], 0)
        self.assertEqual(entry["status"], "Recorded")

        activity = self.rows("activity_log")[0]
        self.assertEqual(activity["activity_class"], "FOOD_FLUID")
        self.assertEqual(
            activity["activity_type"],
            "food_fluid_entry_created"
        )
        self.assertEqual(activity["user_id"], 1)
        self.assertEqual(activity["client_id"], 1)
        self.assertEqual(activity["shift_id"], 10)
        self.assertEqual(activity["related_table"], "food_fluid_entries")
        self.assertEqual(
            activity["related_id"],
            entry["food_fluid_entry_id"]
        )
        self.assertEqual(activity["summary"], "Food & Fluid entry recorded")
        self.assertNotIn("Toast and water", activity["details"])

    def test_valid_overnight_before_midnight(self):
        self.login(1)
        response = self.post(
            11,
            event_local="2024-01-15T23:30"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.rows("food_fluid_entries")[0]["event_at_utc"],
            "2024-01-16T07:30:00Z"
        )

    def test_valid_overnight_after_midnight_uses_previous_start_date(self):
        self.login(1)
        response = self.post(
            11,
            event_local="2024-01-16T01:30"
        )
        self.assertEqual(response.status_code, 302)
        entry = self.rows("food_fluid_entries")[0]
        self.assertEqual(entry["shift_id"], 11)
        self.assertEqual(entry["event_at_utc"], "2024-01-16T09:30:00Z")

    def test_current_shift_date_uses_start_date_after_midnight(self):
        after_midnight = datetime(
            2026, 7, 26, 1, 30,
            tzinfo=app.VANCOUVER_TIMEZONE
        )
        before_midnight = datetime(
            2026, 7, 25, 23, 30,
            tzinfo=app.VANCOUVER_TIMEZONE
        )
        self.assertEqual(
            app.get_current_shift_type(after_midnight),
            "Overnight"
        )
        self.assertEqual(
            app.get_current_shift_date(after_midnight).isoformat(),
            "2026-07-25"
        )
        self.assertEqual(
            app.get_current_shift_date(before_midnight).isoformat(),
            "2026-07-25"
        )

    def test_event_time_outside_shift_window_is_rejected(self):
        self.login(1)
        response = self.post(event_local="2024-01-15T15:00")
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"within the selected shift", response.data)
        self.assertEqual(self.counts(), (0, 0))

    def test_repeated_fall_back_time_requires_explicit_choice(self):
        self.login(1)
        missing = self.post(
            15,
            event_local="2024-11-03T01:30"
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(self.counts(), (0, 0))

        accepted = self.post(
            15,
            event_local="2024-11-03T01:30",
            repeated_hour_choice="second"
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(
            self.rows("food_fluid_entries")[0]["event_at_utc"],
            "2024-11-03T09:30:00Z"
        )

    def test_non_support_worker_roles_are_rejected_from_get_and_post(self):
        for user_id in (2, 4, 7):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Support Worker")
                self.assertEqual(
                    self.client.get(
                        "/shift/10/food-fluid/new"
                    ).status_code,
                    403
                )
                self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.counts(), (0, 0))

    def test_inactive_worker_is_rejected(self):
        self.login(3, session_role="Support Worker")
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.counts(), (0, 0))

    def test_worker_without_active_participation_is_rejected(self):
        for user_id in (5, 6):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.counts(), (0, 0))

    def test_authorization_is_rechecked_on_post(self):
        self.login(1)
        self.assertEqual(
            self.client.get("/shift/10/food-fluid/new").status_code,
            200
        )
        conn = sqlite3.connect(self.database_path)
        conn.execute("""
            UPDATE shift_staff
            SET active = 0
            WHERE shift_id = 10 AND user_id = 1
        """)
        conn.commit()
        conn.close()

        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.counts(), (0, 0))

    def test_closed_shift_is_rejected(self):
        self.login(1)
        self.assertEqual(self.post(12).status_code, 403)
        self.assertEqual(self.counts(), (0, 0))

    def test_inactive_client_is_rejected(self):
        self.login(1)
        self.assertEqual(self.post(13).status_code, 403)
        self.assertEqual(self.counts(), (0, 0))

    def test_browser_client_shift_identity_and_timestamps_are_rejected(self):
        self.login(1)
        unapproved_fields = {
            "client_id": "3",
            "shift_id": "14",
            "recorded_by_user_id": "2",
            "submitted_at_utc": "2000-01-01T00:00:00Z",
            "status": "Voided",
            "submission_token": "browser-token",
        }
        for field_name, value in unapproved_fields.items():
            with self.subTest(field_name=field_name):
                self.assertEqual(
                    self.post(**{field_name: value}).status_code,
                    400
                )
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_interaction_type_is_rejected(self):
        self.login(1)
        self.assertEqual(
            self.post(interaction_type="Provided").status_code,
            400
        )
        self.assertEqual(self.counts(), (0, 0))

    def test_blank_ascii_whitespace_item_is_rejected(self):
        self.login(1)
        for item_description in ("", "   ", "\t\r\n\v\f"):
            with self.subTest(item_description=item_description):
                self.assertEqual(
                    self.post(
                        item_description=item_description
                    ).status_code,
                    400
                )
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_outcome_is_rejected(self):
        self.login(1)
        self.assertEqual(
            self.post(outcome="Mostly consumed").status_code,
            400
        )
        self.assertEqual(self.counts(), (0, 0))

    def test_item_not_available_requires_requested(self):
        self.login(1)
        self.assertEqual(
            self.post(
                interaction_type="Offered",
                outcome="Item not available"
            ).status_code,
            400
        )
        self.assertEqual(self.counts(), (0, 0))

        self.assertEqual(
            self.post(
                interaction_type="Requested",
                outcome="Item not available"
            ).status_code,
            302
        )

    def test_physically_thrown_requires_partial_or_refused_outcome(self):
        self.login(1)
        self.assertEqual(
            self.post(
                outcome="All consumed",
                physically_thrown="1"
            ).status_code,
            400
        )
        self.assertEqual(self.counts(), (0, 0))

        self.assertEqual(
            self.post(
                outcome="Refused",
                physically_thrown="1"
            ).status_code,
            302
        )
        self.assertEqual(
            self.rows("food_fluid_entries")[0]["physically_thrown"],
            1
        )

    def test_checkbox_and_additional_details_have_distinct_labels(self):
        self.login(1)
        response = self.client.get("/shift/10/food-fluid/new")
        self.assertEqual(response.status_code, 200)
        markup = response.get_data(as_text=True)

        self.assertRegex(
            markup,
            re.compile(
                r'<div>\s*'
                r'<label\s+for="physically_thrown">\s*'
                r'<input\s+type="checkbox"\s+'
                r'id="physically_thrown"\s+'
                r'name="physically_thrown"\s+'
                r'value="1"\s+style="width:auto;"[^>]*>\s*'
                r'Physically thrown\s*</label>\s*</div>\s*'
                r'<label\s+for="additional_details">',
                re.DOTALL
            )
        )
        self.assertRegex(
            markup,
            re.compile(
                r'<label\s+for="additional_details">\s*'
                r'Additional details\s*</label>\s*'
                r'<textarea\s+id="additional_details"\s+'
                r'name="additional_details"',
                re.DOTALL
            )
        )
        self.assertEqual(markup.count("Physically thrown"), 1)
        self.assertEqual(markup.count("Additional details"), 1)

    def test_server_generates_submission_timestamp_and_unique_tokens(self):
        self.login(1)
        before = datetime.now(timezone.utc).replace(microsecond=0)
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(
            self.post(
                event_local="2024-01-15T10:00",
                item_description="Juice"
            ).status_code,
            302
        )
        after = datetime.now(timezone.utc).replace(microsecond=0)

        entries = self.rows("food_fluid_entries")
        tokens = [entry["submission_token"] for entry in entries]
        self.assertEqual(len(set(tokens)), 2)
        for token in tokens:
            self.assertRegex(token, r"^[A-Za-z0-9_-]{32,128}$")

        for entry in entries:
            submitted = datetime.strptime(
                entry["submitted_at_utc"],
                "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
            self.assertLessEqual(before, submitted)
            self.assertLessEqual(submitted, after + timedelta(seconds=1))

    def test_failed_validation_creates_no_entry_or_activity(self):
        self.login(1)
        response = self.post(
            interaction_type="Offered",
            outcome="Item not available"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.counts(), (0, 0))

    def test_activity_failure_rolls_back_entry(self):
        self.login(1)
        conn = sqlite3.connect(self.database_path)
        conn.execute("""
            CREATE TRIGGER reject_food_fluid_activity
            BEFORE INSERT ON activity_log
            BEGIN
                SELECT RAISE(ABORT, 'forced activity failure');
            END
        """)
        conn.commit()
        conn.close()

        with self.assertRaises(sqlite3.IntegrityError):
            self.post()
        self.assertEqual(self.counts(), (0, 0))

    def test_free_text_is_normally_escaped_when_form_is_redisplayed(self):
        self.login(1)
        response = self.post(
            item_description="<script>alert(1)</script>",
            outcome="invalid",
            additional_details="<b>private</b>"
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)
        self.assertIn(
            b"&lt;script&gt;alert(1)&lt;/script&gt;",
            response.data
        )
        self.assertNotIn(b"<b>private</b>", response.data)
        self.assertIn(b"&lt;b&gt;private&lt;/b&gt;", response.data)


if __name__ == "__main__":
    unittest.main()
