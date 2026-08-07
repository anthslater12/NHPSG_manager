import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app
import add_shift_activities_table
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


class ShiftCancellationTests(unittest.TestCase):
    FIXED_NOW = datetime(2026, 8, 7, 18, 0, tzinfo=timezone.utc)
    FIXED_TIMESTAMP = "2026-08-07T18:00:00Z"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "shift-cancellation.db"
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
                    status TEXT NOT NULL DEFAULT 'Open',
                    scheduled_start_time TEXT,
                    scheduled_end_time TEXT,
                    actual_end_at_utc TEXT,
                    closed_at TEXT
                );
                CREATE TABLE shift_staff (
                    shift_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    sign_on_at TEXT,
                    actual_start_time TEXT,
                    actual_end_time TEXT,
                    actual_end_at_utc TEXT,
                    sign_off_at TEXT,
                    start_checklist_completed INTEGER NOT NULL DEFAULT 0,
                    end_checklist_completed INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL
                );
                CREATE TABLE shift_tasks (
                    shift_task_id INTEGER PRIMARY KEY,
                    task_name TEXT,
                    task_stage TEXT,
                    requires_input INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE care_tasks (
                    care_task_id INTEGER PRIMARY KEY,
                    task_name TEXT,
                    instructions TEXT,
                    occurs TEXT,
                    active INTEGER,
                    comment_required_attempted INTEGER NOT NULL DEFAULT 0,
                    comment_required_not_completed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE shift_care_task_entries (
                    entry_id INTEGER PRIMARY KEY,
                    shift_id INTEGER,
                    care_task_id INTEGER,
                    completed_by_user_id INTEGER,
                    outcome TEXT,
                    completed_at TEXT,
                    comment TEXT
                );
                CREATE TABLE housekeeping_tasks (
                    housekeeping_task_id INTEGER PRIMARY KEY,
                    task_name TEXT,
                    instructions TEXT,
                    occurs TEXT,
                    active INTEGER,
                    comment_required_attempted INTEGER NOT NULL DEFAULT 0,
                    comment_required_not_completed INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE shift_housekeeping_task_entries (
                    entry_id INTEGER PRIMARY KEY,
                    shift_id INTEGER,
                    housekeeping_task_id INTEGER,
                    completed_by_user_id INTEGER,
                    outcome TEXT,
                    completed_at TEXT,
                    comment TEXT
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
                    acknowledgement_type TEXT NOT NULL,
                    acknowledged_at TEXT NOT NULL,
                    comment TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    invalidated_at_utc TEXT,
                    invalidated_by_user_id INTEGER,
                    invalidation_reason TEXT
                );

                CREATE TABLE shift_notes (
                    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    shift_date TEXT NOT NULL,
                    shift_type TEXT NOT NULL,
                    note_text TEXT NOT NULL,
                    follow_up_required INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)
            add_shift_activities_table.migrate(conn)
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
                (5, "Inactive Admin", "Admin", 0)
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

    def seed_shift(self, *, status="Open", actual_end_at_utc=None):
        conn = self.open_database()
        try:
            cursor = conn.execute("""
                INSERT INTO shifts
                (
                    client_id, shift_date, shift_type, status,
                    scheduled_start_time, scheduled_end_time,
                    actual_end_at_utc
                )
                VALUES (
                    1, '2026-08-10', 'Day', ?, '07:00', '15:00', ?
                )
            """, (status, actual_end_at_utc))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_assignment(self, shift_id, **overrides):
        values = {
            "sign_on_at": None,
            "actual_start_time": None,
            "actual_end_time": None,
            "actual_end_at_utc": None,
            "sign_off_at": None,
            "start_checklist_completed": 0,
            "end_checklist_completed": 0,
            "active": 1
        }
        values.update(overrides)
        conn = self.open_database()
        try:
            cursor = conn.execute("""
                INSERT INTO shift_staff
                (
                    shift_id, user_id, sign_on_at, actual_start_time,
                    actual_end_time, actual_end_at_utc, sign_off_at,
                    start_checklist_completed, end_checklist_completed,
                    active
                )
                VALUES (?, 4, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                shift_id,
                values["sign_on_at"],
                values["actual_start_time"],
                values["actual_end_time"],
                values["actual_end_at_utc"],
                values["sign_off_at"],
                values["start_checklist_completed"],
                values["end_checklist_completed"],
                values["active"]
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_notice_delivery(
        self,
        shift_id,
        *,
        requirement_status="Required",
        recipient_access=1,
        acknowledged=False,
        occurrence_status="Scheduled"
    ):
        conn = self.open_database()
        try:
            cursor = conn.execute("""
                INSERT INTO staff_notices
                (
                    title, notice_text, priority, client_id, status,
                    draft_active, effective_start_at_utc, until_withdrawn,
                    version_number, created_by_user_id, created_at_utc,
                    published_by_user_id, published_at_utc
                )
                VALUES (
                    'Cancellation Notice', 'Preserved notice.', 'Important',
                    1, 'Published', 0, '2026-08-01T07:00:00Z', 1, 1, 1,
                    '2026-08-01T07:00:00Z', 1, '2026-08-01T07:05:00Z'
                )
            """)
            notice_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id, occurrence_basis, recurrence_pattern,
                    shift_applicability, specific_shift_client_id,
                    specific_shift_date, specific_shift_type, created_at_utc
                )
                VALUES (
                    ?, 'Shift', 'Once', 'Specific Shift', 1,
                    '2026-08-10', 'Day', '2026-08-01T07:00:00Z'
                )
            """, (notice_id,))
            schedule_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_occurrences
                (
                    schedule_id, occurrence_kind, occurrence_date,
                    planned_client_id, planned_shift_type, shift_id,
                    is_specific_shift_occurrence, occurrence_status,
                    created_at_utc, shift_bound_at_utc
                )
                VALUES (
                    ?, 'Shift', '2026-08-10', 1, 'Day', ?, 1, ?,
                    '2026-08-01T07:00:00Z', '2026-08-01T07:00:00Z'
                )
            """, (schedule_id, shift_id, occurrence_status))
            occurrence_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_deliveries
                (
                    occurrence_id, user_id, requirement_status,
                    recipient_access, assigned_at_utc,
                    eligibility_cutoff_at_utc
                )
                VALUES (
                    ?, 4, ?, ?, '2026-08-01T07:00:00Z',
                    '2026-08-10T22:00:00Z'
                )
            """, (
                occurrence_id,
                requirement_status,
                recipient_access
            ))
            delivery_id = cursor.lastrowid
            if acknowledged:
                conn.execute("""
                    INSERT INTO acknowledgements
                    (
                        source_table, source_id, user_id,
                        acknowledgement_type, acknowledged_at, active
                    )
                    VALUES (
                        'staff_notice_deliveries', ?, 4,
                        'Acknowledgement', '2026-08-02T07:00:00Z', 1
                    )
                """, (delivery_id,))
            conn.commit()
            return occurrence_id, delivery_id
        finally:
            conn.close()

    def snapshot(self):
        conn = self.open_database()
        try:
            return {
                table: [
                    tuple(row)
                    for row in conn.execute(
                        f"SELECT * FROM {table} ORDER BY 1"
                    ).fetchall()
                ]
                for table in (
                    "shifts",
                    "shift_staff",
                    "staff_notice_occurrences",
                    "staff_notice_deliveries",
                    "staff_notice_delivery_history",
                    "acknowledgements",
                    "activity_log"
                )
            }
        finally:
            conn.close()

    def cancel(self, shift_id, actor_user_id=1, reason="Client away"):
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = app.cancel_shift_in_transaction(
                conn,
                shift_id,
                actor_user_id,
                reason,
                self.FIXED_NOW
            )
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def test_cancellation_deactivates_never_started_assignments_and_audits_last(self):
        shift_id = self.seed_shift()
        assignment_id = self.seed_assignment(shift_id)

        result = self.cancel(shift_id)

        self.assertEqual(result["assignment_ids"], (assignment_id,))
        conn = self.open_database()
        try:
            shift = conn.execute(
                "SELECT * FROM shifts WHERE shift_id = ?",
                (shift_id,)
            ).fetchone()
            assignment = conn.execute(
                "SELECT * FROM shift_staff WHERE shift_staff_id = ?",
                (assignment_id,)
            ).fetchone()
            activities = conn.execute("""
                SELECT * FROM activity_log ORDER BY activity_id
            """).fetchall()
        finally:
            conn.close()
        self.assertEqual(shift["status"], "Cancelled")
        self.assertIsNone(shift["actual_end_at_utc"])
        self.assertEqual(assignment["active"], 0)
        for field in (
            "actual_end_time",
            "actual_end_at_utc",
            "sign_off_at"
        ):
            self.assertIsNone(assignment[field])
        self.assertEqual(
            [row["activity_type"] for row in activities],
            ["shift_cancelled"]
        )
        self.assertIn("Deactivated assignment count: 1", activities[0]["details"])

    def test_cancellation_uses_existing_notice_transitions_and_preserves_acknowledgement(self):
        shift_id = self.seed_shift()
        self.seed_assignment(shift_id)
        occurrence_id, outstanding_id = self.seed_notice_delivery(shift_id)
        _, acknowledged_id = self.seed_notice_delivery(
            shift_id,
            acknowledged=True
        )
        _, no_longer_required_id = self.seed_notice_delivery(
            shift_id,
            requirement_status="No Longer Required"
        )

        self.cancel(shift_id)

        conn = self.open_database()
        try:
            occurrences = conn.execute("""
                SELECT occurrence_status, status_reason
                FROM staff_notice_occurrences
                ORDER BY occurrence_id
            """).fetchall()
            deliveries = {
                row["delivery_id"]: row
                for row in conn.execute("""
                    SELECT * FROM staff_notice_deliveries
                    ORDER BY delivery_id
                """).fetchall()
            }
            history = conn.execute("""
                SELECT * FROM staff_notice_delivery_history
                ORDER BY delivery_history_id
            """).fetchall()
            activities = conn.execute("""
                SELECT activity_type FROM activity_log ORDER BY activity_id
            """).fetchall()
            acknowledgements = conn.execute(
                "SELECT * FROM acknowledgements"
            ).fetchall()
        finally:
            conn.close()
        self.assertTrue(all(
            row["occurrence_status"] == "Cancelled"
            and row["status_reason"] == "Shift Cancelled"
            for row in occurrences
        ))
        self.assertEqual(
            deliveries[outstanding_id]["requirement_status"],
            "Cancelled"
        )
        self.assertEqual(
            deliveries[acknowledged_id]["requirement_status"],
            "Required"
        )
        self.assertEqual(
            deliveries[no_longer_required_id]["requirement_status"],
            "No Longer Required"
        )
        self.assertTrue(all(
            row["recipient_access"] == 0
            for row in deliveries.values()
        ))
        self.assertEqual(len(acknowledgements), 1)
        self.assertEqual(acknowledgements[0]["active"], 1)
        self.assertEqual(
            [row["event_type"] for row in history],
            [
                "Cancelled",
                "Access Revoked",
                "Access Revoked",
                "Access Revoked"
            ]
        )
        self.assertEqual(activities[-1]["activity_type"], "shift_cancelled")

    def test_consistent_retry_is_write_free_and_ignores_changed_reason(self):
        shift_id = self.seed_shift()
        self.seed_assignment(shift_id)
        self.cancel(shift_id, reason="Original reason")
        before = self.snapshot()

        result = self.cancel(shift_id, reason="Changed reason")

        self.assertEqual(result["cancelled"], 0)
        self.assertEqual(self.snapshot(), before)

    def test_cancelled_shift_with_missing_or_duplicate_audit_conflicts(self):
        for audit_count in (0, 2):
            with self.subTest(audit_count=audit_count):
                shift_id = self.seed_shift(status="Cancelled")
                conn = self.open_database()
                try:
                    for _ in range(audit_count):
                        app.log_activity(
                            conn,
                            "SHIFT",
                            "shift_cancelled",
                            "Shift cancelled",
                            user_id=1,
                            client_id=1,
                            shift_id=shift_id,
                            related_table="shifts",
                            related_id=shift_id,
                            details=(
                                f"Shift ID: {shift_id}; Reason: Test; "
                                f"Effective at UTC: {self.FIXED_TIMESTAMP}"
                            )
                        )
                    conn.commit()
                finally:
                    conn.close()
                before = self.snapshot()
                with self.assertRaises(app.ShiftCancellationConflictError):
                    self.cancel(shift_id)
                self.assertEqual(self.snapshot(), before)

    def test_any_start_or_completion_evidence_blocks_without_writes(self):
        evidence = (
            ("sign_on_at", "2026-08-10 07:00:00"),
            ("actual_start_time", "07:00"),
            ("actual_end_time", "15:00"),
            ("actual_end_at_utc", "2026-08-10T22:00:00Z"),
            ("sign_off_at", "2026-08-10 15:00:00"),
            ("start_checklist_completed", 1),
            ("end_checklist_completed", 1)
        )
        for field, value in evidence:
            with self.subTest(field=field):
                shift_id = self.seed_shift()
                self.seed_assignment(shift_id, **{field: value})
                before = self.snapshot()
                with self.assertRaises(app.ShiftCancellationConflictError):
                    self.cancel(shift_id)
                self.assertEqual(self.snapshot(), before)

    def test_completed_shift_and_unauthorized_actor_are_write_free(self):
        cases = (
            (
                lambda: self.seed_shift(
                    actual_end_at_utc="2026-08-10T22:00:00Z"
                ),
                1,
                app.ShiftCancellationConflictError
            ),
            (self.seed_shift, 4, PermissionError),
            (self.seed_shift, 5, PermissionError)
        )
        for create_shift, actor_id, exception_type in cases:
            with self.subTest(actor_id=actor_id, exception=exception_type):
                shift_id = create_shift()
                before = self.snapshot()
                with self.assertRaises(exception_type):
                    self.cancel(shift_id, actor_user_id=actor_id)
                self.assertEqual(self.snapshot(), before)

    def test_all_approved_management_roles_can_cancel(self):
        for actor_id in (1, 2, 3):
            with self.subTest(actor_id=actor_id):
                shift_id = self.seed_shift()
                result = self.cancel(shift_id, actor_user_id=actor_id)
                self.assertEqual(result["cancelled"], 1)

    def test_management_list_finds_unstaffed_future_shift(self):
        shift_id = self.seed_shift()
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Program Manager"

        response = client.get("/shifts/manage")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"2026-08-10", response.data)
        self.assertIn(
            f"/shift/{shift_id}/cancel".encode(),
            response.data
        )

    def test_service_failure_rolls_back_every_change(self):
        shift_id = self.seed_shift()
        self.seed_assignment(shift_id)
        self.seed_notice_delivery(shift_id)
        before = self.snapshot()
        real_log_activity = app.log_activity

        def fail_on_shift_audit(conn, *args, **kwargs):
            activity_type = kwargs.get("activity_type")
            if activity_type is None and len(args) > 1:
                activity_type = args[1]
            if activity_type == "shift_cancelled":
                raise RuntimeError("controlled activity failure")
            return real_log_activity(conn, *args, **kwargs)

        with mock.patch.object(
            app,
            "log_activity",
            side_effect=fail_on_shift_audit
        ):
            with self.assertRaises(RuntimeError):
                self.cancel(shift_id)
        self.assertEqual(self.snapshot(), before)

    def test_commit_failure_route_rolls_back_and_returns_retryable_error(self):
        shift_id = self.seed_shift()
        self.seed_assignment(shift_id)
        before = self.snapshot()
        real_get_db = app.get_db

        def wrapped_get_db():
            return _CommitFailureConnection(real_get_db())

        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"
        with mock.patch.object(app, "get_db", side_effect=wrapped_get_db):
            response = client.post(
                f"/shift/{shift_id}/cancel",
                data={"reason": "Client away", "confirm": "yes"}
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Please retry", response.data)
        self.assertEqual(self.snapshot(), before)

    def test_cancelled_match_blocks_automatic_and_manual_recreation(self):
        shift_id = self.seed_shift(status="Cancelled")
        before = self.snapshot()
        with mock.patch.object(
            app,
            "get_current_shift_date",
            return_value=datetime(2026, 8, 10).date()
        ), mock.patch.object(
            app,
            "get_current_shift_type",
            return_value="Day"
        ):
            with self.assertRaises(app.StaffNoticeShiftSignOnError):
                app.auto_sign_on_user(4)
        self.assertEqual(self.snapshot(), before)

        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 4
            session_data["role"] = "Support Worker"
        response = client.post(
            "/shift/sign-on",
            data={
                "shift_date": "2026-08-10",
                "shift_type": "Day",
                "actual_start_time": "07:00"
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"This shift was cancelled", response.data)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(shift_id, 1)

    def test_cancelled_shift_blocks_staffing_and_completion_services(self):
        shift_id = self.seed_shift(status="Cancelled")
        assignment_id = self.seed_assignment(
            shift_id,
            actual_start_time="07:00"
        )
        before = self.snapshot()
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(app.ShiftCancellationConflictError):
                app.remove_shift_staff_assignment(
                    conn,
                    assignment_id,
                    1,
                    "Changed staffing",
                    self.FIXED_NOW
                )
            conn.rollback()

            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaises(app.ShiftStaffCompletionError):
                app.complete_shift_staff_assignment(
                    conn,
                    assignment_id,
                    "2026-08-10T22:00:00Z",
                    "2026-08-10T22:00:00Z",
                    4,
                    1
                )
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.snapshot(), before)

    def test_ambiguous_unbound_occurrence_is_not_bound_or_cancelled(self):
        shift_id = self.seed_shift()
        self.seed_shift()
        fixture_occurrence, _ = self.seed_notice_delivery(shift_id)
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET shift_id = NULL,
                    shift_bound_at_utc = NULL,
                    occurrence_status = 'Pending Shift'
                WHERE occurrence_id = ?
            """, (fixture_occurrence,))
            conn.commit()
        finally:
            conn.close()

        self.cancel(shift_id)

        conn = self.open_database()
        try:
            occurrence = conn.execute("""
                SELECT * FROM staff_notice_occurrences
                WHERE occurrence_id = ?
            """, (fixture_occurrence,)).fetchone()
        finally:
            conn.close()
        self.assertIsNone(occurrence["shift_id"])
        self.assertEqual(occurrence["occurrence_status"], "Pending Shift")

    def test_unique_applicable_pending_occurrence_is_bound_then_cancelled(self):
        shift_id = self.seed_shift()
        occurrence_id, delivery_id = self.seed_notice_delivery(shift_id)
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET shift_id = NULL,
                    shift_bound_at_utc = NULL,
                    occurrence_status = 'Pending Shift'
                WHERE occurrence_id = ?
            """, (occurrence_id,))
            conn.commit()
        finally:
            conn.close()

        self.cancel(shift_id)

        conn = self.open_database()
        try:
            occurrence = conn.execute("""
                SELECT * FROM staff_notice_occurrences
                WHERE occurrence_id = ?
            """, (occurrence_id,)).fetchone()
            delivery = conn.execute("""
                SELECT * FROM staff_notice_deliveries
                WHERE delivery_id = ?
            """, (delivery_id,)).fetchone()
            activities = conn.execute("""
                SELECT activity_type
                FROM activity_log
                ORDER BY activity_id
            """).fetchall()
        finally:
            conn.close()
        self.assertEqual(occurrence["shift_id"], shift_id)
        self.assertEqual(
            occurrence["shift_bound_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertEqual(occurrence["occurrence_status"], "Cancelled")
        self.assertEqual(delivery["requirement_status"], "Cancelled")
        self.assertEqual(delivery["recipient_access"], 0)
        self.assertEqual(
            [row["activity_type"] for row in activities],
            [
                "staff_notice_occurrence_bound_to_shift",
                "staff_notice_occurrence_status_changed",
                "staff_notice_delivery_cancelled",
                "staff_notice_delivery_access_revoked",
                "shift_cancelled"
            ]
        )

    def test_cancelled_operational_routes_reject_direct_urls(self):
        shift_id = self.seed_shift(status="Cancelled")
        conn = self.open_database()
        try:
            conn.execute("""
                INSERT INTO care_tasks
                (care_task_id, task_name, occurs, active)
                VALUES (1, 'Care task', 'Day', 1)
            """)
            conn.execute("""
                INSERT INTO housekeeping_tasks
                (housekeeping_task_id, task_name, occurs, active)
                VALUES (1, 'Housekeeping task', 'Day', 1)
            """)
            conn.execute("""
                INSERT INTO shift_care_task_entries
                (
                    entry_id, shift_id, care_task_id,
                    completed_by_user_id, outcome
                )
                VALUES (1, ?, 1, 4, 'Completed')
            """, (shift_id,))
            conn.execute("""
                INSERT INTO shift_housekeeping_task_entries
                (
                    entry_id, shift_id, housekeeping_task_id,
                    completed_by_user_id, outcome
                )
                VALUES (1, ?, 1, 4, 'Completed')
            """, (shift_id,))
            conn.commit()
        finally:
            conn.close()
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 4
            session_data["role"] = "Support Worker"
        for path in (
            f"/shift/{shift_id}/toileting-event/new",
            f"/shift/{shift_id}/start-checklist",
            f"/shift/{shift_id}/end-shift",
            f"/shift/{shift_id}/care-task/1/record",
            f"/shift/{shift_id}/care-task-entry/1/edit",
            f"/shift/{shift_id}/housekeeping-task/1/record"
        ):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 409)
        housekeeping_edit_response = client.get(
            f"/shift/{shift_id}/housekeeping-task-entry/1/edit"
        )
        self.assertEqual(housekeeping_edit_response.status_code, 404)
        food_fluid_response = client.get(
            f"/shift/{shift_id}/food-fluid"
        )
        self.assertEqual(food_fluid_response.status_code, 403)
        note_response = client.get(f"/shift/{shift_id}/note")
        self.assertEqual(note_response.status_code, 200)
        self.assertNotIn(b"<textarea", note_response.data)
        self.assertEqual(
            client.post(
                f"/shift/{shift_id}/note",
                data={"note_text": "Not allowed"}
            ).status_code,
            403
        )
        activity_response = client.get(
            f"/shift/{shift_id}/activity"
        )
        self.assertEqual(activity_response.status_code, 200)
        self.assertNotIn(b"<form method=\"post\">", activity_response.data)
        self.assertEqual(
            client.post(
                f"/shift/{shift_id}/activity",
                data={
                    "start_time": "09:00",
                    "end_time": "10:00",
                    "a_selected": "1",
                    "activity_description": "Not allowed",
                }
            ).status_code,
            403
        )

    def test_database_fixture_is_temporary(self):
        self.assertNotEqual(
            Path(self.database_path).resolve(),
            Path(self.original_database_name).resolve()
        )
        self.assertTrue(
            str(Path(self.database_path).resolve()).startswith(
                str(Path(self.temporary_directory.name).resolve())
            )
        )


if __name__ == "__main__":
    unittest.main()
