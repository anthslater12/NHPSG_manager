import os
import sqlite3
import sys
import tempfile
import unittest

from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_behaviour_occurrences_table
import app


class BehaviourReportingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "reporting.db")
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
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Alex Admin', 'Admin', 1),
                (2, 'director', 'hash', 'Dana Director', 'Director', 1),
                (3, 'manager', 'hash', 'Morgan Manager', 'Program Manager', 1),
                (4, 'worker', 'hash', 'Sam Worker', 'Support Worker', 1),
                (5, 'consultant', 'hash', 'Case Consultant', 'Behaviour Consultant', 1),
                (6, 'inactive', 'hash', 'Inactive Admin', 'Admin', 0);
            INSERT INTO clients VALUES
                (1, 'Client One', 1),
                (2, 'Other Client', 0);
        """)
        add_behaviour_occurrences_table.migrate(conn)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, role="Admin"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = "Report Tester"

    def insert_occurrence(
        self,
        occurrence_id,
        local_date,
        local_time="10:00",
        record_format="V1",
        status="Recorded",
        client_id=1,
        duration=None,
        category="aggression_towards_others",
    ):
        local = datetime.strptime(
            f"{local_date} {local_time}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=app.VANCOUVER_TIMEZONE)
        occurred_at = app.serialize_behaviour_utc(local)
        values = {
            "behaviour_occurrence_id": occurrence_id,
            "client_id": client_id,
            "occurred_at_utc": occurred_at,
            "recorded_by_user_id": 1,
            "recorded_at_utc": occurred_at,
            "submission_token": f"report-{occurrence_id}",
            "status": status,
            "record_format": record_format,
        }
        if record_format == "ABC":
            values["behaviour_crying_yelling_screaming"] = 1
            values["duration_until_calm_minutes"] = duration
        else:
            values[category] = 1
            values["notes"] = f"Report occurrence {occurrence_id}"
        if status == "Completed":
            values["completed_at_utc"] = app.serialize_behaviour_utc(
                local.astimezone(timezone.utc).replace(microsecond=0)
            )
            values["completed_by_user_id"] = 1
        if status == "Voided":
            values["voided_at_utc"] = "2026-08-20T19:00:00Z"
            values["voided_by_user_id"] = 1
            values["void_reason"] = "Reporting test void"

        columns = list(values)
        placeholders = ", ".join("?" for _ in columns)
        conn = sqlite3.connect(app.DB_NAME)
        try:
            conn.execute(
                f"INSERT INTO behaviour_occurrences "
                f"({', '.join(columns)}) VALUES ({placeholders})",
                [values[column] for column in columns],
            )
            conn.commit()
        finally:
            conn.close()

    def report(self, query=""):
        return self.client.get("/reports/behaviour" + query)

    def test_authorized_roles_and_unauthorized_users(self):
        for user_id, role in (
            (1, "Admin"),
            (2, "Director"),
            (3, "Program Manager"),
            (5, "Behaviour Consultant"),
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 200)

        for user_id, role in ((4, "Support Worker"), (6, "Admin")):
            with self.subTest(user_id=user_id):
                self.login(user_id, role)
                self.assertEqual(self.report().status_code, 403)

    def test_single_active_client_is_resolved_and_queries_are_client_scoped(self):
        self.insert_occurrence(1, "2026-08-05", client_id=1)
        self.insert_occurrence(2, "2026-08-05", client_id=2)
        self.login()
        response = self.report("?from_date=2026-08-01&to_date=2026-08-31")
        self.assertEqual(response.status_code, 200)
        page = response.data.decode()
        self.assertIn("Client One", page)
        self.assertNotIn("Other Client", page)
        self.assertIn("Total finalized occurrences</dt><dd>1", page)

    def test_invalid_dates_and_grouping_are_rejected(self):
        self.login()
        self.assertEqual(
            self.report("?from_date=2026-08-03&to_date=2026-08-01").status_code,
            400,
        )
        self.assertEqual(
            self.report("?from_date=not-a-date&to_date=2026-08-01").status_code,
            400,
        )
        self.assertEqual(
            self.report("?group_by=Hourly").status_code,
            400,
        )

    def test_date_range_uses_vancouver_calendar_boundaries(self):
        self.insert_occurrence(1, "2026-07-31", "23:59")
        self.insert_occurrence(2, "2026-08-01", "00:00")
        self.insert_occurrence(3, "2026-08-01", "23:30")
        self.insert_occurrence(4, "2026-08-02", "00:00")
        self.login()
        response = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01&group_by=Daily"
        )
        page = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Total finalized occurrences</dt><dd>2", page)
        self.assertIn("Calendar dates with occurrences</dt><dd>1", page)
        self.assertIn("<th scope=\"row\">Night</th><td>2</td>", page)
        self.assertIn("<th scope=\"row\">2026-08-01</th><td>2</td>", page)
        self.assertIn("&mdash;", page)
        self.assertNotIn("â", page)
        self.assertNotIn("Report occurrence 1", page)
        self.assertNotIn("Report occurrence 4", page)

    def test_summary_excludes_voided_and_separates_formats_and_duration(self):
        self.insert_occurrence(1, "2026-08-01", record_format="V1")
        self.insert_occurrence(
            2, "2026-08-02", record_format="ABC", status="Completed", duration=10
        )
        self.insert_occurrence(
            3, "2026-08-03", record_format="ABC", duration=20
        )
        self.insert_occurrence(4, "2026-08-04", status="Voided")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-04"
        ).data.decode()
        self.assertIn("Total finalized occurrences</dt><dd>3", page)
        self.assertIn("ABC occurrences</dt><dd>2", page)
        self.assertIn("V1 occurrences</dt><dd>1", page)
        self.assertIn("Voided occurrences</dt><dd>1", page)
        self.assertIn("ABC records with duration</dt><dd>2", page)
        self.assertIn("Average duration until calm (minutes)</dt><dd>15.0", page)
        self.assertIn("Median duration until calm (minutes)</dt><dd>15.0", page)
        self.assertIn("Shortest duration (minutes)</dt><dd>10", page)
        self.assertIn("Longest duration (minutes)</dt><dd>20", page)

    def test_only_finalized_occurrences_contribute_to_report_statistics(self):
        self.insert_occurrence(1, "2026-08-01", status="In Progress")
        self.insert_occurrence(
            2, "2026-08-02", record_format="ABC", status="In Progress", duration=99
        )
        self.insert_occurrence(3, "2026-08-01", status="Recorded")
        self.insert_occurrence(
            4, "2026-08-02", record_format="ABC", status="Completed", duration=10
        )
        self.insert_occurrence(5, "2026-08-03", status="Voided")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-03&group_by=Daily"
        ).data.decode()
        self.assertIn("Total finalized occurrences</dt><dd>2", page)
        self.assertIn("ABC occurrences</dt><dd>1", page)
        self.assertIn("V1 occurrences</dt><dd>1", page)
        self.assertIn("Voided occurrences</dt><dd>1", page)
        self.assertIn("ABC records with duration</dt><dd>1", page)
        self.assertIn("Average duration until calm (minutes)</dt><dd>10", page)
        self.assertIn("Median duration until calm (minutes)</dt><dd>10", page)
        self.assertIn("<th scope=\"row\">2026-08-01</th><td>1</td>", page)
        self.assertIn("<th scope=\"row\">2026-08-02</th><td>1</td>", page)
        self.assertNotIn("<th scope=\"row\">2026-08-03</th><td>1</td>", page)
        self.assertIn("In Progress", page)
        self.assertIn("Voided", page)

    def test_operational_band_counts_use_behaviour_band_boundaries(self):
        self.insert_occurrence(1, "2026-08-01", "06:59")
        self.insert_occurrence(2, "2026-08-01", "07:30")
        self.insert_occurrence(3, "2026-08-01", "15:30")
        self.login()
        page = self.report(
            "?from_date=2026-08-01&to_date=2026-08-01"
        ).data.decode()
        self.assertIn("<th scope=\"row\">Night</th><td>1</td>", page)
        self.assertIn("<th scope=\"row\">Day</th><td>1</td>", page)
        self.assertIn("<th scope=\"row\">Evening</th><td>1</td>", page)

    def test_date_range_handles_vancouver_spring_dst_transition(self):
        self.insert_occurrence(1, "2026-03-07", "23:59")
        self.insert_occurrence(2, "2026-03-08", "00:00")
        self.insert_occurrence(3, "2026-03-08", "23:59")
        self.insert_occurrence(4, "2026-03-09", "00:00")
        self.login()
        response = self.report(
            "?from_date=2026-03-08&to_date=2026-03-08&group_by=Daily"
        )
        page = response.data.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Total finalized occurrences</dt><dd>2", page)
        self.assertIn("<th scope=\"row\">2026-03-08</th><td>2</td>", page)
        self.assertNotIn("Report occurrence 1", page)
        self.assertNotIn("Report occurrence 4", page)

    def test_daily_weekly_monthly_and_annual_series(self):
        rows = (
            (1, "2026-08-01"), (2, "2026-08-02"),
            (3, "2026-08-10"), (4, "2026-09-01"),
            (5, "2027-01-01"),
        )
        for occurrence_id, local_date in rows:
            self.insert_occurrence(occurrence_id, local_date)
        self.login()

        cases = (
            ("Daily", "2026-08-01", "2027-01-01", "2026-08-01", 1),
            ("Weekly", "2026-08-01", "2026-08-31", "2026-07-27", 2),
            ("Monthly", "2026-08-01", "2026-12-31", "2026-08-01", 3),
            ("Annual", "2026-01-01", "2027-12-31", "2026-01-01", 4),
        )
        for grouping, from_date, to_date, first_period, count in cases:
            with self.subTest(grouping=grouping):
                response = self.report(
                    f"?from_date={from_date}&to_date={to_date}"
                    f"&group_by={grouping}"
                )
                page = response.data.decode()
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    f"<th scope=\"row\">{first_period}</th><td>{count}</td>",
                    page,
                )


if __name__ == "__main__":
    unittest.main()
