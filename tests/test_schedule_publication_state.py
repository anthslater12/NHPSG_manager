import sqlite3
import unittest
from datetime import date

import add_schedule_tables
import app


class SchedulePublicationStateTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self.conn.execute(
            "INSERT INTO users (user_id, full_name, role) VALUES (1, 'Test User', 'Admin')"
        )
        self.conn.execute(
            "CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)"
        )
        self.conn.execute(
            "INSERT INTO clients (client_id, client_name) VALUES (10, 'Test Client')"
        )
        add_schedule_tables.migrate(self.conn)

    def tearDown(self):
        self.conn.close()

    def add_shift(self, status):
        return self.conn.execute(
            """
            INSERT INTO schedule_shifts
                (client_id, shift_date, shift_type, planned_start_time,
                 planned_end_time, status, created_by, created_at_utc,
                 updated_by, updated_at_utc)
            VALUES (10, '2026-08-03', 'Day', '08:00', '16:00', ?, 1,
                    '2026-01-01T00:00:00Z', 1, '2026-01-01T00:00:00Z')
            """,
            (status,),
        ).lastrowid

    def state(self):
        return app._schedule_week_publication_state(
            self.conn, date(2026, 8, 3), 10
        )

    def test_empty_week_is_unpublished_and_not_visible(self):
        state = self.state()

        self.assertEqual(state["state"], "Empty")
        self.assertFalse(state["is_fully_published"])
        self.assertFalse(app._schedule_week_visible_to_support(state))

    def test_all_draft_week(self):
        self.add_shift("Draft")

        state = self.state()

        self.assertEqual(state["state"], "Draft")
        self.assertTrue(state["has_draft_rows"])
        self.assertFalse(app._schedule_week_visible_to_support(state))

    def test_all_published_week_is_visible(self):
        self.add_shift("Published")

        state = self.state()

        self.assertEqual(state["state"], "Published")
        self.assertTrue(state["is_fully_published"])
        self.assertTrue(app._schedule_week_visible_to_support(state))

    def test_published_and_draft_week_is_not_fully_published(self):
        self.add_shift("Published")
        self.conn.execute(
            """
            INSERT INTO schedule_shifts
                (client_id, shift_date, shift_type, planned_start_time,
                 planned_end_time, status, created_by, created_at_utc,
                 updated_by, updated_at_utc)
            VALUES (10, '2026-08-04', 'Day', '08:00', '16:00', 'Draft', 1,
                    '2026-01-01T00:00:00Z', 1, '2026-01-01T00:00:00Z')
            """
        )

        state = self.state()

        self.assertEqual(state["state"], "Mixed")
        self.assertFalse(state["is_fully_published"])
        self.assertFalse(app._schedule_week_visible_to_support(state))

    def test_terminal_statuses_are_reported_as_mixed(self):
        self.add_shift("Published")
        self.conn.execute(
            """
            INSERT INTO schedule_shifts
                (client_id, shift_date, shift_type, planned_start_time,
                 planned_end_time, status, created_by, created_at_utc,
                 updated_by, updated_at_utc)
            VALUES (10, '2026-08-04', 'Day', '08:00', '16:00', 'Closed', 1,
                    '2026-01-01T00:00:00Z', 1, '2026-01-01T00:00:00Z')
            """
        )

        state = self.state()

        self.assertEqual(state["state"], "Mixed")
        self.assertFalse(state["is_fully_published"])


if __name__ == "__main__":
    unittest.main()
