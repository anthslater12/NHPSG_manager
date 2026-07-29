import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app
import add_staff_notices_tables as staff_notice_schema


class _CommitFailureConnection:
    def __init__(self, connection):
        self.connection = connection

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        raise sqlite3.OperationalError("controlled commit failure")


class StaffNoticeManagementLifecycleTests(unittest.TestCase):
    FIXED_NOW = datetime(2026, 8, 4, 0, 30, tzinfo=timezone.utc)
    FIXED_TIMESTAMP = "2026-08-04T00:30:00Z"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "management-lifecycle.db"
        )
        self.original_database_name = app.DB_NAME
        self.addCleanup(self.restore_application_state)
        app.DB_NAME = self.database_path
        self.create_database()

    def restore_application_state(self):
        app.DB_NAME = self.original_database_name

    def create_database(self):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL
                );
                CREATE TABLE clients (
                    client_id INTEGER PRIMARY KEY,
                    client_name TEXT NOT NULL,
                    active INTEGER NOT NULL
                );
                CREATE TABLE shifts (
                    shift_id INTEGER PRIMARY KEY,
                    client_id INTEGER NOT NULL,
                    shift_date TEXT NOT NULL,
                    shift_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scheduled_start_time TEXT,
                    scheduled_end_time TEXT
                );
                CREATE TABLE shift_staff (
                    shift_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    actual_start_time TEXT,
                    active INTEGER NOT NULL
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
                    success INTEGER
                );
                CREATE TABLE acknowledgements (
                    acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_table TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    comment TEXT,
                    acknowledgement_type TEXT DEFAULT 'Read',
                    active INTEGER NOT NULL DEFAULT 1
                        CHECK (active IN (0, 1)),
                    invalidated_at_utc TEXT,
                    invalidated_by_user_id INTEGER,
                    invalidation_reason TEXT
                );
                CREATE UNIQUE INDEX
                    ux_acknowledgements_active_source_user
                ON acknowledgements(source_table, source_id, user_id)
                WHERE active = 1;
            """)
            for sql in staff_notice_schema.TABLE_SQL.values():
                conn.execute(sql)
            for sql in staff_notice_schema.INDEX_SQL.values():
                conn.execute(sql)
            conn.executemany("""
                INSERT INTO users (user_id, full_name, role, active)
                VALUES (?, ?, ?, ?)
            """, (
                (1, "Admin User", "Admin", 1),
                (2, "Program Manager", "Program Manager", 1),
                (3, "Director User", "Director", 1),
                (4, "Assigned Worker", "Support Worker", 1),
                (5, "Other Worker", "Support Worker", 1)
            ))
            conn.execute("""
                INSERT INTO clients (client_id, client_name, active)
                VALUES (1, 'Active Client', 1)
            """)
            conn.commit()
        finally:
            conn.close()

    def open_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def management_client(self, role="Admin", user_id=1):
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = role
        return client

    def create_notice(self):
        conn = self.open_database()
        try:
            cursor = conn.execute("""
                INSERT INTO staff_notices
                (
                    title, notice_text, priority, client_id, status,
                    draft_active, effective_start_at_utc, expires_at_utc,
                    until_withdrawn, version_number, created_by_user_id,
                    created_at_utc, published_by_user_id, published_at_utc
                )
                VALUES (
                    'Lifecycle Notice', 'Preserved lifecycle notice.',
                    'Important', 1, 'Published', 0,
                    '2026-08-01T07:00:00Z', NULL, 1, 1, 1,
                    '2026-08-01T07:00:00Z', 1,
                    '2026-08-01T07:05:00Z'
                )
            """)
            notice_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_audiences
                (notice_id, created_at_utc)
                VALUES (?, '2026-08-01T07:00:00Z')
            """, (notice_id,))
            audience_id = cursor.lastrowid
            conn.execute("""
                INSERT INTO staff_notice_audience_rules
                (audience_id, rule_type, created_at_utc)
                VALUES (?, 'Applicable Shift Staff',
                        '2026-08-01T07:00:00Z')
            """, (audience_id,))
            cursor = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id, occurrence_basis, recurrence_pattern,
                    shift_applicability, specific_shift_client_id,
                    specific_shift_date, specific_shift_type, created_at_utc
                )
                VALUES (
                    ?, 'Shift', 'Once', 'Specific Shift', 1,
                    '2026-08-03', 'Day', '2026-08-01T07:00:00Z'
                )
            """, (notice_id,))
            schedule_id = cursor.lastrowid
            conn.commit()
            return {
                "notice_id": notice_id,
                "audience_id": audience_id,
                "schedule_id": schedule_id
            }
        finally:
            conn.close()

    def create_pending_occurrence(self):
        fixture = self.create_notice()
        conn = self.open_database()
        try:
            cursor = conn.execute("""
                INSERT INTO staff_notice_occurrences
                (
                    schedule_id, occurrence_kind, occurrence_date,
                    planned_client_id, planned_shift_type,
                    is_specific_shift_occurrence, occurrence_status,
                    created_at_utc
                )
                VALUES (
                    ?, 'Shift', '2026-08-03', 1, 'Day', 1,
                    'Pending Shift', '2026-08-01T07:00:00Z'
                )
            """, (fixture["schedule_id"],))
            fixture["occurrence_id"] = cursor.lastrowid
            conn.commit()
            return fixture
        finally:
            conn.close()

    def create_bound_delivery(self, *, viewed=True, acknowledged=False):
        fixture = self.create_notice()
        conn = self.open_database()
        try:
            conn.execute("""
                INSERT INTO shifts
                (
                    shift_id, client_id, shift_date, shift_type, status,
                    scheduled_start_time, scheduled_end_time
                )
                VALUES (10, 1, '2026-08-03', 'Day', 'Open',
                        '07:00', '15:00')
            """)
            cursor = conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (10, 4, '07:00', 1)
            """)
            fixture["shift_staff_id"] = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_occurrences
                (
                    schedule_id, occurrence_kind, occurrence_date,
                    planned_client_id, planned_shift_type, shift_id,
                    is_specific_shift_occurrence, visible_from_at_utc,
                    due_at_utc, due_at_is_provisional, occurrence_status,
                    created_at_utc, shift_bound_at_utc
                )
                VALUES (
                    ?, 'Shift', '2026-08-03', 1, 'Day', 10, 1,
                    '2026-08-03T14:00:00Z', '2026-08-03T22:00:00Z',
                    1, 'Active', '2026-08-01T07:00:00Z',
                    '2026-08-03T14:00:00Z'
                )
            """, (fixture["schedule_id"],))
            fixture["occurrence_id"] = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_deliveries
                (
                    occurrence_id, user_id, requirement_status,
                    assigned_at_utc, eligibility_cutoff_at_utc,
                    first_viewed_at_utc, viewed_by_user_id, recipient_access
                )
                VALUES (?, 4, 'Required', '2026-08-03T14:00:00Z',
                        '2026-08-03T14:00:00Z', ?, ?, 1)
            """, (
                fixture["occurrence_id"],
                "2026-08-03T15:00:00Z" if viewed else None,
                4 if viewed else None
            ))
            fixture["delivery_id"] = cursor.lastrowid
            conn.execute("""
                INSERT INTO staff_notice_delivery_history
                (
                    delivery_id, event_type,
                    previous_requirement_status, new_requirement_status,
                    previous_recipient_access, new_recipient_access,
                    changed_at_utc
                )
                VALUES (?, 'Assigned', NULL, 'Required', NULL, 1,
                        '2026-08-03T14:00:00Z')
            """, (fixture["delivery_id"],))
            if acknowledged:
                cursor = conn.execute("""
                    INSERT INTO acknowledgements
                    (
                        source_table, source_id, user_id, acknowledged_at,
                        acknowledgement_type, active
                    )
                    VALUES ('staff_notice_deliveries', ?, 4,
                            '2026-08-03T16:00:00Z',
                            'Acknowledgement', 1)
                """, (fixture["delivery_id"],))
                fixture["acknowledgement_id"] = cursor.lastrowid
            conn.commit()
            return fixture
        finally:
            conn.close()

    def table_rows(self, table):
        conn = self.open_database()
        try:
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY 1"
                ).fetchall()
            ]
        finally:
            conn.close()

    def snapshot(self):
        tables = (
            "shifts",
            "shift_staff",
            "staff_notice_occurrences",
            "staff_notice_deliveries",
            "staff_notice_delivery_history",
            "acknowledgements",
            "activity_log"
        )
        return {table: self.table_rows(table) for table in tables}

    def test_manual_no_longer_required_preserves_assignment_and_history(self):
        fixture = self.create_bound_delivery()
        response = self.management_client().post(
            (
                f"/staff-notices/delivery/{fixture['delivery_id']}"
                "/manual-no-longer-required"
            ),
            data={
                "confirm_no_longer_required": "yes",
                "reason": "Requirement waived by management."
            }
        )

        self.assertEqual(response.status_code, 302)
        delivery = self.table_rows("staff_notice_deliveries")[0]
        self.assertEqual(delivery["requirement_status"], "No Longer Required")
        self.assertEqual(delivery["recipient_access"], 1)
        self.assertEqual(
            delivery["first_viewed_at_utc"],
            "2026-08-03T15:00:00Z"
        )
        self.assertEqual(
            delivery["eligibility_cutoff_at_utc"],
            "2026-08-03T14:00:00Z"
        )
        self.assertEqual(
            self.table_rows("shift_staff")[0]["active"],
            1
        )
        history = self.table_rows("staff_notice_delivery_history")
        self.assertEqual(
            [row["event_type"] for row in history],
            ["Assigned", "No Longer Required"]
        )
        self.assertEqual(
            history[-1]["reason_code"],
            "Manual No Longer Required"
        )
        self.assertEqual(
            history[-1]["reason_text"],
            "Requirement waived by management."
        )
        activities = self.table_rows("activity_log")
        self.assertEqual(
            [row["activity_type"] for row in activities],
            ["staff_notice_delivery_no_longer_required"]
        )

    def test_manual_no_longer_required_stale_acknowledged_and_unauthorized_are_write_free(
        self
    ):
        fixture = self.create_bound_delivery(acknowledged=True)
        path = (
            f"/staff-notices/delivery/{fixture['delivery_id']}"
            "/manual-no-longer-required"
        )
        before = self.snapshot()
        worker = self.management_client("Support Worker", 4)
        self.assertEqual(
            worker.post(path, data={
                "confirm_no_longer_required": "yes",
                "reason": "Unauthorized."
            }).status_code,
            403
        )
        self.assertEqual(
            self.management_client().post(path, data={
                "confirm_no_longer_required": "yes",
                "reason": "Acknowledged delivery."
            }).status_code,
            409
        )
        self.assertEqual(self.snapshot(), before)

        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE acknowledgements SET active = 0
                WHERE acknowledgement_id = ?
            """, (fixture["acknowledgement_id"],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET requirement_status = 'No Longer Required',
                    current_reason_code = 'Manual No Longer Required',
                    current_reason_text = 'Original reason.'
                WHERE delivery_id = ?
            """, (fixture["delivery_id"],))
            conn.commit()
        finally:
            conn.close()
        before_repeat = self.snapshot()
        repeated = self.management_client().post(path, data={
            "confirm_no_longer_required": "yes",
            "reason": "Changed stale reason."
        })
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(self.snapshot(), before_repeat)

    def test_manual_no_longer_required_activity_and_commit_failures_roll_back(self):
        for failure_target in ("activity", "commit"):
            with self.subTest(failure_target=failure_target):
                fixture = self.create_bound_delivery(viewed=False)
                before = self.snapshot()
                path = (
                    f"/staff-notices/delivery/{fixture['delivery_id']}"
                    "/manual-no-longer-required"
                )
                if failure_target == "activity":
                    patcher = mock.patch.object(
                        app,
                        "_log_staff_notice_delivery_transition",
                        side_effect=RuntimeError("controlled activity failure")
                    )
                else:
                    real_connection = self.open_database()
                    patcher = mock.patch.object(
                        app,
                        "get_db",
                        return_value=_CommitFailureConnection(real_connection)
                    )
                with patcher:
                    response = self.management_client().post(path, data={
                        "confirm_no_longer_required": "yes",
                        "reason": "Rollback requirement."
                    })
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.snapshot(), before)

                conn = self.open_database()
                try:
                    conn.execute(
                        "DELETE FROM staff_notice_delivery_history"
                    )
                    conn.execute("DELETE FROM staff_notice_deliveries")
                    conn.execute("DELETE FROM shift_staff")
                    conn.execute("DELETE FROM staff_notice_occurrences")
                    conn.execute("DELETE FROM shifts")
                    conn.execute("DELETE FROM staff_notice_schedules")
                    conn.execute("DELETE FROM staff_notice_audience_rules")
                    conn.execute("DELETE FROM staff_notice_audiences")
                    conn.execute("DELETE FROM staff_notices")
                    conn.commit()
                finally:
                    conn.close()

    def test_no_shift_confirmation_records_status_audit_and_is_idempotent(self):
        fixture = self.create_pending_occurrence()
        path = (
            f"/staff-notices/occurrence/{fixture['occurrence_id']}"
            "/no-shift-occurred"
        )
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            response = self.management_client(
                "Program Manager",
                2
            ).post(path, data={
                "confirm_no_shift_occurred": "yes",
                "reason": "The scheduled visit did not proceed."
            })
        self.assertEqual(response.status_code, 302)
        occurrence = self.table_rows("staff_notice_occurrences")[0]
        self.assertEqual(occurrence["occurrence_status"], "No Shift Occurred")
        self.assertEqual(
            occurrence["status_reason"],
            "The scheduled visit did not proceed."
        )
        self.assertEqual(
            occurrence["status_changed_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertEqual(occurrence["status_changed_by_user_id"], 2)
        self.assertEqual(self.table_rows("staff_notice_deliveries"), [])
        activity = self.table_rows("activity_log")[0]
        self.assertEqual(
            activity["activity_type"],
            "staff_notice_no_shift_occurred"
        )
        self.assertIn(
            "Previous status: Pending Shift; New status: No Shift Occurred",
            activity["details"]
        )
        before_repeat = self.snapshot()
        repeated = self.management_client().post(path, data={
            "confirm_no_shift_occurred": "yes",
            "reason": "A changed stale reason."
        })
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(self.snapshot(), before_repeat)

    def test_tracking_displays_only_the_approved_management_controls(self):
        fixture = self.create_pending_occurrence()
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            response = self.management_client().get(
                f"/staff-notices/{fixture['notice_id']}/tracking"
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Confirm No Shift Occurred", response.data)
        self.assertNotIn(b"Mark No Longer Required", response.data)

        self.delete_notice_fixture()
        fixture = self.create_bound_delivery(viewed=False)
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            response = self.management_client().get(
                f"/staff-notices/{fixture['notice_id']}/tracking"
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mark No Longer Required", response.data)
        self.assertNotIn(b"Confirm No Shift Occurred", response.data)

    def test_no_shift_confirmation_requires_passed_end_and_no_exact_shift(self):
        fixture = self.create_pending_occurrence()
        path = (
            f"/staff-notices/occurrence/{fixture['occurrence_id']}"
            "/no-shift-occurred"
        )
        before = self.snapshot()
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(
                2026, 8, 3, 22, 0, tzinfo=timezone.utc
            )
        ):
            response = self.management_client().post(path, data={
                "confirm_no_shift_occurred": "yes",
                "reason": "Too early."
            })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

        conn = self.open_database()
        try:
            conn.execute("""
                INSERT INTO shifts
                (
                    shift_id, client_id, shift_date, shift_type, status,
                    scheduled_start_time, scheduled_end_time
                )
                VALUES (20, 1, '2026-08-03', 'Day', 'Open',
                        '07:00', '15:00')
            """)
            conn.commit()
        finally:
            conn.close()
        before_match = self.snapshot()
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            response = self.management_client().post(path, data={
                "confirm_no_shift_occurred": "yes",
                "reason": "A shift exists."
            })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before_match)

    def test_no_shift_confirmation_authorization_activity_and_commit_failures(
        self
    ):
        fixture = self.create_pending_occurrence()
        path = (
            f"/staff-notices/occurrence/{fixture['occurrence_id']}"
            "/no-shift-occurred"
        )
        before = self.snapshot()
        self.assertEqual(
            self.management_client("Support Worker", 4).post(path, data={
                "confirm_no_shift_occurred": "yes",
                "reason": "Unauthorized."
            }).status_code,
            403
        )
        self.assertEqual(self.snapshot(), before)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ), mock.patch.object(
            app,
            "_log_staff_notice_no_shift_occurred",
            side_effect=RuntimeError("controlled activity failure")
        ):
            response = self.management_client().post(path, data={
                "confirm_no_shift_occurred": "yes",
                "reason": "Rollback confirmation."
            })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)

        real_connection = self.open_database()
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ), mock.patch.object(
            app,
            "get_db",
            return_value=_CommitFailureConnection(real_connection)
        ):
            response = self.management_client().post(path, data={
                "confirm_no_shift_occurred": "yes",
                "reason": "Commit rollback confirmation."
            })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_late_exact_shift_corrects_same_occurrence_and_assigns_delivery(self):
        fixture = self.create_pending_occurrence()
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.confirm_staff_notice_no_shift_occurred(
                conn,
                fixture["occurrence_id"],
                1,
                "Original no-shift confirmation.",
                self.FIXED_TIMESTAMP
            )
            conn.commit()
            conn.execute("""
                INSERT INTO shifts
                (
                    shift_id, client_id, shift_date, shift_type, status,
                    scheduled_start_time, scheduled_end_time
                )
                VALUES (30, 1, '2026-08-03', 'Day', 'Open',
                        '07:00', '15:00')
            """)
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (30, 4, '07:00', 1)
            """)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            result = app.reconcile_staff_notice_shift_sign_on(
                conn,
                30,
                4,
                "2026-08-04T01:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(result["deliveries_assigned"], 1)
        occurrences = self.table_rows("staff_notice_occurrences")
        self.assertEqual(len(occurrences), 1)
        occurrence = occurrences[0]
        self.assertEqual(occurrence["occurrence_id"], fixture["occurrence_id"])
        self.assertEqual(occurrence["shift_id"], 30)
        self.assertEqual(occurrence["occurrence_status"], "Active")
        self.assertIsNone(occurrence["status_reason"])
        self.assertEqual(
            occurrence["status_changed_at_utc"],
            "2026-08-04T01:00:00Z"
        )
        self.assertEqual(occurrence["status_changed_by_user_id"], 4)
        deliveries = self.table_rows("staff_notice_deliveries")
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["user_id"], 4)
        self.assertEqual(deliveries[0]["requirement_status"], "Required")
        history = self.table_rows("staff_notice_delivery_history")
        self.assertEqual([row["event_type"] for row in history], ["Assigned"])
        self.assertEqual(
            [row["activity_type"] for row in self.table_rows("activity_log")],
            [
                "staff_notice_no_shift_occurred",
                "staff_notice_no_shift_correction",
                "staff_notice_occurrence_bound_to_shift",
                "staff_notice_delivery_assigned"
            ]
        )

        before_repeat = self.snapshot()
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            repeated = app.reconcile_staff_notice_shift_sign_on(
                conn,
                30,
                4,
                "2026-08-04T01:05:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(repeated["deliveries_assigned"], 0)
        self.assertEqual(self.snapshot(), before_repeat)

    def test_late_exact_shift_corrects_after_notice_expiry(self):
        fixture = self.create_pending_occurrence()
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.confirm_staff_notice_no_shift_occurred(
                conn,
                fixture["occurrence_id"],
                1,
                "Original no-shift confirmation.",
                self.FIXED_TIMESTAMP
            )
            conn.execute("""
                UPDATE staff_notices
                SET until_withdrawn = 0,
                    expires_at_utc = '2026-08-03T23:00:00Z'
                WHERE notice_id = ?
            """, (fixture["notice_id"],))
            conn.commit()
            conn.execute("""
                INSERT INTO shifts
                (
                    shift_id, client_id, shift_date, shift_type, status,
                    scheduled_start_time, scheduled_end_time
                )
                VALUES (31, 1, '2026-08-03', 'Day', 'Open',
                        '07:00', '15:00')
            """)
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (31, 4, '07:00', 1)
            """)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            result = app.reconcile_staff_notice_shift_sign_on(
                conn,
                31,
                4,
                "2026-08-04T01:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(result["deliveries_assigned"], 1)
        occurrences = self.table_rows("staff_notice_occurrences")
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["shift_id"], 31)
        self.assertEqual(
            occurrences[0]["occurrence_id"],
            fixture["occurrence_id"]
        )

    def test_ambiguous_and_mismatched_shifts_do_not_bind_or_write(self):
        for mode in ("ambiguous", "mismatched"):
            with self.subTest(mode=mode):
                fixture = self.create_pending_occurrence()
                conn = self.open_database()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    app.confirm_staff_notice_no_shift_occurred(
                        conn,
                        fixture["occurrence_id"],
                        1,
                        "No original shift.",
                        self.FIXED_TIMESTAMP
                    )
                    conn.commit()
                    if mode == "ambiguous":
                        shifts = (
                            (40, 1, "2026-08-03", "Day"),
                            (41, 1, "2026-08-03", "Day")
                        )
                        sign_on_shift_id = 40
                    else:
                        shifts = ((42, 1, "2026-08-04", "Day"),)
                        sign_on_shift_id = 42
                    conn.executemany("""
                        INSERT INTO shifts
                        (
                            shift_id, client_id, shift_date, shift_type,
                            status, scheduled_start_time, scheduled_end_time
                        )
                        VALUES (?, ?, ?, ?, 'Open', '07:00', '15:00')
                    """, shifts)
                    conn.execute("""
                        INSERT INTO shift_staff
                        (shift_id, user_id, actual_start_time, active)
                        VALUES (?, 4, '07:00', 1)
                    """, (sign_on_shift_id,))
                    conn.commit()
                    before = self.snapshot()
                    conn.execute("BEGIN IMMEDIATE")
                    app.reconcile_staff_notice_shift_sign_on(
                        conn,
                        sign_on_shift_id,
                        4,
                        "2026-08-04T01:00:00Z"
                    )
                    conn.commit()
                finally:
                    conn.close()
                self.assertEqual(self.snapshot(), before)
                self.delete_notice_fixture()

    def delete_notice_fixture(self):
        conn = self.open_database()
        try:
            conn.execute("DELETE FROM activity_log")
            conn.execute("DELETE FROM staff_notice_delivery_history")
            conn.execute("DELETE FROM staff_notice_deliveries")
            conn.execute("DELETE FROM shift_staff")
            conn.execute("DELETE FROM staff_notice_occurrences")
            conn.execute("DELETE FROM shifts")
            conn.execute("DELETE FROM staff_notice_schedules")
            conn.execute("DELETE FROM staff_notice_audience_rules")
            conn.execute("DELETE FROM staff_notice_audiences")
            conn.execute("DELETE FROM staff_notices")
            conn.commit()
        finally:
            conn.close()

    def test_late_correction_failure_rolls_back_to_no_shift_snapshot(self):
        fixture = self.create_pending_occurrence()
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.confirm_staff_notice_no_shift_occurred(
                conn,
                fixture["occurrence_id"],
                1,
                "Original no-shift confirmation.",
                self.FIXED_TIMESTAMP
            )
            conn.commit()
            conn.execute("""
                INSERT INTO shifts
                (
                    shift_id, client_id, shift_date, shift_type, status,
                    scheduled_start_time, scheduled_end_time
                )
                VALUES (50, 1, '2026-08-03', 'Day', 'Open',
                        '07:00', '15:00')
            """)
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (50, 4, '07:00', 1)
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.snapshot()

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            with mock.patch.object(
                app,
                "_log_staff_notice_no_shift_correction",
                side_effect=RuntimeError("controlled correction failure")
            ):
                with self.assertRaises(RuntimeError):
                    app.reconcile_staff_notice_shift_sign_on(
                        conn,
                        50,
                        4,
                        "2026-08-04T01:00:00Z"
                    )
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
