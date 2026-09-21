import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app


class GroceryListWeeklyHistoryTests(unittest.TestCase):
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
                (6, 'Inactive Worker', 'Support Worker', 0),
                (7, 'Edit Worker', 'Support Worker', 1);
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
            session["full_name"] = "Session Display Name"

    def create_list(self, client_id=10, title="Grocery List"):
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

    def add_section(self, list_id, name="Pantry", display_order=0):
        conn = self.connect()
        section_id = conn.execute(
            "INSERT INTO grocery_list_sections "
            "(grocery_list_id, name, display_order) VALUES (?, ?, ?)",
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
            VALUES (?, ?, ?, ?, ?, ?, '2026-09-20T01:00:00Z',
                    '2026-09-20T01:00:00Z', 1)
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

    def share(self, list_id, user_id, permission):
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

    def snapshot_count(self, list_id, kind="WEEKLY"):
        conn = self.connect()
        count = conn.execute(
            "SELECT COUNT(*) FROM grocery_list_snapshots "
            "WHERE grocery_list_id = ? AND snapshot_kind = ?",
            (list_id, kind),
        ).fetchone()[0]
        conn.close()
        return count

    def weekly_rows(self, list_id):
        conn = self.connect()
        rows = conn.execute(
            "SELECT snapshot_id, week_start FROM grocery_list_snapshots "
            "WHERE grocery_list_id = ? AND snapshot_kind = 'WEEKLY' "
            "ORDER BY week_start DESC, snapshot_id DESC",
            (list_id,),
        ).fetchall()
        conn.close()
        return rows

    def test_first_content_mutation_snapshots_baseline_once_per_week(self):
        list_id = self.create_list()
        section_id = self.add_section(list_id)
        item_id = self.add_item(section_id)
        self.login(1, session_role="Support Worker")
        week_one = datetime(2026, 9, 16, 18, tzinfo=timezone.utc)
        week_two = datetime(2026, 9, 23, 18, tzinfo=timezone.utc)

        with patch("app.get_application_now_utc", return_value=week_one):
            response = self.client.post(
                "/client/10/grocery-list/sections/new",
                data={"name": "Frozen"},
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.snapshot_count(list_id), 1)

            snapshot = self.weekly_rows(list_id)[0]
            self.assertEqual(snapshot["week_start"], "2026-09-14")
            conn = self.connect()
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM grocery_list_sections "
                    "WHERE grocery_list_id = ? AND name = 'Pantry'",
                    (list_id,),
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM grocery_list_snapshot_sections "
                    "WHERE snapshot_id = ? AND name = 'Frozen'",
                    (snapshot["snapshot_id"],),
                ).fetchone()
            )
            conn.close()

            response = self.client.post(
                f"/client/10/grocery-list/sections/{section_id}/items/new",
                data={"item_name": "Bread"},
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.snapshot_count(list_id), 1)

        with patch("app.get_application_now_utc", return_value=week_two):
            response = self.client.post(
                f"/client/10/grocery-list/items/{item_id}/purchased",
                data={"purchased": "1"},
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot_count(list_id), 2)
        self.assertEqual(
            [row["week_start"] for row in self.weekly_rows(list_id)],
            ["2026-09-21", "2026-09-14"],
        )

    def test_item_edit_purchased_delete_and_section_delete_snapshot_before_change(self):
        list_id = self.create_list()
        section_id = self.add_section(list_id)
        delete_section_id = self.add_section(
            list_id,
            name="Delete Section",
            display_order=1,
        )
        item_id = self.add_item(
            section_id,
            item_name="Original",
            stock_text="Stock",
            needed_text="Needed",
        )
        self.add_item(delete_section_id, item_name="Section Item")
        self.login(1)
        now = datetime(2026, 9, 16, 18, tzinfo=timezone.utc)

        with patch("app.get_application_now_utc", return_value=now):
            response = self.client.post(
                f"/client/10/grocery-list/items/{item_id}/edit",
                data={
                    "item_name": "Edited",
                    "stock_text": "Changed stock",
                    "needed_text": "Changed needed",
                },
            )
            self.assertEqual(response.status_code, 302)
        conn = self.connect()
        snapshot_item = conn.execute(
            "SELECT item_name, stock_text, needed_text, purchased "
            "FROM grocery_list_snapshot_items"
        ).fetchone()
        self.assertEqual(
            tuple(snapshot_item),
            ("Original", "Stock", "Needed", 0),
        )
        conn.close()

        list_id = self.create_list(client_id=20, title="Second List")
        section_id = self.add_section(list_id, name="Second Section")
        item_id = self.add_item(section_id, item_name="Delete Me")
        self.login(1)
        with patch("app.get_application_now_utc", return_value=now):
            response = self.client.post(
                f"/client/20/grocery-list/items/{item_id}/purchased",
                data={"purchased": "1"},
            )
            self.assertEqual(response.status_code, 302)
            response = self.client.post(
                f"/client/20/grocery-list/items/{item_id}/delete",
                data={"confirm": "yes"},
            )
            self.assertEqual(response.status_code, 302)
        conn = self.connect()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_items "
                "WHERE item_name = 'Delete Me'"
            ).fetchone()[0],
            1,
        )
        conn.close()

        with patch("app.get_application_now_utc", return_value=now):
            response = self.client.post(
                f"/client/10/grocery-list/sections/{delete_section_id}/delete",
                data={"confirm": "yes"},
            )
            self.assertEqual(response.status_code, 302)
        conn = self.connect()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_sections "
                "WHERE name = 'Delete Section'"
            ).fetchone()[0],
            1,
        )
        conn.close()

    def test_vancouver_week_boundary_and_empty_sources(self):
        list_id = self.create_list()
        monday_before_midnight_utc = datetime(
            2026, 9, 21, 6, 59, tzinfo=timezone.utc
        )
        monday_after_midnight_vancouver = datetime(
            2026, 9, 21, 7, 1, tzinfo=timezone.utc
        )
        first_conn = self.connect()
        first = app.ensure_grocery_list_weekly_snapshot(
            first_conn,
            list_id,
            1,
            now_utc=monday_before_midnight_utc,
        )
        first_conn.close()
        conn = self.connect()
        self.assertEqual(
            conn.execute(
                "SELECT week_start FROM grocery_list_snapshots "
                "WHERE snapshot_id = ?",
                (first,),
            ).fetchone()[0],
            "2026-09-14",
        )
        conn.close()
        second_conn = self.connect()
        second = app.ensure_grocery_list_weekly_snapshot(
            second_conn,
            list_id,
            1,
            now_utc=monday_after_midnight_vancouver,
        )
        second_conn.close()
        self.assertNotEqual(first, second)

        empty_list = self.create_list(client_id=20, title="No Sections")
        empty_section = self.add_section(empty_list, name="No Items")
        conn = self.connect()
        empty_snapshot = app.create_grocery_list_snapshot(
            conn,
            empty_list,
            "WEEKLY",
            1,
            week_start="2026-09-28",
        )
        conn.close()
        conn = self.connect()
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_sections "
                "WHERE snapshot_id = ?",
                (empty_snapshot,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_items AS i "
                "JOIN grocery_list_snapshot_sections AS s "
                "ON s.snapshot_section_id = i.snapshot_section_id "
                "WHERE s.snapshot_id = ?",
                (empty_snapshot,),
            ).fetchone()[0],
            0,
        )
        conn.close()
        self.assertIsNotNone(empty_section)

    def test_failed_content_mutation_rolls_back_new_weekly_snapshot(self):
        list_id = self.create_list()
        self.add_section(list_id, name="Existing")
        self.login(1)
        with patch(
            "app.get_application_now_utc",
            return_value=datetime(2026, 9, 16, 18, tzinfo=timezone.utc),
        ):
            response = self.client.post(
                "/client/10/grocery-list/sections/new",
                data={"name": "Existing"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot_count(list_id), 0)

    def test_views_and_share_changes_do_not_create_history(self):
        list_id = self.create_list()
        self.share(list_id, 5, "VIEW")
        self.login(5)
        self.assertEqual(self.client.get("/client/10/grocery-list").status_code, 200)
        self.assertEqual(
            self.client.get("/client/10/grocery-list/history").status_code,
            200,
        )
        self.assertEqual(self.snapshot_count(list_id), 0)

        self.login(1)
        response = self.client.post(
            "/client/10/grocery-list/shares/new",
            data={"user_id": 7, "permission": "EDIT"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.snapshot_count(list_id), 0)

    def test_history_access_and_cross_client_isolation(self):
        first_list = self.create_list()
        second_list = self.create_list(client_id=20, title="Second List")
        conn = self.connect()
        first_snapshot = app.create_grocery_list_snapshot(
            conn, first_list, "WEEKLY", 1, week_start="2026-09-14"
        )
        second_snapshot = app.create_grocery_list_snapshot(
            conn, second_list, "WEEKLY", 1, week_start="2026-09-14"
        )
        conn.close()

        for user_id in (1, 2, 3):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Support Worker")
                self.assertEqual(
                    self.client.get("/client/10/grocery-list/history").status_code,
                    200,
                )

        self.share(first_list, 5, "VIEW")
        self.share(second_list, 7, "EDIT")
        self.login(5)
        self.assertEqual(
            self.client.get("/client/10/grocery-list/history").status_code,
            200,
        )
        self.assertIn(
            self.client.get(
                f"/client/10/grocery-list/history/{second_snapshot}"
            ).status_code,
            (403, 404),
        )
        self.login(7)
        self.assertEqual(
            self.client.get("/client/20/grocery-list/history").status_code,
            200,
        )
        self.login(5)
        self.assertEqual(
            self.client.get("/client/20/grocery-list/history").status_code,
            403,
        )

        self.login(4)
        self.share(first_list, 4, "VIEW")
        self.assertEqual(
            self.client.get("/client/10/grocery-list/history").status_code,
            403,
        )
        self.login(6)
        self.share(first_list, 6, "VIEW")
        self.assertEqual(
            self.client.get("/client/10/grocery-list/history").status_code,
            403,
        )
        self.assertNotEqual(first_snapshot, second_snapshot)

    def test_history_listing_order_email_exclusion_and_empty_state(self):
        list_id = self.create_list()
        conn = self.connect()
        app.create_grocery_list_snapshot(
            conn, list_id, "WEEKLY", 1, week_start="2026-09-07"
        )
        app.create_grocery_list_snapshot(
            conn, list_id, "WEEKLY", 1, week_start="2026-09-14"
        )
        app.create_grocery_list_snapshot(
            conn, list_id, "EMAIL", 1
        )
        empty_list = self.create_list(client_id=20, title="Empty History")
        conn.close()
        self.login(1)
        response = self.client.get("/client/10/grocery-list/history")
        self.assertEqual(response.status_code, 200)
        self.assertLess(
            response.data.index(b"September 14, 2026"),
            response.data.index(b"September 7, 2026"),
        )
        self.assertNotIn(b"EMAIL", response.data)
        self.assertEqual(
            self.client.get(
                "/client/20/grocery-list/history"
            ).status_code,
            200,
        )
        response = self.client.get("/client/20/grocery-list/history")
        self.assertIn(b"No weekly Grocery List history is available.", response.data)
        self.assertIsNotNone(empty_list)

    def test_history_detail_is_read_only_and_preserves_order_values(self):
        list_id = self.create_list()
        first_section = self.add_section(list_id, "Later", 2)
        second_section = self.add_section(list_id, "First", 0)
        self.add_item(second_section, "Z item", 1, "Stock Z", "Need Z", 1)
        self.add_item(second_section, "A item", 1, "Stock A", "Need A", 0)
        self.add_item(first_section, "Later item", 0, None, None, 0)
        conn = self.connect()
        snapshot_id = app.create_grocery_list_snapshot(
            conn, list_id, "WEEKLY", 1, week_start="2026-09-14"
        )
        conn.close()
        self.login(1)
        response = self.client.get(
            f"/client/10/grocery-list/history/{snapshot_id}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client A", response.data)
        self.assertIn(b"September 14, 2026", response.data)
        self.assertIn(b"Stock Z", response.data)
        self.assertIn(b"Need A", response.data)
        self.assertIn(b"Yes", response.data)
        self.assertNotIn(b"Add Item", response.data)
        self.assertNotIn(b"Mark Purchased", response.data)
        self.assertNotIn(b"Share Grocery List", response.data)
        self.assertEqual(
            self.client.post(
                f"/client/10/grocery-list/history/{snapshot_id}"
            ).status_code,
            405,
        )

        conn = self.connect()
        self.assertEqual(
            [row[0] for row in conn.execute(
                "SELECT name FROM grocery_list_snapshot_sections "
                "WHERE snapshot_id = ? ORDER BY display_order, snapshot_section_id",
                (snapshot_id,),
            )],
            ["First", "Later"],
        )
        conn.close()

    def test_current_edits_do_not_change_history_and_no_shift_tables_are_needed(self):
        list_id = self.create_list()
        section_id = self.add_section(list_id)
        item_id = self.add_item(section_id, "Historical", 0)
        conn = self.connect()
        snapshot_id = app.create_grocery_list_snapshot(
            conn, list_id, "WEEKLY", 1, week_start="2026-09-14"
        )
        conn.close()
        conn = self.connect()
        conn.execute(
            "UPDATE grocery_list_items SET item_name = 'Current Only' "
            "WHERE item_id = ?",
            (item_id,),
        )
        conn.execute(
            "UPDATE grocery_list_sections SET name = 'Current Section' "
            "WHERE section_id = ?",
            (section_id,),
        )
        conn.commit()
        self.assertEqual(
            conn.execute(
                "SELECT i.item_name, s.name "
                "FROM grocery_list_snapshot_items AS i "
                "JOIN grocery_list_snapshot_sections AS s "
                "ON s.snapshot_section_id = i.snapshot_section_id "
                "WHERE s.snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()[0],
            "Historical",
        )
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
