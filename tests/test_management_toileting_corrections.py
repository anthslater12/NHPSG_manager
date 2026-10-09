import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import add_toileting_events_table
import app


class ManagementToiletingCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "toileting.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self._create_database()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def _create_database(self):
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,
                full_name TEXT NOT NULL, role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER DEFAULT 1
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY, client_id INTEGER, shift_date TEXT,
                shift_type TEXT, status TEXT, scheduled_start_time TEXT,
                scheduled_end_time TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER, user_id INTEGER,
                active INTEGER DEFAULT 1, actual_start_time TEXT,
                actual_end_at_utc TEXT, actual_end_time TEXT,
                sign_on_at TEXT, sign_off_at TEXT
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_datetime TEXT, activity_class TEXT, activity_type TEXT,
                user_id INTEGER, client_id INTEGER, shift_id INTEGER,
                related_table TEXT, related_id INTEGER, summary TEXT, details TEXT,
                success INTEGER DEFAULT 1, storyline_visible INTEGER DEFAULT 0,
                event_datetime TEXT
            );
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT, source_id INTEGER, user_id INTEGER,
                acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                acknowledgement_type TEXT DEFAULT 'Review', comment TEXT,
                active INTEGER DEFAULT 1, invalidated_at_utc TEXT,
                invalidated_by_user_id INTEGER, invalidation_reason TEXT
            );
            CREATE TABLE management_notes (
                management_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT, source_id INTEGER, note_text TEXT,
                visibility TEXT, created_by_user_id INTEGER, created_at TEXT,
                active INTEGER DEFAULT 1, shared_at TEXT, shared_by_user_id INTEGER
            );
            CREATE TABLE action_items (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT, source_id INTEGER, title TEXT,
                description TEXT, status TEXT, priority TEXT,
                shift_id INTEGER, assigned_to_user_id INTEGER,
                created_by_user_id INTEGER, due_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO users VALUES
                (1, 'worker', 'x', 'Original Worker', 'Support Worker', 1),
                (2, 'manager', 'x', 'Program Manager', 'Program Manager', 1),
                (3, 'consultant', 'x', 'Behaviour Consultant', 'Behaviour Consultant', 1),
                (4, 'admin', 'x', 'Admin User', 'Admin', 1),
                (5, 'director', 'x', 'Director User', 'Director', 1),
                (6, 'inactive', 'x', 'Inactive Director', 'Director', 0);
            INSERT INTO clients VALUES (1, 'Client One', 1), (2, 'Client Two', 1);
            INSERT INTO shifts VALUES
                (10, 1, '2026-08-06', 'Day', 'Open', '07:00', '15:00'),
                (20, 2, '2026-08-06', 'Day', 'Open', '07:00', '15:00');
            INSERT INTO shift_staff VALUES
                (10, 10, 1, 1, '07:00', NULL, NULL,
                 '2026-08-06T07:00:00Z', NULL);
        """)
        add_toileting_events_table.migrate(conn)
        conn.commit()
        conn.close()

    def login(self, user_id=2, role="Program Manager"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = role

    def db(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_event(self, event_id=1, client_id=1, shift_id=10, active=1,
                  correction_of_event_id=None, event_datetime="2026-08-06T10:00",
                  event_type="BM"):
        conn = self.db()
        conn.execute("""
            INSERT INTO toileting_events
            (toileting_event_id, shift_id, client_id, recorded_by_user_id,
             event_type, event_datetime, location, bm_size, bm_consistency,
             bm_unusual_details, general_comments, correction_of_event_id,
             correction_reason, active)
            VALUES (?, ?, ?, 1, ?, ?, 'Bathroom', 'Medium', 'Soft',
                    NULL, 'Original note', ?, NULL, ?)
        """, (event_id, shift_id, client_id, event_type, event_datetime,
              correction_of_event_id, active))
        conn.commit()
        conn.close()

    def add_visible_creation_audit(self, event_id=1):
        conn = self.db()
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_class, activity_type, user_id, client_id,
             shift_id, related_table, related_id, summary, details, success,
             storyline_visible, event_datetime)
            VALUES ('2026-08-06 17:00:00', 'TOILETING',
                    'toileting_event_created', 1, 1, 10, 'toileting_events', ?,
                    'Toileting event recorded: BM', 'Original note', 1, 1,
                    '2026-08-06T17:00:00Z')
        """, (event_id,))
        conn.commit()
        conn.close()

    def add_review(self, event_id=1):
        conn = self.db()
        conn.execute("""
            INSERT INTO acknowledgements
            (source_table, source_id, user_id, acknowledgement_type, active)
            VALUES ('toileting_events', ?, 2, 'Review', 1)
        """, (event_id,))
        conn.commit()
        conn.close()

    def correction_data(self, **overrides):
        values = {
            "event_type": "BM",
            "event_datetime": "2026-08-06T11:00",
            "location": "Bedroom",
            "location_other": "",
            "bm_size": "Large",
            "bm_consistency": "Firm",
            "bm_unusual": "No",
            "bm_unusual_details": "",
            "urine_volume": "",
            "urine_unusual": "",
            "urine_unusual_details": "",
            "behaviour_before": "Calm",
            "behaviour_during": "Calm",
            "behaviour_after": "Calm",
            "behaviour_comments": "Updated behaviour",
            "general_comments": "Corrected note",
            "correction_reason": "Correcting the recorded details",
        }
        values.update(overrides)
        return values

    def test_management_correction_creates_active_leaf_and_invalidates_review(self):
        self.add_event()
        conn = self.db()
        conn.execute(
            "UPDATE toileting_events SET pain_or_distress = 1, "
            "other_concern = 0, concern_details = 'Original concern' "
            "WHERE toileting_event_id = 1"
        )
        conn.commit()
        conn.close()
        self.add_review()
        self.login()
        response = self.client.post(
            "/manager-review/toileting/1/correct",
            data=self.correction_data(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/manager-review/toileting/2", response.location)
        conn = self.db()
        rows = conn.execute(
            "SELECT * FROM toileting_events ORDER BY toileting_event_id"
        ).fetchall()
        review = conn.execute(
            "SELECT active, invalidated_at_utc, invalidated_by_user_id, "
            "invalidation_reason FROM acknowledgements WHERE source_id = 1"
        ).fetchone()
        audit = conn.execute(
            "SELECT activity_type, storyline_visible FROM activity_log "
            "WHERE activity_type = 'management_toileting_event_updated'"
        ).fetchone()
        conn.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["active"], 0)
        self.assertEqual(rows[1]["active"], 1)
        self.assertEqual(rows[1]["correction_of_event_id"], 1)
        self.assertEqual(rows[1]["recorded_by_user_id"], 1)
        self.assertEqual(rows[1]["general_comments"], "Corrected note")
        self.assertEqual(rows[1]["pain_or_distress"], 1)
        self.assertEqual(rows[1]["other_concern"], 0)
        self.assertEqual(rows[1]["concern_details"], "Original concern")
        self.assertEqual(review["active"], 0)
        self.assertTrue(review["invalidated_at_utc"])
        self.assertEqual(review["invalidated_by_user_id"], 2)
        self.assertEqual(
            review["invalidation_reason"],
            "Toileting event corrected by management",
        )
        self.assertEqual(audit["storyline_visible"], 0)

    def test_each_management_role_can_get_and_post_correction(self):
        for event_id, user_id, role in (
            (101, 2, "Program Manager"),
            (201, 4, "Admin"),
            (301, 5, "Director"),
        ):
            with self.subTest(role=role):
                self.add_event(event_id=event_id)
                self.login(user_id, role)
                self.assertEqual(
                    self.client.get(
                        f"/manager-review/toileting/{event_id}/correct"
                    ).status_code,
                    200,
                )
                response = self.client.post(
                    f"/manager-review/toileting/{event_id}/correct",
                    data=self.correction_data(
                        general_comments=f"{role} correction"
                    ),
                )
                self.assertEqual(response.status_code, 302)

    def test_inactive_management_user_is_denied_get_and_post(self):
        self.add_event()
        self.login(6, "Director")
        url = "/manager-review/toileting/1/correct"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(url, data=self.correction_data()).status_code,
            403,
        )

    def test_management_correction_requires_management_role_and_reason(self):
        self.add_event()
        self.login(3, "Behaviour Consultant")
        self.assertEqual(
            self.client.get("/manager-review/toileting/1/correct").status_code,
            403,
        )
        self.login()
        response = self.client.post(
            "/manager-review/toileting/1/correct",
            data=self.correction_data(correction_reason=""),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Correction reason is required", response.data)

    def test_database_role_authority_ignores_session_role_and_support_worker_is_denied(self):
        self.add_event()
        self.login(2, "Support Worker")
        self.assertEqual(
            self.client.get("/manager-review/toileting/1/correct").status_code,
            200,
        )

        self.login(1, "Admin")
        url = "/manager-review/toileting/1/correct"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(url, data=self.correction_data()).status_code,
            403,
        )

    def test_cancelled_shift_denies_correction(self):
        self.add_event()
        conn = self.db()
        conn.execute("UPDATE shifts SET status = 'Cancelled' WHERE shift_id = 10")
        conn.commit()
        conn.close()
        self.login()
        url = "/manager-review/toileting/1/correct"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(
            self.client.post(url, data=self.correction_data()).status_code,
            403,
        )

    def test_only_management_roles_see_correct_entry_on_review_detail(self):
        self.add_event()
        self.login(2, "Program Manager")
        manager_page = self.client.get("/manager-review/toileting/1")
        self.assertEqual(manager_page.status_code, 200)
        self.assertIn(b'class="button"', manager_page.data)
        self.assertIn(b"Correct Entry", manager_page.data)

        self.login(3, "Behaviour Consultant")
        consultant_page = self.client.get("/manager-review/toileting/1")
        self.assertEqual(consultant_page.status_code, 200)
        self.assertNotIn(b"Correct Entry", consultant_page.data)

    def test_correction_form_does_not_add_worker_hidden_concern_fields(self):
        self.add_event()
        self.login()
        page = self.client.get("/manager-review/toileting/1/correct")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'name="pain_or_distress"', page.data)
        self.assertNotIn(b'name="other_concern"', page.data)
        self.assertNotIn(b'name="concern_details"', page.data)

    def test_noop_does_not_create_child_or_audit(self):
        self.add_event()
        self.add_review()
        self.login()
        response = self.client.post(
            "/manager-review/toileting/1/correct",
            data=self.correction_data(
                event_datetime="2026-08-06T10:00",
                location="Bathroom",
                bm_size="Medium",
                bm_consistency="Soft",
                behaviour_before="",
                behaviour_during="",
                behaviour_after="",
                behaviour_comments="",
                general_comments="Original note",
            ),
        )
        self.assertEqual(response.status_code, 400)
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM toileting_events").fetchone()[0], 1)
        source = conn.execute(
            "SELECT event_datetime, general_comments FROM toileting_events "
            "WHERE toileting_event_id = 1"
        ).fetchone()
        self.assertEqual(source["event_datetime"], "2026-08-06T10:00")
        self.assertEqual(source["general_comments"], "Original note")
        self.assertEqual(
            conn.execute(
                "SELECT active FROM acknowledgements "
                "WHERE source_table = 'toileting_events' AND source_id = 1"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE activity_type = 'management_toileting_event_updated'"
        ).fetchone()[0], 0)
        conn.close()

    def test_repeated_corrections_form_one_linear_three_generation_chain(self):
        self.add_event()
        self.login()
        first = self.client.post(
            "/manager-review/toileting/1/correct",
            data=self.correction_data(event_datetime="2026-08-06T11:00"),
        )
        self.assertEqual(first.status_code, 302)
        second = self.client.post(
            "/manager-review/toileting/2/correct",
            data=self.correction_data(event_datetime="2026-08-06T12:00"),
        )
        self.assertEqual(second.status_code, 302)
        conn = self.db()
        rows = conn.execute(
            "SELECT toileting_event_id, active, correction_of_event_id, "
            "recorded_by_user_id FROM toileting_events "
            "ORDER BY toileting_event_id"
        ).fetchall()
        audits = conn.execute(
            "SELECT COUNT(*) FROM activity_log "
            "WHERE activity_type = 'management_toileting_event_updated'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(
            [(row[0], row[1], row[2], row[3]) for row in rows],
            [(1, 0, None, 1), (2, 0, 1, 1), (3, 1, 2, 1)],
        )
        self.assertEqual(audits, 2)

    def test_inactive_predecessor_resolves_to_leaf_and_cannot_be_reviewed(self):
        self.add_event()
        self.login()
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/correct",
                data=self.correction_data(),
            ).status_code,
            302,
        )
        stale_detail = self.client.get("/manager-review/toileting/1")
        self.assertEqual(stale_detail.status_code, 302)
        self.assertIn("/manager-review/toileting/2", stale_detail.location)
        self.assertEqual(
            self.client.post("/manager-review/toileting/1/review").status_code,
            409,
        )

    def test_review_list_contains_only_the_active_leaf(self):
        self.add_event()
        self.login()
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/correct",
                data=self.correction_data(),
            ).status_code,
            302,
        )
        page = self.client.get("/manager-review/toileting")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'href="/manager-review/toileting/2"', page.data)
        self.assertNotIn(b'href="/manager-review/toileting/1"', page.data)

    def test_malformed_loop_and_missing_predecessor_are_rejected(self):
        self.add_event()
        conn = self.db()
        conn.execute(
            "UPDATE toileting_events SET correction_of_event_id = 1 "
            "WHERE toileting_event_id = 1"
        )
        conn.commit()
        with self.assertRaises(app.ToiletingCorrectionChainError):
            app.resolve_toileting_correction_chain(conn, 1)
        conn.execute(
            "UPDATE toileting_events SET correction_of_event_id = NULL "
            "WHERE toileting_event_id = 1"
        )
        conn.execute("""
            INSERT INTO toileting_events
            (toileting_event_id, shift_id, client_id, recorded_by_user_id,
             event_type, event_datetime, location, correction_of_event_id,
             active)
            VALUES (2, 10, 1, 1, 'BM', '2026-08-06T11:00', 'Bathroom', 999, 1)
        """)
        conn.commit()
        with self.assertRaises(app.ToiletingCorrectionChainError):
            app.resolve_toileting_correction_chain(conn, 2)
        conn.close()

    def test_current_record_operations_fail_closed_on_malformed_chain(self):
        self.add_event()
        conn = self.db()
        conn.execute(
            "UPDATE toileting_events SET correction_of_event_id = 1 "
            "WHERE toileting_event_id = 1"
        )
        conn.commit()
        conn.close()
        self.login()
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/management-note",
                data={"note_text": "Should not be saved"},
            ).status_code,
            409,
        )
        self.assertEqual(
            self.client.get("/manager-review/toileting/1/action/new").status_code,
            409,
        )
        self.assertEqual(
            self.client.post("/manager-review/toileting/1/review").status_code,
            409,
        )

    def test_chain_notes_and_actions_remain_visible_and_new_links_use_leaf(self):
        self.add_event()
        conn = self.db()
        conn.execute(
            "INSERT INTO management_notes "
            "(source_table, source_id, note_text, visibility, "
            "created_by_user_id, created_at) VALUES "
            "('toileting_events', 1, 'Original management note', "
            "'management_only', 2, '2026-08-06 11:00:00')"
        )
        conn.execute(
            "INSERT INTO action_items "
            "(source_table, source_id, title, status, priority, created_at) "
            "VALUES ('toileting_events', 1, 'Original action', 'Open', "
            "'Medium', '2026-08-06 11:00:00')"
        )
        conn.commit()
        conn.close()
        self.login()
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/correct",
                data=self.correction_data(),
            ).status_code,
            302,
        )
        detail = self.client.get("/manager-review/toileting/2")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Original management note", detail.data)
        self.assertIn(b"Original action", detail.data)
        new_note = self.client.post(
            "/manager-review/toileting/2/management-note",
            data={"note_text": "Leaf management note"},
        )
        self.assertEqual(new_note.status_code, 302)
        new_action = self.client.post(
            "/manager-review/toileting/2/action/new",
            data={
                "title": "Leaf action",
                "description": "Follow up",
                "priority": "High",
                "assigned_to_user_id": "",
            },
        )
        self.assertEqual(new_action.status_code, 302)
        conn = self.db()
        note_source = conn.execute(
            "SELECT source_id FROM management_notes "
            "WHERE note_text = 'Leaf management note'"
        ).fetchone()[0]
        action_source = conn.execute(
            "SELECT source_id FROM action_items WHERE title = 'Leaf action'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(note_source, 2)
        self.assertEqual(action_source, 2)

    def test_storyline_rehydrates_active_leaf_and_hides_management_audit(self):
        self.add_event()
        self.add_visible_creation_audit()
        self.login()
        response = self.client.post(
            "/manager-review/toileting/1/correct",
            data=self.correction_data(),
        )
        self.assertEqual(response.status_code, 302)
        page = self.client.get("/client/1/storyline?date=2026-08-06")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data.count(b"Toileting event recorded: BM"), 1)
        self.assertIn(b"11:00", page.data)
        self.assertIn(b"Corrected note", page.data)
        self.assertNotIn(b"10:00", page.data)
        self.assertNotIn(b"management_toileting_event_updated", page.data)

    def test_storyline_keeps_original_audit_when_source_chain_is_missing(self):
        self.add_event()
        self.add_visible_creation_audit()
        conn = self.db()
        conn.execute("DELETE FROM toileting_events WHERE toileting_event_id = 1")
        conn.commit()
        conn.close()
        self.login()
        page = self.client.get("/client/1/storyline?date=2026-08-06")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Toileting event recorded: BM", page.data)
        self.assertIn(b"Original note", page.data)

    def test_correction_moving_across_local_date_moves_storyline_event(self):
        self.add_event()
        self.add_visible_creation_audit()
        self.login()
        response = self.client.post(
            "/manager-review/toileting/1/correct",
            data=self.correction_data(event_datetime="2026-08-07T00:30"),
        )
        self.assertEqual(response.status_code, 302)
        old_day = self.client.get("/client/1/storyline?date=2026-08-06")
        new_day = self.client.get("/client/1/storyline?date=2026-08-07")
        self.assertNotIn(b"Toileting event recorded: BM", old_day.data)
        self.assertEqual(
            new_day.data.count(b"Toileting event recorded: BM"), 1
        )
        self.assertIn(b"00:30", new_day.data)

    def test_chain_rejects_client_or_shift_mismatch_and_multiple_active_children(self):
        self.add_event()
        conn = self.db()
        conn.execute("UPDATE toileting_events SET client_id = 2 WHERE toileting_event_id = 1")
        conn.commit()
        conn.close()
        self.login()
        self.assertEqual(
            self.client.get("/manager-review/toileting/1/correct").status_code,
            403,
        )
        conn = self.db()
        with self.assertRaises(app.ToiletingCorrectionChainError):
            app.resolve_toileting_correction_chain(conn, 1)
        conn.execute("UPDATE toileting_events SET client_id = 1 WHERE toileting_event_id = 1")
        conn.execute("""
            INSERT INTO toileting_events
            (shift_id, client_id, recorded_by_user_id, event_type, event_datetime,
             location, active, correction_of_event_id)
            VALUES (10, 1, 1, 'BM', '2026-08-06T11:00', 'Bathroom', 1, 1)
        """)
        conn.execute("""
            INSERT INTO toileting_events
            (shift_id, client_id, recorded_by_user_id, event_type, event_datetime,
             location, active, correction_of_event_id)
            VALUES (10, 1, 1, 'BM', '2026-08-06T12:00', 'Bathroom', 1, 1)
        """)
        conn.commit()
        with self.assertRaises(app.ToiletingCorrectionChainError):
            app.resolve_toileting_correction_chain(conn, 1)
        conn.close()

    def test_missing_shift_client_or_recorder_rejects_correction(self):
        for event_id, (field, value) in enumerate((
            ("shift_id", 999),
            ("client_id", 999),
            ("recorded_by_user_id", 999),
        ), 1):
            with self.subTest(field=field):
                self.add_event(event_id=event_id)
                conn = self.db()
                conn.execute(
                    f"UPDATE toileting_events SET {field} = ? "
                    "WHERE toileting_event_id = ?",
                    (value, event_id),
                )
                conn.commit()
                conn.close()
                self.login()
                self.assertEqual(
                    self.client.get(
                        f"/manager-review/toileting/{event_id}/correct"
                    ).status_code,
                    403,
                )

    def test_child_with_different_recorder_is_rejected_and_storyline_falls_back(self):
        self.add_event()
        self.add_visible_creation_audit()
        self.login()
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/correct",
                data=self.correction_data(),
            ).status_code,
            302,
        )
        conn = self.db()
        conn.execute(
            "UPDATE toileting_events SET recorded_by_user_id = 2 "
            "WHERE toileting_event_id = 2"
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            self.client.get(
                "/manager-review/toileting/2/correct"
            ).status_code,
            403,
        )
        page = self.client.get(
            "/client/1/storyline?date=2026-08-06"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Original note", page.data)

    def test_review_state_is_leaf_specific(self):
        self.add_event()
        self.add_review()
        self.login()
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/correct",
                data=self.correction_data(),
            ).status_code,
            302,
        )
        conn = self.db()
        conn.execute(
            "INSERT INTO acknowledgements "
            "(source_table, source_id, user_id, acknowledgement_type, active) "
            "VALUES ('toileting_events', 1, 2, 'Review', 1)"
        )
        conn.commit()
        conn.close()
        page = self.client.get("/manager-review/toileting/2")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Mark as Reviewed", page.data)
        self.assertNotIn(b"You have reviewed this record.", page.data)

    def test_event_type_transitions_normalize_irrelevant_fields(self):
        self.login()
        transitions = (
            (1, "BM", "Urination", False, False),
            (10, "Urination", "BM", False, False),
            (20, "Both", "BM", True, False),
            (30, "Both", "Urination", False, True),
            (40, "BM", "BM", True, False),
        )
        for event_id, source_type, target_type, keep_bm, keep_urine in transitions:
            with self.subTest(source_type=source_type, target_type=target_type):
                self.add_event(event_id=event_id, event_type=source_type)
                conn = self.db()
                conn.execute("""
                    UPDATE toileting_events SET
                        bm_colour = 'Brown', estimated_bristol_type = 4,
                        bm_blood_observed = 1, bm_mucus_observed = 1,
                        bm_unusual_colour = 1, urine_colour = 'Dark',
                        urine_blood_observed = 1, urine_strong_odour = 1,
                        urine_unusual_colour = 1
                    WHERE toileting_event_id = ?
                """, (event_id,))
                conn.commit()
                conn.close()
                self.assertEqual(
                    self.client.post(
                        f"/manager-review/toileting/{event_id}/correct",
                        data=self.correction_data(
                            event_type=target_type,
                            event_datetime=f"2026-08-06T{11 + event_id // 10:02d}:00",
                        ),
                    ).status_code,
                    302,
                )
                conn = self.db()
                corrected = conn.execute(
                    "SELECT * FROM toileting_events WHERE toileting_event_id = ?",
                    (event_id + 1,),
                ).fetchone()
                conn.close()
                if keep_bm:
                    self.assertEqual(corrected["bm_colour"], "Brown")
                    self.assertEqual(corrected["estimated_bristol_type"], 4)
                    self.assertEqual(corrected["bm_blood_observed"], 1)
                else:
                    self.assertIsNone(corrected["bm_colour"])
                    self.assertIsNone(corrected["estimated_bristol_type"])
                    self.assertEqual(corrected["bm_blood_observed"], 0)
                if keep_urine:
                    self.assertEqual(corrected["urine_colour"], "Dark")
                    self.assertEqual(corrected["urine_strong_odour"], 1)
                else:
                    self.assertIsNone(corrected["urine_colour"])
                    self.assertEqual(corrected["urine_blood_observed"], 0)

    def test_forward_correction_chain_traversal_is_bounded(self):
        self.add_event(event_id=1)
        conn = self.db()
        for event_id in range(2, 102):
            conn.execute("""
                INSERT INTO toileting_events
                (toileting_event_id, shift_id, client_id, recorded_by_user_id,
                 event_type, event_datetime, location, correction_of_event_id,
                 active)
                VALUES (?, 10, 1, 1, 'BM', ?, 'Bathroom', ?, ?)
            """, (
                event_id, f"2026-08-06T{event_id % 24:02d}:00",
                event_id - 1, 1 if event_id == 101 else 0,
            ))
        conn.commit()
        with self.assertRaises(app.ToiletingCorrectionChainError):
            app.resolve_toileting_correction_chain(conn, 1)
        conn.close()

    def test_reporting_counts_one_active_logical_occurrence(self):
        self.add_event()
        self.login()
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/correct",
                data=self.correction_data(),
            ).status_code,
            302,
        )
        conn = self.db()
        report = app._toileting_report_context(
            conn, 1, date(2026, 8, 6), date(2026, 8, 6), "Daily"
        )
        conn.close()
        self.assertEqual(report["summary"]["total"], 1)

    def test_behaviour_consultant_can_review_note_and_action_but_not_correct(self):
        self.add_event()
        self.login(3, "Behaviour Consultant")
        self.assertEqual(
            self.client.post("/manager-review/toileting/1/review").status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/management-note",
                data={"note_text": "Consultant note"},
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/toileting/1/action/new",
                data={
                    "title": "Consultant action",
                    "description": "Follow up",
                    "priority": "Medium",
                    "assigned_to_user_id": "",
                },
            ).status_code,
            302,
        )
        self.assertEqual(
            self.client.get("/manager-review/toileting/1/correct").status_code,
            403,
        )


if __name__ == "__main__":
    unittest.main()
