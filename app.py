#####################################################################
# IMPORTS / APPLICATION CONFIGURATION
#####################################################################

from flask import (
    Flask,
    has_request_context,
    render_template,
    request,
    redirect,
    session,
    url_for
)
from collections.abc import Mapping
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os
import time

app = Flask(__name__)
app.secret_key = "change-this-later"

#
# Session Security Framework
#

SESSION_TIMEOUT_SECONDS = 10 * 60
SESSION_WARNING_SECONDS = 60


@app.before_request
def enforce_session_timeout():

    if "user_id" not in session:
        return None

    if request.endpoint in [
        "static",
        "login",
        "logout",
        "session_timeout"
    ]:
        return None

    current_time = time.time()
    last_activity = session.get("last_activity")

    if last_activity is not None:

        inactive_seconds = current_time - last_activity

        if inactive_seconds >= SESSION_TIMEOUT_SECONDS:

            user_id = session.get("user_id")
            full_name = session.get("full_name")

            conn = get_db()

            log_activity(
                conn,
                activity_class="LOGIN",
                activity_type="session_timeout",
                summary=f"User session timed out: {full_name}",
                user_id=user_id,
                success=1
            )

            conn.commit()
            conn.close()

            session.clear()

            return redirect(
                url_for(
                    "login",
                    timeout=1
                )
            )

    session["last_activity"] = current_time

    return None

@app.route("/session/keep-alive", methods=["POST"])
def session_keep_alive():

    if "user_id" not in session:
        return {
            "success": False,
            "expired": True
        }, 401

    session["last_activity"] = time.time()

    return {
        "success": True
    }

@app.route("/session/timeout", methods=["POST"])
def session_timeout():

    if "user_id" not in session:
        return {
            "success": True,
            "already_logged_out": True
        }

    user_id = session.get("user_id")
    full_name = session.get("full_name")

    conn = get_db()

    log_activity(
        conn,
        activity_class="LOGIN",
        activity_type="session_timeout",
        summary=f"User session timed out: {full_name}",
        user_id=user_id,
        success=1
    )

    conn.commit()
    conn.close()

    session.clear()

    return {
        "success": True,
        "redirect_url": url_for(
            "login",
            timeout=1
        )
    }

@app.context_processor
def inject_session_timeout_settings():

    return {
        "session_timeout_seconds": SESSION_TIMEOUT_SECONDS,
        "session_warning_seconds": SESSION_WARNING_SECONDS
    }

DB_NAME = "nhpsg.db"

#####################################################################
# DATABASE & CORE HELPER FUNCTIONS
#####################################################################

def get_db():
    print("Using database:", os.path.abspath(DB_NAME))
    conn = sqlite3.connect(DB_NAME)

    error_message = (
        "SQLite foreign-key enforcement could not be enabled or "
        "verified."
    )

    try:
        conn.execute("PRAGMA foreign_keys = ON")
        foreign_keys = conn.execute(
            "PRAGMA foreign_keys"
        ).fetchone()

        if (
            foreign_keys is None
            or type(foreign_keys[0]) is not int
            or foreign_keys[0] != 1
            or conn.in_transaction is not False
        ):
            raise RuntimeError(error_message)

        conn.row_factory = sqlite3.Row
        return conn

    except Exception as error:
        try:
            conn.close()
        finally:
            if (
                isinstance(error, RuntimeError)
                and str(error) == error_message
            ):
                raise

            raise RuntimeError(error_message) from error

def log_activity(
    conn,
    activity_class,
    activity_type,
    summary,
    user_id=None,
    client_id=None,
    shift_id=None,
    related_table=None,
    related_id=None,
    details=None,
    success=1
):
    
    local_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("""
        INSERT INTO activity_log
        (
            activity_datetime,
            activity_class,
            activity_type,
            user_id,
            client_id,
            shift_id,
            related_table,
            related_id,
            summary,
            details,
            success
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        local_now,
        activity_class,
        activity_type,
        user_id,
        client_id,
        shift_id,
        related_table,
        related_id,
        summary,
        details,
        success
    ))

#####################################################################
# STAFF NOTICES FOUNDATION HELPERS
#####################################################################

STAFF_NOTICE_MANAGEMENT_ROLES = frozenset({
    "Admin",
    "Program Manager",
    "Director"
})

STAFF_NOTICE_SELECTABLE_ROLES = frozenset({
    "Admin",
    "Program Manager",
    "Director",
    "Support Worker",
    "Behaviour Consultant"
})

STAFF_NOTICE_PRIORITIES = frozenset({
    "Normal",
    "Important",
    "Urgent"
})

STAFF_NOTICE_AUDIENCE_RULE_TYPES = frozenset({
    "Core Organization",
    "All Support Workers",
    "Selected Role",
    "Selected Individual",
    "Applicable Shift Staff"
})

STAFF_NOTICE_OCCURRENCE_BASES = frozenset({
    "One Time",
    "Calendar",
    "Shift"
})

STAFF_NOTICE_RECURRENCE_PATTERNS = frozenset({
    "Once",
    "Daily",
    "Interval Days",
    "Selected Weekdays"
})

STAFF_NOTICE_SHIFT_APPLICABILITY_VALUES = frozenset({
    "None",
    "Every Shift",
    "Selected Shift Types",
    "Specific Shift"
})

STAFF_NOTICE_SHIFT_TYPES = frozenset({
    "Day",
    "Afternoon",
    "Overnight"
})

STAFF_NOTICE_SCHEDULE_COMBINATIONS = frozenset({
    ("One Time", "Once", "None"),
    ("Calendar", "Once", "None"),
    ("Calendar", "Daily", "None"),
    ("Calendar", "Interval Days", "None"),
    ("Calendar", "Selected Weekdays", "None"),
    ("Shift", "Once", "Every Shift"),
    ("Shift", "Once", "Selected Shift Types"),
    ("Shift", "Once", "Specific Shift"),
    ("Shift", "Daily", "Every Shift"),
    ("Shift", "Daily", "Selected Shift Types"),
    ("Shift", "Interval Days", "Every Shift"),
    ("Shift", "Interval Days", "Selected Shift Types"),
    ("Shift", "Selected Weekdays", "Every Shift"),
    ("Shift", "Selected Weekdays", "Selected Shift Types")
})

STAFF_NOTICE_DRAFT_KEYS = frozenset({
    "title",
    "notice_text",
    "priority",
    "client_id",
    "effective_start_local",
    "expires_local",
    "until_withdrawn",
    "audience_rules",
    "schedule"
})

STAFF_NOTICE_AUDIENCE_RULE_KEYS = frozenset({
    "rule_type",
    "role_name",
    "user_id"
})

STAFF_NOTICE_SCHEDULE_KEYS = frozenset({
    "occurrence_basis",
    "recurrence_pattern",
    "shift_applicability",
    "interval_days",
    "recurrence_anchor_date",
    "specific_calendar_date",
    "specific_shift_client_id",
    "specific_shift_date",
    "specific_shift_type",
    "one_time_due_local",
    "shift_types",
    "weekdays"
})

STAFF_NOTICE_MANAGEMENT_FORM_KEYS = frozenset({
    "title",
    "notice_text",
    "priority",
    "client_id",
    "effective_start_local",
    "expires_local",
    "until_withdrawn",
    "audience_rule_types",
    "selected_roles",
    "selected_user_ids",
    "schedule_enabled",
    "occurrence_basis",
    "recurrence_pattern",
    "shift_applicability",
    "interval_days",
    "recurrence_anchor_date",
    "specific_calendar_date",
    "specific_shift_client_id",
    "specific_shift_date",
    "specific_shift_type",
    "one_time_due_local",
    "shift_types",
    "weekdays",
    "expected_updated_at_utc"
})

STAFF_NOTICE_CREATE_FORM_KEYS = (
    STAFF_NOTICE_MANAGEMENT_FORM_KEYS
    - {"expected_updated_at_utc"}
)

STAFF_NOTICE_SCALAR_FORM_KEYS = frozenset({
    "title",
    "notice_text",
    "priority",
    "client_id",
    "effective_start_local",
    "expires_local",
    "occurrence_basis",
    "recurrence_pattern",
    "shift_applicability",
    "interval_days",
    "recurrence_anchor_date",
    "specific_calendar_date",
    "specific_shift_client_id",
    "specific_shift_date",
    "specific_shift_type",
    "one_time_due_local"
})

STAFF_NOTICE_CHECKBOX_FORM_KEYS = frozenset({
    "until_withdrawn",
    "schedule_enabled"
})

STAFF_NOTICE_TIMEZONE_NAME = "America/Vancouver"
STAFF_NOTICE_TIMEZONE = ZoneInfo(STAFF_NOTICE_TIMEZONE_NAME)
STAFF_NOTICE_LOCAL_DATETIME_FORMAT = "%Y-%m-%dT%H:%M"

STAFF_NOTICE_REQUIREMENT_STATUSES = frozenset({
    "Required",
    "No Longer Required",
    "Cancelled"
})


class StaffNoticeDraftCommittedCloseError(RuntimeError):

    def __init__(self, notice_id):
        super().__init__(
            f"Staff Notice draft {notice_id} was committed, but its "
            "database connection could not be closed. Do not retry "
            "draft creation."
        )
        self.notice_id = notice_id
        self.committed = True
        self.retry_safe = False


class StaffNoticeDraftChangeCommittedCloseError(RuntimeError):

    def __init__(self, notice_id, operation):
        super().__init__(
            f"Staff Notice draft {notice_id} was {operation}, but its "
            "database connection could not be closed. Do not retry "
            "the operation."
        )
        self.notice_id = notice_id
        self.committed = True
        self.retry_safe = False


class StaffNoticePublicationCommittedCloseError(RuntimeError):

    def __init__(self, notice_id):
        super().__init__(
            f"Staff Notice {notice_id} was published, but its database "
            "connection could not be closed. Do not retry publication."
        )
        self.notice_id = notice_id
        self.committed = True
        self.retry_safe = False


class StaffNoticeNotFoundError(LookupError):
    pass


class StaffNoticeNotEditableError(ValueError):
    pass


class StaffNoticeStaleEditError(ValueError):
    pass


class StaffNoticePublicationNotReadyError(ValueError):

    def __init__(self, blocking_errors):
        self.blocking_errors = tuple(blocking_errors)
        super().__init__(
            "Staff Notice draft is not ready for publication."
        )


class StaffNoticeStalePublicationError(ValueError):
    pass


def _is_valid_staff_notice_identifier(value):
    return type(value) is int and value > 0


def user_can_manage_staff_notices(session_data=None):
    if session_data is None:
        if not has_request_context():
            return False

        session_data = session

    user_id = session_data.get("user_id")
    role = session_data.get("role")

    return (
        _is_valid_staff_notice_identifier(user_id)
        and role in STAFF_NOTICE_MANAGEMENT_ROLES
    )


def get_application_now_utc():
    return datetime.now(timezone.utc)


def parse_staff_notice_utc_datetime(value):
    if isinstance(value, datetime):
        parsed_value = value
    elif isinstance(value, str) and value:
        normalized_value = value

        if normalized_value.endswith("Z"):
            normalized_value = normalized_value[:-1] + "+00:00"

        try:
            parsed_value = datetime.fromisoformat(normalized_value)
        except ValueError as error:
            raise ValueError(
                "Staff Notice timestamp must be valid ISO-8601."
            ) from error
    else:
        raise ValueError(
            "Staff Notice timestamp must be an aware datetime or "
            "ISO-8601 string."
        )

    if (
        parsed_value.tzinfo is None
        or parsed_value.utcoffset() is None
    ):
        raise ValueError(
            "Staff Notice timestamp must include a UTC offset."
        )

    return parsed_value.astimezone(timezone.utc)


def format_staff_notice_utc_datetime(value):
    utc_value = parse_staff_notice_utc_datetime(value)

    return (
        utc_value.isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_staff_notice_local_datetime(value):
    if not isinstance(value, str) or not value:
        raise ValueError(
            "Staff Notice local date and time is required."
        )

    try:
        local_naive_value = datetime.strptime(
            value,
            STAFF_NOTICE_LOCAL_DATETIME_FORMAT
        )
    except ValueError as error:
        raise ValueError(
            "Staff Notice local date and time must use "
            "YYYY-MM-DDTHH:MM."
        ) from error

    if (
        local_naive_value.strftime(
            STAFF_NOTICE_LOCAL_DATETIME_FORMAT
        ) != value
    ):
        raise ValueError(
            "Staff Notice local date and time must use "
            "YYYY-MM-DDTHH:MM."
        )

    valid_candidates = []

    for fold in (0, 1):
        candidate = local_naive_value.replace(
            tzinfo=STAFF_NOTICE_TIMEZONE,
            fold=fold
        )

        round_trip = (
            candidate.astimezone(timezone.utc)
            .astimezone(STAFF_NOTICE_TIMEZONE)
        )

        if (
            round_trip.replace(tzinfo=None) == local_naive_value
            and round_trip.fold == fold
        ):
            valid_candidates.append(candidate)

    if not valid_candidates:
        raise ValueError(
            "Staff Notice local date and time does not exist in "
            f"{STAFF_NOTICE_TIMEZONE_NAME}."
        )

    candidate_offsets = {
        candidate.utcoffset()
        for candidate in valid_candidates
    }

    if len(candidate_offsets) > 1:
        raise ValueError(
            "Staff Notice local date and time is ambiguous in "
            f"{STAFF_NOTICE_TIMEZONE_NAME}."
        )

    return valid_candidates[0]


def staff_notice_local_datetime_to_utc(value):
    return parse_staff_notice_local_datetime(value).astimezone(
        timezone.utc
    )


def staff_notice_utc_datetime_to_local(value):
    return parse_staff_notice_utc_datetime(value).astimezone(
        STAFF_NOTICE_TIMEZONE
    )


def format_staff_notice_local_datetime(
    value,
    format_string="%Y-%m-%d %H:%M"
):
    return staff_notice_utc_datetime_to_local(value).strftime(
        format_string
    )


def get_application_local_date(now_utc=None):
    if now_utc is None:
        now_utc = get_application_now_utc()

    return staff_notice_utc_datetime_to_local(now_utc).date()


def _reject_unknown_staff_notice_keys(
    value,
    allowed_keys,
    context
):
    unknown_keys = set(value) - allowed_keys

    if unknown_keys:
        field_name = sorted(
            str(key) for key in unknown_keys
        )[0]
        raise ValueError(
            f"Unknown {context} field: {field_name}."
        )


def _normalize_staff_notice_text(
    value,
    field_name,
    *,
    required=False
):
    if value is None:
        normalized_value = None
    elif isinstance(value, str):
        normalized_value = value.strip()
        if not normalized_value:
            normalized_value = None
    else:
        raise ValueError(f"{field_name} must be text.")

    if required and normalized_value is None:
        raise ValueError(f"{field_name} is required.")

    return normalized_value


def _normalize_staff_notice_identifier(value, field_name):
    if value is None:
        return None

    if not _is_valid_staff_notice_identifier(value):
        raise ValueError(
            f"{field_name} must be a positive integer."
        )

    return value


def _normalize_staff_notice_local_date(value, field_name):
    normalized_value = _normalize_staff_notice_text(
        value,
        field_name
    )

    if normalized_value is None:
        return None

    try:
        parsed_value = datetime.strptime(
            normalized_value,
            "%Y-%m-%d"
        )
    except ValueError as error:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD."
        ) from error

    if parsed_value.strftime("%Y-%m-%d") != normalized_value:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD."
        )

    return normalized_value


def _normalize_staff_notice_local_datetime(value, field_name):
    normalized_value = _normalize_staff_notice_text(
        value,
        field_name
    )

    if normalized_value is None:
        return None

    try:
        utc_value = staff_notice_local_datetime_to_utc(
            normalized_value
        )
    except ValueError as error:
        raise ValueError(f"Invalid {field_name}.") from error

    return format_staff_notice_utc_datetime(utc_value)


def _normalize_staff_notice_audience_rules(value):
    if value is None:
        return tuple()

    if not isinstance(value, (list, tuple)):
        raise ValueError(
            "Staff Notice audience rules must be a list or tuple."
        )

    normalized_rules = []
    rule_keys = set()

    for rule in value:
        if not isinstance(rule, Mapping):
            raise ValueError(
                "Each Staff Notice audience rule must be a mapping."
            )

        _reject_unknown_staff_notice_keys(
            rule,
            STAFF_NOTICE_AUDIENCE_RULE_KEYS,
            "Staff Notice audience rule"
        )

        rule_type = _normalize_staff_notice_text(
            rule.get("rule_type"),
            "Staff Notice audience rule type",
            required=True
        )

        if rule_type not in STAFF_NOTICE_AUDIENCE_RULE_TYPES:
            raise ValueError(
                "Invalid Staff Notice audience rule type."
            )

        role_name = _normalize_staff_notice_text(
            rule.get("role_name"),
            "Staff Notice audience role"
        )
        user_id = _normalize_staff_notice_identifier(
            rule.get("user_id"),
            "Staff Notice audience user ID"
        )

        if rule_type == "Selected Role":
            if role_name is None or user_id is not None:
                raise ValueError(
                    "Selected Role requires a role and forbids a user."
                )

            if role_name not in STAFF_NOTICE_SELECTABLE_ROLES:
                raise ValueError("Invalid Staff Notice audience role.")

            duplicate_key = (rule_type, role_name)

        elif rule_type == "Selected Individual":
            if user_id is None or role_name is not None:
                raise ValueError(
                    "Selected Individual requires a user and forbids "
                    "a role."
                )

            duplicate_key = (rule_type, user_id)

        else:
            if role_name is not None or user_id is not None:
                raise ValueError(
                    f"{rule_type} forbids a role and user."
                )

            duplicate_key = (rule_type,)

        if duplicate_key in rule_keys:
            raise ValueError("Duplicate Staff Notice audience rule.")

        rule_keys.add(duplicate_key)
        normalized_rules.append({
            "rule_type": rule_type,
            "role_name": role_name,
            "user_id": user_id
        })

    return tuple(normalized_rules)


def _normalize_staff_notice_schedule(value):
    if value is None:
        return None

    if not isinstance(value, Mapping):
        raise ValueError("Staff Notice schedule must be a mapping.")

    _reject_unknown_staff_notice_keys(
        value,
        STAFF_NOTICE_SCHEDULE_KEYS,
        "Staff Notice schedule"
    )

    occurrence_basis = _normalize_staff_notice_text(
        value.get("occurrence_basis"),
        "Staff Notice occurrence basis",
        required=True
    )
    recurrence_pattern = _normalize_staff_notice_text(
        value.get("recurrence_pattern"),
        "Staff Notice recurrence pattern",
        required=True
    )
    shift_applicability = _normalize_staff_notice_text(
        value.get("shift_applicability"),
        "Staff Notice shift applicability",
        required=True
    )

    if occurrence_basis not in STAFF_NOTICE_OCCURRENCE_BASES:
        raise ValueError("Invalid Staff Notice occurrence basis.")

    if recurrence_pattern not in STAFF_NOTICE_RECURRENCE_PATTERNS:
        raise ValueError("Invalid Staff Notice recurrence pattern.")

    if (
        shift_applicability
        not in STAFF_NOTICE_SHIFT_APPLICABILITY_VALUES
    ):
        raise ValueError("Invalid Staff Notice shift applicability.")

    combination = (
        occurrence_basis,
        recurrence_pattern,
        shift_applicability
    )

    if combination not in STAFF_NOTICE_SCHEDULE_COMBINATIONS:
        raise ValueError("Invalid Staff Notice schedule combination.")

    interval_days = value.get("interval_days")

    if (
        "interval_days" in value
        and recurrence_pattern != "Interval Days"
    ):
        raise ValueError(
            "Interval days is allowed only for Interval Days."
        )

    if interval_days is not None:
        if type(interval_days) is not int or interval_days < 2:
            raise ValueError(
                "Staff Notice interval days must be an integer of at "
                "least 2."
            )

    recurrence_anchor_date = _normalize_staff_notice_local_date(
        value.get("recurrence_anchor_date"),
        "Staff Notice recurrence anchor date"
    )
    specific_calendar_date = _normalize_staff_notice_local_date(
        value.get("specific_calendar_date"),
        "Staff Notice specific calendar date"
    )

    if combination == ("Calendar", "Once", "None"):
        if specific_calendar_date is None:
            raise ValueError(
                "A one-time calendar schedule requires a specific date."
            )
    elif "specific_calendar_date" in value:
        raise ValueError(
            "Specific calendar date is allowed only for a one-time "
            "calendar schedule."
        )

    specific_shift_client_id = _normalize_staff_notice_identifier(
        value.get("specific_shift_client_id"),
        "Staff Notice specific shift client ID"
    )
    specific_shift_date = _normalize_staff_notice_local_date(
        value.get("specific_shift_date"),
        "Staff Notice specific shift date"
    )
    specific_shift_type = _normalize_staff_notice_text(
        value.get("specific_shift_type"),
        "Staff Notice specific shift type"
    )
    specific_shift_values = (
        specific_shift_client_id,
        specific_shift_date,
        specific_shift_type
    )

    if shift_applicability == "Specific Shift":
        if any(item is None for item in specific_shift_values):
            raise ValueError(
                "Specific Shift requires a client, date, and shift "
                "type."
            )

        if specific_shift_type not in STAFF_NOTICE_SHIFT_TYPES:
            raise ValueError("Invalid Staff Notice shift type.")

    elif any(
        field_name in value
        for field_name in (
            "specific_shift_client_id",
            "specific_shift_date",
            "specific_shift_type"
        )
    ):
        raise ValueError(
            "Specific-shift fields are allowed only for Specific Shift."
        )

    one_time_due_at_utc = _normalize_staff_notice_local_datetime(
        value.get("one_time_due_local"),
        "Staff Notice one-time due date and time"
    )

    if (
        "one_time_due_local" in value
        and occurrence_basis != "One Time"
    ):
        raise ValueError(
            "One-time due date is allowed only for One Time."
        )

    shift_types = value.get("shift_types", [])

    if shift_types is None:
        shift_types = []

    if not isinstance(shift_types, (list, tuple)):
        raise ValueError("Staff Notice shift types must be a list or tuple.")

    normalized_shift_types = []

    for shift_type in shift_types:
        normalized_shift_type = _normalize_staff_notice_text(
            shift_type,
            "Staff Notice shift type",
            required=True
        )

        if normalized_shift_type not in STAFF_NOTICE_SHIFT_TYPES:
            raise ValueError("Invalid Staff Notice shift type.")

        if normalized_shift_type in normalized_shift_types:
            raise ValueError("Duplicate Staff Notice shift type.")

        normalized_shift_types.append(normalized_shift_type)

    if (
        "shift_types" in value
        and shift_applicability != "Selected Shift Types"
    ):
        raise ValueError(
            "Shift types are allowed only for Selected Shift Types."
        )

    weekdays = value.get("weekdays", [])

    if weekdays is None:
        weekdays = []

    if not isinstance(weekdays, (list, tuple)):
        raise ValueError("Staff Notice weekdays must be a list or tuple.")

    normalized_weekdays = []

    for weekday in weekdays:
        if type(weekday) is not int or not 0 <= weekday <= 6:
            raise ValueError(
                "Staff Notice weekdays must be integers from 0 to 6."
            )

        if weekday in normalized_weekdays:
            raise ValueError("Duplicate Staff Notice weekday.")

        normalized_weekdays.append(weekday)

    if (
        "weekdays" in value
        and recurrence_pattern != "Selected Weekdays"
    ):
        raise ValueError(
            "Weekdays are allowed only for Selected Weekdays."
        )

    return {
        "occurrence_basis": occurrence_basis,
        "recurrence_pattern": recurrence_pattern,
        "shift_applicability": shift_applicability,
        "interval_days": interval_days,
        "recurrence_anchor_date": recurrence_anchor_date,
        "specific_calendar_date": specific_calendar_date,
        "specific_shift_client_id": specific_shift_client_id,
        "specific_shift_date": specific_shift_date,
        "specific_shift_type": specific_shift_type,
        "one_time_due_at_utc": one_time_due_at_utc,
        "shift_types": tuple(normalized_shift_types),
        "weekdays": tuple(normalized_weekdays)
    }


def validate_staff_notice_draft(payload):
    if not isinstance(payload, Mapping):
        raise ValueError("Staff Notice draft payload must be a mapping.")

    _reject_unknown_staff_notice_keys(
        payload,
        STAFF_NOTICE_DRAFT_KEYS,
        "Staff Notice draft"
    )

    title = _normalize_staff_notice_text(
        payload.get("title"),
        "Staff Notice title",
        required=True
    )
    notice_text = _normalize_staff_notice_text(
        payload.get("notice_text"),
        "Staff Notice content",
        required=True
    )
    priority = _normalize_staff_notice_text(
        payload.get("priority", "Normal"),
        "Staff Notice priority",
        required=True
    )

    if priority not in STAFF_NOTICE_PRIORITIES:
        raise ValueError("Invalid Staff Notice priority.")

    client_id = _normalize_staff_notice_identifier(
        payload.get("client_id"),
        "Staff Notice client ID"
    )
    until_withdrawn = payload.get("until_withdrawn", False)

    if type(until_withdrawn) is not bool:
        raise ValueError("Staff Notice until withdrawn must be a boolean.")

    effective_start_at_utc = _normalize_staff_notice_local_datetime(
        payload.get("effective_start_local"),
        "Staff Notice effective start"
    )
    expires_at_utc = _normalize_staff_notice_local_datetime(
        payload.get("expires_local"),
        "Staff Notice expiry"
    )

    if until_withdrawn and expires_at_utc is not None:
        raise ValueError(
            "An until-withdrawn Staff Notice cannot have an expiry."
        )

    if (
        effective_start_at_utc is not None
        and expires_at_utc is not None
        and parse_staff_notice_utc_datetime(expires_at_utc)
        <= parse_staff_notice_utc_datetime(effective_start_at_utc)
    ):
        raise ValueError(
            "Staff Notice expiry must be later than its effective start."
        )

    audience_rules = _normalize_staff_notice_audience_rules(
        payload.get("audience_rules")
    )
    schedule = _normalize_staff_notice_schedule(
        payload.get("schedule")
    )

    if schedule is not None:
        specific_shift_client_id = schedule[
            "specific_shift_client_id"
        ]

        if (
            client_id is not None
            and specific_shift_client_id is not None
            and client_id != specific_shift_client_id
        ):
            raise ValueError(
                "Specific-shift client must match the Staff Notice "
                "client."
            )

        if (
            schedule["one_time_due_at_utc"] is not None
            and expires_at_utc is not None
            and parse_staff_notice_utc_datetime(
                schedule["one_time_due_at_utc"]
            ) > parse_staff_notice_utc_datetime(expires_at_utc)
        ):
            raise ValueError(
                "One-time due date cannot be after the notice expiry."
            )

        if (
            schedule["occurrence_basis"] != "Shift"
            and any(
                rule["rule_type"] == "Applicable Shift Staff"
                for rule in audience_rules
            )
        ):
            raise ValueError(
                "Applicable Shift Staff requires a Shift schedule."
            )

    return {
        "title": title,
        "notice_text": notice_text,
        "priority": priority,
        "client_id": client_id,
        "effective_start_at_utc": effective_start_at_utc,
        "expires_at_utc": expires_at_utc,
        "until_withdrawn": 1 if until_withdrawn else 0,
        "audience_rules": audience_rules,
        "schedule": schedule
    }


def _validate_staff_notice_draft_references(
    conn,
    normalized_draft,
    actor_user_id
):
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise PermissionError("Staff Notice management access denied.")

    actor = conn.execute("""
        SELECT user_id, role, active
        FROM users
        WHERE user_id = ?
    """, (actor_user_id,)).fetchone()

    if (
        actor is None
        or type(actor["active"]) is not int
        or actor["active"] != 1
        or not user_can_manage_staff_notices({
            "user_id": actor["user_id"],
            "role": actor["role"]
        })
    ):
        raise PermissionError("Staff Notice management access denied.")

    client_ids = set()

    if normalized_draft["client_id"] is not None:
        client_ids.add(normalized_draft["client_id"])

    schedule = normalized_draft["schedule"]

    if (
        schedule is not None
        and schedule["specific_shift_client_id"] is not None
    ):
        client_ids.add(schedule["specific_shift_client_id"])

    for client_id in client_ids:
        client = conn.execute("""
            SELECT client_id, active
            FROM clients
            WHERE client_id = ?
        """, (client_id,)).fetchone()

        if (
            client is None
            or type(client["active"]) is not int
            or client["active"] != 1
        ):
            raise ValueError(
                "Staff Notice client must exist and be active."
            )

    selected_user_ids = {
        rule["user_id"]
        for rule in normalized_draft["audience_rules"]
        if rule["rule_type"] == "Selected Individual"
    }

    for user_id in selected_user_ids:
        selected_user = conn.execute("""
            SELECT user_id, active
            FROM users
            WHERE user_id = ?
        """, (user_id,)).fetchone()

        if (
            selected_user is None
            or type(selected_user["active"]) is not int
            or selected_user["active"] != 1
        ):
            raise ValueError(
                "Selected Staff Notice user must exist and be active."
            )


def _get_staff_notice_exception_link(error, attribute_name):
    try:
        error_type = type(error)
        effective_getattribute = type.__getattribute__(
            error_type,
            "__getattribute__"
        )

        if effective_getattribute is not BaseException.__getattribute__:
            return False, None

        canonical_link = BaseException.__getattribute__(
            error,
            attribute_name
        )

        if (
            canonical_link is not None
            and not isinstance(canonical_link, BaseException)
        ):
            return False, None

        return True, canonical_link

    except BaseException:
        return False, None


def _get_staff_notice_exception_graph_ids(error):
    try:
        pending = [(error, False)]
        visiting = set()
        visited = set()

        while pending:
            current_error, completed = pending.pop()

            if not isinstance(current_error, BaseException):
                return None

            current_id = id(current_error)

            if completed:
                visiting.remove(current_id)
                visited.add(current_id)
                continue

            if current_id in visiting:
                return None

            if current_id in visited:
                continue

            visiting.add(current_id)
            pending.append((current_error, True))

            cause_is_safe, cause = _get_staff_notice_exception_link(
                current_error,
                "__cause__"
            )
            context_is_safe, context = (
                _get_staff_notice_exception_link(
                    current_error,
                    "__context__"
                )
            )

            if not cause_is_safe or not context_is_safe:
                return None

            for related_error in (cause, context):
                if related_error is not None:
                    pending.append((related_error, False))

        return frozenset(visited)

    except BaseException:
        return None


def _preserve_staff_notice_cleanup_error(
    primary_error,
    attribute_name,
    cleanup_error,
    note_text
):
    try:
        diagnostic_attached = False

        try:
            setattr(primary_error, attribute_name, cleanup_error)

            if getattr(primary_error, attribute_name) is cleanup_error:
                diagnostic_attached = True
        except BaseException:
            pass

        try:
            add_note = getattr(primary_error, "add_note", None)
        except BaseException:
            add_note = None

        if callable(add_note):
            try:
                add_note(note_text)
                diagnostic_attached = True
            except BaseException:
                pass

        if diagnostic_attached:
            return

        try:
            cause_is_safe, primary_cause = (
                _get_staff_notice_exception_link(
                    primary_error,
                    "__cause__"
                )
            )
            context_is_safe, primary_context = (
                _get_staff_notice_exception_link(
                    primary_error,
                    "__context__"
                )
            )

            if not cause_is_safe or not context_is_safe:
                return

            if primary_cause is not None or primary_context is not None:
                return

            if cleanup_error is primary_error:
                return

            primary_graph_ids = (
                _get_staff_notice_exception_graph_ids(primary_error)
            )
            cleanup_graph_ids = (
                _get_staff_notice_exception_graph_ids(cleanup_error)
            )

            if primary_graph_ids is None or cleanup_graph_ids is None:
                return

            if (
                id(cleanup_error) in primary_graph_ids
                or id(primary_error) in cleanup_graph_ids
            ):
                return

            original_suppress_context = BaseException.__getattribute__(
                primary_error,
                "__suppress_context__"
            )
            BaseException.__setattr__(
                primary_error,
                "__cause__",
                cleanup_error
            )
            cause_is_safe, retained_cause = (
                _get_staff_notice_exception_link(
                    primary_error,
                    "__cause__"
                )
            )

            if not cause_is_safe or retained_cause is not cleanup_error:
                BaseException.__setattr__(
                    primary_error,
                    "__cause__",
                    None
                )
                BaseException.__setattr__(
                    primary_error,
                    "__suppress_context__",
                    original_suppress_context
                )
        except BaseException:
            pass

    except BaseException:
        pass


def create_staff_notice_draft(payload, actor_user_id):
    normalized_draft = validate_staff_notice_draft(payload)
    conn = None
    primary_error = None
    notice_id = None
    commit_succeeded = False

    try:
        conn = get_db()
        _validate_staff_notice_draft_references(
            conn,
            normalized_draft,
            actor_user_id
        )

        conn.execute("BEGIN IMMEDIATE")

        _validate_staff_notice_draft_references(
            conn,
            normalized_draft,
            actor_user_id
        )

        created_at_utc = format_staff_notice_utc_datetime(
            get_application_now_utc()
        )

        cur = conn.execute("""
            INSERT INTO staff_notices
            (
                title,
                notice_text,
                priority,
                client_id,
                status,
                draft_active,
                effective_start_at_utc,
                expires_at_utc,
                until_withdrawn,
                version_number,
                created_by_user_id,
                created_at_utc
            )
            VALUES (?, ?, ?, ?, 'Draft', 1, ?, ?, ?, 1, ?, ?)
        """, (
            normalized_draft["title"],
            normalized_draft["notice_text"],
            normalized_draft["priority"],
            normalized_draft["client_id"],
            normalized_draft["effective_start_at_utc"],
            normalized_draft["expires_at_utc"],
            normalized_draft["until_withdrawn"],
            actor_user_id,
            created_at_utc
        ))
        notice_id = cur.lastrowid

        audience_rules = normalized_draft["audience_rules"]

        if audience_rules:
            cur = conn.execute("""
                INSERT INTO staff_notice_audiences
                (notice_id, created_at_utc)
                VALUES (?, ?)
            """, (notice_id, created_at_utc))
            audience_id = cur.lastrowid

            for rule in audience_rules:
                conn.execute("""
                    INSERT INTO staff_notice_audience_rules
                    (
                        audience_id,
                        rule_type,
                        role_name,
                        user_id,
                        created_at_utc
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    audience_id,
                    rule["rule_type"],
                    rule["role_name"],
                    rule["user_id"],
                    created_at_utc
                ))

        schedule = normalized_draft["schedule"]

        if schedule is not None:
            cur = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id,
                    occurrence_basis,
                    recurrence_pattern,
                    shift_applicability,
                    interval_days,
                    recurrence_anchor_date,
                    specific_calendar_date,
                    specific_shift_client_id,
                    specific_shift_date,
                    specific_shift_type,
                    one_time_due_at_utc,
                    created_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                notice_id,
                schedule["occurrence_basis"],
                schedule["recurrence_pattern"],
                schedule["shift_applicability"],
                schedule["interval_days"],
                schedule["recurrence_anchor_date"],
                schedule["specific_calendar_date"],
                schedule["specific_shift_client_id"],
                schedule["specific_shift_date"],
                schedule["specific_shift_type"],
                schedule["one_time_due_at_utc"],
                created_at_utc
            ))
            schedule_id = cur.lastrowid

            for shift_type in schedule["shift_types"]:
                conn.execute("""
                    INSERT INTO staff_notice_schedule_shift_types
                    (schedule_id, shift_type)
                    VALUES (?, ?)
                """, (schedule_id, shift_type))

            for weekday in schedule["weekdays"]:
                conn.execute("""
                    INSERT INTO staff_notice_schedule_weekdays
                    (schedule_id, weekday_number)
                    VALUES (?, ?)
                """, (schedule_id, weekday))

        log_activity(
            conn,
            activity_class="STAFF_NOTICE",
            activity_type="staff_notice_draft_created",
            summary=(
                "Staff Notice draft created: "
                f"{normalized_draft['title']}"
            ),
            user_id=actor_user_id,
            client_id=normalized_draft["client_id"],
            shift_id=None,
            related_table="staff_notices",
            related_id=notice_id,
            details=(
                f"Priority: {normalized_draft['priority']}; "
                f"Audience rules: {len(audience_rules)}; "
                "Schedule configured: "
                f"{'Yes' if schedule is not None else 'No'}"
            ),
            success=1
        )

        conn.commit()
        commit_succeeded = True
        return notice_id

    except BaseException as error:
        primary_error = error

        if conn is not None:
            try:
                if conn.in_transaction:
                    conn.rollback()
            except BaseException as rollback_error:
                _preserve_staff_notice_cleanup_error(
                    error,
                    "staff_notice_rollback_error",
                    rollback_error,
                    "Staff Notice draft rollback also failed: "
                    f"{rollback_error}"
                )

        raise

    finally:
        if conn is not None:
            try:
                conn.close()
            except BaseException as close_error:
                if primary_error is None:
                    if commit_succeeded:
                        raise StaffNoticeDraftCommittedCloseError(
                            notice_id
                        ) from close_error

                    raise

                _preserve_staff_notice_cleanup_error(
                    primary_error,
                    "staff_notice_close_error",
                    close_error,
                    "Staff Notice database close also failed: "
                    f"{close_error}"
                )


