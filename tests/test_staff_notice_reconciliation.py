import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app
import add_staff_notices_tables as staff_notice_schema


class ReconciliationTrackingConnection:

    def __init__(self, connection):
        self.connection = connection
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        return self.connection.execute(sql, parameters)

    def commit(self):
        self.commit_calls += 1
        self.connection.commit()

    def rollback(self):
        self.rollback_calls += 1
        self.connection.rollback()

    def close(self):
        self.close_calls += 1
        self.connection.close()


class StaffNoticeReconciliationTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "reconciliation.db"
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
                    shift_staff_id INTEGER PRIMARY KEY,
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
                    task_name TEXT NOT NULL,
                    instructions TEXT,
                    task_stage TEXT NOT NULL,
                    requires_input INTEGER NOT NULL DEFAULT 0,
                    input_label TEXT,
                    input_type TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE shift_task_entries (
                    shift_task_entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    shift_id INTEGER NOT NULL,
                    shift_task_id INTEGER NOT NULL,
                    task_stage TEXT NOT NULL,
                    completed_by_user_id INTEGER NOT NULL,
                    input_value TEXT
                );

                CREATE TABLE shift_notes (
                    note_id INTEGER PRIMARY KEY,
                    shift_date TEXT,
                    shift_type TEXT,
                    client_id INTEGER,
                    user_id INTEGER,
                    note_text TEXT,
                    follow_up_required INTEGER,
                    created_at TEXT
                );

                CREATE TABLE care_tasks (
                    care_task_id INTEGER PRIMARY KEY,
                    task_name TEXT,
                    instructions TEXT,
                    occurs TEXT,
                    active INTEGER
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
                    active INTEGER
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
                (2, "Support Worker", "Support Worker", 1),
                (3, "Behaviour Consultant", "Behaviour Consultant", 1),
                (4, "Inactive Support Worker", "Support Worker", 0),
                (5, "Program Manager", "Program Manager", 1)
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

    def create_published_notice(
        self,
        *,
        occurrence_basis="Calendar",
        recurrence_pattern="Daily",
        shift_applicability="None",
        effective_start="2026-08-01T07:00:00Z",
        expires_at="2026-08-10T06:59:59Z",
        published_at="2026-08-01T16:00:00Z",
        audience_rules=(("All Support Workers", None, None),),
        recurrence_anchor_date=None,
        specific_calendar_date=None,
        weekdays=(),
        specific_shift_client_id=None,
        specific_shift_date=None,
        specific_shift_type=None,
        shift_types=()
    ):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
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
                    created_at_utc,
                    published_by_user_id,
                    published_at_utc
                )
                VALUES (
                    'Reconciliation Notice',
                    'Controlled reconciliation test.',
                    'Important',
                    1,
                    'Published',
                    0,
                    ?,
                    ?,
                    ?,
                    1,
                    1,
                    '2026-07-31T19:00:00Z',
                    1,
                    ?
                )
            """, (
                effective_start,
                expires_at,
                int(expires_at is None),
                published_at
            ))
            notice_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_audiences
                (notice_id, created_at_utc)
                VALUES (?, ?)
            """, (notice_id, published_at))
            audience_id = cursor.lastrowid
            conn.executemany("""
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
                (
                    audience_id,
                    rule_type,
                    role_name,
                    user_id,
                    published_at
                )
                for rule_type, role_name, user_id in audience_rules
            ))
            cursor = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id,
                    occurrence_basis,
                    recurrence_pattern,
                    shift_applicability,
                    recurrence_anchor_date,
                    specific_calendar_date,
                    specific_shift_client_id,
                    specific_shift_date,
                    specific_shift_type,
                    created_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                notice_id,
                occurrence_basis,
                recurrence_pattern,
                shift_applicability,
                recurrence_anchor_date,
                specific_calendar_date,
                specific_shift_client_id,
                specific_shift_date,
                specific_shift_type,
                published_at
            ))
            schedule_id = cursor.lastrowid
            conn.executemany("""
                INSERT INTO staff_notice_schedule_weekdays
                (schedule_id, weekday_number)
                VALUES (?, ?)
            """, ((schedule_id, weekday) for weekday in weekdays))
            conn.executemany("""
                INSERT INTO staff_notice_schedule_shift_types
                (schedule_id, shift_type)
                VALUES (?, ?)
            """, ((schedule_id, shift_type) for shift_type in shift_types))
            conn.commit()
            return {
                "notice_id": notice_id,
                "audience_id": audience_id,
                "schedule_id": schedule_id
            }
        finally:
            conn.close()

    def seed_eligibility(
        self,
        fixture,
        user_id,
        *,
        eligible_from="2026-08-01T16:00:00Z",
        eligible_until=None,
        sources="All Support Workers"
    ):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO staff_notice_audience_eligibility_periods
                (
                    audience_id,
                    user_id,
                    eligible_from_at_utc,
                    eligible_until_at_utc,
                    eligibility_source_summary,
                    created_at_utc,
                    updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                fixture["audience_id"],
                user_id,
                eligible_from,
                eligible_until,
                sources,
                eligible_from,
                eligible_until
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_one_time_occurrence(
        self,
        fixture,
        *,
        visible_from="2026-08-01T16:00:00Z",
        due_at=None
    ):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO staff_notice_occurrences
                (
                    schedule_id,
                    occurrence_kind,
                    visible_from_at_utc,
                    due_at_utc,
                    occurrence_status,
                    created_at_utc
                )
                VALUES (?, 'One Time', ?, ?, 'Active', ?)
            """, (
                fixture["schedule_id"],
                visible_from,
                due_at,
                visible_from
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_delivery(
        self,
        occurrence_id,
        user_id,
        *,
        assigned_at="2026-08-01T16:00:00Z",
        cutoff="2026-08-01T16:00:00Z"
    ):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO staff_notice_deliveries
                (
                    occurrence_id,
                    user_id,
                    assigned_at_utc,
                    eligibility_cutoff_at_utc
                )
                VALUES (?, ?, ?, ?)
            """, (occurrence_id, user_id, assigned_at, cutoff))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_pending_shift_occurrence(self, fixture, *, date, shift_type):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO staff_notice_occurrences
                (
                    schedule_id,
                    occurrence_kind,
                    occurrence_date,
                    planned_client_id,
                    planned_shift_type,
                    is_specific_shift_occurrence,
                    occurrence_status,
                    created_at_utc
                )
                VALUES (?, 'Shift', ?, 1, ?, 1, 'Pending Shift', ?)
            """, (
                fixture["schedule_id"],
                date,
                shift_type,
                "2026-08-01T16:00:00Z"
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_shift(
        self,
        *,
        date="2026-08-03",
        shift_type="Day",
        scheduled_end_time=None
    ):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO shifts
                (
                    client_id,
                    shift_date,
                    shift_type,
                    status,
                    scheduled_end_time
                )
                VALUES (1, ?, ?, 'Open', ?)
            """, (date, shift_type, scheduled_end_time))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_shift_staff(
        self,
        shift_id,
        user_id,
        actual_start_time="08:00"
    ):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, ?, ?, 1)
            """, (shift_id, user_id, actual_start_time))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_staff_notice_acknowledgement(
        self,
        delivery_id,
        user_id,
        acknowledged_at
    ):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO acknowledgements
                (
                    source_table,
                    source_id,
                    user_id,
                    acknowledgement_type,
                    acknowledged_at,
                    active
                )
                VALUES (
                    'staff_notice_deliveries',
                    ?,
                    ?,
                    'Acknowledgement',
                    ?,
                    1
                )
            """, (delivery_id, user_id, acknowledged_at))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def create_shift_notice_delivery(
        self,
        *,
        occurrence_basis="Shift",
        shift_applicability="Every Shift",
        recurrence_pattern="Daily",
        shift_date="2026-08-03",
        shift_type="Day",
        user_id=2,
        effective_start="2026-08-01T07:00:00Z",
        expires_at="2026-08-10T06:59:59Z",
        scheduled_end_time=None,
        actual_start_time="08:00"
    ):
        notice_arguments = {
            "occurrence_basis": occurrence_basis,
            "recurrence_pattern": recurrence_pattern,
            "shift_applicability": shift_applicability,
            "audience_rules": (("Applicable Shift Staff", None, None),),
            "effective_start": effective_start,
            "expires_at": expires_at
        }
        if shift_applicability == "Specific Shift":
            notice_arguments.update({
                "specific_shift_client_id": 1,
                "specific_shift_date": shift_date,
                "specific_shift_type": shift_type
            })
        fixture = self.create_published_notice(**notice_arguments)
        shift_id = self.seed_shift(
            date=shift_date,
            shift_type=shift_type,
            scheduled_end_time=scheduled_end_time
        )
        shift_staff_id = self.seed_shift_staff(
            shift_id,
            user_id,
            actual_start_time
        )
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                user_id,
                "2026-08-03T15:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        return fixture, shift_id, shift_staff_id

    def set_user(self, user_id, *, role=None, active=None):
        values = {}
        if role is not None:
            values["role"] = role
        if active is not None:
            values["active"] = active
        self.assertTrue(values)
        assignments = ", ".join(f"{key} = ?" for key in values)
        conn = self.open_database()

        try:
            conn.execute(
                f"UPDATE users SET {assignments} WHERE user_id = ?",
                (*values.values(), user_id)
            )
            conn.commit()
        finally:
            conn.close()

    def joined_rows(self, table_name, fixture):
        joins = {
            "staff_notice_audience_eligibility_periods": """
                JOIN staff_notice_audiences a
                    ON t.audience_id = a.audience_id
                WHERE a.notice_id = ?
            """,
            "staff_notice_occurrences": """
                JOIN staff_notice_schedules s
                    ON t.schedule_id = s.schedule_id
                WHERE s.notice_id = ?
            """,
            "staff_notice_deliveries": """
                JOIN staff_notice_occurrences o
                    ON t.occurrence_id = o.occurrence_id
                JOIN staff_notice_schedules s
                    ON o.schedule_id = s.schedule_id
                WHERE s.notice_id = ?
            """,
            "staff_notice_delivery_history": """
                JOIN staff_notice_deliveries d
                    ON t.delivery_id = d.delivery_id
                JOIN staff_notice_occurrences o
                    ON d.occurrence_id = o.occurrence_id
                JOIN staff_notice_schedules s
                    ON o.schedule_id = s.schedule_id
                WHERE s.notice_id = ?
            """
        }
        conn = self.open_database()

        try:
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT t.* FROM {table_name} t "
                    f"{joins[table_name]} ORDER BY t.rowid",
                    (fixture["notice_id"],)
                ).fetchall()
            ]
        finally:
            conn.close()

    def eligibility_rows(self, fixture):
        return self.joined_rows(
            "staff_notice_audience_eligibility_periods",
            fixture
        )

    def occurrence_rows(self, fixture):
        return self.joined_rows("staff_notice_occurrences", fixture)

    def delivery_rows(self, fixture):
        return self.joined_rows("staff_notice_deliveries", fixture)

    def delivery_history_rows(self, fixture):
        return self.joined_rows(
            "staff_notice_delivery_history",
            fixture
        )

    def activity_rows(self):
        conn = self.open_database()

        try:
            return [
                dict(row)
                for row in conn.execute("""
                    SELECT *
                    FROM activity_log
                    ORDER BY activity_id
                """).fetchall()
            ]
        finally:
            conn.close()

    def database_snapshot(self):
        conn = self.open_database()

        try:
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
            return {
                table_name: tuple(
                    conn.execute(
                        f'SELECT * FROM "{table_name}" ORDER BY rowid'
                    ).fetchall()
                )
                for table_name in table_names
            }
        finally:
            conn.close()

    def test_union_eligibility_opens_once_with_deterministic_sources(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Daily",
            shift_applicability="Every Shift",
            audience_rules=(
                ("Core Organization", None, None),
                ("All Support Workers", None, None),
                ("Selected Individual", None, 2),
                ("Selected Role", "Program Manager", None)
            )
        )

        result = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-02T19:00:00Z"
        )

        self.assertEqual(result["eligibility_started"], 3)
        rows = self.eligibility_rows(fixture)
        self.assertEqual([row["user_id"] for row in rows], [1, 2, 5])
        self.assertEqual(
            rows[1]["eligibility_source_summary"],
            "Core Organization, All Support Workers, Selected Individual"
        )
        self.assertEqual(
            rows[2]["eligibility_source_summary"],
            "Core Organization, Selected Role: Program Manager"
        )
        self.assertEqual(self.occurrence_rows(fixture), [])
        self.assertEqual(self.delivery_rows(fixture), [])
        self.assertEqual(
            [row["activity_type"] for row in self.activity_rows()],
            ["staff_notice_audience_eligibility_started"] * 3
        )

    def test_eligibility_closes_and_reopens_idempotently(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Daily",
            shift_applicability="Every Shift"
        )
        first_period_id = self.seed_eligibility(fixture, 2)
        self.set_user(2, role="Behaviour Consultant")

        closed = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-02T19:00:00Z"
        )

        self.assertEqual(closed["eligibility_ended"], 1)
        first_period = self.eligibility_rows(fixture)[0]
        self.assertEqual(first_period["eligibility_period_id"], first_period_id)
        self.assertEqual(
            first_period["eligible_until_at_utc"],
            "2026-08-02T19:00:00Z"
        )
        self.assertIsNone(first_period["closed_by_user_id"])
        self.assertIn("No longer matches", first_period["close_reason"])

        self.set_user(2, role="Support Worker")
        reopened = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T19:00:00Z"
        )
        no_op = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T19:00:00Z"
        )

        rows = self.eligibility_rows(fixture)
        self.assertEqual(reopened["eligibility_started"], 1)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[1]["eligible_until_at_utc"])
        self.assertEqual(no_op, app._staff_notice_reconciliation_result())
        self.assertEqual(
            [row["activity_type"] for row in self.activity_rows()],
            [
                "staff_notice_audience_eligibility_ended",
                "staff_notice_audience_eligibility_started"
            ]
        )

    def test_union_source_summary_updates_without_eligibility_transition(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Daily",
            shift_applicability="Every Shift",
            audience_rules=(
                ("All Support Workers", None, None),
                ("Selected Individual", None, 2)
            )
        )
        self.seed_eligibility(fixture, 2, sources="All Support Workers")

        result = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-02T19:00:00Z"
        )

        self.assertEqual(result["eligibility_sources_updated"], 1)
        self.assertEqual(
            self.eligibility_rows(fixture)[0][
                "eligibility_source_summary"
            ],
            "All Support Workers, Selected Individual"
        )
        self.assertEqual(self.activity_rows(), [])

    def test_newly_eligible_user_receives_current_one_time_notice(self):
        fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            expires_at=None
        )
        self.seed_eligibility(fixture, 2)
        occurrence_id = self.seed_one_time_occurrence(fixture)
        self.seed_delivery(occurrence_id, 2)
        self.set_user(4, active=1)

        result = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T19:00:00Z"
        )

        deliveries = self.delivery_rows(fixture)
        self.assertEqual(result["eligibility_started"], 1)
        self.assertEqual(result["deliveries_assigned"], 1)
        self.assertEqual([row["user_id"] for row in deliveries], [2, 4])
        self.assertEqual(
            deliveries[1]["eligibility_cutoff_at_utc"],
            "2026-08-03T19:00:00Z"
        )
        history = self.delivery_history_rows(fixture)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["event_type"], "Assigned")

    def test_one_time_delivery_requires_current_effective_period(self):
        future = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            effective_start="2026-08-05T07:00:00Z",
            expires_at="2026-08-10T06:59:59Z",
            published_at="2026-08-01T16:00:00Z"
        )
        self.seed_one_time_occurrence(
            future,
            visible_from="2026-08-05T07:00:00Z",
            due_at="2026-08-10T06:59:59Z"
        )
        expired = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            effective_start="2026-07-01T07:00:00Z",
            expires_at="2026-08-01T06:59:59Z",
            published_at="2026-07-01T16:00:00Z"
        )
        self.seed_one_time_occurrence(
            expired,
            visible_from="2026-07-01T16:00:00Z",
            due_at="2026-08-01T06:59:59Z"
        )

        app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T19:00:00Z"
        )

        self.assertEqual(self.delivery_rows(future), [])
        self.assertEqual(self.delivery_rows(expired), [])

    def test_newly_ineligible_user_gets_no_current_one_time_delivery(self):
        fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            expires_at=None
        )
        self.seed_eligibility(fixture, 2)
        self.seed_one_time_occurrence(fixture)
        self.set_user(2, role="Behaviour Consultant")

        result = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T19:00:00Z"
        )

        self.assertEqual(result["eligibility_ended"], 1)
        self.assertEqual(result["deliveries_assigned"], 0)
        self.assertEqual(self.delivery_rows(fixture), [])

    def test_newly_eligible_user_receives_only_future_calendar_occurrences(self):
        fixture = self.create_published_notice()
        self.seed_eligibility(fixture, 2)
        app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T19:00:00Z"
        )
        self.set_user(3, role="Support Worker")

        opened = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T20:00:00Z"
        )
        before_future = [
            row for row in self.delivery_rows(fixture)
            if row["user_id"] == 3
        ]
        app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-04T19:00:00Z"
        )
        occurrence_dates = {
            row["occurrence_id"]: row["occurrence_date"]
            for row in self.occurrence_rows(fixture)
        }
        new_user_dates = [
            occurrence_dates[row["occurrence_id"]]
            for row in self.delivery_rows(fixture)
            if row["user_id"] == 3
        ]

        self.assertEqual(opened["eligibility_started"], 1)
        self.assertEqual(before_future, [])
        self.assertEqual(new_user_dates, ["2026-08-04"])

    def test_calendar_materializes_exactly_at_effective_boundary(self):
        fixture = self.create_published_notice(
            effective_start="2026-08-03T20:00:00Z",
            published_at="2026-08-01T16:00:00Z"
        )
        self.seed_eligibility(fixture, 2)

        before_boundary = (
            app.reconcile_staff_notice_non_shift_requirements(
                "2026-08-03T19:59:59Z"
            )
        )

        self.assertEqual(
            before_boundary,
            app._staff_notice_reconciliation_result()
        )
        self.assertEqual(self.occurrence_rows(fixture), [])
        self.assertEqual(self.delivery_rows(fixture), [])
        self.assertEqual(self.delivery_history_rows(fixture), [])
        self.assertEqual(self.activity_rows(), [])

        at_boundary = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T20:00:00Z"
        )
        repeated = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T20:00:00Z"
        )

        occurrence = self.occurrence_rows(fixture)[0]
        delivery = self.delivery_rows(fixture)[0]
        self.assertEqual(at_boundary["occurrences_created"], 1)
        self.assertEqual(at_boundary["deliveries_assigned"], 1)
        self.assertEqual(occurrence["occurrence_date"], "2026-08-03")
        self.assertEqual(
            occurrence["visible_from_at_utc"],
            "2026-08-03T20:00:00Z"
        )
        self.assertEqual(occurrence["occurrence_status"], "Active")
        self.assertEqual(
            delivery["eligibility_cutoff_at_utc"],
            "2026-08-03T20:00:00Z"
        )
        self.assertEqual(repeated, app._staff_notice_reconciliation_result())
        self.assertEqual(len(self.activity_rows()), 2)
        self.assertEqual(len(self.delivery_history_rows(fixture)), 1)

    def test_existing_future_calendar_occurrence_defers_delivery(self):
        fixture = self.create_published_notice(
            effective_start="2026-08-03T20:00:00Z",
            published_at="2026-08-01T16:00:00Z"
        )
        self.seed_eligibility(fixture, 2)
        conn = self.open_database()
        try:
            conn.execute("""
                INSERT INTO staff_notice_occurrences
                (
                    schedule_id,
                    occurrence_kind,
                    occurrence_date,
                    visible_from_at_utc,
                    due_at_utc,
                    occurrence_status,
                    created_at_utc
                )
                VALUES (
                    ?,
                    'Calendar',
                    '2026-08-03',
                    '2026-08-03T20:00:00Z',
                    '2026-08-04T06:59:59Z',
                    'Scheduled',
                    '2026-08-01T16:00:00Z'
                )
            """, (fixture["schedule_id"],))
            conn.commit()
        finally:
            conn.close()

        before_boundary = (
            app.reconcile_staff_notice_non_shift_requirements(
                "2026-08-03T19:59:59Z"
            )
        )

        self.assertEqual(
            before_boundary,
            app._staff_notice_reconciliation_result()
        )
        self.assertEqual(self.delivery_rows(fixture), [])
        self.assertEqual(self.delivery_history_rows(fixture), [])
        self.assertEqual(self.activity_rows(), [])

        at_boundary = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T20:00:00Z"
        )
        repeated = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T20:00:00Z"
        )

        self.assertEqual(at_boundary["occurrences_created"], 0)
        self.assertEqual(at_boundary["deliveries_assigned"], 1)
        self.assertEqual(len(self.delivery_rows(fixture)), 1)
        self.assertEqual(len(self.delivery_history_rows(fixture)), 1)
        self.assertEqual(
            [row["activity_type"] for row in self.activity_rows()],
            ["staff_notice_delivery_assigned"]
        )
        self.assertEqual(repeated, app._staff_notice_reconciliation_result())

    def test_until_withdrawn_calendar_publishes_and_reconciles(self):
        payload = {
            "title": "Ongoing Calendar Notice",
            "notice_text": "Ongoing daily requirement.",
            "effective_start_local": "2026-08-01T00:00",
            "until_withdrawn": True,
            "audience_rules": [{
                "rule_type": "All Support Workers",
                "role_name": None,
                "user_id": None
            }],
            "schedule": {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None"
            }
        }

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(
                2026,
                7,
                31,
                19,
                0,
                tzinfo=timezone.utc
            )
        ):
            notice_id = app.create_staff_notice_draft(payload, 1)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(
                2026,
                8,
                1,
                19,
                0,
                tzinfo=timezone.utc
            )
        ):
            app.publish_staff_notice(notice_id, 1)

        fixture = {"notice_id": notice_id}
        reconciled = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-02T19:00:00Z"
        )
        repeated = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-02T19:00:00Z"
        )
        conn = self.open_database()
        try:
            notice = dict(conn.execute("""
                SELECT *
                FROM staff_notices
                WHERE notice_id = ?
            """, (notice_id,)).fetchone())
        finally:
            conn.close()

        self.assertEqual(notice["status"], "Published")
        self.assertEqual(notice["until_withdrawn"], 1)
        self.assertIsNone(notice["expires_at_utc"])
        self.assertEqual(
            [row["occurrence_date"] for row in self.occurrence_rows(fixture)],
            ["2026-08-01", "2026-08-02"]
        )
        self.assertEqual(len(self.delivery_rows(fixture)), 2)
        self.assertEqual(reconciled["occurrences_created"], 1)
        self.assertEqual(reconciled["deliveries_assigned"], 1)
        self.assertEqual(repeated, app._staff_notice_reconciliation_result())

    def test_downtime_recovery_generates_each_due_occurrence_once(self):
        fixture = self.create_published_notice(
            recurrence_pattern="Interval Days",
            recurrence_anchor_date="2026-08-01"
        )
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_schedules
                SET interval_days = 2
                WHERE schedule_id = ?
            """, (fixture["schedule_id"],))
            conn.commit()
        finally:
            conn.close()
        self.seed_eligibility(fixture, 2)

        first = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-06T19:00:00Z"
        )
        first_activity_count = len(self.activity_rows())
        first_history_count = len(self.delivery_history_rows(fixture))
        repeated = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-06T19:00:00Z"
        )

        self.assertEqual(first["occurrences_created"], 3)
        self.assertEqual(first["deliveries_assigned"], 3)
        self.assertEqual(
            [row["occurrence_date"] for row in self.occurrence_rows(fixture)],
            ["2026-08-01", "2026-08-03", "2026-08-05"]
        )
        self.assertEqual(len(self.delivery_rows(fixture)), 3)
        self.assertEqual(repeated, app._staff_notice_reconciliation_result())
        self.assertEqual(len(self.activity_rows()), first_activity_count)
        self.assertEqual(
            len(self.delivery_history_rows(fixture)),
            first_history_count
        )

    def test_inactive_user_gets_no_new_delivery_and_history_is_preserved(self):
        fixture = self.create_published_notice()
        self.seed_eligibility(fixture, 2)
        inactive_period_id = self.seed_eligibility(fixture, 4)

        app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-03T19:00:00Z"
        )

        self.assertEqual(
            [row["user_id"] for row in self.delivery_rows(fixture)],
            [2, 2, 2]
        )
        inactive_period = next(
            row for row in self.eligibility_rows(fixture)
            if row["eligibility_period_id"] == inactive_period_id
        )
        self.assertEqual(
            inactive_period["eligible_until_at_utc"],
            "2026-08-03T19:00:00Z"
        )

    def test_no_occurrence_or_requirement_precedes_publication(self):
        fixture = self.create_published_notice(
            effective_start="2026-08-01T07:00:00Z",
            published_at="2026-08-03T18:30:00Z"
        )
        self.seed_eligibility(
            fixture,
            2,
            eligible_from="2026-08-03T18:30:00Z"
        )

        before_publication = (
            app.reconcile_staff_notice_non_shift_requirements(
                "2026-08-02T19:00:00Z"
            )
        )
        self.assertEqual(
            before_publication,
            app._staff_notice_reconciliation_result()
        )
        self.assertEqual(self.occurrence_rows(fixture), [])
        self.assertEqual(self.delivery_rows(fixture), [])
        self.assertEqual(self.activity_rows(), [])

        app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-04T19:00:00Z"
        )

        occurrences = self.occurrence_rows(fixture)
        self.assertEqual(
            [row["occurrence_date"] for row in occurrences],
            ["2026-08-03", "2026-08-04"]
        )
        self.assertEqual(
            occurrences[0]["visible_from_at_utc"],
            "2026-08-03T18:30:00Z"
        )
        self.assertTrue(all(
            row["eligibility_cutoff_at_utc"] >= "2026-08-03T18:30:00Z"
            for row in self.delivery_rows(fixture)
        ))

    def test_real_transitions_have_authoritative_audits_and_no_op_has_none(self):
        fixture = self.create_published_notice(
            published_at="2026-08-02T19:00:00Z"
        )

        first = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-02T19:00:00Z"
        )
        activities = self.activity_rows()
        repeated = app.reconcile_staff_notice_non_shift_requirements(
            "2026-08-02T19:00:00Z"
        )

        self.assertEqual(first["eligibility_started"], 1)
        self.assertEqual(first["occurrences_created"], 1)
        self.assertEqual(first["deliveries_assigned"], 1)
        self.assertEqual(
            [row["activity_type"] for row in activities],
            [
                "staff_notice_audience_eligibility_started",
                "staff_notice_occurrence_created",
                "staff_notice_delivery_assigned"
            ]
        )
        for activity in activities:
            self.assertEqual(activity["activity_class"], "STAFF_NOTICE")
            self.assertEqual(activity["client_id"], 1)
            self.assertIsNone(activity["user_id"])
            self.assertIsNone(activity["shift_id"])
            self.assertEqual(activity["success"], 1)
            self.assertIn(
                f"Notice ID: {fixture['notice_id']}",
                activity["details"]
            )
        self.assertEqual(repeated, app._staff_notice_reconciliation_result())
        self.assertEqual(self.activity_rows(), activities)

    def test_failure_rolls_back_all_reconciliation_rows_and_audits(self):
        fixture = self.create_published_notice(
            published_at="2026-08-02T19:00:00Z"
        )
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_delivery_assignment_audit
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type = 'staff_notice_delivery_assigned'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled reconciliation audit failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        tracking = ReconciliationTrackingConnection(self.open_database())

        with mock.patch.object(app, "get_db", return_value=tracking):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled reconciliation audit failure"
            ):
                app.reconcile_staff_notice_non_shift_requirements(
                    "2026-08-02T19:00:00Z"
                )

        self.assertEqual(tracking.commit_calls, 0)
        self.assertEqual(tracking.rollback_calls, 1)
        self.assertEqual(tracking.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.eligibility_rows(fixture), [])
        self.assertEqual(self.occurrence_rows(fixture), [])
        self.assertEqual(self.delivery_rows(fixture), [])
        self.assertEqual(self.delivery_history_rows(fixture), [])
        self.assertEqual(self.activity_rows(), [])

    def test_calendar_boundaries_use_vancouver_dst_offsets(self):
        fixture = self.create_published_notice(
            effective_start="2024-11-03T07:00:00Z",
            expires_at="2024-11-05T07:59:59Z",
            published_at="2024-11-02T19:00:00Z"
        )
        self.seed_eligibility(
            fixture,
            2,
            eligible_from="2024-11-02T19:00:00Z"
        )

        app.reconcile_staff_notice_non_shift_requirements(
            "2024-11-03T20:00:00Z"
        )

        occurrence = self.occurrence_rows(fixture)[0]
        self.assertEqual(occurrence["occurrence_date"], "2024-11-03")
        self.assertEqual(
            occurrence["visible_from_at_utc"],
            "2024-11-03T07:00:00Z"
        )
        self.assertEqual(
            occurrence["due_at_utc"],
            "2024-11-04T07:59:59Z"
        )

    def test_shift_sign_on_binds_pending_occurrence_and_assigns_delivery(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Once",
            shift_applicability="Specific Shift",
            audience_rules=(("Applicable Shift Staff", None, None),),
            specific_shift_client_id=1,
            specific_shift_date="2026-08-03",
            specific_shift_type="Day"
        )
        pending_id = self.seed_pending_shift_occurrence(
            fixture,
            date="2026-08-03",
            shift_type="Day"
        )
        before = self.database_snapshot()
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            shift_cursor = conn.execute("""
                INSERT INTO shifts
                (client_id, shift_date, shift_type, status)
                VALUES (1, '2026-08-03', 'Day', 'Open')
            """)
            shift_id = shift_cursor.lastrowid
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, 2, '08:00', 1)
            """, (shift_id,))
            result = app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                2,
                "2026-08-03T15:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        occurrence = self.occurrence_rows(fixture)[0]
        deliveries = self.delivery_rows(fixture)
        histories = self.delivery_history_rows(fixture)
        self.assertEqual(occurrence["occurrence_id"], pending_id)
        self.assertEqual(occurrence["shift_id"], shift_id)
        self.assertEqual(occurrence["occurrence_status"], "Active")
        self.assertEqual(
            occurrence["visible_from_at_utc"],
            "2026-08-03T15:00:00Z"
        )
        self.assertNotEqual(self.database_snapshot(), before)
        self.assertEqual(result["occurrences_created"], 0)
        self.assertEqual(result["deliveries_assigned"], 1)
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["user_id"], 2)
        self.assertEqual(deliveries[0]["requirement_status"], "Required")
        self.assertEqual(len(histories), 1)
        self.assertEqual(histories[0]["event_type"], "Assigned")
        self.assertEqual(
            [row["activity_type"] for row in self.activity_rows()],
            [
                "staff_notice_occurrence_bound_to_shift",
                "staff_notice_delivery_assigned"
            ]
        )

    def test_shift_reconciliation_is_idempotent(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            shift_applicability="Every Shift",
            audience_rules=(("Applicable Shift Staff", None, None),)
        )
        shift_id = self.seed_shift()
        self.seed_shift_staff(shift_id, 2)

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            first = app.reconcile_staff_notice_shift_sign_on(
                conn, shift_id, 2, "2026-08-03T15:00:00Z"
            )
            repeated = app.reconcile_staff_notice_shift_sign_on(
                conn, shift_id, 2, "2026-08-03T15:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(first["occurrences_created"], 1)
        self.assertEqual(first["deliveries_assigned"], 1)
        self.assertEqual(repeated, app._staff_notice_reconciliation_result())
        self.assertEqual(len(self.occurrence_rows(fixture)), 1)
        self.assertEqual(len(self.delivery_rows(fixture)), 1)
        self.assertEqual(len(self.delivery_history_rows(fixture)), 1)
        self.assertEqual(len(self.activity_rows()), 2)

    def test_second_shift_worker_gets_only_their_missing_delivery(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            shift_applicability="Every Shift",
            audience_rules=(("Applicable Shift Staff", None, None),)
        )
        shift_id = self.seed_shift()
        self.seed_shift_staff(shift_id, 2)
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn, shift_id, 2, "2026-08-03T15:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        original_delivery = self.delivery_rows(fixture)[0]

        self.seed_shift_staff(shift_id, 3)
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = app.reconcile_staff_notice_shift_sign_on(
                conn, shift_id, 3, "2026-08-03T16:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        deliveries = self.delivery_rows(fixture)
        self.assertEqual(result["occurrences_created"], 0)
        self.assertEqual(result["deliveries_assigned"], 1)
        self.assertEqual(len(deliveries), 2)
        self.assertEqual(deliveries[0], original_delivery)
        self.assertEqual([row["user_id"] for row in deliveries], [2, 3])
        self.assertEqual(len(self.delivery_history_rows(fixture)), 2)
        self.assertEqual(
            [row["activity_type"] for row in self.activity_rows()],
            [
                "staff_notice_occurrence_created",
                "staff_notice_delivery_assigned",
                "staff_notice_delivery_assigned"
            ]
        )

    def test_pending_shift_binding_leaves_unrelated_pending_unchanged(self):
        applicable = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Once",
            shift_applicability="Specific Shift",
            audience_rules=(("Applicable Shift Staff", None, None),),
            specific_shift_client_id=1,
            specific_shift_date="2026-08-03",
            specific_shift_type="Day"
        )
        unrelated = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Once",
            shift_applicability="Specific Shift",
            audience_rules=(("Applicable Shift Staff", None, None),),
            specific_shift_client_id=1,
            specific_shift_date="2026-08-04",
            specific_shift_type="Overnight"
        )
        self.seed_pending_shift_occurrence(
            applicable, date="2026-08-03", shift_type="Day"
        )
        unrelated_id = self.seed_pending_shift_occurrence(
            unrelated, date="2026-08-04", shift_type="Overnight"
        )
        shift_id = self.seed_shift()
        self.seed_shift_staff(shift_id, 2)

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn, shift_id, 2, "2026-08-03T15:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        unrelated_occurrence = self.occurrence_rows(unrelated)[0]
        self.assertEqual(unrelated_occurrence["occurrence_id"], unrelated_id)
        self.assertEqual(
            unrelated_occurrence["occurrence_status"],
            "Pending Shift"
        )
        self.assertIsNone(unrelated_occurrence["shift_id"])
        self.assertEqual(self.delivery_rows(unrelated), [])

    def test_applicable_shift_staff_requires_explicit_manager_assignment(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            shift_applicability="Every Shift",
            audience_rules=(("Applicable Shift Staff", None, None),)
        )
        shift_id = self.seed_shift()
        self.seed_shift_staff(shift_id, 2)
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn, shift_id, 2, "2026-08-03T15:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([row["user_id"] for row in self.delivery_rows(fixture)], [2])

        self.seed_shift_staff(shift_id, 5)
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn, shift_id, 5, "2026-08-03T16:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(
            [row["user_id"] for row in self.delivery_rows(fixture)],
            [2, 5]
        )

    def test_shift_reconciliation_failure_rolls_back_caller_transaction(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            shift_applicability="Every Shift",
            audience_rules=(("Applicable Shift Staff", None, None),)
        )
        before = self.database_snapshot()
        conn = self.open_database()

        with self.assertRaisesRegex(RuntimeError, "controlled failure"):
            try:
                conn.execute("BEGIN IMMEDIATE")
                shift_id = conn.execute("""
                    INSERT INTO shifts
                    (client_id, shift_date, shift_type, status)
                    VALUES (1, '2026-08-03', 'Day', 'Open')
                """).lastrowid
                conn.execute("""
                    INSERT INTO shift_staff
                    (shift_id, user_id, actual_start_time, active)
                    VALUES (?, 2, '08:00', 1)
                """, (shift_id,))
                with mock.patch.object(
                    app,
                    "_assign_staff_notice_delivery",
                    side_effect=RuntimeError("controlled failure")
                ):
                    app.reconcile_staff_notice_shift_sign_on(
                        conn, shift_id, 2, "2026-08-03T15:00:00Z"
                    )
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.occurrence_rows(fixture), [])

    def test_manual_sign_on_failure_rolls_back_new_worker_only(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            shift_applicability="Every Shift",
            audience_rules=(("Applicable Shift Staff", None, None),)
        )
        shift_id = self.seed_shift()
        self.seed_shift_staff(shift_id, 2)
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn, shift_id, 2, "2026-08-03T15:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        client = app.app.test_client()

        with client.session_transaction() as session_data:
            session_data["user_id"] = 3
            session_data["role"] = "Behaviour Consultant"
        with mock.patch.object(
            app,
            "_assign_staff_notice_delivery",
            side_effect=RuntimeError("controlled failure")
        ):
            with mock.patch.object(
                app,
                "get_application_now_utc",
                return_value=datetime(
                    2026, 8, 3, 16, 0, tzinfo=timezone.utc
                )
            ):
                response = client.post("/shift/sign-on", data={
                    "shift_date": "2026-08-03",
                    "shift_type": "Day",
                    "actual_start_time": "09:00"
                })

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please try again", response.data)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(len(self.occurrence_rows(fixture)), 1)
        self.assertEqual(
            [row["user_id"] for row in self.delivery_rows(fixture)],
            [2]
        )

    def test_manual_sign_on_creates_shift_and_binds_pending_atomically(self):
        fixture = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Once",
            shift_applicability="Specific Shift",
            audience_rules=(("Applicable Shift Staff", None, None),),
            specific_shift_client_id=1,
            specific_shift_date="2026-08-03",
            specific_shift_type="Day"
        )
        pending_id = self.seed_pending_shift_occurrence(
            fixture,
            date="2026-08-03",
            shift_type="Day"
        )
        tracking = ReconciliationTrackingConnection(self.open_database())
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"

        with mock.patch.object(app, "get_db", return_value=tracking):
            with mock.patch.object(
                app,
                "get_application_now_utc",
                return_value=datetime(
                    2026, 8, 3, 15, 0, tzinfo=timezone.utc
                )
            ):
                response = client.post("/shift/sign-on", data={
                    "shift_date": "2026-08-03",
                    "shift_type": "Day",
                    "actual_start_time": "08:00"
                })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(tracking.commit_calls, 1)
        self.assertEqual(tracking.rollback_calls, 0)
        self.assertEqual(tracking.close_calls, 1)
        occurrence = self.occurrence_rows(fixture)[0]
        deliveries = self.delivery_rows(fixture)
        self.assertEqual(occurrence["occurrence_id"], pending_id)
        self.assertIsNotNone(occurrence["shift_id"])
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["user_id"], 2)
        self.assertEqual(len(self.delivery_history_rows(fixture)), 1)

    def test_manual_sign_on_failure_rolls_back_new_shift_and_returns_error(self):
        before = self.database_snapshot()
        tracking = ReconciliationTrackingConnection(self.open_database())
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"

        with mock.patch.object(app, "get_db", return_value=tracking):
            with mock.patch.object(
                app,
                "reconcile_staff_notice_shift_sign_on",
                side_effect=RuntimeError("controlled failure")
            ):
                response = client.post("/shift/sign-on", data={
                    "shift_date": "2026-08-03",
                    "shift_type": "Day",
                    "actual_start_time": "08:00"
                })

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please try again", response.data)
        self.assertEqual(tracking.commit_calls, 0)
        self.assertEqual(tracking.rollback_calls, 1)
        self.assertEqual(tracking.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)

    def test_manual_sign_on_without_notice_commits_one_owned_connection(self):
        tracking = ReconciliationTrackingConnection(self.open_database())
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"

        with mock.patch.object(app, "get_db", return_value=tracking):
            response = client.post("/shift/sign-on", data={
                "shift_date": "2026-08-03",
                "shift_type": "Day",
                "actual_start_time": "08:00"
            })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(tracking.commit_calls, 1)
        self.assertEqual(tracking.rollback_calls, 0)
        self.assertEqual(tracking.close_calls, 1)
        conn = self.open_database()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],
                1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM shift_staff").fetchone()[0],
                1
            )
        finally:
            conn.close()

    def test_auto_sign_on_without_notice_commits_one_owned_connection(self):
        tracking = ReconciliationTrackingConnection(self.open_database())

        with mock.patch.object(app, "get_db", return_value=tracking):
            shift_id, checklist_completed = app.auto_sign_on_user(2)

        self.assertIsInstance(shift_id, int)
        self.assertEqual(checklist_completed, 0)
        self.assertEqual(tracking.commit_calls, 1)
        self.assertEqual(tracking.rollback_calls, 0)
        self.assertEqual(tracking.close_calls, 1)
        conn = self.open_database()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],
                1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM shift_staff").fetchone()[0],
                1
            )
        finally:
            conn.close()

    def test_auto_sign_on_failure_rolls_back_and_dashboard_is_retryable(self):
        before = self.database_snapshot()
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
            side_effect=RuntimeError("controlled failure")
        ):
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Please try again", response.data)
        self.assertEqual(self.database_snapshot(), before)

    def test_manual_sign_on_login_and_validation_failures_are_unchanged(self):
        before = self.database_snapshot()
        client = app.app.test_client()

        response = client.post("/shift/sign-on", data={
            "shift_date": "2026-08-03",
            "shift_type": "Day",
            "actual_start_time": "08:00"
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"
        response = client.post("/shift/sign-on", data={
            "shift_date": "",
            "shift_type": "Day",
            "actual_start_time": "08:00"
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"are required", response.data)
        self.assertEqual(self.database_snapshot(), before)

    def test_worker_removal_before_view_transitions_and_audits_in_order(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        delivery = self.delivery_rows(fixture)[0]
        baseline_activity_count = len(self.activity_rows())
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            result = app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Worker removed from coverage.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        updated = self.delivery_rows(fixture)[0]
        history = self.delivery_history_rows(fixture)
        activities = self.activity_rows()[baseline_activity_count:]
        self.assertEqual(result, {
            "assignments_removed": 1,
            "deliveries_no_longer_required": 1,
            "delivery_access_revoked": 1
        })
        self.assertEqual(updated["delivery_id"], delivery["delivery_id"])
        self.assertEqual(updated["requirement_status"], "No Longer Required")
        self.assertEqual(updated["recipient_access"], 0)
        self.assertIsNone(updated["first_viewed_at_utc"])
        self.assertEqual(
            [row["event_type"] for row in history],
            ["Assigned", "No Longer Required", "Access Revoked"]
        )
        self.assertEqual(
            [row["activity_type"] for row in activities],
            [
                "staff_notice_delivery_no_longer_required",
                "staff_notice_delivery_access_revoked",
                "shift_staff_removed"
            ]
        )
        for row in history[1:]:
            self.assertEqual(row["reason_code"], "Shift Assignment Removed")
            self.assertEqual(
                row["reason_text"],
                "Worker removed from coverage."
            )
            self.assertEqual(row["changed_by_user_id"], 1)
            self.assertEqual(
                row["changed_at_utc"],
                "2026-08-03T17:00:00Z"
            )
        self.assertEqual(
            history[1]["previous_requirement_status"],
            "Required"
        )
        self.assertEqual(
            history[1]["new_requirement_status"],
            "No Longer Required"
        )
        self.assertIsNone(history[1]["previous_recipient_access"])
        self.assertIsNone(history[1]["new_recipient_access"])
        self.assertIsNone(history[2]["previous_requirement_status"])
        self.assertIsNone(history[2]["new_requirement_status"])
        self.assertEqual(history[2]["previous_recipient_access"], 1)
        self.assertEqual(history[2]["new_recipient_access"], 0)
        for activity in activities:
            self.assertEqual(activity["shift_id"], shift_id)
            self.assertIn("Worker removed from coverage.", activity["details"])
            self.assertEqual(activity["user_id"], 1)

    def test_worker_removal_after_view_preserves_view_history(self):
        fixture, _, shift_staff_id = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:30:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (delivery["delivery_id"],))
            conn.commit()
        finally:
            conn.close()

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Coverage changed.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        updated = self.delivery_rows(fixture)[0]
        self.assertEqual(updated["requirement_status"], "No Longer Required")
        self.assertEqual(updated["recipient_access"], 0)
        self.assertEqual(
            updated["first_viewed_at_utc"],
            "2026-08-03T15:30:00Z"
        )
        self.assertEqual(updated["viewed_by_user_id"], 2)

    def assert_worker_removal_preserves_acknowledgement(
        self,
        acknowledged_at,
        expected_status
    ):
        fixture, _, shift_staff_id = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET due_at_utc = '2026-08-03T17:00:00Z'
                WHERE occurrence_id = ?
            """, (delivery["occurrence_id"],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:30:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (delivery["delivery_id"],))
            conn.commit()
        finally:
            conn.close()
        acknowledgement_id = self.seed_staff_notice_acknowledgement(
            delivery["delivery_id"],
            2,
            acknowledged_at
        )

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Assignment ended early.",
                "2026-08-03T19:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        updated = self.delivery_rows(fixture)[0]
        histories = self.delivery_history_rows(fixture)
        conn = self.open_database()
        try:
            acknowledgement = conn.execute("""
                SELECT *
                FROM acknowledgements
                WHERE acknowledgement_id = ?
            """, (acknowledgement_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(result["deliveries_no_longer_required"], 0)
        self.assertEqual(result["delivery_access_revoked"], 1)
        self.assertEqual(updated["requirement_status"], "Required")
        self.assertEqual(updated["recipient_access"], 0)
        self.assertEqual(
            updated["first_viewed_at_utc"],
            "2026-08-03T15:30:00Z"
        )
        self.assertEqual(acknowledgement["acknowledged_at"], acknowledged_at)
        self.assertEqual(acknowledgement["active"], 1)
        self.assertEqual(
            [row["event_type"] for row in histories],
            ["Assigned", "Access Revoked"]
        )
        self.assertEqual(
            app.get_recipient_staff_notice_status(
                active_acknowledgement_at_utc=acknowledged_at,
                due_at_utc="2026-08-03T17:00:00Z",
                requirement_status=updated["requirement_status"],
                first_viewed_at_utc=updated["first_viewed_at_utc"]
            ),
            expected_status
        )

    def test_worker_removal_preserves_on_time_acknowledgement(self):
        self.assert_worker_removal_preserves_acknowledgement(
            "2026-08-03T16:00:00Z",
            "Acknowledged"
        )

    def test_worker_removal_preserves_late_acknowledgement(self):
        self.assert_worker_removal_preserves_acknowledgement(
            "2026-08-03T18:00:00Z",
            "Acknowledged Late"
        )

    def test_future_worker_removal_handles_existing_or_missing_delivery(self):
        existing, _, existing_assignment = (
            self.create_shift_notice_delivery(shift_date="2026-08-06")
        )
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_result = app.remove_shift_staff_assignment(
                conn,
                existing_assignment,
                1,
                "Future coverage changed.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(existing_result["deliveries_no_longer_required"], 1)
        self.assertEqual(
            self.delivery_rows(existing)[0]["requirement_status"],
            "No Longer Required"
        )

        pending = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Once",
            shift_applicability="Specific Shift",
            audience_rules=(("Applicable Shift Staff", None, None),),
            specific_shift_client_id=1,
            specific_shift_date="2026-08-07",
            specific_shift_type="Day"
        )
        pending_id = self.seed_pending_shift_occurrence(
            pending,
            date="2026-08-07",
            shift_type="Day"
        )
        shift_id = self.seed_shift(date="2026-08-07")
        assignment_id = self.seed_shift_staff(shift_id, 3)
        baseline_activity_count = len(self.activity_rows())
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            missing_result = app.remove_shift_staff_assignment(
                conn,
                assignment_id,
                1,
                "Future worker removed.",
                "2026-08-03T18:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(missing_result, {
            "assignments_removed": 1,
            "deliveries_no_longer_required": 0,
            "delivery_access_revoked": 0
        })
        occurrence = self.occurrence_rows(pending)[0]
        self.assertEqual(occurrence["occurrence_id"], pending_id)
        self.assertEqual(occurrence["occurrence_status"], "Pending Shift")
        self.assertIsNone(occurrence["shift_id"])
        self.assertEqual(self.delivery_rows(pending), [])
        self.assertEqual(
            [row["activity_type"] for row in self.activity_rows()[
                baseline_activity_count:
            ]],
            ["shift_staff_removed"]
        )

    def test_worker_removal_supports_specific_and_every_shift_deliveries(self):
        every, shift_id, shift_staff_id = self.create_shift_notice_delivery()
        specific = self.create_published_notice(
            occurrence_basis="Shift",
            recurrence_pattern="Once",
            shift_applicability="Specific Shift",
            audience_rules=(("Applicable Shift Staff", None, None),),
            specific_shift_client_id=1,
            specific_shift_date="2026-08-03",
            specific_shift_type="Day"
        )
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                2,
                "2026-08-03T15:30:00Z"
            )
            result = app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Removed from both requirements.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(result["deliveries_no_longer_required"], 2)
        self.assertEqual(result["delivery_access_revoked"], 2)
        for fixture in (every, specific):
            delivery = self.delivery_rows(fixture)[0]
            self.assertEqual(
                delivery["requirement_status"],
                "No Longer Required"
            )
            self.assertEqual(delivery["recipient_access"], 0)

    def test_worker_removal_is_idempotent(self):
        fixture, _, shift_staff_id = self.create_shift_notice_delivery()
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            first = app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Assignment removed.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        snapshot = self.database_snapshot()

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            repeated = app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Assignment removed.",
                "2026-08-03T18:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(first["assignments_removed"], 1)
        self.assertEqual(repeated, {
            "assignments_removed": 0,
            "deliveries_no_longer_required": 0,
            "delivery_access_revoked": 0
        })
        self.assertEqual(self.database_snapshot(), snapshot)
        self.assertEqual(len(self.delivery_history_rows(fixture)), 3)

    def test_worker_removal_rejects_empty_reason_without_changes(self):
        _, _, shift_staff_id = self.create_shift_notice_delivery()
        before = self.database_snapshot()

        for reason in ("", " \t\r\n"):
            with self.subTest(reason=repr(reason)):
                conn = self.open_database()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    with self.assertRaisesRegex(
                        ValueError,
                        "reason is required"
                    ):
                        app.remove_shift_staff_assignment(
                            conn,
                            shift_staff_id,
                            1,
                            reason,
                            "2026-08-03T17:00:00Z"
                        )
                    conn.rollback()
                finally:
                    conn.close()
                self.assertEqual(self.database_snapshot(), before)

    def test_worker_removal_failure_during_nlr_rolls_back_everything(self):
        _, _, shift_staff_id = self.create_shift_notice_delivery()
        before = self.database_snapshot()
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            with mock.patch.object(
                app,
                "_mark_staff_notice_delivery_no_longer_required",
                side_effect=RuntimeError("controlled NLR failure")
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "controlled NLR failure"
                ):
                    app.remove_shift_staff_assignment(
                        conn,
                        shift_staff_id,
                        1,
                        "Removal failed.",
                        "2026-08-03T17:00:00Z"
                    )
            conn.rollback()
        finally:
            conn.close()

        self.assertEqual(self.database_snapshot(), before)

    def test_worker_removal_failure_during_access_rolls_back_everything(self):
        _, _, shift_staff_id = self.create_shift_notice_delivery()
        before = self.database_snapshot()
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            with mock.patch.object(
                app,
                "_revoke_staff_notice_delivery_access",
                side_effect=RuntimeError("controlled access failure")
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "controlled access failure"
                ):
                    app.remove_shift_staff_assignment(
                        conn,
                        shift_staff_id,
                        1,
                        "Removal failed.",
                        "2026-08-03T17:00:00Z"
                    )
            conn.rollback()
        finally:
            conn.close()

        self.assertEqual(self.database_snapshot(), before)

    def test_worker_removal_audit_failure_rolls_back_everything(self):
        _, _, shift_staff_id = self.create_shift_notice_delivery()
        before = self.database_snapshot()
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_removal_activity_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type =
                    'staff_notice_delivery_no_longer_required'
                BEGIN
                    SELECT RAISE(ABORT, 'controlled removal audit failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled removal audit failure"
            ):
                app.remove_shift_staff_assignment(
                    conn,
                    shift_staff_id,
                    1,
                    "Removal failed.",
                    "2026-08-03T17:00:00Z"
                )
            conn.rollback()
        finally:
            conn.close()

        self.assertEqual(self.database_snapshot(), before)

    def test_worker_removal_history_failure_rolls_back_everything(self):
        _, _, shift_staff_id = self.create_shift_notice_delivery()
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_removal_history_failure
                BEFORE INSERT ON staff_notice_delivery_history
                WHEN NEW.event_type = 'No Longer Required'
                BEGIN
                    SELECT RAISE(ABORT, 'controlled removal history failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled removal history failure"
            ):
                app.remove_shift_staff_assignment(
                    conn,
                    shift_staff_id,
                    1,
                    "Removal failed.",
                    "2026-08-03T17:00:00Z"
                )
            conn.rollback()
        finally:
            conn.close()

        self.assertEqual(self.database_snapshot(), before)

    def test_shift_reassignment_reinstates_existing_delivery_once(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        original = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:30:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (original["delivery_id"],))
            app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Coverage changed.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        replacement_assignment = self.seed_shift_staff(shift_id, 2)
        baseline_activity_count = len(self.activity_rows())
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                2,
                "2026-08-03T18:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        restored = self.delivery_rows(fixture)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["delivery_id"], original["delivery_id"])
        self.assertEqual(restored[0]["requirement_status"], "Required")
        self.assertEqual(restored[0]["recipient_access"], 1)
        self.assertEqual(
            restored[0]["first_viewed_at_utc"],
            "2026-08-03T15:30:00Z"
        )
        self.assertEqual(restored[0]["viewed_by_user_id"], 2)
        history = self.delivery_history_rows(fixture)
        self.assertEqual(
            [row["event_type"] for row in history],
            [
                "Assigned",
                "No Longer Required",
                "Access Revoked",
                "Reinstated",
                "Access Restored"
            ]
        )
        for row in history[-2:]:
            self.assertEqual(
                row["reason_code"],
                "Shift Assignment Restored"
            )
            self.assertEqual(
                row["reason_text"],
                "Worker assigned to shift."
            )
            self.assertEqual(row["changed_by_user_id"], 2)
            self.assertEqual(
                row["changed_at_utc"],
                "2026-08-03T18:00:00Z"
            )
        self.assertEqual(
            history[-2]["previous_requirement_status"],
            "No Longer Required"
        )
        self.assertEqual(history[-2]["new_requirement_status"], "Required")
        self.assertEqual(history[-1]["previous_recipient_access"], 0)
        self.assertEqual(history[-1]["new_recipient_access"], 1)
        activities = self.activity_rows()[baseline_activity_count:]
        self.assertEqual(
            [row["activity_type"] for row in activities],
            [
                "staff_notice_delivery_reinstated",
                "staff_notice_delivery_access_restored"
            ]
        )
        for activity in activities:
            self.assertEqual(activity["user_id"], 2)
            self.assertEqual(activity["shift_id"], shift_id)
            self.assertEqual(
                activity["related_id"],
                original["delivery_id"]
            )
            self.assertIn(
                "Reason: Worker assigned to shift.",
                activity["details"]
            )
        snapshot = self.database_snapshot()

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                2,
                "2026-08-03T19:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.database_snapshot(), snapshot)
        self.assertNotEqual(replacement_assignment, shift_staff_id)

    def test_shift_sign_on_route_reinstates_existing_delivery_atomically(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Temporary shift reassignment.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(
                2026,
                8,
                3,
                18,
                0,
                tzinfo=timezone.utc
            )
        ):
            response = client.post("/shift/sign-on", data={
                "shift_date": "2026-08-03",
                "shift_type": "Day",
                "actual_start_time": "08:00"
            })

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/shift/{shift_id}", response.headers["Location"])
        deliveries = self.delivery_rows(fixture)
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["delivery_id"], delivery["delivery_id"])
        self.assertEqual(deliveries[0]["requirement_status"], "Required")
        self.assertEqual(deliveries[0]["recipient_access"], 1)

    def test_reassignment_to_different_worker_preserves_original_delivery(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        original = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Replacement coverage required.",
                "2026-08-03T17:00:00Z"
            )
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, 3, '08:00', 1)
            """, (shift_id,))
            app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                3,
                "2026-08-03T17:05:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        deliveries = self.delivery_rows(fixture)
        self.assertEqual(len(deliveries), 2)
        original_after = next(
            row for row in deliveries if row["user_id"] == 2
        )
        replacement = next(
            row for row in deliveries if row["user_id"] == 3
        )
        self.assertEqual(
            original_after["delivery_id"],
            original["delivery_id"]
        )
        self.assertEqual(
            original_after["requirement_status"],
            "No Longer Required"
        )
        self.assertEqual(original_after["recipient_access"], 0)
        self.assertEqual(replacement["requirement_status"], "Required")
        self.assertEqual(replacement["recipient_access"], 1)
        self.assertIsNone(replacement["first_viewed_at_utc"])
        self.assertIsNone(replacement["viewed_by_user_id"])

    def test_reassignment_after_acknowledgement_restores_only_access(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:30:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (delivery["delivery_id"],))
            conn.commit()
        finally:
            conn.close()
        acknowledgement_id = self.seed_staff_notice_acknowledgement(
            delivery["delivery_id"],
            2,
            "2026-08-03T16:00:00Z"
        )
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Temporary coverage change.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        self.seed_shift_staff(shift_id, 2)
        baseline_activity_count = len(self.activity_rows())

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                2,
                "2026-08-03T18:00:00Z"
            )
            conn.commit()
            acknowledgement = conn.execute("""
                SELECT *
                FROM acknowledgements
                WHERE acknowledgement_id = ?
            """, (acknowledgement_id,)).fetchone()
        finally:
            conn.close()

        restored = self.delivery_rows(fixture)[0]
        self.assertEqual(restored["requirement_status"], "Required")
        self.assertEqual(restored["recipient_access"], 1)
        self.assertEqual(
            restored["first_viewed_at_utc"],
            "2026-08-03T15:30:00Z"
        )
        self.assertEqual(
            acknowledgement["acknowledged_at"],
            "2026-08-03T16:00:00Z"
        )
        self.assertEqual(acknowledgement["active"], 1)
        self.assertEqual(
            [row["event_type"] for row in self.delivery_history_rows(
                fixture
            )],
            ["Assigned", "Access Revoked", "Access Restored"]
        )
        self.assertEqual(
            [row["activity_type"] for row in self.activity_rows()[
                baseline_activity_count:
            ]],
            ["staff_notice_delivery_access_restored"]
        )

    def test_reconciliation_does_not_restore_cancelled_occurrence(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Temporary coverage change.",
                "2026-08-03T17:00:00Z"
            )
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET occurrence_status = 'Cancelled'
                WHERE schedule_id = ?
            """, (fixture["schedule_id"],))
            conn.commit()
        finally:
            conn.close()
        self.seed_shift_staff(shift_id, 2)
        before = self.database_snapshot()

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                2,
                "2026-08-03T18:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(self.database_snapshot(), before)

    def test_management_removal_route_authorization_validation_and_get(self):
        fixture, _, _ = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        path = (
            f"/staff-notices/delivery/{delivery['delivery_id']}"
            "/no-longer-required"
        )
        before = self.database_snapshot()

        anonymous = app.app.test_client().post(path, data={
            "confirm_removal": "yes",
            "reason": "Not authorized."
        })
        worker = app.app.test_client()
        with worker.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"
        worker_response = worker.post(path, data={
            "confirm_removal": "yes",
            "reason": "Not authorized."
        })
        manager = app.app.test_client()
        with manager.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"
        get_response = manager.get(path)
        missing_confirmation = manager.post(path, data={
            "reason": "Missing confirmation."
        })
        missing_reason = manager.post(path, data={
            "confirm_removal": "yes"
        })

        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(worker_response.status_code, 403)
        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertEqual(missing_reason.status_code, 400)
        self.assertEqual(self.database_snapshot(), before)

    def test_management_removal_route_is_atomic_and_idempotent(self):
        fixture, _, shift_staff_id = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        path = (
            f"/staff-notices/delivery/{delivery['delivery_id']}"
            "/no-longer-required"
        )
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"
        now_utc = datetime(2026, 8, 3, 17, 0, tzinfo=timezone.utc)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=now_utc
        ):
            response = client.post(path, data={
                "confirm_removal": "yes",
                "reason": "Manager changed shift coverage."
            })

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            f"/staff-notices/{fixture['notice_id']}/tracking",
            response.headers["Location"]
        )
        updated = self.delivery_rows(fixture)[0]
        self.assertEqual(updated["requirement_status"], "No Longer Required")
        self.assertEqual(updated["recipient_access"], 0)
        conn = self.open_database()
        try:
            assignment = conn.execute("""
                SELECT active
                FROM shift_staff
                WHERE shift_staff_id = ?
            """, (shift_staff_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(assignment["active"], 0)
        snapshot = self.database_snapshot()

        repeated = client.post(path, data={
            "confirm_removal": "yes",
            "reason": "Repeated stale request."
        })
        self.assertEqual(repeated.status_code, 409)
        self.assertEqual(self.database_snapshot(), snapshot)

    def test_management_removal_route_failure_rolls_back(self):
        fixture, _, _ = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        before = self.database_snapshot()
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"

        with mock.patch.object(
            app,
            "_revoke_staff_notice_delivery_access",
            side_effect=RuntimeError("controlled route failure")
        ):
            response = client.post(
                (
                    f"/staff-notices/delivery/{delivery['delivery_id']}"
                    "/no-longer-required"
                ),
                data={
                    "confirm_removal": "yes",
                    "reason": "Rollback test."
                }
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.database_snapshot(), before)

    def test_management_reinstatement_requires_active_assignment_and_is_atomic(
        self
    ):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Temporary removal.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        path = (
            f"/staff-notices/delivery/{delivery['delivery_id']}/reinstate"
        )
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"
        before = self.database_snapshot()

        no_assignment = client.post(path, data={
            "confirm_reinstatement": "yes",
            "reason": "Worker returned."
        })
        self.assertEqual(no_assignment.status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

        self.seed_shift_staff(shift_id, 2)
        baseline_activity_count = len(self.activity_rows())
        response = client.post(path, data={
            "confirm_reinstatement": "yes",
            "reason": "Worker returned to coverage."
        })
        self.assertEqual(response.status_code, 302)
        restored = self.delivery_rows(fixture)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["delivery_id"], delivery["delivery_id"])
        self.assertEqual(restored[0]["requirement_status"], "Required")
        self.assertEqual(restored[0]["recipient_access"], 1)
        self.assertEqual(
            [row["activity_type"] for row in self.activity_rows()[
                baseline_activity_count:
            ]],
            [
                "staff_notice_delivery_reinstated",
                "staff_notice_delivery_access_restored"
            ]
        )
        snapshot = self.database_snapshot()
        repeated = client.post(path, data={
            "confirm_reinstatement": "yes",
            "reason": "Repeated reinstatement."
        })
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(self.database_snapshot(), snapshot)

    def test_management_reinstatement_rechecks_eligibility_and_rolls_back(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                shift_staff_id,
                1,
                "Temporary removal.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        self.seed_shift_staff(shift_id, 2)
        self.set_user(2, active=0)
        path = (
            f"/staff-notices/delivery/{delivery['delivery_id']}/reinstate"
        )
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"
        before = self.database_snapshot()

        ineligible = client.post(path, data={
            "confirm_reinstatement": "yes",
            "reason": "Ineligible worker."
        })
        self.assertEqual(ineligible.status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

        self.set_user(2, active=1)
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_reinstatement_activity_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type =
                    'staff_notice_delivery_reinstated'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled reinstatement activity failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        failed = client.post(path, data={
            "confirm_reinstatement": "yes",
            "reason": "Rollback reinstatement."
        })
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(self.database_snapshot(), before)

    def test_completion_and_manager_sign_off_do_not_transition_delivery(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        original_delivery = self.delivery_rows(fixture)[0]
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"
        with mock.patch.object(app, "save_shift_task_entries"):
            response = client.post(f"/shift/{shift_id}/end-shift", data={})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.delivery_rows(fixture)[0], original_delivery)
        self.assertEqual(
            [row["event_type"] for row in self.delivery_history_rows(fixture)],
            ["Assigned"]
        )

        second_fixture, _, second_assignment = (
            self.create_shift_notice_delivery(
                shift_date="2026-08-04",
                user_id=3
            )
        )
        second_original = self.delivery_rows(second_fixture)[0]
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(
                2026,
                8,
                5,
                0,
                0,
                tzinfo=timezone.utc
            )
        ):
            response = client.post(
                f"/shift-staff/{second_assignment}/manager-sign-off",
                data={
                    "actual_end_date": "2026-08-04",
                    "actual_end_time": "14:00",
                    "reason": "Forgotten sign-off correction."
                }
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.delivery_rows(second_fixture)[0],
            second_original
        )
        self.assertEqual(
            [row["event_type"] for row in self.delivery_history_rows(
                second_fixture
            )],
            ["Assigned"]
        )
        self.assertNotEqual(shift_staff_id, second_assignment)

    def recipient_request(
        self,
        path,
        *,
        method="GET",
        data=None,
        user_id=2,
        role="Support Worker",
        now_utc=None,
        patch_food_fluid=False
    ):
        if now_utc is None:
            now_utc = datetime(
                2026,
                8,
                3,
                16,
                0,
                tzinfo=timezone.utc
            )
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = role
            session_data["full_name"] = f"User {user_id}"

        patches = [
            mock.patch.object(
                app,
                "get_application_now_utc",
                return_value=now_utc
            )
        ]
        if patch_food_fluid:
            patches.append(mock.patch.object(
                app,
                "get_active_food_fluid_shift_context",
                side_effect=PermissionError
            ))

        with patches[0]:
            if len(patches) == 2:
                with patches[1]:
                    response = client.open(
                        path,
                        method=method,
                        data=data or {}
                    )
            else:
                response = client.open(
                    path,
                    method=method,
                    data=data or {}
                )
        return client, response

    def create_recipient_notice(self, *, title=None, priority=None):
        fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None",
            audience_rules=(("All Support Workers", None, None),)
        )
        self.seed_one_time_occurrence(fixture)
        if title is not None or priority is not None:
            conn = self.open_database()
            try:
                updates = []
                parameters = []
                if title is not None:
                    updates.append("title = ?")
                    parameters.append(title)
                if priority is not None:
                    updates.append("priority = ?")
                    parameters.append(priority)
                conn.execute(
                    "UPDATE staff_notices SET "
                    + ", ".join(updates)
                    + " WHERE notice_id = ?",
                    (*parameters, fixture["notice_id"])
                )
                conn.commit()
            finally:
                conn.close()
        return fixture

    def test_recipient_reconciliation_precedes_list_and_is_idempotent(self):
        fixture = self.create_recipient_notice(title="Worker Notice")

        _, first = self.recipient_request("/staff-notices")
        first_delivery = self.delivery_rows(fixture)
        first_history = self.delivery_history_rows(fixture)
        first_activity = self.activity_rows()
        _, repeated = self.recipient_request("/staff-notices")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(len(first_delivery), 1)
        self.assertEqual(
            self.delivery_rows(fixture),
            first_delivery
        )
        self.assertEqual(
            self.delivery_history_rows(fixture),
            first_history
        )
        self.assertEqual(self.activity_rows(), first_activity)
        self.assertIn(b"Worker Notice", first.data)
        self.assertIsNone(first_delivery[0]["first_viewed_at_utc"])

        inactive_fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None",
            audience_rules=(
                ("Selected Individual", None, 4),
            )
        )
        _, response = self.recipient_request("/staff-notices")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.delivery_rows(inactive_fixture), [])

    def test_recipient_routes_require_login_active_identity_and_ownership(self):
        fixture = self.create_recipient_notice()
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        path = f"/staff-notices/delivery/{delivery['delivery_id']}"

        anonymous = app.app.test_client().get("/staff-notices")
        _, inactive = self.recipient_request(
            "/staff-notices",
            user_id=4
        )
        _, manager = self.recipient_request(
            path,
            user_id=5,
            role="Program Manager"
        )

        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login", anonymous.headers["Location"])
        self.assertEqual(inactive.status_code, 403)
        self.assertEqual(manager.status_code, 404)
        self.assertEqual(
            self.delivery_rows(fixture)[0]["first_viewed_at_utc"],
            None
        )

    def test_recipient_reconciliation_failure_rolls_back_dashboard(self):
        self.create_recipient_notice(title="Rollback Notice")
        shift_id = self.seed_shift()
        self.seed_shift_staff(shift_id, 2)
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_recipient_reconciliation_failure
                BEFORE INSERT ON staff_notice_audience_eligibility_periods
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled recipient reconciliation failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()

        _, response = self.recipient_request(
            f"/shift/{shift_id}",
            patch_food_fluid=True
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Please retry", response.data)
        self.assertEqual(self.database_snapshot(), before)

    def test_recipient_dashboard_limits_orders_and_does_not_view(self):
        fixtures = []
        for index in range(6):
            fixtures.append(self.create_recipient_notice(
                title=f"Notice {index}",
                priority="Urgent" if index == 5 else "Normal"
            ))
        shift_id = self.seed_shift()
        self.seed_shift_staff(shift_id, 2)

        _, response = self.recipient_request(
            f"/shift/{shift_id}",
            patch_food_fluid=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data.count(b"/staff-notices/delivery/"),
            5
        )
        self.assertIn(b"View All Staff Notices", response.data)
        self.assertLess(
            response.data.index(b"Notice 5"),
            response.data.index(b"Notice 0")
        )
        for fixture in fixtures:
            for delivery in self.delivery_rows(fixture):
                self.assertIsNone(delivery["first_viewed_at_utc"])

    def test_recipient_dashboard_empty_state(self):
        shift_id = self.seed_shift()
        self.seed_shift_staff(shift_id, 2)

        _, response = self.recipient_request(
            f"/shift/{shift_id}",
            patch_food_fluid=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"No Staff Notices are currently available",
            response.data
        )

    def test_recipient_list_groups_statuses_without_recording_view(self):
        fixtures = [
            self.create_recipient_notice(title="Not Viewed Notice"),
            self.create_recipient_notice(title="Viewed Notice"),
            self.create_recipient_notice(title="Acknowledged Notice"),
            self.create_recipient_notice(title="NLR Notice"),
            self.create_recipient_notice(title="Cancelled Notice")
        ]
        self.recipient_request("/staff-notices")
        deliveries = [
            self.delivery_rows(fixture)[0]
            for fixture in fixtures
        ]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:00:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (deliveries[1]["delivery_id"],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:00:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (deliveries[2]["delivery_id"],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET requirement_status = 'No Longer Required',
                    recipient_access = 0
                WHERE delivery_id = ?
            """, (deliveries[3]["delivery_id"],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET requirement_status = 'Cancelled',
                    recipient_access = 0
                WHERE delivery_id = ?
            """, (deliveries[4]["delivery_id"],))
            conn.commit()
        finally:
            conn.close()
        self.seed_staff_notice_acknowledgement(
            deliveries[2]["delivery_id"],
            2,
            "2026-08-03T15:30:00Z"
        )
        before = self.database_snapshot()

        _, response = self.recipient_request("/staff-notices")

        self.assertEqual(response.status_code, 200)
        for status in (
            b"Not Viewed",
            "Viewed – Awaiting Acknowledgement".encode(),
            b"Acknowledged",
            b"No Longer Required",
            b"Cancelled"
        ):
            self.assertIn(status, response.data)
        self.assertEqual(self.database_snapshot(), before)

    def test_recipient_detail_records_first_view_once_with_exact_audit(self):
        fixture = self.create_recipient_notice(title="View Me")
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        baseline_activity_count = len(self.activity_rows())
        baseline_history = self.delivery_history_rows(fixture)
        first_view = datetime(
            2026,
            8,
            3,
            16,
            30,
            tzinfo=timezone.utc
        )

        _, response = self.recipient_request(
            f"/staff-notices/delivery/{delivery['delivery_id']}",
            now_utc=first_view
        )

        self.assertEqual(response.status_code, 200)
        viewed_delivery = self.delivery_rows(fixture)[0]
        self.assertEqual(
            viewed_delivery["first_viewed_at_utc"],
            "2026-08-03T16:30:00Z"
        )
        self.assertEqual(viewed_delivery["viewed_by_user_id"], 2)
        activities = self.activity_rows()[baseline_activity_count:]
        self.assertEqual(
            [row["activity_type"] for row in activities],
            ["staff_notice_viewed"]
        )
        self.assertEqual(activities[0]["activity_class"], "STAFF_NOTICE")
        self.assertEqual(activities[0]["user_id"], 2)
        self.assertEqual(activities[0]["client_id"], 1)
        self.assertIsNone(activities[0]["shift_id"])
        self.assertEqual(
            activities[0]["related_table"],
            "staff_notice_deliveries"
        )
        self.assertEqual(
            activities[0]["related_id"],
            delivery["delivery_id"]
        )
        self.assertEqual(activities[0]["success"], 1)
        self.assertEqual(
            activities[0]["details"],
            f"Notice ID: {fixture['notice_id']}; "
            f"Occurrence ID: {delivery['occurrence_id']}; "
            f"Delivery ID: {delivery['delivery_id']}; "
            "Recipient User ID: 2; Viewer User ID: 2; "
            "Viewed at UTC: 2026-08-03T16:30:00Z"
        )
        self.assertEqual(
            self.delivery_history_rows(fixture),
            baseline_history
        )
        snapshot = self.database_snapshot()
        _, repeated = self.recipient_request(
            f"/staff-notices/delivery/{delivery['delivery_id']}",
            now_utc=datetime(
                2026,
                8,
                3,
                17,
                0,
                tzinfo=timezone.utc
            )
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(self.database_snapshot(), snapshot)

    def test_recipient_detail_enforces_ownership_and_escapes_content(self):
        fixture = self.create_recipient_notice(
            title="<script>title()</script>"
        )
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notices
                SET notice_text = '<script>alert(1)</script>'
                WHERE notice_id = ?
            """, (fixture["notice_id"],))
            conn.commit()
        finally:
            conn.close()
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        before = self.database_snapshot()

        _, denied = self.recipient_request(
            f"/staff-notices/delivery/{delivery['delivery_id']}",
            user_id=3,
            role="Behaviour Consultant"
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(self.database_snapshot(), before)

        _, allowed = self.recipient_request(
            f"/staff-notices/delivery/{delivery['delivery_id']}"
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", allowed.data)
        self.assertNotIn(b"<script>alert(1)</script>", allowed.data)

    def test_recipient_view_audit_failure_rolls_back_view(self):
        fixture = self.create_recipient_notice()
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_recipient_view_audit_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type = 'staff_notice_viewed'
                BEGIN
                    SELECT RAISE(ABORT, 'controlled view audit failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()

        _, response = self.recipient_request(
            f"/staff-notices/delivery/{delivery['delivery_id']}"
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.database_snapshot(), before)

    def test_recipient_does_not_repair_inconsistent_acknowledgement_history(self):
        fixture = self.create_recipient_notice()
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        self.seed_staff_notice_acknowledgement(
            delivery["delivery_id"],
            2,
            "2026-08-03T15:00:00Z"
        )
        before = self.database_snapshot()

        _, response = self.recipient_request(
            f"/staff-notices/delivery/{delivery['delivery_id']}"
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn(b"inconsistent acknowledgement history", response.data)
        self.assertEqual(self.database_snapshot(), before)

    def test_recipient_acknowledgement_is_explicit_atomic_and_idempotent(self):
        fixture = self.create_recipient_notice(title="Acknowledge Me")
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        detail_path = (
            f"/staff-notices/delivery/{delivery['delivery_id']}"
        )
        self.recipient_request(detail_path)
        viewed_delivery = self.delivery_rows(fixture)[0]
        baseline_activity_count = len(self.activity_rows())
        baseline_history = self.delivery_history_rows(fixture)
        acknowledged_at = datetime(
            2026,
            8,
            3,
            17,
            0,
            tzinfo=timezone.utc
        )

        _, missing_confirmation = self.recipient_request(
            detail_path + "/acknowledge",
            method="POST",
            now_utc=acknowledged_at
        )
        self.assertEqual(missing_confirmation.status_code, 400)
        _, response = self.recipient_request(
            detail_path + "/acknowledge",
            method="POST",
            data={"acknowledge": "yes"},
            now_utc=acknowledged_at
        )

        self.assertEqual(response.status_code, 302)
        conn = self.open_database()
        try:
            acknowledgements = conn.execute("""
                SELECT *
                FROM acknowledgements
                WHERE source_table = 'staff_notice_deliveries'
                  AND source_id = ?
                  AND user_id = 2
                  AND active = 1
            """, (delivery["delivery_id"],)).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(acknowledgements), 1)
        self.assertEqual(
            acknowledgements[0]["acknowledged_at"],
            "2026-08-03T17:00:00Z"
        )
        self.assertEqual(
            self.delivery_rows(fixture)[0]["first_viewed_at_utc"],
            viewed_delivery["first_viewed_at_utc"]
        )
        activities = self.activity_rows()[baseline_activity_count:]
        self.assertEqual(
            [row["activity_type"] for row in activities],
            ["record_acknowledged"]
        )
        self.assertEqual(
            activities[0]["activity_class"],
            "ACKNOWLEDGEMENT"
        )
        self.assertEqual(activities[0]["user_id"], 2)
        self.assertEqual(activities[0]["client_id"], 1)
        self.assertIsNone(activities[0]["shift_id"])
        self.assertEqual(
            activities[0]["related_table"],
            "acknowledgements"
        )
        self.assertEqual(
            activities[0]["related_id"],
            acknowledgements[0]["acknowledgement_id"]
        )
        self.assertEqual(activities[0]["success"], 1)
        self.assertEqual(
            activities[0]["details"],
            f"Notice ID: {fixture['notice_id']}; "
            f"Occurrence ID: {delivery['occurrence_id']}; "
            f"Delivery ID: {delivery['delivery_id']}; "
            "Recipient User ID: 2; Actor User ID: 2; "
            "Acknowledged at UTC: 2026-08-03T17:00:00Z"
        )
        self.assertEqual(
            self.delivery_history_rows(fixture),
            baseline_history
        )
        snapshot = self.database_snapshot()
        _, repeated = self.recipient_request(
            detail_path + "/acknowledge",
            method="POST",
            data={"acknowledge": "yes"},
            now_utc=datetime(
                2026,
                8,
                3,
                18,
                0,
                tzinfo=timezone.utc
            )
        )
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(self.database_snapshot(), snapshot)

    def test_recipient_acknowledgement_requires_view_and_ownership(self):
        fixture = self.create_recipient_notice()
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        path = (
            f"/staff-notices/delivery/{delivery['delivery_id']}"
            "/acknowledge"
        )
        before = self.database_snapshot()

        _, unviewed = self.recipient_request(
            path,
            method="POST",
            data={"acknowledge": "yes"}
        )
        self.assertEqual(unviewed.status_code, 409)
        self.assertEqual(self.database_snapshot(), before)
        _, other_worker = self.recipient_request(
            path,
            method="POST",
            data={"acknowledge": "yes"},
            user_id=3,
            role="Behaviour Consultant"
        )
        self.assertEqual(other_worker.status_code, 404)
        self.assertEqual(self.database_snapshot(), before)

    def test_recipient_overdue_acknowledgement_remains_accessible_and_late(self):
        fixture = self.create_recipient_notice(title="Overdue Notice")
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET due_at_utc = '2026-08-03T15:00:00Z'
                WHERE occurrence_id = ?
            """, (delivery["occurrence_id"],))
            conn.commit()
        finally:
            conn.close()
        path = f"/staff-notices/delivery/{delivery['delivery_id']}"

        _, detail = self.recipient_request(path)
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"still required", detail.data)
        _, acknowledged = self.recipient_request(
            path + "/acknowledge",
            method="POST",
            data={"acknowledge": "yes"},
            now_utc=datetime(
                2026,
                8,
                3,
                17,
                0,
                tzinfo=timezone.utc
            )
        )
        self.assertEqual(acknowledged.status_code, 302)
        _, reopened = self.recipient_request(path)
        self.assertEqual(reopened.status_code, 200)
        self.assertIn(b"Acknowledged Late", reopened.data)

    def test_recipient_rejects_unavailable_and_preserves_history(self):
        fixtures = [
            self.create_recipient_notice(title="NLR Recipient"),
            self.create_recipient_notice(title="Cancelled Recipient")
        ]
        self.recipient_request("/staff-notices")
        deliveries = [
            self.delivery_rows(fixture)[0]
            for fixture in fixtures
        ]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET requirement_status = 'No Longer Required',
                    recipient_access = 0
                WHERE delivery_id = ?
            """, (deliveries[0]["delivery_id"],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET requirement_status = 'Cancelled',
                    recipient_access = 0
                WHERE delivery_id = ?
            """, (deliveries[1]["delivery_id"],))
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()

        _, history = self.recipient_request("/staff-notices")
        self.assertIn(b"No Longer Required", history.data)
        self.assertIn(b"Cancelled", history.data)
        for delivery in deliveries:
            _, detail = self.recipient_request(
                f"/staff-notices/delivery/{delivery['delivery_id']}"
            )
            self.assertEqual(detail.status_code, 403)
            _, acknowledge = self.recipient_request(
                f"/staff-notices/delivery/{delivery['delivery_id']}"
                "/acknowledge",
                method="POST",
                data={"acknowledge": "yes"}
            )
            self.assertEqual(acknowledge.status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

    def test_recipient_acknowledgement_audit_failure_rolls_back(self):
        fixture = self.create_recipient_notice()
        self.recipient_request("/staff-notices")
        delivery = self.delivery_rows(fixture)[0]
        path = f"/staff-notices/delivery/{delivery['delivery_id']}"
        self.recipient_request(path)
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_recipient_ack_audit_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type = 'record_acknowledged'
                BEGIN
                    SELECT RAISE(ABORT, 'controlled ack audit failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()

        _, response = self.recipient_request(
            path + "/acknowledge",
            method="POST",
            data={"acknowledge": "yes"}
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.database_snapshot(), before)

    def management_tracking_request(
        self,
        path,
        *,
        user_id=1,
        role="Admin",
        now_utc=None
    ):
        if now_utc is None:
            now_utc = datetime(
                2026,
                8,
                3,
                17,
                0,
                tzinfo=timezone.utc
            )
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = role
            session_data["full_name"] = f"Manager {user_id}"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=now_utc
        ):
            response = client.get(path)
        return client, response

    def create_management_tracking_matrix(self):
        fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None",
            audience_rules=(("Selected Individual", None, 2),)
        )
        self.seed_eligibility(
            fixture,
            2,
            sources="Selected Individual"
        )
        occurrence_id = self.seed_one_time_occurrence(
            fixture,
            due_at="2026-08-03T16:00:00Z"
        )
        conn = self.open_database()
        try:
            conn.executemany("""
                INSERT INTO users (user_id, full_name, role, active)
                VALUES (?, ?, 'Behaviour Consultant', 1)
            """, (
                (6, "Late Recipient"),
                (7, "NLR Recipient"),
                (8, "Cancelled Recipient")
            ))
            conn.commit()
        finally:
            conn.close()

        user_ids = (2, 3, 5, 6, 7, 8)
        deliveries = {
            user_id: self.seed_delivery(occurrence_id, user_id)
            for user_id in user_ids
        }
        conn = self.open_database()
        try:
            conn.executemany("""
                INSERT INTO staff_notice_delivery_history
                (
                    delivery_id,
                    event_type,
                    new_requirement_status,
                    new_recipient_access,
                    changed_at_utc
                )
                VALUES (?, 'Assigned', 'Required', 1, ?)
            """, (
                (
                    delivery_id,
                    "2026-08-01T16:00:00Z"
                )
                for delivery_id in deliveries.values()
            ))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:00:00Z',
                    viewed_by_user_id = 3
                WHERE delivery_id = ?
            """, (deliveries[3],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T14:00:00Z',
                    viewed_by_user_id = 5
                WHERE delivery_id = ?
            """, (deliveries[5],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T14:00:00Z',
                    viewed_by_user_id = 6
                WHERE delivery_id = ?
            """, (deliveries[6],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET requirement_status = 'No Longer Required',
                    recipient_access = 0,
                    current_reason_code = 'Eligibility Ended',
                    current_reason_text = 'Historical removal test.'
                WHERE delivery_id = ?
            """, (deliveries[7],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET requirement_status = 'Cancelled',
                    recipient_access = 0,
                    current_reason_code = 'Notice Cancelled',
                    current_reason_text = 'Historical cancellation test.'
                WHERE delivery_id = ?
            """, (deliveries[8],))
            conn.commit()
        finally:
            conn.close()
        self.seed_staff_notice_acknowledgement(
            deliveries[5],
            5,
            "2026-08-03T16:00:00Z"
        )
        self.seed_staff_notice_acknowledgement(
            deliveries[6],
            6,
            "2026-08-03T16:00:01Z"
        )
        return fixture, occurrence_id, deliveries

    def test_management_tracking_permissions_list_navigation_and_missing_states(
        self
    ):
        fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None",
            audience_rules=(("Selected Individual", None, 2),)
        )
        draft_fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None"
        )
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notices
                SET status = 'Draft',
                    draft_active = 1,
                    published_by_user_id = NULL,
                    published_at_utc = NULL
                WHERE notice_id = ?
            """, (draft_fixture["notice_id"],))
            conn.commit()
        finally:
            conn.close()

        _, admin_list = self.management_tracking_request(
            "/staff-notices/manage"
        )
        self.assertEqual(admin_list.status_code, 200)
        self.assertIn(b"Track Recipients", admin_list.data)
        self.assertIn(
            (
                f"/staff-notices/{fixture['notice_id']}/tracking"
            ).encode(),
            admin_list.data
        )
        for role, user_id in (
            ("Admin", 1),
            ("Program Manager", 5),
            ("Director", 1)
        ):
            with self.subTest(role=role):
                _, response = self.management_tracking_request(
                    f"/staff-notices/{fixture['notice_id']}/tracking",
                    user_id=user_id,
                    role=role
                )
                self.assertEqual(response.status_code, 200)

        anonymous = app.app.test_client().get(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )
        _, worker = self.management_tracking_request(
            f"/staff-notices/{fixture['notice_id']}/tracking",
            user_id=2,
            role="Support Worker"
        )
        _, draft = self.management_tracking_request(
            f"/staff-notices/{draft_fixture['notice_id']}/tracking"
        )
        _, missing = self.management_tracking_request(
            "/staff-notices/999999/tracking"
        )
        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(worker.status_code, 403)
        self.assertEqual(draft.status_code, 404)
        self.assertEqual(missing.status_code, 404)

    def test_management_tracking_statuses_counts_history_and_read_only_display(
        self
    ):
        fixture, _, deliveries = self.create_management_tracking_matrix()
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notices
                SET title = '<script>tracking()</script>',
                    notice_text = '<script>alert(1)</script>'
                WHERE notice_id = ?
            """, (fixture["notice_id"],))
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()

        _, response = self.management_tracking_request(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )

        self.assertEqual(response.status_code, 200)
        for status in (
            b"Not Viewed",
            "Viewed \u2013 Awaiting Acknowledgement".encode(),
            b"Acknowledged",
            b"Acknowledged Late",
            b"No Longer Required",
            b"Cancelled"
        ):
            self.assertIn(status, response.data)
        self.assertIn(b"Total Deliveries", response.data)
        self.assertRegex(
            response.data,
            br"Total Deliveries</th>\s*<td>6</td>"
        )
        self.assertRegex(
            response.data,
            br"Outstanding</th>\s*<td>2</td>"
        )
        self.assertRegex(
            response.data,
            br"Overdue</th>\s*<td>2</td>"
        )
        self.assertIn(b"Support Worker", response.data)
        self.assertIn(b"Viewed by Behaviour Consultant", response.data)
        self.assertIn(b"Historical removal test.", response.data)
        self.assertIn(b"Historical cancellation test.", response.data)
        self.assertIn(b"Delivery changes", response.data)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", response.data)
        self.assertNotIn(b"<script>alert(1)</script>", response.data)
        for delivery_id in deliveries.values():
            self.assertEqual(
                sum(
                    delivery_id == row["delivery_id"]
                    for row in self.delivery_rows(fixture)
                ),
                1
            )
        self.assertEqual(self.database_snapshot(), before)

    def test_management_tracking_shift_context_and_zero_delivery_state(self):
        fixture, shift_id, _ = self.create_shift_notice_delivery(
            scheduled_end_time="15:00"
        )
        _, shift_response = self.management_tracking_request(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )
        self.assertEqual(shift_response.status_code, 200)
        self.assertIn(b"Active Client", shift_response.data)
        self.assertIn(b"2026-08-03", shift_response.data)
        self.assertIn(b"Day", shift_response.data)
        self.assertIn(b"Occurrences and Shift Context", shift_response.data)
        self.assertIn(b"Remove Worker", shift_response.data)
        self.assertIn(b"confirm_removal", shift_response.data)
        self.assertIn(
            b"authoritative shift sign-on workflow",
            shift_response.data
        )
        self.assertNotEqual(shift_id, 0)

        empty_fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None",
            audience_rules=(("Selected Individual", None, 4),)
        )
        _, empty_response = self.management_tracking_request(
            f"/staff-notices/{empty_fixture['notice_id']}/tracking"
        )
        self.assertEqual(empty_response.status_code, 200)
        self.assertRegex(
            empty_response.data,
            br"Total Deliveries</th>\s*<td>0</td>"
        )
        self.assertIn(
            b"No occurrences have been created",
            empty_response.data
        )

    def test_management_tracking_uses_corrected_deadline_dynamically(self):
        fixture, occurrence_id, deliveries = (
            self.create_management_tracking_matrix()
        )
        _, boundary_response = self.management_tracking_request(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )
        boundary_rows = re.findall(
            br"<tr[^>]*>.*?</tr>",
            boundary_response.data,
            re.DOTALL
        )
        boundary_recipient_row = next(
            row for row in boundary_rows
            if b"Program Manager" in row
        )
        self.assertIn(b">Acknowledged<", boundary_recipient_row)
        self.assertNotIn(b"Acknowledged Late", boundary_recipient_row)

        conn = self.open_database()
        try:
            original_acknowledged_at = conn.execute("""
                SELECT acknowledged_at
                FROM acknowledgements
                WHERE source_table = 'staff_notice_deliveries'
                  AND source_id = ?
                  AND active = 1
            """, (deliveries[5],)).fetchone()["acknowledged_at"]
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET due_at_utc = '2026-08-03T15:59:59Z',
                    due_at_is_provisional = 0
                WHERE occurrence_id = ?
            """, (occurrence_id,))
            conn.commit()
        finally:
            conn.close()

        _, corrected_response = self.management_tracking_request(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )
        corrected_rows = re.findall(
            br"<tr[^>]*>.*?</tr>",
            corrected_response.data,
            re.DOTALL
        )
        corrected_recipient_row = next(
            row for row in corrected_rows
            if b"Program Manager" in row
        )
        self.assertIn(b"Acknowledged Late", corrected_recipient_row)
        conn = self.open_database()
        try:
            management_status = next(
                delivery["derived_status"]
                for delivery in app._load_staff_notice_tracking(
                    conn,
                    fixture["notice_id"],
                    datetime(
                        2026,
                        8,
                        3,
                        17,
                        0,
                        tzinfo=timezone.utc
                    )
                )["historical_deliveries"]
                if delivery["user_id"] == 5
            )
            worker_status = app._load_recipient_staff_notice_deliveries(
                conn,
                5,
                datetime(
                    2026,
                    8,
                    3,
                    17,
                    0,
                    tzinfo=timezone.utc
                )
            )[0]["derived_status"]
            self.assertEqual(management_status, worker_status)
            self.assertEqual(
                conn.execute("""
                    SELECT acknowledged_at
                    FROM acknowledgements
                    WHERE source_table = 'staff_notice_deliveries'
                      AND source_id = ?
                      AND active = 1
                """, (deliveries[5],)).fetchone()["acknowledged_at"],
                original_acknowledged_at
            )
        finally:
            conn.close()

    def test_management_tracking_reconciles_only_notice_and_is_idempotent(self):
        fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None",
            audience_rules=(("Selected Individual", None, 2),)
        )
        self.seed_one_time_occurrence(fixture)
        unrelated = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None",
            audience_rules=(("Selected Individual", None, 3),)
        )
        self.seed_one_time_occurrence(unrelated)

        _, first = self.management_tracking_request(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )
        first_snapshot = self.database_snapshot()
        _, repeated = self.management_tracking_request(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(len(self.delivery_rows(fixture)), 1)
        self.assertEqual(self.delivery_rows(unrelated), [])
        self.assertEqual(self.database_snapshot(), first_snapshot)

    def test_management_tracking_reconciliation_failure_rolls_back(self):
        fixture = self.create_published_notice(
            occurrence_basis="One Time",
            recurrence_pattern="Once",
            shift_applicability="None",
            audience_rules=(("Selected Individual", None, 2),)
        )
        self.seed_one_time_occurrence(fixture)
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_tracking_reconciliation_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type =
                    'staff_notice_audience_eligibility_started'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled tracking reconciliation failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()

        _, response = self.management_tracking_request(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Please retry", response.data)
        self.assertEqual(self.database_snapshot(), before)

    def post_worker_end(self, shift_id, completed_at, data=None):
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=completed_at
        ):
            response = client.post(
                f"/shift/{shift_id}/end-shift",
                data=data or {}
            )
        return client, response

    def post_manager_end(
        self,
        shift_staff_id,
        correction_entry,
        *,
        date,
        time,
        reason="Forgotten sign-off correction.",
        ambiguous_occurrence=""
    ):
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=correction_entry
        ):
            response = client.post(
                f"/shift-staff/{shift_staff_id}/manager-sign-off",
                data={
                    "actual_end_date": date,
                    "actual_end_time": time,
                    "ambiguous_occurrence": ambiguous_occurrence,
                    "reason": reason
                }
            )
        return response

    def shift_staff_row(self, shift_staff_id):
        conn = self.open_database()
        try:
            return dict(conn.execute("""
                SELECT *
                FROM shift_staff
                WHERE shift_staff_id = ?
            """, (shift_staff_id,)).fetchone())
        finally:
            conn.close()

    def shift_row(self, shift_id):
        conn = self.open_database()
        try:
            return dict(conn.execute("""
                SELECT *
                FROM shifts
                WHERE shift_id = ?
            """, (shift_id,)).fetchone())
        finally:
            conn.close()

    def test_worker_completion_is_atomic_and_finalizes_notice_deadline(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery(
                scheduled_end_time="18:00"
            )
        )
        delivery_before = self.delivery_rows(fixture)[0]
        history_before = self.delivery_history_rows(fixture)
        conn = self.open_database()
        try:
            conn.execute("""
                INSERT INTO shift_tasks
                (
                    shift_task_id,
                    task_name,
                    task_stage,
                    requires_input,
                    input_label,
                    input_type,
                    active
                )
                VALUES (
                    1,
                    'Record handover',
                    'END_SHIFT',
                    1,
                    'Handover',
                    'text',
                    1
                )
            """)
            conn.commit()
        finally:
            conn.close()
        baseline_activity_count = len(self.activity_rows())
        completed_at = datetime(
            2026,
            8,
            3,
            20,
            0,
            tzinfo=timezone.utc
        )

        client, response = self.post_worker_end(
            shift_id,
            completed_at,
            {"shift_task_input_1": "Handover complete."}
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/login"))
        with client.session_transaction() as session_data:
            self.assertNotIn("user_id", session_data)
        assignment = self.shift_staff_row(shift_staff_id)
        self.assertEqual(
            assignment["actual_end_at_utc"],
            "2026-08-03T20:00:00Z"
        )
        self.assertEqual(
            assignment["sign_off_at"],
            "2026-08-03T20:00:00Z"
        )
        self.assertEqual(assignment["end_checklist_completed"], 1)
        self.assertEqual(assignment["active"], 0)
        shift = self.shift_row(shift_id)
        self.assertEqual(
            shift["actual_end_at_utc"],
            "2026-08-03T20:00:00Z"
        )
        self.assertEqual(shift["status"], "Open")
        self.assertIsNone(shift["closed_at"])
        occurrence = self.occurrence_rows(fixture)[0]
        self.assertEqual(
            occurrence["due_at_utc"],
            "2026-08-03T20:00:00Z"
        )
        self.assertEqual(occurrence["due_at_is_provisional"], 0)
        self.assertEqual(
            occurrence["due_at_updated_at_utc"],
            "2026-08-03T20:00:00Z"
        )
        conn = self.open_database()
        try:
            task_entries = conn.execute("""
                SELECT input_value
                FROM shift_task_entries
                ORDER BY shift_task_entry_id
            """).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            [row["input_value"] for row in task_entries],
            ["Handover complete."]
        )
        self.assertEqual(
            [
                row["activity_type"]
                for row in self.activity_rows()[baseline_activity_count:]
            ],
            [
                "shift_task_completed",
                "staff_notice_occurrence_due_at_adjusted",
                "end_shift_completed"
            ]
        )
        self.assertEqual(self.delivery_rows(fixture)[0], delivery_before)
        self.assertEqual(self.delivery_history_rows(fixture), history_before)

    def test_worker_completion_waits_for_last_active_assignment(self):
        fixture, shift_id, first_assignment = (
            self.create_shift_notice_delivery(
                scheduled_end_time="18:00"
            )
        )
        second_assignment = self.seed_shift_staff(shift_id, 3)
        first_end = datetime(
            2026,
            8,
            3,
            20,
            0,
            tzinfo=timezone.utc
        )

        _, response = self.post_worker_end(shift_id, first_end)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.shift_staff_row(first_assignment)["active"],
            0
        )
        self.assertEqual(
            self.shift_staff_row(second_assignment)["active"],
            1
        )
        self.assertIsNone(self.shift_row(shift_id)["actual_end_at_utc"])
        self.assertEqual(
            self.occurrence_rows(fixture)[0]["due_at_is_provisional"],
            1
        )

        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = 3
            session_data["role"] = "Behaviour Consultant"
        second_end = datetime(
            2026,
            8,
            3,
            21,
            0,
            tzinfo=timezone.utc
        )
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=second_end
        ):
            response = client.post(f"/shift/{shift_id}/end-shift", data={})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.shift_row(shift_id)["actual_end_at_utc"],
            "2026-08-03T21:00:00Z"
        )
        self.assertEqual(
            self.occurrence_rows(fixture)[0]["due_at_utc"],
            "2026-08-03T21:00:00Z"
        )

    def test_shift_end_uses_completed_workers_and_excludes_removals(self):
        fixture, shift_id, completing_assignment = (
            self.create_shift_notice_delivery()
        )
        removed_assignment = self.seed_shift_staff(shift_id, 3)
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                removed_assignment,
                1,
                "Worker reassigned.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        _, response = self.post_worker_end(
            shift_id,
            datetime(
                2026,
                8,
                3,
                22,
                0,
                tzinfo=timezone.utc
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.shift_staff_row(removed_assignment)["actual_end_at_utc"],
            None
        )
        self.assertEqual(
            self.shift_staff_row(completing_assignment)[
                "actual_end_at_utc"
            ],
            "2026-08-03T22:00:00Z"
        )
        self.assertEqual(
            self.shift_row(shift_id)["actual_end_at_utc"],
            "2026-08-03T22:00:00Z"
        )
        self.assertEqual(
            self.occurrence_rows(fixture)[0]["due_at_utc"],
            "2026-08-03T22:00:00Z"
        )

    def test_no_genuine_assignment_end_never_finalizes_shift(self):
        shift_id = self.seed_shift()
        removed_assignment = self.seed_shift_staff(shift_id, 2)
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.remove_shift_staff_assignment(
                conn,
                removed_assignment,
                1,
                "Shift assignment removed.",
                "2026-08-03T17:00:00Z"
            )
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""
                SELECT MAX(actual_end_at_utc) AS actual_end_at_utc
                FROM shift_staff
                WHERE shift_id = ?
                  AND active = 0
                  AND actual_end_at_utc IS NOT NULL
            """, (shift_id,)).fetchone()
            self.assertIsNone(row["actual_end_at_utc"])
            conn.rollback()
        finally:
            conn.close()
        self.assertIsNone(self.shift_row(shift_id)["actual_end_at_utc"])

        empty_shift_id = self.seed_shift(date="2026-08-04")
        self.assertIsNone(
            self.shift_row(empty_shift_id)["actual_end_at_utc"]
        )

    def test_manager_completion_records_historical_and_entry_times(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery(
                scheduled_end_time="13:00"
            )
        )
        baseline_activity_count = len(self.activity_rows())
        correction_entry = datetime(
            2026,
            8,
            4,
            1,
            0,
            tzinfo=timezone.utc
        )

        response = self.post_manager_end(
            shift_staff_id,
            correction_entry,
            date="2026-08-03",
            time="14:00",
            reason="Worker confirmed a forgotten sign-off."
        )

        self.assertEqual(response.status_code, 302)
        assignment = self.shift_staff_row(shift_staff_id)
        self.assertEqual(
            assignment["actual_end_at_utc"],
            "2026-08-03T21:00:00Z"
        )
        self.assertEqual(
            assignment["sign_off_at"],
            "2026-08-04T01:00:00Z"
        )
        self.assertEqual(assignment["end_checklist_completed"], 0)
        self.assertEqual(assignment["active"], 0)
        self.assertEqual(
            self.shift_row(shift_id)["actual_end_at_utc"],
            "2026-08-03T21:00:00Z"
        )
        activities = self.activity_rows()[baseline_activity_count:]
        self.assertEqual(
            [row["activity_type"] for row in activities],
            [
                "staff_notice_occurrence_due_at_adjusted",
                "manager_signed_staff_off"
            ]
        )
        manager_activity = activities[-1]
        self.assertEqual(manager_activity["user_id"], 1)
        self.assertEqual(manager_activity["shift_id"], shift_id)
        self.assertEqual(manager_activity["related_id"], shift_staff_id)
        self.assertEqual(
            manager_activity["details"],
            f"Shift Staff ID: {shift_staff_id}; "
            f"Shift ID: {shift_id}; Actor User ID: 1; "
            "Genuine actual end UTC: 2026-08-03T21:00:00Z; "
            "Correction entry UTC: 2026-08-04T01:00:00Z; "
            "Reason: Worker confirmed a forgotten sign-off."
        )
        self.assertEqual(
            self.occurrence_rows(fixture)[0]["due_at_utc"],
            "2026-08-03T21:00:00Z"
        )

    def test_manager_completion_preserves_permissions_and_form_fields(self):
        shift_id = self.seed_shift()
        shift_staff_id = self.seed_shift_staff(shift_id, 2)
        client = app.app.test_client()

        response = client.get(
            f"/shift-staff/{shift_staff_id}/manager-sign-off"
        )
        self.assertEqual(response.status_code, 302)

        with client.session_transaction() as session_data:
            session_data["user_id"] = 2
            session_data["role"] = "Support Worker"
        response = client.get(
            f"/shift-staff/{shift_staff_id}/manager-sign-off"
        )
        self.assertEqual(response.status_code, 403)

        with client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"
        response = client.get(
            f"/shift-staff/{shift_staff_id}/manager-sign-off"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="actual_end_date"', response.data)
        self.assertIn(b'name="actual_end_time"', response.data)
        self.assertIn(b'name="ambiguous_occurrence"', response.data)
        self.assertIn(b'name="reason"', response.data)

    def test_manager_completion_waits_for_other_active_worker(self):
        fixture, shift_id, shift_staff_id = (
            self.create_shift_notice_delivery()
        )
        self.seed_shift_staff(shift_id, 3)

        response = self.post_manager_end(
            shift_staff_id,
            datetime(
                2026,
                8,
                4,
                1,
                0,
                tzinfo=timezone.utc
            ),
            date="2026-08-03",
            time="14:00"
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.shift_row(shift_id)["actual_end_at_utc"])
        self.assertEqual(
            self.occurrence_rows(fixture)[0]["due_at_is_provisional"],
            0
        )

    def test_manager_historical_time_validation_and_preserved_values(self):
        shift_id = self.seed_shift(date="2024-03-10", shift_type="Day")
        shift_staff_id = self.seed_shift_staff(shift_id, 2, "08:00")
        response = self.post_manager_end(
            shift_staff_id,
            datetime(
                2024,
                3,
                11,
                0,
                0,
                tzinfo=timezone.utc
            ),
            date="2024-03-10",
            time="02:30",
            reason="Preserved reason."
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"does not exist", response.data)
        self.assertIn(b"2024-03-10", response.data)
        self.assertIn(b"02:30", response.data)
        self.assertIn(b"Preserved reason.", response.data)
        self.assertEqual(self.shift_staff_row(shift_staff_id)["active"], 1)

        shift_id = self.seed_shift(
            date="2024-11-02",
            shift_type="Overnight"
        )
        shift_staff_id = self.seed_shift_staff(shift_id, 2, "23:00")
        response = self.post_manager_end(
            shift_staff_id,
            datetime(
                2024,
                11,
                4,
                0,
                0,
                tzinfo=timezone.utc
            ),
            date="2024-11-03",
            time="01:30"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"occurs twice", response.data)
        self.assertIn(b"First occurrence", response.data)
        self.assertIn(b"Second occurrence", response.data)

    def test_manager_ambiguous_time_choices_convert_to_distinct_utc(self):
        first = app.staff_notice_manager_local_datetime_to_utc(
            "2024-11-03T01:30",
            "first"
        )
        second = app.staff_notice_manager_local_datetime_to_utc(
            "2024-11-03T01:30",
            "second"
        )
        winter = app.staff_notice_manager_local_datetime_to_utc(
            "2026-01-15T12:00"
        )
        summer = app.staff_notice_manager_local_datetime_to_utc(
            "2026-07-15T12:00"
        )

        self.assertEqual(
            first,
            datetime(2024, 11, 3, 8, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            second,
            datetime(2024, 11, 3, 9, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            winter,
            datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(
            summer,
            datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc)
        )

    def test_manager_rejects_reason_future_start_and_invalid_start(self):
        cases = (
            (
                "2026-08-03",
                "14:00",
                "",
                "08:00",
                b"correction reason"
            ),
            (
                "2026-08-05",
                "14:00",
                "Reason.",
                "08:00",
                b"future-dated"
            ),
            (
                "2026-08-03",
                "07:00",
                "Reason.",
                "08:00",
                b"cannot precede"
            ),
            (
                "2026-08-03",
                "14:00",
                "Reason.",
                None,
                b"requires repair"
            ),
            (
                "2026-08-03",
                "14:00",
                "Reason.",
                "16:00",
                b"inconsistent"
            )
        )
        for index, (
            end_date,
            end_time,
            reason,
            start_time,
            expected
        ) in enumerate(cases):
            with self.subTest(index=index):
                shift_id = self.seed_shift(date="2026-08-03")
                shift_staff_id = self.seed_shift_staff(
                    shift_id,
                    2,
                    start_time
                )
                response = self.post_manager_end(
                    shift_staff_id,
                    datetime(
                        2026,
                        8,
                        4,
                        1,
                        0,
                        tzinfo=timezone.utc
                    ),
                    date=end_date,
                    time=end_time,
                    reason=reason
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected, response.data)
                self.assertEqual(
                    self.shift_staff_row(shift_staff_id)["active"],
                    1
                )

    def test_completion_repeat_and_inconsistent_states_are_guarded(self):
        _, shift_id, shift_staff_id = self.create_shift_notice_delivery()
        completed_at = datetime(
            2026,
            8,
            3,
            20,
            0,
            tzinfo=timezone.utc
        )
        _, first_response = self.post_worker_end(shift_id, completed_at)
        snapshot = self.database_snapshot()
        _, repeated_response = self.post_worker_end(
            shift_id,
            datetime(
                2026,
                8,
                3,
                21,
                0,
                tzinfo=timezone.utc
            )
        )
        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(repeated_response.status_code, 302)
        self.assertEqual(self.database_snapshot(), snapshot)

        response = self.post_manager_end(
            shift_staff_id,
            datetime(
                2026,
                8,
                4,
                1,
                0,
                tzinfo=timezone.utc
            ),
            date="2026-08-03",
            time="13:00"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.database_snapshot(), snapshot)
        response = self.post_manager_end(
            shift_staff_id,
            datetime(
                2026,
                8,
                4,
                1,
                0,
                tzinfo=timezone.utc
            ),
            date="2026-08-03",
            time="14:00"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.database_snapshot(), snapshot)

    def test_removed_and_inconsistent_active_assignments_are_rejected(self):
        shift_id = self.seed_shift()
        removed_assignment = self.seed_shift_staff(shift_id, 2)
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE shift_staff
                SET active = 0
                WHERE shift_staff_id = ?
            """, (removed_assignment,))
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        response = self.post_manager_end(
            removed_assignment,
            datetime(
                2026,
                8,
                4,
                1,
                0,
                tzinfo=timezone.utc
            ),
            date="2026-08-03",
            time="14:00"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

        inconsistent_shift = self.seed_shift(date="2026-08-04")
        inconsistent_assignment = self.seed_shift_staff(
            inconsistent_shift,
            2
        )
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE shift_staff
                SET actual_end_at_utc = '2026-08-04T20:00:00Z'
                WHERE shift_staff_id = ?
            """, (inconsistent_assignment,))
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        _, response = self.post_worker_end(
            inconsistent_shift,
            datetime(
                2026,
                8,
                4,
                21,
                0,
                tzinfo=timezone.utc
            )
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

    def test_worker_completion_rolls_back_each_route_integration_stage(self):
        trigger_cases = (
            (
                "shift_staff",
                "BEFORE UPDATE",
                "",
                "controlled assignment failure"
            ),
            (
                "shift_task_entries",
                "BEFORE INSERT",
                "",
                "controlled checklist failure"
            ),
            (
                "activity_log",
                "BEFORE INSERT",
                "WHEN NEW.activity_type = 'shift_task_completed'",
                "controlled checklist activity failure"
            ),
            (
                "staff_notice_occurrences",
                "BEFORE UPDATE",
                "",
                "controlled occurrence failure"
            ),
            (
                "activity_log",
                "BEFORE INSERT",
                "WHEN NEW.activity_type = "
                "'staff_notice_occurrence_due_at_adjusted'",
                "controlled due activity failure"
            ),
            (
                "activity_log",
                "BEFORE INSERT",
                "WHEN NEW.activity_type = 'end_shift_completed'",
                "controlled final activity failure"
            )
        )
        for index, (
            table_name,
            timing,
            condition,
            message
        ) in enumerate(trigger_cases):
            with self.subTest(message=message):
                fixture, shift_id, _ = self.create_shift_notice_delivery(
                    shift_date=f"2026-08-0{index + 3}"
                )
                if table_name == "shift_task_entries" or (
                    table_name == "activity_log"
                    and "checklist activity" in message
                ):
                    conn = self.open_database()
                    try:
                        conn.execute("""
                            INSERT INTO shift_tasks
                            (
                                shift_task_id,
                                task_name,
                                task_stage,
                                requires_input,
                                active
                            )
                            VALUES (?, 'Check', 'END_SHIFT', 0, 1)
                        """, (index + 1,))
                        conn.commit()
                    finally:
                        conn.close()
                conn = self.open_database()
                try:
                    conn.execute(f"""
                        CREATE TRIGGER control_completion_failure_{index}
                        {timing} ON {table_name}
                        {condition}
                        BEGIN
                            SELECT RAISE(ABORT, '{message}');
                        END
                    """)
                    conn.commit()
                finally:
                    conn.close()
                before = self.database_snapshot()

                _, response = self.post_worker_end(
                    shift_id,
                    datetime(
                        2026,
                        8,
                        index + 3,
                        22,
                        0,
                        tzinfo=timezone.utc
                    )
                )

                self.assertEqual(response.status_code, 500)
                self.assertEqual(self.database_snapshot(), before)
                self.assertEqual(
                    [
                        row["event_type"]
                        for row in self.delivery_history_rows(fixture)
                    ],
                    ["Assigned"]
                )

    def test_classification_activity_failure_rolls_back_completion_route(self):
        fixture, shift_id, _ = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET due_at_utc = '2026-08-03T19:00:00Z',
                    due_at_is_provisional = 1
                WHERE occurrence_id = ?
            """, (delivery["occurrence_id"],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:00:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (delivery["delivery_id"],))
            conn.commit()
        finally:
            conn.close()
        self.seed_staff_notice_acknowledgement(
            delivery["delivery_id"],
            2,
            "2026-08-03T17:00:00Z"
        )
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_completion_classification_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type =
                    'staff_notice_acknowledgement_classification_changed'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled classification activity failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()

        _, response = self.post_worker_end(
            shift_id,
            datetime(
                2026,
                8,
                3,
                16,
                0,
                tzinfo=timezone.utc
            )
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.database_snapshot(), before)

    def test_shift_deadline_finalizes_provisional_and_preserves_delivery(self):
        fixture, shift_id, _ = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET due_at_utc = '2026-08-03T23:00:00Z',
                    due_at_is_provisional = 1
                WHERE occurrence_id = ?
            """, (delivery["occurrence_id"],))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T16:00:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (delivery["delivery_id"],))
            conn.commit()
        finally:
            conn.close()
        original_delivery = self.delivery_rows(fixture)[0]
        original_history = self.delivery_history_rows(fixture)
        baseline_activity_count = len(self.activity_rows())

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = app.finalize_shift_notice_due_at(
                conn,
                shift_id,
                "2026-08-03T22:00:00Z",
                1,
                "2026-08-04T01:00:00Z",
                "Manager supplied the genuine operational end."
            )
            conn.commit()
        finally:
            conn.close()

        occurrence = self.occurrence_rows(fixture)[0]
        activities = self.activity_rows()[baseline_activity_count:]
        self.assertEqual(result, {
            "shift_end_updated": 1,
            "occurrences_adjusted": 1,
            "acknowledgement_classifications_changed": 0
        })
        self.assertEqual(occurrence["due_at_utc"], "2026-08-03T22:00:00Z")
        self.assertEqual(occurrence["due_at_is_provisional"], 0)
        self.assertEqual(
            occurrence["due_at_updated_at_utc"],
            "2026-08-04T01:00:00Z"
        )
        self.assertEqual(self.delivery_rows(fixture)[0], original_delivery)
        self.assertEqual(self.delivery_history_rows(fixture), original_history)
        self.assertEqual(
            [row["activity_type"] for row in activities],
            ["staff_notice_occurrence_due_at_adjusted"]
        )
        activity = activities[0]
        self.assertEqual(activity["user_id"], 1)
        self.assertEqual(activity["shift_id"], shift_id)
        self.assertEqual(
            activity["related_table"],
            "staff_notice_occurrences"
        )
        self.assertEqual(
            activity["related_id"],
            occurrence["occurrence_id"]
        )
        self.assertEqual(
            activity["summary"],
            "Staff Notice occurrence deadline adjusted: "
            "Reconciliation Notice"
        )
        self.assertEqual(
            activity["details"],
            f"Notice ID: {fixture['notice_id']}; "
            f"Occurrence ID: {occurrence['occurrence_id']}; "
            f"Shift ID: {shift_id}; "
            "Old due at: 2026-08-03T23:00:00Z; "
            "New due at: 2026-08-03T22:00:00Z; "
            "Prior deadline provisional: 1; "
            "Reason: Manager supplied the genuine operational end.; "
            "Effective at UTC: 2026-08-04T01:00:00Z"
        )
        conn = self.open_database()
        try:
            shift = conn.execute(
                "SELECT actual_end_at_utc, closed_at FROM shifts "
                "WHERE shift_id = ?",
                (shift_id,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(shift["actual_end_at_utc"], "2026-08-03T22:00:00Z")
        self.assertIsNone(shift["closed_at"])

    def test_shift_deadline_finalizes_later_or_initially_missing_deadline(self):
        fixtures = []
        for index, old_due in enumerate(
            ("2026-08-03T20:00:00Z", None)
        ):
            fixture, shift_id, _ = self.create_shift_notice_delivery(
                shift_date=f"2026-08-0{index + 3}"
            )
            occurrence = self.occurrence_rows(fixture)[0]
            conn = self.open_database()
            try:
                conn.execute("""
                    UPDATE staff_notice_occurrences
                    SET due_at_utc = ?,
                        due_at_is_provisional = ?
                    WHERE occurrence_id = ?
                """, (
                    old_due,
                    int(old_due is not None),
                    occurrence["occurrence_id"]
                ))
                conn.commit()
            finally:
                conn.close()
            fixtures.append((fixture, shift_id))

        for fixture, shift_id in fixtures:
            conn = self.open_database()
            try:
                conn.execute("BEGIN IMMEDIATE")
                app.finalize_shift_notice_due_at(
                    conn,
                    shift_id,
                    "2026-08-04T02:00:00Z",
                    1,
                    "2026-08-04T03:00:00Z"
                )
                conn.commit()
            finally:
                conn.close()
            occurrence = self.occurrence_rows(fixture)[0]
            self.assertEqual(
                occurrence["due_at_utc"],
                "2026-08-04T02:00:00Z"
            )
            self.assertEqual(occurrence["due_at_is_provisional"], 0)

    def test_shift_deadline_same_final_value_is_no_op_and_repairs_drift(self):
        fixture, shift_id, _ = self.create_shift_notice_delivery()
        occurrence = self.occurrence_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE shifts
                SET actual_end_at_utc = '2026-08-03T22:00:00Z'
                WHERE shift_id = ?
            """, (shift_id,))
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET due_at_utc = '2026-08-03T22:00:00Z',
                    due_at_is_provisional = 1
                WHERE occurrence_id = ?
            """, (occurrence["occurrence_id"],))
            conn.commit()
        finally:
            conn.close()
        baseline_activity_count = len(self.activity_rows())

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            repaired = app.finalize_shift_notice_due_at(
                conn,
                shift_id,
                "2026-08-03T22:00:00Z",
                1,
                "2026-08-04T01:00:00Z"
            )
            repeated = app.finalize_shift_notice_due_at(
                conn,
                shift_id,
                "2026-08-03T22:00:00Z",
                1,
                "2026-08-04T02:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(repaired, {
            "shift_end_updated": 0,
            "occurrences_adjusted": 1,
            "acknowledgement_classifications_changed": 0
        })
        self.assertEqual(repeated, {
            "shift_end_updated": 0,
            "occurrences_adjusted": 0,
            "acknowledgement_classifications_changed": 0
        })
        self.assertEqual(
            len(self.activity_rows()) - baseline_activity_count,
            1
        )

    def test_shift_deadline_adjusts_multiple_notices_not_workers(self):
        first, shift_id, _ = self.create_shift_notice_delivery()
        second = self.create_published_notice(
            occurrence_basis="Shift",
            shift_applicability="Every Shift",
            audience_rules=(("Applicable Shift Staff", None, None),)
        )
        self.seed_shift_staff(shift_id, 3)
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            app.reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                3,
                "2026-08-03T16:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        baseline_activity_count = len(self.activity_rows())

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = app.finalize_shift_notice_due_at(
                conn,
                shift_id,
                "2026-08-03T23:00:00Z",
                1,
                "2026-08-04T00:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(result["occurrences_adjusted"], 2)
        self.assertEqual(
            [row["due_at_utc"] for row in self.occurrence_rows(first)],
            ["2026-08-03T23:00:00Z"]
        )
        self.assertEqual(
            [row["due_at_utc"] for row in self.occurrence_rows(second)],
            ["2026-08-03T23:00:00Z"]
        )
        conn = self.open_database()
        try:
            worker_ends = conn.execute("""
                SELECT actual_end_at_utc
                FROM shift_staff
                WHERE shift_id = ?
                ORDER BY shift_staff_id
            """, (shift_id,)).fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in worker_ends], [None, None])
        self.assertEqual(
            [
                row["activity_type"]
                for row in self.activity_rows()[baseline_activity_count:]
            ],
            [
                "staff_notice_occurrence_due_at_adjusted",
                "staff_notice_occurrence_due_at_adjusted"
            ]
        )

    def assert_shift_deadline_acknowledgement_classification(
        self,
        old_due_at_utc,
        new_due_at_utc,
        acknowledged_at_utc,
        expected_old,
        expected_new,
        expected_change_count
    ):
        fixture, shift_id, _ = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET due_at_utc = ?,
                    due_at_is_provisional = 1
                WHERE occurrence_id = ?
            """, (old_due_at_utc, delivery["occurrence_id"]))
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-08-03T15:00:00Z',
                    viewed_by_user_id = 2
                WHERE delivery_id = ?
            """, (delivery["delivery_id"],))
            conn.commit()
        finally:
            conn.close()
        acknowledgement_id = self.seed_staff_notice_acknowledgement(
            delivery["delivery_id"],
            2,
            acknowledged_at_utc
        )
        original_delivery = self.delivery_rows(fixture)[0]
        original_history = self.delivery_history_rows(fixture)
        baseline_activity_count = len(self.activity_rows())

        self.assertEqual(
            app.get_recipient_staff_notice_status(
                active_acknowledgement_at_utc=acknowledged_at_utc,
                due_at_utc=old_due_at_utc,
                requirement_status="Required",
                first_viewed_at_utc="2026-08-03T15:00:00Z"
            ),
            expected_old
        )
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = app.finalize_shift_notice_due_at(
                conn,
                shift_id,
                new_due_at_utc,
                1,
                "2026-08-04T01:00:00Z",
                "Corrected actual shift end."
            )
            conn.commit()
        finally:
            conn.close()

        activities = self.activity_rows()[baseline_activity_count:]
        self.assertEqual(
            result["acknowledgement_classifications_changed"],
            expected_change_count
        )
        self.assertEqual(
            [row["activity_type"] for row in activities],
            ["staff_notice_occurrence_due_at_adjusted"]
            + (
                [
                    "staff_notice_acknowledgement_classification_changed"
                ]
                if expected_change_count else []
            )
        )
        if expected_change_count:
            classification_activity = activities[1]
            occurrence = self.occurrence_rows(fixture)[0]
            self.assertEqual(
                classification_activity["related_table"],
                "acknowledgements"
            )
            self.assertEqual(
                classification_activity["related_id"],
                acknowledgement_id
            )
            self.assertEqual(
                classification_activity["activity_class"],
                "STAFF_NOTICE"
            )
            self.assertEqual(
                classification_activity["user_id"],
                1
            )
            self.assertEqual(
                classification_activity["client_id"],
                1
            )
            self.assertEqual(
                classification_activity["shift_id"],
                shift_id
            )
            self.assertEqual(
                classification_activity["summary"],
                "Staff Notice acknowledgement classification changed: "
                "Reconciliation Notice"
            )
            self.assertEqual(
                classification_activity["details"],
                f"Notice ID: {fixture['notice_id']}; "
                f"Acknowledgement ID: {acknowledgement_id}; "
                f"Delivery ID: {delivery['delivery_id']}; "
                f"Occurrence ID: {occurrence['occurrence_id']}; "
                f"Shift ID: {shift_id}; "
                f"Old classification: {expected_old}; "
                f"New classification: {expected_new}; "
                f"Old due at: {old_due_at_utc}; "
                f"New due at: {new_due_at_utc}; "
                f"Acknowledged at: {acknowledged_at_utc}; "
                "Reason: Corrected actual shift end.; "
                "Effective at UTC: 2026-08-04T01:00:00Z"
            )
        self.assertEqual(self.delivery_rows(fixture)[0], original_delivery)
        self.assertEqual(self.delivery_history_rows(fixture), original_history)
        conn = self.open_database()
        try:
            acknowledgement = conn.execute("""
                SELECT acknowledged_at, active
                FROM acknowledgements
                WHERE acknowledgement_id = ?
            """, (acknowledgement_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(
            acknowledgement["acknowledged_at"],
            acknowledged_at_utc
        )
        self.assertEqual(acknowledgement["active"], 1)

    def test_shift_deadline_acknowledgement_remains_on_time(self):
        self.assert_shift_deadline_acknowledgement_classification(
            "2026-08-03T18:00:00Z",
            "2026-08-03T19:00:00Z",
            "2026-08-03T17:00:00Z",
            "Acknowledged",
            "Acknowledged",
            0
        )

    def test_shift_deadline_acknowledgement_changes_to_late(self):
        self.assert_shift_deadline_acknowledgement_classification(
            "2026-08-03T19:00:00Z",
            "2026-08-03T16:00:00Z",
            "2026-08-03T17:00:00Z",
            "Acknowledged",
            "Acknowledged Late",
            1
        )

    def test_shift_deadline_late_acknowledgement_changes_to_on_time(self):
        self.assert_shift_deadline_acknowledgement_classification(
            "2026-08-03T16:00:00Z",
            "2026-08-03T19:00:00Z",
            "2026-08-03T17:00:00Z",
            "Acknowledged Late",
            "Acknowledged",
            1
        )

    def test_shift_deadline_normalizes_aware_timestamp_and_rejects_naive(self):
        fixture, shift_id, _ = self.create_shift_notice_delivery(
            shift_date="2026-11-01",
            shift_type="Overnight",
            effective_start="2026-10-30T07:00:00Z",
            expires_at="2026-11-10T07:59:59Z"
        )
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = app.finalize_shift_notice_due_at(
                conn,
                shift_id,
                "2026-11-02T00:30:00-08:00",
                1,
                "2026-11-02T09:00:00Z"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(result["occurrences_adjusted"], 1)
        self.assertEqual(
            self.occurrence_rows(fixture)[0]["due_at_utc"],
            "2026-11-02T08:30:00Z"
        )
        before = self.database_snapshot()

        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(ValueError, "include a UTC offset"):
                app.finalize_shift_notice_due_at(
                    conn,
                    shift_id,
                    "2026-11-02T01:30:00",
                    1,
                    "2026-11-02T10:00:00Z"
                )
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.database_snapshot(), before)

    def test_shift_deadline_occurrence_failure_rolls_back_shift_end(self):
        _, shift_id, _ = self.create_shift_notice_delivery()
        before = self.database_snapshot()
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_shift_deadline_update_failure
                BEFORE UPDATE ON staff_notice_occurrences
                BEGIN
                    SELECT RAISE(ABORT, 'controlled deadline failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled deadline failure"
            ):
                app.finalize_shift_notice_due_at(
                    conn,
                    shift_id,
                    "2026-08-03T22:00:00Z",
                    1,
                    "2026-08-04T01:00:00Z"
                )
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.database_snapshot(), before)

    def test_shift_deadline_due_activity_failure_rolls_back_everything(self):
        _, shift_id, _ = self.create_shift_notice_delivery()
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_due_activity_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type =
                    'staff_notice_occurrence_due_at_adjusted'
                BEGIN
                    SELECT RAISE(ABORT, 'controlled due activity failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled due activity failure"
            ):
                app.finalize_shift_notice_due_at(
                    conn,
                    shift_id,
                    "2026-08-03T22:00:00Z",
                    1,
                    "2026-08-04T01:00:00Z"
                )
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.database_snapshot(), before)

    def test_shift_deadline_classification_activity_failure_rolls_back(self):
        fixture, shift_id, _ = self.create_shift_notice_delivery()
        delivery = self.delivery_rows(fixture)[0]
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET due_at_utc = '2026-08-03T19:00:00Z',
                    due_at_is_provisional = 1
                WHERE occurrence_id = ?
            """, (delivery["occurrence_id"],))
            conn.commit()
        finally:
            conn.close()
        self.seed_staff_notice_acknowledgement(
            delivery["delivery_id"],
            2,
            "2026-08-03T17:00:00Z"
        )
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER control_classification_activity_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type =
                    'staff_notice_acknowledgement_classification_changed'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled classification activity failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        conn = self.open_database()

        try:
            conn.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled classification activity failure"
            ):
                app.finalize_shift_notice_due_at(
                    conn,
                    shift_id,
                    "2026-08-03T16:00:00Z",
                    1,
                    "2026-08-04T01:00:00Z"
                )
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.database_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
