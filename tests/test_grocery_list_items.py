import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class GroceryListItemTests(unittest.TestCase):
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

    def create_list_and_section(self, client_id=10, section_name="Pantry"):
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
            VALUES (?, ?, 0)
        """, (list_id, section_name))
        section_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return section_id

    def add_item_directly(self, section_id, item_name, display_order=0):
        conn = self.connect()
        conn.execute("""
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, ?, NULL, NULL, 0, ?,
                    '2026-09-20T01:00:00Z',
                    '2026-09-20T01:00:00Z', 1)
        """, (section_id, item_name, display_order))
        item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return item_id

    def items(self, section_id=None):
        conn = self.connect()
        query = "SELECT * FROM grocery_list_items"
        parameters = ()
        if section_id is not None:
            query += " WHERE section_id = ?"
            parameters = (section_id,)
        query += " ORDER BY display_order, item_id"
        rows = conn.execute(query, parameters).fetchall()
        conn.close()
        return rows

    def test_all_management_roles_can_add_item(self):
        section_id = self.create_list_and_section()
        for expected_count, user_id in enumerate((1, 2, 3), 1):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.post(
                    f"/client/10/grocery-list/sections/{section_id}/items/new",
                    data={"item_name": f"Item {user_id}"},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(len(self.items(section_id)), expected_count)

    def test_non_management_and_inactive_users_cannot_add_or_delete(self):
        section_id = self.create_list_and_section()
        item_id = self.add_item_directly(section_id, "Protected")
        for user_id in (4, 5, 6, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                self.assertEqual(
                    self.client.post(
                        f"/client/10/grocery-list/sections/{section_id}/items/new",
                        data={"item_name": "Denied"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        f"/client/10/grocery-list/items/{item_id}/delete",
                        data={"confirm": "yes"},
                    ).status_code,
                    403,
                )
        self.assertEqual(len(self.items(section_id)), 1)

    def test_blank_and_whitespace_item_names_are_rejected(self):
        section_id = self.create_list_and_section()
        self.login(1)
        for item_name in ("", "   "):
            with self.subTest(item_name=repr(item_name)):
                response = self.client.post(
                    f"/client/10/grocery-list/sections/{section_id}/items/new",
                    data={"item_name": item_name},
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Item name is required.", response.data)
        self.assertEqual(self.items(section_id), [])

    def test_item_text_is_trimmed_and_stored_as_free_text(self):
        section_id = self.create_list_and_section()
        self.login(1)
        response = self.client.post(
            f"/client/10/grocery-list/sections/{section_id}/items/new",
            data={
                "item_name": "  Doritos/Cheetos???  ",
                "stock_text": "  1/3 tank  ",
                "needed_text": "  8 cups  ",
            },
        )
        self.assertEqual(response.status_code, 302)
        row = self.items(section_id)[0]
        self.assertEqual(row["item_name"], "Doritos/Cheetos???")
        self.assertEqual(row["stock_text"], "1/3 tank")
        self.assertEqual(row["needed_text"], "8 cups")
        self.assertEqual(row["purchased"], 0)
        self.assertEqual(row["display_order"], 0)
        self.assertTrue(row["created_at_utc"])
        self.assertTrue(row["updated_at_utc"])
        self.assertEqual(row["updated_by_user_id"], 1)
        self.assertEqual(response.headers["Location"], "/client/10/grocery-list")

    def test_blank_optional_text_is_stored_as_null_and_order_increments(self):
        section_id = self.create_list_and_section()
        self.add_item_directly(section_id, "Existing", display_order=3)
        self.login(2)
        response = self.client.post(
            f"/client/10/grocery-list/sections/{section_id}/items/new",
            data={
                "item_name": "  Refill  ",
                "stock_text": "   ",
                "needed_text": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        row = self.items(section_id)[-1]
        self.assertEqual(row["item_name"], "Refill")
        self.assertIsNone(row["stock_text"])
        self.assertIsNone(row["needed_text"])
        self.assertEqual(row["display_order"], 4)
        self.assertEqual(row["updated_by_user_id"], 2)

    def test_cannot_add_to_another_clients_section(self):
        self.create_list_and_section(10)
        other_section_id = self.create_list_and_section(20, "Other Pantry")
        self.login(1)
        response = self.client.post(
            f"/client/10/grocery-list/sections/{other_section_id}/items/new",
            data={"item_name": "Hijacked"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.items(other_section_id), [])

    def test_delete_is_post_only_confirmed_and_preserves_siblings(self):
        section_id = self.create_list_and_section()
        first_id = self.add_item_directly(section_id, "First", 0)
        sibling_id = self.add_item_directly(section_id, "Sibling", 1)
        self.login(1)

        get_response = self.client.get(
            f"/client/10/grocery-list/items/{first_id}/delete"
        )
        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(len(self.items(section_id)), 2)

        no_confirmation = self.client.post(
            f"/client/10/grocery-list/items/{first_id}/delete",
            follow_redirects=True,
        )
        self.assertEqual(no_confirmation.status_code, 200)
        self.assertIn(b"explicitly confirm", no_confirmation.data)
        self.assertEqual(len(self.items(section_id)), 2)

        response = self.client.post(
            f"/client/10/grocery-list/items/{first_id}/delete",
            data={"confirm": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/client/10/grocery-list")
        remaining = self.items(section_id)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["item_id"], sibling_id)

    def test_cross_client_and_missing_items_are_rejected(self):
        self.create_list_and_section(10)
        other_section_id = self.create_list_and_section(20, "Other Pantry")
        other_item_id = self.add_item_directly(other_section_id, "Other Item")
        self.login(1)
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/items/{other_item_id}/delete",
                data={"confirm": "yes"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/client/10/grocery-list/items/999/delete",
                data={"confirm": "yes"},
            ).status_code,
            404,
        )
        self.assertEqual(len(self.items(other_section_id)), 1)

    def test_existing_page_orders_items_and_renders_free_text_without_schedule_state(self):
        section_id = self.create_list_and_section()
        self.add_item_directly(section_id, "Later", 4)
        self.add_item_directly(section_id, "First", 0)
        self.login(3)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertLess(body.index(b"First"), body.index(b"Later"))
        self.assertIn(b"Current Stock", body)
        self.assertIn(b"Amount Needed", body)

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
