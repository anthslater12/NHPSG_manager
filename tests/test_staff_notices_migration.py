import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

import add_staff_notices_tables as migration
from add_staff_notice_acknowledgement_invalidation import (
    migrate as migrate_acknowledgements,
    schema_is_current as acknowledgement_schema_is_current,
    sql_token_signature,
)


# Canonical table DDL transcribed directly from Section 4 of the approved
# Staff Notices Version 1 DOCX blueprint. These fixtures deliberately do
# not use migration.TABLE_SQL or migration.expected_schema_metadata().
BLUEPRINT_TABLE_SQL = {
    "staff_notices": """
        CREATE TABLE staff_notices (
            notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            notice_text TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'Normal'
                CHECK (priority IN ('Normal', 'Important', 'Urgent')),
            client_id INTEGER NULL,
            status TEXT NOT NULL DEFAULT 'Draft'
                CHECK (status IN ('Draft', 'Published', 'Withdrawn', 'Replaced')),
            draft_active INTEGER NOT NULL DEFAULT 1
                CHECK (draft_active IN (0, 1)),
            effective_start_at_utc TEXT NULL,
            expires_at_utc TEXT NULL,
            until_withdrawn INTEGER NOT NULL DEFAULT 0
                CHECK (until_withdrawn IN (0, 1)),
            version_number INTEGER NOT NULL DEFAULT 1
                CHECK (version_number >= 1),
            replaces_notice_id INTEGER NULL,
            created_by_user_id INTEGER NOT NULL,
            created_at_utc TEXT NOT NULL,
            updated_by_user_id INTEGER NULL,
            updated_at_utc TEXT NULL,
            published_by_user_id INTEGER NULL,
            published_at_utc TEXT NULL,
            withdrawn_by_user_id INTEGER NULL,
            withdrawn_at_utc TEXT NULL,
            withdrawal_reason TEXT NULL,
            replaced_by_user_id INTEGER NULL,
            replaced_at_utc TEXT NULL,
            replacement_reason TEXT NULL,
            FOREIGN KEY (client_id) REFERENCES clients(client_id),
            FOREIGN KEY (replaces_notice_id) REFERENCES staff_notices(notice_id),
            FOREIGN KEY (created_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (published_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (withdrawn_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (replaced_by_user_id) REFERENCES users(user_id),
            CHECK (
                status = 'Draft'
                OR (
                    effective_start_at_utc IS NOT NULL
                    AND published_at_utc IS NOT NULL
                    AND (
                        (until_withdrawn = 1 AND expires_at_utc IS NULL)
                        OR
                        (until_withdrawn = 0 AND expires_at_utc IS NOT NULL)
                    )
                )
            ),
            CHECK (
                expires_at_utc IS NULL
                OR effective_start_at_utc IS NULL
                OR expires_at_utc >= effective_start_at_utc
            )
        )
    """,
    "staff_notice_audiences": """
        CREATE TABLE staff_notice_audiences (
            audience_id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_id INTEGER NOT NULL UNIQUE,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY (notice_id) REFERENCES staff_notices(notice_id)
        )
    """,
    "staff_notice_audience_rules": """
        CREATE TABLE staff_notice_audience_rules (
            audience_rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
            audience_id INTEGER NOT NULL,
            rule_type TEXT NOT NULL
                CHECK (rule_type IN (
                    'Core Organization',
                    'All Support Workers',
                    'Selected Role',
                    'Selected Individual',
                    'Applicable Shift Staff'
                )),
            role_name TEXT NULL,
            user_id INTEGER NULL,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY (audience_id) REFERENCES staff_notice_audiences(audience_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            CHECK (
                (rule_type = 'Selected Role' AND role_name IS NOT NULL AND user_id IS NULL)
                OR
                (rule_type = 'Selected Individual' AND role_name IS NULL AND user_id IS NOT NULL)
                OR
                (rule_type IN ('Core Organization', 'All Support Workers', 'Applicable Shift Staff')
                    AND role_name IS NULL AND user_id IS NULL)
            )
        )
    """,
    "staff_notice_audience_eligibility_periods": """
        CREATE TABLE staff_notice_audience_eligibility_periods (
            eligibility_period_id INTEGER PRIMARY KEY AUTOINCREMENT,
            audience_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            eligible_from_at_utc TEXT NOT NULL,
            eligible_until_at_utc TEXT NULL,
            eligibility_source_summary TEXT NOT NULL,
            opened_by_user_id INTEGER NULL,
            closed_by_user_id INTEGER NULL,
            close_reason TEXT NULL,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NULL,
            FOREIGN KEY (audience_id) REFERENCES staff_notice_audiences(audience_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (opened_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (closed_by_user_id) REFERENCES users(user_id),
            CHECK (
                eligible_until_at_utc IS NULL
                OR eligible_until_at_utc >= eligible_from_at_utc
            )
        )
    """,
    "staff_notice_schedules": """
        CREATE TABLE staff_notice_schedules (
            schedule_id INTEGER PRIMARY KEY AUTOINCREMENT,
            notice_id INTEGER NOT NULL UNIQUE,
            occurrence_basis TEXT NOT NULL
                CHECK (occurrence_basis IN ('One Time', 'Calendar', 'Shift')),
            recurrence_pattern TEXT NOT NULL
                CHECK (recurrence_pattern IN ('Once', 'Daily', 'Interval Days', 'Selected Weekdays')),
            shift_applicability TEXT NOT NULL DEFAULT 'None'
                CHECK (shift_applicability IN ('None', 'Every Shift', 'Selected Shift Types', 'Specific Shift')),
            interval_days INTEGER NULL
                CHECK (interval_days IS NULL OR interval_days >= 2),
            recurrence_anchor_date TEXT NULL,
            specific_calendar_date TEXT NULL,
            specific_shift_client_id INTEGER NULL,
            specific_shift_date TEXT NULL,
            specific_shift_type TEXT NULL
                CHECK (specific_shift_type IS NULL OR specific_shift_type IN ('Day', 'Afternoon', 'Overnight')),
            one_time_due_at_utc TEXT NULL,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY (notice_id) REFERENCES staff_notices(notice_id),
            FOREIGN KEY (specific_shift_client_id) REFERENCES clients(client_id),
            CHECK (recurrence_pattern = 'Interval Days' OR interval_days IS NULL),
            CHECK (
                occurrence_basis <> 'One Time'
                OR (
                    recurrence_pattern = 'Once'
                    AND shift_applicability = 'None'
                    AND specific_calendar_date IS NULL
                    AND specific_shift_client_id IS NULL
                    AND specific_shift_date IS NULL
                    AND specific_shift_type IS NULL
                )
            ),
            CHECK (occurrence_basis <> 'Calendar' OR shift_applicability = 'None'),
            CHECK (
                occurrence_basis <> 'Calendar'
                OR recurrence_pattern <> 'Once'
                OR specific_calendar_date IS NOT NULL
            ),
            CHECK (occurrence_basis <> 'Shift' OR shift_applicability <> 'None'),
            CHECK (
                shift_applicability <> 'Specific Shift'
                OR (
                    occurrence_basis = 'Shift'
                    AND recurrence_pattern = 'Once'
                    AND specific_shift_client_id IS NOT NULL
                    AND specific_shift_date IS NOT NULL
                    AND specific_shift_type IS NOT NULL
                )
            ),
            CHECK (recurrence_pattern = 'Once' OR specific_calendar_date IS NULL)
        )
    """,
    "staff_notice_schedule_shift_types": """
        CREATE TABLE staff_notice_schedule_shift_types (
            schedule_shift_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            shift_type TEXT NOT NULL
                CHECK (shift_type IN ('Day', 'Afternoon', 'Overnight')),
            FOREIGN KEY (schedule_id) REFERENCES staff_notice_schedules(schedule_id),
            UNIQUE (schedule_id, shift_type)
        )
    """,
    "staff_notice_schedule_weekdays": """
        CREATE TABLE staff_notice_schedule_weekdays (
            schedule_weekday_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            weekday_number INTEGER NOT NULL
                CHECK (weekday_number BETWEEN 0 AND 6),
            FOREIGN KEY (schedule_id) REFERENCES staff_notice_schedules(schedule_id),
            UNIQUE (schedule_id, weekday_number)
        )
    """,
    "staff_notice_occurrences": """
        CREATE TABLE staff_notice_occurrences (
            occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            occurrence_kind TEXT NOT NULL
                CHECK (occurrence_kind IN ('One Time', 'Calendar', 'Shift')),
            occurrence_date TEXT NULL,
            planned_client_id INTEGER NULL,
            planned_shift_type TEXT NULL
                CHECK (planned_shift_type IS NULL OR planned_shift_type IN ('Day', 'Afternoon', 'Overnight')),
            shift_id INTEGER NULL,
            is_specific_shift_occurrence INTEGER NOT NULL DEFAULT 0
                CHECK (is_specific_shift_occurrence IN (0, 1)),
            visible_from_at_utc TEXT NULL,
            due_at_utc TEXT NULL,
            due_at_is_provisional INTEGER NOT NULL DEFAULT 0
                CHECK (due_at_is_provisional IN (0, 1)),
            due_at_updated_at_utc TEXT NULL,
            occurrence_status TEXT NOT NULL DEFAULT 'Scheduled'
                CHECK (occurrence_status IN (
                    'Pending Shift',
                    'Scheduled',
                    'Active',
                    'Closed',
                    'No Shift Occurred',
                    'Cancelled'
                )),
            status_reason TEXT NULL,
            created_at_utc TEXT NOT NULL,
            shift_bound_at_utc TEXT NULL,
            status_changed_at_utc TEXT NULL,
            status_changed_by_user_id INTEGER NULL,
            FOREIGN KEY (schedule_id) REFERENCES staff_notice_schedules(schedule_id),
            FOREIGN KEY (planned_client_id) REFERENCES clients(client_id),
            FOREIGN KEY (shift_id) REFERENCES shifts(shift_id),
            FOREIGN KEY (status_changed_by_user_id) REFERENCES users(user_id),
            CHECK (occurrence_kind = 'One Time' OR occurrence_date IS NOT NULL),
            CHECK (
                occurrence_kind <> 'Shift'
                OR (
                    occurrence_date IS NOT NULL
                    AND planned_client_id IS NOT NULL
                    AND planned_shift_type IS NOT NULL
                )
            ),
            CHECK (
                is_specific_shift_occurrence = 0
                OR occurrence_kind = 'Shift'
            )
        )
    """,
    "staff_notice_deliveries": """
        CREATE TABLE staff_notice_deliveries (
            delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurrence_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            requirement_status TEXT NOT NULL DEFAULT 'Required'
                CHECK (requirement_status IN ('Required', 'No Longer Required', 'Cancelled')),
            assigned_at_utc TEXT NOT NULL,
            eligibility_cutoff_at_utc TEXT NOT NULL,
            first_viewed_at_utc TEXT NULL,
            viewed_by_user_id INTEGER NULL,
            recipient_access INTEGER NOT NULL DEFAULT 1
                CHECK (recipient_access IN (0, 1)),
            status_changed_at_utc TEXT NULL,
            status_changed_by_user_id INTEGER NULL,
            current_reason_code TEXT NULL,
            current_reason_text TEXT NULL,
            access_revoked_at_utc TEXT NULL,
            FOREIGN KEY (occurrence_id) REFERENCES staff_notice_occurrences(occurrence_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (viewed_by_user_id) REFERENCES users(user_id),
            FOREIGN KEY (status_changed_by_user_id) REFERENCES users(user_id),
            UNIQUE (occurrence_id, user_id),
            CHECK (
                (first_viewed_at_utc IS NULL AND viewed_by_user_id IS NULL)
                OR
                (first_viewed_at_utc IS NOT NULL AND viewed_by_user_id IS NOT NULL)
            )
        )
    """,
    "staff_notice_delivery_history": """
        CREATE TABLE staff_notice_delivery_history (
            delivery_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_id INTEGER NOT NULL,
            event_type TEXT NOT NULL
                CHECK (event_type IN (
                    'Assigned',
                    'No Longer Required',
                    'Reinstated',
                    'Cancelled',
                    'Access Revoked',
                    'Access Restored'
                )),
            previous_requirement_status TEXT NULL,
            new_requirement_status TEXT NULL,
            previous_recipient_access INTEGER NULL
                CHECK (previous_recipient_access IS NULL OR previous_recipient_access IN (0, 1)),
            new_recipient_access INTEGER NULL
                CHECK (new_recipient_access IS NULL OR new_recipient_access IN (0, 1)),
            reason_code TEXT NULL,
            reason_text TEXT NULL,
            changed_by_user_id INTEGER NULL,
            changed_at_utc TEXT NOT NULL,
            FOREIGN KEY (delivery_id) REFERENCES staff_notice_deliveries(delivery_id),
            FOREIGN KEY (changed_by_user_id) REFERENCES users(user_id)
        )
    """,
}

