import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app


class GroceryListItemPurchasedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp.name) / "grocery-lists.db")
        self.old_db_name = app.DB_NAME
        self.old_testing = app.app.config.get("TESTING")
        app.DB_NAME = self.database_path
        app.app.config.update(TESTING=True)
        self._create_database()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db_name
        app.app.config.update(TESTING=self.old_testing)
        self.temp.cleanup()

    def _create_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users (user_id, full_name, role, active) VALUES
                (1, 'Admin User', 'Admin', 1),
                (2, 'Program Manager', 'Program Manager', 1),
                (3, 'Director User', 'Director', 1),
                (4, 'Behaviour Consultant', 'Behaviour Consultant', 1),
                (5, 'Support Worker', 'Support Worker', 1),
                (6, 'Inactive Director', 'Director', 0);
            INSERT INTO clients (client_id, client_name, active) VALUES
                (10, 'Active Client', 1),
                (20, 'Other Client', 1),
                (30, 'Inactive Client', 0);
            """
        )
        conn.commit()
        conn.close()
        conn = app.get_db()
        conn.close()

    def connect(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def login(self, user_id, session_role=None):
        roles = {
            1: "Admin",
            2: "Program Manager",
            3: "Director",
            4: "Behaviour Consultant",
            5: "Support Worker",
            6: "Director",
        }
        with self.client.session_transaction() as session:
            session.clear()
            session["user_id"] = user_id
            session["role"] = session_role or roles[user_id]
            session["full_name"] = "Test User"

    def create_item(self, client_id=10, purchased=0):
        conn = self.connect()
        conn.execute("""
            INSERT INTO grocery_lists
            (client_id, title, created_by_user_id,
             created_at_utc, updated_at_utc)
            VALUES (?, 'Grocery List', 1,
                    '2026-09-20T01:00:00Z',
                    '2026-09-20T01:00:00Z')
        """, (client_id,))
        list_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""
            INSERT INTO grocery_list_sections
            (grocery_list_id, name, display_order)
            VALUES (?, 'Pantry', 2)
        """, (list_id,))
        section_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, 'Milk', 'Low', '2 cartons', ?, 7,
                    '2026-09-20T01:00:00Z',
                    '2026-09-20T02:00:00Z', 1)
        """, (section_id, purchased))
        item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return item_id

    def item(self, item_id):
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM grocery_list_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        conn.close()
        return row

    def test_management_roles_can_mark_purchased_and_not_purchased(self):
        item_id = self.create_item()
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.post(
                    f"/client/10/grocery-list/items/{item_id}/purchased",
                    data={"purchased": "1"},
                )
                self.assertEqual(response.status_code, 302)
        self.assertEqual(self.item(item_id)["purchased"], 1)

        self.login(2)
        response = self.client.post(
            f"/client/10/grocery-list/items/{item_id}/purchased",
            data={"purchased": "0"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.item(item_id)["purchased"], 0)

    def test_non_management_and_inactive_users_cannot_change_state(self):
        item_id = self.create_item()
        for user_id in (4, 5, 6, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                response = self.client.post(
                    f"/client/10/grocery-list/items/{item_id}/purchased",
                    data={"purchased": "1"},
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.item(item_id)["purchased"], 0)

    def test_invalid_missing_and_cross_client_items_are_rejected(self):
        item_id = self.create_item(10)
        other_item_id = self.create_item(20)
        self.login(1)
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/items/{item_id}/purchased",
                data={"purchased": "2"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/items/{other_item_id}/purchased",
                data={"purchased": "1"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/client/10/grocery-list/items/999/purchased",
                data={"purchased": "1"},
            ).status_code,
            404,
        )
        self.assertEqual(self.item(item_id)["purchased"], 0)
        self.assertEqual(self.item(other_item_id)["purchased"], 0)

    def test_audit_and_other_fields_are_preserved(self):
        item_id = self.create_item()
        before = self.item(item_id)
        edit_time = datetime(2026, 9, 20, 4, 0, tzinfo=timezone.utc)
        self.login(3)
        with patch.object(app, "get_application_now_utc", return_value=edit_time):
            response = self.client.post(
                f"/client/10/grocery-list/items/{item_id}/purchased",
                data={"purchased": "1"},
            )
        self.assertEqual(response.status_code, 302)
        after = self.item(item_id)
        self.assertEqual(after["purchased"], 1)
        self.assertEqual(after["updated_at_utc"], "2026-09-20T04:00:00Z")
        self.assertNotEqual(after["updated_at_utc"], before["updated_at_utc"])
        self.assertEqual(after["updated_by_user_id"], 3)
        for field in (
            "created_at_utc", "item_name", "stock_text", "needed_text",
            "display_order",
        ):
            self.assertEqual(after[field], before[field], field)

    def test_purchased_state_renders_and_items_remain_visible_without_schedule(self):
        item_id = self.create_item()
        self.login(1)
        response = self.client.post(
            f"/client/10/grocery-list/items/{item_id}/purchased",
            data={"purchased": "1"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Milk", response.data)
        self.assertIn(b"Purchased", response.data)
        self.assertIn(b"Mark Not Purchased", response.data)

        conn = self.connect()
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertNotIn("shifts", table_names)
        self.assertNotIn("shift_staff", table_names)
        self.assertNotIn("schedule_weeks", table_names)
        conn.close()


if __name__ == "__main__":
    unittest.main()
