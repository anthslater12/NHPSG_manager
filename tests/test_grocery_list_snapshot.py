import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import app
import add_grocery_lists_tables as migration


class GroceryListSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "grocery-lists.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL
            );
            INSERT INTO users VALUES (1, 'manager'), (2, 'worker');
            INSERT INTO clients VALUES (10, 'Client A'), (20, 'Client B');
            """
        )
        self.conn.commit()
        migration.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def insert_list(self, client_id=10, title="Grocery List"):
        return self.conn.execute(
            """
            INSERT INTO grocery_lists
            (client_id, title, created_by_user_id,
             created_at_utc, updated_at_utc)
            VALUES (?, ?, 1, '2026-09-20T12:00:00Z',
                    '2026-09-20T12:00:00Z')
            """,
            (client_id, title),
        ).lastrowid

    def insert_section(self, list_id, name, display_order):
        return self.conn.execute(
            "INSERT INTO grocery_list_sections "
            "(grocery_list_id, name, display_order) VALUES (?, ?, ?)",
            (list_id, name, display_order),
        ).lastrowid

    def insert_item(
        self,
        section_id,
        item_name,
        display_order,
        stock_text="Low",
        needed_text="2 bags",
        purchased=0,
    ):
        return self.conn.execute(
            """
            INSERT INTO grocery_list_items
            (section_id, item_name, stock_text, needed_text, purchased,
             display_order, created_at_utc, updated_at_utc,
             updated_by_user_id)
            VALUES (?, ?, ?, ?, ?, ?, '2026-09-20T12:00:00Z',
                    '2026-09-20T12:00:00Z', 1)
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

    def insert_share(self, list_id):
        self.conn.execute(
            """
            INSERT INTO grocery_list_shares
            (grocery_list_id, user_id, permission,
             shared_by_user_id, shared_at_utc)
            VALUES (?, 2, 'VIEW', 1, '2026-09-20T12:00:00Z')
            """,
            (list_id,),
        )

    def test_populated_weekly_snapshot_copies_header_sections_and_items(self):
        list_id = self.insert_list()
        self.insert_share(list_id)
        later_section_id = self.insert_section(list_id, "Later", 2)
        first_section_id = self.insert_section(list_id, "First", 0)
        self.insert_item(first_section_id, "Z item", 1)
        self.insert_item(
            first_section_id,
            "A item",
            1,
            stock_text="1/3 tank",
            needed_text="Fruit?",
            purchased=1,
        )
        self.insert_item(later_section_id, "Later item", 0)
        self.conn.commit()

        captured_at = datetime(2026, 9, 20, 19, 16, 50, tzinfo=timezone.utc)
        with patch("app.get_application_now_utc", return_value=captured_at):
            snapshot_id = app.create_grocery_list_snapshot(
                self.conn,
                list_id,
                "WEEKLY",
                1,
                week_start="2026-09-14",
            )

        header = self.conn.execute(
            "SELECT grocery_list_id, snapshot_kind, week_start, "
            "captured_by_user_id, captured_at_utc "
            "FROM grocery_list_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        self.assertEqual(
            tuple(header),
            (
                list_id,
                "WEEKLY",
                "2026-09-14",
                1,
                "2026-09-20T19:16:50Z",
            ),
        )

        sections = self.conn.execute(
            "SELECT snapshot_section_id, name, display_order "
            "FROM grocery_list_snapshot_sections WHERE snapshot_id = ? "
            "ORDER BY display_order, snapshot_section_id",
            (snapshot_id,),
        ).fetchall()
        self.assertEqual(
            [(row["name"], row["display_order"]) for row in sections],
            [("First", 0), ("Later", 2)],
        )
        items = self.conn.execute(
            "SELECT item_name, stock_text, needed_text, purchased, display_order "
            "FROM grocery_list_snapshot_items WHERE snapshot_section_id = ? "
            "ORDER BY display_order, snapshot_item_id",
            (sections[0]["snapshot_section_id"],),
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in items],
            [
                ("Z item", "Low", "2 bags", 0, 1),
                ("A item", "1/3 tank", "Fruit?", 1, 1),
            ],
        )

    def test_email_snapshots_and_empty_sources_are_supported(self):
        list_id = self.insert_list()
        empty_section_id = self.insert_section(list_id, "Empty", 0)
        empty_list_id = self.insert_list(client_id=20, title="Empty List")
        self.conn.commit()

        email_id = app.create_grocery_list_snapshot(
            self.conn,
            list_id,
            "EMAIL",
            2,
        )
        another_email_id = app.create_grocery_list_snapshot(
            self.conn,
            list_id,
            "EMAIL",
            2,
        )
        empty_list_snapshot_id = app.create_grocery_list_snapshot(
            self.conn,
            empty_list_id,
            "WEEKLY",
            1,
            week_start="2026-09-14",
        )
        self.assertNotEqual(email_id, another_email_id)
        self.assertNotEqual(email_id, empty_list_snapshot_id)
        self.assertEqual(
            self.conn.execute(
                "SELECT week_start FROM grocery_list_snapshots "
                "WHERE snapshot_id = ?",
                (email_id,),
            ).fetchone()[0],
            None,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_sections "
                "WHERE snapshot_id = ?",
                (empty_list_snapshot_id,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_items AS i "
                "JOIN grocery_list_snapshot_sections AS s "
                "ON s.snapshot_section_id = i.snapshot_section_id "
                "WHERE s.snapshot_id = ?",
                (email_id,),
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(empty_section_id)

    def test_snapshot_parameter_source_user_and_duplicate_validation(self):
        list_id = self.insert_list()
        self.conn.commit()

        for arguments in (
            ("WEEKLY", 1, None),
            ("EMAIL", 1, "2026-09-14"),
            ("INVALID", 1, None),
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    app.create_grocery_list_snapshot(
                        self.conn,
                        list_id,
                        arguments[0],
                        arguments[1],
                        week_start=arguments[2],
                    )

        with self.assertRaises(ValueError):
            app.create_grocery_list_snapshot(
                self.conn,
                list_id,
                "WEEKLY",
                1,
                week_start="2026-09-14T00:00:00Z",
            )
        with self.assertRaises(ValueError):
            app.create_grocery_list_snapshot(
                self.conn,
                999,
                "EMAIL",
                1,
            )
        with self.assertRaises(ValueError):
            app.create_grocery_list_snapshot(
                self.conn,
                list_id,
                "EMAIL",
                999,
            )

        app.create_grocery_list_snapshot(
            self.conn,
            list_id,
            "WEEKLY",
            1,
            week_start="2026-09-14",
        )
        with self.assertRaises(ValueError):
            app.create_grocery_list_snapshot(
                self.conn,
                list_id,
                "WEEKLY",
                1,
                week_start="2026-09-14",
            )
        different_week_id = app.create_grocery_list_snapshot(
            self.conn,
            list_id,
            "WEEKLY",
            1,
            week_start="2026-09-21",
        )
        self.assertIsInstance(different_week_id, int)

    def test_snapshot_is_independent_from_current_edits_and_deletes(self):
        list_id = self.insert_list()
        section_id = self.insert_section(list_id, "Original", 0)
        item_id = self.insert_item(
            section_id,
            "Original item",
            0,
            stock_text=None,
            needed_text=None,
        )
        self.conn.commit()
        snapshot_id = app.create_grocery_list_snapshot(
            self.conn,
            list_id,
            "EMAIL",
            1,
        )

        self.conn.execute(
            "UPDATE grocery_list_sections SET name = 'Renamed' "
            "WHERE section_id = ?",
            (section_id,),
        )
        self.conn.execute(
            "UPDATE grocery_list_items SET item_name = 'Changed', purchased = 1 "
            "WHERE item_id = ?",
            (item_id,),
        )
        self.conn.execute(
            "DELETE FROM grocery_list_items WHERE item_id = ?",
            (item_id,),
        )
        self.conn.execute(
            "DELETE FROM grocery_list_sections WHERE section_id = ?",
            (section_id,),
        )
        self.conn.commit()

        snapshot_section = self.conn.execute(
            "SELECT snapshot_section_id, name FROM grocery_list_snapshot_sections "
            "WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        snapshot_item = self.conn.execute(
            "SELECT item_name, stock_text, needed_text, purchased "
            "FROM grocery_list_snapshot_items WHERE snapshot_section_id = ?",
            (snapshot_section["snapshot_section_id"],),
        ).fetchone()
        self.assertEqual(snapshot_section["name"], "Original")
        self.assertEqual(
            tuple(snapshot_item),
            ("Original item", None, None, 0),
        )

    def test_copy_failure_rolls_back_header_and_children(self):
        list_id = self.insert_list()
        section_id = self.insert_section(list_id, "Pantry", 0)
        self.insert_item(section_id, "Fail", 0)
        self.conn.commit()
        self.conn.execute(
            """
            CREATE TRIGGER fail_snapshot_copy
            BEFORE INSERT ON grocery_list_snapshot_items
            WHEN NEW.item_name = 'Fail'
            BEGIN
                SELECT RAISE(ABORT, 'forced snapshot copy failure');
            END
            """
        )
        self.conn.commit()

        with self.assertRaises(sqlite3.IntegrityError):
            app.create_grocery_list_snapshot(
                self.conn,
                list_id,
                "EMAIL",
                1,
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshots "
                "WHERE grocery_list_id = ?",
                (list_id,),
            ).fetchone()[0],
            0,
        )

    def test_caller_owned_transaction_is_not_committed(self):
        list_id = self.insert_list()
        self.conn.commit()
        self.conn.execute("BEGIN")
        self.conn.execute("INSERT INTO users VALUES (3, 'caller')")

        snapshot_id = app.create_grocery_list_snapshot(
            self.conn,
            list_id,
            "EMAIL",
            1,
        )
        self.assertTrue(self.conn.in_transaction)
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM grocery_list_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        )
        self.conn.rollback()
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM grocery_list_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM users WHERE user_id = 3"
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
