import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class GroceryListNavigationTests(unittest.TestCase):
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
                (6, 'Inactive Worker', 'Support Worker', 0);
            INSERT INTO clients (client_id, client_name, active) VALUES
                (10, 'Client A', 1),
                (20, 'Client B', 1),
                (30, 'Inactive Client', 0),
                (40, 'Unshared Client', 1);
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
        }
        with self.client.session_transaction() as session:
            session.clear()
            session["user_id"] = user_id
            session["role"] = session_role or roles[user_id]
            session["full_name"] = "Session Display Name"

    def create_list(self, client_id, title=None):
        title = title or f"List for {client_id}"
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO grocery_lists
            (client_id, title, created_by_user_id,
             created_at_utc, updated_at_utc)
            VALUES (?, ?, 1, '2026-09-20T01:00:00Z',
                    '2026-09-20T01:00:00Z')
            """,
            (client_id, title),
        )
        list_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return list_id

    def share(self, list_id, user_id=5, permission="VIEW"):
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO grocery_list_shares
            (grocery_list_id, user_id, permission,
             shared_by_user_id, shared_at_utc)
            VALUES (?, ?, ?, 1, '2026-09-20T01:00:00Z')
            """,
            (list_id, user_id, permission),
        )
        conn.commit()
        conn.close()

    def activate_only_client(self, client_id):
        conn = self.connect()
        conn.execute(
            "UPDATE clients SET active = CASE WHEN client_id = ? "
            "THEN 1 ELSE 0 END",
            (client_id,),
        )
        conn.commit()
        conn.close()

    def test_management_roles_discover_active_client_grocery_lists(self):
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.get("/clients?status=active")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.data.count(b'href="/grocery-list"'),
                    1,
                )
                self.assertIn(b"Grocery List", response.data)
                self.assertIn(b"/client/10/grocery-list", response.data)
                self.assertNotIn(b"/client/30/grocery-list", response.data)

    def test_behaviour_consultant_and_support_worker_do_not_see_management_link(self):
        self.login(4)
        consultant_response = self.client.get("/clients")
        self.assertEqual(consultant_response.status_code, 403)

        self.login(5)
        worker_response = self.client.get("/clients")
        self.assertEqual(worker_response.status_code, 403)

    def test_shared_view_and_edit_workers_see_navigation_and_index_rows(self):
        view_list = self.create_list(10, "A Shared List")
        edit_list = self.create_list(20, "B Shared List")
        inactive_client_list = self.create_list(30, "Inactive Client List")
        unshared_list = self.create_list(40, "Unshared List")
        self.share(view_list, permission="VIEW")
        self.share(edit_list, permission="EDIT")
        self.share(inactive_client_list, permission="EDIT")

        self.login(5)
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data.count(b'href="/grocery-list"'),
            1,
        )
        self.assertIn(b"Grocery List", response.data)

        response = self.client.get("/grocery-lists")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client A", response.data)
        self.assertIn(b"A Shared List", response.data)
        self.assertIn(b"VIEW", response.data)
        self.assertIn(b"Client B", response.data)
        self.assertIn(b"B Shared List", response.data)
        self.assertIn(b"EDIT", response.data)
        self.assertNotIn(b"Inactive Client", response.data)
        self.assertNotIn(b"Unshared Client", response.data)
        self.assertNotIn(str(unshared_list).encode(), response.data)

    def test_unshared_and_inactive_support_workers_have_no_discovery(self):
        list_id = self.create_list(10)
        self.share(list_id, user_id=6, permission="VIEW")

        self.login(6)
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/grocery-lists").status_code, 403)

        self.login(5)
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'href="/grocery-list"', response.data)

    def test_behaviour_consultant_with_manual_share_has_no_discovery(self):
        list_id = self.create_list(10)
        self.share(list_id, user_id=4, permission="EDIT")
        self.login(4)
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'href="/grocery-list"', response.data)
        self.assertEqual(self.client.get("/grocery-lists").status_code, 403)

    def test_removed_share_and_invalid_permission_disappear_from_discovery(self):
        view_list = self.create_list(10)
        edit_list = self.create_list(20)
        invalid_list = self.create_list(40)
        self.share(view_list, permission="VIEW")
        self.share(edit_list, permission="EDIT")
        conn = self.connect()
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            """
            INSERT INTO grocery_list_shares
            (grocery_list_id, user_id, permission,
             shared_by_user_id, shared_at_utc)
            VALUES (?, 5, 'INVALID', 1, '2026-09-20T01:00:00Z')
            """,
            (invalid_list,),
        )
        conn.commit()
        conn.execute(
            "DELETE FROM grocery_list_shares WHERE grocery_list_id = ?",
            (view_list,),
        )
        conn.commit()
        conn.close()

        self.login(5)
        response = self.client.get("/grocery-lists")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Client A", response.data)
        self.assertIn(b"Client B", response.data)
        self.assertNotIn(b"Unshared Client", response.data)

        conn = self.connect()
        conn.execute(
            "DELETE FROM grocery_list_shares WHERE grocery_list_id = ?",
            (edit_list,),
        )
        conn.commit()
        conn.close()
        response = self.client.get("/worker-resources")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'href="/grocery-list"', response.data)

    def test_one_active_client_redirects_without_hard_coded_client_details(self):
        conn = self.connect()
        conn.execute("UPDATE clients SET active = 0")
        conn.execute(
            "INSERT INTO clients (client_id, client_name, active) "
            "VALUES (42, 'Different Active Client', 1)"
        )
        conn.commit()
        conn.close()

        list_id = self.create_list(42, "Different Client Grocery List")

        self.login(1)
        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/client/42/grocery-list")
        self.assertNotIn(b"Neville", response.data)
        self.assertNotIn(b"client_id=1", response.data)
        self.assertIsNotNone(list_id)

    def test_multiple_active_clients_fall_back_to_grocery_lists_index(self):
        self.create_list(10)
        self.login(1)

        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/grocery-lists")

    def test_direct_navigation_preserves_shared_worker_access(self):
        list_id = self.create_list(10)
        self.share(list_id, user_id=5, permission="VIEW")
        self.activate_only_client(10)

        self.login(5)
        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/client/10/grocery-list")

    def test_behaviour_consultant_cannot_use_direct_navigation(self):
        self.create_list(10)
        self.activate_only_client(10)
        self.login(4)

        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 403)

    def test_unshared_support_worker_cannot_use_direct_navigation(self):
        self.create_list(10)
        self.activate_only_client(10)
        self.login(5)

        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 403)

    def test_inactive_user_cannot_use_direct_navigation(self):
        self.login(6)

        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_user_is_redirected_to_login(self):
        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_edit_share_reaches_the_single_active_client_grocery_list(self):
        list_id = self.create_list(10)
        self.share(list_id, user_id=5, permission="EDIT")
        self.activate_only_client(10)
        self.login(5)

        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/client/10/grocery-list")

    def test_share_for_different_client_does_not_grant_direct_access(self):
        shared_list = self.create_list(10)
        self.create_list(20)
        self.share(shared_list, user_id=5, permission="VIEW")
        self.activate_only_client(20)
        self.login(5)

        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 403)

    def test_zero_active_clients_fall_back_to_grocery_lists_index(self):
        conn = self.connect()
        conn.execute("UPDATE clients SET active = 0")
        conn.commit()
        conn.close()
        self.login(1)

        response = self.client.get("/grocery-list")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/grocery-lists")

    def test_shared_direct_route_authorization_and_no_shift_dependencies(self):
        shared_list = self.create_list(10)
        self.share(shared_list, permission="VIEW")
        self.login(5)
        self.assertEqual(
            self.client.get("/client/10/grocery-list").status_code,
            200,
        )
        self.assertEqual(
            self.client.get("/client/20/grocery-list").status_code,
            403,
        )

        conn = self.connect()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertNotIn("shifts", tables)
        self.assertNotIn("shift_staff", tables)
        self.assertNotIn("schedule_weeks", tables)
        conn.close()


if __name__ == "__main__":
    unittest.main()
