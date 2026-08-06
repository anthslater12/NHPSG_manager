import re
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app
import add_staff_notices_tables as staff_notice_schema


STAFF_NOTICE_TABLES = (
    "staff_notices",
    "staff_notice_audiences",
    "staff_notice_audience_rules",
    "staff_notice_audience_eligibility_periods",
    "staff_notice_schedules",
    "staff_notice_schedule_shift_types",
    "staff_notice_schedule_weekdays",
    "staff_notice_occurrences",
    "staff_notice_deliveries",
    "staff_notice_delivery_history"
)


class DuplicateParentRowsCursor:

    def __init__(self, rows):
        self.rows = list(rows)

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class DuplicateParentRowsConnection:

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        cursor = self.connection.execute(sql, parameters)
        normalized_sql = " ".join(sql.split())

        if (
            "SELECT audience_id, notice_id, created_at_utc "
            "FROM staff_notice_audiences" in normalized_sql
            or (
                "FROM staff_notice_schedules s" in normalized_sql
                and "WHERE s.notice_id = ?" in normalized_sql
            )
        ):
            rows = cursor.fetchall()
            return DuplicateParentRowsCursor(rows + rows)

        return cursor

    def close(self):
        self.connection.close()


class PreviewWrapperConnection:

    def __init__(self, close_error=None):
        self.close_calls = 0
        self.close_error = close_error

    def close(self):
        self.close_calls += 1

        if self.close_error is not None:
            raise self.close_error


class PreviewCalculationConnection:

    def __init__(self, connection):
        self.connection = connection
        self.close_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    def execute(self, sql, parameters=()):
        return self.connection.execute(sql, parameters)

    def close(self):
        self.close_calls += 1

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


