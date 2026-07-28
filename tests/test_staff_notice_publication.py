import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from werkzeug.datastructures import MultiDict

import app
import add_staff_notices_tables as staff_notice_schema


LATER_PUBLICATION_TABLES = (
    "acknowledgements",
)


class PublicationTrackingConnection:

    def __init__(
        self,
        connection,
        *,
        commit_error=None,
        rollback_error=None,
        close_error=None
    ):
        self.connection = connection
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.begin_calls = 0
        self.eligibility_insert_calls = 0
        self.occurrence_insert_calls = 0
        self.delivery_insert_calls = 0
        self.delivery_history_insert_calls = 0
        self.activity_insert_calls = 0
        self.update_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        normalized_sql = " ".join(sql.split())

        if normalized_sql == "BEGIN IMMEDIATE":
            self.begin_calls += 1
        if normalized_sql.startswith(
            "INSERT INTO staff_notice_audience_eligibility_periods"
        ):
            self.eligibility_insert_calls += 1
        if normalized_sql.startswith(
            "INSERT INTO staff_notice_occurrences"
        ):
            self.occurrence_insert_calls += 1
        if normalized_sql.startswith(
            "INSERT INTO staff_notice_deliveries"
        ):
            self.delivery_insert_calls += 1
        if normalized_sql.startswith(
            "INSERT INTO staff_notice_delivery_history"
        ):
            self.delivery_history_insert_calls += 1
        if normalized_sql.startswith("INSERT INTO activity_log"):
            self.activity_insert_calls += 1
        if normalized_sql.startswith(
            "UPDATE staff_notices SET status = 'Published'"
        ):
            self.update_calls += 1

        return self.connection.execute(sql, parameters)

    def commit(self):
        self.commit_calls += 1

        if self.commit_error is not None:
            raise self.commit_error

        self.connection.commit()

    def rollback(self):
        self.rollback_calls += 1
        self.connection.rollback()

        if self.rollback_error is not None:
            raise self.rollback_error

    def close(self):
        self.close_calls += 1
        self.connection.close()

        if self.close_error is not None:
            raise self.close_error


