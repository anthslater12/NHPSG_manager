import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class GroceryListSharedAccessTests(unittest.TestCase):
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
                (5, 'Shared Worker', 'Support Worker', 1),
                (6, 'Inactive Worker', 'Support Worker', 0),
                (7, 'Other Worker', 'Support Worker', 1);
            INSERT INTO clients (client_id, client_name, active) VALUES
                (10, 'Client A', 1),
                (20, 'Client B', 1),
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
            6: "Support Worker",
            7: "Support Worker",
        }
        with self.client.session_transaction() as session:
            session.clear()
            session["user_id"] = user_id
            session["role"] = session_role or roles[user_id]
            session["full_name"] = "Spoofed Session User"

    def create_list_content(self, client_id=10):
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
            VALUES (?, 'Pantry', 0)
        """, (list_id,))
        section_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, 'Milk', 'Low', '2 cartons', 0, 0,
                    '2026-09-20T01:00:00Z',
                    '2026-09-20T02:00:00Z', 1)
        """, (section_id,))
        item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return list_id, section_id, item_id

    def create_share(self, list_id, user_id=5, permission="VIEW"):
        conn = self.connect()
        conn.execute("""
            INSERT INTO grocery_list_shares
            (grocery_list_id, user_id, permission,
             shared_by_user_id, shared_at_utc)
            VALUES (?, ?, ?, 1, '2026-09-20T01:00:00Z')
        """, (list_id, user_id, permission))
        conn.commit()
        conn.close()

    def test_view_share_can_read_and_hides_all_management_controls(self):
        list_id, section_id, item_id = self.create_list_content()
        self.create_share(list_id, permission="VIEW")
        self.login(5)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client A", response.data)
        self.assertIn(b"Pantry", response.data)
        self.assertIn(b"Milk", response.data)
        for control in (
            b"Share Grocery List",
            b"Add Section",
            b"Add Item",
            b"Save",
            b"Mark Purchased",
            b"Mark Not Purchased",
        ):
            self.assertNotIn(control, response.data)

    def test_view_share_cannot_use_any_content_mutation_route(self):
        list_id, section_id, item_id = self.create_list_content()
        self.create_share(list_id, permission="VIEW")
        self.login(5)
        requests = (
            ("/client/10/grocery-list/sections/new", {"name": "New"}),
            (f"/client/10/grocery-list/sections/{section_id}/rename", {"name": "Renamed"}),
            (f"/client/10/grocery-list/sections/{section_id}/delete", {"confirm": "yes"}),
            (f"/client/10/grocery-list/sections/{section_id}/items/new", {"item_name": "New Item"}),
            (f"/client/10/grocery-list/items/{item_id}/edit", {"item_name": "Edited"}),
            (f"/client/10/grocery-list/items/{item_id}/delete", {"confirm": "yes"}),
            (f"/client/10/grocery-list/items/{item_id}/purchased", {"purchased": "1"}),
        )
        for path, data in requests:
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.post(path, data=data).status_code,
                    403,
                )

    def test_edit_share_can_edit_content_but_not_manage_shares(self):
        list_id, section_id, item_id = self.create_list_content()
        self.create_share(list_id, permission="EDIT")
        self.login(5)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        for control in (b"Add Section", b"Add Item", b"Save", b"Mark Purchased"):
            self.assertIn(control, response.data)
        self.assertNotIn(b"Share Grocery List", response.data)

        self.assertEqual(
            self.client.post(
                "/client/10/grocery-list/shares/new",
                data={"user_id": 7, "permission": "VIEW"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/sections/{section_id}/items/new",
                data={"item_name": "Bread"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/items/{item_id}/edit",
                data={"item_name": "Updated Milk"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/items/{item_id}/purchased",
                data={"purchased": "1"},
            ).status_code,
            302,
        )
        conn = self.connect()
        row = conn.execute(
            "SELECT updated_by_user_id, purchased FROM grocery_list_items "
            "WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        self.assertEqual(row["updated_by_user_id"], 5)
        self.assertEqual(row["purchased"], 1)
        conn.close()

    def test_edit_share_can_manage_sections_and_items(self):
        list_id, section_id, item_id = self.create_list_content()
        self.create_share(list_id, permission="EDIT")
        self.login(5)
        self.assertEqual(
            self.client.post(
                "/client/10/grocery-list/sections/new",
                data={"name": "Frozen"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/sections/{section_id}/rename",
                data={"name": "Kitchen"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/items/{item_id}/delete",
                data={"confirm": "yes"},
            ).status_code,
            302,
        )
        conn = self.connect()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM grocery_list_sections "
                "WHERE grocery_list_id = ?",
                (list_id,),
            ).fetchone()[0],
            2,
        )
        conn.close()

    def test_unshared_removed_inactive_and_behaviour_users_are_denied(self):
        list_id, _section_id, _item_id = self.create_list_content()
        self.login(5)
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            403,
        )
        self.create_share(list_id, user_id=5, permission="VIEW")
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            200,
        )
        conn = self.connect()
        conn.execute(
            "DELETE FROM grocery_list_shares WHERE grocery_list_id = ?",
            (list_id,),
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            403,
        )
        self.create_share(list_id, user_id=5, permission="VIEW")
        conn = self.connect()
        conn.execute("UPDATE users SET active = 0 WHERE user_id = 5")
        conn.commit()
        conn.close()
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            403,
        )

        self.create_share(list_id, user_id=4, permission="VIEW")
        self.login(4)
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            403,
        )

    def test_share_for_another_client_and_url_manipulation_are_denied(self):
        first_list, _section_id, _item_id = self.create_list_content(10)
        second_list, _section_id, _item_id = self.create_list_content(20)
        self.create_share(second_list, user_id=5, permission="VIEW")
        self.login(5)
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/client/20/grocery-list").status_code,
            200,
        )
        self.assertNotEqual(first_list, second_list)

    def test_invalid_database_permission_fails_closed(self):
        list_id, _section_id, _item_id = self.create_list_content()
        conn = self.connect()
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("""
            INSERT INTO grocery_list_shares
            (grocery_list_id, user_id, permission,
             shared_by_user_id, shared_at_utc)
            VALUES (?, 5, 'INVALID', 1, '2026-09-20T01:00:00Z')
        """, (list_id,))
        conn.commit()
        conn.close()
        self.login(5)
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            403,
        )

    def test_management_users_retain_full_access_and_auto_creation(self):
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Support Worker")
                response = self.client.get("/client/10/grocery-list")
                self.assertEqual(response.status_code, 200)
        conn = self.connect()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM grocery_lists WHERE client_id = 10"
            ).fetchone()[0],
            1,
        )
        conn.close()

    def test_access_does_not_require_shift_assignment_or_schedule(self):
        list_id, _section_id, _item_id = self.create_list_content()
        self.create_share(list_id, permission="VIEW")
        self.login(5)
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            200,
        )
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
