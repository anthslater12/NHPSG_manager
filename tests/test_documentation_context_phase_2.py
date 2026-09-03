import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app


class DocumentationContextPhase2Tests(unittest.TestCase):

    NOW = datetime(2026, 8, 10, 19, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.now_patcher = mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.NOW,
        )
        self.now_patcher.start()
        self.addCleanup(self.now_patcher.stop)

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "documentation-context.db"
        )
        self.original_db_name = app.DB_NAME
        app.DB_NAME = self.database_path
        self.addCleanup(self.restore_application_state)
        self.create_database()
        self.client = app.app.test_client()

    def restore_application_state(self):
        app.DB_NAME = self.original_db_name

    def create_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
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
                status TEXT NOT NULL,
                scheduled_end_time TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT,
                actual_end_at_utc TEXT,
                sign_on_at TEXT,
                sign_off_at TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY,
                activity_datetime TEXT,
                user_id INTEGER,
                shift_id INTEGER,
                summary TEXT
            );
        """)
        conn.execute(
            "INSERT INTO users (user_id, role) VALUES (1, 'Support Worker')"
        )
        conn.execute(
            "INSERT INTO users (user_id, role) VALUES (2, 'Support Worker')"
        )
        conn.execute(
            "INSERT INTO users (user_id, role) VALUES (3, 'Program Manager')"
        )
        conn.execute(
            "INSERT INTO clients (client_id, client_name) "
            "VALUES (10, 'Neville')"
        )
        conn.commit()
        conn.close()

    def db(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def authenticate(self, role="Support Worker"):
        with self.client.session_transaction() as session:
            session["user_id"] = 1 if role == "Support Worker" else 3
            session["role"] = role
            session["full_name"] = "Worker"

    def seed_shift(self, shift_id, *, active=True, user_id=1, end=None):
        conn = self.db()
        conn.execute("""
            INSERT INTO shifts
            (shift_id, client_id, shift_date, shift_type, status,
             scheduled_end_time)
            VALUES (?, 10, '2026-08-10', 'Day', 'Open', '15:00')
        """, (shift_id,))
        conn.execute("""
            INSERT INTO shift_staff
            (shift_staff_id, shift_id, user_id, actual_start_time,
             actual_end_at_utc, sign_on_at, sign_off_at, active)
            VALUES (?, ?, ?, '07:00', ?, '2026-08-10T07:00:00Z', ?, ?)
        """, (
            shift_id, shift_id, user_id, end,
            end, 1 if active else 0
        ))
        conn.commit()
        conn.close()

    def test_active_only_context_is_selected_without_auto_sign_on(self):
        self.authenticate()
        self.seed_shift(1)

        with mock.patch.object(app, "auto_sign_on_user") as auto_sign_on:
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/1", response.headers["Location"])
        auto_sign_on.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertEqual(session[app.DOCUMENTATION_CONTEXT_SESSION_KEY], 1)

    def test_active_and_previous_contexts_are_offered_before_auto_sign_on(self):
        self.authenticate()
        self.seed_shift(1)
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )

        with mock.patch.object(app, "auto_sign_on_user") as auto_sign_on:
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/documentation-context", response.headers["Location"])
        auto_sign_on.assert_not_called()

        page = self.client.get(response.headers["Location"])
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Continue Current Shift", page.data)
        self.assertIn(b"Finish Documentation for Previous Shift", page.data)
        self.assertIn(b'value="1"', page.data)
        self.assertIn(b'value="2"', page.data)

    def test_previous_context_can_be_selected_without_signing_on(self):
        self.authenticate()
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )

        with mock.patch.object(app, "auto_sign_on_user") as auto_sign_on:
            response = self.client.post(
                "/documentation-context",
                data={"shift_id": "2"}
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/2", response.headers["Location"])
        auto_sign_on.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertEqual(session[app.DOCUMENTATION_CONTEXT_SESSION_KEY], 2)

    def test_exact_boundary_is_selectable_and_expired_context_is_not(self):
        self.authenticate()
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )
        with mock.patch.object(
            app, "get_application_now_utc", return_value=self.NOW
        ):
            response = self.client.post(
                "/documentation-context", data={"shift_id": "2"}
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/2", response.headers["Location"])

        with self.client.session_transaction() as session:
            session.pop(app.DOCUMENTATION_CONTEXT_SESSION_KEY, None)
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(
                2026, 8, 10, 19, 0, 1, tzinfo=timezone.utc
            )
        ):
            response = self.client.post(
                "/documentation-context", data={"shift_id": "2"}
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/documentation-context", response.headers["Location"])

    def test_start_new_shift_uses_existing_auto_sign_on_flow(self):
        self.authenticate()
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )

        with mock.patch.object(
            app,
            "auto_sign_on_user",
            return_value=(4, True)
        ) as auto_sign_on:
            response = self.client.post(
                "/documentation-context",
                data={"action": "start_new_shift"}
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/4", response.headers["Location"])
        auto_sign_on.assert_called_once_with(1)

    def test_multiple_previous_contexts_are_all_displayed(self):
        self.authenticate()
        self.seed_shift(2, active=False, end="2026-08-10T15:00:00Z")
        self.seed_shift(3, active=False, end="2026-08-10T16:00:00Z")

        response = self.client.get("/documentation-context")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="2"', response.data)
        self.assertIn(b'value="3"', response.data)
        self.assertEqual(response.data.count(b"Finish Documentation for Previous Shift"), 2)

    def test_no_eligible_context_preserves_automatic_sign_on(self):
        self.authenticate()
        with mock.patch.object(
            app,
            "auto_sign_on_user",
            return_value=(9, True)
        ) as auto_sign_on:
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/9", response.headers["Location"])
        auto_sign_on.assert_called_once_with(1)
        with self.client.session_transaction() as session:
            self.assertEqual(session[app.DOCUMENTATION_CONTEXT_SESSION_KEY], 9)

    def test_invalid_selection_is_revalidated_and_rejected(self):
        self.authenticate()
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )

        response = self.client.post(
            "/documentation-context",
            data={"shift_id": "999", "return_to": "https://evil.test"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/documentation-context", response.headers["Location"])
        self.assertNotIn("evil.test", response.headers["Location"])
        with self.client.session_transaction() as session:
            self.assertNotIn(app.DOCUMENTATION_CONTEXT_SESSION_KEY, session)

    def test_other_workers_and_cancelled_or_expired_contexts_are_rejected(self):
        self.authenticate()
        self.seed_shift(2, user_id=2, active=False, end="2026-08-10T15:00:00Z")
        self.seed_shift(3, active=False, end="2026-08-10T14:59:59Z")
        conn = self.db()
        conn.execute("UPDATE shifts SET status = 'Cancelled' WHERE shift_id = 3")
        conn.commit()
        conn.close()

        response = self.client.post(
            "/documentation-context", data={"shift_id": "2"}
        )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn(app.DOCUMENTATION_CONTEXT_SESSION_KEY, session)

        response = self.client.post(
            "/documentation-context", data={"shift_id": "3"}
        )
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn(app.DOCUMENTATION_CONTEXT_SESSION_KEY, session)

    def test_forged_session_shift_id_is_revalidated(self):
        self.authenticate()
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = 999

        with mock.patch.object(app, "auto_sign_on_user") as auto_sign_on:
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/documentation-context", response.headers["Location"])
        auto_sign_on.assert_not_called()
        with self.client.session_transaction() as session:
            self.assertNotIn(app.DOCUMENTATION_CONTEXT_SESSION_KEY, session)

    def test_expired_session_selection_is_cleared_on_dashboard_request(self):
        self.authenticate()
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = 2

        with mock.patch.object(
            app,
            "auto_sign_on_user",
            return_value=(4, True)
        ):
            with mock.patch.object(
                app,
                "get_application_now_utc",
                return_value=datetime(
                    2026, 8, 10, 19, 0, 1, tzinfo=timezone.utc
                )
            ):
                response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotEqual(
                session.get(app.DOCUMENTATION_CONTEXT_SESSION_KEY), 2
            )

    def test_expired_session_selection_is_cleared_on_selector_request(self):
        self.authenticate()
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = 2

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(
                2026, 8, 10, 19, 0, 1, tzinfo=timezone.utc
            )
        ):
            response = self.client.get("/documentation-context")

        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn(app.DOCUMENTATION_CONTEXT_SESSION_KEY, session)

    def test_banner_identifies_current_previous_and_switch_control(self):
        current = {
            "shift_label": "Current Day Shift",
            "date_display": "August 10, 2026",
            "time_display": "7:00 AM - 3:00 PM",
        }
        previous = {
            "shift_label": "Previous Day Shift",
            "date_display": "August 10, 2026",
            "time_display": "7:00 AM - 3:00 PM",
        }

        with app.app.test_request_context():
            current_html = app.render_template(
                "_documentation_context_banner.html",
                documentation_context=current,
                documentation_context_alternatives=[previous]
            )
            previous_html = app.render_template(
                "_documentation_context_banner.html",
                documentation_context=previous,
                documentation_context_alternatives=[]
            )

        self.assertIn("Current Day Shift", current_html)
        self.assertIn("Previous Day Shift", previous_html)
        self.assertIn("Change documentation shift", current_html)
        self.assertNotIn("late", previous_html.lower())
        self.assertNotIn("active", previous_html.lower())
        self.assertNotIn("Change documentation shift", previous_html)

    def test_selection_does_not_write_lifecycle_or_activity_records(self):
        self.authenticate()
        self.seed_shift(
            2,
            active=False,
            end="2026-08-10T15:00:00Z"
        )
        conn = self.db()
        before_shift = tuple(conn.execute(
            "SELECT * FROM shifts WHERE shift_id = 2"
        ).fetchone())
        before_assignment = tuple(conn.execute(
            "SELECT * FROM shift_staff WHERE shift_id = 2"
        ).fetchone())
        before_audit_count = conn.execute(
            "SELECT COUNT(*) FROM activity_log"
        ).fetchone()[0]
        conn.close()

        self.client.post("/documentation-context", data={"shift_id": "2"})

        conn = self.db()
        self.assertEqual(
            tuple(conn.execute(
                "SELECT * FROM shifts WHERE shift_id = 2"
            ).fetchone()),
            before_shift
        )
        self.assertEqual(
            tuple(conn.execute(
                "SELECT * FROM shift_staff WHERE shift_id = 2"
            ).fetchone()),
            before_assignment
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0],
            before_audit_count
        )
        conn.close()

    def test_management_role_cannot_use_worker_context_selector(self):
        self.authenticate(role="Program Manager")

        self.assertEqual(
            self.client.get("/documentation-context").status_code,
            403
        )


if __name__ == "__main__":
    unittest.main()
