import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from werkzeug.security import generate_password_hash

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


class _StaleUserConnection:
    def __init__(self, connection, user_id):
        self.connection = connection
        self.user_id = user_id
        self.changed = False

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, parameters=()):
        if sql.strip().upper() == "BEGIN IMMEDIATE" and not self.changed:
            self.connection.execute("""
                UPDATE users
                SET full_name = 'Concurrent Edit'
                WHERE user_id = ?
            """, (self.user_id,))
            self.connection.commit()
            self.changed = True
        return self.connection.execute(sql, parameters)


class StaffNoticeAcceptanceRemediationTests(unittest.TestCase):
    FIXED_NOW = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
    FIXED_TIMESTAMP = "2026-08-12T18:00:00Z"

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "acceptance-remediation.db"
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
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    must_change_password INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE clients (
                    client_id INTEGER PRIMARY KEY,
                    client_name TEXT NOT NULL,
                    active INTEGER NOT NULL
                );
                CREATE TABLE shifts (
                    shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                    acknowledged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    comment TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
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
            password_hash = generate_password_hash("password")
            conn.executemany("""
                INSERT INTO users
                (
                    user_id, username, password_hash, full_name,
                    role, active, must_change_password
                )
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (
                (1, "admin", password_hash, "Admin User", "Admin", 1),
                (
                    2,
                    "manager",
                    password_hash,
                    "Program Manager",
                    "Program Manager",
                    1
                ),
                (
                    3,
                    "director",
                    password_hash,
                    "Director User",
                    "Director",
                    1
                ),
                (
                    4,
                    "worker",
                    password_hash,
                    "Support Worker",
                    "Support Worker",
                    1
                ),
                (
                    5,
                    "consultant",
                    password_hash,
                    "Consultant",
                    "Behaviour Consultant",
                    1
                ),
                (
                    6,
                    "inactive",
                    password_hash,
                    "Inactive Consultant",
                    "Behaviour Consultant",
                    0
                )
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

    def login_session(self, user_id, role):
        client = app.app.test_client()
        with client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = role
            session_data["full_name"] = "Recipient"
        return client

    def create_one_time_notice(
        self,
        *,
        audience_rule_type,
        selected_user_id=None,
        selected_role=None,
        title="Acceptance Notice"
    ):
        conn = self.open_database()
        try:
            cursor = conn.execute("""
                INSERT INTO staff_notices
                (
                    title, notice_text, priority, status, draft_active,
                    effective_start_at_utc, until_withdrawn,
                    version_number, created_by_user_id, created_at_utc,
                    published_by_user_id, published_at_utc
                )
                VALUES (
                    ?, 'Read the approved notice body.', 'Important',
                    'Published', 0, '2026-08-01T07:00:00Z', 1,
                    1, 1, '2026-08-01T07:00:00Z',
                    1, '2026-08-01T07:00:00Z'
                )
            """, (title,))
            notice_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_audiences
                (notice_id, created_at_utc)
                VALUES (?, '2026-08-01T07:00:00Z')
            """, (notice_id,))
            audience_id = cursor.lastrowid
            conn.execute("""
                INSERT INTO staff_notice_audience_rules
                (
                    audience_id, rule_type, role_name,
                    user_id, created_at_utc
                )
                VALUES (?, ?, ?, ?, '2026-08-01T07:00:00Z')
            """, (
                audience_id,
                audience_rule_type,
                selected_role,
                selected_user_id
            ))
            cursor = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id, occurrence_basis, recurrence_pattern,
                    shift_applicability, one_time_due_at_utc,
                    created_at_utc
                )
                VALUES (
                    ?, 'One Time', 'Once', 'None',
                    '2026-08-15T07:00:00Z',
                    '2026-08-01T07:00:00Z'
                )
            """, (notice_id,))
            schedule_id = cursor.lastrowid
            cursor = conn.execute("""
                INSERT INTO staff_notice_occurrences
                (
                    schedule_id, occurrence_kind, visible_from_at_utc,
                    due_at_utc, occurrence_status, created_at_utc
                )
                VALUES (
                    ?, 'One Time', '2026-08-01T07:00:00Z',
                    '2026-08-15T07:00:00Z', 'Active',
                    '2026-08-01T07:00:00Z'
                )
            """, (schedule_id,))
            occurrence_id = cursor.lastrowid
            conn.commit()
            return {
                "notice_id": notice_id,
                "audience_id": audience_id,
                "occurrence_id": occurrence_id
            }
        finally:
            conn.close()

    def assign_delivery(
        self,
        fixture,
        user_id,
        *,
        viewed_at=None,
        acknowledged_at=None
    ):
        conn = self.open_database()
        try:
            cursor = conn.execute("""
                INSERT INTO staff_notice_deliveries
                (
                    occurrence_id, user_id, requirement_status,
                    assigned_at_utc, eligibility_cutoff_at_utc,
                    first_viewed_at_utc, viewed_by_user_id,
                    recipient_access
                )
                VALUES (
                    ?, ?, 'Required', '2026-08-01T07:00:00Z',
                    '2026-08-01T07:00:00Z', ?, ?, 1
                )
            """, (
                fixture["occurrence_id"],
                user_id,
                viewed_at,
                user_id if viewed_at else None
            ))
            delivery_id = cursor.lastrowid
            if acknowledged_at:
                conn.execute("""
                    INSERT INTO acknowledgements
                    (
                        source_table, source_id, user_id,
                        acknowledgement_type, acknowledged_at, active
                    )
                    VALUES (
                        'staff_notice_deliveries', ?, ?,
                        'Acknowledgement', ?, 1
                    )
                """, (delivery_id, user_id, acknowledged_at))
            conn.commit()
            return delivery_id
        finally:
            conn.close()

    def create_dashboard_matrix(self, user_id):
        deliveries = []
        states = (
            (None, None),
            (None, None),
            ("2026-08-10T07:00:00Z", None),
            (
                "2026-08-10T07:00:00Z",
                "2026-08-14T07:00:00Z"
            ),
            (
                "2026-08-10T07:00:00Z",
                "2026-08-16T07:00:00Z"
            ),
            (
                "2026-08-10T07:00:00Z",
                "2026-08-11T07:00:00Z"
            )
        )
        for index, (viewed_at, acknowledged_at) in enumerate(
            states,
            start=1
        ):
            fixture = self.create_one_time_notice(
                audience_rule_type="Selected Individual",
                selected_user_id=user_id,
                title=f"Personal Notice {index}"
            )
            deliveries.append(self.assign_delivery(
                fixture,
                user_id,
                viewed_at=viewed_at,
                acknowledged_at=acknowledged_at
            ))
        return deliveries

    def dashboard_response(self, user_id, role):
        client = self.login_session(user_id, role)
        patches = (
            mock.patch.object(app, "get_dashboard_stats", return_value={
                "outstanding_action_count": 0,
                "outstanding_actions": [],
                "notes_to_review": 0,
                "open_incidents": 0,
                "recent_activity": 0
            }),
            mock.patch.object(app, "get_management_inbox", return_value={
                "high_priority_actions": [],
                "notes_to_review_list": [],
                "recent_incidents": [],
                "recent_activity_list": []
            }),
            mock.patch.object(app, "get_active_shift_staff", return_value=[]),
            mock.patch.object(app, "get_manager_alerts", return_value=[])
        )
        with patches[0], patches[1], patches[2], patches[3]:
            return client.get("/dashboard")

    def snapshot(self):
        conn = self.open_database()
        try:
            tables = (
                "users",
                "shift_staff",
                "staff_notice_audience_eligibility_periods",
                "staff_notice_occurrences",
                "staff_notice_deliveries",
                "staff_notice_delivery_history",
                "acknowledgements",
                "activity_log"
            )
            return {
                table: [
                    tuple(row)
                    for row in conn.execute(
                        f"SELECT * FROM {table} ORDER BY 1"
                    ).fetchall()
                ]
                for table in tables
            }
        finally:
            conn.close()

    def test_each_management_role_gets_personal_recipient_dashboard(self):
        for user_id, role in (
            (1, "Admin"),
            (2, "Program Manager"),
            (3, "Director")
        ):
            with self.subTest(role=role):
                fixture = self.create_one_time_notice(
                    audience_rule_type="Selected Individual",
                    selected_user_id=user_id,
                    title=f"{role} Personal Notice"
                )
                self.assign_delivery(fixture, user_id)

                response = self.dashboard_response(user_id, role)

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"My Staff Notices", response.data)
                self.assertIn(
                    f"{role} Personal Notice".encode(),
                    response.data
                )
                self.assertIn(b"Not Viewed", response.data)
                self.assertIn(b"View All Staff Notices", response.data)

    def test_management_dashboard_limits_five_counts_and_shows_statuses(self):
        self.create_dashboard_matrix(1)

        response = self.dashboard_response(1, "Admin")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count("/staff-notices/delivery/"), 5)
        self.assertIn("<strong>Outstanding:</strong>\n            3", html)
        self.assertIn("Not Viewed", html)
        self.assertIn(
            "Viewed – Awaiting Acknowledgement",
            html
        )
        self.assertIn("Acknowledged", html)
        self.assertIn("Acknowledged Late", html)

    def test_dashboard_render_does_not_record_view(self):
        fixture = self.create_one_time_notice(
            audience_rule_type="Selected Individual",
            selected_user_id=1
        )
        delivery_id = self.assign_delivery(fixture, 1)

        self.dashboard_response(1, "Admin")

        conn = self.open_database()
        try:
            delivery = conn.execute("""
                SELECT first_viewed_at_utc
                FROM staff_notice_deliveries
                WHERE delivery_id = ?
            """, (delivery_id,)).fetchone()
            viewed_events = conn.execute("""
                SELECT COUNT(*) AS event_count
                FROM activity_log
                WHERE activity_type = 'staff_notice_viewed'
            """).fetchone()["event_count"]
        finally:
            conn.close()
        self.assertIsNone(delivery["first_viewed_at_utc"])
        self.assertEqual(viewed_events, 0)

    def test_management_recipient_detail_view_and_acknowledgement(self):
        fixture = self.create_one_time_notice(
            audience_rule_type="Selected Individual",
            selected_user_id=1
        )
        delivery_id = self.assign_delivery(fixture, 1)
        client = self.login_session(1, "Admin")

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            detail = client.get(
                f"/staff-notices/delivery/{delivery_id}"
            )
            acknowledgement = client.post(
                f"/staff-notices/delivery/{delivery_id}/acknowledge",
                data={"acknowledge": "yes"}
            )

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(acknowledgement.status_code, 302)
        conn = self.open_database()
        try:
            delivery = conn.execute("""
                SELECT * FROM staff_notice_deliveries
                WHERE delivery_id = ?
            """, (delivery_id,)).fetchone()
            active_acknowledgements = conn.execute("""
                SELECT COUNT(*) AS acknowledgement_count
                FROM acknowledgements
                WHERE source_table = 'staff_notice_deliveries'
                  AND source_id = ?
                  AND user_id = 1
                  AND active = 1
            """, (delivery_id,)).fetchone()["acknowledgement_count"]
        finally:
            conn.close()
        self.assertEqual(
            delivery["first_viewed_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertEqual(delivery["viewed_by_user_id"], 1)
        self.assertEqual(active_acknowledgements, 1)

    def test_login_and_dashboard_reconciliation_are_idempotent(self):
        fixture = self.create_one_time_notice(
            audience_rule_type="All Support Workers"
        )
        client = app.app.test_client()

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            login_response = client.post(
                "/login",
                data={"username": "worker", "password": "password"}
            )
        self.assertEqual(login_response.status_code, 302)
        first_snapshot = self.snapshot()
        conn = self.open_database()
        try:
            delivery_count = conn.execute("""
                SELECT COUNT(*) AS delivery_count
                FROM staff_notice_deliveries
                WHERE occurrence_id = ?
                  AND user_id = 4
            """, (fixture["occurrence_id"],)).fetchone()["delivery_count"]
        finally:
            conn.close()
        self.assertEqual(delivery_count, 1)

        with mock.patch.object(
            app,
            "auto_sign_on_user",
            return_value=(10, 1)
        ):
            dashboard_response = client.get("/dashboard")
        self.assertEqual(dashboard_response.status_code, 302)
        self.assertEqual(self.snapshot(), first_snapshot)

    def test_user_creation_reconciles_eligible_but_not_ineligible_role(self):
        fixture = self.create_one_time_notice(
            audience_rule_type="All Support Workers"
        )
        client = self.login_session(1, "Admin")

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            eligible = client.post("/user/new", data={
                "username": "new-worker",
                "full_name": "New Worker",
                "role": "Support Worker",
                "password": "password"
            })
            ineligible = client.post("/user/new", data={
                "username": "new-consultant",
                "full_name": "New Consultant",
                "role": "Behaviour Consultant",
                "password": "password"
            })
        self.assertEqual(eligible.status_code, 302)
        self.assertEqual(ineligible.status_code, 302)

        conn = self.open_database()
        try:
            rows = conn.execute("""
                SELECT
                    u.username,
                    ep.eligible_from_at_utc,
                    d.delivery_id
                FROM users u
                LEFT JOIN staff_notice_audience_eligibility_periods ep
                    ON ep.user_id = u.user_id
                   AND ep.audience_id = ?
                LEFT JOIN staff_notice_deliveries d
                    ON d.user_id = u.user_id
                   AND d.occurrence_id = ?
                WHERE u.username IN ('new-worker', 'new-consultant')
                ORDER BY u.username
            """, (
                fixture["audience_id"],
                fixture["occurrence_id"]
            )).fetchall()
        finally:
            conn.close()
        by_username = {row["username"]: row for row in rows}
        self.assertIsNone(by_username["new-consultant"]["delivery_id"])
        self.assertEqual(
            by_username["new-worker"]["eligible_from_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertIsNotNone(by_username["new-worker"]["delivery_id"])

    def test_user_management_authorization_uses_current_database_role(self):
        stale_worker_session = self.login_session(1, "Support Worker")
        allowed = stale_worker_session.get("/user/new")
        self.assertEqual(allowed.status_code, 200)

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
        stale_admin_session = self.login_session(1, "Admin")

        denied = stale_admin_session.get("/user/new")

        self.assertEqual(denied.status_code, 403)

    def test_activation_deactivation_and_role_changes_use_authoritative_time(self):
        fixture = self.create_one_time_notice(
            audience_rule_type="All Support Workers"
        )
        client = self.login_session(1, "Admin")
        activate_at = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
        deactivate_at = datetime(2026, 8, 13, 18, 0, tzinfo=timezone.utc)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=activate_at
        ):
            activated = client.post("/user/edit/6", data={
                "username": "inactive",
                "full_name": "Inactive Consultant",
                "role": "Support Worker",
                "active": "on"
            })
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=deactivate_at
        ):
            deactivated = client.post("/user/edit/6", data={
                "username": "inactive",
                "full_name": "Inactive Consultant",
                "role": "Support Worker"
            })
        self.assertEqual(activated.status_code, 302)
        self.assertEqual(deactivated.status_code, 302)

        conn = self.open_database()
        try:
            period = conn.execute("""
                SELECT *
                FROM staff_notice_audience_eligibility_periods
                WHERE audience_id = ?
                  AND user_id = 6
            """, (fixture["audience_id"],)).fetchone()
            delivery = conn.execute("""
                SELECT *
                FROM staff_notice_deliveries
                WHERE occurrence_id = ?
                  AND user_id = 6
            """, (fixture["occurrence_id"],)).fetchone()
        finally:
            conn.close()
        self.assertEqual(
            period["eligible_from_at_utc"],
            "2026-08-12T18:00:00Z"
        )
        self.assertEqual(
            period["eligible_until_at_utc"],
            "2026-08-13T18:00:00Z"
        )
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery["requirement_status"], "Required")
        self.assertEqual(delivery["recipient_access"], 1)

    def test_role_change_adds_then_removes_eligibility(self):
        fixture = self.create_one_time_notice(
            audience_rule_type="Selected Role",
            selected_role="Behaviour Consultant"
        )
        client = self.login_session(1, "Admin")
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.FIXED_NOW
        ):
            added = client.post("/user/edit/4", data={
                "username": "worker",
                "full_name": "Support Worker",
                "role": "Behaviour Consultant",
                "active": "on"
            })
        remove_at = datetime(2026, 8, 13, 18, 0, tzinfo=timezone.utc)
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=remove_at
        ):
            removed = client.post("/user/edit/4", data={
                "username": "worker",
                "full_name": "Support Worker",
                "role": "Support Worker",
                "active": "on"
            })
        self.assertEqual(added.status_code, 302)
        self.assertEqual(removed.status_code, 302)
        conn = self.open_database()
        try:
            period = conn.execute("""
                SELECT *
                FROM staff_notice_audience_eligibility_periods
                WHERE audience_id = ?
                  AND user_id = 4
            """, (fixture["audience_id"],)).fetchone()
        finally:
            conn.close()
        self.assertEqual(
            period["eligible_from_at_utc"],
            self.FIXED_TIMESTAMP
        )
        self.assertEqual(
            period["eligible_until_at_utc"],
            "2026-08-13T18:00:00Z"
        )

    def test_repeated_user_edit_is_write_free(self):
        client = self.login_session(1, "Admin")
        before = self.snapshot()

        response = client.post("/user/edit/4", data={
            "username": "worker",
            "full_name": "Support Worker",
            "role": "Support Worker",
            "active": "on"
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot(), before)

    def test_stale_user_edit_is_rejected_without_lifecycle_writes(self):
        client = self.login_session(1, "Admin")
        conn = self.open_database()
        try:
            activity_count = conn.execute("""
                SELECT COUNT(*) AS activity_count
                FROM activity_log
            """).fetchone()["activity_count"]
        finally:
            conn.close()
        real_get_db = app.get_db

        def stale_get_db():
            return _StaleUserConnection(real_get_db(), 4)

        with mock.patch.object(app, "get_db", side_effect=stale_get_db):
            response = client.post("/user/edit/4", data={
                "username": "worker",
                "full_name": "Submitted Edit",
                "role": "Behaviour Consultant",
                "active": "on"
            })

        self.assertEqual(response.status_code, 409)
        conn = self.open_database()
        try:
            user = conn.execute("""
                SELECT full_name, role, active
                FROM users
                WHERE user_id = 4
            """).fetchone()
            new_activity_count = conn.execute("""
                SELECT COUNT(*) AS activity_count
                FROM activity_log
            """).fetchone()["activity_count"]
            eligibility_count = conn.execute("""
                SELECT COUNT(*) AS eligibility_count
                FROM staff_notice_audience_eligibility_periods
                WHERE user_id = 4
            """).fetchone()["eligibility_count"]
        finally:
            conn.close()
        self.assertEqual(user["full_name"], "Concurrent Edit")
        self.assertEqual(user["role"], "Support Worker")
        self.assertEqual(user["active"], 1)
        self.assertEqual(new_activity_count, activity_count)
        self.assertEqual(eligibility_count, 0)

    def test_reconciliation_and_activity_failures_roll_back_user_creation(self):
        client = self.login_session(1, "Admin")
        for failure_target in ("reconciliation", "activity"):
            with self.subTest(failure_target=failure_target):
                before = self.snapshot()
                if failure_target == "reconciliation":
                    patcher = mock.patch.object(
                        app,
                        "reconcile_staff_notice_user_lifecycle_in_transaction",
                        side_effect=RuntimeError("controlled reconciliation")
                    )
                else:
                    patcher = mock.patch.object(
                        app,
                        "log_activity",
                        side_effect=RuntimeError("controlled activity")
                    )
                with patcher:
                    response = client.post("/user/new", data={
                        "username": f"failed-{failure_target}",
                        "full_name": "Failed User",
                        "role": "Support Worker",
                        "password": "password"
                    })
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.snapshot(), before)

    def test_commit_failure_rolls_back_user_role_change(self):
        client = self.login_session(1, "Admin")
        before = self.snapshot()
        real_get_db = app.get_db

        def wrapped_get_db():
            return _CommitFailureConnection(real_get_db())

        with mock.patch.object(app, "get_db", side_effect=wrapped_get_db):
            response = client.post("/user/edit/4", data={
                "username": "worker",
                "full_name": "Support Worker",
                "role": "Behaviour Consultant",
                "active": "on"
            })

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.snapshot(), before)

    def test_specialist_dashboard_never_auto_assigns_shift(self):
        client = self.login_session(5, "Behaviour Consultant")
        with mock.patch.object(
            app,
            "auto_sign_on_user",
            side_effect=AssertionError("specialist was auto-assigned")
        ) as auto_sign_on:
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/staff-notices"))
        auto_sign_on.assert_not_called()
        conn = self.open_database()
        try:
            assignment_count = conn.execute("""
                SELECT COUNT(*) AS assignment_count
                FROM shift_staff
                WHERE user_id = 5
            """).fetchone()["assignment_count"]
        finally:
            conn.close()
        self.assertEqual(assignment_count, 0)

    def test_support_worker_dashboard_retains_auto_sign_on(self):
        client = self.login_session(4, "Support Worker")
        with mock.patch.object(
            app,
            "auto_sign_on_user",
            return_value=(27, 1)
        ) as auto_sign_on:
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/shift/27"))
        auto_sign_on.assert_called_once_with(4)


if __name__ == "__main__":
    unittest.main()
