import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import add_worker_resources_table as migration
import app


class WorkerResourcesMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "worker-resources.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL
            )
        """)
        self.conn.execute("INSERT INTO users VALUES (1, 'manager')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def resource_values(self, **overrides):
        values = {
            "title": "Orientation",
            "description": "New worker orientation",
            "category": "Training",
            "resource_type": "Video",
            "stored_filename": "abc123.mp4",
            "original_filename": "orientation.mp4",
            "mime_type": "video/mp4",
            "file_size_bytes": 10,
            "display_order": 0,
            "active": 1,
            "uploaded_by_user_id": 1,
            "created_at_utc": "2026-09-19T12:00:00Z",
            "updated_at_utc": "2026-09-19T12:00:00Z",
        }
        values.update(overrides)
        return values

    def insert_resource(self, **overrides):
        values = self.resource_values(**overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        return self.conn.execute(
            f"INSERT INTO worker_resources ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )

    def assert_rejected(self, **overrides):
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_resource(**overrides)
        self.conn.rollback()

    def test_creates_expected_table_columns_indexes_and_foreign_key(self):
        migration.migrate(self.conn)

        self.assertEqual(
            {row[1] for row in self.conn.execute("PRAGMA table_info(worker_resources)")},
            migration.EXPECTED_COLUMNS,
        )
        self.assertEqual(
            {
                row[1]
                for row in self.conn.execute("PRAGMA index_list(worker_resources)")
            }
            & {name for name, _ in migration.INDEXES},
            {name for name, _ in migration.INDEXES},
        )
        self.assertIn(
            ("uploaded_by_user_id", "users", "user_id", "RESTRICT"),
            {
                (row[3], row[2], row[4], row[6])
                for row in self.conn.execute("PRAGMA foreign_key_list(worker_resources)")
            },
        )

    def test_constraints_are_enforced(self):
        migration.migrate(self.conn)
        for field, value in (
            ("category", "Other"),
            ("resource_type", "Audio"),
            ("active", 2),
            ("file_size_bytes", -1),
        ):
            self.assert_rejected(**{field: value, "stored_filename": f"{field}.bin"})

        self.assert_rejected(
            uploaded_by_user_id=999,
            stored_filename="missing-user.bin",
        )
        self.insert_resource()
        self.conn.commit()

    def test_migration_is_idempotent(self):
        migration.migrate(self.conn)
        first_schema = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'worker_resources'"
        ).fetchone()[0]
        migration.migrate(self.conn)
        second_schema = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'worker_resources'"
        ).fetchone()[0]
        self.assertEqual(first_schema, second_schema)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM worker_resources").fetchone()[0],
            0,
        )

    def test_migration_does_not_commit_a_caller_owned_transaction(self):
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("BEGIN")
        self.conn.execute("INSERT INTO users VALUES (2, 'worker')")

        migration.migrate(self.conn)

        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'worker_resources'"
            ).fetchone()
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM users WHERE user_id = 2"
            ).fetchone()
        )

    def test_migration_failure_preserves_caller_transaction(self):
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("BEGIN")
        self.conn.execute("INSERT INTO users VALUES (2, 'worker')")
        self.conn.execute(
            "CREATE TABLE worker_resources "
            "(resource_id INTEGER PRIMARY KEY)"
        )

        with self.assertRaises(RuntimeError):
            migration.migrate(self.conn)

        self.assertTrue(self.conn.in_transaction)
        self.conn.commit()
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM users WHERE user_id = 2"
            ).fetchone()
        )
        self.assertEqual(
            [row[1] for row in self.conn.execute("PRAGMA table_info(worker_resources)")],
            ["resource_id"],
        )

    def test_incompatible_existing_table_is_rejected_without_replacement(self):
        self.conn.execute(
            "CREATE TABLE worker_resources "
            "(resource_id INTEGER PRIMARY KEY, title TEXT NOT NULL)"
        )
        self.conn.commit()

        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            migration.migrate(self.conn)

        self.assertEqual(
            [row[1] for row in self.conn.execute("PRAGMA table_info(worker_resources)")],
            ["resource_id", "title"],
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'index'").fetchone()[0],
            0,
        )

    def test_get_db_applies_worker_resources_migration(self):
        database_path = Path(self.temp.name) / "startup.db"
        conn = sqlite3.connect(database_path)
        conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        old_db = app.DB_NAME
        app.DB_NAME = str(database_path)
        try:
            connected = app.get_db()
            self.assertIsNotNone(
                connected.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'worker_resources'"
                ).fetchone()
            )
            connected.close()

            connected = app.get_db()
            connected.close()
        finally:
            app.DB_NAME = old_db

    def test_storage_default_and_environment_override(self):
        expected_default = os.path.join(
            os.path.dirname(os.path.abspath(app.__file__)),
            "data",
            "worker_resources",
        )
        self.assertEqual(app.WORKER_RESOURCE_STORAGE_PATH, expected_default)

        override = "D:\\nhpsg\\resources"
        with patch.dict(os.environ, {"NHPSG_RESOURCE_STORAGE_PATH": "D:\\nhpsg\\resources"}):
            spec = importlib.util.spec_from_file_location(
                "worker_resource_storage_override_app",
                app.__file__,
            )
            isolated_app = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(isolated_app)

        self.assertEqual(isolated_app.WORKER_RESOURCE_STORAGE_PATH, override)


if __name__ == "__main__":
    unittest.main()
