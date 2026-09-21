import sqlite3
import tempfile
import unittest
from pathlib import Path

from flask import session

import app


class GroceryListAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp.name) / "grocery-lists.db")
        self.conn = sqlite3.connect(self.database_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users (user_id, role, active) VALUES
                (1, 'Admin', 1),
                (2, 'Program Manager', 1),
                (3, 'Director', 1),
                (4, 'Behaviour Consultant', 1),
                (5, 'Support Worker', 1),
                (6, 'Director', 0),
                (7, 'Support Worker', 1);
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_privileged_management_roles_are_authorized(self):
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                actor = app.validate_grocery_list_management_authority(
                    self.conn, user_id
                )
                self.assertEqual(actor["user_id"], user_id)

    def test_behaviour_consultant_and_support_worker_are_denied(self):
        for user_id in (4, 5):
            with self.subTest(user_id=user_id):
                with self.assertRaises(PermissionError):
                    app.validate_grocery_list_management_authority(
                        self.conn, user_id
                    )

    def test_inactive_management_user_and_missing_user_are_denied(self):
        for user_id in (6, 999):
            with self.subTest(user_id=user_id):
                with self.assertRaises(PermissionError):
                    app.validate_grocery_list_management_authority(
                        self.conn, user_id
                    )

    def test_database_role_is_authoritative_over_session_role(self):
        with app.app.test_request_context("/"):
            session["user_id"] = 7
            session["role"] = "Admin"
            with self.assertRaises(PermissionError):
                app.validate_grocery_list_management_authority(
                    self.conn, session["user_id"]
                )

            self.conn.execute(
                "UPDATE users SET role = 'Director' WHERE user_id = 7"
            )
            self.conn.commit()
            actor = app.validate_grocery_list_management_authority(
                self.conn, session["user_id"]
            )
            self.assertEqual(actor["role"], "Director")


if __name__ == "__main__":
    unittest.main()
