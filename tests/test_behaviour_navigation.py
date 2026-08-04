import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app


class BehaviourWeeklyNavigationTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

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


if __name__ == "__main__":
    unittest.main()
