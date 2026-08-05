import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_leave_requests_table
import app


class LeaveRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "leave.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.addCleanup(self.cleanup)
        self.create_database()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def create_database(self):
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                password_hash TEXT,
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
                status TEXT NOT NULL
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT,
                activity_class TEXT,
                activity_type TEXT,
                user_id INTEGER,
                client_id INTEGER,
                shift_id INTEGER,
                related_table TEXT,
                related_id INTEGER,
                summary TEXT,
                details TEXT,
                success INTEGER NOT NULL DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT NULL
            );
            INSERT INTO users VALUES
                (1, 'worker', 'x', 'Worker', 'Support Worker', 1),
                (2, 'manager', 'x', 'Manager', 'Program Manager', 1),
                (3, 'other', 'x', 'Other Worker', 'Support Worker', 1),
                (4, 'inactive', 'x', 'Inactive', 'Support Worker', 0);
            INSERT INTO clients VALUES (1, 'Client One', 1);
            INSERT INTO shifts VALUES (10, 1, 'Open');
            INSERT INTO shift_staff VALUES (100, 10, 1, 1);
        """)
        add_leave_requests_table.migrate(conn)
        conn.close()

    def login(self, user_id=1, role="Support Worker"):
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=role, full_name="Test User")

    def token(self):
        response = self.client.get("/leave-requests/new")
        self.assertEqual(response.status_code, 200)
        match = re.search(rb'name="submission_token" value="([A-Za-z0-9_-]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def post_valid(self, token=None, **overrides):
        data = {
            "submission_token": token or self.token(),
            "leave_type": "Vacation",
            "start_date": "2026-08-10",
            "end_date": "2026-08-12",
            "day_part": "FULL_DAY",
            "start_time": "",
            "end_time": "",
            "employee_comments": "Family plans",
        }
        data.update(overrides)
        return self.client.post("/leave-requests/new", data=data)

    def rows(self, table):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        conn.close()
        return rows

    def test_schema_is_created_idempotently_with_indexes_and_foreign_keys(self):
        conn = sqlite3.connect(self.path)
        self.assertFalse(add_leave_requests_table.migrate(conn))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(leave_requests)")}
        self.assertIn("submission_token", columns)
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(leave_requests)")}
        self.assertTrue({
            "idx_leave_requests_user_status",
            "idx_leave_requests_status_dates",
            "idx_leave_requests_start_date",
        }.issubset(indexes))
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_application_database_connection_applies_leave_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "startup.db")
            conn = sqlite3.connect(path)
            conn.execute("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.commit()
            conn.close()
            old_db = app.DB_NAME
            app.DB_NAME = path
            try:
                connected = app.get_db()
                self.assertIsNotNone(
                    connected.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='leave_requests'"
                    ).fetchone()
                )
                connected.close()
            finally:
                app.DB_NAME = old_db

    def test_incompatible_existing_table_rolls_back_without_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "incompatible.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE leave_requests (leave_request_id INTEGER PRIMARY KEY)")
            conn.commit()
            with self.assertRaises(RuntimeError):
                add_leave_requests_table.migrate(conn)
            self.assertEqual(
                conn.execute("PRAGMA table_info(leave_requests)").fetchall(),
                [(0, "leave_request_id", "INTEGER", 0, None, 1)],
            )
            conn.close()

    def test_worker_resources_and_navigation_are_available_without_top_level_leave_link(self):
        self.login()
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Worker Resources", response.data)
        self.assertIn(b"Request Leave", response.data)
        self.assertIn(b"/staff-notices", response.data)
        navigation = self.client.get("/leave-requests").data
        self.assertIn(b"Worker Resources", navigation)

    def test_leave_form_has_associated_radio_controls_and_conditional_rows(self):
        self.login()
        response = self.client.get("/leave-requests/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="day_part_full"', response.data)
        self.assertIn(b'for="day_part_full"', response.data)
        self.assertIn(b'value="FULL_DAY"', response.data)
        self.assertIn(b'id="day_part_partial"', response.data)
        self.assertIn(b'for="day_part_partial"', response.data)
        self.assertIn(b'value="PARTIAL_DAY"', response.data)
        self.assertIn(b"leave-request-grid", response.data)
        self.assertIn(b"leave-request-conditional-hidden", response.data)
        self.assertIn(b'name="start_time"', response.data)
        self.assertIn(b'name="end_time"', response.data)
        self.assertIn(b"disabled", response.data)

        self.assertEqual(self.post_valid().status_code, 302)
        request_id = self.rows("leave_requests")[0]["leave_request_id"]
        edit = self.client.get(f"/leave-requests/{request_id}/edit")
        self.assertEqual(edit.status_code, 200)
        self.assertIn(b'value="FULL_DAY" checked', edit.data)

        conn = sqlite3.connect(self.path)
        conn.execute(
            "UPDATE leave_requests SET day_part = 'PARTIAL_DAY', "
            "start_date = '2026-08-10', end_date = '2026-08-10', "
            "start_time = '09:00', end_time = '10:30', "
            "requested_days = NULL, requested_hours = 1.5 "
            "WHERE leave_request_id = ?",
            (request_id,),
        )
        conn.commit()
        conn.close()
        edit_partial = self.client.get(f"/leave-requests/{request_id}/edit")
        self.assertEqual(edit_partial.status_code, 200)
        self.assertIn(b'value="PARTIAL_DAY" checked', edit_partial.data)

    def test_partial_day_server_validation_remains_authoritative_without_times(self):
        self.login()
        response = self.post_valid(
            start_date="2026-08-10",
            end_date="2026-08-10",
            day_part="PARTIAL_DAY",
            start_time="",
            end_time="",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"requires start and end times", response.data)

    def test_unauthenticated_and_inactive_users_are_rejected(self):
        self.assertEqual(self.client.get("/worker-resources").status_code, 302)
        self.login(4)
        self.assertEqual(self.client.get("/leave-requests").status_code, 403)

    def test_valid_full_day_submission_calculates_days_and_writes_one_hidden_audit(self):
        self.login()
        response = self.post_valid()
        self.assertEqual(response.status_code, 302)
        request_row = self.rows("leave_requests")[0]
        self.assertEqual(request_row["user_id"], 1)
        self.assertEqual(request_row["requested_days"], 3.0)
        self.assertEqual(request_row["status"], "PENDING")
        audit = self.rows("activity_log")
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["activity_type"], "leave_request_created")
        self.assertIsNone(audit[0]["client_id"])
        self.assertIsNone(audit[0]["shift_id"])
        self.assertEqual(audit[0]["related_table"], "leave_requests")
        self.assertEqual(audit[0]["related_id"], request_row["leave_request_id"])
        self.assertEqual(audit[0]["storyline_visible"], 0)

    def test_valid_partial_day_calculates_hours_and_multi_day_is_rejected(self):
        self.login()
        response = self.post_valid(
            leave_type="Medical Appointment",
            start_date="2026-08-10",
            end_date="2026-08-10",
            day_part="PARTIAL_DAY",
            start_time="09:30",
            end_time="12:00",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("leave_requests")[0]["requested_hours"], 2.5)
        bad = self.post_valid(
            leave_type="Vacation",
            day_part="PARTIAL_DAY",
            start_time="09:00",
            end_time="10:00",
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn(b"same start and end date", bad.data)

    def test_validation_rejects_other_without_reason_bad_type_and_bad_times(self):
        self.login()
        for data, message in (
            ({"leave_type": "Other"}, b"explanation is required"),
            ({"leave_type": "Not Real"}, b"valid leave type"),
            ({"day_part": "PARTIAL_DAY", "start_date": "2026-08-10", "end_date": "2026-08-10", "start_time": "10:00", "end_time": "09:00"}, b"later"),
            ({"day_part": "PARTIAL_DAY", "start_date": "2026-08-10", "end_date": "2026-08-10", "start_time": "", "end_time": ""}, b"requires start and end"),
        ):
            response = self.post_valid(**data)
            self.assertEqual(response.status_code, 400)
            self.assertIn(message, response.data)

    def test_duplicate_submission_token_does_not_duplicate_request_or_audit(self):
        self.login()
        token = self.token()
        self.assertEqual(self.post_valid(token).status_code, 302)
        self.assertEqual(self.post_valid(token).status_code, 302)
        self.assertEqual(len(self.rows("leave_requests")), 1)
        self.assertEqual(len(self.rows("activity_log")), 1)

    def test_worker_owns_history_detail_edit_and_cancel(self):
        self.login()
        self.assertEqual(self.post_valid().status_code, 302)
        request_id = self.rows("leave_requests")[0]["leave_request_id"]
        self.assertEqual(self.client.get(f"/leave-requests/{request_id}").status_code, 200)
        edited = self.client.post(f"/leave-requests/{request_id}/edit", data={
            "leave_type": "Vacation", "start_date": "2026-08-11",
            "end_date": "2026-08-12", "day_part": "FULL_DAY",
            "employee_comments": "Updated",
        })
        self.assertEqual(edited.status_code, 302)
        self.assertEqual(self.client.get(f"/leave-requests/{request_id}/cancel").status_code, 200)
        cancelled = self.client.post(f"/leave-requests/{request_id}/cancel")
        self.assertEqual(cancelled.status_code, 302)
        self.assertEqual(self.rows("leave_requests")[0]["status"], "CANCELLED")
        self.assertEqual(
            [row["activity_type"] for row in self.rows("activity_log")],
            ["leave_request_created", "leave_request_updated", "leave_request_cancelled"],
        )

    def test_worker_cannot_access_another_workers_request(self):
        self.login()
        self.assertEqual(self.post_valid().status_code, 302)
        request_id = self.rows("leave_requests")[0]["leave_request_id"]
        self.login(3)
        self.assertEqual(self.client.get(f"/leave-requests/{request_id}").status_code, 404)
        self.assertEqual(self.client.post(f"/leave-requests/{request_id}/cancel").status_code, 404)

    def test_management_review_approve_decline_and_self_decision_rules(self):
        self.login()
        self.assertEqual(self.post_valid().status_code, 302)
        request_id = self.rows("leave_requests")[0]["leave_request_id"]
        self.login(2, "Program Manager")
        review = self.client.get("/manager-review/leave-requests")
        self.assertEqual(review.status_code, 200)
        self.assertIn(b"Worker", review.data)
        approved = self.client.post(f"/manager-review/leave-requests/{request_id}/approve", data={
            "management_comments": "Approved for coverage."
        })
        self.assertEqual(approved.status_code, 302)
        self.assertEqual(self.rows("leave_requests")[0]["status"], "APPROVED")
        self.assertEqual(self.rows("activity_log")[1]["activity_type"], "leave_request_approved")
        self.assertEqual(self.client.post(f"/manager-review/leave-requests/{request_id}/decline", data={}).status_code, 400)

        self.login(1, "Support Worker")
        self.assertEqual(self.post_valid(start_date="2026-09-01", end_date="2026-09-01").status_code, 302)
        second_id = self.rows("leave_requests")[-1]["leave_request_id"]
        self.login(2, "Program Manager")
        declined = self.client.post(
            f"/manager-review/leave-requests/{second_id}/decline",
            data={"management_comments": "Coverage is unavailable."},
        )
        self.assertEqual(declined.status_code, 302)
        self.assertEqual(self.rows("leave_requests")[-1]["status"], "DECLINED")
        self.assertEqual(self.rows("activity_log")[-1]["activity_type"], "leave_request_declined")

    def test_worker_cannot_review_and_manager_cannot_self_decide(self):
        self.login()
        self.assertEqual(self.post_valid().status_code, 302)
        request_id = self.rows("leave_requests")[0]["leave_request_id"]
        self.assertEqual(self.client.get("/manager-review/leave-requests").status_code, 403)
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE leave_requests SET user_id = 2 WHERE leave_request_id = ?", (request_id,))
        conn.commit()
        conn.close()
        self.login(2, "Program Manager")
        response = self.client.post(f"/manager-review/leave-requests/{request_id}/approve")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.rows("leave_requests")[0]["status"], "PENDING")

    def test_leave_audit_rows_are_excluded_from_client_storyline(self):
        self.login()
        self.assertEqual(self.post_valid().status_code, 302)
        manager = self.client
        self.login(2, "Program Manager")
        page = manager.get("/client/1/storyline")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"Leave request submitted:", page.data)


if __name__ == "__main__":
    unittest.main()