EXPECTED_COLUMNS = {
    "staff_notices": (
        ("notice_id", "INTEGER", 0, None, 1, 0),
        ("title", "TEXT", 1, None, 0, 0),
        ("notice_text", "TEXT", 1, None, 0, 0),
        ("priority", "TEXT", 1, "'Normal'", 0, 0),
        ("client_id", "INTEGER", 0, None, 0, 0),
        ("status", "TEXT", 1, "'Draft'", 0, 0),
        ("draft_active", "INTEGER", 1, "1", 0, 0),
        ("effective_start_at_utc", "TEXT", 0, None, 0, 0),
        ("expires_at_utc", "TEXT", 0, None, 0, 0),
        ("until_withdrawn", "INTEGER", 1, "0", 0, 0),
        ("version_number", "INTEGER", 1, "1", 0, 0),
        ("replaces_notice_id", "INTEGER", 0, None, 0, 0),
        ("created_by_user_id", "INTEGER", 1, None, 0, 0),
        ("created_at_utc", "TEXT", 1, None, 0, 0),
        ("updated_by_user_id", "INTEGER", 0, None, 0, 0),
        ("updated_at_utc", "TEXT", 0, None, 0, 0),
        ("published_by_user_id", "INTEGER", 0, None, 0, 0),
        ("published_at_utc", "TEXT", 0, None, 0, 0),
        ("withdrawn_by_user_id", "INTEGER", 0, None, 0, 0),
        ("withdrawn_at_utc", "TEXT", 0, None, 0, 0),
        ("withdrawal_reason", "TEXT", 0, None, 0, 0),
        ("replaced_by_user_id", "INTEGER", 0, None, 0, 0),
        ("replaced_at_utc", "TEXT", 0, None, 0, 0),
        ("replacement_reason", "TEXT", 0, None, 0, 0),
    ),
    "staff_notice_audiences": (
        ("audience_id", "INTEGER", 0, None, 1, 0),
        ("notice_id", "INTEGER", 1, None, 0, 0),
        ("created_at_utc", "TEXT", 1, None, 0, 0),
    ),
    "staff_notice_audience_rules": (
        ("audience_rule_id", "INTEGER", 0, None, 1, 0),
        ("audience_id", "INTEGER", 1, None, 0, 0),
        ("rule_type", "TEXT", 1, None, 0, 0),
        ("role_name", "TEXT", 0, None, 0, 0),
        ("user_id", "INTEGER", 0, None, 0, 0),
        ("created_at_utc", "TEXT", 1, None, 0, 0),
    ),
    "staff_notice_audience_eligibility_periods": (
        ("eligibility_period_id", "INTEGER", 0, None, 1, 0),
        ("audience_id", "INTEGER", 1, None, 0, 0),
        ("user_id", "INTEGER", 1, None, 0, 0),
        ("eligible_from_at_utc", "TEXT", 1, None, 0, 0),
        ("eligible_until_at_utc", "TEXT", 0, None, 0, 0),
        ("eligibility_source_summary", "TEXT", 1, None, 0, 0),
        ("opened_by_user_id", "INTEGER", 0, None, 0, 0),
        ("closed_by_user_id", "INTEGER", 0, None, 0, 0),
        ("close_reason", "TEXT", 0, None, 0, 0),
        ("created_at_utc", "TEXT", 1, None, 0, 0),
        ("updated_at_utc", "TEXT", 0, None, 0, 0),
    ),
    "staff_notice_schedules": (
        ("schedule_id", "INTEGER", 0, None, 1, 0),
        ("notice_id", "INTEGER", 1, None, 0, 0),
        ("occurrence_basis", "TEXT", 1, None, 0, 0),
        ("recurrence_pattern", "TEXT", 1, None, 0, 0),
        ("shift_applicability", "TEXT", 1, "'None'", 0, 0),
        ("interval_days", "INTEGER", 0, None, 0, 0),
        ("recurrence_anchor_date", "TEXT", 0, None, 0, 0),
        ("specific_calendar_date", "TEXT", 0, None, 0, 0),
        ("specific_shift_client_id", "INTEGER", 0, None, 0, 0),
        ("specific_shift_date", "TEXT", 0, None, 0, 0),
        ("specific_shift_type", "TEXT", 0, None, 0, 0),
        ("one_time_due_at_utc", "TEXT", 0, None, 0, 0),
        ("created_at_utc", "TEXT", 1, None, 0, 0),
    ),
    "staff_notice_schedule_shift_types": (
        ("schedule_shift_type_id", "INTEGER", 0, None, 1, 0),
        ("schedule_id", "INTEGER", 1, None, 0, 0),
        ("shift_type", "TEXT", 1, None, 0, 0),
    ),
    "staff_notice_schedule_weekdays": (
        ("schedule_weekday_id", "INTEGER", 0, None, 1, 0),
        ("schedule_id", "INTEGER", 1, None, 0, 0),
        ("weekday_number", "INTEGER", 1, None, 0, 0),
    ),
    "staff_notice_occurrences": (
        ("occurrence_id", "INTEGER", 0, None, 1, 0),
        ("schedule_id", "INTEGER", 1, None, 0, 0),
        ("occurrence_kind", "TEXT", 1, None, 0, 0),
        ("occurrence_date", "TEXT", 0, None, 0, 0),
        ("planned_client_id", "INTEGER", 0, None, 0, 0),
        ("planned_shift_type", "TEXT", 0, None, 0, 0),
        ("shift_id", "INTEGER", 0, None, 0, 0),
        ("is_specific_shift_occurrence", "INTEGER", 1, "0", 0, 0),
        ("visible_from_at_utc", "TEXT", 0, None, 0, 0),
        ("due_at_utc", "TEXT", 0, None, 0, 0),
        ("due_at_is_provisional", "INTEGER", 1, "0", 0, 0),
        ("due_at_updated_at_utc", "TEXT", 0, None, 0, 0),
        ("occurrence_status", "TEXT", 1, "'Scheduled'", 0, 0),
        ("status_reason", "TEXT", 0, None, 0, 0),
        ("created_at_utc", "TEXT", 1, None, 0, 0),
        ("shift_bound_at_utc", "TEXT", 0, None, 0, 0),
        ("status_changed_at_utc", "TEXT", 0, None, 0, 0),
        ("status_changed_by_user_id", "INTEGER", 0, None, 0, 0),
    ),
    "staff_notice_deliveries": (
        ("delivery_id", "INTEGER", 0, None, 1, 0),
        ("occurrence_id", "INTEGER", 1, None, 0, 0),
        ("user_id", "INTEGER", 1, None, 0, 0),
        ("requirement_status", "TEXT", 1, "'Required'", 0, 0),
        ("assigned_at_utc", "TEXT", 1, None, 0, 0),
        ("eligibility_cutoff_at_utc", "TEXT", 1, None, 0, 0),
        ("first_viewed_at_utc", "TEXT", 0, None, 0, 0),
        ("viewed_by_user_id", "INTEGER", 0, None, 0, 0),
        ("recipient_access", "INTEGER", 1, "1", 0, 0),
        ("status_changed_at_utc", "TEXT", 0, None, 0, 0),
        ("status_changed_by_user_id", "INTEGER", 0, None, 0, 0),
        ("current_reason_code", "TEXT", 0, None, 0, 0),
        ("current_reason_text", "TEXT", 0, None, 0, 0),
        ("access_revoked_at_utc", "TEXT", 0, None, 0, 0),
    ),
    "staff_notice_delivery_history": (
        ("delivery_history_id", "INTEGER", 0, None, 1, 0),
        ("delivery_id", "INTEGER", 1, None, 0, 0),
        ("event_type", "TEXT", 1, None, 0, 0),
        ("previous_requirement_status", "TEXT", 0, None, 0, 0),
        ("new_requirement_status", "TEXT", 0, None, 0, 0),
        ("previous_recipient_access", "INTEGER", 0, None, 0, 0),
        ("new_recipient_access", "INTEGER", 0, None, 0, 0),
        ("reason_code", "TEXT", 0, None, 0, 0),
        ("reason_text", "TEXT", 0, None, 0, 0),
        ("changed_by_user_id", "INTEGER", 0, None, 0, 0),
        ("changed_at_utc", "TEXT", 1, None, 0, 0),
    ),
}

