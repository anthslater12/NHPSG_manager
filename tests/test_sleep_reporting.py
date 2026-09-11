import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime

import add_sleep_events_note
import add_sleep_events_table
import app


class SleepReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "sleep-reporting.db")
        self.addCleanup(self.cleanup)

        conn = sqlite3.connect(app.DB_NAME)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                password_hash TEXT,
                full_name TEXT,
                role TEXT,
                active INTEGER NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT,
                active INTEGER NOT NULL
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                status TEXT NOT NULL
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
                event_datetime TEXT NULL
            );
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                acknowledgement_type TEXT DEFAULT 'Read',
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Admin', 'Admin', 1),
                (2, 'director', 'hash', 'Director', 'Director', 1),
                (3, 'manager', 'hash', 'Manager', 'Program Manager', 1),
                (4, 'consultant', 'hash', 'Consultant', 'Behaviour Consultant', 1),
                (5, 'worker', 'hash', 'Worker', 'Support Worker', 1),
                (6, 'inactive', 'hash', 'Inactive', 'Admin', 0);
            INSERT INTO clients VALUES
                (1, 'Client One', 1),
                (2, 'Other Client', 0);
            INSERT INTO shifts VALUES
                (10, 1, '2026-08-01', 'Day', 'Open'),
                (11, 1, '2026-08-01', 'Afternoon', 'Open'),
                (12, 1, '2026-08-01', 'Overnight', 'Open'),
                (13, 1, '2026-07-31', 'Day', 'Open'),
                (20, 2, '2026-08-01', 'Day', 'Open');
        """)
        add_sleep_events_table.migrate(conn)
        add_sleep_events_note.migrate(conn)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, session_role=None):
        roles = {
            1: "Admin",
            2: "Director",
            3: "Program Manager",
            4: "Behaviour Consultant",
            5: "Support Worker",
            6: "Admin",
        }
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                role=session_role or roles[user_id],
                full_name="Untrusted Session User",
            )

    def insert_event(
        self,
        event_id,
        local_date,
        local_time="10:00",
        event_type="fell_asleep",
        client_id=1,
        shift_id=10,
        note=None,
    ):
        local = datetime.strptime(
            f"{local_date} {local_time}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=app.VANCOUVER_TIMEZONE)
        event_utc = app.serialize_behaviour_utc(local)
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("""
            INSERT INTO sleep_events
                (sleep_event_id, client_id, shift_id, event_type,
                 event_datetime, recorded_by_user_id, note)
            VALUES (?, ?, ?, ?, ?, 1, ?)
        """, (event_id, client_id, shift_id, event_type, event_utc, note))
        conn.commit()
        conn.close()

    def report(self, query=""):
        return self.client.get("/reports/sleep" + query)

    def report_context(
        self, from_date="2026-08-01", to_date="2026-08-01", group_by="Daily"
    ):
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            return app._sleep_report_context(
                conn,
                1,
                date.fromisoformat(from_date),
                date.fromisoformat(to_date),
                group_by,
            )
        finally:
            conn.close()

    def test_reporting_roles_are_allowed_and_unauthorized_users_are_denied(self):
        for user_id, role in (
            (1, "Admin"),
            (2, "Director"),
            (3, "Program Manager"),
            (4, "Behaviour Consultant"),
        ):
            with self.subTest(role=role):
                self.login(user_id)
                self.assertEqual(self.report().status_code, 200)

        for user_id in (5, 6):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                self.assertEqual(self.report().status_code, 403)

    def test_reporting_authority_is_database_backed(self):
        self.login(1, session_role="Support Worker")
        self.assertEqual(self.report().status_code, 200)

        self.login(5, session_role="Admin")
        self.assertEqual(self.report().status_code, 403)

    def test_summary_counts_event_types_and_distinct_local_dates(self):
        self.insert_event(1, "2026-08-01", event_type="fell_asleep")
        self.insert_event(2, "2026-08-01", "23:59", event_type="woke_up")
        self.insert_event(3, "2026-08-02", "00:01", event_type="woke_up")
        report = self.report_context("2026-08-01", "2026-08-02")

        self.assertEqual(
            report["summary"],
            {
                "total": 3,
                "fell_asleep": 1,
                "woke_up": 2,
                "days": 2,
                "shifts": {
                    "Day": 3,
                    "Afternoon": 0,
                    "Overnight": 0,
                    "Unassigned / Invalid Shift Link": 0,
                },
            },
        )

    def test_local_date_boundaries_are_inclusive_and_ignore_shift_date(self):
        self.insert_event(1, "2026-07-31", "23:59")
        self.insert_event(2, "2026-08-01", "00:00", shift_id=13)
        self.insert_event(3, "2026-08-01", "23:59", event_type="woke_up")
        self.insert_event(4, "2026-08-02", "00:00")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01&group_by=Daily"
        ).data.decode()

        self.assertIn("Total Sleep Events</dt><dd>2", page)
        self.assertIn("<th scope=\"row\">2026-08-01</th>", page)
        self.assertIn("2026-08-01 00:00", page)
        self.assertNotIn("2026-07-31 23:59", page)
        self.assertNotIn("2026-08-02 00:00", page)

    def test_dst_transition_uses_vancouver_local_event_date_and_time(self):
        self.insert_event(1, "2026-03-08", "00:30")
        self.insert_event(2, "2026-03-08", "03:30", event_type="woke_up")
        self.login()
        response = self.report(
            "?from_date=2026-03-08&to_date=2026-03-08"
        )
        page = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Total Sleep Events</dt><dd>2", page)
        self.assertIn("2026-03-08 00:30", page)
        self.assertIn("2026-03-08 03:30", page)

    def test_all_groupings_use_local_calendar_periods_and_preserve_zeros(self):
        self.insert_event(1, "2026-08-01")
        self.insert_event(2, "2026-08-05", event_type="woke_up")
        self.insert_event(3, "2026-09-01")
        self.insert_event(4, "2027-01-01", event_type="woke_up")

        daily = self.report_context("2026-08-01", "2026-08-05")
        self.assertEqual(
            [item["fell_asleep_count"] + item["woke_up_count"] for item in daily["series"]],
            [1, 0, 0, 0, 1],
        )

        weekly = self.report_context("2026-08-01", "2026-08-31", "Weekly")
        self.assertEqual(weekly["series"][0]["period"], "2026-07-27")
        self.assertEqual(weekly["series"][0]["woke_up_count"], 0)
        self.assertEqual(weekly["series"][1]["woke_up_count"], 1)
        self.assertEqual(weekly["series"][2]["fell_asleep_count"], 0)

        monthly = self.report_context("2026-08-01", "2026-12-31", "Monthly")
        self.assertEqual([item["period"] for item in monthly["series"]], [
            "2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01", "2026-12-01"
        ])
        self.assertEqual(monthly["series"][2]["fell_asleep_count"], 0)

        annual = self.report_context("2026-01-01", "2027-12-31", "Annual")
        self.assertEqual([item["period"] for item in annual["series"]], [
            "2026-01-01", "2027-01-01"
        ])
        self.assertEqual(annual["series"][0]["fell_asleep_count"], 2)
        self.assertEqual(annual["series"][1]["woke_up_count"], 1)

    def test_actual_shift_breakdown_does_not_infer_from_event_clock_time(self):
        self.insert_event(1, "2026-08-01", "23:00", shift_id=10)
        self.insert_event(2, "2026-08-01", "07:00", shift_id=11)
        self.insert_event(3, "2026-08-01", "12:00", shift_id=12)
        self.insert_event(4, "2026-08-01", "14:00", shift_id=999)
        self.insert_event(5, "2026-08-01", "15:00", shift_id=20)
        self.login()
        page = self.report("?from_date=2026-08-01&to_date=2026-08-01").data.decode()

        self.assertIn("<dt>Day</dt><dd>1", page)
        self.assertIn("<dt>Afternoon</dt><dd>1", page)
        self.assertIn("<dt>Overnight</dt><dd>1", page)
        self.assertIn("<dt>Unassigned / Invalid Shift Link</dt><dd>2", page)
        self.assertIn("2026-08-01 23:00", page)
        self.assertIn("<td>Fell Asleep</td>", page)
        self.assertIn("<td>Day</td>", page)
        self.assertIn("Unassigned / Invalid Shift Link", page)

    def test_client_scope_excludes_other_client_records(self):
        self.insert_event(1, "2026-08-01", note="Visible client event")
        self.insert_event(
            2, "2026-08-01", client_id=2, shift_id=20,
            note="Other client secret", event_type="woke_up"
        )
        self.login()
        page = self.report("?from_date=2026-08-01&to_date=2026-08-01").data.decode()

        self.assertIn("Total Sleep Events</dt><dd>1", page)
        self.assertIn("Visible client event", page)
        self.assertNotIn("Other client secret", page)

    def test_timeline_retains_local_dates_duplicates_and_irregular_sequence(self):
        self.insert_event(1, "2026-08-01", "23:30", event_type="fell_asleep")
        self.insert_event(2, "2026-08-02", "00:30", event_type="woke_up")
        self.insert_event(3, "2026-08-02", "01:00", event_type="woke_up")
        self.insert_event(4, "2026-08-02", "01:00", event_type="woke_up")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-02"
        ).data.decode()

        self.assertIn("Sleep/Wake Timeline", page)
        for event_id in (1, 2, 3, 4):
            self.assertIn(f'data-event-id="{event_id}"', page)
        self.assertIn("2026-08-01 23:30", page)
        self.assertIn("2026-08-02 00:30", page)
        self.assertIn("2026-08-02 01:00", page)
        self.assertNotIn("fell_asleep", page)
        self.assertNotIn("woke_up", page)
        self.assertNotIn("Total Sleep Duration", page)
        self.assertNotIn("Average Sleep Duration", page)
        self.assertNotIn("<path", page)

    def test_empty_range_is_safe_and_keeps_zero_periods(self):
        self.login()
        response = self.report(
            "?from_date=2026-09-01&to_date=2026-09-03&group_by=Daily"
        )
        page = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Total Sleep Events</dt><dd>0", page)
        self.assertIn("Days With Sleep Events</dt><dd>0", page)
        self.assertIn('<th scope="row">2026-09-02</th>', page)
        self.assertIn("<td>0</td>", page)
        self.assertIn("No Sleep events were found for this period.", page)

    def test_controls_and_user_facing_labels_are_valid(self):
        self.login()
        self.assertEqual(
            self.report("?from_date=2026-08-02&to_date=2026-08-01").status_code,
            400,
        )
        self.assertEqual(self.report("?group_by=Hourly").status_code, 400)

        self.insert_event(1, "2026-08-01", event_type="fell_asleep")
        self.insert_event(2, "2026-08-01", event_type="woke_up")
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn("Fell Asleep", page)
        self.assertIn("Woke Up", page)
        self.assertIn('href="/manager-review/sleep/1"', page)
        self.assertIn('href="/manager-review/sleep/2"', page)
        self.assertNotIn("fell_asleep", page)
        self.assertNotIn("woke_up", page)

    def test_reports_landing_page_keeps_existing_reports_and_adds_sleep(self):
        self.login()
        page = self.client.get("/reports")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'href="/reports/behaviour"', page.data)
        self.assertIn(b'href="/reports/activities"', page.data)
        self.assertIn(b'href="/reports/sleep"', page.data)
        self.assertIn(b"Open Sleep Report", page.data)


if __name__ == "__main__":
    unittest.main()