class StaffNoticePublicationPreviewTests(unittest.TestCase):

    FIXED_NOW = datetime(2026, 7, 31, 19, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "publication_preview.db"
        )
        self.original_database_name = app.DB_NAME
        self.original_testing = app.app.config.get("TESTING")
        self.original_now = app.get_application_now_utc
        self.addCleanup(self.restore_application_state)
        app.DB_NAME = self.database_path
        app.app.config["TESTING"] = True
        app.get_application_now_utc = lambda: self.FIXED_NOW
        self.create_database()
        self.client = app.app.test_client()

    def restore_application_state(self):
        app.DB_NAME = self.original_database_name
        app.app.config["TESTING"] = self.original_testing
        app.get_application_now_utc = self.original_now

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
                    scheduled_end_time TEXT
                );

                CREATE TABLE shift_staff (
                    shift_staff_id INTEGER PRIMARY KEY,
                    shift_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    actual_start_time TEXT,
                    actual_end_time TEXT,
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
                    active INTEGER NOT NULL DEFAULT 1
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
                (4, "Support Worker One", "Support Worker", 1),
                (5, "Behaviour Consultant", "Behaviour Consultant", 1),
                (6, "Inactive Worker", "Support Worker", 0),
                (7, "Support Worker Two", "Support Worker", 1),
                (8, "Other Role", "Specialist", 1)
            ))
            conn.executemany("""
                INSERT INTO clients (client_id, client_name, active)
                VALUES (?, ?, ?)
            """, (
                (1, "Active Client", 1),
                (2, "Other Client", 1),
                (3, "Inactive Client", 0)
            ))
            conn.commit()
        finally:
            conn.close()

    def open_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def execute(self, sql, parameters=()):
        conn = sqlite3.connect(self.database_path)

        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(sql, parameters)
            conn.commit()
        finally:
            conn.close()

    def execute_ignoring_checks(self, sql, parameters=()):
        conn = sqlite3.connect(self.database_path)

        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute(sql, parameters)
            conn.commit()
        finally:
            conn.close()

    def execute_without_foreign_keys(self, sql, parameters=()):
        conn = sqlite3.connect(self.database_path)

        try:
            self.assertEqual(
                conn.execute("PRAGMA foreign_keys").fetchone()[0],
                0
            )
            conn.execute(sql, parameters)
            conn.commit()
        finally:
            conn.close()

    def login(self, user_id, session_role):
        with self.client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = session_role
            session_data["full_name"] = "Preview User"
            session_data["last_activity"] = time.time()

    def create_notice(
        self,
        *,
        title="Policy Reminder",
        notice_text="Review the current operational policy.",
        priority="Important",
        client_id=None,
        status="Draft",
        draft_active=1,
        effective_start_at_utc="2026-08-01T07:00:00Z",
        expires_at_utc="2026-08-16T06:59:00Z",
        until_withdrawn=0,
        audience_rules=None,
        schedule=None,
        shift_types=(),
        weekdays=()
    ):
        if audience_rules is None:
            audience_rules = (
                {"rule_type": "All Support Workers"},
            )
        if schedule is None:
            schedule = {
                "occurrence_basis": "One Time",
                "recurrence_pattern": "Once",
                "shift_applicability": "None"
            }

        conn = sqlite3.connect(self.database_path)

        try:
            conn.execute("PRAGMA foreign_keys = ON")
            cur = conn.execute("""
                INSERT INTO staff_notices
                (
                    title,
                    notice_text,
                    priority,
                    client_id,
                    status,
                    draft_active,
                    effective_start_at_utc,
                    expires_at_utc,
                    until_withdrawn,
                    version_number,
                    created_by_user_id,
                    created_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?)
            """, (
                title,
                notice_text,
                priority,
                client_id,
                status,
                draft_active,
                effective_start_at_utc,
                expires_at_utc,
                until_withdrawn,
                "2026-07-30T19:00:00Z"
            ))
            notice_id = cur.lastrowid

            if audience_rules is not False:
                cur = conn.execute("""
                    INSERT INTO staff_notice_audiences
                    (notice_id, created_at_utc)
                    VALUES (?, ?)
                """, (notice_id, "2026-07-30T19:00:00Z"))
                audience_id = cur.lastrowid

                for rule in audience_rules:
                    conn.execute("""
                        INSERT INTO staff_notice_audience_rules
                        (
                            audience_id,
                            rule_type,
                            role_name,
                            user_id,
                            created_at_utc
                        )
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        audience_id,
                        rule["rule_type"],
                        rule.get("role_name"),
                        rule.get("user_id"),
                        "2026-07-30T19:00:00Z"
                    ))

            if schedule is not False:
                cur = conn.execute("""
                    INSERT INTO staff_notice_schedules
                    (
                        notice_id,
                        occurrence_basis,
                        recurrence_pattern,
                        shift_applicability,
                        interval_days,
                        recurrence_anchor_date,
                        specific_calendar_date,
                        specific_shift_client_id,
                        specific_shift_date,
                        specific_shift_type,
                        one_time_due_at_utc,
                        created_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    notice_id,
                    schedule["occurrence_basis"],
                    schedule["recurrence_pattern"],
                    schedule["shift_applicability"],
                    schedule.get("interval_days"),
                    schedule.get("recurrence_anchor_date"),
                    schedule.get("specific_calendar_date"),
                    schedule.get("specific_shift_client_id"),
                    schedule.get("specific_shift_date"),
                    schedule.get("specific_shift_type"),
                    schedule.get("one_time_due_at_utc"),
                    "2026-07-30T19:00:00Z"
                ))
                schedule_id = cur.lastrowid

                for shift_type in shift_types:
                    conn.execute("""
                        INSERT INTO staff_notice_schedule_shift_types
                        (schedule_id, shift_type)
                        VALUES (?, ?)
                    """, (schedule_id, shift_type))

                for weekday in weekdays:
                    conn.execute("""
                        INSERT INTO staff_notice_schedule_weekdays
                        (schedule_id, weekday_number)
                        VALUES (?, ?)
                    """, (schedule_id, weekday))

            conn.commit()
            return notice_id
        finally:
            conn.close()

    def add_shift(
        self,
        shift_id,
        shift_date,
        shift_type,
        *,
        client_id=1,
        status="Open",
        staff=()
    ):
        conn = sqlite3.connect(self.database_path)

        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("""
                INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status)
                VALUES (?, ?, ?, ?, ?)
            """, (
                shift_id,
                client_id,
                shift_date,
                shift_type,
                status
            ))
            for index, (user_id, active) in enumerate(staff, start=1):
                conn.execute("""
                    INSERT INTO shift_staff
                    (shift_staff_id, shift_id, user_id, actual_start_time, active)
                    VALUES (?, ?, ?, '07:00', ?)
                """, (shift_id * 100 + index, shift_id, user_id, active))
            conn.commit()
        finally:
            conn.close()

    def preview(self, notice_id, actor_user_id=1, now_utc=None):
        return app.get_staff_notice_publish_preview(
            notice_id,
            actor_user_id,
            now_utc or self.FIXED_NOW
        )

    def database_snapshot(self):
        conn = sqlite3.connect(self.database_path)

        try:
            objects = tuple(conn.execute("""
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                ORDER BY type, name
            """).fetchall())
            table_names = [
                row[0]
                for row in conn.execute("""
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                """).fetchall()
            ]
            rows = {
                table_name: tuple(
                    conn.execute(
                        f'SELECT * FROM "{table_name}" ORDER BY rowid'
                    ).fetchall()
                )
                for table_name in table_names
            }
            sequence = tuple(
                conn.execute(
                    "SELECT * FROM sqlite_sequence ORDER BY name"
                ).fetchall()
            )
            return objects, rows, sequence
        finally:
            conn.close()

    def assert_no_derived_rows(self):
        conn = self.open_database()
        for table_name in (
            "staff_notice_audience_eligibility_periods",
            "staff_notice_occurrences",
            "staff_notice_deliveries",
            "staff_notice_delivery_history",
            "acknowledgements",
            "activity_log"
        ):
            count = conn.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()[0]
            self.assertEqual(count, 0, table_name)

    def assert_route_blocker_is_read_only(
        self,
        notice_id,
        expected_message,
        *,
        actor_user_id=1
    ):
        self.login(actor_user_id, "Admin")
        before = self.database_snapshot()
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(expected_message, html)
        self.assertIn("Publication is blocked", html)
        self.assertEqual(self.database_snapshot(), before)
        return html

    def test_public_wrapper_owns_exactly_one_connection(self):
        wrapper_connection = PreviewWrapperConnection()
        internal_result = {
            "public_value": "unchanged",
            "_publication_audience_candidates": {4: {"user_id": 4}}
        }

        with mock.patch.object(
            app,
            "get_db",
            return_value=wrapper_connection
        ) as get_db_mock, mock.patch.object(
            app,
            "_build_staff_notice_publish_preview",
            return_value=internal_result
        ) as calculation_mock:
            result = app.get_staff_notice_publish_preview(
                9,
                1,
                self.FIXED_NOW
            )

        get_db_mock.assert_called_once_with()
        calculation_mock.assert_called_once_with(
            wrapper_connection,
            9,
            1,
            self.FIXED_NOW
        )
        self.assertEqual(wrapper_connection.close_calls, 1)
        self.assertEqual(result, {"public_value": "unchanged"})

    def test_public_wrapper_closes_connection_after_calculation_failure(self):
        wrapper_connection = PreviewWrapperConnection()
        calculation_error = ValueError("calculation failed")

        with mock.patch.object(
            app,
            "get_db",
            return_value=wrapper_connection
        ) as get_db_mock, mock.patch.object(
            app,
            "_build_staff_notice_publish_preview",
            side_effect=calculation_error
        ):
            with self.assertRaises(ValueError) as raised:
                app.get_staff_notice_publish_preview(
                    9,
                    1,
                    self.FIXED_NOW
                )

        get_db_mock.assert_called_once_with()
        self.assertIs(raised.exception, calculation_error)
        self.assertEqual(wrapper_connection.close_calls, 1)

    def test_public_wrapper_preserves_existing_close_error_behavior(self):
        calculation_error = ValueError("calculation failed")
        close_error = RuntimeError("close failed")
        wrapper_connection = PreviewWrapperConnection(close_error)

        with mock.patch.object(
            app,
            "get_db",
            return_value=wrapper_connection
        ), mock.patch.object(
            app,
            "_build_staff_notice_publish_preview",
            side_effect=calculation_error
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.get_staff_notice_publish_preview(
                    9,
                    1,
                    self.FIXED_NOW
                )

        self.assertIs(raised.exception, close_error)
        self.assertIs(raised.exception.__context__, calculation_error)
        self.assertEqual(wrapper_connection.close_calls, 1)

    def test_public_wrapper_surfaces_close_error_after_success(self):
        close_error = RuntimeError("close failed")
        wrapper_connection = PreviewWrapperConnection(close_error)

        with mock.patch.object(
            app,
            "get_db",
            return_value=wrapper_connection
        ), mock.patch.object(
            app,
            "_build_staff_notice_publish_preview",
            return_value={"public_value": "unchanged"}
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.get_staff_notice_publish_preview(
                    9,
                    1,
                    self.FIXED_NOW
                )

        self.assertIs(raised.exception, close_error)
        self.assertEqual(wrapper_connection.close_calls, 1)

    def test_internal_calculation_uses_but_does_not_own_connection(self):
        notice_id = self.create_notice()
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        self.addCleanup(raw_connection.close)
        supplied_connection = PreviewCalculationConnection(raw_connection)
        before = self.database_snapshot()
        self.assertFalse(raw_connection.in_transaction)

        with mock.patch.object(
            app,
            "get_db",
            side_effect=AssertionError("internal helper opened a connection")
        ):
            preview = app._build_staff_notice_publish_preview(
                supplied_connection,
                notice_id,
                1,
                self.FIXED_NOW
            )

        self.assertTrue(preview["ready_for_publication"])
        self.assertEqual(supplied_connection.close_calls, 0)
        self.assertEqual(supplied_connection.commit_calls, 0)
        self.assertEqual(supplied_connection.rollback_calls, 0)
        self.assertFalse(raw_connection.in_transaction)
        self.assertEqual(
            supplied_connection.execute("SELECT 1").fetchone()[0],
            1
        )
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_derived_rows()

    def test_internal_calculation_failure_does_not_own_connection(self):
        notice_id = self.create_notice()
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        self.addCleanup(raw_connection.close)
        supplied_connection = PreviewCalculationConnection(raw_connection)
        before = self.database_snapshot()
        self.assertFalse(raw_connection.in_transaction)

        with mock.patch.object(
            app,
            "get_db",
            side_effect=AssertionError("internal helper opened a connection")
        ):
            with self.assertRaises(PermissionError):
                app._build_staff_notice_publish_preview(
                    supplied_connection,
                    notice_id,
                    4,
                    self.FIXED_NOW
                )

        self.assertEqual(supplied_connection.close_calls, 0)
        self.assertEqual(supplied_connection.commit_calls, 0)
        self.assertEqual(supplied_connection.rollback_calls, 0)
        self.assertFalse(raw_connection.in_transaction)
        self.assertEqual(
            supplied_connection.execute("SELECT 1").fetchone()[0],
            1
        )
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_derived_rows()

    def test_internal_calculation_exposes_complete_non_shift_candidates(self):
        notice_id = self.create_notice(
            audience_rules=(
                {"rule_type": "All Support Workers"},
                {
                    "rule_type": "Selected Role",
                    "role_name": "Support Worker"
                },
                {"rule_type": "Applicable Shift Staff"}
            ),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(
            1,
            "2026-08-01",
            "Day",
            staff=((4, 1), (6, 1), (8, 1))
        )
        conn = app.get_db()

        try:
            internal_preview = app._build_staff_notice_publish_preview(
                conn,
                notice_id,
                1,
                self.FIXED_NOW
            )
        finally:
            conn.close()

        candidates = internal_preview[
            "_publication_audience_candidates"
        ]
        self.assertEqual(set(candidates), {4, 7})
        self.assertEqual(
            candidates[4]["qualification_sources"],
            [
                "All Support Workers",
                "Selected Role: Support Worker"
            ]
        )
        self.assertEqual(
            len(candidates[4]["qualification_sources"]),
            len(set(candidates[4]["qualification_sources"]))
        )
        self.assertNotIn(6, candidates)
        self.assertNotIn(8, candidates)
        self.assertEqual(
            {recipient["user_id"] for recipient in internal_preview["recipients"]},
            {4, 8}
        )
        self.assertNotIn(
            7,
            {
                recipient["user_id"]
                for recipient in internal_preview["recipients"]
            }
        )
        self.assertEqual(internal_preview["estimated_delivery_count"], 2)

        public_preview = self.preview(notice_id)
        self.assertNotIn(
            "_publication_audience_candidates",
            public_preview
        )

        self.login(1, "Admin")
        html = self.client.get(
            f"/staff-notices/{notice_id}/review"
        ).get_data(as_text=True)
        self.assertNotIn("_publication_audience_candidates", html)
        self.assertNotIn("Support Worker Two", html)

    def test_internal_blocked_calculation_is_completely_read_only(self):
        notice_id = self.create_notice(schedule=False)
        conn = app.get_db()
        before = self.database_snapshot()

        try:
            preview = app._build_staff_notice_publish_preview(
                conn,
                notice_id,
                1,
                self.FIXED_NOW
            )
        finally:
            conn.close()

        self.assertFalse(preview["ready_for_publication"])
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_derived_rows()

    def test_unauthenticated_request_redirects_to_login(self):
        notice_id = self.create_notice()
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_each_current_database_management_role_is_authorized(self):
        notice_id = self.create_notice()
        for user_id, role in ((1, "Admin"), (2, "Program Manager"), (3, "Director")):
            with self.subTest(role=role):
                self.login(user_id, "stale session role")
                response = self.client.get(
                    f"/staff-notices/{notice_id}/review"
                )
                self.assertEqual(response.status_code, 200)

    def test_current_database_role_overrides_elevated_session_role(self):
        notice_id = self.create_notice()
        self.login(4, "Admin")
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 403)

    def test_current_database_manager_overrides_stale_session_denial(self):
        notice_id = self.create_notice()
        self.login(1, "Support Worker")
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)

    def test_nonmanagement_inactive_and_missing_users_are_denied(self):
        notice_id = self.create_notice()
        for user_id, role in (
            (4, "Support Worker"),
            (5, "Behaviour Consultant"),
            (6, "Support Worker"),
            (8, "Specialist"),
            (999, "Admin")
        ):
            with self.subTest(user_id=user_id):
                self.login(user_id, role)
                response = self.client.get(
                    f"/staff-notices/{notice_id}/review"
                )
                self.assertEqual(response.status_code, 403)

    def test_missing_and_invalid_notice_ids_return_not_found(self):
        self.login(1, "Admin")
        for path in (
            "/staff-notices/999/review",
            "/staff-notices/0/review",
            "/staff-notices/not-an-id/review",
            "/staff-notices/-1/review"
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_inactive_and_non_draft_lifecycle_states_return_conflict(self):
        notice_ids = [
            self.create_notice(draft_active=0),
            self.create_notice(),
            self.create_notice(),
            self.create_notice()
        ]
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.executemany("""
                UPDATE staff_notices
                SET status = ?,
                    published_at_utc = '2026-07-30T20:00:00Z'
                WHERE notice_id = ?
            """, (
                ("Published", notice_ids[1]),
                ("Withdrawn", notice_ids[2]),
                ("Replaced", notice_ids[3])
            ))
            conn.commit()
        finally:
            conn.close()
        self.login(1, "Admin")
        for notice_id in notice_ids:
            with self.subTest(notice_id=notice_id):
                response = self.client.get(
                    f"/staff-notices/{notice_id}/review"
                )
                self.assertEqual(response.status_code, 409)

    def test_valid_one_time_preview_and_until_withdrawn_warning(self):
        notice_id = self.create_notice(
            until_withdrawn=1,
            expires_at_utc=None
        )
        preview = self.preview(notice_id)
        self.assertTrue(preview["ready_for_publication"])
        self.assertEqual(preview["recipient_count"], 2)
        self.assertIn("one acknowledgement", preview["acknowledgement_description"])
        self.assertTrue(any(
            "no explicit due date" in warning
            for warning in preview["warnings"]
        ))

    def test_notice_field_and_period_blockers_are_collected(self):
        notice_id = self.create_notice()
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("""
                UPDATE staff_notices
                SET title = '   ',
                    notice_text = '',
                    priority = 'Bad',
                    effective_start_at_utc = NULL,
                    expires_at_utc = NULL,
                    until_withdrawn = 0
                WHERE notice_id = ?
            """, (notice_id,))
            conn.commit()
        finally:
            conn.close()
        preview = self.preview(notice_id)
        joined = " ".join(preview["blocking_errors"])
        self.assertIn("title", joined.lower())
        self.assertIn("text", joined.lower())
        self.assertIn("priority", joined.lower())
        self.assertIn("effective", joined.lower())
        self.assertIn("expiry", joined.lower())

    def test_invalid_until_withdrawn_is_blocking_and_read_only(self):
        notice_id = self.create_notice()
        self.execute_ignoring_checks("""
            UPDATE staff_notices
            SET until_withdrawn = 2
            WHERE notice_id = ?
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "Until Withdrawn must be either 0 or 1."
        )

    def test_until_withdrawn_with_expiry_is_blocking_and_read_only(self):
        notice_id = self.create_notice()
        self.execute("""
            UPDATE staff_notices
            SET until_withdrawn = 1
            WHERE notice_id = ?
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "An Until Withdrawn notice cannot have an expiry."
        )

    def test_expiry_before_effective_is_blocking_and_read_only(self):
        notice_id = self.create_notice()
        self.execute_ignoring_checks("""
            UPDATE staff_notices
            SET expires_at_utc = '2026-08-01T06:59:00Z'
            WHERE notice_id = ?
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "Expiry cannot be before the effective start."
        )

    def test_inconsistent_lifecycle_fields_are_blocking_and_read_only(self):
        notice_id = self.create_notice()
        self.execute("""
            UPDATE staff_notices
            SET published_by_user_id = 2,
                published_at_utc = '2026-07-30T20:00:00Z'
            WHERE notice_id = ?
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "The Draft contains inconsistent publication lifecycle data."
        )

    def test_malformed_effective_timestamp_is_blocking_and_read_only(self):
        notice_id = self.create_notice()
        self.execute_ignoring_checks("""
            UPDATE staff_notices
            SET effective_start_at_utc = 'not-a-timestamp'
            WHERE notice_id = ?
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "Effective start is not a valid UTC timestamp."
        )

    def test_malformed_expiry_timestamp_is_blocking_and_read_only(self):
        notice_id = self.create_notice()
        self.execute("""
            UPDATE staff_notices
            SET expires_at_utc = 'not-a-timestamp'
            WHERE notice_id = ?
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "Expiry is not a valid UTC timestamp."
        )

    def test_missing_creator_reference_is_blocking_and_read_only(self):
        notice_id = self.create_notice()
        self.execute_without_foreign_keys("""
            UPDATE staff_notices
            SET created_by_user_id = 999
            WHERE notice_id = ?
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "The Staff Notice creator reference could not be resolved."
        )

    def test_missing_updater_reference_is_blocking_and_read_only(self):
        notice_id = self.create_notice()
        self.execute_without_foreign_keys("""
            UPDATE staff_notices
            SET updated_by_user_id = 999,
                updated_at_utc = '2026-07-30T20:00:00Z'
            WHERE notice_id = ?
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "The Staff Notice updater reference could not be resolved."
        )

    def test_inactive_client_scope_is_blocking(self):
        notice_id = self.create_notice(client_id=3)
        preview = self.preview(notice_id)
        self.assertTrue(any(
            "active client" in error
            for error in preview["blocking_errors"]
        ))

    def test_past_effective_warns_but_current_notice_is_not_blocked(self):
        notice_id = self.create_notice(
            effective_start_at_utc="2026-07-30T07:00:00Z"
        )
        preview = self.preview(notice_id)
        self.assertTrue(preview["ready_for_publication"])
        self.assertTrue(any(
            "already in the past" in warning
            for warning in preview["warnings"]
        ))

    def test_already_ended_notice_is_blocked(self):
        notice_id = self.create_notice(
            effective_start_at_utc="2026-07-29T07:00:00Z",
            expires_at_utc="2026-07-30T07:00:00Z"
        )
        preview = self.preview(notice_id)
        self.assertTrue(any(
            "already ended" in error
            for error in preview["blocking_errors"]
        ))

    def test_missing_audience_and_schedule_are_both_reported(self):
        notice_id = self.create_notice(audience_rules=False, schedule=False)
        preview = self.preview(notice_id)
        joined = " ".join(preview["blocking_errors"])
        self.assertIn("one audience", joined.lower())
        self.assertIn("one schedule", joined.lower())

    def test_every_audience_rule_type_and_union_deduplication(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "Core Organization"},
            {"rule_type": "All Support Workers"},
            {"rule_type": "Selected Role", "role_name": "Support Worker"},
            {"rule_type": "Selected Individual", "user_id": 4}
        ))
        preview = self.preview(notice_id)
        self.assertEqual(preview["recipient_count"], 5)
        worker = next(
            item for item in preview["recipients"] if item["user_id"] == 4
        )
        self.assertEqual(len(worker["qualification_sources"]), 4)

    def test_selected_role_with_no_active_member_is_valid(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "Selected Role", "role_name": "Behaviour Consultant"},
        ))
        self.execute("UPDATE users SET active = 0 WHERE user_id = 5")
        preview = self.preview(notice_id)
        self.assertTrue(preview["ready_for_publication"])
        self.assertEqual(preview["recipient_count"], 0)
        self.assertTrue(any(
            "No currently identifiable" in warning
            for warning in preview["warnings"]
        ))

    def test_selected_individual_current_role_does_not_remove_eligibility(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "Selected Individual", "user_id": 5},
        ))
        self.execute("UPDATE users SET role = 'Specialist' WHERE user_id = 5")
        preview = self.preview(notice_id)
        self.assertEqual(preview["recipient_count"], 1)
        self.assertEqual(preview["recipients"][0]["role"], "Specialist")

    def test_role_based_eligibility_uses_the_current_database_role(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "All Support Workers"},
        ))
        self.execute(
            "UPDATE users SET role = 'Behaviour Consultant' WHERE user_id = 4"
        )
        preview = self.preview(notice_id)
        self.assertEqual(
            [recipient["user_id"] for recipient in preview["recipients"]],
            [7]
        )

    def test_inactive_selected_individual_is_blocking_and_excluded(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "Selected Individual", "user_id": 5},
        ))
        self.execute("UPDATE users SET active = 0 WHERE user_id = 5")
        preview = self.preview(notice_id)
        self.assertEqual(preview["recipient_count"], 0)
        self.assertTrue(any(
            "selected individual" in error.lower()
            for error in preview["blocking_errors"]
        ))

    def test_missing_selected_user_is_blocking_and_read_only(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "Selected Individual", "user_id": 5},
        ))
        self.execute_without_foreign_keys("""
            UPDATE staff_notice_audience_rules
            SET user_id = 999
            WHERE audience_id = (
                SELECT audience_id
                FROM staff_notice_audiences
                WHERE notice_id = ?
            )
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "Every selected individual must exist and be active."
        )

    def test_invalid_selected_role_is_blocking_and_read_only(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "Selected Role", "role_name": "Support Worker"},
        ))
        self.execute("""
            UPDATE staff_notice_audience_rules
            SET role_name = 'Specialist'
            WHERE audience_id = (
                SELECT audience_id
                FROM staff_notice_audiences
                WHERE notice_id = ?
            )
        """, (notice_id,))
        self.assert_route_blocker_is_read_only(
            notice_id,
            "A Selected Role audience rule is invalid."
        )

    def test_actual_duplicate_audience_rules_are_blocking_and_deduplicated(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "All Support Workers"},
        ))
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("DROP INDEX ux_staff_notice_audience_broad_rule")
            audience_id = conn.execute("""
                SELECT audience_id
                FROM staff_notice_audiences
                WHERE notice_id = ?
            """, (notice_id,)).fetchone()[0]
            conn.execute("""
                INSERT INTO staff_notice_audience_rules
                (audience_id, rule_type, created_at_utc)
                VALUES (?, 'All Support Workers', ?)
            """, (audience_id, "2026-07-30T19:00:00Z"))
            conn.commit()
        finally:
            conn.close()

        self.login(1, "Admin")
        before = self.database_snapshot()
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Duplicate audience rules", response.get_data(as_text=True))
        preview = self.preview(notice_id)
        self.assertFalse(preview["ready_for_publication"])
        self.assertEqual(preview["recipient_count"], 2)
        for recipient in preview["recipients"]:
            self.assertEqual(
                recipient["qualification_sources"],
                ["All Support Workers"]
            )
        self.assertEqual(self.database_snapshot(), before)

    def test_applicable_shift_staff_requires_shift_schedule(self):
        notice_id = self.create_notice(audience_rules=(
            {"rule_type": "Applicable Shift Staff"},
        ))
        preview = self.preview(notice_id)
        self.assertTrue(any(
            "requires a Shift schedule" in error
            for error in preview["blocking_errors"]
        ))

    def test_calendar_once_and_same_day_shortened_window(self):
        notice_id = self.create_notice(
            effective_start_at_utc="2026-07-31T07:00:00Z",
            schedule={
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Once",
                "shift_applicability": "None",
                "specific_calendar_date": "2026-07-31"
            }
        )
        preview = self.preview(notice_id)
        self.assertTrue(preview["ready_for_publication"])
        self.assertTrue(any(
            "shortens the acknowledgement window" in warning
            for warning in preview["warnings"]
        ))

    def test_every_recurring_calendar_pattern_has_future_occurrence(self):
        schedules = (
            ({
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None"
            }, (), ()),
            ({
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Interval Days",
                "shift_applicability": "None",
                "interval_days": 2,
                "recurrence_anchor_date": "2026-08-01"
            }, (), ()),
            ({
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "None"
            }, (), (0, 2))
        )
        for schedule, shift_types, weekdays in schedules:
            with self.subTest(pattern=schedule["recurrence_pattern"]):
                notice_id = self.create_notice(
                    schedule=schedule,
                    shift_types=shift_types,
                    weekdays=weekdays
                )
                self.assertTrue(self.preview(notice_id)["ready_for_publication"])

    def test_interval_requires_anchor_and_selected_children_are_required(self):
        cases = (
            ({
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Interval Days",
                "shift_applicability": "None",
                "interval_days": 2
            }, (), (), "anchor"),
            ({
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "None"
            }, (), (), "weekday"),
            ({
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Selected Shift Types"
            }, (), (), "shift type")
        )
        for schedule, shift_types, weekdays, expected in cases:
            with self.subTest(expected=expected):
                notice_id = self.create_notice(
                    schedule=schedule,
                    shift_types=shift_types,
                    weekdays=weekdays
                )
                joined = " ".join(
                    self.preview(notice_id)["blocking_errors"]
                ).lower()
                self.assertIn(expected, joined)

    def test_malformed_rule_schedule_and_unexpected_children_are_blocking(self):
        rule_id = self.create_notice()
        self.execute_ignoring_checks("""
            UPDATE staff_notice_audience_rules
            SET rule_type = 'Unknown Rule'
            WHERE audience_id = (
                SELECT audience_id
                FROM staff_notice_audiences
                WHERE notice_id = ?
            )
        """, (rule_id,))
        self.assertTrue(any(
            "invalid rule type" in error
            for error in self.preview(rule_id)["blocking_errors"]
        ))

        schedule_id = self.create_notice(schedule={
            "occurrence_basis": "Calendar",
            "recurrence_pattern": "Daily",
            "shift_applicability": "None"
        })
        conn = sqlite3.connect(self.database_path)
        try:
            stored_schedule_id = conn.execute(
                "SELECT schedule_id FROM staff_notice_schedules "
                "WHERE notice_id = ?",
                (schedule_id,)
            ).fetchone()[0]
            conn.execute("""
                INSERT INTO staff_notice_schedule_weekdays
                (schedule_id, weekday_number)
                VALUES (?, 1)
            """, (stored_schedule_id,))
            conn.execute("""
                INSERT INTO staff_notice_schedule_shift_types
                (schedule_id, shift_type)
                VALUES (?, 'Day')
            """, (stored_schedule_id,))
            conn.execute("PRAGMA ignore_check_constraints = ON")
            conn.execute("""
                UPDATE staff_notice_schedules
                SET occurrence_basis = 'Unknown Basis'
                WHERE schedule_id = ?
            """, (stored_schedule_id,))
            conn.commit()
        finally:
            conn.close()
        joined = " ".join(
            self.preview(schedule_id)["blocking_errors"]
        ).lower()
        self.assertIn("occurrence basis", joined)
        self.assertIn("weekday selections", joined)
        self.assertIn("shift-type selections", joined)

    def test_malformed_local_schedule_date_is_blocking_and_read_only(self):
        notice_id = self.create_notice(schedule={
            "occurrence_basis": "Calendar",
            "recurrence_pattern": "Once",
            "shift_applicability": "None",
            "specific_calendar_date": "not-a-date"
        })
        self.assert_route_blocker_is_read_only(
            notice_id,
            "Specific calendar date must use YYYY-MM-DD."
        )

    def test_parent_cardinality_anomalies_are_reported_together(self):
        notice_id = self.create_notice()
        conn = DuplicateParentRowsConnection(app.get_db())
        try:
            notice = app._load_staff_notice_publish_record(conn, notice_id)
        finally:
            conn.close()
        self.assertEqual(notice["audience_parent_count"], 2)
        self.assertEqual(notice["schedule_parent_count"], 2)

        original_get_db = app.get_db
        app.get_db = lambda: DuplicateParentRowsConnection(original_get_db())
        try:
            self.login(1, "Admin")
            before = self.database_snapshot()
            response = self.client.get(f"/staff-notices/{notice_id}/review")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True).lower()
            self.assertIn("exactly one audience", html)
            self.assertIn("exactly one schedule", html)
            self.assertEqual(self.database_snapshot(), before)
        finally:
            app.get_db = original_get_db

    def test_specific_past_calendar_date_has_no_future_occurrence(self):
        notice_id = self.create_notice(
            effective_start_at_utc="2026-07-01T07:00:00Z",
            expires_at_utc="2026-08-16T06:59:00Z",
            schedule={
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Once",
                "shift_applicability": "None",
                "specific_calendar_date": "2026-07-15"
            }
        )
        self.assertTrue(any(
            "no current or future" in error
            for error in self.preview(notice_id)["blocking_errors"]
        ))

    def test_due_equal_to_expiry_is_valid_and_due_after_is_blocking(self):
        equal_id = self.create_notice(schedule={
            "occurrence_basis": "One Time",
            "recurrence_pattern": "Once",
            "shift_applicability": "None",
            "one_time_due_at_utc": "2026-08-16T06:59:00Z"
        })
        later_id = self.create_notice(schedule={
            "occurrence_basis": "One Time",
            "recurrence_pattern": "Once",
            "shift_applicability": "None",
            "one_time_due_at_utc": "2026-08-16T07:00:00Z"
        })
        self.assertTrue(self.preview(equal_id)["ready_for_publication"])
        self.assertTrue(any(
            "after the notice expiry" in error
            for error in self.preview(later_id)["blocking_errors"]
        ))

    def test_past_explicit_one_time_due_warns(self):
        notice_id = self.create_notice(
            effective_start_at_utc="2026-07-29T07:00:00Z",
            schedule={
                "occurrence_basis": "One Time",
                "recurrence_pattern": "Once",
                "shift_applicability": "None",
                "one_time_due_at_utc": "2026-07-30T07:00:00Z"
            }
        )
        preview = self.preview(notice_id)
        self.assertTrue(any(
            "already in the past" in warning
            for warning in preview["warnings"]
        ))

    def test_due_before_effective_and_due_on_non_one_time_are_blocking(self):
        early_id = self.create_notice(schedule={
            "occurrence_basis": "One Time",
            "recurrence_pattern": "Once",
            "shift_applicability": "None",
            "one_time_due_at_utc": "2026-08-01T06:59:00Z"
        })
        self.assertTrue(any(
            "before the effective start" in error
            for error in self.preview(early_id)["blocking_errors"]
        ))

        calendar_id = self.create_notice(schedule={
            "occurrence_basis": "Calendar",
            "recurrence_pattern": "Daily",
            "shift_applicability": "None"
        })
        self.execute("""
            UPDATE staff_notice_schedules
            SET one_time_due_at_utc = '2026-08-02T07:00:00Z'
            WHERE notice_id = ?
        """, (calendar_id,))
        self.assertTrue(any(
            "only for a One Time" in error
            for error in self.preview(calendar_id)["blocking_errors"]
        ))

    def test_shift_intersection_and_operational_program_manager(self):
        notice_id = self.create_notice(
            client_id=1,
            audience_rules=(
                {"rule_type": "All Support Workers"},
            ),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(10, "2026-08-01", "Day", staff=((2, 1), (4, 1), (5, 1)))
        preview = self.preview(notice_id)
        self.assertEqual(
            [recipient["user_id"] for recipient in preview["recipients"]],
            [4]
        )

        applicable_id = self.create_notice(
            client_id=1,
            audience_rules=(
                {"rule_type": "Applicable Shift Staff"},
            ),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        applicable = self.preview(applicable_id)
        self.assertEqual(
            {recipient["user_id"] for recipient in applicable["recipients"]},
            {2, 4, 5}
        )

    def test_inactive_users_and_inactive_assignments_are_excluded(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(11, "2026-08-01", "Day", staff=((4, 0), (6, 1), (7, 1)))
        preview = self.preview(notice_id)
        self.assertEqual(
            [recipient["user_id"] for recipient in preview["recipients"]],
            [7]
        )

    def specific_shift_notice(self):
        return self.create_notice(
            client_id=1,
            expires_at_utc="2026-08-31T06:59:00Z",
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Specific Shift",
                "specific_shift_client_id": 1,
                "specific_shift_date": "2026-08-20",
                "specific_shift_type": "Overnight"
            }
        )

    def test_specific_shift_zero_one_and_multiple_match_behavior(self):
        notice_id = self.specific_shift_notice()
        self.login(1, "Admin")
        before = self.database_snapshot()
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("create a Pending Shift occurrence", html)
        self.assertIn(
            "Ready for the later controlled publication step",
            html
        )
        self.assertNotIn("Publication is blocked", html)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_derived_rows()

        no_match = self.preview(notice_id)
        self.assertTrue(no_match["ready_for_publication"])
        self.assertTrue(any(
            "No matching shift" in warning
            for warning in no_match["warnings"]
        ))
        self.assertTrue(any(
            "create a Pending Shift occurrence" in warning
            for warning in no_match["warnings"]
        ))

        self.add_shift(20, "2026-08-20", "Overnight", staff=((2, 1),))
        one_match = self.preview(notice_id)
        self.assertEqual(one_match["matching_shift_count"], 1)
        self.assertEqual(one_match["recipient_count"], 1)
        self.assertEqual(one_match["recipients"][0]["user_id"], 2)

        self.add_shift(21, "2026-08-20", "Overnight", staff=((4, 1),))
        multiple = self.preview(notice_id)
        self.assertFalse(multiple["ready_for_publication"])
        self.assertEqual(multiple["matching_shift_count"], 2)
        self.assertEqual(multiple["recipient_count"], 0)
        self.assertTrue(any(
            "cannot safely choose" in error
            for error in multiple["blocking_errors"]
        ))

    def test_cancelled_specific_shift_blocks_publication(self):
        notice_id = self.specific_shift_notice()
        self.add_shift(
            20,
            "2026-08-20",
            "Overnight",
            status="Cancelled",
            staff=((2, 0),)
        )

        preview = self.preview(notice_id)

        self.assertFalse(preview["ready_for_publication"])
        self.assertEqual(preview["matching_shifts"], [])
        self.assertTrue(any(
            "specific shift is cancelled" in error
            for error in preview["blocking_errors"]
        ))

    def test_blocked_future_specific_shift_does_not_promise_pending_creation(self):
        notice_id = self.create_notice(
            title="",
            client_id=1,
            expires_at_utc="2026-08-31T06:59:00Z",
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Specific Shift",
                "specific_shift_client_id": 1,
                "specific_shift_date": "2026-08-20",
                "specific_shift_type": "Overnight"
            }
        )
        self.login(1, "Admin")
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Notice title is required before publication.", html)
        self.assertIn("Publication is blocked", html)
        self.assertNotIn("create a Pending Shift occurrence", html)
        self.assertFalse(preview["ready_for_publication"])
        self.assertFalse(any(
            "create a Pending Shift occurrence" in warning
            for warning in preview["warnings"]
        ))
        self.assertEqual(self.database_snapshot(), before)

    def test_past_specific_shift_does_not_promise_pending_creation(self):
        notice_id = self.create_notice(
            effective_start_at_utc="2026-07-01T07:00:00Z",
            expires_at_utc="2026-08-31T06:59:00Z",
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Specific Shift",
                "specific_shift_client_id": 1,
                "specific_shift_date": "2026-07-15",
                "specific_shift_type": "Day"
            }
        )
        self.login(1, "Admin")
        before = self.database_snapshot()
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(
            "The schedule has no current or future applicable occurrence.",
            html
        )
        self.assertNotIn("create a Pending Shift occurrence", html)
        self.assertEqual(self.database_snapshot(), before)

    def test_malformed_specific_shift_does_not_promise_pending_creation(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Specific Shift",
                "specific_shift_client_id": 1,
                "specific_shift_date": "not-a-date",
                "specific_shift_type": "Day"
            }
        )
        self.login(1, "Admin")
        before = self.database_snapshot()
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Specific shift date must use YYYY-MM-DD.", html)
        self.assertNotIn("create a Pending Shift occurrence", html)
        self.assertEqual(self.database_snapshot(), before)

    def test_malformed_otherwise_matching_shift_date_is_blocking(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(22, "not-a-date", "Day", staff=((4, 1),))
        self.add_shift(23, "2026-08-01", "Day", staff=((7, 1),))
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        self.assertFalse(preview["ready_for_publication"])
        self.assertEqual(
            [shift["shift_id"] for shift in preview["matching_shifts"]],
            [23]
        )
        self.assertNotIn(
            22,
            [shift["shift_id"] for shift in preview["matching_shifts"]]
        )
        self.assertEqual(preview["matching_shift_count"], 1)
        self.assertEqual(preview["recipient_count"], 1)
        self.assertEqual(preview["estimated_delivery_count"], 1)
        self.assertEqual(
            [recipient["user_id"] for recipient in preview["recipients"]],
            [7]
        )
        self.assertEqual(
            preview["matching_shifts"][0]["estimated_delivery_count"],
            1
        )
        html = self.assert_route_blocker_is_read_only(
            notice_id,
            "A potentially applicable shift has a malformed stored date"
        )
        self.assertIn("cannot be evaluated safely", html)
        self.assertEqual(self.database_snapshot(), before)

    def test_missing_matching_shift_client_is_blocking(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(
            23,
            "2026-08-01",
            "Day",
            client_id=999,
            staff=((4, 1),)
        )
        self.add_shift(24, "2026-08-01", "Day", staff=((7, 1),))
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        self.assertFalse(preview["ready_for_publication"])
        self.assertEqual(
            [shift["shift_id"] for shift in preview["matching_shifts"]],
            [24]
        )
        self.assertNotIn(
            23,
            [shift["shift_id"] for shift in preview["matching_shifts"]]
        )
        self.assertEqual(preview["matching_shift_count"], 1)
        self.assertEqual(preview["recipient_count"], 1)
        self.assertEqual(preview["estimated_delivery_count"], 1)
        self.assertEqual(
            [recipient["user_id"] for recipient in preview["recipients"]],
            [7]
        )
        self.assertEqual(
            preview["matching_shifts"][0]["estimated_delivery_count"],
            1
        )
        self.assert_route_blocker_is_read_only(
            notice_id,
            "A potentially applicable shift has an unresolved client reference"
        )
        self.assertEqual(self.database_snapshot(), before)

    def test_missing_client_on_nonselected_weekday_is_irrelevant(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "Every Shift"
            },
            weekdays=(0,)
        )
        self.add_shift(
            25,
            "2026-08-04",
            "Day",
            client_id=999,
            staff=((4, 1),)
        )
        self.login(1, "Admin")
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("unresolved client reference", html)
        self.assertIn(
            "Ready for the later controlled publication step",
            html
        )
        self.assertTrue(preview["ready_for_publication"])
        self.assertEqual(preview["matching_shifts"], [])
        self.assertEqual(preview["matching_shift_count"], 0)
        self.assertEqual(preview["recipients"], [])
        self.assertEqual(preview["recipient_count"], 0)
        self.assertEqual(preview["estimated_delivery_count"], 0)
        self.assertEqual(self.database_snapshot(), before)

    def test_missing_client_on_nonmatching_interval_date_is_irrelevant(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Interval Days",
                "shift_applicability": "Every Shift",
                "interval_days": 2,
                "recurrence_anchor_date": "2026-08-01"
            }
        )
        self.add_shift(
            26,
            "2026-08-02",
            "Day",
            client_id=999,
            staff=((4, 1),)
        )
        self.login(1, "Admin")
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("unresolved client reference", html)
        self.assertIn(
            "Ready for the later controlled publication step",
            html
        )
        self.assertTrue(preview["ready_for_publication"])
        self.assertEqual(preview["matching_shifts"], [])
        self.assertEqual(preview["matching_shift_count"], 0)
        self.assertEqual(preview["recipients"], [])
        self.assertEqual(preview["recipient_count"], 0)
        self.assertEqual(preview["estimated_delivery_count"], 0)
        self.assertEqual(self.database_snapshot(), before)

    def test_missing_client_on_matching_recurrence_date_is_blocking(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "Every Shift"
            },
            weekdays=(0,)
        )
        self.add_shift(
            27,
            "2026-08-03",
            "Day",
            client_id=999,
            staff=((4, 1),)
        )
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        self.assertFalse(preview["ready_for_publication"])
        self.assertEqual(preview["matching_shifts"], [])
        self.assertEqual(preview["matching_shift_count"], 0)
        self.assertEqual(preview["recipients"], [])
        self.assertEqual(preview["recipient_count"], 0)
        self.assertEqual(preview["estimated_delivery_count"], 0)
        self.assert_route_blocker_is_read_only(
            notice_id,
            "A potentially applicable shift has an unresolved client reference"
        )
        self.assertEqual(self.database_snapshot(), before)

    def test_missing_client_with_malformed_date_remains_blocking(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "Every Shift"
            },
            weekdays=(0,)
        )
        self.add_shift(
            28,
            "not-a-date",
            "Day",
            client_id=999,
            staff=((4, 1),)
        )
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        self.assertFalse(preview["ready_for_publication"])
        self.assertEqual(preview["matching_shifts"], [])
        self.assertEqual(preview["matching_shift_count"], 0)
        self.assertEqual(preview["recipients"], [])
        self.assertEqual(preview["recipient_count"], 0)
        self.assertEqual(preview["estimated_delivery_count"], 0)
        html = self.assert_route_blocker_is_read_only(
            notice_id,
            "A potentially applicable shift has a malformed stored date"
        )
        self.assertIn("cannot be evaluated safely", html)
        self.assertEqual(self.database_snapshot(), before)

    def test_orphaned_active_shift_assignment_is_blocking(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(
            29,
            "2026-08-01",
            "Day",
            staff=((4, 1), (999, 1))
        )
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        self.assertFalse(preview["ready_for_publication"])
        self.assertEqual(preview["matching_shift_count"], 1)
        self.assertEqual(
            [shift["shift_id"] for shift in preview["matching_shifts"]],
            [29]
        )
        self.assertEqual(preview["recipient_count"], 1)
        self.assertEqual(preview["estimated_delivery_count"], 1)
        self.assertEqual(
            [recipient["user_id"] for recipient in preview["recipients"]],
            [4]
        )
        self.assertEqual(
            [
                recipient["user_id"]
                for recipient in preview["matching_shifts"][0]["recipients"]
            ],
            [4]
        )
        self.assertEqual(
            preview["matching_shifts"][0]["estimated_delivery_count"],
            1
        )
        self.assert_route_blocker_is_read_only(
            notice_id,
            "An active assignment on a matching shift references a missing user"
        )
        self.assertEqual(self.database_snapshot(), before)

    def test_duplicate_shift_assignments_do_not_inflate_estimates(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(25, "2026-08-01", "Day", staff=((4, 1), (4, 1)))
        before = self.database_snapshot()
        preview = self.preview(notice_id)
        self.assertTrue(preview["ready_for_publication"])
        self.assertEqual(preview["recipient_count"], 1)
        self.assertEqual(preview["estimated_delivery_count"], 1)
        self.assertEqual(
            preview["matching_shifts"][0]["estimated_delivery_count"],
            1
        )
        self.assertEqual(self.database_snapshot(), before)

    def test_general_duplicate_shift_tuples_remain_separate_occurrences(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(30, "2026-08-01", "Day", staff=((4, 1),))
        self.add_shift(31, "2026-08-01", "Day", staff=((4, 1),))
        preview = self.preview(notice_id)
        self.assertTrue(preview["ready_for_publication"])
        self.assertEqual(preview["matching_shift_count"], 2)
        self.assertEqual(preview["recipient_count"], 1)
        self.assertEqual(preview["estimated_delivery_count"], 2)

    def test_selected_shift_types_and_weekdays_filter_actual_shifts(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "Selected Shift Types"
            },
            shift_types=("Day", "Afternoon"),
            weekdays=(0, 2)
        )
        self.add_shift(40, "2026-08-03", "Day", staff=((4, 1),))
        self.add_shift(41, "2026-08-04", "Day", staff=((4, 1),))
        self.add_shift(42, "2026-08-05", "Overnight", staff=((4, 1),))
        preview = self.preview(notice_id)
        self.assertEqual(
            [shift["shift_id"] for shift in preview["matching_shifts"]],
            [40]
        )

    def test_interval_day_shift_schedule_filters_by_anchor(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Interval Days",
                "shift_applicability": "Every Shift",
                "interval_days": 2,
                "recurrence_anchor_date": "2026-08-01"
            }
        )
        self.add_shift(43, "2026-08-01", "Day", staff=((4, 1),))
        self.add_shift(44, "2026-08-02", "Day", staff=((4, 1),))
        self.add_shift(45, "2026-08-03", "Day", staff=((4, 1),))
        preview = self.preview(notice_id)
        self.assertEqual(
            [shift["shift_id"] for shift in preview["matching_shifts"]],
            [43, 45]
        )

    def test_once_shift_schedule_uses_the_effective_local_date(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Every Shift"
            }
        )
        self.add_shift(46, "2026-08-01", "Day", staff=((4, 1),))
        self.add_shift(47, "2026-08-02", "Day", staff=((4, 1),))
        preview = self.preview(notice_id)
        self.assertEqual(
            [shift["shift_id"] for shift in preview["matching_shifts"]],
            [46]
        )

    def test_overnight_uses_local_shift_start_date(self):
        notice_id = self.create_notice(
            audience_rules=({"rule_type": "Applicable Shift Staff"},),
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "Selected Shift Types"
            },
            shift_types=("Overnight",),
            weekdays=(0,)
        )
        self.add_shift(50, "2026-08-03", "Overnight", staff=((4, 1),))
        preview = self.preview(notice_id)
        self.assertEqual(preview["matching_shifts"][0]["shift_date"], "2026-08-03")

    def test_specific_shift_client_mismatch_is_blocking(self):
        notice_id = self.create_notice(
            client_id=2,
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Specific Shift",
                "specific_shift_client_id": 1,
                "specific_shift_date": "2026-08-20",
                "specific_shift_type": "Day"
            }
        )
        html = self.assert_route_blocker_is_read_only(
            notice_id,
            "Specific Shift client must match the notice scope."
        )
        self.assertNotIn("create a Pending Shift occurrence", html)

    def test_vancouver_display_and_daylight_saving_boundaries(self):
        winter_id = self.create_notice(
            effective_start_at_utc="2026-01-15T20:00:00Z",
            expires_at_utc=None,
            until_withdrawn=1
        )
        winter = self.preview(
            winter_id,
            now_utc=datetime(2026, 1, 15, 19, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(winter["effective_start_local"], "2026-01-15 12:00")

        summer_id = self.create_notice(
            effective_start_at_utc="2026-07-15T19:00:00Z",
            expires_at_utc=None,
            until_withdrawn=1
        )
        summer = self.preview(
            summer_id,
            now_utc=datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(summer["effective_start_local"], "2026-07-15 12:00")

        self.assertEqual(
            app.staff_notice_local_datetime_to_utc("2026-03-08T01:30"),
            datetime(2026, 3, 8, 9, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            app.staff_notice_local_datetime_to_utc("2026-11-01T00:30"),
            datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)
        )

    def test_valid_and_invalid_preview_gets_leave_complete_database_unchanged(self):
        valid_id = self.create_notice()
        invalid_id = self.create_notice(schedule=False)
        self.login(1, "Admin")
        before = self.database_snapshot()
        self.assertEqual(
            self.client.get(f"/staff-notices/{valid_id}/review").status_code,
            200
        )
        self.assertEqual(
            self.client.get(f"/staff-notices/{invalid_id}/review").status_code,
            200
        )
        self.assertEqual(self.database_snapshot(), before)

    def test_unauthorized_missing_and_repeated_gets_are_read_only(self):
        notice_id = self.create_notice()
        before = self.database_snapshot()
        self.login(4, "Admin")
        self.assertEqual(
            self.client.get(f"/staff-notices/{notice_id}/review").status_code,
            403
        )
        self.login(1, "Admin")
        self.assertEqual(
            self.client.get("/staff-notices/999/review").status_code,
            404
        )
        first = self.client.get(f"/staff-notices/{notice_id}/review")
        second = self.client.get(f"/staff-notices/{notice_id}/review")
        self.assertEqual(first.data, second.data)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_derived_rows()

    def test_template_contract_navigation_and_safe_escaping(self):
        notice_id = self.create_notice(
            title="<script>alert('x')</script>",
            notice_text="<b>Unsafe</b>"
        )
        self.login(1, "Admin")
        response = self.client.get(f"/staff-notices/{notice_id}/review")
        html = response.get_data(as_text=True)
        self.assertIn("Staff Notice Publication Preview", html)
        self.assertIn(
            "This is a read-only preview. The Staff Notice has not been",
            html
        )
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert('x')</script>", html)
        self.assertIn(f"/staff-notices/manage/{notice_id}", html)
        self.assertIn(f"/staff-notices/manage/{notice_id}/edit", html)
        preview_html = html[
            html.index("<h2>Staff Notice Publication Preview"):
            html.index('<div class="footer">')
        ].lower()
        self.assertEqual(preview_html.count("<form"), 1)
        self.assertIn('method="post"', preview_html)
        self.assertIn("<button", preview_html)
        self.assertIn("publish staff notice", preview_html)
        self.assertIn(f"/staff-notices/{notice_id}/publish", preview_html)
        self.assertEqual(
            preview_html.count('name="expected_updated_at_utc"'),
            1
        )
        self.assertNotIn('name="title"', preview_html)
        self.assertNotIn('name="notice_text"', preview_html)
        template_source = Path(
            "templates/staff_notice_publish_review.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("<script", template_source.lower())
        self.assertNotIn("|safe", template_source)
        self.assertIn("<form", template_source.lower())
        self.assertIn("<button", template_source.lower())
        self.assertIsNone(re.search(
            r"<input\b[^>]*\btype\s*=\s*(['\"])submit\1",
            template_source,
            re.IGNORECASE
        ))
        self.assertIsNotNone(re.search(
            r"url_for\s*\(\s*['\"][^'\"]*publish[^'\"]*['\"]",
            template_source,
            re.IGNORECASE
        ))

    def test_detail_has_review_link_only_for_active_draft(self):
        active_id = self.create_notice()
        inactive_id = self.create_notice(draft_active=0)
        self.login(1, "Admin")
        active_html = self.client.get(
            f"/staff-notices/manage/{active_id}"
        ).get_data(as_text=True)
        inactive_html = self.client.get(
            f"/staff-notices/manage/{inactive_id}"
        ).get_data(as_text=True)
        self.assertIn(f"/staff-notices/{active_id}/review", active_html)
        self.assertIn("Review for Publication", active_html)
        self.assertNotIn(f"/staff-notices/{inactive_id}/review", inactive_html)

    def test_admin_list_formats_created_and_updated_times_in_vancouver(self):
        notice_id = self.create_notice()
        second_id = self.create_notice()
        conn = sqlite3.connect(self.database_path)
        conn.execute("""
            UPDATE staff_notices
            SET created_at_utc = '2026-07-26T22:39:20Z',
                updated_at_utc = '2026-11-01T09:15:00Z'
            WHERE notice_id = ?
        """, (notice_id,))
        conn.execute(
            "UPDATE staff_notices SET updated_at_utc = NULL WHERE notice_id = ?",
            (second_id,)
        )
        conn.commit()
        conn.close()

        self.login(1, "Admin")
        html = self.client.get("/staff-notices/manage").get_data(as_text=True)
        self.assertIn("Jul 26, 2026 at 3:39 PM", html)
        self.assertIn("Nov 1, 2026 at 2:15 AM", html)
        self.assertIn("—", html)
        self.assertNotIn("2026-07-26T22:39:20Z", html)

    def test_all_staff_notice_templates_load(self):
        for template_name in (
            "staff_notice_admin_list.html",
            "staff_notice_admin_detail.html",
            "staff_notice_new.html",
            "staff_notice_edit.html",
            "staff_notice_publish_review.html"
        ):
            with self.subTest(template_name=template_name):
                self.assertIsNotNone(
                    app.app.jinja_env.get_template(template_name)
                )


if __name__ == "__main__":
    unittest.main()
