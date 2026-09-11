import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime

import add_food_fluid_entries_table
import app


class FoodFluidReportingTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "food-fluid-report.db")
        conn = sqlite3.connect(app.DB_NAME)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT, password_hash TEXT, full_name TEXT,
                role TEXT, active INTEGER NOT NULL
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
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
                activity_datetime TEXT DEFAULT CURRENT_TIMESTAMP,
                activity_class TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                user_id INTEGER,
                client_id INTEGER,
                shift_id INTEGER,
                related_table TEXT,
                related_id INTEGER,
                summary TEXT NOT NULL,
                details TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                success INTEGER DEFAULT 1,
                storyline_visible INTEGER NOT NULL DEFAULT 0,
                event_datetime TEXT NULL
            );
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT,
                comment TEXT,
                acknowledgement_type TEXT DEFAULT 'Read',
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE management_notes (
                management_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                note_text TEXT NOT NULL,
                visibility TEXT NOT NULL,
                created_by_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                shared_at TEXT,
                shared_by_user_id INTEGER
            );
            CREATE TABLE action_items (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT,
                source_id INTEGER,
                title TEXT,
                description TEXT,
                status TEXT,
                priority TEXT,
                assigned_to_user_id INTEGER,
                created_by_user_id INTEGER,
                created_at TEXT
            );
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Admin', 'Admin', 1),
                (2, 'director', 'hash', 'Director', 'Director', 1),
                (3, 'manager', 'hash', 'Manager', 'Program Manager', 1),
                (4, 'consultant', 'hash', 'Consultant', 'Behaviour Consultant', 1),
                (5, 'worker', 'hash', 'Worker', 'Support Worker', 1),
                (6, 'inactive', 'hash', 'Inactive', 'Admin', 0);
            INSERT INTO clients VALUES
                (1, 'Active Client', 1),
                (2, 'Other Client', 0),
                (3, 'Second Active Client', 1);
            INSERT INTO shifts VALUES
                (10, 1, '2026-08-01', 'Day', 'Open'),
                (11, 1, '2026-08-01', 'Afternoon', 'Open'),
                (12, 1, '2026-08-01', 'Overnight', 'Open'),
                (13, 1, '2026-08-01', 'Unexpected', 'Open'),
                (20, 2, '2026-08-01', 'Day', 'Open'),
                (30, 3, '2026-08-01', 'Day', 'Open');
        """)
        add_food_fluid_entries_table.migrate(conn)
        conn.execute("UPDATE clients SET active = 0 WHERE client_id = 3")
        conn.commit()
        conn.close()
        self.client = app.app.test_client()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, session_role="Admin"):
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                role=session_role,
                full_name="Untrusted Session User",
            )

    def insert_entry(
        self, entry_id, local_date, local_time="10:00", *, shift_id=10,
        client_id=1, interaction="Offered", item="Toast", outcome="All consumed",
        thrown=0, details=None, status="Recorded", void_reason=None,
        recorded_by=5,
    ):
        local = datetime.strptime(
            f"{local_date} {local_time}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=app.VANCOUVER_TIMEZONE)
        event_utc = app.serialize_behaviour_utc(local)
        conn = sqlite3.connect(app.DB_NAME)
        try:
            if status == "Voided":
                conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("""
                INSERT INTO food_fluid_entries (
                    food_fluid_entry_id, shift_id, client_id,
                    recorded_by_user_id, event_at_utc, interaction_type,
                    item_description, outcome, physically_thrown,
                    additional_details, submitted_at_utc, submission_token,
                    status, voided_by_user_id, voided_at_utc, void_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry_id, shift_id, client_id, recorded_by, event_utc,
                interaction, item, outcome, thrown, details, event_utc,
                f"token-{entry_id}", status,
                1 if status == "Voided" else None,
                "2026-08-02T20:00:00Z" if status == "Voided" else None,
                void_reason if status == "Voided" else None,
            ))
            conn.commit()
        finally:
            conn.close()

    def report(self, query=""):
        return self.client.get("/reports/food-fluid" + query)

    def report_context(
        self, from_date="2026-08-01", to_date="2026-08-01", group_by="Daily"
    ):
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            return app._food_fluid_report_context(
                conn, 1, date.fromisoformat(from_date),
                date.fromisoformat(to_date), group_by,
            )
        finally:
            conn.close()

    def test_access_is_database_backed_for_all_reporting_roles(self):
        for user_id, role in (
            (1, "Admin"), (2, "Director"), (3, "Program Manager"),
            (4, "Behaviour Consultant"),
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 200)

        for user_id, role in ((5, "Support Worker"), (6, "Admin"), (5, "Admin")):
            with self.subTest(user_id=user_id, role=role):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 403)

    def test_active_client_is_required_and_rows_are_explicitly_scoped(self):
        self.insert_entry(1, "2026-08-01", item="Included")
        self.insert_entry(2, "2026-08-01", client_id=2, shift_id=20, item="Secret")
        self.login()
        page = self.report("?from_date=2026-08-01&to_date=2026-08-01").data.decode()
        self.assertIn("Total Recorded Entries</dt><dd>1", page)
        self.assertIn("Included", page)
        self.assertNotIn("Secret", page)

        conn = sqlite3.connect(app.DB_NAME)
        try:
            conn.execute("UPDATE clients SET active = 1 WHERE client_id = 3")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.report().status_code, 400)

    def test_local_date_boundaries_use_event_time_not_shift_date(self):
        self.insert_entry(1, "2026-07-31", "23:59", item="Outside before range")
        self.insert_entry(2, "2026-08-01", "00:00", shift_id=13, item="Exact start")
        self.insert_entry(3, "2026-08-01", "23:59", item="Exact end")
        self.insert_entry(4, "2026-08-02", "00:00", item="Outside after range")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn("Total Recorded Entries</dt><dd>2", page)
        self.assertIn("Exact start", page)
        self.assertIn("Exact end", page)
        self.assertNotIn("Outside before range", page)
        self.assertNotIn("Outside after range", page)
        self.assertIn("Unassigned / Invalid Shift Link", page)

    def test_recorded_metrics_exclude_voided_rows_but_detail_retains_them(self):
        self.insert_entry(1, "2026-08-01", outcome="Refused")
        self.insert_entry(
            2, "2026-08-01", interaction="Requested",
            outcome="Partially consumed", thrown=1,
        )
        self.insert_entry(
            3, "2026-08-01", outcome="Refused", thrown=1, status="Voided",
            item="Voided item", void_reason="Duplicate entry",
        )
        report = self.report_context()
        summary = report["summary"]
        self.assertEqual(summary["total_recorded"], 2)
        self.assertEqual(summary["days"], 1)
        self.assertEqual(summary["refused"], 1)
        self.assertEqual(summary["physically_thrown"], 1)
        self.assertEqual(summary["voided"], 1)
        self.assertEqual(sum(summary["shifts"].values()), 2)
        self.assertEqual(sum(summary["interactions"].values()), 2)
        self.assertEqual(sum(summary["outcomes"].values()), 2)
        self.assertEqual(report["shift_series"][0]["count"], 2)
        self.login()
        page = self.report("?from_date=2026-08-01&to_date=2026-08-01").data.decode()
        self.assertIn("Voided Entries</dt><dd>1", page)
        self.assertIn("Voided item", page)
        self.assertIn("<td>Voided</td>", page)

    def test_actual_shifts_override_clock_and_invalid_links_reconcile(self):
        self.insert_entry(1, "2026-08-01", "23:00", shift_id=10)
        self.insert_entry(2, "2026-08-01", "07:00", shift_id=11)
        self.insert_entry(3, "2026-08-01", "12:00", shift_id=12)
        self.insert_entry(4, "2026-08-01", "14:00", shift_id=13)
        self.insert_entry(5, "2026-08-01", "15:00", shift_id=20)
        report = self.report_context()
        self.assertEqual(
            report["summary"]["shifts"],
            {"Day": 1, "Afternoon": 1, "Overnight": 1,
             "Unassigned / Invalid Shift Link": 2},
        )
        self.assertEqual(
            report["shift_series"][0]["count"],
            sum(report["shift_series"][0][key] for key in (
                "day_count", "afternoon_count", "overnight_count",
                "unassigned_count",
            )),
        )

    def test_interaction_outcome_and_throwing_series_reconcile(self):
        self.insert_entry(1, "2026-08-01", interaction="Offered", outcome="All consumed")
        self.insert_entry(2, "2026-08-01", interaction="Requested", outcome="Partially consumed")
        self.insert_entry(3, "2026-08-01", interaction="Requested", outcome="Refused", thrown=1)
        self.insert_entry(4, "2026-08-01", interaction="Requested", outcome="Item not available")
        report = self.report_context()
        summary = report["summary"]
        self.assertEqual(summary["interactions"], {"Offered": 1, "Requested": 3})
        self.assertEqual(summary["outcomes"], {
            "All consumed": 1, "Partially consumed": 1,
            "Refused": 1, "Item not available": 1,
        })
        self.assertEqual(summary["physically_thrown_breakdown"], {"Yes": 1, "No": 3})
        for item in report["trend"]:
            self.assertEqual(item["total_recorded"], sum(item["interactions"].values()))
            self.assertEqual(item["total_recorded"], sum(item["outcomes"].values()))

    def test_all_groupings_are_local_and_zero_filled(self):
        self.insert_entry(1, "2026-08-01")
        self.insert_entry(2, "2026-08-05", interaction="Requested")
        self.insert_entry(3, "2026-09-01")
        self.insert_entry(4, "2027-01-01", interaction="Requested")

        daily = self.report_context("2026-08-01", "2026-08-05")
        self.assertEqual([item["count"] for item in daily["shift_series"]], [1, 0, 0, 0, 1])

        weekly = self.report_context("2026-08-01", "2026-08-31", "Weekly")
        self.assertEqual(weekly["shift_series"][0]["period"], "2026-07-27")
        self.assertEqual(weekly["shift_series"][1]["period"], "2026-08-03")
        self.assertEqual(weekly["shift_series"][0]["count"], 1)
        self.assertEqual(weekly["shift_series"][2]["count"], 0)

        monthly = self.report_context("2026-08-01", "2026-12-31", "Monthly")
        self.assertEqual([item["period"] for item in monthly["shift_series"]], [
            "2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01", "2026-12-01"
        ])
        self.assertEqual(monthly["shift_series"][2]["count"], 0)

        annual = self.report_context("2026-01-01", "2027-12-31", "Annual")
        self.assertEqual([item["period"] for item in annual["shift_series"]], [
            "2026-01-01", "2027-01-01"
        ])
        self.assertEqual(annual["shift_series"][0]["count"], 3)
        self.assertEqual(annual["shift_series"][1]["count"], 1)

    def test_empty_range_is_safe_and_contains_zero_periods(self):
        self.login()
        response = self.report(
            "?from_date=2026-09-01&to_date=2026-09-03&group_by=Daily"
        )
        page = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Total Recorded Entries</dt><dd>0", page)
        self.assertIn("Days With Entries</dt><dd>0", page)
        report = self.report_context("2026-09-01", "2026-09-03")
        self.assertEqual([item["count"] for item in report["shift_series"]], [0, 0, 0])
        self.assertEqual(report["summary"]["physically_thrown"], 0)
        self.assertNotIn("volume", page.lower())
        self.assertNotIn("hydration", page.lower())

    def test_controls_labels_narratives_and_detail_route(self):
        self.insert_entry(
            1, "2026-08-01", item="Toast <script>alert(1)</script>",
            details="Observed <b>carefully</b>",
        )
        self.login()
        self.assertEqual(self.report("?group_by=Hourly").status_code, 400)
        self.assertEqual(
            self.report("?from_date=2026-08-02&to_date=2026-08-01").status_code,
            400,
        )
        page = self.report("?from_date=2026-08-01&to_date=2026-08-01").data.decode()
        self.assertIn("Food &amp; Fluid Entries Over Time", page)
        self.assertIn("Interaction Type Over Time", page)
        self.assertIn("Outcome Over Time", page)
        self.assertIn("Toast &lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("Observed &lt;b&gt;carefully&lt;/b&gt;", page)
        self.assertNotIn("item_description", page)
        self.assertNotIn("additional_details", page)
        self.assertNotIn("millilitre", page.lower())
        self.assertIn('href="/manager-review/food-fluid/1"', page)
        self.assertEqual(self.client.get("/manager-review/food-fluid/1").status_code, 200)

    def test_reports_landing_page_keeps_all_report_links(self):
        self.login()
        page = self.client.get("/reports")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'href="/reports/behaviour"', page.data)
        self.assertIn(b'href="/reports/activities"', page.data)
        self.assertIn(b'href="/reports/sleep"', page.data)
        self.assertIn(b'href="/reports/food-fluid"', page.data)
        self.assertIn(b"Open Food &amp; Fluid Report", page.data)


if __name__ == "__main__":
    unittest.main()
