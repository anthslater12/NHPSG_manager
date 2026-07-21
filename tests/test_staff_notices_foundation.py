import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import app


class StaffNoticeAuthorizationTests(unittest.TestCase):

    def test_recognized_management_roles_are_allowed(self):
        for role in ["Admin", "Program Manager", "Director"]:
            with self.subTest(role=role):
                self.assertTrue(
                    app.user_can_manage_staff_notices({
                        "user_id": 12,
                        "role": role
                    })
                )

    def test_non_management_roles_are_denied(self):
        for role in [
            "Support Worker",
            "Behaviour Consultant",
            "Unknown Role",
            "",
            None
        ]:
            with self.subTest(role=role):
                self.assertFalse(
                    app.user_can_manage_staff_notices({
                        "user_id": 12,
                        "role": role
                    })
                )

    def test_missing_user_or_role_is_denied(self):
        for session_data in [
            {},
            {"role": "Admin"},
            {"user_id": 12},
            {"user_id": None, "role": "Admin"}
        ]:
            with self.subTest(session_data=session_data):
                self.assertFalse(
                    app.user_can_manage_staff_notices(session_data)
                )

    def test_malformed_session_user_ids_are_denied(self):
        malformed_user_ids = [
            True,
            False,
            1.0,
            0,
            -1,
            "1",
            "",
            "not-an-id",
            None,
            object()
        ]

        for user_id in malformed_user_ids:
            with self.subTest(user_id=user_id):
                self.assertFalse(
                    app.user_can_manage_staff_notices({
                        "user_id": user_id,
                        "role": "Admin"
                    })
                )

    def test_missing_request_context_is_denied(self):
        self.assertFalse(app.user_can_manage_staff_notices())

    def test_current_session_is_used_inside_request_context(self):
        with app.app.test_request_context("/"):
            app.session["user_id"] = 18
            app.session["role"] = "Program Manager"

            self.assertTrue(app.user_can_manage_staff_notices())


