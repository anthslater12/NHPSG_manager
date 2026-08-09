import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_schedule_staff_order
import add_schedule_tables
import app


class ScheduleStaffViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = app.DB_NAME
        app.DB_NAME = os.path.join(self.temp.name, "schedule.db")
        conn = sqlite3.connect(app.DB_NAME)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
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
                event_datetime TEXT
            );
            INSERT INTO users VALUES
                (1, 'admin', 'hash', 'Alex Manager', 'Admin', 1),
                (2, 'anne', 'hash', 'Anne Worker', 'Support Worker', 1),
                (3, 'zara', 'hash', 'Zara Worker', 'Support Worker', 1),
                (4, 'historical', 'hash', 'Historical Worker', 'Support Worker', 0),
                (5, 'unused', 'hash', 'Unused Worker', 'Support Worker', 0),
                (6, 'director', 'hash', 'Dana Director', 'Director', 1),
                (7, 'manager', 'hash', 'Morgan Manager', 'Program Manager', 1),
                (8, 'worker', 'hash', 'Sam Worker', 'Support Worker', 1);
            INSERT INTO clients VALUES
                (10, 'Client Ten', 1),
                (20, 'Client Twenty', 1);
        """)
        conn.commit()
        add_schedule_tables.migrate(conn)
        add_schedule_staff_order.migrate(conn)
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id

    def add_shift(self, client_id=10, shift_date="2026-08-03",
                  shift_type="Day", start="08:00", end="16:00"):
        conn = sqlite3.connect(app.DB_NAME)
        shift_id = conn.execute("""
            INSERT INTO schedule_shifts
            (client_id, shift_date, shift_type, planned_start_time,
             planned_end_time, status, notes, created_by, created_at_utc,
             updated_by, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, 'Published', NULL, 1,
                    '2026-08-01T15:00:00Z', 1, '2026-08-01T15:00:00Z')
        """, (client_id, shift_date, shift_type, start, end)).lastrowid
        conn.commit()
        conn.close()
        return shift_id

    def add_assignment(self, shift_id, user_id, start=None, end=None):
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("""
            INSERT INTO schedule_staff
            (schedule_shift_id, user_id, planned_start_time,
             planned_end_time, assigned_by, assigned_at_utc)
            VALUES (?, ?, ?, ?, 1, '2026-08-01T15:00:00Z')
        """, (shift_id, user_id, start, end))
        conn.commit()
        conn.close()

    def set_order(self, client_id, user_id, display_order):
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("""
            INSERT INTO schedule_staff_order
            (client_id, user_id, display_order, updated_by, updated_at_utc)
            VALUES (?, ?, ?, 1, '2026-08-01T15:00:00Z')
        """, (client_id, user_id, display_order))
        conn.commit()
        conn.close()

    def page(self, user_id=1, query=""):
        self.login(user_id)
        return self.client.get(
            f"/schedule/client/10/week/2026-08-03{query}"
        )

    def staff_new_url(self, user_id=2, shift_date="2026-08-09"):
        return (
            f"/schedule/client/10/week/2026-08-03/staff/"
            f"{user_id}/new/{shift_date}"
        )

    def staff_edit_url(self, assignment_id):
        return (
            f"/schedule/client/10/week/2026-08-03/"
            f"staff-assignment/{assignment_id}/edit"
        )

    def staff_remove_url(self, assignment_id, client_id=10, monday="2026-08-03"):
        return (
            f"/schedule/client/{client_id}/week/{monday}/"
            f"staff-assignment/{assignment_id}/remove"
        )

    def rows(self, sql, params=()):
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def order_signature(self):
        conn = sqlite3.connect(app.DB_NAME)
        conn.row_factory = sqlite3.Row
        try:
            return app._schedule_staff_view_context(
                conn, date(2026, 8, 3), 10
            )["order_signature"]
        finally:
            conn.close()

    def move(self, user_id, direction, signature=None):
        self.login()
        return self.client.post(
            f"/schedule/client/10/staff-order/{user_id}/move-{direction}",
            data={
                "monday": "2026-08-03",
                "expected_order_signature": signature or self.order_signature(),
            },
        )

    def test_management_view_selection_and_summary(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"schedule-day-card", response.data)
        self.assertNotIn(b"id=\"schedule-staff-view-heading\"", response.data)
        self.assertIn(b"Weekly Staff Summary", response.data)
        self.assertIn(b"view=staff", response.data)

        response = self.page(query="?view=shift")
        self.assertIn(b"schedule-day-card", response.data)
        response = self.page(query="?view=staff")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"id=\"schedule-staff-view-heading\"", response.data)
        self.assertIn(b"Weekly Staff Summary", response.data)

    def test_all_management_roles_can_use_staff_view(self):
        for user_id in (1, 6, 7):
            response = self.page(user_id, "?view=staff")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Staff View", response.data)

    def test_support_worker_cannot_select_staff_view(self):
        response = self.page(8, "?view=staff")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"schedule-day-card", response.data)
        self.assertNotIn(b"schedule-staff-view-heading", response.data)
        self.assertNotIn(b"Planning View:", response.data)
        self.assertNotIn(b"Weekly Staff Summary", response.data)

    def test_management_roles_see_order_controls_but_support_worker_does_not(self):
        for user_id in (1, 6, 7):
            response = self.page(user_id, "?view=staff")
            self.assertIn(b"schedule-staff-order-controls", response.data)
        response = self.page(8, "?view=staff")
        self.assertNotIn(b"schedule-staff-order-controls", response.data)

    def test_move_up_down_persists_contiguous_order_and_logs(self):
        response = self.move(2, "down")
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=staff", response.location)
        rows = self.rows(
            "SELECT user_id, display_order FROM schedule_staff_order "
            "WHERE client_id = 10 ORDER BY display_order"
        )
        self.assertEqual([(row[0], row[1]) for row in rows], [(8, 1), (2, 2), (3, 3)])
        self.assertEqual([row[1] for row in rows], list(range(1, 4)))
        self.assertEqual(
            self.rows(
                "SELECT activity_type FROM activity_log "
                "WHERE activity_type = 'schedule_staff_order_changed'"
            )[0][0],
            "schedule_staff_order_changed",
        )
        response = self.move(2, "up")
        self.assertEqual(response.status_code, 302)
        rows = self.rows(
            "SELECT user_id, display_order FROM schedule_staff_order "
            "WHERE client_id = 10 ORDER BY display_order"
        )
        self.assertEqual([(row[0], row[1]) for row in rows], [(2, 1), (8, 2), (3, 3)])
        self.assertEqual(
            self.client.get("/schedule/client/10/week/2026-08-10?view=staff").status_code,
            200,
        )

    def test_boundary_stale_and_client_scope_requests_are_safe(self):
        signature = self.order_signature()
        response = self.move(2, "up", signature)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM schedule_staff_order"), [])
        self.assertEqual(
            self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'schedule_staff_order_changed'"
            ),
            [],
        )

        response = self.move(2, "down", "stale-signature")
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=staff", response.location)
        self.assertEqual(self.rows("SELECT * FROM schedule_staff_order"), [])

        self.login()
        response = self.client.post(
            "/schedule/client/999/staff-order/2/move-down",
            data={"monday": "2026-08-03", "expected_order_signature": signature},
        )
        self.assertEqual(response.status_code, 404)

    def test_unordered_worker_can_be_moved_and_inactive_history_is_preserved(self):
        inactive_shift = self.add_shift(shift_date="2026-08-09", shift_type="Afternoon")
        self.add_assignment(inactive_shift, 4, "16:00", "23:00")
        self.set_order(10, 2, 1)
        response = self.page(query="?view=staff")
        self.assertIn(b"Historical Worker", response.data)
        signature = self.order_signature()
        response = self.move(4, "up", signature)
        self.assertEqual(response.status_code, 302)
        rows = self.rows(
            "SELECT user_id, display_order FROM schedule_staff_order "
            "WHERE client_id = 10 ORDER BY display_order"
        )
        self.assertEqual([row[1] for row in rows], list(range(1, len(rows) + 1)))
        self.assertIn(4, [row[0] for row in rows])

    def test_matrix_columns_worker_inclusion_and_client_scoping(self):
        shift = self.add_shift()
        self.add_assignment(shift, 4, "09:00", "17:00")
        response = self.page(query="?view=staff")
        page = response.data.decode()
        matrix = page.split('class="schedule-staff-matrix"', 1)[1].split(
            "</table>", 1
        )[0]
        self.assertEqual(matrix.count('<th scope="col">'), 8)
        self.assertIn("Anne Worker", page)
        self.assertIn("Zara Worker", page)
        self.assertIn("Historical Worker", page)
        self.assertNotIn("Unused Worker", page)
        self.assertIn("Sam Worker", page)

        other_shift = self.add_shift(client_id=20)
        self.add_assignment(other_shift, 4, "09:00", "17:00")
        self.set_order(20, 4, 1)
        response = self.page(query="?view=staff")
        self.assertNotIn("Client Twenty", response.data.decode())

    def test_ordering_times_fallback_multiple_assignments_and_overnight(self):
        self.set_order(10, 3, 1)
        self.set_order(10, 2, 2)
        day = self.add_shift(start="08:00", end="16:00")
        afternoon = self.add_shift(shift_type="Afternoon", start="16:00", end="23:00")
        overnight = self.add_shift(
            shift_date="2026-08-04", shift_type="Overnight",
            start="23:00", end="07:30"
        )
        self.add_assignment(day, 2, "09:00", "11:00")
        self.add_assignment(afternoon, 2, "17:00", "19:00")
        self.add_assignment(overnight, 3, None, None)

        page = self.page(query="?view=staff").data.decode()
        self.assertLess(page.index("Zara Worker"), page.index("Anne Worker"))
        self.assertIn("9:00AM&ndash;11:00AM", page)
        self.assertIn("5:00PM&ndash;7:00PM", page)
        self.assertIn("11:00PM&ndash;7:30AM ND", page)
        self.assertIn("+ Add", page)
        self.assertNotIn("Move Up", page)
        self.assertNotIn("Move Down", page)

    def test_staff_view_keeps_existing_pdf_link_and_shift_edit_controls(self):
        shift = self.add_shift(shift_date="2026-08-09")
        response = self.page(query="?view=staff")
        self.assertIn(b"Export Weekly Schedule PDF", response.data)
        self.assertIn(b"Export Staff Matrix PDF", response.data)
        self.assertNotIn(b"schedule_shift_edit", response.data)

        self.add_assignment(shift, 2)
        response = self.page()
        self.assertIn(b"/schedule/shift/", response.data)

    def test_add_form_is_bound_to_worker_and_date_and_roles_can_add(self):
        for user_id in (1, 6, 7):
            self.login(user_id)
            response = self.client.get(self.staff_new_url(2))
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Anne Worker", response.data)
            self.assertIn(b"Sunday, Aug 9, 2026", response.data)
            self.assertIn(b"Select shift type", response.data)

    def test_add_creates_new_parent_and_assignment_with_activity_log(self):
        self.login()
        response = self.client.post(
            self.staff_new_url(2),
            data={
                "shift_type": "Overnight",
                "planned_start_time": "23:00",
                "planned_end_time": "07:30",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=staff", response.location)
        parent = self.rows("SELECT * FROM schedule_shifts")[0]
        assignment = self.rows("SELECT * FROM schedule_staff")[0]
        self.assertEqual(parent["shift_type"], "Overnight")
        self.assertEqual(parent["planned_start_time"], "23:00")
        self.assertEqual(parent["planned_end_time"], "07:30")
        self.assertEqual(assignment["planned_start_time"], "23:00")
        self.assertEqual(assignment["planned_end_time"], "07:30")
        self.assertEqual(
            [row["activity_type"] for row in self.rows(
                "SELECT activity_type FROM activity_log ORDER BY activity_id"
            )],
            ["schedule_shift_created", "schedule_staff_assigned"],
        )
        shift_view = self.client.get("/schedule/client/10/week/2026-08-03")
        self.assertIn(b"Anne Worker", shift_view.data)
        self.assertIn(b"11:00PM&ndash;7:30AM", shift_view.data)
        self.assertIn(b"8.5", shift_view.data)

    def test_add_reuses_parent_and_duplicate_redirects_to_edit(self):
        parent_id = self.add_shift(shift_date="2026-08-09", start="08:00", end="16:00")
        self.login()
        data = {
            "shift_type": "Day",
            "planned_start_time": "09:00",
            "planned_end_time": "17:00",
        }
        response = self.client.post(self.staff_new_url(2), data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.rows("SELECT * FROM schedule_shifts")), 1)
        assignment_id = self.rows("SELECT schedule_staff_id FROM schedule_staff")[0][0]
        self.assertEqual(self.rows("SELECT schedule_shift_id FROM schedule_staff")[0][0], parent_id)
        response = self.client.post(self.staff_new_url(2), data=data)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"staff-assignment/{assignment_id}/edit", response.location)
        self.assertEqual(len(self.rows("SELECT * FROM schedule_staff")), 1)

    def test_edit_changes_only_target_assignment_and_preserves_parent(self):
        parent_id = self.add_shift(
            shift_date="2026-08-09", start="08:00", end="16:00"
        )
        self.add_assignment(parent_id, 2, "09:00", "11:00")
        self.add_assignment(parent_id, 3, "12:00", "14:00")
        assignment_id = self.rows(
            "SELECT schedule_staff_id FROM schedule_staff WHERE user_id = 2"
        )[0][0]
        self.login()
        response = self.client.post(
            self.staff_edit_url(assignment_id),
            data={
                "shift_type": "Day",
                "planned_start_time": "10:00",
                "planned_end_time": "15:00",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=staff", response.location)
        parent = self.rows("SELECT * FROM schedule_shifts WHERE schedule_shift_id = ?", (parent_id,))[0]
        self.assertEqual((parent["planned_start_time"], parent["planned_end_time"]), ("08:00", "16:00"))
        rows = self.rows("SELECT user_id, planned_start_time, planned_end_time FROM schedule_staff ORDER BY user_id")
        self.assertEqual([(row[0], row[1], row[2]) for row in rows], [(2, "10:00", "15:00"), (3, "12:00", "14:00")])

    def test_staff_view_remove_deletes_only_assignment_and_preserves_parent(self):
        parent_id = self.add_shift(
            shift_date="2026-08-09", start="08:00", end="16:00"
        )
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute(
            "UPDATE schedule_shifts SET status = 'Published', notes = 'Keep this note' "
            "WHERE schedule_shift_id = ?", (parent_id,)
        )
        conn.commit()
        conn.close()
        self.add_assignment(parent_id, 2, "09:00", "11:00")
        self.add_assignment(parent_id, 3, "12:00", "14:00")
        assignment_id = self.rows(
            "SELECT schedule_staff_id FROM schedule_staff WHERE user_id = 2"
        )[0][0]
        self.login()
        response = self.client.post(self.staff_remove_url(assignment_id))
        self.assertEqual(response.status_code, 302)
        self.assertIn("view=staff", response.location)
        self.assertEqual(
            [row[0] for row in self.rows("SELECT user_id FROM schedule_staff")],
            [3],
        )
        parent = self.rows(
            "SELECT status, planned_start_time, planned_end_time, notes "
            "FROM schedule_shifts WHERE schedule_shift_id = ?", (parent_id,)
        )[0]
        self.assertEqual(
            tuple(parent), ("Published", "08:00", "16:00", "Keep this note")
        )
        log = self.rows(
            "SELECT activity_type, related_table, related_id, details, storyline_visible "
            "FROM activity_log WHERE activity_type = 'schedule_staff_removed'"
        )[0]
        self.assertEqual(tuple(log), (
            "schedule_staff_removed", "schedule_staff", assignment_id,
            "Worker user ID: 2", 0,
        ))

    def test_staff_view_remove_last_assignment_keeps_parent_and_replay_is_safe(self):
        parent_id = self.add_shift(shift_date="2026-08-09")
        self.add_assignment(parent_id, 2, "09:00", "11:00")
        assignment_id = self.rows("SELECT schedule_staff_id FROM schedule_staff")[0][0]
        self.login()
        response = self.client.post(self.staff_remove_url(assignment_id))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.rows("SELECT * FROM schedule_staff"), [])
        self.assertEqual(
            len(self.rows("SELECT * FROM schedule_shifts WHERE schedule_shift_id = ?", (parent_id,))),
            1,
        )
        response = self.client.post(self.staff_remove_url(assignment_id))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            len(self.rows("SELECT * FROM activity_log WHERE activity_type = 'schedule_staff_removed'")),
            1,
        )

    def test_staff_view_remove_roles_and_scope_protection(self):
        for user_id in (1, 6, 7):
            shift_type = {1: "Day", 6: "Afternoon", 7: "Overnight"}[user_id]
            parent_id = self.add_shift(shift_date="2026-08-09", shift_type=shift_type)
            self.add_assignment(parent_id, 2)
            assignment_id = self.rows(
                "SELECT schedule_staff_id FROM schedule_staff ORDER BY schedule_staff_id DESC"
            )[0][0]
            self.login(user_id)
            response = self.client.post(self.staff_remove_url(assignment_id))
            self.assertEqual(response.status_code, 302)

        parent_id = self.add_shift(shift_date="2026-08-09", client_id=20)
        self.add_assignment(parent_id, 2)
        assignment_id = self.rows(
            "SELECT schedule_staff_id FROM schedule_staff ORDER BY schedule_staff_id DESC"
        )[0][0]
        self.login()
        self.assertEqual(self.client.post(self.staff_remove_url(assignment_id)).status_code, 404)
        parent_id = self.add_shift(shift_date="2026-08-10")
        self.add_assignment(parent_id, 2)
        assignment_id = self.rows(
            "SELECT schedule_staff_id FROM schedule_staff ORDER BY schedule_staff_id DESC"
        )[0][0]
        self.assertEqual(
            self.client.post(self.staff_remove_url(assignment_id, monday="2026-08-03")).status_code,
            400,
        )

        self.login(8)
        self.assertEqual(self.client.post(self.staff_remove_url(assignment_id)).status_code, 403)

    def test_staff_view_remove_rejects_past_closed_and_cancelled(self):
        cases = (
            ("2026-08-08", "Day", "Published"),
            ("2026-08-09", "Afternoon", "Closed"),
            ("2026-08-09", "Overnight", "Cancelled"),
        )
        self.login()
        for shift_date, shift_type, status in cases:
            parent_id = self.add_shift(
                shift_date=shift_date, shift_type=shift_type
            )
            self.add_assignment(parent_id, 2)
            conn = sqlite3.connect(app.DB_NAME)
            conn.execute(
                "UPDATE schedule_shifts SET status = ? WHERE schedule_shift_id = ?",
                (status, parent_id),
            )
            conn.commit()
            conn.close()
            assignment_id = self.rows(
                "SELECT schedule_staff_id FROM schedule_staff ORDER BY schedule_staff_id DESC"
            )[0][0]
            response = self.client.post(self.staff_remove_url(assignment_id))
            self.assertEqual(response.status_code, 403)
        self.assertEqual(
            len(self.rows("SELECT * FROM schedule_staff")), 3
        )

    def test_staff_view_remove_ui_and_position_markup(self):
        parent_id = self.add_shift(shift_date="2026-08-09")
        self.add_assignment(parent_id, 2)
        page = self.page(query="?view=staff").data.decode()
        self.assertIn('id="schedule-staff-worker-2"', page)
        self.assertIn('method="post"', page)
        self.assertIn("staff-assignment/1/remove", page)
        self.assertIn("data-confirm-remove=\"Remove Anne Worker", page)
        self.assertIn("schedule-staff-matrix-add", page)
        self.assertIn("schedule-staff-view-position", page)
        self.assertIn("viewportTop", page)
        self.assertIn("scrollLeft", page)
        self.assertIn("requestAnimationFrame", page)

    def test_validation_and_past_closed_cancelled_protection(self):
        self.login()
        response = self.client.post(
            self.staff_new_url(2),
            data={"shift_type": "", "planned_start_time": "10:00", "planned_end_time": "10:00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"A valid shift type is required", response.data)
        response = self.client.post(
            self.staff_new_url(2),
            data={"shift_type": "Overnight", "planned_start_time": "22:00", "planned_end_time": "22:00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cannot be equal", response.data)

        for shift_type in ("Day", "Afternoon"):
            response = self.client.post(
                self.staff_new_url(2),
                data={
                    "shift_type": shift_type,
                    "planned_start_time": "16:00",
                    "planned_end_time": "08:00",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"must be later", response.data)

        response = self.client.post(
            self.staff_new_url(2),
            data={
                "shift_type": "Overnight",
                "planned_start_time": "22:00",
                "planned_end_time": "06:00",
            },
        )
        self.assertEqual(response.status_code, 302)

        closed_id = self.add_shift(shift_date="2026-08-09")
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute("UPDATE schedule_shifts SET status = 'Closed' WHERE schedule_shift_id = ?", (closed_id,))
        conn.commit()
        conn.close()
        response = self.client.post(
            self.staff_new_url(2),
            data={"shift_type": "Day", "planned_start_time": "08:00", "planned_end_time": "16:00"},
        )
        self.assertEqual(response.status_code, 403)

        cancelled_id = self.add_shift(
            shift_date="2026-08-09", shift_type="Afternoon"
        )
        conn = sqlite3.connect(app.DB_NAME)
        conn.execute(
            "UPDATE schedule_shifts SET status = 'Cancelled' WHERE schedule_shift_id = ?",
            (cancelled_id,),
        )
        conn.commit()
        conn.close()
        response = self.client.post(
            self.staff_new_url(2),
            data={
                "shift_type": "Afternoon",
                "planned_start_time": "16:00",
                "planned_end_time": "23:00",
            },
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
