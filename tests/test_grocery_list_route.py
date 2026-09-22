import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class GroceryListRouteTests(unittest.TestCase):
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

    def connect(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def list_rows(self):
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM grocery_lists ORDER BY grocery_list_id"
        ).fetchall()
        conn.close()
        return rows

    def seed_content(self):
        conn = self.connect()
        grocery_list = conn.execute("""
            SELECT grocery_list_id
            FROM grocery_lists
            WHERE client_id = 10
        """).fetchone()
        if grocery_list is None:
            conn.execute("""
                INSERT INTO grocery_lists
                (client_id, title, created_by_user_id,
                 created_at_utc, updated_at_utc)
                VALUES (10, 'Client Ten List', 2,
                        '2026-09-20T01:00:00Z',
                        '2026-09-20T01:00:00Z')
            """)
            grocery_list_id = conn.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]
        else:
            grocery_list_id = grocery_list["grocery_list_id"]
        conn.execute("""
            INSERT INTO grocery_list_sections
            (grocery_list_id, name, display_order)
            VALUES (?, 'Later', 20)
        """, (grocery_list_id,))
        later_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""
            INSERT INTO grocery_list_sections
            (grocery_list_id, name, display_order)
            VALUES (?, 'First', 10)
        """, (grocery_list_id,))
        first_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("""
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, 'Second Item', 'Full', 'Two', 1, 20,
                    '2026-09-20T01:00:00Z', '2026-09-20T01:00:00Z', 2)
        """, (first_id,))
        conn.execute("""
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, 'First Item', NULL, NULL, 0, 10,
                    '2026-09-20T01:00:00Z', '2026-09-20T01:00:00Z', 2)
        """, (first_id,))
        conn.execute("""
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, 'Later Item', 'Low', 'One', 0, 1,
                    '2026-09-20T01:00:00Z', '2026-09-20T01:00:00Z', 2)
        """, (later_id,))
        conn.commit()
        conn.close()

    def test_management_roles_can_open_the_page(self):
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.get("/client/10/grocery-list")
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Active Client", response.data)
                self.assertIn(b"No sections have been added yet.", response.data)

    def test_unauthorized_and_inactive_users_are_denied(self):
        for user_id in (4, 5, 6, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                response = self.client.get("/client/10/grocery-list")
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.list_rows(), [])

    def test_unauthenticated_user_is_redirected_to_login(self):
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_missing_and_inactive_clients_are_rejected(self):
        self.login(1)
        for client_id in (30, 999):
            with self.subTest(client_id=client_id):
                response = self.client.get(
                    f"/client/{client_id}/grocery-list"
                )
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.list_rows(), [])

    def test_first_open_creates_one_default_list_for_requested_client(self):
        self.login(1)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        rows = self.list_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["client_id"], 10)
        self.assertEqual(rows[0]["title"], "Grocery List")
        self.assertEqual(rows[0]["created_by_user_id"], 1)

        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.list_rows()), 1)

    def test_empty_list_state_requires_no_shift_or_schedule_context(self):
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

        self.login(2)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)

    def test_sections_and_items_render_in_display_order(self):
        self.seed_content()
        self.login(2)
        response = self.client.get("/client/10/grocery-list")
        body = response.data
        self.assertEqual(response.status_code, 200)
        self.assertLess(body.index(b"First"), body.index(b"Later"))
        self.assertLess(body.index(b"First Item"), body.index(b"Second Item"))
        self.assertLess(body.index(b"Second Item"), body.index(b"Later Item"))
        self.assertIn(b"Current Stock", body)
        self.assertIn(b"Amount Needed", body)
        self.assertIn(b"Full", body)
        self.assertIn(b"Two", body)
        self.assertIn(b"Purchased", body)
        self.assertIn(b"Not Purchased", body)
        self.assertIn("—".encode(), body)

    def test_amount_needed_highlight_uses_only_non_blank_text(self):
        self.seed_content()
        conn = self.connect()
        grocery_list_id = conn.execute(
            "SELECT grocery_list_id FROM grocery_lists WHERE client_id = 10"
        ).fetchone()[0]
        section_id = conn.execute(
            "SELECT section_id FROM grocery_list_sections "
            "WHERE grocery_list_id = ? ORDER BY display_order LIMIT 1",
            (grocery_list_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, 'Needs Purchase', 'Available', '  2 bags  ', 0, 30,
                    '2026-09-20T01:00:00Z', '2026-09-20T01:00:00Z', 2),
                   (?, 'Blank Needed', 'Available', NULL, 0, 40,
                    '2026-09-20T01:00:00Z', '2026-09-20T01:00:00Z', 2),
                   (?, 'Whitespace Needed', 'Available', '   ', 0, 50,
                    '2026-09-20T01:00:00Z', '2026-09-20T01:00:00Z', 2)
            """,
            (section_id, section_id, section_id),
        )
        conn.commit()
        conn.close()

        self.login(2)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        body = response.data

        self.assertRegex(
            body,
            rb'<td class="grocery-needed-low">  2 bags  </td>',
        )
        self.assertNotRegex(
            body,
            rb'<td class="grocery-needed-low">\s*'
            + "—".encode()
            + rb'\s*</td>',
        )
        self.assertNotRegex(
            body,
            rb'<td class="grocery-needed-low">\s*</td>',
        )
        self.assertIn(b"<td>   </td>", body)
        self.assertIn(b"Whitespace Needed", body)

    def test_other_clients_list_data_is_not_returned(self):
        conn = self.connect()
        conn.execute("""
            INSERT INTO grocery_lists
            (client_id, title, created_by_user_id,
             created_at_utc, updated_at_utc)
            VALUES (20, 'Other Client Secret List', 2,
                    '2026-09-20T01:00:00Z',
                    '2026-09-20T01:00:00Z')
        """)
        conn.commit()
        conn.close()

        self.login(2)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Other Client Secret List", response.data)
        self.assertNotIn(b"Other Client", response.data)


if __name__ == "__main__":
    unittest.main()
