import sqlite3
import tempfile
import time
import unittest
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from werkzeug.datastructures import MultiDict

import add_staff_notices_tables as staff_notice_schema
import app


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


class CleanupFailureConnection:

    def __init__(
        self,
        connection,
        primary_error,
        failure_sql="INSERT INTO staff_notice_audience_rules"
    ):
        self.connection = connection
        self.primary_error = primary_error
        self.failure_sql = failure_sql
        self.rollback_error = RuntimeError("controlled rollback failure")
        self.close_error = RuntimeError("controlled close failure")
        self.rollback_attempted = False
        self.close_attempted = False

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def execute(self, sql, parameters=()):
        if self.failure_sql in sql:
            raise self.primary_error

        return self.connection.execute(sql, parameters)

    def commit(self):
        return self.connection.commit()

    def rollback(self):
        self.rollback_attempted = True
        self.connection.rollback()
        raise self.rollback_error

    def close(self):
        self.close_attempted = True
        self.connection.close()
        raise self.close_error


class StaffNoticeDraftManagementTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.original_database_name = app.DB_NAME
        self.original_testing = app.app.config.get("TESTING")
        self.addCleanup(self.restore_application_state)
        self.database_path = str(
            Path(self.temporary_directory.name) / "draft_management.db"
        )
        app.DB_NAME = self.database_path
        app.app.config["TESTING"] = True
        self.create_database()
        self.client = app.app.test_client()

    def restore_application_state(self):
        app.DB_NAME = self.original_database_name
        app.app.config["TESTING"] = self.original_testing

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
                    client_id INTEGER,
                    shift_date TEXT,
                    shift_type TEXT
                );

                CREATE TABLE shift_staff (
                    shift_staff_id INTEGER PRIMARY KEY,
                    shift_id INTEGER,
                    user_id INTEGER,
                    active INTEGER
                );

                CREATE TABLE activity_log (
                    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_datetime TEXT,
                    activity_class TEXT NOT NULL,
                    activity_type TEXT NOT NULL,
                    user_id INTEGER,
                    client_id INTEGER,
                    shift_id INTEGER,
                    related_table TEXT,
                    related_id INTEGER,
                    summary TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    success INTEGER DEFAULT 1
                );

                CREATE TABLE acknowledgements (
                    acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_table TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL
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
                (5, "Consultant", "Behaviour Consultant", 1),
                (6, "Inactive Worker", "Support Worker", 0)
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

    def login(self, user_id, role):
        with self.client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = role
            session_data["full_name"] = role
            session_data["last_activity"] = time.time()

    def valid_form(self, **changes):
        values = {
            "title": "Policy Reminder",
            "notice_text": "Review the current operational policy.",
            "priority": "Important",
            "audience_rule_types": ["All Support Workers"]
        }
        values.update(changes)
        return values

    def form_multidict(self, values=None, **changes):
        form = MultiDict()

        for key, value in (values or self.valid_form()).items():
            if isinstance(value, (list, tuple)):
                for item in value:
                    form.add(key, item)
            else:
                form.add(key, value)

        for key, value in changes.items():
            form.setlist(
                key,
                list(value) if isinstance(value, (list, tuple)) else [value]
            )

        return form

    def valid_payload(self, **changes):
        payload = {
            "title": "Policy Reminder",
            "notice_text": "Review the current operational policy.",
            "priority": "Important",
            "audience_rules": [
                {"rule_type": "All Support Workers"}
            ]
        }
        payload.update(changes)
        return payload

    def create_draft(self, payload=None, actor_user_id=1):
        return app.create_staff_notice_draft(
            payload or self.valid_payload(),
            actor_user_id
        )

    def table_count(self, table_name):
        conn = self.open_database()
        return conn.execute(
            f'SELECT COUNT(*) FROM "{table_name}"'
        ).fetchone()[0]

    def rows(self, table_name):
        conn = self.open_database()
        return [
            tuple(row)
            for row in conn.execute(
                f'SELECT * FROM "{table_name}" ORDER BY 1'
            ).fetchall()
        ]

    def aggregate_snapshot(self):
        return {
            table_name: self.rows(table_name)
            for table_name in STAFF_NOTICE_TABLES
        } | {
            "acknowledgements": self.rows("acknowledgements"),
            "activity_log": self.rows("activity_log")
        }

    def assert_no_operational_rows(self):
        for table_name in (
            "staff_notice_audience_eligibility_periods",
            "staff_notice_occurrences",
            "staff_notice_deliveries",
            "staff_notice_delivery_history",
            "acknowledgements"
        ):
            self.assertEqual(self.table_count(table_name), 0, table_name)

    def edit_form(self, notice_id, **changes):
        conn = self.open_database()
        notice = conn.execute(
            "SELECT * FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()
        values = self.valid_form(
            expected_updated_at_utc=(
                notice["updated_at_utc"] or notice["created_at_utc"]
            )
        )
        values.update(changes)
        return values

    def test_management_roles_can_access_every_draft_route(self):
        for user_id, role in (
            (1, "Admin"),
            (2, "Program Manager"),
            (3, "Director")
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                self.assertEqual(
                    self.client.get("/staff-notices/manage").status_code,
                    200
                )
                self.assertEqual(
                    self.client.get("/staff-notices/new").status_code,
                    200
                )
                create_response = self.client.post(
                    "/staff-notices/new",
                    data=self.valid_form(
                        title=f"{role} Draft"
                    )
                )
                self.assertEqual(create_response.status_code, 302)
                notice_id = int(
                    create_response.headers["Location"].rstrip("/").rsplit(
                        "/",
                        1
                    )[1]
                )
                self.assertEqual(
                    self.client.get(
                        f"/staff-notices/manage/{notice_id}"
                    ).status_code,
                    200
                )
                self.assertEqual(
                    self.client.get(
                        f"/staff-notices/manage/{notice_id}/edit"
                    ).status_code,
                    200
                )
                self.assertEqual(
                    self.client.post(
                        f"/staff-notices/manage/{notice_id}/edit",
                        data=self.edit_form(
                            notice_id,
                            title=f"{role} Updated Draft"
                        )
                    ).status_code,
                    302
                )
                self.assertEqual(
                    self.client.post(
                        f"/staff-notices/manage/{notice_id}/draft/deactivate"
                    ).status_code,
                    302
                )

    def test_management_dashboard_links_to_staff_notice_management(self):
        self.login(1, "Admin")

        dashboard_stats = {
            "outstanding_action_count": 0,
            "outstanding_actions": [],
            "notes_to_review": 0,
            "open_incidents": 0,
            "recent_activity": 0
        }
        management_inbox = {
            "notes_to_review_list": [],
            "recent_incidents": [],
            "recent_activity_list": []
        }

        with (
            mock.patch.object(
                app,
                "get_dashboard_stats",
                return_value=dashboard_stats
            ),
            mock.patch.object(
                app,
                "get_management_inbox",
                return_value=management_inbox
            ),
            mock.patch.object(
                app,
                "get_active_shift_staff",
                return_value=[]
            ),
            mock.patch.object(
                app,
                "get_manager_alerts",
                return_value=[]
            ),
            mock.patch.object(
                app,
                "_load_management_staff_notice_dashboard",
                return_value={
                    "dashboard": [],
                    "outstanding_count": 0
                }
            )
        ):
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Staff Notices", response.data)
        self.assertIn(
            b'href="/staff-notices/manage"',
            response.data
        )

    def test_unauthenticated_routes_redirect_to_login(self):
        notice_id = self.create_draft()
        requests = (
            ("get", "/staff-notices/manage"),
            ("get", "/staff-notices/new"),
            ("post", "/staff-notices/new"),
            ("get", f"/staff-notices/manage/{notice_id}"),
            ("get", f"/staff-notices/manage/{notice_id}/edit"),
            ("post", f"/staff-notices/manage/{notice_id}/edit"),
            (
                "post",
                f"/staff-notices/manage/{notice_id}/draft/deactivate"
            )
        )

        for method, path in requests:
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.headers["Location"])

    def test_support_worker_is_denied_without_database_changes(self):
        notice_id = self.create_draft()
        before = self.aggregate_snapshot()
        self.login(4, "Support Worker")

        requests = (
            ("get", "/staff-notices/manage", None),
            ("get", "/staff-notices/new", None),
            ("post", "/staff-notices/new", self.valid_form()),
            ("get", f"/staff-notices/manage/{notice_id}", None),
            ("get", f"/staff-notices/manage/{notice_id}/edit", None),
            (
                "post",
                f"/staff-notices/manage/{notice_id}/edit",
                self.edit_form(notice_id)
            ),
            (
                "post",
                f"/staff-notices/manage/{notice_id}/draft/deactivate",
                None
            )
        )

        for method, path, data in requests:
            with self.subTest(path=path, method=method):
                response = getattr(self.client, method)(path, data=data)
                self.assertEqual(response.status_code, 403)

        self.assertEqual(self.aggregate_snapshot(), before)

    def test_valid_web_creation_uses_single_creation_activity(self):
        self.login(1, "Admin")
        response = self.client.post(
            "/staff-notices/new",
            data=self.valid_form(),
            follow_redirects=False
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.table_count("staff_notices"), 1)
        self.assertEqual(self.table_count("staff_notice_audiences"), 1)
        self.assertEqual(self.table_count("staff_notice_audience_rules"), 1)
        conn = self.open_database()
        activities = conn.execute("""
            SELECT activity_type
            FROM activity_log
            WHERE activity_class = 'STAFF_NOTICE'
        """).fetchall()
        self.assertEqual(
            [row["activity_type"] for row in activities],
            ["staff_notice_draft_created"]
        )
        self.assert_no_operational_rows()

    def test_invalid_creation_preserves_safe_form_values_and_writes_nothing(self):
        self.login(1, "Admin")
        response = self.client.post(
            "/staff-notices/new",
            data=self.valid_form(
                priority="Urgent",
                client_id="1",
                audience_rule_types=[],
                notice_text="Preserve this entered notice text"
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Preserve this entered notice text", response.data)
        self.assertRegex(response.data, br'value="Urgent"\s+selected')
        self.assertRegex(response.data, br'value="1"\s+selected')
        self.assertEqual(self.table_count("staff_notices"), 0)
        self.assertEqual(self.table_count("activity_log"), 0)

    def test_duplicate_create_scalars_are_rejected_before_writes(self):
        self.login(1, "Admin")

        for field_name, values in (
            ("title", ["First title", "Second title"]),
            ("title", ["Same title", "Same title"]),
            (
                "effective_start_local",
                ["2026-08-01T10:00", "2026-08-01T10:00"]
            )
        ):
            with self.subTest(field_name=field_name, values=values):
                form = self.form_multidict()
                form.setlist(field_name, values)
                before = self.aggregate_snapshot()
                response = self.client.post(
                    "/staff-notices/new",
                    data=form
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn(b"must be submitted once", response.data)
                self.assertEqual(self.aggregate_snapshot(), before)

    def test_form_field_classification_covers_the_complete_contract(self):
        list_fields = {
            "audience_rule_types",
            "selected_roles",
            "selected_user_ids",
            "shift_types",
            "weekdays"
        }
        expected_scalar_fields = (
            app.STAFF_NOTICE_MANAGEMENT_FORM_KEYS
            - app.STAFF_NOTICE_CHECKBOX_FORM_KEYS
            - list_fields
            - {"expected_updated_at_utc"}
        )

        self.assertEqual(
            app.STAFF_NOTICE_SCALAR_FORM_KEYS,
            expected_scalar_fields
        )
        self.assertNotIn(
            "expected_updated_at_utc",
            app.STAFF_NOTICE_CREATE_FORM_KEYS
        )

    def test_create_rejects_edit_token_and_server_fields_at_route(self):
        self.login(1, "Admin")

        for field_name in (
            "expected_updated_at_utc",
            "unexpected",
            "status",
            "draft_active",
            "created_by_user_id"
        ):
            with self.subTest(field_name=field_name):
                form = self.form_multidict()
                form.add(field_name, "malicious")
                before = self.aggregate_snapshot()
                response = self.client.post(
                    "/staff-notices/new",
                    data=form
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.aggregate_snapshot(), before)

    def test_checkbox_omission_and_exact_one_are_saved(self):
        self.login(1, "Admin")
        omitted_response = self.client.post(
            "/staff-notices/new",
            data=self.valid_form(title="Omitted Checkboxes")
        )
        enabled_response = self.client.post(
            "/staff-notices/new",
            data=self.valid_form(
                title="Enabled Checkboxes",
                until_withdrawn="1",
                schedule_enabled="1",
                occurrence_basis="Calendar",
                recurrence_pattern="Daily",
                shift_applicability="None"
            )
        )

        self.assertEqual(omitted_response.status_code, 302)
        self.assertEqual(enabled_response.status_code, 302)
        conn = self.open_database()
        omitted = conn.execute("""
            SELECT notice_id, until_withdrawn
            FROM staff_notices
            WHERE title = 'Omitted Checkboxes'
        """).fetchone()
        enabled = conn.execute("""
            SELECT until_withdrawn
            FROM staff_notices
            WHERE title = 'Enabled Checkboxes'
        """).fetchone()
        enabled_schedule_count = conn.execute("""
            SELECT COUNT(*)
            FROM staff_notice_schedules s
            JOIN staff_notices n ON s.notice_id = n.notice_id
            WHERE n.title = 'Enabled Checkboxes'
        """).fetchone()[0]
        omitted_schedule_count = conn.execute("""
            SELECT COUNT(*)
            FROM staff_notice_schedules
            WHERE notice_id = ?
        """, (omitted["notice_id"],)).fetchone()[0]
        self.assertEqual(omitted["until_withdrawn"], 0)
        self.assertEqual(omitted_schedule_count, 0)
        self.assertEqual(enabled["until_withdrawn"], 1)
        self.assertEqual(enabled_schedule_count, 1)

    def test_malformed_checkbox_values_are_rejected_and_not_checked(self):
        self.login(1, "Admin")

        for field_name in ("until_withdrawn", "schedule_enabled"):
            for values in (
                [""],
                ["0"],
                ["false"],
                ["unexpected"],
                ["1", "1"]
            ):
                with self.subTest(field_name=field_name, values=values):
                    form = self.form_multidict()
                    form.setlist(field_name, values)
                    before = self.aggregate_snapshot()
                    response = self.client.post(
                        "/staff-notices/new",
                        data=form
                    )

                    self.assertEqual(response.status_code, 400)
                    self.assertIn(b"must have the value 1", response.data)
                    self.assertEqual(self.aggregate_snapshot(), before)
                    checkbox_tag = re.search(
                        rb'<input[^>]*name="'
                        + field_name.encode()
                        + rb'"[^>]*>',
                        response.data,
                        re.DOTALL
                    )
                    self.assertIsNotNone(checkbox_tag)
                    self.assertNotIn(b"checked", checkbox_tag.group(0))

    def test_list_distinguishes_active_and_inactive_drafts(self):
        active_id = self.create_draft()
        inactive_id = self.create_draft({
            **self.valid_payload(),
            "title": "Inactive Draft"
        })
        app.deactivate_staff_notice_draft(inactive_id, 1)
        self.login(1, "Admin")
        response = self.client.get("/staff-notices/manage")

        self.assertEqual(response.status_code, 200)
        rows = re.findall(br"<tr[^>]*>.*?</tr>", response.data, re.DOTALL)
        active_rows = [row for row in rows if b"Policy Reminder" in row]
        inactive_rows = [row for row in rows if b"Inactive Draft" in row]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(len(inactive_rows), 1)
        self.assertIn(b"status-active", active_rows[0])
        self.assertIn(b">Active Draft<", active_rows[0])
        self.assertRegex(active_rows[0], br">\s*Edit\s*<")
        self.assertIn(b"status-inactive", inactive_rows[0])
        self.assertIn(b">Inactive Draft<", inactive_rows[0])
        self.assertNotRegex(inactive_rows[0], br">\s*Edit\s*<")
        self.assertIn(str(active_id).encode(), active_rows[0])

    def test_detail_displays_parent_audience_schedule_and_no_publish_action(self):
        notice_id = self.create_draft({
            **self.valid_payload(),
            "schedule": {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "Selected Shift Types",
                "shift_types": ["Day", "Afternoon"],
                "weekdays": [0, 2]
            }
        })
        self.login(1, "Admin")
        response = self.client.get(
            f"/staff-notices/manage/{notice_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Policy Reminder", response.data)
        self.assertIn(b"All Support Workers", response.data)
        self.assertIn(b"Selected Weekdays", response.data)
        self.assertIn(b"Monday", response.data)
        self.assertNotIn(b"Publish", response.data)

    def test_management_get_routes_are_read_only(self):
        notice_id = self.create_draft({
            **self.valid_payload(),
            "schedule": {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "Selected Shift Types",
                "shift_types": ["Day"],
                "weekdays": [1]
            }
        })
        self.login(1, "Admin")

        for path in (
            "/staff-notices/manage",
            f"/staff-notices/manage/{notice_id}",
            f"/staff-notices/manage/{notice_id}/edit"
        ):
            with self.subTest(path=path):
                before = self.aggregate_snapshot()
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.aggregate_snapshot(), before)

    def test_edit_template_contract_and_complete_state_restoration(self):
        notice_id = self.create_draft({
            **self.valid_payload(),
            "title": "Saved Scalar",
            "notice_text": "Saved notice text",
            "priority": "Urgent",
            "client_id": 1,
            "effective_start_local": "2026-08-01T10:00",
            "until_withdrawn": True,
            "audience_rules": [
                {
                    "rule_type": "Selected Role",
                    "role_name": "Behaviour Consultant"
                },
                {"rule_type": "Selected Individual", "user_id": 4}
            ],
            "schedule": {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "Selected Shift Types",
                "recurrence_anchor_date": "2026-08-01",
                "shift_types": ["Day", "Overnight"],
                "weekdays": [0, 4]
            }
        })
        conn = self.open_database()
        token = conn.execute("""
            SELECT created_at_utc
            FROM staff_notices
            WHERE notice_id = ?
        """, (notice_id,)).fetchone()[0]
        self.login(1, "Admin")
        response = self.client.get(
            f"/staff-notices/manage/{notice_id}/edit"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request.path, (
            f"/staff-notices/manage/{notice_id}/edit"
        ))
        self.assertEqual(response.data.count(b'<form method="post">'), 1)
        self.assertNotRegex(response.data, br'<form method="post"[^>]*action=')
        self.assertEqual(
            response.data.count(b'name="expected_updated_at_utc"'),
            1
        )
        self.assertIn(f'value="{token}"'.encode(), response.data)
        self.assertIn(b"Edit Staff Notice Draft", response.data)
        self.assertIn(b"Save Draft", response.data)
        self.assertNotIn(b"Create Staff Notice Draft", response.data)
        self.assertIn(b'value="Saved Scalar"', response.data)
        self.assertIn(b">Saved notice text</textarea>", response.data)
        self.assertRegex(response.data, br'value="Urgent"\s+selected')
        until_withdrawn_input = re.search(
            br'<input[^>]*name="until_withdrawn"[^>]*>',
            response.data,
            re.DOTALL
        )
        schedule_enabled_input = re.search(
            br'<input[^>]*name="schedule_enabled"[^>]*>',
            response.data,
            re.DOTALL
        )
        self.assertIsNotNone(until_withdrawn_input)
        self.assertIsNotNone(schedule_enabled_input)
        self.assertIn(b"checked", until_withdrawn_input.group(0))
        self.assertIn(b"checked", schedule_enabled_input.group(0))
        self.assertRegex(
            response.data,
            br'value="Behaviour Consultant"\s+selected'
        )
        self.assertRegex(response.data, br'value="4"\s+selected')
        self.assertRegex(response.data, br'value="Day"\s+checked')
        self.assertRegex(response.data, br'value="Overnight"\s+checked')
        self.assertRegex(response.data, br'value="0"\s+checked')
        self.assertRegex(response.data, br'value="4"\s+checked')
        effective_input = re.search(
            br'<input[^>]*name="effective_start_local"[^>]*>',
            response.data,
            re.DOTALL
        )
        self.assertIsNotNone(effective_input)
        self.assertIn(
            b'value="2026-08-01T10:00"',
            effective_input.group(0)
        )
        self.assertIn(b'value="2026-08-01"', response.data)

        timed_notice_id = self.create_draft({
            **self.valid_payload(),
            "title": "Timed Draft",
            "effective_start_local": "2026-08-01T09:00",
            "expires_local": "2026-08-02T09:00",
            "schedule": {
                "occurrence_basis": "One Time",
                "recurrence_pattern": "Once",
                "shift_applicability": "None",
                "one_time_due_local": "2026-08-02T09:00"
            }
        })
        timed_response = self.client.get(
            f"/staff-notices/manage/{timed_notice_id}/edit"
        )
        self.assertEqual(timed_response.status_code, 200)
        timed_effective_input = re.search(
            br'<input[^>]*name="effective_start_local"[^>]*>',
            timed_response.data,
            re.DOTALL
        )
        expires_input = re.search(
            br'<input[^>]*name="expires_local"[^>]*>',
            timed_response.data,
            re.DOTALL
        )
        due_input = re.search(
            br'<input[^>]*name="one_time_due_local"[^>]*>',
            timed_response.data,
            re.DOTALL
        )
        self.assertIsNotNone(timed_effective_input)
        self.assertIsNotNone(expires_input)
        self.assertIsNotNone(due_input)
        self.assertIn(
            b'value="2026-08-01T09:00"',
            timed_effective_input.group(0)
        )
        self.assertIn(
            b'value="2026-08-02T09:00"',
            expires_input.group(0)
        )
        self.assertIn(
            b'value="2026-08-02T09:00"',
            due_input.group(0)
        )

    def test_missing_detail_and_edit_return_not_found(self):
        self.login(1, "Admin")
        self.assertEqual(
            self.client.get("/staff-notices/manage/999").status_code,
            404
        )
        self.assertEqual(
            self.client.get(
                "/staff-notices/manage/999/edit"
            ).status_code,
            404
        )

    def test_valid_edit_replaces_complete_child_configuration(self):
        notice_id = self.create_draft({
            **self.valid_payload(),
            "audience_rules": [{"rule_type": "Core Organization"}],
            "schedule": {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None"
            }
        })
        self.login(1, "Admin")
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/edit",
            data=self.edit_form(
                notice_id,
                title="Updated Draft",
                audience_rule_types=[
                    "Selected Role",
                    "Selected Individual"
                ],
                selected_roles=["Behaviour Consultant"],
                selected_user_ids=["4"],
                schedule_enabled="1",
                occurrence_basis="Shift",
                recurrence_pattern="Selected Weekdays",
                shift_applicability="Selected Shift Types",
                shift_types=["Day", "Overnight"],
                weekdays=["1", "4"]
            )
        )

        self.assertEqual(response.status_code, 302)
        conn = self.open_database()
        notice = conn.execute(
            "SELECT * FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()
        rules = conn.execute("""
            SELECT ar.rule_type, ar.role_name, ar.user_id
            FROM staff_notice_audience_rules ar
            JOIN staff_notice_audiences a
                ON ar.audience_id = a.audience_id
            WHERE a.notice_id = ?
            ORDER BY ar.rule_type
        """, (notice_id,)).fetchall()
        shift_types = conn.execute("""
            SELECT st.shift_type
            FROM staff_notice_schedule_shift_types st
            JOIN staff_notice_schedules s
                ON st.schedule_id = s.schedule_id
            WHERE s.notice_id = ?
            ORDER BY st.shift_type
        """, (notice_id,)).fetchall()
        weekdays = conn.execute("""
            SELECT sw.weekday_number
            FROM staff_notice_schedule_weekdays sw
            JOIN staff_notice_schedules s
                ON sw.schedule_id = s.schedule_id
            WHERE s.notice_id = ?
            ORDER BY sw.weekday_number
        """, (notice_id,)).fetchall()
        self.assertEqual(notice["title"], "Updated Draft")
        self.assertEqual(notice["updated_by_user_id"], 1)
        self.assertEqual(
            [tuple(row) for row in rules],
            [
                ("Selected Individual", None, 4),
                ("Selected Role", "Behaviour Consultant", None)
            ]
        )
        self.assertEqual(
            [row[0] for row in shift_types],
            ["Day", "Overnight"]
        )
        self.assertEqual([row[0] for row in weekdays], [1, 4])
        self.assertEqual(
            self.table_count("staff_notice_audiences"),
            1
        )
        self.assertEqual(self.table_count("staff_notice_schedules"), 1)
        self.assert_no_operational_rows()

    def test_multiple_edits_do_not_duplicate_children(self):
        notice_id = self.create_draft()
        self.login(1, "Admin")

        for title in ("First Edit", "Second Edit"):
            response = self.client.post(
                f"/staff-notices/manage/{notice_id}/edit",
                data=self.edit_form(
                    notice_id,
                    title=title,
                    audience_rule_types=["Selected Role"],
                    selected_roles=["Support Worker"]
                )
            )
            self.assertEqual(response.status_code, 302)

        self.assertEqual(self.table_count("staff_notice_audiences"), 1)
        self.assertEqual(self.table_count("staff_notice_audience_rules"), 1)

    def test_duplicate_edit_scalars_and_tokens_are_rejected(self):
        notice_id = self.create_draft()
        self.login(1, "Admin")

        for field_name, values in (
            ("title", ["First edit", "Second edit"]),
            ("title", ["Same edit", "Same edit"]),
            (
                "expected_updated_at_utc",
                [
                    self.edit_form(notice_id)["expected_updated_at_utc"],
                    self.edit_form(notice_id)["expected_updated_at_utc"]
                ]
            ),
            ("expires_local", ["", ""])
        ):
            with self.subTest(field_name=field_name, values=values):
                form = self.form_multidict(self.edit_form(notice_id))
                form.setlist(field_name, values)
                before = self.aggregate_snapshot()
                response = self.client.post(
                    f"/staff-notices/manage/{notice_id}/edit",
                    data=form
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn(b"must be submitted once", response.data)
                self.assertEqual(self.aggregate_snapshot(), before)

    def test_edit_requires_token_and_rejects_server_fields_at_route(self):
        notice_id = self.create_draft()
        self.login(1, "Admin")

        missing_token_form = self.form_multidict(
            self.edit_form(notice_id)
        )
        missing_token_form.poplist("expected_updated_at_utc")
        before = self.aggregate_snapshot()
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/edit",
            data=missing_token_form
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.aggregate_snapshot(), before)

        blank_token_form = self.form_multidict(self.edit_form(notice_id))
        blank_token_form.setlist("expected_updated_at_utc", ["   "])
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/edit",
            data=blank_token_form
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.aggregate_snapshot(), before)

        for field_name in (
            "unexpected",
            "status",
            "draft_active",
            "updated_by_user_id"
        ):
            with self.subTest(field_name=field_name):
                form = self.form_multidict(self.edit_form(notice_id))
                form.add(field_name, "malicious")
                before = self.aggregate_snapshot()
                response = self.client.post(
                    f"/staff-notices/manage/{notice_id}/edit",
                    data=form
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.aggregate_snapshot(), before)

    def test_child_insert_failure_rolls_back_entire_edit(self):
        notice_id = self.create_draft()
        before = self.aggregate_snapshot()
        conn = self.open_database()
        conn.execute("""
            CREATE TRIGGER fail_rule_insert
            BEFORE INSERT ON staff_notice_audience_rules
            BEGIN
                SELECT RAISE(ABORT, 'controlled child failure');
            END
        """)
        conn.commit()
        self.login(1, "Admin")
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/edit",
            data=self.edit_form(notice_id, title="Must Roll Back")
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_parent_update_failure_rolls_back_entire_edit(self):
        notice_id = self.create_draft()
        before = self.aggregate_snapshot()
        conn = self.open_database()
        conn.execute("""
            CREATE TRIGGER fail_notice_update
            BEFORE UPDATE ON staff_notices
            BEGIN
                SELECT RAISE(ABORT, 'controlled parent failure');
            END
        """)
        conn.commit()
        self.login(1, "Admin")
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/edit",
            data=self.edit_form(notice_id, title="Must Roll Back")
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_activity_failure_rolls_back_entire_edit(self):
        notice_id = self.create_draft()
        before = self.aggregate_snapshot()
        conn = self.open_database()
        conn.execute("""
            CREATE TRIGGER fail_update_activity
            BEFORE INSERT ON activity_log
            WHEN NEW.activity_type = 'staff_notice_draft_updated'
            BEGIN
                SELECT RAISE(ABORT, 'controlled activity failure');
            END
        """)
        conn.commit()
        self.login(1, "Admin")
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/edit",
            data=self.edit_form(notice_id, title="Must Roll Back")
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_stale_edit_is_rejected_without_changes(self):
        notice_id = self.create_draft()
        before = self.aggregate_snapshot()
        self.login(1, "Admin")
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/edit",
            data=self.valid_form(
                expected_updated_at_utc="2000-01-01T00:00:00Z"
            )
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn(b"changed after the form was opened", response.data)
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_edit_token_advances_when_clock_has_not_advanced(self):
        notice_id = self.create_draft()
        conn = self.open_database()
        original_token = conn.execute(
            "SELECT created_at_utc FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()[0]
        same_instant = app.parse_staff_notice_utc_datetime(original_token)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=same_instant
        ):
            app.update_staff_notice_draft(
                notice_id,
                self.valid_payload(title="First Edit"),
                1,
                original_token
            )

        updated_token = conn.execute(
            "SELECT updated_at_utc FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()[0]
        self.assertGreater(updated_token, original_token)

        with self.assertRaises(app.StaffNoticeStaleEditError):
            app.update_staff_notice_draft(
                notice_id,
                self.valid_payload(title="Stale Edit"),
                1,
                original_token
            )

    def test_edit_removes_obsolete_draft_eligibility_before_audience(self):
        notice_id = self.create_draft()
        conn = self.open_database()
        audience_id = conn.execute("""
            SELECT audience_id
            FROM staff_notice_audiences
            WHERE notice_id = ?
        """, (notice_id,)).fetchone()[0]
        conn.execute("""
            INSERT INTO staff_notice_audience_eligibility_periods
            (
                audience_id,
                user_id,
                eligible_from_at_utc,
                eligibility_source_summary,
                created_at_utc
            )
            VALUES (?, 4, '2026-01-01T00:00:00Z', 'Test fixture',
                    '2026-01-01T00:00:00Z')
        """, (audience_id,))
        conn.commit()
        token = conn.execute(
            "SELECT created_at_utc FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()[0]

        app.update_staff_notice_draft(
            notice_id,
            self.valid_payload(title="Replacement Audience"),
            1,
            token
        )

        self.assertEqual(
            self.table_count("staff_notice_audience_eligibility_periods"),
            0
        )
        self.assertEqual(self.table_count("staff_notice_audiences"), 1)

    def test_valid_create_and_edit_leave_draft_eligibility_empty(self):
        notice_id = self.create_draft()
        self.assertEqual(
            self.table_count("staff_notice_audience_eligibility_periods"),
            0
        )
        token = self.edit_form(notice_id)["expected_updated_at_utc"]

        app.update_staff_notice_draft(
            notice_id,
            self.valid_payload(title="Eligibility-Free Edit"),
            1,
            token
        )

        self.assertEqual(
            self.table_count("staff_notice_audience_eligibility_periods"),
            0
        )
        self.assert_no_operational_rows()

    def test_inactive_and_published_notices_cannot_be_edited(self):
        inactive_id = self.create_draft()
        published_id = self.create_draft({
            **self.valid_payload(),
            "title": "Published Record"
        })
        conn = self.open_database()
        conn.execute(
            "UPDATE staff_notices SET draft_active = 0 WHERE notice_id = ?",
            (inactive_id,)
        )
        conn.execute("""
            UPDATE staff_notices
            SET status = 'Published',
                effective_start_at_utc = '2026-01-01T08:00:00Z',
                expires_at_utc = '2026-02-01T08:00:00Z',
                published_at_utc = '2025-12-31T08:00:00Z'
            WHERE notice_id = ?
        """, (published_id,))
        conn.commit()
        self.login(1, "Admin")

        for notice_id in (inactive_id, published_id):
            with self.subTest(notice_id=notice_id):
                self.assertEqual(
                    self.client.get(
                        f"/staff-notices/manage/{notice_id}/edit"
                    ).status_code,
                    409
                )

    def test_audience_validation_rules(self):
        invalid_payloads = (
            self.valid_payload(audience_rules=[]),
            self.valid_payload(audience_rules=[{
                "rule_type": "Selected Role",
                "role_name": "Invented Role"
            }]),
            self.valid_payload(
                audience_rules=[{"rule_type": "Applicable Shift Staff"}]
            )
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_management_draft(payload)

    def test_every_supported_audience_type_accepts_valid_data(self):
        rules = (
            {"rule_type": "Core Organization"},
            {"rule_type": "All Support Workers"},
            {
                "rule_type": "Selected Role",
                "role_name": "Behaviour Consultant"
            },
            {"rule_type": "Selected Individual", "user_id": 4}
        )

        for rule in rules:
            with self.subTest(rule=rule):
                normalized = app.validate_staff_notice_management_draft(
                    self.valid_payload(audience_rules=[rule])
                )
                self.assertEqual(normalized["audience_rules"][0], {
                    "rule_type": rule["rule_type"],
                    "role_name": rule.get("role_name"),
                    "user_id": rule.get("user_id")
                })

        normalized = app.validate_staff_notice_management_draft(
            self.valid_payload(
                audience_rules=[{"rule_type": "Applicable Shift Staff"}],
                schedule={
                    "occurrence_basis": "Shift",
                    "recurrence_pattern": "Daily",
                    "shift_applicability": "Every Shift"
                }
            )
        )
        self.assertEqual(
            normalized["audience_rules"][0]["rule_type"],
            "Applicable Shift Staff"
        )

    def test_nonexistent_and_inactive_selected_people_are_rejected(self):
        for user_id in (6, 999):
            with self.subTest(user_id=user_id):
                payload = self.valid_payload(audience_rules=[{
                    "rule_type": "Selected Individual",
                    "user_id": user_id
                }])

                with self.assertRaisesRegex(ValueError, "exist and be active"):
                    app.create_staff_notice_draft(payload, 1)

    def test_schedule_validation_rejects_required_children_and_bad_values(self):
        schedules = (
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "None"
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Selected Shift Types"
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Interval Days",
                "shift_applicability": "None"
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "None",
                "weekdays": [7]
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Selected Shift Types",
                "shift_types": ["Evening"]
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "one_time_due_local": "2026-08-01T10:00"
            }
        )

        for schedule in schedules:
            with self.subTest(schedule=schedule):
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_management_draft(
                        self.valid_payload(schedule=schedule)
                    )

    def test_schedule_validation_rejects_chronological_conflicts(self):
        payloads = (
            self.valid_payload(
                effective_start_local="2026-08-02T10:00",
                schedule={
                    "occurrence_basis": "One Time",
                    "recurrence_pattern": "Once",
                    "shift_applicability": "None",
                    "one_time_due_local": "2026-08-01T10:00"
                }
            ),
            self.valid_payload(
                expires_local="2026-08-01T10:00",
                schedule={
                    "occurrence_basis": "One Time",
                    "recurrence_pattern": "Once",
                    "shift_applicability": "None",
                    "one_time_due_local": "2026-08-02T10:00"
                }
            ),
            self.valid_payload(
                effective_start_local="2026-08-02T10:00",
                schedule={
                    "occurrence_basis": "Calendar",
                    "recurrence_pattern": "Once",
                    "shift_applicability": "None",
                    "specific_calendar_date": "2026-08-01"
                }
            )
        )

        for payload in payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_management_draft(payload)

    def test_due_equal_to_expiry_is_allowed_and_later_due_is_rejected(self):
        self.login(1, "Admin")
        common_values = {
            "notice_text": "Boundary test",
            "priority": "Normal",
            "effective_start_local": "2026-08-01T09:00",
            "expires_local": "2026-08-02T09:00",
            "audience_rule_types": ["All Support Workers"],
            "schedule_enabled": "1",
            "occurrence_basis": "One Time",
            "recurrence_pattern": "Once",
            "shift_applicability": "None"
        }
        equal_response = self.client.post(
            "/staff-notices/new",
            data={
                **common_values,
                "title": "Equal Boundary",
                "one_time_due_local": "2026-08-02T09:00"
            }
        )
        before_late = self.aggregate_snapshot()
        late_response = self.client.post(
            "/staff-notices/new",
            data={
                **common_values,
                "title": "Late Boundary",
                "one_time_due_local": "2026-08-02T09:01"
            }
        )

        self.assertEqual(equal_response.status_code, 302)
        self.assertEqual(late_response.status_code, 400)
        self.assertIn(b"cannot be after", late_response.data)
        self.assertEqual(self.aggregate_snapshot(), before_late)

    def test_valid_one_time_and_recurring_schedules_save_no_occurrences(self):
        payloads = (
            self.valid_payload(schedule={
                "occurrence_basis": "One Time",
                "recurrence_pattern": "Once",
                "shift_applicability": "None",
                "one_time_due_local": "2026-08-01T10:00"
            }),
            self.valid_payload(schedule={
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "None",
                "weekdays": [0, 3]
            })
        )

        for payload in payloads:
            app.validate_staff_notice_management_draft(payload)
            self.create_draft(payload)

        self.assertEqual(self.table_count("staff_notice_schedules"), 2)
        self.assert_no_operational_rows()

    def test_specific_shift_client_must_match_notice_client(self):
        payload = self.valid_payload(
            client_id=1,
            schedule={
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Specific Shift",
                "specific_shift_client_id": 2,
                "specific_shift_date": "2026-08-01",
                "specific_shift_type": "Day"
            }
        )

        with self.assertRaisesRegex(ValueError, "must match"):
            app.validate_staff_notice_management_draft(payload)

    def test_invalid_post_preserves_multiselect_and_datetime_values(self):
        self.login(1, "Admin")
        response = self.client.post(
            "/staff-notices/new",
            data=MultiDict([
                ("title", "Preserved Title"),
                ("notice_text", "Preserved Text"),
                ("priority", "Important"),
                ("audience_rule_types", "Selected Role"),
                ("selected_roles", "Support Worker"),
                ("schedule_enabled", "1"),
                ("occurrence_basis", "Calendar"),
                ("recurrence_pattern", "Selected Weekdays"),
                ("shift_applicability", "None"),
                ("effective_start_local", "2026-08-01T10:00"),
                ("weekdays", "0"),
                ("weekdays", "9")
            ])
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Preserved Title", response.data)
        self.assertIn(b"Preserved Text", response.data)
        self.assertIn(b"2026-08-01T10:00", response.data)
        self.assertRegex(
            response.data,
            br'value="Support Worker"\s+selected'
        )
        self.assertRegex(response.data, br'value="0"\s+checked')
        self.assertEqual(self.table_count("staff_notices"), 0)

    def test_deactivation_retains_record_and_children_and_logs_activity(self):
        notice_id = self.create_draft({
            **self.valid_payload(),
            "schedule": {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None"
            }
        })
        before_rules = self.rows("staff_notice_audience_rules")
        before_schedule = self.rows("staff_notice_schedules")
        self.login(1, "Admin")
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/draft/deactivate"
        )

        self.assertEqual(response.status_code, 302)
        conn = self.open_database()
        notice = conn.execute(
            "SELECT * FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()
        self.assertEqual(notice["draft_active"], 0)
        self.assertEqual(self.rows("staff_notice_audience_rules"), before_rules)
        self.assertEqual(self.rows("staff_notice_schedules"), before_schedule)
        self.assertEqual(
            conn.execute("""
                SELECT COUNT(*)
                FROM activity_log
                WHERE activity_type = 'staff_notice_draft_deactivated'
            """).fetchone()[0],
            1
        )
        self.assert_no_operational_rows()

    def test_repeated_and_non_draft_deactivation_are_rejected(self):
        inactive_id = self.create_draft()
        published_id = self.create_draft({
            **self.valid_payload(),
            "title": "Published"
        })
        app.deactivate_staff_notice_draft(inactive_id, 1)
        conn = self.open_database()
        conn.execute("""
            UPDATE staff_notices
            SET status = 'Published',
                effective_start_at_utc = '2026-01-01T08:00:00Z',
                expires_at_utc = '2026-02-01T08:00:00Z',
                published_at_utc = '2025-12-31T08:00:00Z'
            WHERE notice_id = ?
        """, (published_id,))
        conn.commit()
        self.login(1, "Admin")

        for notice_id in (inactive_id, published_id):
            with self.subTest(notice_id=notice_id):
                response = self.client.post(
                    f"/staff-notices/manage/{notice_id}/draft/deactivate"
                )
                self.assertEqual(response.status_code, 409)

    def test_deactivation_activity_failure_rolls_back(self):
        notice_id = self.create_draft()
        before = self.aggregate_snapshot()
        conn = self.open_database()
        conn.execute("""
            CREATE TRIGGER fail_deactivate_activity
            BEFORE INSERT ON activity_log
            WHEN NEW.activity_type = 'staff_notice_draft_deactivated'
            BEGIN
                SELECT RAISE(ABORT, 'controlled activity failure');
            END
        """)
        conn.commit()
        self.login(1, "Admin")

        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/draft/deactivate"
        )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b"controlled activity failure", response.data)
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_deactivation_timestamp_advances_after_same_second_edit(self):
        notice_id = self.create_draft()
        conn = self.open_database()
        created_at = conn.execute("""
            SELECT created_at_utc
            FROM staff_notices
            WHERE notice_id = ?
        """, (notice_id,)).fetchone()[0]
        same_instant = app.parse_staff_notice_utc_datetime(created_at)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=same_instant
        ):
            app.update_staff_notice_draft(
                notice_id,
                self.valid_payload(title="Same-Second Edit"),
                1,
                created_at
            )
            edited_at = conn.execute("""
                SELECT updated_at_utc
                FROM staff_notices
                WHERE notice_id = ?
            """, (notice_id,)).fetchone()[0]
            app.deactivate_staff_notice_draft(notice_id, 1)

        deactivated_at = conn.execute("""
            SELECT updated_at_utc
            FROM staff_notices
            WHERE notice_id = ?
        """, (notice_id,)).fetchone()[0]
        self.assertGreater(
            app.parse_staff_notice_utc_datetime(deactivated_at),
            app.parse_staff_notice_utc_datetime(edited_at)
        )
        self.assertEqual(
            conn.execute("""
                SELECT COUNT(*)
                FROM activity_log
                WHERE activity_type = 'staff_notice_draft_deactivated'
            """).fetchone()[0],
            1
        )

    def test_deactivation_parent_update_failure_rolls_back(self):
        notice_id = self.create_draft({
            **self.valid_payload(),
            "schedule": {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None"
            }
        })
        conn = self.open_database()
        conn.execute("""
            CREATE TRIGGER fail_deactivation_update
            BEFORE UPDATE ON staff_notices
            BEGIN
                SELECT RAISE(ABORT, 'controlled deactivation update failure');
            END
        """)
        conn.commit()
        before = self.aggregate_snapshot()
        self.login(1, "Admin")
        response = self.client.post(
            f"/staff-notices/manage/{notice_id}/draft/deactivate"
        )

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(
            b"controlled deactivation update failure",
            response.data
        )
        self.assertEqual(self.aggregate_snapshot(), before)
        self.assertEqual(
            conn.execute("""
                SELECT COUNT(*)
                FROM activity_log
                WHERE activity_type = 'staff_notice_draft_deactivated'
            """).fetchone()[0],
            0
        )

    def test_deactivation_preserves_primary_cleanup_exception(self):
        notice_id = self.create_draft()
        before = self.aggregate_snapshot()
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        primary_error = RuntimeError("controlled deactivation failure")
        wrapper = CleanupFailureConnection(
            conn,
            primary_error,
            failure_sql="UPDATE staff_notices"
        )

        with mock.patch.object(app, "get_db", return_value=wrapper):
            with self.assertRaises(RuntimeError) as caught:
                app.deactivate_staff_notice_draft(notice_id, 1)

        self.assertIs(caught.exception, primary_error)
        self.assertIs(
            caught.exception.staff_notice_rollback_error,
            wrapper.rollback_error
        )
        self.assertIs(
            caught.exception.staff_notice_close_error,
            wrapper.close_error
        )
        self.assertTrue(wrapper.rollback_attempted)
        self.assertTrue(wrapper.close_attempted)
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_edit_preserves_primary_exception_when_cleanup_also_fails(self):
        notice_id = self.create_draft()
        before = self.aggregate_snapshot()
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        primary_error = RuntimeError("controlled primary failure")
        wrapper = CleanupFailureConnection(conn, primary_error)

        with mock.patch.object(app, "get_db", return_value=wrapper):
            with self.assertRaises(RuntimeError) as caught:
                app.update_staff_notice_draft(
                    notice_id,
                    self.valid_payload(audience_rules=[{
                        "rule_type": "Selected Role",
                        "role_name": "Support Worker"
                    }]),
                    1,
                    self.rows("staff_notices")[0][13]
                )

        self.assertIs(caught.exception, primary_error)
        self.assertIs(
            caught.exception.staff_notice_rollback_error,
            wrapper.rollback_error
        )
        self.assertIs(
            caught.exception.staff_notice_close_error,
            wrapper.close_error
        )
        self.assertEqual(self.aggregate_snapshot(), before)

    def test_unknown_and_server_controlled_form_fields_are_rejected(self):
        for field_name in (
            "unexpected",
            "status",
            "draft_active",
            "created_by_user_id",
            "published_at_utc",
            "activity_type"
        ):
            with self.subTest(field_name=field_name):
                form = MultiDict(self.valid_form())
                form.add(field_name, "malicious")

                with self.assertRaisesRegex(
                    ValueError,
                    "Unexpected Staff Notice form field"
                ):
                    app.build_staff_notice_draft_payload_from_form(form)


if __name__ == "__main__":
    unittest.main()
