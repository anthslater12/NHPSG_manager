import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app
import add_staff_notices_tables as staff_notice_schema


class StaffNoticeReplacementTests(unittest.TestCase):
    FIXED_NOW = datetime(2026, 8, 1, 19, 30, tzinfo=timezone.utc)
    FIXED_TIMESTAMP = "2026-08-01T19:30:00Z"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "replacement.db"
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
                    status TEXT NOT NULL DEFAULT 'Open'
                );
                CREATE TABLE shift_staff (
                    shift_staff_id INTEGER PRIMARY KEY,
                    shift_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
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
                    acknowledgement_type TEXT NOT NULL,
                    acknowledged_at TEXT NOT NULL,
                    comments TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    invalidated_at_utc TEXT,
                    invalidated_by_user_id INTEGER,
                    invalidation_reason TEXT
                );
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
                (4, "Worker One", "Support Worker", 1),
                (5, "Worker Two", "Support Worker", 1),
                (6, "Inactive Admin", "Admin", 0)
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

    def create_published_notice(self):
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
                    'Original Notice', 'Immutable original content.',
                    'Urgent', 1, 'Published', 0,
                    '2026-07-01T08:00:00Z', NULL, 1, 3, 1,
                    '2026-07-01T19:00:00Z', 1,
                    '2026-07-01T19:05:00Z'
                )
            """)
            notice_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_audiences
                (notice_id, created_at_utc)
                VALUES (?, '2026-07-01T19:00:00Z')
            """, (notice_id,))
            audience_id = cursor.lastrowid
            conn.executemany("""
                INSERT INTO staff_notice_audience_rules
                (audience_id, rule_type, role_name, user_id, created_at_utc)
                VALUES (?, ?, ?, ?, '2026-07-01T19:00:00Z')
            """, (
                (audience_id, "All Support Workers", None, None),
                (audience_id, "Selected Role", "Director", None),
                (audience_id, "Selected Individual", None, 4)
            ))
            conn.execute("""
                INSERT INTO staff_notice_audience_eligibility_periods
                (
                    audience_id, user_id, eligible_from_at_utc,
                    eligibility_source_summary, created_at_utc
                )
                VALUES (
                    ?, 4, '2026-07-01T19:05:00Z',
                    'Original eligibility', '2026-07-01T19:05:00Z'
                )
            """, (audience_id,))
            cursor = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id, occurrence_basis, recurrence_pattern,
                    shift_applicability, interval_days,
                    recurrence_anchor_date, specific_calendar_date,
                    specific_shift_client_id, specific_shift_date,
                    specific_shift_type, one_time_due_at_utc, created_at_utc
                )
                VALUES (
                    ?, 'Shift', 'Selected Weekdays',
                    'Selected Shift Types', NULL, '2026-07-01', NULL,
                    NULL, NULL, NULL, NULL, '2026-07-01T19:00:00Z'
                )
            """, (notice_id,))
            schedule_id = cursor.lastrowid
            conn.executemany("""
                INSERT INTO staff_notice_schedule_shift_types
                (schedule_id, shift_type)
                VALUES (?, ?)
            """, ((schedule_id, "Day"), (schedule_id, "Overnight")))
            conn.executemany("""
                INSERT INTO staff_notice_schedule_weekdays
                (schedule_id, weekday_number)
                VALUES (?, ?)
            """, ((schedule_id, 0), (schedule_id, 4)))

            occurrence_ids = {}
            for status in (
                "Pending Shift",
                "Scheduled",
                "Active",
                "Closed",
                "No Shift Occurred",
                "Cancelled"
            ):
                cursor = conn.execute("""
                    INSERT INTO staff_notice_occurrences
                    (
                        schedule_id, occurrence_kind, occurrence_date,
                        planned_client_id, planned_shift_type,
                        occurrence_status, created_at_utc
                    )
                    VALUES (
                        ?, 'Shift', '2026-08-04', 1, 'Day', ?,
                        '2026-07-01T19:05:00Z'
                    )
                """, (schedule_id, status))
                occurrence_ids[status] = cursor.lastrowid

            delivery_ids = {}
            for label, occurrence_status, user_id, requirement, viewed in (
                ("outstanding", "Scheduled", 4, "Required", False),
                ("viewed", "Active", 5, "Required", True),
                ("acknowledged", "Closed", 4, "Required", True),
                (
                    "not_required",
                    "No Shift Occurred",
                    5,
                    "No Longer Required",
                    False
                )
            ):
                cursor = conn.execute("""
                    INSERT INTO staff_notice_deliveries
                    (
                        occurrence_id, user_id, requirement_status,
                        assigned_at_utc, eligibility_cutoff_at_utc,
                        first_viewed_at_utc, viewed_by_user_id,
                        recipient_access
                    )
                    VALUES (?, ?, ?, '2026-07-01T19:05:00Z',
                            '2026-07-01T19:05:00Z', ?, ?, 1)
                """, (
                    occurrence_ids[occurrence_status],
                    user_id,
                    requirement,
                    "2026-07-02T19:00:00Z" if viewed else None,
                    user_id if viewed else None
                ))
                delivery_ids[label] = cursor.lastrowid
                conn.execute("""
                    INSERT INTO staff_notice_delivery_history
                    (
                        delivery_id, event_type,
                        new_requirement_status, new_recipient_access,
                        changed_at_utc
                    )
                    VALUES (?, 'Assigned', 'Required', 1,
                            '2026-07-01T19:05:00Z')
                """, (cursor.lastrowid,))

            conn.execute("""
                INSERT INTO acknowledgements
                (
                    source_table, source_id, user_id,
                    acknowledgement_type, acknowledged_at, active
                )
                VALUES (
                    'staff_notice_deliveries', ?, 4,
                    'Staff Notice', '2026-07-03T19:00:00Z', 1
                )
            """, (delivery_ids["acknowledged"],))
            conn.commit()
            return notice_id, occurrence_ids, delivery_ids
        finally:
            conn.close()

    def client(self, user_id=1, role="Admin"):
        client = app.app.test_client()
        if user_id is not None:
            with client.session_transaction() as session_data:
                session_data["user_id"] = user_id
                session_data["role"] = role
                session_data["full_name"] = f"User {user_id}"
        return client

    def post_replacement(
        self,
        client,
        notice_id,
        *,
        reason="Corrected operational guidance.",
        confirmed=True
    ):
        data = {"replacement_reason": reason}
        if confirmed:
            data["confirm_replacement"] = "yes"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            return client.post(
                f"/staff-notices/{notice_id}/replace",
                data=data
            )

    def snapshot(self):
        conn = self.open_database()
        try:
            result = {}
            for table in (
                "staff_notices",
                "staff_notice_audiences",
                "staff_notice_audience_rules",
                "staff_notice_audience_eligibility_periods",
                "staff_notice_schedules",
                "staff_notice_schedule_shift_types",
                "staff_notice_schedule_weekdays",
                "staff_notice_occurrences",
                "staff_notice_deliveries",
                "staff_notice_delivery_history",
                "acknowledgements",
                "activity_log"
            ):
                result[table] = [
                    tuple(row)
                    for row in conn.execute(
                        f"SELECT * FROM {table} ORDER BY 1"
                    ).fetchall()
                ]
            return result
        finally:
            conn.close()

    def test_replacement_copies_configuration_and_preserves_history(self):
        notice_id, occurrence_ids, delivery_ids = (
            self.create_published_notice()
        )
        response = self.post_replacement(self.client(), notice_id)
        self.assertEqual(response.status_code, 302)

        conn = self.open_database()
        try:
            original = conn.execute("""
                SELECT *
                FROM staff_notices
                WHERE notice_id = ?
            """, (notice_id,)).fetchone()
            replacement = conn.execute("""
                SELECT *
                FROM staff_notices
                WHERE replaces_notice_id = ?
            """, (notice_id,)).fetchone()
            self.assertEqual(original["status"], "Replaced")
            self.assertEqual(original["replaced_by_user_id"], 1)
            self.assertEqual(
                original["replaced_at_utc"],
                self.FIXED_TIMESTAMP
            )
            self.assertEqual(
                original["replacement_reason"],
                "Corrected operational guidance."
            )
            self.assertEqual(replacement["status"], "Draft")
            self.assertEqual(replacement["draft_active"], 1)
            self.assertEqual(replacement["version_number"], 4)
            for field in (
                "title",
                "notice_text",
                "priority",
                "client_id",
                "effective_start_at_utc",
                "expires_at_utc",
                "until_withdrawn"
            ):
                self.assertEqual(replacement[field], original[field])
            self.assertEqual(
                replacement["created_at_utc"],
                self.FIXED_TIMESTAMP
            )
            self.assertIsNone(replacement["published_at_utc"])

            replacement_audience = conn.execute("""
                SELECT *
                FROM staff_notice_audiences
                WHERE notice_id = ?
            """, (replacement["notice_id"],)).fetchone()
            rules = conn.execute("""
                SELECT rule_type, role_name, user_id, created_at_utc
                FROM staff_notice_audience_rules
                WHERE audience_id = ?
                ORDER BY audience_rule_id
            """, (replacement_audience["audience_id"],)).fetchall()
            self.assertEqual(
                [tuple(row) for row in rules],
                [
                    (
                        "All Support Workers",
                        None,
                        None,
                        self.FIXED_TIMESTAMP
                    ),
                    (
                        "Selected Role",
                        "Director",
                        None,
                        self.FIXED_TIMESTAMP
                    ),
                    (
                        "Selected Individual",
                        None,
                        4,
                        self.FIXED_TIMESTAMP
                    )
                ]
            )
            replacement_schedule = conn.execute("""
                SELECT *
                FROM staff_notice_schedules
                WHERE notice_id = ?
            """, (replacement["notice_id"],)).fetchone()
            self.assertEqual(
                replacement_schedule["occurrence_basis"],
                "Shift"
            )
            self.assertEqual(
                replacement_schedule["recurrence_pattern"],
                "Selected Weekdays"
            )
            self.assertEqual(
                [
                    row["shift_type"]
                    for row in conn.execute("""
                        SELECT shift_type
                        FROM staff_notice_schedule_shift_types
                        WHERE schedule_id = ?
                        ORDER BY schedule_shift_type_id
                    """, (replacement_schedule["schedule_id"],))
                ],
                ["Day", "Overnight"]
            )
            self.assertEqual(
                [
                    row["weekday_number"]
                    for row in conn.execute("""
                        SELECT weekday_number
                        FROM staff_notice_schedule_weekdays
                        WHERE schedule_id = ?
                        ORDER BY schedule_weekday_id
                    """, (replacement_schedule["schedule_id"],))
                ],
                [0, 4]
            )
            self.assertEqual(
                conn.execute("""
                    SELECT COUNT(*)
                    FROM staff_notice_audience_eligibility_periods
                    WHERE audience_id = ?
                """, (replacement_audience["audience_id"],)).fetchone()[0],
                0
            )
            self.assertEqual(
                conn.execute("""
                    SELECT COUNT(*)
                    FROM staff_notice_occurrences
                    WHERE schedule_id = ?
                """, (replacement_schedule["schedule_id"],)).fetchone()[0],
                0
            )

            statuses = {
                row["occurrence_id"]: row["occurrence_status"]
                for row in conn.execute("""
                    SELECT occurrence_id, occurrence_status
                    FROM staff_notice_occurrences
                    WHERE occurrence_id IN ({})
                """.format(",".join("?" * len(occurrence_ids))), tuple(
                    occurrence_ids.values()
                ))
            }
            self.assertEqual(
                statuses[occurrence_ids["Pending Shift"]],
                "Cancelled"
            )
            self.assertEqual(
                statuses[occurrence_ids["Scheduled"]],
                "Cancelled"
            )
            for unchanged in (
                "Active",
                "Closed",
                "No Shift Occurred",
                "Cancelled"
            ):
                self.assertEqual(
                    statuses[occurrence_ids[unchanged]],
                    unchanged
                )

            deliveries = {
                row["delivery_id"]: dict(row)
                for row in conn.execute(
                    "SELECT * FROM staff_notice_deliveries"
                )
            }
            self.assertEqual(
                deliveries[delivery_ids["outstanding"]][
                    "requirement_status"
                ],
                "Cancelled"
            )
            self.assertEqual(
                deliveries[delivery_ids["viewed"]]["requirement_status"],
                "Cancelled"
            )
            self.assertEqual(
                deliveries[delivery_ids["acknowledged"]][
                    "requirement_status"
                ],
                "Required"
            )
            self.assertEqual(
                deliveries[delivery_ids["not_required"]][
                    "requirement_status"
                ],
                "No Longer Required"
            )
            self.assertTrue(all(
                delivery["recipient_access"] == 0
                for delivery in deliveries.values()
            ))
            self.assertEqual(
                deliveries[delivery_ids["viewed"]]["first_viewed_at_utc"],
                "2026-07-02T19:00:00Z"
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM acknowledgements"
                ).fetchone()[0],
                1
            )

            activity_types = [
                row["activity_type"]
                for row in conn.execute("""
                    SELECT activity_type
                    FROM activity_log
                    ORDER BY activity_id
                """)
            ]
            self.assertEqual(activity_types, [
                "staff_notice_occurrence_status_changed",
                "staff_notice_occurrence_status_changed",
                "staff_notice_delivery_cancelled",
                "staff_notice_delivery_access_revoked",
                "staff_notice_delivery_cancelled",
                "staff_notice_delivery_access_revoked",
                "staff_notice_delivery_access_revoked",
                "staff_notice_delivery_access_revoked",
                "staff_notice_replacement_created",
                "staff_notice_replaced"
            ])
            lifecycle_activities = conn.execute("""
                SELECT activity_type, related_id, summary, details
                FROM activity_log
                WHERE activity_type IN (
                    'staff_notice_replacement_created',
                    'staff_notice_replaced'
                )
                ORDER BY activity_id
            """).fetchall()
            self.assertEqual(
                lifecycle_activities[0]["related_id"],
                replacement["notice_id"]
            )
            self.assertEqual(
                lifecycle_activities[0]["summary"],
                "Staff Notice replacement draft created: Original Notice"
            )
            self.assertEqual(
                lifecycle_activities[0]["details"],
                (
                    f"Original notice ID: {notice_id}; Replacement notice "
                    f"ID: {replacement['notice_id']}; Version number: 4; "
                    "Reason: Corrected operational guidance.; Created at "
                    f"UTC: {self.FIXED_TIMESTAMP}"
                )
            )
            self.assertEqual(
                lifecycle_activities[1]["related_id"],
                notice_id
            )
            self.assertEqual(
                lifecycle_activities[1]["summary"],
                "Staff Notice replaced: Original Notice"
            )
            self.assertEqual(
                lifecycle_activities[1]["details"],
                (
                    f"Notice ID: {notice_id}; Replacement notice ID: "
                    f"{replacement['notice_id']}; Reason: Corrected "
                    "operational guidance.; Replaced at UTC: "
                    f"{self.FIXED_TIMESTAMP}; Occurrences cancelled: 2; "
                    "Deliveries cancelled: 2; Delivery access revoked: 4"
                )
            )
            history = conn.execute("""
                SELECT event_type, reason_code, reason_text, changed_at_utc
                FROM staff_notice_delivery_history
                WHERE event_type <> 'Assigned'
                ORDER BY delivery_history_id
            """).fetchall()
            self.assertTrue(history)
            for row in history:
                self.assertEqual(row["reason_code"], "Notice Replaced")
                self.assertEqual(
                    row["reason_text"],
                    "Corrected operational guidance."
                )
                self.assertEqual(
                    row["changed_at_utc"],
                    self.FIXED_TIMESTAMP
                )
        finally:
            conn.close()

    def test_retry_reuses_successor_without_writes_or_reason_change(self):
        notice_id, _, _ = self.create_published_notice()
        first = self.post_replacement(self.client(), notice_id)
        self.assertEqual(first.status_code, 302)
        before = self.snapshot()
        second = self.post_replacement(
            self.client(),
            notice_id,
            reason="A different reason on retry."
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_replaced_notice_without_successor_conflicts(self):
        notice_id, _, _ = self.create_published_notice()
        conn = self.open_database()
        try:
            conn.execute(
                "UPDATE staff_notices SET status = 'Replaced' "
                "WHERE notice_id = ?",
                (notice_id,)
            )
            conn.commit()
        finally:
            conn.close()
        before = self.snapshot()
        response = self.post_replacement(self.client(), notice_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_replaced_notice_with_multiple_successors_conflicts(self):
        notice_id, _, _ = self.create_published_notice()
        conn = self.open_database()
        try:
            conn.execute("DROP INDEX ux_staff_notices_replaces")
            conn.execute(
                "UPDATE staff_notices SET status = 'Replaced' "
                "WHERE notice_id = ?",
                (notice_id,)
            )
            for title in ("Successor One", "Successor Two"):
                conn.execute("""
                    INSERT INTO staff_notices
                    (
                        title, notice_text, status, draft_active,
                        version_number, replaces_notice_id,
                        created_by_user_id, created_at_utc
                    )
                    VALUES (?, 'Draft content', 'Draft', 1, 4, ?, 1,
                            '2026-08-01T19:00:00Z')
                """, (title, notice_id))
            conn.commit()
        finally:
            conn.close()
        before = self.snapshot()
        response = self.post_replacement(self.client(), notice_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)
        get_response = self.client().get(
            f"/staff-notices/{notice_id}/replace"
        )
        self.assertEqual(get_response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_authorization_and_required_form_values(self):
        notice_id, _, _ = self.create_published_notice()
        before = self.snapshot()
        get_response = self.client().get(
            f"/staff-notices/{notice_id}/replace"
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertIn(b"Replace Staff Notice", get_response.data)
        self.assertIn(b"new version 4 Draft", get_response.data)
        self.assertEqual(
            self.post_replacement(
                self.client(4, "Support Worker"),
                notice_id
            ).status_code,
            403
        )
        self.assertEqual(
            self.post_replacement(
                self.client(),
                notice_id,
                reason=""
            ).status_code,
            400
        )
        self.assertEqual(
            self.post_replacement(
                self.client(),
                notice_id,
                confirmed=False
            ).status_code,
            400
        )
        self.assertEqual(self.snapshot(), before)

    def test_activity_failure_rolls_back_complete_replacement(self):
        notice_id, _, _ = self.create_published_notice()
        before = self.snapshot()
        original_log_activity = app.log_activity

        def fail_replaced_activity(conn, *args, **kwargs):
            if kwargs.get("activity_type") == "staff_notice_replaced":
                raise sqlite3.OperationalError("forced activity failure")
            return original_log_activity(conn, *args, **kwargs)

        with mock.patch.object(
            app,
            "log_activity",
            side_effect=fail_replaced_activity
        ):
            response = self.post_replacement(self.client(), notice_id)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_configuration_copy_failure_rolls_back_complete_replacement(self):
        notice_id, _, _ = self.create_published_notice()
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER fail_replacement_shift_type_copy
                BEFORE INSERT ON staff_notice_schedule_shift_types
                WHEN NEW.schedule_id <> (
                    SELECT schedule_id
                    FROM staff_notice_schedules
                    ORDER BY schedule_id
                    LIMIT 1
                )
                BEGIN
                    SELECT RAISE(ABORT, 'forced configuration failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.snapshot()
        response = self.post_replacement(self.client(), notice_id)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_commit_failure_attempts_rollback_and_preserves_snapshot(self):
        notice_id, _, _ = self.create_published_notice()
        before = self.snapshot()
        real_get_db = app.get_db
        rollback_calls = []

        class CommitFailingConnection:
            def __init__(self, connection):
                self.connection = connection

            @property
            def in_transaction(self):
                return self.connection.in_transaction

            def execute(self, *args, **kwargs):
                return self.connection.execute(*args, **kwargs)

            def commit(self):
                raise sqlite3.OperationalError("forced commit failure")

            def rollback(self):
                rollback_calls.append(True)
                return self.connection.rollback()

            def close(self):
                return self.connection.close()

        def failing_get_db():
            return CommitFailingConnection(real_get_db())

        with mock.patch.object(app, "get_db", side_effect=failing_get_db):
            response = self.post_replacement(self.client(), notice_id)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(rollback_calls)
        self.assertEqual(self.snapshot(), before)

    def test_replaced_notice_denies_worker_but_management_keeps_history(self):
        notice_id, _, delivery_ids = self.create_published_notice()
        self.assertEqual(
            self.post_replacement(self.client(), notice_id).status_code,
            302
        )
        worker_response = self.client(
            4,
            "Support Worker"
        ).get(
            f"/staff-notices/delivery/{delivery_ids['outstanding']}"
        )
        self.assertIn(worker_response.status_code, (403, 404))
        management_response = self.client().get(
            f"/staff-notices/{notice_id}/tracking"
        )
        self.assertEqual(management_response.status_code, 200)
        self.assertIn(b"Corrected operational guidance.", management_response.data)


if __name__ == "__main__":
    unittest.main()
