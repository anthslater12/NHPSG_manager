import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app


class BehaviourWeeklyNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "navigation.db")
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER)")
        conn.execute("INSERT INTO clients VALUES (7, 'Client Seven', 1)")
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, role):
        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["role"] = role
            session["full_name"] = role

    def test_management_roles_see_current_behaviour_week_link(self):
        expected = app.get_behaviour_operational_week_start(
            datetime.now(app.VANCOUVER_TIMEZONE)
        ).isoformat()
        for role in ("Admin", "Program Manager", "Director"):
            self.login(role)
            response = self.client.get("/manager-review")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Behaviour Weekly Review", response.data)
            self.assertEqual(response.data.count(b"\n                Storyline\n"), 1)
            self.assertIn(b"/client/7/storyline", response.data)
            self.assertIn(
                f"/behaviour/week/{expected}".encode(), response.data
            )
            self.assertIn(b"Open Food &amp; Fluid Review", response.data)
            self.assertIn(b"Open Incidents", response.data)

    def test_support_worker_does_not_see_management_hub_or_behaviour_link(self):
        self.login("Support Worker")
        response = self.client.get("/manager-review")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b"Behaviour Weekly Review", response.data)

    def test_operational_week_boundary_uses_vancouver_helper(self):
        before = datetime(2026, 8, 2, 22, 59, tzinfo=app.VANCOUVER_TIMEZONE)
        after = datetime(2026, 8, 2, 23, 0, tzinfo=app.VANCOUVER_TIMEZONE)
        self.assertEqual(
            app.get_behaviour_operational_week_start(before).isoformat(),
            "2026-07-27"
        )
        self.assertEqual(
            app.get_behaviour_operational_week_start(after).isoformat(),
            "2026-08-03"
        )

    def test_zero_or_multiple_active_clients_omit_storyline_link_safely(self):
        self.login("Admin")
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("UPDATE clients SET active = 0")
        conn.commit()
        conn.close()
        self.assertEqual(self.client.get("/manager-review").status_code, 200)
        with sqlite3.connect(app.DB_NAME) as conn:
            conn.executemany(
                "INSERT INTO clients VALUES (?, ?, 1)",
                ((8, "Client Eight"), (9, "Client Nine"))
            )
            conn.commit()
        page = self.client.get("/manager-review")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"\n                Storyline\n", page.data)


if __name__ == "__main__":
    unittest.main()
