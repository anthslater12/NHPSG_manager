import sqlite3
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import tests.test_schedule_staff_view as staff_view_tests

import app


class SchedulePhase3Tests(unittest.TestCase):
    setUp = staff_view_tests.ScheduleStaffViewTests.setUp
    tearDown = staff_view_tests.ScheduleStaffViewTests.tearDown
    login = staff_view_tests.ScheduleStaffViewTests.login
    add_assignment = staff_view_tests.ScheduleStaffViewTests.add_assignment
    rows = staff_view_tests.ScheduleStaffViewTests.rows

    def future_week(self):
        selected = datetime.now(app.VANCOUVER_TIMEZONE).date() + timedelta(days=5)
        return selected - timedelta(days=selected.weekday()), selected

    def add_future_shift(self, client_id=10, shift_type="Day", status="Published"):
        monday, selected = self.future_week()
        conn = sqlite3.connect(app.DB_NAME)
        shift_id = conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (?, ?, ?, '08:00', '16:00', ?, 'Published note', 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, (client_id, selected.isoformat(), shift_type, status)).lastrowid
        conn.commit()
        conn.close()
        return shift_id, monday, selected

    def activity_types(self):
        return [row["activity_type"] for row in self.rows(
            "SELECT activity_type FROM activity_log ORDER BY activity_id"
        )]

    def test_shift_edit_returns_entire_week_to_draft_and_hides_it_from_worker(self):
        shift_id, monday, selected = self.add_future_shift()
        second_id, _, _ = self.add_future_shift(shift_type="Afternoon")
        other_client_id, _, _ = self.add_future_shift(client_id=20)
        other_week_id = self.add_future_shift(shift_type="Overnight")[0]
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute(
            "UPDATE schedule_shifts SET shift_date = '2026-08-31' WHERE schedule_shift_id = ?",
            (other_week_id,),
        )
        conn.commit()
        conn.close()

        self.add_assignment(shift_id, 2, "08:00", "16:00")
        self.login()
        response = self.client.post(
            f"/schedule/shift/{shift_id}/edit",
            data={
                "client_id": "10",
                "shift_date": selected.isoformat(),
                "shift_type": "Day",
                "planned_start_time": "09:00",
                "planned_end_time": "17:00",
                "status": "Published",
                "notes": "Changed published note",
                "worker_ids": ["2"],
                "worker_planned_start_time_2": "09:00",
                "worker_planned_end_time_2": "17:00",
            },
        )
        self.assertEqual(response.status_code, 302)

        statuses = self.rows(
            "SELECT schedule_shift_id, status FROM schedule_shifts "
            "WHERE client_id = 10 AND shift_date BETWEEN ? AND ?",
            (monday.isoformat(), (monday + timedelta(days=6)).isoformat()),
        )
        self.assertEqual({row["status"] for row in statuses}, {"Draft"})
        self.assertEqual(self.rows(
            "SELECT status FROM schedule_shifts WHERE schedule_shift_id = ?",
            (other_client_id,),
        )[0]["status"], "Published")
        self.assertEqual(self.rows(
            "SELECT status FROM schedule_shifts WHERE schedule_shift_id = ?",
            (second_id,),
        )[0]["status"], "Draft")
        self.assertEqual(self.rows(
            "SELECT status FROM schedule_shifts WHERE schedule_shift_id = ?",
            (other_week_id,),
        )[0]["status"], "Published")
        self.assertEqual(
            self.activity_types().count("schedule_week_returned_to_draft"), 1
        )

        self.login(2)
        worker_response = self.client.get(
            f"/schedule/client/10/week/{monday.isoformat()}"
        )
        self.assertIn(b"The schedule for this week has not yet been published.", worker_response.data)
        self.assertNotIn(b"Anne Worker", worker_response.data)
        self.assertNotIn(b"Changed published note", worker_response.data)
        self.assertNotIn(b"9:00AM", worker_response.data)

        self.login(1)
        management_response = self.client.get(
            f"/schedule/client/10/week/{monday.isoformat()}"
        )
        self.assertIn(b"Draft", management_response.data)
        self.assertIn(b"Publish Schedule", management_response.data)
        self.assertIn(b"Changed published note", management_response.data)

    def test_staff_assignment_add_edit_and_remove_return_week_to_draft(self):
        for operation in ("add", "edit", "remove"):
            shift_id, monday, selected = self.add_future_shift(
                shift_type={"add": "Day", "edit": "Afternoon", "remove": "Overnight"}[operation]
            )
            assignment_id = None
            if operation in ("edit", "remove"):
                self.add_assignment(shift_id, 2, "08:00", "16:00")
                assignment_id = self.rows(
                    "SELECT schedule_staff_id FROM schedule_staff WHERE schedule_shift_id = ?",
                    (shift_id,),
                )[0]["schedule_staff_id"]
            self.login()
            if operation == "add":
                response = self.client.post(
                    f"/schedule/client/10/week/{monday.isoformat()}/staff/2/new/{selected.isoformat()}",
                    data={"shift_type": "Day", "planned_start_time": "09:00", "planned_end_time": "17:00"},
                )
            elif operation == "edit":
                response = self.client.post(
                    f"/schedule/client/10/week/{monday.isoformat()}/staff-assignment/{assignment_id}/edit",
                    data={"planned_start_time": "09:00", "planned_end_time": "17:00"},
                )
            else:
                response = self.client.post(
                    f"/schedule/client/10/week/{monday.isoformat()}/staff-assignment/{assignment_id}/remove"
                )
            self.assertEqual(response.status_code, 302, operation)
            self.assertEqual(
                self.rows("SELECT status FROM schedule_shifts WHERE schedule_shift_id = ?", (shift_id,))[0]["status"],
                "Draft",
            )
            self.assertEqual(
                self.activity_types().count("schedule_week_returned_to_draft"),
                {"add": 1, "edit": 2, "remove": 3}[operation],
            )

    def test_staff_ordering_preserves_publication_and_worker_visibility(self):
        shift_id, monday, _ = self.add_future_shift()
        self.add_assignment(shift_id, 2, "08:00", "16:00")
        self.add_assignment(shift_id, 3, "08:00", "16:00")
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            signature = app._schedule_staff_view_context(
                conn, monday, 10
            )["order_signature"]
        finally:
            conn.close()

        self.login()
        response = self.client.post(
            "/schedule/client/10/staff-order/2/move-down",
            data={
                "monday": monday.isoformat(),
                "expected_order_signature": signature,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.rows(
                "SELECT status FROM schedule_shifts WHERE schedule_shift_id = ?",
                (shift_id,),
            )[0]["status"],
            "Published",
        )
        self.assertIn("schedule_staff_order_changed", self.activity_types())
        self.assertNotIn("schedule_week_returned_to_draft", self.activity_types())

        self.login(2)
        worker_response = self.client.get(
            f"/schedule/client/10/week/{monday.isoformat()}"
        )
        self.assertIn(b"Anne Worker", worker_response.data)
        self.assertIn(b"8:00AM", worker_response.data)
        self.assertNotIn(
            b"The schedule for this week has not yet been published.",
            worker_response.data,
        )

    def test_terminal_rows_are_preserved_and_draft_week_is_not_logged(self):
        published_id, monday, _ = self.add_future_shift()
        closed_id, _, _ = self.add_future_shift(shift_type="Afternoon", status="Closed")
        cancelled_id, _, _ = self.add_future_shift(shift_type="Overnight", status="Cancelled")
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            result = app._schedule_week_return_to_draft(
                conn, 10, monday, 1, "Mixed", "schedule_shift_updated"
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(result, 1)
        statuses = self.rows(
            "SELECT schedule_shift_id, status FROM schedule_shifts WHERE schedule_shift_id IN (?, ?, ?)",
            (published_id, closed_id, cancelled_id),
        )
        self.assertEqual({row["status"] for row in statuses}, {"Draft", "Closed", "Cancelled"})

        conn = sqlite3.connect(app.DB_NAME)
        try:
            conn.execute("UPDATE schedule_shifts SET status = 'Draft' WHERE schedule_shift_id = ?", (published_id,))
            conn.commit()
            conn.execute("BEGIN")
            self.assertEqual(
                app._schedule_week_return_to_draft(
                    conn, 10, monday, 1, "Draft", "schedule_shift_updated"
                ),
                0,
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self.activity_types().count("schedule_week_returned_to_draft"), 1)

    def test_failed_activity_log_rolls_back_mutation_and_status_transition(self):
        shift_id, _, selected = self.add_future_shift()
        self.login()
        with patch.object(app, "log_activity", side_effect=RuntimeError("forced log failure")):
            response = self.client.post(
                f"/schedule/shift/{shift_id}/edit",
                data={
                    "client_id": "10",
                    "shift_date": selected.isoformat(),
                    "shift_type": "Day",
                    "planned_start_time": "09:00",
                    "planned_end_time": "17:00",
                    "status": "Published",
                    "notes": "Should roll back",
                },
            )
        self.assertEqual(response.status_code, 500)
        row = self.rows(
            "SELECT status, planned_start_time, notes FROM schedule_shifts WHERE schedule_shift_id = ?",
            (shift_id,),
        )[0]
        self.assertEqual(row["status"], "Published")
        self.assertEqual(row["planned_start_time"], "08:00")
        self.assertEqual(row["notes"], "Published note")
        self.assertEqual(self.activity_types(), [])


if __name__ == "__main__":
    unittest.main()
