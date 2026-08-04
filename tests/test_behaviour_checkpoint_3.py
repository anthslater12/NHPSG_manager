import os
import sqlite3
import sys
import tempfile
import unittest
from werkzeug.datastructures import MultiDict


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import add_behaviour_occurrences_table as migration
import app


class BehaviourCheckpointThreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "behaviour_checkpoint_three.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT,
                password_hash TEXT, full_name TEXT, role TEXT, active INTEGER);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER);
            CREATE TABLE activity_log (activity_id INTEGER PRIMARY KEY, activity_datetime TEXT,
                activity_class TEXT NOT NULL, activity_type TEXT NOT NULL, user_id INTEGER,
                client_id INTEGER, shift_id INTEGER, related_table TEXT, related_id INTEGER,
                summary TEXT NOT NULL, details TEXT, success INTEGER);
            INSERT INTO users VALUES
                (1, 'admin', 'x', 'Admin', 'Admin', 1),
                (2, 'manager', 'x', 'Manager', 'Program Manager', 1),
                (3, 'director', 'x', 'Director', 'Director', 1),
                (4, 'worker', 'x', 'Worker', 'Support Worker', 1),
                (5, 'inactive', 'x', 'Inactive', 'Admin', 0);
            INSERT INTO clients VALUES (1, 'Client One', 1);
        """)
        migration.migrate(conn)
        conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id, role="Support Worker"):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = "Untrusted session name"

    def insert_occurrence(self, token="token-1", **overrides):
        values = {
            "client_id": 1,
            "occurred_at_utc": "2024-01-08T15:30:00Z",
            "aggression_towards_others": 1,
            "injury_to_others": 0,
            "self_harm": 0,
            "injury_to_self": 0,
            "property_damage": 0,
            "notes": "original notes",
            "recorded_by_user_id": 4,
            "recorded_at_utc": "2024-01-08T15:31:00Z",
            "submission_token": token,
        }
        values.update(overrides)
        conn = sqlite3.connect(self.path)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cur = conn.execute(
            f"INSERT INTO behaviour_occurrences ({columns}) VALUES ({placeholders})",
            tuple(values.values())
        )
        conn.commit()
        occurrence_id = cur.lastrowid
        conn.close()
        return occurrence_id

    def occurrence(self, occurrence_id):
        conn = sqlite3.connect(self.path)
        row = conn.execute("SELECT * FROM behaviour_occurrences WHERE behaviour_occurrence_id = ?", (occurrence_id,)).fetchone()
        conn.close()
        return row

    def audit_rows(self):
        conn = sqlite3.connect(self.path)
        rows = conn.execute("SELECT activity_class, activity_type, user_id, client_id, related_table, related_id, summary, details, success FROM activity_log ORDER BY activity_id").fetchall()
        conn.close()
        return rows

    def void(self, occurrence_id, reason="Incorrect duplicate"):
        return self.client.post(
            f"/behaviour/occurrences/{occurrence_id}/void",
            data={"void_reason": reason}, follow_redirects=False
        )

    def test_only_current_active_management_roles_can_void(self):
        occurrence_id = self.insert_occurrence()
        self.assertEqual(self.void(occurrence_id).status_code, 302)
        self.login(4, "Admin")
        self.assertEqual(self.void(occurrence_id).status_code, 403)
        self.login(5, "Admin")
        self.assertEqual(self.void(occurrence_id).status_code, 403)
        for user_id, role in ((1, "Support Worker"), (2, "Support Worker"), (3, "Support Worker")):
            occurrence_id = self.insert_occurrence(token=f"management-role-{user_id}")
            self.login(user_id, role)
            response = self.void(occurrence_id)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.occurrence(occurrence_id)[12], "Voided")

    def test_void_preserves_original_data_and_writes_exact_audit(self):
        occurrence_id = self.insert_occurrence(
            aggression_towards_others=1, self_harm=1, property_damage=1,
            notes="private original note"
        )
        self.login(2)
        response = self.void(occurrence_id, "Entered twice")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/behaviour/week/2024-01-08")
        row = self.occurrence(occurrence_id)
        self.assertEqual(row[1:12], (1, "2024-01-08T15:30:00Z", 1, 0, 1, 0, 1,
                                     "private original note", 4, "2024-01-08T15:31:00Z", "token-1"))
        self.assertEqual(row[12], "Voided")
        self.assertEqual(row[13], 2)
        self.assertRegex(row[14], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(row[15], "Entered twice")
        audits = self.audit_rows()
        self.assertEqual(len(audits), 1)
        audit = audits[0]
        self.assertEqual(audit[:7], ("BEHAVIOUR", "behaviour_occurrence_voided", 2, 1,
                                     "behaviour_occurrences", occurrence_id,
                                     "Behaviour occurrence voided"))
        self.assertEqual(audit[8], 1)
        self.assertEqual(audit[7], "Status: Voided")
        self.assertNotIn("Entered twice", audit[7])
        self.assertNotIn("private original note", audit[7])

    def test_reason_input_and_repeated_void_are_rejected_without_extra_audit(self):
        occurrence_id = self.insert_occurrence()
        self.login(1)
        for reason in ("", "   "):
            self.assertEqual(self.void(occurrence_id, reason).status_code, 400)
            self.assertEqual(self.occurrence(occurrence_id)[12], "Recorded")
            self.assertEqual(self.audit_rows(), [])
        self.assertEqual(self.client.post(
            f"/behaviour/occurrences/{occurrence_id}/void",
            data=MultiDict([("void_reason", "first"), ("void_reason", "second")])
        ).status_code, 400)
        self.assertEqual(self.void(occurrence_id).status_code, 302)
        self.assertEqual(self.void(occurrence_id, "again").status_code, 409)
        self.assertEqual(len(self.audit_rows()), 1)

    def test_missing_or_unsafe_submission_is_rejected(self):
        self.login(1)
        self.assertEqual(self.void(9999).status_code, 404)
        occurrence_id = self.insert_occurrence()
        self.assertEqual(self.client.post(
            f"/behaviour/occurrences/{occurrence_id}/void",
            data={"void_reason": "reason", "status": "Recorded"}
        ).status_code, 400)
        self.assertEqual(self.occurrence(occurrence_id)[12], "Recorded")
        self.assertEqual(self.audit_rows(), [])

    def test_audit_failure_rolls_back_void_transition(self):
        occurrence_id = self.insert_occurrence()
        conn = sqlite3.connect(self.path)
        conn.execute("""CREATE TRIGGER reject_behaviour_void_audit BEFORE INSERT ON activity_log
            BEGIN SELECT RAISE(ABORT, 'forced void audit failure'); END""")
        conn.commit()
        conn.close()
        self.login(1)
        self.assertEqual(self.void(occurrence_id).status_code, 500)
        row = self.occurrence(occurrence_id)
        self.assertEqual(row[12:16], ("Recorded", None, None, None))
        self.assertEqual(self.audit_rows(), [])

    def test_update_failure_creates_no_audit_or_partial_void(self):
        occurrence_id = self.insert_occurrence()
        conn = sqlite3.connect(self.path)
        conn.execute("""CREATE TRIGGER reject_behaviour_void_update
            BEFORE UPDATE OF status ON behaviour_occurrences
            BEGIN SELECT RAISE(ABORT, 'forced void update failure'); END""")
        conn.commit()
        conn.close()
        self.login(1)
        self.assertEqual(self.void(occurrence_id).status_code, 500)
        row = self.occurrence(occurrence_id)
        self.assertEqual(row[12:16], ("Recorded", None, None, None))
        self.assertEqual(self.audit_rows(), [])

    def test_weekly_visibility_counts_and_controls_follow_database_authority(self):
        occurrence_id = self.insert_occurrence()
        self.login(4, "Admin")
        worker_page = self.client.get("/behaviour/week/2024-01-08")
        self.assertEqual(worker_page.status_code, 200)
        self.assertNotIn(b"Void record", worker_page.data)
        self.login(1, "Support Worker")
        manager_page = self.client.get("/behaviour/week/2024-01-08")
        self.assertIn(b"Void record", manager_page.data)
        self.assertEqual(self.void(occurrence_id).status_code, 302)
        page = self.client.get("/behaviour/week/2024-01-08")
        self.assertIn(b"Voided by Admin", page.data)
        self.assertIn(b"Incorrect duplicate", page.data)
        self.assertIn(b"<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td>", page.data)

    def test_corrected_occurrence_is_a_new_record(self):
        original_id = self.insert_occurrence(token="original")
        self.login(1)
        self.assertEqual(self.void(original_id, "Incorrect time").status_code, 302)
        corrected_id = self.insert_occurrence(
            token="corrected", occurred_at_utc="2024-01-08T16:00:00Z"
        )
        self.assertNotEqual(original_id, corrected_id)
        self.assertEqual(self.occurrence(original_id)[12], "Voided")
        self.assertEqual(self.occurrence(corrected_id)[12], "Recorded")

    def test_checkpoint_three_has_no_staff_notice_dependency(self):
        self.assertNotIn("staff_notice", app.behaviour_occurrence_void.__code__.co_names)
        self.assertNotIn("staff_notice", app._behaviour_week_occurrences.__code__.co_names)


if __name__ == "__main__":
    unittest.main()
