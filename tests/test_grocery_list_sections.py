import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class GroceryListSectionTests(unittest.TestCase):
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

    def create_list(self, client_id=10):
        conn = self.connect()
        conn.execute("""
            INSERT INTO grocery_lists
            (client_id, title, created_by_user_id,
             created_at_utc, updated_at_utc)
            VALUES (?, 'Grocery List', 1,
                    '2026-09-20T01:00:00Z',
                    '2026-09-20T01:00:00Z')
        """, (client_id,))
        conn.commit()
        list_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return list_id

    def create_section(self, client_id=10, name="Existing", display_order=0):
        list_id = self._list_id(client_id)
        conn = self.connect()
        conn.execute("""
            INSERT INTO grocery_list_sections
            (grocery_list_id, name, display_order)
            VALUES (?, ?, ?)
        """, (list_id, name, display_order))
        conn.commit()
        section_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        return section_id

    def _list_id(self, client_id):
        conn = self.connect()
        row = conn.execute(
            "SELECT grocery_list_id FROM grocery_lists WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        conn.close()
        return row[0]

    def sections(self, client_id=10):
        conn = self.connect()
        rows = conn.execute("""
            SELECT s.*
            FROM grocery_list_sections AS s
            JOIN grocery_lists AS gl
              ON gl.grocery_list_id = s.grocery_list_id
            WHERE gl.client_id = ?
            ORDER BY s.display_order, s.section_id
        """, (client_id,)).fetchall()
        conn.close()
        return rows

    def test_all_management_roles_can_add_section(self):
        self.create_list()
        for user_id, name in ((1, "Admin Section"), (2, "Manager Section"), (3, "Director Section")):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.post(
                    "/client/10/grocery-list/sections/new",
                    data={"name": name},
                )
                self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.sections()), 3)

    def test_non_management_and_inactive_users_cannot_add_rename_or_delete(self):
        self.create_list()
        section_id = self.create_section()
        for user_id in (4, 5, 6, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                self.assertEqual(
                    self.client.post(
                        "/client/10/grocery-list/sections/new",
                        data={"name": "Denied"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        f"/client/10/grocery-list/sections/{section_id}/rename",
                        data={"name": "Denied Rename"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        f"/client/10/grocery-list/sections/{section_id}/delete",
                        data={"confirm": "yes"},
                    ).status_code,
                    403,
                )
        self.assertEqual([row["name"] for row in self.sections()], ["Existing"])

    def test_add_rejects_blank_and_whitespace_names(self):
        self.create_list()
        self.login(1)
        for name in ("", "   "):
            with self.subTest(name=repr(name)):
                response = self.client.post(
                    "/client/10/grocery-list/sections/new",
                    data={"name": name},
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Section name is required.", response.data)
        self.assertEqual(self.sections(), [])

    def test_add_trims_name_uses_next_order_and_redirects(self):
        self.create_list()
        self.create_section(name="First", display_order=0)
        self.create_section(name="Second", display_order=4)
        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/sections/new",
            data={"name": "  Cleaning Products  "},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/client/10/grocery-list")
        row = self.sections()[-1]
        self.assertEqual(row["name"], "Cleaning Products")
        self.assertEqual(row["display_order"], 5)

    def test_duplicate_add_is_friendly_and_does_not_add_row(self):
        self.create_list()
        self.create_section(name="Pantry")
        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/sections/new",
            data={"name": "Pantry"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"That section name already exists.", response.data)
        self.assertEqual(len(self.sections()), 1)

    def test_manager_can_rename_with_trim_without_changing_order(self):
        self.create_list()
        section_id = self.create_section(name="Old", display_order=7)
        self.login(2)
        response = self.client.post(
            f"/client/10/grocery-list/sections/{section_id}/rename",
            data={"name": "  New Name  "},
        )
        self.assertEqual(response.status_code, 302)
        row = self.sections()[0]
        self.assertEqual(row["name"], "New Name")
        self.assertEqual(row["display_order"], 7)

    def test_rename_rejects_blank_and_duplicate_names(self):
        self.create_list()
        first_id = self.create_section(name="First", display_order=0)
        self.create_section(name="Second", display_order=1)
        self.login(1)
        for name, message in (
            ("", "Section name is required."),
            ("Second", "That section name already exists."),
        ):
            with self.subTest(name=name):
                response = self.client.post(
                    f"/client/10/grocery-list/sections/{first_id}/rename",
                    data={"name": name},
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(message.encode(), response.data)
        self.assertEqual(self.sections()[0]["name"], "First")

    def test_cross_client_section_cannot_be_renamed_or_deleted(self):
        self.create_list(10)
        self.create_list(20)
        other_section_id = self.create_section(20, "Other Section")
        self.login(1)
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/sections/{other_section_id}/rename",
                data={"name": "Hijacked"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/sections/{other_section_id}/delete",
                data={"confirm": "yes"},
            ).status_code,
            404,
        )
        self.assertEqual(self.sections(20)[0]["name"], "Other Section")

    def test_delete_requires_post_confirmation_and_cascades_items(self):
        self.create_list()
        section_id = self.create_section()
        conn = self.connect()
        conn.execute("""
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, 'Milk', NULL, NULL, 0, 0,
                    '2026-09-20T01:00:00Z',
                    '2026-09-20T01:00:00Z', 1)
        """, (section_id,))
        conn.commit()
        conn.close()

        self.login(1)
        get_response = self.client.get(
            f"/client/10/grocery-list/sections/{section_id}/delete"
        )
        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(len(self.sections()), 1)

        no_confirmation = self.client.post(
            f"/client/10/grocery-list/sections/{section_id}/delete",
            follow_redirects=True,
        )
        self.assertEqual(no_confirmation.status_code, 200)
        self.assertIn(b"explicitly confirm", no_confirmation.data)
        self.assertEqual(len(self.sections()), 1)

        response = self.client.post(
            f"/client/10/grocery-list/sections/{section_id}/delete",
            data={"confirm": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/client/10/grocery-list")
        conn = self.connect()
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM grocery_list_sections WHERE section_id = ?",
            (section_id,),
        ).fetchone())
        self.assertIsNone(conn.execute(
            "SELECT 1 FROM grocery_list_items WHERE section_id = ?",
            (section_id,),
        ).fetchone())
        conn.close()

    def test_missing_section_and_actions_need_no_shift_or_assignment_state(self):
        self.create_list()
        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/sections/999/rename",
            data={"name": "Missing"},
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            "/client/10/grocery-list/sections/999/delete",
            data={"confirm": "yes"},
        )
        self.assertEqual(response.status_code, 404)

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
