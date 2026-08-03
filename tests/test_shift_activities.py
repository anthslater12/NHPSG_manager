import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import add_shift_activities_table
import app


class ShiftActivitiesTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "activities.db"
        )
        self.original_database_name = app.DB_NAME
        self.addCleanup(self.restore_application_state)
        app.DB_NAME = self.database_path
        app.app.config.update(TESTING=True)
        self.create_database()
        self.client = app.app.test_client()

    def restore_application_state(self):
        app.DB_NAME = self.original_database_name

    def create_database(self):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.executescript("""
                PRAGMA foreign_keys = ON;

                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL
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

                CREATE TABLE shift_staff (
                    shift_staff_id INTEGER PRIMARY KEY,
                    shift_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    active INTEGER NOT NULL
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
                    success INTEGER
                );

                CREATE TABLE acknowledgements (
                    acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_table TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    comment TEXT,
                    acknowledgement_type TEXT DEFAULT 'Read',
                    active INTEGER NOT NULL DEFAULT 1,
                    invalidated_at_utc TEXT,
                    invalidated_by_user_id INTEGER,
                    invalidation_reason TEXT
                );

                CREATE UNIQUE INDEX
                    ux_acknowledgements_active_source_user
                ON acknowledgements(source_table, source_id, user_id)
                WHERE active = 1;

                CREATE TABLE action_items (
                    action_id INTEGER PRIMARY KEY,
                    title TEXT,
                    due_date TEXT,
                    priority TEXT,
                    status TEXT,
                    assigned_to_user_id INTEGER,
                    created_at TEXT
                );

                CREATE TABLE shift_notes (
                    note_id INTEGER PRIMARY KEY,
                    client_id INTEGER,
                    user_id INTEGER,
                    shift_date TEXT,
                    shift_type TEXT
                );

                CREATE TABLE incident_reports (
                    incident_id INTEGER PRIMARY KEY,
                    incident_type TEXT,
                    incident_date TEXT,
                    incident_time TEXT,
                    client_id INTEGER
                );

                INSERT INTO users VALUES
                    (1, 'Worker One', 'Support Worker', 1),
                    (2, 'Worker Two', 'Support Worker', 1),
                    (3, 'Unassigned Worker', 'Support Worker', 1),
                    (4, 'Signed Off Worker', 'Support Worker', 1),
                    (5, 'Inactive Worker', 'Support Worker', 0),
                    (6, 'Admin User', 'Admin', 1),
                    (7, 'Manager User', 'Program Manager', 1),
                    (8, 'Director User', 'Director', 1),
                    (9, 'Inactive Manager', 'Admin', 0);

                INSERT INTO clients VALUES
                    (1, 'Client One', 1),
                    (2, 'Inactive Client', 0);

                INSERT INTO shifts VALUES
                    (10, 1, '2026-08-03', 'Day', 'Open'),
                    (20, 1, '2026-08-04', 'Day', 'Closed'),
                    (30, 1, '2026-08-05', 'Day', 'Cancelled'),
                    (40, 2, '2026-08-06', 'Day', 'Open');

                INSERT INTO shift_staff VALUES
                    (1, 10, 1, 1),
                    (2, 10, 2, 1),
                    (3, 10, 4, 0),
                    (4, 20, 1, 1),
                    (5, 30, 1, 1),
                    (6, 40, 1, 1);
            """)
            add_shift_activities_table.migrate(conn)
            conn.commit()
        finally:
            conn.close()

    def login(self, user_id, role="Support Worker"):
        with self.client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = role
            session_data["full_name"] = f"User {user_id}"

    def valid_form(self, **overrides):
        values = {
            "start_time": "09:00",
            "end_time": "10:00",
            "a_selected": "1",
            "activity_description": "Community walk",
        }
        values.update(overrides)
        return values

    def post_activity(self, shift_id=10, **overrides):
        return self.client.post(
            f"/shift/{shift_id}/activity",
            data=self.valid_form(**overrides)
        )

    def rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            return [
                dict(row)
                for row in conn.execute(sql, parameters).fetchall()
            ]
        finally:
            conn.close()

    def insert_activity(self, shift_id=10, user_id=1, description="Existing"):
        conn = sqlite3.connect(self.database_path)
        try:
            cursor = conn.execute("""
                INSERT INTO shift_activities
                (
                    shift_id, recorded_by_user_id, start_time, end_time,
                    a_selected, t_selected, ls_selected,
                    activity_description
                )
                VALUES (?, ?, '09:00', '10:00', 1, 0, 0, ?)
            """, (shift_id, user_id, description))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def test_one_two_and_three_checkbox_combinations_append(self):
        self.login(1)
        forms = (
            {"a_selected": "1"},
            {"a_selected": "1", "t_selected": "1"},
            {"a_selected": "1", "t_selected": "1", "ls_selected": "1"},
        )
        for index, categories in enumerate(forms):
            data = {
                "start_time": f"{index + 8:02d}:00",
                "end_time": f"{index + 8:02d}:30",
                "activity_description": f"Activity {index}",
                **categories,
            }
            self.assertEqual(
                self.client.post("/shift/10/activity", data=data).status_code,
                302
            )

        entries = self.rows("""
            SELECT a_selected, t_selected, ls_selected,
                   activity_description, recorded_by_user_id, created_at
            FROM shift_activities
            ORDER BY shift_activity_id
        """)
        self.assertEqual(len(entries), 3)
        self.assertEqual(
            [(e["a_selected"], e["t_selected"], e["ls_selected"]) for e in entries],
            [(1, 0, 0), (1, 1, 0), (1, 1, 1)]
        )
        self.assertTrue(all(e["recorded_by_user_id"] == 1 for e in entries))
        self.assertTrue(all(e["created_at"] for e in entries))

    def test_validation_rejects_no_category_blank_description_and_bad_times(self):
        self.login(1)
        invalid_forms = (
            {
                "start_time": "09:00",
                "end_time": "10:00",
                "activity_description": "No category",
            },
            self.valid_form(activity_description=" \t\r\n"),
            self.valid_form(start_time="9:00"),
            self.valid_form(end_time="09:00"),
            self.valid_form(start_time="10:00", end_time="09:00"),
        )
        for data in invalid_forms:
            with self.subTest(data=data):
                response = self.client.post("/shift/10/activity", data=data)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows("SELECT * FROM shift_activities"), [])

    def test_unknown_duplicate_and_server_controlled_fields_are_rejected(self):
        self.login(1)
        self.assertEqual(
            self.post_activity(user_id="2").status_code,
            400
        )
        response = self.client.post(
            "/shift/10/activity",
            data={
                "start_time": ["09:00", "09:30"],
                "end_time": "10:00",
                "a_selected": "1",
                "activity_description": "Duplicate",
            }
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows("SELECT * FROM shift_activities"), [])

    def test_assigned_workers_share_entries_and_always_append(self):
        self.login(1)
        self.assertEqual(self.post_activity().status_code, 302)
        self.assertEqual(
            self.post_activity(
                start_time="10:00",
                end_time="11:00",
                activity_description="Second activity"
            ).status_code,
            302
        )
        self.login(2)
        response = self.client.get("/shift/10/activity")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Community walk", response.data)
        self.assertIn(b"Second activity", response.data)
        self.assertEqual(
            len(self.rows("SELECT * FROM shift_activities")),
            2
        )

    def test_creation_denied_for_unauthorized_workers_roles_and_client(self):
        cases = (
            (3, "Support Worker", 10),
            (4, "Support Worker", 10),
            (5, "Support Worker", 10),
            (6, "Admin", 10),
            (1, "Support Worker", 40),
        )
        for user_id, role, shift_id in cases:
            with self.subTest(user_id=user_id, shift_id=shift_id):
                self.login(user_id, role)
                self.assertEqual(
                    self.post_activity(shift_id=shift_id).status_code,
                    403
                )
        self.assertEqual(self.rows("SELECT * FROM shift_activities"), [])

    def test_unauthenticated_redirects(self):
        self.assertEqual(
            self.client.get("/shift/10/activity").status_code,
            302
        )
        self.assertEqual(
            self.client.post("/shift/10/activity").status_code,
            302
        )

    def test_closed_and_cancelled_shifts_are_read_only(self):
        for shift_id in (20, 30):
            with self.subTest(shift_id=shift_id):
                self.insert_activity(
                    shift_id=shift_id,
                    description=f"Historical {shift_id}"
                )
                self.login(1)
                response = self.client.get(f"/shift/{shift_id}/activity")
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    f"Historical {shift_id}".encode(),
                    response.data
                )
                self.assertNotIn(b"<form method=\"post\">", response.data)
                self.assertEqual(
                    self.post_activity(shift_id=shift_id).status_code,
                    403
                )

    def test_creation_and_audit_are_atomic(self):
        self.login(1)
        self.assertEqual(self.post_activity().status_code, 302)
        activities = self.rows("SELECT * FROM shift_activities")
        audits = self.rows("""
            SELECT * FROM activity_log
            WHERE activity_type = 'shift_activity_created'
        """)
        self.assertEqual(len(activities), 1)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["related_table"], "shift_activities")
        self.assertEqual(
            audits[0]["related_id"],
            activities[0]["shift_activity_id"]
        )
        self.assertEqual(audits[0]["summary"], "Community walk")
        self.assertEqual(audits[0]["details"], "A")

        with mock.patch.object(
            app,
            "log_activity",
            side_effect=RuntimeError("audit failed")
        ):
            with self.assertRaises(RuntimeError):
                self.post_activity(
                    start_time="10:00",
                    end_time="11:00"
                )
        self.assertEqual(
            len(self.rows("SELECT * FROM shift_activities")),
            1
        )

    def test_management_access_and_independent_idempotent_reviews(self):
        activity_id = self.insert_activity()

        for user_id, role in (
            (6, "Admin"),
            (7, "Program Manager"),
            (8, "Director"),
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                self.assertEqual(
                    self.client.get("/manager-review/activities").status_code,
                    200
                )
                self.assertEqual(
                    self.client.get(
                        f"/manager-review/activities/{activity_id}"
                    ).status_code,
                    200
                )

        self.login(1)
        self.assertEqual(
            self.client.get("/manager-review/activities").status_code,
            403
        )
        self.login(9, "Admin")
        self.assertEqual(
            self.client.get("/manager-review/activities").status_code,
            403
        )

        self.login(6, "Admin")
        review_url = (
            f"/manager-review/activities/{activity_id}/review"
        )
        self.assertEqual(self.client.post(review_url).status_code, 302)
        self.assertEqual(self.client.post(review_url).status_code, 302)
        self.login(7, "Program Manager")
        detail = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertIn(b"Admin User", detail.data)
        self.assertIn(b"Mark as Reviewed", detail.data)
        self.assertEqual(self.client.post(review_url).status_code, 302)

        reviews = self.rows("""
            SELECT source_table, source_id, user_id,
                   acknowledgement_type, active
            FROM acknowledgements
            WHERE source_table = 'shift_activities'
            ORDER BY user_id
        """)
        self.assertEqual(len(reviews), 2)
        self.assertEqual([row["user_id"] for row in reviews], [6, 7])
        self.assertTrue(all(
            row["source_id"] == activity_id
            and row["acknowledgement_type"] == "Review"
            and row["active"] == 1
            for row in reviews
        ))
        review_audits = self.rows("""
            SELECT user_id, client_id, shift_id, summary
            FROM activity_log
            WHERE activity_class = 'ACKNOWLEDGEMENT'
              AND activity_type = 'record_acknowledged'
            ORDER BY activity_id
        """)
        self.assertEqual(len(review_audits), 2)
        self.assertEqual(
            [audit["user_id"] for audit in review_audits],
            [6, 7]
        )
        self.assertTrue(all(
            audit["client_id"] == 1
            and audit["shift_id"] == 10
            and audit["summary"] == "Review acknowledgement recorded"
            for audit in review_audits
        ))

    def test_personal_dashboard_count_and_preview(self):
        activity_ids = [
            self.insert_activity(description=f"Activity {index}")
            for index in range(7)
        ]
        conn = sqlite3.connect(self.database_path)
        try:
            conn.executemany("""
                INSERT INTO acknowledgements
                    (source_table, source_id, user_id,
                     acknowledgement_type, active)
                VALUES ('shift_activities', ?, ?, 'Review', ?)
            """, (
                (activity_ids[0], 6, 1),
                (activity_ids[1], 6, 0),
                (activity_ids[0], 7, 1),
                (activity_ids[1], 7, 1),
            ))
            conn.commit()
        finally:
            conn.close()

        admin_stats = app.get_dashboard_stats(6)
        admin_inbox = app.get_management_inbox(6)
        manager_stats = app.get_dashboard_stats(7)
        manager_inbox = app.get_management_inbox(7)

        self.assertEqual(admin_stats["activities_to_review"], 6)
        self.assertEqual(
            len(admin_inbox["activities_to_review_list"]),
            5
        )
        self.assertEqual(manager_stats["activities_to_review"], 5)
        self.assertEqual(
            len(manager_inbox["activities_to_review_list"]),
            5
        )


if __name__ == "__main__":
    unittest.main()
