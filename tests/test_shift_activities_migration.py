import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_shift_activities_table as migration


class ShiftActivitiesMigrationTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        database_path = (
            Path(self.temporary_directory.name) / "activities-schema.db"
        )
        self.conn = sqlite3.connect(database_path)
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY
            );
            INSERT INTO users VALUES (1);
            INSERT INTO shifts VALUES (10);
        """)
        migration.migrate(self.conn)

    def insert_activity(self, **overrides):
        values = {
            "shift_id": 10,
            "recorded_by_user_id": 1,
            "start_time": "09:00",
            "end_time": "10:00",
            "a_selected": 1,
            "t_selected": 0,
            "ls_selected": 0,
            "activity_description": "Community walk",
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        return self.conn.execute(
            f"""
            INSERT INTO shift_activities ({columns})
            VALUES ({placeholders})
            """,
            tuple(values.values())
        ).lastrowid

    def assert_rejected(self, **overrides):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_activity(**overrides)
        self.conn.rollback()

    def test_schema_constraints_foreign_keys_and_index(self):
        columns = {
            row["name"]: row
            for row in self.conn.execute(
                "PRAGMA table_info(shift_activities)"
            )
        }
        self.assertEqual(set(columns), {
            "shift_activity_id",
            "shift_id",
            "recorded_by_user_id",
            "start_time",
            "end_time",
            "a_selected",
            "t_selected",
            "ls_selected",
            "activity_description",
            "created_at",
        })
        self.assertEqual(columns["created_at"]["dflt_value"], "CURRENT_TIMESTAMP")
        foreign_keys = {
            (row["from"], row["table"], row["to"])
            for row in self.conn.execute(
                "PRAGMA foreign_key_list(shift_activities)"
            )
        }
        self.assertEqual(foreign_keys, {
            ("shift_id", "shifts", "shift_id"),
            ("recorded_by_user_id", "users", "user_id"),
        })
        indexes = {
            row["name"]
            for row in self.conn.execute(
                "PRAGMA index_list(shift_activities)"
            )
        }
        self.assertIn("idx_shift_activities_shift_created", indexes)

    def test_one_two_and_three_categories_are_accepted(self):
        combinations = (
            (1, 0, 0),
            (0, 1, 1),
            (1, 1, 1),
        )
        for index, combination in enumerate(combinations, start=1):
            self.insert_activity(
                start_time=f"0{index}:00",
                end_time=f"0{index}:30",
                a_selected=combination[0],
                t_selected=combination[1],
                ls_selected=combination[2],
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM shift_activities"
            ).fetchone()[0],
            3
        )

    def test_invalid_categories_blank_description_and_foreign_keys_rejected(self):
        self.assert_rejected(a_selected=0, t_selected=0, ls_selected=0)
        self.assert_rejected(a_selected=2)
        self.assert_rejected(activity_description=" \t\r\n")
        self.assert_rejected(shift_id=999)
        self.assert_rejected(recorded_by_user_id=999)

    def test_migration_is_idempotent(self):
        migration.migrate(self.conn)
        migration.migrate(self.conn)
        self.insert_activity()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM shift_activities"
            ).fetchone()[0],
            1
        )


if __name__ == "__main__":
    unittest.main()
