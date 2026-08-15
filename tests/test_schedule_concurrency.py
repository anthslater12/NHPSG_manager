import sqlite3
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import tests.test_schedule_staff_view as staff_view_tests

import app


class ScheduleConcurrencyTests(unittest.TestCase):
    setUp = staff_view_tests.ScheduleStaffViewTests.setUp
    tearDown = staff_view_tests.ScheduleStaffViewTests.tearDown
    login = staff_view_tests.ScheduleStaffViewTests.login
    add_assignment = staff_view_tests.ScheduleStaffViewTests.add_assignment
    rows = staff_view_tests.ScheduleStaffViewTests.rows

    def add_future_shift(self, status="Published", shift_type="Day"):
        selected = datetime.now(app.VANCOUVER_TIMEZONE).date() + timedelta(days=5)
        monday = selected - timedelta(days=selected.weekday())
        conn = sqlite3.connect(app.DB_NAME)
        shift_id = conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (10, ?, ?, '08:00', '16:00', ?, 'Original note', 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, (selected.isoformat(), shift_type, status)).lastrowid
        conn.commit()
        conn.close()
        return shift_id, monday, selected

    def force_status_after_first_editability_check(self, shift_id, status):
        original = app._schedule_shift_is_editable
        changed = False

        def guarded(shift_date, current_status, today=None):
            nonlocal changed
            if not changed:
                changed = True
                with sqlite3.connect(app.DB_NAME) as conn:
                    conn.execute(
                        "UPDATE schedule_shifts SET status = ? WHERE schedule_shift_id = ?",
                        (status, shift_id),
                    )
                    conn.commit()
            return original(shift_date, current_status, today)

        return guarded

    def test_shift_edit_rejects_status_changed_to_terminal_inside_transaction(self):
        for index, terminal_status in enumerate(("Closed", "Cancelled")):
            with self.subTest(status=terminal_status):
                shift_id, _, selected = self.add_future_shift(
                    shift_type=("Day", "Afternoon")[index]
                )
                self.login()
                with patch.object(
                    app,
                    "_schedule_shift_is_editable",
                    side_effect=self.force_status_after_first_editability_check(
                        shift_id, terminal_status
                    ),
                ):
                    response = self.client.post(
                        f"/schedule/shift/{shift_id}/edit",
                        data={
                            "client_id": "10",
                            "shift_date": selected.isoformat(),
                            "shift_type": "Day",
                            "planned_start_time": "09:00",
                            "planned_end_time": "17:00",
                            "status": "Published",
                            "notes": "Stale update",
                        },
                    )
                self.assertEqual(response.status_code, 403)
                row = self.rows(
                    "SELECT status, planned_start_time, notes "
                    "FROM schedule_shifts WHERE schedule_shift_id = ?",
                    (shift_id,),
                )[0]
                self.assertEqual(row["status"], terminal_status)
                self.assertEqual(row["planned_start_time"], "08:00")
                self.assertEqual(row["notes"], "Original note")
                self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_assignment_edit_rejects_terminal_parent_without_mutation_or_events(self):
        for index, terminal_status in enumerate(("Closed", "Cancelled")):
            with self.subTest(status=terminal_status):
                shift_id, _, _ = self.add_future_shift(
                    shift_type=("Day", "Afternoon")[index]
                )
                self.add_assignment(shift_id, 2, "08:00", "16:00")
                assignment_id = self.rows(
                    "SELECT schedule_staff_id FROM schedule_staff "
                    "WHERE schedule_shift_id = ?",
                    (shift_id,),
                )[0]["schedule_staff_id"]
                self.login()
                with patch.object(
                    app,
                    "_schedule_shift_is_editable",
                    side_effect=self.force_status_after_first_editability_check(
                        shift_id, terminal_status
                    ),
                ):
                    response = self.client.post(
                        f"/schedule/client/10/week/{self._monday(shift_id)}"
                        f"/staff-assignment/{assignment_id}/edit",
                        data={
                            "planned_start_time": "09:00",
                            "planned_end_time": "17:00",
                        },
                    )
                self.assertEqual(response.status_code, 403)
                assignment = self.rows(
                    "SELECT planned_start_time, planned_end_time "
                    "FROM schedule_staff WHERE schedule_staff_id = ?",
                    (assignment_id,),
                )[0]
                self.assertEqual(assignment["planned_start_time"], "08:00")
                self.assertEqual(assignment["planned_end_time"], "16:00")
                self.assertEqual(
                    self.rows(
                        "SELECT status FROM schedule_shifts WHERE schedule_shift_id = ?",
                        (shift_id,),
                    )[0]["status"],
                    terminal_status,
                )
                self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def test_assignment_remove_rejects_terminal_parent_and_preserves_assignment(self):
        for index, terminal_status in enumerate(("Closed", "Cancelled")):
            with self.subTest(status=terminal_status):
                shift_id, monday, _ = self.add_future_shift(
                    shift_type=("Day", "Afternoon")[index]
                )
                self.add_assignment(shift_id, 2, "08:00", "16:00")
                assignment_id = self.rows(
                    "SELECT schedule_staff_id FROM schedule_staff "
                    "WHERE schedule_shift_id = ?",
                    (shift_id,),
                )[0]["schedule_staff_id"]
                self.login()
                with patch.object(
                    app,
                    "_schedule_shift_is_editable",
                    side_effect=self.force_status_after_first_editability_check(
                        shift_id, terminal_status
                    ),
                ):
                    response = self.client.post(
                        f"/schedule/client/10/week/{monday.isoformat()}"
                        f"/staff-assignment/{assignment_id}/remove"
                    )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(
                    len(self.rows(
                        "SELECT * FROM schedule_staff WHERE schedule_staff_id = ?",
                        (assignment_id,),
                    )),
                    1,
                )
                self.assertEqual(
                    self.rows(
                        "SELECT status FROM schedule_shifts WHERE schedule_shift_id = ?",
                        (shift_id,),
                    )[0]["status"],
                    terminal_status,
                )
                self.assertEqual(self.rows("SELECT * FROM activity_log"), [])

    def _monday(self, shift_id):
        shift_date = self.rows(
            "SELECT shift_date FROM schedule_shifts WHERE schedule_shift_id = ?",
            (shift_id,),
        )[0]["shift_date"]
        selected = datetime.fromisoformat(shift_date).date()
        return (selected - timedelta(days=selected.weekday())).isoformat()


if __name__ == "__main__":
    unittest.main()
