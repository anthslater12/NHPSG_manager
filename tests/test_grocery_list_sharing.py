import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app


class GroceryListSharingTests(unittest.TestCase):
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
                (5, 'Active Worker One', 'Support Worker', 1),
                (6, 'Inactive Worker', 'Support Worker', 0),
                (7, 'Active Worker Two', 'Support Worker', 1),
                (8, 'Active Worker Three', 'Support Worker', 1);
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
        list_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return list_id

    def create_share(
        self,
        list_id,
        user_id=5,
        permission="VIEW",
        shared_by_user_id=1,
    ):
        conn = self.connect()
        conn.execute("""
            INSERT INTO grocery_list_shares
            (grocery_list_id, user_id, permission,
             shared_by_user_id, shared_at_utc)
            VALUES (?, ?, ?, ?, '2026-09-20T01:00:00Z')
        """, (list_id, user_id, permission, shared_by_user_id))
        share_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return share_id

    def shares(self):
        conn = self.connect()
        rows = conn.execute(
            "SELECT * FROM grocery_list_shares ORDER BY share_id"
        ).fetchall()
        conn.close()
        return rows

    def test_eligible_users_are_displayed_and_filtered(self):
        list_id = self.create_list()
        self.create_share(list_id, user_id=7)
        self.login(1)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn(b"Active Worker One", body)
        self.assertNotIn(b"Behaviour Consultant", body.split(b"Share Grocery List", 1)[1].split(b"Currently Shared", 1)[0])
        self.assertNotIn(b"Inactive Worker", body.split(b"Share Grocery List", 1)[1].split(b"Currently Shared", 1)[0])
        self.assertNotIn(b"value=\"7\"", body)
        self.assertNotIn(b"value=\"1\"", body)
        self.assertNotIn(b"value=\"2\"", body)
        self.assertNotIn(b"value=\"3\"", body)
        self.assertIn(b"Active Worker Two", body)

    def test_all_management_roles_can_create_shares(self):
        self.create_list()
        for user_id, recipient_id, permission in (
            (1, 5, "VIEW"),
            (2, 7, "EDIT"),
            (3, 8, "VIEW"),
        ):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.post(
                    "/client/10/grocery-list/shares/new",
                    data={"user_id": recipient_id, "permission": permission},
                )
                self.assertEqual(response.status_code, 302)
        rows = self.shares()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["permission"], "EDIT")

    def test_non_management_users_cannot_manage_shares(self):
        list_id = self.create_list()
        share_id = self.create_share(list_id)
        for user_id in (4, 5, 6, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                self.assertEqual(
                    self.client.post(
                        "/client/10/grocery-list/shares/new",
                        data={"user_id": 7, "permission": "VIEW"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        f"/client/10/grocery-list/shares/{share_id}/permission",
                        data={"permission": "EDIT"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        f"/client/10/grocery-list/shares/{share_id}/delete",
                        data={"confirm": "yes"},
                    ).status_code,
                    403,
                )
        self.assertEqual(len(self.shares()), 1)

    def test_forged_recipient_and_permission_values_are_rejected(self):
        self.create_list()
        self.login(1)
        for recipient_id in (4, 6, 2):
            with self.subTest(recipient_id=recipient_id):
                response = self.client.post(
                    "/client/10/grocery-list/shares/new",
                    data={"user_id": recipient_id, "permission": "VIEW"},
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/client/10/grocery-list/shares/new",
            data={"user_id": 5, "permission": "INVALID"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.shares(), [])

    def test_share_creation_records_manager_timestamp_and_is_assignment_independent(self):
        list_id = self.create_list()
        share_time = datetime(2026, 9, 20, 5, 0, tzinfo=timezone.utc)
        self.login(2)
        with patch.object(app, "get_application_now_utc", return_value=share_time):
            response = self.client.post(
                "/client/10/grocery-list/shares/new",
                data={"user_id": 5, "permission": "EDIT"},
            )
        self.assertEqual(response.status_code, 302)
        row = self.shares()[0]
        self.assertEqual(row["grocery_list_id"], list_id)
        self.assertEqual(row["user_id"], 5)
        self.assertEqual(row["shared_by_user_id"], 2)
        self.assertEqual(row["shared_at_utc"], "2026-09-20T05:00:00Z")
        conn = self.connect()
        table_names = {
            item[0]
            for item in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertNotIn("shifts", table_names)
        self.assertNotIn("shift_staff", table_names)
        self.assertNotIn("schedule_weeks", table_names)
        conn.close()

    def test_duplicate_share_is_friendly(self):
        list_id = self.create_list()
        self.create_share(list_id, user_id=5)
        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/shares/new",
            data={"user_id": 5, "permission": "EDIT"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"already has a share", response.data)
        self.assertEqual(len(self.shares()), 1)

    def test_permission_updates_preserve_provenance_and_reject_cross_client(self):
        first_list = self.create_list(10)
        second_list = self.create_list(20)
        first_share = self.create_share(first_list, permission="VIEW")
        second_share = self.create_share(second_list, user_id=7, permission="EDIT")
        self.login(1)
        response = self.client.post(
            f"/client/10/grocery-list/shares/{first_share}/permission",
            data={"permission": "EDIT"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            f"/client/10/grocery-list/shares/{first_share}/permission",
            data={"permission": "VIEW"},
        )
        self.assertEqual(response.status_code, 302)
        updated = self.shares()[0]
        self.assertEqual(updated["permission"], "VIEW")
        self.assertEqual(updated["shared_by_user_id"], 1)
        self.assertEqual(updated["shared_at_utc"], "2026-09-20T01:00:00Z")

        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/shares/{second_share}/permission",
                data={"permission": "VIEW"},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/shares/{first_share}/permission",
                data={"permission": "INVALID"},
                follow_redirects=True,
            ).status_code,
            200,
        )

    def test_inactive_existing_share_is_displayed_but_not_eligible_for_changes(self):
        list_id = self.create_list()
        share_id = self.create_share(list_id, user_id=5)
        conn = self.connect()
        conn.execute("UPDATE users SET active = 0 WHERE user_id = 5")
        conn.commit()
        conn.close()
        self.login(1)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Active Worker One", response.data)
        self.assertIn(b"No longer eligible", response.data)
        response = self.client.post(
            f"/client/10/grocery-list/shares/{share_id}/permission",
            data={"permission": "EDIT"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.shares()[0]["permission"], "VIEW")

    def test_manager_can_remove_share_post_only_and_cross_client_isolated(self):
        first_list = self.create_list(10)
        second_list = self.create_list(20)
        first_share = self.create_share(first_list)
        second_share = self.create_share(second_list, user_id=7)
        self.login(1)
        self.assertEqual(
            self.client.get(
                f"/client/10/grocery-list/shares/{first_share}/delete"
            ).status_code,
            405,
        )
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/shares/{second_share}/delete",
                data={"confirm": "yes"},
            ).status_code,
            404,
        )
        response = self.client.post(
            f"/client/10/grocery-list/shares/{first_share}/delete",
            data={"confirm": "yes"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.shares()), 1)
        self.assertEqual(self.shares()[0]["share_id"], second_share)

    def test_view_share_grants_support_worker_grocery_list_access(self):
        list_id = self.create_list()
        self.create_share(list_id, user_id=5, permission="VIEW")
        self.login(5)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client A", response.data)


if __name__ == "__main__":
    unittest.main()
