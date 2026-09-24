import re
import sqlite3
import unittest
from werkzeug.datastructures import MultiDict

import app
from tests.test_behaviour_lifecycle_phase_2 import (
    BehaviourLifecyclePhaseTwoTests,
)


class BehaviourSettingEventsUITests(unittest.TestCase):
    """Focused form and management-review coverage for Setting Events."""

    def setUp(self):
        self.fixture = BehaviourLifecyclePhaseTwoTests("runTest")
        self.fixture.setUp()
        self.client = self.fixture.client

    def tearDown(self):
        self.fixture.tearDown()

    @property
    def path(self):
        return self.fixture.path

    def login(self, user_id=1, shift_id=10):
        self.fixture.login(user_id, shift_id)

    def option_id(self, code):
        conn = sqlite3.connect(self.path)
        value = conn.execute(
            "SELECT setting_event_option_id FROM "
            "behaviour_setting_event_options WHERE code = ?",
            (code,),
        ).fetchone()[0]
        conn.close()
        return value

    def option_count(self, active_only=True):
        conn = sqlite3.connect(self.path)
        query = "SELECT COUNT(*) FROM behaviour_setting_event_options"
        if active_only:
            query += " WHERE active = 1"
        value = conn.execute(query).fetchone()[0]
        conn.close()
        return value

    def row(self, occurrence_id=None):
        return self.fixture.row(occurrence_id)

    def create_direct_abc(self, codes, other_text=None, token="UI" * 22):
        self.login()
        payload = self.fixture.abc_payload(
            token=token[:43], client_id="1"
        )
        form = MultiDict()
        for key, value in payload.items():
            if key != "setting_event_option_ids":
                form.add(key, value)
        form.add("setting_events_present", "1")
        for code in codes:
            form.add("setting_event_option_ids", str(self.option_id(code)))
        if other_text is not None:
            code = next(code for code in codes if code.startswith("OTHER_"))
            form.add(app.setting_event_other_field_name(code), other_text)
        response = self.client.post("/behaviour/record", data=form)
        self.assertEqual(response.status_code, 302)
        return self.row()

    def review_page(self, occurrence_id):
        self.fixture.login(3, shift_id=None)
        return self.client.get(
            f"/manager-review/behaviour/{occurrence_id}"
        )

    def test_abc_form_renders_marker_options_categories_special_and_other_fields(self):
        self.login()
        response = self.client.get("/shift/10/behaviour")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="setting-events-section"', html)
        self.assertIn('name="setting_events_present" value="1"', html)
        self.assertEqual(
            html.count('name="setting_event_option_ids"'),
            self.option_count(),
        )
        self.assertLess(
            html.index("Setting Events"),
            html.index("Before the Behaviour (A)"),
        )
        for label in (
            "Physiological / Biological",
            "Physical / Environmental",
            "Routine / Transition",
            "Social / Interpersonal",
            "Special",
        ):
            self.assertIn(label, html)
        self.assertLess(
            html.index("Physiological / Biological"),
            html.index("Physical / Environmental"),
        )
        self.assertLess(
            html.index("Physical / Environmental"),
            html.index("Routine / Transition"),
        )
        self.assertLess(
            html.index("Routine / Transition"),
            html.index("Social / Interpersonal"),
        )
        self.assertLess(html.index("Social / Interpersonal"), html.index("Special"))
        self.assertIn("None observed", html)
        self.assertIn("Information not known or unavailable", html)
        self.assertEqual(html.count('maxlength="1000"'), 4)

        hunger_id = self.option_id("HUNGER")
        self.assertIn(
            f'id="setting-event-{hunger_id}"', html
        )
        self.assertIn(
            f'for="setting-event-{hunger_id}"', html
        )

    def test_v1_edit_form_does_not_render_setting_event_controls_or_marker(self):
        self.login()
        response = self.client.post(
            "/shift/10/behaviour",
            data={
                "occurrence_local": "2026-08-03T07:00",
                "submission_token": "V" * 43,
                "self_harm": "1",
                "notes": "Draft notes",
                "lifecycle_action": "in_progress",
                "confirm_distinct_episode": "1",
            },
        )
        self.assertEqual(response.status_code, 302)

        page = self.client.get("/shift/10/behaviour/1/edit")
        html = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('class="setting-events-section"', html)
        self.assertNotIn('name="setting_events_present"', html)
        self.assertNotIn('name="setting_event_option_ids"', html)

    def test_validation_error_preserves_modern_setting_event_selection_and_other_text(self):
        self.login()
        hunger_id = self.option_id("HUNGER")
        other_id = self.option_id("OTHER_HEALTH_PHYSICAL")
        payload = self.fixture.abc_payload(token="E" * 43)
        payload.pop("response_blocked_behaviour")
        form = MultiDict()
        for key, value in payload.items():
            if key != "setting_event_option_ids":
                form.add(key, value)
        form.add("setting_events_present", "1")
        form.add("setting_event_option_ids", str(hunger_id))
        form.add("setting_event_option_ids", str(other_id))
        form.add(
            app.setting_event_other_field_name("OTHER_HEALTH_PHYSICAL"),
            "Observed health detail",
        )

        response = self.client.post("/shift/10/behaviour", data=form)
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 400)
        self.assertRegex(
            html,
            rf'id="setting-event-{hunger_id}"[^>]*checked',
        )
        self.assertRegex(
            html,
            rf'id="setting-event-{other_id}"[^>]*checked',
        )
        self.assertIn("Observed health detail", html)
        self.assertIn('name="setting_events_present" value="1"', html)

    def test_modern_zero_selection_draft_redisplays_without_database_selection(self):
        self.login()
        self.assertEqual(
            self.client.post(
                "/shift/10/behaviour",
                data=self.fixture.in_progress_payload(),
            ).status_code,
            302,
        )
        hunger_id = self.option_id("HUNGER")
        update = self.fixture.in_progress_payload(
            expected_version="1"
        )
        update.pop("lifecycle_action")
        update.pop("submission_token")
        update.pop("setting_event_option_ids")
        update.pop("antecedent_transition_activities")
        update.update({"action": "save", "setting_events_present": "1"})

        response = self.client.post(
            "/shift/10/behaviour/1/edit", data=update
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 400)
        self.assertNotRegex(
            html,
            rf'id="setting-event-{hunger_id}"[^>]*checked',
        )
        self.assertIn('name="setting_events_present" value="1"', html)
        self.assertEqual(self.row()["version_number"], 1)

    def test_edit_form_prechecks_existing_and_only_shows_related_inactive_option(self):
        self.login()
        self.assertEqual(
            self.client.post(
                "/shift/10/behaviour",
                data=self.fixture.in_progress_payload(),
            ).status_code,
            302,
        )
        thirst_id = self.option_id("THIRST")
        unrelated_id = self.option_id("POOR_SLEEP")
        conn = sqlite3.connect(self.path)
        conn.execute(
            "UPDATE behaviour_occurrence_setting_events "
            "SET setting_event_option_id = ? "
            "WHERE behaviour_occurrence_id = 1",
            (thirst_id,),
        )
        conn.execute(
            "UPDATE behaviour_setting_event_options SET active = 0 "
            "WHERE setting_event_option_id IN (?, ?)",
            (thirst_id, unrelated_id),
        )
        conn.commit()
        conn.close()

        page = self.client.get("/shift/10/behaviour/1/edit")
        html = page.get_data(as_text=True)

        self.assertEqual(page.status_code, 200)
        self.assertRegex(
            html,
            rf'id="setting-event-{thirst_id}"[^>]*checked',
        )
        self.assertIn("inactive historical option", html)
        self.assertNotIn("Poor or insufficient sleep", html)

    def test_review_detail_displays_selected_other_before_antecedent(self):
        occurrence = self.create_direct_abc(
            ["OTHER_HEALTH_PHYSICAL"],
            other_text="Observed earlier health detail",
            token="R" * 43,
        )
        response = self.review_page(occurrence["behaviour_occurrence_id"])
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Setting Events", html)
        self.assertIn("Other health or physical factor", html)
        self.assertIn("Other details: Observed earlier health detail", html)
        self.assertLess(
            html.index("Setting Events"),
            html.index("Before the Behaviour (A)"),
        )

    def test_historical_zero_row_abc_has_neutral_review_message(self):
        self.login()
        payload = self.fixture.in_progress_payload(token="H" * 43)
        payload.pop("setting_event_option_ids")
        payload["setting_events_present"] = "1"
        payload["confirm_distinct_episode"] = "1"
        self.assertEqual(
            self.client.post("/shift/10/behaviour", data=payload).status_code,
            302,
        )
        conn = sqlite3.connect(self.path)
        conn.execute(
            "UPDATE behaviour_occurrences SET status = 'Recorded', "
            "duration_until_calm_minutes = 1 "
            "WHERE behaviour_occurrence_id = 1"
        )
        conn.commit()
        conn.close()

        response = self.review_page(1)
        html = response.get_data(as_text=True)
        self.assertIn(
            "Not captured before Setting Events were introduced.", html
        )
        self.assertNotIn("None observed", html)

    def test_explicit_special_options_display_naturally(self):
        none = self.create_direct_abc(
            ["NONE_OBSERVED"], token="N" * 43
        )
        response = self.review_page(none["behaviour_occurrence_id"])
        self.assertIn("None observed", response.get_data(as_text=True))

        unknown = self.create_direct_abc(
            ["INFORMATION_UNKNOWN"], token="U" * 43
        )
        response = self.review_page(unknown["behaviour_occurrence_id"])
        self.assertIn(
            "Information not known or unavailable",
            response.get_data(as_text=True),
        )

    def test_voided_abc_keeps_setting_events_on_review_detail(self):
        occurrence = self.create_direct_abc(
            ["HUNGER"], token="W" * 43
        )
        self.fixture.login(3, shift_id=None)
        response = self.client.post(
            f"/behaviour/occurrences/{occurrence['behaviour_occurrence_id']}/void",
            data={"void_reason": "Test correction"},
        )
        self.assertEqual(response.status_code, 302)

        response = self.review_page(occurrence["behaviour_occurrence_id"])
        self.assertIn("Hunger", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
