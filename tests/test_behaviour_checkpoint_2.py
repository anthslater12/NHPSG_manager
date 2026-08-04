import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from werkzeug.datastructures import MultiDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import add_behaviour_occurrences_table as migration
import app


class BehaviourCheckpointTwoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, "behaviour.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        conn = sqlite3.connect(self.path)
        conn.executescript("""
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT,
          full_name TEXT, role TEXT, active INTEGER);
        CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER);
        CREATE TABLE shifts (shift_id INTEGER PRIMARY KEY, client_id INTEGER, status TEXT);
        CREATE TABLE shift_staff (shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER,
          user_id INTEGER, active INTEGER);
        CREATE TABLE activity_log (activity_id INTEGER PRIMARY KEY, activity_datetime TEXT,
          activity_class TEXT NOT NULL, activity_type TEXT NOT NULL, user_id INTEGER, client_id INTEGER,
          shift_id INTEGER, related_table TEXT, related_id INTEGER, summary TEXT NOT NULL, details TEXT, success INTEGER);
        INSERT INTO users VALUES (1,'worker','x','Worker','Support Worker',1);
        INSERT INTO users VALUES (2,'inactive','x','Inactive','Support Worker',0);
        INSERT INTO users VALUES (3,'other','x','Other','Support Worker',1);
        INSERT INTO clients VALUES (1,'Active Client',1);
        INSERT INTO clients VALUES (2,'Inactive Client',0);
        INSERT INTO shifts VALUES (10,1,'Open'), (11,1,'Closed'), (12,1,'Cancelled'), (20,2,'Open');
        INSERT INTO shift_staff VALUES (1,10,1,1), (2,20,1,1);
        """)
        migration.migrate(conn); conn.close()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1):
        with self.client.session_transaction() as session:
            session["user_id"] = user_id
            session["full_name"] = "Worker"
            session["role"] = "Support Worker"

    def payload(self, token="A" * 43, **extra):
        local = (datetime.now(app.VANCOUVER_TIMEZONE) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
        value = {"client_id": "1", "occurrence_local": local, "submission_token": token,
                 "self_harm": "1", "notes": " private note ",
                 "repeated_hour_choice": ""}
        value.update(extra)
        return value

    def counts(self):
        conn = sqlite3.connect(self.path)
        result = (conn.execute("SELECT COUNT(*) FROM behaviour_occurrences").fetchone()[0],
                  conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0])
        conn.close(); return result

    def test_authentication_active_clients_and_week_validation(self):
        self.assertEqual(self.client.get("/behaviour/record").status_code, 302)
        self.login()
        page = self.client.get("/behaviour/record")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Active Client", page.data)
        self.assertNotIn(b"Inactive Client", page.data)
        self.assertEqual(self.client.get("/behaviour/week/2026-01-06").status_code, 404)
        with self.client.session_transaction() as session: session["user_id"] = 2
        self.assertEqual(self.client.get("/behaviour/record").status_code, 403)

    def test_shift_behaviour_route_requires_assignment_and_open_shift(self):
        self.assertEqual(self.client.get("/shift/10/behaviour").status_code, 302)
        self.login()
        self.assertEqual(self.client.get("/shift/11/behaviour").status_code, 403)
        self.assertEqual(self.client.get("/shift/12/behaviour").status_code, 403)
        self.assertEqual(self.client.get("/shift/20/behaviour").status_code, 403)
        self.login(3)
        self.assertEqual(self.client.get("/shift/10/behaviour").status_code, 403)

    def test_shift_behaviour_uses_authoritative_client_and_audits_shift(self):
        self.login()
        page = self.client.get("/shift/10/behaviour")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b'<select name="client_id"', page.data)
        response = self.client.post("/shift/10/behaviour", data=self.payload(
            token="S" * 43, client_id="2", notes="tampered"
        ))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.counts(), (0, 0))
        valid = self.payload(token="T" * 43, notes="shift-scoped note")
        valid.pop("client_id")
        response = self.client.post("/shift/10/behaviour", data=valid)
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.path)
        row = conn.execute("""
            SELECT client_id, shift_id, user_id, related_table, related_id,
                   success, activity_type
            FROM activity_log
        """).fetchone()
        occurrence = conn.execute(
            "SELECT client_id FROM behaviour_occurrences"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0:4], (1, 10, 1, "behaviour_occurrences"))
        self.assertEqual(row[4], 1)
        self.assertEqual(row[5:7], (1, "behaviour_occurrence_created"))
        self.assertEqual(occurrence[0], 1)

    def test_record_multi_category_idempotency_and_audit(self):
        self.login()
        data = self.payload(property_damage="1")
        first = self.client.post("/behaviour/record", data=data)
        second = self.client.post("/behaviour/record", data=data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self.counts(), (1, 1))
        conn = sqlite3.connect(self.path); row = conn.execute("SELECT notes, recorded_by_user_id, status, occurred_at_utc FROM behaviour_occurrences").fetchone(); log = conn.execute("SELECT details FROM activity_log").fetchone()[0]; conn.close()
        self.assertEqual(row[0], "private note")
        self.assertEqual(row[1:3], (1, "Recorded"))
        self.assertRegex(row[3], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(
            log,
            "Categories:\nSelf-Harm\nProperty Damage\n\nNotes:\nprivate note"
        )

    def test_validation_and_other_user_token_collision(self):
        self.login()
        self.assertEqual(self.client.post("/behaviour/record", data=self.payload(token="bad")).status_code, 400)
        self.assertEqual(self.client.post("/behaviour/record", data=self.payload(token="B" * 43, self_harm="0")).status_code, 400)
        self.assertEqual(self.counts(), (0, 0))
        self.client.post("/behaviour/record", data=self.payload(token="C" * 43))
        self.login(3)
        response = self.client.post("/behaviour/record", data=self.payload(token="C" * 43))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.counts(), (1, 1))

    def test_complete_category_multimap_validation(self):
        self.login()
        invalid = MultiDict(self.payload(token="E" * 43))
        invalid.add("unapproved_category", "1")
        self.assertEqual(self.client.post("/behaviour/record", data=invalid).status_code, 400)
        for field in app.BEHAVIOUR_CATEGORY_FIELDS:
            duplicated = MultiDict(self.payload(token=("F" * 42) + field[0]))
            duplicated.setlist(field, ["1", "1"])
            self.assertEqual(self.client.post("/behaviour/record", data=duplicated).status_code, 400)
            malformed = MultiDict(self.payload(token=("G" * 42) + field[0]))
            malformed.setlist(field, ["1", "not-1"])
            self.assertEqual(self.client.post("/behaviour/record", data=malformed).status_code, 400)
        self.assertEqual(self.counts(), (0, 0))

    def test_ambiguity_choice_validation_and_distinct_fallback_instants(self):
        self.login()
        ordinary = self.payload(token="H" * 43, repeated_hour_choice="first")
        self.assertEqual(self.client.post("/behaviour/record", data=ordinary).status_code, 400)
        no_choice = self.payload(token="I" * 43, occurrence_local="2024-11-03T01:30")
        self.assertEqual(self.client.post("/behaviour/record", data=no_choice).status_code, 400)
        first = self.payload(token="J" * 43, occurrence_local="2024-11-03T01:30", repeated_hour_choice="first")
        second = self.payload(token="K" * 43, occurrence_local="2024-11-03T01:30", repeated_hour_choice="second")
        self.assertEqual(self.client.post("/behaviour/record", data=first).status_code, 302)
        self.assertEqual(self.client.post("/behaviour/record", data=second).status_code, 302)
        conn = sqlite3.connect(self.path)
        instants = [row[0] for row in conn.execute("SELECT occurred_at_utc FROM behaviour_occurrences ORDER BY occurred_at_utc")]
        conn.close()
        self.assertEqual(instants, ["2024-11-03T08:30:00Z", "2024-11-03T09:30:00Z"])

    def test_absent_choice_is_valid_only_for_ordinary_time(self):
        self.login()
        ordinary = self.payload(token="M" * 43)
        ordinary.pop("repeated_hour_choice")
        self.assertEqual(self.client.post("/behaviour/record", data=ordinary).status_code, 302)
        ambiguous = self.payload(token="N" * 43, occurrence_local="2024-11-03T01:30")
        ambiguous.pop("repeated_hour_choice")
        self.assertEqual(self.client.post("/behaviour/record", data=ambiguous).status_code, 400)
        spring = self.payload(token="O" * 43, occurrence_local="2024-03-10T02:30")
        spring.pop("repeated_hour_choice")
        self.assertEqual(self.client.post("/behaviour/record", data=spring).status_code, 400)

    def test_activity_log_failure_rolls_back_occurrence(self):
        self.login()
        conn = sqlite3.connect(self.path)
        conn.execute("""CREATE TRIGGER reject_behaviour_audit BEFORE INSERT ON activity_log
            BEGIN SELECT RAISE(ABORT, 'forced audit failure'); END""")
        conn.commit(); conn.close()
        response = self.client.post("/behaviour/record", data=self.payload(token="P" * 43))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.counts(), (0, 0))

    def test_weekly_route_boundary_and_dst_intervals(self):
        self.login()
        conn = sqlite3.connect(self.path)
        rows = [
            ("2024-03-04T07:00:00Z", "spring-start"),
            ("2024-03-11T06:00:00Z", "following-monday"),
            ("2024-11-04T07:00:00Z", "fall-start"),
            ("2024-11-11T07:00:00Z", "following-monday-fall"),
        ]
        for instant, token in rows:
            conn.execute("""INSERT INTO behaviour_occurrences
              (client_id, occurred_at_utc, aggression_towards_others, notes, recorded_by_user_id,
               recorded_at_utc, submission_token) VALUES (1, ?, 1, ?, 1, ?, ?)""",
              (instant, token, instant, token))
        conn.commit(); conn.close()
        spring = self.client.get("/behaviour/week/2024-03-04").data
        self.assertIn(b"spring-start", spring)
        self.assertNotIn(b"following-monday", spring)
        fall = self.client.get("/behaviour/week/2024-11-04").data
        self.assertIn(b"fall-start", fall)
        self.assertNotIn(b"following-monday-fall", fall)

    def test_weekly_route_places_exact_boundaries_in_correct_bands_and_counts(self):
        self.login()
        conn = sqlite3.connect(self.path)
        rows = (
            ("2024-01-08T07:00:00Z", "boundary-night", 1, 0, 0, 0, 0),
            ("2024-01-08T15:30:00Z", "boundary-day", 0, 1, 0, 0, 0),
            ("2024-01-08T23:30:00Z", "boundary-evening", 0, 0, 1, 0, 1),
            ("2024-01-15T07:00:00Z", "next-week-sunday-2300", 1, 0, 0, 0, 0),
        )
        for instant, note, a, i, s, self_i, property_d in rows:
            conn.execute("""INSERT INTO behaviour_occurrences
              (client_id, occurred_at_utc, aggression_towards_others, injury_to_others,
               self_harm, injury_to_self, property_damage, notes, recorded_by_user_id,
               recorded_at_utc, submission_token) VALUES (1, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
              (instant, a, i, s, self_i, property_d, note, instant, note))
        conn.commit(); conn.close()
        page = self.client.get("/behaviour/week/2024-01-08").data.decode()
        monday = page.split("<h3>Monday 2024-01-08</h3>", 1)[1].split("<h3>Tuesday", 1)[0]
        night, remainder = monday.split("<h4>Night</h4>", 1)[1].split("<h4>Day</h4>", 1)
        day, evening = remainder.split("<h4>Evening</h4>", 1)
        self.assertIn("boundary-night", night)
        self.assertNotIn("boundary-night", day + evening)
        self.assertIn("<td>1</td><td>0</td><td>0</td><td>0</td><td>0</td>", night)
        self.assertIn("boundary-day", day)
        self.assertIn("<td>0</td><td>1</td><td>0</td><td>0</td><td>0</td>", day)
        self.assertIn("boundary-evening", evening)
        self.assertIn("<td>0</td><td>0</td><td>1</td><td>0</td><td>1</td>", evening)
        self.assertNotIn("next-week-sunday-2300", page)
        next_week = self.client.get("/behaviour/week/2024-01-15").data.decode()
        self.assertIn("next-week-sunday-2300", next_week)

    def test_activity_log_complete_payload_for_multi_category_occurrence(self):
        self.login()
        response = self.client.post("/behaviour/record", data=self.payload(
            token="Q" * 43, aggression_towards_others="1", property_damage="1",
            notes="sensitive behaviour note"
        ))
        self.assertEqual(response.status_code, 302)
        conn = sqlite3.connect(self.path)
        occurrence = conn.execute("SELECT behaviour_occurrence_id, occurred_at_utc FROM behaviour_occurrences").fetchone()
        audit = conn.execute("""SELECT activity_class, activity_type, user_id, client_id,
            related_table, related_id, summary, details FROM activity_log""").fetchone()
        conn.close()
        self.assertEqual(audit[:6], ("BEHAVIOUR", "behaviour_occurrence_created", 1, 1,
                                     "behaviour_occurrences", occurrence[0]))
        self.assertEqual(audit[7], (
            "Categories:\nAggression towards others\nSelf-Harm\nProperty Damage\n\n"
            "Notes:\nsensitive behaviour note"
        ))
        self.assertNotIn(occurrence[1], audit[7])
        self.assertNotIn("recorded", audit[7].lower())
        self.assertNotIn("1", audit[7])
        self.assertNotIn("Void", " ".join(str(value) for value in audit))
        self.assertEqual(self.counts(), (1, 1))

    def test_unrelated_integrity_error_is_not_replay(self):
        self.login()
        conn = sqlite3.connect(self.path)
        conn.execute("""CREATE TRIGGER reject_behaviour_insert BEFORE INSERT ON behaviour_occurrences
            BEGIN SELECT RAISE(ABORT, 'forced unrelated integrity error'); END""")
        conn.commit(); conn.close()
        response = self.client.post("/behaviour/record", data=self.payload(token="L" * 43))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.counts(), (0, 0))

    def test_weekly_counts_void_visibility_and_recent_client(self):
        self.login()
        self.client.post("/behaviour/record", data=self.payload(token="D" * 43, aggression_towards_others="1"))
        conn = sqlite3.connect(self.path); conn.execute("UPDATE behaviour_occurrences SET status='Voided', voided_by_user_id=1, voided_at_utc='2026-01-01T00:00:00Z', void_reason='test'"); conn.commit(); conn.close()
        week = app.get_behaviour_operational_week_start(datetime.now(app.VANCOUVER_TIMEZONE)).isoformat()
        page = self.client.get("/behaviour/week/" + week)
        self.assertIn(b"Voided", page.data)
        self.assertNotIn(b"Recent occurrences", self.client.get("/behaviour/record?client_id=1").data)


if __name__ == "__main__":
    unittest.main()