EXPECTED_FOREIGN_KEYS = {
    "staff_notices": {
        ("client_id", "clients", "client_id", "NO ACTION"),
        ("replaces_notice_id", "staff_notices", "notice_id", "NO ACTION"),
        ("created_by_user_id", "users", "user_id", "NO ACTION"),
        ("updated_by_user_id", "users", "user_id", "NO ACTION"),
        ("published_by_user_id", "users", "user_id", "NO ACTION"),
        ("withdrawn_by_user_id", "users", "user_id", "NO ACTION"),
        ("replaced_by_user_id", "users", "user_id", "NO ACTION"),
    },
    "staff_notice_audiences": {
        ("notice_id", "staff_notices", "notice_id", "NO ACTION"),
    },
    "staff_notice_audience_rules": {
        ("audience_id", "staff_notice_audiences", "audience_id", "NO ACTION"),
        ("user_id", "users", "user_id", "NO ACTION"),
    },
    "staff_notice_audience_eligibility_periods": {
        ("audience_id", "staff_notice_audiences", "audience_id", "NO ACTION"),
        ("user_id", "users", "user_id", "NO ACTION"),
        ("opened_by_user_id", "users", "user_id", "NO ACTION"),
        ("closed_by_user_id", "users", "user_id", "NO ACTION"),
    },
    "staff_notice_schedules": {
        ("notice_id", "staff_notices", "notice_id", "NO ACTION"),
        ("specific_shift_client_id", "clients", "client_id", "NO ACTION"),
    },
    "staff_notice_schedule_shift_types": {
        ("schedule_id", "staff_notice_schedules", "schedule_id", "NO ACTION"),
    },
    "staff_notice_schedule_weekdays": {
        ("schedule_id", "staff_notice_schedules", "schedule_id", "NO ACTION"),
    },
    "staff_notice_occurrences": {
        ("schedule_id", "staff_notice_schedules", "schedule_id", "NO ACTION"),
        ("planned_client_id", "clients", "client_id", "NO ACTION"),
        ("shift_id", "shifts", "shift_id", "NO ACTION"),
        ("status_changed_by_user_id", "users", "user_id", "NO ACTION"),
    },
    "staff_notice_deliveries": {
        ("occurrence_id", "staff_notice_occurrences", "occurrence_id", "NO ACTION"),
        ("user_id", "users", "user_id", "NO ACTION"),
        ("viewed_by_user_id", "users", "user_id", "NO ACTION"),
        ("status_changed_by_user_id", "users", "user_id", "NO ACTION"),
    },
    "staff_notice_delivery_history": {
        ("delivery_id", "staff_notice_deliveries", "delivery_id", "NO ACTION"),
        ("changed_by_user_id", "users", "user_id", "NO ACTION"),
    },
}

EXPECTED_INDEX_NAMES = {
    "idx_staff_notices_status_effective",
    "idx_staff_notices_client",
    "idx_staff_notices_priority_published",
    "ux_staff_notices_replaces",
    "ux_staff_notice_audience_broad_rule",
    "ux_staff_notice_audience_role",
    "ux_staff_notice_audience_user",
    "idx_staff_notice_audience_rules_audience",
    "idx_staff_notice_audience_rules_user",
    "ux_staff_notice_open_eligibility",
    "idx_staff_notice_eligibility_at_time",
    "idx_staff_notice_eligibility_user",
    "idx_staff_notice_schedule_specific_shift",
    "idx_staff_notice_schedule_recurrence",
    "ux_staff_notice_occurrence_one_time",
    "ux_staff_notice_occurrence_calendar",
    "ux_staff_notice_occurrence_bound_shift",
    "ux_staff_notice_occurrence_specific_shift",
    "idx_staff_notice_occurrence_pending_shift",
    "idx_staff_notice_occurrence_visibility",
    "idx_staff_notice_occurrence_due",
    "idx_staff_notice_delivery_user_access",
    "idx_staff_notice_delivery_occurrence",
    "idx_staff_notice_delivery_viewed",
    "idx_staff_notice_delivery_assigned",
    "idx_staff_notice_delivery_history_delivery",
    "idx_staff_notice_delivery_history_event",
}

# Blueprint-derived independently from the migration's INDEX_SQL mapping.
# Values: owning table, unique, partial, ordered columns, WHERE predicate.
EXPECTED_EXPLICIT_INDEXES = {
    "idx_staff_notices_status_effective": (
        "staff_notices", 0, 0,
        ("status", "effective_start_at_utc", "expires_at_utc"), None,
    ),
    "idx_staff_notices_client": (
        "staff_notices", 0, 0, ("client_id",), None,
    ),
    "idx_staff_notices_priority_published": (
        "staff_notices", 0, 0,
        ("priority", "published_at_utc"), None,
    ),
    "ux_staff_notices_replaces": (
        "staff_notices", 1, 1, ("replaces_notice_id",),
        "replaces_notice_id IS NOT NULL",
    ),
    "ux_staff_notice_audience_broad_rule": (
        "staff_notice_audience_rules", 1, 1,
        ("audience_id", "rule_type"),
        "rule_type IN ('Core Organization', 'All Support Workers', "
        "'Applicable Shift Staff')",
    ),
    "ux_staff_notice_audience_role": (
        "staff_notice_audience_rules", 1, 1,
        ("audience_id", "role_name"), "rule_type = 'Selected Role'",
    ),
    "ux_staff_notice_audience_user": (
        "staff_notice_audience_rules", 1, 1,
        ("audience_id", "user_id"),
        "rule_type = 'Selected Individual'",
    ),
    "idx_staff_notice_audience_rules_audience": (
        "staff_notice_audience_rules", 0, 0, ("audience_id",), None,
    ),
    "idx_staff_notice_audience_rules_user": (
        "staff_notice_audience_rules", 0, 0, ("user_id",), None,
    ),
    "ux_staff_notice_open_eligibility": (
        "staff_notice_audience_eligibility_periods", 1, 1,
        ("audience_id", "user_id"), "eligible_until_at_utc IS NULL",
    ),
    "idx_staff_notice_eligibility_at_time": (
        "staff_notice_audience_eligibility_periods", 0, 0,
        ("audience_id", "eligible_from_at_utc", "eligible_until_at_utc"),
        None,
    ),
    "idx_staff_notice_eligibility_user": (
        "staff_notice_audience_eligibility_periods", 0, 0,
        ("user_id", "eligible_from_at_utc"), None,
    ),
    "idx_staff_notice_schedule_specific_shift": (
        "staff_notice_schedules", 0, 0,
        ("specific_shift_client_id", "specific_shift_date",
         "specific_shift_type"), None,
    ),
    "idx_staff_notice_schedule_recurrence": (
        "staff_notice_schedules", 0, 0,
        ("occurrence_basis", "recurrence_pattern",
         "recurrence_anchor_date"), None,
    ),
    "ux_staff_notice_occurrence_one_time": (
        "staff_notice_occurrences", 1, 1, ("schedule_id",),
        "occurrence_kind = 'One Time'",
    ),
    "ux_staff_notice_occurrence_calendar": (
        "staff_notice_occurrences", 1, 1,
        ("schedule_id", "occurrence_date"),
        "occurrence_kind = 'Calendar'",
    ),
    "ux_staff_notice_occurrence_bound_shift": (
        "staff_notice_occurrences", 1, 1,
        ("schedule_id", "shift_id"),
        "occurrence_kind = 'Shift' AND shift_id IS NOT NULL",
    ),
    "ux_staff_notice_occurrence_specific_shift": (
        "staff_notice_occurrences", 1, 1,
        ("schedule_id", "planned_client_id", "occurrence_date",
         "planned_shift_type"),
        "occurrence_kind = 'Shift' AND is_specific_shift_occurrence = 1",
    ),
    "idx_staff_notice_occurrence_pending_shift": (
        "staff_notice_occurrences", 0, 0,
        ("planned_client_id", "occurrence_date", "planned_shift_type",
         "occurrence_status"), None,
    ),
    "idx_staff_notice_occurrence_visibility": (
        "staff_notice_occurrences", 0, 0,
        ("occurrence_status", "visible_from_at_utc"), None,
    ),
    "idx_staff_notice_occurrence_due": (
        "staff_notice_occurrences", 0, 0, ("due_at_utc",), None,
    ),
    "idx_staff_notice_delivery_user_access": (
        "staff_notice_deliveries", 0, 0,
        ("user_id", "recipient_access", "requirement_status"), None,
    ),
    "idx_staff_notice_delivery_occurrence": (
        "staff_notice_deliveries", 0, 0, ("occurrence_id",), None,
    ),
    "idx_staff_notice_delivery_viewed": (
        "staff_notice_deliveries", 0, 0,
        ("first_viewed_at_utc",), None,
    ),
    "idx_staff_notice_delivery_assigned": (
        "staff_notice_deliveries", 0, 0, ("assigned_at_utc",), None,
    ),
    "idx_staff_notice_delivery_history_delivery": (
        "staff_notice_delivery_history", 0, 0,
        ("delivery_id", "changed_at_utc"), None,
    ),
    "idx_staff_notice_delivery_history_event": (
        "staff_notice_delivery_history", 0, 0,
        ("event_type", "changed_at_utc"), None,
    ),
}

