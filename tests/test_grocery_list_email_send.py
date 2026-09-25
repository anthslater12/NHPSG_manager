import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class GroceryListEmailSendTests(unittest.TestCase):
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
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT,
                activity_class TEXT,
                activity_type TEXT,
                user_id INTEGER,
                client_id INTEGER,
                shift_id INTEGER,
                related_table TEXT,
                related_id INTEGER,
                summary TEXT,
                details TEXT,
                success INTEGER NOT NULL DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT
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
        stock_text="Low",
        needed_text="2 cartons",
        purchased=0,
        display_order=0,
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

    def delete_recipient(self, recipient_id):
        conn = self.connect()
        conn.execute(
            "DELETE FROM grocery_list_email_recipients WHERE recipient_id = ?",
            (recipient_id,),
        )
        conn.commit()
        conn.close()

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

    def prepare_preview(self, list_id=None, add_default=True):
        if list_id is None:
            list_id = self.create_list()
        if add_default:
            self.add_recipient(list_id)
        self.login(1)
        response = self.client.post("/client/10/grocery-list/email-preview")
        self.assertEqual(response.status_code, 200)
        return self.snapshot_id(list_id), response

    def snapshot_id(self, list_id, kind="EMAIL"):
        conn = self.connect()
        row = conn.execute(
            """
            SELECT snapshot_id FROM grocery_list_snapshots
            WHERE grocery_list_id = ? AND snapshot_kind = ?
            ORDER BY snapshot_id DESC LIMIT 1
            """,
            (list_id, kind),
        ).fetchone()
        conn.close()
        return None if row is None else row["snapshot_id"]

    def snapshot_count(self, list_id):
        conn = self.connect()
        count = conn.execute(
            """
            SELECT COUNT(*) FROM grocery_list_snapshots
            WHERE grocery_list_id = ? AND snapshot_kind = 'EMAIL'
            """,
            (list_id,),
        ).fetchone()[0]
        conn.close()
        return count

    def activity_rows(self):
        conn = self.connect()
        rows = conn.execute(
            """
            SELECT activity_type, success, client_id,
                   related_table, related_id, details
            FROM activity_log
            WHERE activity_class = 'GROCERY_LIST'
            ORDER BY activity_id
            """
        ).fetchall()
        conn.close()
        return rows

    def test_all_management_roles_send_through_existing_mail_service(self):
        list_id = self.create_list()
        snapshot_id, _preview = self.prepare_preview(list_id)
        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                with patch("app.mail_service.send_email") as send_email:
                    response = self.client.post(
                        f"/client/10/grocery-list/email-send/{snapshot_id}"
                    )
                self.assertEqual(response.status_code, 302)
                send_email.assert_called_once()
                self.assertEqual(send_email.call_args.args[0], "contact@example.com")
                self.assertEqual(
                    send_email.call_args.args[1],
                    "Grocery List",
                )

    def test_non_managers_cannot_send_even_with_edit_share(self):
        list_id = self.create_list()
        snapshot_id, _preview = self.prepare_preview(list_id)
        self.share(list_id, 5, "VIEW")
        self.share(list_id, 6, "EDIT")
        for user_id in (4, 5, 6, 7, 999):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Admin")
                with patch("app.mail_service.send_email") as send_email:
                    response = self.client.post(
                        f"/client/10/grocery-list/email-send/{snapshot_id}"
                    )
                self.assertEqual(response.status_code, 403)
                send_email.assert_not_called()

    def test_send_requires_email_snapshot_for_same_client(self):
        list_id = self.create_list()
        self.add_recipient(list_id)
        conn = self.connect()
        weekly_id = app.create_grocery_list_snapshot(
            conn,
            list_id,
            "WEEKLY",
            1,
            week_start="2026-09-14",
        )
        conn.close()
        self.login(1)
        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{weekly_id}"
            )
        self.assertEqual(response.status_code, 404)
        send_email.assert_not_called()

        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                "/client/10/grocery-list/email-send/999999"
            )
        self.assertEqual(response.status_code, 404)
        send_email.assert_not_called()

    def test_snapshot_from_another_list_or_client_is_rejected(self):
        first_list = self.create_list()
        second_list = self.create_list(client_id=20)
        first_snapshot, _preview = self.prepare_preview(first_list)
        self.add_recipient(second_list)
        self.login(1)
        conn = self.connect()
        second_snapshot = app.create_grocery_list_snapshot(
            conn, second_list, "EMAIL", 1
        )
        conn.close()

        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{second_snapshot}"
            )
        self.assertEqual(response.status_code, 404)
        send_email.assert_not_called()

        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                f"/client/20/grocery-list/email-send/{first_snapshot}"
            )
        self.assertEqual(response.status_code, 404)
        send_email.assert_not_called()

    def test_send_uses_server_side_subject_body_and_snapshot_content(self):
        list_id = self.create_list()
        private_client_name = "PRIVATE CLIENT NAME 123"
        conn = self.connect()
        conn.execute(
            "UPDATE clients SET client_name = ? WHERE client_id = 10",
            (private_client_name,),
        )
        conn.commit()
        conn.close()
        section_id = self.add_section(list_id, "Pantry")
        self.add_item(
            section_id,
            item_name="Original Item",
            stock_text="Original Stock",
            needed_text="Original Need",
            purchased=1,
        )
        snapshot_id, _preview = self.prepare_preview(list_id)
        self.login(1)
        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}",
                data={
                    "subject": "Forged subject",
                    "body": "Forged body",
                    "email_address": "forged@example.com",
                },
            )
        self.assertEqual(response.status_code, 302)
        recipient, subject, body = send_email.call_args.args
        self.assertEqual(recipient, "contact@example.com")
        self.assertEqual(subject, "Grocery List")
        self.assertNotIn(private_client_name, subject)
        self.assertIn("Original Item", body)
        self.assertIn("Original Stock", body)
        self.assertIn("Original Need", body)
        self.assertIn("Purchased", body)
        self.assertNotIn(private_client_name, body)
        html_body = send_email.call_args.kwargs["html_body"]
        self.assertIn("Original Item", html_body)
        self.assertIn("Original Stock", html_body)
        self.assertIn("Original Need", html_body)
        self.assertIn("Purchased", html_body)
        self.assertNotIn(private_client_name, html_body)
        self.assertNotIn("Forged", subject + body + recipient)
        self.assertNotIn("Forged", html_body)

    def test_current_edits_after_preview_do_not_change_sent_body(self):
        list_id = self.create_list()
        section_id = self.add_section(list_id)
        item_id = self.add_item(
            section_id,
            item_name="Original Item",
            stock_text="Original Stock",
            needed_text="Original Need",
        )
        snapshot_id, _preview = self.prepare_preview(list_id)
        conn = self.connect()
        conn.execute(
            "UPDATE grocery_list_items SET item_name = ?, stock_text = ?, "
            "needed_text = ?, purchased = 1 WHERE item_id = ?",
            ("Changed Item", "Changed Stock", "Changed Need", item_id),
        )
        conn.commit()
        conn.close()
        self.login(1)
        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}"
            )
        self.assertEqual(response.status_code, 302)
        body = send_email.call_args.args[2]
        html_body = send_email.call_args.kwargs["html_body"]
        self.assertIn("Original Item", body)
        self.assertIn("Original Stock", body)
        self.assertIn("Original Need", body)
        self.assertNotIn("Changed Item", body)
        self.assertNotIn("Purchased", body)
        self.assertIn("Original Item", html_body)
        self.assertIn("Original Stock", html_body)
        self.assertIn("Original Need", html_body)
        self.assertNotIn("Changed Item", html_body)
        self.assertNotIn(">Purchased</td>", html_body)
        self.assertIn(">Not Purchased</td>", html_body)

    def test_all_current_recipients_are_used_after_recipient_changes(self):
        list_id = self.create_list()
        first_id = self.add_recipient(list_id, "first@example.com", "First")
        second_id = self.add_recipient(list_id, "second@example.com", "Second")
        snapshot_id, _preview = self.prepare_preview(
            list_id,
            add_default=False,
        )
        self.delete_recipient(first_id)
        self.add_recipient(list_id, "third@example.com", "Third")
        self.login(1)
        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}"
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(send_email.call_count, 2)
        self.assertEqual(
            {call.args[0] for call in send_email.call_args_list},
            {"second@example.com", "third@example.com"},
        )
        self.assertNotEqual(second_id, first_id)

    def test_no_current_recipients_produces_friendly_failure_without_send(self):
        list_id = self.create_list()
        recipient_id = self.add_recipient(list_id)
        snapshot_id, _preview = self.prepare_preview(
            list_id,
            add_default=False,
        )
        self.delete_recipient(recipient_id)
        self.login(1)
        before = self.snapshot_count(list_id)
        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}",
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Add at least one email recipient", response.data)
        send_email.assert_not_called()
        self.assertEqual(self.snapshot_count(list_id), before)

    def test_success_redirects_and_does_not_create_second_snapshot(self):
        list_id = self.create_list()
        snapshot_id, _preview = self.prepare_preview(list_id)
        self.login(1)
        before = self.snapshot_count(list_id)
        with patch("app.mail_service.send_email"):
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}",
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Grocery List emailed successfully.", response.data)
        self.assertEqual(self.snapshot_count(list_id), before)

    def test_failed_send_preserves_snapshot_and_returns_safe_feedback(self):
        list_id = self.create_list()
        snapshot_id, _preview = self.prepare_preview(list_id)
        self.login(1)
        before = self.snapshot_count(list_id)
        with self.assertLogs(app.app.logger, level="ERROR") as logs:
            with patch(
                "app.mail_service.send_email",
                side_effect=RuntimeError(
                    "secret SMTP credentials must not leak"
                ),
            ) as send_email:
                response = self.client.post(
                    f"/client/10/grocery-list/email-send/{snapshot_id}",
                    follow_redirects=True,
                )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"could not be sent", response.data)
        self.assertNotIn(b"secret SMTP credentials", response.data)
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(self.snapshot_count(list_id), before)
        log_output = "\n".join(logs.output)
        self.assertIn("Grocery List email delivery failed", log_output)
        self.assertNotIn("secret SMTP credentials", log_output)
        self.assertNotIn("RuntimeError", log_output)
        self.assertNotIn("Traceback", log_output)

    def test_partial_failure_continues_and_logs_each_result_without_body(self):
        list_id = self.create_list()
        self.add_recipient(list_id, "first@example.com", "First")
        self.add_recipient(list_id, "second@example.com", "Second")
        snapshot_id, _preview = self.prepare_preview(
            list_id,
            add_default=False,
        )

        def send_side_effect(recipient, subject, body, html_body=None):
            if recipient == "first@example.com":
                raise RuntimeError("SMTP failure")

        self.login(1)
        with patch(
            "app.mail_service.send_email",
            side_effect=send_side_effect,
        ) as send_email:
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}",
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"emailed to 1 recipient", response.data)
        self.assertIn(b"failed for 1 recipient", response.data)
        self.assertEqual(send_email.call_count, 2)
        rows = self.activity_rows()
        self.assertEqual(
            [row["activity_type"] for row in rows],
            ["grocery_list_email_failed", "grocery_list_email_sent"],
        )
        self.assertEqual([row["success"] for row in rows], [0, 1])
        for row in rows:
            self.assertNotIn("Original Item", row["details"] or "")

    def test_repeat_send_of_same_snapshot_is_allowed_without_new_snapshot(self):
        list_id = self.create_list()
        snapshot_id, _preview = self.prepare_preview(list_id)
        self.login(1)
        with patch("app.mail_service.send_email") as send_email:
            first = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}"
            )
            second = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}"
            )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(send_email.call_count, 2)
        self.assertEqual(self.snapshot_count(list_id), 1)

    def test_sending_does_not_require_shift_assignment_or_schedule(self):
        list_id = self.create_list()
        snapshot_id, _preview = self.prepare_preview(list_id)
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
        with patch("app.mail_service.send_email") as send_email:
            response = self.client.post(
                f"/client/10/grocery-list/email-send/{snapshot_id}"
            )
        self.assertEqual(response.status_code, 302)
        send_email.assert_called_once()

    def test_inactive_and_missing_clients_are_rejected(self):
        self.login(1)
        for client_id in (30, 999):
            with self.subTest(client_id=client_id):
                with patch("app.mail_service.send_email") as send_email:
                    response = self.client.post(
                        f"/client/{client_id}/grocery-list/email-send/1"
                    )
                self.assertEqual(response.status_code, 404)
                send_email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