def get_recipient_staff_notice_status(
    *,
    active_acknowledgement_at_utc=None,
    due_at_utc=None,
    requirement_status="Required",
    first_viewed_at_utc=None
):
    if requirement_status not in STAFF_NOTICE_REQUIREMENT_STATUSES:
        raise ValueError("Invalid Staff Notice requirement status.")

    if active_acknowledgement_at_utc is not None:
        acknowledged_at = parse_staff_notice_utc_datetime(
            active_acknowledgement_at_utc
        )

        if due_at_utc is not None:
            due_at = parse_staff_notice_utc_datetime(due_at_utc)

            if acknowledged_at > due_at:
                return "Acknowledged Late"

        return "Acknowledged"

    if requirement_status == "Cancelled":
        return "Cancelled"

    if requirement_status == "No Longer Required":
        return "No Longer Required"

    if first_viewed_at_utc is not None:
        parse_staff_notice_utc_datetime(first_viewed_at_utc)
        return "Viewed – Awaiting Acknowledgement"

    return "Not Viewed"


def _get_staff_notice_record_value(record, field_name):
    if record is None:
        return None

    try:
        return record[field_name]
    except (KeyError, IndexError, TypeError):
        return None


def user_owns_staff_notice_delivery(delivery, user_id):
    delivery_user_id = _get_staff_notice_record_value(
        delivery,
        "user_id"
    )

    if not _is_valid_staff_notice_identifier(user_id):
        return False

    return (
        _is_valid_staff_notice_identifier(delivery_user_id)
        and delivery_user_id == user_id
    )


def staff_notice_delivery_has_content_access(delivery):
    recipient_access = _get_staff_notice_record_value(
        delivery,
        "recipient_access"
    )

    return type(recipient_access) is int and recipient_access == 1


def user_can_access_staff_notice_delivery(delivery, user_id):
    return (
        user_owns_staff_notice_delivery(delivery, user_id)
        and staff_notice_delivery_has_content_access(delivery)
    )


def _staff_notice_form_state(form):
    list_fields = {
        "audience_rule_types",
        "selected_roles",
        "selected_user_ids",
        "shift_types",
        "weekdays"
    }
    state = {}

    for key in STAFF_NOTICE_MANAGEMENT_FORM_KEYS:
        if key == "expected_updated_at_utc":
            continue

        if key in list_fields:
            state[key] = form.getlist(key)
        elif key in STAFF_NOTICE_CHECKBOX_FORM_KEYS:
            values = form.getlist(key)
            state[key] = len(values) == 1 and values[0] == "1"
        else:
            values = form.getlist(key)
            state[key] = values[0] if len(values) == 1 else ""

    return state


def _staff_notice_single_form_value(form, field_name, *, required=False):
    values = form.getlist(field_name)

    if len(values) > 1:
        raise ValueError(
            f"Staff Notice field {field_name} must be submitted once."
        )

    if not values or (required and not str(values[0]).strip()):
        if required:
            raise ValueError(
                f"Staff Notice field {field_name} is required."
            )

        return ""

    return values[0]


def _staff_notice_checkbox_form_value(form, field_name):
    values = form.getlist(field_name)

    if not values:
        return False

    if len(values) != 1 or values[0] != "1":
        raise ValueError(
            f"Staff Notice field {field_name} must have the value 1."
        )

    return True


def _staff_notice_form_identifier(value, field_name):
    normalized_value = str(value or "").strip()

    if not normalized_value:
        return None

    if not normalized_value.isascii() or not normalized_value.isdigit():
        raise ValueError(f"{field_name} must be a valid selection.")

    identifier = int(normalized_value)

    if not _is_valid_staff_notice_identifier(identifier):
        raise ValueError(f"{field_name} must be a valid selection.")

    return identifier


def build_staff_notice_draft_payload_from_form(form, *, edit=False):
    allowed_keys = (
        STAFF_NOTICE_MANAGEMENT_FORM_KEYS
        if edit
        else STAFF_NOTICE_CREATE_FORM_KEYS
    )
    unknown_keys = set(form.keys()).difference(allowed_keys)

    if unknown_keys:
        raise ValueError("Unexpected Staff Notice form field.")

    scalar_values = {
        field_name: _staff_notice_single_form_value(form, field_name)
        for field_name in STAFF_NOTICE_SCALAR_FORM_KEYS
    }

    if edit:
        _staff_notice_single_form_value(
            form,
            "expected_updated_at_utc",
            required=True
        )

    checkbox_values = {
        field_name: _staff_notice_checkbox_form_value(form, field_name)
        for field_name in STAFF_NOTICE_CHECKBOX_FORM_KEYS
    }

    audience_rule_types = form.getlist("audience_rule_types")
    selected_roles = form.getlist("selected_roles")
    selected_user_values = form.getlist("selected_user_ids")

    if len(audience_rule_types) != len(set(audience_rule_types)):
        raise ValueError("Duplicate Staff Notice audience selection.")

    if len(selected_roles) != len(set(selected_roles)):
        raise ValueError("Duplicate Staff Notice role selection.")

    if len(selected_user_values) != len(set(selected_user_values)):
        raise ValueError("Duplicate Staff Notice person selection.")

    if selected_roles and "Selected Role" not in audience_rule_types:
        raise ValueError(
            "Selected roles require the Selected Role audience option."
        )

    if (
        selected_user_values
        and "Selected Individual" not in audience_rule_types
    ):
        raise ValueError(
            "Selected people require the Selected Individual audience "
            "option."
        )

    audience_rules = []

    for rule_type in audience_rule_types:
        if rule_type == "Selected Role":
            if not selected_roles:
                raise ValueError("Select at least one Staff Notice role.")

            for role_name in selected_roles:
                audience_rules.append({
                    "rule_type": rule_type,
                    "role_name": role_name
                })
        elif rule_type == "Selected Individual":
            if not selected_user_values:
                raise ValueError("Select at least one Staff Notice person.")

            for user_value in selected_user_values:
                audience_rules.append({
                    "rule_type": rule_type,
                    "user_id": _staff_notice_form_identifier(
                        user_value,
                        "Staff Notice person"
                    )
                })
        else:
            audience_rules.append({"rule_type": rule_type})

    payload = {
        "title": scalar_values["title"],
        "notice_text": scalar_values["notice_text"],
        "priority": scalar_values["priority"],
        "client_id": _staff_notice_form_identifier(
            scalar_values["client_id"],
            "Staff Notice client"
        ),
        "effective_start_local": scalar_values[
            "effective_start_local"
        ],
        "expires_local": scalar_values["expires_local"],
        "until_withdrawn": checkbox_values["until_withdrawn"],
        "audience_rules": audience_rules
    }

    schedule_fields = (
        "occurrence_basis",
        "recurrence_pattern",
        "shift_applicability",
        "interval_days",
        "recurrence_anchor_date",
        "specific_calendar_date",
        "specific_shift_client_id",
        "specific_shift_date",
        "specific_shift_type",
        "one_time_due_local"
    )
    schedule_has_values = any(
        str(scalar_values[field_name]).strip()
        for field_name in schedule_fields
    ) or bool(form.getlist("shift_types")) or bool(
        form.getlist("weekdays")
    )

    if not checkbox_values["schedule_enabled"]:
        if schedule_has_values:
            raise ValueError(
                "Schedule fields require Schedule Configured to be "
                "selected."
            )

        payload["schedule"] = None
        return payload

    schedule = {
        "occurrence_basis": scalar_values["occurrence_basis"],
        "recurrence_pattern": scalar_values["recurrence_pattern"],
        "shift_applicability": scalar_values["shift_applicability"]
    }
    optional_text_fields = (
        "recurrence_anchor_date",
        "specific_calendar_date",
        "specific_shift_date",
        "specific_shift_type",
        "one_time_due_local"
    )

    for field_name in optional_text_fields:
        value = scalar_values[field_name]

        if str(value).strip():
            schedule[field_name] = value

    interval_value = str(scalar_values["interval_days"]).strip()

    if interval_value:
        try:
            schedule["interval_days"] = int(interval_value)
        except ValueError as error:
            raise ValueError(
                "Staff Notice interval days must be a whole number."
            ) from error

    specific_client_value = scalar_values["specific_shift_client_id"]

    if str(specific_client_value).strip():
        schedule["specific_shift_client_id"] = (
            _staff_notice_form_identifier(
                specific_client_value,
                "Specific-shift client"
            )
        )

    shift_types = form.getlist("shift_types")
    weekdays = form.getlist("weekdays")

    if shift_types:
        schedule["shift_types"] = shift_types

    if weekdays:
        normalized_weekdays = []

        for weekday in weekdays:
            if (
                not str(weekday).isascii()
                or not str(weekday).isdigit()
            ):
                raise ValueError("Invalid Staff Notice weekday.")

            normalized_weekdays.append(int(weekday))

        schedule["weekdays"] = normalized_weekdays

    payload["schedule"] = schedule
    return payload


def validate_staff_notice_management_draft(payload):
    normalized_draft = validate_staff_notice_draft(payload)
    audience_rules = normalized_draft["audience_rules"]
    schedule = normalized_draft["schedule"]

    if not audience_rules:
        raise ValueError(
            "A Staff Notice draft requires at least one audience rule."
        )

    if any(
        rule["rule_type"] == "Applicable Shift Staff"
        for rule in audience_rules
    ) and (schedule is None or schedule["occurrence_basis"] != "Shift"):
        raise ValueError(
            "Applicable Shift Staff requires a Shift schedule."
        )

    if schedule is None:
        return normalized_draft

    if (
        schedule["recurrence_pattern"] == "Selected Weekdays"
        and not schedule["weekdays"]
    ):
        raise ValueError("Select at least one Staff Notice weekday.")

    if (
        schedule["shift_applicability"] == "Selected Shift Types"
        and not schedule["shift_types"]
    ):
        raise ValueError("Select at least one Staff Notice shift type.")

    if (
        schedule["recurrence_pattern"] == "Interval Days"
        and schedule["interval_days"] is None
    ):
        raise ValueError("Staff Notice interval days are required.")

    effective_start = normalized_draft["effective_start_at_utc"]
    expires_at = normalized_draft["expires_at_utc"]
    due_at = schedule["one_time_due_at_utc"]

    if (
        due_at is not None
        and effective_start is not None
        and parse_staff_notice_utc_datetime(due_at)
        < parse_staff_notice_utc_datetime(effective_start)
    ):
        raise ValueError(
            "One-time due date cannot be before the notice effective "
            "start."
        )

    scheduled_date = (
        schedule["specific_calendar_date"]
        or schedule["specific_shift_date"]
    )

    if scheduled_date is not None and effective_start is not None:
        effective_date = staff_notice_utc_datetime_to_local(
            effective_start
        ).date().isoformat()

        if scheduled_date < effective_date:
            raise ValueError(
                "Specific schedule date cannot be before the notice "
                "effective date."
            )

    if scheduled_date is not None and expires_at is not None:
        expiry_date = staff_notice_utc_datetime_to_local(
            expires_at
        ).date().isoformat()

        if scheduled_date > expiry_date:
            raise ValueError(
                "Specific schedule date cannot be after the notice "
                "expiry date."
            )

    return normalized_draft


def _staff_notice_draft_token(notice):
    return notice["updated_at_utc"] or notice["created_at_utc"]


def _next_staff_notice_draft_timestamp(notice):
    next_timestamp = format_staff_notice_utc_datetime(
        get_application_now_utc()
    )

    if (
        parse_staff_notice_utc_datetime(next_timestamp)
        <= parse_staff_notice_utc_datetime(
            _staff_notice_draft_token(notice)
        )
    ):
        next_timestamp = format_staff_notice_utc_datetime(
            parse_staff_notice_utc_datetime(
                _staff_notice_draft_token(notice)
            ) + timedelta(seconds=1)
        )

    return next_timestamp


def _get_editable_staff_notice(conn, notice_id, expected_token=None):
    if not _is_valid_staff_notice_identifier(notice_id):
        raise StaffNoticeNotFoundError("Staff Notice draft not found.")

    notice = conn.execute("""
        SELECT *
        FROM staff_notices
        WHERE notice_id = ?
    """, (notice_id,)).fetchone()

    if notice is None:
        raise StaffNoticeNotFoundError("Staff Notice draft not found.")

    if notice["status"] != "Draft" or notice["draft_active"] != 1:
        raise StaffNoticeNotEditableError(
            "Staff Notice draft is not editable."
        )

    if (
        expected_token is not None
        and _staff_notice_draft_token(notice) != expected_token
    ):
        raise StaffNoticeStaleEditError(
            "This Staff Notice draft changed after the form was "
            "opened. Reload it and try again."
        )

    return notice


def update_staff_notice_draft(
    notice_id,
    payload,
    actor_user_id,
    expected_updated_at_utc
):
    normalized_draft = validate_staff_notice_management_draft(payload)

    if not str(expected_updated_at_utc or "").strip():
        raise StaffNoticeStaleEditError(
            "The Staff Notice edit version is missing. Reload it and "
            "try again."
        )

    conn = None
    primary_error = None
    commit_succeeded = False

    try:
        conn = get_db()
        _validate_staff_notice_draft_references(
            conn,
            normalized_draft,
            actor_user_id
        )
        _get_editable_staff_notice(
            conn,
            notice_id,
            expected_updated_at_utc
        )

        conn.execute("BEGIN IMMEDIATE")

        _validate_staff_notice_draft_references(
            conn,
            normalized_draft,
            actor_user_id
        )
        current_notice = _get_editable_staff_notice(
            conn,
            notice_id,
            expected_updated_at_utc
        )

        updated_at_utc = _next_staff_notice_draft_timestamp(
            current_notice
        )
        conn.execute("""
            UPDATE staff_notices
            SET title = ?,
                notice_text = ?,
                priority = ?,
                client_id = ?,
                effective_start_at_utc = ?,
                expires_at_utc = ?,
                until_withdrawn = ?,
                updated_by_user_id = ?,
                updated_at_utc = ?
            WHERE notice_id = ?
        """, (
            normalized_draft["title"],
            normalized_draft["notice_text"],
            normalized_draft["priority"],
            normalized_draft["client_id"],
            normalized_draft["effective_start_at_utc"],
            normalized_draft["expires_at_utc"],
            normalized_draft["until_withdrawn"],
            actor_user_id,
            updated_at_utc,
            notice_id
        ))

        audience = conn.execute("""
            SELECT audience_id
            FROM staff_notice_audiences
            WHERE notice_id = ?
        """, (notice_id,)).fetchone()

        if audience is not None:
            audience_id = audience["audience_id"]
            conn.execute("""
                DELETE FROM staff_notice_audience_eligibility_periods
                WHERE audience_id = ?
            """, (audience_id,))
            conn.execute("""
                DELETE FROM staff_notice_audience_rules
                WHERE audience_id = ?
            """, (audience_id,))
            conn.execute("""
                DELETE FROM staff_notice_audiences
                WHERE audience_id = ?
            """, (audience_id,))

        cur = conn.execute("""
            INSERT INTO staff_notice_audiences
            (notice_id, created_at_utc)
            VALUES (?, ?)
        """, (notice_id, updated_at_utc))
        audience_id = cur.lastrowid

        for rule in normalized_draft["audience_rules"]:
            conn.execute("""
                INSERT INTO staff_notice_audience_rules
                (
                    audience_id,
                    rule_type,
                    role_name,
                    user_id,
                    created_at_utc
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                audience_id,
                rule["rule_type"],
                rule["role_name"],
                rule["user_id"],
                updated_at_utc
            ))

        stored_schedule = conn.execute("""
            SELECT schedule_id
            FROM staff_notice_schedules
            WHERE notice_id = ?
        """, (notice_id,)).fetchone()

        if stored_schedule is not None:
            schedule_id = stored_schedule["schedule_id"]
            conn.execute("""
                DELETE FROM staff_notice_schedule_shift_types
                WHERE schedule_id = ?
            """, (schedule_id,))
            conn.execute("""
                DELETE FROM staff_notice_schedule_weekdays
                WHERE schedule_id = ?
            """, (schedule_id,))
            conn.execute("""
                DELETE FROM staff_notice_schedules
                WHERE schedule_id = ?
            """, (schedule_id,))

        schedule = normalized_draft["schedule"]

        if schedule is not None:
            cur = conn.execute("""
                INSERT INTO staff_notice_schedules
                (
                    notice_id,
                    occurrence_basis,
                    recurrence_pattern,
                    shift_applicability,
                    interval_days,
                    recurrence_anchor_date,
                    specific_calendar_date,
                    specific_shift_client_id,
                    specific_shift_date,
                    specific_shift_type,
                    one_time_due_at_utc,
                    created_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                notice_id,
                schedule["occurrence_basis"],
                schedule["recurrence_pattern"],
                schedule["shift_applicability"],
                schedule["interval_days"],
                schedule["recurrence_anchor_date"],
                schedule["specific_calendar_date"],
                schedule["specific_shift_client_id"],
                schedule["specific_shift_date"],
                schedule["specific_shift_type"],
                schedule["one_time_due_at_utc"],
                updated_at_utc
            ))
            schedule_id = cur.lastrowid

            for shift_type in schedule["shift_types"]:
                conn.execute("""
                    INSERT INTO staff_notice_schedule_shift_types
                    (schedule_id, shift_type)
                    VALUES (?, ?)
                """, (schedule_id, shift_type))

            for weekday in schedule["weekdays"]:
                conn.execute("""
                    INSERT INTO staff_notice_schedule_weekdays
                    (schedule_id, weekday_number)
                    VALUES (?, ?)
                """, (schedule_id, weekday))

        log_activity(
            conn,
            activity_class="STAFF_NOTICE",
            activity_type="staff_notice_draft_updated",
            summary=(
                "Staff Notice draft updated: "
                f"{normalized_draft['title']}"
            ),
            user_id=actor_user_id,
            client_id=normalized_draft["client_id"],
            shift_id=None,
            related_table="staff_notices",
            related_id=notice_id,
            details=(
                f"Priority: {normalized_draft['priority']}; "
                "Audience rules: "
                f"{len(normalized_draft['audience_rules'])}; "
                "Schedule configured: "
                f"{'Yes' if schedule is not None else 'No'}"
            ),
            success=1
        )

        conn.commit()
        commit_succeeded = True
        return notice_id

    except BaseException as error:
        primary_error = error

        if conn is not None:
            try:
                if conn.in_transaction:
                    conn.rollback()
            except BaseException as rollback_error:
                _preserve_staff_notice_cleanup_error(
                    error,
                    "staff_notice_rollback_error",
                    rollback_error,
                    "Staff Notice draft update rollback also failed: "
                    f"{rollback_error}"
                )

        raise

    finally:
        if conn is not None:
            try:
                conn.close()
            except BaseException as close_error:
                if primary_error is None:
                    if commit_succeeded:
                        raise StaffNoticeDraftChangeCommittedCloseError(
                            notice_id,
                            "updated"
                        ) from close_error

                    raise

                _preserve_staff_notice_cleanup_error(
                    primary_error,
                    "staff_notice_close_error",
                    close_error,
                    "Staff Notice database close also failed: "
                    f"{close_error}"
                )


def deactivate_staff_notice_draft(notice_id, actor_user_id):
    conn = None
    primary_error = None
    commit_succeeded = False

    try:
        conn = get_db()
        actor_payload = {
            "client_id": None,
            "schedule": None,
            "audience_rules": tuple()
        }
        _validate_staff_notice_draft_references(
            conn,
            actor_payload,
            actor_user_id
        )
        _get_editable_staff_notice(conn, notice_id)

        conn.execute("BEGIN IMMEDIATE")
        _validate_staff_notice_draft_references(
            conn,
            actor_payload,
            actor_user_id
        )
        notice = _get_editable_staff_notice(conn, notice_id)
        updated_at_utc = _next_staff_notice_draft_timestamp(notice)
        conn.execute("""
            UPDATE staff_notices
            SET draft_active = 0,
                updated_by_user_id = ?,
                updated_at_utc = ?
            WHERE notice_id = ?
        """, (actor_user_id, updated_at_utc, notice_id))

        log_activity(
            conn,
            activity_class="STAFF_NOTICE",
            activity_type="staff_notice_draft_deactivated",
            summary=f"Staff Notice draft deactivated: {notice['title']}",
            user_id=actor_user_id,
            client_id=notice["client_id"],
            shift_id=None,
            related_table="staff_notices",
            related_id=notice_id,
            details="Draft retained with its saved configuration.",
            success=1
        )

        conn.commit()
        commit_succeeded = True
        return notice_id

    except BaseException as error:
        primary_error = error

        if conn is not None:
            try:
                if conn.in_transaction:
                    conn.rollback()
            except BaseException as rollback_error:
                _preserve_staff_notice_cleanup_error(
                    error,
                    "staff_notice_rollback_error",
                    rollback_error,
                    "Staff Notice draft deactivation rollback also "
                    f"failed: {rollback_error}"
                )

        raise

    finally:
        if conn is not None:
            try:
                conn.close()
            except BaseException as close_error:
                if primary_error is None:
                    if commit_succeeded:
                        raise StaffNoticeDraftChangeCommittedCloseError(
                            notice_id,
                            "deactivated"
                        ) from close_error

                    raise

                _preserve_staff_notice_cleanup_error(
                    primary_error,
                    "staff_notice_close_error",
                    close_error,
                    "Staff Notice database close also failed: "
                    f"{close_error}"
                )


def _load_staff_notice_admin_record(conn, notice_id):
    notice = conn.execute("""
        SELECT
            sn.*,
            c.client_name,
            creator.full_name AS created_by,
            updater.full_name AS updated_by
        FROM staff_notices sn
        LEFT JOIN clients c
            ON sn.client_id = c.client_id
        JOIN users creator
            ON sn.created_by_user_id = creator.user_id
        LEFT JOIN users updater
            ON sn.updated_by_user_id = updater.user_id
        WHERE sn.notice_id = ?
    """, (notice_id,)).fetchone()

    if notice is None:
        return None

    result = dict(notice)
    audience = conn.execute("""
        SELECT audience_id, created_at_utc
        FROM staff_notice_audiences
        WHERE notice_id = ?
    """, (notice_id,)).fetchone()
    result["audience"] = dict(audience) if audience else None
    result["audience_rules"] = []

    if audience is not None:
        result["audience_rules"] = [
            dict(row)
            for row in conn.execute("""
                SELECT
                    ar.*,
                    u.full_name AS selected_user_name,
                    u.active AS selected_user_active
                FROM staff_notice_audience_rules ar
                LEFT JOIN users u
                    ON ar.user_id = u.user_id
                WHERE ar.audience_id = ?
                ORDER BY
                    ar.rule_type,
                    ar.role_name,
                    u.full_name
            """, (audience["audience_id"],)).fetchall()
        ]

    schedule = conn.execute("""
        SELECT
            s.*,
            c.client_name AS specific_shift_client_name
        FROM staff_notice_schedules s
        LEFT JOIN clients c
            ON s.specific_shift_client_id = c.client_id
        WHERE s.notice_id = ?
    """, (notice_id,)).fetchone()
    result["schedule"] = dict(schedule) if schedule else None
    result["shift_types"] = []
    result["weekdays"] = []

    if schedule is not None:
        result["schedule"]["one_time_due_local"] = (
            format_staff_notice_local_datetime(
                schedule["one_time_due_at_utc"]
            ) if schedule["one_time_due_at_utc"] else None
        )
        schedule_id = schedule["schedule_id"]
        result["shift_types"] = [
            row["shift_type"]
            for row in conn.execute("""
                SELECT shift_type
                FROM staff_notice_schedule_shift_types
                WHERE schedule_id = ?
                ORDER BY shift_type
            """, (schedule_id,)).fetchall()
        ]
        result["weekdays"] = [
            row["weekday_number"]
            for row in conn.execute("""
                SELECT weekday_number
                FROM staff_notice_schedule_weekdays
                WHERE schedule_id = ?
                ORDER BY weekday_number
            """, (schedule_id,)).fetchall()
        ]

    return result


