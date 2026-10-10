import sqlite3
import unittest

import app

try:
    import tests.test_incident_management_engagement as incident_tests
except ImportError:
    import test_incident_management_engagement as incident_tests


class ManagementIncidentCorrectionTests(unittest.TestCase):
    def setUp(self):
        incident_tests.IncidentManagementEngagementTests.setUp(self)
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "ALTER TABLE acknowledgements ADD COLUMN invalidated_at_utc TEXT"
            )
            conn.execute(
                "ALTER TABLE acknowledgements "
                "ADD COLUMN invalidated_by_user_id INTEGER"
            )
            conn.execute(
                "ALTER TABLE acknowledgements "
                "ADD COLUMN invalidation_reason TEXT"
            )
            conn.commit()
        finally:
            conn.close()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id):
        roles = {
            1: "Support Worker",
            2: "Program Manager",
            3: "Director",
            4: "Admin",
            5: "Program Manager",
            7: "Behaviour Consultant",
        }
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=roles[user_id])

    def rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, parameters).fetchall()
        finally:
            conn.close()

    def add_incident(
        self,
        incident_id,
        client_id=1,
        reporter_id=1,
        incident_date="2026-08-02",
        incident_time="10:00",
        incident_type="Medical",
        description="Original incident",
    ):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                INSERT INTO incident_reports
                (incident_id, client_id, reported_by_user_id,
                 incident_date, incident_time, location, incident_type,
                 severity, description, actions_taken, follow_up_required,
                 witnesses, injuries, injury_details, police_notified,
                 medical_treatment, status, reviewed_by_user_id, reviewed_at)
                VALUES (?, ?, ?, ?, ?, 'Home', ?, 'High', ?, 'Observed', 1,
                        'Witness', 1, 'Original injury', 1, 1,
                        'Awaiting Review', 77, '2026-08-02T18:00:00Z')
            """, (
                incident_id,
                client_id,
                reporter_id,
                incident_date,
                incident_time,
                incident_type,
                description,
            ))
            conn.commit()
        finally:
            conn.close()

    def source(self, incident_id=41):
        return self.rows(
            "SELECT * FROM incident_reports WHERE incident_id = ?",
            (incident_id,),
        )[0]

    def correction_data(self, incident_id=41, source=None, **changes):
        source = source or self.source(incident_id)
        fields = {
            field: ("" if source[field] is None else str(source[field]))
            for field in app.INCIDENT_CORRECTION_TEXT_FIELDS
        }
        fields["injuries"] = int(bool(source["injuries"]))
        original_fields = dict(fields)
        fields.update(changes)
        data = {
            field: fields[field]
            for field in app.INCIDENT_CORRECTION_TEXT_FIELDS
        }
        data["correction_reason"] = "Corrected by management"
        data["repeated_hour_choice"] = changes.get(
            "repeated_hour_choice", ""
        )
        data.update({
            f"original_{field}": str(original_fields[field])
            for field in app.INCIDENT_CORRECTION_TEXT_FIELDS
        })
        data["original_injuries"] = str(original_fields["injuries"])
        audit_rows = self.rows(
            "SELECT event_datetime FROM activity_log "
            "WHERE related_table = 'incident_reports' "
            "AND related_id = ? "
            "AND activity_type IN ('incident_created', "
            "'incident_management_corrected') "
            "AND event_datetime IS NOT NULL "
            "ORDER BY activity_id DESC LIMIT 1",
            (incident_id,),
        )
        data["original_occurrence_utc"] = (
            audit_rows[0]["event_datetime"] if audit_rows else ""
        )
        if fields["injuries"]:
            data["injuries"] = "1"
        return data

    def add_creation_audit(self, incident_id, event_datetime):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                INSERT INTO activity_log
                (activity_class, activity_type, user_id, client_id,
                 related_table, related_id, summary, storyline_visible,
                 event_datetime)
                VALUES ('INCIDENT', 'incident_created', 1, 1,
                        'incident_reports', ?, 'Incident created', 1, ?)
            """, (incident_id, event_datetime))
            conn.commit()
        finally:
            conn.close()

    def add_review(self, incident_id=41, user_id=2):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                INSERT INTO acknowledgements
                (source_table, source_id, user_id, acknowledgement_type, active)
                VALUES ('incident_reports', ?, ?, 'Review', 1)
            """, (incident_id, user_id))
            conn.commit()
        finally:
            conn.close()
        return self.rows(
            "SELECT acknowledgement_id FROM acknowledgements "
            "WHERE source_table = 'incident_reports' AND source_id = ?",
            (incident_id,),
        )[-1]["acknowledgement_id"]

    def add_note_and_action(self, incident_id=41):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                INSERT INTO management_notes
                (source_table, source_id, note_text, visibility,
                 created_by_user_id, active)
                VALUES ('incident_reports', ?, 'Keep linked note',
                        'management_only', 2, 1)
            """, (incident_id,))
            conn.execute("""
                INSERT INTO action_items
                (title, description, source_table, source_id,
                 created_by_user_id)
                VALUES ('Keep linked action', 'Action', 'incident_reports', ?, 2)
            """, (incident_id,))
            conn.commit()
        finally:
            conn.close()

    def test_management_roles_can_get_and_post_correction(self):
        for user_id, role in (
            (2, "Program Manager"),
            (3, "Director"),
            (4, "Admin"),
        ):
            with self.subTest(role=role):
                incident_id = 100 + user_id
                self.add_incident(incident_id)
                self.login(user_id)
                url = f"/manager-review/incidents/{incident_id}/correct"
                correction_page = self.client.get(url)
                self.assertEqual(correction_page.status_code, 200)
                detail_page = self.client.get(
                    f"/manager-review/incidents/{incident_id}"
                )
                self.assertIn(b"Correct Entry", detail_page.data)
                self.assertIn(
                    url.encode("ascii"),
                    detail_page.data,
                )
                response = self.client.post(
                    url,
                    data=self.correction_data(
                        incident_id,
                        description=f"{role} corrected incident",
                    ),
                    follow_redirects=True,
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Incident correction saved successfully.", response.data)

    def test_non_management_roles_and_inactive_user_are_denied(self):
        for user_id in (7, 1, 5):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                url = "/manager-review/incidents/41/correct"
                self.assertEqual(self.client.get(url).status_code, 403)
                detail_page = self.client.get("/manager-review/incidents/41")
                if user_id == 7:
                    self.assertNotIn(b"Correct Entry", detail_page.data)
                self.assertEqual(
                    self.client.post(url, data={"correction_reason": "Denied"}).status_code,
                    403,
                )

    def test_database_role_controls_access_not_session_role(self):
        self.login(2)
        with self.client.session_transaction() as session:
            session["role"] = "Support Worker"
        url = "/manager-review/incidents/41/correct"
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(
            self.client.post(
                url,
                data=self.correction_data(description="Database role wins"),
            ).status_code,
            302,
        )

        self.login(1)
        with self.client.session_transaction() as session:
            session["role"] = "Admin"
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_missing_incident_client_or_reporter_is_rejected(self):
        self.login(2)
        self.assertEqual(
            self.client.get("/manager-review/incidents/999/correct").status_code,
            404,
        )
        self.add_incident(101, client_id=999)
        self.assertEqual(
            self.client.get("/manager-review/incidents/101/correct").status_code,
            403,
        )
        self.add_incident(102, reporter_id=999)
        self.assertEqual(
            self.client.get("/manager-review/incidents/102/correct").status_code,
            403,
        )

    def test_same_row_update_preserves_identity_and_noneditable_fields(self):
        before = dict(self.source())
        self.login(2)
        response = self.client.post(
            "/manager-review/incidents/41/correct",
            data=self.correction_data(
                description="Corrected narrative",
                incident_time="11:30",
            ),
        )
        self.assertEqual(response.status_code, 302)
        after = dict(self.source())
        self.assertEqual(after["incident_id"], before["incident_id"])
        self.assertEqual(after["client_id"], before["client_id"])
        self.assertEqual(after["reported_by_user_id"], before["reported_by_user_id"])
        for field in (
            "severity", "follow_up_required", "police_notified",
            "medical_treatment", "status", "reviewed_by_user_id",
            "reviewed_at", "created_at",
        ):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after["description"], "Corrected narrative")
        self.assertEqual(after["incident_time"], "11:30")
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) AS count FROM incident_reports"
            )[0]["count"],
            1,
        )

    def test_required_reason_and_noop_do_not_write_or_invalidate(self):
        review_id = self.add_review()
        before = dict(self.source())
        self.login(2)
        url = "/manager-review/incidents/41/correct"
        missing_reason = self.correction_data()
        missing_reason["correction_reason"] = ""
        response = self.client.post(url, data=missing_reason)
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Correction reason is required.", response.data)

        noop = self.client.post(url, data=self.correction_data())
        self.assertEqual(noop.status_code, 400)
        self.assertIn(b"No changes were detected.", noop.data)
        self.assertEqual(dict(self.source()), before)
        review = self.rows(
            "SELECT active FROM acknowledgements WHERE acknowledgement_id = ?",
            (review_id,),
        )[0]
        self.assertEqual(review["active"], 1)
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) AS count FROM activity_log "
                "WHERE activity_type = 'incident_management_corrected'"
            )[0]["count"],
            0,
        )

    def test_real_correction_invalidates_review_and_keeps_links(self):
        review_id = self.add_review()
        self.add_note_and_action()
        self.login(2)
        response = self.client.post(
            "/manager-review/incidents/41/correct",
            data=self.correction_data(description="Reviewed correction"),
        )
        self.assertEqual(response.status_code, 302)
        review = self.rows(
            "SELECT * FROM acknowledgements WHERE acknowledgement_id = ?",
            (review_id,),
        )[0]
        self.assertEqual(review["active"], 0)
        self.assertEqual(review["invalidated_by_user_id"], 2)
        self.assertEqual(review["invalidation_reason"], "Incident corrected by management")
        self.assertIsNotNone(review["invalidated_at_utc"])
        audit = self.rows(
            "SELECT * FROM activity_log "
            "WHERE activity_type = 'incident_management_corrected'"
        )[0]
        self.assertEqual(audit["storyline_visible"], 0)
        self.assertEqual(audit["related_id"], 41)
        self.assertIn("Original reporter user ID: 1", audit["details"])
        self.assertIn("Correction reason: Corrected by management", audit["details"])
        self.assertIn("Before snapshot:", audit["details"])
        self.assertIn("After snapshot:", audit["details"])
        self.assertIn("Invalidated Review acknowledgement IDs:", audit["details"])

        multiline = "Corrected line one\nCorrected line two"
        self.client.post(
            "/manager-review/incidents/41/correct",
            data=self.correction_data(description=multiline),
        )
        audit = self.rows(
            "SELECT details FROM activity_log "
            "WHERE activity_type = 'incident_management_corrected' "
            "ORDER BY activity_id DESC"
        )[0]
        self.assertIn(
            "description: 'Corrected line one\\nCorrected line two'",
            audit["details"],
        )

        detail = self.client.get("/manager-review/incidents/41")
        self.assertIn(b"Review required", detail.data)
        self.assertIn(b"Keep linked note", detail.data)
        self.assertIn(b"Keep linked action", detail.data)

    def test_stale_form_is_rejected_without_overwriting_newer_source(self):
        self.login(2)
        self.assertEqual(
            self.client.get("/manager-review/incidents/41/correct").status_code,
            200,
        )
        original = self.source()
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE incident_reports SET description = ? WHERE incident_id = 41",
                ("Concurrent correction",),
            )
            conn.commit()
        finally:
            conn.close()
        response = self.client.post(
            "/manager-review/incidents/41/correct",
            data=self.correction_data(
                source=original,
                description="Stale overwrite",
            ),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.source()["description"], "Concurrent correction")

    def test_nonexistent_datetime_and_new_ambiguous_requires_choice(self):
        self.login(2)
        url = "/manager-review/incidents/41/correct"
        nonexistent = self.client.post(
            url,
            data=self.correction_data(
                incident_date="2026-03-08",
                incident_time="02:30",
            ),
        )
        self.assertEqual(nonexistent.status_code, 400)
        self.assertIn(b"does not exist in Vancouver time", nonexistent.data)

        ambiguous = self.client.post(
            url,
            data=self.correction_data(
                incident_date="2025-11-02",
                incident_time="01:30",
            ),
        )
        self.assertEqual(ambiguous.status_code, 400)
        self.assertIn(b"Repeated Vancouver times require", ambiguous.data)

    def test_ambiguous_source_time_preserves_authoritative_instant(self):
        incident_id = 301
        self.add_incident(
            incident_id,
            incident_date="2025-11-02",
            incident_time="01:30",
        )
        first = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30", "first"
        )
        self.add_creation_audit(incident_id, first)
        self.login(2)
        response = self.client.post(
            f"/manager-review/incidents/{incident_id}/correct",
            data=self.correction_data(
                incident_id,
                location="Changed without changing time",
            ),
        )
        self.assertEqual(response.status_code, 302)
        audit = self.rows(
            "SELECT event_datetime FROM activity_log "
            "WHERE activity_type = 'incident_management_corrected' "
            "AND related_id = ?",
            (incident_id,),
        )[0]
        self.assertEqual(audit["event_datetime"], first)

    def test_fold_choice_change_is_a_real_correction(self):
        incident_id = 306
        self.add_incident(
            incident_id,
            incident_date="2025-11-02",
            incident_time="01:30",
        )
        first = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30", "first"
        )
        second = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30", "second"
        )
        self.add_creation_audit(incident_id, first)
        review_id = self.add_review(incident_id)
        before = dict(self.source(incident_id))
        self.login(2)
        response = self.client.post(
            f"/manager-review/incidents/{incident_id}/correct",
            data=self.correction_data(
                incident_id,
                repeated_hour_choice="second",
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(dict(self.source(incident_id)), before)
        audit = self.rows(
            "SELECT event_datetime, details FROM activity_log "
            "WHERE activity_type = 'incident_management_corrected' "
            "AND related_id = ?",
            (incident_id,),
        )[0]
        self.assertEqual(audit["event_datetime"], second)
        self.assertIn("Changed fields: occurrence_utc", audit["details"])
        self.assertEqual(
            self.rows(
                "SELECT active FROM acknowledgements "
                "WHERE acknowledgement_id = ?",
                (review_id,),
            )[0]["active"],
            0,
        )

        response = self.client.post(
            f"/manager-review/incidents/{incident_id}/correct",
            data=self.correction_data(
                incident_id,
                repeated_hour_choice="first",
            ),
        )
        self.assertEqual(response.status_code, 302)
        audit = self.rows(
            "SELECT event_datetime FROM activity_log "
            "WHERE activity_type = 'incident_management_corrected' "
            "AND related_id = ? ORDER BY activity_id DESC",
            (incident_id,),
        )[0]
        self.assertEqual(audit["event_datetime"], first)

    def test_authoritative_occurrence_change_makes_form_stale(self):
        incident_id = 307
        self.add_incident(
            incident_id,
            incident_date="2025-11-02",
            incident_time="01:30",
        )
        first = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30", "first"
        )
        second = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30", "second"
        )
        self.add_creation_audit(incident_id, first)
        self.login(2)
        stale_form = self.correction_data(
            incident_id,
            description="Should not overwrite newer occurrence",
        )
        self.add_creation_audit(incident_id, second)
        response = self.client.post(
            f"/manager-review/incidents/{incident_id}/correct",
            data=stale_form,
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn(
            b"changed after the correction form was opened", response.data
        )
        self.assertEqual(
            self.source(incident_id)["description"],
            "Original incident",
        )

    def test_storyline_follows_new_fold_choice(self):
        incident_id = 308
        self.add_incident(
            incident_id,
            incident_date="2025-11-02",
            incident_time="01:30",
        )
        first = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30", "first"
        )
        second = app.convert_vancouver_occurrence_input_to_utc(
            "2025-11-02T01:30", "second"
        )
        self.add_creation_audit(incident_id, first)
        self.login(2)
        response = self.client.post(
            f"/manager-review/incidents/{incident_id}/correct",
            data=self.correction_data(
                incident_id,
                repeated_hour_choice="second",
                description="Fold-selected Incident",
            ),
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(
            "/client/1/storyline?filter=Incident&date=2025-11-02"
        )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data.count(b"Fold-selected Incident"), 1)
        audit = self.rows(
            "SELECT event_datetime FROM activity_log "
            "WHERE activity_type = 'incident_management_corrected' "
            "AND related_id = ?",
            (incident_id,),
        )[0]
        self.assertEqual(audit["event_datetime"], second)

    def test_ambiguous_correction_accepts_first_and_second_occurrences(self):
        self.login(2)
        for incident_id, choice in ((302, "first"), (303, "second")):
            with self.subTest(choice=choice):
                self.add_incident(incident_id)
                response = self.client.post(
                    f"/manager-review/incidents/{incident_id}/correct",
                    data=self.correction_data(
                        incident_id,
                        incident_date="2025-11-02",
                        incident_time="01:30",
                        repeated_hour_choice=choice,
                    ),
                )
                self.assertEqual(response.status_code, 302)
                audit = self.rows(
                    "SELECT event_datetime FROM activity_log "
                    "WHERE activity_type = 'incident_management_corrected' "
                    "AND related_id = ?",
                    (incident_id,),
                )[0]
                self.assertEqual(
                    audit["event_datetime"],
                    app.convert_vancouver_occurrence_input_to_utc(
                        "2025-11-02T01:30", choice
                    ),
                )

    def test_ambiguous_incident_rehydrates_storyline_details(self):
        incident_id = 304
        self.add_incident(incident_id)
        self.add_creation_audit(
            incident_id,
            app.convert_vancouver_occurrence_input_to_utc(
                "2026-08-02T10:00"
            ),
        )
        self.login(2)
        response = self.client.post(
            f"/manager-review/incidents/{incident_id}/correct",
            data=self.correction_data(
                incident_id,
                incident_date="2025-11-02",
                incident_time="01:30",
                repeated_hour_choice="second",
                description="Ambiguous corrected incident",
            ),
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get(
            "/client/1/storyline?filter=Incident&date=2025-11-02"
        )
        self.assertEqual(page.status_code, 200)
        self.assertEqual(
            page.data.count(b"Ambiguous corrected incident"), 1
        )
        self.assertNotIn(b"Incident corrected", page.data)

    def test_unchecked_injuries_state_is_preserved_on_error_rerender(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(
                "UPDATE incident_reports SET injuries = 1 WHERE incident_id = 41"
            )
            conn.commit()
        finally:
            conn.close()
        self.login(2)
        data = self.correction_data(incident_id=41)
        data.pop("injuries")
        data.update({
            "incident_date": "2026-03-08",
            "incident_time": "02:30",
        })
        response = self.client.post(
            "/manager-review/incidents/41/correct",
            data=data,
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotRegex(
            response.data,
            rb'name="injuries"[^>]*checked',
        )

    def test_storyline_rehydrates_current_source_and_preserves_one_event(self):
        self.add_incident(
            201,
            incident_date="2026-08-02",
            incident_time="10:00",
            description="Original unique incident",
        )
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                INSERT INTO activity_log
                (activity_datetime, activity_class, activity_type, user_id,
                 client_id, related_table, related_id, summary, details,
                 success, storyline_visible, event_datetime)
                VALUES ('2026-08-02 17:00:00', 'INCIDENT', 'incident_created',
                        1, 1, 'incident_reports', 201,
                        'Incident created: Medical',
                        'Location: Home\nInjury: Yes\nDescription: Original unique incident\nFollow-up required: Yes',
                        1, 1, '2026-08-02T17:00:00Z')
            """)
            conn.commit()
        finally:
            conn.close()

        self.login(2)
        response = self.client.post(
            "/manager-review/incidents/201/correct",
            data=self.correction_data(
                201,
                incident_date="2026-08-03",
                incident_time="12:00",
                incident_type="Other",
                location="Updated location",
                description="Updated unique incident",
                actions_taken="Updated action",
                witnesses="Updated witness",
                injury_details="Updated injury",
            ),
        )
        self.assertEqual(response.status_code, 302)

        old_day = self.client.get(
            "/client/1/storyline?filter=Incident&date=2026-08-02"
        )
        self.assertNotIn(b"Original unique incident", old_day.data)
        new_day = self.client.get(
            "/client/1/storyline?filter=Incident&date=2026-08-03"
        )
        self.assertEqual(new_day.data.count(b"Updated unique incident"), 1)
        self.assertIn(b"Incident created: Other", new_day.data)
        self.assertIn(b"Updated location", new_day.data)
        self.assertNotIn(b"Incident corrected", new_day.data)
        self.assertEqual(new_day.data.count(b"Updated unique incident"), 1)
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) AS count FROM activity_log "
                "WHERE activity_type = 'incident_management_corrected' "
                "AND storyline_visible = 1"
            )[0]["count"],
            0,
        )

    def test_storyline_missing_source_keeps_audit_fallback(self):
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("""
                INSERT INTO activity_log
                (activity_datetime, activity_class, activity_type, user_id,
                 client_id, related_table, related_id, summary, details,
                 success, storyline_visible, event_datetime)
                VALUES ('2026-08-02 18:00:00', 'INCIDENT', 'incident_created',
                        1, 1, 'incident_reports', 999,
                        'Missing incident audit', 'Legacy incident details',
                        1, 1, '2026-08-02T18:00:00Z')
            """)
            conn.commit()
        finally:
            conn.close()
        self.login(2)
        page = self.client.get(
            "/client/1/storyline?filter=Incident&date=2026-08-02"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Missing incident audit", page.data)
        self.assertIn(b"Legacy incident details", page.data)


if __name__ == "__main__":
    unittest.main()
