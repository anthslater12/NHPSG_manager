import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import app


class ForgottenSignoffAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp.name) / "forgotten-signoff.db")
        self.old_db_name = app.DB_NAME
        self.old_testing = app.app.config.get("TESTING")
        app.DB_NAME = self.database_path
        app.app.config.update(TESTING=True)
        self._create_database()

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
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                scheduled_start_time TEXT,
                scheduled_end_time TEXT,
                actual_end_at_utc TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT NOT NULL DEFAULT '15:00',
                actual_end_time TEXT,
                actual_end_at_utc TEXT,
                sign_on_at TEXT,
                sign_off_at TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE schedule_shifts (
                schedule_shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                planned_start_time TEXT NOT NULL,
                planned_end_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Published'
            );
            CREATE TABLE schedule_staff (
                schedule_staff_id INTEGER PRIMARY KEY,
                schedule_shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                planned_start_time TEXT,
                planned_end_time TEXT
            );
            INSERT INTO users (user_id, full_name, role)
            VALUES (1, 'Regression Worker', 'Support Worker');
            INSERT INTO users (user_id, full_name, role)
            VALUES (2, 'Other Worker', 'Support Worker');
            INSERT INTO clients (client_id, client_name)
            VALUES (1, 'Regression Client');
            INSERT INTO clients (client_id, client_name)
            VALUES (2, 'Other Client');
            """
        )
        conn.commit()
        conn.close()

        conn = app.get_db()
        conn.close()

    def _connect(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_shift(
        self,
        shift_id,
        shift_date,
        shift_type,
        scheduled_start_time=None,
        scheduled_end_time=None,
        actual_end_at_utc=None,
        sign_off_at=None,
        client_id=1,
        user_id=1,
        status="Open",
        worker_actual_end_at_utc=None,
        active=1,
    ):
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO shifts
            (shift_id, client_id, shift_date, shift_type, status,
             scheduled_start_time, scheduled_end_time, actual_end_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shift_id,
                client_id,
                shift_date,
                shift_type,
                status,
                scheduled_start_time,
                scheduled_end_time,
                actual_end_at_utc,
            ),
        )
        conn.execute(
            """
            INSERT INTO shift_staff
            (shift_staff_id, shift_id, user_id, actual_start_time,
             actual_end_time, actual_end_at_utc, sign_on_at, sign_off_at,
             active)
            VALUES (?, ?, ?, ?, NULL, ?, '2026-09-22T15:00:00Z', ?, ?)
            """,
            (
                shift_id,
                shift_id,
                user_id,
                scheduled_start_time or "15:00",
                worker_actual_end_at_utc,
                sign_off_at,
                active,
            ),
        )
        conn.commit()
        conn.close()

    def _insert_schedule(
        self,
        schedule_shift_id,
        shift_date,
        shift_type,
        parent_start,
        parent_end,
        worker_start=None,
        worker_end=None,
        client_id=1,
        user_id=1,
        status="Published",
    ):
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO schedule_shifts
            (schedule_shift_id, client_id, shift_date, shift_type,
             planned_start_time, planned_end_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule_shift_id,
                client_id,
                shift_date,
                shift_type,
                parent_start,
                parent_end,
                status,
            ),
        )
        if worker_start is not None or worker_end is not None:
            conn.execute(
                """
                INSERT INTO schedule_staff
                (schedule_staff_id, schedule_shift_id, user_id,
                 planned_start_time, planned_end_time)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    schedule_shift_id,
                    schedule_shift_id,
                    user_id,
                    worker_start,
                    worker_end,
                ),
            )
        conn.commit()
        conn.close()

    def _insert_schedule_staff(
        self,
        schedule_staff_id,
        schedule_shift_id,
        user_id,
        planned_start_time,
        planned_end_time,
    ):
        conn = self._connect()
        conn.execute(
            """
            INSERT INTO schedule_staff
            (schedule_staff_id, schedule_shift_id, user_id,
             planned_start_time, planned_end_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                schedule_staff_id,
                schedule_shift_id,
                user_id,
                planned_start_time,
                planned_end_time,
            ),
        )
        conn.commit()
        conn.close()

    def _alerts_at(self, local_value):
        for value_format in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                local_now = datetime.strptime(local_value, value_format)
                break
            except ValueError:
                continue
        else:
            raise ValueError("Unsupported local test timestamp format")
        local_now = local_now.replace(tzinfo=app.VANCOUVER_TIMEZONE)
        now_utc = local_now.astimezone(timezone.utc)
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=now_utc,
        ):
            return app.get_manager_alerts()

    def _forgotten_alerts_at(self, local_value):
        return [
            alert
            for alert in self._alerts_at(local_value)
            if alert["title"] == "Forgotten Sign Off"
        ]

    def test_day_shift_before_fallback_end_is_not_alerted(self):
        self._insert_shift(1, "2026-09-22", "Day")

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 14:00"),
            [],
        )

    def test_day_shift_after_fallback_end_is_alerted(self):
        self._insert_shift(1, "2026-09-22", "Day")

        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 15:01")),
            1,
        )

    def test_afternoon_shift_before_fallback_end_is_not_alerted(self):
        self._insert_shift(1, "2026-09-22", "Afternoon")

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 19:00"),
            [],
        )

    def test_afternoon_shift_after_fallback_end_is_alerted(self):
        self._insert_shift(1, "2026-09-22", "Afternoon")

        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:01")),
            1,
        )

    def test_overnight_fallback_rolls_to_the_next_vancouver_day(self):
        self._insert_shift(1, "2026-09-22", "Overnight")

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-23 02:00"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-23 07:01")),
            1,
        )

    def test_explicit_overnight_planned_end_crosses_midnight(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Overnight",
            "22:00",
            "06:30",
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Overnight",
            scheduled_start_time="22:00",
            scheduled_end_time="06:30",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-23 06:29"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-23 06:31")),
            1,
        )

    def test_parent_planned_end_overrides_legacy_shift_end(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "22:00",
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="23:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 21:59"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 22:01")),
            1,
        )

    def test_worker_planned_end_overrides_parent_planned_end(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "22:00",
            worker_start="15:30",
            worker_end="23:30",
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="23:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 23:00"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:31")),
            1,
        )

    def test_completed_assignment_is_not_alerted(self):
        self._insert_shift(
            1,
            "2026-09-22",
            "Day",
            worker_actual_end_at_utc="2026-09-22T22:00:00Z",
        )
        self._insert_shift(
            2,
            "2026-09-22",
            "Day",
            sign_off_at="2026-09-22T22:00:00Z",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-23 09:00"),
            [],
        )

    def test_inactive_assignment_is_not_alerted(self):
        self._insert_shift(
            1,
            "2026-09-22",
            "Day",
            active=0,
        )

        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-23 09:00")),
            0,
        )

    def test_parent_actual_end_is_not_alerted(self):
        self._insert_shift(
            1,
            "2026-09-22",
            "Day",
            actual_end_at_utc="2026-09-22T22:00:00Z",
        )

        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-23 09:00")),
            0,
        )

    def test_non_open_parent_shift_is_not_alerted(self):
        self._insert_shift(
            1,
            "2026-09-22",
            "Day",
            status="Closed",
        )

        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-23 09:00")),
            0,
        )

    def test_utc_date_advanced_before_vancouver_afternoon_end_is_not_alerted(self):
        self._insert_shift(1, "2026-09-22", "Afternoon")

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 19:00"),
            [],
        )

    def test_schedule_for_another_client_does_not_change_effective_end(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "20:00",
            client_id=2,
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="23:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 20:01"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:01")),
            1,
        )

    def test_schedule_for_another_date_does_not_change_effective_end(self):
        self._insert_schedule(
            1,
            "2026-09-23",
            "Afternoon",
            "15:00",
            "20:00",
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="23:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 20:01"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:01")),
            1,
        )

    def test_schedule_override_for_another_worker_does_not_apply(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "23:00",
            worker_start="15:00",
            worker_end="20:00",
            user_id=2,
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="23:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 20:01"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:01")),
            1,
        )

    def test_draft_schedule_never_overrides_published_schedule(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "23:00",
            status="Published",
        )
        self._insert_schedule(
            2,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "20:00",
            status="Draft",
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="23:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 20:01"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:01")),
            1,
        )

    def test_cancelled_schedule_never_changes_effective_end(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "20:00",
            status="Cancelled",
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="23:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 20:01"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:01")),
            1,
        )

    def test_highest_published_schedule_id_wins_deterministically(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "20:00",
            status="Published",
        )
        self._insert_schedule(
            2,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "23:00",
            status="Published",
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="22:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 22:01"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:01")),
            1,
        )

    def test_highest_schedule_staff_id_wins_worker_override_deterministically(self):
        self._insert_schedule(
            1,
            "2026-09-22",
            "Afternoon",
            "15:00",
            "22:00",
            worker_start="15:00",
            worker_end="20:00",
        )
        self._insert_schedule_staff(
            2,
            1,
            1,
            "15:00",
            "23:00",
        )
        self._insert_shift(
            1,
            "2026-09-22",
            "Afternoon",
            scheduled_start_time="15:00",
            scheduled_end_time="22:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 22:01"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:01")),
            1,
        )

    def test_dst_nonexistent_worker_end_falls_back_to_parent(self):
        self._insert_schedule(
            1,
            "2026-03-08",
            "Day",
            "00:30",
            "14:00",
            worker_start="00:30",
            worker_end="02:30",
        )
        self._insert_shift(
            1,
            "2026-03-08",
            "Day",
            scheduled_start_time="00:30",
            scheduled_end_time="15:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-03-08 13:59"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-03-08 14:01")),
            1,
        )

    def test_dst_nonexistent_parent_end_falls_back_to_shift_end(self):
        self._insert_schedule(
            1,
            "2026-03-08",
            "Day",
            "00:30",
            "02:30",
        )
        self._insert_shift(
            1,
            "2026-03-08",
            "Day",
            scheduled_start_time="00:30",
            scheduled_end_time="14:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2026-03-08 13:59"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-03-08 14:01")),
            1,
        )

    def test_dst_ambiguous_parent_end_falls_back_to_shift_end(self):
        self._insert_schedule(
            1,
            "2025-11-02",
            "Day",
            "00:30",
            "01:30",
        )
        self._insert_shift(
            1,
            "2025-11-02",
            "Day",
            scheduled_start_time="00:30",
            scheduled_end_time="14:00",
        )

        self.assertEqual(
            self._forgotten_alerts_at("2025-11-02 13:59"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2025-11-02 14:01")),
            1,
        )

    def test_exact_day_end_is_not_alerted_until_after_end(self):
        self._insert_shift(1, "2026-09-22", "Day")

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 15:00"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 15:00:01")),
            1,
        )

    def test_exact_afternoon_end_is_not_alerted_until_after_end(self):
        self._insert_shift(1, "2026-09-22", "Afternoon")

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-22 23:00"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-22 23:00:01")),
            1,
        )

    def test_exact_overnight_end_is_not_alerted_until_after_end(self):
        self._insert_shift(1, "2026-09-22", "Overnight")

        self.assertEqual(
            self._forgotten_alerts_at("2026-09-23 07:00"),
            [],
        )
        self.assertEqual(
            len(self._forgotten_alerts_at("2026-09-23 07:00:01")),
            1,
        )


if __name__ == "__main__":
    unittest.main()
