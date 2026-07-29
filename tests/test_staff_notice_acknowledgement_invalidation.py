import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app
import add_staff_notices_tables as staff_notice_schema


class StaffNoticeAcknowledgementInvalidationTests(unittest.TestCase):
    FIXED_NOW = datetime(2026, 8, 3, 19, 30, tzinfo=timezone.utc)
    FIXED_TIMESTAMP = "2026-08-03T19:30:00Z"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "invalidation.db"
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
                    status TEXT NOT NULL
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
                    acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    comment TEXT,
                    acknowledgement_type TEXT DEFAULT 'Read',
                    active INTEGER NOT NULL DEFAULT 1
                        CHECK (active IN (0, 1)),
                    invalidated_at_utc TEXT,
                    invalidated_by_user_id INTEGER,
                    invalidation_reason TEXT,
                    FOREIGN KEY (invalidated_by_user_id)
                        REFERENCES users(user_id)
                        ON DELETE RESTRICT
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
                (5, "Other Worker", "Support Worker", 1),
                (6, "Inactive Admin", "Admin", 0)
            ))
            conn.execute("""
                INSERT INTO clients (client_id, client_name, active)
                VALUES (1, 'Active Client', 1)
            """)
            conn.execute("""
                INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status)
                VALUES (1, 1, '2026-08-03', 'Day', 'Open')
            """)
            conn.commit()
        finally:
            conn.close()

    def open_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def create_acknowledged_delivery(self):
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
                    'Acknowledgement Test', 'Preserved notice text.',
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
            conn.execute("""
                INSERT INTO staff_notice_audience_rules
                (audience_id, rule_type, created_at_utc)
                VALUES (?, 'Applicable Shift Staff',
                        '2026-08-01T07:00:00Z')
            """, (cursor.lastrowid,))
            cursor = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id, occurrence_basis, recurrence_pattern,
                    shift_applicability, created_at_utc
                )
                VALUES (
                    ?, 'Shift', 'Daily', 'Every Shift',
                    '2026-08-01T07:00:00Z'
                )
            """, (notice_id,))
            schedule_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_occurrences
                (
                    schedule_id, occurrence_kind, occurrence_date,
                    planned_client_id, planned_shift_type, shift_id,
                    visible_from_at_utc, due_at_utc, occurrence_status,
                    created_at_utc
                )
                VALUES (
                    ?, 'Shift', '2026-08-03', 1, 'Day', 1,
                    '2026-08-03T14:00:00Z', '2026-08-03T22:00:00Z',
                    'Active', '2026-08-01T07:05:00Z'
                )
            """, (schedule_id,))
            occurrence_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_deliveries
                (
                    occurrence_id, user_id, requirement_status,
                    assigned_at_utc, eligibility_cutoff_at_utc,
                    first_viewed_at_utc, viewed_by_user_id,
                    recipient_access
                )
                VALUES (
                    ?, 4, 'Required', '2026-08-01T07:05:00Z',
                    '2026-08-01T07:05:00Z', '2026-08-03T15:00:00Z',
                    4, 1
                )
            """, (occurrence_id,))
            delivery_id = cursor.lastrowid
            conn.execute("""
                INSERT INTO staff_notice_delivery_history
                (
                    delivery_id, event_type, new_requirement_status,
                    new_recipient_access, changed_at_utc
                )
                VALUES (
                    ?, 'Assigned', 'Required', 1,
                    '2026-08-01T07:05:00Z'
                )
            """, (delivery_id,))
            cursor = conn.execute("""
                INSERT INTO acknowledgements
                (
                    source_table, source_id, user_id, acknowledged_at,
                    comment, acknowledgement_type, active
                )
                VALUES (
                    'staff_notice_deliveries', ?, 4,
                    '2026-08-03T16:00:00Z', 'Original comment',
                    'Acknowledgement', 1
                )
            """, (delivery_id,))
            acknowledgement_id = cursor.lastrowid
            conn.commit()
            return notice_id, occurrence_id, delivery_id, acknowledgement_id
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

    def invalidate(
        self,
        client,
        acknowledgement_id,
        *,
        reason="Acknowledgement entered in error.",
        confirmed=True
    ):
        data = {"invalidation_reason": reason}
        if confirmed:
            data["confirm_invalidation"] = "yes"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            return client.post(
                "/staff-notices/acknowledgement/"
                f"{acknowledgement_id}/invalidate",
                data=data
            )

    def snapshot(self):
        conn = self.open_database()
        try:
            snapshot = {}
            for table in (
                "staff_notices",
                "staff_notice_occurrences",
                "staff_notice_deliveries",
                "staff_notice_delivery_history",
                "acknowledgements",
                "activity_log"
            ):
                snapshot[table] = [
                    tuple(row)
                    for row in conn.execute(
                        f"SELECT * FROM {table} ORDER BY 1"
                    ).fetchall()
                ]
            return snapshot
        finally:
            conn.close()

    def test_invalidation_preserves_delivery_and_original_acknowledgement(self):
        notice_id, occurrence_id, delivery_id, acknowledgement_id = (
            self.create_acknowledged_delivery()
        )
        before = self.snapshot()
        response = self.invalidate(
            self.client(),
            acknowledgement_id,
            reason="<script>correction</script>"
        )
        self.assertEqual(response.status_code, 302)

        conn = self.open_database()
        try:
            acknowledgement = conn.execute("""
                SELECT *
                FROM acknowledgements
                WHERE acknowledgement_id = ?
            """, (acknowledgement_id,)).fetchone()
            self.assertEqual(
                (
                    acknowledgement["source_table"],
                    acknowledgement["source_id"],
                    acknowledgement["user_id"],
                    acknowledgement["acknowledged_at"],
                    acknowledgement["comment"],
                    acknowledgement["acknowledgement_type"]
                ),
                (
                    "staff_notice_deliveries",
                    delivery_id,
                    4,
                    "2026-08-03T16:00:00Z",
                    "Original comment",
                    "Acknowledgement"
                )
            )
            self.assertEqual(acknowledgement["active"], 0)
            self.assertEqual(
                acknowledgement["invalidated_at_utc"],
                self.FIXED_TIMESTAMP
            )
            self.assertEqual(
                acknowledgement["invalidated_by_user_id"],
                1
            )
            self.assertEqual(
                acknowledgement["invalidation_reason"],
                "<script>correction</script>"
            )
            self.assertEqual(
                tuple(conn.execute("""
                    SELECT *
                    FROM staff_notice_deliveries
                    WHERE delivery_id = ?
                """, (delivery_id,)).fetchone()),
                before["staff_notice_deliveries"][0]
            )
            self.assertEqual(
                len(conn.execute("""
                    SELECT *
                    FROM staff_notice_delivery_history
                    WHERE delivery_id = ?
                """, (delivery_id,)).fetchall()),
                1
            )
            activity = conn.execute(
                "SELECT * FROM activity_log"
            ).fetchone()
            self.assertEqual(
                activity["activity_type"],
                "staff_notice_acknowledgement_invalidated"
            )
            self.assertEqual(activity["related_table"], "acknowledgements")
            self.assertEqual(activity["related_id"], acknowledgement_id)
            self.assertEqual(
                activity["summary"],
                (
                    "Staff Notice acknowledgement invalidated: "
                    "Acknowledgement Test"
                )
            )
            self.assertEqual(
                activity["details"],
                (
                    f"Notice ID: {notice_id}; Occurrence ID: "
                    f"{occurrence_id}; Delivery ID: {delivery_id}; "
                    "Recipient User ID: 4; Acknowledgement ID: "
                    f"{acknowledgement_id}; Acknowledged at UTC: "
                    "2026-08-03T16:00:00Z; Invalidated by User ID: 1; "
                    "Reason: <script>correction</script>; Invalidated at "
                    f"UTC: {self.FIXED_TIMESTAMP}"
                )
            )
        finally:
            conn.close()

        tracking = self.client().get(
            f"/staff-notices/{notice_id}/tracking"
        )
        self.assertEqual(tracking.status_code, 200)
        self.assertIn(b"Viewed", tracking.data)
        self.assertIn(b"Awaiting Acknowledgement", tracking.data)
        self.assertIn(b"Admin User", tracking.data)
        self.assertIn(
            b"&lt;script&gt;correction&lt;/script&gt;",
            tracking.data
        )
        self.assertNotIn(b"<script>correction</script>", tracking.data)

    def test_reacknowledgement_creates_new_active_and_preserves_history(self):
        notice_id, _, delivery_id, acknowledgement_id = (
            self.create_acknowledged_delivery()
        )
        self.assertEqual(
            self.invalidate(self.client(), acknowledgement_id).status_code,
            302
        )
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(
                2026,
                8,
                3,
                20,
                0,
                tzinfo=timezone.utc
            )
        ):
            response = self.client(
                4,
                "Support Worker"
            ).post(
                f"/staff-notices/delivery/{delivery_id}/acknowledge",
                data={"acknowledge": "yes"}
            )
        self.assertEqual(response.status_code, 302)
        duplicate = self.client(
            4,
            "Support Worker"
        ).post(
            f"/staff-notices/delivery/{delivery_id}/acknowledge",
            data={"acknowledge": "yes"}
        )
        self.assertEqual(duplicate.status_code, 302)

        conn = self.open_database()
        try:
            acknowledgements = conn.execute("""
                SELECT *
                FROM acknowledgements
                WHERE source_table = 'staff_notice_deliveries'
                  AND source_id = ?
                  AND user_id = 4
                ORDER BY acknowledgement_id
            """, (delivery_id,)).fetchall()
            self.assertEqual(len(acknowledgements), 2)
            self.assertEqual(
                acknowledgements[0]["acknowledgement_id"],
                acknowledgement_id
            )
            self.assertEqual(acknowledgements[0]["active"], 0)
            self.assertEqual(
                acknowledgements[0]["acknowledged_at"],
                "2026-08-03T16:00:00Z"
            )
            self.assertEqual(
                acknowledgements[0]["invalidation_reason"],
                "Acknowledgement entered in error."
            )
            self.assertEqual(acknowledgements[1]["active"], 1)
            self.assertEqual(
                acknowledgements[1]["acknowledged_at"],
                "2026-08-03T20:00:00Z"
            )
            self.assertIsNone(
                acknowledgements[1]["invalidated_at_utc"]
            )
            self.assertIsNone(
                acknowledgements[1]["invalidated_by_user_id"]
            )
            self.assertIsNone(
                acknowledgements[1]["invalidation_reason"]
            )
            self.assertEqual(
                conn.execute("""
                    SELECT COUNT(*)
                    FROM activity_log
                    WHERE activity_type = 'record_acknowledged'
                """).fetchone()[0],
                1
            )
        finally:
            conn.close()

        tracking = self.client().get(
            f"/staff-notices/{notice_id}/tracking"
        )
        self.assertEqual(tracking.status_code, 200)
        self.assertIn(b"Invalidated", tracking.data)
        self.assertIn(b"Active", tracking.data)

    def test_repeated_invalidation_is_completely_write_free(self):
        _, _, _, acknowledgement_id = self.create_acknowledged_delivery()
        self.assertEqual(
            self.invalidate(self.client(), acknowledgement_id).status_code,
            302
        )
        before = self.snapshot()
        response = self.invalidate(
            self.client(),
            acknowledgement_id,
            reason="Changed reason must be ignored."
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_roles_authorization_and_form_validation(self):
        for user_id, role in (
            (1, "Admin"),
            (2, "Program Manager"),
            (3, "Director")
        ):
            _, _, _, acknowledgement_id = (
                self.create_acknowledged_delivery()
            )
            self.assertEqual(
                self.invalidate(
                    self.client(user_id, role),
                    acknowledgement_id
                ).status_code,
                302
            )

        _, _, _, acknowledgement_id = self.create_acknowledged_delivery()
        before = self.snapshot()
        self.assertEqual(
            self.invalidate(
                self.client(4, "Support Worker"),
                acknowledgement_id
            ).status_code,
            403
        )
        self.assertEqual(
            self.invalidate(
                self.client(6, "Admin"),
                acknowledgement_id
            ).status_code,
            403
        )
        self.assertEqual(
            self.invalidate(
                self.client(),
                acknowledgement_id,
                reason=""
            ).status_code,
            400
        )
        self.assertEqual(
            self.invalidate(
                self.client(),
                acknowledgement_id,
                confirmed=False
            ).status_code,
            400
        )
        self.assertEqual(self.snapshot(), before)

    def test_wrong_source_and_inconsistent_inactive_history_conflict(self):
        _, _, _, acknowledgement_id = self.create_acknowledged_delivery()
        conn = self.open_database()
        try:
            cursor = conn.execute("""
                INSERT INTO acknowledgements
                (
                    source_table, source_id, user_id,
                    acknowledgement_type, active
                )
                VALUES ('shift_notes', 1, 4, 'Read', 1)
            """)
            wrong_source_id = cursor.lastrowid
            conn.execute("""
                UPDATE acknowledgements
                SET active = 0,
                    invalidated_at_utc = NULL,
                    invalidated_by_user_id = NULL,
                    invalidation_reason = NULL
                WHERE acknowledgement_id = ?
            """, (acknowledgement_id,))
            conn.commit()
        finally:
            conn.close()
        before = self.snapshot()
        self.assertEqual(
            self.invalidate(self.client(), wrong_source_id).status_code,
            404
        )
        self.assertEqual(
            self.invalidate(self.client(), acknowledgement_id).status_code,
            409
        )
        self.assertEqual(self.snapshot(), before)

    def test_unavailable_delivery_cannot_be_invalidated(self):
        _, _, delivery_id, acknowledgement_id = (
            self.create_acknowledged_delivery()
        )
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_deliveries
                SET recipient_access = 0
                WHERE delivery_id = ?
            """, (delivery_id,))
            conn.commit()
        finally:
            conn.close()
        before = self.snapshot()
        response = self.invalidate(self.client(), acknowledgement_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_activity_failure_rolls_back_everything(self):
        _, _, _, acknowledgement_id = self.create_acknowledged_delivery()
        before = self.snapshot()
        with mock.patch.object(
            app,
            "log_activity",
            side_effect=sqlite3.OperationalError("forced activity failure")
        ):
            response = self.invalidate(
                self.client(),
                acknowledgement_id
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_acknowledgement_update_failure_rolls_back_everything(self):
        _, _, _, acknowledgement_id = self.create_acknowledged_delivery()
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER fail_acknowledgement_invalidation
                BEFORE UPDATE OF active ON acknowledgements
                WHEN OLD.acknowledgement_id = NEW.acknowledgement_id
                 AND OLD.active = 1
                 AND NEW.active = 0
                BEGIN
                    SELECT RAISE(ABORT, 'forced invalidation failure');
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.snapshot()
        response = self.invalidate(self.client(), acknowledgement_id)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_verification_failure_rolls_back_everything(self):
        _, _, _, acknowledgement_id = self.create_acknowledged_delivery()
        before = self.snapshot()
        original_loader = (
            app._load_staff_notice_acknowledgement_invalidation_context
        )
        call_count = 0

        def corrupt_second_load(conn, loaded_acknowledgement_id):
            nonlocal call_count
            call_count += 1
            acknowledgement, delivery = original_loader(
                conn,
                loaded_acknowledgement_id
            )
            if call_count == 2:
                delivery = dict(delivery)
                delivery["assigned_at_utc"] = "corrupt"
            return acknowledgement, delivery

        with mock.patch.object(
            app,
            "_load_staff_notice_acknowledgement_invalidation_context",
            side_effect=corrupt_second_load
        ):
            response = self.invalidate(
                self.client(),
                acknowledgement_id
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_commit_failure_attempts_rollback_and_preserves_snapshot(self):
        _, _, _, acknowledgement_id = self.create_acknowledged_delivery()
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

        with mock.patch.object(
            app,
            "get_db",
            side_effect=lambda: CommitFailingConnection(real_get_db())
        ):
            response = self.invalidate(
                self.client(),
                acknowledgement_id
            )
        self.assertEqual(response.status_code, 503)
        self.assertTrue(rollback_calls)
        self.assertEqual(self.snapshot(), before)

    def test_service_is_caller_transaction_owned(self):
        _, _, _, acknowledgement_id = self.create_acknowledged_delivery()
        conn = self.open_database()
        try:
            conn.execute("BEGIN IMMEDIATE")
            with mock.patch.object(
                app,
                "get_db",
                side_effect=AssertionError("service opened a connection")
            ):
                result = app.invalidate_staff_notice_acknowledgement(
                    conn,
                    acknowledgement_id,
                    1,
                    "Caller-owned transaction.",
                    self.FIXED_NOW
                )
            self.assertEqual(result["invalidated"], 1)
            self.assertTrue(conn.in_transaction)
            conn.rollback()
        finally:
            conn.close()

    def test_create_acknowledgement_race_reuses_exact_active_row(self):
        conn = self.open_database()
        try:
            class RacingConnection:
                def __init__(self, connection):
                    self.connection = connection
                    self.raced_id = None

                def execute(self, sql, parameters=()):
                    if "INSERT INTO acknowledgements" in sql:
                        cursor = self.connection.execute("""
                            INSERT INTO acknowledgements
                            (
                                source_table, source_id, user_id,
                                acknowledged_at, acknowledgement_type,
                                active
                            )
                            VALUES (?, ?, ?, '2026-08-03T19:00:00Z',
                                    'Acknowledgement', 1)
                        """, (parameters[0], parameters[1], parameters[2]))
                        self.raced_id = cursor.lastrowid
                        raise sqlite3.IntegrityError("simulated race")
                    return self.connection.execute(sql, parameters)

            racing = RacingConnection(conn)
            with mock.patch.object(app, "log_activity") as activity:
                acknowledgement_id = app.create_acknowledgement(
                    racing,
                    "staff_notice_deliveries",
                    999,
                    4,
                    acknowledgement_type="Acknowledgement"
                )
            self.assertEqual(acknowledgement_id, racing.raced_id)
            activity.assert_not_called()
            conn.rollback()
        finally:
            conn.close()

    def test_create_acknowledgement_unexplained_integrity_error_raises(self):
        conn = self.open_database()
        try:
            class FailingConnection:
                def __init__(self, connection):
                    self.connection = connection

                def execute(self, sql, parameters=()):
                    if "INSERT INTO acknowledgements" in sql:
                        raise sqlite3.IntegrityError("unexplained")
                    return self.connection.execute(sql, parameters)

            with self.assertRaises(sqlite3.IntegrityError):
                app.create_acknowledgement(
                    FailingConnection(conn),
                    "staff_notice_deliveries",
                    999,
                    4,
                    acknowledgement_type="Acknowledgement"
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