class StaffNoticeTimeTests(unittest.TestCase):

    def test_vancouver_winter_time_converts_to_utc(self):
        result = app.staff_notice_local_datetime_to_utc(
            "2026-01-15T12:00"
        )

        self.assertEqual(
            result,
            datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc)
        )

    def test_vancouver_summer_time_converts_to_utc(self):
        result = app.staff_notice_local_datetime_to_utc(
            "2026-07-15T12:00"
        )

        self.assertEqual(
            result,
            datetime(2026, 7, 15, 19, 0, tzinfo=timezone.utc)
        )

    def test_times_on_both_sides_of_spring_transition(self):
        before = app.staff_notice_local_datetime_to_utc(
            "2024-03-10T01:30"
        )
        after = app.staff_notice_local_datetime_to_utc(
            "2024-03-10T03:30"
        )

        self.assertEqual(
            before,
            datetime(2024, 3, 10, 9, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            after,
            datetime(2024, 3, 10, 10, 30, tzinfo=timezone.utc)
        )

    def test_times_on_both_sides_of_fall_transition(self):
        before = app.staff_notice_local_datetime_to_utc(
            "2024-11-03T00:30"
        )
        after = app.staff_notice_local_datetime_to_utc(
            "2024-11-03T02:30"
        )

        self.assertEqual(
            before,
            datetime(2024, 11, 3, 7, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            after,
            datetime(2024, 11, 3, 10, 30, tzinfo=timezone.utc)
        )

    def test_nonexistent_vancouver_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            app.staff_notice_local_datetime_to_utc(
                "2024-03-10T02:30"
            )

    def test_ambiguous_vancouver_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            app.staff_notice_local_datetime_to_utc(
                "2024-11-03T01:30"
            )

    def test_malformed_local_timestamps_are_rejected(self):
        malformed_values = [
            None,
            "",
            "2026-07-15",
            "2026-07-15 12:00",
            "2026-7-15T12:00",
            "2026-07-15T25:00",
            "not-a-time"
        ]

        for value in malformed_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    app.parse_staff_notice_local_datetime(value)

    def test_naive_and_malformed_utc_timestamps_are_rejected(self):
        for value in [
            datetime(2026, 7, 15, 19, 0),
            "2026-07-15T19:00:00",
            "not-a-time",
            ""
        ]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    app.parse_staff_notice_utc_datetime(value)

    def test_utc_timestamp_is_formatted_canonically(self):
        value = datetime(
            2026,
            7,
            15,
            12,
            34,
            56,
            tzinfo=timezone(timedelta(hours=-7))
        )

        self.assertEqual(
            app.format_staff_notice_utc_datetime(value),
            "2026-07-15T19:34:56Z"
        )

    def test_utc_time_converts_to_vancouver_for_presentation(self):
        local_value = app.staff_notice_utc_datetime_to_local(
            "2026-07-15T19:34:56Z"
        )

        self.assertEqual(local_value.strftime("%z"), "-0700")
        self.assertEqual(
            app.format_staff_notice_local_datetime(
                "2026-07-15T19:34:56Z"
            ),
            "2026-07-15 12:34"
        )

    def test_application_local_date_uses_vancouver_date(self):
        result = app.get_application_local_date(
            datetime(2026, 7, 21, 6, 30, tzinfo=timezone.utc)
        )

        self.assertEqual(result, date(2026, 7, 20))

    def test_application_now_is_aware_utc(self):
        result = app.get_application_now_utc()

        self.assertEqual(result.tzinfo, timezone.utc)
        self.assertIsNotNone(result.utcoffset())


class StaffNoticeStatusTests(unittest.TestCase):

    DUE_AT = "2026-07-21T22:00:00Z"
    VIEWED_AT = "2026-07-21T20:00:00Z"

    def get_status(self, **overrides):
        values = {
            "active_acknowledgement_at_utc": None,
            "due_at_utc": self.DUE_AT,
            "requirement_status": "Required",
            "first_viewed_at_utc": None
        }
        values.update(overrides)

        return app.get_recipient_staff_notice_status(**values)

    def test_not_viewed(self):
        self.assertEqual(self.get_status(), "Not Viewed")

    def test_viewed_awaiting_acknowledgement(self):
        self.assertEqual(
            self.get_status(first_viewed_at_utc=self.VIEWED_AT),
            "Viewed – Awaiting Acknowledgement"
        )

    def test_no_longer_required(self):
        self.assertEqual(
            self.get_status(
                requirement_status="No Longer Required"
            ),
            "No Longer Required"
        )

    def test_cancelled(self):
        self.assertEqual(
            self.get_status(requirement_status="Cancelled"),
            "Cancelled"
        )

    def test_acknowledged_without_deadline(self):
        self.assertEqual(
            self.get_status(
                active_acknowledgement_at_utc=(
                    "2026-07-21T22:00:01Z"
                ),
                due_at_utc=None
            ),
            "Acknowledged"
        )

    def test_acknowledgement_before_deadline_is_on_time(self):
        self.assertEqual(
            self.get_status(
                active_acknowledgement_at_utc=(
                    "2026-07-21T21:59:59Z"
                )
            ),
            "Acknowledged"
        )

    def test_acknowledgement_exactly_at_deadline_is_on_time(self):
        self.assertEqual(
            self.get_status(
                active_acknowledgement_at_utc=self.DUE_AT
            ),
            "Acknowledged"
        )

    def test_acknowledgement_after_deadline_is_late(self):
        self.assertEqual(
            self.get_status(
                active_acknowledgement_at_utc=(
                    "2026-07-21T22:00:01Z"
                )
            ),
            "Acknowledged Late"
        )

    def test_acknowledgement_takes_precedence_over_other_fields(self):
        self.assertEqual(
            self.get_status(
                active_acknowledgement_at_utc=self.DUE_AT,
                requirement_status="Cancelled",
                first_viewed_at_utc=self.VIEWED_AT
            ),
            "Acknowledged"
        )
        self.assertEqual(
            self.get_status(
                active_acknowledgement_at_utc=(
                    "2026-07-21T22:00:01Z"
                ),
                requirement_status="No Longer Required",
                first_viewed_at_utc=self.VIEWED_AT
            ),
            "Acknowledged Late"
        )

    def test_requirement_status_takes_precedence_over_view(self):
        self.assertEqual(
            self.get_status(
                requirement_status="Cancelled",
                first_viewed_at_utc=self.VIEWED_AT
            ),
            "Cancelled"
        )
        self.assertEqual(
            self.get_status(
                requirement_status="No Longer Required",
                first_viewed_at_utc=self.VIEWED_AT
            ),
            "No Longer Required"
        )

    def test_overdue_is_not_a_separate_display_status(self):
        self.assertEqual(
            self.get_status(
                due_at_utc="2020-01-01T00:00:00Z"
            ),
            "Not Viewed"
        )

    def test_invalid_requirement_status_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "requirement status"):
            self.get_status(requirement_status="Overdue")


class StaffNoticeDeliveryAuthorizationTests(unittest.TestCase):

    def test_matching_recipient_owns_delivery(self):
        delivery = {"user_id": 42, "recipient_access": 1}

        self.assertTrue(
            app.user_owns_staff_notice_delivery(delivery, 42)
        )

    def test_nonmatching_and_missing_users_do_not_own_delivery(self):
        delivery = {"user_id": 42, "recipient_access": 1}

        self.assertFalse(
            app.user_owns_staff_notice_delivery(delivery, 41)
        )
        self.assertFalse(
            app.user_owns_staff_notice_delivery(delivery, None)
        )
        self.assertFalse(
            app.user_owns_staff_notice_delivery(None, 42)
        )

    def test_malformed_delivery_user_ids_fail_closed(self):
        malformed_delivery_user_ids = [
            True,
            False,
            1.0,
            0,
            -1,
            "1",
            "",
            "not-an-id",
            None,
            object()
        ]

        for delivery_user_id in malformed_delivery_user_ids:
            with self.subTest(delivery_user_id=delivery_user_id):
                self.assertFalse(
                    app.user_owns_staff_notice_delivery(
                        {
                            "user_id": delivery_user_id,
                            "recipient_access": 1
                        },
                        1
                    )
                )

    def test_malformed_requesting_user_ids_fail_closed(self):
        malformed_requesting_user_ids = [
            True,
            False,
            1.0,
            0,
            -1,
            "1",
            "",
            "not-an-id",
            None,
            object()
        ]

        delivery = {"user_id": 1, "recipient_access": 1}

        for user_id in malformed_requesting_user_ids:
            with self.subTest(user_id=user_id):
                self.assertFalse(
                    app.user_owns_staff_notice_delivery(
                        delivery,
                        user_id
                    )
                )

    def test_manager_can_own_only_their_personal_delivery(self):
        manager_user_id = 7
        manager_delivery = {
            "user_id": manager_user_id,
            "recipient_access": 1,
            "role": "Admin"
        }
        another_delivery = {
            "user_id": 8,
            "recipient_access": 1,
            "role": "Support Worker"
        }

        self.assertTrue(
            app.user_owns_staff_notice_delivery(
                manager_delivery,
                manager_user_id
            )
        )
        self.assertFalse(
            app.user_owns_staff_notice_delivery(
                another_delivery,
                manager_user_id
            )
        )

    def test_role_and_shift_access_do_not_establish_ownership(self):
        delivery = {
            "user_id": 15,
            "recipient_access": 1,
            "role": "Admin",
            "shift_id": 99
        }

        self.assertFalse(
            app.user_owns_staff_notice_delivery(delivery, 16)
        )

    def test_content_access_predicate_is_independent_of_ownership(self):
        allowed = {"user_id": 42, "recipient_access": 1}
        revoked = {"user_id": 42, "recipient_access": 0}

        self.assertTrue(
            app.staff_notice_delivery_has_content_access(allowed)
        )
        self.assertFalse(
            app.staff_notice_delivery_has_content_access(revoked)
        )
        self.assertFalse(
            app.staff_notice_delivery_has_content_access({
                "user_id": 42
            })
        )

    def test_malformed_recipient_access_values_fail_closed(self):
        malformed_access_values = [
            True,
            False,
            1.0,
            "1",
            "",
            "not-access",
            0,
            2,
            -1,
            None,
            object()
        ]

        for recipient_access in malformed_access_values:
            with self.subTest(recipient_access=recipient_access):
                self.assertFalse(
                    app.staff_notice_delivery_has_content_access({
                        "user_id": 42,
                        "recipient_access": recipient_access
                    })
                )

        self.assertFalse(
            app.staff_notice_delivery_has_content_access({
                "user_id": 42
            })
        )
        self.assertFalse(
            app.staff_notice_delivery_has_content_access(object())
        )

    def test_exact_integer_identifiers_and_access_are_accepted(self):
        delivery = {"user_id": 42, "recipient_access": 1}

        self.assertTrue(
            app.user_owns_staff_notice_delivery(delivery, 42)
        )
        self.assertTrue(
            app.staff_notice_delivery_has_content_access(delivery)
        )
        self.assertTrue(
            app.user_can_access_staff_notice_delivery(delivery, 42)
        )

    def test_combined_access_requires_ownership_and_current_access(self):
        allowed = {"user_id": 42, "recipient_access": 1}
        revoked = {"user_id": 42, "recipient_access": 0}

        self.assertTrue(
            app.user_can_access_staff_notice_delivery(allowed, 42)
        )
        self.assertFalse(
            app.user_can_access_staff_notice_delivery(allowed, 41)
        )
        self.assertFalse(
            app.user_can_access_staff_notice_delivery(revoked, 42)
        )

    def test_combined_access_rejects_malformed_values(self):
        malformed_cases = [
            ({"user_id": 1, "recipient_access": 1}, True),
            ({"user_id": 1, "recipient_access": 1}, 1.0),
            ({"user_id": True, "recipient_access": 1}, 1),
            ({"user_id": 1.0, "recipient_access": 1}, 1),
            ({"user_id": 1, "recipient_access": True}, 1),
            ({"user_id": 1, "recipient_access": 1.0}, 1),
            ({"user_id": 1, "recipient_access": "1"}, 1)
        ]

        for delivery, user_id in malformed_cases:
            with self.subTest(
                delivery=delivery,
                user_id=user_id
            ):
                self.assertFalse(
                    app.user_can_access_staff_notice_delivery(
                        delivery,
                        user_id
                    )
                )


class StaffNoticeFoundationSafetyTests(unittest.TestCase):

    def test_foundation_helpers_do_not_open_database_connections(self):
        delivery = {"user_id": 42, "recipient_access": 1}

        with (
            mock.patch.object(
                app,
                "get_db",
                side_effect=AssertionError(
                    "get_db() access is forbidden"
                )
            ),
            mock.patch.object(
                app.sqlite3,
                "connect",
                side_effect=AssertionError(
                    "Direct sqlite3.connect() access is forbidden"
                )
            )
        ):
            self.assertTrue(
                app.user_can_manage_staff_notices({
                    "user_id": 1,
                    "role": "Admin"
                })
            )
            app.get_application_now_utc()
            app.parse_staff_notice_utc_datetime(
                "2026-07-21T19:00:00Z"
            )
            app.format_staff_notice_utc_datetime(
                "2026-07-21T19:00:00Z"
            )
            app.parse_staff_notice_local_datetime(
                "2026-07-21T12:00"
            )
            app.get_application_local_date(
                "2026-07-21T12:00:00Z"
            )
            app.staff_notice_local_datetime_to_utc(
                "2026-07-21T12:00"
            )
            app.staff_notice_utc_datetime_to_local(
                "2026-07-21T19:00:00Z"
            )
            app.format_staff_notice_local_datetime(
                "2026-07-21T19:00:00Z"
            )
            app.get_recipient_staff_notice_status(
                requirement_status="Required"
            )
            app.user_owns_staff_notice_delivery(delivery, 42)
            app.staff_notice_delivery_has_content_access(delivery)
            app.user_can_access_staff_notice_delivery(delivery, 42)

    def test_importing_app_does_not_create_or_modify_a_database(self):
        repository_root = Path(__file__).resolve().parents[1]
        production_database = repository_root / "nhpsg.db"

        def get_file_fingerprint(path):
            stat_result = path.stat()

            return {
                "exists": path.exists(),
                "size": stat_result.st_size,
                "sha256": hashlib.sha256(
                    path.read_bytes()
                ).hexdigest(),
                "modified_at_ns": stat_result.st_mtime_ns
            }

        before_fingerprint = get_file_fingerprint(
            production_database
        )

        with tempfile.TemporaryDirectory() as directory:
            environment = os.environ.copy()
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPATH"] = str(repository_root)

            import_script = """
import sqlite3

def blocked_connect(*args, **kwargs):
    raise AssertionError(
        "sqlite3.connect() was called while importing app"
    )

sqlite3.connect = blocked_connect

import app
"""

            result = subprocess.run(
                [sys.executable, "-B", "-c", import_script],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(
                (Path(directory) / "nhpsg.db").exists()
            )

        after_fingerprint = get_file_fingerprint(
            production_database
        )

        self.assertEqual(
            after_fingerprint,
            before_fingerprint
        )


if __name__ == "__main__":
    unittest.main()
