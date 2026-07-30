import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class SharedShiftNotesTests(unittest.TestCase):

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = str(
            Path(self.temporary_directory.name) / "shared_notes.db"
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
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    role TEXT NOT NULL,
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

                CREATE TABLE shift_notes (
                    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    shift_date TEXT NOT NULL,
                    shift_type TEXT NOT NULL,
                    note_text TEXT NOT NULL,
                    follow_up_required INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            """)
            conn.executemany("""
                INSERT INTO users
                    (user_id, full_name, role, active)
                VALUES (?, ?, ?, 1)
            """, (
                (1, "Worker One", "Support Worker"),
                (2, "Worker Two", "Support Worker"),
                (3, "Unassigned Worker", "Support Worker"),
                (4, "Signed Off Worker", "Support Worker"),
                (5, "Admin User", "Admin"),
                (6, "Manager User", "Program Manager"),
                (7, "Director User", "Director")
            ))
            conn.executemany("""
                INSERT INTO shifts
                    (shift_id, client_id, shift_date, shift_type, status)
                VALUES (?, 1, ?, ?, ?)
            """, (
                (1, "2026-08-03", "Day", "Open"),
                (2, "2026-08-04", "Day", "Closed"),
                (3, "2026-08-05", "Day", "Cancelled")
            ))
            conn.executemany("""
                INSERT INTO shift_staff
                    (shift_staff_id, shift_id, user_id, active)
                VALUES (?, ?, ?, ?)
            """, (
                (1, 1, 1, 1),
                (2, 1, 2, 1),
                (3, 1, 4, 0),
                (4, 2, 1, 1),
                (5, 3, 1, 1)
            ))
            conn.commit()
        finally:
            conn.close()

    def login(self, user_id, role="Support Worker"):
        with self.client.session_transaction() as session_data:
            session_data["user_id"] = user_id
            session_data["role"] = role

    def post_note(self, shift_id, text):
        return self.client.post(
            f"/shift/{shift_id}/note",
            data={"note_text": text}
        )

    def database_rows(self, sql, parameters=()):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            return [
                dict(row)
                for row in conn.execute(sql, parameters).fetchall()
            ]
        finally:
            conn.close()

    def insert_note(self, shift_id, user_id, text):
        conn = sqlite3.connect(self.database_path)
        try:
            shift = conn.execute("""
                SELECT client_id, shift_date, shift_type
                FROM shifts
                WHERE shift_id = ?
            """, (shift_id,)).fetchone()
            conn.execute("""
                INSERT INTO shift_notes
                    (client_id, user_id, shift_date, shift_type, note_text)
                VALUES (?, ?, ?, ?, ?)
            """, (
                shift[0],
                user_id,
                shift[1],
                shift[2],
                text
            ))
            conn.commit()
        finally:
            conn.close()

    def test_active_workers_can_edit_resave_and_share_one_note(self):
        self.login(1)
        first = self.post_note(1, "First shared update")
        second = self.post_note(1, "Worker one revised it")

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        notes = self.database_rows(
            "SELECT user_id, note_text FROM shift_notes"
        )
        self.assertEqual(notes, [{
            "user_id": 1,
            "note_text": "Worker one revised it"
        }])

        self.login(2)
        visible = self.client.get("/shift/1/note")
        self.assertEqual(visible.status_code, 200)
        self.assertIn(b"Worker one revised it", visible.data)

        updated = self.post_note(1, "Worker two updated the shared note")
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(
            self.database_rows(
                "SELECT user_id, note_text FROM shift_notes"
            ),
            [{
                "user_id": 2,
                "note_text": "Worker two updated the shared note"
            }]
        )

        logs = self.database_rows("""
            SELECT activity_type, summary, user_id, shift_id, success
            FROM activity_log
            ORDER BY activity_id
        """)
        self.assertEqual(len(logs), 3)
        self.assertTrue(all(
            row["activity_type"] == "shift_note_updated"
            and row["summary"] == "Updated staff notes for shift"
            and row["shift_id"] == 1
            and row["success"] == 1
            for row in logs
        ))

    def test_unassigned_and_inactive_workers_are_denied(self):
        for user_id in (3, 4):
            with self.subTest(user_id=user_id):
                self.login(user_id)
                view = self.client.get("/shift/1/note")
                self.assertEqual(view.status_code, 200)
                self.assertNotIn(b"<textarea", view.data)
                self.assertEqual(
                    self.post_note(1, "Unauthorized update").status_code,
                    403
                )

        self.assertEqual(
            self.database_rows("SELECT * FROM shift_notes"),
            []
        )
        self.assertEqual(
            self.database_rows("SELECT * FROM activity_log"),
            []
        )

    def test_closed_and_cancelled_shifts_are_read_only(self):
        for shift_id in (2, 3):
            with self.subTest(shift_id=shift_id):
                note_text = f"Historical note for shift {shift_id}"
                self.insert_note(shift_id, 1, note_text)
                self.login(1)
                response = self.client.get(f"/shift/{shift_id}/note")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"<textarea", response.data)
                self.assertIn(note_text.encode(), response.data)
                self.assertEqual(
                    self.post_note(
                        shift_id,
                        "Not allowed after closure"
                    ).status_code,
                    403
                )

    def test_management_roles_can_edit_open_shift_without_assignment(self):
        for user_id, role in (
            (5, "Admin"),
            (6, "Program Manager"),
            (7, "Director")
        ):
            with self.subTest(role=role):
                self.login(user_id, role)
                response = self.post_note(1, f"Updated by {role}")
                self.assertEqual(response.status_code, 302)

        self.assertEqual(
            self.database_rows(
                "SELECT user_id, note_text FROM shift_notes"
            ),
            [{
                "user_id": 7,
                "note_text": "Updated by Director"
            }]
        )


if __name__ == "__main__":
    unittest.main()