def _staff_notice_form_data_from_record(notice):
    audience_rule_types = []
    selected_roles = []
    selected_user_ids = []

    for rule in notice["audience_rules"]:
        if rule["rule_type"] not in audience_rule_types:
            audience_rule_types.append(rule["rule_type"])

        if rule["role_name"] is not None:
            selected_roles.append(rule["role_name"])

        if rule["user_id"] is not None:
            selected_user_ids.append(str(rule["user_id"]))

    schedule = notice["schedule"] or {}
    return {
        "title": notice["title"],
        "notice_text": notice["notice_text"],
        "priority": notice["priority"],
        "client_id": str(notice["client_id"] or ""),
        "effective_start_local": (
            format_staff_notice_local_datetime(
                notice["effective_start_at_utc"],
                STAFF_NOTICE_LOCAL_DATETIME_FORMAT
            ) if notice["effective_start_at_utc"] else ""
        ),
        "expires_local": (
            format_staff_notice_local_datetime(
                notice["expires_at_utc"],
                STAFF_NOTICE_LOCAL_DATETIME_FORMAT
            )
            if notice["expires_at_utc"] else ""
        ),
        "until_withdrawn": notice["until_withdrawn"] == 1,
        "audience_rule_types": audience_rule_types,
        "selected_roles": selected_roles,
        "selected_user_ids": selected_user_ids,
        "schedule_enabled": notice["schedule"] is not None,
        "occurrence_basis": schedule.get("occurrence_basis", ""),
        "recurrence_pattern": schedule.get("recurrence_pattern", ""),
        "shift_applicability": schedule.get(
            "shift_applicability",
            ""
        ),
        "interval_days": str(schedule.get("interval_days") or ""),
        "recurrence_anchor_date": schedule.get(
            "recurrence_anchor_date"
        ) or "",
        "specific_calendar_date": schedule.get(
            "specific_calendar_date"
        ) or "",
        "specific_shift_client_id": str(
            schedule.get("specific_shift_client_id") or ""
        ),
        "specific_shift_date": schedule.get("specific_shift_date") or "",
        "specific_shift_type": schedule.get("specific_shift_type") or "",
        "one_time_due_local": (
            format_staff_notice_local_datetime(
                schedule.get("one_time_due_at_utc"),
                STAFF_NOTICE_LOCAL_DATETIME_FORMAT
            ) if schedule.get("one_time_due_at_utc") else ""
        ),
        "shift_types": list(notice["shift_types"]),
        "weekdays": [str(value) for value in notice["weekdays"]]
    }


def build_staff_notice_plain_language_summary(notice):
    client_summary = (
        f"Client-specific: {notice['client_name']}"
        if notice["client_id"] is not None
        else "Organization-wide"
    )
    audience_parts = []

    for rule in notice["audience_rules"]:
        if rule["rule_type"] == "Selected Role":
            audience_parts.append(f"role: {rule['role_name']}")
        elif rule["rule_type"] == "Selected Individual":
            audience_parts.append(
                f"person: {rule['selected_user_name']}"
            )
        else:
            audience_parts.append(rule["rule_type"])

    audience_summary = (
        ", ".join(audience_parts)
        if audience_parts
        else "No audience rules configured"
    )
    schedule = notice["schedule"]

    if schedule is None:
        schedule_summary = "No schedule configured"
    else:
        schedule_parts = [
            schedule["occurrence_basis"],
            schedule["recurrence_pattern"]
        ]

        if schedule["shift_applicability"] != "None":
            schedule_parts.append(schedule["shift_applicability"])

        if schedule["interval_days"] is not None:
            schedule_parts.append(
                f"every {schedule['interval_days']} days"
            )

        if notice["weekdays"]:
            weekday_names = (
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday"
            )
            schedule_parts.append(
                "weekdays: "
                + ", ".join(
                    weekday_names[value] for value in notice["weekdays"]
                )
            )

        if notice["shift_types"]:
            schedule_parts.append(
                "shift types: " + ", ".join(notice["shift_types"])
            )

        if schedule["specific_calendar_date"]:
            schedule_parts.append(
                f"date: {schedule['specific_calendar_date']}"
            )

        if schedule["specific_shift_date"]:
            schedule_parts.append(
                "specific shift: "
                f"{schedule['specific_shift_client_name']}, "
                f"{schedule['specific_shift_date']} "
                f"{schedule['specific_shift_type']}"
            )

        if schedule["recurrence_anchor_date"]:
            schedule_parts.append(
                f"anchored on {schedule['recurrence_anchor_date']}"
            )

        if schedule["one_time_due_at_utc"]:
            schedule_parts.append(
                "due: "
                + format_staff_notice_local_datetime(
                    schedule["one_time_due_at_utc"]
                )
            )

        schedule_summary = "; ".join(schedule_parts)

    period_parts = []

    if notice["effective_start_at_utc"]:
        period_parts.append(
            "starts "
            + format_staff_notice_local_datetime(
                notice["effective_start_at_utc"]
            )
        )

    if notice["until_withdrawn"] == 1:
        period_parts.append("continues until withdrawn")
    elif notice["expires_at_utc"]:
        period_parts.append(
            "expires "
            + format_staff_notice_local_datetime(
                notice["expires_at_utc"]
            )
        )

    return {
        "scope": client_summary,
        "audience": audience_summary,
        "schedule": schedule_summary,
        "period": "; ".join(period_parts) or "No application period set",
        "priority": notice["priority"],
        "state": (
            "Active draft" if notice["draft_active"] == 1
            else "Inactive draft"
        )
    }


def _append_staff_notice_preview_message(messages, message):
    if message not in messages:
        messages.append(message)


def _parse_staff_notice_preview_utc(
    value,
    field_name,
    blocking_errors,
    *,
    required=False
):
    if value is None or value == "":
        if required:
            _append_staff_notice_preview_message(
                blocking_errors,
                f"{field_name} is required before publication."
            )
        return None

    try:
        return parse_staff_notice_utc_datetime(value)
    except (TypeError, ValueError):
        _append_staff_notice_preview_message(
            blocking_errors,
            f"{field_name} is not a valid UTC timestamp."
        )
        return None


def _parse_staff_notice_preview_date(
    value,
    field_name,
    blocking_errors,
    *,
    required=False
):
    if value is None or value == "":
        if required:
            _append_staff_notice_preview_message(
                blocking_errors,
                f"{field_name} is required before publication."
            )
        return None

    if not isinstance(value, str):
        _append_staff_notice_preview_message(
            blocking_errors,
            f"{field_name} must use YYYY-MM-DD."
        )
        return None

    try:
        parsed_value = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        _append_staff_notice_preview_message(
            blocking_errors,
            f"{field_name} must use YYYY-MM-DD."
        )
        return None

    if parsed_value.isoformat() != value:
        _append_staff_notice_preview_message(
            blocking_errors,
            f"{field_name} must use YYYY-MM-DD."
        )
        return None

    return parsed_value


def _load_staff_notice_publish_record(conn, notice_id):
    notice = conn.execute("""
        SELECT
            sn.*,
            c.client_name,
            c.active AS client_active,
            creator.user_id AS created_by_resolved_user_id,
            creator.full_name AS created_by,
            updater.user_id AS updated_by_resolved_user_id,
            updater.full_name AS updated_by
        FROM staff_notices sn
        LEFT JOIN clients c
            ON sn.client_id = c.client_id
        LEFT JOIN users creator
            ON sn.created_by_user_id = creator.user_id
        LEFT JOIN users updater
            ON sn.updated_by_user_id = updater.user_id
        WHERE sn.notice_id = ?
    """, (notice_id,)).fetchone()

    if notice is None:
        return None

    result = dict(notice)
    audiences = conn.execute("""
        SELECT audience_id, notice_id, created_at_utc
        FROM staff_notice_audiences
        WHERE notice_id = ?
        ORDER BY audience_id
    """, (notice_id,)).fetchall()
    result["audience_parent_count"] = len(audiences)
    result["audience"] = (
        dict(audiences[0]) if len(audiences) == 1 else None
    )
    result["audience_rules"] = [
        dict(row)
        for row in conn.execute("""
            SELECT
                ar.*,
                u.full_name AS selected_user_name,
                u.role AS selected_user_role,
                u.active AS selected_user_active
            FROM staff_notice_audience_rules ar
            JOIN staff_notice_audiences a
                ON ar.audience_id = a.audience_id
            LEFT JOIN users u
                ON ar.user_id = u.user_id
            WHERE a.notice_id = ?
            ORDER BY
                ar.audience_rule_id
        """, (notice_id,)).fetchall()
    ]

    schedules = conn.execute("""
        SELECT
            s.*,
            c.client_name AS specific_shift_client_name,
            c.active AS specific_shift_client_active
        FROM staff_notice_schedules s
        LEFT JOIN clients c
            ON s.specific_shift_client_id = c.client_id
        WHERE s.notice_id = ?
        ORDER BY s.schedule_id
    """, (notice_id,)).fetchall()
    result["schedule_parent_count"] = len(schedules)
    result["schedule"] = (
        dict(schedules[0]) if len(schedules) == 1 else None
    )
    result["shift_types"] = []
    result["weekdays"] = []

    if len(schedules) == 1:
        schedule_id = schedules[0]["schedule_id"]
        result["shift_types"] = [
            row["shift_type"]
            for row in conn.execute("""
                SELECT shift_type
                FROM staff_notice_schedule_shift_types
                WHERE schedule_id = ?
                ORDER BY schedule_shift_type_id
            """, (schedule_id,)).fetchall()
        ]
        result["weekdays"] = [
            row["weekday_number"]
            for row in conn.execute("""
                SELECT weekday_number
                FROM staff_notice_schedule_weekdays
                WHERE schedule_id = ?
                ORDER BY schedule_weekday_id
            """, (schedule_id,)).fetchall()
        ]

    return result


def _staff_notice_schedule_applies_on_date(
    schedule,
    candidate_date,
    weekdays,
    recurrence_anchor_date=None,
    once_date=None
):
    pattern = schedule["recurrence_pattern"]

    if pattern == "Daily":
        return True

    if pattern == "Selected Weekdays":
        return candidate_date.weekday() in weekdays

    if pattern == "Interval Days":
        if recurrence_anchor_date is None:
            return False

        difference = (candidate_date - recurrence_anchor_date).days
        interval_days = schedule["interval_days"]
        return (
            type(interval_days) is int
            and interval_days >= 2
            and difference >= 0
            and difference % interval_days == 0
        )

    if pattern == "Once":
        configured_date = (
            schedule.get("specific_calendar_date")
            or schedule.get("specific_shift_date")
        )
        return (
            configured_date == candidate_date.isoformat()
            if configured_date is not None
            else once_date is None or candidate_date == once_date
        )

    return False


def _staff_notice_has_applicable_date(
    schedule,
    first_date,
    last_date,
    weekdays,
    recurrence_anchor_date,
    specific_date
):
    if last_date is not None and first_date > last_date:
        return False

    pattern = schedule["recurrence_pattern"]

    if pattern == "Once" and specific_date is not None:
        return (
            specific_date >= first_date
            and (last_date is None or specific_date <= last_date)
        )

    if pattern in ("Once", "Daily"):
        return True

    if pattern == "Selected Weekdays":
        if not weekdays:
            return False

        days_until_match = min(
            (weekday - first_date.weekday()) % 7
            for weekday in weekdays
        )
        first_match = first_date + timedelta(days=days_until_match)
        return last_date is None or first_match <= last_date

    if pattern == "Interval Days":
        if (
            recurrence_anchor_date is None
            or type(schedule["interval_days"]) is not int
            or schedule["interval_days"] < 2
        ):
            return False

        interval_days = schedule["interval_days"]
        if first_date <= recurrence_anchor_date:
            first_match = recurrence_anchor_date
        else:
            difference = (first_date - recurrence_anchor_date).days
            intervals = (
                difference + interval_days - 1
            ) // interval_days
            first_match = recurrence_anchor_date + timedelta(
                days=intervals * interval_days
            )

        return last_date is None or first_match <= last_date

    return False


def _validate_staff_notice_publication_readiness(
    notice,
    now_utc
):
    blocking_errors = []
    warnings = []
    information = []

    if not isinstance(notice.get("title"), str) or not notice["title"].strip():
        blocking_errors.append("Notice title is required before publication.")

    if (
        not isinstance(notice.get("notice_text"), str)
        or not notice["notice_text"].strip()
    ):
        blocking_errors.append("Notice text is required before publication.")

    if notice.get("priority") not in STAFF_NOTICE_PRIORITIES:
        blocking_errors.append("Notice priority is invalid.")

    if notice.get("status") != "Draft":
        blocking_errors.append("Only a Draft Staff Notice can be published.")

    if type(notice.get("draft_active")) is not int or notice["draft_active"] != 1:
        blocking_errors.append("The Staff Notice draft is not active.")

    if notice.get("client_id") is not None and (
        notice.get("client_name") is None
        or type(notice.get("client_active")) is not int
        or notice["client_active"] != 1
    ):
        blocking_errors.append(
            "The client-specific scope must reference an active client."
        )

    if (
        not _is_valid_staff_notice_identifier(
            notice.get("created_by_user_id")
        )
        or notice.get("created_by_resolved_user_id")
        != notice.get("created_by_user_id")
    ):
        blocking_errors.append(
            "The Staff Notice creator reference could not be resolved."
        )

    if notice.get("updated_by_user_id") is not None and (
        not _is_valid_staff_notice_identifier(
            notice.get("updated_by_user_id")
        )
        or notice.get("updated_by_resolved_user_id")
        != notice.get("updated_by_user_id")
    ):
        blocking_errors.append(
            "The Staff Notice updater reference could not be resolved."
        )

    effective_start = _parse_staff_notice_preview_utc(
        notice.get("effective_start_at_utc"),
        "Effective start",
        blocking_errors,
        required=True
    )
    until_withdrawn = notice.get("until_withdrawn")

    if type(until_withdrawn) is not int or until_withdrawn not in (0, 1):
        blocking_errors.append("Until Withdrawn must be either 0 or 1.")

    expires_at = _parse_staff_notice_preview_utc(
        notice.get("expires_at_utc"),
        "Expiry",
        blocking_errors,
        required=(until_withdrawn == 0)
    )

    if until_withdrawn == 1 and notice.get("expires_at_utc") not in (None, ""):
        blocking_errors.append(
            "An Until Withdrawn notice cannot have an expiry."
        )

    if effective_start is not None and expires_at is not None:
        if expires_at < effective_start:
            blocking_errors.append(
                "Expiry cannot be before the effective start."
            )

    if expires_at is not None and expires_at <= now_utc:
        blocking_errors.append(
            "The notice applicability period has already ended."
        )

    if effective_start is not None and effective_start < now_utc:
        warnings.append(
            "The effective time is already in the past. Publication "
            "will not create requirements for any time before publication."
        )

    lifecycle_fields = (
        "published_by_user_id",
        "published_at_utc",
        "withdrawn_by_user_id",
        "withdrawn_at_utc",
        "withdrawal_reason",
        "replaced_by_user_id",
        "replaced_at_utc",
        "replacement_reason"
    )

    if any(notice.get(field_name) is not None for field_name in lifecycle_fields):
        blocking_errors.append(
            "The Draft contains inconsistent publication lifecycle data."
        )

    audience_parent_count = notice.get("audience_parent_count")

    if audience_parent_count != 1:
        blocking_errors.append(
            "Exactly one audience configuration is required before publication."
        )

    audience_rules = notice.get("audience_rules", [])

    if not audience_rules:
        blocking_errors.append(
            "At least one audience rule is required before publication."
        )

    seen_rule_keys = set()
    has_applicable_shift_staff = False

    for rule in audience_rules:
        rule_type = rule.get("rule_type")
        role_name = rule.get("role_name")
        user_id = rule.get("user_id")

        if rule_type not in STAFF_NOTICE_AUDIENCE_RULE_TYPES:
            _append_staff_notice_preview_message(
                blocking_errors,
                "An audience rule has an invalid rule type."
            )
            continue

        if rule_type == "Selected Role":
            if role_name not in STAFF_NOTICE_SELECTABLE_ROLES or user_id is not None:
                _append_staff_notice_preview_message(
                    blocking_errors,
                    "A Selected Role audience rule is invalid."
                )
            rule_key = (rule_type, role_name)
        elif rule_type == "Selected Individual":
            if (
                not _is_valid_staff_notice_identifier(user_id)
                or role_name is not None
            ):
                _append_staff_notice_preview_message(
                    blocking_errors,
                    "A Selected Individual audience rule is invalid."
                )
            elif (
                rule.get("selected_user_name") is None
                or type(rule.get("selected_user_active")) is not int
                or rule["selected_user_active"] != 1
            ):
                _append_staff_notice_preview_message(
                    blocking_errors,
                    "Every selected individual must exist and be active."
                )
            rule_key = (rule_type, user_id)
        else:
            if role_name is not None or user_id is not None:
                _append_staff_notice_preview_message(
                    blocking_errors,
                    f"The {rule_type} audience rule has invalid fields."
                )
            rule_key = (rule_type,)

        if rule_key in seen_rule_keys:
            _append_staff_notice_preview_message(
                blocking_errors,
                "Duplicate audience rules must be removed before publication."
            )
        seen_rule_keys.add(rule_key)

        if rule_type == "Applicable Shift Staff":
            has_applicable_shift_staff = True

    if notice.get("schedule_parent_count") != 1:
        blocking_errors.append(
            "Exactly one schedule is required before publication."
        )

    schedule = notice.get("schedule")
    effective_local_date = (
        effective_start.astimezone(STAFF_NOTICE_TIMEZONE).date()
        if effective_start is not None else None
    )
    expiry_local_date = (
        expires_at.astimezone(STAFF_NOTICE_TIMEZONE).date()
        if expires_at is not None else None
    )
    publication_local_date = now_utc.astimezone(
        STAFF_NOTICE_TIMEZONE
    ).date()
    first_usable_date = max(
        value
        for value in (effective_local_date, publication_local_date)
        if value is not None
    ) if effective_local_date is not None else publication_local_date

    if schedule is None:
        if has_applicable_shift_staff:
            blocking_errors.append(
                "Applicable Shift Staff requires a Shift schedule."
            )
        return {
            "blocking_errors": tuple(blocking_errors),
            "warnings": tuple(warnings),
            "information": tuple(information),
            "effective_start": effective_start,
            "expires_at": expires_at,
            "first_usable_date": first_usable_date,
            "expiry_local_date": expiry_local_date,
            "recurrence_anchor_date": None,
            "specific_date": None,
            "specific_shift_can_wait": False
        }

    occurrence_basis = schedule.get("occurrence_basis")
    recurrence_pattern = schedule.get("recurrence_pattern")
    shift_applicability = schedule.get("shift_applicability")
    combination = (
        occurrence_basis,
        recurrence_pattern,
        shift_applicability
    )

    if occurrence_basis not in STAFF_NOTICE_OCCURRENCE_BASES:
        blocking_errors.append("Occurrence basis is invalid.")
    if recurrence_pattern not in STAFF_NOTICE_RECURRENCE_PATTERNS:
        blocking_errors.append("Recurrence pattern is invalid.")
    if shift_applicability not in STAFF_NOTICE_SHIFT_APPLICABILITY_VALUES:
        blocking_errors.append("Shift applicability is invalid.")
    if combination not in STAFF_NOTICE_SCHEDULE_COMBINATIONS:
        blocking_errors.append("The schedule combination is invalid.")

    if has_applicable_shift_staff and occurrence_basis != "Shift":
        blocking_errors.append(
            "Applicable Shift Staff requires a Shift schedule."
        )

    interval_days = schedule.get("interval_days")
    recurrence_anchor_date = _parse_staff_notice_preview_date(
        schedule.get("recurrence_anchor_date"),
        "Recurrence anchor date",
        blocking_errors,
        required=(recurrence_pattern == "Interval Days")
    )

    if recurrence_pattern == "Interval Days":
        if type(interval_days) is not int or interval_days < 2:
            blocking_errors.append(
                "Interval Days requires an interval of at least 2."
            )
    elif interval_days is not None:
        blocking_errors.append(
            "Interval days may be used only with Interval Days."
        )

    if (
        recurrence_pattern != "Interval Days"
        and recurrence_anchor_date is not None
    ):
        warnings.append(
            "The recurrence anchor does not affect this recurrence pattern."
        )

    weekdays = notice.get("weekdays", [])
    valid_weekdays = []

    for weekday in weekdays:
        if type(weekday) is not int or not 0 <= weekday <= 6:
            _append_staff_notice_preview_message(
                blocking_errors,
                "A selected weekday is invalid."
            )
        elif weekday in valid_weekdays:
            _append_staff_notice_preview_message(
                blocking_errors,
                "Selected weekdays contain a duplicate."
            )
        else:
            valid_weekdays.append(weekday)

    if recurrence_pattern == "Selected Weekdays":
        if not valid_weekdays:
            blocking_errors.append(
                "Selected Weekdays requires at least one weekday."
            )
    elif weekdays:
        blocking_errors.append(
            "Weekday selections are allowed only for Selected Weekdays."
        )

    shift_types = notice.get("shift_types", [])
    valid_shift_types = []

    for shift_type in shift_types:
        if shift_type not in STAFF_NOTICE_SHIFT_TYPES:
            _append_staff_notice_preview_message(
                blocking_errors,
                "A selected shift type is invalid."
            )
        elif shift_type in valid_shift_types:
            _append_staff_notice_preview_message(
                blocking_errors,
                "Selected shift types contain a duplicate."
            )
        else:
            valid_shift_types.append(shift_type)

    if shift_applicability == "Selected Shift Types":
        if not valid_shift_types:
            blocking_errors.append(
                "Selected Shift Types requires at least one shift type."
            )
    elif shift_types:
        blocking_errors.append(
            "Shift-type selections are allowed only for Selected Shift Types."
        )

    specific_calendar_date = _parse_staff_notice_preview_date(
        schedule.get("specific_calendar_date"),
        "Specific calendar date",
        blocking_errors,
        required=(combination == ("Calendar", "Once", "None"))
    )
    specific_shift_date = _parse_staff_notice_preview_date(
        schedule.get("specific_shift_date"),
        "Specific shift date",
        blocking_errors,
        required=(shift_applicability == "Specific Shift")
    )

    if combination != ("Calendar", "Once", "None") and (
        schedule.get("specific_calendar_date") is not None
    ):
        blocking_errors.append(
            "Specific calendar date is allowed only for Calendar Once."
        )

    specific_shift_fields = (
        schedule.get("specific_shift_client_id"),
        schedule.get("specific_shift_date"),
        schedule.get("specific_shift_type")
    )

    if shift_applicability == "Specific Shift":
        if (
            not _is_valid_staff_notice_identifier(
                schedule.get("specific_shift_client_id")
            )
            or schedule.get("specific_shift_type") not in STAFF_NOTICE_SHIFT_TYPES
        ):
            blocking_errors.append(
                "Specific Shift requires a valid client, date, and shift type."
            )
        if (
            schedule.get("specific_shift_client_name") is None
            or type(schedule.get("specific_shift_client_active")) is not int
            or schedule["specific_shift_client_active"] != 1
        ):
            blocking_errors.append(
                "Specific Shift must reference an active client."
            )
        if (
            notice.get("client_id") is not None
            and notice["client_id"] != schedule.get("specific_shift_client_id")
        ):
            blocking_errors.append(
                "Specific Shift client must match the notice scope."
            )
    elif any(value is not None for value in specific_shift_fields):
        blocking_errors.append(
            "Specific-shift fields are allowed only for Specific Shift."
        )

    due_at = _parse_staff_notice_preview_utc(
        schedule.get("one_time_due_at_utc"),
        "One-time due date",
        blocking_errors
    )

    if due_at is not None and occurrence_basis != "One Time":
        blocking_errors.append(
            "One-time due date may be used only for a One Time notice."
        )
    if due_at is not None and effective_start is not None and due_at < effective_start:
        blocking_errors.append(
            "One-time due date cannot be before the effective start."
        )
    if due_at is not None and expires_at is not None and due_at > expires_at:
        blocking_errors.append(
            "One-time due date cannot be after the notice expiry."
        )
    if due_at is not None and due_at < now_utc:
        warnings.append(
            "The explicit one-time due time is already in the past. "
            "Recipients would be immediately overdue after publication."
        )
    if occurrence_basis == "One Time" and until_withdrawn == 1 and due_at is None:
        warnings.append(
            "This one-time Until Withdrawn notice has no explicit due "
            "date, so acknowledgements will not receive a late classification."
        )

    specific_date = specific_calendar_date or specific_shift_date

    if (
        occurrence_basis == "Shift"
        and recurrence_pattern == "Once"
        and specific_date is None
    ):
        specific_date = effective_local_date
    schedule_shape_is_known = combination in STAFF_NOTICE_SCHEDULE_COMBINATIONS
    specific_shift_can_wait = (
        combination == ("Shift", "Once", "Specific Shift")
        and _is_valid_staff_notice_identifier(
            schedule.get("specific_shift_client_id")
        )
        and schedule.get("specific_shift_client_name") is not None
        and type(schedule.get("specific_shift_client_active")) is int
        and schedule["specific_shift_client_active"] == 1
        and schedule.get("specific_shift_type") in STAFF_NOTICE_SHIFT_TYPES
        and specific_shift_date is not None
        and specific_shift_date >= first_usable_date
        and (
            expiry_local_date is None
            or specific_shift_date <= expiry_local_date
        )
        and (
            notice.get("client_id") is None
            or notice["client_id"]
            == schedule.get("specific_shift_client_id")
        )
    )

    if (
        schedule_shape_is_known
        and effective_start is not None
        and (expires_at is None or expires_at > now_utc)
        and not _staff_notice_has_applicable_date(
            schedule,
            first_usable_date,
            expiry_local_date,
            valid_weekdays,
            recurrence_anchor_date,
            specific_date
        )
    ):
        blocking_errors.append(
            "The schedule has no current or future applicable occurrence."
        )

    if (
        occurrence_basis == "Calendar"
        and effective_start is not None
        and (expires_at is None or expires_at > now_utc)
        and publication_local_date >= effective_local_date
        and (
            expiry_local_date is None
            or publication_local_date <= expiry_local_date
        )
        and _staff_notice_schedule_applies_on_date(
            schedule,
            publication_local_date,
            valid_weekdays,
            recurrence_anchor_date
        )
    ):
        warnings.append(
            "Publishing this Calendar notice today shortens the "
            "acknowledgement window; the normal Vancouver end-of-day "
            "deadline remains."
        )

    return {
        "blocking_errors": tuple(blocking_errors),
        "warnings": tuple(warnings),
        "information": tuple(information),
        "effective_start": effective_start,
        "expires_at": expires_at,
        "first_usable_date": first_usable_date,
        "expiry_local_date": expiry_local_date,
        "recurrence_anchor_date": recurrence_anchor_date,
        "specific_date": specific_date,
        "once_date": specific_date if recurrence_pattern == "Once" else None,
        "specific_shift_can_wait": specific_shift_can_wait
    }


def _resolve_staff_notice_audience_candidates(conn, audience_rules):
    users = [
        dict(row)
        for row in conn.execute("""
            SELECT user_id, full_name, role, active
            FROM users
            ORDER BY full_name, user_id
        """).fetchall()
    ]
    candidates = {}

    for user in users:
        if type(user["active"]) is not int or user["active"] != 1:
            continue

        sources = []

        for rule in audience_rules:
            rule_type = rule.get("rule_type")

            if (
                rule_type == "Core Organization"
                and user["role"] in {
                    "Admin",
                    "Program Manager",
                    "Director",
                    "Support Worker"
                }
            ):
                sources.append("Core Organization")
            elif (
                rule_type == "All Support Workers"
                and user["role"] == "Support Worker"
            ):
                sources.append("All Support Workers")
            elif (
                rule_type == "Selected Role"
                and user["role"] == rule.get("role_name")
            ):
                sources.append(f"Selected Role: {rule.get('role_name')}")
            elif (
                rule_type == "Selected Individual"
                and user["user_id"] == rule.get("user_id")
            ):
                sources.append("Selected Individual")

        if sources:
            candidates[user["user_id"]] = {
                "user_id": user["user_id"],
                "full_name": user["full_name"],
                "role": user["role"],
                "qualification_sources": list(dict.fromkeys(sources))
            }

    return candidates, users


def _load_staff_notice_matching_shifts(
    conn,
    notice,
    validation
):
    schedule = notice["schedule"]
    matching_shifts = []
    blocking_errors = []

    if schedule is None or schedule.get("occurrence_basis") != "Shift":
        return {
            "matching_shifts": matching_shifts,
            "blocking_errors": tuple(blocking_errors)
        }

    for row in conn.execute("""
        SELECT
            s.shift_id,
            s.client_id,
            s.shift_date,
            s.shift_type,
            s.status,
            s.scheduled_start_time,
            s.scheduled_end_time,
            c.client_id AS resolved_client_id,
            c.client_name
        FROM shifts s
        LEFT JOIN clients c
            ON s.client_id = c.client_id
        ORDER BY s.shift_date, s.shift_type, s.shift_id
    """).fetchall():
        shift = dict(row)
        if notice.get("client_id") is not None and (
            shift["client_id"] != notice["client_id"]
        ):
            continue

        applicability = schedule.get("shift_applicability")

        if applicability == "Specific Shift":
            if (
                shift["client_id"]
                != schedule.get("specific_shift_client_id")
                or shift["shift_type"]
                != schedule.get("specific_shift_type")
            ):
                continue
        elif applicability == "Selected Shift Types":
            if shift["shift_type"] not in notice.get("shift_types", []):
                continue
        elif applicability != "Every Shift":
            continue

        shift_date_errors = []
        shift_date = _parse_staff_notice_preview_date(
            shift["shift_date"],
            "Stored shift date",
            shift_date_errors,
            required=True
        )

        if shift_date is None:
            _append_staff_notice_preview_message(
                blocking_errors,
                "A potentially applicable shift has a malformed stored "
                "date and cannot be evaluated safely."
            )
            continue
        if shift_date < validation["first_usable_date"]:
            continue
        if (
            validation["expiry_local_date"] is not None
            and shift_date > validation["expiry_local_date"]
        ):
            continue
        if applicability == "Specific Shift":
            if (
                shift["shift_date"] != schedule.get("specific_shift_date")
            ):
                continue

        if not _staff_notice_schedule_applies_on_date(
            schedule,
            shift_date,
            notice.get("weekdays", []),
            validation["recurrence_anchor_date"],
            validation.get("once_date")
        ):
            continue

        if shift.get("resolved_client_id") is None:
            _append_staff_notice_preview_message(
                blocking_errors,
                "A potentially applicable shift has an unresolved client "
                "reference and cannot be evaluated safely."
            )
            continue

        matching_shifts.append(shift)

    return {
        "matching_shifts": matching_shifts,
        "blocking_errors": tuple(blocking_errors)
    }


