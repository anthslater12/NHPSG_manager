import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import add_behaviour_occurrences_table as behaviour_migration
import app


class BehaviourCheckpointOneTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(
            self.temporary_directory.name,
            "behaviour_checkpoint_one.db"
        )
        self.conn = sqlite3.connect(self.database_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
        """)
        self.conn.executemany("""
            INSERT INTO users (user_id, username, password_hash, full_name, role, active)
            VALUES (?, ?, 'hash', ?, ?, ?)
        """, (
            (1, "admin", "Admin User", "Admin", 1),
            (2, "manager", "Manager User", "Program Manager", 1),
            (3, "director", "Director User", "Director", 1),
            (4, "worker", "Worker User", "Support Worker", 1),
            (5, "inactive", "Inactive User", "Admin", 0),
        ))
        self.conn.executemany("""
            INSERT INTO clients (client_id, client_name, active)
            VALUES (?, ?, ?)
        """, (
            (1, "Active Client", 1),
            (2, "Inactive Client", 0),
        ))
        behaviour_migration.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temporary_directory.cleanup()

    def insert_occurrence(self, token="token-1", **overrides):
        values = {
            "client_id": 1,
            "occurred_at_utc": "2026-01-02T20:00:00Z",
            "aggression_towards_others": 1,
            "injury_to_others": 0,
            "self_harm": 0,
            "injury_to_self": 0,
            "property_damage": 0,
            "notes": None,
            "recorded_by_user_id": 1,
            "recorded_at_utc": "2026-01-02T20:01:00Z",
            "submission_token": token,
            "status": "Recorded",
            "voided_by_user_id": None,
            "voided_at_utc": None,
            "void_reason": None,
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        self.conn.execute(
            f"INSERT INTO behaviour_occurrences ({columns}) VALUES ({placeholders})",
            tuple(values.values())
        )
        self.conn.commit()

    def test_exact_schema_defaults_and_foreign_keys(self):
        columns = {
            row["name"]: row
            for row in self.conn.execute(
                "PRAGMA table_info(behaviour_occurrences)"
            )
        }
        self.assertEqual(set(columns), {
            "behaviour_occurrence_id", "client_id", "occurred_at_utc",
            "aggression_towards_others", "injury_to_others", "self_harm",
            "injury_to_self", "property_damage", "notes",
            "recorded_by_user_id", "recorded_at_utc", "submission_token",
            "status", "voided_by_user_id", "voided_at_utc", "void_reason",
            "shift_id", "record_format", *behaviour_migration.ABC_BOOLEAN_COLUMNS,
            *behaviour_migration.ABC_TEXT_COLUMNS, "duration_until_calm_minutes",
        })
        self.assertEqual(columns["status"]["dflt_value"], "'Recorded'")
        for category in app.BEHAVIOUR_CATEGORY_FIELDS:
            self.assertEqual(columns[category]["dflt_value"], "0")
        foreign_keys = {
            (row["from"], row["table"], row["to"])
            for row in self.conn.execute("PRAGMA foreign_key_list(behaviour_occurrences)")
        }
        self.assertEqual(foreign_keys, {
            ("client_id", "clients", "client_id"),
            ("recorded_by_user_id", "users", "user_id"),
            ("voided_by_user_id", "users", "user_id"),
        })

    def test_category_and_at_least_one_constraints(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_occurrence(
                "none-selected",
                aggression_towards_others=0
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_occurrence("invalid-boolean", self_harm=2)

    def test_canonical_utc_timestamp_constraints(self):
        self.insert_occurrence("canonical-timestamps")
        invalid_values = (
            "not-a-utc-instant",
            "2026-01-02T20:00:00+00:00",
            "2026-01-02T20:00:00",
            "2026-01-02 20:00:00Z",
            "2026-01-02T20:00:00.1Z",
            "2026-01-02T20:00:0Z",
            "2026-02-30T20:00:00Z",
            "2026-01-02T24:00:00Z",
            "2026-01-02T20:60:00Z",
        )
        for field_name in ("occurred_at_utc", "recorded_at_utc"):
            for index, invalid_value in enumerate(invalid_values):
                with self.subTest(field_name=field_name, value=invalid_value):
                    with self.assertRaises(sqlite3.IntegrityError):
                        self.insert_occurrence(
                            f"invalid-timestamp-{field_name}-{index}",
                            **{field_name: invalid_value}
                        )

    def test_canonical_utc_serializer_and_parser(self):
        source = datetime(
            2026, 7, 1, 12, 0,
            tzinfo=app.VANCOUVER_TIMEZONE
        )
        canonical = app.serialize_behaviour_utc(source)
        self.assertEqual(canonical, "2026-07-01T19:00:00Z")
        self.assertEqual(
            app.parse_behaviour_utc(canonical),
            datetime(2026, 7, 1, 19, 0, tzinfo=timezone.utc)
        )
        with self.assertRaises(ValueError):
            app.serialize_behaviour_utc(datetime(2026, 7, 1, 19, 0))
        with self.assertRaises(ValueError):
            app.serialize_behaviour_utc(
                datetime(2026, 7, 1, 19, 0, 0, 1, tzinfo=timezone.utc)
            )

    def test_canonical_utc_text_orders_chronologically(self):
        self.insert_occurrence(
            "later-occurrence",
            occurred_at_utc="2026-01-02T20:00:01Z"
        )
        self.insert_occurrence(
            "earlier-occurrence",
            occurred_at_utc="2026-01-02T20:00:00Z"
        )
        self.assertEqual(
            [row[0] for row in self.conn.execute("""
                SELECT occurred_at_utc
                FROM behaviour_occurrences
                ORDER BY occurred_at_utc
            """)],
            ["2026-01-02T20:00:00Z", "2026-01-02T20:00:01Z"]
        )

    def test_recorded_and_voided_state_combinations(self):
        self.insert_occurrence("recorded")
        self.insert_occurrence(
            "voided",
            status="Voided",
            voided_by_user_id=1,
            voided_at_utc="2026-01-02T20:05:00Z",
            void_reason="Entered in error"
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM behaviour_occurrences"
            ).fetchone()[0],
            2
        )

    def test_partial_void_metadata_is_rejected(self):
        invalid_recorded = (
            {"voided_by_user_id": 1},
            {"voided_at_utc": "2026-01-02T20:05:00Z"},
            {"void_reason": "No"},
        )
        invalid_voided = (
            {},
            {"voided_by_user_id": 1},
            {"voided_at_utc": "2026-01-02T20:05:00Z"},
            {"void_reason": "Reason"},
            {
                "voided_by_user_id": 1,
                "voided_at_utc": "2026-01-02T20:05:00Z",
                "void_reason": ""
            },
            {
                "voided_by_user_id": 1,
                "voided_at_utc": "2026-01-02T20:05:00Z",
                "void_reason": "   "
            },
        )
        for index, overrides in enumerate(invalid_recorded):
            with self.subTest(status="Recorded", overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_occurrence(
                        f"invalid-recorded-{index}",
                        **overrides
                    )
        for index, overrides in enumerate(invalid_voided):
            with self.subTest(status="Voided", overrides=overrides):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_occurrence(
                        f"invalid-voided-{index}",
                        status="Voided",
                        **overrides
                    )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_occurrence("invalid-status", status="Invalid")

    def test_submission_token_is_unique_and_required_indexes_exist(self):
        self.insert_occurrence("unique-token")
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_occurrence("unique-token")
        indexes = {
            row["name"]
            for row in self.conn.execute("PRAGMA index_list(behaviour_occurrences)")
        }
        self.assertTrue({
            "idx_behaviour_occurrences_client_occurred_at",
            "idx_behaviour_occurrences_status_occurred_at",
        }.issubset(indexes))
        index_columns = {
            index_name: [row["name"] for row in self.conn.execute(
                f"PRAGMA index_info({index_name})"
            )]
            for index_name in indexes
            if index_name.startswith("idx_behaviour_occurrences_")
        }
        self.assertEqual(
            index_columns["idx_behaviour_occurrences_client_occurred_at"],
            ["client_id", "occurred_at_utc"]
        )
        self.assertEqual(
            index_columns["idx_behaviour_occurrences_status_occurred_at"],
            ["status", "occurred_at_utc"]
        )

    def test_foreign_keys_are_enforced_and_migration_is_repeatable(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_occurrence("unknown-client", client_id=999)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_occurrence("unknown-recorder", recorded_by_user_id=999)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_occurrence(
                "unknown-voider",
                status="Voided",
                voided_by_user_id=999,
                voided_at_utc="2026-01-02T20:05:00Z",
                void_reason="Entered in error"
            )
        behaviour_migration.migrate(self.conn)
        self.insert_occurrence("after-repeat-migration")

    def test_operational_day_and_band_boundaries(self):
        cases = (
            ("2026-03-02T06:59:00Z", "2026-03-01", "Evening"),
            ("2026-03-02T07:00:00Z", "2026-03-02", "Night"),
            ("2026-03-02T15:29:00Z", "2026-03-02", "Night"),
            ("2026-03-02T15:30:00Z", "2026-03-02", "Day"),
            ("2026-03-02T23:29:00Z", "2026-03-02", "Day"),
            ("2026-03-02T23:30:00Z", "2026-03-02", "Evening"),
            ("2026-03-03T06:59:00Z", "2026-03-02", "Evening"),
            ("2026-03-03T07:00:00Z", "2026-03-03", "Night"),
        )
        for stored_utc, expected_day, expected_band in cases:
            with self.subTest(stored_utc=stored_utc):
                local = app.behaviour_utc_to_vancouver(stored_utc)
                self.assertEqual(
                    app.get_behaviour_operational_day(local).isoformat(),
                    expected_day
                )
                self.assertEqual(
                    app.get_behaviour_operational_band(local),
                    expected_band
                )

    def test_sunday_2300_belongs_to_monday_and_next_week(self):
        local = app.behaviour_utc_to_vancouver("2026-03-09T06:00:00Z")
        self.assertEqual(app.get_behaviour_operational_day(local).isoformat(), "2026-03-09")
        self.assertEqual(
            app.get_behaviour_operational_week_start(local).isoformat(),
            "2026-03-09"
        )

    def test_vancouver_utc_conversion_and_dst_validation(self):
        now = datetime(2026, 12, 1, tzinfo=timezone.utc)
        self.assertEqual(
            app.convert_vancouver_occurrence_input_to_utc(
                "2026-07-01T12:00",
                now_utc=now
            ),
            "2026-07-01T19:00:00Z"
        )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            app.convert_vancouver_occurrence_input_to_utc(
                "2024-03-10T02:30",
                now_utc=datetime(2024, 12, 1, tzinfo=timezone.utc)
            )
        with self.assertRaisesRegex(ValueError, "first or second"):
            app.convert_vancouver_occurrence_input_to_utc(
                "2024-11-03T01:30",
                now_utc=datetime(2024, 12, 1, tzinfo=timezone.utc)
            )
        self.assertEqual(
            app.convert_vancouver_occurrence_input_to_utc(
                "2024-11-03T01:30",
                "first",
                datetime(2024, 12, 1, tzinfo=timezone.utc)
            ),
            "2024-11-03T08:30:00Z"
        )
        self.assertEqual(
            app.convert_vancouver_occurrence_input_to_utc(
                "2024-11-03T01:30",
                "second",
                datetime(2024, 12, 1, tzinfo=timezone.utc)
            ),
            "2024-11-03T09:30:00Z"
        )

    def test_historical_time_and_future_time_validation(self):
        now = datetime(2026, 7, 1, 19, 0, tzinfo=timezone.utc)
        self.assertEqual(
            app.convert_vancouver_occurrence_input_to_utc(
                "2026-07-01T12:00", now_utc=now
            ),
            "2026-07-01T19:00:00Z"
        )
        with self.assertRaisesRegex(ValueError, "future"):
            app.convert_vancouver_occurrence_input_to_utc(
                "2026-07-01T12:01", now_utc=now
            )

    def test_active_user_client_category_and_void_role_validation(self):
        self.assertEqual(app.get_active_authenticated_user(self.conn, 1)["user_id"], 1)
        with self.assertRaises(PermissionError):
            app.get_active_authenticated_user(self.conn, 5)
        self.assertEqual(app.validate_active_behaviour_client(self.conn, 1)["client_id"], 1)
        with self.assertRaises(ValueError):
            app.validate_active_behaviour_client(self.conn, 2)
        for user_id in (1, 2, 3):
            self.assertEqual(
                app.validate_behaviour_void_authority(self.conn, user_id)["user_id"],
                user_id
            )
        with self.assertRaises(PermissionError):
            app.validate_behaviour_void_authority(self.conn, 4)
        with self.assertRaises(PermissionError):
            app.validate_behaviour_void_authority(self.conn, 5)
        flags = {field: 0 for field in app.BEHAVIOUR_CATEGORY_FIELDS}
        flags["self_harm"] = 1
        self.assertEqual(app.validate_behaviour_category_flags(flags)["self_harm"], 1)
        with self.assertRaises(ValueError):
            app.validate_behaviour_category_flags({field: 0 for field in flags})

    def test_category_validation_accepts_only_bool_or_exact_integer_flags(self):
        for selected_value in (True, 1):
            flags = {field: False for field in app.BEHAVIOUR_CATEGORY_FIELDS}
            flags["property_damage"] = selected_value
            self.assertEqual(
                app.validate_behaviour_category_flags(flags)["property_damage"],
                1
            )
        rejected_values = (0.0, 1.0, "0", "1", None, 2, -1, [], object())
        for invalid_value in rejected_values:
            flags = {field: 0 for field in app.BEHAVIOUR_CATEGORY_FIELDS}
            flags["aggression_towards_others"] = invalid_value
            flags["self_harm"] = 1
            with self.subTest(value=repr(invalid_value)):
                with self.assertRaises(ValueError):
                    app.validate_behaviour_category_flags(flags)
        incomplete = {field: 0 for field in app.BEHAVIOUR_CATEGORY_FIELDS[:-1]}
        with self.assertRaises(ValueError):
            app.validate_behaviour_category_flags(incomplete)
        extra = {field: 0 for field in app.BEHAVIOUR_CATEGORY_FIELDS}
        extra["unapproved"] = 1
        with self.assertRaises(ValueError):
            app.validate_behaviour_category_flags(extra)

    def test_behaviour_migration_does_not_create_staff_notice_tables(self):
        behaviour_migration.migrate(self.conn)
        self.assertIsNone(self.conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name LIKE 'staff_notice%'
        """).fetchone())


if __name__ == "__main__":
    unittest.main()
