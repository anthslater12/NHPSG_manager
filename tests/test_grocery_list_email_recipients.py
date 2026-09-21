import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class GroceryListEmailRecipientTests(unittest.TestCase):
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
                (5, 'View Worker', 'Support Worker', 1),
                (6, 'Edit Worker', 'Support Worker', 1),
                (7, 'Inactive Director', 'Director', 0);
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
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO grocery_lists
            (client_id, title, created_by_user_id,
             created_at_utc, updated_at_utc)
            VALUES (10, 'Client Ten List', 1,
                    '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO grocery_lists
            (client_id, title, created_by_user_id,
             created_at_utc, updated_at_utc)
            VALUES (20, 'Client Twenty List', 1,
                    '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z')
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
            6: "Support Worker",
            7: "Director",
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

    def list_id(self, client_id=10):
        conn = self.connect()
        row = conn.execute(
            "SELECT grocery_list_id FROM grocery_lists WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        conn.close()
        return row["grocery_list_id"]

    def insert_recipient(
        self,
        grocery_list_id=None,
        display_name="Existing Contact",
        email_address="existing@example.com",
        created_by_user_id=1,
        created_at_utc="2026-01-01T00:00:00Z",
        updated_by_user_id=1,
        updated_at_utc="2026-01-01T00:00:00Z",
    ):
        conn = self.connect()
        if grocery_list_id is None:
            grocery_list_id = self.list_id()
        recipient_id = conn.execute(
            """
            INSERT INTO grocery_list_email_recipients
            (grocery_list_id, display_name, email_address,
             created_by_user_id, created_at_utc,
             updated_by_user_id, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                grocery_list_id,
                display_name,
                email_address,
                created_by_user_id,
                created_at_utc,
                updated_by_user_id,
                updated_at_utc,
            ),
        ).lastrowid
        conn.commit()
        conn.close()
        return recipient_id

    def insert_share(self, user_id, client_id=10, permission="VIEW"):
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO grocery_list_shares
            (grocery_list_id, user_id, permission,
             shared_by_user_id, shared_at_utc)
            VALUES (?, ?, ?, 1, '2026-01-01T00:00:00Z')
            """,
            (self.list_id(client_id), user_id, permission),
        )
        conn.commit()
        conn.close()

    def recipient_rows(self):
        conn = self.connect()
        rows = conn.execute(
            """
            SELECT * FROM grocery_list_email_recipients
            ORDER BY recipient_id
            """
        ).fetchall()
        conn.close()
        return rows

    def test_management_roles_see_recipient_controls(self):
        self.insert_recipient()
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.get("/client/10/grocery-list")
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Email Recipients", response.data)
                self.assertIn(b"existing@example.com", response.data)
                self.assertIn(b"Add Recipient", response.data)
                self.assertIn(b"Edit", response.data)
                self.assertIn(b"Delete", response.data)

    def test_shared_workers_and_behaviour_consultant_do_not_manage_recipients(
        self,
    ):
        self.insert_recipient()
        self.insert_share(5, permission="VIEW")
        self.insert_share(6, permission="EDIT")

        for user_id in (5, 6):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.get("/client/10/grocery-list")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"Email Recipients", response.data)
                self.assertNotIn(b"existing@example.com", response.data)

        self.login(4)
        response = self.client.get("/client/10/grocery-list")
        self.assertEqual(response.status_code, 403)

    def test_manager_can_add_trimmed_recipient_with_provenance(self):
        self.login(2)
        response = self.client.post(
            "/client/10/grocery-list/email-recipients/new",
            data={
                "display_name": "  Jane Smith  ",
                "email_address": "  jane@example.com ",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/client/10/grocery-list", response.headers["Location"])

        rows = self.recipient_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["display_name"], "Jane Smith")
        self.assertEqual(rows[0]["email_address"], "jane@example.com")
        self.assertEqual(rows[0]["created_by_user_id"], 2)
        self.assertEqual(rows[0]["updated_by_user_id"], 2)
        self.assertTrue(rows[0]["created_at_utc"].endswith("Z"))
        self.assertTrue(rows[0]["updated_at_utc"].endswith("Z"))

    def test_blank_display_name_is_stored_as_null(self):
        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/email-recipients/new",
            data={"display_name": "   ", "email_address": "mom@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.recipient_rows()[0]["display_name"])

    def test_invalid_email_addresses_are_rejected_without_raw_sql_errors(self):
        invalid_addresses = (
            "",
            "   ",
            "not-an-email",
            "has space@example.com",
            "a" * 251 + "@example.com",
        )
        for email_address in invalid_addresses:
            with self.subTest(email_address=email_address):
                self.login(1)
                response = self.client.post(
                    "/client/10/grocery-list/email-recipients/new",
                    data={
                        "display_name": "Invalid",
                        "email_address": email_address,
                    },
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"Traceback", response.data)
        self.assertEqual(self.recipient_rows(), [])

    def test_duplicate_same_list_is_rejected_but_other_list_is_allowed(self):
        self.insert_recipient(email_address="shared@example.com")
        self.login(1)
        duplicate = self.client.post(
            "/client/10/grocery-list/email-recipients/new",
            data={"email_address": " shared@example.com "},
            follow_redirects=True,
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(b"already saved", duplicate.data)
        self.assertEqual(len(self.recipient_rows()), 1)

        other_list = self.client.post(
            "/client/20/grocery-list/email-recipients/new",
            data={"email_address": "shared@example.com"},
        )
        self.assertEqual(other_list.status_code, 302)
        self.assertEqual(len(self.recipient_rows()), 2)

    def test_recipient_management_does_not_require_shift_assignment_or_schedule(
        self,
    ):
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

        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/email-recipients/new",
            data={"email_address": "independent@example.com"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.recipient_rows()), 1)

    def test_add_is_manager_only_and_uses_database_authority(self):
        for user_id in (4, 5, 6, 7, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                response = self.client.post(
                    "/client/10/grocery-list/email-recipients/new",
                    data={"email_address": "blocked@example.com"},
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.recipient_rows(), [])

    def test_recipient_actions_reject_inactive_and_missing_clients(self):
        self.login(1)
        for client_id in (30, 999):
            with self.subTest(client_id=client_id):
                response = self.client.post(
                    f"/client/{client_id}/grocery-list/email-recipients/new",
                    data={"email_address": "blocked@example.com"},
                )
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.recipient_rows(), [])

    def test_edit_preserves_creation_provenance_and_updates_editor(self):
        recipient_id = self.insert_recipient(
            display_name="Old Name",
            email_address="old@example.com",
            created_by_user_id=1,
            created_at_utc="2026-01-01T00:00:00Z",
            updated_by_user_id=1,
            updated_at_utc="2026-01-01T00:00:00Z",
        )
        self.login(3)
        response = self.client.post(
            f"/client/10/grocery-list/email-recipients/{recipient_id}/edit",
            data={
                "display_name": "  New Name ",
                "email_address": " new@example.com ",
            },
        )
        self.assertEqual(response.status_code, 302)
        row = self.recipient_rows()[0]
        self.assertEqual(row["display_name"], "New Name")
        self.assertEqual(row["email_address"], "new@example.com")
        self.assertEqual(row["created_by_user_id"], 1)
        self.assertEqual(row["created_at_utc"], "2026-01-01T00:00:00Z")
        self.assertEqual(row["updated_by_user_id"], 3)
        self.assertNotEqual(row["updated_at_utc"], "2026-01-01T00:00:00Z")

    def test_edit_invalid_and_duplicate_email_are_rejected_cleanly(self):
        first_id = self.insert_recipient(email_address="first@example.com")
        self.insert_recipient(email_address="second@example.com")
        self.login(1)
        invalid = self.client.post(
            f"/client/10/grocery-list/email-recipients/{first_id}/edit",
            data={"email_address": "not valid"},
            follow_redirects=True,
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertNotIn(b"Traceback", invalid.data)
        duplicate = self.client.post(
            f"/client/10/grocery-list/email-recipients/{first_id}/edit",
            data={"email_address": "second@example.com"},
            follow_redirects=True,
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(b"already saved", duplicate.data)
        self.assertEqual(self.recipient_rows()[0]["email_address"], "first@example.com")

    def test_cross_client_recipient_cannot_be_edited(self):
        recipient_id = self.insert_recipient(
            grocery_list_id=self.list_id(20),
            email_address="other@example.com",
        )
        self.login(1)
        response = self.client.post(
            f"/client/10/grocery-list/email-recipients/{recipient_id}/edit",
            data={"email_address": "forged@example.com"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.recipient_rows()[0]["email_address"], "other@example.com")

    def test_delete_requires_post_confirmation_and_preserves_other_recipients(
        self,
    ):
        first_id = self.insert_recipient(email_address="first@example.com")
        second_id = self.insert_recipient(email_address="second@example.com")
        self.login(1)
        get_response = self.client.get(
            f"/client/10/grocery-list/email-recipients/{first_id}/delete"
        )
        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(len(self.recipient_rows()), 2)

        unconfirmed = self.client.post(
            f"/client/10/grocery-list/email-recipients/{first_id}/delete",
            follow_redirects=True,
        )
        self.assertEqual(unconfirmed.status_code, 200)
        self.assertIn(b"explicitly confirm", unconfirmed.data)
        self.assertEqual(len(self.recipient_rows()), 2)

        deleted = self.client.post(
            f"/client/10/grocery-list/email-recipients/{first_id}/delete",
            data={"confirm": "yes"},
        )
        self.assertEqual(deleted.status_code, 302)
        rows = self.recipient_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recipient_id"], second_id)

    def test_only_managers_can_delete_and_cross_client_delete_is_rejected(self):
        recipient_id = self.insert_recipient()
        for user_id in (4, 5, 6, 7, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                denied = self.client.post(
                    f"/client/10/grocery-list/email-recipients/{recipient_id}/delete",
                    data={"confirm": "yes"},
                )
                self.assertEqual(denied.status_code, 403)
        self.assertEqual(len(self.recipient_rows()), 1)

        other_id = self.insert_recipient(
            grocery_list_id=self.list_id(20),
            email_address="other@example.com",
        )
        self.login(1)
        forged = self.client.post(
            f"/client/10/grocery-list/email-recipients/{other_id}/delete",
            data={"confirm": "yes"},
        )
        self.assertEqual(forged.status_code, 404)
        self.assertEqual(len(self.recipient_rows()), 2)

    def test_add_does_not_create_snapshot_or_send_email(self):
        self.login(1)
        with patch.object(app.mail_service, "send_email") as send_email:
            response = self.client.post(
                "/client/10/grocery-list/email-recipients/new",
                data={"email_address": "external@example.com"},
            )
        send_email.assert_not_called()
        self.assertEqual(response.status_code, 302)
        conn = self.connect()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshots"
            ).fetchone()[0],
            0,
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