def _resolve_staff_notice_preview_recipients(
    conn,
    notice,
    validation
):
    audience_candidates, users = _resolve_staff_notice_audience_candidates(
        conn,
        notice.get("audience_rules", [])
    )
    warnings = []
    information = []
    schedule = notice.get("schedule")

    if schedule is None or schedule.get("occurrence_basis") != "Shift":
        recipients = sorted(
            audience_candidates.values(),
            key=lambda item: (item["full_name"], item["user_id"])
        )

        warnings.append(
            "Current recipients are an estimate. Future eligibility may "
            "change before later recurring or ongoing requirements."
        )

        if not recipients:
            warnings.append(
                "No currently identifiable active recipient matches this audience."
            )

        return {
            "recipients": recipients,
            "recipient_count": len(recipients),
            "matching_shifts": [],
            "matching_shift_count": 0,
            "estimated_delivery_count": len(recipients),
            "audience_candidates": audience_candidates,
            "blocking_errors": tuple(),
            "warnings": tuple(warnings),
            "information": tuple(information)
        }

    shift_resolution = _load_staff_notice_matching_shifts(
        conn,
        notice,
        validation
    )
    matching_shifts = shift_resolution["matching_shifts"]
    blocking_errors = list(shift_resolution["blocking_errors"])
    is_specific_shift = schedule.get("shift_applicability") == "Specific Shift"

    if (
        is_specific_shift
        and validation.get("specific_shift_can_wait")
        and not validation["blocking_errors"]
        and not matching_shifts
        and not blocking_errors
    ):
        warnings.append(
            "No matching shift currently exists. A later publication step "
            "would create a Pending Shift occurrence and wait for the "
            "actual shift and its assignments."
        )
    elif is_specific_shift and len(matching_shifts) > 1:
        blocking_errors.append(
            "More than one matching shift exists for the Specific Shift. "
            "The system cannot safely choose an actual shift."
        )

    warnings.append(
        "Shift recipients are estimates and may change when shift_staff "
        "assignments change."
    )

    if is_specific_shift and len(matching_shifts) > 1:
        for shift in matching_shifts:
            shift["recipients"] = []
            shift["estimated_delivery_count"] = 0
        return {
            "recipients": [],
            "recipient_count": 0,
            "matching_shifts": matching_shifts,
            "matching_shift_count": len(matching_shifts),
            "estimated_delivery_count": 0,
            "audience_candidates": audience_candidates,
            "blocking_errors": tuple(blocking_errors),
            "warnings": tuple(warnings),
            "information": tuple(information)
        }

    user_by_id = {
        user["user_id"]: user
        for user in users
        if type(user["active"]) is int and user["active"] == 1
    }
    has_applicable_shift_staff = any(
        rule.get("rule_type") == "Applicable Shift Staff"
        for rule in notice.get("audience_rules", [])
    )
    all_recipients = {}
    estimated_delivery_count = 0

    for shift in matching_shifts:
        assigned_user_ids = []

        for row in conn.execute("""
            SELECT
                ss.user_id,
                ss.active,
                u.user_id AS resolved_user_id
            FROM shift_staff ss
            LEFT JOIN users u
                ON ss.user_id = u.user_id
            WHERE ss.shift_id = ?
            ORDER BY ss.shift_staff_id
        """, (shift["shift_id"],)).fetchall():
            if (
                type(row["active"]) is int
                and row["active"] == 1
                and row["resolved_user_id"] is None
            ):
                _append_staff_notice_preview_message(
                    blocking_errors,
                    "An active assignment on a matching shift references "
                    "a missing user and cannot be evaluated safely."
                )
                continue
            if (
                type(row["active"]) is int
                and row["active"] == 1
                and row["user_id"] in user_by_id
                and row["user_id"] not in assigned_user_ids
            ):
                assigned_user_ids.append(row["user_id"])

        shift_recipients = []

        for user_id in assigned_user_ids:
            sources = []

            if user_id in audience_candidates:
                sources.extend(
                    audience_candidates[user_id]["qualification_sources"]
                )
            if has_applicable_shift_staff:
                sources.append("Applicable Shift Staff")
            if not sources:
                continue

            user = user_by_id[user_id]
            recipient = {
                "user_id": user_id,
                "full_name": user["full_name"],
                "role": user["role"],
                "qualification_sources": list(dict.fromkeys(sources))
            }
            shift_recipients.append(recipient)

            if user_id not in all_recipients:
                all_recipients[user_id] = {
                    **recipient,
                    "matching_shift_ids": []
                }
            all_recipients[user_id]["matching_shift_ids"].append(
                shift["shift_id"]
            )

        shift_recipients.sort(
            key=lambda item: (item["full_name"], item["user_id"])
        )
        shift["recipients"] = shift_recipients
        shift["estimated_delivery_count"] = len(shift_recipients)
        estimated_delivery_count += len(shift_recipients)

    recipients = sorted(
        all_recipients.values(),
        key=lambda item: (item["full_name"], item["user_id"])
    )

    if not recipients:
        warnings.append(
            "No currently identifiable active recipient matches the "
            "current shift assignments."
        )

    return {
        "recipients": recipients,
        "recipient_count": len(recipients),
        "matching_shifts": matching_shifts,
        "matching_shift_count": len(matching_shifts),
        "estimated_delivery_count": estimated_delivery_count,
        "audience_candidates": audience_candidates,
        "blocking_errors": tuple(blocking_errors),
        "warnings": tuple(warnings),
        "information": tuple(information)
    }


def _build_staff_notice_publish_preview(
    conn,
    notice_id,
    actor_user_id,
    now_utc
):
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise PermissionError("Staff Notice management access denied.")
    if not _is_valid_staff_notice_identifier(notice_id):
        raise StaffNoticeNotFoundError("Staff Notice draft not found.")

    now_utc = parse_staff_notice_utc_datetime(now_utc)

    actor = conn.execute("""
        SELECT user_id, role, active
        FROM users
        WHERE user_id = ?
    """, (actor_user_id,)).fetchone()

    if (
        actor is None
        or type(actor["active"]) is not int
        or actor["active"] != 1
        or not user_can_manage_staff_notices({
            "user_id": actor["user_id"],
            "role": actor["role"]
        })
    ):
        raise PermissionError("Staff Notice management access denied.")

    notice = _load_staff_notice_publish_record(conn, notice_id)

    if notice is None:
        raise StaffNoticeNotFoundError("Staff Notice draft not found.")
    if notice["status"] != "Draft" or notice["draft_active"] != 1:
        raise StaffNoticeNotEditableError(
            "Staff Notice draft is not available for publication review."
        )

    validation = _validate_staff_notice_publication_readiness(
        notice,
        now_utc
    )
    resolution = _resolve_staff_notice_preview_recipients(
        conn,
        notice,
        validation
    )
    blocking_errors = list(validation["blocking_errors"])
    warnings = list(validation["warnings"])
    information = list(validation["information"])

    for message in resolution["blocking_errors"]:
        _append_staff_notice_preview_message(blocking_errors, message)
    for message in resolution["warnings"]:
        _append_staff_notice_preview_message(warnings, message)
    for message in resolution["information"]:
        _append_staff_notice_preview_message(information, message)

    if notice["schedule"] is None:
        acknowledgement_description = (
            "Acknowledgement frequency cannot be determined until a "
            "complete schedule is configured."
        )
    elif notice["schedule"]["occurrence_basis"] == "One Time":
        acknowledgement_description = (
            "Every assigned recipient will require one acknowledgement."
        )
    elif notice["schedule"]["occurrence_basis"] == "Calendar":
        acknowledgement_description = (
            "Every assigned recipient will require a separate "
            "acknowledgement for each applicable calendar occurrence."
        )
    elif notice["schedule"]["occurrence_basis"] == "Shift":
        acknowledgement_description = (
            "Every assigned recipient will require a separate "
            "acknowledgement for each applicable actual shift. No final "
            "shift deadline is claimed by this preview."
        )
    else:
        acknowledgement_description = (
            "Acknowledgement frequency cannot be determined from the "
            "saved schedule."
        )

    try:
        summary = build_staff_notice_plain_language_summary(notice)
    except (IndexError, KeyError, TypeError, ValueError):
        summary = {
            "scope": (
                notice.get("client_name")
                if notice.get("client_id") is not None
                else "Organization-wide"
            ),
            "audience": "See publication-readiness findings",
            "schedule": "See publication-readiness findings",
            "period": "See publication-readiness findings",
            "priority": notice.get("priority") or "Unknown",
            "state": "Active draft"
        }

    return {
        "notice": notice,
        "summary": summary,
        "preview_generated_at_local": format_staff_notice_local_datetime(
            now_utc
        ),
        "effective_start_local": (
            format_staff_notice_local_datetime(
                notice["effective_start_at_utc"]
            ) if validation["effective_start"] is not None else None
        ),
        "expires_local": (
            format_staff_notice_local_datetime(
                notice["expires_at_utc"]
            ) if validation["expires_at"] is not None else None
        ),
        "blocking_errors": tuple(blocking_errors),
        "warnings": tuple(warnings),
        "information": tuple(information),
        "ready_for_publication": not blocking_errors,
        "acknowledgement_description": acknowledgement_description,
        "recipients": resolution["recipients"],
        "recipient_count": resolution["recipient_count"],
        "matching_shifts": resolution["matching_shifts"],
        "matching_shift_count": resolution["matching_shift_count"],
        "estimated_delivery_count": resolution[
            "estimated_delivery_count"
        ],
        "_publication_audience_candidates": resolution[
            "audience_candidates"
        ]
    }


def get_staff_notice_publish_preview(
    notice_id,
    actor_user_id,
    now_utc=None
):
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise PermissionError("Staff Notice management access denied.")
    if not _is_valid_staff_notice_identifier(notice_id):
        raise StaffNoticeNotFoundError("Staff Notice draft not found.")

    if now_utc is None:
        now_utc = get_application_now_utc()
    else:
        now_utc = parse_staff_notice_utc_datetime(now_utc)

    conn = get_db()

    try:
        preview = _build_staff_notice_publish_preview(
            conn,
            notice_id,
            actor_user_id,
            now_utc
        )
    finally:
        conn.close()

    preview.pop("_publication_audience_candidates", None)
    return preview


def _create_initial_staff_notice_eligibility_periods(
    conn,
    preview,
    actor_user_id,
    opened_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice eligibility creation requires an active "
            "transaction."
        )

    audience_id = preview["notice"]["audience"]["audience_id"]
    candidates = preview["_publication_audience_candidates"]

    for user_id in sorted(candidates):
        sources = list(dict.fromkeys(
            candidates[user_id]["qualification_sources"]
        ))
        conn.execute("""
            INSERT INTO staff_notice_audience_eligibility_periods
            (
                audience_id,
                user_id,
                eligible_from_at_utc,
                eligible_until_at_utc,
                eligibility_source_summary,
                opened_by_user_id,
                closed_by_user_id,
                close_reason,
                created_at_utc,
                updated_at_utc
            )
            VALUES (?, ?, ?, NULL, ?, ?, NULL, NULL, ?, NULL)
        """, (
            audience_id,
            user_id,
            opened_at_utc,
            ", ".join(sources),
            actor_user_id,
            opened_at_utc
        ))


def _publish_staff_notice_in_transaction(
    conn,
    notice_id,
    actor_user_id,
    now_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice publication requires an active transaction."
        )

    now_utc = parse_staff_notice_utc_datetime(now_utc)
    preview = _build_staff_notice_publish_preview(
        conn,
        notice_id,
        actor_user_id,
        now_utc
    )

    if not preview["ready_for_publication"]:
        raise StaffNoticePublicationNotReadyError(
            preview["blocking_errors"]
        )

    published_at_utc = format_staff_notice_utc_datetime(now_utc)
    _create_initial_staff_notice_eligibility_periods(
        conn,
        preview,
        actor_user_id,
        published_at_utc
    )
    cursor = conn.execute("""
        UPDATE staff_notices
        SET status = 'Published',
            draft_active = 0,
            published_by_user_id = ?,
            published_at_utc = ?
        WHERE notice_id = ?
          AND status = 'Draft'
          AND draft_active = 1
          AND published_by_user_id IS NULL
          AND published_at_utc IS NULL
    """, (
        actor_user_id,
        published_at_utc,
        notice_id
    ))

    if cursor.rowcount != 1:
        raise StaffNoticeStalePublicationError(
            "Staff Notice publication state changed before it could be "
            "published. Reload it and try again."
        )

    return {
        "notice_id": notice_id,
        "published_by_user_id": actor_user_id,
        "published_at_utc": published_at_utc,
        "_publication_preview": preview
    }


def publish_staff_notice(notice_id, actor_user_id):
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise PermissionError("Staff Notice management access denied.")
    if not _is_valid_staff_notice_identifier(notice_id):
        raise StaffNoticeNotFoundError("Staff Notice draft not found.")

    conn = None
    primary_error = None
    commit_succeeded = False

    try:
        conn = get_db()
        conn.execute("BEGIN IMMEDIATE")
        result = _publish_staff_notice_in_transaction(
            conn,
            notice_id,
            actor_user_id,
            get_application_now_utc()
        )
        conn.commit()
        commit_succeeded = True
        result.pop("_publication_preview", None)
        return result

    except BaseException as error:
        primary_error = error

        if conn is not None:
            try:
                if conn.in_transaction:
                    conn.rollback()
            except BaseException as rollback_error:
                _preserve_staff_notice_cleanup_error(
                    error,
                    "staff_notice_rollback_error",
                    rollback_error,
                    "Staff Notice publication rollback also failed: "
                    f"{rollback_error}"
                )

        raise

    finally:
        if conn is not None:
            try:
                conn.close()
            except BaseException as close_error:
                if primary_error is None:
                    if commit_succeeded:
                        raise StaffNoticePublicationCommittedCloseError(
                            notice_id
                        ) from close_error

                    raise

                _preserve_staff_notice_cleanup_error(
                    primary_error,
                    "staff_notice_close_error",
                    close_error,
                    "Staff Notice database close also failed: "
                    f"{close_error}"
                )


def _staff_notice_management_choices(conn, notice=None):
    existing_client_ids = set()
    existing_user_ids = set()

    if notice is not None:
        if notice["client_id"] is not None:
            existing_client_ids.add(notice["client_id"])

        schedule = notice["schedule"]

        if schedule and schedule["specific_shift_client_id"] is not None:
            existing_client_ids.add(schedule["specific_shift_client_id"])

        existing_user_ids.update(
            rule["user_id"]
            for rule in notice["audience_rules"]
            if rule["user_id"] is not None
        )

    clients = [
        dict(row)
        for row in conn.execute("""
            SELECT client_id, client_name, active
            FROM clients
            ORDER BY client_name
        """).fetchall()
        if row["active"] == 1 or row["client_id"] in existing_client_ids
    ]
    users = [
        dict(row)
        for row in conn.execute("""
            SELECT user_id, full_name, role, active
            FROM users
            ORDER BY full_name
        """).fetchall()
        if row["active"] == 1 or row["user_id"] in existing_user_ids
    ]

    return {
        "clients": clients,
        "users": users,
        "selectable_roles": sorted(STAFF_NOTICE_SELECTABLE_ROLES),
        "priorities": sorted(STAFF_NOTICE_PRIORITIES),
        "audience_rule_types": sorted(
            STAFF_NOTICE_AUDIENCE_RULE_TYPES
        ),
        "occurrence_bases": sorted(STAFF_NOTICE_OCCURRENCE_BASES),
        "recurrence_patterns": sorted(
            STAFF_NOTICE_RECURRENCE_PATTERNS
        ),
        "shift_applicability_values": sorted(
            STAFF_NOTICE_SHIFT_APPLICABILITY_VALUES
        ),
        "shift_types": ("Day", "Afternoon", "Overnight"),
        "weekdays": (
            (0, "Monday"),
            (1, "Tuesday"),
            (2, "Wednesday"),
            (3, "Thursday"),
            (4, "Friday"),
            (5, "Saturday"),
            (6, "Sunday")
        )
    }


def _staff_notice_management_access_response():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if not user_can_manage_staff_notices():
        return "Access denied", 403

    return None


def _render_staff_notice_form(
    template_name,
    form_data,
    *,
    error=None,
    notice=None,
    expected_updated_at_utc=None,
    status_code=200
):
    conn = get_db()

    try:
        choices = _staff_notice_management_choices(conn, notice)
    finally:
        conn.close()

    return render_template(
        template_name,
        form_data=form_data,
        error=error,
        notice=notice,
        expected_updated_at_utc=expected_updated_at_utc,
        **choices
    ), status_code


#####################################################################
# STAFF NOTICES — DRAFT MANAGEMENT
#####################################################################

@app.route("/staff-notices/manage")
def staff_notice_admin_list():
    access_response = _staff_notice_management_access_response()

    if access_response is not None:
        return access_response

    conn = get_db()

    try:
        notices = conn.execute("""
            SELECT
                sn.notice_id,
                sn.title,
                sn.priority,
                sn.status,
                sn.draft_active,
                sn.created_at_utc,
                sn.updated_at_utc,
                c.client_name,
                u.full_name AS created_by
            FROM staff_notices sn
            LEFT JOIN clients c
                ON sn.client_id = c.client_id
            JOIN users u
                ON sn.created_by_user_id = u.user_id
            WHERE sn.status = 'Draft'
            ORDER BY sn.draft_active DESC,
                     COALESCE(sn.updated_at_utc, sn.created_at_utc) DESC,
                     sn.notice_id DESC
        """).fetchall()
    finally:
        conn.close()

    return render_template(
        "staff_notice_admin_list.html",
        notices=notices
    )


@app.route("/staff-notices/new", methods=["GET", "POST"])
def staff_notice_new():
    access_response = _staff_notice_management_access_response()

    if access_response is not None:
        return access_response

    if request.method == "POST":
        form_data = _staff_notice_form_state(request.form)

        try:
            payload = build_staff_notice_draft_payload_from_form(
                request.form
            )
            validate_staff_notice_management_draft(payload)
            notice_id = create_staff_notice_draft(
                payload,
                session["user_id"]
            )
        except StaffNoticeDraftCommittedCloseError as error:
            return redirect(url_for(
                "staff_notice_admin_detail",
                notice_id=error.notice_id
            ))
        except ValueError as error:
            return _render_staff_notice_form(
                "staff_notice_new.html",
                form_data,
                error=str(error),
                status_code=400
            )
        except PermissionError:
            return "Access denied", 403
        except Exception:
            return _render_staff_notice_form(
                "staff_notice_new.html",
                form_data,
                error=(
                    "The Staff Notice draft could not be saved. "
                    "No changes were made."
                ),
                status_code=500
            )

        return redirect(url_for(
            "staff_notice_admin_detail",
            notice_id=notice_id
        ))

    return _render_staff_notice_form(
        "staff_notice_new.html",
        {
            "title": "",
            "notice_text": "",
            "priority": "Normal",
            "client_id": "",
            "effective_start_local": "",
            "expires_local": "",
            "until_withdrawn": False,
            "audience_rule_types": [],
            "selected_roles": [],
            "selected_user_ids": [],
            "schedule_enabled": False,
            "occurrence_basis": "",
            "recurrence_pattern": "",
            "shift_applicability": "",
            "interval_days": "",
            "recurrence_anchor_date": "",
            "specific_calendar_date": "",
            "specific_shift_client_id": "",
            "specific_shift_date": "",
            "specific_shift_type": "",
            "one_time_due_local": "",
            "shift_types": [],
            "weekdays": []
        }
    )


@app.route("/staff-notices/manage/<int:notice_id>")
def staff_notice_admin_detail(notice_id):
    access_response = _staff_notice_management_access_response()

    if access_response is not None:
        return access_response

    conn = get_db()

    try:
        notice = _load_staff_notice_admin_record(conn, notice_id)
    finally:
        conn.close()

    if notice is None or notice["status"] != "Draft":
        return "Staff Notice draft not found", 404

    return render_template(
        "staff_notice_admin_detail.html",
        notice=notice,
        summary=build_staff_notice_plain_language_summary(notice)
    )


