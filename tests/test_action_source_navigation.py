import unittest

import app


class ActionSourceNavigationTests(unittest.TestCase):
    SUPPORTED_SOURCES = (
        (
            "shift_notes",
            "Shift Note",
            "shift_note_review_detail",
            "note_id",
        ),
        (
            "food_fluid_entries",
            "Food & Fluid",
            "food_fluid_review_detail",
            "entry_id",
        ),
        (
            "shift_activities",
            "Activity",
            "activity_review_detail",
            "activity_id",
        ),
        (
            "sleep_events",
            "Sleep",
            "sleep_review_detail",
            "sleep_event_id",
        ),
        (
            "behaviour_occurrences",
            "Behaviour",
            "behaviour_review_detail",
            "occurrence_id",
        ),
        (
            "incident_reports",
            "Incident",
            "incident_review_detail",
            "incident_id",
        ),
        (
            "shift_care_task_entries",
            "Care",
            "care_review_detail",
            "entry_id",
        ),
        (
            "toileting_events",
            "Toileting",
            "toileting_review_detail",
            "entry_id",
        ),
        (
            "shift_housekeeping_task_entries",
            "Housekeeping",
            "housekeeping_review_detail",
            "entry_id",
        ),
    )

    LEGACY_SOURCES = (
        ("behaviour_entries", "Behaviour"),
        ("medication_entries", "Medication"),
        ("care_entries", "Care Entry"),
    )

    def test_supported_sources_have_friendly_labels_and_review_links(self):
        with app.app.test_request_context("/action/1"):
            for source_table, label, endpoint, id_parameter in (
                self.SUPPORTED_SOURCES
            ):
                with self.subTest(source_table=source_table):
                    context = app.get_action_source_context(
                        {
                            "source_table": source_table,
                            "source_id": 17,
                        },
                        can_link=True,
                    )

                    self.assertEqual(context["label"], label)
                    self.assertEqual(context["source_id"], 17)
                    self.assertEqual(
                        context["url"],
                        app.url_for(
                            endpoint,
                            **{id_parameter: 17}
                        )
                    )

    def test_action_source_link_preserves_valid_storyline_context(self):
        with app.app.test_request_context("/action/1"):
            context = app.get_action_source_context(
                {
                    "source_table": "incident_reports",
                    "source_id": 17,
                },
                can_link=True,
                return_context={
                    "storyline_client_id": 3,
                    "storyline_filter": "Incident",
                    "storyline_page": 2,
                },
            )

            self.assertEqual(
                context["url"],
                "/manager-review/incidents/17?"
                "storyline_client_id=3&storyline_filter=Incident&"
                "storyline_page=2",
            )

    def test_storyline_context_rejects_invalid_values(self):
        with app.app.test_request_context(
            "/action/1?storyline_client_id=3&"
            "storyline_filter=NotAFilter&storyline_page=0"
        ):
            self.assertIsNone(
                app.get_action_storyline_return_context(app.request.args)
            )

    def test_unknown_or_malformed_sources_have_no_link(self):
        with app.app.test_request_context("/action/1"):
            cases = (
                (
                    {
                        "source_table": "legacy_source_table",
                        "source_id": 99,
                    },
                    "Source record",
                ),
                (
                    {
                        "source_table": "incident_reports",
                        "source_id": "not-an-integer",
                    },
                    "Incident",
                ),
            )
            for action, label in cases:
                with self.subTest(action=action):
                    context = app.get_action_source_context(
                        action,
                        can_link=True,
                    )

                    self.assertEqual(context["label"], label)
                    self.assertIsNone(context["url"])

    def test_legacy_sources_retain_labels_without_source_links(self):
        with app.app.test_request_context("/action/1"):
            for source_table, label in self.LEGACY_SOURCES:
                with self.subTest(source_table=source_table):
                    context = app.get_action_source_context(
                        {
                            "source_table": source_table,
                            "source_id": 23,
                        },
                        can_link=True,
                    )

                    self.assertEqual(context["label"], label)
                    self.assertEqual(context["source_id"], 23)
                    self.assertIsNone(context["url"])


if __name__ == "__main__":
    unittest.main()
