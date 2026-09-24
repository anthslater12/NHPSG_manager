import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import add_behaviour_occurrences_table as behaviour_migration
import add_behaviour_setting_events_tables as migration
import app


EXPECTED_OPTIONS = {
    "POOR_SLEEP": ("Poor or insufficient sleep", "PHYSIOLOGICAL_BIOLOGICAL", 10, "NORMAL"),
    "SLEEP_ROUTINE_CHANGE": ("Change in sleep routine", "PHYSIOLOGICAL_BIOLOGICAL", 20, "NORMAL"),
    "HUNGER": ("Hunger", "PHYSIOLOGICAL_BIOLOGICAL", 30, "NORMAL"),
    "THIRST": ("Thirst", "PHYSIOLOGICAL_BIOLOGICAL", 40, "NORMAL"),
    "MEAL_ROUTINE_CHANGE": ("Missed or delayed meal / change in eating routine", "PHYSIOLOGICAL_BIOLOGICAL", 50, "NORMAL"),
    "DIETARY_CHANGE": ("Dietary change or possible food sensitivity", "PHYSIOLOGICAL_BIOLOGICAL", 60, "NORMAL"),
    "PAIN_DISCOMFORT": ("Pain or physical discomfort", "PHYSIOLOGICAL_BIOLOGICAL", 70, "NORMAL"),
    "FEELING_UNWELL": ("Feeling unwell or illness symptoms", "PHYSIOLOGICAL_BIOLOGICAL", 80, "NORMAL"),
    "BOWEL_CONCERNS": ("Bowel movement or constipation concerns", "PHYSIOLOGICAL_BIOLOGICAL", 90, "NORMAL"),
    "OTHER_HEALTH_PHYSICAL": ("Other health or physical factor", "PHYSIOLOGICAL_BIOLOGICAL", 100, "OTHER"),
    "BRIGHT_LIGHT": ("Bright light or glare", "PHYSICAL_ENVIRONMENTAL", 10, "NORMAL"),
    "LOUD_UNEXPECTED_NOISE": ("Loud or unexpected noise", "PHYSICAL_ENVIRONMENTAL", 20, "NORMAL"),
    "TOO_HOT": ("Too hot or overheated", "PHYSICAL_ENVIRONMENTAL", 30, "NORMAL"),
    "WET_CLOTHING": ("Wet clothing or footwear", "PHYSICAL_ENVIRONMENTAL", 40, "NORMAL"),
    "TOO_COLD": ("Too cold", "PHYSICAL_ENVIRONMENTAL", 50, "NORMAL"),
    "SEATING_LOCATION_CHANGE": ("Preferred seating or location unavailable or changed", "PHYSICAL_ENVIRONMENTAL", 60, "NORMAL"),
    "LIMITED_SENSORY_INPUT": ("Limited access to preferred sensory input or deep pressure", "PHYSICAL_ENVIRONMENTAL", 70, "NORMAL"),
    "BUSY_CLUTTERED_ENVIRONMENT": ("Busy, crowded, cluttered, or highly active environment", "PHYSICAL_ENVIRONMENTAL", 80, "NORMAL"),
    "UNPREDICTABLE_ENVIRONMENT": ("Unpredictable environment or routine", "PHYSICAL_ENVIRONMENTAL", 90, "NORMAL"),
    "HIGH_LANGUAGE_DEMANDS": ("High verbal/language demands", "PHYSICAL_ENVIRONMENTAL", 100, "NORMAL"),
    "LIMITED_MOVEMENT": ("Limited access to movement or movement breaks", "PHYSICAL_ENVIRONMENTAL", 110, "NORMAL"),
    "LONG_NONPREFERRED_ACTIVITY": ("Long period of quiet or non-preferred activity", "PHYSICAL_ENVIRONMENTAL", 120, "NORMAL"),
    "UNFAMILIAR_DIFFICULT_ACTIVITY": ("Unfamiliar or difficult activity", "PHYSICAL_ENVIRONMENTAL", 130, "NORMAL"),
    "OTHER_ENVIRONMENTAL": ("Other environmental factor", "PHYSICAL_ENVIRONMENTAL", 140, "OTHER"),
    "UNEXPECTED_ROUTINE_CHANGE": ("Unexpected schedule or routine change", "ROUTINE_TRANSITION", 10, "NORMAL"),
    "PREFERRED_ACTIVITY_ENDING": ("Preferred activity ending", "ROUTINE_TRANSITION", 20, "NORMAL"),
    "PREFERRED_TO_NONPREFERRED": ("Transition from preferred to non-preferred activity", "ROUTINE_TRANSITION", 30, "NORMAL"),
    "INSUFFICIENT_TRANSITION_TIME": ("Insufficient transition time", "ROUTINE_TRANSITION", 40, "NORMAL"),
    "WAITING_DELAY": ("Waiting or delay", "ROUTINE_TRANSITION", 50, "NORMAL"),
    "ROUTINE_INTERRUPTED": ("Routine interrupted", "ROUTINE_TRANSITION", 60, "NORMAL"),
    "OTHER_ROUTINE_TRANSITION": ("Other transition/routine factor", "ROUTINE_TRANSITION", 70, "OTHER"),
    "REQUEST_UNAVAILABLE": ("Request or preferred activity unavailable/refused", "SOCIAL_INTERPERSONAL", 10, "NORMAL"),
    "CORRECTION_REPRIMAND": ("Correction, discipline, or reprimand", "SOCIAL_INTERPERSONAL", 20, "NORMAL"),
    "STAFFING_CHANGE": ("Staffing change", "SOCIAL_INTERPERSONAL", 30, "NORMAL"),
    "FAMILIAR_STAFF_UNAVAILABLE": ("Familiar staff unavailable or on break", "SOCIAL_INTERPERSONAL", 40, "NORMAL"),
    "DIFFICULT_INTERACTION": ("Difficult interaction with another person", "SOCIAL_INTERPERSONAL", 50, "NORMAL"),
    "LIMITED_POSITIVE_INTERACTION": ("Limited positive social interaction", "SOCIAL_INTERPERSONAL", 60, "NORMAL"),
    "PERSONAL_CARE_HYGIENE": ("Personal care or hygiene routine", "SOCIAL_INTERPERSONAL", 70, "NORMAL"),
    "OTHER_SOCIAL_INTERPERSONAL": ("Other social/interpersonal factor", "SOCIAL_INTERPERSONAL", 80, "OTHER"),
    "NONE_OBSERVED": ("None observed", "SPECIAL", 10, "NONE"),
    "INFORMATION_UNKNOWN": ("Information not known or unavailable", "SPECIAL", 20, "UNKNOWN"),
}


class BehaviourSettingEventsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = self._base_connection()
        behaviour_migration.migrate(self.conn)
        migration.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _base_connection(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users (user_id, role) VALUES (1, 'Admin');
            INSERT INTO clients (client_id, client_name) VALUES (1, 'Client');
        """)
        return conn

    def _insert_occurrence(self, token="setting-events-occurrence"):
        return self.conn.execute("""
            INSERT INTO behaviour_occurrences (
                client_id, occurred_at_utc, aggression_towards_others,
                recorded_by_user_id, recorded_at_utc, submission_token, status
            ) VALUES (1, '2026-01-02T20:00:00Z', 1, 1,
                      '2026-01-02T20:01:00Z', ?, 'Recorded')
        """, (token,)).lastrowid

    def _option_id(self, code):
        return self.conn.execute(
            "SELECT setting_event_option_id FROM "
            "behaviour_setting_event_options WHERE code = ?",
            (code,),
        ).fetchone()[0]

    def _migration_snapshot(self):
        schema = tuple(
            tuple(row)
            for row in self.conn.execute("""
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
            """)
        )
        indexes = tuple(sorted(
            (table_name, *tuple(row))
            for table_name in (
                migration.OPTION_TABLE,
                migration.JUNCTION_TABLE,
            )
            for row in self.conn.execute(
                f"PRAGMA index_list({table_name})"
            )
        ))
        return {
            "options": tuple(
                tuple(row)
                for row in self.conn.execute(
                    f"SELECT * FROM {migration.OPTION_TABLE} "
                    "ORDER BY setting_event_option_id"
                )
            ),
            "junction": tuple(
                tuple(row)
                for row in self.conn.execute(
                    f"SELECT * FROM {migration.JUNCTION_TABLE} "
                    "ORDER BY behaviour_occurrence_setting_event_id"
                )
            ),
            "sqlite_sequence": tuple(
                tuple(row)
                for row in self.conn.execute(
                    "SELECT name, seq FROM sqlite_sequence ORDER BY name"
                )
            ),
            "indexes": indexes,
            "schema": schema,
        }

    def test_tables_have_expected_columns_foreign_keys_and_indexes(self):
        self.assertEqual(
            {
                row["name"]
                for row in self.conn.execute(
                    "PRAGMA table_info(behaviour_setting_event_options)"
                )
            },
            migration.OPTION_COLUMNS,
        )
        self.assertEqual(
            {
                row["name"]
                for row in self.conn.execute(
                    "PRAGMA table_info(behaviour_occurrence_setting_events)"
                )
            },
            migration.JUNCTION_COLUMNS,
        )

        foreign_keys = {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in self.conn.execute(
                "PRAGMA foreign_key_list(behaviour_occurrence_setting_events)"
            )
        }
        self.assertEqual(foreign_keys, {
            (
                "behaviour_occurrence_id",
                "behaviour_occurrences",
                "behaviour_occurrence_id",
                "CASCADE",
            ),
            (
                "setting_event_option_id",
                "behaviour_setting_event_options",
                "setting_event_option_id",
                "RESTRICT",
            ),
        })

        indexes = {
            row["name"]
            for table in (
                "behaviour_setting_event_options",
                "behaviour_occurrence_setting_events",
            )
            for row in self.conn.execute(f"PRAGMA index_list({table})")
        }
        self.assertTrue({
            name for name, _ in migration.INDEXES
        }.issubset(indexes))

        for index_name, indexed_columns in migration.INDEXES:
            table_name, columns = indexed_columns.split("(", 1)
            expected_columns = tuple(
                column.strip().rstrip(")")
                for column in columns.split(",")
            )
            actual_columns = tuple(
                row[2]
                for row in self.conn.execute(
                    "SELECT * FROM pragma_index_info(?) ORDER BY seqno",
                    (index_name,),
                )
            )
            self.assertEqual(actual_columns, expected_columns)
            self.assertEqual(
                self.conn.execute(
                    f"SELECT tbl_name FROM sqlite_master "
                    "WHERE type = 'index' AND name = ?",
                    (index_name,),
                ).fetchone()[0],
                table_name,
            )

    def test_column_metadata_matches_authoritative_definition(self):
        expected = {
            "behaviour_setting_event_options": migration.OPTION_COLUMN_METADATA,
            "behaviour_occurrence_setting_events": migration.JUNCTION_COLUMN_METADATA,
        }
        for table_name, metadata in expected.items():
            with self.subTest(table_name=table_name):
                actual = {
                    row[1]: (row[2], row[3], row[5])
                    for row in self.conn.execute(
                        f"PRAGMA table_info({table_name})"
                    )
                }
                self.assertEqual(actual, metadata)

    def test_all_41_options_are_seeded_with_stable_codes_and_kinds(self):
        rows = self.conn.execute("""
            SELECT code, category, label, display_order, option_kind
            FROM behaviour_setting_event_options
            ORDER BY category, display_order
        """).fetchall()
        self.assertEqual(len(rows), 41)
        self.assertEqual(
            {
                row["code"]: (
                    row["label"],
                    row["category"],
                    row["display_order"],
                    row["option_kind"],
                )
                for row in rows
            },
            EXPECTED_OPTIONS,
        )

    def test_option_and_other_text_checks_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""
                INSERT INTO behaviour_setting_event_options
                    (code, category, label, display_order, active, option_kind)
                VALUES ('BAD_ACTIVE', 'SPECIAL', 'Bad', 1, 2, 'NORMAL')
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""
                INSERT INTO behaviour_setting_event_options
                    (code, category, label, display_order, active, option_kind)
                VALUES ('BAD_KIND', 'SPECIAL', 'Bad', 1, 1, 'INVALID')
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""
                INSERT INTO behaviour_setting_event_options
                    (code, category, label, display_order, active, option_kind)
                VALUES ('   ', 'SPECIAL', 'Bad', 1, 1, 'NORMAL')
            """)

        occurrence_id = self._insert_occurrence()
        option_id = self._option_id("OTHER_HEALTH_PHYSICAL")
        valid = (occurrence_id, option_id, "Physical discomfort")
        self.conn.execute("""
            INSERT INTO behaviour_occurrence_setting_events
                (behaviour_occurrence_id, setting_event_option_id, other_text)
            VALUES (?, ?, ?)
        """, valid)
        for invalid_text in ("   ", " x ", "x" * 1001):
            with self.subTest(invalid_text=repr(invalid_text)):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute("""
                        INSERT INTO behaviour_occurrence_setting_events
                            (behaviour_occurrence_id, setting_event_option_id, other_text)
                        VALUES (?, ?, ?)
                    """, (occurrence_id, self._option_id("OTHER_ENVIRONMENTAL"), invalid_text))

    def test_junction_has_unique_occurrence_option_pair_and_fk_delete_rules(self):
        occurrence_id = self._insert_occurrence()
        option_id = self._option_id("HUNGER")
        values = (occurrence_id, option_id, None)
        self.conn.execute("""
            INSERT INTO behaviour_occurrence_setting_events
                (behaviour_occurrence_id, setting_event_option_id, other_text)
            VALUES (?, ?, ?)
        """, values)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""
                INSERT INTO behaviour_occurrence_setting_events
                    (behaviour_occurrence_id, setting_event_option_id)
                VALUES (?, ?)
            """, (occurrence_id, option_id))

        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "DELETE FROM behaviour_setting_event_options WHERE setting_event_option_id = ?",
                (option_id,),
            )
        self.conn.execute(
            "DELETE FROM behaviour_occurrences WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM behaviour_occurrence_setting_events"
            ).fetchone()[0],
            0,
        )

    def test_migration_and_seed_are_idempotent_and_non_destructive(self):
        self.conn.execute("""
            UPDATE behaviour_setting_event_options
            SET active = 0, label = 'Locally maintained label', display_order = 999
            WHERE code = 'POOR_SLEEP'
        """)
        before_count = self.conn.execute(
            "SELECT COUNT(*) FROM behaviour_setting_event_options"
        ).fetchone()[0]

        migration.migrate(self.conn)

        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM behaviour_setting_event_options"
            ).fetchone()[0],
            before_count,
        )
        row = self.conn.execute("""
            SELECT active, label, display_order, category, option_kind
            FROM behaviour_setting_event_options
            WHERE code = 'POOR_SLEEP'
        """).fetchone()
        self.assertEqual(
            tuple(row),
            (0, "Locally maintained label", 999,
             "PHYSIOLOGICAL_BIOLOGICAL", "NORMAL"),
        )

    def test_repeated_migration_preserves_complete_database_snapshot_and_sequence(self):
        occurrence_id = self._insert_occurrence("snapshot-idempotence")
        self.conn.execute(
            "INSERT INTO behaviour_occurrence_setting_events "
            "(behaviour_occurrence_id, setting_event_option_id) VALUES (?, ?)",
            (occurrence_id, self._option_id("HUNGER")),
        )
        self.conn.commit()

        first_snapshot = self._migration_snapshot()
        first_sequence = self.conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?",
            (migration.OPTION_TABLE,),
        ).fetchone()[0]
        self.assertEqual(
            self.conn.execute(
                f"SELECT COUNT(*) FROM {migration.OPTION_TABLE}"
            ).fetchone()[0],
            41,
        )
        self.assertEqual(first_sequence, 41)

        for _ in range(2):
            migration.migrate(self.conn)
            self.assertEqual(
                self.conn.execute(
                    f"SELECT COUNT(*) FROM {migration.OPTION_TABLE}"
                ).fetchone()[0],
                41,
            )
            self.assertEqual(
                self.conn.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = ?",
                    (migration.OPTION_TABLE,),
                ).fetchone()[0],
                first_sequence,
            )
            self.assertEqual(self._migration_snapshot(), first_snapshot)

    def test_historical_behaviour_rows_are_preserved_without_backfill(self):
        conn = self._base_connection()
        behaviour_migration.migrate(conn)
        occurrence_id = conn.execute("""
            INSERT INTO behaviour_occurrences (
                client_id, occurred_at_utc, aggression_towards_others,
                recorded_by_user_id, recorded_at_utc, submission_token, status
            ) VALUES (1, '2026-01-02T20:00:00Z', 1, 1,
                      '2026-01-02T20:01:00Z', 'historical', 'Recorded')
            RETURNING behaviour_occurrence_id
        """).fetchone()[0]
        original = tuple(conn.execute(
            "SELECT occurred_at_utc, submission_token, status "
            "FROM behaviour_occurrences WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        ).fetchone())

        migration.migrate(conn)

        self.assertEqual(tuple(conn.execute(
            "SELECT occurred_at_utc, submission_token, status "
            "FROM behaviour_occurrences WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()), original)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM behaviour_occurrence_setting_events"
            ).fetchone()[0],
            0,
        )
        conn.close()

    def test_incompatible_existing_option_schema_is_rejected_without_partial_migration(self):
        conn = self._base_connection()
        behaviour_migration.migrate(conn)
        conn.execute("""
            CREATE TABLE behaviour_setting_event_options (
                setting_event_option_id INTEGER PRIMARY KEY
            )
        """)
        conn.commit()

        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            migration.migrate(conn)

        self.assertIsNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'behaviour_occurrence_setting_events'"
        ).fetchone())
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM pragma_index_list "
                "WHERE name LIKE 'idx_behaviour_%setting_event%'"
            ).fetchone()[0],
            0,
        )
        conn.close()

    def test_incompatible_existing_junction_schema_is_rejected(self):
        conn = self._base_connection()
        behaviour_migration.migrate(conn)
        migration._create_option_table(conn)
        conn.execute("""
            CREATE TABLE behaviour_occurrence_setting_events (
                behaviour_occurrence_setting_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                behaviour_occurrence_id INTEGER NOT NULL,
                setting_event_option_id INTEGER NOT NULL,
                other_text TEXT NOT NULL,
                FOREIGN KEY (behaviour_occurrence_id)
                    REFERENCES behaviour_occurrences(behaviour_occurrence_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (setting_event_option_id)
                    REFERENCES behaviour_setting_event_options(setting_event_option_id)
                    ON DELETE RESTRICT,
                UNIQUE (behaviour_occurrence_id, setting_event_option_id)
            )
        """)
        conn.commit()

        with self.assertRaisesRegex(RuntimeError, "metadata"):
            migration.migrate(conn)

        self.assertEqual(
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = ?",
                (migration.JUNCTION_TABLE,),
            ).fetchone()[0].count("other_text TEXT NOT NULL"),
            1,
        )
        conn.close()

    def test_incompatible_seed_category_and_kind_are_rejected_without_overwrite(self):
        for field, value in (
            ("category", "WRONG_CATEGORY"),
            ("option_kind", "OTHER"),
        ):
            with self.subTest(field=field):
                conn = self._base_connection()
                behaviour_migration.migrate(conn)
                migration._create_option_table(conn)
                conn.execute(
                    "INSERT INTO behaviour_setting_event_options "
                    "(code, category, label, display_order, active, option_kind) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "POOR_SLEEP",
                        value if field == "category" else "PHYSIOLOGICAL_BIOLOGICAL",
                        "Custom label",
                        444,
                        0,
                        value if field == "option_kind" else "NORMAL",
                    ),
                )
                conn.commit()

                with self.assertRaisesRegex(RuntimeError, "incompatible identity"):
                    migration.migrate(conn)

                row = conn.execute(
                    "SELECT category, option_kind FROM "
                    "behaviour_setting_event_options WHERE code = 'POOR_SLEEP'"
                ).fetchone()
                self.assertEqual(
                    tuple(row),
                    (
                        value if field == "category" else "PHYSIOLOGICAL_BIOLOGICAL",
                        value if field == "option_kind" else "NORMAL",
                    ),
                )
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = ?",
                    (migration.JUNCTION_TABLE,),
                ).fetchone())
                conn.close()

    def test_mid_migration_failure_rolls_back_all_owned_work(self):
        conn = self._base_connection()
        behaviour_migration.migrate(conn)
        original_seed = migration._seed_options

        def failing_seed(connection):
            original_seed(connection)
            raise RuntimeError("forced Setting Events seed failure")

        migration._seed_options = failing_seed
        try:
            with self.assertRaisesRegex(RuntimeError, "forced"):
                migration.migrate(conn)
        finally:
            migration._seed_options = original_seed

        for table_name in (migration.OPTION_TABLE, migration.JUNCTION_TABLE):
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone())
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'index' AND name LIKE 'idx_behaviour_%setting_event%'"
            ).fetchone()[0],
            0,
        )
        conn.close()

    def test_caller_transaction_first_creation_can_be_rolled_back(self):
        conn = self._base_connection()
        behaviour_migration.migrate(conn)
        conn.execute("BEGIN")

        migration.migrate(conn)

        self.assertTrue(conn.in_transaction)
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (migration.OPTION_TABLE,),
        ).fetchone())
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM behaviour_setting_event_options"
            ).fetchone()[0],
            41,
        )
        conn.rollback()
        for table_name in (migration.OPTION_TABLE, migration.JUNCTION_TABLE):
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone())
        conn.close()

    def test_get_db_applies_behaviour_setting_events_upgrade(self):
        path = Path(self.temp.name) / "get-db-upgrade.db"
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES (1);
            INSERT INTO clients VALUES (1);
        """)
        conn.commit()
        conn.close()

        original_db_name = app.DB_NAME
        app.DB_NAME = str(path)
        try:
            connected = app.get_db()
            self.assertIsNotNone(connected.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (migration.OPTION_TABLE,),
            ).fetchone())
            self.assertIsNotNone(connected.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (migration.JUNCTION_TABLE,),
            ).fetchone())
            connected.close()
        finally:
            app.DB_NAME = original_db_name

    def test_init_db_applies_setting_events_migration(self):
        init_db = ROOT / "init_db.py"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT)
        result = subprocess.run(
            [sys.executable, str(init_db)],
            cwd=self.temp.name,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        conn = sqlite3.connect(Path(self.temp.name) / "nhpsg.db")
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (migration.OPTION_TABLE,),
        ).fetchone())
        self.assertIsNotNone(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (migration.JUNCTION_TABLE,),
        ).fetchone())
        conn.close()


if __name__ == "__main__":
    unittest.main()
