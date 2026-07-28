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
                    scheduled_end_time TEXT
                );

                CREATE TABLE shift_staff (
                    shift_staff_id INTEGER PRIMARY KEY,
                    shift_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    actual_start_time TEXT,
                    actual_end_time TEXT,
                    start_checklist_completed INTEGER NOT NULL DEFAULT 0,
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

    def seed_shift(self, *, date="2026-08-03", shift_type="Day"):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO shifts
                (client_id, shift_date, shift_type, status)
                VALUES (1, ?, ?, 'Open')
            """, (date, shift_type))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def seed_shift_staff(self, shift_id, user_id):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, ?, '08:00', 1)
            """, (shift_id, user_id))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

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


if __name__ == "__main__":
    unittest.main()
