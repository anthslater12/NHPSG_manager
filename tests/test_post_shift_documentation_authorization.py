import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import app


class PostShiftDocumentationAuthorizationTests(unittest.TestCase):

    NOW = datetime(2026, 8, 10, 19, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(
            self.temporary_directory.name
        ) / "post_shift_documentation.db"
        self.conn = sqlite3.connect(self.database_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
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
                scheduled_end_time TEXT,
                actual_end_at_utc TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT,
                actual_end_time TEXT,
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
        self.conn.execute(
            "INSERT INTO users (user_id, role) VALUES (1, 'Support Worker')"
        )
        self.conn.execute(
            "INSERT INTO users (user_id, role) VALUES (2, 'Support Worker')"
        )
        self.conn.execute(
            "INSERT INTO users (user_id, role) VALUES (3, 'Program Manager')"
        )
        self.conn.execute(
            "INSERT INTO clients (client_id, client_name) VALUES (10, 'Test Client')"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def add_shift(self, shift_id, *, status="Open", scheduled_end="09:00"):
        self.conn.execute("""
            INSERT INTO shifts
            (shift_id, client_id, shift_date, shift_type, status,
             scheduled_end_time)
            VALUES (?, 10, '2026-08-10', 'Day', ?, ?)
        """, (shift_id, status, scheduled_end))

    def add_assignment(
        self,
        shift_id,
        *,
        assignment_id,
        user_id=1,
        active=1,
        start="07:00",
        sign_on="2026-08-10T07:00:00Z",
        end=None,
        sign_off=None,
    ):
        self.conn.execute("""
            INSERT INTO shift_staff
            (shift_staff_id, shift_id, user_id, actual_start_time,
             actual_end_at_utc, sign_on_at, sign_off_at, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            assignment_id, shift_id, user_id, start, end,
            sign_on, sign_off, active
        ))

    def commit(self):
        self.conn.commit()

    def test_active_signed_on_assignment_is_eligible(self):
        self.add_shift(1)
        self.add_assignment(1, assignment_id=1)
        self.commit()

        context = app.get_worker_documentation_shift_context(
            self.conn, 1, 1, now_utc=self.NOW
        )

        self.assertEqual(context["documentation_access"], "active_assignment")
        self.assertEqual(context["shift_id"], 1)

    def test_completed_assignment_is_eligible_at_and_inside_boundary(self):
        self.add_shift(1)
        self.add_assignment(
            1,
            assignment_id=1,
            active=0,
            end="2026-08-10T15:00:00Z",
            sign_off="2026-08-10T15:00:00Z",
        )
        self.commit()

        inside = app.get_worker_documentation_shift_context(
            self.conn, 1, 1,
            now_utc=datetime(2026, 8, 10, 18, 59, 59, tzinfo=timezone.utc)
        )
        boundary = app.get_worker_documentation_shift_context(
            self.conn, 1, 1, now_utc=self.NOW
        )

        self.assertEqual(inside["documentation_access"], "post_shift")
        self.assertEqual(boundary["documentation_access"], "post_shift")

    def test_completed_assignment_expires_after_four_hours(self):
        self.add_shift(1)
        self.add_assignment(
            1,
            assignment_id=1,
            active=0,
            end="2026-08-10T15:00:00Z",
            sign_off="2026-08-10T15:00:00Z",
        )
        self.commit()

        self.assertIsNone(app.get_worker_documentation_shift_context(
            self.conn, 1, 1,
            now_utc=datetime(2026, 8, 10, 19, 0, 1, tzinfo=timezone.utc)
        ))

    def test_scheduled_end_and_parent_open_status_do_not_control_window(self):
        self.add_shift(1, scheduled_end="09:00")
        self.add_assignment(
            1,
            assignment_id=1,
            active=0,
            end="2026-08-10T15:00:00Z",
            sign_off="2026-08-10T15:00:00Z",
        )
        self.commit()

        context = app.get_worker_documentation_shift_context(
            self.conn, 1, 1, now_utc=self.NOW
        )

        self.assertIsNotNone(context)

    def test_cancelled_parent_is_rejected(self):
        self.add_shift(1, status="Cancelled")
        self.add_assignment(
            1,
            assignment_id=1,
            active=0,
            end="2026-08-10T15:00:00Z",
            sign_off="2026-08-10T15:00:00Z",
        )
        self.commit()

        self.assertFalse(app.can_worker_document_shift(
            self.conn, 1, 1, now_utc=self.NOW
        ))

    def test_invalid_missing_or_wrong_assignments_are_rejected(self):
        self.add_shift(1)
        self.add_shift(2)
        self.add_assignment(1, assignment_id=1, active=0)
        self.add_assignment(
            2,
            assignment_id=2,
            user_id=2,
            active=0,
            end="2026-08-10T15:00:00Z",
            sign_off="2026-08-10T15:00:00Z",
        )
        self.commit()

        self.assertIsNone(app.get_worker_documentation_shift_context(
            self.conn, 1, 1, now_utc=self.NOW
        ))
        self.assertIsNone(app.get_worker_documentation_shift_context(
            self.conn, 2, 1, now_utc=self.NOW
        ))

    def test_missing_end_or_sign_on_evidence_does_not_grant_access(self):
        self.add_shift(1)
        self.add_assignment(1, assignment_id=1, sign_on=None)
        self.add_shift(2)
        self.add_assignment(
            2,
            assignment_id=2,
            active=0,
            end=None,
            sign_off=None,
        )
        self.commit()

        self.assertEqual(
            app.get_worker_documentation_assignments(
                self.conn, 1, now_utc=self.NOW
            ),
            []
        )

    def test_malformed_completion_timestamp_fails_closed(self):
        self.add_shift(1)
        self.add_assignment(
            1,
            assignment_id=1,
            active=0,
            end="not-a-utc-timestamp",
            sign_off="2026-08-10T15:00:00Z",
        )
        self.commit()

        self.assertIsNone(app.get_worker_documentation_shift_context(
            self.conn, 1, 1, now_utc=self.NOW
        ))

    def test_utc_window_is_independent_of_daylight_saving_transition(self):
        self.add_shift(1)
        self.add_assignment(
            1,
            assignment_id=1,
            active=0,
            end="2026-11-01T08:00:00Z",
            sign_off="2026-11-01T08:00:00Z",
        )
        self.commit()

        context = app.get_worker_documentation_shift_context(
            self.conn,
            1,
            1,
            now_utc=datetime(
                2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc
            )
        )

        self.assertEqual(context["documentation_access"], "post_shift")

    def test_multiple_and_double_shift_assignments_are_all_returned(self):
        self.add_shift(1)
        self.add_assignment(1, assignment_id=1)
        self.add_shift(2)
        self.add_assignment(
            2,
            assignment_id=2,
            active=0,
            end="2026-08-10T15:00:00Z",
            sign_off="2026-08-10T15:00:00Z",
        )
        self.add_shift(3)
        self.add_assignment(3, assignment_id=3)
        self.commit()

        assignments = app.get_worker_documentation_assignments(
            self.conn, 1, now_utc=self.NOW
        )

        self.assertEqual(
            {assignment["shift_id"] for assignment in assignments},
            {1, 2, 3}
        )
        self.assertEqual(
            app.get_worker_documentation_shift_context(
                self.conn, 2, 1, now_utc=self.NOW
            )["shift_id"],
            2
        )

    def test_non_worker_and_session_state_cannot_grant_access(self):
        self.add_shift(1)
        self.add_assignment(1, assignment_id=1)
        self.commit()

        self.assertFalse(app.can_worker_document_shift(
            self.conn, 1, 3, now_utc=self.NOW
        ))
        self.assertFalse(app.can_worker_document_shift(
            self.conn, 999, 1, now_utc=self.NOW
        ))

    def test_helper_is_read_only_and_does_not_broaden_lifecycle_permissions(self):
        self.add_shift(1)
        self.add_assignment(1, assignment_id=1)
        self.commit()
        before_shift = self.conn.execute(
            "SELECT * FROM shifts WHERE shift_id = 1"
        ).fetchone()
        before_assignment = self.conn.execute(
            "SELECT * FROM shift_staff WHERE shift_id = 1"
        ).fetchone()
        before_audit_count = self.conn.execute(
            "SELECT COUNT(*) FROM activity_log"
        ).fetchone()[0]

        self.assertTrue(app.can_worker_document_shift(
            self.conn, 1, 1, now_utc=self.NOW
        ))

        self.assertEqual(
            tuple(self.conn.execute(
                "SELECT * FROM shifts WHERE shift_id = 1"
            ).fetchone()),
            tuple(before_shift)
        )
        self.assertEqual(
            tuple(self.conn.execute(
                "SELECT * FROM shift_staff WHERE shift_id = 1"
            ).fetchone()),
            tuple(before_assignment)
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0],
            before_audit_count
        )


if __name__ == "__main__":
    unittest.main()
