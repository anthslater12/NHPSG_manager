import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_tables
import app


class ScheduleAwareSignOnTests(unittest.TestCase):
    NOW_UTC = datetime(2026, 8, 3, 21, 5, tzinfo=timezone.utc)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp.name, "sign-on.db")
        self.old_db_name = app.DB_NAME
        app.DB_NAME = self.database_path
        self.now_patcher = mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=self.NOW_UTC,
        )
        self.now_patcher.start()
        self.create_database()
        self.client = app.app.test_client()

    def tearDown(self):
        self.now_patcher.stop()
        app.DB_NAME = self.old_db_name
        self.temp.cleanup()

    def create_database(self):
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
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                scheduled_start_time TEXT,
                scheduled_end_time TEXT,
                actual_end_at_utc TEXT,
                created_at TEXT,
                closed_at TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT NOT NULL,
                actual_end_time TEXT,
                actual_end_at_utc TEXT,
                sign_on_at TEXT,
                sign_off_at TEXT,
                start_checklist_completed INTEGER NOT NULL DEFAULT 0,
                end_checklist_completed INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE shift_tasks (
                shift_task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                instructions TEXT,
                task_stage TEXT NOT NULL,
                requires_input INTEGER NOT NULL DEFAULT 0,
                input_label TEXT,
                input_type TEXT,
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
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Admin', 'admin@example.com', 'Admin', 1),
                (2, 'worker', 'hash', 'Worker', 'worker@example.com', 'Support Worker', 1),
                (3, 'second-worker', 'hash', 'Second Worker', 'second@example.com', 'Support Worker', 1);
            INSERT INTO clients VALUES
                (10, 'Client Ten', 1);
            INSERT INTO shift_tasks
                (task_name, task_stage, active)
                VALUES ('Open the shift', 'BEGIN_SHIFT', 1);
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        conn.close()

    def open_database(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def authenticate(self):
        self.authenticate_user(2, "Worker")

    def authenticate_user(self, user_id, full_name):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = "Support Worker"
            session["full_name"] = full_name

    def add_schedule(
        self,
        shift_type,
        start,
        end,
        *,
        shift_date="2026-08-03",
        user_id=2,
        worker_start=None,
        worker_end=None,
    ):
        conn = self.open_database()
        try:
            schedule_shift_id = conn.execute("""
                INSERT INTO schedule_shifts
                (client_id, shift_date, shift_type, planned_start_time,
                 planned_end_time, status, notes, created_by,
                 created_at_utc, updated_by, updated_at_utc)
                VALUES (10, ?, ?, ?, ?, 'Published', NULL, 1,
                        '2026-08-01T15:00:00Z', 1,
                        '2026-08-01T15:00:00Z')
            """, (
                shift_date,
                shift_type,
                start,
                end,
            )).lastrowid
            conn.execute("""
                INSERT INTO schedule_staff
                (schedule_shift_id, user_id, planned_start_time,
                 planned_end_time, assignment_note, assigned_by,
                 assigned_at_utc)
                VALUES (?, ?, ?, ?, NULL, 1,
                        '2026-08-01T15:00:00Z')
            """, (
                schedule_shift_id,
                user_id,
                worker_start,
                worker_end,
            ))
            conn.commit()
            return schedule_shift_id
        finally:
            conn.close()

    def add_schedule_assignment(
        self,
        schedule_shift_id,
        user_id,
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
                VALUES (?, ?, ?, ?, NULL, 1,
                        '2026-08-01T15:00:00Z')
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

    def allow_duplicate_schedule_types(self):
        conn = self.open_database()
        try:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DROP TABLE schedule_staff")
            conn.execute("DROP TABLE schedule_shifts")
            conn.executescript("""
                CREATE TABLE schedule_shifts (
                    schedule_shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    shift_date TEXT NOT NULL,
                    shift_type TEXT NOT NULL,
                    planned_start_time TEXT NOT NULL,
                    planned_end_time TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'Draft',
                    notes TEXT,
                    created_by INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    updated_by INTEGER NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE schedule_staff (
                    schedule_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_shift_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    planned_start_time TEXT,
                    planned_end_time TEXT,
                    assignment_note TEXT,
                    assigned_by INTEGER NOT NULL,
                    assigned_at_utc TEXT NOT NULL
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def counts(self):
        conn = self.open_database()
        try:
            return tuple(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("shifts", "shift_staff")
            )
        finally:
            conn.close()

    def shift_rows(self):
        conn = self.open_database()
        try:
            return conn.execute("""
                SELECT s.*, ss.actual_start_time,
                       ss.start_checklist_completed
                FROM shifts s
                JOIN shift_staff ss ON ss.shift_id = s.shift_id
                ORDER BY s.shift_id
            """).fetchall()
        finally:
            conn.close()

    def test_single_published_afternoon_signs_on_without_confirmation(self):
        self.add_schedule("Afternoon", "14:00", "22:00")
        self.authenticate()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ) as reconciliation:
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/1/start-checklist", response.headers["Location"])
        self.assertEqual(self.counts(), (1, 1))
        row = self.shift_rows()[0]
        self.assertEqual(row["shift_type"], "Afternoon")
        self.assertEqual(row["shift_date"], "2026-08-03")
        self.assertEqual(row["scheduled_start_time"], "14:00")
        self.assertEqual(row["scheduled_end_time"], "22:00")
        self.assertEqual(row["actual_start_time"], "14:05")
        reconciliation.assert_called_once()

        checklist = self.client.get(response.headers["Location"])
        self.assertEqual(checklist.status_code, 200)
        self.assertIn(b"Afternoon Shift", checklist.data)
        self.assertNotIn(b"Which shift are you starting?", checklist.data)

    def test_worker_overrides_do_not_mutate_shared_shift_times(self):
        schedule_shift_id = self.add_schedule(
            "Afternoon",
            "14:00",
            "23:00",
            worker_start="14:00",
            worker_end="22:00",
        )
        self.add_schedule_assignment(
            schedule_shift_id,
            3,
            worker_start="15:00",
            worker_end="23:00",
        )

        self.authenticate()
        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            first = self.client.get("/dashboard")

        self.assertEqual(first.status_code, 302)
        self.assertEqual(self.counts(), (1, 1))
        first_shift = self.shift_rows()[0]
        self.assertEqual(first_shift["scheduled_start_time"], "14:00")
        self.assertEqual(first_shift["scheduled_end_time"], "23:00")

        self.authenticate_user(3, "Second Worker")
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 8, 3, 22, 5, tzinfo=timezone.utc),
        ), mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            second = self.client.get("/dashboard")

        self.assertEqual(second.status_code, 302)
        self.assertEqual(self.counts(), (1, 2))
        shared_shift = self.shift_rows()[0]
        self.assertEqual(shared_shift["scheduled_start_time"], "14:00")
        self.assertEqual(shared_shift["scheduled_end_time"], "23:00")

        conn = self.open_database()
        try:
            worker_two = app.resolve_shift_sign_on_candidates(
                conn,
                3,
                10,
                datetime(2026, 8, 3, 22, 5, tzinfo=timezone.utc),
            )
        finally:
            conn.close()
        self.assertEqual(worker_two["state"], "SCHEDULED_SINGLE")
        self.assertEqual(
            worker_two["candidates"][0]["planned_start_time"],
            "15:00",
        )
        self.assertEqual(
            worker_two["candidates"][0]["planned_end_time"],
            "23:00",
        )

    def test_unscheduled_sign_on_requires_confirmation_before_persistence(self):
        self.authenticate()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ) as reconciliation:
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])
        self.assertEqual(self.counts(), (0, 0))
        reconciliation.assert_not_called()

        confirmation = self.client.get(response.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn(b"Which shift are you starting?", confirmation.data)
        self.assertIn(b'value="Afternoon"', confirmation.data)
        self.assertIn(b"checked", confirmation.data)
        self.assertIn(b'value="Day"', confirmation.data)
        self.assertIn(b'value="Overnight"', confirmation.data)

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ) as reconciliation:
            confirmed = self.client.post(
                "/shift/sign-on/confirm",
                data={"shift_type": "Day"},
            )

        self.assertEqual(confirmed.status_code, 302)
        self.assertIn("/shift/1/start-checklist", confirmed.headers["Location"])
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(self.shift_rows()[0]["shift_type"], "Day")
        self.assertIsNone(self.shift_rows()[0]["scheduled_start_time"])
        reconciliation.assert_called_once()

    def test_manual_current_sign_on_uses_authoritative_schedule(self):
        self.add_schedule("Afternoon", "14:00", "22:00")
        self.authenticate()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            response = self.client.post(
                "/shift/sign-on",
                data={
                    "shift_date": "2026-08-03",
                    "shift_type": "Day",
                    "actual_start_time": "14:05",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/1/start-checklist", response.headers["Location"])
        row = self.shift_rows()[0]
        self.assertEqual(row["client_id"], 10)
        self.assertEqual(row["shift_type"], "Afternoon")
        self.assertEqual(row["scheduled_start_time"], "14:00")
        self.assertEqual(row["actual_start_time"], "14:05")

        conn = self.open_database()
        try:
            activity = conn.execute("""
                SELECT activity_type, summary, client_id, shift_id
                FROM activity_log
                WHERE activity_type = 'manual_sign_on'
            """).fetchone()
        finally:
            conn.close()
        self.assertEqual(activity["client_id"], 10)
        self.assertEqual(activity["shift_id"], 1)
        self.assertEqual(activity["summary"], "User manually signed onto Afternoon shift")

    def test_support_worker_non_current_date_cannot_bypass_schedule_resolution(self):
        self.add_schedule("Afternoon", "14:00", "22:00")
        self.authenticate()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            response = self.client.post(
                "/shift/sign-on",
                data={
                    "shift_date": "2026-08-02",
                    "shift_type": "Day",
                    "actual_start_time": "14:05",
                },
            )

        self.assertEqual(response.status_code, 302)
        rows = self.shift_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["shift_date"], "2026-08-03")
        self.assertEqual(rows[0]["shift_type"], "Afternoon")
        self.assertNotEqual(rows[0]["shift_date"], "2026-08-02")

    def test_management_role_retains_historical_manual_sign_on(self):
        with self.client.session_transaction() as session_data:
            session_data["user_id"] = 1
            session_data["role"] = "Admin"

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            response = self.client.post(
                "/shift/sign-on",
                data={
                    "shift_date": "2026-08-02",
                    "shift_type": "Day",
                    "actual_start_time": "08:00",
                },
            )

        self.assertEqual(response.status_code, 302)
        row = self.shift_rows()[0]
        self.assertEqual(row["shift_date"], "2026-08-02")
        self.assertEqual(row["shift_type"], "Day")

    def test_non_management_non_worker_role_cannot_use_historical_manual_sign_on(self):
        conn = self.open_database()
        try:
            conn.execute(
                "UPDATE users SET role = 'Behaviour Consultant' WHERE user_id = 3"
            )
            conn.commit()
        finally:
            conn.close()

        self.authenticate_user(3, "Second Worker")
        response = self.client.post(
            "/shift/sign-on",
            data={
                "shift_date": "2026-08-02",
                "shift_type": "Day",
                "actual_start_time": "08:00",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.counts(), (0, 0))

    def test_manual_sign_on_uses_worker_override_but_persists_parent_times(self):
        self.add_schedule(
            "Afternoon",
            "14:00",
            "23:00",
            worker_start="14:30",
            worker_end="22:00",
        )
        self.authenticate()

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 8, 3, 21, 35, tzinfo=timezone.utc),
        ), mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            response = self.client.post(
                "/shift/sign-on",
                data={
                    "shift_date": "2026-08-02",
                    "shift_type": "Day",
                    "actual_start_time": "14:35",
                },
            )

        self.assertEqual(response.status_code, 302)
        row = self.shift_rows()[0]
        self.assertEqual(row["shift_type"], "Afternoon")
        self.assertEqual(row["scheduled_start_time"], "14:00")
        self.assertEqual(row["scheduled_end_time"], "23:00")

    def test_active_staff_listing_uses_persisted_afternoon_at_fourteen_thirty(self):
        self.add_schedule("Afternoon", "14:00", "22:00")
        self.authenticate()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 8, 3, 21, 30, tzinfo=timezone.utc),
        ):
            active_staff = app.get_active_shift_staff()

        self.assertEqual(len(active_staff), 1)
        self.assertEqual(active_staff[0]["full_name"], "Worker")
        self.assertEqual(active_staff[0]["shift_type"], "Afternoon")

    def test_active_staff_listing_includes_mixed_persisted_shift_types(self):
        conn = self.open_database()
        try:
            day_shift_id = conn.execute("""
                INSERT INTO shifts
                    (client_id, shift_date, shift_type, status)
                VALUES (10, '2026-08-03', 'Day', 'Open')
            """).lastrowid
            afternoon_shift_id = conn.execute("""
                INSERT INTO shifts
                    (client_id, shift_date, shift_type, status)
                VALUES (10, '2026-08-03', 'Afternoon', 'Open')
            """).lastrowid
            conn.execute("""
                INSERT INTO shift_staff
                    (shift_id, user_id, actual_start_time, active)
                VALUES (?, 3, '13:55', 1)
            """, (day_shift_id,))
            conn.execute("""
                INSERT INTO shift_staff
                    (shift_id, user_id, actual_start_time, active)
                VALUES (?, 2, '14:05', 1)
            """, (afternoon_shift_id,))
            conn.commit()
        finally:
            conn.close()

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 8, 3, 21, 30, tzinfo=timezone.utc),
        ):
            active_staff = app.get_active_shift_staff()

        self.assertEqual(
            [row["shift_type"] for row in active_staff],
            ["Day", "Afternoon"],
        )

    def test_manual_sign_on_confirmation_handles_afternoon_and_overnight_overlap(self):
        afternoon_id = self.add_schedule(
            "Afternoon",
            "14:00",
            "23:59",
        )
        overnight_id = self.add_schedule(
            "Overnight",
            "23:00",
            "07:00",
        )
        self.authenticate()
        late_evening_utc = datetime(2026, 8, 4, 6, 30, tzinfo=timezone.utc)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=late_evening_utc,
        ):
            response = self.client.post(
                "/shift/sign-on",
                data={
                    "shift_date": "2026-08-03",
                    "shift_type": "Day",
                    "actual_start_time": "23:30",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertIn("/shift/sign-on/confirm", response.headers["Location"])

            confirmation = self.client.get(response.headers["Location"])
            self.assertEqual(confirmation.status_code, 200)
            self.assertIn(f'value="{afternoon_id}"'.encode(), confirmation.data)
            self.assertIn(f'value="{overnight_id}"'.encode(), confirmation.data)

            with mock.patch.object(
                app,
                "reconcile_staff_notice_shift_sign_on",
            ):
                selected = self.client.post(
                    "/shift/sign-on/confirm",
                    data={"schedule_shift_id": str(overnight_id)},
                )

        self.assertEqual(selected.status_code, 302)
        row = self.shift_rows()[0]
        self.assertEqual(row["shift_date"], "2026-08-03")
        self.assertEqual(row["shift_type"], "Overnight")
        self.assertEqual(row["scheduled_start_time"], "23:00")
        self.assertEqual(row["scheduled_end_time"], "07:00")

    def test_manual_current_unscheduled_sign_on_reuses_confirmation_flow(self):
        self.authenticate()

        response = self.client.post(
            "/shift/sign-on",
            data={
                "shift_date": "2026-08-03",
                "shift_type": "Day",
                "actual_start_time": "14:05",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])
        self.assertEqual(self.counts(), (0, 0))

        confirmation = self.client.get(response.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn(b'value="Afternoon"', confirmation.data)
        self.assertIn(b"checked", confirmation.data)

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            confirmed = self.client.post(
                "/shift/sign-on/confirm",
                data={"shift_type": "Afternoon"},
            )

        self.assertEqual(confirmed.status_code, 302)
        self.assertIn("/shift/1/start-checklist", confirmed.headers["Location"])
        self.assertEqual(self.shift_rows()[0]["shift_type"], "Afternoon")

    def test_manual_current_sign_on_with_multiple_schedules_requires_selection(self):
        day_id = self.add_schedule("Day", "12:00", "16:00")
        afternoon_id = self.add_schedule("Afternoon", "14:00", "18:00")
        self.authenticate()

        response = self.client.post(
            "/shift/sign-on",
            data={
                "shift_date": "2026-08-03",
                "shift_type": "Day",
                "actual_start_time": "14:05",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])
        self.assertEqual(self.counts(), (0, 0))

        confirmation = self.client.get(response.headers["Location"])
        self.assertIn(f'value="{day_id}"'.encode(), confirmation.data)
        self.assertIn(f'value="{afternoon_id}"'.encode(), confirmation.data)

        invalid = self.client.post(
            "/shift/sign-on/confirm",
            data={"schedule_shift_id": "999"},
        )
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(self.counts(), (0, 0))

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            selected = self.client.post(
                "/shift/sign-on/confirm",
                data={"schedule_shift_id": str(afternoon_id)},
            )
        self.assertEqual(selected.status_code, 302)
        self.assertIn("/shift/1/start-checklist", selected.headers["Location"])
        self.assertEqual(self.shift_rows()[0]["shift_type"], "Afternoon")

    def test_manual_current_sign_on_rejects_zero_or_multiple_active_clients(self):
        for active_client_ids in ((), (10, 11)):
            with self.subTest(active_client_ids=active_client_ids):
                conn = self.open_database()
                try:
                    conn.execute("UPDATE clients SET active = 0")
                    if 11 in active_client_ids:
                        conn.execute("""
                            INSERT INTO clients (client_id, client_name, active)
                            VALUES (11, 'Client Eleven', 1)
                        """)
                    for client_id in active_client_ids:
                        conn.execute(
                            "UPDATE clients SET active = 1 WHERE client_id = ?",
                            (client_id,),
                        )
                    conn.commit()
                finally:
                    conn.close()

                self.authenticate()
                response = self.client.post(
                    "/shift/sign-on",
                    data={
                        "shift_date": "2026-08-03",
                        "shift_type": "Afternoon",
                        "actual_start_time": "14:05",
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Please try again", response.data)
                self.assertEqual(self.counts(), (0, 0))

    def test_manual_current_sign_on_preserves_active_assignment_guard(self):
        self.add_schedule("Afternoon", "14:00", "22:00")
        conn = self.open_database()
        try:
            shift_id = conn.execute("""
                INSERT INTO shifts
                (client_id, shift_date, shift_type, status)
                VALUES (10, '2026-08-03', 'Day', 'Open')
            """).lastrowid
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, 2, '08:00', 1)
            """, (shift_id,))
            conn.commit()
        finally:
            conn.close()
        self.authenticate()

        response = self.client.post(
            "/shift/sign-on",
            data={
                "shift_date": "2026-08-03",
                "shift_type": "Day",
                "actual_start_time": "14:05",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please try again", response.data)
        self.assertEqual(self.counts(), (1, 1))

    def test_manual_current_duplicate_post_does_not_create_second_assignment(self):
        self.add_schedule("Afternoon", "14:00", "22:00")
        self.authenticate()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            first = self.client.post(
                "/shift/sign-on",
                data={
                    "shift_date": "2026-08-03",
                    "shift_type": "Day",
                    "actual_start_time": "14:05",
                },
            )
            second = self.client.post(
                "/shift/sign-on",
                data={
                    "shift_date": "2026-08-03",
                    "shift_type": "Day",
                    "actual_start_time": "14:06",
                },
            )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self.counts(), (1, 1))

    def test_manual_activity_log_failure_rolls_back_new_sign_on(self):
        self.add_schedule("Afternoon", "14:00", "22:00")
        self.authenticate()
        real_log_activity = app.log_activity

        def fail_manual_activity(conn, *args, **kwargs):
            if kwargs.get("activity_type") == "manual_sign_on":
                raise RuntimeError("controlled manual activity failure")
            return real_log_activity(conn, *args, **kwargs)

        with mock.patch.object(app, "log_activity", side_effect=fail_manual_activity):
            response = self.client.post(
                "/shift/sign-on",
                data={
                    "shift_date": "2026-08-03",
                    "shift_type": "Day",
                    "actual_start_time": "14:05",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Please try again", response.data)
        self.assertEqual(self.counts(), (0, 0))

    def test_scheduled_afternoon_at_fourteen_five_can_later_be_signed_off(self):
        self.add_schedule("Afternoon", "14:00", "22:00")
        self.authenticate()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        conn = self.open_database()
        try:
            assignment_id = conn.execute("""
                SELECT shift_staff_id
                FROM shift_staff
                WHERE user_id = 2
            """).fetchone()[0]
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (1, 3, '14:05', 1)
            """)
            conn.commit()
        finally:
            conn.close()

        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["role"] = "Admin"
            session["full_name"] = "Admin"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 8, 4, 1, 5, tzinfo=timezone.utc),
        ):
            signed_off = self.client.post(
                f"/shift-staff/{assignment_id}/manager-sign-off",
                data={
                    "actual_end_date": "2026-08-03",
                    "actual_end_time": "18:00",
                    "reason": "Recorded Afternoon sign-off.",
                },
            )

        self.assertEqual(signed_off.status_code, 302)
        conn = self.open_database()
        try:
            assignment = conn.execute("""
                SELECT actual_end_at_utc, sign_off_at, active
                FROM shift_staff
                WHERE shift_staff_id = ?
            """, (assignment_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(
            assignment["actual_end_at_utc"],
            "2026-08-04T01:00:00Z",
        )
        self.assertEqual(assignment["sign_off_at"], "2026-08-04T01:05:00Z")
        self.assertEqual(assignment["active"], 0)

    def test_confirmed_unscheduled_afternoon_can_later_be_signed_off(self):
        self.authenticate()
        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            confirmed = self.client.post(
                "/shift/sign-on/confirm",
                data={"shift_type": "Afternoon"},
            )

        self.assertEqual(confirmed.status_code, 302)
        row = self.shift_rows()[0]
        self.assertEqual(row["shift_type"], "Afternoon")
        self.assertEqual(row["actual_start_time"], "14:05")

        conn = self.open_database()
        try:
            assignment_id = conn.execute("""
                SELECT shift_staff_id
                FROM shift_staff
                WHERE user_id = 2
            """).fetchone()[0]
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (1, 3, '14:05', 1)
            """)
            conn.commit()
        finally:
            conn.close()

        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["role"] = "Admin"
            session["full_name"] = "Admin"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 8, 4, 1, 5, tzinfo=timezone.utc),
        ):
            signed_off = self.client.post(
                f"/shift-staff/{assignment_id}/manager-sign-off",
                data={
                    "actual_end_date": "2026-08-03",
                    "actual_end_time": "18:00",
                    "reason": "Confirmed Afternoon sign-off.",
                },
            )

        self.assertEqual(signed_off.status_code, 302)
        conn = self.open_database()
        try:
            assignment = conn.execute("""
                SELECT actual_end_at_utc, sign_off_at, active
                FROM shift_staff
                WHERE shift_staff_id = ?
            """, (assignment_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(
            assignment["actual_end_at_utc"],
            "2026-08-04T01:00:00Z",
        )
        self.assertEqual(assignment["sign_off_at"], "2026-08-04T01:05:00Z")
        self.assertEqual(assignment["active"], 0)

    def test_overnight_post_midnight_start_can_later_be_signed_off(self):
        conn = self.open_database()
        try:
            shift_id = conn.execute("""
                INSERT INTO shifts
                (client_id, shift_date, shift_type, status)
                VALUES (10, '2026-08-03', 'Overnight', 'Open')
            """).lastrowid
            assignment_id = conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, 2, '00:30', 1)
            """, (shift_id,)).lastrowid
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, 3, '00:30', 1)
            """, (shift_id,))
            conn.commit()
        finally:
            conn.close()

        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["role"] = "Admin"
            session["full_name"] = "Admin"
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 8, 4, 13, 5, tzinfo=timezone.utc),
        ):
            signed_off = self.client.post(
                f"/shift-staff/{assignment_id}/manager-sign-off",
                data={
                    "actual_end_date": "2026-08-04",
                    "actual_end_time": "06:00",
                    "reason": "Overnight sign-off.",
                },
            )

        self.assertEqual(signed_off.status_code, 302)
        conn = self.open_database()
        try:
            assignment = conn.execute("""
                SELECT actual_end_at_utc, sign_off_at, active
                FROM shift_staff
                WHERE shift_staff_id = ?
            """, (assignment_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(
            assignment["actual_end_at_utc"],
            "2026-08-04T13:00:00Z",
        )
        self.assertEqual(assignment["sign_off_at"], "2026-08-04T13:05:00Z")
        self.assertEqual(assignment["active"], 0)

    def test_documentation_context_start_new_shift_redirects_before_persistence(self):
        self.authenticate()

        response = self.client.post(
            "/documentation-context",
            data={"action": "start_new_shift"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])
        self.assertEqual(self.counts(), (0, 0))

    def test_confirmation_get_has_no_reconciliation_or_database_writes(self):
        self.authenticate()
        before = self.counts()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ) as reconciliation:
            response = self.client.get("/shift/sign-on/confirm")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(), before)
        reconciliation.assert_not_called()

    def test_confirmation_post_rolls_back_when_reconciliation_fails(self):
        self.authenticate()
        before_counts = self.counts()
        conn = self.open_database()
        try:
            before_activity_count = conn.execute(
                "SELECT COUNT(*) FROM activity_log"
            ).fetchone()[0]
        finally:
            conn.close()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
            side_effect=RuntimeError("controlled failure"),
        ):
            response = self.client.post(
                "/shift/sign-on/confirm",
                data={"shift_type": "Afternoon"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.counts(), before_counts)
        conn = self.open_database()
        try:
            after_activity_count = conn.execute(
                "SELECT COUNT(*) FROM activity_log"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(after_activity_count, before_activity_count)

    def test_multiple_schedules_require_valid_candidate_selection(self):
        day_id = self.add_schedule("Day", "12:00", "16:00")
        afternoon_id = self.add_schedule("Afternoon", "14:00", "18:00")
        self.authenticate()

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])
        self.assertEqual(self.counts(), (0, 0))

        confirmation = self.client.get(response.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn(f'value="{day_id}"'.encode(), confirmation.data)
        self.assertIn(f'value="{afternoon_id}"'.encode(), confirmation.data)
        self.assertNotIn(b'name="shift_type"', confirmation.data)

        invalid = self.client.post(
            "/shift/sign-on/confirm",
            data={"schedule_shift_id": "999"},
        )
        self.assertEqual(invalid.status_code, 409)
        self.assertEqual(self.counts(), (0, 0))

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            valid = self.client.post(
                "/shift/sign-on/confirm",
                data={"schedule_shift_id": str(afternoon_id)},
            )
        self.assertEqual(valid.status_code, 302)
        self.assertIn("/shift/1/start-checklist", valid.headers["Location"])
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(self.shift_rows()[0]["shift_type"], "Afternoon")
        self.assertEqual(self.shift_rows()[0]["scheduled_start_time"], "14:00")

    def test_same_type_candidates_are_selected_by_schedule_shift_id(self):
        self.allow_duplicate_schedule_types()
        first_id = self.add_schedule("Afternoon", "14:00", "18:00")
        second_id = self.add_schedule("Afternoon", "14:00", "19:00")
        self.authenticate()

        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302, response.data.decode())
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])

        confirmation = self.client.get(response.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn(f'value="{first_id}"'.encode(), confirmation.data)
        self.assertIn(f'value="{second_id}"'.encode(), confirmation.data)
        self.assertNotIn(b'name="shift_type"', confirmation.data)

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            selected = self.client.post(
                "/shift/sign-on/confirm",
                data={"schedule_shift_id": str(second_id)},
            )

        self.assertEqual(selected.status_code, 302)
        self.assertEqual(self.counts(), (1, 1))
        row = self.shift_rows()[0]
        self.assertEqual(row["shift_type"], "Afternoon")
        self.assertEqual(row["scheduled_start_time"], "14:00")
        self.assertEqual(row["scheduled_end_time"], "19:00")

    def test_confirmation_post_uses_new_single_authoritative_candidate(self):
        day_id = self.add_schedule("Day", "12:00", "16:00")
        afternoon_id = self.add_schedule("Afternoon", "14:00", "18:00")
        self.authenticate()

        response = self.client.get("/dashboard")
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])

        conn = self.open_database()
        try:
            conn.execute(
                "DELETE FROM schedule_staff WHERE schedule_shift_id = ?",
                (day_id,),
            )
            conn.execute(
                "DELETE FROM schedule_shifts WHERE schedule_shift_id = ?",
                (day_id,),
            )
            conn.commit()
        finally:
            conn.close()

        with mock.patch.object(
            app,
            "reconcile_staff_notice_shift_sign_on",
        ):
            confirmed = self.client.post(
                "/shift/sign-on/confirm",
                data={"schedule_shift_id": str(day_id)},
            )
        self.assertEqual(confirmed.status_code, 302)
        self.assertIn("/shift/1/start-checklist", confirmed.headers["Location"])
        self.assertEqual(self.shift_rows()[0]["shift_type"], "Afternoon")
        self.assertEqual(
            self.shift_rows()[0]["scheduled_start_time"],
            "14:00",
        )
        self.assertEqual(afternoon_id, 2)

    def test_confirmation_rejects_candidate_that_becomes_unavailable(self):
        day_id = self.add_schedule("Day", "12:00", "16:00")
        self.add_schedule("Afternoon", "14:00", "18:00")
        self.authenticate()

        response = self.client.get("/dashboard")
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])

        conn = self.open_database()
        try:
            conn.execute(
                "DELETE FROM schedule_staff WHERE schedule_shift_id = ?",
                (day_id,),
            )
            conn.execute(
                "DELETE FROM schedule_shifts WHERE schedule_shift_id = ?",
                (day_id,),
            )
            conn.execute(
                "DELETE FROM schedule_staff WHERE schedule_shift_id = 2"
            )
            conn.execute(
                "DELETE FROM schedule_shifts WHERE schedule_shift_id = 2"
            )
            conn.commit()
        finally:
            conn.close()

        rejected = self.client.post(
            "/shift/sign-on/confirm",
            data={"schedule_shift_id": str(day_id)},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.counts(), (0, 0))

    def test_confirmation_post_rejects_new_active_assignment(self):
        self.authenticate()
        response = self.client.get("/dashboard")
        self.assertIn("/shift/sign-on/confirm", response.headers["Location"])

        conn = self.open_database()
        try:
            shift_id = conn.execute("""
                INSERT INTO shifts
                (client_id, shift_date, shift_type, status)
                VALUES (10, '2026-08-03', 'Day', 'Open')
            """).lastrowid
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, 2, '12:00', 1)
            """, (shift_id,))
            conn.commit()
        finally:
            conn.close()

        rejected = self.client.post(
            "/shift/sign-on/confirm",
            data={"shift_type": "Afternoon"},
        )
        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(self.counts(), (1, 1))


if __name__ == "__main__":
    unittest.main()
