import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_leave_requests_table
import app


class LeaveManagerAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "alerts.db")
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
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users VALUES
                (1, 'Worker', 'Support Worker', 1),
                (2, 'Manager', 'Program Manager', 1);
            INSERT INTO shifts VALUES (10, '2026-08-01', 'Day');
            INSERT INTO shift_staff VALUES (100, 10, 1, 1);
        """)
        add_leave_requests_table.migrate(conn)
        conn.close()

    def login(self, user_id=2, role="Program Manager"):
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=role, full_name="Test User")

    def add_pending_request(self, token="pending-token"):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO leave_requests (
                user_id, leave_type, start_date, end_date, day_part,
                submitted_at_utc, updated_at_utc, submission_token
            ) VALUES (?, 'Vacation', '2026-08-10', '2026-08-10',
                      'FULL_DAY', '2026-08-05T12:00:00Z',
                      '2026-08-05T12:00:00Z', ?)
        """, (1, token))
        conn.commit()
        conn.close()

    def test_pending_leave_alert_has_live_count_and_review_link(self):
        self.add_pending_request()
        self.login()

        response = self.client.get("/manager-alerts")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Pending Leave Requests", response.data)
        self.assertIn(b"1 pending leave request requires review.", response.data)
        self.assertIn(
            b'href="/manager-review/leave-requests?status=PENDING"',
            response.data,
        )
        self.assertIn(b"Review Pending Leave Requests", response.data)

    def test_pending_leave_alert_is_rendered_in_administration_dashboard(self):
        self.add_pending_request()
        self.login()

        with app.app.test_request_context("/"):
            alerts = app.get_manager_alerts()
            rendered = app.render_template(
                "admin_dashboard.html",
                manager_alerts=alerts,
                outstanding_action_count=0,
                outstanding_actions=[],
                staff_notices=[],
                staff_notice_outstanding_count=0,
                active_staff=[],
            )

        self.assertIn("Pending Leave Requests", rendered)
        self.assertIn("Review Pending Leave Requests", rendered)

    def test_support_worker_cannot_view_management_alerts(self):
        self.add_pending_request()
        self.login(user_id=1, role="Support Worker")

        response = self.client.get("/manager-alerts")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"Pending Leave Requests", response.data)

    def test_no_pending_requests_produces_no_leave_alert(self):
        self.login()

        response = self.client.get("/manager-alerts")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Pending Leave Requests", response.data)


if __name__ == "__main__":
    unittest.main()
