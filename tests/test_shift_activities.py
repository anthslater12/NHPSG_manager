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
                    status TEXT NOT NULL,
                    scheduled_end_time TEXT
                );

                CREATE TABLE shift_staff (
                    shift_staff_id INTEGER PRIMARY KEY,
                    shift_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    active INTEGER NOT NULL,
                    actual_start_time TEXT,
                    actual_end_at_utc TEXT,
                    sign_on_at TEXT,
                    sign_off_at TEXT
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
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'Open',
                    priority TEXT DEFAULT 'Medium',
                    source_table TEXT,
                    source_id INTEGER,
                    assigned_to_user_id INTEGER,
                    created_by_user_id INTEGER,
                    due_date TEXT,
                    acknowledged_at TEXT,
                    completed_at TEXT,
                    closed_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    shift_id INTEGER
                );

                CREATE TABLE action_comments (
                    comment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    comment TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE management_notes (
                    management_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_table TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    note_text TEXT NOT NULL,
                    visibility TEXT NOT NULL DEFAULT 'management_only',
                    created_by_user_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    active INTEGER NOT NULL DEFAULT 1,
                    shared_at TEXT,
                    shared_by_user_id INTEGER
                );

                CREATE TABLE shift_notes (
                    note_id INTEGER PRIMARY KEY,
                    client_id INTEGER,
                    user_id INTEGER,
                    shift_date TEXT,
                    shift_type TEXT,
                    created_at TEXT,
                    note_text TEXT
                );

                CREATE TABLE shift_care_task_entries (
                    entry_id INTEGER PRIMARY KEY,
                    care_task_id INTEGER,
                    shift_id INTEGER,
                    completed_by_user_id INTEGER,
                    completed_at TEXT,
                    outcome TEXT,
                    comment TEXT
                );

                CREATE TABLE shift_housekeeping_task_entries (
                    entry_id INTEGER PRIMARY KEY,
                    housekeeping_task_id INTEGER,
                    shift_id INTEGER,
                    completed_by_user_id INTEGER,
                    completed_at TEXT,
                    outcome TEXT,
                    comment TEXT
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
                    (9, 'Consultant User', 'Behaviour Consultant', 1),
                    (10, 'Inactive Manager', 'Admin', 0);

                INSERT INTO clients VALUES
                    (1, 'Client One', 1),
                    (2, 'Inactive Client', 0);

                INSERT INTO shifts
                    (shift_id, client_id, shift_date, shift_type, status)
                VALUES
                    (10, 1, '2026-08-03', 'Day', 'Open'),
                    (20, 1, '2026-08-04', 'Day', 'Closed'),
                    (30, 1, '2026-08-05', 'Day', 'Cancelled'),
                    (40, 2, '2026-08-06', 'Day', 'Open');

                INSERT INTO shift_staff
                    (shift_staff_id, shift_id, user_id, active,
                     actual_start_time, sign_on_at)
                VALUES
                    (1, 10, 1, 1, '09:00', '2026-08-03T16:00:00Z'),
                    (2, 10, 2, 1, '09:00', '2026-08-03T16:00:00Z'),
                    (3, 10, 4, 0, NULL, NULL),
                    (4, 20, 1, 1, NULL, NULL),
                    (5, 30, 1, 1, NULL, NULL),
                    (6, 40, 1, 1, NULL, NULL);
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
            "lifecycle_action": "recorded",
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

    def insert_in_progress_activity(
        self, shift_id=10, user_id=1, start_time="09:00",
        end_time=None, a_selected=1, t_selected=0, ls_selected=0,
        description="Draft activity", version=1
    ):
        conn = sqlite3.connect(self.database_path)
        try:
            cursor = conn.execute("""
                INSERT INTO shift_activities (
                    shift_id, recorded_by_user_id, start_time, end_time,
                    a_selected, t_selected, ls_selected,
                    activity_description, status, completed_at_utc,
                    completed_by_user_id, version_number
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'In Progress', NULL, NULL, ?)
            """, (
                shift_id, user_id, start_time, end_time,
                a_selected, t_selected, ls_selected, description, version,
            ))
            conn.execute("""
                INSERT INTO activity_log (
                    activity_class, activity_type, user_id, client_id,
                    shift_id, related_table, related_id, summary, details,
                    success
                ) VALUES (
                    'ACTIVITY', 'shift_activity_created', ?, 1, ?,
                    'shift_activities', ?, ?, 'Status: In Progress', 1
                )
            """, (
                user_id, shift_id, cursor.lastrowid,
                description or "Activity saved in progress",
            ))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def insert_completed_activity(self, shift_id=10, user_id=1):
        conn = sqlite3.connect(self.database_path)
        try:
            cursor = conn.execute("""
                INSERT INTO shift_activities (
                    shift_id, recorded_by_user_id, start_time, end_time,
                    a_selected, t_selected, ls_selected,
                    activity_description, status, completed_at_utc,
                    completed_by_user_id, version_number
                ) VALUES (?, ?, '09:00', '10:00', 1, 0, 0,
                          'Completed activity', 'Completed',
                          '2026-08-03T17:00:00Z', ?, 2)
            """, (shift_id, user_id, user_id))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def activity_row(self, activity_id):
        return self.rows(
            "SELECT * FROM shift_activities WHERE shift_activity_id = ?",
            (activity_id,),
        )[0]

    def edit_payload(self, activity_id, action="save", expected_version=1, **overrides):
        values = {
            "action": action,
            "expected_version": str(expected_version),
            "start_time": "09:00",
            "end_time": "",
            "a_selected": "1",
            "activity_description": "Draft activity",
        }
        values.update(overrides)
        return values

    def dashboard(self, user_id=1, role="Support Worker", shift_id=10):
        self.login(user_id, role=role)
        patches = (
            mock.patch.object(
                app,
                "get_worker_documentation_context_state",
                return_value={"selected": None, "available": []},
            ),
            mock.patch.object(
                app, "get_behaviour_in_progress_resume_records", return_value=[]
            ),
            mock.patch.object(
                app,
                "_get_authenticated_staff_notice_recipient",
                return_value={"user_id": user_id},
            ),
            mock.patch.object(
                app,
                "_get_staff_notice_recipient_collections",
                return_value={"dashboard": [], "outstanding_count": 0},
            ),
            mock.patch.object(
                app, "reconcile_staff_notice_non_shift_requirements_in_transaction"
            ),
            mock.patch.object(
                app, "get_active_food_fluid_shift_context", side_effect=PermissionError
            ),
            mock.patch.object(
                app, "get_active_sleep_shift_context", side_effect=PermissionError
            ),
            mock.patch.object(app, "get_food_fluid_shift_entries", return_value=[]),
            mock.patch.object(app, "get_sleep_events", return_value=[]),
            mock.patch.object(app, "get_applicable_care_tasks", return_value=[]),
            mock.patch.object(
                app, "get_applicable_housekeeping_tasks", return_value=[]
            ),
        )
        with (
            patches[0], patches[1], patches[2], patches[3], patches[4],
            patches[5], patches[6], patches[7], patches[8], patches[9],
            patches[10]
        ):
            return self.client.get(f"/shift/{shift_id}")

    def test_current_shift_shows_creator_activity_resume_link(self):
        activity_id = self.insert_in_progress_activity()

        page = self.dashboard().data

        self.assertIn(b"Activities In Progress", page)
        self.assertIn(b"09:00", page)
        self.assertIn(b"A", page)
        self.assertIn(b"Draft activity", page)
        self.assertIn(b"In Progress", page)
        self.assertIn(
            f"/shift/10/activity/{activity_id}/edit".encode(),
            page,
        )
        self.assertIn(b"Continue Activity", page)

    def test_current_shift_resume_excludes_other_workers_activity(self):
        own_id = self.insert_in_progress_activity(user_id=1)
        other_id = self.insert_in_progress_activity(user_id=2)

        page = self.dashboard().data

        self.assertIn(f"/shift/10/activity/{own_id}/edit".encode(), page)
        self.assertNotIn(f"/shift/10/activity/{other_id}/edit".encode(), page)

    def test_current_shift_resume_excludes_recorded_and_completed_activity(self):
        self.insert_activity(user_id=1)
        self.insert_completed_activity(user_id=1)

        page = self.dashboard().data

        self.assertNotIn(b"Activities In Progress", page)
        self.assertNotIn(b"Continue Activity", page)

    def test_completed_activity_disappears_from_current_shift_resume(self):
        activity_id = self.insert_in_progress_activity()
        self.login(1)

        response = self.client.post(
            f"/shift/10/activity/{activity_id}/edit",
            data=self.edit_payload(
                activity_id,
                action="complete",
                end_time="10:00",
            ),
        )

        self.assertEqual(response.status_code, 302)
        page = self.dashboard().data
        self.assertNotIn(b"Activities In Progress", page)
        self.assertNotIn(b"Continue Activity", page)

    def test_current_shift_resume_excludes_ineligible_workers_and_shifts(self):
        self.insert_in_progress_activity()

        self.assertNotIn(
            b"Activities In Progress",
            self.dashboard(user_id=3).data,
        )

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            with self.assertRaises(PermissionError):
                app.get_shift_activity_in_progress_resume_records(conn, 10, 5)
        finally:
            conn.close()

        conn = sqlite3.connect(self.database_path)
        conn.execute(
            "UPDATE shifts SET status = 'Closed' WHERE shift_id = 10"
        )
        conn.commit()
        conn.close()
        self.assertNotIn(b"Activities In Progress", self.dashboard().data)

    def test_management_roles_do_not_receive_activity_resume_controls(self):
        self.insert_in_progress_activity()

        for user_id, role in (
            (6, "Admin"),
            (7, "Program Manager"),
            (8, "Director"),
            (9, "Behaviour Consultant"),
        ):
            with self.subTest(user_id=user_id, role=role):
                page = self.dashboard(user_id=user_id, role=role).data
                self.assertNotIn(b"Activities In Progress", page)
                self.assertNotIn(b"Continue Activity", page)

    def test_one_two_and_three_checkbox_combinations_append(self):
        self.login(1)
        forms = (
            {"a_selected": "1"},
            {"a_selected": "1", "t_selected": "1"},
            {"a_selected": "1", "t_selected": "1", "ls_selected": "1"},
        )
        for index, categories in enumerate(forms):
            data = {
                "lifecycle_action": "recorded",
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

    def test_activity_form_has_explicit_recorded_and_in_progress_actions(self):
        self.login(1)
        response = self.client.get("/shift/10/activity")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Save In Progress", response.data)
        self.assertIn(b"Record Activity", response.data)
        self.assertIn(b'name="lifecycle_action"', response.data)

    def test_lifecycle_action_is_required_single_and_known(self):
        self.login(1)
        valid = self.valid_form()
        invalid_forms = (
            {key: value for key, value in valid.items()
             if key != "lifecycle_action"},
            {**valid, "lifecycle_action": "unknown"},
            {**valid, "lifecycle_action": ["recorded", "in_progress"]},
        )
        for data in invalid_forms:
            with self.subTest(data=data):
                self.assertEqual(
                    self.client.post("/shift/10/activity", data=data).status_code,
                    400,
                )
        self.assertEqual(
            self.rows("SELECT COUNT(*) AS count FROM shift_activities")[0]["count"],
            0,
        )

    def test_in_progress_creation_persists_partial_data_and_one_creation_audit(self):
        self.login(1)
        category_only = self.client.post("/shift/10/activity", data={
            "lifecycle_action": "in_progress",
            "start_time": "09:00",
            "end_time": "",
            "a_selected": "1",
            "activity_description": "",
        })
        description_only = self.client.post("/shift/10/activity", data={
            "lifecycle_action": "in_progress",
            "start_time": "10:00",
            "activity_description": "Partial activity notes",
        })
        self.assertEqual(category_only.status_code, 302)
        self.assertEqual(description_only.status_code, 302)

        rows = self.rows("""
            SELECT shift_id, recorded_by_user_id, start_time, end_time,
                   a_selected, t_selected, ls_selected, activity_description,
                   status, completed_at_utc, completed_by_user_id, version_number
            FROM shift_activities
            ORDER BY shift_activity_id
        """)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [(row["status"], row["end_time"], row["activity_description"])
             for row in rows],
            [("In Progress", None, ""),
             ("In Progress", None, "Partial activity notes")],
        )
        self.assertTrue(all(row["shift_id"] == 10 for row in rows))
        self.assertTrue(all(row["recorded_by_user_id"] == 1 for row in rows))
        self.assertTrue(all(row["completed_at_utc"] is None for row in rows))
        self.assertTrue(all(row["completed_by_user_id"] is None for row in rows))
        self.assertTrue(all(row["version_number"] == 1 for row in rows))

        audits = self.rows("""
            SELECT activity_type, summary, details, related_id
            FROM activity_log
            ORDER BY activity_id
        """)
        self.assertEqual(len(audits), 2)
        self.assertTrue(all(
            audit["activity_type"] == "shift_activity_created"
            for audit in audits
        ))
        self.assertEqual(audits[0]["summary"], "Activity saved in progress")
        self.assertIn("Status: In Progress", audits[0]["details"])
        self.assertIn("Categories: A", audits[0]["details"])
        self.assertIn("Partial activity notes", audits[1]["details"])
        self.assertTrue(all(audit["related_id"] for audit in audits))

    def test_in_progress_validation_allows_missing_end_but_rejects_bad_end_or_meaningless_data(self):
        self.login(1)
        invalid_forms = (
            {
                "lifecycle_action": "in_progress",
                "start_time": "",
                "a_selected": "1",
            },
            {
                "lifecycle_action": "in_progress",
                "start_time": "09:00",
                "end_time": "9:00",
                "a_selected": "1",
            },
            {
                "lifecycle_action": "in_progress",
                "start_time": "09:00",
                "end_time": "",
            },
        )
        for data in invalid_forms:
            with self.subTest(data=data):
                self.assertEqual(
                    self.client.post("/shift/10/activity", data=data).status_code,
                    400,
                )
        self.assertEqual(
            self.client.post("/shift/10/activity", data={
                "lifecycle_action": "in_progress",
                "start_time": "09:00",
                "a_selected": "1",
            }).status_code,
            302,
        )

    def test_worker_activity_list_displays_lifecycle_status(self):
        self.login(1)
        self.assertEqual(
            self.client.post("/shift/10/activity", data={
                "lifecycle_action": "in_progress",
                "start_time": "09:00",
                "a_selected": "1",
            }).status_code,
            302,
        )
        self.assertEqual(
            self.post_activity(start_time="10:00", end_time="11:00").status_code,
            302,
        )
        response = self.client.get("/shift/10/activity")
        self.assertIn(b"In Progress", response.data)
        self.assertIn(b"Recorded", response.data)

    def test_creator_can_get_edit_page_and_form_has_save_actions(self):
        activity_id = self.insert_in_progress_activity()
        self.login(1)
        response = self.client.get(
            f"/shift/10/activity/{activity_id}/edit"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="expected_version" value="1"', response.data)
        self.assertIn(b'name="action" value="save"', response.data)
        self.assertIn(b'name="action" value="complete"', response.data)
        self.assertIn(b"Save &amp; Complete", response.data)
        self.assertIn(b"Draft activity", response.data)

    def test_authoritative_documentation_context_allows_activity_edit_and_resume(self):
        activity_id = self.insert_in_progress_activity()
        self.login(1)
        with self.client.session_transaction() as session_data:
            session_data[app.DOCUMENTATION_CONTEXT_SESSION_KEY] = 10

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            context = app.get_worker_documentation_shift_context(conn, 10, 1)
            context_state = app.get_worker_documentation_context_state(
                conn, 1, selected_shift_id=10
            )
        finally:
            conn.close()
        self.assertIsNotNone(context)
        self.assertNotIn("client_active", context)
        self.assertIsNotNone(context_state["selected"])
        self.assertNotIn("client_active", context_state["selected"])

        edit_url = f"/shift/10/activity/{activity_id}/edit"
        self.assertEqual(self.client.get(edit_url).status_code, 200)
        self.assertEqual(
            self.client.post(
                edit_url,
                data=self.edit_payload(
                    activity_id,
                    activity_description="Updated from selected context",
                ),
            ).status_code,
            302,
        )
        self.assertEqual(
            self.activity_row(activity_id)["activity_description"],
            "Updated from selected context",
        )

        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            resumed = app.get_shift_activity_in_progress_resume_records(
                conn, 10, 1
            )
        finally:
            conn.close()
        self.assertEqual([row["shift_activity_id"] for row in resumed], [activity_id])

        self.login(2)
        self.assertEqual(self.client.get(edit_url).status_code, 403)
        self.login(4)
        self.assertEqual(self.client.get(edit_url).status_code, 403)

        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("UPDATE shifts SET status = 'Closed' WHERE shift_id = 10")
            conn.commit()
        finally:
            conn.close()
        self.login(1)
        self.assertEqual(self.client.get(edit_url).status_code, 403)

    def test_only_active_creator_can_edit_an_in_progress_activity(self):
        activity_id = self.insert_in_progress_activity()
        edit_url = f"/shift/10/activity/{activity_id}/edit"
        denied = (
            (2, "Support Worker"),
            (3, "Support Worker"),
            (5, "Support Worker"),
            (6, "Admin"),
            (7, "Program Manager"),
            (8, "Director"),
            (9, "Behaviour Consultant"),
        )
        for user_id, role in denied:
            with self.subTest(user_id=user_id, role=role):
                self.login(user_id, role)
                self.assertEqual(self.client.get(edit_url).status_code, 403)
                self.assertEqual(
                    self.client.post(
                        edit_url,
                        data=self.edit_payload(activity_id),
                    ).status_code,
                    403,
                )

        self.login(1)
        wrong_shift = self.client.get(
            f"/shift/40/activity/{activity_id}/edit"
        )
        self.assertEqual(wrong_shift.status_code, 403)

        recorded_id = self.insert_activity()
        self.assertEqual(
            self.client.get(
                f"/shift/10/activity/{recorded_id}/edit"
            ).status_code,
            403,
        )
        completed_id = self.insert_completed_activity()
        self.assertEqual(
            self.client.get(
                f"/shift/10/activity/{completed_id}/edit"
            ).status_code,
            403,
        )

    def test_edit_action_and_expected_version_are_strictly_validated(self):
        activity_id = self.insert_in_progress_activity()
        edit_url = f"/shift/10/activity/{activity_id}/edit"
        self.login(1)
        for payload in (
            dict(self.edit_payload(activity_id), action=None),
            dict(self.edit_payload(activity_id), action="unknown"),
            dict(self.edit_payload(activity_id), action=["save", "complete"]),
            dict(self.edit_payload(activity_id), expected_version="0"),
            dict(self.edit_payload(activity_id), expected_version="one"),
            dict(self.edit_payload(activity_id), expected_version=["1", "1"]),
        ):
            with self.subTest(payload=payload):
                if payload.get("action") is None:
                    payload.pop("action")
                self.assertEqual(
                    self.client.post(edit_url, data=payload).status_code,
                    400,
                )
        self.assertEqual(self.activity_row(activity_id)["version_number"], 1)
        self.assertEqual(
            self.rows("SELECT activity_type FROM activity_log"),
            [{"activity_type": "shift_activity_created"}],
        )

    def test_save_updates_activity_keeps_in_progress_and_audits_once(self):
        activity_id = self.insert_in_progress_activity()
        self.login(1)
        edit_url = f"/shift/10/activity/{activity_id}/edit"
        response = self.client.post(
            edit_url,
            data=self.edit_payload(
                activity_id,
                action="save",
                end_time="10:00",
                t_selected="1",
                activity_description="Updated draft",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/shift/10/activity/{activity_id}/edit", response.location)
        row = self.activity_row(activity_id)
        self.assertEqual(row["status"], "In Progress")
        self.assertEqual(row["end_time"], "10:00")
        self.assertEqual(row["t_selected"], 1)
        self.assertEqual(row["activity_description"], "Updated draft")
        self.assertEqual(row["version_number"], 2)
        self.assertIsNone(row["completed_at_utc"])
        audits = self.rows("""
            SELECT activity_type, details
            FROM activity_log ORDER BY activity_id
        """)
        self.assertEqual([audit["activity_type"] for audit in audits], [
            "shift_activity_created", "shift_activity_updated"
        ])
        self.assertIn("activity_description", audits[1]["details"])
        self.assertIn("Resulting version: 2", audits[1]["details"])

    def test_save_noop_is_rejected_without_version_or_audit_change(self):
        activity_id = self.insert_in_progress_activity()
        self.login(1)
        response = self.client.post(
            f"/shift/10/activity/{activity_id}/edit",
            data=self.edit_payload(activity_id),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.activity_row(activity_id)["version_number"], 1)
        self.assertEqual(
            self.rows("SELECT activity_type FROM activity_log"),
            [{"activity_type": "shift_activity_created"}],
        )

    def test_save_and_complete_persists_final_values_and_one_completion_audit(self):
        activity_id = self.insert_in_progress_activity(description="Draft")
        self.login(1)
        response = self.client.post(
            f"/shift/10/activity/{activity_id}/edit",
            data=self.edit_payload(
                activity_id,
                action="complete",
                end_time="11:00",
                a_selected=None,
                t_selected="1",
                activity_description="Final activity",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/10/activity", response.location)
        row = self.activity_row(activity_id)
        self.assertEqual(row["status"], "Completed")
        self.assertEqual(row["end_time"], "11:00")
        self.assertEqual(row["a_selected"], 0)
        self.assertEqual(row["t_selected"], 1)
        self.assertEqual(row["activity_description"], "Final activity")
        self.assertEqual(row["completed_by_user_id"], 1)
        self.assertRegex(
            row["completed_at_utc"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
        )
        self.assertEqual(row["version_number"], 2)
        audits = self.rows(
            "SELECT activity_type, details FROM activity_log ORDER BY activity_id"
        )
        self.assertEqual([audit["activity_type"] for audit in audits], [
            "shift_activity_created", "shift_activity_completed"
        ])
        self.assertIn("Status: In Progress -> Completed", audits[1]["details"])

    def test_invalid_completion_does_not_partially_persist(self):
        activity_id = self.insert_in_progress_activity()
        self.login(1)
        response = self.client.post(
            f"/shift/10/activity/{activity_id}/edit",
            data=self.edit_payload(
                activity_id,
                action="complete",
                end_time="",
                activity_description="",
                a_selected=None,
            ),
        )
        self.assertEqual(response.status_code, 400)
        row = self.activity_row(activity_id)
        self.assertEqual(row["status"], "In Progress")
        self.assertEqual(row["version_number"], 1)
        self.assertIsNone(row["completed_at_utc"])
        self.assertEqual(
            self.rows("SELECT activity_type FROM activity_log"),
            [{"activity_type": "shift_activity_created"}],
        )

    def test_stale_save_and_complete_change_nothing(self):
        activity_id = self.insert_in_progress_activity()
        self.login(1)
        edit_url = f"/shift/10/activity/{activity_id}/edit"
        self.assertEqual(
            self.client.post(
                edit_url,
                data=self.edit_payload(
                    activity_id,
                    action="save",
                    activity_description="First saved draft",
                ),
            ).status_code,
            302,
        )
        current = self.activity_row(activity_id)
        stale_save = self.edit_payload(
            activity_id,
            action="save",
            expected_version=1,
            activity_description="Stale draft",
        )
        self.assertEqual(self.client.post(edit_url, data=stale_save).status_code, 409)
        stale_complete = self.edit_payload(
            activity_id,
            action="complete",
            expected_version=1,
            end_time="10:00",
            activity_description="Stale completion",
        )
        self.assertEqual(
            self.client.post(edit_url, data=stale_complete).status_code,
            409,
        )
        unchanged = self.activity_row(activity_id)
        self.assertEqual(unchanged["activity_description"], current["activity_description"])
        self.assertEqual(unchanged["status"], "In Progress")
        self.assertEqual(unchanged["version_number"], 2)
        self.assertEqual(
            [row["activity_type"] for row in self.rows(
                "SELECT activity_type FROM activity_log ORDER BY activity_id"
            )],
            ["shift_activity_created", "shift_activity_updated"],
        )

    def test_authorization_and_state_are_rechecked_when_posting(self):
        activity_id = self.insert_in_progress_activity()
        self.login(1)
        edit_url = f"/shift/10/activity/{activity_id}/edit"
        self.client.get(edit_url)
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute(
                "UPDATE shift_staff SET active = 0 WHERE shift_id = 10 AND user_id = 1"
            )
            conn.commit()
        finally:
            conn.close()
        response = self.client.post(
            edit_url,
            data=self.edit_payload(
                activity_id, activity_description="Should not save"
            ),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.activity_row(activity_id)["version_number"], 1)

    def test_continue_link_is_visible_only_for_creator(self):
        own_id = self.insert_in_progress_activity(user_id=1)
        other_id = self.insert_in_progress_activity(user_id=2)
        recorded_id = self.insert_activity(user_id=1)
        self.login(1)
        response = self.client.get("/shift/10/activity")
        self.assertIn(
            f"/shift/10/activity/{own_id}/edit".encode(), response.data
        )
        self.assertNotIn(
            f"/shift/10/activity/{other_id}/edit".encode(), response.data
        )
        self.assertNotIn(
            f"/shift/10/activity/{recorded_id}/edit".encode(), response.data
        )

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

    def test_in_progress_creation_uses_same_database_backed_authorization(self):
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
                    self.post_activity(
                        shift_id=shift_id,
                        lifecycle_action="in_progress",
                    ).status_code,
                    403,
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
        self.login(10, "Admin")
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

    def test_in_progress_activity_is_visible_but_not_reviewable(self):
        activity_id = self.insert_in_progress_activity()
        self.login(6, "Admin")

        detail = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"<th>Status</th>", detail.data)
        self.assertIn(b"In Progress", detail.data)
        self.assertIn(
            b"cannot be reviewed until finalized",
            detail.data,
        )
        self.assertNotIn(b"Mark as Reviewed", detail.data)

        review_list = self.client.get("/manager-review/activities")
        self.assertEqual(review_list.status_code, 200)
        self.assertIn(b"Not ready for review", review_list.data)
        self.assertNotIn(
            f"/manager-review/activities/{activity_id}/review".encode(),
            review_list.data,
        )

        response = self.client.post(
            f"/manager-review/activities/{activity_id}/review"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.rows("""
                SELECT *
                FROM acknowledgements
                WHERE source_table = 'shift_activities'
                  AND source_id = ?
                  AND acknowledgement_type = 'Review'
            """, (activity_id,)),
            [],
        )
        self.assertEqual(
            self.rows("""
                SELECT *
                FROM activity_log
                WHERE activity_type = 'record_acknowledged'
                  AND related_table = 'shift_activities'
                  AND related_id = ?
            """, (activity_id,)),
            [],
        )

    def test_awaiting_review_counts_only_unreviewed_finalized_activities(self):
        in_progress_id = self.insert_in_progress_activity(
            description="Still in progress"
        )
        completed_id = self.insert_completed_activity()
        recorded_id = self.insert_activity(description="Recorded activity")

        for user_id in (6, 7, 8):
            with self.subTest(user_id=user_id):
                stats = app.get_dashboard_stats(user_id)
                inbox = app.get_management_inbox(user_id)
                self.assertEqual(stats["activities_to_review"], 2)
                inbox_descriptions = {
                    row["activity_description"]
                    for row in inbox["activities_to_review_list"]
                }
                self.assertEqual(
                    inbox_descriptions,
                    {"Completed activity", "Recorded activity"},
                )
                self.assertNotIn(
                    in_progress_id,
                    [row["shift_activity_id"]
                     for row in inbox["activities_to_review_list"]],
                )

        self.login(6, "Admin")
        review_url = f"/manager-review/activities/{completed_id}/review"
        self.assertEqual(self.client.post(review_url).status_code, 302)
        self.assertEqual(app.get_dashboard_stats(6)["activities_to_review"], 1)
        self.assertEqual(
            [row["shift_activity_id"] for row in app.get_management_inbox(6)[
                "activities_to_review_list"
            ]],
            [recorded_id],
        )

        review_url = f"/manager-review/activities/{recorded_id}/review"
        self.assertEqual(self.client.post(review_url).status_code, 302)
        self.assertEqual(app.get_dashboard_stats(6)["activities_to_review"], 0)
        self.assertEqual(
            app.get_management_inbox(6)["activities_to_review_list"],
            [],
        )

        review_list = self.client.get("/manager-review/activities")
        self.assertEqual(review_list.status_code, 200)
        self.assertIn(b"Still in progress", review_list.data)
        self.assertIn(
            f"/manager-review/activities/{in_progress_id}".encode(),
            review_list.data,
        )
        detail = self.client.get(
            f"/manager-review/activities/{in_progress_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"In Progress", detail.data)
        self.assertIn(b"cannot be reviewed until finalized", detail.data)

    def test_review_rechecks_current_activity_status_inside_transaction(self):
        activity_id = self.insert_activity()
        self.login(6, "Admin")
        detail = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertIn(b"Mark as Reviewed", detail.data)

        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute(
                "UPDATE shift_activities SET status = 'In Progress' "
                "WHERE shift_activity_id = ?",
                (activity_id,),
            )
            conn.commit()
        finally:
            conn.close()

        response = self.client.post(
            f"/manager-review/activities/{activity_id}/review"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.rows(
                "SELECT * FROM acknowledgements "
                "WHERE source_table = 'shift_activities' AND source_id = ?",
                (activity_id,),
            ),
            [],
        )

    def test_completed_activity_remains_reviewable_for_behaviour_consultant(self):
        activity_id = self.insert_completed_activity()
        self.login(9, "Behaviour Consultant")

        detail = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"<th>Status</th>", detail.data)
        self.assertIn(b"Completed", detail.data)
        self.assertIn(b"Mark as Reviewed", detail.data)

        review_url = f"/manager-review/activities/{activity_id}/review"
        self.assertEqual(self.client.post(review_url).status_code, 302)
        self.assertEqual(self.client.post(review_url).status_code, 302)
        reviews = self.rows(
            "SELECT user_id, acknowledgement_type FROM acknowledgements "
            "WHERE source_table = 'shift_activities' AND source_id = ?",
            (activity_id,),
        )
        self.assertEqual(
            reviews,
            [{"user_id": 9, "acknowledgement_type": "Review"}],
        )

    def test_management_notes_display_and_persist_for_activity(self):
        activity_id = self.insert_activity(description="Meal preparation")
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("""
                INSERT INTO management_notes (
                    source_table, source_id, note_text, visibility,
                    created_by_user_id, created_at, active
                ) VALUES (
                    'shift_activities', ?, 'Review activity support needs',
                    'management_only', 7, '2026-08-03 12:10:00', 1
                )
            """, (activity_id,))
            conn.commit()
        finally:
            conn.close()

        self.login(6, "Admin")
        detail = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Back to Management Review", detail.data)
        self.assertIn(b"Meal preparation", detail.data)
        self.assertIn(b"09:00", detail.data)
        self.assertIn(b"Management Notes", detail.data)
        self.assertIn(b"Review activity support needs", detail.data)
        self.assertIn(b"Manager User", detail.data)
        self.assertIn(b"This note is visible to management only.", detail.data)

        response = self.client.post(
            f"/manager-review/activities/{activity_id}/management-note?"
            "storyline_client_id=1&storyline_filter=Activity&"
            "storyline_page=2",
            data={"note_text": "  Confirm activity staffing.  "}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("filter=Activity", response.headers["Location"])

        rows = self.rows("""
            SELECT source_table, source_id, note_text, visibility,
                   created_by_user_id
            FROM management_notes
            WHERE source_table = 'shift_activities'
              AND source_id = ?
            ORDER BY management_note_id
        """, (activity_id,))
        self.assertEqual(rows, [{
            "source_table": "shift_activities",
            "source_id": activity_id,
            "note_text": "Review activity support needs",
            "visibility": "management_only",
            "created_by_user_id": 7,
        }, {
            "source_table": "shift_activities",
            "source_id": activity_id,
            "note_text": "Confirm activity staffing.",
            "visibility": "management_only",
            "created_by_user_id": 6,
        }])
        audit = self.rows("""
            SELECT activity_class, activity_type, user_id, shift_id,
                   related_table, related_id
            FROM activity_log
            WHERE activity_type = 'management_note_added'
        """)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["activity_class"], "MANAGEMENT_NOTE")
        self.assertEqual(audit[0]["user_id"], 6)
        self.assertEqual(audit[0]["shift_id"], 10)
        self.assertEqual(audit[0]["related_table"], "management_notes")

    def test_behaviour_consultant_can_add_activity_management_note(self):
        activity_id = self.insert_activity()
        self.login(9, "Behaviour Consultant")
        detail = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Add Management Note", detail.data)
        response = self.client.post(
            f"/manager-review/activities/{activity_id}/management-note",
            data={"note_text": "Consultant follow-up"}
        )
        self.assertEqual(response.status_code, 302)
        rows = self.rows("""
            SELECT created_by_user_id, visibility
            FROM management_notes
            WHERE source_table = 'shift_activities'
              AND source_id = ?
        """, (activity_id,))
        self.assertEqual(rows, [{
            "created_by_user_id": 9,
            "visibility": "management_only",
        }])

    def test_activity_management_note_route_denies_support_workers(self):
        activity_id = self.insert_activity()
        self.login(1, "Support Worker")
        response = self.client.post(
            f"/manager-review/activities/{activity_id}/management-note",
            data={"note_text": "Not allowed"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.rows("SELECT * FROM management_notes"),
            []
        )

    def test_linked_actions_display_and_create_for_activity(self):
        activity_id = self.insert_activity(description="Community outing")
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("""
                INSERT INTO action_items (
                    title, description, status, priority, source_table,
                    source_id, assigned_to_user_id, created_by_user_id,
                    created_at, shift_id
                ) VALUES (
                    'Review activity plan', 'Check support plan', 'Open',
                    'High', 'shift_activities', ?, 1, 7,
                    '2026-08-03 12:20:00', 10
                )
            """, (activity_id,))
            conn.execute("""
                INSERT INTO action_items (
                    title, source_table, source_id, created_at, shift_id
                ) VALUES (
                    'Unrelated action', 'shift_notes', 99,
                    '2026-08-03 12:21:00', 10
                )
            """)
            conn.commit()
        finally:
            conn.close()

        self.login(6, "Admin")
        detail = self.client.get(
            f"/manager-review/activities/{activity_id}?"
            "storyline_client_id=1&storyline_filter=Activity&"
            "storyline_page=2"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Linked Actions", detail.data)
        self.assertIn(b"Review activity plan", detail.data)
        self.assertNotIn(b"Unrelated action", detail.data)
        self.assertIn(b"Create Action", detail.data)

        form = self.client.get(
            f"/manager-review/activities/{activity_id}/action/new?"
            "storyline_client_id=1&storyline_filter=Activity&"
            "storyline_page=2"
        )
        self.assertEqual(form.status_code, 200)
        self.assertIn(b"Create Activity Action", form.data)
        self.assertIn(b"Community outing", form.data)
        self.assertIn(b"filter=Activity", form.data)

        response = self.client.post(
            f"/manager-review/activities/{activity_id}/action/new",
            data={
                "title": "Confirm outing support",
                "description": "Confirm staffing.",
                "priority": "Medium",
                "assigned_to_user_id": "1",
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/action/", response.headers["Location"])

        actions = self.rows("""
            SELECT title, description, priority, source_table, source_id,
                   shift_id, created_by_user_id, assigned_to_user_id
            FROM action_items
            WHERE source_table = 'shift_activities'
              AND source_id = ?
            ORDER BY action_id
        """, (activity_id,))
        self.assertEqual(actions[-1], {
            "title": "Confirm outing support",
            "description": "Confirm staffing.",
            "priority": "Medium",
            "source_table": "shift_activities",
            "source_id": activity_id,
            "shift_id": 10,
            "created_by_user_id": 6,
            "assigned_to_user_id": 1,
        })

    def test_activity_action_link_and_route_use_strict_management_roles(self):
        activity_id = self.insert_activity()

        self.login(9, "Behaviour Consultant")
        consultant_detail = self.client.get(
            f"/manager-review/activities/{activity_id}"
        )
        self.assertEqual(consultant_detail.status_code, 200)
        self.assertNotIn(b"Create Action", consultant_detail.data)
        self.assertEqual(
            self.client.get(
                f"/manager-review/activities/{activity_id}/action/new"
            ).status_code,
            403
        )
        self.assertEqual(
            self.client.post(
                f"/manager-review/activities/{activity_id}/action/new",
                data={"title": "Not allowed"}
            ).status_code,
            403
        )

        self.login(10, "Admin")
        self.assertEqual(
            self.client.get(
                f"/manager-review/activities/{activity_id}/action/new"
            ).status_code,
            403
        )

        self.login(1, "Support Worker")
        self.assertEqual(
            self.client.get(
                f"/manager-review/activities/{activity_id}/action/new"
            ).status_code,
            403
        )

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