EXPECTED_IMPLICIT_UNIQUES = {
    "staff_notices": set(),
    "staff_notice_audiences": {("notice_id",)},
    "staff_notice_audience_rules": set(),
    "staff_notice_audience_eligibility_periods": set(),
    "staff_notice_schedules": {("notice_id",)},
    "staff_notice_schedule_shift_types": {("schedule_id", "shift_type")},
    "staff_notice_schedule_weekdays": {("schedule_id", "weekday_number")},
    "staff_notice_occurrences": set(),
    "staff_notice_deliveries": {("occurrence_id", "user_id")},
    "staff_notice_delivery_history": set(),
}


def approved_blueprint_table_sql():
    blueprint_path = (
        Path(__file__).resolve().parents[1]
        / "documentation"
        / "Staff Notices V1.0"
        / "Staff_Notices_V1_Final_Technical_Blueprint.docx"
    )
    namespace = {
        "w": (
            "http://schemas.openxmlformats.org/"
            "wordprocessingml/2006/main"
        )
    }

    with zipfile.ZipFile(blueprint_path) as archive:
        document = ElementTree.fromstring(
            archive.read("word/document.xml")
        )

    table_sql = {}
    for paragraph in document.findall(".//w:p", namespace):
        text = "".join(
            node.text or ""
            for node in paragraph.findall(".//w:t", namespace)
        ).strip()

        if text.startswith("CREATE TABLE staff_notice"):
            table_name = text.split("CREATE TABLE ", 1)[1].split(" ", 1)[0]
            table_sql[table_name] = text

    return table_sql


class StaffNoticeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temp_directory.name) / "staff_notice_migration.db"
        )
        self.conn = sqlite3.connect(self.database_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.create_prerequisites()
        migrate_acknowledgements(self.conn)
        self.assertTrue(acknowledgement_schema_is_current(self.conn))

    def tearDown(self):
        self.conn.close()
        self.temp_directory.cleanup()

    def create_prerequisites(self, conn=None, composite_table=None):
        conn = conn or self.conn
        users_key = (
            "user_id INTEGER, tenant_id INTEGER NOT NULL DEFAULT 1"
            if composite_table == "users"
            else "user_id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        users_constraint = (
            ", PRIMARY KEY (user_id, tenant_id)"
            if composite_table == "users"
            else ""
        )
        clients_key = (
            "client_id INTEGER, tenant_id INTEGER NOT NULL DEFAULT 1"
            if composite_table == "clients"
            else "client_id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        clients_constraint = (
            ", PRIMARY KEY (client_id, tenant_id)"
            if composite_table == "clients"
            else ""
        )
        shifts_key = (
            "shift_id INTEGER, tenant_id INTEGER NOT NULL DEFAULT 1"
            if composite_table == "shifts"
            else "shift_id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        shifts_constraint = (
            ", PRIMARY KEY (shift_id, tenant_id)"
            if composite_table == "shifts"
            else ""
        )
        conn.executescript(f"""
            CREATE TABLE users (
                {users_key},
                role TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
                {users_constraint}
            );
            CREATE TABLE clients (
                {clients_key}
                {clients_constraint}
            );
            CREATE TABLE shifts (
                {shifts_key},
                client_id INTEGER NOT NULL,
                shift_date TEXT NOT NULL,
                shift_type TEXT NOT NULL
                {shifts_constraint}
            );
            CREATE TABLE shift_staff (
                shift_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE activity_log (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity_class TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                user_id INTEGER,
                client_id INTEGER,
                shift_id INTEGER,
                related_table TEXT,
                related_id INTEGER,
                summary TEXT NOT NULL,
                details TEXT,
                success INTEGER DEFAULT 1
            );
            CREATE TABLE unrelated_records (
                record_id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE unrelated_autoincrement (
                record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT NOT NULL
            );
            INSERT INTO users (user_id, role, active)
            VALUES (1, 'Admin', 1), (2, 'Support Worker', 1);
            INSERT INTO clients (client_id) VALUES (1);
            INSERT INTO shifts (shift_id, client_id, shift_date, shift_type)
            VALUES (1, 1, '2026-08-01', 'Day');
            INSERT INTO shift_staff (shift_id, user_id, active)
            VALUES (1, 2, 1);
            INSERT INTO activity_log (
                activity_class, activity_type, summary
            ) VALUES ('SYSTEM', 'fixture', 'Preserve me');
            INSERT INTO unrelated_records VALUES (1, 'preserved');
            INSERT INTO unrelated_autoincrement (record_id, value)
            VALUES (5, 'preserved lower row');
            INSERT INTO unrelated_autoincrement (record_id, value)
            VALUES (50, 'advance sequence only');
            DELETE FROM unrelated_autoincrement WHERE record_id = 50;
        """)
        conn.commit()

    def staff_objects(self):
        return self.conn.execute("""
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name LIKE 'staff_notice%'
               OR tbl_name LIKE 'staff_notice%'
            ORDER BY type, name
        """).fetchall()

    def insert_notice(self, **overrides):
        values = {
            "title": "Policy Update",
            "notice_text": "Read this notice.",
            "created_by_user_id": 1,
            "created_at_utc": "2026-08-01T16:00:00Z",
        }
        values.update(overrides)
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = self.conn.execute(
            f"INSERT INTO staff_notices ({columns}) "
            f"VALUES ({placeholders})",
            tuple(values.values()),
        )
        return cursor.lastrowid

    def create_valid_graph(self):
        notice_id = self.insert_notice()
        audience_id = self.conn.execute("""
            INSERT INTO staff_notice_audiences (
                notice_id, created_at_utc
            ) VALUES (?, '2026-08-01T16:00:00Z')
        """, (notice_id,)).lastrowid
        self.conn.execute("""
            INSERT INTO staff_notice_audience_rules (
                audience_id, rule_type, created_at_utc
            ) VALUES (?, 'Core Organization', '2026-08-01T16:00:00Z')
        """, (audience_id,))
        schedule_id = self.conn.execute("""
            INSERT INTO staff_notice_schedules (
                notice_id, occurrence_basis, recurrence_pattern,
                created_at_utc
            ) VALUES (?, 'One Time', 'Once', '2026-08-01T16:00:00Z')
        """, (notice_id,)).lastrowid
        occurrence_id = self.conn.execute("""
            INSERT INTO staff_notice_occurrences (
                schedule_id, occurrence_kind, created_at_utc
            ) VALUES (?, 'One Time', '2026-08-01T16:00:00Z')
        """, (schedule_id,)).lastrowid
        delivery_id = self.conn.execute("""
            INSERT INTO staff_notice_deliveries (
                occurrence_id, user_id, assigned_at_utc,
                eligibility_cutoff_at_utc
            ) VALUES (?, 2, '2026-08-01T16:00:00Z',
                      '2026-08-01T16:00:00Z')
        """, (occurrence_id,)).lastrowid
        return notice_id, audience_id, schedule_id, occurrence_id, delivery_id

    def assert_invalid(self, sql, parameters=()):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(sql, parameters)
        self.conn.rollback()

    def file_hash(self):
        return hashlib.sha256(self.database_path.read_bytes()).hexdigest()

    def schema_snapshot(self, conn=None):
        conn = conn or self.conn
        table_rows = []
        temp_table_rows = []

        for row in conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name <> 'sqlite_sequence'
            ORDER BY name
        """).fetchall():
            table_name = row[0]
            quoted_name = '"' + table_name.replace('"', '""') + '"'
            table_rows.append((
                table_name,
                conn.execute(
                    f"SELECT * FROM {quoted_name} ORDER BY rowid"
                ).fetchall(),
            ))

        for row in conn.execute("""
            SELECT name FROM sqlite_temp_master
            WHERE type = 'table' AND name <> 'sqlite_sequence'
            ORDER BY name
        """).fetchall():
            table_name = row[0]
            quoted_name = '"' + table_name.replace('"', '""') + '"'
            temp_table_rows.append((
                table_name,
                conn.execute(
                    f"SELECT * FROM temp.{quoted_name} ORDER BY rowid"
                ).fetchall(),
            ))

        temp_sequence_exists = conn.execute("""
            SELECT 1 FROM sqlite_temp_master
            WHERE type = 'table' AND name = 'sqlite_sequence'
        """).fetchone()

        return {
            "main_schema": conn.execute("""
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                ORDER BY type, name
            """).fetchall(),
            "temp_schema": conn.execute("""
                SELECT type, name, tbl_name, sql
                FROM sqlite_temp_master
                ORDER BY type, name
            """).fetchall(),
            "sequences": conn.execute("""
                SELECT name, seq FROM sqlite_sequence ORDER BY name
            """).fetchall(),
            "temp_sequences": (
                conn.execute("""
                    SELECT name, seq FROM temp.sqlite_sequence ORDER BY name
                """).fetchall()
                if temp_sequence_exists
                else []
            ),
            "unrelated_records": conn.execute("""
                SELECT * FROM unrelated_records ORDER BY record_id
            """).fetchall(),
            "unrelated_autoincrement": conn.execute("""
                SELECT * FROM unrelated_autoincrement ORDER BY record_id
            """).fetchall(),
            "table_rows": table_rows,
            "temp_table_rows": temp_table_rows,
        }

    def acknowledgement_snapshot(self):
        table_sql = self.conn.execute("""
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'acknowledgements'
        """).fetchone()[0]
        rows = self.conn.execute("""
            SELECT * FROM acknowledgements ORDER BY acknowledgement_id
        """).fetchall()
        sequence = self.conn.execute("""
            SELECT seq FROM sqlite_sequence
            WHERE name = 'acknowledgements'
        """).fetchone()
        indexes = []

        for row in self.conn.execute(
            "PRAGMA index_list(acknowledgements)"
        ).fetchall():
            index_name = row[1]
            index_sql = self.conn.execute("""
                SELECT sql FROM sqlite_master
                WHERE type = 'index' AND name = ?
            """, (index_name,)).fetchone()
            indexes.append((
                row,
                index_sql[0] if index_sql else None,
                self.conn.execute(
                    f'PRAGMA index_xinfo("{index_name}")'
                ).fetchall(),
            ))

        return {
            "table_sql": table_sql,
            "rows": rows,
            "row_count": len(rows),
            "maximum_id": max((row[0] for row in rows), default=None),
            "sequence": sequence,
            "indexes": sorted(indexes, key=lambda item: item[0][1]),
        }

    def test_successful_fresh_migration_creates_all_ten_tables(self):
        self.assertTrue(migration.migrate(self.conn))
        self.assertTrue(migration.schema_is_current(self.conn))
        tables = {
            row[0]
            for row in self.conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name LIKE 'staff_notice%'
            """)
        }
        self.assertEqual(tables, set(migration.TABLE_NAMES))
        self.assertEqual(len(tables), 10)

    def test_unexpected_namespace_objects_are_rejected_before_begin(self):
        cases = (
            (
                "table",
                "CREATE TABLE staff_notice_legacy (legacy_id INTEGER)",
                "DROP TABLE staff_notice_legacy",
            ),
            (
                "view",
                "CREATE VIEW staff_notice_reporting AS "
                "SELECT record_id FROM unrelated_records",
                "DROP VIEW staff_notice_reporting",
            ),
            (
                "index",
                "CREATE INDEX IDX_StAfF_NoTiCe_LeGaCy "
                "ON unrelated_records(value)",
                "DROP INDEX IDX_StAfF_NoTiCe_LeGaCy",
            ),
            (
                "trigger",
                "CREATE TRIGGER STAFF_NOTICE_LEGACY_TRIGGER "
                "AFTER INSERT ON unrelated_records BEGIN SELECT 1; END",
                "DROP TRIGGER STAFF_NOTICE_LEGACY_TRIGGER",
            ),
        )

        for object_type, create_sql, drop_sql in cases:
            with self.subTest(object_type=object_type):
                self.conn.execute(create_sql)
                self.conn.commit()
                before = self.schema_snapshot()
                before_hash = self.file_hash()
                traced_sql = []
                self.conn.set_trace_callback(traced_sql.append)

                try:
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "partial or incompatible",
                    ):
                        migration.migrate(self.conn)
                finally:
                    self.conn.set_trace_callback(None)

                self.assertEqual(self.schema_snapshot(), before)
                self.assertEqual(self.file_hash(), before_hash)
                self.assertFalse(self.conn.in_transaction)
                self.assertEqual(
                    self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
                    1,
                )
                self.assertFalse(any(
                    statement.strip().upper().startswith("BEGIN IMMEDIATE")
                    for statement in traced_sql
                ))
                self.assertEqual(
                    {
                        row[0]
                        for row in self.conn.execute("""
                            SELECT name FROM sqlite_master
                            WHERE type = 'table'
                              AND name IN (
                                  'staff_notices',
                                  'staff_notice_audiences',
                                  'staff_notice_audience_rules',
                                  'staff_notice_audience_eligibility_periods',
                                  'staff_notice_schedules',
                                  'staff_notice_schedule_shift_types',
                                  'staff_notice_schedule_weekdays',
                                  'staff_notice_occurrences',
                                  'staff_notice_deliveries',
                                  'staff_notice_delivery_history'
                              )
                        """).fetchall()
                    },
                    set(),
                )
                self.conn.execute(drop_sql)
                self.conn.commit()

    def test_exact_schema_plus_unexpected_object_is_not_current(self):
        migration.migrate(self.conn)
        self.conn.execute("""
            CREATE VIEW staff_notice_reporting AS
            SELECT notice_id FROM staff_notices
        """)
        self.conn.commit()
        before = self.schema_snapshot()
        before_hash = self.file_hash()
        traced_sql = []
        self.conn.set_trace_callback(traced_sql.append)

        try:
            self.assertFalse(migration.schema_is_current(self.conn))
            with self.assertRaisesRegex(
                RuntimeError,
                "partial or incompatible",
            ):
                migration.migrate(self.conn)
        finally:
            self.conn.set_trace_callback(None)

        self.assertEqual(self.schema_snapshot(), before)
        self.assertEqual(self.file_hash(), before_hash)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        self.assertFalse(any(
            statement.strip().upper().startswith("BEGIN IMMEDIATE")
            for statement in traced_sql
        ))

    def test_temporary_namespace_objects_are_rejected_before_begin(self):
        self.conn.execute("CREATE TEMP TABLE temp_unrelated (value TEXT)")
        self.conn.execute(
            "INSERT INTO temp_unrelated VALUES ('preserve temp data')"
        )
        self.conn.commit()
        cases = (
            (
                "table",
                "CREATE TEMP TABLE staff_notice_legacy (legacy_id INTEGER)",
                "DROP TABLE temp.staff_notice_legacy",
            ),
            (
                "view",
                "CREATE TEMP VIEW staff_notice_reporting AS SELECT 1",
                "DROP VIEW temp.staff_notice_reporting",
            ),
            (
                "index",
                "CREATE INDEX temp.IDX_Staff_Notice_Temp "
                "ON temp_unrelated(value)",
                "DROP INDEX temp.IDX_Staff_Notice_Temp",
            ),
            (
                "trigger",
                "CREATE TEMP TRIGGER staff_notice_temp_trigger "
                "AFTER INSERT ON temp_unrelated BEGIN SELECT 1; END",
                "DROP TRIGGER temp.staff_notice_temp_trigger",
            ),
            (
                "mixed-case table",
                "CREATE TEMP TABLE StAfF_NoTiCe_TeMp_MiXeD (id INTEGER)",
                "DROP TABLE temp.StAfF_NoTiCe_TeMp_MiXeD",
            ),
        )

        for object_type, create_sql, drop_sql in cases:
            with self.subTest(object_type=object_type):
                self.conn.execute(create_sql)
                self.conn.commit()
                discovered = migration.find_staff_notice_objects(self.conn)
                unexpected = migration.unexpected_staff_notice_objects(
                    self.conn
                )
                self.assertTrue(
                    any(row[0] == "temp" for row in discovered),
                    discovered,
                )
                self.assertTrue(
                    any(row[0] == "temp" for row in unexpected),
                    unexpected,
                )
                self.assertFalse(migration.schema_is_current(self.conn))
                before = self.schema_snapshot()
                before_hash = self.file_hash()
                incoming_foreign_keys = self.conn.execute(
                    "PRAGMA foreign_keys"
                ).fetchone()[0]
                traced_sql = []
                self.conn.set_trace_callback(traced_sql.append)

                try:
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "partial or incompatible",
                    ):
                        migration.migrate(self.conn)
                finally:
                    self.conn.set_trace_callback(None)

                self.assertEqual(self.schema_snapshot(), before)
                self.assertEqual(self.file_hash(), before_hash)
                self.assertFalse(self.conn.in_transaction)
                self.assertEqual(
                    self.conn.execute("PRAGMA foreign_keys").fetchone()[0],
                    incoming_foreign_keys,
                )
                self.assertFalse(any(
                    statement.strip().upper().startswith("BEGIN IMMEDIATE")
                    for statement in traced_sql
                ))
                self.assertEqual(
                    {
                        row[0]
                        for row in self.conn.execute("""
                            SELECT name FROM sqlite_master
                            WHERE type = 'table'
                              AND name IN (
                                  'staff_notices',
                                  'staff_notice_audiences',
                                  'staff_notice_audience_rules',
                                  'staff_notice_audience_eligibility_periods',
                                  'staff_notice_schedules',
                                  'staff_notice_schedule_shift_types',
                                  'staff_notice_schedule_weekdays',
                                  'staff_notice_occurrences',
                                  'staff_notice_deliveries',
                                  'staff_notice_delivery_history'
                              )
                        """).fetchall()
                    },
                    set(),
                )
                self.conn.execute(drop_sql)
                self.conn.commit()

    def test_exact_main_schema_plus_temporary_object_is_not_current(self):
        migration.migrate(self.conn)
        self.conn.execute("""
            CREATE TEMP VIEW staff_notice_reporting AS SELECT 1
        """)
        self.conn.commit()
        before = self.schema_snapshot()
        before_hash = self.file_hash()
        self.assertFalse(migration.schema_is_current(self.conn))
        self.assertTrue(any(
            row[0] == "temp"
            for row in migration.unexpected_staff_notice_objects(self.conn)
        ))
        traced_sql = []
        self.conn.set_trace_callback(traced_sql.append)

        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "partial or incompatible",
            ):
                migration.migrate(self.conn)
        finally:
            self.conn.set_trace_callback(None)

        self.assertEqual(self.schema_snapshot(), before)
        self.assertEqual(self.file_hash(), before_hash)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        self.assertFalse(any(
            statement.strip().upper().startswith("BEGIN IMMEDIATE")
            for statement in traced_sql
        ))

    def test_exact_columns_types_defaults_keys_and_autoincrement(self):
        migration.migrate(self.conn)

        for table_name, expected_columns in EXPECTED_COLUMNS.items():
            columns = self.conn.execute(
                f'PRAGMA table_xinfo("{table_name}")'
            ).fetchall()
            self.assertEqual(
                tuple(row[0] for row in columns),
                tuple(range(len(expected_columns))),
                table_name,
            )
            self.assertEqual(
                tuple(row[1:7] for row in columns),
                expected_columns,
                table_name,
            )
            table_sql = self.conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()[0]
            self.assertIn("AUTOINCREMENT", table_sql.upper())
            self.assertEqual(
                sql_token_signature(table_sql),
                sql_token_signature(BLUEPRINT_TABLE_SQL[table_name]),
                table_name,
            )

            implicit_uniques = set()
            for index_row in self.conn.execute(
                f'PRAGMA index_list("{table_name}")'
            ).fetchall():
                if index_row[3] == "u":
                    index_name = index_row[1]
                    implicit_uniques.add(tuple(
                        row[2]
                        for row in self.conn.execute(
                            f'PRAGMA index_xinfo("{index_name}")'
                        ).fetchall()
                        if row[5] == 1
                    ))
            self.assertEqual(
                implicit_uniques,
                EXPECTED_IMPLICIT_UNIQUES[table_name],
                table_name,
            )

        priority = next(
            column
            for column in EXPECTED_COLUMNS["staff_notices"]
            if column[0] == "priority"
        )
        self.assertEqual(priority[3], "'Normal'")
        actual_priority = self.conn.execute(
            "PRAGMA table_xinfo(staff_notices)"
        ).fetchall()[3]
        self.assertEqual(actual_priority[4], "'Normal'")

    def test_blueprint_table_fixture_rejects_changed_implementation_default(self):
        self.assertEqual(
            set(BLUEPRINT_TABLE_SQL),
            set(EXPECTED_COLUMNS),
        )
        approved = BLUEPRINT_TABLE_SQL["staff_notices"]
        mutated_implementation = migration.TABLE_SQL[
            "staff_notices"
        ].replace(
            "DEFAULT 'Normal'",
            "DEFAULT 'Urgent'",
            1,
        )
        self.assertNotEqual(
            sql_token_signature(mutated_implementation),
            sql_token_signature(approved),
        )
        self.assertEqual(
            sql_token_signature(migration.TABLE_SQL["staff_notices"]),
            sql_token_signature(approved),
        )

    def test_canonical_table_fixtures_match_the_approved_docx(self):
        docx_table_sql = approved_blueprint_table_sql()
        self.assertEqual(
            set(docx_table_sql),
            set(BLUEPRINT_TABLE_SQL),
        )

        for table_name, expected_sql in BLUEPRINT_TABLE_SQL.items():
            with self.subTest(table_name=table_name):
                self.assertEqual(
                    sql_token_signature(docx_table_sql[table_name]),
                    sql_token_signature(expected_sql),
                )

    def test_every_foreign_key_and_delete_action(self):
        migration.migrate(self.conn)

        for table_name, expected in EXPECTED_FOREIGN_KEYS.items():
            actual = {
                (row[3], row[2], row[4], row[6])
                for row in self.conn.execute(
                    f'PRAGMA foreign_key_list("{table_name}")'
                ).fetchall()
            }
            self.assertEqual(actual, expected, table_name)

    def test_every_index_has_exact_generated_semantics(self):
        migration.migrate(self.conn)
        actual_names = set()
        for table_name in EXPECTED_COLUMNS:
            actual_names.update(
                row[1]
                for row in self.conn.execute(
                    f'PRAGMA index_list("{table_name}")'
                ).fetchall()
                if row[3] == "c"
            )
        self.assertEqual(actual_names, EXPECTED_INDEX_NAMES)
        self.assertEqual(
            set(EXPECTED_EXPLICIT_INDEXES),
            EXPECTED_INDEX_NAMES,
        )

        for index_name, expected in EXPECTED_EXPLICIT_INDEXES.items():
            table_name, unique, partial, columns, predicate = expected
            master_row = self.conn.execute("""
                SELECT tbl_name, sql FROM sqlite_master
                WHERE type = 'index' AND name = ?
            """, (index_name,)).fetchone()
            self.assertIsNotNone(master_row, index_name)
            self.assertEqual(master_row[0], table_name, index_name)

            list_row = next(
                row
                for row in self.conn.execute(
                    f'PRAGMA index_list("{table_name}")'
                ).fetchall()
                if row[1] == index_name
            )
            self.assertEqual(list_row[2], unique, index_name)
            self.assertEqual(list_row[3], "c", index_name)
            self.assertEqual(list_row[4], partial, index_name)

            key_rows = tuple(
                row
                for row in self.conn.execute(
                    f'PRAGMA index_xinfo("{index_name}")'
                ).fetchall()
                if row[5] == 1
            )
            self.assertEqual(
                tuple(row[2] for row in key_rows),
                columns,
                index_name,
            )
            self.assertEqual(
                tuple(row[3] for row in key_rows),
                (0,) * len(columns),
                index_name,
            )
            self.assertEqual(
                tuple(row[4] for row in key_rows),
                ("BINARY",) * len(columns),
                index_name,
            )
            self.assertTrue(
                all(row[1] >= 0 and row[2] is not None for row in key_rows),
                index_name,
            )

            signature = sql_token_signature(master_row[1])
            where_token = ("word", "where")
            actual_predicate = (
                signature[signature.index(where_token) + 1:]
                if where_token in signature
                else None
            )
            expected_predicate = (
                sql_token_signature(predicate)
                if predicate is not None
                else None
            )
            self.assertEqual(
                actual_predicate,
                expected_predicate,
                index_name,
            )

    def test_enum_constraints_reject_invalid_values(self):
        migration.migrate(self.conn)
        notice_id, audience_id, schedule_id, occurrence_id, delivery_id = (
            self.create_valid_graph()
        )
        self.conn.commit()
        invalid_statements = (
            ("UPDATE staff_notices SET priority='Bad' WHERE notice_id=?", (notice_id,)),
            ("UPDATE staff_notices SET status='Bad' WHERE notice_id=?", (notice_id,)),
            ("INSERT INTO staff_notice_audience_rules (audience_id,rule_type,created_at_utc) VALUES (?,'Bad','t')", (audience_id,)),
            ("UPDATE staff_notice_schedules SET occurrence_basis='Bad' WHERE schedule_id=?", (schedule_id,)),
            ("UPDATE staff_notice_schedules SET recurrence_pattern='Bad' WHERE schedule_id=?", (schedule_id,)),
            ("UPDATE staff_notice_schedules SET shift_applicability='Bad' WHERE schedule_id=?", (schedule_id,)),
            ("INSERT INTO staff_notice_schedule_shift_types (schedule_id,shift_type) VALUES (?,'Bad')", (schedule_id,)),
            ("UPDATE staff_notice_occurrences SET occurrence_kind='Bad' WHERE occurrence_id=?", (occurrence_id,)),
            ("UPDATE staff_notice_occurrences SET planned_shift_type='Bad' WHERE occurrence_id=?", (occurrence_id,)),
            ("UPDATE staff_notice_occurrences SET occurrence_status='Bad' WHERE occurrence_id=?", (occurrence_id,)),
            ("UPDATE staff_notice_deliveries SET requirement_status='Bad' WHERE delivery_id=?", (delivery_id,)),
            ("INSERT INTO staff_notice_delivery_history (delivery_id,event_type,changed_at_utc) VALUES (?,'Bad','t')", (delivery_id,)),
        )

        for sql, parameters in invalid_statements:
            with self.subTest(sql=sql):
                self.assert_invalid(sql, parameters)

    def test_boolean_and_weekday_constraints_reject_invalid_values(self):
        migration.migrate(self.conn)
        notice_id, _, schedule_id, occurrence_id, delivery_id = self.create_valid_graph()
        self.conn.commit()
        statements = (
            ("UPDATE staff_notices SET draft_active=2 WHERE notice_id=?", (notice_id,)),
            ("UPDATE staff_notices SET until_withdrawn=2 WHERE notice_id=?", (notice_id,)),
            ("INSERT INTO staff_notice_schedule_weekdays (schedule_id,weekday_number) VALUES (?,7)", (schedule_id,)),
            ("UPDATE staff_notice_occurrences SET due_at_is_provisional=2 WHERE occurrence_id=?", (occurrence_id,)),
            ("UPDATE staff_notice_occurrences SET is_specific_shift_occurrence=2 WHERE occurrence_id=?", (occurrence_id,)),
            ("UPDATE staff_notice_deliveries SET recipient_access=2 WHERE delivery_id=?", (delivery_id,)),
            ("INSERT INTO staff_notice_delivery_history (delivery_id,event_type,previous_recipient_access,changed_at_utc) VALUES (?,'Assigned',2,'t')", (delivery_id,)),
            ("INSERT INTO staff_notice_delivery_history (delivery_id,event_type,new_recipient_access,changed_at_utc) VALUES (?,'Assigned',2,'t')", (delivery_id,)),
        )

        for sql, parameters in statements:
            with self.subTest(sql=sql):
                self.assert_invalid(sql, parameters)

    def test_required_date_and_status_combinations_are_enforced(self):
        migration.migrate(self.conn)
        notice_id, audience_id, schedule_id, _, _ = self.create_valid_graph()
        self.conn.commit()
        calendar_notice_id = self.insert_notice(title="Calendar")
        self.conn.commit()
        statements = (
            ("UPDATE staff_notices SET status='Published' WHERE notice_id=?", (notice_id,)),
            ("UPDATE staff_notices SET effective_start_at_utc='z', expires_at_utc='a' WHERE notice_id=?", (notice_id,)),
            ("INSERT INTO staff_notice_audience_eligibility_periods (audience_id,user_id,eligible_from_at_utc,eligible_until_at_utc,eligibility_source_summary,created_at_utc) VALUES (?,2,'z','a','x','t')", (audience_id,)),
            ("INSERT INTO staff_notice_schedules (notice_id,occurrence_basis,recurrence_pattern,created_at_utc) VALUES (?,'Calendar','Once','t')", (calendar_notice_id,)),
            ("INSERT INTO staff_notice_occurrences (schedule_id,occurrence_kind,created_at_utc) VALUES (?,'Calendar','t')", (schedule_id,)),
            ("INSERT INTO staff_notice_occurrences (schedule_id,occurrence_kind,occurrence_date,created_at_utc) VALUES (?,'Shift','2026-08-01','t')", (schedule_id,)),
        )

        for sql, parameters in statements:
            with self.subTest(sql=sql):
                self.assert_invalid(sql, parameters)

    def test_notice_lifecycle_constraints_are_enforced(self):
        migration.migrate(self.conn)
        notice_id = self.insert_notice()
        self.conn.commit()

        invalid_updates = (
            (
                "UPDATE staff_notices SET version_number=0 "
                "WHERE notice_id=?",
                (notice_id,),
            ),
            (
                "UPDATE staff_notices SET status='Published', "
                "effective_start_at_utc='2026-08-01T00:00:00Z', "
                "published_at_utc='2026-08-01T00:00:00Z', "
                "until_withdrawn=1, "
                "expires_at_utc='2026-08-02T00:00:00Z' "
                "WHERE notice_id=?",
                (notice_id,),
            ),
            (
                "UPDATE staff_notices SET status='Published', "
                "effective_start_at_utc='2026-08-01T00:00:00Z', "
                "published_at_utc='2026-08-01T00:00:00Z', "
                "until_withdrawn=0, expires_at_utc=NULL "
                "WHERE notice_id=?",
                (notice_id,),
            ),
        )
        for sql, parameters in invalid_updates:
            with self.subTest(sql=sql):
                self.assert_invalid(sql, parameters)

        ongoing_id = self.insert_notice(
            title="Ongoing",
            status="Published",
            effective_start_at_utc="2026-08-01T00:00:00Z",
            until_withdrawn=1,
            published_at_utc="2026-08-01T00:00:00Z",
        )
        finite_id = self.insert_notice(
            title="Finite",
            status="Published",
            effective_start_at_utc="2026-08-01T00:00:00Z",
            expires_at_utc="2026-08-02T00:00:00Z",
            until_withdrawn=0,
            published_at_utc="2026-08-01T00:00:00Z",
        )
        self.conn.commit()
        self.assertGreater(ongoing_id, notice_id)
        self.assertGreater(finite_id, ongoing_id)

    def test_audience_rule_payload_constraints_are_enforced(self):
        migration.migrate(self.conn)
        _, audience_id, _, _, _ = self.create_valid_graph()
        self.conn.commit()
        statements = (
            ("Selected Role", None, None),
            ("Selected Role", "Support Worker", 2),
            ("Selected Individual", None, None),
            ("Selected Individual", "Support Worker", 2),
            ("Core Organization", "Support Worker", None),
            ("All Support Workers", None, 2),
            ("Applicable Shift Staff", "Support Worker", None),
        )

        for rule_type, role_name, user_id in statements:
            with self.subTest(
                rule_type=rule_type,
                role_name=role_name,
                user_id=user_id,
            ):
                self.assert_invalid("""
                    INSERT INTO staff_notice_audience_rules (
                        audience_id, rule_type, role_name, user_id,
                        created_at_utc
                    ) VALUES (?, ?, ?, ?, 't')
                """, (audience_id, rule_type, role_name, user_id))

    def test_schedule_cross_field_constraints_are_enforced(self):
        migration.migrate(self.conn)
        notice_id = self.insert_notice()
        self.conn.commit()
        statements = (
            (
                "Calendar", "Interval Days", "None", 1,
                None, None, None, None,
            ),
            (
                "Calendar", "Daily", "None", 2,
                None, None, None, None,
            ),
            (
                "One Time", "Daily", "None", None,
                None, None, None, None,
            ),
            (
                "One Time", "Once", "Every Shift", None,
                None, None, None, None,
            ),
            (
                "One Time", "Once", "None", None,
                "2026-08-01", None, None, None,
            ),
            (
                "Calendar", "Daily", "Every Shift", None,
                None, None, None, None,
            ),
            (
                "Shift", "Daily", "None", None,
                None, None, None, None,
            ),
            (
                "Shift", "Once", "Specific Shift", None,
                None, None, None, None,
            ),
            (
                "Shift", "Daily", "Specific Shift", None,
                None, 1, "2026-08-01", "Day",
            ),
            (
                "Calendar", "Daily", "None", None,
                "2026-08-01", None, None, None,
            ),
            (
                "Shift", "Once", "Specific Shift", None,
                None, 1, "2026-08-01", "Bad",
            ),
        )

        for (
            occurrence_basis,
            recurrence_pattern,
            shift_applicability,
            interval_days,
            specific_calendar_date,
            specific_shift_client_id,
            specific_shift_date,
            specific_shift_type,
        ) in statements:
            with self.subTest(
                occurrence_basis=occurrence_basis,
                recurrence_pattern=recurrence_pattern,
                shift_applicability=shift_applicability,
                interval_days=interval_days,
            ):
                self.assert_invalid("""
                    INSERT INTO staff_notice_schedules (
                        notice_id, occurrence_basis, recurrence_pattern,
                        shift_applicability, interval_days,
                        specific_calendar_date,
                        specific_shift_client_id, specific_shift_date,
                        specific_shift_type, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 't')
                """, (
                    notice_id,
                    occurrence_basis,
                    recurrence_pattern,
                    shift_applicability,
                    interval_days,
                    specific_calendar_date,
                    specific_shift_client_id,
                    specific_shift_date,
                    specific_shift_type,
                ))

    def test_specific_shift_occurrence_constraint_is_enforced(self):
        migration.migrate(self.conn)
        _, _, _, occurrence_id, _ = self.create_valid_graph()
        self.conn.commit()
        self.assert_invalid(
            "UPDATE staff_notice_occurrences "
            "SET is_specific_shift_occurrence=1 WHERE occurrence_id=?",
            (occurrence_id,),
        )

    def test_view_identity_pairing_constraint_is_enforced(self):
        migration.migrate(self.conn)
        _, _, _, _, delivery_id = self.create_valid_graph()
        self.conn.commit()
        self.assert_invalid(
            "UPDATE staff_notice_deliveries SET first_viewed_at_utc='t' "
            "WHERE delivery_id=?",
            (delivery_id,),
        )
        self.assert_invalid(
            "UPDATE staff_notice_deliveries SET viewed_by_user_id=2 "
            "WHERE delivery_id=?",
            (delivery_id,),
        )

    def test_foreign_keys_reject_broken_references(self):
        migration.migrate(self.conn)
        self.assert_invalid("""
            INSERT INTO staff_notice_audiences (notice_id, created_at_utc)
            VALUES (999, 't')
        """)

    def test_uniqueness_constraints_and_partial_indexes(self):
        migration.migrate(self.conn)
        notice_id, audience_id, schedule_id, occurrence_id, _ = self.create_valid_graph()
        self.conn.execute("""
            INSERT INTO staff_notice_audience_rules (
                audience_id, rule_type, created_at_utc
            ) VALUES (?, 'All Support Workers', 't')
        """, (audience_id,))
        self.conn.commit()
        self.assert_invalid(
            "INSERT INTO staff_notice_audience_rules (audience_id,rule_type,created_at_utc) VALUES (?,'All Support Workers','t')",
            (audience_id,),
        )
        self.assert_invalid(
            "INSERT INTO staff_notice_deliveries (occurrence_id,user_id,assigned_at_utc,eligibility_cutoff_at_utc) VALUES (?,2,'t','t')",
            (occurrence_id,),
        )
        self.assert_invalid(
            "INSERT INTO staff_notice_occurrences (schedule_id,occurrence_kind,created_at_utc) VALUES (?,'One Time','t')",
            (schedule_id,),
        )
        replacement = self.insert_notice(replaces_notice_id=notice_id)
        self.conn.commit()
        self.assertGreater(replacement, notice_id)
        self.assert_invalid(
            "INSERT INTO staff_notices (title,notice_text,replaces_notice_id,created_by_user_id,created_at_utc) VALUES ('x','x',?,1,'t')",
            (notice_id,),
        )

    def test_second_run_is_byte_for_byte_no_op(self):
        self.assertTrue(migration.migrate(self.conn))
        before_schema = self.staff_objects()
        before_hash = self.file_hash()
        self.assertFalse(migration.migrate(self.conn))
        self.assertEqual(self.staff_objects(), before_schema)
        self.assertEqual(self.file_hash(), before_hash)
        self.assertFalse(self.conn.in_transaction)

    def test_forced_partial_creation_failure_rolls_back_every_object(self):
        def fail_partway(conn):
            for sql in migration.TABLE_SQL.values():
                conn.execute(sql)
            for sql in tuple(migration.INDEX_SQL.values())[:7]:
                conn.execute(sql)
            raise RuntimeError("forced creation failure")

        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.assertEqual(
            self.conn.execute("""
                SELECT record_id, value
                FROM unrelated_autoincrement
                ORDER BY record_id
            """).fetchall(),
            [(5, "preserved lower row")],
        )
        self.assertEqual(
            self.conn.execute("""
                SELECT seq FROM sqlite_sequence
                WHERE name = 'unrelated_autoincrement'
            """).fetchone(),
            (50,),
        )
        self.assertLess(
            self.conn.execute("""
                SELECT MAX(record_id) FROM unrelated_autoincrement
            """).fetchone()[0],
            50,
        )
        before = self.schema_snapshot()
        with mock.patch.object(
            migration, "create_schema", side_effect=fail_partway
        ):
            with self.assertRaisesRegex(RuntimeError, "forced creation"):
                migration.migrate(self.conn)

        self.assertEqual(self.schema_snapshot(), before)
        self.assertEqual(self.staff_objects(), [])
        self.assertFalse(any(
            name.casefold().startswith("staff_notice")
            for name, _ in self.conn.execute(
                "SELECT name, seq FROM sqlite_sequence"
            ).fetchall()
        ))
        self.assertEqual(
            self.conn.execute("""
                SELECT record_id, value
                FROM unrelated_autoincrement
                ORDER BY record_id
            """).fetchall(),
            [(5, "preserved lower row")],
        )
        self.assertEqual(
            self.conn.execute("""
                SELECT seq FROM sqlite_sequence
                WHERE name = 'unrelated_autoincrement'
            """).fetchone(),
            (50,),
        )
        self.assertLess(
            self.conn.execute("""
                SELECT MAX(record_id) FROM unrelated_autoincrement
            """).fetchone()[0],
            50,
        )
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 0
        )

    def test_final_database_check_failure_rolls_back_entire_schema(self):
        self.conn.execute("PRAGMA foreign_keys = OFF")

        with mock.patch.object(
            migration,
            "run_database_checks",
            side_effect=RuntimeError("forced final check failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced final check"):
                migration.migrate(self.conn)

        self.assertEqual(self.staff_objects(), [])
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 0
        )

    def test_partial_preexisting_schema_is_rejected_unchanged(self):
        self.conn.execute(migration.TABLE_SQL["staff_notices"])
        self.conn.commit()
        before = self.staff_objects()

        with self.assertRaisesRegex(RuntimeError, "partial or incompatible"):
            migration.migrate(self.conn)

        self.assertEqual(self.staff_objects(), before)
        self.assertFalse(self.conn.in_transaction)

    def test_approved_index_name_on_other_table_is_rejected(self):
        self.conn.execute(
            "CREATE INDEX idx_staff_notices_client "
            "ON unrelated_records(value)"
        )
        self.conn.commit()
        before = self.staff_objects()

        with self.assertRaisesRegex(RuntimeError, "partial or incompatible"):
            migration.migrate(self.conn)

        self.assertEqual(self.staff_objects(), before)

    def test_complete_schema_with_incompatible_index_is_rejected_unchanged(self):
        migration.create_schema(self.conn)
        self.conn.execute("DROP INDEX idx_staff_notices_client")
        self.conn.execute(
            "CREATE INDEX idx_staff_notices_client "
            "ON staff_notices(title)"
        )
        self.conn.commit()
        before_objects = self.staff_objects()
        before_hash = self.file_hash()

        with self.assertRaisesRegex(RuntimeError, "partial or incompatible"):
            migration.migrate(self.conn)

        self.assertEqual(self.staff_objects(), before_objects)
        self.assertEqual(self.file_hash(), before_hash)
        self.assertFalse(self.conn.in_transaction)

    def test_unrelated_existing_data_and_acknowledgements_are_preserved(self):
        self.conn.execute("""
            INSERT INTO acknowledgements (
                acknowledgement_id, source_table, source_id, user_id,
                acknowledged_at, comment, acknowledgement_type, active
            ) VALUES (
                100, 'staff_notice_deliveries', 10, 1,
                '2026-08-01 16:00:00', 'Active acknowledgement',
                'Acknowledgement', 1
            )
        """)
        self.conn.execute("""
            INSERT INTO acknowledgements (
                acknowledgement_id, source_table, source_id, user_id,
                acknowledged_at, comment, acknowledgement_type, active,
                invalidated_at_utc, invalidated_by_user_id,
                invalidation_reason
            ) VALUES (
                125, 'staff_notice_deliveries', 10, 1,
                '2026-08-01 15:00:00', 'Historical acknowledgement',
                'Acknowledgement', 0, '2026-08-01T17:00:00Z', 1,
                'Delivery was reassigned'
            )
        """)
        self.conn.commit()
        acknowledgement_before = self.acknowledgement_snapshot()
        migration.migrate(self.conn)
        acknowledgement_after = self.acknowledgement_snapshot()

        self.assertEqual(
            acknowledgement_after,
            acknowledgement_before,
        )
        self.assertEqual(acknowledgement_after["row_count"], 2)
        self.assertEqual(acknowledgement_after["maximum_id"], 125)
        self.assertEqual(acknowledgement_after["sequence"], (125,))
        self.assertEqual(
            self.conn.execute(
                "SELECT value FROM unrelated_records WHERE record_id=1"
            ).fetchone()[0],
            "preserved",
        )
        self.assertTrue(acknowledgement_schema_is_current(self.conn))

    def test_detailed_foreign_key_check_failure_reporting(self):
        migration.migrate(self.conn)
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.execute("""
            INSERT INTO staff_notice_audiences (notice_id, created_at_utc)
            VALUES (999, 't')
        """)
        self.conn.commit()

        with self.assertRaisesRegex(
            RuntimeError,
            "staff_notice_audiences.*staff_notices",
        ):
            migration.verify_database(self.conn)

    def test_verifier_is_read_only_and_does_not_commit(self):
        migration.migrate(self.conn)
        self.conn.execute(
            "INSERT INTO unrelated_records VALUES (2, 'uncommitted')"
        )
        self.assertTrue(self.conn.in_transaction)
        migration.verify_database(self.conn)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertIsNone(
            self.conn.execute(
                "SELECT 1 FROM unrelated_records WHERE record_id=2"
            ).fetchone()
        )

    def test_foreign_keys_on_remain_on_after_success(self):
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        self.assertTrue(migration.migrate(self.conn))
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        self.assertFalse(self.conn.in_transaction)

    def test_foreign_keys_on_remain_on_after_forced_failure(self):
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        with mock.patch.object(
            migration,
            "create_schema",
            side_effect=RuntimeError("forced failure with foreign keys on"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced failure"):
                migration.migrate(self.conn)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        self.assertFalse(self.conn.in_transaction)

    def test_foreign_keys_off_remain_off_after_success(self):
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.assertTrue(migration.migrate(self.conn))
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 0
        )
        self.assertFalse(self.conn.in_transaction)

    def test_foreign_keys_off_remain_off_after_no_op(self):
        self.assertTrue(migration.migrate(self.conn))
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.assertFalse(migration.migrate(self.conn))
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 0
        )
        self.assertFalse(self.conn.in_transaction)

    def test_verify_database_preserves_foreign_keys_on(self):
        migration.migrate(self.conn)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        migration.verify_database(self.conn)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )
        self.assertFalse(self.conn.in_transaction)

    def test_verify_database_preserves_foreign_keys_off(self):
        migration.migrate(self.conn)
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 0
        )
        migration.verify_database(self.conn)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 0
        )
        self.assertFalse(self.conn.in_transaction)

    def test_missing_prerequisite_is_rejected_without_schema(self):
        self.conn.execute("DROP TABLE activity_log")
        self.conn.commit()

        with self.assertRaisesRegex(RuntimeError, "activity_log"):
            migration.migrate(self.conn)

        self.assertEqual(self.staff_objects(), [])

    def test_composite_prerequisite_keys_are_rejected_before_begin(self):
        for table_name in ("users", "clients", "shifts"):
            with self.subTest(table_name=table_name):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / f"composite_{table_name}.db"
                    conn = sqlite3.connect(path)

                    try:
                        conn.execute("PRAGMA foreign_keys = ON")
                        if table_name == "users":
                            self.create_prerequisites(conn=conn)
                            migrate_acknowledgements(conn)
                            conn.execute("PRAGMA foreign_keys = OFF")
                            conn.execute("DROP TABLE users")
                            conn.executescript("""
                                CREATE TABLE users (
                                    user_id INTEGER,
                                    tenant_id INTEGER NOT NULL DEFAULT 1,
                                    role TEXT NOT NULL,
                                    active INTEGER NOT NULL DEFAULT 1,
                                    PRIMARY KEY (user_id, tenant_id)
                                );
                                INSERT INTO users (
                                    user_id, tenant_id, role, active
                                ) VALUES
                                    (1, 1, 'Admin', 1),
                                    (2, 1, 'Support Worker', 1);
                            """)
                            conn.commit()
                            conn.execute("PRAGMA foreign_keys = ON")
                        else:
                            self.create_prerequisites(
                                conn=conn,
                                composite_table=table_name,
                            )
                            migrate_acknowledgements(conn)
                        before = self.schema_snapshot(conn)
                        before_hash = hashlib.sha256(
                            path.read_bytes()
                        ).hexdigest()
                        traced_sql = []
                        conn.set_trace_callback(traced_sql.append)

                        try:
                            with self.assertRaisesRegex(
                                RuntimeError,
                                "single-column primary key",
                            ):
                                migration.migrate(conn)
                        finally:
                            conn.set_trace_callback(None)

                        self.assertEqual(self.schema_snapshot(conn), before)
                        self.assertEqual(
                            hashlib.sha256(path.read_bytes()).hexdigest(),
                            before_hash,
                        )
                        self.assertFalse(conn.in_transaction)
                        self.assertEqual(
                            conn.execute("PRAGMA foreign_keys").fetchone()[0],
                            1,
                        )
                        self.assertFalse(any(
                            statement.strip().upper().startswith(
                                "BEGIN IMMEDIATE"
                            )
                            for statement in traced_sql
                        ))
                        self.assertEqual(
                            migration.find_staff_notice_objects(conn),
                            (),
                        )
                    finally:
                        conn.close()

    def test_import_does_not_create_or_migrate_a_database(self):
        import_directory = Path(self.temp_directory.name) / "import_only"
        import_directory.mkdir()
        environment = os.environ.copy()
        repository = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = repository
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "import add_staff_notices_tables",
            ],
            cwd=import_directory,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((import_directory / "nhpsg.db").exists())


if __name__ == "__main__":
    unittest.main()
