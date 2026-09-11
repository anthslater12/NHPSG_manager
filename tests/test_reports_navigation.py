import os
import sqlite3
import tempfile
import unittest

import app


class ReportsNavigationTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "reports-navigation.db")
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
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Admin', 'Admin', 1),
                (2, 'director', 'hash', 'Director', 'Director', 1),
                (3, 'manager', 'hash', 'Manager', 'Program Manager', 1),
                (4, 'consultant', 'hash', 'Consultant', 'Behaviour Consultant', 1),
                (5, 'worker', 'hash', 'Worker', 'Support Worker', 1),
                (6, 'inactive', 'hash', 'Inactive', 'Admin', 0);
            INSERT INTO clients VALUES (1, 'Active Client', 1);
        """)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id, session_role=None):
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
                full_name="Navigation User",
            )

    def test_reports_navigation_is_visible_for_reporting_roles(self):
        for user_id, role in (
            (1, "Admin"),
            (2, "Director"),
            (3, "Program Manager"),
            (4, "Behaviour Consultant"),
        ):
            with self.subTest(role=role):
                self.login(user_id)
                response = self.client.get("/worker-resources")
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'href="/reports"', response.data)

    def test_reports_navigation_uses_database_authority(self):
        self.login(1, session_role="Support Worker")
        self.assertIn(
            b'href="/reports"',
            self.client.get("/worker-resources").data,
        )

        self.login(5, session_role="Admin")
        self.assertNotIn(
            b'href="/reports"',
            self.client.get("/worker-resources").data,
        )

    def test_reports_navigation_is_hidden_for_support_worker_and_inactive_user(self):
        self.login(5)
        worker_page = self.client.get("/worker-resources")
        self.assertEqual(worker_page.status_code, 200)
        self.assertNotIn(b'href="/reports"', worker_page.data)

        self.login(6)
        inactive_page = self.client.get("/login")
        self.assertEqual(inactive_page.status_code, 200)
        self.assertNotIn(b'href="/reports"', inactive_page.data)

    def test_reports_landing_page_links_to_both_report_routes(self):
        self.login(1)
        response = self.client.get("/reports")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Reports", response.data)
        self.assertIn(b'href="/reports/behaviour"', response.data)
        self.assertIn(b'href="/reports/activities"', response.data)

    def test_activity_report_link_is_removed_from_management_review_hub(self):
        self.login(1)
        response = self.client.get("/manager-review")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Open Activity Review", response.data)
        self.assertNotIn(b"Open Activity Report", response.data)
        self.assertNotIn(b'href="/reports/activities"', response.data)

    def test_existing_navigation_links_remain_available(self):
        self.login(1)
        page = self.client.get("/worker-resources").data
        for label in (
            "Current Shift", "Worker Resources", "Schedule", "View Notes",
            "Incidents", "Management Review", "My Actions", "Logout",
        ):
            with self.subTest(label=label):
                self.assertIn(label.encode(), page)


if __name__ == "__main__":
    unittest.main()
