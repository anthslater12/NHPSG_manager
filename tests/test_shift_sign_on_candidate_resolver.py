import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_tables
import app


class ShiftSignOnCandidateResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp.name, "resolver.db")
        conn = sqlite3.connect(self.database_path)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                email_address TEXT,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users VALUES
                (1, 'manager', 'hash', 'Manager', 'manager@example.com', 'Admin', 1),
                (2, 'worker', 'hash', 'Worker', 'worker@example.com', 'Support Worker', 1),
                (3, 'other', 'hash', 'Other Worker', 'other@example.com', 'Support Worker', 1);
            INSERT INTO clients VALUES
                (10, 'Client Ten', 1),
                (20, 'Client Twenty', 1);
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        conn.close()

        self.old_db_name = app.DB_NAME
        app.DB_NAME = self.database_path

    def tearDown(self):
        app.DB_NAME = self.old_db_name
        self.temp.cleanup()

    def open_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_schedule(
        self,
        *,
        client_id=10,
        shift_date="2026-08-03",
        shift_type="Afternoon",
        parent_start="14:00",
        parent_end="22:00",
        status="Published",
    ):
        conn = self.open_database()
        try:
            schedule_shift_id = conn.execute("""
                INSERT INTO schedule_shifts
                (client_id, shift_date, shift_type, planned_start_time,
                 planned_end_time, status, notes, created_by,
                 created_at_utc, updated_by, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, NULL, 1,
                        '2026-08-01T15:00:00Z', 1,
                        '2026-08-01T15:00:00Z')
            """, (
                client_id,
                shift_date,
                shift_type,
                parent_start,
                parent_end,
                status,
            )).lastrowid
            conn.commit()
            return schedule_shift_id
        finally:
            conn.close()

    def add_assignment(
        self,
        schedule_shift_id,
        *,
        user_id=2,
        worker_start=None,
        worker_end=None,
    ):
        conn = self.open_database()
        try:
            schedule_staff_id = conn.execute("""
                INSERT INTO schedule_staff
                (schedule_shift_id, user_id, planned_start_time,
                 planned_end_time, assignment_note, assigned_by,
                 assigned_at_utc)
                VALUES (?, ?, ?, ?, NULL, 1, '2026-08-01T15:00:00Z')
            """, (
                schedule_shift_id,
                user_id,
                worker_start,
                worker_end,
            )).lastrowid
            conn.commit()
            return schedule_staff_id
        finally:
            conn.close()

    def resolve(self, local_datetime, *, user_id=2, client_id=10):
        conn = self.open_database()
        try:
            return app.resolve_shift_sign_on_candidates(
                conn,
                user_id,
                client_id,
                local_datetime.astimezone(timezone.utc),
            )
        finally:
            conn.close()

    def test_unscheduled_clock_suggestions_use_fourteen_hundred_boundary(self):
        cases = (
            ((13, 59), "Day"),
            ((14, 0), "Afternoon"),
            ((14, 30), "Afternoon"),
            ((14, 59), "Afternoon"),
            ((15, 0), "Afternoon"),
            ((22, 59), "Afternoon"),
            ((23, 0), "Overnight"),
            ((6, 59), "Overnight"),
            ((7, 0), "Day"),
        )
        for (hour, minute), expected in cases:
            with self.subTest(hour=hour, minute=minute):
                result = self.resolve(
                    datetime(2026, 8, 3, hour, minute,
                             tzinfo=app.VANCOUVER_TIMEZONE)
                )
                self.assertEqual(result["state"], "UNSCHEDULED")
                self.assertEqual(result["suggested_shift_type"], expected)
                self.assertEqual(result["candidates"], [])

    def test_single_published_afternoon_schedule_at_fourteen_hundred(self):
        schedule_shift_id = self.add_schedule()
        schedule_staff_id = self.add_assignment(schedule_shift_id)

        result = self.resolve(
            datetime(2026, 8, 3, 14, 30,
                     tzinfo=app.VANCOUVER_TIMEZONE)
        )

        self.assertEqual(result["state"], "SCHEDULED_SINGLE")
        self.assertIsNone(result["suggested_shift_type"])
        self.assertEqual(result["operational_date"], "2026-08-03")
        self.assertEqual(result["candidates"], [{
            "schedule_shift_id": schedule_shift_id,
            "schedule_staff_id": schedule_staff_id,
            "client_id": 10,
            "user_id": 2,
            "shift_date": "2026-08-03",
            "shift_type": "Afternoon",
            "planned_start_time": "14:00",
            "planned_end_time": "22:00",
            "parent_planned_start_time": "14:00",
            "parent_planned_end_time": "22:00",
            "worker_specific_times_used": False,
            "source": "scheduled",
        }])

    def test_worker_specific_planned_times_override_parent_times(self):
        schedule_shift_id = self.add_schedule(
            parent_start="13:00",
            parent_end="21:00",
        )
        self.add_assignment(
            schedule_shift_id,
            worker_start="14:00",
            worker_end="22:00",
        )

        result = self.resolve(
            datetime(2026, 8, 3, 21, 30,
                     tzinfo=app.VANCOUVER_TIMEZONE)
        )

        self.assertEqual(result["state"], "SCHEDULED_SINGLE")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["planned_start_time"], "14:00")
        self.assertEqual(candidate["planned_end_time"], "22:00")
        self.assertEqual(candidate["parent_planned_start_time"], "13:00")
        self.assertEqual(candidate["parent_planned_end_time"], "21:00")
        self.assertTrue(candidate["worker_specific_times_used"])

    def test_blank_worker_times_use_parent_times(self):
        schedule_shift_id = self.add_schedule(
            parent_start="14:00",
            parent_end="22:00",
        )
        self.add_assignment(
            schedule_shift_id,
            worker_start=None,
            worker_end=None,
        )

        result = self.resolve(
            datetime(2026, 8, 3, 21, 30,
                     tzinfo=app.VANCOUVER_TIMEZONE)
        )

        self.assertEqual(result["state"], "SCHEDULED_SINGLE")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["planned_start_time"], "14:00")
        self.assertEqual(candidate["planned_end_time"], "22:00")
        self.assertFalse(candidate["worker_specific_times_used"])

    def test_non_published_schedules_are_ignored(self):
        for shift_type, status in (
            ("Day", "Draft"),
            ("Afternoon", "Cancelled"),
            ("Overnight", "Closed"),
        ):
            schedule_shift_id = self.add_schedule(
                shift_type=shift_type,
                parent_start="14:00",
                parent_end="22:00",
                status=status,
            )
            self.add_assignment(schedule_shift_id)

        result = self.resolve(
            datetime(2026, 8, 3, 14, 30,
                     tzinfo=app.VANCOUVER_TIMEZONE)
        )

        self.assertEqual(result["state"], "UNSCHEDULED")
        self.assertEqual(result["candidates"], [])

    def test_wrong_worker_and_client_are_ignored(self):
        wrong_worker_shift = self.add_schedule(shift_type="Day")
        self.add_assignment(wrong_worker_shift, user_id=3)
        wrong_client_shift = self.add_schedule(client_id=20)
        self.add_assignment(wrong_client_shift)

        result = self.resolve(
            datetime(2026, 8, 3, 14, 30,
                     tzinfo=app.VANCOUVER_TIMEZONE)
        )

        self.assertEqual(result["state"], "UNSCHEDULED")
        self.assertEqual(result["candidates"], [])

    def test_schedule_outside_current_interval_is_not_a_candidate(self):
        schedule_shift_id = self.add_schedule(
            parent_start="09:00",
            parent_end="12:00",
        )
        self.add_assignment(schedule_shift_id)

        result = self.resolve(
            datetime(2026, 8, 3, 14, 0,
                     tzinfo=app.VANCOUVER_TIMEZONE)
        )

        self.assertEqual(result["state"], "UNSCHEDULED")
        self.assertEqual(result["suggested_shift_type"], "Afternoon")

    def test_overnight_schedule_from_previous_operational_date_matches(self):
        schedule_shift_id = self.add_schedule(
            shift_date="2026-08-03",
            shift_type="Overnight",
            parent_start="23:00",
            parent_end="07:00",
        )
        self.add_assignment(schedule_shift_id)

        result = self.resolve(
            datetime(2026, 8, 4, 0, 30,
                     tzinfo=app.VANCOUVER_TIMEZONE)
        )

        self.assertEqual(result["state"], "SCHEDULED_SINGLE")
        self.assertEqual(result["operational_date"], "2026-08-03")
        self.assertEqual(
            result["candidates"][0]["shift_date"],
            "2026-08-03"
        )

    def test_overlapping_published_candidates_require_selection(self):
        day_shift_id = self.add_schedule(
            shift_type="Day",
            parent_start="12:00",
            parent_end="16:00",
        )
        afternoon_shift_id = self.add_schedule(
            shift_type="Afternoon",
            parent_start="14:00",
            parent_end="18:00",
        )
        day_staff_id = self.add_assignment(day_shift_id)
        afternoon_staff_id = self.add_assignment(afternoon_shift_id)

        result = self.resolve(
            datetime(2026, 8, 3, 14, 30,
                     tzinfo=app.VANCOUVER_TIMEZONE)
        )

        self.assertEqual(result["state"], "SCHEDULED_MULTIPLE")
        self.assertEqual(
            {
                (candidate["schedule_shift_id"], candidate["schedule_staff_id"])
                for candidate in result["candidates"]
            },
            {
                (day_shift_id, day_staff_id),
                (afternoon_shift_id, afternoon_staff_id),
            },
        )

    def test_resolver_requires_an_aware_current_datetime(self):
        conn = self.open_database()
        try:
            with self.assertRaises(ValueError):
                app.resolve_shift_sign_on_candidates(
                    conn,
                    2,
                    10,
                    datetime(2026, 8, 3, 14, 0),
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
