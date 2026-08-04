import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import app


class ClientStorylineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "storyline.db")
        self.old_db = app.DB_NAME
        app.DB_NAME = self.path
        app.app.config.update(TESTING=True)
        self.addCleanup(self.cleanup)
        conn = sqlite3.connect(self.path)
        conn.executescript("""
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, full_name TEXT, role TEXT, active INTEGER);
            CREATE TABLE clients (client_id INTEGER PRIMARY KEY, client_name TEXT, active INTEGER);
            CREATE TABLE shifts (shift_id INTEGER PRIMARY KEY, client_id INTEGER, status TEXT);
            CREATE TABLE shift_staff (shift_staff_id INTEGER PRIMARY KEY, shift_id INTEGER, user_id INTEGER, active INTEGER);
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT, activity_datetime TEXT,
                activity_class TEXT, activity_type TEXT, user_id INTEGER, client_id INTEGER,
                shift_id INTEGER, related_table TEXT, related_id INTEGER, summary TEXT,
                details TEXT, success INTEGER DEFAULT 1, storyline_visible INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE toileting_events (
                toileting_event_id INTEGER PRIMARY KEY, client_id INTEGER,
                location TEXT, bm_size TEXT, bm_consistency TEXT,
                behaviour_before TEXT, behaviour_during TEXT,
                behaviour_after TEXT, behaviour_comments TEXT
            );
            INSERT INTO users VALUES (1, 'Worker', 'Support Worker', 1), (2, 'Manager', 'Program Manager', 1), (3, 'Other', 'Support Worker', 1);
            INSERT INTO clients VALUES (1, 'Client One', 1), (2, 'Client Two', 1);
            INSERT INTO shifts VALUES (10, 1, 'Open'), (20, 2, 'Open');
            INSERT INTO shift_staff VALUES (100, 10, 1, 1), (200, 20, 3, 1), (300, 10, 3, 0);
        """)
        conn.commit()
        conn.close()
        self.client = app.app.test_client()

    def cleanup(self):
        app.DB_NAME = self.old_db
        self.temp.cleanup()

    def login(self, user_id=1, role="Support Worker"):
        with self.client.session_transaction() as session:
            session.update(user_id=user_id, role=role, full_name="Test User")

    def add_event(self, event_type, summary, client_id=1, visible=1, success=1, when="2026-08-02 10:00:00", user_id=1, details=None):
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_type, user_id, client_id, summary, details, success, storyline_visible)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (when, event_type, user_id, client_id, summary, details, success, visible))
        conn.commit()
        conn.close()

    def test_authorized_worker_sees_only_visible_events_for_client(self):
        self.login()
        self.add_event("sleep_fell_asleep", "Client fell asleep")
        self.add_event("user_login", "Login", visible=0)
        self.add_event("incident_created", "Other client", client_id=2)
        response = self.client.get("/client/1/storyline")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Client fell asleep", response.data)
        self.assertNotIn(b"Login", response.data)
        self.assertNotIn(b"Other client", response.data)

    def test_worker_cannot_view_unrelated_client(self):
        self.login()
        self.assertEqual(self.client.get("/client/2/storyline").status_code, 403)

    def test_unauthenticated_and_unassigned_workers_are_rejected(self):
        self.assertEqual(self.client.get("/client/1/storyline").status_code, 302)
        self.login(3)
        self.assertEqual(self.client.get("/client/1/storyline").status_code, 403)

    def test_active_assignment_is_sufficient_before_sign_on_and_inactive_is_denied(self):
        self.login(1)
        self.assertEqual(self.client.get("/client/1/storyline").status_code, 200)
        self.login(3)
        self.assertEqual(self.client.get("/client/1/storyline").status_code, 403)

    def test_labels_filters_unknown_events_and_date_heading(self):
        self.login()
        today = datetime.now().date()
        self.add_event("sleep_woke_up", "Client woke up", when=f"{today} 10:00:00")
        self.add_event("care_task_completed", "Bath - Completed", when=f"{today} 10:01:00")
        self.add_event("unknown_visible", "Unknown summary", when=f"{today - timedelta(days=2)} 10:02:00")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Sleep", page)
        self.assertIn(b"Care", page)
        self.assertIn(b"Client activity", page)
        self.assertIn(b"Today", page)
        filtered = self.client.get("/client/1/storyline?filter=Sleep").data
        self.assertIn(b"Client woke up", filtered)
        self.assertNotIn(b"Bath - Completed", filtered)

    def test_all_labels_and_success_filtering(self):
        self.login()
        events = (
            ("food_fluid_entry_created", "Food record"),
            ("behaviour_occurrence_created", "Behaviour record"),
            ("toileting_event_created", "Toileting record"),
            ("shift_activity_created", "Activity record"),
            ("incident_created", "Incident record"),
            ("shift_note_updated", "Note record"),
            ("start_shift_completed", "Start record"),
            ("end_shift_completed", "End record"),
            ("care_task_completed", "Bath - Completed"),
            ("housekeeping_task_completed", "Housekeeping record"),
        )
        for event_type, summary in events:
            self.add_event(event_type, summary)
        self.add_event("incident_created", "Failed", success=0)
        page = self.client.get("/client/1/storyline").data
        for _, summary in events:
            self.assertIn(summary.encode(), page)
        self.assertNotIn(b"Failed", page)
        for category, included, excluded in (
            ("Food & Fluid", b"Food record", b"Behaviour record"),
            ("Behaviour", b"Behaviour record", b"Food record"),
            ("Toileting", b"Toileting record", b"Food record"),
            ("Activity", b"Activity record", b"Food record"),
            ("Incident", b"Incident record", b"Food record"),
            ("Shift Notes", b"Note record", b"Food record"),
            ("Shift", b"Start record", b"Food record"),
            ("Care", b"Bath - Completed", b"Food record"),
            ("Housekeeping", b"Housekeeping record", b"Food record"),
        ):
            filtered = self.client.get(
                "/client/1/storyline", query_string={"filter": category}
            ).data
            self.assertIn(included, filtered)
            self.assertNotIn(excluded, filtered)

    def test_invalid_filter_and_pagination_are_safe(self):
        self.login()
        for index in range(30):
            self.add_event("shift_activity_created", f"Activity {index}", when=f"2026-08-02 10:{index % 60:02d}:00")
        response = self.client.get("/client/1/storyline?filter=invalid&page=2")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Newer Events", response.data)
        older = self.client.get("/client/1/storyline?filter=Activity&page=2").data
        self.assertIn(b"Newer Events", older)
        self.assertNotIn(b"Client activity", older)

    def test_pagination_is_limited_and_links_preserve_filter(self):
        self.login()
        for index in range(26):
            self.add_event("shift_activity_created", f"Paged {index}", when=f"2026-08-02 09:{index:02d}:00")
        first = self.client.get("/client/1/storyline?filter=Activity&page=1").data
        self.assertIn(b"Older Events", first)
        self.assertIn(b"filter=Activity", first)
        self.assertLess(first.find(b"Paged 25"), first.find(b"Paged 1"))
        second = self.client.get("/client/1/storyline?filter=Activity&page=2").data
        self.assertIn(b"Newer Events", second)
        self.assertIn(b"Paged 0", second)

    def test_empty_and_filtered_empty_states_render(self):
        self.login()
        empty = self.client.get("/client/1/storyline").data
        self.assertIn(b"No Storyline events have been recorded", empty)
        self.add_event("sleep_fell_asleep", "Sleep event")
        filtered_empty = self.client.get("/client/1/storyline?filter=Incident").data
        self.assertIn(b"No Incident events match this filter", filtered_empty)

    def test_food_fluid_details_are_shown_but_other_details_are_hidden_and_escaped(self):
        self.login()
        self.add_event(
            "food_fluid_entry_created", "Offered <Toast>",
            details="Outcome: All consumed\nAdditional details: <b>Observed</b>"
        )
        self.add_event(
            "user_login", "Administrative summary",
            details="Internal behaviour detail"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Outcome: All consumed", page)
        self.assertIn(b"&lt;b&gt;Observed&lt;/b&gt;", page)
        self.assertNotIn(b"Internal behaviour detail", page)

    def test_behaviour_details_render_and_void_details_are_safe(self):
        self.login()
        created = (
            "Categories:\nAggression towards others\nSelf-Harm\n\n"
            "Notes:\nObserved <script>\nSecond line\n" + ("Long note " * 80)
        )
        self.add_event("behaviour_occurrence_created", "Behaviour occurrence recorded", details=created)
        self.add_event("behaviour_occurrence_voided", "Behaviour occurrence voided", details="Status: Voided")
        self.add_event("behaviour_occurrence_created", "Legacy Behaviour", details="")
        self.add_event("behaviour_occurrence_created", "Hidden Behaviour", details="hidden", visible=0)
        self.add_event("behaviour_occurrence_voided", "Failed Behaviour", details="Status: Voided", success=0)
        self.add_event("behaviour_occurrence_created", "Other Client Behaviour", details="other", client_id=2)
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Categories:", page)
        self.assertIn(b"Aggression towards others", page)
        self.assertIn(b"Self-Harm", page)
        self.assertIn(b"&lt;script&gt;", page)
        self.assertIn(b"Second line", page)
        self.assertIn(b"Status: Voided", page)
        self.assertIn(b"Legacy Behaviour", page)
        self.assertNotIn(b"Hidden Behaviour", page)
        self.assertNotIn(b"Failed Behaviour", page)
        self.assertNotIn(b"Other Client Behaviour", page)
        self.assertNotIn(b"Void reason", page)

    def test_abc_behaviour_details_use_nested_presentation_roles(self):
        self.login()
        details = (
            "Before the Behaviour (A):\n"
            "Asked to transition between activities\n"
            "Other\nOther: Was pacing\n\n"
            "Behaviour Observed (B):\nPhysical aggression\n\n"
            "Staff Response (C):\nBlocked behaviour\n\n"
            "Outcome:\nDuration until calm: 1 minute\n"
            "How the client calmed down:\nClient stopped pacing\nAnd smiled\n"
            "Additional notes:\n<test>"
        )
        self.add_event(
            "behaviour_occurrence_created", "Behaviour occurrence recorded",
            details=details
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"storyline-behaviour-section-heading", page)
        self.assertIn(b"storyline-behaviour-section-item", page)
        self.assertIn(b"storyline-behaviour-nested-detail", page)
        self.assertIn(b"storyline-behaviour-outcome-item", page)
        self.assertIn(b"storyline-behaviour-outcome-label", page)
        self.assertIn(b"storyline-behaviour-nested-text", page)
        self.assertIn(b"And smiled", page)
        self.assertIn(b"&lt;test&gt;", page)
        self.assertIn(b"storyline-divider", page)

    def test_incident_details_render_from_activity_log_only_and_escape_values(self):
        self.login()
        details = (
            "Location: Home\nSeverity: High\nInjury: Yes\n"
            "Injury details: <arm>\nActions taken: " + ("Call " * 80) +
            "\nDescription: <script>incident</script>\n"
            "Follow-up required: No\nPolice notified: Yes\nMedical treatment: No"
        )
        self.add_event("incident_created", "Incident created: Fall", details=details)
        self.add_event("incident_created", "Old generic incident", details="Event UTC: old")
        self.add_event("incident_created", "Hidden incident", details="hidden", visible=0)
        self.add_event("incident_created", "Failed incident", details="failed", success=0)
        self.add_event("incident_created", "Other client incident", details="other", client_id=2)
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Location: Home", page)
        self.assertIn(b"&lt;arm&gt;", page)
        self.assertIn(b"&lt;script&gt;incident&lt;/script&gt;", page)
        self.assertIn(b"Event UTC: old", page)
        self.assertNotIn(b"Hidden incident", page)
        self.assertNotIn(b"Failed incident", page)
        self.assertNotIn(b"Other client incident", page)

    def test_shift_note_details_render_from_activity_log_with_safe_history(self):
        self.login()
        first_note = "First line\nSecond <line>\n" + ("Long text " * 80)
        second_note = "Replacement note"
        self.add_event("shift_note_updated", "Updated staff notes for shift", details=first_note)
        self.add_event("shift_note_updated", "Updated staff notes for shift", details=second_note)
        self.add_event(
            "shift_note_updated", "Generic note", details="",
            visible=1
        )
        self.add_event(
            "shift_note_updated", "Hidden note", details="hidden",
            visible=0
        )
        self.add_event(
            "shift_note_updated", "Failed note", details="failed",
            success=0
        )
        self.add_event(
            "shift_note_updated", "Other client note", details="other",
            client_id=2
        )
        self.add_event(
            "shift_note_updated", "Note with follow-up", details="Worker note content"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"First line", page)
        self.assertIn(b"Second &lt;line&gt;", page)
        self.assertIn(b"Long text", page)
        self.assertIn(b"Replacement note", page)
        self.assertIn(b"Generic note", page)
        self.assertIn(b"Note with follow-up", page)
        self.assertNotIn(b"Hidden note", page)
        self.assertNotIn(b"Failed note", page)
        self.assertNotIn(b"Other client note", page)
        self.assertNotIn(b"Follow-up required", page)

    def test_storyline_time_omits_seconds_and_username_but_keeps_user_linkage(self):
        self.login()
        self.add_event(
            "food_fluid_entry_created", "Offered - Juice",
            when="2026-08-02 15:44:30", user_id=1
        )
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE users SET full_name = 'ActorUsername' WHERE user_id = 1")
        conn.commit()
        conn.close()
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"15:44", page)
        self.assertNotIn(b"15:44:30", page)
        self.assertNotIn(b"ActorUsername", page)
        conn = sqlite3.connect(self.path)
        self.assertEqual(
            conn.execute("SELECT user_id FROM activity_log").fetchone()[0], 1
        )
        conn.close()

    def test_food_fluid_details_render_on_separate_lines_and_blank_details_are_omitted(self):
        self.login()
        self.add_event(
            "food_fluid_entry_created", "Offered - Juice",
            details="Outcome: Partially consumed\nAdditional details: Consumed about 80%"
        )
        self.add_event(
            "food_fluid_entry_created", "Drink - Water",
            details="Outcome: Fully consumed"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Outcome: Partially consumed</div>", page)
        self.assertIn(b"Additional details: Consumed about 80%</div>", page)
        self.assertIn(b"Outcome: Fully consumed</div>", page)
        self.assertNotIn(b"Additional details:</div>", page)

    def test_old_generic_food_fluid_details_render_without_raw_technical_data(self):
        self.login()
        self.add_event(
            "food_fluid_entry_created", "Food & Fluid entry recorded",
            details="Event UTC: 2024-01-01T10:00:00Z; Outcome: All consumed"
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Food &amp; Fluid entry recorded", page)
        self.assertNotIn(b"Event UTC", page)

    def test_activity_summary_categories_and_escaped_long_description_render_safely(self):
        self.login()
        long_description = "Walk around neighbourhood " + ("x" * 300)
        self.add_event(
            "shift_activity_created", long_description,
            details="A, T, LS"
        )
        self.add_event(
            "shift_activity_created", "Trampoline",
            details="A"
        )
        self.add_event(
            "shift_activity_created", "<script>alert(1)</script>",
            details="A, T"
        )
        self.add_event(
            "shift_activity_created", "Blank details", details=""
        )
        self.add_event(
            "user_login", "Hidden raw details", details="Private detail"
        )
        self.add_event(
            "shift_activity_created", "Failed Activity", details="LS", success=0
        )
        page = self.client.get("/client/1/storyline").data
        self.assertIn(long_description.encode(), page)
        self.assertIn(b"A, T, LS", page)
        self.assertIn(b"Trampoline", page)
        self.assertIn(b"A, T", page)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn(b"<script>alert(1)</script>", page)
        self.assertNotIn(b"Private detail", page)
        self.assertNotIn(b"Failed Activity", page)
        self.assertEqual(page.count(b"storyline-details"), 3)

    def test_storyline_events_have_compact_wrappers_and_dividers(self):
        self.login()
        self.add_event("shift_activity_created", "Trampoline", details="A, T")
        self.add_event("food_fluid_entry_created", "Offered - Juice", details="Outcome: Consumed")
        self.add_event("sleep_fell_asleep", "Fell asleep")
        page = self.client.get("/client/1/storyline").data
        self.assertEqual(page.count(b'class="storyline-event"'), 3)
        self.assertEqual(page.count(b"storyline-divider"), 3)
        self.assertIn(b"Trampoline", page)
        self.assertIn(b"A, T", page)
        self.assertIn(b"Outcome: Consumed", page)
        self.assertIn(b"Fell asleep", page)

    def test_activity_details_use_compact_spacing_and_keep_divider(self):
        self.login()
        self.add_event("shift_activity_created", "Trampoline", details="A, T")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"storyline-activity-details", page)
        self.assertIn(b"margin-top: 4px; margin-bottom: 0", page)
        self.assertIn(b"class=\"storyline-event\" style=\"padding: 0.25rem 0 0;\"", page)
        self.assertIn(b"A, T", page)
        self.assertIn(b"storyline-activity-divider\" style=\"border: 0; border-top: 1px solid #d9d9d9; margin: 0.15rem 0 0.35rem;", page)
        self.assertNotIn(b"A, T</div>\n                        </div>", page)

    def test_non_activity_divider_spacing_remains_unchanged(self):
        self.login()
        self.add_event("sleep_fell_asleep", "Fell asleep")
        page = self.client.get("/client/1/storyline").data
        self.assertIn(
            b"class=\"storyline-divider\" style=\"border: 0; border-top: 1px solid #d9d9d9; margin: 0.35rem 0 0.35rem;",
            page
        )

    def test_toileting_details_show_nonblank_fields_and_escape_values(self):
        self.login()
        conn = sqlite3.connect(self.path)
        conn.execute("""
            INSERT INTO toileting_events
            (toileting_event_id, client_id, location, bm_size, bm_consistency,
             behaviour_before, behaviour_during, behaviour_after, behaviour_comments)
            VALUES (7, 1, 'Bathroom', 'Large', 'Soft',
                    '<Observed>', '', 'Calm', '')
        """)
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_type, user_id, client_id, related_id,
             summary, details, success, storyline_visible)
            VALUES ('2026-08-03 10:00:00', 'toileting_event_created', 1, 1, 7,
                    'Toileting event recorded: BM',
                    'Location: Bathroom\nSize: Large\nConsistency: Soft\nBehaviour: Before: <Observed>; After: Calm',
                    1, 1)
        """)
        conn.commit()
        conn.close()
        page = self.client.get("/client/1/storyline").data
        self.assertIn(b"Location: Bathroom", page)
        self.assertIn(b"Size: Large", page)
        self.assertIn(b"Consistency: Soft", page)
        self.assertIn(b"Behaviour: Before: &lt;Observed&gt;; After: Calm", page)
        self.assertNotIn(b"During:", page)
        self.assertNotIn(b"Comments:", page)

    def test_manager_can_view_and_storyline_view_does_not_write(self):
        self.login(2, "Program Manager")
        before = sqlite3.connect(self.path).execute("SELECT count(*) FROM activity_log").fetchone()[0]
        self.assertEqual(self.client.get("/client/2/storyline").status_code, 200)
        after = sqlite3.connect(self.path).execute("SELECT count(*) FROM activity_log").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
