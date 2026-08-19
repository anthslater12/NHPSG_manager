import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

import add_behaviour_occurrences_table
import add_food_fluid_entries_table
import add_shift_activities_table
import app


class PostShiftDocumentationPhase3Tests(unittest.TestCase):

    NOW = datetime(2026, 8, 6, 18, 59, 59, tzinfo=timezone.utc)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "phase-3.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.create_database()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def create_database(self):
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                password_hash TEXT,
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
                status TEXT NOT NULL,
                scheduled_start_time TEXT,
                scheduled_end_time TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT,
                actual_end_at_utc TEXT,
                sign_on_at TEXT,
                sign_off_at TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE UNIQUE INDEX idx_shifts_shift_client
                ON shifts(shift_id, client_id);
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
                success INTEGER DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT
            );
            CREATE TABLE sleep_events (
                sleep_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                shift_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_datetime TEXT NOT NULL,
                recorded_by_user_id INTEGER NOT NULL,
                note TEXT
            );
            CREATE TABLE toileting_events (
                toileting_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                client_id INTEGER NOT NULL,
                recorded_by_user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_datetime TEXT NOT NULL,
                location TEXT NOT NULL,
                location_other TEXT,
                bm_size TEXT,
                bm_consistency TEXT,
                bm_unusual_details TEXT,
                urine_volume TEXT,
                urine_unusual_details TEXT,
                behaviour_before TEXT,
                behaviour_during TEXT,
                behaviour_after TEXT,
                behaviour_comments TEXT,
                general_comments TEXT
            );
            INSERT INTO users
                (user_id, username, password_hash, full_name, role)
            VALUES
                (1, 'worker', 'x', 'Worker One', 'Support Worker'),
                (2, 'other', 'x', 'Worker Two', 'Support Worker'),
                (3, 'manager', 'x', 'Manager', 'Program Manager');
            INSERT INTO clients (client_id, client_name)
            VALUES (1, 'Client One'), (2, 'Client Two');
            INSERT INTO shifts
                (shift_id, client_id, shift_date, shift_type, status,
                 scheduled_start_time, scheduled_end_time)
            VALUES
                (10, 1, '2026-08-06', 'Day', 'Open', '07:00', '15:00'),
                (11, 2, '2026-08-06', 'Day', 'Closed', '07:00', '15:00'),
                (12, 1, '2026-08-06', 'Day', 'Cancelled', '07:00', '15:00');
            INSERT INTO shift_staff
                (shift_staff_id, shift_id, user_id, actual_start_time,
                 actual_end_at_utc, sign_on_at, sign_off_at, active)
            VALUES
                (10, 10, 1, '07:00', NULL, '2026-08-06T07:00:00Z', NULL, 1),
                (11, 11, 1, '07:00', '2026-08-06T15:00:00Z',
                 '2026-08-06T07:00:00Z', '2026-08-06T15:00:00Z', 0),
                (12, 12, 1, '07:00', '2026-08-06T15:00:00Z',
                 '2026-08-06T07:00:00Z', '2026-08-06T15:00:00Z', 0),
                (13, 11, 2, '07:00', '2026-08-06T15:00:00Z',
                 '2026-08-06T07:00:00Z', '2026-08-06T15:00:00Z', 0);
        """)
        add_food_fluid_entries_table.migrate(conn)
        add_shift_activities_table.migrate(conn)
        add_behaviour_occurrences_table.migrate(conn)
        conn.commit()
        conn.close()

    def login(self, shift_id=11, role="Support Worker"):
        user_id = 1 if role == "Support Worker" else 3
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = "Worker One"
            if shift_id is not None:
                session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = shift_id

    def db(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def now(self):
        return mock.patch.object(app, "get_application_now_utc", return_value=self.NOW)

    def toileting_data(self, location, location_other=None):
        return {
            "event_type": "BM",
            "event_datetime": "2026-08-06T10:00",
            "location": location,
            "location_other": location_other or "",
            "bm_size": "Medium",
            "bm_consistency": "Firm",
            "bm_unusual": "No",
        }

    def test_toileting_predefined_location_clears_stale_custom_value(self):
        self.login(shift_id=10)
        with self.now():
            response = self.client.post(
                "/shift/10/toileting-event/new",
                data=self.toileting_data("Bathroom", "Should be ignored"),
            )
        self.assertEqual(response.status_code, 302)
        row = self.db().execute(
            "SELECT location, location_other FROM toileting_events"
        ).fetchone()
        self.assertEqual(tuple(row), ("Bathroom", None))

    def test_toileting_other_requires_and_saves_custom_location(self):
        self.login(shift_id=10)
        with self.now():
            missing = self.client.post(
                "/shift/10/toileting-event/new",
                data=self.toileting_data("Other", "   "),
            )
        self.assertEqual(missing.status_code, 200)
        self.assertIn(b"Enter a custom location", missing.data)
        self.assertIn(b'name="location_other"', missing.data)

        with self.now():
            saved = self.client.post(
                "/shift/10/toileting-event/new",
                data=self.toileting_data("Other", "Community Centre"),
            )
        self.assertEqual(saved.status_code, 302)
        row = self.db().execute(
            "SELECT location, location_other FROM toileting_events"
        ).fetchone()
        self.assertEqual(tuple(row), ("Other", "Community Centre"))

    def test_toileting_other_rejects_overlong_custom_location(self):
        self.login(shift_id=10)
        with self.now():
            response = self.client.post(
                "/shift/10/toileting-event/new",
                data=self.toileting_data("Other", "x" * 201),
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"200 characters or fewer", response.data)

    def test_toileting_location_formatter_handles_custom_and_legacy_values(self):
        self.assertEqual(
            app.format_toileting_location("Bathroom", None), "Bathroom"
        )
        self.assertEqual(
            app.format_toileting_location("Other", "Community Centre"),
            "Other — Community Centre",
        )
        self.assertEqual(
            app.format_toileting_location("Other", None), "Other"
        )

    def test_all_integrated_get_pages_show_selected_previous_context(self):
        self.login()
        with self.now():
            routes = (
                "/shift/11/sleep",
                "/shift/11/food-fluid/new",
                "/shift/11/toileting-event/new",
                "/shift/11/activity",
                "/shift/11/behaviour",
            )
            for route in routes:
                with self.subTest(route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b"Documentation context:", response.data)
                    self.assertIn(b"Previous Day Shift", response.data)
                    self.assertIn(b"Client Two", response.data)
                    self.assertIn(b"Change documentation shift", response.data)

    def test_all_integrated_get_pages_allow_selected_active_context(self):
        self.login(shift_id=10)
        with self.now():
            routes = (
                "/shift/10/sleep",
                "/shift/10/food-fluid/new",
                "/shift/10/toileting-event/new",
                "/shift/10/activity",
                "/shift/10/behaviour",
            )
            for route in routes:
                with self.subTest(route=route):
                    response = self.client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b"Documentation context:", response.data)
                    self.assertIn(b"Current Day Shift", response.data)
                    self.assertIn(b"Client One", response.data)

    def test_missing_previous_context_cannot_authorize_a_post(self):
        self.login(shift_id=None)
        with self.now():
            response = self.client.post("/shift/11/sleep", data={
                "event_type": "woke_up",
                "event_local": "2026-08-06T08:00",
            })
        self.assertNotIn(response.status_code, (200, 201, 302))
        conn = self.db()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM sleep_events").fetchone()[0],
            0
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0],
            0
        )
        conn.close()

    def test_each_module_save_works_with_active_selected_context(self):
        self.login(shift_id=10)
        with self.now():
            self.client.post("/shift/10/sleep", data={
                "event_type": "woke_up",
                "event_local": "2026-08-06T08:30",
                "note": "active sleep",
            })
            self.client.post("/shift/10/food-fluid/new", data={
                "event_local": "2026-08-06T09:30",
                "interaction_type": "Offered",
                "item_description": "Active breakfast",
                "outcome": "All consumed",
            })
            self.client.post("/shift/10/toileting-event/new", data={
                "event_type": "BM",
                "event_datetime": "2026-08-06T10:30",
                "location": "Bathroom",
                "bm_size": "Small",
                "bm_consistency": "Firm",
                "bm_unusual": "No",
            })
            self.client.post("/shift/10/activity", data={
                "start_time": "11:30",
                "end_time": "12:30",
                "activity_description": "Active walk",
                "a_selected": "1",
            })
            self.client.post("/shift/10/behaviour", data={
                "occurrence_local": "2026-08-06T13:30",
                "submission_token": "C" * 43,
                "self_harm": "1",
                "notes": "Active context behaviour",
            })

        conn = self.db()
        expected = {
            "sleep_events": ("event_type", "woke_up", "note", "active sleep"),
            "food_fluid_entries": (
                "item_description", "Active breakfast", "outcome", "All consumed"
            ),
            "toileting_events": ("location", "Bathroom", "bm_size", "Small"),
            "shift_activities": (
                "activity_description", "Active walk", "recorded_by_user_id", 1
            ),
            "behaviour_occurrences": (
                "notes", "Active context behaviour", "recorded_by_user_id", 1
            ),
        }
        for table, (field_one, value_one, field_two, value_two) in expected.items():
            with self.subTest(table=table):
                if table == "shift_activities":
                    row = conn.execute(
                        f"SELECT sa.shift_id, s.client_id, "
                        f"sa.recorded_by_user_id, sa.{field_one}, "
                        f"sa.{field_two} FROM shift_activities sa "
                        "JOIN shifts s ON s.shift_id = sa.shift_id "
                        "WHERE sa.shift_id = 10 "
                        "ORDER BY sa.shift_activity_id DESC LIMIT 1"
                    ).fetchone()
                else:
                    row = conn.execute(
                        f"SELECT shift_id, client_id, recorded_by_user_id, "
                        f"{field_one}, {field_two} FROM {table} "
                        "WHERE shift_id = 10 ORDER BY 1 DESC LIMIT 1"
                    ).fetchone()
                self.assertEqual(tuple(row[:3]), (10, 1, 1))
                self.assertEqual(row[3], value_one)
                self.assertEqual(row[4], value_two)
        self.assertEqual(
            conn.execute(
                "SELECT event_datetime FROM sleep_events WHERE shift_id = 10"
            ).fetchone()[0],
            "2026-08-06T15:30:00Z"
        )
        self.assertEqual(
            conn.execute(
                "SELECT event_at_utc FROM food_fluid_entries WHERE shift_id = 10"
            ).fetchone()[0],
            "2026-08-06T16:30:00Z"
        )
        self.assertEqual(
            conn.execute(
                "SELECT event_datetime FROM toileting_events WHERE shift_id = 10"
            ).fetchone()[0],
            "2026-08-06T10:30"
        )
        self.assertEqual(
            tuple(conn.execute(
                "SELECT start_time, end_time FROM shift_activities "
                "WHERE shift_id = 10"
            ).fetchone()),
            ("11:30", "12:30")
        )
        self.assertEqual(
            conn.execute(
                "SELECT occurred_at_utc FROM behaviour_occurrences "
                "WHERE shift_id = 10"
            ).fetchone()[0],
            "2026-08-06T20:30:00Z"
        )
        logs = conn.execute(
            "SELECT shift_id, client_id, user_id, related_table, related_id, "
            "storyline_visible FROM activity_log ORDER BY activity_id"
        ).fetchall()
        self.assertEqual(len(logs), 5)
        self.assertTrue(all(tuple(row[:3]) == (10, 1, 1) for row in logs))
        self.assertTrue(all(row[3] in expected for row in logs))
        self.assertTrue(all(row[5] == 1 for row in logs))
        source_ids = {
            "sleep_events": conn.execute(
                "SELECT sleep_event_id FROM sleep_events WHERE shift_id = 10"
            ).fetchone()[0],
            "food_fluid_entries": conn.execute(
                "SELECT food_fluid_entry_id FROM food_fluid_entries WHERE shift_id = 10"
            ).fetchone()[0],
            "toileting_events": conn.execute(
                "SELECT toileting_event_id FROM toileting_events WHERE shift_id = 10"
            ).fetchone()[0],
            "shift_activities": conn.execute(
                "SELECT shift_activity_id FROM shift_activities WHERE shift_id = 10"
            ).fetchone()[0],
            "behaviour_occurrences": conn.execute(
                "SELECT behaviour_occurrence_id FROM behaviour_occurrences WHERE shift_id = 10"
            ).fetchone()[0],
        }
        self.assertEqual(
            {(row[3], row[4]) for row in logs},
            set(source_ids.items())
        )
        conn.close()

    def test_selected_context_validation_failures_write_no_source_or_log(self):
        self.login(shift_id=11)
        with self.now():
            self.client.post("/shift/11/sleep", data={
                "event_type": "woke_up", "event_local": ""
            })
            self.client.post("/shift/11/food-fluid/new", data={})
            self.client.post("/shift/11/toileting-event/new", data={
                "event_type": "BM", "event_datetime": "", "location": ""
            })
            self.client.post("/shift/11/activity", data={
                "start_time": "10:00", "end_time": "09:00",
                "activity_description": "", "a_selected": ""
            })
            self.client.post("/shift/11/behaviour", data={
                "occurrence_local": "",
                "submission_token": "D" * 43,
            })
        conn = self.db()
        for table in (
            "sleep_events", "food_fluid_entries", "toileting_events",
            "shift_activities", "behaviour_occurrences"
        ):
            with self.subTest(table=table):
                self.assertEqual(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    0
                )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0],
            0
        )
        conn.close()

    def test_each_module_save_uses_selected_shift_client_and_worker(self):
        self.login()
        with self.now():
            self.client.post("/shift/11/sleep", data={
                "event_type": "woke_up",
                "event_local": "2026-08-06T08:00",
                "note": "previous sleep",
            })
            self.client.post("/shift/11/food-fluid/new", data={
                "event_local": "2026-08-06T09:00",
                "interaction_type": "Offered",
                "item_description": "Breakfast",
                "outcome": "All consumed",
            })
            self.client.post("/shift/11/toileting-event/new", data={
                "event_type": "BM",
                "event_datetime": "2026-08-06T10:00",
                "location": "Bathroom",
                "bm_size": "Medium",
                "bm_consistency": "Firm",
                "bm_unusual": "No",
            })
            self.client.post("/shift/11/activity", data={
                "start_time": "11:00",
                "end_time": "12:00",
                "activity_description": "Walk",
                "a_selected": "1",
            })
            self.client.post("/shift/11/behaviour", data={
                "occurrence_local": "2026-08-06T13:00",
                "submission_token": "B" * 43,
                "self_harm": "1",
                "notes": "Previous context behaviour",
            })

        conn = self.db()
        tables = (
            "sleep_events", "food_fluid_entries", "toileting_events",
            "shift_activities", "behaviour_occurrences"
        )
        for table in tables:
            with self.subTest(table=table):
                if table == "shift_activities":
                    row = conn.execute("""
                        SELECT sa.shift_id, s.client_id, sa.recorded_by_user_id
                        FROM shift_activities sa
                        JOIN shifts s ON s.shift_id = sa.shift_id
                        ORDER BY sa.shift_activity_id DESC LIMIT 1
                    """).fetchone()
                else:
                    row = conn.execute(
                        f"SELECT shift_id, client_id, recorded_by_user_id "
                        f"FROM {table} ORDER BY 1 DESC LIMIT 1"
                    ).fetchone()
                self.assertEqual(tuple(row), (11, 2, 1))
        self.assertEqual(
            conn.execute(
                "SELECT event_datetime FROM sleep_events WHERE shift_id = 11"
            ).fetchone()[0],
            "2026-08-06T15:00:00Z"
        )
        self.assertEqual(
            conn.execute(
                "SELECT event_at_utc FROM food_fluid_entries WHERE shift_id = 11"
            ).fetchone()[0],
            "2026-08-06T16:00:00Z"
        )
        self.assertEqual(
            conn.execute(
                "SELECT event_datetime FROM toileting_events WHERE shift_id = 11"
            ).fetchone()[0],
            "2026-08-06T10:00"
        )
        self.assertEqual(
            tuple(conn.execute(
                "SELECT start_time, end_time FROM shift_activities "
                "WHERE shift_id = 11"
            ).fetchone()),
            ("11:00", "12:00")
        )
        self.assertEqual(
            conn.execute(
                "SELECT occurred_at_utc FROM behaviour_occurrences "
                "WHERE shift_id = 11"
            ).fetchone()[0],
            "2026-08-06T20:00:00Z"
        )
        logs = conn.execute("""
            SELECT shift_id, client_id, user_id, related_table, related_id
            FROM activity_log
            ORDER BY activity_id
        """).fetchall()
        self.assertEqual(len(logs), 5)
        self.assertTrue(all(tuple(row)[:3] == (11, 2, 1) for row in logs))
        self.assertTrue(all(row[3] in tables for row in logs))
        source_ids = {
            "sleep_events": conn.execute(
                "SELECT sleep_event_id FROM sleep_events WHERE shift_id = 11"
            ).fetchone()[0],
            "food_fluid_entries": conn.execute(
                "SELECT food_fluid_entry_id FROM food_fluid_entries WHERE shift_id = 11"
            ).fetchone()[0],
            "toileting_events": conn.execute(
                "SELECT toileting_event_id FROM toileting_events WHERE shift_id = 11"
            ).fetchone()[0],
            "shift_activities": conn.execute(
                "SELECT shift_activity_id FROM shift_activities WHERE shift_id = 11"
            ).fetchone()[0],
            "behaviour_occurrences": conn.execute(
                "SELECT behaviour_occurrence_id FROM behaviour_occurrences WHERE shift_id = 11"
            ).fetchone()[0],
        }
        self.assertEqual(
            {(row[3], row[4]) for row in logs},
            set(source_ids.items())
        )
        conn.close()

    def test_stale_form_cannot_switch_to_current_context_or_write(self):
        self.login(shift_id=11)
        with self.now():
            self.assertEqual(
                self.client.get("/shift/11/sleep").status_code, 200
            )
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = 10

        with self.now():
            response = self.client.post("/shift/11/sleep", data={
                "event_type": "woke_up",
                "event_local": "2026-08-06T08:00",
            })
        self.assertEqual(response.status_code, 302)
        self.assertIn("documentation-context", response.location)
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sleep_events").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0], 0)
        self.assertEqual(
            tuple(conn.execute(
                "SELECT status, client_id FROM shifts WHERE shift_id = 11"
            ).fetchone()),
            ("Closed", 2)
        )
        conn.close()

    def test_invalid_cancelled_other_worker_and_expired_contexts_fail_safely(self):
        self.login(shift_id=12)
        with self.now():
            self.assertEqual(
                self.client.get("/shift/12/sleep").status_code, 302
            )
        self.login(shift_id=999)
        with self.now():
            self.assertEqual(
                self.client.get("/shift/999/activity").status_code, 302
            )
        self.login(shift_id=11)
        with mock.patch.object(
            app, "get_application_now_utc",
            return_value=datetime(2026, 8, 6, 19, 0, 1, tzinfo=timezone.utc)
        ):
            self.assertEqual(
                self.client.get("/shift/11/behaviour").status_code, 302
            )
        self.login(shift_id=11, role="Program Manager")
        self.assertEqual(self.client.get("/shift/11/sleep").status_code, 403)

    def test_malformed_session_context_fails_without_authorizing_a_write(self):
        self.login(shift_id=None)
        with self.client.session_transaction() as session:
            session[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = "not-a-shift-id"
        with self.now():
            response = self.client.get("/shift/10/food-fluid/new")
        self.assertEqual(response.status_code, 302)
        self.assertIn("documentation-context", response.location)
        with self.client.session_transaction() as session:
            self.assertNotIn(app.DOCUMENTATION_CONTEXT_SESSION_KEY, session)
        conn = self.db()
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM food_fluid_entries").fetchone()[0],
            0
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0],
            0
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
