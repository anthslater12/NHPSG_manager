import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import add_user_email_address as migration


class UserEmailAddressMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "users.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            INSERT INTO users
            (username, password_hash, full_name, role, active)
            VALUES
            ('worker1', 'hash1', 'Worker One', 'Support Worker', 1),
            ('manager1', 'hash2', 'Manager One', 'Program Manager', 1);
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_adds_nullable_email_address_and_preserves_existing_rows(self):
        before = self.conn.execute("""
            SELECT user_id, username, password_hash, full_name, role, active
            FROM users
            ORDER BY user_id
        """).fetchall()

        self.assertTrue(migration.migrate(self.conn))

        columns = {
            row[1]: row
            for row in self.conn.execute("PRAGMA table_info(users)")
        }

        self.assertIn("email_address", columns)
        self.assertEqual(columns["email_address"][3], 0)

        after = self.conn.execute("""
            SELECT user_id, username, password_hash, full_name, role, active
            FROM users
            ORDER BY user_id
        """).fetchall()

        self.assertEqual(before, after)

        email_values = self.conn.execute("""
            SELECT email_address
            FROM users
            ORDER BY user_id
        """).fetchall()

        self.assertEqual(email_values, [(None,), (None,)])

    def test_migration_is_idempotent_and_preserves_existing_email_values(self):
        self.assertTrue(migration.migrate(self.conn))

        self.conn.execute("""
            UPDATE users
            SET email_address = 'worker@example.com'
            WHERE username = 'worker1'
        """)
        self.conn.commit()

        self.assertFalse(migration.migrate(self.conn))

        self.assertEqual(
            self.conn.execute("""
                SELECT email_address
                FROM users
                WHERE username = 'worker1'
            """).fetchone()[0],
            "worker@example.com",
        )

    def test_missing_users_table_is_rejected(self):
        path = Path(self.temp.name) / "missing-users.db"
        conn = sqlite3.connect(path)

        try:
            with self.assertRaises(RuntimeError) as caught:
                migration.migrate(conn)

            self.assertIn("users table does not exist", str(caught.exception))
        finally:
            conn.close()

    def test_active_transaction_is_rejected_without_changes(self):
        self.conn.execute("BEGIN")

        with self.assertRaises(RuntimeError):
            migration.migrate(self.conn)

        columns = {
            row[1]
            for row in self.conn.execute("PRAGMA table_info(users)")
        }

        self.assertNotIn("email_address", columns)

        self.conn.rollback()

    def test_standalone_path_precedence_and_missing_path_safety(self):
        explicit = Path(self.temp.name) / "explicit.db"
        environment = Path(self.temp.name) / "environment.db"

        for path in (explicit, environment):
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL
                );
            """)
            conn.commit()
            conn.close()

        with patch.dict(
            os.environ,
            {"NHPSG_DB_PATH": str(environment)}
        ):
            migration.main([str(explicit)])

        explicit_conn = sqlite3.connect(explicit)
        try:
            explicit_columns = {
                row[1]
                for row in explicit_conn.execute(
                    "PRAGMA table_info(users)"
                )
            }
        finally:
            explicit_conn.close()

        environment_conn = sqlite3.connect(environment)
        try:
            environment_columns = {
                row[1]
                for row in environment_conn.execute(
                    "PRAGMA table_info(users)"
                )
            }
        finally:
            environment_conn.close()

        self.assertIn("email_address", explicit_columns)
        self.assertNotIn("email_address", environment_columns)

        with patch.dict(
            os.environ,
            {"NHPSG_DB_PATH": str(environment)}
        ):
            migration.main([])

        environment_conn = sqlite3.connect(environment)
        try:
            environment_columns = {
                row[1]
                for row in environment_conn.execute(
                    "PRAGMA table_info(users)"
                )
            }
        finally:
            environment_conn.close()

        self.assertIn("email_address", environment_columns)

        missing = Path(self.temp.name) / "missing.db"

        with patch.dict(os.environ, {}, clear=False):
            with self.assertRaises(SystemExit):
                migration.main([str(missing)])

        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
