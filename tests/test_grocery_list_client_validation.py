import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class GroceryListClientValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp.name) / "grocery-lists.db")
        self.conn = sqlite3.connect(self.database_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        self.conn.executemany(
            "INSERT INTO clients (client_id, client_name, active) VALUES (?, ?, ?)",
            (
                (10, "Active Client", 1),
                (20, "Inactive Client", 0),
            ),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_active_client_is_returned_with_expected_identity(self):
        client = app.validate_active_grocery_list_client(self.conn, 10)

        self.assertEqual(client["client_id"], 10)
        self.assertEqual(client["client_name"], "Active Client")

    def test_inactive_client_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "An active client is required"):
            app.validate_active_grocery_list_client(self.conn, 20)

    def test_missing_client_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "An active client is required"):
            app.validate_active_grocery_list_client(self.conn, 999)

    def test_validation_does_not_require_shifts_assignments_or_schedule_state(self):
        table_names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertEqual(table_names, {"clients"})

        client = app.validate_active_grocery_list_client(self.conn, 10)

        self.assertEqual(client["client_id"], 10)


if __name__ == "__main__":
    unittest.main()