@app.route("/staff-notices/<int:notice_id>/review")
def staff_notice_publish_review(notice_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        preview = get_staff_notice_publish_preview(
            notice_id,
            session["user_id"]
        )
    except PermissionError:
        return "Access denied", 403
    except StaffNoticeNotFoundError:
        return "Staff Notice draft not found", 404
    except StaffNoticeNotEditableError:
        return (
            "Staff Notice draft is not available for publication review",
            409
        )

    return render_template(
        "staff_notice_publish_review.html",
        **preview
    )


@app.route(
    "/staff-notices/manage/<int:notice_id>/edit",
    methods=["GET", "POST"]
)
def staff_notice_edit(notice_id):
    access_response = _staff_notice_management_access_response()

    if access_response is not None:
        return access_response

    conn = get_db()

    try:
        notice = _load_staff_notice_admin_record(conn, notice_id)
    finally:
        conn.close()

    if notice is None:
        return "Staff Notice draft not found", 404

    if notice["status"] != "Draft" or notice["draft_active"] != 1:
        return "Staff Notice draft is not editable", 409

    expected_token = _staff_notice_draft_token(notice)

    if request.method == "POST":
        form_data = _staff_notice_form_state(request.form)
        submitted_token = ""

        try:
            payload = build_staff_notice_draft_payload_from_form(
                request.form,
                edit=True
            )
            submitted_token = _staff_notice_single_form_value(
                request.form,
                "expected_updated_at_utc",
                required=True
            )
            update_staff_notice_draft(
                notice_id,
                payload,
                session["user_id"],
                submitted_token
            )
        except StaffNoticeDraftChangeCommittedCloseError as error:
            return redirect(url_for(
                "staff_notice_admin_detail",
                notice_id=error.notice_id
            ))
        except StaffNoticeNotFoundError:
            return "Staff Notice draft not found", 404
        except (StaffNoticeNotEditableError, StaffNoticeStaleEditError) as error:
            return _render_staff_notice_form(
                "staff_notice_edit.html",
                form_data,
                error=str(error),
                notice=notice,
                expected_updated_at_utc=submitted_token,
                status_code=409
            )
        except ValueError as error:
            return _render_staff_notice_form(
                "staff_notice_edit.html",
                form_data,
                error=str(error),
                notice=notice,
                expected_updated_at_utc=submitted_token,
                status_code=400
            )
        except PermissionError:
            return "Access denied", 403
        except Exception:
            return _render_staff_notice_form(
                "staff_notice_edit.html",
                form_data,
                error=(
                    "The Staff Notice draft could not be updated. "
                    "No changes were made."
                ),
                notice=notice,
                expected_updated_at_utc=submitted_token,
                status_code=500
            )

        return redirect(url_for(
            "staff_notice_admin_detail",
            notice_id=notice_id
        ))

    return _render_staff_notice_form(
        "staff_notice_edit.html",
        _staff_notice_form_data_from_record(notice),
        notice=notice,
        expected_updated_at_utc=expected_token
    )


@app.route(
    "/staff-notices/manage/<int:notice_id>/draft/deactivate",
    methods=["POST"]
)
def staff_notice_draft_deactivate(notice_id):
    access_response = _staff_notice_management_access_response()

    if access_response is not None:
        return access_response

    try:
        deactivate_staff_notice_draft(
            notice_id,
            session["user_id"]
        )
    except StaffNoticeDraftChangeCommittedCloseError:
        return redirect(url_for(
            "staff_notice_admin_detail",
            notice_id=notice_id
        ))
    except StaffNoticeNotFoundError:
        return "Staff Notice draft not found", 404
    except StaffNoticeNotEditableError:
        return "Staff Notice draft is not editable", 409
    except PermissionError:
        return "Access denied", 403
    except Exception:
        return (
            "The Staff Notice draft could not be deactivated. "
            "No changes were made.",
            500
        )

    return redirect(url_for(
        "staff_notice_admin_detail",
        notice_id=notice_id
    ))

def get_current_shift_type():
    now = datetime.now()
    hour = now.hour

    if 7 <= hour < 15:
        return "Day"
    elif 15 <= hour < 23:
        return "Afternoon"
    else:
        return "Overnight"
    

def get_active_shift_staff():
    shift_type = get_current_shift_type()
    shift_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()

    active_staff = conn.execute("""
        SELECT
            ss.shift_staff_id,
            ss.shift_id,
            ss.actual_start_time,
            ss.start_checklist_completed,
            u.full_name,
            s.shift_date,
            s.shift_type
        FROM shift_staff ss

        JOIN users u
            ON ss.user_id = u.user_id

        JOIN shifts s
            ON ss.shift_id = s.shift_id

        WHERE ss.active = 1
          AND s.status = 'Open'
          AND s.shift_date = ?
          AND s.shift_type = ?
          AND u.role = 'Support Worker'

        ORDER BY ss.actual_start_time
    """, (
        shift_date,
        shift_type
    )).fetchall()

    conn.close()

    return active_staff

def get_management_inbox():
    conn = get_db()

    high_priority_actions = conn.execute("""
        SELECT action_id, title, due_date, priority
        FROM action_items
        WHERE status NOT IN ('Completed', 'Closed')
          AND priority IN ('High', 'Urgent')
        ORDER BY 
            CASE WHEN priority='Urgent' THEN 1 ELSE 2 END,
            due_date
        LIMIT 5
    """).fetchall()

    # FIXED: using shift_date and user_id like your original query
    notes_to_review_list = conn.execute("""
        SELECT
            sn.note_id,
            sn.shift_date,    -- CHANGED from note_date
            sn.shift_type,    -- ADDED back for template
            c.client_name,
            u.full_name
        FROM shift_notes sn
        LEFT JOIN clients c ON sn.client_id = c.client_id
        LEFT JOIN users u ON sn.user_id = u.user_id  -- you have user_id not author_user_id

        WHERE NOT EXISTS (
            SELECT 1
            FROM acknowledgements ack
            WHERE ack.source_table = 'shift_notes'
              AND ack.source_id = sn.note_id
              AND ack.active = 1
        )
        ORDER BY sn.shift_date DESC  -- CHANGED from note_date
        LIMIT 5
    """).fetchall()

    recent_incidents = conn.execute("""
        SELECT
            ir.incident_id,
            ir.incident_type,
            ir.incident_date,
            ir.incident_time,
            c.client_name
        FROM incident_reports ir
        LEFT JOIN clients c ON ir.client_id = c.client_id
        ORDER BY ir.incident_date DESC, ir.incident_time DESC
        LIMIT 5
    """).fetchall()

    recent_activity_list = conn.execute("""
        SELECT
            al.activity_datetime,
            al.activity_type,
            al.summary,
            u.full_name
        FROM activity_log al
        LEFT JOIN users u ON al.user_id = u.user_id
        ORDER BY al.activity_datetime DESC
        LIMIT 5
    """).fetchall()

    conn.close()

    return {
        "high_priority_actions": [dict(r) for r in high_priority_actions],
        "notes_to_review_list": [dict(r) for r in notes_to_review_list],
        "recent_incidents": [dict(r) for r in recent_incidents],
        "recent_activity_list": [dict(r) for r in recent_activity_list]
    }

def get_manager_alerts():

    alerts = []

    conn = get_db()

    #
    # Forgotten Sign Offs
    #
    forgotten = conn.execute("""
        SELECT
            ss.shift_staff_id,
            u.full_name,
            s.shift_date,
            s.shift_type
        FROM shift_staff ss

        JOIN users u
            ON ss.user_id = u.user_id

        JOIN shifts s
            ON ss.shift_id = s.shift_id

        WHERE ss.active = 1
          AND s.shift_date < date('now')

        ORDER BY s.shift_date
    """).fetchall()

    for row in forgotten:
        alerts.append({
            "level": "danger",
            "title": "Forgotten Sign Off",
            "message":
                f"{row['full_name']} is still signed onto the "
                f"{row['shift_type']} shift on {row['shift_date']}.",
            "shift_staff_id": row["shift_staff_id"]
        })

    conn.close()

    return alerts

def user_can_reset_password():
    return session.get("role") in ["Admin", "Director", "Program Manager"]

#####################################################################
# AUTHENTICATION / LOGIN / PASSWORDS
#####################################################################

@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND active = 1",
            (username,)
        ).fetchone()
        conn.close()


        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["user_id"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            session["last_activity"] = time.time()

            conn = get_db()

            log_activity(
                conn,
                activity_class="LOGIN",
                activity_type="user_login",
                summary=f"User logged in: {user['full_name']}",
                user_id=user["user_id"],
                success=1
            )

            conn.commit()
            conn.close()

            if user["must_change_password"] == 1:
                return redirect(url_for("change_password"))

            return redirect(url_for("dashboard"))

        else:
            error = "Invalid username or password."

    return render_template("login.html", error=error)

@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (session["user_id"],)
    ).fetchone()

    error = None

    if request.method == "POST":
        new_password = request.form["new_password"]
        confirm_password = request.form["confirm_password"]

        if new_password != confirm_password:
            error = "Passwords do not match."
        elif len(new_password) < 6:
            error = "Password must be at least 6 characters."
        else:
            password_hash = generate_password_hash(new_password)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn.execute(
                """
                UPDATE users
                SET password_hash = ?,
                    must_change_password = 0,
                    last_password_changed_at = ?
                WHERE user_id = ?
                """,
                (password_hash, now, session["user_id"])
            )

            log_activity(
                conn,
                activity_class="USER",
                activity_type="password_changed",
                summary=f"User changed password: {session.get('full_name')}",
                user_id=session["user_id"],
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(url_for("dashboard"))

    conn.close()

    return render_template(
        "change_password.html",
        user=user,
        error=error
    )

@app.route("/logout")
def logout():
    if "user_id" in session:
        conn = get_db()

        log_activity(
            conn,
            activity_class="LOGIN",
            activity_type="user_logout",
            summary=f"User logged out: {session.get('full_name')}",
            user_id=session["user_id"],
            success=1
        )

        conn.commit()
        conn.close()

    session.clear()
    return redirect(url_for("login"))

@app.route("/user/reset-password/<int:user_id>", methods=["GET", "POST"])
def reset_user_password(user_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if not user_can_reset_password():
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        conn.close()
        return "User not found", 404

    error = None

    if request.method == "POST":
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        if password != confirm_password:
            error = "Passwords do not match."
        elif len(password) < 6:
            error = "Temporary password must be at least 6 characters."
        else:
            password_hash = generate_password_hash(password)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn.execute(
                """
                UPDATE users
                SET password_hash = ?,
                    must_change_password = 1,
                    password_reset_at = ?,
                    password_reset_by = ?
                WHERE user_id = ?
                """,
                (
                    password_hash,
                    now,
                    session["user_id"],
                    user_id
                )
            )

            log_activity(
                conn,
                activity_class="USER",
                activity_type="password_reset",
                summary=f"Password reset for {user['full_name']} by {session.get('full_name')}",
                user_id=session["user_id"],
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(url_for("users", status=status_filter))

    conn.close()

    return render_template(
        "user_reset_password.html",
        user=user,
        error=error,
        status_filter=status_filter
    )

#####################################################################
# DASHBOARDS
#####################################################################

def get_dashboard_stats():
    conn = get_db()

    outstanding_action_count = conn.execute("""
        SELECT COUNT(*) AS count
        FROM action_items
        WHERE status NOT IN ('Completed', 'Closed')
    """).fetchone()["count"]

    outstanding_actions = conn.execute("""
        SELECT
            ai.action_id,
            ai.title,
            ai.priority,
            ai.status,
            u.full_name AS assigned_to
        FROM action_items ai

        LEFT JOIN users u
            ON ai.assigned_to_user_id = u.user_id

        WHERE ai.status NOT IN ('Completed', 'Closed')

        ORDER BY
            CASE ai.priority
                WHEN 'High' THEN 1
                WHEN 'Medium' THEN 2
                WHEN 'Low' THEN 3
                ELSE 4
            END,
            ai.created_at

        LIMIT 5
    """).fetchall()

    notes_to_review = conn.execute("""
        SELECT COUNT(*) AS count
        FROM shift_notes sn
        WHERE NOT EXISTS (
            SELECT 1
            FROM acknowledgements ack
            WHERE ack.source_table = 'shift_notes'
              AND ack.source_id = sn.note_id
              AND ack.active = 1
        )
    """).fetchone()["count"]

    open_incidents = conn.execute("""
        SELECT COUNT(*) AS count
        FROM incident_reports
    """).fetchone()["count"]

    recent_activity = conn.execute("""
        SELECT COUNT(*) AS count
        FROM activity_log
        WHERE activity_datetime >= datetime('now', '-1 day')
    """).fetchone()["count"]

    conn.close()

    return {
        "outstanding_action_count": outstanding_action_count,
        "outstanding_actions": outstanding_actions,
        "notes_to_review": notes_to_review,
        "open_incidents": open_incidents,
        "recent_activity": recent_activity
    }

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] in ["Admin", "Program Manager", "Director"]:
        stats = get_dashboard_stats()
        inbox = get_management_inbox()
        active_staff = get_active_shift_staff()
        manager_alerts = get_manager_alerts()

        return render_template(
            "admin_dashboard.html",
            active_staff=active_staff,
            manager_alerts=manager_alerts,
            **stats,
            **inbox
        )

    shift_id, start_checklist_completed = auto_sign_on_user(session["user_id"])

    if not start_checklist_completed:
        return redirect(url_for("start_checklist", shift_id=shift_id))

    return redirect(url_for("shift_dashboard", shift_id=shift_id))

@app.route("/manager-alerts")
def manager_alerts():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    alerts = get_manager_alerts()

    return render_template(
        "manager_alerts.html",
        alerts=alerts
    )

#####################################################################
# USER MANAGEMENT
#####################################################################

@app.route("/users")
def users():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    query = """
        SELECT user_id, username, full_name, role, active
        FROM users
    """

    parameters = ()

    if active_filter is not None:
        query += """
            WHERE active = ?
        """
        parameters = (active_filter,)

    query += """
        ORDER BY full_name
    """

    users = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "users.html",
        users=users,
        status_filter=status_filter
    )
    
@app.route("/user/new", methods=["GET", "POST"])
def user_new():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    error = None

    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form["full_name"].strip()
        role = request.form["role"]
        password = request.form["password"]

        if not username or not full_name or not password:
            error = "Username, full name, and password are required."
        else:
            from werkzeug.security import generate_password_hash

            conn = get_db()

            try:
                cur = conn.execute("""
                    INSERT INTO users
                    (username, password_hash, full_name, role, active, must_change_password)
                    VALUES (?, ?, ?, ?, 1, 1)
                """, (
                    username,
                    generate_password_hash(password),
                    full_name,
                    role
                ))

                new_user_id = cur.lastrowid

                log_activity(
                    conn,
                    activity_class="USER",
                    activity_type="user_created",
                    summary=f"User created: {full_name} ({role})",
                    user_id=session["user_id"],
                    related_table="users",
                    related_id=new_user_id,
                    details=f"Username: {username}; Role: {role}",
                    success=1
                )

                conn.commit()
                conn.close()

                return redirect(url_for("users"))

            except sqlite3.IntegrityError:
                conn.close()
                error = "That username already exists."

    return render_template("user_new.html", error=error)

@app.route("/user/edit/<int:user_id>", methods=["GET", "POST"])
def user_edit(user_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    user = conn.execute("""
        SELECT user_id, username, full_name, role, active
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    if user is None:
        conn.close()
        return "User not found", 404

    error = None

    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form["full_name"].strip()
        role = request.form["role"]
        active = 1 if "active" in request.form else 0

        if not username or not full_name:
            error = "Username and full name are required."
        else:
            try:
                conn.execute("""
                    UPDATE users
                    SET username = ?, full_name = ?, role = ?, active = ?
                    WHERE user_id = ?
                """, (
                    username,
                    full_name,
                    role,
                    active,
                    user_id
                ))

                details = (
                    f"Username: {user['username']} → {username}; "
                    f"Full name: {user['full_name']} → {full_name}; "
                    f"Role: {user['role']} → {role}; "
                    f"Active: {user['active']} → {active}"
                )

                log_activity(
                    conn,
                    activity_class="USER",
                    activity_type="user_updated",
                    summary=f"User updated: {full_name}",
                    user_id=session["user_id"],
                    related_table="users",
                    related_id=user_id,
                    details=details,
                    success=1
                )

                conn.commit()
                conn.close()

                return redirect(url_for("users", status=status_filter))

            except sqlite3.IntegrityError:
                error = "That username already exists."

    conn.close()

    return render_template(
        "user_edit.html",
        user=user,
        error=error,
        status_filter=status_filter
    )

#####################################################################
# CLIENT MANAGEMENT
#####################################################################

@app.route("/clients")
def clients():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    query = """
        SELECT client_id, client_name, active
        FROM clients
    """

    parameters = ()

    if active_filter is not None:
        query += """
            WHERE active = ?
        """
        parameters = (active_filter,)

    query += """
        ORDER BY client_name
    """

    clients = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "clients.html",
        clients=clients,
        status_filter=status_filter
    )

@app.route("/client/new", methods=["GET", "POST"])
def client_new():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    error = None

    if request.method == "POST":
        client_name = request.form["client_name"].strip()

        if not client_name:
            error = "Client name is required."
        else:
            conn = get_db()

            cur = conn.execute("""
                INSERT INTO clients
                (client_name, active)
                VALUES (?, 1)
            """, (client_name,))

            client_id = cur.lastrowid

            log_activity(
                conn,
                activity_class="ADMIN",
                activity_type="client_created",
                summary=f"Client created: {client_name}",
                user_id=session["user_id"],
                related_table="clients",
                related_id=client_id,
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(url_for("clients"))

    return render_template("client_new.html", error=error)

#####################################################################
# SHIFT MANAGEMENT
#####################################################################

def auto_sign_on_user(user_id):
    shift_type = get_current_shift_type()
    shift_date = datetime.now().strftime("%Y-%m-%d")
    actual_start_time = datetime.now().strftime("%H:%M")

    conn = get_db()

    shift = conn.execute("""
        SELECT shift_id
        FROM shifts
        WHERE client_id = 1
          AND shift_date = ?
          AND shift_type = ?
          AND status = 'Open'
    """, (shift_date, shift_type)).fetchone()

    if shift is None:
        cur = conn.execute("""
            INSERT INTO shifts
            (client_id, shift_date, shift_type, status)
            VALUES (1, ?, ?, 'Open')
        """, (shift_date, shift_type))

        shift_id = cur.lastrowid
    else:
        shift_id = shift["shift_id"]

    existing = conn.execute("""
        SELECT shift_staff_id, start_checklist_completed
        FROM shift_staff
        WHERE shift_id = ?
          AND user_id = ?
          AND active = 1
    """, (shift_id, user_id)).fetchone()

    if existing is None:
        cur = conn.execute("""
            INSERT INTO shift_staff
            (shift_id, user_id, actual_start_time, active)
            VALUES (?, ?, ?, 1)
        """, (
            shift_id,
            user_id,
            actual_start_time
        ))

        shift_staff_id = cur.lastrowid
        start_checklist_completed = 0

        log_activity(
         conn,
            activity_class="SHIFT",
            activity_type="auto_sign_on",
            summary=f"User automatically signed onto {shift_type} shift",
            user_id=user_id,
            client_id=1,
            shift_id=shift_id,
            related_table="shift_staff",
            related_id=shift_staff_id,
            success=1
        )
    else:
        shift_staff_id = existing["shift_staff_id"]
        start_checklist_completed = existing["start_checklist_completed"]

    conn.commit()
    conn.close()

    return shift_id, start_checklist_completed

@app.route("/shift/sign-on", methods=["GET", "POST"])
def shift_sign_on():
    if "user_id" not in session:
        return redirect(url_for("login"))

    error = None

    if request.method == "POST":
        shift_date = request.form["shift_date"]
        shift_type = request.form["shift_type"]
        actual_start_time = request.form["actual_start_time"]

        if not shift_date or not shift_type or not actual_start_time:
            error = "Shift date, shift type, and actual start time are required."
        else:
            conn = get_db()

            # Find an open shift for this date/type/client
            shift = conn.execute("""
                SELECT shift_id
                FROM shifts
                WHERE client_id = 1
                  AND shift_date = ?
                  AND shift_type = ?
                  AND status = 'Open'
            """, (shift_date, shift_type)).fetchone()

            # If no open shift exists, create one
            if shift is None:
                cur = conn.execute("""
                    INSERT INTO shifts
                    (client_id, shift_date, shift_type, status)
                    VALUES (1, ?, ?, 'Open')
                """, (shift_date, shift_type))

                shift_id = cur.lastrowid
            else:
                shift_id = shift["shift_id"]

            # Check if this user is already signed on to this shift
            existing = conn.execute("""
                SELECT shift_staff_id
                FROM shift_staff
                WHERE shift_id = ?
                  AND user_id = ?
                  AND active = 1
            """, (shift_id, session["user_id"])).fetchone()

            if existing:
                conn.close()
                return redirect(url_for("shift_dashboard", shift_id=shift_id))

            # Sign user onto the shift
            conn.execute("""
                INSERT INTO shift_staff
                (shift_id, user_id, actual_start_time, active)
                VALUES (?, ?, ?, 1)
            """, (
                shift_id,
                session["user_id"],
                actual_start_time
            ))

            conn.commit()
            conn.close()

            return redirect(url_for("shift_dashboard", shift_id=shift_id))

    return render_template("shift_sign_on.html", error=error)

@app.route("/shift/<int:shift_id>")
def shift_dashboard(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    shift = conn.execute("""
        SELECT *
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()

    if shift is None:
      conn.close()
      return "Shift not found", 404

    staff = conn.execute("""
        SELECT ss.*, u.full_name, u.role
        FROM shift_staff ss
        JOIN users u ON ss.user_id = u.user_id
        WHERE ss.shift_id = ?
            AND ss.active = 1
            AND u.role = 'Support Worker'
        ORDER BY ss.sign_on_at
    """, (shift_id,)).fetchall()

    notes = conn.execute("""
        SELECT sn.*, u.full_name
        FROM shift_notes sn
        JOIN users u ON sn.user_id = u.user_id
        WHERE sn.shift_date = ?
        AND sn.shift_type = ?
        AND sn.client_id = ?
        ORDER BY sn.created_at DESC
    """, (
        shift["shift_date"],
        shift["shift_type"],
        shift["client_id"]
    )).fetchall()
    
    care_tasks = conn.execute("""
        SELECT *
        FROM care_tasks
        WHERE active = 1
        AND occurs LIKE ?
        ORDER BY task_name
    """, (f"%{shift['shift_type']}%",)).fetchall()

    care_task_entries = conn.execute("""
        SELECT
         cte.*,
         u.full_name
        FROM shift_care_task_entries cte
        JOIN users u
            ON cte.completed_by_user_id = u.user_id
        WHERE cte.shift_id = ?
    """, (shift_id,)).fetchall()

    care_task_lookup = {}

    for entry in care_task_entries:
        care_task_lookup[entry["care_task_id"]] = entry

    total_care_tasks = len(care_tasks)
    completed_care_tasks = len(care_task_entries)
    remaining_care_tasks = total_care_tasks - completed_care_tasks

    housekeeping_tasks = conn.execute("""
        SELECT *
        FROM housekeeping_tasks
        WHERE active = 1
        AND occurs LIKE ?
        ORDER BY task_name
    """, (f"%{shift['shift_type']}%",)).fetchall()

    housekeeping_task_entries = conn.execute("""
        SELECT
            hte.*,
            u.full_name
        FROM shift_housekeeping_task_entries hte

        JOIN users u
            ON hte.completed_by_user_id = u.user_id

        WHERE hte.shift_id = ?
    """, (shift_id,)).fetchall()

    housekeeping_task_lookup = {}

    for entry in housekeeping_task_entries:
        housekeeping_task_lookup[
            entry["housekeeping_task_id"]
        ] = entry

    total_housekeeping_tasks = len(housekeeping_tasks)
    completed_housekeeping_tasks = len(
        housekeeping_task_entries
    )

    remaining_housekeeping_tasks = (
        total_housekeeping_tasks
        - completed_housekeeping_tasks
    )

    conn.close()

    return render_template(
        "shift_dashboard.html",
        shift=shift,
        staff=staff,
        notes=notes,

        care_tasks=care_tasks,
        care_task_entries=care_task_entries,
        care_task_lookup=care_task_lookup,
        total_care_tasks=total_care_tasks,
        completed_care_tasks=completed_care_tasks,
        remaining_care_tasks=remaining_care_tasks,

        housekeeping_tasks=housekeeping_tasks,
        housekeeping_task_entries=housekeeping_task_entries,
        housekeeping_task_lookup=housekeeping_task_lookup,
        total_housekeeping_tasks=total_housekeeping_tasks,
        completed_housekeeping_tasks=completed_housekeeping_tasks,
        remaining_housekeeping_tasks=remaining_housekeeping_tasks
    )
    

# ============================================================
# BM AND URINATION TRACKER
# ============================================================

@app.route(
    "/shift/<int:shift_id>/toileting-event/new",
    methods=["GET", "POST"]
)
def toileting_event_new(shift_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    shift = conn.execute("""
    SELECT
        shifts.*,
        clients.client_name
    FROM shifts
    JOIN clients
        ON shifts.client_id = clients.client_id
    WHERE shifts.shift_id = ?
    """, (shift_id,)).fetchone()

    if shift is None:
        conn.close()
        return "Shift not found", 404

    if request.method == "POST":
        event_type = request.form.get("event_type", "").strip()
        event_datetime = request.form.get(
            "event_datetime",
            ""
        ).strip()
        location = request.form.get("location", "").strip()

        bm_size = request.form.get("bm_size", "").strip()
        bm_consistency = request.form.get(
            "bm_consistency",
            ""
        ).strip()
        bm_unusual = request.form.get("bm_unusual", "").strip()
        bm_unusual_details = request.form.get(
            "bm_unusual_details",
            ""
        ).strip()

        urine_volume = request.form.get("urine_volume", "").strip()
        urine_unusual = request.form.get(
            "urine_unusual",
            ""
        ).strip()
        urine_unusual_details = request.form.get(
            "urine_unusual_details",
            ""
        ).strip()

        behaviour_before = request.form.get(
            "behaviour_before",
            ""
        ).strip()
        behaviour_during = request.form.get(
            "behaviour_during",
            ""
        ).strip()
        behaviour_after = request.form.get(
            "behaviour_after",
            ""
        ).strip()
        behaviour_comments = request.form.get(
            "behaviour_comments",
            ""
        ).strip()
        general_comments = request.form.get(
            "general_comments",
            ""
        ).strip()

        error = None

        valid_event_types = [
            "BM",
            "Urination",
            "Both"
        ]

        valid_locations = [
            "Bathroom",
            "Bedroom",
            "Living Room",
            "Kitchen",
            "Community",
            "Vehicle",
            "Other"
        ]

        valid_bm_sizes = [
            "",
            "Small",
            "Medium",
            "Large"
        ]

        valid_bm_consistencies = [
            "",
            "Hard",
            "Firm",
            "Soft",
            "Loose",
            "Watery"
        ]

        valid_unusual_values = [
            "",
            "No",
            "Yes"
        ]

        valid_urine_volumes = [
            "",
            "Small",
            "Medium",
            "Large"
        ]

        event_datetime_is_valid = False

        if event_datetime:
            try:
                parsed_event_datetime = datetime.strptime(
                    event_datetime,
                    "%Y-%m-%dT%H:%M"
                )

                event_datetime_is_valid = (
                    parsed_event_datetime.strftime(
                        "%Y-%m-%dT%H:%M"
                    ) == event_datetime
                )
            except ValueError:
                event_datetime_is_valid = False

        if event_type not in valid_event_types:
            error = "Please select a valid event type."

        elif not event_datetime:
            error = "Event date and time is required."

        elif not event_datetime_is_valid:
            error = "Please enter a valid event date and time."

        elif not location:
            error = "Location is required."

        elif location not in valid_locations:
            error = "Please select a valid location."

        elif (
            event_type in ["BM", "Both"]
            and bm_size not in valid_bm_sizes
        ):
            error = "Please select a valid BM size."

        elif (
            event_type in ["BM", "Both"]
            and bm_consistency not in valid_bm_consistencies
        ):
            error = "Please select a valid BM consistency."

        elif (
            event_type in ["BM", "Both"]
            and bm_unusual not in valid_unusual_values
        ):
            error = "Please select a valid BM observation option."

        elif (
            event_type in ["BM", "Both"]
            and bm_unusual == "Yes"
            and not bm_unusual_details
        ):
            error = (
                "Additional BM observations are required when "
                "Anything Unusual is Yes."
            )

        elif (
            event_type in ["Urination", "Both"]
            and urine_volume not in valid_urine_volumes
        ):
            error = "Please select a valid urine volume."

        elif (
            event_type in ["Urination", "Both"]
            and urine_unusual not in valid_unusual_values
        ):
            error = (
                "Please select a valid urination observation option."
            )

        elif (
            event_type in ["Urination", "Both"]
            and urine_unusual == "Yes"
            and not urine_unusual_details
        ):
            error = (
                "Additional urination observations are required when "
                "Anything Unusual is Yes."
            )

        if error:
            conn.close()

            return render_template(
                "toileting_event_new.html",
                shift=shift,
                error=error,
                event_type=event_type,
                event_datetime=event_datetime,
                location=location,
                bm_size=bm_size,
                bm_consistency=bm_consistency,
                bm_unusual=bm_unusual,
                bm_unusual_details=bm_unusual_details,
                urine_volume=urine_volume,
                urine_unusual=urine_unusual,
                urine_unusual_details=urine_unusual_details,
                behaviour_before=behaviour_before,
                behaviour_during=behaviour_during,
                behaviour_after=behaviour_after,
                behaviour_comments=behaviour_comments,
                general_comments=general_comments
            )

        if event_type == "Urination":
            bm_size = None
            bm_consistency = None
            bm_unusual = None
            bm_unusual_details = None

        elif event_type == "BM":
            urine_volume = None
            urine_unusual = None
            urine_unusual_details = None

        if bm_unusual != "Yes":
            bm_unusual_details = None

        if urine_unusual != "Yes":
            urine_unusual_details = None

        cur = conn.execute("""
            INSERT INTO toileting_events
            (
                shift_id,
                client_id,
                recorded_by_user_id,
                event_type,
                event_datetime,
                location,
                bm_size,
                bm_consistency,
                bm_unusual_details,
                urine_volume,
                urine_unusual_details,
                behaviour_before,
                behaviour_during,
                behaviour_after,
                behaviour_comments,
                general_comments
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            shift_id,
            shift["client_id"],
            session["user_id"],
            event_type,
            event_datetime,
            location,
            bm_size,
            bm_consistency,
            bm_unusual_details,
            urine_volume,
            urine_unusual_details,
            behaviour_before,
            behaviour_during,
            behaviour_after,
            behaviour_comments,
            general_comments
        ))

        toileting_event_id = cur.lastrowid

        log_activity(
            conn,
            activity_class="TOILETING",
            activity_type="toileting_event_created",
            summary=f"Toileting event recorded: {event_type}",
            user_id=session["user_id"],
            client_id=shift["client_id"],
            shift_id=shift_id,
            related_table="toileting_events",
            related_id=toileting_event_id,
            details=general_comments,
            success=1
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "shift_dashboard",
                shift_id=shift_id
            )
        )

    conn.close()

    return render_template(
        "toileting_event_new.html",
        shift=shift
    )

@app.route("/shift-staff/<int:shift_staff_id>/manager-sign-off", methods=["GET", "POST"])
def manager_sign_off(shift_staff_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()

    staff_shift = conn.execute("""
        SELECT
            ss.*,
            u.full_name,
            s.shift_date,
            s.shift_type,
            s.client_id
        FROM shift_staff ss

        JOIN users u
            ON ss.user_id = u.user_id

        JOIN shifts s
            ON ss.shift_id = s.shift_id

        WHERE ss.shift_staff_id = ?
    """, (shift_staff_id,)).fetchone()

    if staff_shift is None:
        conn.close()
        return "Shift staff record not found", 404

    if request.method == "POST":
        reason = request.form["reason"].strip()

        if not reason:
            conn.close()
            return "Reason is required", 400

        conn.execute("""
            UPDATE shift_staff
            SET active = 0,
                end_checklist_completed = 0
            WHERE shift_staff_id = ?
        """, (shift_staff_id,))

        log_activity(
            conn,
            activity_class="SHIFT",
            activity_type="manager_signed_staff_off",
            summary=f"Manager manually signed off {staff_shift['full_name']}",
            user_id=session["user_id"],
            client_id=staff_shift["client_id"],
            shift_id=staff_shift["shift_id"],
            related_table="shift_staff",
            related_id=shift_staff_id,
            details=reason,
            success=1
        )

        conn.commit()
        conn.close()

        return redirect(url_for("dashboard"))

    conn.close()

    return render_template(
        "manager_sign_off.html",
        staff_shift=staff_shift
    )

#####################################################################
# SHIFT CHECKLISTS
#####################################################################

@app.route("/shift/<int:shift_id>/start-checklist", methods=["GET", "POST"])
def start_checklist(shift_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    shift = conn.execute("""
        SELECT *
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()

    if shift is None:
        conn.close()
        return "Shift not found", 404

    if request.method == "POST":

        save_shift_task_entries(
            conn,
            shift_id,
            "BEGIN_SHIFT",
            session["user_id"],
            request.form
        )

        conn.execute("""
            UPDATE shift_staff
            SET start_checklist_completed = 1
            WHERE shift_id = ?
              AND user_id = ?
              AND active = 1
        """, (shift_id, session["user_id"]))

        shift_staff = conn.execute("""
            SELECT shift_staff_id
            FROM shift_staff
            WHERE shift_id = ?
            AND user_id = ?
            AND active = 1
        """, (
            shift_id,
            session["user_id"]
        )).fetchone()

        log_activity(
            conn,
            activity_class="SHIFT",
            activity_type="start_shift_completed",
            summary="Beginning of Shift completed",
            user_id=session["user_id"],
            client_id=shift["client_id"],
            shift_id=shift_id,
            related_table="shift_staff",
            related_id=shift_staff["shift_staff_id"],
            success=1
        )

        conn.commit()
        conn.close()

        return redirect(url_for("shift_dashboard", shift_id=shift_id))

    shift_tasks = conn.execute("""
       SELECT *
      FROM shift_tasks
      WHERE task_stage = 'BEGIN_SHIFT'
         AND active = 1
        ORDER BY task_name
    """).fetchall()

    conn.close()

    return render_template(
        "start_checklist.html",
        shift=shift,
        shift_tasks=shift_tasks
    )

@app.route("/shift/<int:shift_id>/end-shift", methods=["GET", "POST"])
def end_shift(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    shift = conn.execute("""
        SELECT *
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()

    if shift is None:
        conn.close()
        return "Shift not found", 404

    if request.method == "POST":

        save_shift_task_entries(
            conn,
            shift_id,
            "END_SHIFT",
            session["user_id"],
            request.form
        )
        
        conn.execute("""
            UPDATE shift_staff
            SET end_checklist_completed = 1,
                active = 0
            WHERE shift_id = ?
              AND user_id = ?
              AND active = 1
        """, (shift_id, session["user_id"]))

        shift_staff = conn.execute("""
                SELECT shift_staff_id
                FROM shift_staff
                WHERE shift_id = ?
                AND user_id = ?
            """, (
                shift_id,
                session["user_id"]
            )).fetchone()


        log_activity(
            conn,
            activity_class="SHIFT",
            activity_type="end_shift_completed",
            summary="End of Shift completed",
            user_id=session["user_id"],
            client_id=shift["client_id"],
            shift_id=shift_id,
            related_table="shift_staff",
            related_id=shift_staff["shift_staff_id"],
            success=1
        )

        conn.commit()
        conn.close()

        session.clear()
        return redirect(url_for("login"))

    shift_tasks = conn.execute("""
        SELECT *
     FROM shift_tasks
      WHERE task_stage = 'END_SHIFT'
         AND active = 1
     ORDER BY task_name
    """).fetchall()

    conn.close()

    return render_template(
        "end_shift.html",
        shift=shift,
        shift_tasks=shift_tasks
    )

def save_shift_task_entries(conn, shift_id, task_stage, user_id, form):
    shift_tasks = conn.execute("""
        SELECT *
        FROM shift_tasks
        WHERE task_stage = ?
          AND active = 1
        ORDER BY task_name
    """, (task_stage,)).fetchall()

    for task in shift_tasks:
        input_value = None

        if task["requires_input"]:
            input_value = form.get(
                f"shift_task_input_{task['shift_task_id']}",
                ""
            ).strip()

        cur = conn.execute("""
            INSERT INTO shift_task_entries
            (
                shift_id,
                shift_task_id,
                task_stage,
                completed_by_user_id,
                input_value
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            shift_id,
            task["shift_task_id"],
            task_stage,
            user_id,
            input_value
        ))

        entry_id = cur.lastrowid

        log_activity(
            conn,
            activity_class="SHIFT_TASK",
            activity_type="shift_task_completed",
            summary=f"Shift task completed: {task['task_name']}",
            user_id=user_id,
            shift_id=shift_id,
            related_table="shift_task_entries",
            related_id=entry_id,
            details=input_value,
            success=1
        )

@app.route("/shift-tasks")
def shift_tasks():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    query = """
        SELECT *
        FROM shift_tasks
    """

    parameters = ()

    if active_filter is not None:
        query += """
            WHERE active = ?
        """
        parameters = (active_filter,)

    query += """
        ORDER BY task_stage, task_name, shift_task_id
    """

    tasks = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "shift_tasks.html",
        tasks=tasks,
        status_filter=status_filter
    )

@app.route("/shift-task/new", methods=["GET", "POST"])
def shift_task_new():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    error = None

    if request.method == "POST":
        task_name = request.form["task_name"].strip()
        instructions = request.form.get("instructions", "").strip()
        task_stage = request.form["task_stage"]
        required = 1 if "required" in request.form else 0
        active = 1 if "active" in request.form else 0
        requires_input = 1 if "requires_input" in request.form else 0
        input_label = request.form.get("input_label", "").strip()
        input_type = request.form.get("input_type", "text")

        if not task_name:
            error = "Task name is required."
        else:
            conn = get_db()

            cur = conn.execute("""
                INSERT INTO shift_tasks
                (task_name, instructions, task_stage, required, active, requires_input, input_label, input_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_name,
                instructions,
                task_stage,
                required,
                active,
                requires_input,
                input_label if input_label else None,
                input_type
            ))

            shift_task_id = cur.lastrowid

            log_activity(
                conn,
                activity_class="ADMIN",
                activity_type="shift_task_created",
                summary=f"Shift task created: {task_name}",
                user_id=session["user_id"],
                related_table="shift_tasks",
                related_id=shift_task_id,
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(url_for("shift_tasks"))

    return render_template("shift_task_new.html", error=error)

@app.route("/shift-task/deactivate/<int:shift_task_id>")
def shift_task_deactivate(shift_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT *
        FROM shift_tasks
        WHERE shift_task_id = ?
    """, (shift_task_id,)).fetchone()

    if task is None:
        conn.close()
        return "Shift task not found", 404

    conn.execute("""
        UPDATE shift_tasks
        SET active = 0
        WHERE shift_task_id = ?
    """, (shift_task_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="shift_task_deactivated",
        summary=f"Shift task deactivated: {task['task_name']}",
        user_id=session["user_id"],
        related_table="shift_tasks",
        related_id=shift_task_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(url_for("shift_tasks", status=status_filter))

@app.route("/shift-task/reactivate/<int:shift_task_id>")
def shift_task_reactivate(shift_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT *
        FROM shift_tasks
        WHERE shift_task_id = ?
    """, (shift_task_id,)).fetchone()

    if task is None:
        conn.close()
        return "Shift task not found", 404

    conn.execute("""
        UPDATE shift_tasks
        SET active = 1
        WHERE shift_task_id = ?
    """, (shift_task_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="shift_task_reactivated",
        summary=f"Shift task reactivated: {task['task_name']}",
        user_id=session["user_id"],
        related_table="shift_tasks",
        related_id=shift_task_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(url_for("shift_tasks", status=status_filter))

@app.route("/shift-task/edit/<int:shift_task_id>", methods=["GET", "POST"])
def shift_task_edit(shift_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT *
        FROM shift_tasks
        WHERE shift_task_id = ?
    """, (shift_task_id,)).fetchone()

    if task is None:
        conn.close()
        return "Shift task not found", 404

    error = None

    if request.method == "POST":

        task_name = request.form["task_name"].strip()
        instructions = request.form.get("instructions", "").strip()
        task_stage = request.form["task_stage"]
        required = 1 if "required" in request.form else 0
        active = 1 if "active" in request.form else 0
        requires_input = 1 if "requires_input" in request.form else 0
        input_label = request.form.get("input_label", "").strip()
        input_type = request.form.get("input_type", "text")

        if not task_name:
            error = "Task name is required."
        else:

            conn.execute("""
                UPDATE shift_tasks
                SET task_name = ?,
                    instructions = ?,
                    task_stage = ?,
                    required = ?,
                    active = ?,
                    requires_input = ?,
                    input_label = ?,
                    input_type = ?
                WHERE shift_task_id = ?
            """, (
                task_name,
                instructions,
                task_stage,
                required,
                active,
                requires_input,
                input_label if input_label else None,
                input_type,
                shift_task_id
            ))

            log_activity(
                conn,
                activity_class="ADMIN",
                activity_type="shift_task_updated",
                summary=f"Shift task updated: {task_name}",
                user_id=session["user_id"],
                related_table="shift_tasks",
                related_id=shift_task_id,
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(url_for("shift_tasks", status=status_filter))

    conn.close()

    return render_template(
        "shift_task_edit.html",
        task=task,
        error=error,
        status_filter=status_filter
    )

#####################################################################
# SHIFT NOTES
#####################################################################

@app.route("/shift-note", methods=["GET", "POST"])
def shift_note():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        shift_date = request.form["shift_date"]
        shift_type = request.form["shift_type"]
        note_text = request.form["note_text"]
        follow_up_required = 1 if "follow_up_required" in request.form else 0

        conn = get_db()
        conn.execute("""
            INSERT INTO shift_notes
            (client_id, user_id, shift_date, shift_type, note_text, follow_up_required)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            1,
            session["user_id"],
            shift_date,
            shift_type,
            note_text,
            follow_up_required
        ))
        conn.commit()
        conn.close()

        return redirect(url_for("shift_notes"))

    return render_template("shift_note.html")


@app.route("/shift-notes")
def shift_notes():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    notes = conn.execute("""
        SELECT
            sn.*,
            u.full_name,
            c.client_name
        FROM shift_notes sn

        JOIN users u
            ON sn.user_id = u.user_id

        JOIN clients c
            ON sn.client_id = c.client_id

        ORDER BY sn.created_at DESC
    """).fetchall()

    reviews = conn.execute("""
        SELECT
            ack.source_id AS note_id,
            ack.acknowledged_at,
            u.full_name AS reviewed_by
        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table = 'shift_notes'
          AND ack.active = 1

        ORDER BY ack.acknowledged_at
    """).fetchall()

    current_user_reviews = conn.execute("""
        SELECT source_id AS note_id
        FROM acknowledgements
        WHERE source_table = 'shift_notes'
          AND user_id = ?
          AND active = 1
    """, (session["user_id"],)).fetchall()

    conn.close()

    reviews_by_note = {}

    for review in reviews:
        note_id = review["note_id"]

        if note_id not in reviews_by_note:
            reviews_by_note[note_id] = []

        reviews_by_note[note_id].append(review)

    reviewed_by_current_user = set()

    for review in current_user_reviews:
        reviewed_by_current_user.add(review["note_id"])

    return render_template(
        "shift_notes.html",
        notes=notes,
        reviews_by_note=reviews_by_note,
        reviewed_by_current_user=reviewed_by_current_user
    )

@app.route("/manager-review/shift-notes/<int:note_id>")
def shift_note_review_detail(note_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT
            sn.note_id,
            sn.shift_date,
            sn.shift_type,
            sn.client_id,
            c.client_name,
            sn.user_id,
            u.full_name AS staff_member,
            sn.follow_up_required,
            sn.created_at,
            sn.note_text

        FROM shift_notes sn

        JOIN clients c
            ON sn.client_id = c.client_id

        JOIN users u
            ON sn.user_id = u.user_id

        WHERE sn.note_id = ?
    """, (note_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Shift note not found", 404

    reviews = conn.execute("""
        SELECT
            ack.acknowledgement_id,
            ack.acknowledged_at,
            ack.acknowledgement_type,
            u.full_name AS reviewed_by

        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table = 'shift_notes'
          AND ack.source_id = ?
          AND ack.active = 1

        ORDER BY
            ack.acknowledged_at ASC,
            ack.acknowledgement_id ASC
    """, (note_id,)).fetchall()

    current_user_review = conn.execute("""
        SELECT acknowledgement_id
        FROM acknowledgements

        WHERE source_table = 'shift_notes'
          AND source_id = ?
          AND user_id = ?
          AND active = 1
    """, (
        note_id,
        session["user_id"]
    )).fetchone()

    management_notes = get_management_notes(
        conn,
        source_table="shift_notes",
        source_id=note_id
    )

    linked_actions = conn.execute("""
        SELECT
            ai.action_id,
            ai.title,
            ai.status,
            ai.priority,
            ai.created_at,

            assigned_to.full_name AS assigned_to

        FROM action_items ai

        LEFT JOIN users assigned_to
            ON ai.assigned_to_user_id =
               assigned_to.user_id

        WHERE ai.source_table = 'shift_notes'
          AND ai.source_id = ?

        ORDER BY ai.created_at DESC
    """, (note_id,)).fetchall()

    conn.close()

    return render_template(
        "shift_note_review_detail.html",
        entry=entry,
        reviews=reviews,
        current_user_reviewed=(
            current_user_review is not None
        ),
        management_notes=management_notes,
        linked_actions=linked_actions
    )

@app.route(
    "/manager-review/shift-notes/<int:note_id>/management-note",
    methods=["POST"]
)
def add_shift_note_management_note(note_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT note_id
        FROM shift_notes
        WHERE note_id = ?
    """, (note_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Shift note not found", 404

    note_text = request.form.get(
        "note_text",
        ""
    ).strip()

    if not note_text:
        conn.close()

        return redirect(
            url_for(
                "shift_note_review_detail",
                note_id=note_id,
                note_error="Management note text is required."
            )
        )

    add_management_note(
        conn,
        source_table="shift_notes",
        source_id=note_id,
        note_text=note_text,
        created_by_user_id=session["user_id"],
        visibility="management_only",
        shift_id=None
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "shift_note_review_detail",
            note_id=note_id
        )
    )

@app.route(
    "/manager-review/shift-notes/<int:note_id>/action/new",
    methods=["GET", "POST"]
)
def shift_note_action_new(note_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT
            sn.note_id,
            sn.shift_date,
            sn.shift_type,
            sn.client_id,
            c.client_name,
            u.full_name AS staff_member,
            sn.note_text,
            sn.follow_up_required

        FROM shift_notes sn

        JOIN clients c
            ON sn.client_id = c.client_id

        JOIN users u
            ON sn.user_id = u.user_id

        WHERE sn.note_id = ?
    """, (note_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Shift note not found", 404

    active_users = conn.execute("""
        SELECT
            user_id,
            full_name,
            role
        FROM users
        WHERE active = 1
        ORDER BY full_name
    """).fetchall()

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        description = request.form.get(
            "description",
            ""
        ).strip()

        priority = request.form.get(
            "priority",
            "Medium"
        ).strip()

        assigned_to_user_id = request.form.get(
            "assigned_to_user_id",
            ""
        ).strip()

        due_date = None
        error = None

        if not title:
            error = "Action title is required."

        elif priority not in [
            "High",
            "Medium",
            "Low"
        ]:
            error = "Invalid priority."

        if assigned_to_user_id:

            try:
                assigned_to_user_id = int(
                    assigned_to_user_id
                )
            except ValueError:
                error = "Invalid assigned user."

            if (
                isinstance(assigned_to_user_id, int)
                and assigned_to_user_id not in {
                    user["user_id"] for user in active_users
                }
            ):
                error = "Invalid assigned user."

        else:
            assigned_to_user_id = None

        if error:
            conn.close()

            return render_template(
                "shift_note_action_new.html",
                entry=entry,
                active_users=active_users,
                error=error,
                title=title,
                description=description,
                priority=priority,
                assigned_to_user_id=assigned_to_user_id
            )

        action_id = create_action(
            conn,
            title=title,
            description=description or None,
            source_table="shift_notes",
            source_id=note_id,
            shift_id=None,
            created_by_user_id=session["user_id"],
            assigned_to_user_id=assigned_to_user_id,
            priority=priority,
            due_date=due_date
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "action_detail",
                action_id=action_id
            )
        )

    default_description = (
        f"Shift note\n"
        f"Date: {entry['shift_date']}\n"
        f"Shift: {entry['shift_type']}\n"
        f"Client: {entry['client_name']}\n"
        f"Staff member: {entry['staff_member']}\n"
        f"Follow-up required: "
        f"{'Yes' if entry['follow_up_required'] else 'No'}\n"
        f"Note: {entry['note_text']}"
    )

    conn.close()

    return render_template(
        "shift_note_action_new.html",
        entry=entry,
        active_users=active_users,
        error=None,
        title="Shift Note Follow-up",
        description=default_description,
        priority="Medium",
        assigned_to_user_id=None
    )

@app.route(
    "/shift-note/<int:note_id>/acknowledge",
    methods=["POST"]
)
def acknowledge_shift_note(note_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()

    note = conn.execute("""
        SELECT *
        FROM shift_notes
        WHERE note_id = ?
    """, (note_id,)).fetchone()

    if note is None:
        conn.close()
        return "Shift note not found", 404

    create_acknowledgement(
        conn,
        source_table="shift_notes",
        source_id=note_id,
        user_id=session["user_id"],
        acknowledgement_type="Review"
    )

    conn.commit()
    conn.close()

    return_to = request.form.get("return_to", "")

    if return_to == "detail":
        return redirect(
            url_for(
                "shift_note_review_detail",
                note_id=note_id
            )
        )

    return redirect(
        url_for("shift_notes")
        + f"#shift-note-{note_id}"
    )

@app.route("/shift/<int:shift_id>/note", methods=["GET", "POST"])
def shift_add_note(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    shift = conn.execute("""
        SELECT *
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()

    if shift is None:
        conn.close()
        return "Shift not found", 404

    if request.method == "POST":
        note_text = request.form["note_text"]
        follow_up_required = 1 if "follow_up_required" in request.form else 0

        cur = conn.execute("""
            INSERT INTO shift_notes
            (
                client_id,
                user_id,
                shift_date,
                shift_type,
                note_text,
                follow_up_required
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            shift["client_id"],
            session["user_id"],
            shift["shift_date"],
            shift["shift_type"],
            note_text,
            follow_up_required
        ))

        shift_note_id = cur.lastrowid

        log_activity(
            conn,
            activity_class="NOTE",
            activity_type="shift_note_created",
            summary="Shift note added",
            user_id=session["user_id"],
            client_id=shift["client_id"],
            shift_id=shift_id,
            related_table="shift_notes",
            related_id=shift_note_id,
            details=note_text,
            success=1
        )

        if follow_up_required:
            create_action(
                conn,
                title="Shift Note Follow-up",
                description=note_text,
                source_table="shift_notes",
                source_id=shift_note_id,
                shift_id=shift_id,
                created_by_user_id=session["user_id"],
                priority="Medium"
            )

        conn.commit()
        conn.close()

        return redirect(url_for("shift_dashboard", shift_id=shift_id))

    conn.close()

    return render_template(
        "shift_add_note.html",
        shift=shift
    )
    
#####################################################################
# INCIDENT REPORTS
#####################################################################

@app.route("/incident/new", methods=["GET", "POST"])
def incident_new():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        incident_date = request.form["incident_date"]
        incident_time = request.form["incident_time"]
        location = request.form["location"]
        incident_type = request.form["incident_type"]
        description = request.form["description"]
        actions_taken = request.form.get("actions_taken", "")
        witnesses = request.form.get("witnesses", "")
        injury_details = request.form.get("injury_details", "")

        injuries = 1 if "injuries" in request.form else 0
        police_notified = 1 if "police_notified" in request.form else 0
        medical_treatment = 1 if "medical_treatment" in request.form else 0
        follow_up_required = 1 if "follow_up_required" in request.form else 0

        conn = get_db()

        active_shift = conn.execute("""
            SELECT shift_id
            FROM shift_staff
            WHERE user_id = ?
                AND active = 1
        """, (session["user_id"],)).fetchone()

        shift_id = active_shift["shift_id"] if active_shift else None


        cur = conn.execute("""
            INSERT INTO incident_reports
            (
                client_id,
                reported_by_user_id,
                incident_date,
                incident_time,
                location,
                incident_type,
                description,
                actions_taken,
                follow_up_required,
                witnesses,
                injuries,
                injury_details,
                police_notified,
                medical_treatment
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            1,
            session["user_id"],
            incident_date,
            incident_time,
            location,
            incident_type,
            description,
            actions_taken,
            follow_up_required,
            witnesses,
            injuries,
            injury_details,
            police_notified,
            medical_treatment
        ))

        incident_id = cur.lastrowid

        log_activity(
            conn,
            activity_class="INCIDENT",
            activity_type="incident_created",
            summary=f"Incident created: {incident_type}",
            user_id=session["user_id"],
            client_id=1,
            shift_id=shift_id,
            related_table="incident_reports",
            related_id=incident_id,
            details=description,
            success=1
        )

        conn.commit()
        conn.close()

        return redirect(url_for("incident_list"))

    return render_template("incident_new.html")

@app.route("/incidents")
def incident_list():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    incidents = conn.execute("""
        SELECT
            ir.*,
            u.full_name,
            c.client_name
        FROM incident_reports ir

        JOIN users u
            ON ir.reported_by_user_id = u.user_id

        JOIN clients c
            ON ir.client_id = c.client_id

        ORDER BY ir.created_at DESC
    """).fetchall()

    reviews = conn.execute("""
        SELECT
            ack.source_id AS incident_id,
            ack.acknowledged_at,
            u.full_name AS reviewed_by
        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table = 'incident_reports'
          AND ack.active = 1

        ORDER BY ack.acknowledged_at
    """).fetchall()

    current_user_reviews = conn.execute("""
        SELECT source_id AS incident_id
        FROM acknowledgements
        WHERE source_table = 'incident_reports'
          AND user_id = ?
          AND active = 1
    """, (session["user_id"],)).fetchall()

    conn.close()

    reviews_by_incident = {}

    for review in reviews:
        incident_id = review["incident_id"]

        if incident_id not in reviews_by_incident:
            reviews_by_incident[incident_id] = []

        reviews_by_incident[incident_id].append(review)

    reviewed_by_current_user = set()

    for review in current_user_reviews:
        reviewed_by_current_user.add(review["incident_id"])

    return render_template(
        "incident_list.html",
        incidents=incidents,
        reviews_by_incident=reviews_by_incident,
        reviewed_by_current_user=reviewed_by_current_user
    )

@app.route("/incident/<int:incident_id>/review")
def review_incident(incident_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()

    incident = conn.execute("""
        SELECT *
        FROM incident_reports
        WHERE incident_id = ?
    """, (incident_id,)).fetchone()

    if incident is None:
        conn.close()
        return "Incident not found", 404

    create_acknowledgement(
        conn,
        source_table="incident_reports",
        source_id=incident_id,
        user_id=session["user_id"],
        acknowledgement_type="Review"
    )

    conn.commit()
    conn.close()

    return redirect(url_for("incident_list"))

#####################################################################
# MANAGER REVIEW
#####################################################################

@app.route("/manager-review")
def manager_review_hub():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    return render_template(
        "manager_review_hub.html"
    )

#
# Care Review
#

@app.route("/manager-review/care")
def care_review_list():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    care_entries = conn.execute("""
        SELECT
            cte.entry_id,
            cte.shift_id,
            cte.care_task_id,
            cte.outcome,
            cte.comment,
            cte.completed_at,

            ct.task_name,

            completed_by.full_name AS completed_by,

            s.shift_date,
            s.shift_type,

            c.client_name

        FROM shift_care_task_entries cte

        JOIN care_tasks ct
            ON cte.care_task_id = ct.care_task_id

        JOIN users completed_by
            ON cte.completed_by_user_id =
               completed_by.user_id

        JOIN shifts s
            ON cte.shift_id = s.shift_id

        JOIN clients c
            ON s.client_id = c.client_id

        ORDER BY
            s.shift_date DESC,
            cte.completed_at DESC
    """).fetchall()

    reviews = conn.execute("""
        SELECT
            ack.source_id AS entry_id,
            ack.acknowledged_at,
            u.full_name AS reviewed_by

        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table =
              'shift_care_task_entries'
          AND ack.active = 1

        ORDER BY ack.acknowledged_at
    """).fetchall()

    current_user_reviews = conn.execute("""
        SELECT
            source_id AS entry_id

        FROM acknowledgements

        WHERE source_table =
              'shift_care_task_entries'
          AND user_id = ?
          AND active = 1
    """, (
        session["user_id"],
    )).fetchall()

    conn.close()

    reviews_by_entry = {}

    for review in reviews:
        entry_id = review["entry_id"]

        if entry_id not in reviews_by_entry:
            reviews_by_entry[entry_id] = []

        reviews_by_entry[entry_id].append(review)

    reviewed_by_current_user = set()

    for review in current_user_reviews:
        reviewed_by_current_user.add(
            review["entry_id"]
        )

    return render_template(
        "care_review_list.html",
        care_entries=care_entries,
        reviews_by_entry=reviews_by_entry,
        reviewed_by_current_user=reviewed_by_current_user
    )

@app.route("/manager-review/toileting")
def toileting_review_list():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entries = conn.execute("""
        SELECT
            te.toileting_event_id AS entry_id,
            te.shift_id,
            te.event_type,
            te.event_datetime,
            te.location,
            te.bm_size,
            te.bm_consistency,
            te.bm_unusual_details,
            te.urine_volume,
            te.urine_unusual_details,
            te.behaviour_before,
            te.behaviour_during,
            te.behaviour_after,
            te.behaviour_comments,
            te.general_comments,

            recorded_by.full_name AS recorded_by,

            s.shift_date,
            s.shift_type,

            c.client_name

        FROM toileting_events te

        JOIN users recorded_by
            ON te.recorded_by_user_id = recorded_by.user_id

        JOIN shifts s
            ON te.shift_id = s.shift_id

        JOIN clients c
            ON te.client_id = c.client_id

        ORDER BY
            te.event_datetime DESC,
            te.toileting_event_id DESC
    """).fetchall()

    reviews = conn.execute("""
        SELECT
            ack.source_id AS entry_id,
            ack.acknowledged_at,
            u.full_name AS reviewed_by

        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table =
              'toileting_events'
          AND ack.active = 1

        ORDER BY ack.acknowledged_at
    """).fetchall()

    current_user_reviews = conn.execute("""
        SELECT
            source_id AS entry_id

        FROM acknowledgements

        WHERE source_table =
              'toileting_events'
          AND user_id = ?
          AND active = 1
    """, (
        session["user_id"],
    )).fetchall()

    conn.close()

    reviews_by_entry = {}

    for review in reviews:
        entry_id = review["entry_id"]

        if entry_id not in reviews_by_entry:
            reviews_by_entry[entry_id] = []

        reviews_by_entry[entry_id].append(review)

    reviewed_by_current_user = set()

    for review in current_user_reviews:
        reviewed_by_current_user.add(
            review["entry_id"]
        )

    return render_template(
        "toileting_review_list.html",
        entries=entries,
        reviews_by_entry=reviews_by_entry,
        reviewed_by_current_user=reviewed_by_current_user
    )

@app.route("/manager-review/housekeeping")
def housekeeping_review_list():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    housekeeping_entries = conn.execute("""
        SELECT
            hte.entry_id,
            hte.shift_id,
            hte.housekeeping_task_id,
            hte.outcome,
            hte.comment,
            hte.completed_at,

            ht.task_name,

            completed_by.full_name AS completed_by,

            s.shift_date,
            s.shift_type,

            c.client_name

        FROM shift_housekeeping_task_entries hte

        JOIN housekeeping_tasks ht
            ON hte.housekeeping_task_id =
               ht.housekeeping_task_id

        JOIN users completed_by
            ON hte.completed_by_user_id =
               completed_by.user_id

        JOIN shifts s
            ON hte.shift_id = s.shift_id

        JOIN clients c
            ON s.client_id = c.client_id

        ORDER BY
            s.shift_date DESC,
            hte.completed_at DESC
    """).fetchall()

    reviews = conn.execute("""
        SELECT
            ack.source_id AS entry_id,
            ack.acknowledged_at,
            u.full_name AS reviewed_by

        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table =
              'shift_housekeeping_task_entries'
          AND ack.active = 1

        ORDER BY ack.acknowledged_at
    """).fetchall()

    current_user_reviews = conn.execute("""
        SELECT
            source_id AS entry_id

        FROM acknowledgements

        WHERE source_table =
              'shift_housekeeping_task_entries'
          AND user_id = ?
          AND active = 1
    """, (
        session["user_id"],
    )).fetchall()

    conn.close()

    reviews_by_entry = {}

    for review in reviews:
        entry_id = review["entry_id"]

        if entry_id not in reviews_by_entry:
            reviews_by_entry[entry_id] = []

        reviews_by_entry[entry_id].append(review)

    reviewed_by_current_user = set()

    for review in current_user_reviews:
        reviewed_by_current_user.add(
            review["entry_id"]
        )

    return render_template(
        "housekeeping_review_list.html",
        housekeeping_entries=housekeeping_entries,
        reviews_by_entry=reviews_by_entry,
        reviewed_by_current_user=reviewed_by_current_user
    )

@app.route("/manager-review/housekeeping/<int:entry_id>")
def housekeeping_review_detail(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT
            hte.entry_id,
            hte.shift_id,
            hte.housekeeping_task_id,
            hte.outcome,
            hte.comment,
            hte.completed_by_user_id,
            hte.completed_at,

            ht.task_name,
            ht.instructions,
            ht.category_id,

            htc.category_name,

            completed_by.full_name AS completed_by,

            s.shift_date,
            s.shift_type,
            s.client_id,

            c.client_name

        FROM shift_housekeeping_task_entries hte

        JOIN housekeeping_tasks ht
            ON hte.housekeeping_task_id =
               ht.housekeeping_task_id

        LEFT JOIN housekeeping_task_categories htc
            ON ht.category_id = htc.category_id

        JOIN users completed_by
            ON hte.completed_by_user_id =
               completed_by.user_id

        JOIN shifts s
            ON hte.shift_id = s.shift_id

        JOIN clients c
            ON s.client_id = c.client_id

        WHERE hte.entry_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Housekeeping task entry not found", 404

    reviews = conn.execute("""
        SELECT
            ack.acknowledgement_id,
            ack.user_id,
            ack.acknowledged_at,
            ack.acknowledgement_type,

            u.full_name AS reviewed_by

        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table =
              'shift_housekeeping_task_entries'
          AND ack.source_id = ?
          AND ack.active = 1

        ORDER BY
            ack.acknowledged_at ASC,
            ack.acknowledgement_id ASC
    """, (entry_id,)).fetchall()

    current_user_review = conn.execute("""
        SELECT acknowledgement_id
        FROM acknowledgements

        WHERE source_table =
              'shift_housekeeping_task_entries'
          AND source_id = ?
          AND user_id = ?
          AND active = 1
    """, (
        entry_id,
        session["user_id"]
    )).fetchone()

    management_notes = get_management_notes(
        conn,
        source_table="shift_housekeeping_task_entries",
        source_id=entry_id
    )

    linked_actions = conn.execute("""
        SELECT
            ai.action_id,
            ai.title,
            ai.status,
            ai.priority,
            ai.created_at,

            assigned_to.full_name AS assigned_to

        FROM action_items ai

        LEFT JOIN users assigned_to
            ON ai.assigned_to_user_id =
               assigned_to.user_id

        WHERE ai.source_table =
              'shift_housekeeping_task_entries'
          AND ai.source_id = ?

        ORDER BY ai.created_at DESC
    """, (entry_id,)).fetchall()

    shift_staff = conn.execute("""
        SELECT
            u.full_name,
            u.role,
            ss.actual_start_time,
            ss.actual_end_time

        FROM shift_staff ss

        JOIN users u
            ON ss.user_id = u.user_id

        WHERE ss.shift_id = ?

        ORDER BY ss.sign_on_at
    """, (entry["shift_id"],)).fetchall()

    conn.close()

    return render_template(
        "housekeeping_review_detail.html",
        entry=entry,
        reviews=reviews,
        current_user_reviewed=(
            current_user_review is not None
        ),
        management_notes=management_notes,
        linked_actions=linked_actions,
        shift_staff=shift_staff
    )

@app.route(
    "/manager-review/housekeeping/<int:entry_id>/management-note",
    methods=["POST"]
)
def add_housekeeping_management_note(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    note_text = request.form.get(
        "note_text",
        ""
    ).strip()

    if not note_text:
        return redirect(
            url_for(
                "housekeeping_review_detail",
                entry_id=entry_id,
                note_error="Management note text is required."
            )
        )

    conn = get_db()

    entry = conn.execute("""
        SELECT
            entry_id,
            shift_id
        FROM shift_housekeeping_task_entries
        WHERE entry_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Housekeeping task entry not found", 404

    add_management_note(
        conn,
        source_table="shift_housekeeping_task_entries",
        source_id=entry_id,
        note_text=note_text,
        created_by_user_id=session["user_id"],
        visibility="management_only",
        shift_id=entry["shift_id"]
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "housekeeping_review_detail",
            entry_id=entry_id
        )
    )

@app.route(
    "/manager-review/housekeeping/<int:entry_id>/action/new",
    methods=["GET", "POST"]
)
def housekeeping_action_new(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT
            hte.entry_id,
            hte.shift_id,
            hte.outcome,
            hte.comment,

            ht.task_name,

            s.shift_date,
            s.shift_type

        FROM shift_housekeeping_task_entries hte

        JOIN housekeeping_tasks ht
            ON hte.housekeeping_task_id =
               ht.housekeeping_task_id

        JOIN shifts s
            ON hte.shift_id = s.shift_id

        WHERE hte.entry_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Housekeeping task entry not found", 404

    active_users = conn.execute("""
        SELECT
            user_id,
            full_name,
            role
        FROM users
        WHERE active = 1
        ORDER BY full_name
    """).fetchall()

    if request.method == "POST":

        title = request.form.get("title", "").strip()

        description = request.form.get(
            "description",
            ""
        ).strip()

        priority = request.form.get(
            "priority",
            "Medium"
        )

        assigned_to_user_id = request.form.get(
            "assigned_to_user_id",
            ""
        ).strip()

        error = None

        if not title:
            error = "Action title is required."

        if priority not in [
            "High",
            "Medium",
            "Low"
        ]:
            error = "Invalid priority."

        if assigned_to_user_id:
            assigned_to_user_id = int(
                assigned_to_user_id
            )
        else:
            assigned_to_user_id = None

        if error:
            conn.close()

            return render_template(
                "housekeeping_action_new.html",
                entry=entry,
                active_users=active_users,
                error=error,
                title=title,
                description=description,
                priority=priority,
                assigned_to_user_id=assigned_to_user_id
            )

        action_id = create_action(
            conn,
            title=title,
            description=description or None,
            source_table="shift_housekeeping_task_entries",
            source_id=entry_id,
            shift_id=entry["shift_id"],
            created_by_user_id=session["user_id"],
            assigned_to_user_id=assigned_to_user_id,
            priority=priority
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "action_detail",
                action_id=action_id
            )
        )

    default_description = (
        f"Housekeeping task: {entry['task_name']}\n"
        f"Date: {entry['shift_date']}\n"
        f"Shift: {entry['shift_type']}\n"
        f"Outcome: {entry['outcome']}"
    )

    if entry["comment"]:
        default_description += (
            f"\nOperational comment: "
            f"{entry['comment']}"
        )

    conn.close()

    return render_template(
        "housekeeping_action_new.html",
        entry=entry,
        active_users=active_users,
        error=None,
        title=(
            f"Housekeeping Follow-up: "
            f"{entry['task_name']}"
        ),
        description=default_description,
        priority="Medium",
        assigned_to_user_id=None
    )

@app.route(
    "/manager-review/housekeeping/<int:entry_id>/review",
    methods=["POST"]
)
def review_housekeeping_entry(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT entry_id
        FROM shift_housekeeping_task_entries
        WHERE entry_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Housekeeping task entry not found", 404

    create_acknowledgement(
        conn,
        source_table="shift_housekeeping_task_entries",
        source_id=entry_id,
        user_id=session["user_id"],
        acknowledgement_type="Review"
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("housekeeping_review_list")
        + f"#housekeeping-entry-{entry_id}"
    )

@app.route("/manager-review/toileting/<int:entry_id>")
def toileting_review_detail(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT
            te.*,

            recorded_by.full_name AS recorded_by,

            s.shift_date,
            s.shift_type,
            s.client_id,

            c.client_name

        FROM toileting_events te

        JOIN users recorded_by
            ON te.recorded_by_user_id = recorded_by.user_id

        JOIN shifts s
            ON te.shift_id = s.shift_id

        JOIN clients c
            ON te.client_id = c.client_id

        WHERE te.toileting_event_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Toileting event not found", 404

    review_history = conn.execute("""
        SELECT
            ack.acknowledgement_id,
            ack.user_id,
            ack.acknowledged_at,
            ack.acknowledgement_type,

            u.full_name AS reviewed_by

        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table =
              'toileting_events'
          AND ack.source_id = ?
          AND ack.active = 1

        ORDER BY
            ack.acknowledged_at ASC,
            ack.acknowledgement_id ASC
    """, (entry_id,)).fetchall()

    current_user_review = conn.execute("""
        SELECT acknowledgement_id
        FROM acknowledgements

        WHERE source_table =
              'toileting_events'
          AND source_id = ?
          AND user_id = ?
          AND active = 1
    """, (
        entry_id,
        session["user_id"]
    )).fetchone()

    management_notes = get_management_notes(
        conn,
        source_table="toileting_events",
        source_id=entry_id
    )

    linked_actions = conn.execute("""
        SELECT
            ai.action_id,
            ai.title,
            ai.status,
            ai.priority,
            ai.created_at,

            assigned_to.full_name AS assigned_to

        FROM action_items ai

        LEFT JOIN users assigned_to
            ON ai.assigned_to_user_id =
               assigned_to.user_id

        WHERE ai.source_table =
              'toileting_events'
          AND ai.source_id = ?

        ORDER BY ai.created_at DESC
    """, (entry_id,)).fetchall()

    shift_staff = conn.execute("""
        SELECT
            u.full_name,
            u.role,
            ss.actual_start_time,
            ss.actual_end_time

        FROM shift_staff ss

        JOIN users u
            ON ss.user_id = u.user_id

        WHERE ss.shift_id = ?

        ORDER BY ss.sign_on_at
    """, (entry["shift_id"],)).fetchall()

    conn.close()

    current_user_reviewed = (
        current_user_review is not None
    )

    return render_template(
        "toileting_review_detail.html",
        entry=entry,
        review_history=review_history,
        current_user_reviewed=current_user_reviewed,
        management_notes=management_notes,
        linked_actions=linked_actions,
        shift_staff=shift_staff
    )

@app.route(
    "/manager-review/toileting/<int:entry_id>/management-note",
    methods=["POST"]
)
def add_toileting_management_note(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    note_text = request.form.get(
        "note_text",
        ""
    ).strip()

    if not note_text:
        return redirect(
            url_for(
                "toileting_review_detail",
                entry_id=entry_id,
                note_error="Management note text is required."
            )
        )

    conn = get_db()

    entry = conn.execute("""
        SELECT
            toileting_event_id,
            shift_id
        FROM toileting_events
        WHERE toileting_event_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Toileting event not found", 404

    add_management_note(
        conn,
        source_table="toileting_events",
        source_id=entry_id,
        note_text=note_text,
        created_by_user_id=session["user_id"],
        visibility="management_only",
        shift_id=entry["shift_id"]
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "toileting_review_detail",
            entry_id=entry_id
        )
    )

@app.route(
    "/manager-review/toileting/<int:entry_id>/action/new",
    methods=["GET", "POST"]
)
def toileting_action_new(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT
            te.toileting_event_id AS entry_id,
            te.shift_id,
            te.event_type,
            te.event_datetime,
            te.location,
            te.general_comments,

            s.shift_date,
            s.shift_type

        FROM toileting_events te

        JOIN shifts s
            ON te.shift_id = s.shift_id

        WHERE te.toileting_event_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Toileting event not found", 404

    active_users = conn.execute("""
        SELECT
            user_id,
            full_name,
            role
        FROM users
        WHERE active = 1
        ORDER BY full_name
    """).fetchall()

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        description = request.form.get(
            "description",
            ""
        ).strip()

        priority = request.form.get(
            "priority",
            "Medium"
        )

        assigned_to_user_id = request.form.get(
            "assigned_to_user_id",
            ""
        ).strip()

        error = None

        if not title:
            error = "Action title is required."

        if priority not in [
            "High",
            "Medium",
            "Low"
        ]:
            error = "Invalid priority."

        if assigned_to_user_id:
            assigned_to_user_id = int(
                assigned_to_user_id
            )
        else:
            assigned_to_user_id = None

        if error:
            conn.close()

            return render_template(
                "toileting_action_new.html",
                entry=entry,
                active_users=active_users,
                error=error,
                title=title,
                description=description,
                priority=priority,
                assigned_to_user_id=assigned_to_user_id
            )

        action_id = create_action(
            conn,
            title=title,
            description=description or None,
            source_table="toileting_events",
            source_id=entry_id,
            shift_id=entry["shift_id"],
            created_by_user_id=session["user_id"],
            assigned_to_user_id=assigned_to_user_id,
            priority=priority
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "action_detail",
                action_id=action_id
            )
        )

    default_description = (
        f"Toileting event: {entry['event_type']}\n"
        f"Event date and time: {entry['event_datetime']}\n"
        f"Shift: {entry['shift_date']} "
        f"{entry['shift_type']}\n"
        f"Location: {entry['location']}"
    )

    if entry["general_comments"]:
        default_description += (
            f"\nOperational comment: "
            f"{entry['general_comments']}"
        )

    conn.close()

    return render_template(
        "toileting_action_new.html",
        entry=entry,
        active_users=active_users,
        error=None,
        title=f"Toileting Follow-up: {entry['event_type']}",
        description=default_description,
        priority="Medium",
        assigned_to_user_id=None
    )

@app.route("/manager-review/care/<int:entry_id>")
def care_review_detail(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT
            cte.entry_id,
            cte.shift_id,
            cte.care_task_id,
            cte.outcome,
            cte.comment,
            cte.completed_by_user_id,
            cte.completed_at,

            ct.task_name,
            ct.instructions,
            ct.category_id,

            ctc.category_name,

            completed_by.full_name AS completed_by,

            s.shift_date,
            s.shift_type,
            s.client_id,

            c.client_name

        FROM shift_care_task_entries cte

        JOIN care_tasks ct
            ON cte.care_task_id = ct.care_task_id

        LEFT JOIN care_task_categories ctc
            ON ct.category_id = ctc.category_id

        JOIN users completed_by
            ON cte.completed_by_user_id =
               completed_by.user_id

        JOIN shifts s
            ON cte.shift_id = s.shift_id

        JOIN clients c
            ON s.client_id = c.client_id

        WHERE cte.entry_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Care task entry not found", 404

    reviews = conn.execute("""
        SELECT
            ack.acknowledgement_id,
            ack.user_id,
            ack.acknowledged_at,
            ack.acknowledgement_type,

            u.full_name AS reviewed_by

        FROM acknowledgements ack

        JOIN users u
            ON ack.user_id = u.user_id

        WHERE ack.source_table =
              'shift_care_task_entries'
          AND ack.source_id = ?
          AND ack.active = 1

        ORDER BY
            ack.acknowledged_at ASC,
            ack.acknowledgement_id ASC
    """, (entry_id,)).fetchall()

    current_user_review = conn.execute("""
        SELECT acknowledgement_id
        FROM acknowledgements

        WHERE source_table =
              'shift_care_task_entries'
          AND source_id = ?
          AND user_id = ?
          AND active = 1
    """, (
        entry_id,
        session["user_id"]
    )).fetchone()

    management_notes = get_management_notes(
        conn,
        source_table="shift_care_task_entries",
        source_id=entry_id
    )

    linked_actions = conn.execute("""
        SELECT
            ai.action_id,
            ai.title,
            ai.status,
            ai.priority,
            ai.created_at,

            assigned_to.full_name AS assigned_to

        FROM action_items ai

        LEFT JOIN users assigned_to
            ON ai.assigned_to_user_id =
               assigned_to.user_id

        WHERE ai.source_table =
              'shift_care_task_entries'
          AND ai.source_id = ?

        ORDER BY ai.created_at DESC
    """, (entry_id,)).fetchall()

    shift_staff = conn.execute("""
        SELECT
            u.full_name,
            u.role,
            ss.actual_start_time,
            ss.actual_end_time

        FROM shift_staff ss

        JOIN users u
            ON ss.user_id = u.user_id

        WHERE ss.shift_id = ?

        ORDER BY ss.sign_on_at
    """, (entry["shift_id"],)).fetchall()

    conn.close()

    return render_template(
        "care_review_detail.html",
        entry=entry,
        reviews=reviews,
        current_user_reviewed=(
            current_user_review is not None
        ),
        management_notes=management_notes,
        linked_actions=linked_actions,
        shift_staff=shift_staff
    )

@app.route(
    "/manager-review/care/<int:entry_id>/management-note",
    methods=["POST"]
)
def add_care_management_note(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    note_text = request.form.get(
        "note_text",
        ""
    ).strip()

    if not note_text:
        return redirect(
            url_for(
                "care_review_detail",
                entry_id=entry_id,
                note_error="Management note text is required."
            )
        )

    conn = get_db()

    entry = conn.execute("""
        SELECT
            entry_id,
            shift_id
        FROM shift_care_task_entries
        WHERE entry_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Care task entry not found", 404

    add_management_note(
        conn,
        source_table="shift_care_task_entries",
        source_id=entry_id,
        note_text=note_text,
        created_by_user_id=session["user_id"],
        visibility="management_only",
        shift_id=entry["shift_id"]
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "care_review_detail",
            entry_id=entry_id
        )
    )

@app.route(
    "/manager-review/care/<int:entry_id>/action/new",
    methods=["GET", "POST"]
)
def care_action_new(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT
            cte.entry_id,
            cte.shift_id,
            cte.outcome,
            cte.comment,

            ct.task_name,

            s.shift_date,
            s.shift_type

        FROM shift_care_task_entries cte

        JOIN care_tasks ct
            ON cte.care_task_id = ct.care_task_id

        JOIN shifts s
            ON cte.shift_id = s.shift_id

        WHERE cte.entry_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Care task entry not found", 404

    active_users = conn.execute("""
        SELECT
            user_id,
            full_name,
            role
        FROM users
        WHERE active = 1
        ORDER BY full_name
    """).fetchall()

    if request.method == "POST":

        title = request.form.get("title", "").strip()
        description = request.form.get(
            "description",
            ""
        ).strip()

        priority = request.form.get(
            "priority",
            "Medium"
        )

        assigned_to_user_id = request.form.get(
            "assigned_to_user_id",
            ""
        ).strip()

        error = None

        if not title:
            error = "Action title is required."

        if priority not in [
            "High",
            "Medium",
            "Low"
        ]:
            error = "Invalid priority."

        if assigned_to_user_id:
            assigned_to_user_id = int(
                assigned_to_user_id
            )
        else:
            assigned_to_user_id = None

        if error:
            conn.close()

            return render_template(
                "care_action_new.html",
                entry=entry,
                active_users=active_users,
                error=error,
                title=title,
                description=description,
                priority=priority,
                assigned_to_user_id=assigned_to_user_id
            )

        action_id = create_action(
            conn,
            title=title,
            description=description or None,
            source_table="shift_care_task_entries",
            source_id=entry_id,
            shift_id=entry["shift_id"],
            created_by_user_id=session["user_id"],
            assigned_to_user_id=assigned_to_user_id,
            priority=priority
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "action_detail",
                action_id=action_id
            )
        )

    default_description = (
        f"Care task: {entry['task_name']}\n"
        f"Date: {entry['shift_date']}\n"
        f"Shift: {entry['shift_type']}\n"
        f"Outcome: {entry['outcome']}"
    )

    if entry["comment"]:
        default_description += (
            f"\nOperational comment: "
            f"{entry['comment']}"
        )

    conn.close()

    return render_template(
        "care_action_new.html",
        entry=entry,
        active_users=active_users,
        error=None,
        title=f"Care Follow-up: {entry['task_name']}",
        description=default_description,
        priority="Medium",
        assigned_to_user_id=None
    )

@app.route(
    "/manager-review/care/<int:entry_id>/review",
    methods=["POST"]
)
def review_care_entry(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT entry_id
        FROM shift_care_task_entries
        WHERE entry_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Care task entry not found", 404

    create_acknowledgement(
        conn,
        source_table="shift_care_task_entries",
        source_id=entry_id,
        user_id=session["user_id"],
        acknowledgement_type="Review"
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("care_review_list")
        + f"#care-entry-{entry_id}"
    )

@app.route(
    "/manager-review/toileting/<int:entry_id>/review",
    methods=["POST"]
)
def review_toileting_entry(entry_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in [
        "Admin",
        "Program Manager",
        "Director"
    ]:
        return "Access denied", 403

    conn = get_db()

    entry = conn.execute("""
        SELECT toileting_event_id
        FROM toileting_events
        WHERE toileting_event_id = ?
    """, (entry_id,)).fetchone()

    if entry is None:
        conn.close()
        return "Toileting event not found", 404

    create_acknowledgement(
        conn,
        source_table="toileting_events",
        source_id=entry_id,
        user_id=session["user_id"],
        acknowledgement_type="Review"
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("toileting_review_list")
        + f"#toileting-entry-{entry_id}"
    )

#####################################################################
# ACKNOWLEDGEMENT FRAMEWORK
#####################################################################

def create_acknowledgement(
    conn,
    source_table,
    source_id,
    user_id,
    acknowledgement_type="Read",
    comment=None
):
    existing = conn.execute("""
        SELECT acknowledgement_id
        FROM acknowledgements
        WHERE source_table = ?
          AND source_id = ?
          AND user_id = ?
          AND active = 1
    """, (
        source_table,
        source_id,
        user_id
    )).fetchone()

    if existing:
        return existing["acknowledgement_id"]

    acknowledged_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur = conn.execute("""
        INSERT INTO acknowledgements
        (
            source_table,
            source_id,
            user_id,
            acknowledged_at,
            acknowledgement_type,
            comment,
            active
        )
        VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (
        source_table,
        source_id,
        user_id,
        acknowledged_at,
        acknowledgement_type,
        comment
    ))

    acknowledgement_id = cur.lastrowid

    log_activity(
        conn,
        activity_class="ACKNOWLEDGEMENT",
        activity_type="record_acknowledged",
        summary=f"{acknowledgement_type} acknowledgement recorded",
        user_id=user_id,
        related_table="acknowledgements",
        related_id=acknowledgement_id,
        details=f"{source_table} #{source_id}",
        success=1
    )

    return acknowledgement_id

#####################################################################
# MANAGEMENT NOTES FRAMEWORK
#####################################################################

def get_management_notes(
    conn,
    source_table,
    source_id
):
    return conn.execute("""
        SELECT
            mn.management_note_id,
            mn.source_table,
            mn.source_id,
            mn.note_text,
            mn.visibility,
            mn.created_by_user_id,
            mn.created_at,
            mn.active,
            mn.shared_at,
            mn.shared_by_user_id,

            created_by.full_name AS created_by_name

        FROM management_notes mn

        JOIN users created_by
            ON mn.created_by_user_id = created_by.user_id

        WHERE mn.source_table = ?
          AND mn.source_id = ?
          AND mn.active = 1

        ORDER BY
            mn.created_at ASC,
            mn.management_note_id ASC
    """, (
        source_table,
        source_id
    )).fetchall()

def add_management_note(
    conn,
    source_table,
    source_id,
    note_text,
    created_by_user_id,
    visibility="management_only",
    shift_id=None
):
    note_text = note_text.strip()

    if not note_text:
        raise ValueError("Management note text is required.")

    allowed_visibility = [
        "management_only"
    ]

    if visibility not in allowed_visibility:
        raise ValueError("Invalid management note visibility.")

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur = conn.execute("""
        INSERT INTO management_notes
        (
            source_table,
            source_id,
            note_text,
            visibility,
            created_by_user_id,
            created_at,
            active
        )
        VALUES (?, ?, ?, ?, ?, ?, 1)
    """, (
        source_table,
        source_id,
        note_text,
        visibility,
        created_by_user_id,
        created_at
    ))

    management_note_id = cur.lastrowid

    log_activity(
        conn,
        activity_class="MANAGEMENT_NOTE",
        activity_type="management_note_added",
        summary="Management note added",
        user_id=created_by_user_id,
        shift_id=shift_id,
        related_table="management_notes",
        related_id=management_note_id,
        details=f"{source_table} #{source_id}",
        success=1
    )

    return management_note_id

def get_management_note_count(
    conn,
    source_table,
    source_id
):
    result = conn.execute("""
        SELECT COUNT(*) AS note_count
        FROM management_notes
        WHERE source_table = ?
          AND source_id = ?
          AND active = 1
    """, (
        source_table,
        source_id
    )).fetchone()

    return result["note_count"]

#####################################################################
# ACTION MANAGEMENT
#####################################################################

def create_action(
    conn,
    title,
    description=None,
    source_table=None,
    source_id=None,
    shift_id=None,
    created_by_user_id=None,
    assigned_to_user_id=None,
    priority="Medium",
    due_date=None
):
    cur = conn.execute("""
        INSERT INTO action_items
        (
            title,
            description,
            status,
            priority,
            source_table,
            source_id,
            shift_id,
            assigned_to_user_id,
            created_by_user_id,
            due_date
        )
        VALUES (?, ?, 'Open', ?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        description,
        priority,
        source_table,
        source_id,
        shift_id,
        assigned_to_user_id,
        created_by_user_id,
        due_date
    ))

    action_id = cur.lastrowid

    log_activity(
        conn,
        activity_class="ACTION",
        activity_type="action_created",
        summary=title,
        user_id=created_by_user_id,
        shift_id=shift_id,
        related_table="action_items",
        related_id=action_id,
        details=description,
        success=1
    )

    return action_id

@app.route("/actions")
def actions():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()

    actions = conn.execute("""
        SELECT
            ai.*,
            u.full_name AS assigned_to
        FROM action_items ai

        LEFT JOIN users u
            ON ai.assigned_to_user_id = u.user_id

        WHERE ai.status NOT IN ('Completed', 'Closed')

        ORDER BY
            CASE ai.priority
                WHEN 'High' THEN 1
                WHEN 'Medium' THEN 2
                WHEN 'Low' THEN 3
                ELSE 4
            END,
            ai.created_at DESC
    """).fetchall()

    conn.close()

    return render_template(
        "actions.html",
        actions=actions
    )

@app.route("/action/<int:action_id>", methods=["GET", "POST"])
def action_detail(action_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()

    # Read the basic action first (used by POST processing)
    action = conn.execute("""
        SELECT *
        FROM action_items
        WHERE action_id = ?
    """, (action_id,)).fetchone()

    if action is None:
        conn.close()
        return "Action not found", 404

    if request.method == "POST":

        form_type = request.form.get("form_type")

        #
        # Update Action
        #
        if form_type == "update":

            old_status = action["status"]
            old_priority = action["priority"]
            old_assigned_to_user_id = action["assigned_to_user_id"]

            status = request.form["status"]
            priority = request.form["priority"]
            assigned_to_user_id = request.form.get("assigned_to_user_id")

            if assigned_to_user_id == "":
                assigned_to_user_id = None
            else:
                assigned_to_user_id = int(assigned_to_user_id)

            conn.execute("""
                UPDATE action_items
                SET status = ?,
                    priority = ?,
                    assigned_to_user_id = ?,
                    acknowledged_at = CASE
                        WHEN ? = 'Acknowledged' AND acknowledged_at IS NULL
                        THEN CURRENT_TIMESTAMP
                        ELSE acknowledged_at
                    END,
                    completed_at = CASE
                        WHEN ? = 'Completed' AND completed_at IS NULL
                        THEN CURRENT_TIMESTAMP
                        ELSE completed_at
                    END,
                    closed_at = CASE
                        WHEN ? = 'Closed' AND closed_at IS NULL
                        THEN CURRENT_TIMESTAMP
                        ELSE closed_at
                    END
                WHERE action_id = ?
            """, (
                status,
                priority,
                assigned_to_user_id,
                status,
                status,
                status,
                action_id
            ))

            if old_status != status:
                log_activity(
                    conn,
                    activity_class="ACTION",
                    activity_type="action_status_changed",
                    summary=f"Action status changed: {action['title']}",
                    user_id=session["user_id"],
                    shift_id=action["shift_id"],
                    related_table="action_items",
                    related_id=action_id,
                    details=f"Status changed from {old_status} to {status}",
                    success=1
                )

            if old_priority != priority:
                log_activity(
                    conn,
                    activity_class="ACTION",
                    activity_type="action_priority_changed",
                    summary=f"Action priority changed: {action['title']}",
                    user_id=session["user_id"],
                    shift_id=action["shift_id"],
                    related_table="action_items",
                    related_id=action_id,
                    details=f"Priority changed from {old_priority} to {priority}",
                    success=1
                )

            if old_assigned_to_user_id != assigned_to_user_id:
                log_activity(
                    conn,
                    activity_class="ACTION",
                    activity_type="action_assigned",
                    summary=f"Action assignment changed: {action['title']}",
                    user_id=session["user_id"],
                    shift_id=action["shift_id"],
                    related_table="action_items",
                    related_id=action_id,
                    details=f"Assigned user changed from {old_assigned_to_user_id} to {assigned_to_user_id}",
                    success=1
                )

            conn.commit()

            return redirect(url_for("action_detail", action_id=action_id))

        #
        # Add Comment
        #
        elif form_type == "comment":

            comment = request.form["comment"].strip()

            if comment:

                cur = conn.execute("""
                    INSERT INTO action_comments
                    (
                        action_id,
                        user_id,
                        comment
                    )
                    VALUES (?, ?, ?)
                """, (
                    action_id,
                    session["user_id"],
                    comment
                ))

                comment_id = cur.lastrowid

                log_activity(
                    conn,
                    activity_class="ACTION",
                    activity_type="action_comment_added",
                    summary=f"Comment added to action: {action['title']}",
                    user_id=session["user_id"],
                    shift_id=action["shift_id"],
                    related_table="action_comments",
                    related_id=comment_id,
                    details=comment,
                    success=1
                )

                conn.commit()

            return redirect(url_for("action_detail", action_id=action_id))

    users = conn.execute("""
        SELECT user_id, full_name, role
        FROM users
        WHERE active = 1
        ORDER BY full_name
    """).fetchall()

    action = conn.execute("""
        SELECT
            ai.*,
            u.full_name AS assigned_to,
            cu.full_name AS created_by,
            s.shift_type,
            s.shift_date
        FROM action_items ai

        LEFT JOIN users u
            ON ai.assigned_to_user_id = u.user_id

        LEFT JOIN users cu
            ON ai.created_by_user_id = cu.user_id

        LEFT JOIN shifts s
            ON ai.shift_id = s.shift_id

        WHERE ai.action_id = ?
    """, (action_id,)).fetchone()

    comments = conn.execute("""
        SELECT
            ac.*,
            u.full_name
        FROM action_comments ac

        LEFT JOIN users u
            ON ac.user_id = u.user_id

        WHERE ac.action_id = ?

        ORDER BY ac.created_at
    """, (action_id,)).fetchall()

    history = conn.execute("""
        SELECT
            al.*,
            u.full_name
        FROM activity_log al

        LEFT JOIN users u
          ON al.user_id = u.user_id

        WHERE al.related_table = 'action_items'
        AND al.related_id = ?

        ORDER BY al.activity_datetime
    """, (action_id,)).fetchall()

    conn.close()

    return render_template(
        "action_detail.html",
        action=action,
        users=users,
        comments=comments,
        history=history
    )

#####################################################################
# CARE TASKS
#####################################################################

@app.route("/care-tasks")
def care_tasks():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    query = """
        SELECT
            ct.*,
            ctc.category_name
        FROM care_tasks ct

        LEFT JOIN care_task_categories ctc
            ON ct.category_id = ctc.category_id
    """

    parameters = ()

    if active_filter is not None:
        query += """
            WHERE ct.active = ?
        """
        parameters = (active_filter,)

    query += """
        ORDER BY ct.task_name
    """

    tasks = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "care_tasks.html",
        tasks=tasks,
        status_filter=status_filter
    )

@app.route("/care-task/new", methods=["GET", "POST"])
def care_task_new():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    error = None
    conn = get_db()

    categories = conn.execute("""
        SELECT *
        FROM care_task_categories
        WHERE active = 1
        ORDER BY category_name
    """).fetchall()

    if request.method == "POST":

        task_name = request.form.get("task_name", "").strip()
        category_id = request.form.get("category_id")
        instructions = request.form.get("instructions", "").strip()
        schedule_type = request.form.get("schedule_type", "")
        occurs = ",".join(request.form.getlist("occurs"))
        timing_type = request.form.get("timing_type", "")
        due_time = request.form.get("due_time", "").strip()
        days_of_week = ",".join(request.form.getlist("days_of_week"))

        required = 1 if "required" in request.form else 0

        comment_required_attempted = (
            1 if "comment_required_attempted" in request.form else 0
        )

        comment_required_not_completed = (
            1 if "comment_required_not_completed" in request.form else 0
        )

        active = 1 if "active" in request.form else 0

        if not task_name:
            error = "Task name is required."

        elif not category_id:
            error = "Category is required."

        elif not occurs:
            error = "At least one shift occurrence is required."

        else:
            cur = conn.execute("""
                INSERT INTO care_tasks
                (
                    task_name,
                    category_id,
                    instructions,
                    schedule_type,
                    occurs,
                    timing_type,
                    due_time,
                    days_of_week,
                    required,
                    comment_required_attempted,
                    comment_required_not_completed,
                    active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_name,
                int(category_id),
                instructions,
                schedule_type,
                occurs,
                timing_type,
                due_time if due_time else None,
                days_of_week,
                required,
                comment_required_attempted,
                comment_required_not_completed,
                active
            ))

            care_task_id = cur.lastrowid

            log_activity(
                conn,
                activity_class="ADMIN",
                activity_type="care_task_created",
                summary=f"Care task created: {task_name}",
                user_id=session["user_id"],
                related_table="care_tasks",
                related_id=care_task_id,
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(url_for("care_tasks"))

    conn.close()

    return render_template(
        "care_task_new.html",
        error=error,
        categories=categories,
        form_data=request.form,
        is_post=(request.method == "POST")
    )

@app.route("/care-task/edit/<int:care_task_id>", methods=["GET", "POST"])
def care_task_edit(care_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT *
        FROM care_tasks
        WHERE care_task_id = ?
    """, (care_task_id,)).fetchone()

    if task is None:
        conn.close()
        return "Care task not found", 404

    categories = conn.execute("""
        SELECT *
        FROM care_task_categories
        WHERE active = 1
           OR category_id = ?
        ORDER BY category_name
    """, (task["category_id"],)).fetchall()

    error = None

    if request.method == "POST":

        task_name = request.form.get("task_name", "").strip()
        category_id = request.form.get("category_id")
        instructions = request.form.get("instructions", "").strip()
        schedule_type = request.form.get("schedule_type", "")
        occurs = ",".join(request.form.getlist("occurs"))
        timing_type = request.form.get("timing_type", "")
        due_time = request.form.get("due_time", "").strip()
        days_of_week = ",".join(request.form.getlist("days_of_week"))

        required = 1 if "required" in request.form else 0

        comment_required_attempted = (
            1 if "comment_required_attempted" in request.form else 0
        )

        comment_required_not_completed = (
            1 if "comment_required_not_completed" in request.form else 0
        )

        active = 1 if "active" in request.form else 0

        if not task_name:
            error = "Task name is required."

        elif not category_id:
            error = "Category is required."

        elif not occurs:
            error = "At least one shift occurrence is required."

        else:
            conn.execute("""
                UPDATE care_tasks
                SET task_name = ?,
                    category_id = ?,
                    instructions = ?,
                    schedule_type = ?,
                    occurs = ?,
                    timing_type = ?,
                    due_time = ?,
                    days_of_week = ?,
                    required = ?,
                    comment_required_attempted = ?,
                    comment_required_not_completed = ?,
                    active = ?
                WHERE care_task_id = ?
            """, (
                task_name,
                int(category_id),
                instructions,
                schedule_type,
                occurs,
                timing_type,
                due_time if due_time else None,
                days_of_week,
                required,
                comment_required_attempted,
                comment_required_not_completed,
                active,
                care_task_id
            ))

            log_activity(
                conn,
                activity_class="ADMIN",
                activity_type="care_task_updated",
                summary=f"Care task updated: {task_name}",
                user_id=session["user_id"],
                related_table="care_tasks",
                related_id=care_task_id,
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(url_for("care_tasks", status=status_filter))

    conn.close()

    return render_template(
        "care_task_edit.html",
        task=task,
        categories=categories,
        error=error,
        status_filter=status_filter
    )

@app.route("/care-task/deactivate/<int:care_task_id>")
def care_task_deactivate(care_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT task_name
        FROM care_tasks
        WHERE care_task_id = ?
    """, (care_task_id,)).fetchone()

    conn.execute("""
        UPDATE care_tasks
        SET active = 0
        WHERE care_task_id = ?
    """, (care_task_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="care_task_deactivated",
        summary=f"Care task deactivated: {task['task_name']} (ID {care_task_id})",
        user_id=session["user_id"],
        related_table="care_tasks",
        related_id=care_task_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(url_for("care_tasks", status=status_filter))

@app.route("/care-task/reactivate/<int:care_task_id>")
def care_task_reactivate(care_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT task_name
        FROM care_tasks
        WHERE care_task_id = ?
    """, (care_task_id,)).fetchone()



    conn.execute("""
        UPDATE care_tasks
        SET active = 1
        WHERE care_task_id = ?
    """, (care_task_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="care_task_reactivated",
        summary=f"Care task reactivated: {task['task_name']} (ID {care_task_id})",
        user_id=session["user_id"],
        related_table="care_tasks",
        related_id=care_task_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(url_for("care_tasks", status=status_filter))

@app.route("/care-task-categories")
def care_task_categories():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    query = """
        SELECT *
        FROM care_task_categories
    """

    parameters = ()

    if active_filter is not None:
        query += """
            WHERE active = ?
        """
        parameters = (active_filter,)

    query += """
        ORDER BY category_name
    """

    categories = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "care_task_categories.html",
        categories=categories,
        status_filter=status_filter
    )

@app.route("/care-task-category/new", methods=["GET", "POST"])
def care_task_category_new():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    error = None

    if request.method == "POST":

        category_name = request.form["category_name"].strip()

        if not category_name:
            error = "Category name is required."

        else:

            conn = get_db()

            existing = conn.execute("""
                SELECT *
                FROM care_task_categories
                WHERE category_name = ?
            """, (category_name,)).fetchone()

            if existing:

                error = "Category already exists."

                conn.close()

            else:

                conn.execute("""
                    INSERT INTO care_task_categories
                    (category_name, active)
                    VALUES (?,1)
                """, (category_name,))

                conn.commit()
                conn.close()

                return redirect(url_for("care_task_categories"))

    return render_template(
        "care_task_category_new.html",
        error=error
    )

@app.route(
    "/care-task-category/edit/<int:category_id>",
    methods=["GET", "POST"]
)
def care_task_category_edit(category_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    category = conn.execute("""
        SELECT *
        FROM care_task_categories
        WHERE category_id = ?
    """, (category_id,)).fetchone()

    if category is None:
        conn.close()
        return "Care category not found", 404

    error = None

    if request.method == "POST":

        category_name = request.form["category_name"].strip()

        if not category_name:
            error = "Category name is required."

        else:

            existing = conn.execute("""
                SELECT *
                FROM care_task_categories
                WHERE category_name = ?
                  AND category_id <> ?
            """, (
                category_name,
                category_id
            )).fetchone()

            if existing:

                error = "Category already exists."

            else:

                conn.execute("""
                    UPDATE care_task_categories
                    SET category_name = ?
                    WHERE category_id = ?
                """, (
                    category_name,
                    category_id
                ))

                log_activity(
                    conn,
                    activity_class="ADMIN",
                    activity_type="care_category_updated",
                    summary=f"Care category updated: {category_name}",
                    user_id=session["user_id"],
                    related_table="care_task_categories",
                    related_id=category_id,
                    success=1
                )

                conn.commit()
                conn.close()

                return redirect(
                    url_for("care_task_categories", status=status_filter)
                )

    conn.close()

    return render_template(
        "care_task_category_edit.html",
        category=category,
        error=error,
        status_filter=status_filter
    )

@app.route("/care-task-category/deactivate/<int:category_id>")
def care_task_category_deactivate(category_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    category = conn.execute("""
        SELECT *
        FROM care_task_categories
        WHERE category_id = ?
    """, (category_id,)).fetchone()

    if category is None:
        conn.close()
        return "Care category not found", 404

    active_task_count = conn.execute("""
        SELECT COUNT(*) AS count
        FROM care_tasks
        WHERE category_id = ?
          AND active = 1
    """, (category_id,)).fetchone()["count"]

    if active_task_count > 0:
        conn.close()

        return (
            "This category cannot be deactivated because it is being used "
            "by one or more active care tasks. "
            "Please deactivate or move those tasks first.",
            400
        )

    conn.execute("""
        UPDATE care_task_categories
        SET active = 0
        WHERE category_id = ?
    """, (category_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="care_category_deactivated",
        summary=f"Care category deactivated: {category['category_name']}",
        user_id=session["user_id"],
        related_table="care_task_categories",
        related_id=category_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("care_task_categories", status=status_filter)
    )

@app.route("/care-task-category/reactivate/<int:category_id>")
def care_task_category_reactivate(category_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    category = conn.execute("""
        SELECT *
        FROM care_task_categories
        WHERE category_id = ?
    """, (category_id,)).fetchone()

    if category is None:
        conn.close()
        return "Care category not found", 404

    conn.execute("""
        UPDATE care_task_categories
        SET active = 1
        WHERE category_id = ?
    """, (category_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="care_category_reactivated",
        summary=f"Care category reactivated: {category['category_name']}",
        user_id=session["user_id"],
        related_table="care_task_categories",
        related_id=category_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("care_task_categories", status=status_filter)
    )

@app.route("/shift/<int:shift_id>/care-task/<int:care_task_id>/record", methods=["GET", "POST"])
def shift_care_task_record(shift_id, care_task_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    shift = conn.execute("""
        SELECT *
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()

    task = conn.execute("""
        SELECT *
        FROM care_tasks
        WHERE care_task_id = ?
    """, (care_task_id,)).fetchone()

    if shift is None:
        conn.close()
        return "Shift not found", 404

    if task is None:
        conn.close()
        return "Care task not found", 404

    if request.method == "POST":
        status = request.form.get("status", "")
        comment = request.form.get("comment", "").strip()

        error = None

        valid_outcomes = [
            "Completed",
            "Attempted",
            "Not Completed",
        ]

        if status not in valid_outcomes:
            error = "Please select a valid outcome."

        elif (
            status == "Attempted"
            and task["comment_required_attempted"] == 1
            and not comment
        ):
            error = "A comment is required when this task is Attempted."

        elif (
            status == "Not Completed"
            and task["comment_required_not_completed"] == 1
            and not comment
        ):
            error = (
                "A comment is required when this task is Not Completed."
            )

        if error:
            conn.close()

            return render_template(
                "shift_care_task_record.html",
                shift=shift,
                task=task,
                error=error,
                selected_status=status,
                comment=comment
            )

        existing = conn.execute("""
            SELECT entry_id
            FROM shift_care_task_entries
            WHERE shift_id = ?
              AND care_task_id = ?
        """, (
            shift_id,
            care_task_id
        )).fetchone()

        if existing:

            conn.close()

            return redirect(
                url_for(
                    "shift_care_task_entry_edit",
                    shift_id=shift_id,
                    entry_id=existing["entry_id"]
                )
            )

        cur = conn.execute("""
            INSERT INTO shift_care_task_entries
            (
                shift_id,
                care_task_id,
                outcome,
                comment,
                completed_by_user_id
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            shift_id,
            care_task_id,
            status,
            comment,
            session["user_id"]
        ))

        entry_id = cur.lastrowid

        log_activity(
            conn,
            activity_class="CARE",
            activity_type=f"care_task_{status.lower().replace(' ', '_')}",
            summary=f"{task['task_name']} - {status}",
            user_id=session["user_id"],
            client_id=shift["client_id"],
            shift_id=shift_id,
            related_table="shift_care_task_entries",
            related_id=entry_id,
            details=comment,
            success=1
        )

        conn.commit()
        conn.close()

        return redirect(url_for("shift_dashboard", shift_id=shift_id))

    conn.close()

    return render_template(
        "shift_care_task_record.html",
        shift=shift,
        task=task
    )

@app.route(
    "/shift/<int:shift_id>/care-task-entry/<int:entry_id>/edit",
    methods=["GET", "POST"]
)
def shift_care_task_entry_edit(shift_id, entry_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    entry = conn.execute("""
        SELECT
            shift_care_task_entries.*,
            care_tasks.task_name,
            care_tasks.comment_required_attempted,
            care_tasks.comment_required_not_completed,
            shifts.client_id
        FROM shift_care_task_entries
        JOIN care_tasks
            ON care_tasks.care_task_id =
               shift_care_task_entries.care_task_id
        JOIN shifts
            ON shifts.shift_id =
               shift_care_task_entries.shift_id
        WHERE shift_care_task_entries.entry_id = ?
          AND shift_care_task_entries.shift_id = ?
    """, (
        entry_id,
        shift_id
    )).fetchone()

    if entry is None:
        conn.close()
        return "Care task entry not found", 404

    if request.method == "POST":
        outcome = request.form.get("status", "")
        comment = request.form.get("comment", "").strip()

        error = None

        valid_outcomes = [
            "Completed",
            "Attempted",
            "Not Completed",
        ]

        legacy_outcome_unchanged = (
            entry["outcome"] == "Not Applicable"
            and outcome == "Not Applicable"
        )

        if outcome not in valid_outcomes and not legacy_outcome_unchanged:
            error = "Please select a valid outcome."

        elif (
            outcome == "Attempted"
            and entry["comment_required_attempted"] == 1
            and not comment
        ):
            error = (
                "A comment is required when this task is Attempted."
            )

        elif (
            outcome == "Not Completed"
            and entry["comment_required_not_completed"] == 1
            and not comment
        ):
            error = (
                "A comment is required when this task is Not Completed."
            )

        if error:
            conn.close()

            return render_template(
                "shift_care_task_edit.html",
                entry=entry,
                shift_id=shift_id,
                error=error,
                selected_status=outcome,
                comment=comment
            )

        conn.execute("""
            UPDATE shift_care_task_entries
            SET outcome = ?,
                comment = ?
            WHERE entry_id = ?
              AND shift_id = ?
        """, (
            outcome,
            comment,
            entry_id,
            shift_id
        ))

        log_activity(
            conn,
            activity_class="CARE",
            activity_type="care_task_updated",
            summary=f"{entry['task_name']} updated to '{outcome}'",
            user_id=session["user_id"],
            client_id=entry["client_id"],
            shift_id=shift_id,
            related_table="shift_care_task_entries",
            related_id=entry_id,
            details=comment,
            success=1
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "shift_dashboard",
                shift_id=shift_id
            )
        )

    conn.close()

    return render_template(
        "shift_care_task_edit.html",
        entry=entry,
        shift_id=shift_id,
        error=None,
        selected_status=entry["outcome"],
        comment=entry["comment"]
    )

#####################################################################
# HOUSEKEEPING TASKS
#####################################################################

#
# Category Administration
#

@app.route("/housekeeping-task-categories")
def housekeeping_task_categories():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    query = """
        SELECT *
        FROM housekeeping_task_categories
    """

    parameters = ()

    if active_filter is not None:
        query += """
            WHERE active = ?
        """
        parameters = (active_filter,)

    query += """
        ORDER BY category_name
    """

    categories = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "housekeeping_task_categories.html",
        categories=categories,
        status_filter=status_filter
    )

@app.route("/housekeeping-task-category/new", methods=["GET", "POST"])
def housekeeping_task_category_new():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    error = None

    if request.method == "POST":

        category_name = request.form["category_name"].strip()

        if not category_name:
            error = "Category name is required."

        else:
            conn = get_db()

            existing = conn.execute("""
                SELECT category_id
                FROM housekeeping_task_categories
                WHERE LOWER(category_name) = LOWER(?)
            """, (category_name,)).fetchone()

            if existing:
                error = "Category already exists."
                conn.close()

            else:
                cur = conn.execute("""
                    INSERT INTO housekeeping_task_categories
                    (
                        category_name,
                        active
                    )
                    VALUES (?, 1)
                """, (category_name,))

                category_id = cur.lastrowid

                log_activity(
                    conn,
                    activity_class="ADMIN",
                    activity_type="housekeeping_category_created",
                    summary=f"Housekeeping category created: {category_name}",
                    user_id=session["user_id"],
                    related_table="housekeeping_task_categories",
                    related_id=category_id,
                    success=1
                )

                conn.commit()
                conn.close()

                return redirect(
                    url_for("housekeeping_task_categories")
                )

    return render_template(
        "housekeeping_task_category_new.html",
        error=error
    )

@app.route(
    "/housekeeping-task-category/edit/<int:category_id>",
    methods=["GET", "POST"]
)
def housekeeping_task_category_edit(category_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    category = conn.execute("""
        SELECT *
        FROM housekeeping_task_categories
        WHERE category_id = ?
    """, (category_id,)).fetchone()

    if category is None:
        conn.close()
        return "Housekeeping category not found", 404

    error = None

    if request.method == "POST":
        category_name = request.form["category_name"].strip()

        if not category_name:
            error = "Category name is required."

        else:
            existing = conn.execute("""
                SELECT category_id
                FROM housekeeping_task_categories
                WHERE LOWER(category_name) = LOWER(?)
                  AND category_id <> ?
            """, (
                category_name,
                category_id
            )).fetchone()

            if existing:
                error = "Category already exists."

            else:
                old_name = category["category_name"]

                conn.execute("""
                    UPDATE housekeeping_task_categories
                    SET category_name = ?
                    WHERE category_id = ?
                """, (
                    category_name,
                    category_id
                ))

                log_activity(
                    conn,
                    activity_class="ADMIN",
                    activity_type="housekeeping_category_updated",
                    summary=f"Housekeeping category updated: {category_name}",
                    user_id=session["user_id"],
                    related_table="housekeeping_task_categories",
                    related_id=category_id,
                    details=f"Category name changed from {old_name} to {category_name}",
                    success=1
                )

                conn.commit()
                conn.close()

                return redirect(
                    url_for(
                        "housekeeping_task_categories",
                        status=status_filter
                    )
                )

    conn.close()

    return render_template(
        "housekeeping_task_category_edit.html",
        category=category,
        error=error,
        status_filter=status_filter
    )

@app.route("/housekeeping-task-category/deactivate/<int:category_id>")
def housekeeping_task_category_deactivate(category_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    category = conn.execute("""
        SELECT *
        FROM housekeeping_task_categories
        WHERE category_id = ?
    """, (category_id,)).fetchone()

    if category is None:
        conn.close()
        return "Housekeeping category not found", 404

    active_task_count = conn.execute("""
        SELECT COUNT(*) AS count
        FROM housekeeping_tasks
        WHERE category_id = ?
          AND active = 1
    """, (category_id,)).fetchone()["count"]

    if active_task_count > 0:
        conn.close()

        return (
            "This category cannot be deactivated because it is being used "
            "by one or more active housekeeping tasks. "
            "Please deactivate or move those tasks first.",
            400
        )

    conn.execute("""
        UPDATE housekeeping_task_categories
        SET active = 0
        WHERE category_id = ?
    """, (category_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="housekeeping_category_deactivated",
        summary=(
            f"Housekeeping category deactivated: "
            f"{category['category_name']}"
        ),
        user_id=session["user_id"],
        related_table="housekeeping_task_categories",
        related_id=category_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("housekeeping_task_categories", status=status_filter)
    )

@app.route("/housekeeping-task-category/reactivate/<int:category_id>")
def housekeeping_task_category_reactivate(category_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    category = conn.execute("""
        SELECT *
        FROM housekeeping_task_categories
        WHERE category_id = ?
    """, (category_id,)).fetchone()

    if category is None:
        conn.close()
        return "Housekeeping category not found", 404

    conn.execute("""
        UPDATE housekeeping_task_categories
        SET active = 1
        WHERE category_id = ?
    """, (category_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="housekeeping_category_reactivated",
        summary=f"Housekeeping category reactivated: {category['category_name']}",
        user_id=session["user_id"],
        related_table="housekeeping_task_categories",
        related_id=category_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("housekeeping_task_categories", status=status_filter)
    )

#
# Task Administration
#

@app.route("/housekeeping-tasks")
def housekeeping_tasks():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    query = """
        SELECT
            ht.*,
            htc.category_name
        FROM housekeeping_tasks ht

        LEFT JOIN housekeeping_task_categories htc
            ON ht.category_id = htc.category_id
    """

    parameters = ()

    if active_filter is not None:
        query += """
            WHERE ht.active = ?
        """
        parameters = (active_filter,)

    query += """
        ORDER BY ht.task_name
    """

    tasks = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "housekeeping_tasks.html",
        tasks=tasks,
        status_filter=status_filter
    )

@app.route("/housekeeping-task/new", methods=["GET", "POST"])
def housekeeping_task_new():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    error = None
    conn = get_db()

    categories = conn.execute("""
        SELECT *
        FROM housekeeping_task_categories
        WHERE active = 1
        ORDER BY category_name
    """).fetchall()

    if request.method == "POST":

        task_name = request.form.get("task_name", "").strip()
        category_id = request.form.get("category_id")
        instructions = request.form.get("instructions", "").strip()
        schedule_type = request.form.get("schedule_type", "")
        occurs = ",".join(request.form.getlist("occurs"))
        timing_type = request.form.get("timing_type", "")
        due_time = request.form.get("due_time", "").strip()
        days_of_week = ",".join(request.form.getlist("days_of_week"))

        required = 1 if "required" in request.form else 0
        comment_required_attempted = (
            1 if "comment_required_attempted" in request.form else 0
        )
        comment_required_not_completed = (
            1 if "comment_required_not_completed" in request.form else 0
        )
        active = 1 if "active" in request.form else 0

        if not task_name:
            error = "Task name is required."

        elif not category_id:
            error = "Category is required."

        elif not occurs:
            error = "At least one shift occurrence is required."

        else:
            cur = conn.execute("""
                INSERT INTO housekeeping_tasks
                (
                    task_name,
                    category_id,
                    instructions,
                    schedule_type,
                    occurs,
                    timing_type,
                    due_time,
                    days_of_week,
                    required,
                    comment_required_attempted,
                    comment_required_not_completed,
                    active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_name,
                int(category_id),
                instructions,
                schedule_type,
                occurs,
                timing_type,
                due_time if due_time else None,
                days_of_week,
                required,
                comment_required_attempted,
                comment_required_not_completed,
                active
            ))

            housekeeping_task_id = cur.lastrowid

            log_activity(
                conn,
                activity_class="ADMIN",
                activity_type="housekeeping_task_created",
                summary=f"Housekeeping task created: {task_name}",
                user_id=session["user_id"],
                related_table="housekeeping_tasks",
                related_id=housekeeping_task_id,
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(url_for("housekeeping_tasks"))

    conn.close()

    return render_template(
        "housekeeping_task_new.html",
        categories=categories,
        error=error,
        form_data=request.form,
        is_post=(request.method == "POST")
    )

@app.route(
    "/housekeeping-task/edit/<int:housekeeping_task_id>",
    methods=["GET", "POST"]
)
def housekeeping_task_edit(housekeeping_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT *
        FROM housekeeping_tasks
        WHERE housekeeping_task_id = ?
    """, (housekeeping_task_id,)).fetchone()

    if task is None:
        conn.close()
        return "Housekeeping task not found", 404

    categories = conn.execute("""
        SELECT *
        FROM housekeeping_task_categories
        WHERE active = 1
           OR category_id = ?
        ORDER BY category_name
    """, (task["category_id"],)).fetchall()

    error = None

    if request.method == "POST":

        task_name = request.form["task_name"].strip()
        category_id = request.form.get("category_id")
        instructions = request.form.get("instructions", "").strip()
        schedule_type = request.form["schedule_type"]
        occurs = ",".join(request.form.getlist("occurs"))
        timing_type = request.form["timing_type"]
        due_time = request.form.get("due_time", "").strip()
        days_of_week = ",".join(request.form.getlist("days_of_week"))

        required = 1 if "required" in request.form else 0
        comment_required_attempted = (
            1 if "comment_required_attempted" in request.form else 0
        )
        comment_required_not_completed = (
            1 if "comment_required_not_completed" in request.form else 0
        )
        active = 1 if "active" in request.form else 0

        if not task_name:
            error = "Task name is required."

        elif not category_id:
            error = "Category is required."

        elif not occurs:
            error = "At least one shift occurrence is required."

        else:
            conn.execute("""
                UPDATE housekeeping_tasks
                SET task_name = ?,
                    category_id = ?,
                    instructions = ?,
                    schedule_type = ?,
                    occurs = ?,
                    timing_type = ?,
                    due_time = ?,
                    days_of_week = ?,
                    required = ?,
                    comment_required_attempted = ?,
                    comment_required_not_completed = ?,
                    active = ?
                WHERE housekeeping_task_id = ?
            """, (
                task_name,
                int(category_id),
                instructions,
                schedule_type,
                occurs,
                timing_type,
                due_time if due_time else None,
                days_of_week,
                required,
                comment_required_attempted,
                comment_required_not_completed,
                active,
                housekeeping_task_id
            ))

            log_activity(
                conn,
                activity_class="ADMIN",
                activity_type="housekeeping_task_updated",
                summary=f"Housekeeping task updated: {task_name}",
                user_id=session["user_id"],
                related_table="housekeeping_tasks",
                related_id=housekeeping_task_id,
                success=1
            )

            conn.commit()
            conn.close()

            return redirect(
                url_for("housekeeping_tasks", status=status_filter)
            )

    conn.close()

    return render_template(
        "housekeeping_task_edit.html",
        task=task,
        categories=categories,
        error=error,
        status_filter=status_filter
    )

@app.route(
    "/housekeeping-task/deactivate/<int:housekeeping_task_id>"
)
def housekeeping_task_deactivate(housekeeping_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT *
        FROM housekeeping_tasks
        WHERE housekeeping_task_id = ?
    """, (housekeeping_task_id,)).fetchone()

    if task is None:
        conn.close()
        return "Housekeeping task not found", 404

    conn.execute("""
        UPDATE housekeeping_tasks
        SET active = 0
        WHERE housekeeping_task_id = ?
    """, (housekeeping_task_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="housekeeping_task_deactivated",
        summary=f"Housekeeping task deactivated: {task['task_name']}",
        user_id=session["user_id"],
        related_table="housekeeping_tasks",
        related_id=housekeeping_task_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(url_for("housekeeping_tasks", status=status_filter))

@app.route(
    "/housekeeping-task/reactivate/<int:housekeeping_task_id>"
)
def housekeeping_task_reactivate(housekeeping_task_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    task = conn.execute("""
        SELECT *
        FROM housekeeping_tasks
        WHERE housekeeping_task_id = ?
    """, (housekeeping_task_id,)).fetchone()

    if task is None:
        conn.close()
        return "Housekeeping task not found", 404

    conn.execute("""
        UPDATE housekeeping_tasks
        SET active = 1
        WHERE housekeeping_task_id = ?
    """, (housekeeping_task_id,))

    log_activity(
        conn,
        activity_class="ADMIN",
        activity_type="housekeeping_task_reactivated",
        summary=f"Housekeeping task reactivated: {task['task_name']}",
        user_id=session["user_id"],
        related_table="housekeeping_tasks",
        related_id=housekeeping_task_id,
        success=1
    )

    conn.commit()
    conn.close()

    return redirect(url_for("housekeeping_tasks", status=status_filter))

#
# Shift Housekeeping Operations
#

@app.route(
    "/shift/<int:shift_id>/housekeeping-task/"
    "<int:housekeeping_task_id>/record",
    methods=["GET", "POST"]
)
def shift_housekeeping_task_record(
    shift_id,
    housekeeping_task_id
):

    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    shift = conn.execute("""
        SELECT *
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()

    task = conn.execute("""
        SELECT *
        FROM housekeeping_tasks
        WHERE housekeeping_task_id = ?
    """, (housekeeping_task_id,)).fetchone()

    if shift is None:
        conn.close()
        return "Shift not found", 404

    if task is None:
        conn.close()
        return "Housekeeping task not found", 404

    if request.method == "POST":

        status = request.form.get("status", "")
        comment = request.form.get("comment", "").strip()

        error = None

        valid_outcomes = [
            "Completed",
            "Attempted",
            "Not Completed",
        ]

        if status not in valid_outcomes:
            error = "Please select a valid outcome."

        elif (
            status == "Attempted"
            and task["comment_required_attempted"] == 1
            and not comment
        ):
            error = (
                "A comment is required when this task is Attempted."
            )

        elif (
            status == "Not Completed"
            and task["comment_required_not_completed"] == 1
            and not comment
        ):
            error = (
                "A comment is required when this task is Not Completed."
            )

        if error:
            conn.close()

            return render_template(
                "shift_housekeeping_task_record.html",
                shift=shift,
                task=task,
                error=error,
                selected_status=status,
                comment=comment
            )

        existing = conn.execute("""
            SELECT entry_id
            FROM shift_housekeeping_task_entries
            WHERE shift_id = ?
              AND housekeeping_task_id = ?
        """, (
            shift_id,
            housekeeping_task_id
        )).fetchone()

        if existing:
            conn.close()

            return redirect(
                url_for(
                    "shift_housekeeping_task_entry_edit",
                    shift_id=shift_id,
                    entry_id=existing["entry_id"]
                )
            )

        cur = conn.execute("""
            INSERT INTO shift_housekeeping_task_entries
            (
                shift_id,
                housekeeping_task_id,
                outcome,
                comment,
                completed_by_user_id
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            shift_id,
            housekeeping_task_id,
            status,
            comment,
            session["user_id"]
        ))

        entry_id = cur.lastrowid

        log_activity(
            conn,
            activity_class="HOUSEKEEPING",
            activity_type=(
                f"housekeeping_task_"
                f"{status.lower().replace(' ', '_')}"
            ),
            summary=f"{task['task_name']} - {status}",
            user_id=session["user_id"],
            client_id=shift["client_id"],
            shift_id=shift_id,
            related_table="shift_housekeeping_task_entries",
            related_id=entry_id,
            details=comment,
            success=1
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "shift_dashboard",
                shift_id=shift_id
            )
        )

    conn.close()

    return render_template(
        "shift_housekeeping_task_record.html",
        shift=shift,
        task=task,
        error=None,
        selected_status=None,
        comment=""
    )


@app.route(
    "/shift/<int:shift_id>/housekeeping-task-entry/"
    "<int:entry_id>/edit",
    methods=["GET", "POST"]
)
def shift_housekeeping_task_entry_edit(
    shift_id,
    entry_id
):

    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()

    shift = conn.execute("""
        SELECT *
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()

    entry = conn.execute("""
        SELECT
            shte.*,
            ht.task_name,
            ht.comment_required_attempted,
            ht.comment_required_not_completed
        FROM shift_housekeeping_task_entries shte

        JOIN housekeeping_tasks ht
            ON shte.housekeeping_task_id =
               ht.housekeeping_task_id

        WHERE shte.entry_id = ?
          AND shte.shift_id = ?
    """, (
        entry_id,
        shift_id
    )).fetchone()

    if shift is None:
        conn.close()
        return "Shift not found", 404

    if entry is None:
        conn.close()
        return "Housekeeping task entry not found", 404

    if request.method == "POST":

        outcome = request.form.get("status", "")
        comment = request.form.get("comment", "").strip()

        error = None

        valid_outcomes = [
            "Completed",
            "Attempted",
            "Not Completed",
        ]

        legacy_outcome_unchanged = (
            entry["outcome"] == "Not Applicable"
            and outcome == "Not Applicable"
        )

        if outcome not in valid_outcomes and not legacy_outcome_unchanged:
            error = "Please select a valid outcome."

        elif (
            outcome == "Attempted"
            and entry["comment_required_attempted"] == 1
            and not comment
        ):
            error = (
                "A comment is required when this task is Attempted."
            )

        elif (
            outcome == "Not Completed"
            and entry["comment_required_not_completed"] == 1
            and not comment
        ):
            error = (
                "A comment is required when this task is Not Completed."
            )

        if error:
            conn.close()

            return render_template(
                "shift_housekeeping_task_edit.html",
                entry=entry,
                shift=shift,
                error=error,
                selected_status=outcome,
                comment=comment
            )

        conn.execute("""
            UPDATE shift_housekeeping_task_entries
            SET outcome = ?,
                comment = ?
            WHERE entry_id = ?
              AND shift_id = ?
        """, (
            outcome,
            comment,
            entry_id,
            shift_id
        ))

        log_activity(
            conn,
            activity_class="HOUSEKEEPING",
            activity_type="housekeeping_task_updated",
            summary=(
                f"{entry['task_name']} updated to "
                f"'{outcome}'"
            ),
            user_id=session["user_id"],
            client_id=shift["client_id"],
            shift_id=shift_id,
            related_table="shift_housekeeping_task_entries",
            related_id=entry_id,
            details=comment,
            success=1
        )

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "shift_dashboard",
                shift_id=shift_id
            )
        )

    conn.close()

    return render_template(
        "shift_housekeeping_task_edit.html",
        entry=entry,
        shift=shift,
        error=None,
        selected_status=entry["outcome"],
        comment=entry["comment"]
    )



#####################################################################
# CHECKLIST TEMPLATES
#####################################################################

@app.route("/checklist-templates")
def checklist_templates():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    query = """
        SELECT *
        FROM checklist_templates
    """

    parameters = ()

    if active_filter is not None:
        query += """
            WHERE active = ?
        """
        parameters = (active_filter,)

    query += """
        ORDER BY shift_type, template_name
    """

    templates = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "checklist_templates.html",
        templates=templates,
        status_filter=status_filter
    )
    
@app.route("/checklist-template/new", methods=["GET", "POST"])
def checklist_template_new():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    error = None

    if request.method == "POST":
        template_name = request.form["template_name"].strip()
        shift_type = request.form["shift_type"]

        if not template_name or not shift_type:
            error = "Template name and shift type are required."
        else:
            conn = get_db()
            conn.execute("""
                INSERT INTO checklist_templates
                (template_name, shift_type, active)
                VALUES (?, ?, 1)
            """, (template_name, shift_type))
            conn.commit()
            conn.close()

            return redirect(url_for("checklist_templates"))

    return render_template("checklist_template_new.html", error=error)

@app.route("/checklist-template/<int:template_id>")
def checklist_template(template_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter == "active":
        active_filter = 1
    elif status_filter == "inactive":
        active_filter = 0
    else:
        status_filter = "all"
        active_filter = None

    conn = get_db()

    template = conn.execute("""
        SELECT *
        FROM checklist_templates
        WHERE template_id = ?
    """, (template_id,)).fetchone()

    if template is None:
        conn.close()
        return "Template not found", 404

    query = """
        SELECT *
        FROM checklist_template_items
        WHERE template_id = ?
    """

    parameters = [template_id]

    if active_filter is not None:
        query += """
          AND active = ?
        """
        parameters.append(active_filter)

    query += """
        ORDER BY sort_order
    """

    items = conn.execute(
        query,
        parameters
    ).fetchall()

    conn.close()

    return render_template(
        "checklist_template.html",
        template=template,
        items=items,
        status_filter=status_filter
    )

@app.route("/checklist-template/<int:template_id>/item/new", methods=["GET", "POST"])
def checklist_item_new(template_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()

    template = conn.execute("""
        SELECT *
        FROM checklist_templates
        WHERE template_id = ?
    """, (template_id,)).fetchone()

    if template is None:
        conn.close()
        return "Template not found", 404

    if request.method == "POST":
        item_text = request.form["item_text"].strip()
        timing_type = request.form["timing_type"]
        due_time = request.form.get("due_time", "").strip()
        required = 1 if "required" in request.form else 0

        max_order = conn.execute("""
            SELECT COALESCE(MAX(sort_order), 0) AS max_order
            FROM checklist_template_items
            WHERE template_id = ?
        """, (template_id,)).fetchone()["max_order"]

        conn.execute("""
            INSERT INTO checklist_template_items
            (template_id, item_text, required, sort_order, active, timing_type, due_time)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        """, (
            template_id,
            item_text,
            required,
            max_order + 10,
            timing_type,
            due_time if due_time else None
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("checklist_template", template_id=template_id))

    conn.close()

    return render_template("checklist_item_new.html", template=template)

#####################################################################
# ACTIVITY LOG
#####################################################################

@app.route("/activity-log")
def activity_log():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()

    logs = conn.execute("""
        SELECT
            al.*,
            u.full_name,
            c.client_name
        FROM activity_log al
        LEFT JOIN users u ON al.user_id = u.user_id
        LEFT JOIN clients c ON al.client_id = c.client_id
        ORDER BY al.activity_datetime DESC
        LIMIT 200
    """).fetchall()

    conn.close()

    return render_template("activity_log.html", logs=logs)

@app.route("/activity-log/<int:activity_id>")
def activity_log_detail(activity_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()

    activity = conn.execute("""
        SELECT
            al.*,
            u.full_name,
            c.client_name,
            s.shift_type,
            s.shift_date  
                            
        FROM activity_log al
        LEFT JOIN users u
            ON al.user_id = u.user_id
        LEFT JOIN clients c
            ON al.client_id = c.client_id
        LEFT JOIN shifts s
            ON al.shift_id = s.shift_id
        WHERE al.activity_id = ?
    """, (activity_id,)).fetchone()

    conn.close()

    if activity is None:
        return "Activity not found", 404

    return render_template(
        "activity_log_detail.html",
        log=activity
    )

#####################################################################
# APPLICATION STARTUP
#####################################################################

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
