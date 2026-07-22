import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app
import add_staff_notices_tables as staff_notice_schema


LATER_PUBLICATION_TABLES = (
    "staff_notice_audience_eligibility_periods",
    "staff_notice_occurrences",
    "staff_notice_deliveries",
    "staff_notice_delivery_history",
    "acknowledgements",
    "activity_log"
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

    def create_notice(self, *, audience=True, schedule=True):
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
                conn.execute("""
                    INSERT INTO staff_notice_audience_rules
                    (audience_id, rule_type, created_at_utc)
                    VALUES (?, 'All Support Workers',
                            '2026-07-30T19:00:00Z')
                """, (cursor.lastrowid,))

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

    def test_successful_publication_changes_only_lifecycle_metadata(self):
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
        self.assertEqual(connection.update_calls, 1)
        self.assertEqual(connection.commit_calls, 1)
        self.assertEqual(connection.rollback_calls, 0)
        self.assertEqual(connection.close_calls, 1)

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
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(connection.close_calls, 1)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_later_publication_rows()

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
        self.assertEqual(connection.commit_calls, 0)
        self.assertEqual(connection.rollback_calls, 1)
        self.assertEqual(self.database_snapshot(), before)

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


if __name__ == "__main__":
    unittest.main()
