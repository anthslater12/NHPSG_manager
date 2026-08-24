import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_sleep_events_table
import app


class SleepStorylineReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "sleep_storyline.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.addCleanup(self.cleanup)

        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                role TEXT,
                active INTEGER
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT,
                active INTEGER
            );
            CREATE TABLE shifts (
                shift_id INTEGER PRIMARY KEY,
                client_id INTEGER,
                shift_date TEXT,
                shift_type TEXT,
                status TEXT
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY,
                shift_id INTEGER,
                user_id INTEGER,
                active INTEGER
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
                event_datetime TEXT NULL
            );
            CREATE TABLE acknowledgements (
                acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,
                acknowledgement_type TEXT DEFAULT 'Read',
                comment TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE UNIQUE INDEX ux_acknowledgements_active_source_user
                ON acknowledgements(source_table, source_id, user_id)
                WHERE active = 1;

            INSERT INTO users VALUES
                (1, 'Worker', 'Support Worker', 1),
                (2, 'Admin User', 'Admin', 1),
                (3, 'Director User', 'Director', 1),
                (4, 'Program Manager User', 'Program Manager', 1),
                (5, 'Other Manager', 'Program Manager', 1),
                (6, 'Inactive Admin', 'Admin', 0);
            INSERT INTO clients VALUES
                (1, 'Client One', 1),
                (2, 'Client Two', 1);
            INSERT INTO shifts VALUES
                (10, 1, '2026-08-02', 'Day', 'Open'),
                (20, 2, '2026-08-03', 'Overnight', 'Open');
            INSERT INTO shift_staff VALUES (100, 10, 1, 1);
        """)
        add_sleep_events_table.migrate(conn)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id, role):
        with self.client.session_transaction() as session:
            session.update(
                user_id=user_id,
                role=role,
                full_name="Untrusted session name"
            )

    def add_sleep_event(
        self,
        sleep_event_id,
        event_type="fell_asleep",
        client_id=1,
        shift_id=10,
        note="Settled after music"
    ):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO sleep_events
            (sleep_event_id, client_id, shift_id, event_type,
             event_datetime, recorded_by_user_id, note, created_at)
            VALUES (?, ?, ?, ?, '2026-08-02T15:30:00Z', 1, ?,
                    '2026-08-02 15:31:00')
        """, (
            sleep_event_id, client_id, shift_id, event_type, note
        ))
        conn.commit()
        conn.close()

    def add_storyline_event(
        self,
        activity_type,
        related_id,
        client_id=1,
        shift_id=10,
        related_table="sleep_events",
        visible=1
    ):
        conn = sqlite3.connect(self.path)
        cursor = conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_class, activity_type, user_id,
             client_id, shift_id, related_table, related_id, summary,
             details, success, storyline_visible, event_datetime)
            VALUES ('2026-08-02 08:31:00', 'SLEEP', ?, 1, ?, ?, ?, ?,
                    ?, 'Note: Settled after music', 1, ?,
                    '2026-08-02T15:30:00Z')
        """, (
            activity_type, client_id, shift_id, related_table, related_id,
            "Client fell asleep"
            if activity_type == "sleep_fell_asleep"
            else "Client woke up",
            visible
        ))
        conn.commit()
        activity_id = cursor.lastrowid
        conn.close()
        return activity_id

    def add_acknowledgement(
        self,
        sleep_event_id,
        user_id,
        acknowledgement_type="Review",
        active=1
    ):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO acknowledgements
            (source_table, source_id, user_id, acknowledgement_type, active)
            VALUES ('sleep_events', ?, ?, ?, ?)
        """, (
            sleep_event_id, user_id, acknowledgement_type, active
        ))
        conn.commit()
        conn.close()

    def counts(self):
        conn = sqlite3.connect(self.path)
        result = (
            conn.execute("SELECT COUNT(*) FROM sleep_events").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM acknowledgements").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0],
        )
        conn.close()
        return result

    def test_all_active_management_roles_can_open_detail(self):
        self.add_sleep_event(1)
        for user_id, role in (
            (2, "Admin"),
            (3, "Director"),
            (4, "Program Manager"),
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                response = self.client.get("/manager-review/sleep/1")
                self.assertEqual(response.status_code, 200)

    def test_support_worker_is_denied_get_and_post(self):
        self.add_sleep_event(1)
        self.login(1, "Support Worker")
        self.assertEqual(
            self.client.get("/manager-review/sleep/1").status_code,
            403
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/sleep/1/review"
            ).status_code,
            403
        )

    def test_inactive_management_user_is_denied(self):
        self.add_sleep_event(1)
        self.login(6, "Admin")
        self.assertEqual(
            self.client.get("/manager-review/sleep/1").status_code,
            403
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/sleep/1/review"
            ).status_code,
            403
        )

    def test_missing_source_returns_404_without_writes(self):
        self.login(2, "Admin")
        before = self.counts()
        self.assertEqual(
            self.client.get("/manager-review/sleep/999").status_code,
            404
        )
        self.assertEqual(
            self.client.post(
                "/manager-review/sleep/999/review"
            ).status_code,
            404
        )
        self.assertEqual(self.counts(), before)

    def test_get_detail_is_read_only_and_renders_fell_asleep(self):
        self.add_sleep_event(1)
        self.login(2, "Admin")
        before = self.counts()
        response = self.client.get("/manager-review/sleep/1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(), before)
        self.assertIn(b"Client One", response.data)
        self.assertIn(b"2026-08-02 Day", response.data)
        self.assertIn(b"Fell Asleep", response.data)
        self.assertIn(b"2026-08-02 08:30 AM", response.data)
        self.assertIn(b"Settled after music", response.data)
        self.assertIn(b"Worker", response.data)
        self.assertIn(b"2026-08-02 15:31:00", response.data)

    def test_woke_up_and_blank_note_render_cleanly(self):
        self.add_sleep_event(2, event_type="woke_up", note=None)
        self.login(3, "Director")
        response = self.client.get("/manager-review/sleep/2")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Woke Up", response.data)
        self.assertIn(b"No note recorded.", response.data)

    def test_review_creates_current_managers_active_review_idempotently(self):
        self.add_sleep_event(1)
        self.login(2, "Admin")
        for _ in range(2):
            response = self.client.post(
                "/manager-review/sleep/1/review"
            )
            self.assertEqual(response.status_code, 302)

        conn = sqlite3.connect(self.path)
        reviews = conn.execute("""
            SELECT source_table, source_id, user_id,
                   acknowledgement_type, active
            FROM acknowledgements
        """).fetchall()
        source = conn.execute(
            "SELECT event_type, note FROM sleep_events WHERE sleep_event_id=1"
        ).fetchone()
        audit = conn.execute("""
            SELECT activity_class, activity_type, storyline_visible
            FROM activity_log
        """).fetchall()
        conn.close()
        self.assertEqual(
            reviews,
            [("sleep_events", 1, 2, "Review", 1)]
        )
        self.assertEqual(source, ("fell_asleep", "Settled after music"))
        self.assertEqual(
            audit,
            [("ACKNOWLEDGEMENT", "record_acknowledged", 0)]
        )

    def test_another_managers_review_does_not_count_for_current_manager(self):
        self.add_sleep_event(1)
        self.add_acknowledgement(1, 5)
        self.login(2, "Admin")
        response = self.client.get("/manager-review/sleep/1")
        self.assertIn(b"Other Manager", response.data)
        self.assertIn(b"Review required", response.data)
        self.assertNotIn(b"You have reviewed this Sleep event", response.data)

    def test_inactive_review_does_not_count(self):
        self.add_sleep_event(1)
        self.add_acknowledgement(1, 2, active=0)
        self.login(2, "Admin")
        response = self.client.get("/manager-review/sleep/1")
        self.assertIn(b"Review required", response.data)
        self.assertNotIn(b"You have reviewed this Sleep event", response.data)

    def test_non_review_acknowledgement_does_not_count(self):
        self.add_sleep_event(1)
        self.add_acknowledgement(1, 2, acknowledgement_type="Read")
        self.login(2, "Admin")
        response = self.client.get("/manager-review/sleep/1")
        self.assertIn(b"Review required", response.data)
        self.assertNotIn(b"You have reviewed this Sleep event", response.data)

    def test_both_sleep_storyline_types_get_controls(self):
        self.add_sleep_event(1, event_type="fell_asleep")
        self.add_sleep_event(2, event_type="woke_up")
        first_activity = self.add_storyline_event(
            "sleep_fell_asleep", 1
        )
        second_activity = self.add_storyline_event(
            "sleep_woke_up", 2
        )
        self.login(4, "Program Manager")
        response = self.client.get("/client/1/storyline?filter=Sleep")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b"View details"), 2)
        self.assertIn(b"/manager-review/sleep/1?", response.data)
        self.assertIn(b"/manager-review/sleep/2?", response.data)
        self.assertIn(
            f'id="storyline-event-{first_activity}"'.encode(),
            response.data
        )
        self.assertIn(
            f'id="storyline-event-{second_activity}"'.encode(),
            response.data
        )

    def test_current_manager_storyline_review_status_is_per_user(self):
        self.add_sleep_event(1)
        self.add_storyline_event("sleep_fell_asleep", 1)
        self.add_acknowledgement(1, 2)
        self.login(2, "Admin")
        reviewed = self.client.get("/client/1/storyline").data
        self.assertIn(b"You have reviewed this", reviewed)
        self.login(4, "Program Manager")
        unreviewed = self.client.get("/client/1/storyline").data
        self.assertIn(b"Review required", unreviewed)
        self.assertNotIn(b"You have reviewed this", unreviewed)

    def test_invalid_storyline_sources_do_not_get_controls(self):
        self.add_sleep_event(1)
        self.add_sleep_event(2, client_id=2, shift_id=20)
        self.add_sleep_event(3, client_id=1, shift_id=20)
        self.add_storyline_event(
            "sleep_fell_asleep", 1, related_table="other_table"
        )
        self.add_storyline_event("sleep_woke_up", 999)
        self.add_storyline_event("sleep_fell_asleep", 2)
        self.add_storyline_event("sleep_woke_up", 3)
        self.login(2, "Admin")
        response = self.client.get("/client/1/storyline")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"View details", response.data)

    def test_support_worker_gets_no_storyline_management_controls(self):
        self.add_sleep_event(1)
        self.add_storyline_event("sleep_fell_asleep", 1)
        self.login(1, "Support Worker")
        response = self.client.get("/client/1/storyline")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client fell asleep", response.data)
        self.assertNotIn(b"View details", response.data)
        self.assertNotIn(b"Review required", response.data)

    def test_storyline_context_survives_review_post(self):
        self.add_sleep_event(1)
        self.login(2, "Admin")
        detail = self.client.get(
            "/manager-review/sleep/1?storyline_client_id=1&"
            "storyline_filter=Sleep&storyline_page=2"
        )
        self.assertIn(b"Back to Client Storyline", detail.data)
        self.assertIn(b"filter=Sleep", detail.data)
        response = self.client.post(
            "/manager-review/sleep/1/review",
            data={
                "storyline_client_id": "1",
                "storyline_filter": "Sleep",
                "storyline_page": "2",
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("storyline_client_id=1", response.location)
        self.assertIn("storyline_filter=Sleep", response.location)
        self.assertIn("storyline_page=2", response.location)

    def test_invalid_storyline_context_does_not_render_back_link(self):
        self.add_sleep_event(1)
        self.login(2, "Admin")
        response = self.client.get(
            "/manager-review/sleep/1?storyline_client_id=2&"
            "storyline_filter=Sleep&storyline_page=1"
        )
        self.assertNotIn(b"Back to Client Storyline", response.data)

    def test_existing_storyline_position_mechanism_remains_compatible(self):
        self.add_sleep_event(1)
        activity_id = self.add_storyline_event("sleep_fell_asleep", 1)
        self.login(2, "Admin")
        response = self.client.get("/client/1/storyline")
        self.assertIn(
            f'id="storyline-event-{activity_id}"'.encode(),
            response.data
        )
        self.assertIn(b"client-storyline-detail-position", response.data)
        self.assertIn(b"storyline-detail-link", response.data)
        self.assertIn(b"requestAnimationFrame", response.data)

    def test_hidden_sleep_activity_remains_hidden(self):
        self.add_sleep_event(1, note=None)
        self.add_storyline_event(
            "sleep_fell_asleep", 1, visible=0
        )
        self.login(2, "Admin")
        response = self.client.get("/client/1/storyline?filter=Sleep")
        self.assertNotIn(b"Client fell asleep", response.data)
        self.assertNotIn(b"View details", response.data)


if __name__ == "__main__":
    unittest.main()
