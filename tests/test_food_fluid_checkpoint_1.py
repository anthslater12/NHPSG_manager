import os
import sqlite3
import sys
import tempfile
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import add_food_fluid_entries_table as migration


class FoodFluidCheckpointOneTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(
            self.temporary_directory.name,
            "food_fluid_checkpoint_one.db"
        )
        self.conn = sqlite3.connect(self.database_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open'
            );

            INSERT INTO users
                (user_id, username, full_name, role, active)
            VALUES
                (1, 'worker', 'Worker User', 'Support Worker', 1),
                (2, 'manager', 'Manager User', 'Program Manager', 1);

            INSERT INTO clients
                (client_id, client_name, active)
            VALUES
                (1, 'Client One', 1),
                (2, 'Client Two', 1);

            INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status)
            VALUES
                (10, 1, '2026-07-25', 'Day', 'Open'),
                (20, 2, '2026-07-25', 'Afternoon', 'Open');
        """)
        migration.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temporary_directory.cleanup()

    def entry_values(self, **overrides):
        values = {
            "shift_id": 10,
            "client_id": 1,
            "recorded_by_user_id": 1,
            "event_at_utc": "2026-07-25T17:00:00Z",
            "interaction_type": "Offered",
            "item_description": "Toast and water",
            "outcome": "All consumed",
            "additional_details": None,
            "submitted_at_utc": "2026-07-25T17:05:00Z",
            "submission_token": "token-1",
        }
        values.update(overrides)
        return values

    def insert_entry(self, **overrides):
        values = self.entry_values(**overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = self.conn.execute(
            f"""
            INSERT INTO food_fluid_entries ({columns})
            VALUES ({placeholders})
            """,
            tuple(values.values())
        )
        self.conn.commit()
        return cursor.lastrowid

    def assert_insert_rejected(self, **overrides):
        try:
            self.insert_entry(**overrides)
        except sqlite3.IntegrityError:
            self.conn.rollback()
        else:
            self.fail(
                "Expected SQLite to reject Food & Fluid values: "
                f"{overrides!r}"
            )

    def test_exact_columns_primary_key_nullability_and_defaults(self):
        columns = {
            row["name"]: row
            for row in self.conn.execute(
                "PRAGMA table_info(food_fluid_entries)"
            )
        }

        self.assertEqual(list(columns), [
            "food_fluid_entry_id",
            "shift_id",
            "client_id",
            "recorded_by_user_id",
            "event_at_utc",
            "interaction_type",
            "item_description",
            "outcome",
            "physically_thrown",
            "additional_details",
            "submitted_at_utc",
            "submission_token",
            "status",
            "voided_by_user_id",
            "voided_at_utc",
            "void_reason",
        ])
        self.assertEqual(columns["food_fluid_entry_id"]["type"], "INTEGER")
        self.assertEqual(columns["food_fluid_entry_id"]["pk"], 1)

        required = {
            "shift_id",
            "client_id",
            "recorded_by_user_id",
            "event_at_utc",
            "interaction_type",
            "item_description",
            "outcome",
            "physically_thrown",
            "submitted_at_utc",
            "submission_token",
            "status",
        }
        for name, column in columns.items():
            if name == "food_fluid_entry_id":
                continue
            self.assertEqual(column["notnull"], int(name in required), name)

        self.assertEqual(
            columns["physically_thrown"]["dflt_value"],
            "0"
        )
        self.assertEqual(columns["status"]["dflt_value"], "'Recorded'")
        for name in (
            "additional_details",
            "submitted_at_utc",
            "voided_by_user_id",
            "voided_at_utc",
            "void_reason",
        ):
            self.assertIsNone(columns[name]["dflt_value"], name)

    def test_exact_indexes_and_indexed_columns(self):
        indexes = {
            row["name"]: row
            for row in self.conn.execute(
                "PRAGMA index_list(food_fluid_entries)"
            )
        }
        expected_named_indexes = {
            "idx_food_fluid_entries_shift_event": (
                "shift_id",
                "event_at_utc",
            ),
            "idx_food_fluid_entries_client_event": (
                "client_id",
                "event_at_utc",
            ),
            "idx_food_fluid_entries_status_event": (
                "status",
                "event_at_utc",
            ),
        }
        for index_name, expected_columns in expected_named_indexes.items():
            self.assertIn(index_name, indexes)
            actual_columns = tuple(
                row["name"]
                for row in self.conn.execute(
                    f"PRAGMA index_info({index_name})"
                )
            )
            self.assertEqual(actual_columns, expected_columns)
            self.assertEqual(indexes[index_name]["unique"], 0)

        token_indexes = []
        for index_name, index in indexes.items():
            columns = tuple(
                row["name"]
                for row in self.conn.execute(
                    f"PRAGMA index_info({index_name})"
                )
            )
            if columns == ("submission_token",):
                token_indexes.append(index)
        self.assertEqual(len(token_indexes), 1)
        self.assertEqual(token_indexes[0]["unique"], 1)

        shift_indexes = {
            row["name"]: row
            for row in self.conn.execute("PRAGMA index_list(shifts)")
        }
        self.assertIn("idx_shifts_shift_client", shift_indexes)
        self.assertEqual(
            shift_indexes["idx_shifts_shift_client"]["unique"],
            1
        )
        self.assertEqual(
            tuple(
                row["name"]
                for row in self.conn.execute(
                    "PRAGMA index_info(idx_shifts_shift_client)"
                )
            ),
            ("shift_id", "client_id")
        )

    def test_exact_foreign_keys(self):
        foreign_keys = {
            (row["from"], row["table"], row["to"])
            for row in self.conn.execute(
                "PRAGMA foreign_key_list(food_fluid_entries)"
            )
        }
        self.assertEqual(foreign_keys, {
            ("shift_id", "shifts", "shift_id"),
            ("client_id", "shifts", "client_id"),
            ("client_id", "clients", "client_id"),
            ("recorded_by_user_id", "users", "user_id"),
            ("voided_by_user_id", "users", "user_id"),
        })

        composite_rows = [
            row
            for row in self.conn.execute(
                "PRAGMA foreign_key_list(food_fluid_entries)"
            )
            if row["table"] == "shifts"
        ]
        self.assertEqual(
            {(row["id"], row["seq"], row["from"], row["to"])
             for row in composite_rows},
            {
                (composite_rows[0]["id"], 0, "shift_id", "shift_id"),
                (composite_rows[0]["id"], 1, "client_id", "client_id"),
            }
        )

    def test_default_recorded_state_and_optional_values(self):
        entry_id = self.insert_entry()
        row = self.conn.execute("""
            SELECT *
            FROM food_fluid_entries
            WHERE food_fluid_entry_id = ?
        """, (entry_id,)).fetchone()

        self.assertEqual(row["physically_thrown"], 0)
        self.assertEqual(row["status"], "Recorded")
        self.assertIsNone(row["additional_details"])
        self.assertIsNone(row["voided_by_user_id"])
        self.assertIsNone(row["voided_at_utc"])
        self.assertIsNone(row["void_reason"])

    def test_interaction_outcome_and_physically_thrown_constraints(self):
        self.assert_insert_rejected(
            submission_token="invalid-interaction",
            interaction_type="Provided"
        )
        self.assert_insert_rejected(
            submission_token="invalid-outcome",
            outcome="Mostly consumed"
        )
        self.assert_insert_rejected(
            submission_token="unavailable-offered",
            interaction_type="Offered",
            outcome="Item not available"
        )
        self.insert_entry(
            submission_token="unavailable-requested",
            interaction_type="Requested",
            outcome="Item not available"
        )

        for invalid_boolean in (-1, 2):
            self.assert_insert_rejected(
                submission_token=f"invalid-thrown-{invalid_boolean}",
                physically_thrown=invalid_boolean
            )

        for invalid_outcome in ("All consumed", "Item not available"):
            self.assert_insert_rejected(
                submission_token=f"thrown-{invalid_outcome}",
                interaction_type=(
                    "Requested"
                    if invalid_outcome == "Item not available"
                    else "Offered"
                ),
                outcome=invalid_outcome,
                physically_thrown=1
            )

        for valid_outcome in ("Partially consumed", "Refused"):
            self.insert_entry(
                submission_token=f"valid-thrown-{valid_outcome}",
                outcome=valid_outcome,
                physically_thrown=1
            )

    def test_required_text_and_submission_token_constraints(self):
        for index, item_description in enumerate(("", " ", "\t\r\n")):
            self.assert_insert_rejected(
                submission_token=f"blank-item-{index}",
                item_description=item_description
            )

        self.assert_insert_rejected(
            submission_token="",
            item_description="Water"
        )
        self.assert_insert_rejected(
            submission_token="   ",
            item_description="Water"
        )
        self.assert_insert_rejected(
            submission_token="\t\r\n",
            item_description="Water"
        )

        self.insert_entry(submission_token="duplicate-token")
        self.assert_insert_rejected(
            submission_token="duplicate-token",
            item_description="Juice"
        )

    def test_canonical_utc_timestamp_constraints(self):
        invalid_timestamps = (
            "not-a-time",
            "2026-07-25T17:00:00+00:00",
            "2026-07-25T17:00:00",
            "2026-07-25 17:00:00Z",
            "2026-07-25T17:00:00.1Z",
            "2026-02-30T17:00:00Z",
            "2026-07-25T24:00:00Z",
            "2026-07-25T17:60:00Z",
        )
        for field_name in ("event_at_utc", "submitted_at_utc"):
            for index, invalid_timestamp in enumerate(invalid_timestamps):
                with self.subTest(
                    field_name=field_name,
                    value=invalid_timestamp
                ):
                    self.assert_insert_rejected(
                        submission_token=f"{field_name}-{index}",
                        **{field_name: invalid_timestamp}
                    )

        for index, invalid_timestamp in enumerate(invalid_timestamps):
            with self.subTest(
                field_name="voided_at_utc",
                value=invalid_timestamp
            ):
                self.assert_insert_rejected(
                    submission_token=f"void-time-{index}",
                    status="Voided",
                    voided_by_user_id=2,
                    voided_at_utc=invalid_timestamp,
                    void_reason="Entered in error"
                )

    def test_recorded_and_voided_state_constraints(self):
        invalid_recorded_metadata = (
            {"voided_by_user_id": 2},
            {"voided_at_utc": "2026-07-25T18:00:00Z"},
            {"void_reason": "Entered in error"},
        )
        for index, metadata in enumerate(invalid_recorded_metadata):
            self.assert_insert_rejected(
                submission_token=f"recorded-metadata-{index}",
                **metadata
            )

        invalid_voided_metadata = (
            {},
            {"voided_by_user_id": 2},
            {"voided_at_utc": "2026-07-25T18:00:00Z"},
            {"void_reason": "Entered in error"},
            {
                "voided_by_user_id": 2,
                "voided_at_utc": "2026-07-25T18:00:00Z",
                "void_reason": ""
            },
            {
                "voided_by_user_id": 2,
                "voided_at_utc": "2026-07-25T18:00:00Z",
                "void_reason": "   "
            },
            {
                "voided_by_user_id": 2,
                "voided_at_utc": "2026-07-25T18:00:00Z",
                "void_reason": "\t\r\n"
            },
        )
        for index, metadata in enumerate(invalid_voided_metadata):
            self.assert_insert_rejected(
                submission_token=f"voided-metadata-{index}",
                status="Voided",
                **metadata
            )

        self.assert_insert_rejected(
            submission_token="invalid-status",
            status="Deleted"
        )
        self.insert_entry(
            submission_token="valid-voided",
            status="Voided",
            voided_by_user_id=2,
            voided_at_utc="2026-07-25T18:00:00Z",
            void_reason="Incorrect duplicate"
        )

    def test_foreign_keys_and_shift_client_pair_are_enforced(self):
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )
        self.assert_insert_rejected(
            submission_token="missing-shift",
            shift_id=999
        )
        self.assert_insert_rejected(
            submission_token="missing-client",
            client_id=999
        )
        self.assert_insert_rejected(
            submission_token="missing-recorder",
            recorded_by_user_id=999
        )
        self.assert_insert_rejected(
            submission_token="missing-voider",
            status="Voided",
            voided_by_user_id=999,
            voided_at_utc="2026-07-25T18:00:00Z",
            void_reason="Entered in error"
        )
        self.assert_insert_rejected(
            submission_token="mismatched-shift-client",
            shift_id=10,
            client_id=2
        )

    def test_migration_is_idempotent(self):
        migration.migrate(self.conn)
        migration.migrate(self.conn)

        self.insert_entry(submission_token="after-repeat-migration")
        self.assertEqual(
            self.conn.execute("""
                SELECT COUNT(*)
                FROM food_fluid_entries
                WHERE submission_token = 'after-repeat-migration'
            """).fetchone()[0],
            1
        )


if __name__ == "__main__":
    unittest.main()
