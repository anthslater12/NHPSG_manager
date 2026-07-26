import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app


ERROR_MESSAGE = (
    "SQLite foreign-key enforcement could not be enabled or verified."
)


class StaffNoticesForeignKeyTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)

        self.original_database_name = app.DB_NAME
        self.addCleanup(self.restore_database_name)

        self.database_path = str(
            Path(self.temporary_directory.name) / "foreign_keys.db"
        )
        app.DB_NAME = self.database_path

        conn = sqlite3.connect(self.database_path)

        try:
            conn.executescript("""
                CREATE TABLE parent_records (
                    parent_id INTEGER PRIMARY KEY
                );

                CREATE TABLE child_records (
                    child_id INTEGER PRIMARY KEY,
                    parent_id INTEGER NOT NULL,
                    FOREIGN KEY (parent_id)
                        REFERENCES parent_records(parent_id)
                        ON DELETE RESTRICT
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def restore_database_name(self):
        app.DB_NAME = self.original_database_name

    def open_application_connection(self):
        conn = app.get_db()
        self.addCleanup(conn.close)
        return conn

    def test_get_db_enables_foreign_keys(self):
        conn = self.open_application_connection()

        self.assertEqual(
            conn.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )

    def test_get_db_preserves_sqlite_row_factory(self):
        conn = self.open_application_connection()
        row = conn.execute(
            "SELECT 7 AS record_id, 'Test' AS record_name"
        ).fetchone()

        self.assertIsInstance(row, sqlite3.Row)
        self.assertEqual(row["record_id"], 7)
        self.assertEqual(row["record_name"], "Test")

    def test_get_db_returns_without_an_open_transaction(self):
        conn = self.open_application_connection()

        self.assertIs(conn.in_transaction, False)

    def test_valid_parent_and_child_insert_succeeds(self):
        conn = self.open_application_connection()

        conn.execute(
            "INSERT INTO parent_records (parent_id) VALUES (1)"
        )
        conn.execute("""
            INSERT INTO child_records (child_id, parent_id)
            VALUES (1, 1)
        """)
        conn.commit()

        self.assertEqual(
            conn.execute(
                "SELECT parent_id FROM child_records WHERE child_id = 1"
            ).fetchone()["parent_id"],
            1
        )

    def test_orphan_child_insert_is_rejected(self):
        conn = self.open_application_connection()

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""
                INSERT INTO child_records (child_id, parent_id)
                VALUES (1, 999)
            """)

    def test_invalid_child_update_is_rejected(self):
        conn = self.open_application_connection()
        conn.execute(
            "INSERT INTO parent_records (parent_id) VALUES (1)"
        )
        conn.execute("""
            INSERT INTO child_records (child_id, parent_id)
            VALUES (1, 1)
        """)
        conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("""
                UPDATE child_records
                SET parent_id = 999
                WHERE child_id = 1
            """)

    def test_referenced_parent_delete_is_rejected(self):
        conn = self.open_application_connection()
        conn.execute(
            "INSERT INTO parent_records (parent_id) VALUES (1)"
        )
        conn.execute("""
            INSERT INTO child_records (child_id, parent_id)
            VALUES (1, 1)
        """)
        conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM parent_records WHERE parent_id = 1"
            )

    def test_foreign_keys_are_enabled_on_independent_connections(self):
        first = self.open_application_connection()
        second = self.open_application_connection()

        self.assertEqual(
            first.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )
        self.assertEqual(
            second.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )

        with self.assertRaises(sqlite3.IntegrityError):
            first.execute("""
                INSERT INTO child_records (child_id, parent_id)
                VALUES (1, 999)
            """)

        first.rollback()

        with self.assertRaises(sqlite3.IntegrityError):
            second.execute("""
                INSERT INTO child_records (child_id, parent_id)
                VALUES (2, 999)
            """)

    def test_reopening_connection_preserves_enforcement(self):
        first = app.get_db()
        self.assertEqual(
            first.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )
        first.close()

        second = self.open_application_connection()

        self.assertEqual(
            second.execute("PRAGMA foreign_keys").fetchone()[0],
            1
        )

        with self.assertRaises(sqlite3.IntegrityError):
            second.execute("""
                INSERT INTO child_records (child_id, parent_id)
                VALUES (1, 999)
            """)

    def test_verification_failure_closes_connection(self):
        connection = FakeConnection(foreign_keys_value=0)

        with mock.patch.object(
            app.sqlite3,
            "connect",
            return_value=connection
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "foreign-key enforcement could not be enabled or verified"
            ) as caught:
                app.get_db()

        self.assertTrue(connection.closed)
        self.assertIsNone(caught.exception.__cause__)

    def test_activation_exception_closes_connection_and_is_chained(self):
        activation_error = sqlite3.OperationalError(
            "simulated activation failure"
        )
        connection = FakeConnection(activation_error=activation_error)

        with mock.patch.object(
            app.sqlite3,
            "connect",
            return_value=connection
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "foreign-key enforcement could not be enabled or verified"
            ) as caught:
                app.get_db()

        self.assertTrue(connection.closed)
        self.assertIs(caught.exception.__cause__, activation_error)

    def test_unexpected_post_connection_error_closes_connection(self):
        unexpected_error = RuntimeError("simulated unexpected error")
        connection = FakeConnection(
            foreign_keys_value=1,
            transaction_error=unexpected_error
        )

        with mock.patch.object(
            app.sqlite3,
            "connect",
            return_value=connection
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "foreign-key enforcement could not be enabled or verified"
            ) as caught:
                app.get_db()

        self.assertTrue(connection.closed)
        self.assertIs(caught.exception.__cause__, unexpected_error)


class FakeCursor:

    def __init__(self, foreign_keys_value):
        self.foreign_keys_value = foreign_keys_value

    def fetchone(self):
        return (self.foreign_keys_value,)


class FakeConnection:

    def __init__(
        self,
        *,
        foreign_keys_value=1,
        activation_error=None,
        transaction_error=None
    ):
        self.foreign_keys_value = foreign_keys_value
        self.activation_error = activation_error
        self.transaction_error = transaction_error
        self.closed = False
        self.row_factory = None

    def execute(self, statement):
        if statement == "PRAGMA foreign_keys = ON":
            if self.activation_error is not None:
                raise self.activation_error

            return FakeCursor(self.foreign_keys_value)

        if statement == "PRAGMA foreign_keys":
            return FakeCursor(self.foreign_keys_value)

        raise AssertionError(f"Unexpected SQL: {statement}")

    @property
    def in_transaction(self):
        if self.transaction_error is not None:
            raise self.transaction_error

        return False

    def close(self):
        self.closed = True


class StaffNoticesSafeImportTests(unittest.TestCase):

    def test_import_does_not_connect_or_create_database(self):
        repository_path = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_database = (
                Path(temporary_directory) / "nhpsg.db"
            )
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
