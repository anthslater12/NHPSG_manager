import sqlite3
import unittest
from datetime import datetime, timezone
from unittest import mock

import app

try:
    import tests.test_incident_management_engagement as incident_tests
except ImportError:
    import test_incident_management_engagement as incident_tests


class IncidentWorkerCorrectionTests(unittest.TestCase):
    def setUp(self):
        incident_tests.IncidentManagementEngagementTests.setUp(self)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "ALTER TABLE shifts ADD COLUMN scheduled_end_time TEXT"
            )
            for column, definition in (
                ("actual_start_time", "TEXT"),
                ("actual_end_at_utc", "TEXT"),
                ("sign_on_at", "TEXT"),
                ("sign_off_at", "TEXT"),
            ):
                conn.execute(
                    f"ALTER TABLE shift_staff ADD COLUMN {column} {definition}"
                )
            conn.execute("""
                CREATE TABLE shift_notes (
                    note_id INTEGER PRIMARY KEY,
                    shift_date TEXT NOT NULL,
                    shift_type TEXT NOT NULL,
                    client_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    note_text TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE shift_care_task_entries (
                    entry_id INTEGER PRIMARY KEY,
                    care_task_id INTEGER,
                    shift_id INTEGER,
                    completed_by_user_id INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE shift_housekeeping_task_entries (
                    entry_id INTEGER PRIMARY KEY,
                    housekeeping_task_id INTEGER,
                    shift_id INTEGER,
                    completed_by_user_id INTEGER
                )
            """)
            conn.execute("""
                UPDATE shifts
                SET scheduled_end_time = '15:00'
                WHERE shift_id = 10
            """)
            conn.execute("""
                UPDATE shift_staff
                SET actual_start_time = '09:00',
                    sign_on_at = '2026-08-02T16:00:00Z',
                    actual_end_at_utc = NULL,
                    sign_off_at = NULL
                WHERE shift_id = 10 AND user_id = 1
            """)
            conn.execute("""
                UPDATE activity_log
                SET shift_id = 10
                WHERE activity_type = 'incident_created'
                  AND related_table = 'incident_reports'
                  AND related_id = 41
            """)
            conn.commit()
        finally:
            conn.close()
        migration_conn = app.get_db()
        migration_conn.close()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, session_role="Support Worker"):
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                role=session_role,
                full_name="Support Worker",
                documentation_shift_id=10,
            )

    def rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, parameters).fetchall()
        finally:
            conn.close()

    def source(self):
        return self.rows(
            "SELECT * FROM incident_reports WHERE incident_id = 41"
        )[0]

    def add_second_active_assignment(self, *, move_creation=False):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "INSERT INTO shifts "
                "(shift_id, client_id, shift_date, shift_type, status, "
                "scheduled_end_time) VALUES (?, ?, ?, ?, ?, ?)",
                (20, 1, "2026-08-02", "Afternoon", "Open", "23:00"),
            )
            conn.execute(
                "INSERT INTO shift_staff "
                "(shift_staff_id, shift_id, user_id, active, "
                "actual_start_time, sign_on_at, actual_end_at_utc, sign_off_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (200, 20, 1, 1, "16:00", "2026-08-02T23:00:00Z", None, None),
            )
            if move_creation:
                conn.execute(
                    "UPDATE activity_log SET shift_id = 20 "
                    "WHERE activity_type = 'incident_created' "
                    "AND related_table = 'incident_reports' "
                    "AND related_id = 41"
                )
            conn.commit()
        finally:
            conn.close()

    def correction_data(self, source=None, **changes):
        source = source or self.source()
        fields = {
            field: "" if source[field] is None else str(source[field])
            for field in app.INCIDENT_CORRECTION_TEXT_FIELDS
        }
        fields["injuries"] = int(bool(source["injuries"]))
        original = dict(fields)
        fields.update(changes)
        data = {
            field: fields[field]
            for field in app.INCIDENT_CORRECTION_TEXT_FIELDS
        }
        data.update({
            f"original_{field}": str(original[field])
            for field in app.INCIDENT_CORRECTION_TEXT_FIELDS
        })
        data["original_injuries"] = str(original["injuries"])
        occurrence = self.rows("""
            SELECT event_datetime
            FROM activity_log
            WHERE activity_type IN (
                'incident_created',
                'incident_management_corrected',
                'incident_worker_corrected'
            )
              AND related_table = 'incident_reports'
              AND related_id = 41
            ORDER BY activity_id DESC
            LIMIT 1
        """)[0]["event_datetime"]
        data["original_occurrence_utc"] = occurrence
        data["repeated_hour_choice"] = changes.get(
            "repeated_hour_choice", ""
        )
        if fields["injuries"]:
            data["injuries"] = "1"
        return data

    def worker_audit_count(self):
        return self.rows("""
            SELECT COUNT(*) AS count
            FROM activity_log
            WHERE activity_type = 'incident_worker_corrected'
        """)[0]["count"]

    def assert_creation_linkage_rejected(self, mutate):
        self.login()
        prepared = self.correction_data(description="Must not be saved")
        mutate_conn = sqlite3.connect(self.path)
        try:
            mutate(mutate_conn)
            mutate_conn.commit()
        finally:
            mutate_conn.close()

        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(url, data=prepared).status_code,
            403,
        )
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(self.worker_audit_count(), 0)

    def test_incident_creation_audit_separates_action_and_occurrence_time(self):
        self.login()
        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc),
        ):
            response = self.client.post(
                "/incident/new",
                data={
                    "incident_date": "2026-08-02",
                    "incident_time": "11:00",
                    "location": "Community Centre",
                    "incident_type": "Medical / Medication",
                    "description": "New incident details",
                    "actions_taken": "Called nurse",
                    "witnesses": "Worker",
                    "injury_details": "",
                },
            )
        self.assertEqual(response.status_code, 302)
        audit = self.rows("""
            SELECT activity_datetime, event_datetime, related_id
            FROM activity_log
            WHERE activity_type = 'incident_created'
              AND related_id = 42
        """)[0]
        self.assertEqual(audit["activity_datetime"], "2026-10-20T16:00:00Z")
        self.assertEqual(audit["event_datetime"], "2026-08-02T18:00:00Z")
        self.assertNotEqual(audit["activity_datetime"], audit["event_datetime"])

    def set_ambiguous_source(self, choice):
        event_datetime = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30",
            choice,
        )
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE incident_reports SET incident_date = ?, "
                "incident_time = ? WHERE incident_id = 41",
                ("2025-11-02", "01:30"),
            )
            conn.execute(
                "UPDATE activity_log SET event_datetime = ? "
                "WHERE activity_type = 'incident_created' "
                "AND related_table = 'incident_reports' "
                "AND related_id = 41",
                (event_datetime,),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_dashboard_incident(
        self,
        incident_id,
        reporter_id,
        shift_id,
        description,
        event_datetime,
    ):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                INSERT INTO incident_reports
                    (incident_id, client_id, reported_by_user_id,
                     incident_date, incident_time, location, incident_type,
                     severity, description, actions_taken, follow_up_required)
                VALUES (?, 1, ?, '2026-08-02', '11:00', 'Home', 'Other',
                        'Normal', ?, '', 0)
            """, (incident_id, reporter_id, description))
            conn.execute("""
                INSERT INTO activity_log
                    (activity_type, activity_class, user_id, client_id,
                     shift_id, related_table, related_id, summary,
                     storyline_visible, event_datetime)
                VALUES ('incident_created', 'INCIDENT', ?, 1, ?,
                        'incident_reports', ?, 'Incident created', 1, ?)
            """, (
                reporter_id,
                shift_id,
                incident_id,
                event_datetime,
            ))
            conn.commit()
        finally:
            conn.close()

    def add_dashboard_fixture_rows(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "INSERT INTO shift_staff "
                "(shift_staff_id, shift_id, user_id, active, actual_start_time, "
                "sign_on_at, actual_end_at_utc, sign_off_at) "
                "VALUES (102, 10, 6, 1, '09:00', "
                "'2026-08-02T16:00:00Z', NULL, NULL)"
            )
            conn.execute(
                "INSERT INTO shifts "
                "(shift_id, client_id, shift_date, shift_type, status, "
                "scheduled_end_time) VALUES (20, 1, '2026-08-02', "
                "'Afternoon', 'Open', '23:00')"
            )
            conn.commit()
        finally:
            conn.close()

        self.insert_dashboard_incident(
            42, 6, 10, "Other worker incident", "2026-08-02T18:00:00Z"
        )
        self.insert_dashboard_incident(
            43, 1, 20, "Other shift incident", "2026-08-02T19:00:00Z"
        )
        self.insert_dashboard_incident(
            44, 1, 10, "Reviewed current incident", "2026-08-02T20:00:00Z"
        )
        self.insert_dashboard_incident(
            45, 1, 10, "Cleared current incident", "2026-08-02T21:00:00Z"
        )
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                INSERT INTO acknowledgements
                    (source_table, source_id, user_id,
                     acknowledgement_type, active)
                VALUES ('incident_reports', 44, 2, 'Review', 1)
            """)
            conn.execute("""
                INSERT INTO incident_clearances
                    (incident_id, cleared_by_user_id, cleared_at_utc, active)
                VALUES (45, 2, '2026-08-02T22:00:00Z', 1)
            """)
            conn.commit()
        finally:
            conn.close()

    def test_original_worker_can_correct_and_audit_is_hidden(self):
        self.login()
        url = "/shift/10/incident/41/edit"
        before = dict(self.source())
        immutable_fields = (
            "incident_id",
            "client_id",
            "reported_by_user_id",
            "severity",
            "follow_up_required",
            "police_notified",
            "medical_treatment",
            "status",
            "reviewed_by_user_id",
            "reviewed_at",
            "created_at",
        )
        self.assertEqual(self.client.get(url).status_code, 200)

        with mock.patch.object(
            app,
            "get_application_now_utc",
            return_value=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc),
        ):
            response = self.client.post(
                url,
                data=self.correction_data(description="Corrected description"),
            )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/shift/10", response.location)

        incident = self.source()
        self.assertEqual(incident["incident_id"], 41)
        self.assertEqual(incident["description"], "Corrected description")
        self.assertEqual(incident["client_id"], 1)
        self.assertEqual(incident["reported_by_user_id"], 1)
        for field in immutable_fields:
            self.assertEqual(incident[field], before[field], field)

        audits = self.rows("""
            SELECT activity_type, user_id, client_id, shift_id,
                   related_table, related_id, storyline_visible, details,
                   activity_datetime, event_datetime
            FROM activity_log
            WHERE activity_type = 'incident_worker_corrected'
        """)
        self.assertEqual(len(audits), 1)
        self.assertEqual(
            tuple(audits[0][field] for field in (
                "user_id", "client_id", "shift_id", "related_table",
                "related_id", "storyline_visible"
            )),
            (1, 1, 10, "incident_reports", 41, 0),
        )
        self.assertIn("description", audits[0]["details"])
        self.assertEqual(audits[0]["activity_datetime"], "2026-10-20T16:00:00Z")
        self.assertEqual(audits[0]["event_datetime"], "2026-08-02T17:00:00Z")
        self.assertNotEqual(
            audits[0]["activity_datetime"], audits[0]["event_datetime"]
        )

        creation = self.rows("""
            SELECT user_id, client_id, shift_id, related_table, related_id
            FROM activity_log
            WHERE activity_type = 'incident_created'
              AND related_table = 'incident_reports'
              AND related_id = 41
        """)
        self.assertEqual(
            [tuple(row) for row in creation],
            [(1, 1, 10, "incident_reports", 41)],
        )

    def test_invalid_incident_type_is_rejected_without_mutation(self):
        self.login()
        before = dict(self.source())
        response = self.client.post(
            "/shift/10/incident/41/edit",
            data=self.correction_data(
                incident_type="Not a valid Incident Type",
                description="Must not be saved",
            ),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"A valid incident type is required.", response.data)
        after = self.source()
        self.assertEqual(dict(after), before)
        self.assertEqual(self.worker_audit_count(), 0)

    def test_future_incident_datetime_is_rejected_without_mutation(self):
        self.login()
        before = dict(self.source())
        response = self.client.post(
            "/shift/10/incident/41/edit",
            data=self.correction_data(
                incident_date="2099-01-01",
                incident_time="12:00",
                description="Must not be saved",
            ),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Occurrence time cannot be in the future", response.data)
        self.assertEqual(dict(self.source()), before)
        self.assertEqual(self.worker_audit_count(), 0)

    def test_repeated_hour_selection_is_preserved_and_corrected(self):
        self.set_ambiguous_source("first")
        self.login()
        url = "/shift/10/incident/41/edit"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'<option value="first" selected>',
            response.data,
        )

        response = self.client.post(
            url,
            data=self.correction_data(
                repeated_hour_choice="second",
                description="Second repeated-hour occurrence",
            ),
        )
        self.assertEqual(response.status_code, 302)
        expected_second = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30",
            "second",
        )
        audit = self.rows("""
            SELECT event_datetime
            FROM activity_log
            WHERE activity_type = 'incident_worker_corrected'
              AND related_table = 'incident_reports'
              AND related_id = 41
        """)[0]
        self.assertEqual(audit["event_datetime"], expected_second)

    def test_multiple_active_assignments_use_valid_selected_context(self):
        self.add_second_active_assignment(move_creation=True)
        self.login()
        with self.client.session_transaction() as session:
            session["documentation_shift_id"] = 20

        url = "/shift/20/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url,
            data=self.correction_data(description="Corrected on shift 20"),
        )
        self.assertEqual(response.status_code, 302)

        audits = self.rows("""
            SELECT user_id, client_id, shift_id, related_table, related_id
            FROM activity_log
            WHERE activity_type = 'incident_worker_corrected'
        """)
        self.assertEqual(
            [tuple(row) for row in audits],
            [(1, 1, 20, "incident_reports", 41)],
        )
        self.assertEqual(self.source()["client_id"], 1)
        self.assertEqual(self.source()["reported_by_user_id"], 1)

    def test_selected_context_and_url_mismatch_denies_without_mutation(self):
        self.add_second_active_assignment()
        self.login()
        with self.client.session_transaction() as session:
            session["documentation_shift_id"] = 20

        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        response = self.client.post(
            url,
            data=self.correction_data(description="Must not be saved"),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'incident_worker_corrected'"
            )),
            0,
        )

    def test_missing_creation_audit_is_fail_closed(self):
        def remove_creation(conn):
            conn.execute(
                "DELETE FROM activity_log "
                "WHERE activity_type = 'incident_created' "
                "AND related_table = 'incident_reports' "
                "AND related_id = 41"
            )

        self.assert_creation_linkage_rejected(remove_creation)

    def test_duplicate_creation_audits_are_fail_closed(self):
        def duplicate_creation(conn):
            conn.execute("""
                INSERT INTO activity_log
                    (activity_class, activity_type, user_id, client_id,
                     shift_id, related_table, related_id, summary,
                     success, storyline_visible, event_datetime)
                SELECT activity_class, activity_type, user_id, client_id,
                       shift_id, related_table, related_id, summary,
                       success, storyline_visible, event_datetime
                FROM activity_log
                WHERE activity_type = 'incident_created'
                  AND related_table = 'incident_reports'
                  AND related_id = 41
            """)

        self.assert_creation_linkage_rejected(duplicate_creation)

    def test_null_creation_shift_is_fail_closed(self):
        def clear_creation_shift(conn):
            conn.execute(
                "UPDATE activity_log SET shift_id = NULL "
                "WHERE activity_type = 'incident_created' "
                "AND related_table = 'incident_reports' "
                "AND related_id = 41"
            )

        self.assert_creation_linkage_rejected(clear_creation_shift)

    def test_creation_reporter_mismatch_is_fail_closed(self):
        def change_creation_user(conn):
            conn.execute(
                "UPDATE activity_log SET user_id = 6 "
                "WHERE activity_type = 'incident_created' "
                "AND related_table = 'incident_reports' "
                "AND related_id = 41"
            )

        self.assert_creation_linkage_rejected(change_creation_user)

    def test_creation_client_mismatch_is_fail_closed(self):
        def change_creation_client(conn):
            conn.execute(
                "UPDATE activity_log SET client_id = 999 "
                "WHERE activity_type = 'incident_created' "
                "AND related_table = 'incident_reports' "
                "AND related_id = 41"
            )

        self.assert_creation_linkage_rejected(change_creation_client)

    def test_source_shift_client_mismatch_is_fail_closed(self):
        def change_source_shift_client(conn):
            conn.execute(
                "INSERT INTO clients VALUES (2, 'Other Client', 1)"
            )
            conn.execute(
                "UPDATE shifts SET client_id = 2 WHERE shift_id = 10"
            )

        self.assert_creation_linkage_rejected(change_source_shift_client)

    def test_other_worker_same_shift_cannot_correct(self):
        conn = sqlite3.connect(self.path)
        conn.execute(
            "INSERT INTO users VALUES (8, 'Other Worker', 'Support Worker', 1)"
        )
        conn.execute(
            "INSERT INTO shift_staff "
            "(shift_staff_id, shift_id, user_id, active, actual_start_time, "
            "sign_on_at, actual_end_at_utc, sign_off_at) "
            "VALUES (101, 10, 8, 1, '09:00', "
            "'2026-08-02T16:00:00Z', NULL, NULL)"
        )
        conn.commit()
        conn.close()
        self.login(user_id=8)
        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        response = self.client.post(
            url,
            data=self.correction_data(description="Must not be saved"),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'incident_worker_corrected'"
            )),
            0,
        )

    def test_inactive_database_worker_cannot_correct(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("UPDATE users SET active = 0 WHERE user_id = 1")
            conn.commit()
        finally:
            conn.close()

        self.login()
        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url,
                data=self.correction_data(description="Must not be saved"),
            ).status_code,
            403,
        )
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(self.worker_audit_count(), 0)

    def test_inactive_assignment_cannot_correct(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE shift_staff SET active = 0 "
                "WHERE shift_id = 10 AND user_id = 1"
            )
            conn.commit()
        finally:
            conn.close()

        self.login()
        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url,
                data=self.correction_data(description="Must not be saved"),
            ).status_code,
            403,
        )
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(self.worker_audit_count(), 0)

    def test_ended_assignment_cannot_correct(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE shift_staff SET actual_end_at_utc = ? "
                "WHERE shift_id = 10 AND user_id = 1",
                ("2026-08-02T20:00:00Z",),
            )
            conn.commit()
        finally:
            conn.close()

        self.login()
        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url,
                data=self.correction_data(description="Must not be saved"),
            ).status_code,
            403,
        )
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(self.worker_audit_count(), 0)

    def test_cancelled_shift_cannot_correct(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE shifts SET status = 'Cancelled' WHERE shift_id = 10"
            )
            conn.commit()
        finally:
            conn.close()

        self.login()
        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url,
                data=self.correction_data(description="Must not be saved"),
            ).status_code,
            403,
        )
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(self.worker_audit_count(), 0)

    def test_closed_shift_cannot_correct(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE shifts SET status = 'Closed' WHERE shift_id = 10"
            )
            conn.commit()
        finally:
            conn.close()

        self.login()
        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url,
                data=self.correction_data(description="Must not be saved"),
            ).status_code,
            403,
        )
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(self.worker_audit_count(), 0)

    def test_original_worker_on_later_shift_cannot_correct(self):
        self.add_second_active_assignment()
        self.login()
        with self.client.session_transaction() as session:
            session["documentation_shift_id"] = 20

        url = "/shift/20/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(
                url,
                data=self.correction_data(description="Must not be saved"),
            ).status_code,
            403,
        )
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(self.worker_audit_count(), 0)

    def test_database_role_overrides_spoofed_session_role(self):
        self.login(session_role="Admin")
        self.assertEqual(
            self.client.get("/shift/10/incident/41/edit").status_code,
            200,
        )

        self.login(user_id=2, session_role="Support Worker")
        with self.client.session_transaction() as session:
            session["documentation_shift_id"] = 10
        self.assertEqual(
            self.client.get("/shift/10/incident/41/edit").status_code,
            403,
        )

    def test_review_and_clearance_lock_out_worker_correction(self):
        self.login()
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO acknowledgements
                (source_table, source_id, user_id, acknowledgement_type, active)
            VALUES ('incident_reports', 41, 2, 'Review', 1)
        """)
        conn.commit()
        conn.close()
        self.assertEqual(
            self.client.get("/shift/10/incident/41/edit").status_code,
            403,
        )

        conn = sqlite3.connect(self.path)
        conn.execute(
            "UPDATE acknowledgements SET active = 0 "
            "WHERE source_table = 'incident_reports' AND source_id = 41"
        )
        conn.execute("""
            INSERT INTO incident_clearances
                (incident_id, cleared_by_user_id, cleared_at_utc, active)
            VALUES (41, 2, '2026-08-02T18:00:00Z', 1)
        """)
        conn.commit()
        conn.close()
        self.assertEqual(
            self.client.get("/shift/10/incident/41/edit").status_code,
            403,
        )

    def test_review_added_after_get_blocks_prepared_post(self):
        self.login()
        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 200)
        prepared = self.correction_data(description="Review race change")

        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO acknowledgements
                (source_table, source_id, user_id, acknowledgement_type, active)
            VALUES ('incident_reports', 41, 2, 'Review', 1)
        """)
        conn.commit()
        conn.close()

        response = self.client.post(url, data=prepared)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'incident_worker_corrected'"
            )),
            0,
        )

    def test_clearance_added_after_get_blocks_prepared_post(self):
        self.login()
        url = "/shift/10/incident/41/edit"
        self.assertEqual(self.client.get(url).status_code, 200)
        prepared = self.correction_data(description="Clearance race change")

        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO incident_clearances
                (incident_id, cleared_by_user_id, cleared_at_utc, active)
            VALUES (41, 2, '2026-08-02T18:00:00Z', 1)
        """)
        conn.commit()
        conn.close()

        response = self.client.post(url, data=prepared)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.source()["description"], "Incident details")
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'incident_worker_corrected'"
            )),
            0,
        )

    def test_noop_and_stale_post_do_not_write_worker_audit(self):
        self.login()
        url = "/shift/10/incident/41/edit"
        form = self.client.get(url)
        self.assertEqual(form.status_code, 200)

        source = self.source()
        no_op = self.correction_data(source)
        response = self.client.post(url, data=no_op)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"No changes were detected", response.data)
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'incident_worker_corrected'"
            )),
            0,
        )

        stale_form = self.correction_data(source, description="Submitted")
        conn = sqlite3.connect(self.path)
        conn.execute(
            "UPDATE incident_reports SET description = 'Changed elsewhere' "
            "WHERE incident_id = 41"
        )
        conn.commit()
        conn.close()
        response = self.client.post(url, data=stale_form)
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"changed after the correction form was opened", response.data)
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'incident_worker_corrected'"
            )),
            0,
        )

    def test_worker_correction_rehydrates_storyline_on_new_date(self):
        self.login()
        response = self.client.post(
            "/shift/10/incident/41/edit",
            data=self.correction_data(
                incident_date="2026-08-03",
                incident_time="11:00",
                description="Current Storyline description",
            ),
        )
        self.assertEqual(response.status_code, 302)

        storyline = self.client.get(
            "/client/1/storyline?date=2026-08-03&filter=Incident"
        )
        self.assertEqual(storyline.status_code, 200)
        self.assertIn(b"Current Storyline description", storyline.data)
        self.assertNotIn(b"Incident details", storyline.data)
        self.assertEqual(storyline.data.count(b"Incident created:"), 1)
        self.assertEqual(
            len(self.rows(
                "SELECT * FROM activity_log "
                "WHERE activity_type = 'incident_worker_corrected' "
                "AND storyline_visible = 1"
            )),
            0,
        )

    def test_shift_dashboard_lists_only_owned_current_shift_incidents(self):
        self.add_dashboard_fixture_rows()
        self.login(session_role="Admin")
        with mock.patch.object(
            app,
            "_get_authenticated_staff_notice_recipient",
            return_value={"user_id": 1},
        ), mock.patch.object(
            app,
            "_get_staff_notice_recipient_collections",
            return_value={"dashboard": [], "outstanding_count": 0},
        ), mock.patch.object(
            app,
            "reconcile_staff_notice_non_shift_requirements_in_transaction",
        ), mock.patch.object(
            app,
            "get_applicable_care_tasks",
            return_value=[],
        ), mock.patch.object(
            app,
            "get_applicable_housekeeping_tasks",
            return_value=[],
        ), mock.patch.object(
            app,
            "get_active_food_fluid_shift_context",
            side_effect=PermissionError,
        ), mock.patch.object(
            app,
            "get_active_sleep_shift_context",
            side_effect=PermissionError,
        ):
            response = self.client.get("/shift/10")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Incidents this Shift", response.data)
        self.assertIn(b"Incident details", response.data)
        self.assertNotIn(b"Other worker incident", response.data)
        self.assertNotIn(b"Other shift incident", response.data)
        self.assertIn(b"Reviewed current incident", response.data)
        self.assertIn(b"Reviewed - locked", response.data)
        self.assertIn(b"Cleared current incident", response.data)
        self.assertIn(b"Cleared - locked", response.data)
        self.assertIn(b"Correct", response.data)
        self.assertEqual(
            response.data.count(b"/shift/10/incident/41/edit"),
            1,
        )
        self.assertNotIn(b"/shift/10/incident/44/edit", response.data)
        self.assertNotIn(b"/shift/10/incident/45/edit", response.data)


if __name__ == "__main__":
    unittest.main()
