import sqlite3
import tempfile
import unittest
from pathlib import Path

import add_incident_clearances_table as migration


class IncidentClearancesMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "incident-clearances.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT NOT NULL
            );
            CREATE TABLE incident_reports (
                incident_id INTEGER PRIMARY KEY
            );
            INSERT INTO users VALUES (1, 'Program Manager');
            INSERT INTO incident_reports VALUES (10);
        """)
        migration.migrate(self.conn)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_schema_and_migration_are_idempotent(self):
        migration.migrate(self.conn)
        self.conn.commit()

        columns = {
            row[1]
            for row in self.conn.execute(
                "PRAGMA table_info(incident_clearances)"
            )
        }
        self.assertEqual(
            columns,
            {
                "incident_clearance_id",
                "incident_id",
                "cleared_by_user_id",
                "cleared_at_utc",
                "clearance_note",
                "active",
            },
        )
        self.assertIn(
            migration.ACTIVE_UNIQUE_INDEX_NAME,
            {
                row[1]
                for row in self.conn.execute(
                    "PRAGMA index_list(incident_clearances)"
                )
            },
        )

    def test_only_one_active_clearance_per_incident_but_history_is_preserved(self):
        self.conn.execute("""
            INSERT INTO incident_clearances
                (incident_id, cleared_by_user_id, cleared_at_utc, active)
            VALUES (10, 1, '2026-08-02T18:00:00Z', 1)
        """)
        self.conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""
                INSERT INTO incident_clearances
                    (incident_id, cleared_by_user_id, cleared_at_utc, active)
                VALUES (10, 1, '2026-08-02T19:00:00Z', 1)
            """)
        self.conn.rollback()

        self.conn.execute(
            "UPDATE incident_clearances SET active = 0 WHERE incident_id = 10"
        )
        self.conn.execute("""
            INSERT INTO incident_clearances
                (incident_id, cleared_by_user_id, cleared_at_utc, active)
            VALUES (10, 1, '2026-08-02T19:00:00Z', 1)
        """)
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM incident_clearances "
                "WHERE incident_id = 10"
            ).fetchone()[0],
            2,
        )


if __name__ == "__main__":
    unittest.main()
