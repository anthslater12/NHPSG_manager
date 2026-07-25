import os
import re
import sqlite3
import tempfile
import unittest

import add_food_fluid_entries_table as migration
import app


class FoodFluidCheckpoint4Tests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(
            self.temporary_directory.name,
            "checkpoint_4.sqlite3",
        )
        self.old_database_name = app.DB_NAME
        self.old_testing = app.app.config.get("TESTING")
        app.DB_NAME = self.database_path
        app.app.config["TESTING"] = True

        conn = self.connect()
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

            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT,
                comment TEXT,
                acknowledgement_type TEXT,
                active INTEGER DEFAULT 1,
                UNIQUE(source_table, source_id, user_id)
            );

            INSERT INTO users
                (user_id, username, password_hash, full_name, role, active)
            VALUES
                (1, 'admin', 'x', 'Admin User', 'Admin', 1),
                (2, 'manager', 'x', 'Manager User', 'Program Manager', 1),
                (3, 'director', 'x', 'Director User', 'Director', 1),
                (4, 'worker', 'x', 'Worker User', 'Support Worker', 1),
                (5, 'consultant', 'x', 'Consultant User',
                    'Behaviour Consultant', 1),
                (6, 'inactive', 'x', 'Inactive Admin', 'Admin', 0);

            INSERT INTO clients
                (client_id, client_name, active)
            VALUES
                (1, 'Client One', 1);

            INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status)
            VALUES
                (10, 1, '2024-01-15', 'Day', 'Open');

            INSERT INTO shift_staff
                (shift_staff_id, shift_id, user_id, actual_start_time, active)
            VALUES
                (1, 10, 4, '2024-01-15 07:00:00', 1);
        """)
        migration.migrate(conn)
        conn.executemany("""
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
                submission_token,
                status,
                voided_by_user_id,
                voided_at_utc,
                void_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            (
                10, 1, 4, "2024-01-15T16:00:00Z", "Offered",
                "<script>alert(1)</script>", "All consumed", 0,
                "<b>private detail</b>", "2024-01-15T16:01:00Z",
                "SECRET-SUBMISSION-TOKEN-ONE", "Recorded", None, None, None,
            ),
            (
                10, 1, 4, "2024-01-15T18:00:00Z", "Requested",
                "Newer Item", "Partially consumed", 1,
                "Some remained", "2024-01-15T18:01:00Z",
                "SECRET-SUBMISSION-TOKEN-TWO", "Recorded", None, None, None,
            ),
            (
                10, 1, 4, "2024-01-15T17:00:00Z", "Offered",
                "Voided Item", "Refused", 0,
                None, "2024-01-15T17:01:00Z",
                "SECRET-SUBMISSION-TOKEN-THREE", "Voided", 1,
                "2024-01-15T17:02:00Z", "Entered in error",
            ),
        ))
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_database_name
        app.app.config["TESTING"] = self.old_testing
        self.temporary_directory.cleanup()

    def connect(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def login(self, user_id, session_role="Support Worker"):
        with self.client.session_transaction() as session:
            session.clear()
            session["user_id"] = user_id
            session["role"] = session_role
            session["full_name"] = "Untrusted Session Name"

    def insert_view(self, entry_id, user_id=1):
        conn = self.connect()
        conn.execute("""
            INSERT INTO activity_log (
                activity_datetime,
                activity_class,
                activity_type,
                user_id,
                client_id,
                shift_id,
                related_table,
                related_id,
                summary,
                success
            )
            VALUES (
                '2026-07-25 12:00:00',
                'FOOD_FLUID',
                'food_fluid_entry_viewed',
                ?,
                1,
                10,
                'food_fluid_entries',
                ?,
                'Food & Fluid entry viewed',
                1
            )
        """, (user_id, entry_id))
        conn.commit()
        conn.close()

    def insert_review(self, entry_id, user_id=1):
        conn = self.connect()
        conn.execute("""
            INSERT INTO acknowledgements (
                source_table,
                source_id,
                user_id,
                acknowledged_at,
                acknowledgement_type,
                active
            )
            VALUES (
                'food_fluid_entries',
                ?,
                ?,
                '2026-07-25 12:05:00',
                'Review',
                1
            )
        """, (entry_id, user_id))
        conn.commit()
        conn.close()

    def count_rows(self, table):
        conn = self.connect()
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        conn.close()
        return count

    def test_authorized_roles_use_database_role_not_session_role(self):
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Support Worker")
                response = self.client.get("/manager-review/food-fluid")
                self.assertEqual(response.status_code, 200)

        self.login(4, session_role="Admin")
        self.assertEqual(
            self.client.get("/manager-review/food-fluid").status_code,
            403,
        )

    def test_every_unauthorized_or_inactive_user_case_has_no_writes(self):
        self.assertEqual(
            self.client.get("/manager-review/food-fluid/1").status_code,
            302,
        )
        for user_id in (4, 5, 6, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                before = self.count_rows("activity_log")
                self.assertEqual(
                    self.client.get("/manager-review/food-fluid/1").status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        "/manager-review/food-fluid/1/review"
                    ).status_code,
                    403,
                )
                self.assertEqual(self.count_rows("activity_log"), before)
                self.assertEqual(self.count_rows("acknowledgements"), 0)

    def test_list_get_is_read_only_and_does_not_record_viewed(self):
        self.login(1)
        before = (
            self.count_rows("activity_log"),
            self.count_rows("acknowledgements"),
        )
        response = self.client.get("/manager-review/food-fluid")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            (
                self.count_rows("activity_log"),
                self.count_rows("acknowledgements"),
            ),
            before,
        )
        self.assertIn(b"Not Viewed", response.data)

    def test_full_detail_records_server_derived_viewer_and_timestamp(self):
        self.login(2, session_role="Director")
        response = self.client.get(
            "/manager-review/food-fluid/1"
            "?viewer=4&viewed_at=1900-01-01"
        )
        self.assertEqual(response.status_code, 200)

        conn = self.connect()
        view = conn.execute("""
            SELECT *
            FROM activity_log
            WHERE activity_type = 'food_fluid_entry_viewed'
        """).fetchone()
        conn.close()
        self.assertEqual(view["user_id"], 2)
        self.assertEqual(view["client_id"], 1)
        self.assertEqual(view["shift_id"], 10)
        self.assertEqual(view["related_table"], "food_fluid_entries")
        self.assertEqual(view["related_id"], 1)
        self.assertRegex(
            view["activity_datetime"],
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
        )
        self.assertIn(b"Manager User", response.data)

    def test_repeated_view_is_per_manager_and_idempotent(self):
        self.login(1)
        self.client.get("/manager-review/food-fluid/1")
        self.client.get("/manager-review/food-fluid/1")
        self.assertEqual(self.count_rows("activity_log"), 1)

        self.login(2)
        self.client.get("/manager-review/food-fluid/1")
        self.assertEqual(self.count_rows("activity_log"), 2)

    def test_get_does_not_review_and_explicit_post_records_review(self):
        self.login(3)
        self.client.get("/manager-review/food-fluid/2")
        self.assertEqual(self.count_rows("acknowledgements"), 0)

        response = self.client.post(
            "/manager-review/food-fluid/2/review",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

        conn = self.connect()
        review = conn.execute(
            "SELECT * FROM acknowledgements"
        ).fetchone()
        activity = conn.execute("""
            SELECT *
            FROM activity_log
            WHERE activity_class = 'ACKNOWLEDGEMENT'
        """).fetchone()
        conn.close()
        self.assertEqual(review["source_table"], "food_fluid_entries")
        self.assertEqual(review["source_id"], 2)
        self.assertEqual(review["user_id"], 3)
        self.assertEqual(review["acknowledgement_type"], "Review")
        self.assertRegex(
            review["acknowledged_at"],
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$",
        )
        self.assertEqual(activity["user_id"], 3)
        self.assertEqual(activity["client_id"], 1)
        self.assertEqual(activity["shift_id"], 10)

    def test_review_rejects_client_fields_and_is_idempotent(self):
        self.login(1)
        response = self.client.post(
            "/manager-review/food-fluid/1/review",
            data={"reviewer": "4", "reviewed_at": "1900-01-01"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count_rows("acknowledgements"), 0)

        self.client.post("/manager-review/food-fluid/1/review")
        self.client.post("/manager-review/food-fluid/1/review")
        self.assertEqual(self.count_rows("acknowledgements"), 1)
        conn = self.connect()
        count = conn.execute("""
            SELECT COUNT(*)
            FROM activity_log
            WHERE activity_class = 'ACKNOWLEDGEMENT'
        """).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_view_logging_failure_rolls_back(self):
        conn = self.connect()
        conn.execute("""
            CREATE TRIGGER reject_food_fluid_view
            BEFORE INSERT ON activity_log
            WHEN NEW.activity_class = 'FOOD_FLUID'
            BEGIN
                SELECT RAISE(ABORT, 'view log failed');
            END
        """)
        conn.commit()
        conn.close()

        self.login(1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.get("/manager-review/food-fluid/1")
        self.assertEqual(self.count_rows("activity_log"), 0)
        self.assertEqual(self.count_rows("acknowledgements"), 0)

    def test_review_logging_failure_rolls_back_acknowledgement(self):
        conn = self.connect()
        conn.execute("""
            CREATE TRIGGER reject_review_log
            BEFORE INSERT ON activity_log
            WHEN NEW.activity_class = 'ACKNOWLEDGEMENT'
            BEGIN
                SELECT RAISE(ABORT, 'review log failed');
            END
        """)
        conn.commit()
        conn.close()

        self.login(1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post("/manager-review/food-fluid/1/review")
        self.assertEqual(self.count_rows("acknowledgements"), 0)

    def test_states_filters_active_voided_and_deterministic_order(self):
        self.insert_view(1)
        self.insert_review(2)
        self.login(1)

        response = self.client.get("/manager-review/food-fluid")
        text = response.get_data(as_text=True)
        self.assertLess(
            text.index("Newer Item"),
            text.index("Voided Item"),
        )
        self.assertLess(
            text.index("Voided Item"),
            text.index("&lt;script&gt;alert(1)&lt;/script&gt;"),
        )
        self.assertIn("Reviewed", text)
        self.assertIn("Viewed – Awaiting Review", text)
        self.assertIn("Voided", text)

        not_viewed = self.client.get(
            "/manager-review/food-fluid?state=not_viewed"
        ).get_data(as_text=True)
        self.assertIn("Voided Item", not_viewed)
        self.assertNotIn("Newer Item", not_viewed)

        awaiting = self.client.get(
            "/manager-review/food-fluid?state=awaiting_review"
        ).get_data(as_text=True)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", awaiting)
        self.assertNotIn("Newer Item", awaiting)

        reviewed = self.client.get(
            "/manager-review/food-fluid?state=reviewed"
        ).get_data(as_text=True)
        self.assertIn("Newer Item", reviewed)
        self.assertNotIn("Voided Item", reviewed)

        voided = self.client.get(
            "/manager-review/food-fluid?state=voided"
        ).get_data(as_text=True)
        self.assertIn("Voided Item", voided)
        self.assertNotIn("Newer Item", voided)

    def test_detail_is_complete_escaped_local_and_hides_internal_values(self):
        self.login(1)
        response = self.client.get("/manager-review/food-fluid/1")
        text = response.get_data(as_text=True)
        self.assertIn("2024-01-15 08:00", text)
        self.assertIn("2024-01-15 08:01", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertIn("&lt;b&gt;private detail&lt;/b&gt;", text)
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertNotIn("SECRET-SUBMISSION-TOKEN", text)
        self.assertIn("Client One", text)
        self.assertIn("Worker User", text)

        voided = self.client.get(
            "/manager-review/food-fluid/3"
        ).get_data(as_text=True)
        self.assertIn("Voided", voided)
        self.assertIn("Admin User", voided)
        self.assertIn("Entered in error", voided)
        self.assertIn("2024-01-15 09:02", voided)

    def test_no_management_void_controls(self):
        self.login(1)
        list_text = self.client.get(
            "/manager-review/food-fluid"
        ).get_data(as_text=True)
        detail_text = self.client.get(
            "/manager-review/food-fluid/1"
        ).get_data(as_text=True)
        self.assertNotIn("/void", list_text)
        self.assertNotIn("/void", detail_text)
        self.assertNotRegex(detail_text, re.compile(r"<button[^>]*>Void"))

    def test_worker_access_has_not_regressed(self):
        self.login(4, session_role="Admin")
        self.assertEqual(
            self.client.get("/shift/10/food-fluid").status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/manager-review/food-fluid").status_code,
            403,
        )


if __name__ == "__main__":
    unittest.main()
