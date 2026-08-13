import unittest

from werkzeug.datastructures import MultiDict

import app


class StaffNoticeGuidedSchedulingTests(unittest.TestCase):
    def base_form(self, path):
        data = MultiDict([
            ("title", "Guided notice"),
            ("notice_text", "Notice text"),
            ("priority", "Normal"),
            ("audience_rule_types", "Selected Individual"),
            ("selected_user_ids", "4"),
            ("guided_schedule_path", path),
            ("schedule_enabled", "1"),
        ])
        if path == "one_time":
            data["guided_due_local"] = "2026-08-14T09:00"
        elif path == "calendar_once":
            data["guided_calendar_date"] = "2026-08-14"
        elif "interval" in path:
            data["guided_interval_days"] = "2"
        if "weekdays" in path:
            data.setlist("weekdays", ["0", "4"])
        if "types" in path:
            data.setlist("shift_types", ["Day", "Overnight"])
        if path == "shift_once_specific":
            data.update({
                "guided_shift_client_id": "1",
                "guided_shift_date": "2026-08-14",
                "guided_shift_type": "Day"
            })
        return data

    def test_every_guided_path_maps_to_an_existing_combination(self):
        for path, combination in app.STAFF_NOTICE_GUIDED_SCHEDULES.items():
            with self.subTest(path=path):
                payload = app.build_staff_notice_draft_payload_from_form(
                    self.base_form(path)
                )
                if combination is None:
                    self.assertIsNone(payload["schedule"])
                else:
                    self.assertEqual(
                        tuple(payload["schedule"][field] for field in (
                            "occurrence_basis",
                            "recurrence_pattern",
                            "shift_applicability"
                        )),
                        combination
                    )
                    app.validate_staff_notice_management_draft(payload)

    def test_specific_person_neville_day_shift_mapping(self):
        form = self.base_form("shift_once_specific")
        payload = app.build_staff_notice_draft_payload_from_form(form)
        schedule = payload["schedule"]
        self.assertEqual(payload["audience_rules"][0]["user_id"], 4)
        self.assertEqual(schedule["specific_shift_client_id"], 1)
        self.assertEqual(schedule["specific_shift_date"], "2026-08-14")
        self.assertEqual(schedule["specific_shift_type"], "Day")
        self.assertNotIn("specific_calendar_date", schedule)
        self.assertNotIn("shift_types", schedule)
        self.assertEqual(
            (schedule["occurrence_basis"], schedule["recurrence_pattern"],
             schedule["shift_applicability"]),
            ("Shift", "Once", "Specific Shift")
        )

    def test_invalid_guided_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "guided schedule path"):
            app.build_staff_notice_draft_payload_from_form(
                self.base_form("not-a-real-path")
            )


if __name__ == "__main__":
    unittest.main()
