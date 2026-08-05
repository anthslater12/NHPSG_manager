import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class ToiletingReviewDatetimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "toileting-review.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            "CREATE TABLE toileting_events (event_datetime TEXT)"
        )
        self.conn.execute(
            "INSERT INTO toileting_events VALUES (?)",
            ("2026-08-04T15:49",),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def render(self, template_name, entry):
        with app.app.test_request_context(
            "/manager-review/toileting/7"
        ):
            return app.render_template(
                template_name,
                entry=entry,
                entries=[entry],
                reviews_by_entry={},
                reviewed_by_current_user=set(),
                review_history=[],
                current_user_reviewed=True,
                management_notes=[],
                linked_actions=[],
                shift_staff=[],
                active_users=[],
                error=None,
                title="Action",
                description="Description",
                priority="Medium",
                assigned_to_user_id=None,
            )

    def entry(self, event_datetime):
        values = {
            "entry_id": 7,
            "toileting_event_id": 7,
            "event_datetime": event_datetime,
            "event_local_display": app.format_toileting_local_datetime_display(
                event_datetime
            ),
            "event_type": "BM",
            "location": "Bathroom <A>",
            "client_name": "Client",
            "recorded_by": "Worker",
            "shift_id": 3,
            "shift_date": "2026-08-04",
            "shift_type": "Day",
            "bm_size": "Medium",
            "bm_consistency": "Formed",
            "bm_unusual_details": None,
            "urine_volume": None,
            "urine_unusual_details": None,
            "behaviour_before": None,
            "behaviour_during": None,
            "behaviour_after": None,
            "behaviour_comments": None,
            "general_comments": None,
        }
        return values

    def test_local_display_is_human_readable_and_storage_is_unchanged(self):
        raw = self.conn.execute(
            "SELECT event_datetime FROM toileting_events"
        ).fetchone()[0]
        self.assertEqual(
            app.format_toileting_local_datetime_display(raw),
            "2026-08-04 15:49",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT event_datetime FROM toileting_events"
            ).fetchone()[0],
            raw,
        )

    def test_list_detail_and_action_use_formatted_value(self):
        entry = self.entry("2026-08-04T15:49")
        for template in (
            "toileting_review_list.html",
            "toileting_review_detail.html",
            "toileting_action_new.html",
        ):
            page = self.render(template, entry)
            self.assertIn("2026-08-04 15:49", page)
            self.assertNotIn("2026-08-04T15:49", page)

    def test_blank_and_malformed_values_fail_safe_without_raw_iso(self):
        for value in ("", None, "not-a-date"):
            self.assertEqual(
                app.format_toileting_local_datetime_display(value),
                "Date/time unavailable",
            )
        page = self.render(
            "toileting_review_detail.html", self.entry("not-a-date")
        )
        self.assertIn("Date/time unavailable", page)
        self.assertNotIn("not-a-date", page)

    def test_template_escapes_other_record_values(self):
        page = self.render(
            "toileting_review_detail.html", self.entry("2026-08-04T15:49")
        )
        self.assertIn("Bathroom &lt;A&gt;", page)
        self.assertNotIn("Bathroom <A>", page)


if __name__ == "__main__":
    unittest.main()
