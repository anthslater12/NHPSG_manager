import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

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

PROHIBITED_TOP_LEVEL_FIELDS = (
    "status",
    "draft_active",
    "version_number",
    "created_by_user_id",
    "updated_by_user_id",
    "updated_at_utc",
    "published_by_user_id",
    "published_at_utc",
    "withdrawn_by_user_id",
    "withdrawn_at_utc",
    "withdrawal_reason",
    "replaces_notice_id",
    "replaced_by_user_id",
    "replaced_at_utc",
    "replacement_reason",
    "occurrence_id",
    "delivery_id",
    "first_viewed_at_utc",
    "acknowledgement_id",
    "activity_class",
    "activity_type",
    "activity_user_id",
    "activity_summary",
    "activity_details",
    "related_table",
    "related_id",
    "success"
)


class StaffNoticeDraftCreationTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)

        self.original_database_name = app.DB_NAME
        self.addCleanup(self.restore_database_name)

        self.database_path = str(
            Path(self.temporary_directory.name) / "draft_creation.db"
        )
        app.DB_NAME = self.database_path
        self.create_database()

    def restore_database_name(self):
        app.DB_NAME = self.original_database_name

    def create_database(self):
        conn = sqlite3.connect(self.database_path)

        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL
                );

                CREATE TABLE clients (
                    client_id INTEGER PRIMARY KEY,
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
                INSERT INTO users (user_id, role, active)
                VALUES (?, ?, ?)
            """, (
                (1, "Admin", 1),
                (2, "Program Manager", 1),
                (3, "Director", 1),
                (4, "Support Worker", 1),
                (5, "Other Role", 1),
                (6, "Admin", 0),
                (7, "Support Worker", 0)
            ))
            conn.executemany("""
                INSERT INTO clients (client_id, active)
                VALUES (?, ?)
            """, (
                (1, 1),
                (2, 0)
            ))
            conn.commit()
        finally:
            conn.close()

    def minimal_payload(self):
        return {
            "title": "  Important Update  ",
            "notice_text": (
                "  Sensitive care-plan details: TOKEN-7F3A.  "
            )
        }

    def open_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def table_count(self, table_name):
        conn = self.open_database()
        return conn.execute(
            f'SELECT COUNT(*) AS count FROM "{table_name}"'
        ).fetchone()["count"]

    def assert_exception_graph_acyclic(self, error):
        def visit(current_error, path):
            if current_error is None:
                return

            self.assertIsInstance(current_error, BaseException)
            current_id = id(current_error)
            self.assertNotIn(current_id, path)
            next_path = path | {current_id}
            visit(current_error.__cause__, next_path)
            visit(current_error.__context__, next_path)

        visit(error, set())

    def assert_no_draft_aggregate(self):
        for table_name in STAFF_NOTICE_TABLES:
            self.assertEqual(
                self.table_count(table_name),
                0,
                table_name
            )

        self.assertEqual(self.table_count("activity_log"), 0)
        self.assertEqual(self.table_count("acknowledgements"), 0)

    def install_failure_trigger(self, table_name):
        conn = self.open_database()
        conn.execute(f"""
            CREATE TRIGGER fail_{table_name}_insert
            BEFORE INSERT ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'controlled insert failure');
            END
        """)
        conn.commit()

    def test_minimal_draft_creation_and_activity_log(self):
        fixed_now = datetime(2026, 8, 1, 19, 30, tzinfo=timezone.utc)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=fixed_now
        ):
            notice_id = app.create_staff_notice_draft(
                self.minimal_payload(),
                1
            )

        conn = self.open_database()
        notice = conn.execute(
            "SELECT * FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()
        activity = conn.execute(
            "SELECT * FROM activity_log"
        ).fetchone()

        self.assertEqual(notice_id, notice["notice_id"])
        self.assertEqual(notice["title"], "Important Update")
        self.assertEqual(
            notice["notice_text"],
            "Sensitive care-plan details: TOKEN-7F3A."
        )
        self.assertEqual(notice["priority"], "Normal")
        self.assertIsNone(notice["client_id"])
        self.assertEqual(notice["status"], "Draft")
        self.assertEqual(notice["draft_active"], 1)
        self.assertEqual(notice["until_withdrawn"], 0)
        self.assertEqual(notice["version_number"], 1)
        self.assertEqual(notice["created_by_user_id"], 1)
        self.assertEqual(notice["created_at_utc"], "2026-08-01T19:30:00Z")
        for lifecycle_column in (
            "replaces_notice_id",
            "updated_by_user_id",
            "updated_at_utc",
            "published_by_user_id",
            "published_at_utc",
            "withdrawn_by_user_id",
            "withdrawn_at_utc",
            "withdrawal_reason",
            "replaced_by_user_id",
            "replaced_at_utc",
            "replacement_reason"
        ):
            self.assertIsNone(
                notice[lifecycle_column],
                lifecycle_column
            )
        self.assertEqual(self.table_count("staff_notice_audiences"), 0)
        self.assertEqual(self.table_count("staff_notice_schedules"), 0)
        self.assertEqual(activity["activity_class"], "STAFF_NOTICE")
        self.assertEqual(
            activity["activity_type"],
            "staff_notice_draft_created"
        )
        self.assertEqual(activity["user_id"], 1)
        self.assertIsNone(activity["client_id"])
        self.assertIsNone(activity["shift_id"])
        self.assertEqual(activity["related_table"], "staff_notices")
        self.assertEqual(activity["related_id"], notice_id)
        self.assertEqual(activity["success"], 1)
        self.assertEqual(
            activity["summary"],
            "Staff Notice draft created: Important Update"
        )
        self.assertEqual(
            activity["details"],
            "Priority: Normal; Audience rules: 0; "
            "Schedule configured: No"
        )
        self.assertNotIn(notice["notice_text"], activity["summary"])
        self.assertNotIn(notice["notice_text"], activity["details"])
        self.assertEqual(self.table_count("activity_log"), 1)

    def test_until_withdrawn_true_is_persisted(self):
        payload = self.minimal_payload()
        payload["until_withdrawn"] = True

        notice_id = app.create_staff_notice_draft(payload, 1)
        conn = self.open_database()
        notice = conn.execute(
            "SELECT * FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()

        self.assertEqual(notice["until_withdrawn"], 1)
        self.assertIsNone(notice["expires_at_utc"])
        self.assertEqual(notice["status"], "Draft")
        self.assertEqual(notice["draft_active"], 1)
        self.assertEqual(notice["version_number"], 1)
        self.assertEqual(notice["created_by_user_id"], 1)

    def test_active_client_and_vancouver_times_are_persisted(self):
        payload = self.minimal_payload()
        payload.update({
            "client_id": 1,
            "priority": " Urgent ",
            "effective_start_local": "2026-01-15T09:30",
            "expires_local": "2026-07-15T09:30"
        })

        notice_id = app.create_staff_notice_draft(payload, 2)
        conn = self.open_database()
        notice = conn.execute(
            "SELECT * FROM staff_notices WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()
        activity = conn.execute(
            "SELECT * FROM activity_log WHERE related_id = ?",
            (notice_id,)
        ).fetchone()

        self.assertEqual(notice["client_id"], 1)
        self.assertEqual(activity["client_id"], 1)
        self.assertEqual(notice["priority"], "Urgent")
        self.assertEqual(
            notice["effective_start_at_utc"],
            "2026-01-15T17:30:00Z"
        )
        self.assertEqual(
            notice["expires_at_utc"],
            "2026-07-15T16:30:00Z"
        )

    def test_payload_is_not_mutated(self):
        payload = self.minimal_payload()
        payload["audience_rules"] = [{
            "rule_type": "Selected Role",
            "role_name": " Support Worker ",
            "user_id": None
        }]
        original = deepcopy(payload)

        app.validate_staff_notice_draft(payload)

        self.assertEqual(payload, original)

    def test_each_audience_rule_type_is_created(self):
        rules = (
            {
                "rule_type": "Core Organization",
                "role_name": None,
                "user_id": None
            },
            {
                "rule_type": "All Support Workers",
                "role_name": None,
                "user_id": None
            },
            {
                "rule_type": "Selected Role",
                "role_name": "Behaviour Consultant",
                "user_id": None
            },
            {
                "rule_type": "Selected Individual",
                "role_name": None,
                "user_id": 4
            },
            {
                "rule_type": "Applicable Shift Staff",
                "role_name": None,
                "user_id": None
            }
        )

        for rule in rules:
            with self.subTest(rule_type=rule["rule_type"]):
                payload = self.minimal_payload()
                payload["audience_rules"] = [rule]

                if rule["rule_type"] == "Applicable Shift Staff":
                    payload["schedule"] = {
                        "occurrence_basis": "Shift",
                        "recurrence_pattern": "Daily",
                        "shift_applicability": "Every Shift"
                    }

                notice_id = app.create_staff_notice_draft(payload, 1)
                conn = self.open_database()
                stored = conn.execute("""
                    SELECT ar.*
                    FROM staff_notice_audience_rules ar
                    JOIN staff_notice_audiences a
                        ON ar.audience_id = a.audience_id
                    WHERE a.notice_id = ?
                """, (notice_id,)).fetchone()

                self.assertEqual(stored["rule_type"], rule["rule_type"])
                self.assertEqual(stored["role_name"], rule["role_name"])
                self.assertEqual(stored["user_id"], rule["user_id"])

    def test_selected_role_without_current_user_succeeds(self):
        payload = self.minimal_payload()
        payload["audience_rules"] = [{
            "rule_type": "Selected Role",
            "role_name": "Behaviour Consultant"
        }]

        notice_id = app.create_staff_notice_draft(payload, 1)

        self.assertGreater(notice_id, 0)

    def test_empty_audience_collection_creates_no_audience(self):
        for empty_value in (None, [], ()):
            with self.subTest(empty_value=empty_value):
                payload = self.minimal_payload()
                payload["audience_rules"] = empty_value
                app.create_staff_notice_draft(payload, 1)

        self.assertEqual(self.table_count("staff_notice_audiences"), 0)
        self.assertEqual(self.table_count("staff_notice_audience_rules"), 0)

    def test_complete_schedule_and_children_are_related(self):
        payload = self.minimal_payload()
        payload["schedule"] = {
            "occurrence_basis": "Shift",
            "recurrence_pattern": "Selected Weekdays",
            "shift_applicability": "Selected Shift Types",
            "recurrence_anchor_date": "2026-08-03",
            "shift_types": ["Day", "Afternoon"],
            "weekdays": [0, 2]
        }

        notice_id = app.create_staff_notice_draft(payload, 1)
        conn = self.open_database()
        schedule = conn.execute(
            "SELECT * FROM staff_notice_schedules WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()
        shift_types = conn.execute("""
            SELECT shift_type
            FROM staff_notice_schedule_shift_types
            WHERE schedule_id = ?
            ORDER BY schedule_shift_type_id
        """, (schedule["schedule_id"],)).fetchall()
        weekdays = conn.execute("""
            SELECT weekday_number
            FROM staff_notice_schedule_weekdays
            WHERE schedule_id = ?
            ORDER BY schedule_weekday_id
        """, (schedule["schedule_id"],)).fetchall()

        self.assertEqual(schedule["occurrence_basis"], "Shift")
        self.assertEqual(
            schedule["recurrence_pattern"],
            "Selected Weekdays"
        )
        self.assertEqual(
            [row["shift_type"] for row in shift_types],
            ["Day", "Afternoon"]
        )
        self.assertEqual(
            [row["weekday_number"] for row in weekdays],
            [0, 2]
        )

    def test_schema_valid_incomplete_schedules_are_saved(self):
        schedules = (
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Interval Days",
                "shift_applicability": "None",
                "interval_days": None
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Selected Shift Types",
                "shift_types": []
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "None",
                "weekdays": []
            }
        )

        for schedule in schedules:
            with self.subTest(schedule=schedule):
                payload = self.minimal_payload()
                payload["schedule"] = schedule
                notice_id = app.create_staff_notice_draft(payload, 1)
                self.assertGreater(notice_id, 0)

    def test_specific_shift_and_one_time_due_values(self):
        specific_payload = self.minimal_payload()
        specific_payload["client_id"] = 1
        specific_payload["schedule"] = {
            "occurrence_basis": "Shift",
            "recurrence_pattern": "Once",
            "shift_applicability": "Specific Shift",
            "specific_shift_client_id": 1,
            "specific_shift_date": "2026-08-10",
            "specific_shift_type": "Overnight"
        }
        specific_id = app.create_staff_notice_draft(specific_payload, 1)

        one_time_payload = self.minimal_payload()
        one_time_payload["schedule"] = {
            "occurrence_basis": "One Time",
            "recurrence_pattern": "Once",
            "shift_applicability": "None",
            "one_time_due_local": "2026-08-10T21:00"
        }
        one_time_id = app.create_staff_notice_draft(one_time_payload, 1)

        conn = self.open_database()
        specific = conn.execute(
            "SELECT * FROM staff_notice_schedules WHERE notice_id = ?",
            (specific_id,)
        ).fetchone()
        one_time = conn.execute(
            "SELECT * FROM staff_notice_schedules WHERE notice_id = ?",
            (one_time_id,)
        ).fetchone()

        self.assertEqual(specific["specific_shift_client_id"], 1)
        self.assertEqual(specific["specific_shift_type"], "Overnight")
        self.assertEqual(
            one_time["one_time_due_at_utc"],
            "2026-08-11T04:00:00Z"
        )

    def test_all_allowed_schedule_combinations_validate(self):
        combinations = (
            ("One Time", "Once", "None"),
            ("Calendar", "Once", "None"),
            ("Calendar", "Daily", "None"),
            ("Calendar", "Interval Days", "None"),
            ("Calendar", "Selected Weekdays", "None"),
            ("Shift", "Once", "Every Shift"),
            ("Shift", "Once", "Selected Shift Types"),
            ("Shift", "Once", "Specific Shift"),
            ("Shift", "Daily", "Every Shift"),
            ("Shift", "Daily", "Selected Shift Types"),
            ("Shift", "Interval Days", "Every Shift"),
            ("Shift", "Interval Days", "Selected Shift Types"),
            ("Shift", "Selected Weekdays", "Every Shift"),
            (
                "Shift",
                "Selected Weekdays",
                "Selected Shift Types"
            )
        )

        for basis, recurrence, applicability in combinations:
            with self.subTest(
                basis=basis,
                recurrence=recurrence,
                applicability=applicability
            ):
                schedule = {
                    "occurrence_basis": basis,
                    "recurrence_pattern": recurrence,
                    "shift_applicability": applicability
                }

                if basis == "Calendar" and recurrence == "Once":
                    schedule["specific_calendar_date"] = "2026-08-01"

                if applicability == "Specific Shift":
                    schedule.update({
                        "specific_shift_client_id": 1,
                        "specific_shift_date": "2026-08-01",
                        "specific_shift_type": "Day"
                    })

                payload = self.minimal_payload()
                payload["schedule"] = schedule
                normalized = app.validate_staff_notice_draft(payload)
                self.assertEqual(
                    normalized["schedule"]["occurrence_basis"],
                    basis
                )

    def test_non_mapping_and_unknown_keys_are_rejected(self):
        for invalid_payload in (None, "draft", [], 1):
            with self.subTest(payload=invalid_payload):
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_draft(invalid_payload)

        for field_name in PROHIBITED_TOP_LEVEL_FIELDS:
            with self.subTest(field_name=field_name):
                payload = self.minimal_payload()
                payload[field_name] = 1
                with self.assertRaisesRegex(ValueError, "Unknown"):
                    app.validate_staff_notice_draft(payload)

        payload = self.minimal_payload()
        payload["presentation_only"] = "value"
        with self.assertRaisesRegex(ValueError, "Unknown"):
            app.validate_staff_notice_draft(payload)

    def test_unknown_nested_keys_are_rejected(self):
        payload = self.minimal_payload()
        payload["audience_rules"] = [{
            "rule_type": "Core Organization",
            "unexpected": True
        }]

        with self.assertRaisesRegex(ValueError, "Unknown"):
            app.validate_staff_notice_draft(payload)

    def test_invalid_audience_and_schedule_container_types(self):
        invalid_values = (
            ("audience_rules", {"rule_type": "Core Organization"}),
            ("audience_rules", "Core Organization"),
            ("schedule", []),
            ("schedule", "Calendar")
        )

        for field_name, value in invalid_values:
            with self.subTest(field_name=field_name, value=value):
                payload = self.minimal_payload()
                payload[field_name] = value

                with self.assertRaises(ValueError):
                    app.create_staff_notice_draft(payload, 1)

                self.assert_no_draft_aggregate()

        payload = self.minimal_payload()
        payload["schedule"] = {
            "occurrence_basis": "One Time",
            "recurrence_pattern": "Once",
            "shift_applicability": "None",
            "unexpected": True
        }

        with self.assertRaisesRegex(ValueError, "Unknown"):
            app.validate_staff_notice_draft(payload)

    def test_core_field_validation(self):
        invalid_changes = (
            ("title", "  "),
            ("title", 4),
            ("notice_text", ""),
            ("notice_text", object()),
            ("priority", "Critical"),
            ("client_id", True),
            ("client_id", "1"),
            ("client_id", 0),
            ("until_withdrawn", 1),
            ("until_withdrawn", "true")
        )

        for field_name, value in invalid_changes:
            with self.subTest(field_name=field_name, value=value):
                payload = self.minimal_payload()
                payload[field_name] = value
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_draft(payload)

    def test_identifier_boundaries_are_enforced_for_each_field(self):
        invalid_values = (False, -1, 1.0, "1", 0)

        for field_name in (
            "client_id",
            "audience user_id",
            "specific_shift_client_id"
        ):
            for value in invalid_values:
                with self.subTest(field_name=field_name, value=value):
                    payload = self.minimal_payload()

                    if field_name == "client_id":
                        payload["client_id"] = value
                    elif field_name == "audience user_id":
                        payload["audience_rules"] = [{
                            "rule_type": "Selected Individual",
                            "user_id": value
                        }]
                    else:
                        payload["schedule"] = {
                            "occurrence_basis": "Shift",
                            "recurrence_pattern": "Once",
                            "shift_applicability": "Specific Shift",
                            "specific_shift_client_id": value,
                            "specific_shift_date": "2026-08-01",
                            "specific_shift_type": "Day"
                        }

                    with self.assertRaises(ValueError):
                        app.create_staff_notice_draft(payload, 1)

                    self.assert_no_draft_aggregate()

    def test_effective_and_expiry_validation(self):
        invalid_values = (
            {
                "effective_start_local": "bad"
            },
            {
                "effective_start_local": "2024-03-10T02:30"
            },
            {
                "effective_start_local": "2024-11-03T01:30"
            },
            {
                "effective_start_local": "2026-08-01T10:00",
                "expires_local": "2026-08-01T10:00"
            },
            {
                "effective_start_local": "2026-08-01T10:00",
                "expires_local": "2026-08-01T09:59"
            },
            {
                "until_withdrawn": True,
                "expires_local": "2026-08-01T10:00"
            }
        )

        for changes in invalid_values:
            with self.subTest(changes=changes):
                payload = self.minimal_payload()
                payload.update(changes)
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_draft(payload)

    def test_audience_validation_and_duplicates(self):
        invalid_rules = (
            [{"rule_type": "Bad"}],
            [{"rule_type": "Selected Role"}],
            [{
                "rule_type": "Selected Role",
                "role_name": "Unknown Role"
            }],
            [{
                "rule_type": "Selected Role",
                "role_name": "Admin",
                "user_id": 4
            }],
            [{"rule_type": "Selected Individual"}],
            [{
                "rule_type": "Selected Individual",
                "user_id": True
            }],
            [{
                "rule_type": "Core Organization",
                "role_name": "Admin"
            }],
            [
                {"rule_type": "All Support Workers"},
                {"rule_type": "All Support Workers"}
            ],
            [
                {
                    "rule_type": "Selected Role",
                    "role_name": " Admin "
                },
                {
                    "rule_type": "Selected Role",
                    "role_name": "Admin"
                }
            ],
            [
                {
                    "rule_type": "Selected Individual",
                    "user_id": 4
                },
                {
                    "rule_type": "Selected Individual",
                    "user_id": 4
                }
            ]
        )

        for rules in invalid_rules:
            with self.subTest(rules=rules):
                payload = self.minimal_payload()
                payload["audience_rules"] = rules
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_draft(payload)

    def test_schedule_validation(self):
        invalid_schedules = (
            {
                "occurrence_basis": "One Time",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None"
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Once",
                "shift_applicability": "None"
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None"
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "specific_calendar_date": "2026-08-01"
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Specific Shift",
                "specific_shift_client_id": 1
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift",
                "specific_shift_client_id": 1,
                "specific_shift_date": "2026-08-01",
                "specific_shift_type": "Day"
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "one_time_due_local": "2026-08-01T12:00"
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "interval_days": 2
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Interval Days",
                "shift_applicability": "None",
                "interval_days": True
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Selected Shift Types",
                "shift_types": ["Day", "Day"]
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Selected Shift Types",
                "shift_types": ["Bad"]
            },
            {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Daily",
                "shift_applicability": "Every Shift",
                "shift_types": ["Day"]
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "None",
                "weekdays": [1, 1]
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Selected Weekdays",
                "shift_applicability": "None",
                "weekdays": [True]
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "weekdays": [1]
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "recurrence_anchor_date": "08/01/2026"
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "interval_days": None
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "specific_calendar_date": None
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "specific_shift_client_id": None
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "one_time_due_local": None
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "shift_types": []
            },
            {
                "occurrence_basis": "Calendar",
                "recurrence_pattern": "Daily",
                "shift_applicability": "None",
                "weekdays": []
            }
        )

        for schedule in invalid_schedules:
            with self.subTest(schedule=schedule):
                payload = self.minimal_payload()
                payload["schedule"] = schedule
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_draft(payload)

    def test_interval_days_boundary_is_persisted(self):
        payload = self.minimal_payload()
        payload["schedule"] = {
            "occurrence_basis": "Calendar",
            "recurrence_pattern": "Interval Days",
            "shift_applicability": "None",
            "interval_days": 2
        }

        notice_id = app.create_staff_notice_draft(payload, 1)
        conn = self.open_database()
        schedule = conn.execute(
            "SELECT interval_days FROM staff_notice_schedules "
            "WHERE notice_id = ?",
            (notice_id,)
        ).fetchone()

        self.assertEqual(schedule["interval_days"], 2)

    def test_invalid_interval_days_create_no_aggregate(self):
        for value in (1, 1.5, "2", False, -1):
            with self.subTest(value=value):
                payload = self.minimal_payload()
                payload["schedule"] = {
                    "occurrence_basis": "Calendar",
                    "recurrence_pattern": "Interval Days",
                    "shift_applicability": "None",
                    "interval_days": value
                }

                with self.assertRaises(ValueError):
                    app.create_staff_notice_draft(payload, 1)

                self.assert_no_draft_aggregate()

    def test_weekday_boundaries_are_persisted(self):
        payload = self.minimal_payload()
        payload["schedule"] = {
            "occurrence_basis": "Calendar",
            "recurrence_pattern": "Selected Weekdays",
            "shift_applicability": "None",
            "weekdays": [0, 6]
        }

        notice_id = app.create_staff_notice_draft(payload, 1)
        conn = self.open_database()
        weekdays = conn.execute("""
            SELECT sw.weekday_number
            FROM staff_notice_schedule_weekdays sw
            JOIN staff_notice_schedules s
                ON sw.schedule_id = s.schedule_id
            WHERE s.notice_id = ?
            ORDER BY sw.weekday_number
        """, (notice_id,)).fetchall()

        self.assertEqual(
            [row["weekday_number"] for row in weekdays],
            [0, 6]
        )

    def test_invalid_weekdays_create_no_aggregate(self):
        for value in (-1, 7, 1.0, "1", False):
            with self.subTest(value=value):
                payload = self.minimal_payload()
                payload["schedule"] = {
                    "occurrence_basis": "Calendar",
                    "recurrence_pattern": "Selected Weekdays",
                    "shift_applicability": "None",
                    "weekdays": [value]
                }

                with self.assertRaises(ValueError):
                    app.create_staff_notice_draft(payload, 1)

                self.assert_no_draft_aggregate()

    def test_cross_aggregate_schedule_validation(self):
        mismatch = self.minimal_payload()
        mismatch["client_id"] = 1
        mismatch["schedule"] = {
            "occurrence_basis": "Shift",
            "recurrence_pattern": "Once",
            "shift_applicability": "Specific Shift",
            "specific_shift_client_id": 2,
            "specific_shift_date": "2026-08-01",
            "specific_shift_type": "Day"
        }

        due_after_expiry = self.minimal_payload()
        due_after_expiry["expires_local"] = "2026-08-01T12:00"
        due_after_expiry["schedule"] = {
            "occurrence_basis": "One Time",
            "recurrence_pattern": "Once",
            "shift_applicability": "None",
            "one_time_due_local": "2026-08-01T12:01"
        }

        wrong_audience = self.minimal_payload()
        wrong_audience["audience_rules"] = [{
            "rule_type": "Applicable Shift Staff"
        }]
        wrong_audience["schedule"] = {
            "occurrence_basis": "Calendar",
            "recurrence_pattern": "Daily",
            "shift_applicability": "None"
        }

        for payload in (mismatch, due_after_expiry, wrong_audience):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    app.validate_staff_notice_draft(payload)

    def test_authorized_management_roles_succeed(self):
        for actor_user_id in (1, 2, 3):
            with self.subTest(actor_user_id=actor_user_id):
                notice_id = app.create_staff_notice_draft(
                    self.minimal_payload(),
                    actor_user_id
                )
                self.assertGreater(notice_id, 0)

    def test_unauthorized_actors_fail_without_writes(self):
        for actor_user_id in (
            None,
            True,
            False,
            0,
            -1,
            1.0,
            "1",
            999,
            6,
            4,
            5
        ):
            with self.subTest(actor_user_id=actor_user_id):
                with self.assertRaises(PermissionError):
                    app.create_staff_notice_draft(
                        self.minimal_payload(),
                        actor_user_id
                    )

        self.assert_no_draft_aggregate()

    def test_invalid_database_references_fail_without_writes(self):
        reference_payloads = []

        for client_id in (2, 999):
            payload = self.minimal_payload()
            payload["client_id"] = client_id
            reference_payloads.append(payload)

        for user_id in (7, 999):
            payload = self.minimal_payload()
            payload["audience_rules"] = [{
                "rule_type": "Selected Individual",
                "user_id": user_id
            }]
            reference_payloads.append(payload)

        for client_id in (2, 999):
            payload = self.minimal_payload()
            payload["schedule"] = {
                "occurrence_basis": "Shift",
                "recurrence_pattern": "Once",
                "shift_applicability": "Specific Shift",
                "specific_shift_client_id": client_id,
                "specific_shift_date": "2026-08-01",
                "specific_shift_type": "Day"
            }
            reference_payloads.append(payload)

        for payload in reference_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    app.create_staff_notice_draft(payload, 1)

        self.assert_no_draft_aggregate()

    def test_authorization_and_reference_failures_close_connection(self):
        cases = (
            (self.minimal_payload(), 4, PermissionError),
            (
                {
                    **self.minimal_payload(),
                    "client_id": 999
                },
                1,
                ValueError
            )
        )

        for payload, actor_user_id, error_type in cases:
            with self.subTest(actor_user_id=actor_user_id, payload=payload):
                raw_connection = sqlite3.connect(self.database_path)
                raw_connection.row_factory = sqlite3.Row
                raw_connection.execute("PRAGMA foreign_keys = ON")
                tracking_connection = TrackingConnection(raw_connection)

                with mock.patch.object(
                    app,
                    "get_db",
                    return_value=tracking_connection
                ) as mocked_get_db:
                    with self.assertRaises(error_type):
                        app.create_staff_notice_draft(
                            payload,
                            actor_user_id
                        )

                mocked_get_db.assert_called_once_with()
                self.assertEqual(tracking_connection.commit_calls, 0)
                self.assertEqual(tracking_connection.rollback_calls, 0)
                self.assertTrue(tracking_connection.closed)
                self.assert_no_draft_aggregate()

    def test_payload_creator_cannot_override_authoritative_actor(self):
        payload = self.minimal_payload()
        payload["created_by_user_id"] = 4

        with self.assertRaises(ValueError):
            app.create_staff_notice_draft(payload, 1)

        self.assert_no_draft_aggregate()

    def test_validation_failure_opens_no_connection(self):
        payload = self.minimal_payload()
        payload["title"] = ""

        with mock.patch.object(app, "get_db") as mocked_get_db:
            with self.assertRaises(ValueError):
                app.create_staff_notice_draft(payload, 1)

        mocked_get_db.assert_not_called()

    def test_get_db_is_called_once_and_connection_closes_on_success(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        tracking_connection = TrackingConnection(raw_connection)

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ) as mocked_get_db:
            notice_id = app.create_staff_notice_draft(
                self.minimal_payload(),
                1
            )

        self.assertGreater(notice_id, 0)
        mocked_get_db.assert_called_once_with()
        self.assertTrue(tracking_connection.closed)
        self.assertEqual(tracking_connection.commit_calls, 1)
        self.assertEqual(tracking_connection.rollback_calls, 0)
        self.assertEqual(tracking_connection.foreign_keys_at_begin, 1)
        self.assertFalse(tracking_connection.transaction_at_start)

    def test_in_transaction_validation_closes_actor_race(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")

        def deactivate_actor_before_begin():
            other_connection = sqlite3.connect(self.database_path)

            try:
                other_connection.execute(
                    "UPDATE users SET active = 0 WHERE user_id = 1"
                )
                other_connection.commit()
            finally:
                other_connection.close()

        tracking_connection = TrackingConnection(
            raw_connection,
            before_begin=deactivate_actor_before_begin
        )
        notice_id = None

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ) as mocked_get_db:
            with self.assertRaisesRegex(
                PermissionError,
                "management access denied"
            ) as caught:
                notice_id = app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertIsNone(notice_id)
        self.assertIn(
            "management access denied",
            str(caught.exception)
        )
        mocked_get_db.assert_called_once_with()
        self.assertEqual(tracking_connection.before_begin_calls, 1)
        self.assertEqual(tracking_connection.commit_calls, 0)
        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertEqual(tracking_connection.close_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assert_no_draft_aggregate()

    def test_each_insert_failure_rolls_back_complete_aggregate(self):
        cases = (
            ("staff_notices", self.minimal_payload()),
            (
                "staff_notice_audiences",
                self.payload_with_audience()
            ),
            (
                "staff_notice_audience_rules",
                self.payload_with_audience()
            ),
            (
                "staff_notice_schedules",
                self.payload_with_schedule()
            ),
            (
                "staff_notice_schedule_shift_types",
                self.payload_with_shift_types()
            ),
            (
                "staff_notice_schedule_weekdays",
                self.payload_with_weekdays()
            ),
            ("activity_log", self.payload_with_audience_and_schedule())
        )

        for table_name, payload in cases:
            with self.subTest(table_name=table_name):
                self.install_failure_trigger(table_name)

                raw_connection = sqlite3.connect(self.database_path)
                raw_connection.row_factory = sqlite3.Row
                raw_connection.execute("PRAGMA foreign_keys = ON")
                tracking_connection = TrackingConnection(raw_connection)

                with mock.patch.object(
                    app,
                    "get_db",
                    return_value=tracking_connection
                ) as mocked_get_db:
                    with self.assertRaisesRegex(
                        sqlite3.IntegrityError,
                        "controlled insert failure"
                    ):
                        app.create_staff_notice_draft(payload, 1)

                mocked_get_db.assert_called_once_with()
                self.assertEqual(tracking_connection.commit_calls, 0)
                self.assertEqual(tracking_connection.rollback_calls, 1)
                self.assertTrue(tracking_connection.closed)
                self.assert_no_draft_aggregate()

                conn = self.open_database()
                conn.execute(f"DROP TRIGGER fail_{table_name}_insert")
                conn.commit()

    def test_commit_failure_rolls_back_and_closes(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        commit_error = sqlite3.OperationalError(
            "controlled commit failure"
        )
        tracking_connection = TrackingConnection(
            raw_connection,
            commit_error=commit_error
        )

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ):
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "controlled commit failure"
            ) as caught:
                app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertIs(caught.exception, commit_error)
        self.assertEqual(tracking_connection.commit_calls, 1)
        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assert_no_draft_aggregate()

    def test_failure_connection_closes_and_original_error_survives(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        tracking_connection = TrackingConnection(raw_connection)
        self.install_failure_trigger("activity_log")

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled insert failure"
            ):
                app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assert_no_draft_aggregate()

    def test_rollback_failure_preserves_primary_error_and_closes(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        rollback_error = sqlite3.OperationalError(
            "controlled rollback failure"
        )
        tracking_connection = TrackingConnection(
            raw_connection,
            rollback_error=rollback_error
        )
        self.install_failure_trigger("activity_log")
        notice_id = None

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled insert failure"
            ) as caught:
                notice_id = app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertIsNone(notice_id)
        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertEqual(tracking_connection.close_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assertIs(
            caught.exception.staff_notice_rollback_error,
            rollback_error
        )
        self.assertIsNot(caught.exception.__cause__, rollback_error)
        self.assertIsNot(caught.exception.__context__, rollback_error)
        self.assertIs(rollback_error.__context__, caught.exception)
        self.assert_exception_graph_acyclic(caught.exception)
        self.assert_exception_graph_acyclic(rollback_error)
        self.assert_no_draft_aggregate()

    def test_close_failure_preserves_primary_error(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        close_error = sqlite3.OperationalError(
            "controlled close failure"
        )
        tracking_connection = TrackingConnection(
            raw_connection,
            close_error=close_error
        )
        self.install_failure_trigger("activity_log")
        notice_id = None

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ):
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "controlled insert failure"
            ) as caught:
                notice_id = app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertIsNone(notice_id)
        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertEqual(tracking_connection.close_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assertIs(
            caught.exception.staff_notice_close_error,
            close_error
        )
        self.assert_no_draft_aggregate()

    def test_diagnostic_attribute_failure_preserves_primary_error(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        original_cause = ValueError("controlled original cause")
        original_context = LookupError("controlled original context")
        primary_error = DiagnosticAttributeRejectingError(
            "controlled primary failure"
        )
        primary_error.__cause__ = original_cause
        primary_error.__context__ = original_context
        rollback_error = sqlite3.OperationalError(
            "controlled rollback failure"
        )
        tracking_connection = TrackingConnection(
            raw_connection,
            rollback_error=rollback_error
        )
        notice_id = None

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ), mock.patch.object(
            app,
            "log_activity",
            side_effect=primary_error
        ):
            with self.assertRaises(
                DiagnosticAttributeRejectingError
            ) as caught:
                notice_id = app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertIsNone(notice_id)
        self.assertIs(caught.exception, primary_error)
        self.assertIs(primary_error.__cause__, original_cause)
        self.assertIs(primary_error.__context__, original_context)
        self.assertIs(rollback_error.__context__, primary_error)
        self.assertEqual(primary_error.add_note_calls, 1)
        self.assert_exception_graph_acyclic(primary_error)
        self.assert_exception_graph_acyclic(rollback_error)
        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertEqual(tracking_connection.close_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assert_no_draft_aggregate()

    def test_add_note_failure_preserves_primary_error(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary failure"
        )
        rollback_error = sqlite3.OperationalError(
            "controlled rollback failure"
        )
        tracking_connection = TrackingConnection(
            raw_connection,
            rollback_error=rollback_error
        )
        notice_id = None

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ), mock.patch.object(
            app,
            "log_activity",
            side_effect=primary_error
        ):
            with self.assertRaises(
                DiagnosticAttributeAndNoteRejectingError
            ) as caught:
                notice_id = app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertIsNone(notice_id)
        self.assertIs(caught.exception, primary_error)
        self.assertIsNone(primary_error.__cause__)
        self.assertIsNone(primary_error.__context__)
        self.assertIs(rollback_error.__context__, primary_error)
        self.assertEqual(primary_error.add_note_calls, 1)
        self.assert_exception_graph_acyclic(primary_error)
        self.assert_exception_graph_acyclic(rollback_error)
        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertEqual(tracking_connection.close_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assert_no_draft_aggregate()

    def test_existing_cause_and_context_survive_cleanup_failure(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        original_cause = ValueError("controlled original cause")
        original_context = LookupError("controlled original context")
        primary_error = RuntimeError("controlled primary failure")
        primary_error.__cause__ = original_cause
        primary_error.__context__ = original_context
        rollback_error = sqlite3.OperationalError(
            "controlled rollback failure"
        )
        tracking_connection = TrackingConnection(
            raw_connection,
            rollback_error=rollback_error
        )
        notice_id = None

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ), mock.patch.object(
            app,
            "log_activity",
            side_effect=primary_error
        ):
            with self.assertRaises(RuntimeError) as caught:
                notice_id = app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertIsNone(notice_id)
        self.assertIs(caught.exception, primary_error)
        self.assertIs(primary_error.__cause__, original_cause)
        self.assertIs(primary_error.__context__, original_context)
        self.assertIs(
            primary_error.staff_notice_rollback_error,
            rollback_error
        )
        self.assertIs(rollback_error.__context__, primary_error)
        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertEqual(tracking_connection.close_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assert_exception_graph_acyclic(primary_error)
        self.assert_no_draft_aggregate()

    def test_reused_exception_graphs_do_not_create_cycles(self):
        cause_primary = RuntimeError("cause primary")
        cause_cleanup = RuntimeError("cause cleanup")
        cause_primary.__cause__ = cause_cleanup
        original_cause = cause_primary.__cause__

        app._preserve_staff_notice_cleanup_error(
            cause_primary,
            "staff_notice_rollback_error",
            cause_cleanup,
            "cause diagnostic"
        )

        self.assertIs(cause_primary.__cause__, original_cause)
        self.assertIs(
            cause_primary.staff_notice_rollback_error,
            cause_cleanup
        )
        self.assert_exception_graph_acyclic(cause_primary)

        context_primary = RuntimeError("context primary")
        context_cleanup = RuntimeError("context cleanup")
        context_primary.__context__ = context_cleanup
        original_context = context_primary.__context__

        app._preserve_staff_notice_cleanup_error(
            context_primary,
            "staff_notice_close_error",
            context_cleanup,
            "context diagnostic"
        )

        self.assertIs(context_primary.__context__, original_context)
        self.assertIs(
            context_primary.staff_notice_close_error,
            context_cleanup
        )
        self.assert_exception_graph_acyclic(context_primary)

        mixed_primary = DiagnosticAttributeAndNoteRejectingError(
            "mixed primary"
        )
        mixed_middle = RuntimeError("mixed middle")
        mixed_cleanup = RuntimeError("mixed cleanup")
        mixed_primary.__cause__ = mixed_middle
        mixed_middle.__context__ = mixed_cleanup

        app._preserve_staff_notice_cleanup_error(
            mixed_primary,
            "staff_notice_rollback_error",
            mixed_cleanup,
            "mixed diagnostic"
        )

        self.assertIs(mixed_primary.__cause__, mixed_middle)
        self.assertIs(mixed_middle.__context__, mixed_cleanup)
        self.assertEqual(mixed_primary.add_note_calls, 1)
        self.assert_exception_graph_acyclic(mixed_primary)

        reverse_primary = DiagnosticAttributeAndNoteRejectingError(
            "reverse primary"
        )
        reverse_cleanup = RuntimeError("reverse cleanup")
        reverse_cleanup.__context__ = reverse_primary

        app._preserve_staff_notice_cleanup_error(
            reverse_primary,
            "staff_notice_close_error",
            reverse_cleanup,
            "reverse diagnostic"
        )

        self.assertIsNone(reverse_primary.__cause__)
        self.assertIsNone(reverse_primary.__context__)
        self.assertIs(reverse_cleanup.__context__, reverse_primary)
        self.assertEqual(reverse_primary.add_note_calls, 1)
        self.assert_exception_graph_acyclic(reverse_cleanup)

    def test_safe_detached_cleanup_error_can_be_fallback_cause(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )
        cleanup_error = RuntimeError("controlled detached cleanup")

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            cleanup_error,
            "controlled diagnostic"
        )

        self.assertIs(primary_error.__cause__, cleanup_error)
        self.assertIsNone(primary_error.__context__)
        self.assertEqual(primary_error.add_note_calls, 1)
        self.assert_exception_graph_acyclic(primary_error)

    def test_graph_inspection_failure_abandons_fallback_safely(self):
        primary_error = GraphInspectionRejectingError(
            "controlled primary"
        )
        cleanup_error = RuntimeError("controlled cleanup")

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            cleanup_error,
            "controlled diagnostic"
        )

        self.assertIsNone(
            BaseException.__getattribute__(primary_error, "__cause__")
        )
        self.assertIsNone(
            BaseException.__getattribute__(primary_error, "__context__")
        )
        self.assertEqual(primary_error.add_note_calls, 1)

    def test_hidden_real_context_rejects_fallback_chaining(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )
        cleanup_error = HiddenRealContextError("controlled cleanup")
        BaseException.__setattr__(
            cleanup_error,
            "__context__",
            primary_error
        )

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            cleanup_error,
            "controlled diagnostic"
        )

        self.assertIsNone(primary_error.__cause__)
        self.assertIsNone(primary_error.__context__)
        self.assertIsNone(cleanup_error.__context__)
        self.assertIs(
            BaseException.__getattribute__(
                cleanup_error,
                "__context__"
            ),
            primary_error
        )
        self.assertEqual(primary_error.add_note_calls, 1)

    def test_side_effecting_context_accessor_is_never_invoked(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )
        cleanup_error = SideEffectingContextError(primary_error)

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            cleanup_error,
            "controlled diagnostic"
        )

        self.assertEqual(cleanup_error.context_access_attempts, 0)
        self.assertIsNone(primary_error.__cause__)
        self.assertIsNone(primary_error.__context__)
        self.assertIsNone(
            BaseException.__getattribute__(
                cleanup_error,
                "__context__"
            )
        )
        self.assertEqual(primary_error.add_note_calls, 1)

    def test_cause_only_cyclic_graph_rejects_fallback(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )
        cleanup_error = RuntimeError("controlled cleanup")
        related_error = RuntimeError("controlled related")
        cleanup_error.__cause__ = related_error
        related_error.__cause__ = cleanup_error

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            cleanup_error,
            "controlled diagnostic"
        )

        self.assertIsNone(primary_error.__cause__)
        self.assertIs(cleanup_error.__cause__, related_error)
        self.assertIs(related_error.__cause__, cleanup_error)

    def test_context_only_cyclic_graph_rejects_fallback(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )
        cleanup_error = RuntimeError("controlled cleanup")
        related_error = RuntimeError("controlled related")
        cleanup_error.__context__ = related_error
        related_error.__context__ = cleanup_error

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            cleanup_error,
            "controlled diagnostic"
        )

        self.assertIsNone(primary_error.__cause__)
        self.assertIs(cleanup_error.__context__, related_error)
        self.assertIs(related_error.__context__, cleanup_error)

    def test_mixed_cyclic_graph_rejects_fallback(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )
        cleanup_error = RuntimeError("controlled cleanup")
        related_error = RuntimeError("controlled related")
        cleanup_error.__cause__ = related_error
        related_error.__context__ = cleanup_error

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            cleanup_error,
            "controlled diagnostic"
        )

        self.assertIsNone(primary_error.__cause__)
        self.assertIs(cleanup_error.__cause__, related_error)
        self.assertIs(related_error.__context__, cleanup_error)

    def test_primary_cleanup_identity_rejects_self_cause(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            primary_error,
            "controlled diagnostic"
        )

        self.assertIsNone(primary_error.__cause__)
        self.assertIsNone(primary_error.__context__)

    def test_related_node_inspection_failure_rejects_fallback(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )
        cleanup_error = RuntimeError("controlled cleanup")
        related_error = GraphInspectionRejectingError(
            "controlled related"
        )
        cleanup_error.__cause__ = related_error

        original_link_reader = (
            app._get_staff_notice_exception_link
        )

        def instrumented_link_reader(error, attribute_name):
            if error is related_error:
                related_error.graph_helper_visits += 1

            return original_link_reader(error, attribute_name)

        with mock.patch.object(
            app,
            "_get_staff_notice_exception_link",
            side_effect=instrumented_link_reader
        ):
            app._preserve_staff_notice_cleanup_error(
                primary_error,
                "staff_notice_rollback_error",
                cleanup_error,
                "controlled diagnostic"
            )

        self.assertIsNone(primary_error.__cause__)
        self.assertIs(cleanup_error.__cause__, related_error)
        self.assertGreater(related_error.graph_helper_visits, 0)
        self.assertEqual(related_error.access_attempts, 0)

    def test_custom_cause_setattr_cannot_intercept_fallback(self):
        primary_error = CauseAssignmentInterceptingError(
            "controlled primary"
        )
        cleanup_error = RuntimeError("controlled cleanup")

        app._preserve_staff_notice_cleanup_error(
            primary_error,
            "staff_notice_rollback_error",
            cleanup_error,
            "controlled diagnostic"
        )

        self.assertEqual(primary_error.cause_assignment_attempts, 0)
        self.assertIs(
            BaseException.__getattribute__(
                primary_error,
                "__cause__"
            ),
            cleanup_error
        )
        self.assertIs(primary_error.__cause__, cleanup_error)
        self.assert_exception_graph_acyclic(primary_error)

    def test_failed_fallback_verification_restores_state(self):
        primary_error = DiagnosticAttributeAndNoteRejectingError(
            "controlled primary"
        )
        cleanup_error = RuntimeError("controlled cleanup")
        original_suppress_context = primary_error.__suppress_context__
        original_link_reader = (
            app._get_staff_notice_exception_link
        )
        primary_cause_reads = 0

        def failing_post_assignment_verification(
            error,
            attribute_name
        ):
            nonlocal primary_cause_reads

            if error is primary_error and attribute_name == "__cause__":
                primary_cause_reads += 1

                if primary_cause_reads == 3:
                    return False, None

            return original_link_reader(error, attribute_name)

        with mock.patch.object(
            app,
            "_get_staff_notice_exception_link",
            side_effect=failing_post_assignment_verification
        ):
            app._preserve_staff_notice_cleanup_error(
                primary_error,
                "staff_notice_rollback_error",
                cleanup_error,
                "controlled diagnostic"
            )

        self.assertEqual(primary_cause_reads, 3)
        self.assertIsNone(
            BaseException.__getattribute__(
                primary_error,
                "__cause__"
            )
        )
        self.assertIs(
            BaseException.__getattribute__(
                primary_error,
                "__suppress_context__"
            ),
            original_suppress_context
        )

    def test_rollback_and_close_failures_preserve_primary_error(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        primary_error = sqlite3.IntegrityError(
            "controlled primary failure"
        )
        rollback_error = sqlite3.OperationalError(
            "controlled rollback failure"
        )
        close_error = sqlite3.OperationalError(
            "controlled close failure"
        )
        tracking_connection = TrackingConnection(
            raw_connection,
            rollback_error=rollback_error,
            close_error=close_error
        )
        notice_id = None

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ), mock.patch.object(
            app,
            "log_activity",
            side_effect=primary_error
        ):
            with self.assertRaises(sqlite3.IntegrityError) as caught:
                notice_id = app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        self.assertIsNone(notice_id)
        self.assertIs(caught.exception, primary_error)
        self.assertIs(
            primary_error.staff_notice_rollback_error,
            rollback_error
        )
        self.assertIs(
            primary_error.staff_notice_close_error,
            close_error
        )
        self.assertIsNone(primary_error.__cause__)
        self.assertIsNone(primary_error.__context__)
        self.assertIs(rollback_error.__context__, primary_error)
        self.assertIs(close_error.__context__, primary_error)
        self.assert_exception_graph_acyclic(primary_error)
        self.assert_exception_graph_acyclic(rollback_error)
        self.assert_exception_graph_acyclic(close_error)
        self.assertEqual(tracking_connection.rollback_calls, 1)
        self.assertEqual(tracking_connection.close_calls, 1)
        self.assertTrue(tracking_connection.closed)
        self.assert_no_draft_aggregate()

    def test_close_failure_after_commit_reports_committed_state(self):
        raw_connection = sqlite3.connect(self.database_path)
        raw_connection.row_factory = sqlite3.Row
        raw_connection.execute("PRAGMA foreign_keys = ON")
        close_error = sqlite3.OperationalError(
            "controlled close failure after commit"
        )
        tracking_connection = TrackingConnection(
            raw_connection,
            close_error=close_error
        )
        notice_id = None

        with mock.patch.object(
            app,
            "get_db",
            return_value=tracking_connection
        ):
            with self.assertRaises(
                app.StaffNoticeDraftCommittedCloseError
            ) as caught:
                notice_id = app.create_staff_notice_draft(
                    self.payload_with_audience_and_schedule(),
                    1
                )

        committed_error = caught.exception
        self.assertIsNone(notice_id)
        self.assertTrue(committed_error.committed)
        self.assertFalse(committed_error.retry_safe)
        self.assertGreater(committed_error.notice_id, 0)
        self.assertIn("was committed", str(committed_error))
        self.assertIn("Do not retry", str(committed_error))
        self.assertIs(committed_error.__cause__, close_error)
        self.assertEqual(tracking_connection.commit_calls, 1)
        self.assertEqual(tracking_connection.rollback_calls, 0)
        self.assertEqual(tracking_connection.close_calls, 1)

        conn = self.open_database()
        notice = conn.execute(
            "SELECT * FROM staff_notices"
        ).fetchone()
        audience = conn.execute(
            "SELECT * FROM staff_notice_audiences"
        ).fetchone()
        audience_rule = conn.execute(
            "SELECT * FROM staff_notice_audience_rules"
        ).fetchone()
        schedule = conn.execute(
            "SELECT * FROM staff_notice_schedules"
        ).fetchone()
        schedule_shift_type = conn.execute(
            "SELECT * FROM staff_notice_schedule_shift_types"
        ).fetchone()
        schedule_weekday = conn.execute(
            "SELECT * FROM staff_notice_schedule_weekdays"
        ).fetchone()
        activity = conn.execute(
            "SELECT * FROM activity_log"
        ).fetchone()

        self.assertEqual(self.table_count("staff_notices"), 1)
        self.assertEqual(notice["notice_id"], committed_error.notice_id)
        self.assertEqual(notice["status"], "Draft")
        self.assertEqual(notice["draft_active"], 1)
        self.assertEqual(notice["version_number"], 1)
        self.assertEqual(notice["created_by_user_id"], 1)
        self.assertEqual(self.table_count("staff_notice_audiences"), 1)
        self.assertEqual(
            self.table_count("staff_notice_audience_rules"),
            1
        )
        self.assertEqual(self.table_count("staff_notice_schedules"), 1)
        self.assertEqual(
            self.table_count("staff_notice_schedule_shift_types"),
            1
        )
        self.assertEqual(
            self.table_count("staff_notice_schedule_weekdays"),
            1
        )
        self.assertEqual(
            audience["notice_id"],
            committed_error.notice_id
        )
        self.assertEqual(
            audience_rule["audience_id"],
            audience["audience_id"]
        )
        self.assertEqual(audience_rule["rule_type"], "Selected Individual")
        self.assertEqual(audience_rule["user_id"], 4)
        self.assertEqual(
            schedule["notice_id"],
            committed_error.notice_id
        )
        self.assertEqual(
            schedule_shift_type["schedule_id"],
            schedule["schedule_id"]
        )
        self.assertEqual(schedule_shift_type["shift_type"], "Day")
        self.assertEqual(
            schedule_weekday["schedule_id"],
            schedule["schedule_id"]
        )
        self.assertEqual(schedule_weekday["weekday_number"], 1)
        self.assertEqual(self.table_count("activity_log"), 1)
        self.assertEqual(
            activity["related_id"],
            committed_error.notice_id
        )
        self.assertEqual(
            activity["activity_type"],
            "staff_notice_draft_created"
        )

    def test_no_derived_or_recipient_records_are_created(self):
        app.create_staff_notice_draft(
            self.payload_with_audience_and_schedule(),
            1
        )

        for table_name in (
            "staff_notice_audience_eligibility_periods",
            "staff_notice_occurrences",
            "staff_notice_deliveries",
            "staff_notice_delivery_history",
            "acknowledgements"
        ):
            self.assertEqual(self.table_count(table_name), 0)

    def payload_with_audience(self):
        payload = self.minimal_payload()
        payload["audience_rules"] = [{
            "rule_type": "Selected Individual",
            "user_id": 4
        }]
        return payload

    def payload_with_schedule(self):
        payload = self.minimal_payload()
        payload["schedule"] = {
            "occurrence_basis": "Calendar",
            "recurrence_pattern": "Daily",
            "shift_applicability": "None"
        }
        return payload

    def payload_with_shift_types(self):
        payload = self.minimal_payload()
        payload["schedule"] = {
            "occurrence_basis": "Shift",
            "recurrence_pattern": "Daily",
            "shift_applicability": "Selected Shift Types",
            "shift_types": ["Day"]
        }
        return payload

    def payload_with_weekdays(self):
        payload = self.minimal_payload()
        payload["schedule"] = {
            "occurrence_basis": "Calendar",
            "recurrence_pattern": "Selected Weekdays",
            "shift_applicability": "None",
            "weekdays": [1]
        }
        return payload

    def payload_with_audience_and_schedule(self):
        payload = self.payload_with_audience()
        payload["schedule"] = {
            "occurrence_basis": "Shift",
            "recurrence_pattern": "Selected Weekdays",
            "shift_applicability": "Selected Shift Types",
            "shift_types": ["Day"],
            "weekdays": [1]
        }
        return payload


class DiagnosticAttributeRejectingError(Exception):

    def __init__(self, message):
        super().__init__(message)
        self.add_note_calls = 0
        self.diagnostic_notes = []

    def __setattr__(self, name, value):
        if name.startswith("staff_notice_"):
            raise RuntimeError(
                "controlled diagnostic attribute failure"
            )

        super().__setattr__(name, value)

    def add_note(self, note):
        self.add_note_calls += 1
        self.diagnostic_notes.append(note)


class DiagnosticAttributeAndNoteRejectingError(
    DiagnosticAttributeRejectingError
):

    def __init__(self, message):
        super().__init__(message)
        self.add_note_calls = 0

    def add_note(self, note):
        self.add_note_calls += 1
        raise RuntimeError("controlled add_note failure")


class GraphInspectionRejectingError(
    DiagnosticAttributeAndNoteRejectingError
):

    def __init__(self, message):
        super().__init__(message)
        self.graph_helper_visits = 0
        self.access_attempts = 0

    def __getattribute__(self, name):
        if name in {"__cause__", "__context__"}:
            self.access_attempts += 1
            raise RuntimeError("controlled graph inspection failure")

        return super().__getattribute__(name)


class HiddenRealContextError(RuntimeError):

    def __getattribute__(self, name):
        if name == "__context__":
            return None

        return super().__getattribute__(name)


class SideEffectingContextError(RuntimeError):

    def __init__(self, target_error):
        super().__init__("controlled side-effecting cleanup")
        self.target_error = target_error
        self.context_access_attempts = 0

    def __getattribute__(self, name):
        if name == "__context__":
            self.context_access_attempts += 1
            original_context = BaseException.__getattribute__(
                self,
                "__context__"
            )

            if original_context is None:
                BaseException.__setattr__(
                    self,
                    "__context__",
                    self.target_error
                )

            return original_context

        return super().__getattribute__(name)


class CauseAssignmentInterceptingError(
    DiagnosticAttributeAndNoteRejectingError
):

    def __init__(self, message):
        super().__init__(message)
        self.cause_assignment_attempts = 0

    def __setattr__(self, name, value):
        if name == "__cause__":
            self.cause_assignment_attempts += 1
            return

        super().__setattr__(name, value)


class TrackingConnection:

    def __init__(
        self,
        connection,
        *,
        commit_error=None,
        rollback_error=None,
        close_error=None,
        before_begin=None
    ):
        self.connection = connection
        self.commit_error = commit_error
        self.rollback_error = rollback_error
        self.close_error = close_error
        self.before_begin = before_begin
        self.closed = False
        self.close_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.foreign_keys_at_begin = None
        self.transaction_at_start = None
        self.before_begin_calls = 0

    def execute(self, statement, parameters=()):
        if statement == "BEGIN IMMEDIATE":
            if self.before_begin is not None:
                self.before_begin_calls += 1
                self.before_begin()

            self.foreign_keys_at_begin = self.connection.execute(
                "PRAGMA foreign_keys"
            ).fetchone()[0]
            self.transaction_at_start = self.connection.in_transaction

        return self.connection.execute(statement, parameters)

    @property
    def in_transaction(self):
        return self.connection.in_transaction

    def commit(self):
        self.commit_calls += 1

        if self.commit_error is not None:
            raise self.commit_error

        self.connection.commit()

    def rollback(self):
        self.rollback_calls += 1

        if self.rollback_error is not None:
            raise self.rollback_error

        self.connection.rollback()

    def close(self):
        self.close_calls += 1
        self.closed = True
        self.connection.close()

        if self.close_error is not None:
            raise self.close_error


class StaffNoticeDraftSafeImportTests(unittest.TestCase):

    def test_import_does_not_connect_or_create_database(self):
        repository_path = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_database = Path(temporary_directory) / "nhpsg.db"
            script = """
import sqlite3
import sys

def blocked_connect(*args, **kwargs):
    raise AssertionError("sqlite3.connect called during import")

sqlite3.connect = blocked_connect
sys.path.insert(0, sys.argv[1])
import app
"""
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    script,
                    str(repository_path)
                ],
                cwd=temporary_directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=result.stdout + result.stderr
            )
            self.assertFalse(temporary_database.exists())


if __name__ == "__main__":
    unittest.main()
