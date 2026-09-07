import os
import sqlite3
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_behaviour_occurrences_table as migration
import app


class BehaviourLifecycleMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users (user_id, role) VALUES
                (1, 'Admin'),
                (2, 'Support Worker');
            INSERT INTO clients (client_id) VALUES (1);
        """)

    def tearDown(self):
        self.conn.close()

    def insert_legacy_schema(self):
        self.conn.executescript("""
            CREATE TABLE behaviour_occurrences (
                behaviour_occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL REFERENCES clients(client_id),
                occurred_at_utc TEXT NOT NULL,
                aggression_towards_others INTEGER NOT NULL DEFAULT 0,
                injury_to_others INTEGER NOT NULL DEFAULT 0,
                self_harm INTEGER NOT NULL DEFAULT 0,
                injury_to_self INTEGER NOT NULL DEFAULT 0,
                property_damage INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                recorded_by_user_id INTEGER NOT NULL REFERENCES users(user_id),
                recorded_at_utc TEXT NOT NULL,
                submission_token TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'Recorded'
                    CHECK (status IN ('Recorded', 'Voided')),
                voided_by_user_id INTEGER REFERENCES users(user_id),
                voided_at_utc TEXT,
                void_reason TEXT,
                shift_id INTEGER,
                record_format TEXT NOT NULL DEFAULT 'V1'
                    CHECK (record_format IN ('V1', 'ABC')),
                CHECK (
                    status = 'Recorded'
                    OR (
                        status = 'Voided'
                        AND voided_by_user_id IS NOT NULL
                        AND voided_at_utc IS NOT NULL
                        AND length(trim(void_reason)) > 0
                    )
                )
            );
            CREATE INDEX idx_behaviour_occurrences_client_occurred_at
                ON behaviour_occurrences (client_id, occurred_at_utc);
            CREATE INDEX idx_behaviour_occurrences_status_occurred_at
                ON behaviour_occurrences (status, occurred_at_utc);
        """)
        self.conn.execute("""
            INSERT INTO behaviour_occurrences (
                client_id, occurred_at_utc, aggression_towards_others,
                recorded_by_user_id, recorded_at_utc, submission_token, status
            ) VALUES (1, '2026-01-02T20:00:00Z', 1, 2,
                      '2026-01-02T20:01:00Z', 'legacy-recorded', 'Recorded')
        """)
        self.conn.execute("""
            INSERT INTO behaviour_occurrences (
                client_id, occurred_at_utc, aggression_towards_others,
                recorded_by_user_id, recorded_at_utc, submission_token, status,
                voided_by_user_id, voided_at_utc, void_reason
            ) VALUES (1, '2026-01-03T20:00:00Z', 1, 2,
                      '2026-01-03T20:01:00Z', 'legacy-voided', 'Voided', 1,
                      '2026-01-03T20:05:00Z', 'Entered in error')
        """)
        self.conn.commit()

    def insert_phase_one_schema(self):
        self.conn.executescript("""
            CREATE TABLE behaviour_occurrences (
                behaviour_occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL REFERENCES clients(client_id),
                occurred_at_utc TEXT NOT NULL,
                aggression_towards_others INTEGER NOT NULL DEFAULT 0,
                injury_to_others INTEGER NOT NULL DEFAULT 0,
                self_harm INTEGER NOT NULL DEFAULT 0,
                injury_to_self INTEGER NOT NULL DEFAULT 0,
                property_damage INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                recorded_by_user_id INTEGER NOT NULL REFERENCES users(user_id),
                recorded_at_utc TEXT NOT NULL,
                submission_token TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'Recorded'
                    CHECK (status IN ('In Progress', 'Completed', 'Recorded', 'Voided')),
                voided_by_user_id INTEGER REFERENCES users(user_id),
                voided_at_utc TEXT,
                void_reason TEXT,
                completed_at_utc TEXT,
                completed_by_user_id INTEGER REFERENCES users(user_id),
                shift_id INTEGER,
                record_format TEXT NOT NULL DEFAULT 'V1'
                    CHECK (record_format IN ('V1', 'ABC')),
                CHECK (
                    (status IN ('In Progress', 'Completed', 'Recorded')
                     AND voided_by_user_id IS NULL
                     AND voided_at_utc IS NULL
                     AND void_reason IS NULL)
                    OR
                    (status = 'Voided'
                     AND voided_by_user_id IS NOT NULL
                     AND voided_at_utc IS NOT NULL
                     AND length(trim(void_reason)) > 0)
                )
            );
        """)
        self.conn.execute("""
            INSERT INTO behaviour_occurrences (
                client_id, occurred_at_utc, aggression_towards_others,
                recorded_by_user_id, recorded_at_utc, submission_token, status
            ) VALUES (1, '2026-01-02T20:00:00Z', 1, 2,
                      '2026-01-02T20:01:00Z', 'phase-one-recorded', 'Recorded')
        """)
        self.conn.commit()

    def insert_new_status(
        self,
        status,
        token,
        completed_at_utc=None,
        completed_by_user_id=None,
        voided_by_user_id=None,
        voided_at_utc=None,
        void_reason=None
    ):
        self.conn.execute("""
            INSERT INTO behaviour_occurrences (
                client_id, occurred_at_utc, aggression_towards_others,
                recorded_by_user_id, recorded_at_utc, submission_token, status,
                completed_at_utc, completed_by_user_id, voided_by_user_id,
                voided_at_utc, void_reason
            ) VALUES (1, '2026-01-04T20:00:00Z', 1, 2,
                      '2026-01-04T20:01:00Z', ?, ?, ?, ?, ?, ?, ?)
        """, (
            token,
            status,
            completed_at_utc,
            completed_by_user_id,
            voided_by_user_id,
            voided_at_utc,
            void_reason
        ))
        self.conn.commit()

    def test_existing_schema_is_upgraded_without_changing_legacy_statuses(self):
        self.insert_legacy_schema()

        migration.migrate(self.conn)

        rows = self.conn.execute("""
            SELECT status, completed_at_utc, completed_by_user_id,
                   voided_by_user_id, voided_at_utc, void_reason
            FROM behaviour_occurrences
            ORDER BY behaviour_occurrence_id
        """).fetchall()
        self.assertEqual([row["status"] for row in rows], ["Recorded", "Voided"])
        self.assertIsNone(rows[0]["completed_at_utc"])
        self.assertIsNone(rows[0]["completed_by_user_id"])
        self.assertEqual(
            tuple(rows[1][key] for key in (
                "voided_by_user_id", "voided_at_utc", "void_reason"
            )),
            (1, "2026-01-03T20:05:00Z", "Entered in error")
        )

    def test_migration_is_idempotent(self):
        self.insert_legacy_schema()
        migration.migrate(self.conn)
        first_rows = self.conn.execute(
            "SELECT * FROM behaviour_occurrences ORDER BY behaviour_occurrence_id"
        ).fetchall()
        first_columns = self.conn.execute(
            "PRAGMA table_info(behaviour_occurrences)"
        ).fetchall()

        migration.migrate(self.conn)

        self.assertEqual(
            [tuple(row) for row in self.conn.execute(
                "SELECT * FROM behaviour_occurrences ORDER BY behaviour_occurrence_id"
            ).fetchall()],
            [tuple(row) for row in first_rows]
        )
        self.assertEqual(
            [tuple(row) for row in self.conn.execute(
                "PRAGMA table_info(behaviour_occurrences)"
            ).fetchall()],
            [tuple(row) for row in first_columns]
        )

    def test_phase_one_schema_is_strengthened_when_columns_already_exist(self):
        self.insert_phase_one_schema()

        migration.migrate(self.conn)

        table_sql = self.conn.execute("""
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'behaviour_occurrences'
        """).fetchone()[0]
        self.assertIn("completed_at_utc IS NULL OR", table_sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_new_status(
                "Completed",
                "phase-one-invalid-completed",
                completed_at_utc="2026-01-04T20:02:00Z"
            )

    def test_new_lifecycle_statuses_are_accepted(self):
        migration.migrate(self.conn)

        self.insert_new_status("In Progress", "in-progress")
        self.insert_new_status(
            "Completed",
            "completed",
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1
        )

        statuses = self.conn.execute(
            "SELECT status FROM behaviour_occurrences ORDER BY behaviour_occurrence_id"
        ).fetchall()
        self.assertEqual([row[0] for row in statuses], ["In Progress", "Completed"])

    def test_completed_requires_actor_and_canonical_timestamp(self):
        migration.migrate(self.conn)

        self.insert_new_status(
            "Completed",
            "valid-completed",
            completed_at_utc="2026-01-04T20:02:00Z",
            completed_by_user_id=1
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_new_status(
                "Completed",
                "missing-completed-actor",
                completed_at_utc="2026-01-04T20:02:00Z"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_new_status(
                "Completed",
                "missing-completed-time",
                completed_by_user_id=1
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_new_status(
                "Completed",
                "noncanonical-completed-time",
                completed_at_utc="2026-01-04T20:02:00+00:00",
                completed_by_user_id=1
            )

    def test_non_completed_statuses_reject_completion_metadata(self):
        migration.migrate(self.conn)

        invalid_metadata = (
            ("In Progress", "in-progress-metadata"),
            ("Recorded", "recorded-metadata"),
            ("Voided", "voided-metadata"),
        )
        for status, token in invalid_metadata:
            with self.subTest(status=status):
                values = {
                    "completed_at_utc": "2026-01-04T20:02:00Z",
                    "completed_by_user_id": 1,
                }
                if status == "Voided":
                    values.update({
                        "voided_by_user_id": 1,
                        "voided_at_utc": "2026-01-04T20:03:00Z",
                        "void_reason": "Entered in error",
                    })
                with self.assertRaises(sqlite3.IntegrityError):
                    self.insert_new_status(status, token, **values)

    def test_completion_metadata_is_nullable_for_legacy_rows_and_helpers_classify_statuses(self):
        migration.migrate(self.conn)

        columns = {
            row["name"]: row
            for row in self.conn.execute(
                "PRAGMA table_info(behaviour_occurrences)"
            )
        }
        self.assertEqual(columns["completed_at_utc"]["notnull"], 0)
        self.assertEqual(columns["completed_by_user_id"]["notnull"], 0)
        self.assertTrue(app.is_behaviour_occurrence_editable("In Progress"))
        self.assertFalse(app.is_behaviour_occurrence_editable("Completed"))
        self.assertTrue(app.is_behaviour_occurrence_finalized("Completed"))
        self.assertTrue(app.is_behaviour_occurrence_finalized("Recorded"))
        self.assertFalse(app.is_behaviour_occurrence_finalized("Voided"))

    def test_expected_indexes_are_recreated_and_no_behaviour_triggers_exist(self):
        self.insert_legacy_schema()

        migration.migrate(self.conn)

        indexes = {
            row["name"]
            for row in self.conn.execute("PRAGMA index_list(behaviour_occurrences)")
        }
        self.assertTrue({
            "idx_behaviour_occurrences_client_occurred_at",
            "idx_behaviour_occurrences_status_occurred_at",
        }.issubset(indexes))
        triggers = self.conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'trigger'
              AND tbl_name = 'behaviour_occurrences'
        """).fetchall()
        self.assertEqual(triggers, [])

    def test_failed_lifecycle_rebuild_rolls_back_without_stranding_legacy_table(self):
        self.insert_phase_one_schema()
        original_create_table = migration._create_table

        def fail_create_table(conn):
            raise RuntimeError("forced lifecycle rebuild failure")

        migration._create_table = fail_create_table
        try:
            with self.assertRaises(RuntimeError):
                migration.migrate(self.conn)
        finally:
            migration._create_table = original_create_table

        table_names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("behaviour_occurrences", table_names)
        self.assertNotIn("behaviour_occurrences_lifecycle_legacy", table_names)
        row = self.conn.execute(
            "SELECT status, submission_token FROM behaviour_occurrences"
        ).fetchone()
        self.assertEqual(tuple(row), ("Recorded", "phase-one-recorded"))


if __name__ == "__main__":
    unittest.main()
