import os
import sqlite3
import sys
import tempfile
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import add_food_fluid_entries_table as migration
import add_staff_notices_tables as staff_notice_schema
import app


class FoodFluidCheckpointThreeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(
            self.temporary_directory.name,
            "food_fluid_checkpoint_three.db"
        )
        self.original_database = app.DB_NAME
        app.DB_NAME = self.database_path
        app.app.config.update(TESTING=True)

        conn = sqlite3.connect(self.database_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                scheduled_start_time TEXT,
                scheduled_end_time TEXT,
                created_at TEXT,
                closed_at TEXT
            );

            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT NOT NULL,
                actual_end_time TEXT,
                sign_on_at TEXT,
                sign_off_at TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                start_checklist_completed INTEGER DEFAULT 0,
                end_checklist_completed INTEGER DEFAULT 0
            );

            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT,
                activity_class TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                user_id INTEGER,
                client_id INTEGER,
                shift_id INTEGER,
                related_table TEXT,
                related_id INTEGER,
                summary TEXT NOT NULL,
                details TEXT,
                created_at TEXT,
                success INTEGER DEFAULT 1
            );

            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT,
                comment TEXT,
                acknowledgement_type TEXT,
                active INTEGER DEFAULT 1,
                UNIQUE(source_table, source_id, user_id)
            );

            CREATE TABLE shift_notes (
                note_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                note_text TEXT NOT NULL,
                follow_up_required INTEGER NOT NULL DEFAULT 0,
                created_at TEXT
            );

            CREATE TABLE care_tasks (
                care_task_id INTEGER PRIMARY KEY,
                task_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                occurs TEXT
            );

            CREATE TABLE shift_care_task_entries (
                entry_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                care_task_id INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                completed_by_user_id INTEGER NOT NULL
            );

            CREATE TABLE housekeeping_tasks (
                housekeeping_task_id INTEGER PRIMARY KEY,
                task_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                occurs TEXT
            );

            CREATE TABLE shift_housekeeping_task_entries (
                entry_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                housekeeping_task_id INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                completed_by_user_id INTEGER NOT NULL
            );

            INSERT INTO users
                (user_id, username, password_hash, full_name, role, active)
            VALUES
                (1, 'worker', 'x', 'Worker One', 'Support Worker', 1),
                (2, 'manager', 'x', 'Manager One', 'Program Manager', 1),
                (3, 'inactive', 'x', 'Inactive Worker', 'Support Worker', 0),
                (4, 'consultant', 'x', 'Consultant', 'Behaviour Consultant', 1),
                (5, 'no_shift', 'x', 'No Shift Worker', 'Support Worker', 1),
                (6, 'signed_off', 'x', 'Signed Off Worker', 'Support Worker', 1);

            INSERT INTO clients
                (client_id, client_name, active)
            VALUES
                (1, 'Active Client', 1),
                (2, 'Inactive Client', 0),
                (3, 'Unrelated Client', 1);

            INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status)
            VALUES
                (10, 1, '2024-01-15', 'Day', 'Open'),
                (11, 1, '2024-01-15', 'Overnight', 'Open'),
                (12, 1, '2024-01-15', 'Day', 'Closed'),
                (13, 2, '2024-01-15', 'Day', 'Open'),
                (14, 3, '2024-01-15', 'Afternoon', 'Open');

            INSERT INTO shift_staff
                (shift_staff_id, shift_id, user_id, actual_start_time,
                 sign_on_at, active)
            VALUES
                (100, 10, 1, '07:00', '2024-01-15 15:00:00', 1),
                (101, 11, 1, '23:00', '2024-01-16 07:00:00', 1),
                (102, 12, 1, '07:00', '2024-01-15 15:00:00', 1),
                (103, 13, 1, '07:00', '2024-01-15 15:00:00', 1),
                (104, 10, 2, '07:00', '2024-01-15 15:00:00', 1),
                (105, 10, 3, '07:00', '2024-01-15 15:00:00', 1),
                (106, 10, 4, '07:00', '2024-01-15 15:00:00', 1),
                (107, 10, 6, '07:00', '2024-01-15 15:00:00', 0);
        """)
        migration.migrate(conn)
        for sql in staff_notice_schema.TABLE_SQL.values():
            conn.execute(sql)
        for sql in staff_notice_schema.INDEX_SQL.values():
            conn.execute(sql)
        conn.close()

        self.client = app.app.test_client()
        self.token_number = 0

    def tearDown(self):
        app.DB_NAME = self.original_database
        self.temporary_directory.cleanup()

    def login(self, user_id, session_role="Untrusted Session Role"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = session_role
            session["full_name"] = "Untrusted Session Name"

    def insert_entry(
        self,
        *,
        shift_id=10,
        client_id=1,
        event_at_utc="2024-01-15T17:00:00Z",
        item_description="Toast",
        additional_details=None,
        status="Recorded",
        void_reason=None,
        voided_by_user_id=None,
        voided_at_utc=None,
        submitted_at_utc="2024-01-15T17:05:00Z",
        interaction_type="Offered",
        outcome="All consumed",
        physically_thrown=0
    ):
        self.token_number += 1
        conn = sqlite3.connect(self.database_path)
        cursor = conn.execute("""
            INSERT INTO food_fluid_entries
            (
                shift_id,
                client_id,
                recorded_by_user_id,
                event_at_utc,
                interaction_type,
                item_description,
                outcome,
                physically_thrown,
                additional_details,
                submitted_at_utc,
                submission_token,
                status,
                voided_by_user_id,
                voided_at_utc,
                void_reason
            )
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            shift_id,
            client_id,
            event_at_utc,
            interaction_type,
            item_description,
            outcome,
            physically_thrown,
            additional_details,
            submitted_at_utc,
            f"checkpoint-three-token-{self.token_number}",
            status,
            voided_by_user_id,
            voided_at_utc,
            void_reason,
        ))
        conn.commit()
        entry_id = cursor.lastrowid
        conn.close()
        return entry_id

    def database_counts(self):
        conn = sqlite3.connect(self.database_path)
        counts = tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "food_fluid_entries",
                "activity_log",
                "acknowledgements",
            )
        )
        conn.close()
        return counts

    def populate_ordered_entries(self):
        self.insert_entry(
            event_at_utc="2024-01-15T15:00:00Z",
            item_description="oldest"
        )
        self.insert_entry(
            event_at_utc="2024-01-15T16:00:00Z",
            item_description="second-oldest"
        )
        self.insert_entry(
            event_at_utc="2024-01-15T17:00:00Z",
            item_description="middle-one"
        )
        self.insert_entry(
            event_at_utc="2024-01-15T18:00:00Z",
            item_description="middle-two"
        )
        self.insert_entry(
            event_at_utc="2024-01-15T19:00:00Z",
            item_description="middle-three"
        )
        self.insert_entry(
            event_at_utc="2024-01-15T20:00:00Z",
            item_description="tie-older-id"
        )
        self.insert_entry(
            event_at_utc="2024-01-15T20:00:00Z",
            item_description="tie-newer-id"
        )

    def test_authorized_participating_worker_can_access_complete_list(self):
        self.login(1, session_role="Director")
        response = self.client.get("/shift/10/food-fluid")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Food &amp; Fluid Entries", response.data)

    def test_non_support_worker_roles_are_denied(self):
        for user_id in (2, 4):
            with self.subTest(user_id=user_id):
                self.login(user_id, session_role="Support Worker")
                self.assertEqual(
                    self.client.get("/shift/10/food-fluid").status_code,
                    403
                )

    def test_inactive_nonparticipant_and_signed_off_workers_are_denied(self):
        for user_id in (3, 5, 6):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                self.assertEqual(
                    self.client.get("/shift/10/food-fluid").status_code,
                    403
                )

    def test_closed_shift_and_inactive_client_are_denied(self):
        self.login(1)
        self.assertEqual(
            self.client.get("/shift/12/food-fluid").status_code,
            403
        )
        self.assertEqual(
            self.client.get("/shift/13/food-fluid").status_code,
            403
        )

    def test_unrelated_shift_and_query_client_cannot_broaden_access(self):
        self.login(1)
        response = self.client.get(
            "/shift/14/food-fluid?client_id=1&user_id=1"
        )
        self.assertEqual(response.status_code, 403)

    def test_dashboard_shows_only_latest_five_in_deterministic_order(self):
        self.populate_ordered_entries()
        self.login(1)
        page = self.client.get("/shift/10").data.decode()

        self.assertNotIn("oldest</td>", page)
        self.assertNotIn("second-oldest", page)
        expected = (
            "tie-newer-id",
            "tie-older-id",
            "middle-three",
            "middle-two",
            "middle-one",
        )
        positions = [page.index(value) for value in expected]
        self.assertEqual(positions, sorted(positions))

    def test_dashboard_excludes_entries_from_other_shifts_and_clients(self):
        self.insert_entry(item_description="CURRENT SHIFT")
        self.insert_entry(
            shift_id=10,
            client_id=3,
            item_description="MISMATCHED CLIENT SECRET"
        )
        self.insert_entry(
            shift_id=14,
            client_id=3,
            event_at_utc="2024-01-15T23:00:00Z",
            submitted_at_utc="2024-01-15T23:05:00Z",
            item_description="OTHER SHIFT SECRET"
        )
        self.login(1)
        page = self.client.get("/shift/10").data
        self.assertIn(b"CURRENT SHIFT", page)
        self.assertNotIn(b"MISMATCHED CLIENT SECRET", page)
        self.assertNotIn(b"OTHER SHIFT SECRET", page)

    def test_complete_list_includes_all_entries_in_correct_order(self):
        self.populate_ordered_entries()
        self.login(1)
        page = self.client.get("/shift/10/food-fluid").data.decode()

        for item in (
            "oldest",
            "second-oldest",
            "middle-one",
            "middle-two",
            "middle-three",
            "tie-older-id",
            "tie-newer-id",
        ):
            self.assertIn(item, page)

        expected = (
            "tie-newer-id",
            "tie-older-id",
            "middle-three",
            "middle-two",
            "middle-one",
            "second-oldest",
            "oldest",
        )
        positions = [page.index(value) for value in expected]
        self.assertEqual(positions, sorted(positions))

    def test_complete_list_excludes_other_shift_entries(self):
        self.insert_entry(item_description="VISIBLE CURRENT")
        self.insert_entry(
            shift_id=10,
            client_id=3,
            item_description="HIDDEN MISMATCHED CLIENT"
        )
        self.insert_entry(
            shift_id=14,
            client_id=3,
            event_at_utc="2024-01-15T23:00:00Z",
            submitted_at_utc="2024-01-15T23:05:00Z",
            item_description="HIDDEN UNRELATED"
        )
        self.login(1)
        page = self.client.get("/shift/10/food-fluid").data
        self.assertIn(b"VISIBLE CURRENT", page)
        self.assertNotIn(b"HIDDEN MISMATCHED CLIENT", page)
        self.assertNotIn(b"HIDDEN UNRELATED", page)

    def test_empty_dashboard_and_complete_list_states(self):
        self.login(1)
        dashboard = self.client.get("/shift/10")
        complete_list = self.client.get("/shift/10/food-fluid")
        empty_text = b"No Food &amp; Fluid entries recorded for this shift."
        self.assertIn(empty_text, dashboard.data)
        self.assertIn(empty_text, complete_list.data)

    def test_active_and_voided_entries_are_visible_and_distinguished(self):
        self.insert_entry(
            item_description="Active soup",
            additional_details="Active details"
        )
        self.insert_entry(
            event_at_utc="2024-01-15T18:00:00Z",
            item_description="Voided juice",
            additional_details="Original immutable details",
            status="Voided",
            void_reason="Entered for the wrong item",
            voided_by_user_id=2,
            voided_at_utc="2024-01-15T18:30:00Z"
        )
        self.login(1)
        page = self.client.get("/shift/10/food-fluid").data.decode()

        self.assertIn("Active soup", page)
        self.assertIn("Active details", page)
        self.assertIn("status-active\">Active", page)
        self.assertIn("Voided juice", page)
        self.assertIn("Original immutable details", page)
        self.assertIn("status-inactive\">Voided", page)
        self.assertIn("Entered for the wrong item", page)
        self.assertIn("Manager One", page)
        self.assertIn("2024-01-15 10:30", page)

    def test_void_metadata_is_not_shown_for_active_entry(self):
        self.insert_entry(item_description="Active only")
        self.login(1)
        page = self.client.get("/shift/10/food-fluid").data.decode()
        self.assertIn("Active only", page)
        self.assertNotIn("<strong>Reason:</strong>", page)
        self.assertNotIn("<strong>Voided by:</strong>", page)

    def test_free_text_is_escaped_and_tokens_are_hidden(self):
        self.insert_entry(
            item_description="<script>alert(1)</script>",
            additional_details="<b>details</b>"
        )
        self.login(1)
        page = self.client.get("/shift/10/food-fluid").data

        self.assertNotIn(b"<script>alert(1)</script>", page)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn(b"<b>details</b>", page)
        self.assertIn(b"&lt;b&gt;details&lt;/b&gt;", page)
        self.assertNotIn(b"checkpoint-three-token-", page)

    def test_no_edit_or_void_controls_are_rendered(self):
        self.insert_entry(item_description="Immutable record")
        self.login(1)
        page = self.client.get("/shift/10/food-fluid").data.decode()
        self.assertNotIn("/edit", page)
        self.assertNotIn("/void", page)
        self.assertNotIn("Void record", page)

    def test_quick_entry_navigation_is_available_on_both_worker_views(self):
        self.login(1)
        expected = 'href="/shift/10/food-fluid/new"'
        dashboard = self.client.get("/shift/10").data.decode()
        complete_list = self.client.get(
            "/shift/10/food-fluid"
        ).data.decode()
        self.assertIn(expected, dashboard)
        self.assertIn(expected, complete_list)
        self.assertIn('href="/shift/10/food-fluid"', dashboard)

    def test_unauthorized_dashboard_does_not_render_food_data_or_links(self):
        self.insert_entry(item_description="WORKER ONLY ENTRY")
        self.login(2, session_role="Support Worker")
        page = self.client.get("/shift/10").data
        self.assertNotIn(b"WORKER ONLY ENTRY", page)
        self.assertNotIn(b"+ Food &amp; Fluid", page)
        self.assertNotIn(b"View all Food &amp; Fluid entries", page)

    def test_worker_get_workflows_do_not_write_any_records(self):
        self.insert_entry(item_description="Read only")
        before = self.database_counts()
        self.login(1)

        self.assertEqual(self.client.get("/shift/10").status_code, 200)
        self.assertEqual(
            self.client.get("/shift/10/food-fluid").status_code,
            200
        )

        self.assertEqual(self.database_counts(), before)

    def test_local_event_and_submission_times_include_overnight_entry(self):
        self.insert_entry(
            shift_id=11,
            client_id=1,
            event_at_utc="2024-01-16T09:30:00Z",
            submitted_at_utc="2024-01-16T09:35:00Z",
            item_description="Overnight water"
        )
        self.login(1)
        page = self.client.get("/shift/11/food-fluid").data.decode()
        self.assertIn("Overnight water", page)
        self.assertIn("2024-01-16 01:30", page)
        self.assertIn("2024-01-16 01:35", page)


if __name__ == "__main__":
    unittest.main()
