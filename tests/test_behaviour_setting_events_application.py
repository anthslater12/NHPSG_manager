import sqlite3
import sys
import unittest


if "C:\\NHPSG_Manager" not in sys.path:
    sys.path.insert(0, "C:\\NHPSG_Manager")

import add_behaviour_occurrences_table as behaviour_migration
import add_behaviour_setting_events_tables as setting_event_migration
import app


class BehaviourSettingEventsApplicationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE clients (
                client_id INTEGER PRIMARY KEY,
                client_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO users VALUES (1, 'Support Worker', 1);
            INSERT INTO clients VALUES (1, 'Client', 1);
        """)
        behaviour_migration.migrate(self.conn)
        setting_event_migration.migrate(self.conn)

    def tearDown(self):
        self.conn.close()

    def option_id(self, code):
        return self.conn.execute(
            "SELECT setting_event_option_id FROM "
            "behaviour_setting_event_options WHERE code = ?",
            (code,),
        ).fetchone()[0]

    def insert_occurrence(self, token="application-setting-events"):
        return self.conn.execute("""
            INSERT INTO behaviour_occurrences (
                client_id, occurred_at_utc, aggression_towards_others,
                recorded_by_user_id, recorded_at_utc, submission_token, status
            ) VALUES (1, '2026-01-02T20:00:00Z', 1, 1,
                      '2026-01-02T20:01:00Z', ?, 'In Progress')
        """, (token,)).lastrowid

    def validate(self, codes=(), other=None, finalized=False, allowed=()):
        return app.validate_behaviour_setting_events(
            self.conn,
            [str(self.option_id(code)) for code in codes],
            other,
            finalized=finalized,
            allowed_inactive_option_ids=allowed,
        )

    def test_draft_allows_zero_and_finalized_requires_one_selection(self):
        draft = self.validate()
        self.assertEqual(draft["selections"], ())
        with self.assertRaisesRegex(ValueError, "At least one"):
            self.validate(finalized=True)

    def test_normal_options_and_special_options_validate(self):
        one = self.validate(("HUNGER",), finalized=True)
        self.assertEqual(one["selections"], ({
            "setting_event_option_id": self.option_id("HUNGER"),
            "other_text": None,
        },))
        multiple = self.validate(("HUNGER", "THIRST"), finalized=True)
        self.assertEqual(
            [item["setting_event_option_id"] for item in multiple["selections"]],
            [self.option_id("HUNGER"), self.option_id("THIRST")],
        )
        self.assertEqual(
            self.validate(("NONE_OBSERVED",), finalized=True)["option_ids"],
            (self.option_id("NONE_OBSERVED"),),
        )
        self.assertEqual(
            self.validate(("INFORMATION_UNKNOWN",), finalized=True)["option_ids"],
            (self.option_id("INFORMATION_UNKNOWN"),),
        )

    def test_special_options_are_mutually_exclusive(self):
        for codes in (
            ("NONE_OBSERVED", "HUNGER"),
            ("INFORMATION_UNKNOWN", "HUNGER"),
            ("NONE_OBSERVED", "INFORMATION_UNKNOWN"),
        ):
            with self.subTest(codes=codes):
                with self.assertRaisesRegex(ValueError, "cannot be combined"):
                    self.validate(codes, finalized=True)

    def test_option_ids_are_strict_bounded_and_database_backed(self):
        with self.assertRaisesRegex(ValueError, "option ID"):
            app.validate_behaviour_setting_events(self.conn, ["not-an-id"])
        with self.assertRaisesRegex(ValueError, "does not exist"):
            app.validate_behaviour_setting_events(self.conn, ["999999"])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.validate(("HUNGER", "HUNGER"))

        inactive_id = self.option_id("THIRST")
        self.conn.execute(
            "UPDATE behaviour_setting_event_options SET active = 0 "
            "WHERE setting_event_option_id = ?",
            (inactive_id,),
        )
        with self.assertRaisesRegex(ValueError, "Inactive"):
            app.validate_behaviour_setting_events(self.conn, [str(inactive_id)])

    def test_other_text_is_required_trimmed_and_bounded(self):
        code = "OTHER_HEALTH_PHYSICAL"
        field = app.setting_event_other_field_name(code)
        result = self.validate(
            (code,),
            {field: ["  health detail  "]},
            finalized=True,
        )
        self.assertEqual(result["selections"][0]["other_text"], "health detail")

        with self.assertRaisesRegex(ValueError, "required"):
            self.validate((code,), finalized=True)
        with self.assertRaisesRegex(ValueError, "required"):
            self.validate((code,), {field: ["   "]}, finalized=True)
        with self.assertRaisesRegex(ValueError, "1,000"):
            self.validate((code,), {field: ["x" * 1001]}, finalized=True)
        with self.assertRaisesRegex(ValueError, "matching"):
            self.validate((), {field: ["orphan text"]})

    def test_normal_none_and_unknown_options_never_receive_other_text(self):
        normal_field = app.setting_event_other_field_name("HUNGER")
        with self.assertRaisesRegex(ValueError, "only allowed"):
            self.validate(("HUNGER",), {normal_field: ["wrong field"]})
        self.assertEqual(
            self.validate(("HUNGER",))["selections"][0]["other_text"],
            None,
        )

    def test_persistence_and_replacement_are_transactional(self):
        occurrence_id = self.insert_occurrence()
        self.conn.commit()
        first = self.validate(("HUNGER", "THIRST"), finalized=False)
        self.conn.execute("BEGIN")
        app.replace_behaviour_setting_event_selections(
            self.conn, occurrence_id, first["selections"]
        )
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM behaviour_occurrence_setting_events "
                "WHERE behaviour_occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()[0],
            2,
        )

        replacement = self.validate(("NONE_OBSERVED",), finalized=False)
        self.conn.execute("BEGIN")
        app.replace_behaviour_setting_event_selections(
            self.conn, occurrence_id, replacement["selections"]
        )
        self.conn.rollback()
        self.assertEqual(
            [row[0] for row in self.conn.execute(
                "SELECT setting_event_option_id "
                "FROM behaviour_occurrence_setting_events "
                "WHERE behaviour_occurrence_id = ? "
                "ORDER BY setting_event_option_id",
                (occurrence_id,),
            )],
            sorted((self.option_id("HUNGER"), self.option_id("THIRST"))),
        )

    def test_other_text_and_special_options_persist(self):
        occurrence_id = self.insert_occurrence("application-persistence")
        self.conn.commit()
        other_code = "OTHER_HEALTH_PHYSICAL"
        other = self.validate(
            (other_code,),
            {app.setting_event_other_field_name(other_code): ["  detail  "]},
        )
        app.replace_behaviour_setting_event_selections(
            self.conn, occurrence_id, other["selections"]
        )
        self.conn.commit()
        stored = self.conn.execute(
            "SELECT setting_event_option_id, other_text "
            "FROM behaviour_occurrence_setting_events "
            "WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        self.assertEqual(
            tuple(stored), (self.option_id(other_code), "detail")
        )

        none = self.validate(("NONE_OBSERVED",))
        app.replace_behaviour_setting_event_selections(
            self.conn, occurrence_id, none["selections"]
        )
        self.conn.commit()
        stored = self.conn.execute(
            "SELECT setting_event_option_id, other_text "
            "FROM behaviour_occurrence_setting_events "
            "WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        self.assertEqual(
            tuple(stored), (self.option_id("NONE_OBSERVED"), None)
        )

        unknown = self.validate(("INFORMATION_UNKNOWN",))
        app.replace_behaviour_setting_event_selections(
            self.conn, occurrence_id, unknown["selections"]
        )
        self.conn.commit()
        stored = self.conn.execute(
            "SELECT setting_event_option_id, other_text "
            "FROM behaviour_occurrence_setting_events "
            "WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        ).fetchone()
        self.assertEqual(
            tuple(stored), (self.option_id("INFORMATION_UNKNOWN"), None)
        )

    def test_existing_selections_load_for_edit_including_inactive_options(self):
        occurrence_id = self.insert_occurrence()
        selected = self.validate(("THIRST",), finalized=False)
        self.conn.execute("""
            INSERT INTO behaviour_occurrence_setting_events
                (behaviour_occurrence_id, setting_event_option_id)
            VALUES (?, ?)
        """, (occurrence_id, selected["selections"][0]["setting_event_option_id"]))
        self.conn.execute(
            "UPDATE behaviour_setting_event_options SET active = 0 "
            "WHERE code = 'THIRST'"
        )
        rows = app.load_behaviour_setting_event_selections(
            self.conn, occurrence_id
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "THIRST")
        self.assertEqual(rows[0]["active"], 0)

        retained = app.validate_behaviour_setting_events(
            self.conn,
            [str(self.option_id("THIRST"))],
            allowed_inactive_option_ids={self.option_id("THIRST")},
        )
        self.assertEqual(len(retained["selections"]), 1)

    def test_historical_occurrence_without_selections_remains_valid(self):
        occurrence_id = self.insert_occurrence()
        self.conn.execute(
            "UPDATE behaviour_occurrences SET status = 'Recorded' "
            "WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM behaviour_occurrence_setting_events "
                "WHERE behaviour_occurrence_id = ?",
                (occurrence_id,),
            ).fetchone()[0],
            0,
        )
        self.assertIsNotNone(self.conn.execute(
            "SELECT 1 FROM behaviour_occurrences WHERE behaviour_occurrence_id = ?",
            (occurrence_id,),
        ).fetchone())


if __name__ == "__main__":
    unittest.main()