class StaffNoticePublicationTests(unittest.TestCase):

    FIXED_NOW = datetime(2026, 7, 31, 19, 0, tzinfo=timezone.utc)
    FIXED_TIMESTAMP = "2026-07-31T19:00:00Z"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "publication.db"
        )
        self.original_database_name = app.DB_NAME
        self.original_now = app.get_application_now_utc
        self.addCleanup(self.restore_application_state)
        app.DB_NAME = self.database_path
        app.get_application_now_utc = lambda: self.FIXED_NOW
        self.create_database()

    def restore_application_state(self):
        app.DB_NAME = self.original_database_name
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
                (3, "Inactive Director", "Director", 0),
                (4, "Support Worker", "Support Worker", 1)
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

    def tracking_connection(self, **kwargs):
        return PublicationTrackingConnection(
            self.open_database(),
            **kwargs
        )

    def create_notice(
        self,
        *,
        audience=True,
        audience_rules=None,
        schedule=True
    ):
        conn = self.open_database()

        try:
            cursor = conn.execute("""
                INSERT INTO staff_notices
                (
                    title,
                    notice_text,
                    priority,
                    status,
                    draft_active,
                    effective_start_at_utc,
                    expires_at_utc,
                    until_withdrawn,
                    version_number,
                    created_by_user_id,
                    created_at_utc
                )
                VALUES (
                    'Publication Foundation',
                    'Controlled publication test.',
                    'Important',
                    'Draft',
                    1,
                    '2026-08-01T08:00:00Z',
                    NULL,
                    1,
                    1,
                    1,
                    '2026-07-30T19:00:00Z'
                )
            """)
            notice_id = cursor.lastrowid

            if audience:
                cursor = conn.execute("""
                    INSERT INTO staff_notice_audiences
                    (notice_id, created_at_utc)
                    VALUES (?, '2026-07-30T19:00:00Z')
                """, (notice_id,))
                audience_id = cursor.lastrowid
                if audience_rules is None:
                    audience_rules = ((
                        "All Support Workers",
                        None,
                        None
                    ),)

                conn.executemany("""
                    INSERT INTO staff_notice_audience_rules
                    (
                        audience_id,
                        rule_type,
                        role_name,
                        user_id,
                        created_at_utc
                    )
                    VALUES (?, ?, ?, ?, '2026-07-30T19:00:00Z')
                """, (
                    (audience_id, rule_type, role_name, user_id)
                    for rule_type, role_name, user_id in audience_rules
                ))

            if schedule:
                conn.execute("""
                    INSERT INTO staff_notice_schedules
                    (
                        notice_id,
                        occurrence_basis,
                        recurrence_pattern,
                        shift_applicability,
                        created_at_utc
                    )
                    VALUES (?, 'One Time', 'Once', 'None',
                            '2026-07-30T19:00:00Z')
                """, (notice_id,))

            conn.commit()
            return notice_id
        finally:
            conn.close()

    def notice_row(self, notice_id):
        conn = self.open_database()

        try:
            return dict(conn.execute("""
                SELECT *
                FROM staff_notices
                WHERE notice_id = ?
            """, (notice_id,)).fetchone())
        finally:
            conn.close()

    def eligibility_rows(self, notice_id):
        conn = self.open_database()

        try:
            return [
                dict(row)
                for row in conn.execute("""
                    SELECT ep.*
                    FROM staff_notice_audience_eligibility_periods ep
                    JOIN staff_notice_audiences a
                        ON ep.audience_id = a.audience_id
                    WHERE a.notice_id = ?
                    ORDER BY ep.user_id, ep.eligibility_period_id
                """, (notice_id,)).fetchall()
            ]
        finally:
            conn.close()

    def occurrence_rows(self, notice_id):
        conn = self.open_database()

        try:
            return [
                dict(row)
                for row in conn.execute("""
                    SELECT o.*
                    FROM staff_notice_occurrences o
                    JOIN staff_notice_schedules s
                        ON o.schedule_id = s.schedule_id
                    WHERE s.notice_id = ?
                    ORDER BY o.occurrence_id
                """, (notice_id,)).fetchall()
            ]
        finally:
            conn.close()

    def delivery_rows(self, notice_id):
        conn = self.open_database()

        try:
            return [
                dict(row)
                for row in conn.execute("""
                    SELECT
                        d.*,
                        o.occurrence_kind,
                        o.occurrence_date,
                        o.shift_id,
                        o.due_at_utc
                    FROM staff_notice_deliveries d
                    JOIN staff_notice_occurrences o
                        ON d.occurrence_id = o.occurrence_id
                    JOIN staff_notice_schedules s
                        ON o.schedule_id = s.schedule_id
                    WHERE s.notice_id = ?
                    ORDER BY d.occurrence_id, d.user_id
                """, (notice_id,)).fetchall()
            ]
        finally:
            conn.close()

    def delivery_history_rows(self, notice_id):
        conn = self.open_database()

        try:
            return [
                dict(row)
                for row in conn.execute("""
                    SELECT h.*
                    FROM staff_notice_delivery_history h
                    JOIN staff_notice_deliveries d
                        ON h.delivery_id = d.delivery_id
                    JOIN staff_notice_occurrences o
                        ON d.occurrence_id = o.occurrence_id
                    JOIN staff_notice_schedules s
                        ON o.schedule_id = s.schedule_id
                    WHERE s.notice_id = ?
                    ORDER BY h.delivery_history_id
                """, (notice_id,)).fetchall()
            ]
        finally:
            conn.close()

    def publication_activity_rows(self, notice_id=None):
        conn = self.open_database()

        try:
            sql = """
                SELECT *
                FROM activity_log
                WHERE activity_type = 'staff_notice_published'
            """
            parameters = ()
            if notice_id is not None:
                sql += " AND related_table = 'staff_notices' AND related_id = ?"
                parameters = (notice_id,)
            sql += " ORDER BY activity_id"
            return [
                dict(row)
                for row in conn.execute(sql, parameters).fetchall()
            ]
        finally:
            conn.close()

    def delivery_assignment_activity_rows(self, notice_id):
        conn = self.open_database()

        try:
            return [
                dict(row)
                for row in conn.execute("""
                    SELECT al.*
                    FROM activity_log al
                    JOIN staff_notice_deliveries d
                        ON al.related_table = 'staff_notice_deliveries'
                       AND al.related_id = d.delivery_id
                    JOIN staff_notice_occurrences o
                        ON d.occurrence_id = o.occurrence_id
                    JOIN staff_notice_schedules s
                        ON o.schedule_id = s.schedule_id
                    WHERE al.activity_type =
                        'staff_notice_delivery_assigned'
                      AND s.notice_id = ?
                    ORDER BY al.activity_id
                """, (notice_id,)).fetchall()
            ]
        finally:
            conn.close()

    def staff_notice_activity_rows(self, notice_id):
        conn = self.open_database()

        try:
            return [
                dict(row)
                for row in conn.execute("""
                    SELECT al.*
                    FROM activity_log al
                    WHERE al.activity_class = 'STAFF_NOTICE'
                      AND (
                          (
                              al.related_table = 'staff_notices'
                              AND al.related_id = ?
                          )
                          OR (
                              al.related_table =
                                  'staff_notice_audience_eligibility_periods'
                              AND al.related_id IN (
                                  SELECT ep.eligibility_period_id
                                  FROM
                                      staff_notice_audience_eligibility_periods
                                      ep
                                  JOIN staff_notice_audiences a
                                      ON ep.audience_id = a.audience_id
                                  WHERE a.notice_id = ?
                              )
                          )
                          OR (
                              al.related_table = 'staff_notice_occurrences'
                              AND al.related_id IN (
                                  SELECT o.occurrence_id
                                  FROM staff_notice_occurrences o
                                  JOIN staff_notice_schedules s
                                      ON o.schedule_id = s.schedule_id
                                  WHERE s.notice_id = ?
                              )
                          )
                          OR (
                              al.related_table = 'staff_notice_deliveries'
                              AND al.related_id IN (
                                  SELECT d.delivery_id
                                  FROM staff_notice_deliveries d
                                  JOIN staff_notice_occurrences o
                                      ON d.occurrence_id = o.occurrence_id
                                  JOIN staff_notice_schedules s
                                      ON o.schedule_id = s.schedule_id
                                  WHERE s.notice_id = ?
                              )
                          )
                      )
                    ORDER BY al.activity_id
                """, (
                    notice_id,
                    notice_id,
                    notice_id,
                    notice_id
                )).fetchall()
            ]
        finally:
            conn.close()

    def eligibility_started_activity_rows(self, notice_id):
        return [
            row
            for row in self.staff_notice_activity_rows(notice_id)
            if row["activity_type"]
            == "staff_notice_audience_eligibility_started"
        ]

    def occurrence_created_activity_rows(self, notice_id):
        return [
            row
            for row in self.staff_notice_activity_rows(notice_id)
            if row["activity_type"] == "staff_notice_occurrence_created"
        ]

    def update_notice_period(
        self,
        notice_id,
        *,
        effective_start="2026-07-31T08:00:00Z",
        expires_at="2026-08-31T06:59:59Z",
        until_withdrawn=0
    ):
        conn = self.open_database()

        try:
            conn.execute("""
                UPDATE staff_notices
                SET effective_start_at_utc = ?,
                    expires_at_utc = ?,
                    until_withdrawn = ?
                WHERE notice_id = ?
            """, (
                effective_start,
                expires_at,
                until_withdrawn,
                notice_id
            ))
            conn.commit()
        finally:
            conn.close()

    def configure_schedule(self, notice_id, **values):
        allowed_columns = {
            "occurrence_basis",
            "recurrence_pattern",
            "shift_applicability",
            "interval_days",
            "recurrence_anchor_date",
            "specific_calendar_date",
            "specific_shift_client_id",
            "specific_shift_date",
            "specific_shift_type",
            "one_time_due_at_utc"
        }
        self.assertTrue(values)
        self.assertTrue(set(values) <= allowed_columns)
        assignments = ", ".join(f"{column} = ?" for column in values)
        conn = self.open_database()

        try:
            conn.execute(
                f"""
                    UPDATE staff_notice_schedules
                    SET {assignments}
                    WHERE notice_id = ?
                """,
                (*values.values(), notice_id)
            )
            conn.commit()
        finally:
            conn.close()

    def add_schedule_weekdays(self, notice_id, *weekdays):
        conn = self.open_database()

        try:
            schedule_id = conn.execute("""
                SELECT schedule_id
                FROM staff_notice_schedules
                WHERE notice_id = ?
            """, (notice_id,)).fetchone()[0]
            conn.executemany("""
                INSERT INTO staff_notice_schedule_weekdays
                (schedule_id, weekday_number)
                VALUES (?, ?)
            """, ((schedule_id, weekday) for weekday in weekdays))
            conn.commit()
        finally:
            conn.close()

    def add_schedule_shift_types(self, notice_id, *shift_types):
        conn = self.open_database()

        try:
            schedule_id = conn.execute("""
                SELECT schedule_id
                FROM staff_notice_schedules
                WHERE notice_id = ?
            """, (notice_id,)).fetchone()[0]
            conn.executemany("""
                INSERT INTO staff_notice_schedule_shift_types
                (schedule_id, shift_type)
                VALUES (?, ?)
            """, ((schedule_id, value) for value in shift_types))
            conn.commit()
        finally:
            conn.close()

    def add_shift(
        self,
        shift_id,
        shift_date,
        shift_type,
        *,
        client_id=1,
        scheduled_start_time=None,
        scheduled_end_time=None
    ):
        conn = self.open_database()

        try:
            conn.execute("""
                INSERT INTO shifts
                (
                    shift_id,
                    client_id,
                    shift_date,
                    shift_type,
                    status,
                    scheduled_start_time,
                    scheduled_end_time
                )
                VALUES (?, ?, ?, ?, 'Open', ?, ?)
            """, (
                shift_id,
                client_id,
                shift_date,
                shift_type,
                scheduled_start_time,
                scheduled_end_time
            ))
            conn.commit()
        finally:
            conn.close()

    def add_shift_assignment(
        self,
        shift_id,
        user_id,
        *,
        active=1
    ):
        conn = self.open_database()

        try:
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, active)
                VALUES (?, ?, ?)
            """, (shift_id, user_id, active))
            conn.commit()
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

    def assert_no_later_publication_rows(self):
        conn = self.open_database()

        try:
            for table_name in LATER_PUBLICATION_TABLES:
                with self.subTest(table_name=table_name):
                    self.assertEqual(
                        conn.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0],
                        0
                    )
        finally:
            conn.close()

    def install_publication_update_trigger(self, action_sql):
        conn = self.open_database()

        try:
            conn.execute(f"""
                CREATE TRIGGER control_publication_update
                BEFORE UPDATE OF status ON staff_notices
                WHEN NEW.status = 'Published'
                BEGIN
                    {action_sql};
                END
            """)
            conn.commit()
        finally:
            conn.close()

    def make_notice_shift_scheduled(self, notice_id):
        conn = self.open_database()

        try:
            conn.execute("""
                UPDATE staff_notice_schedules
                SET occurrence_basis = 'Shift',
                    recurrence_pattern = 'Daily',
                    shift_applicability = 'Every Shift'
                WHERE notice_id = ?
            """, (notice_id,))
            conn.commit()
        finally:
            conn.close()

    def publication_client(self, user_id, session_role=None):
        client = app.app.test_client()

        with client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = session_role or "Admin"

        return client

    def publication_form(self, notice_id):
        notice = self.notice_row(notice_id)
        return {
            "expected_updated_at_utc": (
                notice["updated_at_utc"] or notice["created_at_utc"]
            )
        }

    def test_post_route_publishes_with_current_database_authorization(self):
        notice_id = self.create_notice()
        client = self.publication_client(2, session_role="Support Worker")

        response = client.post(
            f"/staff-notices/{notice_id}/publish",
            data=self.publication_form(notice_id)
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            "/staff-notices/manage?publication_result=published",
            response.headers["Location"]
        )
        notice = self.notice_row(notice_id)
        self.assertEqual(notice["status"], "Published")
        self.assertEqual(notice["published_by_user_id"], 2)
        self.assertEqual(len(self.eligibility_rows(notice_id)), 1)
        self.assertEqual(len(self.occurrence_rows(notice_id)), 1)
        deliveries = self.delivery_rows(notice_id)
        self.assertEqual(len(deliveries), 1)
        history_rows = self.delivery_history_rows(notice_id)
        self.assertEqual(len(history_rows), 1)
        self.assertEqual(
            history_rows[0]["delivery_id"],
            deliveries[0]["delivery_id"]
        )
        self.assertEqual(history_rows[0]["event_type"], "Assigned")
        self.assertIsNone(
            history_rows[0]["previous_requirement_status"]
        )
        self.assertEqual(
            history_rows[0]["new_requirement_status"],
            "Required"
        )
        self.assertIsNone(
            history_rows[0]["previous_recipient_access"]
        )
        self.assertEqual(
            history_rows[0]["new_recipient_access"],
            1
        )
        self.assertIsNone(history_rows[0]["reason_code"])
        self.assertIsNone(history_rows[0]["reason_text"])
        self.assertIsNone(history_rows[0]["changed_by_user_id"])
        self.assertEqual(
            history_rows[0]["changed_at_utc"],
            self.FIXED_TIMESTAMP
        )
        assignment_activities = self.delivery_assignment_activity_rows(
            notice_id
        )
        self.assertEqual(len(assignment_activities), 1)
        self.assertEqual(
            assignment_activities[0]["related_id"],
            deliveries[0]["delivery_id"]
        )
        self.assertEqual(
            [row["activity_type"] for row in self.staff_notice_activity_rows(
                notice_id
            )],
            [
                "staff_notice_audience_eligibility_started",
                "staff_notice_occurrence_created",
                "staff_notice_delivery_assigned",
                "staff_notice_published"
            ]
        )
        self.assert_no_later_publication_rows()

    def test_publication_writes_one_authoritative_activity_event(self):
        notice_id = self.create_notice(audience_rules=((
            "Core Organization",
            None,
            None
        ),))
        conn = self.open_database()

        try:
            conn.execute("""
                UPDATE staff_notices
                SET title = 'Authoritative Audit Title',
                    priority = 'Urgent',
                    client_id = 1
                WHERE notice_id = ?
            """, (notice_id,))
            conn.commit()
        finally:
            conn.close()

        app.publish_staff_notice(notice_id, 2)

        activities = self.publication_activity_rows(notice_id)
        self.assertEqual(len(activities), 1)
        activity = activities[0]
        self.assertIsNotNone(activity["activity_datetime"])
        self.assertEqual(activity["activity_class"], "STAFF_NOTICE")
        self.assertEqual(activity["activity_type"], "staff_notice_published")
        self.assertEqual(activity["user_id"], 2)
        self.assertEqual(activity["client_id"], 1)
        self.assertIsNone(activity["shift_id"])
        self.assertEqual(activity["related_table"], "staff_notices")
        self.assertEqual(activity["related_id"], notice_id)
        self.assertEqual(
            activity["summary"],
            "Staff Notice published: Authoritative Audit Title"
        )
        self.assertEqual(
            activity["details"],
            "Priority: Urgent; Eligibility periods: 3; "
            "Occurrences: 1; Deliveries: 3"
        )
        self.assertEqual(activity["success"], 1)
        eligibility_rows = self.eligibility_rows(notice_id)
        self.assertEqual(len(eligibility_rows), 3)
        occurrences = self.occurrence_rows(notice_id)
        self.assertEqual(len(occurrences), 1)
        deliveries = self.delivery_rows(notice_id)
        self.assertEqual(len(deliveries), 3)
        history_rows = self.delivery_history_rows(notice_id)
        self.assertEqual(len(history_rows), 3)
        self.assertEqual(
            [row["delivery_id"] for row in history_rows],
            [row["delivery_id"] for row in deliveries]
        )
        for history_row in history_rows:
            self.assertEqual(history_row["event_type"], "Assigned")
            self.assertIsNone(
                history_row["previous_requirement_status"]
            )
            self.assertEqual(
                history_row["new_requirement_status"],
                "Required"
            )
            self.assertIsNone(
                history_row["previous_recipient_access"]
            )
            self.assertEqual(
                history_row["new_recipient_access"],
                1
            )
            self.assertIsNone(history_row["reason_code"])
            self.assertIsNone(history_row["reason_text"])
            self.assertIsNone(history_row["changed_by_user_id"])
            self.assertEqual(
                history_row["changed_at_utc"],
                self.FIXED_TIMESTAMP
            )
        assignment_activities = self.delivery_assignment_activity_rows(
            notice_id
        )
        self.assertEqual(len(assignment_activities), 3)
        self.assertEqual(
            [row["related_id"] for row in assignment_activities],
            [row["delivery_id"] for row in deliveries]
        )
        all_activities = self.staff_notice_activity_rows(notice_id)
        self.assertEqual(len(all_activities), 8)
        self.assertEqual(
            [row["activity_type"] for row in all_activities],
            [
                "staff_notice_audience_eligibility_started",
                "staff_notice_audience_eligibility_started",
                "staff_notice_audience_eligibility_started",
                "staff_notice_occurrence_created",
                "staff_notice_delivery_assigned",
                "staff_notice_delivery_assigned",
                "staff_notice_delivery_assigned",
                "staff_notice_published"
            ]
        )
        eligibility_activities = self.eligibility_started_activity_rows(
            notice_id
        )
        for eligibility_activity, eligibility_row in zip(
            eligibility_activities,
            eligibility_rows
        ):
            self.assertIsNotNone(
                eligibility_activity["activity_datetime"]
            )
            self.assertEqual(
                eligibility_activity["activity_class"],
                "STAFF_NOTICE"
            )
            self.assertIsNone(eligibility_activity["user_id"])
            self.assertEqual(eligibility_activity["client_id"], 1)
            self.assertIsNone(eligibility_activity["shift_id"])
            self.assertEqual(
                eligibility_activity["related_table"],
                "staff_notice_audience_eligibility_periods"
            )
            self.assertEqual(
                eligibility_activity["related_id"],
                eligibility_row["eligibility_period_id"]
            )
            self.assertEqual(
                eligibility_activity["summary"],
                "Staff Notice audience eligibility started: "
                "Authoritative Audit Title"
            )
            self.assertEqual(
                eligibility_activity["details"],
                f"Notice ID: {notice_id}; Recipient user ID: "
                f"{eligibility_row['user_id']}; Sources: "
                "Core Organization"
            )
            self.assertEqual(eligibility_activity["success"], 1)
        occurrence_activity = self.occurrence_created_activity_rows(
            notice_id
        )[0]
        self.assertIsNotNone(occurrence_activity["activity_datetime"])
        self.assertEqual(occurrence_activity["activity_class"], "STAFF_NOTICE")
        self.assertIsNone(occurrence_activity["user_id"])
        self.assertEqual(occurrence_activity["client_id"], 1)
        self.assertIsNone(occurrence_activity["shift_id"])
        self.assertEqual(
            occurrence_activity["related_table"],
            "staff_notice_occurrences"
        )
        self.assertEqual(
            occurrence_activity["related_id"],
            occurrences[0]["occurrence_id"]
        )
        self.assertEqual(
            occurrence_activity["summary"],
            "Staff Notice occurrence created: Authoritative Audit Title"
        )
        self.assertEqual(
            occurrence_activity["details"],
            f"Notice ID: {notice_id}; Kind: One Time; Date: None; "
            "Visible from: 2026-08-01T08:00:00Z; Due at: None"
        )
        self.assertEqual(occurrence_activity["success"], 1)
        for assignment_activity, delivery in zip(
            assignment_activities,
            deliveries
        ):
            self.assertIsNotNone(
                assignment_activity["activity_datetime"]
            )
            self.assertEqual(
                assignment_activity["activity_class"],
                "STAFF_NOTICE"
            )
            self.assertIsNone(assignment_activity["user_id"])
            self.assertEqual(assignment_activity["client_id"], 1)
            self.assertIsNone(assignment_activity["shift_id"])
            self.assertEqual(
                assignment_activity["related_table"],
                "staff_notice_deliveries"
            )
            self.assertEqual(
                assignment_activity["summary"],
                "Staff Notice delivery assigned: "
                "Authoritative Audit Title"
            )
            self.assertEqual(
                assignment_activity["details"],
                f"Notice ID: {notice_id}; Occurrence ID: "
                f"{delivery['occurrence_id']}; Recipient user ID: "
                f"{delivery['user_id']}; Eligibility cutoff: "
                "2026-08-01T08:00:00Z"
            )
            self.assertEqual(assignment_activity["success"], 1)

    def test_all_active_management_roles_can_publish_through_route(self):
        conn = self.open_database()

        try:
            conn.execute("""
                INSERT INTO users (user_id, full_name, role, active)
                VALUES (5, 'Active Director', 'Director', 1)
            """)
            conn.commit()
        finally:
            conn.close()

        for user_id in (1, 2, 5):
            with self.subTest(user_id=user_id):
                notice_id = self.create_notice()
                response = self.publication_client(user_id).post(
                    f"/staff-notices/{notice_id}/publish",
                    data=self.publication_form(notice_id)
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    self.notice_row(notice_id)["published_by_user_id"],
                    user_id
                )

    def test_route_rejects_inactive_and_unauthorized_database_users(self):
        for user_id in (3, 4):
            with self.subTest(user_id=user_id):
                notice_id = self.create_notice()
                response = self.publication_client(
                    user_id,
                    session_role="Admin"
                ).post(
                    f"/staff-notices/{notice_id}/publish",
                    data=self.publication_form(notice_id)
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    self.notice_row(notice_id)["status"],
                    "Draft"
                )
                self.assertEqual(self.eligibility_rows(notice_id), [])
                self.assertEqual(self.occurrence_rows(notice_id), [])
                self.assertEqual(self.delivery_rows(notice_id), [])
                self.assertEqual(
                    self.publication_activity_rows(notice_id),
                    []
                )

    def test_publication_route_rejects_get_and_requires_login(self):
        notice_id = self.create_notice()
        self.assertEqual(
            app.app.test_client().get(
                f"/staff-notices/{notice_id}/publish"
            ).status_code,
            405
        )
        response = app.app.test_client().post(
            f"/staff-notices/{notice_id}/publish",
            data=self.publication_form(notice_id)
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))
        self.assertEqual(self.notice_row(notice_id)["status"], "Draft")

    def test_route_rejects_missing_invalid_and_tampered_form_values(self):
        client = self.publication_client(1)

        for data in (
            {},
            {"expected_updated_at_utc": ""},
            {
                "expected_updated_at_utc": "2026-07-30T19:00:00Z",
                "title": "Browser replacement"
            },
            MultiDict([
                ("expected_updated_at_utc", "2026-07-30T19:00:00Z"),
                ("expected_updated_at_utc", "2026-07-30T19:00:00Z")
            ])
        ):
            with self.subTest(data=data):
                notice_id = self.create_notice()
                response = client.post(
                    f"/staff-notices/{notice_id}/publish",
                    data=data
                )
                self.assertEqual(response.status_code, 302)
                self.assertIn("publication_result=invalid_form", response.headers[
                    "Location"
                ])
                notice = self.notice_row(notice_id)
                self.assertEqual(notice["title"], "Publication Foundation")
                self.assertEqual(notice["status"], "Draft")
                self.assertEqual(self.eligibility_rows(notice_id), [])
                self.assertEqual(self.occurrence_rows(notice_id), [])
                self.assertEqual(self.delivery_rows(notice_id), [])
                self.assertEqual(
                    self.publication_activity_rows(notice_id),
                    []
                )

    def test_route_rejects_missing_notice_and_readiness_blockers(self):
        client = self.publication_client(1)
        missing = client.post(
            "/staff-notices/999/publish",
            data={"expected_updated_at_utc": "2026-07-30T19:00:00Z"}
        )
        self.assertIn("publication_result=not_found", missing.headers["Location"])

        notice_id = self.create_notice(audience=False)
        blocked = client.post(
            f"/staff-notices/{notice_id}/publish",
            data=self.publication_form(notice_id)
        )
        self.assertEqual(blocked.status_code, 302)
        self.assertIn("publication_result=blocked", blocked.headers["Location"])
        self.assertEqual(self.notice_row(notice_id)["status"], "Draft")
        self.assertEqual(self.eligibility_rows(notice_id), [])
        self.assertEqual(self.occurrence_rows(notice_id), [])
        self.assertEqual(self.delivery_rows(notice_id), [])
        self.assertEqual(self.publication_activity_rows(notice_id), [])

    def test_stale_and_repeated_route_submissions_cannot_republish(self):
        stale_notice_id = self.create_notice()
        stale_form = self.publication_form(stale_notice_id)
        conn = self.open_database()

        try:
            conn.execute("""
                UPDATE staff_notices
                SET updated_at_utc = '2026-07-30T20:00:00Z',
                    updated_by_user_id = 1
                WHERE notice_id = ?
            """, (stale_notice_id,))
            conn.commit()
        finally:
            conn.close()

        client = self.publication_client(1)
        stale = client.post(
            f"/staff-notices/{stale_notice_id}/publish",
            data=stale_form
        )
        self.assertIn("publication_result=conflict", stale.headers["Location"])
        self.assertEqual(self.notice_row(stale_notice_id)["status"], "Draft")
        self.assertEqual(self.eligibility_rows(stale_notice_id), [])
        self.assertEqual(
            self.publication_activity_rows(stale_notice_id),
            []
        )

        notice_id = self.create_notice()
        form = self.publication_form(notice_id)
        first = client.post(f"/staff-notices/{notice_id}/publish", data=form)
        second = client.post(f"/staff-notices/{notice_id}/publish", data=form)
        self.assertIn("publication_result=published", first.headers["Location"])
        self.assertIn("publication_result=conflict", second.headers["Location"])
        self.assertEqual(len(self.eligibility_rows(notice_id)), 1)
        self.assertEqual(len(self.occurrence_rows(notice_id)), 1)
        self.assertEqual(len(self.delivery_rows(notice_id)), 1)
        self.assertEqual(len(self.publication_activity_rows(notice_id)), 1)

    def test_route_returns_safe_feedback_for_unexpected_service_failure(self):
        notice_id = self.create_notice()
        client = self.publication_client(1)

        with mock.patch.object(
            app,
            "publish_staff_notice",
            side_effect=RuntimeError("sensitive database detail")
        ):
            response = client.post(
                f"/staff-notices/{notice_id}/publish",
                data=self.publication_form(notice_id),
                follow_redirects=True
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"could not be published", response.data)
        self.assertNotIn(b"sensitive database detail", response.data)
        self.assertEqual(self.notice_row(notice_id)["status"], "Draft")
        self.assertEqual(self.eligibility_rows(notice_id), [])
        self.assertEqual(self.occurrence_rows(notice_id), [])
        self.assertEqual(self.delivery_rows(notice_id), [])
        self.assertEqual(self.publication_activity_rows(notice_id), [])

    def test_preview_publish_control_is_read_only_and_visibility_gated(self):
        client = self.publication_client(1)
        ready_notice_id = self.create_notice()
        ready = client.get(f"/staff-notices/{ready_notice_id}/review")
        ready_html = ready.data.decode("utf-8")

        self.assertIn("This is a read-only preview", ready_html)
        self.assertIn(
            f'action="/staff-notices/{ready_notice_id}/publish"',
            ready_html
        )
        self.assertIn("Publish Staff Notice", ready_html)
        self.assertEqual(ready_html.count("<form"), 1)
        self.assertEqual(ready_html.count('name="expected_updated_at_utc"'), 1)
        self.assertNotIn('name="title"', ready_html)
        self.assertNotIn('name="notice_text"', ready_html)

        blocked_notice_id = self.create_notice(audience=False)
        blocked_html = client.get(
            f"/staff-notices/{blocked_notice_id}/review"
        ).data.decode("utf-8")
        self.assertNotIn("Publish Staff Notice", blocked_html)
        self.assertNotIn(
            f'action="/staff-notices/{blocked_notice_id}/publish"',
            blocked_html
        )

    def test_successful_publication_creates_initial_eligibility(self):
        notice_id = self.create_notice()
        result = app.publish_staff_notice(notice_id, 2)
        notice = self.notice_row(notice_id)

        self.assertEqual(result, {
            "notice_id": notice_id,
            "published_by_user_id": 2,
            "published_at_utc": self.FIXED_TIMESTAMP
        })
        self.assertEqual(notice["status"], "Published")
        self.assertEqual(notice["draft_active"], 0)
        self.assertEqual(notice["published_by_user_id"], 2)
        self.assertEqual(
            notice["published_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertIsNone(notice["updated_by_user_id"])
        self.assertIsNone(notice["updated_at_utc"])
        rows = self.eligibility_rows(notice_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], 4)
        self.assertEqual(
            rows[0]["eligible_from_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertIsNone(rows[0]["eligible_until_at_utc"])
        self.assertEqual(
            rows[0]["eligibility_source_summary"],
            "All Support Workers"
        )
        self.assertEqual(rows[0]["opened_by_user_id"], 2)
        self.assertIsNone(rows[0]["closed_by_user_id"])
        self.assertIsNone(rows[0]["close_reason"])
        self.assertEqual(rows[0]["created_at_utc"], self.FIXED_TIMESTAMP)
        self.assertIsNone(rows[0]["updated_at_utc"])
        occurrences = self.occurrence_rows(notice_id)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["occurrence_kind"], "One Time")
        self.assertIsNone(occurrences[0]["occurrence_date"])
        self.assertEqual(
            occurrences[0]["visible_from_at_utc"],
            "2026-08-01T08:00:00Z"
        )
        self.assertIsNone(occurrences[0]["due_at_utc"])
        self.assertEqual(occurrences[0]["occurrence_status"], "Scheduled")
        self.assertEqual(
            occurrences[0]["created_at_utc"],
            self.FIXED_TIMESTAMP
        )
        deliveries = self.delivery_rows(notice_id)
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["user_id"], 4)
        self.assertEqual(deliveries[0]["occurrence_id"], occurrences[0][
            "occurrence_id"
        ])
        self.assertEqual(deliveries[0]["requirement_status"], "Required")
        self.assertEqual(
            deliveries[0]["assigned_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertEqual(
            deliveries[0]["eligibility_cutoff_at_utc"],
            "2026-08-01T08:00:00Z"
        )
        self.assertIsNone(deliveries[0]["first_viewed_at_utc"])
        self.assertIsNone(deliveries[0]["viewed_by_user_id"])
        self.assertEqual(deliveries[0]["recipient_access"], 1)
        self.assertIsNone(deliveries[0]["status_changed_at_utc"])
        self.assertIsNone(deliveries[0]["status_changed_by_user_id"])
        self.assertIsNone(deliveries[0]["current_reason_code"])
        self.assertIsNone(deliveries[0]["current_reason_text"])
        self.assertIsNone(deliveries[0]["access_revoked_at_utc"])
        history_rows = self.delivery_history_rows(notice_id)
        self.assertEqual(len(history_rows), 1)
        self.assertEqual(
            history_rows[0]["delivery_id"],
            deliveries[0]["delivery_id"]
        )
        self.assertEqual(history_rows[0]["event_type"], "Assigned")
        self.assertIsNone(
            history_rows[0]["previous_requirement_status"]
        )
        self.assertEqual(
            history_rows[0]["new_requirement_status"],
            "Required"
        )
        self.assertIsNone(
            history_rows[0]["previous_recipient_access"]
        )
        self.assertEqual(
            history_rows[0]["new_recipient_access"],
            1
        )
        self.assertIsNone(history_rows[0]["reason_code"])
        self.assertIsNone(history_rows[0]["reason_text"])
        self.assertIsNone(history_rows[0]["changed_by_user_id"])
        self.assertEqual(
            history_rows[0]["changed_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assert_no_later_publication_rows()

    def test_public_service_owns_one_connection_and_transaction(self):
        notice_id = self.create_notice()
        connection = self.tracking_connection()

        with mock.patch.object(
            app,
            "get_db",
            return_value=connection
        ) as get_db_mock:
            app.publish_staff_notice(notice_id, 1)

        get_db_mock.assert_called_once_with()
        self.assertEqual(connection.begin_calls, 1)
        self.assertEqual(connection.eligibility_insert_calls, 1)
        self.assertEqual(connection.occurrence_insert_calls, 1)
        self.assertEqual(connection.delivery_insert_calls, 1)
        self.assertEqual(connection.delivery_history_insert_calls, 1)
        self.assertEqual(connection.activity_insert_calls, 4)
        self.assertEqual(connection.update_calls, 1)
        self.assertEqual(connection.commit_calls, 1)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 1)

    def test_audience_union_deduplicates_worker_and_sources(self):
        notice_id = self.create_notice(audience_rules=(
            ("Core Organization", None, None),
            ("All Support Workers", None, None),
            ("Selected Role", "Support Worker", None),
            ("Selected Individual", None, 4)
        ))

        app.publish_staff_notice(notice_id, 1)

        rows = self.eligibility_rows(notice_id)
        self.assertEqual([row["user_id"] for row in rows], [1, 2, 4])
        worker = next(row for row in rows if row["user_id"] == 4)
        self.assertEqual(
            worker["eligibility_source_summary"],
            "Core Organization, All Support Workers, "
            "Selected Role: Support Worker, Selected Individual"
        )
        self.assertEqual(
            len(worker["eligibility_source_summary"].split(", ")),
            4
        )
        deliveries = self.delivery_rows(notice_id)
        self.assertEqual([row["user_id"] for row in deliveries], [1, 2, 4])
        self.assertEqual(len({row["user_id"] for row in deliveries}), 3)

    def test_inactive_users_are_excluded_from_initial_eligibility(self):
        notice_id = self.create_notice(audience_rules=(
            ("Core Organization", None, None),
        ))

        app.publish_staff_notice(notice_id, 1)

        self.assertEqual(
            [row["user_id"] for row in self.eligibility_rows(notice_id)],
            [1, 2, 4]
        )
        self.assertNotIn(
            3,
            [row["user_id"] for row in self.eligibility_rows(notice_id)]
        )
        self.assertEqual(
            [row["user_id"] for row in self.delivery_rows(notice_id)],
            [1, 2, 4]
        )

    def test_applicable_shift_staff_alone_creates_no_eligibility(self):
        notice_id = self.create_notice(audience_rules=(
            ("Applicable Shift Staff", None, None),
        ))
        self.make_notice_shift_scheduled(notice_id)
        conn = self.open_database()

        try:
            conn.execute("""
                INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status)
                VALUES (1, 1, '2026-08-01', 'Day', 'Open')
            """)
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, active)
                VALUES (1, 4, 1)
            """)
            conn.commit()
        finally:
            conn.close()

        app.publish_staff_notice(notice_id, 1)

        self.assertEqual(self.eligibility_rows(notice_id), [])
        deliveries = self.delivery_rows(notice_id)
        self.assertEqual([row["user_id"] for row in deliveries], [4])
        self.assertEqual(deliveries[0]["shift_id"], 1)
        self.assert_no_later_publication_rows()

    def test_non_shift_candidate_needs_no_matching_shift_assignment(self):
        notice_id = self.create_notice(audience_rules=(
            ("All Support Workers", None, None),
            ("Applicable Shift Staff", None, None)
        ))
        self.make_notice_shift_scheduled(notice_id)

        app.publish_staff_notice(notice_id, 1)

        rows = self.eligibility_rows(notice_id)
        self.assertEqual([row["user_id"] for row in rows], [4])
        self.assertEqual(
            rows[0]["eligibility_source_summary"],
            "All Support Workers"
        )
        self.assert_no_later_publication_rows()

    def test_internal_publisher_requires_and_reuses_active_transaction(self):
        notice_id = self.create_notice()
        connection = self.tracking_connection()
        self.addCleanup(connection.close)

        with self.assertRaisesRegex(RuntimeError, "active transaction"):
            app._publish_staff_notice_in_transaction(
                connection,
                notice_id,
                1,
                self.FIXED_NOW
            )

        connection.execute("BEGIN IMMEDIATE")
        connection.begin_calls = 0
        result = app._publish_staff_notice_in_transaction(
            connection,
            notice_id,
            1,
            self.FIXED_NOW
        )

        self.assertTrue(connection.in_transaction)
        self.assertEqual(connection.begin_calls, 0)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 0)
        self.assertIn("_publication_preview", result)
        self.assertIn(
            "_publication_audience_candidates",
            result["_publication_preview"]
        )
        connection.rollback()
        self.assertEqual(
            self.notice_row(notice_id)["status"],
            "Draft"
        )

    def test_current_database_role_controls_authorization(self):
        notice_id = self.create_notice()
        conn = self.open_database()

        try:
            conn.execute("""
                UPDATE users
                SET role = 'Support Worker'
                WHERE user_id = 1
            """)
            conn.commit()
        finally:
            conn.close()

        before = self.database_snapshot()

        with self.assertRaises(PermissionError):
            app.publish_staff_notice(notice_id, 1)

        self.assertEqual(self.database_snapshot(), before)

    def test_inactive_actor_is_rejected(self):
        notice_id = self.create_notice()
        before = self.database_snapshot()

        with self.assertRaises(PermissionError):
            app.publish_staff_notice(notice_id, 3)

        self.assertEqual(self.database_snapshot(), before)

    def test_readiness_blocker_rolls_back_without_partial_state(self):
        notice_id = self.create_notice(schedule=False)
        before = self.database_snapshot()

        with self.assertRaises(
            app.StaffNoticePublicationNotReadyError
        ) as raised:
            app.publish_staff_notice(notice_id, 1)

        self.assertIn(
            "Exactly one schedule is required before publication.",
            raised.exception.blocking_errors
        )
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_later_publication_rows()

    def test_duplicate_publication_is_rejected(self):
        notice_id = self.create_notice()
        app.publish_staff_notice(notice_id, 1)
        published = self.database_snapshot()

        with self.assertRaises(app.StaffNoticeNotEditableError):
            app.publish_staff_notice(notice_id, 2)

        self.assertEqual(self.database_snapshot(), published)
        notice = self.notice_row(notice_id)
        self.assertEqual(notice["published_by_user_id"], 1)
        self.assertEqual(
            notice["published_at_utc"],
            self.FIXED_TIMESTAMP
        )

    def test_overlapping_publication_attempt_cannot_duplicate_periods(self):
        notice_id = self.create_notice()
        first_connection = self.tracking_connection()
        self.addCleanup(first_connection.close)
        first_connection.execute("BEGIN IMMEDIATE")
        app._publish_staff_notice_in_transaction(
            first_connection,
            notice_id,
            1,
            self.FIXED_NOW
        )

        second_raw_connection = sqlite3.connect(
            self.database_path,
            timeout=0
        )
        second_raw_connection.execute("PRAGMA foreign_keys = ON")
        second_raw_connection.row_factory = sqlite3.Row
        second_connection = PublicationTrackingConnection(
            second_raw_connection
        )

        with mock.patch.object(
            app,
            "get_db",
            return_value=second_connection
        ):
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "locked"
            ):
                app.publish_staff_notice(notice_id, 2)

        self.assertEqual(second_connection.begin_calls, 1)
        self.assertEqual(second_connection.eligibility_insert_calls, 0)
        self.assertEqual(second_connection.occurrence_insert_calls, 0)
        self.assertEqual(second_connection.delivery_insert_calls, 0)
        self.assertEqual(second_connection.commit_calls, 0)
        self.assertEqual(second_connection.close_calls, 1)

        first_connection.commit()
        rows = self.eligibility_rows(notice_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], 4)

        with self.assertRaises(app.StaffNoticeNotEditableError):
            app.publish_staff_notice(notice_id, 2)

        self.assertEqual(self.eligibility_rows(notice_id), rows)

    def test_inactive_draft_is_rejected(self):
        notice_id = self.create_notice()
        conn = self.open_database()

        try:
            conn.execute("""
                UPDATE staff_notices
                SET draft_active = 0
                WHERE notice_id = ?
            """, (notice_id,))
            conn.commit()
        finally:
            conn.close()

        before = self.database_snapshot()

        with self.assertRaises(app.StaffNoticeNotEditableError):
            app.publish_staff_notice(notice_id, 1)

        self.assertEqual(self.database_snapshot(), before)

    def test_calculation_failure_rolls_back_and_closes(self):
        notice_id = self.create_notice()
        before = self.database_snapshot()
        calculation_error = RuntimeError("controlled calculation failure")
        connection = self.tracking_connection()

        with mock.patch.object(
            app,
            "get_db",
            return_value=connection
        ), mock.patch.object(
            app,
            "_build_staff_notice_publish_preview",
            side_effect=calculation_error
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.publish_staff_notice(notice_id, 1)

        self.assertIs(raised.exception, calculation_error)
        self.assertEqual(connection.begin_calls, 1)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)

    def test_update_failure_rolls_back_without_partial_state(self):
        notice_id = self.create_notice()
        self.install_publication_update_trigger(
            "SELECT RAISE(ABORT, 'controlled update failure')"
        )
        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled update failure"
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.update_calls, 1)
        self.assertEqual(connection.eligibility_insert_calls, 1)
        self.assertEqual(connection.occurrence_insert_calls, 1)
        self.assertEqual(connection.delivery_insert_calls, 1)
        self.assertEqual(connection.delivery_history_insert_calls, 1)
        self.assertEqual(connection.activity_insert_calls, 3)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_later_publication_rows()

    def test_initial_eligibility_activity_failure_rolls_back_publication(self):
        notice_id = self.create_notice()
        conn = self.open_database()

        try:
            conn.execute("""
                CREATE TRIGGER control_initial_eligibility_activity
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type =
                    'staff_notice_audience_eligibility_started'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled initial eligibility activity failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()

        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled initial eligibility activity failure"
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.eligibility_insert_calls, 1)
        self.assertEqual(connection.occurrence_insert_calls, 0)
        self.assertEqual(connection.delivery_insert_calls, 0)
        self.assertEqual(connection.activity_insert_calls, 1)
        self.assertEqual(connection.update_calls, 0)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.eligibility_rows(notice_id), [])
        self.assertEqual(self.occurrence_rows(notice_id), [])
        self.assertEqual(self.delivery_rows(notice_id), [])
        self.assertEqual(self.staff_notice_activity_rows(notice_id), [])

    def test_initial_occurrence_activity_failure_rolls_back_publication(self):
        notice_id = self.create_notice()
        conn = self.open_database()

        try:
            conn.execute("""
                CREATE TRIGGER control_initial_occurrence_activity
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type = 'staff_notice_occurrence_created'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled initial occurrence activity failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()

        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled initial occurrence activity failure"
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.eligibility_insert_calls, 1)
        self.assertEqual(connection.occurrence_insert_calls, 1)
        self.assertEqual(connection.delivery_insert_calls, 0)
        self.assertEqual(connection.activity_insert_calls, 2)
        self.assertEqual(connection.update_calls, 0)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.eligibility_rows(notice_id), [])
        self.assertEqual(self.occurrence_rows(notice_id), [])
        self.assertEqual(self.delivery_rows(notice_id), [])
        self.assertEqual(self.staff_notice_activity_rows(notice_id), [])

    def test_activity_log_failure_rolls_back_complete_publication(self):
        notice_id = self.create_notice()
        conn = self.open_database()

        try:
            conn.execute("""
                CREATE TRIGGER control_publication_activity_insert
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type = 'staff_notice_published'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled publication activity failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()

        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(
            app,
            "get_db",
            return_value=connection
        ):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled publication activity failure"
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.eligibility_insert_calls, 1)
        self.assertEqual(connection.occurrence_insert_calls, 1)
        self.assertEqual(connection.delivery_insert_calls, 1)
        self.assertEqual(connection.delivery_history_insert_calls, 1)
        self.assertEqual(connection.activity_insert_calls, 4)
        self.assertEqual(connection.update_calls, 1)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.notice_row(notice_id)["status"], "Draft")
        self.assertEqual(self.eligibility_rows(notice_id), [])
        self.assertEqual(self.occurrence_rows(notice_id), [])
        self.assertEqual(self.delivery_rows(notice_id), [])
        self.assertEqual(self.publication_activity_rows(notice_id), [])
        self.assertEqual(self.delivery_history_rows(notice_id), [])

    def test_eligibility_insert_failure_rolls_back_everything(self):
        notice_id = self.create_notice(audience_rules=(
            ("Core Organization", None, None),
        ))
        conn = self.open_database()

        try:
            conn.execute("""
                CREATE TRIGGER control_eligibility_insert
                BEFORE INSERT ON staff_notice_audience_eligibility_periods
                WHEN NEW.user_id = 2
                BEGIN
                    SELECT RAISE(ABORT, 'controlled eligibility failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()

        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled eligibility failure"
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.eligibility_insert_calls, 2)
        self.assertEqual(connection.occurrence_insert_calls, 0)
        self.assertEqual(connection.delivery_insert_calls, 0)
        self.assertEqual(connection.activity_insert_calls, 1)
        self.assertEqual(connection.update_calls, 0)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.notice_row(notice_id)["status"], "Draft")
        self.assertEqual(self.eligibility_rows(notice_id), [])

    def test_guarded_update_rejects_stale_write(self):
        notice_id = self.create_notice()
        self.install_publication_update_trigger("SELECT RAISE(IGNORE)")
        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaises(
                app.StaffNoticeStalePublicationError
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.update_calls, 1)
        self.assertEqual(connection.eligibility_insert_calls, 1)
        self.assertEqual(connection.occurrence_insert_calls, 1)
        self.assertEqual(connection.delivery_insert_calls, 1)
        self.assertEqual(connection.activity_insert_calls, 3)
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(self.eligibility_rows(notice_id), [])

    def test_commit_failure_rolls_back_publication(self):
        notice_id = self.create_notice()
        before = self.database_snapshot()
        commit_error = sqlite3.OperationalError(
            "controlled commit failure"
        )
        connection = self.tracking_connection(
            commit_error=commit_error
        )

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaises(sqlite3.OperationalError) as raised:
                app.publish_staff_notice(notice_id, 1)

        self.assertIs(raised.exception, commit_error)
        self.assertEqual(connection.activity_insert_calls, 4)
        self.assertEqual(connection.commit_calls, 1)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)

    def test_cleanup_errors_preserve_primary_publication_failure(self):
        notice_id = self.create_notice()
        primary_error = RuntimeError("controlled primary failure")
        rollback_error = RuntimeError("controlled rollback failure")
        close_error = RuntimeError("controlled close failure")
        connection = self.tracking_connection(
            rollback_error=rollback_error,
            close_error=close_error
        )

        with mock.patch.object(
            app,
            "get_db",
            return_value=connection
        ), mock.patch.object(
            app,
            "_build_staff_notice_publish_preview",
            side_effect=primary_error
        ):
            with self.assertRaises(RuntimeError) as raised:
                app.publish_staff_notice(notice_id, 1)

        self.assertIs(raised.exception, primary_error)
        self.assertIs(
            primary_error.staff_notice_rollback_error,
            rollback_error
        )
        self.assertIs(
            primary_error.staff_notice_close_error,
            close_error
        )
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(
            self.notice_row(notice_id)["status"],
            "Draft"
        )

    def test_committed_close_failure_is_not_retry_safe(self):
        notice_id = self.create_notice()
        close_error = RuntimeError("controlled close failure")
        connection = self.tracking_connection(close_error=close_error)

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaises(
                app.StaffNoticePublicationCommittedCloseError
            ) as raised:
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(raised.exception.notice_id, notice_id)
        self.assertTrue(raised.exception.committed)
        self.assertFalse(raised.exception.retry_safe)
        self.assertIs(raised.exception.__cause__, close_error)
        self.assertEqual(
            self.notice_row(notice_id)["status"],
            "Published"
        )

    def test_caller_values_cannot_override_calculation_or_timestamp(self):
        notice_id = self.create_notice()

        with mock.patch.object(
            app,
            "get_db",
            side_effect=AssertionError("unexpected database access")
        ):
            with self.assertRaises(TypeError):
                app.publish_staff_notice(
                    notice_id,
                    1,
                    now_utc="2030-01-01T00:00:00Z"
                )
            with self.assertRaises(TypeError):
                app.publish_staff_notice(
                    notice_id,
                    1,
                    ready_for_publication=True,
                    recipient_count=999,
                    recipients=[1, 2, 3],
                    status="Draft",
                    role="Admin"
                )

        result = app.publish_staff_notice(notice_id, 1)
        self.assertEqual(
            result["published_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertNotIn("_publication_preview", result)

    def test_one_time_finite_occurrence_uses_expiry_as_due_time(self):
        notice_id = self.create_notice()
        self.update_notice_period(
            notice_id,
            expires_at="2026-08-05T07:00:00Z",
            until_withdrawn=0
        )

        app.publish_staff_notice(notice_id, 1)

        occurrence = self.occurrence_rows(notice_id)[0]
        self.assertEqual(occurrence["due_at_utc"], "2026-08-05T07:00:00Z")
        self.assertEqual(occurrence["due_at_is_provisional"], 0)
        delivery = self.delivery_rows(notice_id)[0]
        self.assertEqual(delivery["due_at_utc"], "2026-08-05T07:00:00Z")

    def test_calendar_recurrence_modes_create_only_applicable_publish_date(self):
        configurations = (
            ({
                "recurrence_pattern": "Once",
                "specific_calendar_date": "2026-07-31"
            }, ()),
            ({"recurrence_pattern": "Daily"}, ()),
            ({
                "recurrence_pattern": "Interval Days",
                "interval_days": 2,
                "recurrence_anchor_date": "2026-07-29"
            }, ()),
            ({"recurrence_pattern": "Selected Weekdays"}, (4,))
        )

        for values, weekdays in configurations:
            with self.subTest(pattern=values["recurrence_pattern"]):
                notice_id = self.create_notice()
                self.update_notice_period(notice_id)
                self.configure_schedule(
                    notice_id,
                    occurrence_basis="Calendar",
                    shift_applicability="None",
                    **values
                )
                if weekdays:
                    self.add_schedule_weekdays(notice_id, *weekdays)

                app.publish_staff_notice(notice_id, 1)

                occurrences = self.occurrence_rows(notice_id)
                self.assertEqual(len(occurrences), 1)
                occurrence = occurrences[0]
                self.assertEqual(occurrence["occurrence_kind"], "Calendar")
                self.assertEqual(occurrence["occurrence_date"], "2026-07-31")
                self.assertEqual(
                    occurrence["visible_from_at_utc"],
                    self.FIXED_TIMESTAMP
                )
                self.assertEqual(
                    occurrence["due_at_utc"],
                    "2026-08-01T06:59:59Z"
                )
                self.assertEqual(occurrence["occurrence_status"], "Active")
                delivery = self.delivery_rows(notice_id)[0]
                self.assertEqual(delivery["user_id"], 4)
                self.assertEqual(
                    delivery["eligibility_cutoff_at_utc"],
                    self.FIXED_TIMESTAMP
                )
                self.assertEqual(
                    delivery["due_at_utc"],
                    "2026-08-01T06:59:59Z"
                )

    def test_calendar_boundaries_are_inclusive_without_future_precreation(self):
        today_notice_id = self.create_notice()
        self.update_notice_period(
            today_notice_id,
            effective_start="2026-07-31T19:00:00Z",
            expires_at="2026-08-01T06:59:59Z"
        )
        self.configure_schedule(
            today_notice_id,
            occurrence_basis="Calendar",
            recurrence_pattern="Daily",
            shift_applicability="None"
        )
        future_notice_id = self.create_notice()
        self.update_notice_period(
            future_notice_id,
            effective_start="2026-08-01T07:00:00Z",
            expires_at="2026-08-03T06:59:59Z"
        )
        self.configure_schedule(
            future_notice_id,
            occurrence_basis="Calendar",
            recurrence_pattern="Daily",
            shift_applicability="None"
        )

        app.publish_staff_notice(today_notice_id, 1)
        app.publish_staff_notice(future_notice_id, 1)

        self.assertEqual(
            [row["occurrence_date"] for row in self.occurrence_rows(
                today_notice_id
            )],
            ["2026-07-31"]
        )
        self.assertEqual(self.occurrence_rows(future_notice_id), [])

    def test_calendar_day_end_uses_vancouver_daylight_saving_boundary(self):
        spring_now = datetime(2026, 3, 8, 18, 0, tzinfo=timezone.utc)
        app.get_application_now_utc = lambda: spring_now
        notice_id = self.create_notice()
        self.update_notice_period(
            notice_id,
            effective_start="2026-03-08T08:00:00Z",
            expires_at="2026-03-09T06:59:59Z"
        )
        self.configure_schedule(
            notice_id,
            occurrence_basis="Calendar",
            recurrence_pattern="Daily",
            shift_applicability="None"
        )

        app.publish_staff_notice(notice_id, 1)

        occurrence = self.occurrence_rows(notice_id)[0]
        self.assertEqual(occurrence["occurrence_date"], "2026-03-08")
        self.assertEqual(occurrence["due_at_utc"], "2026-03-09T06:59:59Z")

    def test_every_shift_creates_separate_bound_occurrences(self):
        notice_id = self.create_notice()
        self.make_notice_shift_scheduled(notice_id)
        self.add_shift(10, "2026-08-01", "Day")
        self.add_shift(11, "2026-08-01", "Day")

        app.publish_staff_notice(notice_id, 1)

        occurrences = self.occurrence_rows(notice_id)
        self.assertEqual([row["shift_id"] for row in occurrences], [10, 11])
        self.assertEqual(
            [row["occurrence_date"] for row in occurrences],
            ["2026-08-01", "2026-08-01"]
        )
        self.assertEqual(
            [row["planned_shift_type"] for row in occurrences],
            ["Day", "Day"]
        )
        self.assertTrue(all(
            row["shift_bound_at_utc"] == self.FIXED_TIMESTAMP
            for row in occurrences
        ))
        self.assertTrue(all(
            row["occurrence_status"] == "Scheduled"
            for row in occurrences
        ))

    def test_selected_shift_types_create_only_matching_occurrences(self):
        notice_id = self.create_notice()
        self.configure_schedule(
            notice_id,
            occurrence_basis="Shift",
            recurrence_pattern="Daily",
            shift_applicability="Selected Shift Types"
        )
        self.add_schedule_shift_types(notice_id, "Day", "Overnight")
        self.add_shift(20, "2026-08-01", "Day")
        self.add_shift(21, "2026-08-01", "Afternoon")
        self.add_shift(22, "2026-08-01", "Overnight")

        app.publish_staff_notice(notice_id, 1)

        self.assertEqual(
            [row["shift_id"] for row in self.occurrence_rows(notice_id)],
            [20, 22]
        )

    def test_all_approved_recurring_shift_schedule_combinations_create(self):
        configurations = (
            ("Once", "Every Shift", {}),
            ("Once", "Selected Shift Types", {}),
            ("Interval Days", "Every Shift", {
                "interval_days": 2,
                "recurrence_anchor_date": "2026-08-01"
            }),
            ("Interval Days", "Selected Shift Types", {
                "interval_days": 2,
                "recurrence_anchor_date": "2026-08-01"
            }),
            ("Selected Weekdays", "Every Shift", {}),
            ("Selected Weekdays", "Selected Shift Types", {})
        )

        for index, (pattern, applicability, extra) in enumerate(
            configurations,
            start=1
        ):
            with self.subTest(
                pattern=pattern,
                applicability=applicability
            ):
                notice_id = self.create_notice()
                self.configure_schedule(
                    notice_id,
                    occurrence_basis="Shift",
                    recurrence_pattern=pattern,
                    shift_applicability=applicability,
                    **extra
                )
                if applicability == "Selected Shift Types":
                    self.add_schedule_shift_types(notice_id, "Day")
                if pattern == "Selected Weekdays":
                    self.add_schedule_weekdays(notice_id, 5)
                shift_id = 100 + index
                self.add_shift(shift_id, "2026-08-01", "Day")

                app.publish_staff_notice(notice_id, 1)

                self.assertEqual(
                    [row["shift_id"] for row in self.occurrence_rows(
                        notice_id
                    )],
                    list(range(101, shift_id + 1))
                )

    def test_specific_shift_binds_existing_overnight_by_start_date(self):
        notice_id = self.create_notice()
        self.configure_schedule(
            notice_id,
            occurrence_basis="Shift",
            recurrence_pattern="Once",
            shift_applicability="Specific Shift",
            specific_shift_client_id=1,
            specific_shift_date="2026-08-01",
            specific_shift_type="Overnight"
        )
        self.add_shift(
            30,
            "2026-08-01",
            "Overnight",
            scheduled_start_time="23:00",
            scheduled_end_time="07:00"
        )
        self.add_shift_assignment(30, 4)

        app.publish_staff_notice(notice_id, 1)

        occurrence = self.occurrence_rows(notice_id)[0]
        self.assertEqual(occurrence["occurrence_date"], "2026-08-01")
        self.assertEqual(occurrence["shift_id"], 30)
        self.assertEqual(occurrence["is_specific_shift_occurrence"], 1)
        self.assertEqual(occurrence["visible_from_at_utc"], "2026-08-02T06:00:00Z")
        self.assertEqual(occurrence["due_at_utc"], "2026-08-02T14:00:00Z")
        self.assertEqual(occurrence["due_at_is_provisional"], 1)
        delivery = self.delivery_rows(notice_id)[0]
        self.assertEqual(delivery["user_id"], 4)
        self.assertEqual(delivery["shift_id"], 30)
        self.assertEqual(
            delivery["eligibility_cutoff_at_utc"],
            "2026-08-02T06:00:00Z"
        )
        self.assertEqual(delivery["due_at_utc"], "2026-08-02T14:00:00Z")
        occurrence_activity = self.occurrence_created_activity_rows(
            notice_id
        )[0]
        self.assertEqual(occurrence_activity["shift_id"], 30)
        self.assertEqual(
            occurrence_activity["related_id"],
            occurrence["occurrence_id"]
        )
        self.assertEqual(
            occurrence_activity["details"],
            f"Notice ID: {notice_id}; Kind: Shift; Date: 2026-08-01; "
            "Visible from: 2026-08-02T06:00:00Z; "
            "Due at: 2026-08-02T14:00:00Z"
        )

    def test_future_specific_shift_without_record_creates_pending_shift(self):
        notice_id = self.create_notice()
        self.configure_schedule(
            notice_id,
            occurrence_basis="Shift",
            recurrence_pattern="Once",
            shift_applicability="Specific Shift",
            specific_shift_client_id=1,
            specific_shift_date="2026-08-02",
            specific_shift_type="Overnight"
        )

        app.publish_staff_notice(notice_id, 1)

        occurrence = self.occurrence_rows(notice_id)[0]
        self.assertEqual(occurrence["occurrence_date"], "2026-08-02")
        self.assertEqual(occurrence["planned_client_id"], 1)
        self.assertEqual(occurrence["planned_shift_type"], "Overnight")
        self.assertIsNone(occurrence["shift_id"])
        self.assertEqual(occurrence["is_specific_shift_occurrence"], 1)
        self.assertEqual(occurrence["occurrence_status"], "Pending Shift")
        self.assertIsNone(occurrence["visible_from_at_utc"])
        self.assertIsNone(occurrence["due_at_utc"])
        self.assertIsNone(occurrence["shift_bound_at_utc"])
        self.assertEqual(self.delivery_rows(notice_id), [])
        self.assertEqual(self.delivery_history_rows(notice_id), [])
        activities = self.staff_notice_activity_rows(notice_id)
        self.assertEqual(len(activities), 3)
        self.assertEqual(
            [row["activity_type"] for row in activities],
            [
                "staff_notice_audience_eligibility_started",
                "staff_notice_occurrence_created",
                "staff_notice_published"
            ]
        )
        occurrence_activity = self.occurrence_created_activity_rows(
            notice_id
        )[0]
        self.assertIsNone(occurrence_activity["shift_id"])
        self.assertEqual(
            occurrence_activity["related_id"],
            occurrence["occurrence_id"]
        )
        self.assertEqual(
            occurrence_activity["details"],
            f"Notice ID: {notice_id}; Kind: Shift; Date: 2026-08-02; "
            "Visible from: None; Due at: None"
        )
        self.assertEqual(
            self.delivery_assignment_activity_rows(notice_id),
            []
        )
        self.assert_no_later_publication_rows()

    def test_occurrence_calculation_failure_rolls_back_publication(self):
        notice_id = self.create_notice()
        self.make_notice_shift_scheduled(notice_id)
        self.add_shift(
            40,
            "2026-08-01",
            "Day",
            scheduled_start_time="invalid"
        )
        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaisesRegex(ValueError, "scheduled shift start"):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.activity_insert_calls, 1)
        self.assertEqual(self.database_snapshot(), before)

    def test_occurrence_insert_failure_rolls_back_all_publication_rows(self):
        notice_id = self.create_notice()
        self.make_notice_shift_scheduled(notice_id)
        self.add_shift(50, "2026-08-01", "Day")
        self.add_shift(51, "2026-08-01", "Afternoon")
        conn = self.open_database()

        try:
            conn.execute("""
                CREATE TRIGGER control_occurrence_insert
                BEFORE INSERT ON staff_notice_occurrences
                WHEN NEW.shift_id = 50
                BEGIN
                    SELECT RAISE(ABORT, 'controlled occurrence failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled occurrence failure"
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.activity_insert_calls, 2)
        self.assertEqual(self.database_snapshot(), before)

    def test_occurrence_uniqueness_rejects_duplicate_schedule_identity(self):
        notice_id = self.create_notice()
        app.publish_staff_notice(notice_id, 1)
        occurrence = self.occurrence_rows(notice_id)[0]
        conn = self.open_database()

        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO staff_notice_occurrences
                    (
                        schedule_id,
                        occurrence_kind,
                        occurrence_status,
                        created_at_utc
                    )
                    VALUES (?, 'One Time', 'Scheduled', ?)
                """, (
                    occurrence["schedule_id"],
                    self.FIXED_TIMESTAMP
                ))
        finally:
            conn.close()

    def test_occurrence_creation_adds_no_later_stage_records(self):
        notice_id = self.create_notice()

        app.publish_staff_notice(notice_id, 1)

        self.assertEqual(len(self.occurrence_rows(notice_id)), 1)
        self.assert_no_later_publication_rows()

    def test_shift_deliveries_require_active_assignment_and_audience(self):
        notice_id = self.create_notice(audience_rules=(
            ("Core Organization", None, None),
            ("Selected Individual", None, 2)
        ))
        self.make_notice_shift_scheduled(notice_id)
        self.add_shift(200, "2026-08-01", "Day")
        self.add_shift_assignment(200, 2)
        self.add_shift_assignment(200, 3)
        self.add_shift_assignment(200, 4, active=0)

        app.publish_staff_notice(notice_id, 1)

        deliveries = self.delivery_rows(notice_id)
        self.assertEqual([row["user_id"] for row in deliveries], [2])
        self.assertEqual(deliveries[0]["shift_id"], 200)

    def test_applicable_shift_staff_includes_active_operational_manager(self):
        notice_id = self.create_notice(audience_rules=(
            ("Applicable Shift Staff", None, None),
        ))
        self.make_notice_shift_scheduled(notice_id)
        self.add_shift(201, "2026-08-01", "Day")
        self.add_shift_assignment(201, 2)
        self.add_shift_assignment(201, 3)

        app.publish_staff_notice(notice_id, 1)

        self.assertEqual(self.eligibility_rows(notice_id), [])
        self.assertEqual(
            [row["user_id"] for row in self.delivery_rows(notice_id)],
            [2]
        )

    def test_worker_receives_one_delivery_for_each_actual_shift_occurrence(self):
        notice_id = self.create_notice()
        self.make_notice_shift_scheduled(notice_id)
        self.add_shift(210, "2026-08-01", "Day")
        self.add_shift(211, "2026-08-01", "Day")
        self.add_shift_assignment(210, 4)
        self.add_shift_assignment(210, 4)
        self.add_shift_assignment(211, 4)

        app.publish_staff_notice(notice_id, 1)

        deliveries = self.delivery_rows(notice_id)
        self.assertEqual(
            [(row["shift_id"], row["user_id"]) for row in deliveries],
            [(210, 4), (211, 4)]
        )
        self.assertEqual(
            len({row["occurrence_id"] for row in deliveries}),
            2
        )

    def test_delivery_uniqueness_rejects_duplicate_occurrence_user(self):
        notice_id = self.create_notice()
        app.publish_staff_notice(notice_id, 1)
        delivery = self.delivery_rows(notice_id)[0]
        conn = self.open_database()

        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO staff_notice_deliveries
                    (
                        occurrence_id,
                        user_id,
                        requirement_status,
                        assigned_at_utc,
                        eligibility_cutoff_at_utc
                    )
                    VALUES (?, ?, 'Required', ?, ?)
                """, (
                    delivery["occurrence_id"],
                    delivery["user_id"],
                    self.FIXED_TIMESTAMP,
                    delivery["eligibility_cutoff_at_utc"]
                ))
        finally:
            conn.close()

    def test_delivery_calculation_failure_rolls_back_entire_publication(self):
        notice_id = self.create_notice()
        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(
            app,
            "_load_initial_staff_notice_delivery_user_ids",
            side_effect=ValueError("controlled delivery calculation failure")
        ), mock.patch.object(
            app,
            "get_db",
            return_value=connection
        ):
            with self.assertRaisesRegex(
                ValueError,
                "controlled delivery calculation failure"
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.activity_insert_calls, 2)
        self.assertEqual(self.database_snapshot(), before)

    def test_delivery_insert_failure_rolls_back_entire_publication(self):
        notice_id = self.create_notice(audience_rules=(
            ("Core Organization", None, None),
        ))
        conn = self.open_database()

        try:
            conn.execute("""
                CREATE TRIGGER control_delivery_insert
                BEFORE INSERT ON staff_notice_deliveries
                WHEN NEW.user_id = 2
                BEGIN
                    SELECT RAISE(ABORT, 'controlled delivery failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        connection = self.tracking_connection()

        with mock.patch.object(app, "get_db", return_value=connection):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled delivery failure"
            ):
                app.publish_staff_notice(notice_id, 1)

        self.assertEqual(connection.activity_insert_calls, 5)
        self.assertEqual(self.database_snapshot(), before)

    def test_initial_delivery_creation_has_assigned_history_and_no_later_state(
        self
    ):
        notice_id = self.create_notice()

        app.publish_staff_notice(notice_id, 1)

        deliveries = self.delivery_rows(notice_id)
        self.assertEqual(len(deliveries), 1)
        history_rows = self.delivery_history_rows(notice_id)
        self.assertEqual(len(history_rows), 1)
        self.assertEqual(
            history_rows[0]["delivery_id"],
            deliveries[0]["delivery_id"]
        )
        self.assertEqual(history_rows[0]["event_type"], "Assigned")
        assignment_activities = self.delivery_assignment_activity_rows(
            notice_id
        )
        self.assertEqual(len(assignment_activities), 1)
        self.assertEqual(
            assignment_activities[0]["related_id"],
            deliveries[0]["delivery_id"]
        )
        self.assert_no_later_publication_rows()


if __name__ == "__main__":
    unittest.main()
