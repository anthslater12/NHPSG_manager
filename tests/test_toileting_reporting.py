import os
import sqlite3
import tempfile
import unittest
from datetime import date

import add_toileting_events_table
import app


class ToiletingReportingTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "toileting-report.db")
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
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT,
                acknowledgement_type TEXT DEFAULT 'Review',
                comment TEXT,
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
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                actual_start_time TEXT,
                actual_end_time TEXT,
                sign_on_at TEXT
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
                (2, 'Other Client', 0);
            INSERT INTO shifts VALUES
                (10, 1, '2026-08-01', 'Day', 'Open'),
                (11, 1, '2026-08-01', 'Afternoon', 'Open'),
                (12, 1, '2026-08-01', 'Overnight', 'Open'),
                (13, 1, '2026-08-01', 'Unexpected', 'Open'),
                (20, 2, '2026-08-01', 'Day', 'Open');
        """)
        add_toileting_events_table.migrate(conn)
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

    def insert_event(
        self, event_id, local_date="2026-08-01", local_time="10:00", *,
        shift_id=10, client_id=1, event_type="BM", location="Bathroom",
        location_other=None, bm_size="Medium", bm_consistency="Soft",
        urine_volume=None, active=1, correction_of_event_id=None,
        correction_reason=None, recorded_by=5, **extra
    ):
        values = {
            "bm_colour": None,
            "estimated_bristol_type": None,
            "bm_blood_observed": 0,
            "bm_mucus_observed": 0,
            "bm_unusual_colour": 0,
            "urine_colour": None,
            "urine_blood_observed": 0,
            "urine_strong_odour": 0,
            "urine_unusual_colour": 0,
            "pain_or_distress": 0,
            "other_concern": 0,
            "concern_details": None,
            "bm_unusual_details": None,
            "urine_unusual_details": None,
            "behaviour_before": None,
            "behaviour_during": None,
            "behaviour_after": None,
            "behaviour_comments": None,
            "general_comments": None,
        }
        values.update(extra)
        conn = sqlite3.connect(app.DB_NAME)
        try:
            conn.execute("""
                INSERT INTO toileting_events (
                    toileting_event_id, shift_id, client_id,
                    recorded_by_user_id, event_type, event_datetime,
                    location, location_other, bm_size, bm_consistency,
                    bm_colour, estimated_bristol_type, bm_blood_observed,
                    bm_mucus_observed, bm_unusual_colour, urine_volume,
                    urine_colour, urine_blood_observed, urine_strong_odour,
                    urine_unusual_colour, pain_or_distress, other_concern,
                    concern_details, behaviour_before, behaviour_during,
                    behaviour_after, behaviour_comments, general_comments,
                    correction_of_event_id, correction_reason, active,
                    created_at, bm_unusual_details, urine_unusual_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, shift_id, client_id, recorded_by, event_type,
                f"{local_date}T{local_time}", location, location_other,
                bm_size, bm_consistency, values["bm_colour"],
                values["estimated_bristol_type"], values["bm_blood_observed"],
                values["bm_mucus_observed"], values["bm_unusual_colour"],
                urine_volume, values["urine_colour"],
                values["urine_blood_observed"], values["urine_strong_odour"],
                values["urine_unusual_colour"], values["pain_or_distress"],
                values["other_concern"], values["concern_details"],
                values["behaviour_before"], values["behaviour_during"],
                values["behaviour_after"], values["behaviour_comments"],
                values["general_comments"], correction_of_event_id,
                correction_reason, active, f"{local_date} {local_time}:00",
                values["bm_unusual_details"], values["urine_unusual_details"],
            ))
            conn.commit()
        finally:
            conn.close()

    def report(self, query=""):
        return self.client.get("/reports/toileting" + query)

    def report_context(
        self, from_date="2026-08-01", to_date="2026-08-01", group_by="Daily"
    ):
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            return app._toileting_report_context(
                conn, 1, date.fromisoformat(from_date),
                date.fromisoformat(to_date), group_by,
            )
        finally:
            conn.close()

    def test_reporting_authorization_uses_active_database_roles(self):
        for user_id, role in (
            (1, "Admin"), (2, "Director"), (3, "Program Manager"),
            (4, "Behaviour Consultant"),
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 200)

        for user_id, role in (
            (5, "Support Worker"), (6, "Admin"), (5, "Admin")
        ):
            with self.subTest(user_id=user_id, role=role):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 403)

    def test_active_client_and_client_matched_shift_scope(self):
        self.insert_event(1, location="Bathroom")
        self.insert_event(2, client_id=2, shift_id=20, location="Kitchen")
        self.insert_event(3, shift_id=20, location="Community")
        report = self.report_context()
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["locations"]["Bathroom"], 1)
        self.assertEqual(report["summary"]["locations"]["Community"], 1)
        self.assertEqual(report["summary"]["locations"]["Kitchen"], 0)
        self.assertEqual(report["summary"]["shifts"]["Unassigned / Invalid Shift Link"], 1)

    def test_event_datetime_controls_local_date_and_boundaries(self):
        self.insert_event(1, local_date="2026-07-31", local_time="23:59")
        self.insert_event(2, local_date="2026-08-01", local_time="00:00")
        self.insert_event(3, local_date="2026-08-01", local_time="23:59")
        self.insert_event(4, local_date="2026-08-02", local_time="00:00")
        self.insert_event(
            5, local_date="2026-08-02", local_time="06:30", shift_id=12
        )
        report = self.report_context("2026-08-01", "2026-08-02")
        self.assertEqual(report["summary"]["total"], 4)
        self.assertEqual(report["summary"]["days"], 2)
        self.assertEqual(report["summary"]["shifts"]["Overnight"], 1)
        self.assertEqual(
            [item["period"] for item in report["shift_series"]],
            ["2026-08-01", "2026-08-02"],
        )
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-02"
        ).data.decode()
        self.assertNotIn("2026-07-31", page)
        self.assertIn("2026-08-02", page)

    def test_groupings_are_monday_based_and_zero_filled(self):
        self.insert_event(1, local_date="2026-08-05")
        self.insert_event(2, local_date="2026-09-15")
        daily = self.report_context("2026-08-03", "2026-08-07")
        self.assertEqual([item["count"] for item in daily["shift_series"]], [0, 0, 1, 0, 0])
        weekly = self.report_context("2026-08-03", "2026-08-16", "Weekly")
        self.assertEqual([item["period"] for item in weekly["shift_series"]], ["2026-08-03", "2026-08-10"])
        self.assertEqual([item["count"] for item in weekly["shift_series"]], [1, 0])
        monthly = self.report_context("2026-08-01", "2026-10-31", "Monthly")
        self.assertEqual([item["period"] for item in monthly["shift_series"]], ["2026-08-01", "2026-09-01", "2026-10-01"])
        self.assertEqual([item["count"] for item in monthly["shift_series"]], [1, 1, 0])
        annual = self.report_context("2025-01-01", "2027-12-31", "Annual")
        self.assertEqual([item["period"] for item in annual["shift_series"]], ["2025-01-01", "2026-01-01", "2027-01-01"])
        self.assertEqual([item["count"] for item in annual["shift_series"]], [0, 2, 0])

    def test_active_policy_excludes_inactive_without_inventing_correction_pairing(self):
        self.insert_event(1, active=0, correction_reason="Superseded")
        self.insert_event(2, active=1, correction_of_event_id=1, correction_reason="Corrected")
        report = self.report_context()
        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(len(report["occurrences"]), 2)
        self.assertEqual(report["occurrences"][0]["active"], 0)
        self.assertEqual(report["occurrences"][1]["active"], 1)

    def test_summary_and_event_type_breakdown_keep_both_overlap_explicit(self):
        self.insert_event(1, event_type="BM")
        self.insert_event(2, event_type="Urination", bm_size=None, bm_consistency=None, urine_volume="Small")
        self.insert_event(3, event_type="Both", urine_volume="Large")
        report = self.report_context()
        self.assertEqual(report["summary"]["total"], 3)
        self.assertEqual(report["summary"]["bm_events"], 2)
        self.assertEqual(report["summary"]["urination_events"], 2)
        self.assertEqual(report["summary"]["event_types"], {"BM": 1, "Urination": 1, "Both": 1})
        self.assertEqual(sum(report["summary"]["event_types"].values()), 3)

    def test_actual_shift_overrides_clock_and_invalid_links_reconcile(self):
        self.insert_event(1, local_time="23:30", shift_id=10)
        self.insert_event(2, local_time="07:30", shift_id=11)
        self.insert_event(3, local_time="12:00", shift_id=12)
        self.insert_event(4, local_time="14:00", shift_id=13)
        self.insert_event(5, local_time="15:00", shift_id=20)
        report = self.report_context()
        shifts = report["summary"]["shifts"]
        self.assertEqual(shifts, {
            "Day": 1, "Afternoon": 1, "Overnight": 1,
            "Unassigned / Invalid Shift Link": 2,
        })
        self.assertEqual(sum(shifts.values()), report["summary"]["total"])
        self.assertEqual(report["shift_series"][0]["count"], 5)

    def test_structured_location_and_bm_urine_distributions_are_conditional(self):
        locations = ("Bathroom", "Bedroom", "Living Room", "Kitchen", "Community", "Vehicle", "Other")
        for index, location in enumerate(locations, 1):
            self.insert_event(index, location=location, location_other="Outside" if location == "Other" else None)
        self.insert_event(8, event_type="Urination", bm_size=None, bm_consistency=None, urine_volume="Small")
        report = self.report_context()
        self.assertEqual(sum(report["summary"]["locations"].values()), 8)
        self.assertEqual(report["summary"]["bm_sizes"], {"Small": 0, "Medium": 7, "Large": 0})
        self.assertEqual(report["summary"]["bm_consistencies"], {"Hard": 0, "Firm": 0, "Soft": 7, "Loose": 0, "Watery": 0})
        self.assertEqual(report["summary"]["urine_volumes"], {"Small": 1, "Medium": 0, "Large": 0})

    def test_unreliable_dormant_fields_do_not_change_primary_statistics(self):
        self.insert_event(
            1, bm_colour="Brown", estimated_bristol_type=4,
            bm_blood_observed=1, bm_mucus_observed=1,
            bm_unusual_colour=1, urine_colour="Dark",
            urine_blood_observed=1, urine_strong_odour=1,
            urine_unusual_colour=1, pain_or_distress=1, other_concern=1,
            concern_details="Sensitive <details>",
        )
        report = self.report_context()
        self.assertEqual(report["summary"]["total"], 1)
        self.assertNotIn("estimated_bristol_type", report["summary"])
        self.assertNotIn("bm_colour", report["summary"])
        self.assertNotIn("pain_or_distress", report["summary"])
        self.assertNotIn("other_concern", report["summary"])

    def test_empty_range_and_controls_are_safe(self):
        self.login()
        response = self.report(
            "?from_date=2026-09-01&to_date=2026-09-03&group_by=Daily"
        )
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn("Total Toileting Events</dt><dd>0", page)
        self.assertIn("Days With Toileting Events</dt><dd>0", page)
        empty = self.report_context("2026-09-01", "2026-09-03")
        self.assertEqual([item["count"] for item in empty["shift_series"]], [0, 0, 0])
        self.assertEqual(self.report("?group_by=Hourly").status_code, 400)
        self.assertEqual(self.report("?from_date=2026-09-03&to_date=2026-09-01").status_code, 400)

    def test_detail_table_uses_user_labels_escaping_and_review_route(self):
        self.insert_event(
            1, event_type="Both", location="Other",
            location_other="Garden <script>", urine_volume="Large",
            bm_unusual_details="BM <b>note</b>",
            general_comments="Comment <em>here</em>",
        )
        self.login()
        page = self.report("?from_date=2026-08-01&to_date=2026-08-01").data.decode()
        self.assertIn("BM and Urination", page)
        self.assertIn("Garden &lt;script&gt;", page)
        self.assertIn("BM &lt;b&gt;note&lt;/b&gt;", page)
        self.assertIn('href="/manager-review/toileting/1"', page)
        self.assertNotIn("estimated_bristol_type", page)
        self.assertEqual(self.client.get("/manager-review/toileting/1").status_code, 200)

    def test_reports_landing_page_keeps_existing_reports_and_adds_toileting(self):
        self.login()
        page = self.client.get("/reports")
        self.assertEqual(page.status_code, 200)
        for endpoint in ("behaviour", "activities", "sleep", "food-fluid", "toileting"):
            self.assertIn(f'href="/reports/{endpoint}"', page.data.decode())
        self.assertIn("Open Toileting Report", page.data.decode())


if __name__ == "__main__":
    unittest.main()
