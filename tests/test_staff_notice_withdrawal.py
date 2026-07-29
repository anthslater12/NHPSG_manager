import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app
import add_staff_notices_tables as staff_notice_schema


class StaffNoticeWithdrawalTests(unittest.TestCase):
    FIXED_NOW = datetime(2026, 7, 31, 19, 0, tzinfo=timezone.utc)
    FIXED_TIMESTAMP = "2026-07-31T19:00:00Z"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "withdrawal.db"
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
                (4, "Support Worker", "Support Worker", 1),
                (5, "Acknowledged Worker", "Support Worker", 1),
                (6, "Other Worker", "Support Worker", 1)
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

    def create_published_notice(self, *, title="Withdrawal Test"):
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
                    ?,
                    'Preserved published content.',
                    'Important',
                    1,
                    'Published',
                    0,
                    '2026-07-01T08:00:00Z',
                    NULL,
                    1,
                    1,
                    1,
                    '2026-07-01T19:00:00Z',
                    1,
                    '2026-07-01T19:05:00Z'
                )
            """, (title,))
            notice_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_audiences
                (notice_id, created_at_utc)
                VALUES (?, '2026-07-01T19:00:00Z')
            """, (notice_id,))
            audience_id = cursor.lastrowid
            conn.execute("""
                INSERT INTO staff_notice_audience_rules
                (audience_id, rule_type, created_at_utc)
                VALUES (?, 'All Support Workers', '2026-07-01T19:00:00Z')
            """, (audience_id,))
            cursor = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id,
                    occurrence_basis,
                    recurrence_pattern,
                    shift_applicability,
                    created_at_utc
                )
                VALUES (?, 'One Time', 'Once', 'None',
                        '2026-07-01T19:00:00Z')
            """, (notice_id,))
            schedule_id = cursor.lastrowid
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
                VALUES (
                    ?,
                    'One Time',
                    '2026-08-01T07:00:00Z',
                    '2026-08-02T07:00:00Z',
                    'Scheduled',
                    '2026-07-01T19:05:00Z'
                )
            """, (schedule_id,))
            occurrence_id = cursor.lastrowid
            delivery_ids = {}
            for user_id in (4, 5):
                cursor = conn.execute("""
                    INSERT INTO staff_notice_deliveries
                    (
                        occurrence_id,
                        user_id,
                        requirement_status,
                        assigned_at_utc,
                        eligibility_cutoff_at_utc,
                        recipient_access
                    )
                    VALUES (
                        ?,
                        ?,
                        'Required',
                        '2026-07-01T19:05:00Z',
                        '2026-07-01T19:05:00Z',
                        1
                    )
                """, (occurrence_id, user_id))
                delivery_ids[user_id] = cursor.lastrowid
                conn.execute("""
                    INSERT INTO staff_notice_delivery_history
                    (
                        delivery_id,
                        event_type,
                        new_requirement_status,
                        new_recipient_access,
                        changed_at_utc
                    )
                    VALUES (?, 'Assigned', 'Required', 1,
                            '2026-07-01T19:05:00Z')
                """, (cursor.lastrowid,))

            conn.execute("""
                UPDATE staff_notice_deliveries
                SET first_viewed_at_utc = '2026-07-02T09:00:00Z',
                    viewed_by_user_id = 4
                WHERE delivery_id = ?
            """, (delivery_ids[4],))
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
                    5,
                    'Acknowledgement',
                    '2026-07-02T10:00:00Z',
                    1
                )
            """, (delivery_ids[5],))
            acknowledgement_id = cursor.lastrowid
            conn.commit()
            return {
                "notice_id": notice_id,
                "schedule_id": schedule_id,
                "occurrence_id": occurrence_id,
                "unacknowledged_delivery_id": delivery_ids[4],
                "acknowledged_delivery_id": delivery_ids[5],
                "acknowledgement_id": acknowledgement_id
            }
        finally:
            conn.close()

    def client_for(self, user_id=None, role=None):
        client = app.app.test_client()
        if user_id is not None:
            with client.session_transaction() as session_data:
                session_data["user_id"] = user_id
                session_data["role"] = role
                session_data["full_name"] = f"User {user_id}"
        return client

    def post_withdrawal(
        self,
        client,
        notice_id,
        *,
        reason="Operational guidance withdrawn.",
        confirmed=True
    ):
        data = {"withdrawal_reason": reason}
        if confirmed:
            data["confirm_withdrawal"] = "yes"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            return client.post(
                f"/staff-notices/{notice_id}/withdraw",
                data=data
            )

    def database_snapshot(self):
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

    def test_withdrawal_cancels_outstanding_and_revokes_all_access(self):
        fixture = self.create_published_notice()
        response = self.post_withdrawal(
            self.client_for(1, "Admin"),
            fixture["notice_id"]
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("withdrawal_result=withdrawn", response.location)

        conn = self.open_database()
        try:
            notice = conn.execute("""
                SELECT *
                FROM staff_notices
                WHERE notice_id = ?
            """, (fixture["notice_id"],)).fetchone()
            occurrence = conn.execute("""
                SELECT *
                FROM staff_notice_occurrences
                WHERE occurrence_id = ?
            """, (fixture["occurrence_id"],)).fetchone()
            deliveries = {
                row["user_id"]: row
                for row in conn.execute("""
                    SELECT *
                    FROM staff_notice_deliveries
                    WHERE occurrence_id = ?
                    ORDER BY user_id
                """, (fixture["occurrence_id"],)).fetchall()
            }
            acknowledgement = conn.execute("""
                SELECT *
                FROM acknowledgements
                WHERE acknowledgement_id = ?
            """, (fixture["acknowledgement_id"],)).fetchone()
            activities = conn.execute("""
                SELECT *
                FROM activity_log
                ORDER BY activity_id
            """).fetchall()
        finally:
            conn.close()

        self.assertEqual(notice["status"], "Withdrawn")
        self.assertEqual(notice["withdrawn_by_user_id"], 1)
        self.assertEqual(
            notice["withdrawn_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertEqual(
            notice["withdrawal_reason"],
            "Operational guidance withdrawn."
        )
        self.assertEqual(occurrence["occurrence_status"], "Cancelled")
        self.assertEqual(occurrence["status_reason"], "Notice Withdrawn")
        self.assertEqual(
            deliveries[4]["requirement_status"],
            "Cancelled"
        )
        self.assertEqual(deliveries[5]["requirement_status"], "Required")
        for delivery in deliveries.values():
            self.assertEqual(delivery["recipient_access"], 0)
            self.assertEqual(
                delivery["access_revoked_at_utc"],
                self.FIXED_TIMESTAMP
            )
        self.assertEqual(
            deliveries[4]["first_viewed_at_utc"],
            "2026-07-02T09:00:00Z"
        )
        self.assertEqual(deliveries[4]["viewed_by_user_id"], 4)
        self.assertEqual(
            acknowledgement["acknowledged_at"],
            "2026-07-02T10:00:00Z"
        )
        self.assertEqual(acknowledgement["active"], 1)
        self.assertEqual(
            [row["activity_type"] for row in activities],
            [
                "staff_notice_occurrence_status_changed",
                "staff_notice_delivery_cancelled",
                "staff_notice_delivery_access_revoked",
                "staff_notice_delivery_access_revoked",
                "staff_notice_withdrawn"
            ]
        )

    def test_withdrawal_history_payloads_and_status_derivation(self):
        fixture = self.create_published_notice()
        self.post_withdrawal(
            self.client_for(2, "Program Manager"),
            fixture["notice_id"],
            reason="<withdrawal reason>"
        )
        conn = self.open_database()
        try:
            unacknowledged_history = conn.execute("""
                SELECT *
                FROM staff_notice_delivery_history
                WHERE delivery_id = ?
                ORDER BY delivery_history_id
            """, (
                fixture["unacknowledged_delivery_id"],
            )).fetchall()
            acknowledged_history = conn.execute("""
                SELECT *
                FROM staff_notice_delivery_history
                WHERE delivery_id = ?
                ORDER BY delivery_history_id
            """, (
                fixture["acknowledged_delivery_id"],
            )).fetchall()
            acknowledged_delivery = conn.execute("""
                SELECT d.*, o.due_at_utc
                FROM staff_notice_deliveries d
                JOIN staff_notice_occurrences o
                    ON d.occurrence_id = o.occurrence_id
                WHERE d.delivery_id = ?
            """, (
                fixture["acknowledged_delivery_id"],
            )).fetchone()
        finally:
            conn.close()

        self.assertEqual(
            [row["event_type"] for row in unacknowledged_history],
            ["Assigned", "Cancelled", "Access Revoked"]
        )
        self.assertEqual(
            [row["event_type"] for row in acknowledged_history],
            ["Assigned", "Access Revoked"]
        )
        for row in (
            unacknowledged_history[1:]
            + acknowledged_history[1:]
        ):
            self.assertEqual(row["reason_code"], "Notice Withdrawn")
            self.assertEqual(row["reason_text"], "<withdrawal reason>")
            self.assertEqual(row["changed_by_user_id"], 2)
            self.assertEqual(
                row["changed_at_utc"],
                self.FIXED_TIMESTAMP
            )
        self.assertEqual(
            app.get_recipient_staff_notice_status(
                active_acknowledgement_at_utc=(
                    "2026-07-02T10:00:00Z"
                ),
                due_at_utc=acknowledged_delivery["due_at_utc"],
                requirement_status=(
                    acknowledged_delivery["requirement_status"]
                ),
                first_viewed_at_utc=None
            ),
            "Acknowledged"
        )

    def test_management_roles_are_authorized(self):
        for user_id, role in (
            (1, "Admin"),
            (2, "Program Manager"),
            (3, "Director")
        ):
            with self.subTest(role=role):
                fixture = self.create_published_notice(title=role)
                response = self.post_withdrawal(
                    self.client_for(user_id, role),
                    fixture["notice_id"]
                )
                self.assertEqual(response.status_code, 302)

    def test_unauthenticated_worker_get_and_invalid_forms_do_not_mutate(self):
        fixture = self.create_published_notice()
        path = f"/staff-notices/{fixture['notice_id']}/withdraw"
        before = self.database_snapshot()
        anonymous = app.app.test_client().post(path, data={
            "confirm_withdrawal": "yes",
            "withdrawal_reason": "Unauthorized"
        })
        worker = self.client_for(4, "Support Worker").post(path, data={
            "confirm_withdrawal": "yes",
            "withdrawal_reason": "Unauthorized"
        })
        manager = self.client_for(1, "Admin")
        get_response = manager.get(path)
        missing_confirmation = manager.post(path, data={
            "withdrawal_reason": "Missing confirmation"
        })
        missing_reason = manager.post(path, data={
            "confirm_withdrawal": "yes"
        })
        self.assertEqual(anonymous.status_code, 302)
        self.assertEqual(worker.status_code, 403)
        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertEqual(missing_reason.status_code, 400)
        self.assertEqual(self.database_snapshot(), before)

    def test_missing_invalid_and_repeated_requests_are_safe(self):
        client = self.client_for(1, "Admin")
        missing = self.post_withdrawal(client, 999999)
        self.assertEqual(missing.status_code, 404)

        fixture = self.create_published_notice()
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notices
                SET status = 'Draft',
                    draft_active = 1,
                    published_by_user_id = NULL,
                    published_at_utc = NULL
                WHERE notice_id = ?
            """, (fixture["notice_id"],))
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        invalid = self.post_withdrawal(client, fixture["notice_id"])
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(self.database_snapshot(), before)

        published = self.create_published_notice(title="Idempotent")
        first = self.post_withdrawal(client, published["notice_id"])
        self.assertEqual(first.status_code, 302)
        snapshot = self.database_snapshot()
        repeated = self.post_withdrawal(
            client,
            published["notice_id"],
            reason="Different stale reason"
        )
        self.assertEqual(repeated.status_code, 302)
        self.assertIn("withdrawal_result=unchanged", repeated.location)
        self.assertEqual(self.database_snapshot(), snapshot)

    def test_unrelated_notice_and_active_occurrence_are_preserved(self):
        target = self.create_published_notice(title="Target")
        unrelated = self.create_published_notice(title="Unrelated")
        conn = self.open_database()
        try:
            conn.execute("""
                UPDATE staff_notice_occurrences
                SET occurrence_status = 'Active'
                WHERE occurrence_id = ?
            """, (target["occurrence_id"],))
            conn.commit()
        finally:
            conn.close()
        unrelated_before = self.database_snapshot()
        self.post_withdrawal(
            self.client_for(1, "Admin"),
            target["notice_id"]
        )
        conn = self.open_database()
        try:
            occurrence_status = conn.execute("""
                SELECT occurrence_status
                FROM staff_notice_occurrences
                WHERE occurrence_id = ?
            """, (target["occurrence_id"],)).fetchone()[0]
            unrelated_notice = conn.execute("""
                SELECT status
                FROM staff_notices
                WHERE notice_id = ?
            """, (unrelated["notice_id"],)).fetchone()[0]
            unrelated_delivery_count = conn.execute("""
                SELECT COUNT(*)
                FROM staff_notice_deliveries d
                JOIN staff_notice_occurrences o
                    ON d.occurrence_id = o.occurrence_id
                JOIN staff_notice_schedules sns
                    ON o.schedule_id = sns.schedule_id
                WHERE sns.notice_id = ?
            """, (unrelated["notice_id"],)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(occurrence_status, "Active")
        self.assertEqual(unrelated_notice, "Published")
        self.assertEqual(unrelated_delivery_count, 2)
        self.assertNotEqual(self.database_snapshot(), unrelated_before)

    def test_history_failure_rolls_back_complete_withdrawal(self):
        fixture = self.create_published_notice()
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER controlled_withdrawal_history_failure
                BEFORE INSERT ON staff_notice_delivery_history
                WHEN NEW.event_type = 'Cancelled'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled withdrawal history failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        response = self.post_withdrawal(
            self.client_for(1, "Admin"),
            fixture["notice_id"]
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.database_snapshot(), before)

    def test_activity_failure_rolls_back_complete_withdrawal(self):
        fixture = self.create_published_notice()
        conn = self.open_database()
        try:
            conn.execute("""
                CREATE TRIGGER controlled_withdrawal_activity_failure
                BEFORE INSERT ON activity_log
                WHEN NEW.activity_type = 'staff_notice_withdrawn'
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'controlled withdrawal activity failure'
                    );
                END
            """)
            conn.commit()
        finally:
            conn.close()
        before = self.database_snapshot()
        response = self.post_withdrawal(
            self.client_for(1, "Admin"),
            fixture["notice_id"]
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.database_snapshot(), before)

    def test_tracking_preserves_history_escapes_reason_and_blocks_worker(self):
        fixture = self.create_published_notice()
        client = self.client_for(1, "Admin")
        self.post_withdrawal(
            client,
            fixture["notice_id"],
            reason="<script>withdraw()</script>"
        )
        before = self.database_snapshot()
        tracking = client.get(
            f"/staff-notices/{fixture['notice_id']}/tracking"
        )
        self.assertEqual(tracking.status_code, 200)
        self.assertIn(b"Withdrawn by Admin User", tracking.data)
        self.assertIn(
            b"&lt;script&gt;withdraw()&lt;/script&gt;",
            tracking.data
        )
        self.assertNotIn(b"<script>withdraw()</script>", tracking.data)
        self.assertNotIn(b"Withdraw Notice</button>", tracking.data)
        management_list = client.get("/staff-notices/manage")
        self.assertEqual(management_list.status_code, 200)
        self.assertIn(b"View History", management_list.data)
        self.assertIn(
            (
                f"/staff-notices/{fixture['notice_id']}/tracking"
            ).encode(),
            management_list.data
        )

        worker = self.client_for(4, "Support Worker")
        detail = worker.get(
            "/staff-notices/delivery/"
            f"{fixture['unacknowledged_delivery_id']}"
        )
        self.assertEqual(detail.status_code, 403)
        self.assertEqual(self.database_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
