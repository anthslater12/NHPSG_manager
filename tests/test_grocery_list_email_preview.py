import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class GroceryListEmailPreviewTests(unittest.TestCase):
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
            7: "Director",
        }
        with self.client.session_transaction() as session:
            session.clear()
            session["user_id"] = user_id
            session["role"] = session_role or roles[user_id]
            session["full_name"] = "Session Display Name"

    def create_list(self, client_id=10, title="Grocery List"):
        conn = self.connect()
        list_id = conn.execute(
            """
            INSERT INTO grocery_lists
            (client_id, title, created_by_user_id,
             created_at_utc, updated_at_utc)
            VALUES (?, ?, 1, '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z')
            """,
            (client_id, title),
        ).lastrowid
        conn.commit()
        conn.close()
        return list_id

    def add_section(self, list_id, name="Pantry", display_order=0):
        conn = self.connect()
        section_id = conn.execute(
            """
            INSERT INTO grocery_list_sections
            (grocery_list_id, name, display_order)
            VALUES (?, ?, ?)
            """,
            (list_id, name, display_order),
        ).lastrowid
        conn.commit()
        conn.close()
        return section_id

    def add_item(
        self,
        section_id,
        item_name="Milk",
        display_order=0,
        stock_text="Low",
        needed_text="2 cartons",
        purchased=0,
    ):
        conn = self.connect()
        item_id = conn.execute(
            """
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, ?, ?, ?, ?, ?, '2026-01-01T00:00:00Z',
                    '2026-01-01T00:00:00Z', 1)
            """,
            (
                section_id,
                item_name,
                stock_text,
                needed_text,
                purchased,
                display_order,
            ),
        ).lastrowid
        conn.commit()
        conn.close()
        return item_id

    def add_recipient(
        self,
        list_id,
        email_address="contact@example.com",
        display_name="Contact",
    ):
        conn = self.connect()
        recipient_id = conn.execute(
            """
            INSERT INTO grocery_list_email_recipients
            (grocery_list_id, display_name, email_address,
             created_by_user_id, created_at_utc,
             updated_by_user_id, updated_at_utc)
            VALUES (?, ?, ?, 1, '2026-01-01T00:00:00Z',
                    1, '2026-01-01T00:00:00Z')
            """,
            (list_id, display_name, email_address),
        ).lastrowid
        conn.commit()
        conn.close()
        return recipient_id

    def share(self, list_id, user_id, permission):
        conn = self.connect()
        conn.execute(
            """
            INSERT INTO grocery_list_shares
            (grocery_list_id, user_id, permission,
             shared_by_user_id, shared_at_utc)
            VALUES (?, ?, ?, 1, '2026-01-01T00:00:00Z')
            """,
            (list_id, user_id, permission),
        )
        conn.commit()
        conn.close()

    def snapshot_rows(self, list_id):
        conn = self.connect()
        rows = conn.execute(
            """
            SELECT snapshot_id, snapshot_kind, week_start,
                   captured_by_user_id, captured_at_utc
            FROM grocery_list_snapshots
            WHERE grocery_list_id = ?
            ORDER BY snapshot_id
            """,
            (list_id,),
        ).fetchall()
        conn.close()
        return rows

    def test_management_roles_can_prepare_preview(self):
        list_id = self.create_list()
        self.add_recipient(list_id)
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                response = self.client.post(
                    "/client/10/grocery-list/email-preview"
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Grocery List Email Preview", response.data)
                self.assertIn(b"This is a preview only", response.data)

    def test_behaviour_consultant_shared_workers_and_inactive_manager_are_denied(
        self,
    ):
        list_id = self.create_list()
        self.add_recipient(list_id)
        self.share(list_id, 5, "VIEW")
        self.share(list_id, 6, "EDIT")

        for user_id in (4, 5, 6, 7, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                response = self.client.post(
                    "/client/10/grocery-list/email-preview"
                )
                self.assertEqual(response.status_code, 403)

        self.assertEqual(self.snapshot_rows(list_id), [])

    def test_get_is_not_a_state_changing_preview_request(self):
        list_id = self.create_list()
        self.add_recipient(list_id)
        self.login(1)
        response = self.client.get("/client/10/grocery-list/email-preview")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self.snapshot_rows(list_id), [])

    def test_preview_renders_all_recipients_and_expected_body_fields(self):
        list_id = self.create_list()
        first_section = self.add_section(list_id, "Refrigerator", 0)
        second_section = self.add_section(list_id, "Frozen", 1)
        self.add_item(
            first_section,
            item_name="Milk",
            stock_text="Low",
            needed_text="2 cartons",
        )
        self.add_item(
            first_section,
            item_name="Bread",
            stock_text="1/3 loaf",
            needed_text="2 loaves",
            purchased=1,
        )
        self.add_item(
            second_section,
            item_name="Chicken tenders",
            stock_text=None,
            needed_text="1 bag",
        )
        self.add_recipient(list_id, "jane@example.com", "Jane Smith")
        self.add_recipient(list_id, "mom@example.com", "Mom")

        self.login(1)
        response = self.client.post("/client/10/grocery-list/email-preview")
        body = response.data
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Grocery List - Client A", body)
        self.assertIn(b"Jane Smith", body)
        self.assertIn(b"jane@example.com", body)
        self.assertIn(b"Mom", body)
        self.assertIn(b"mom@example.com", body)
        self.assertIn(b"Refrigerator", body)
        self.assertIn(b"Frozen", body)
        self.assertIn(b"- Milk | Current: Low | Needed: 2 cartons", body)
        self.assertIn(
            b"- Bread | Current: 1/3 loaf | Needed: 2 loaves | Purchased",
            body,
        )
        self.assertIn(b"- Chicken tenders | Needed: 1 bag", body)
        self.assertNotIn(b"None", body)

    def test_preview_requires_recipients_and_does_not_create_snapshot(self):
        list_id = self.create_list()
        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/email-preview",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Add at least one email recipient", response.data)
        self.assertEqual(self.snapshot_rows(list_id), [])

    def test_recipient_from_another_list_is_not_included(self):
        first_list = self.create_list()
        second_list = self.create_list(client_id=20, title="Second List")
        self.add_recipient(first_list, "first@example.com", "First")
        self.add_recipient(second_list, "other@example.com", "Other")
        self.login(1)
        response = self.client.post("/client/10/grocery-list/email-preview")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"first@example.com", response.data)
        self.assertNotIn(b"other@example.com", response.data)

    def test_preview_creates_one_email_snapshot_with_manager_and_no_week(self):
        list_id = self.create_list()
        section_id = self.add_section(list_id, "Pantry")
        self.add_item(section_id)
        self.add_recipient(list_id)
        self.login(2)
        response = self.client.post("/client/10/grocery-list/email-preview")
        self.assertEqual(response.status_code, 200)
        rows = self.snapshot_rows(list_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["snapshot_kind"], "EMAIL")
        self.assertIsNone(rows[0]["week_start"])
        self.assertEqual(rows[0]["captured_by_user_id"], 2)
        self.assertIn(rows[0]["captured_at_utc"].encode(), response.data)

    def test_snapshot_sections_and_items_match_preparation_state(self):
        list_id = self.create_list()
        section_id = self.add_section(list_id, "Original", 0)
        self.add_item(
            section_id,
            item_name="Original Item",
            stock_text="One",
            needed_text="Two",
            purchased=1,
        )
        self.add_recipient(list_id)
        self.login(1)
        response = self.client.post("/client/10/grocery-list/email-preview")
        self.assertEqual(response.status_code, 200)
        snapshot_id = self.snapshot_rows(list_id)[0]["snapshot_id"]
        conn = self.connect()
        section = conn.execute(
            "SELECT name, display_order FROM grocery_list_snapshot_sections "
            "WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        item = conn.execute(
            """
            SELECT i.item_name, i.stock_text, i.needed_text,
                   i.purchased, i.display_order
            FROM grocery_list_snapshot_items AS i
            JOIN grocery_list_snapshot_sections AS s
              ON s.snapshot_section_id = i.snapshot_section_id
            WHERE s.snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        conn.close()
        self.assertEqual(tuple(section), ("Original", 0))
        self.assertEqual(
            tuple(item),
            ("Original Item", "One", "Two", 1, 0),
        )

    def test_later_current_edits_do_not_change_preview_snapshot_content(self):
        list_id = self.create_list()
        section_id = self.add_section(list_id, "Pantry")
        item_id = self.add_item(
            section_id,
            item_name="Original Item",
            stock_text="Original Stock",
            needed_text="Original Need",
        )
        self.add_recipient(list_id)
        self.login(1)
        preview = self.client.post("/client/10/grocery-list/email-preview")
        self.assertEqual(preview.status_code, 200)
        snapshot_id = self.snapshot_rows(list_id)[0]["snapshot_id"]

        conn = self.connect()
        conn.execute(
            "UPDATE grocery_list_items SET item_name = ?, stock_text = ?, "
            "needed_text = ? WHERE item_id = ?",
            ("Changed Item", "Changed Stock", "Changed Need", item_id),
        )
        conn.commit()
        snapshot_item = conn.execute(
            """
            SELECT i.item_name, i.stock_text, i.needed_text
            FROM grocery_list_snapshot_items AS i
            JOIN grocery_list_snapshot_sections AS s
              ON s.snapshot_section_id = i.snapshot_section_id
            WHERE s.snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        conn.close()

        self.assertIn(b"Original Item", preview.data)
        self.assertNotIn(b"Changed Item", preview.data)
        self.assertEqual(
            tuple(snapshot_item),
            ("Original Item", "Original Stock", "Original Need"),
        )

    def test_email_snapshot_is_not_shown_in_weekly_history(self):
        list_id = self.create_list()
        self.add_recipient(list_id)
        self.login(1)
        preview = self.client.post("/client/10/grocery-list/email-preview")
        self.assertEqual(preview.status_code, 200)
        history = self.client.get("/client/10/grocery-list/history")
        self.assertEqual(history.status_code, 200)
        self.assertNotIn(b"EMAIL", history.data)
        self.assertIn(b"No weekly Grocery List history is available.", history.data)

    def test_empty_list_and_empty_sections_render_sensibly(self):
        list_id = self.create_list()
        self.add_section(list_id, "Empty Section")
        self.add_recipient(list_id)
        self.login(1)
        response = self.client.post("/client/10/grocery-list/email-preview")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Empty Section", response.data)
        self.assertIn(b"No items.", response.data)

        empty_list = self.create_list(client_id=20, title="Empty List")
        self.add_recipient(empty_list)
        response = self.client.post("/client/20/grocery-list/email-preview")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No sections have been added.", response.data)

    def test_inactive_and_missing_clients_are_rejected(self):
        self.login(1)
        for client_id in (30, 999):
            with self.subTest(client_id=client_id):
                response = self.client.post(
                    f"/client/{client_id}/grocery-list/email-preview"
                )
                self.assertEqual(response.status_code, 404)

    def test_snapshot_failure_does_not_render_preview_or_leave_partial_snapshot(
        self,
    ):
        list_id = self.create_list()
        section_id = self.add_section(list_id)
        self.add_item(section_id)
        self.add_recipient(list_id)
        conn = self.connect()
        conn.execute(
            """
            CREATE TRIGGER fail_email_snapshot
            BEFORE INSERT ON grocery_list_snapshot_items
            BEGIN
                SELECT RAISE(ABORT, 'forced email snapshot failure');
            END;
            """
        )
        conn.commit()
        conn.close()

        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/email-preview",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"could not be prepared", response.data)
        self.assertNotIn(b"Grocery List Email Preview", response.data)
        self.assertEqual(self.snapshot_rows(list_id), [])

    def test_preview_does_not_send_email_or_require_shift_context(self):
        list_id = self.create_list()
        self.add_recipient(list_id)
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
        with patch.object(app.mail_service, "send_email") as send_email:
            response = self.client.post(
                "/client/10/grocery-list/email-preview"
            )
        self.assertEqual(response.status_code, 200)
        send_email.assert_not_called()

    def test_cross_client_path_cannot_prepare_another_list(self):
        first_list = self.create_list()
        second_list = self.create_list(client_id=20)
        self.add_recipient(first_list, "first@example.com")
        self.add_recipient(second_list, "second@example.com")
        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/email-preview",
            data={"client_id": "20", "grocery_list_id": str(second_list)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"first@example.com", response.data)
        self.assertNotIn(b"second@example.com", response.data)
        self.assertEqual(len(self.snapshot_rows(first_list)), 1)
        self.assertEqual(len(self.snapshot_rows(second_list)), 0)


if __name__ == "__main__":
    unittest.main()
