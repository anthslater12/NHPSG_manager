import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_grocery_lists_tables as migration
import app


class GroceryListsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "grocery-lists.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
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
        """)
        self.conn.commit()
        migration.migrate(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def list_values(self, client_id=10, created_by_user_id=1, **overrides):
        values = {
            "client_id": client_id,
            "title": "Grocery List",
            "created_by_user_id": created_by_user_id,
            "created_at_utc": "2026-09-20T12:00:00Z",
            "updated_at_utc": "2026-09-20T12:00:00Z",
        }
        values.update(overrides)
        return values

    def insert_list(self, **overrides):
        values = self.list_values(**overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        return self.conn.execute(
            f"INSERT INTO grocery_lists ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        ).lastrowid

    def insert_section(self, grocery_list_id, name="Pantry", display_order=0):
        return self.conn.execute(
            "INSERT INTO grocery_list_sections "
            "(grocery_list_id, name, display_order) VALUES (?, ?, ?)",
            (grocery_list_id, name, display_order),
        ).lastrowid

    def insert_item(
        self,
        section_id,
        item_name="Rice",
        stock_text="Low",
        needed_text="2 loaves",
        purchased=0,
        display_order=0,
        updated_by_user_id=1,
    ):
        return self.conn.execute(
            "INSERT INTO grocery_list_items "
            "(section_id, item_name, stock_text, needed_text, purchased, "
            "display_order, created_at_utc, updated_at_utc, updated_by_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                section_id,
                item_name,
                stock_text,
                needed_text,
                purchased,
                display_order,
                "2026-09-20T12:00:00Z",
                "2026-09-20T12:00:00Z",
                updated_by_user_id,
            ),
        ).lastrowid

    def insert_share(
        self,
        grocery_list_id,
        user_id=2,
        permission="VIEW",
        shared_by_user_id=1,
        shared_at_utc="2026-09-20T12:00:00Z",
    ):
        return self.conn.execute(
            "INSERT INTO grocery_list_shares "
            "(grocery_list_id, user_id, permission, shared_by_user_id, "
            "shared_at_utc) VALUES (?, ?, ?, ?, ?)",
            (
                grocery_list_id,
                user_id,
                permission,
                shared_by_user_id,
                shared_at_utc,
            ),
        ).lastrowid

    def insert_email_recipient(
        self,
        grocery_list_id,
        display_name="Care Team",
        email_address="care@example.com",
        created_by_user_id=1,
        created_at_utc="2026-09-20T12:00:00Z",
        updated_by_user_id=1,
        updated_at_utc="2026-09-20T12:00:00Z",
    ):
        return self.conn.execute(
            "INSERT INTO grocery_list_email_recipients "
            "(grocery_list_id, display_name, email_address, "
            "created_by_user_id, created_at_utc, updated_by_user_id, "
            "updated_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?)",
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

    def assert_rejected(self, statement, parameters=()):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(statement, parameters)
        self.conn.rollback()

    def insert_snapshot(
        self,
        grocery_list_id,
        snapshot_kind="WEEKLY",
        week_start="2026-09-14",
        captured_by_user_id=1,
    ):
        return self.conn.execute(
            "INSERT INTO grocery_list_snapshots "
            "(grocery_list_id, snapshot_kind, week_start, "
            "captured_by_user_id, captured_at_utc) VALUES (?, ?, ?, ?, ?)",
            (
                grocery_list_id,
                snapshot_kind,
                week_start,
                captured_by_user_id,
                "2026-09-20T12:00:00Z",
            ),
        ).lastrowid

    def insert_snapshot_section(
        self, snapshot_id, name="Pantry", display_order=0
    ):
        return self.conn.execute(
            "INSERT INTO grocery_list_snapshot_sections "
            "(snapshot_id, name, display_order) VALUES (?, ?, ?)",
            (snapshot_id, name, display_order),
        ).lastrowid

    def insert_snapshot_item(
        self,
        snapshot_section_id,
        item_name="Rice",
        stock_text="Low",
        needed_text="2 bags",
        purchased=0,
        display_order=0,
    ):
        return self.conn.execute(
            "INSERT INTO grocery_list_snapshot_items "
            "(snapshot_section_id, item_name, stock_text, needed_text, "
            "purchased, display_order) VALUES (?, ?, ?, ?, ?, ?)",
            (
                snapshot_section_id,
                item_name,
                stock_text,
                needed_text,
                purchased,
                display_order,
            ),
        ).lastrowid

    def test_creates_all_tables_columns_and_indexes(self):
        self.assertEqual(
            {
                row[0]
                for row in self.conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name LIKE 'grocery_list%'"
                )
            },
            {
                "grocery_lists",
                "grocery_list_sections",
                "grocery_list_items",
                "grocery_list_shares",
                "grocery_list_email_recipients",
                "grocery_list_snapshots",
                "grocery_list_snapshot_sections",
                "grocery_list_snapshot_items",
            },
        )
        for table_name, expected_columns in migration.EXPECTED_COLUMNS.items():
            self.assertEqual(
                {row["name"] for row in self.conn.execute(
                    f'PRAGMA table_info("{table_name}")'
                )},
                expected_columns,
            )
        indexes = {
            row["name"]
            for table_name in migration.TABLE_SQL
            for row in self.conn.execute(f'PRAGMA index_list("{table_name}")')
        }
        self.assertTrue({name for name, _ in migration.INDEXES}.issubset(indexes))
        self.assertIn(
            "idx_grocery_list_snapshots_weekly_unique",
            indexes,
        )

    def test_migration_is_idempotent(self):
        before = {
            table_name: self.conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()[0]
            for table_name in migration.TABLE_SQL
        }
        self.assertFalse(migration.migrate(self.conn))
        after = {
            table_name: self.conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()[0]
            for table_name in migration.TABLE_SQL
        }
        self.assertEqual(after, before)

    def test_migration_preserves_a_caller_owned_transaction(self):
        path = Path(self.temp.name) / "transaction.db"
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES (1);
            INSERT INTO clients VALUES (10);
        """)
        conn.commit()
        try:
            conn.execute("BEGIN")
            conn.execute("INSERT INTO users VALUES (3)")

            migration.migrate(conn)

            self.assertTrue(conn.in_transaction)
            conn.rollback()
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'grocery_lists'"
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM users WHERE user_id = 3"
                ).fetchone()
            )
        finally:
            conn.close()

    def test_incompatible_partial_schema_is_rejected_without_partial_migration(self):
        path = Path(self.temp.name) / "partial.db"
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY);
            CREATE TABLE grocery_lists (
                grocery_list_id INTEGER PRIMARY KEY
            );
        """)
        conn.commit()
        try:
            with self.assertRaisesRegex(RuntimeError, "incompatible"):
                migration.migrate(conn)
            self.assertEqual(
                [row[1] for row in conn.execute(
                    "PRAGMA table_info(grocery_lists)"
                )],
                ["grocery_list_id"],
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'grocery_list_sections'"
                ).fetchone()
            )
        finally:
            conn.close()

    def test_one_list_per_client_and_valid_lists_for_other_clients(self):
        self.insert_list()
        self.assert_rejected(
            "INSERT INTO grocery_lists "
            "(client_id, title, created_by_user_id, created_at_utc, updated_at_utc) "
            "VALUES (10, 'Another', 1, '2026-09-20T12:00:00Z', '2026-09-20T12:00:00Z')"
        )
        self.insert_list(client_id=20)

    def test_foreign_keys_and_blank_title_are_enforced(self):
        for values in (
            {"client_id": 999},
            {"created_by_user_id": 999},
            {"title": "   "},
        ):
            with self.subTest(values=values):
                list_values = self.list_values(**values)
                columns = ", ".join(list_values)
                placeholders = ", ".join("?" for _ in list_values)
                self.assert_rejected(
                    f"INSERT INTO grocery_lists ({columns}) VALUES ({placeholders})",
                    tuple(list_values.values()),
                )

    def test_section_constraints_and_same_name_across_lists(self):
        first_list = self.insert_list()
        second_list = self.insert_list(client_id=20)
        first_section = self.insert_section(first_list)
        self.conn.commit()
        self.assert_rejected(
            "INSERT INTO grocery_list_sections "
            "(grocery_list_id, name, display_order) VALUES (?, ?, ?)",
            (first_list, "Pantry", 1),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_sections "
            "(grocery_list_id, name, display_order) VALUES (?, ?, ?)",
            (first_list, "   ", 1),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_sections "
            "(grocery_list_id, name, display_order) VALUES (?, ?, ?)",
            (first_list, "Frozen", -1),
        )
        second_section = self.insert_section(second_list)
        self.assertNotEqual(first_section, second_section)

    def test_item_constraints_and_free_text_are_enforced(self):
        list_id = self.insert_list()
        section_id = self.insert_section(list_id)
        self.insert_item(
            section_id,
            stock_text="1/3 tank",
            needed_text="Fruit?",
        )
        self.insert_item(section_id, stock_text="Low", needed_text="2 loaves")
        for values in (
            {"item_name": "   "},
            {"purchased": 2},
            {"display_order": -1},
            {"updated_by_user_id": 999},
        ):
            with self.subTest(values=values):
                columns = {
                    "section_id": section_id,
                    "item_name": "Valid",
                    "stock_text": "Stocked",
                    "needed_text": "1 bag",
                    "purchased": 0,
                    "display_order": 0,
                    "created_at_utc": "2026-09-20T12:00:00Z",
                    "updated_at_utc": "2026-09-20T12:00:00Z",
                    "updated_by_user_id": 1,
                }
                columns.update(values)
                names = ", ".join(columns)
                placeholders = ", ".join("?" for _ in columns)
                self.assert_rejected(
                    f"INSERT INTO grocery_list_items ({names}) "
                    f"VALUES ({placeholders})",
                    tuple(columns.values()),
                )

    def test_valid_shares_permissions_timestamp_and_structural_uniqueness(self):
        first_list = self.insert_list()
        second_list = self.insert_list(client_id=20)
        view_share = self.insert_share(first_list, user_id=2, permission="VIEW")
        edit_share = self.insert_share(
            second_list,
            user_id=2,
            permission="EDIT",
            shared_by_user_id=2,
        )
        self.conn.commit()
        self.assertNotEqual(view_share, edit_share)
        self.assertEqual(
            self.conn.execute(
                "SELECT shared_at_utc FROM grocery_list_shares "
                "WHERE share_id = ?",
                (view_share,),
            ).fetchone()[0],
            "2026-09-20T12:00:00Z",
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_shares "
            "(grocery_list_id, user_id, permission, shared_by_user_id, "
            "shared_at_utc) VALUES (?, ?, ?, ?, ?)",
            (first_list, 2, "VIEW", 1, "2026-09-20T12:00:00Z"),
        )
        self.insert_share(first_list, user_id=1, permission="EDIT")
        self.conn.commit()
        self.assert_rejected(
            "INSERT INTO grocery_list_shares "
            "(grocery_list_id, user_id, permission, shared_by_user_id, "
            "shared_at_utc) VALUES (?, ?, ?, ?, ?)",
            (first_list, 2, "INVALID", 1, "2026-09-20T12:00:00Z"),
        )

    def test_snapshot_kinds_week_start_and_weekly_uniqueness(self):
        list_id = self.insert_list()
        weekly_id = self.insert_snapshot(list_id)
        email_id = self.insert_snapshot(
            list_id,
            snapshot_kind="EMAIL",
            week_start=None,
        )
        another_week_id = self.insert_snapshot(
            list_id,
            week_start="2026-09-21",
        )
        another_email_id = self.insert_snapshot(
            list_id,
            snapshot_kind="EMAIL",
            week_start=None,
        )
        self.conn.commit()

        self.assertNotEqual(weekly_id, email_id)
        self.assertNotEqual(weekly_id, another_week_id)
        self.assertNotEqual(email_id, another_email_id)
        for values in (
            ("INVALID", "2026-09-14"),
            ("WEEKLY", None),
            ("EMAIL", "2026-09-14"),
            ("WEEKLY", "2026-09-14T00:00:00Z"),
        ):
            with self.subTest(values=values):
                self.assert_rejected(
                    "INSERT INTO grocery_list_snapshots "
                    "(grocery_list_id, snapshot_kind, week_start, "
                    "captured_by_user_id, captured_at_utc) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (list_id, values[0], values[1], "2026-09-20T12:00:00Z"),
                )

        self.assert_rejected(
            "INSERT INTO grocery_list_snapshots "
            "(grocery_list_id, snapshot_kind, week_start, "
            "captured_by_user_id, captured_at_utc) "
            "VALUES (?, 'WEEKLY', ?, 1, ?)",
            (list_id, "2026-09-14", "2026-09-20T12:00:00Z"),
        )

    def test_snapshot_foreign_keys_checks_and_free_text_are_enforced(self):
        list_id = self.insert_list()
        self.conn.commit()
        self.assert_rejected(
            "INSERT INTO grocery_list_snapshots "
            "(grocery_list_id, snapshot_kind, week_start, "
            "captured_by_user_id, captured_at_utc) "
            "VALUES (?, 'WEEKLY', '2026-09-14', ?, ?)",
            (999, 1, "2026-09-20T12:00:00Z"),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_snapshots "
            "(grocery_list_id, snapshot_kind, week_start, "
            "captured_by_user_id, captured_at_utc) "
            "VALUES (?, 'WEEKLY', '2026-09-14', ?, ?)",
            (list_id, 999, "2026-09-20T12:00:00Z"),
        )

        snapshot_id = self.insert_snapshot(list_id)
        self.conn.commit()
        self.assert_rejected(
            "INSERT INTO grocery_list_snapshot_sections "
            "(snapshot_id, name, display_order) VALUES (?, 'Pantry', 0)",
            (999,),
        )
        section_id = self.insert_snapshot_section(snapshot_id)
        self.conn.commit()
        self.assert_rejected(
            "INSERT INTO grocery_list_snapshot_items "
            "(snapshot_section_id, item_name, stock_text, needed_text, "
            "purchased, display_order) VALUES (?, 'Rice', 'Low', '2 bags', 0, 0)",
            (999,),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_snapshot_sections "
            "(snapshot_id, name, display_order) VALUES (?, ?, 0)",
            (snapshot_id, "   "),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_snapshot_sections "
            "(snapshot_id, name, display_order) VALUES (?, 'Frozen', -1)",
            (snapshot_id,),
        )
        for values in (
            ("   ", "Low", "2 bags", 0, 0),
            ("Rice", "Low", "2 bags", 2, 0),
            ("Rice", "Low", "2 bags", 0, -1),
        ):
            with self.subTest(values=values):
                self.assert_rejected(
                    "INSERT INTO grocery_list_snapshot_items "
                    "(snapshot_section_id, item_name, stock_text, needed_text, "
                    "purchased, display_order) VALUES (?, ?, ?, ?, ?, ?)",
                    (section_id,) + values,
                )

        item_id = self.insert_snapshot_item(
            section_id,
            stock_text="1/3 tank",
            needed_text="Fruit?",
        )
        self.conn.commit()
        self.assertEqual(
            tuple(self.conn.execute(
                "SELECT stock_text, needed_text FROM grocery_list_snapshot_items "
                "WHERE snapshot_item_id = ?",
                (item_id,),
            ).fetchone()),
            ("1/3 tank", "Fruit?"),
        )

    def test_snapshot_cascades_and_current_list_delete_is_restricted(self):
        list_id = self.insert_list()
        current_section_id = self.insert_section(list_id)
        self.insert_item(current_section_id)
        snapshot_id = self.insert_snapshot(list_id)
        snapshot_section_id = self.insert_snapshot_section(snapshot_id)
        self.insert_snapshot_item(snapshot_section_id)
        second_snapshot_section_id = self.insert_snapshot_section(
            snapshot_id,
            name="Freezer",
            display_order=1,
        )
        self.insert_snapshot_item(
            second_snapshot_section_id,
            item_name="Peas",
        )
        self.conn.commit()

        self.conn.execute(
            "DELETE FROM grocery_list_sections WHERE section_id = ?",
            (current_section_id,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_items "
                "WHERE snapshot_section_id = ?",
                (snapshot_section_id,),
            ).fetchone()[0],
            1,
        )

        self.conn.execute(
            "DELETE FROM grocery_list_snapshot_sections "
            "WHERE snapshot_section_id = ?",
            (snapshot_section_id,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_items "
                "WHERE snapshot_section_id = ?",
                (snapshot_section_id,),
            ).fetchone()[0],
            0,
        )
        self.conn.commit()
        self.assert_rejected(
            "DELETE FROM grocery_lists WHERE grocery_list_id = ?",
            (list_id,),
        )

        self.conn.execute(
            "DELETE FROM grocery_list_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_snapshot_sections "
                "WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()[0],
            0,
        )
        self.conn.commit()

        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_items "
                "WHERE section_id = ?",
                (current_section_id,),
            ).fetchone()[0],
            0,
        )

    def test_share_foreign_keys_and_user_delete_restrictions_are_enforced(self):
        list_id = self.insert_list()
        self.conn.commit()
        self.assert_rejected(
            "INSERT INTO grocery_list_shares "
            "(grocery_list_id, user_id, permission, shared_by_user_id, "
            "shared_at_utc) VALUES (?, ?, ?, ?, ?)",
            (999, 2, "VIEW", 1, "2026-09-20T12:00:00Z"),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_shares "
            "(grocery_list_id, user_id, permission, shared_by_user_id, "
            "shared_at_utc) VALUES (?, ?, ?, ?, ?)",
            (list_id, 999, "VIEW", 1, "2026-09-20T12:00:00Z"),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_shares "
            "(grocery_list_id, user_id, permission, shared_by_user_id, "
            "shared_at_utc) VALUES (?, ?, ?, ?, ?)",
            (list_id, 2, "VIEW", 999, "2026-09-20T12:00:00Z"),
        )
        self.insert_share(list_id, user_id=2, permission="VIEW")
        self.conn.commit()
        self.assert_rejected("DELETE FROM users WHERE user_id = 2")
        self.assert_rejected("DELETE FROM users WHERE user_id = 1")

    def test_email_recipient_constraints_duplicates_and_timestamps(self):
        first_list = self.insert_list()
        second_list = self.insert_list(client_id=20)
        first_recipient = self.insert_email_recipient(
            first_list,
            display_name=None,
            email_address="care@example.com",
            created_by_user_id=1,
            updated_by_user_id=2,
        )
        second_recipient = self.insert_email_recipient(
            first_list,
            display_name="Family",
            email_address="family@example.com",
        )
        other_list_recipient = self.insert_email_recipient(
            second_list,
            email_address="care@example.com",
        )
        self.conn.commit()

        self.assertNotEqual(first_recipient, second_recipient)
        self.assertNotEqual(first_recipient, other_list_recipient)
        row = self.conn.execute(
            "SELECT display_name, email_address, created_by_user_id, "
            "created_at_utc, updated_by_user_id, updated_at_utc "
            "FROM grocery_list_email_recipients WHERE recipient_id = ?",
            (first_recipient,),
        ).fetchone()
        self.assertEqual(
            tuple(row),
            (
                None,
                "care@example.com",
                1,
                "2026-09-20T12:00:00Z",
                2,
                "2026-09-20T12:00:00Z",
            ),
        )

        self.assert_rejected(
            "INSERT INTO grocery_list_email_recipients "
            "(grocery_list_id, email_address, created_by_user_id, "
            "created_at_utc, updated_by_user_id, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                first_list,
                "care@example.com",
                1,
                "2026-09-20T12:00:00Z",
                1,
                "2026-09-20T12:00:00Z",
            ),
        )
        for invalid_email in (None, "", "   ", " care@example.com"):
            self.assert_rejected(
                "INSERT INTO grocery_list_email_recipients "
                "(grocery_list_id, email_address, created_by_user_id, "
                "created_at_utc, updated_by_user_id, updated_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    first_list,
                    invalid_email,
                    1,
                    "2026-09-20T12:00:00Z",
                    1,
                    "2026-09-20T12:00:00Z",
                ),
            )
        self.assert_rejected(
            "INSERT INTO grocery_list_email_recipients "
            "(grocery_list_id, email_address, created_by_user_id, "
            "created_at_utc, updated_by_user_id, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                first_list,
                "a" * 251 + "@x.com",
                1,
                "2026-09-20T12:00:00Z",
                1,
                "2026-09-20T12:00:00Z",
            ),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_email_recipients "
            "(grocery_list_id, email_address, created_by_user_id, "
            "created_at_utc, updated_by_user_id, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                999,
                "missing-list@example.com",
                1,
                "2026-09-20T12:00:00Z",
                1,
                "2026-09-20T12:00:00Z",
            ),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_email_recipients "
            "(grocery_list_id, email_address, created_by_user_id, "
            "created_at_utc, updated_by_user_id, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                first_list,
                "missing-creator@example.com",
                999,
                "2026-09-20T12:00:00Z",
                1,
                "2026-09-20T12:00:00Z",
            ),
        )
        self.assert_rejected(
            "INSERT INTO grocery_list_email_recipients "
            "(grocery_list_id, email_address, created_by_user_id, "
            "created_at_utc, updated_by_user_id, updated_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                first_list,
                "missing-updater@example.com",
                1,
                "2026-09-20T12:00:00Z",
                999,
                "2026-09-20T12:00:00Z",
            ),
        )

    def test_email_recipient_cascade_user_restrictions_and_data_preservation(
        self,
    ):
        list_id = self.insert_list()
        section_id = self.insert_section(list_id)
        item_id = self.insert_item(section_id)
        share_id = self.insert_share(list_id)
        snapshot_id = self.insert_snapshot(list_id)
        snapshot_section_id = self.insert_snapshot_section(snapshot_id)
        snapshot_item_id = self.insert_snapshot_item(snapshot_section_id)
        self.conn.execute("INSERT INTO users VALUES (3, 'creator')")
        self.conn.execute("INSERT INTO users VALUES (4, 'updater')")
        recipient_id = self.insert_email_recipient(
            list_id,
            email_address="preserved@example.com",
            created_by_user_id=3,
            updated_by_user_id=4,
        )
        self.conn.commit()

        self.assertFalse(migration.migrate(self.conn))
        for table_name, key_name, key_value in (
            ("grocery_lists", "grocery_list_id", list_id),
            ("grocery_list_sections", "section_id", section_id),
            ("grocery_list_items", "item_id", item_id),
            ("grocery_list_shares", "share_id", share_id),
            ("grocery_list_snapshots", "snapshot_id", snapshot_id),
            (
                "grocery_list_snapshot_sections",
                "snapshot_section_id",
                snapshot_section_id,
            ),
            (
                "grocery_list_snapshot_items",
                "snapshot_item_id",
                snapshot_item_id,
            ),
            (
                "grocery_list_email_recipients",
                "recipient_id",
                recipient_id,
            ),
        ):
            self.assertIsNotNone(
                self.conn.execute(
                    f"SELECT 1 FROM {table_name} WHERE {key_name} = ?",
                    (key_value,),
                ).fetchone()
            )

        self.assert_rejected("DELETE FROM users WHERE user_id = 3")
        self.assert_rejected("DELETE FROM users WHERE user_id = 4")

        other_list = self.insert_list(client_id=20)
        other_recipient = self.insert_email_recipient(
            other_list,
            email_address="cascade@example.com",
        )
        self.conn.commit()
        self.conn.execute(
            "DELETE FROM grocery_lists WHERE grocery_list_id = ?",
            (other_list,),
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM grocery_list_email_recipients "
                "WHERE recipient_id = ?",
                (other_recipient,),
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM grocery_list_email_recipients "
                "WHERE recipient_id = ?",
                (recipient_id,),
            ).fetchone()
        )

    def test_deleting_a_grocery_list_cascades_share_rows(self):
        list_id = self.insert_list()
        self.insert_share(list_id, user_id=2, permission="VIEW")
        self.conn.execute(
            "DELETE FROM grocery_lists WHERE grocery_list_id = ?",
            (list_id,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_shares "
                "WHERE grocery_list_id = ?",
                (list_id,),
            ).fetchone()[0],
            0,
        )

    def test_migration_preserves_existing_current_schema_data(self):
        list_id = self.insert_list()
        section_id = self.insert_section(list_id)
        item_id = self.insert_item(section_id)
        share_id = self.insert_share(list_id)
        self.conn.commit()

        self.assertFalse(migration.migrate(self.conn))
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM grocery_lists WHERE grocery_list_id = ?",
                (list_id,),
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM grocery_list_sections WHERE section_id = ?",
                (section_id,),
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM grocery_list_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        )
        self.assertIsNotNone(
            self.conn.execute(
                "SELECT 1 FROM grocery_list_shares WHERE share_id = ?",
                (share_id,),
            ).fetchone()
        )

    def test_section_and_list_deletes_cascade_to_current_children(self):
        list_id = self.insert_list()
        section_id = self.insert_section(list_id)
        self.insert_item(section_id)
        self.conn.execute(
            "DELETE FROM grocery_list_sections WHERE section_id = ?",
            (section_id,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_items WHERE section_id = ?",
                (section_id,),
            ).fetchone()[0],
            0,
        )

        second_list = self.insert_list(client_id=20)
        second_section = self.insert_section(second_list)
        self.insert_item(second_section)
        self.conn.execute(
            "DELETE FROM grocery_lists WHERE grocery_list_id = ?",
            (second_list,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_sections "
                "WHERE grocery_list_id = ?",
                (second_list,),
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM grocery_list_items "
                "WHERE section_id = ?",
                (second_section,),
            ).fetchone()[0],
            0,
        )

    def test_referenced_client_and_provenance_users_cannot_be_deleted(self):
        list_id = self.insert_list()
        section_id = self.insert_section(list_id)
        self.insert_item(section_id, updated_by_user_id=2)
        self.conn.commit()
        self.assert_rejected("DELETE FROM clients WHERE client_id = 10")
        self.assert_rejected("DELETE FROM users WHERE user_id = 1")
        self.assert_rejected("DELETE FROM users WHERE user_id = 2")

    def test_application_startup_applies_migration_and_repeats_safely(self):
        startup_path = Path(self.temp.name) / "startup.db"
        conn = sqlite3.connect(startup_path)
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY);
            INSERT INTO users VALUES (1);
            INSERT INTO clients VALUES (10);
        """)
        conn.commit()
        conn.close()

        old_db_name = app.DB_NAME
        app.DB_NAME = str(startup_path)
        try:
            connected = app.get_db()
            self.assertIsNotNone(
                connected.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'grocery_lists'"
                ).fetchone()
            )
            self.assertEqual(
                connected.execute("PRAGMA foreign_keys").fetchone()[0],
                1,
            )
            connected.close()

            connected = app.get_db()
            self.assertIsNotNone(
                connected.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'grocery_list_items'"
                ).fetchone()
            )
            self.assertIsNotNone(
                connected.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'grocery_list_shares'"
                ).fetchone()
            )
            for table_name in (
                "grocery_list_email_recipients",
                "grocery_list_snapshots",
                "grocery_list_snapshot_sections",
                "grocery_list_snapshot_items",
            ):
                self.assertIsNotNone(
                    connected.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = ?",
                        (table_name,),
                    ).fetchone()
                )
            connected.close()
        finally:
            app.DB_NAME = old_db_name


if __name__ == "__main__":
    unittest.main()
