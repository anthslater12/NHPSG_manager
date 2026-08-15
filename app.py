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
    url_for,
    flash
)
from collections.abc import Mapping
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, time as datetime_time, timedelta, timezone
from zoneinfo import ZoneInfo
import os
import time
import re
import secrets
import add_leave_requests_table
import add_sleep_events_note

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


@app.context_processor
def inject_management_storyline_navigation():
    management_roles = {"Admin", "Program Manager", "Director"}
    if session.get("role") not in management_roles:
        return {"management_storyline_url": None}
    conn = None
    try:
        conn = get_db()
        active_clients = conn.execute(
            "SELECT client_id FROM clients WHERE active = 1 ORDER BY client_id"
        ).fetchall()
        if len(active_clients) != 1:
            return {"management_storyline_url": None}
        return {
            "management_storyline_url": url_for(
                "client_storyline",
                client_id=active_clients[0]["client_id"]
            )
        }
    except (sqlite3.Error, RuntimeError):
        return {"management_storyline_url": None}
    finally:
        if conn is not None:
            conn.close()

DB_NAME = os.environ.get("NHPSG_DB_PATH", "nhpsg.db")

VANCOUVER_TIMEZONE = ZoneInfo("America/Vancouver")

BEHAVIOUR_CATEGORY_FIELDS = (
    "aggression_towards_others",
    "injury_to_others",
    "self_harm",
    "injury_to_self",
    "property_damage",
)

BEHAVIOUR_VOID_AUTHORITY_ROLES = frozenset((
    "Admin",
    "Program Manager",
    "Director",
))
FOOD_FLUID_MANAGEMENT_ROLES = BEHAVIOUR_VOID_AUTHORITY_ROLES
BEHAVIOUR_CATEGORY_LABELS = {
    "aggression_towards_others": "Aggression towards others",
    "injury_to_others": "Injury to others",
    "self_harm": "Self-Harm",
    "injury_to_self": "Injury to Self",
    "property_damage": "Property Damage",
}
BEHAVIOUR_NOTES_MAX_LENGTH = 2000
BEHAVIOUR_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

ABC_ANTECEDENT_FIELDS = (
    "antecedent_transition_activities", "antecedent_denied_access",
    "antecedent_delayed_access", "antecedent_given_instruction",
    "antecedent_end_activity", "antecedent_preferred_activity_alone",
    "antecedent_transition_locations", "antecedent_other",
)
ABC_BEHAVIOUR_FIELDS = (
    "behaviour_crying_yelling_screaming", "behaviour_resisting_prompt",
    "behaviour_grabbing_object", "behaviour_throwing_objects",
    "behaviour_physical_aggression", "behaviour_verbal_aggression",
    "behaviour_other",
)
ABC_RESPONSE_FIELDS = (
    "response_ignored_walked_away", "response_followed_instruction",
    "response_adult_attention", "response_removed_preferred_activity",
    "response_gave_preferred_activity", "response_blocked_behaviour",
    "response_redirected_activity", "response_other",
)
ABC_FIELD_LABELS = {
    "antecedent_transition_activities": "Asked to transition between activities",
    "antecedent_denied_access": "Denied access to item/activity",
    "antecedent_delayed_access": "Delayed access to item/activity",
    "antecedent_given_instruction": "Given instruction to do something",
    "antecedent_end_activity": "Given instruction to end activity",
    "antecedent_preferred_activity_alone": "Engaged in preferred activity (alone)",
    "antecedent_transition_locations": "Asked to transition between locations",
    "antecedent_other": "Other",
    "behaviour_crying_yelling_screaming": "Crying / yelling / screaming",
    "behaviour_resisting_prompt": "Resisting prompt",
    "behaviour_grabbing_object": "Grabbing object from another person",
    "behaviour_throwing_objects": "Throwing objects",
    "behaviour_physical_aggression": "Physical aggression",
    "behaviour_verbal_aggression": "Verbal aggression",
    "behaviour_other": "Other",
    "response_ignored_walked_away": "Ignored behaviour / walked away",
    "response_followed_instruction": "Followed through with demand or instruction",
    "response_adult_attention": "Adult gave attention",
    "response_removed_preferred_activity": "Removed preferred activity",
    "response_gave_preferred_activity": "Gave preferred activity",
    "response_blocked_behaviour": "Blocked behaviour",
    "response_redirected_activity": "Redirected to new activity",
    "response_other": "Other",
}

FOOD_FLUID_INTERACTION_TYPES = ("Offered", "Requested")
FOOD_FLUID_OUTCOMES = (
    "All consumed",
    "Partially consumed",
    "Refused",
    "Item not available",
)
FOOD_FLUID_THROWN_OUTCOMES = (
    "Partially consumed",
    "Refused",
)
FOOD_FLUID_ASCII_WHITESPACE = " \t\n\r\v\f"
SHIFT_ACTIVITY_CATEGORY_FIELDS = (
    "a_selected",
    "t_selected",
    "ls_selected",
)
SHIFT_ACTIVITY_ASCII_WHITESPACE = " \t\n\r\v\f"
SCHEDULE_SHIFT_TYPES = ("Day", "Afternoon", "Overnight")
SCHEDULE_VIEW_ROLES = {"Admin", "Director", "Program Manager", "Support Worker"}
SCHEDULE_MANAGEMENT_ROLES = {"Admin", "Director", "Program Manager"}

LEAVE_TYPES = (
    "Vacation",
    "Personal Illness",
    "Family Responsibility",
    "Bereavement",
    "Medical Appointment",
    "Leave Without Pay",
    "Other",
)
LEAVE_STATUSES = ("PENDING", "APPROVED", "DECLINED", "CANCELLED")
LEAVE_DAY_PARTS = ("FULL_DAY", "PARTIAL_DAY")
LEAVE_COMMENT_MAX_LENGTH = 2000
LEAVE_OTHER_REASON_MAX_LENGTH = 500

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
        add_leave_requests_table.migrate(conn)
        add_sleep_events_note.migrate(conn)
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


#####################################################################
# BEHAVIOUR MODULE V1: SCHEMA-LEVEL BUSINESS RULE HELPERS
#####################################################################

def behaviour_utc_to_vancouver(stored_utc):
    """Return a stored UTC instant as an aware Vancouver datetime."""
    if isinstance(stored_utc, str):
        stored_utc = parse_behaviour_utc(stored_utc)
    elif isinstance(stored_utc, datetime):
        stored_utc = parse_behaviour_utc(
            serialize_behaviour_utc(stored_utc)
        )
    else:
        raise ValueError("Stored UTC instant is required.")

    return stored_utc.astimezone(VANCOUVER_TIMEZONE)


def serialize_behaviour_utc(value):
    """Serialize an aware instant in Behaviour V1's canonical UTC format."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("A timezone-aware datetime is required.")
    if value.microsecond != 0:
        raise ValueError("Behaviour UTC timestamps cannot include fractions.")

    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_behaviour_utc(value):
    """Parse one canonical Behaviour V1 UTC timestamp."""
    if not isinstance(value, str) or len(value) != 20:
        raise ValueError("Behaviour UTC timestamp must use YYYY-MM-DDTHH:MM:SSZ.")

    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(
            "Behaviour UTC timestamp must use YYYY-MM-DDTHH:MM:SSZ."
        ) from error

    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ValueError("Behaviour UTC timestamp must be canonical.")

    return parsed.replace(tzinfo=timezone.utc)


def get_behaviour_operational_day(vancouver_datetime):
    """Return the named operational day for an aware Vancouver instant."""
    local_datetime = _require_vancouver_datetime(vancouver_datetime)

    if local_datetime.timetz().replace(tzinfo=None) >= datetime_time(23, 0):
        return local_datetime.date() + timedelta(days=1)

    return local_datetime.date()


def get_behaviour_operational_band(vancouver_datetime):
    """Return Night, Day, or Evening for an aware Vancouver instant."""
    local_datetime = _require_vancouver_datetime(vancouver_datetime)
    local_time = local_datetime.timetz().replace(tzinfo=None)

    if local_time >= datetime_time(23, 0) or local_time < datetime_time(7, 30):
        return "Night"
    if local_time < datetime_time(15, 30):
        return "Day"
    return "Evening"


def get_behaviour_operational_week_start(vancouver_datetime):
    """Return the Monday date for the instant's named operational week."""
    operational_day = get_behaviour_operational_day(vancouver_datetime)
    return operational_day - timedelta(days=operational_day.weekday())


def get_schedule_operational_week_start(vancouver_datetime):
    """Return the current Schedule week using the Vancouver-local convention."""
    return get_behaviour_operational_week_start(vancouver_datetime)


def _parse_schedule_monday(value):
    try:
        monday = date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Schedule week must be a valid ISO Monday.") from error
    if monday.weekday() != 0:
        raise ValueError("Schedule week must start on a Monday.")
    return monday


def _schedule_week_rows(conn, week_start, client_id):
    """Return the parent schedule rows for one client and Monday-based week.

    This is intentionally a status-neutral query.  Phase 1 centralizes the
    weekly publication calculation without changing what any existing route
    currently displays.
    """
    end_date = week_start + timedelta(days=6)
    return conn.execute("""
        SELECT schedule_shift_id, client_id, shift_date, shift_type, status
        FROM schedule_shifts
        WHERE client_id = ?
          AND shift_date BETWEEN ? AND ?
        ORDER BY shift_date, schedule_shift_id
    """, (
        client_id, week_start.isoformat(), end_date.isoformat(),
    )).fetchall()


def _schedule_week_publication_state(conn, week_start, client_id):
    """Describe the publication state of one client/week.

    Publication is a week-level concept, while the existing status column is
    stored on each schedule shift.  A week is considered fully published only
    when it contains at least one row and every row is Published.  This helper
    does not enforce that policy or alter existing route behavior yet.
    """
    rows = _schedule_week_rows(conn, week_start, client_id)
    status_counts = {
        status: sum(1 for row in rows if row["status"] == status)
        for status in ("Draft", "Published", "Closed", "Cancelled")
    }
    statuses = {row["status"] for row in rows}

    if not rows:
        state = "Empty"
    elif statuses == {"Published"}:
        state = "Published"
    elif statuses == {"Draft"}:
        state = "Draft"
    elif statuses <= {"Published", "Draft"}:
        state = "Mixed" if "Published" in statuses else "Draft"
    else:
        state = "Mixed"

    return {
        "state": state,
        "row_count": len(rows),
        "status_counts": status_counts,
        "is_empty": not rows,
        "is_fully_published": bool(rows) and statuses == {"Published"},
        "has_draft_rows": "Draft" in statuses,
        "has_published_rows": "Published" in statuses,
        "has_closed_rows": "Closed" in statuses,
        "has_cancelled_rows": "Cancelled" in statuses,
    }


def _schedule_week_visible_to_support(publication_state):
    """Return whether a weekly state is safe for Support Worker visibility.

    The result is not wired into routes in Phase 1.  Keeping this decision in
    one helper prevents future views and exports from implementing different
    interpretations of weekly publication state.
    """
    return bool(publication_state.get("is_fully_published"))


def _schedule_week_return_to_draft(
    conn, client_id, week_start, actor_user_id, previous_state,
    triggering_event,
):
    """Return published rows in a client/week to Draft within the caller's transaction."""
    if previous_state not in {"Published", "Mixed"}:
        return 0
    end_date = week_start + timedelta(days=6)
    published_rows = conn.execute("""
        SELECT schedule_shift_id
        FROM schedule_shifts
        WHERE client_id = ?
          AND shift_date BETWEEN ? AND ?
          AND status = 'Published'
        ORDER BY shift_date, schedule_shift_id
    """, (
        client_id, week_start.isoformat(), end_date.isoformat(),
    )).fetchall()
    if not published_rows:
        return 0

    now_utc = serialize_behaviour_utc(
        datetime.now(timezone.utc).replace(microsecond=0)
    )
    updated = conn.execute("""
        UPDATE schedule_shifts
        SET status = 'Draft', updated_by = ?, updated_at_utc = ?
        WHERE client_id = ?
          AND shift_date BETWEEN ? AND ?
          AND status = 'Published'
    """, (
        actor_user_id, now_utc, client_id,
        week_start.isoformat(), end_date.isoformat(),
    ))
    if updated.rowcount < 1:
        return 0

    resulting_state = _schedule_week_publication_state(
        conn, week_start, client_id
    )
    log_activity(
        conn, "SCHEDULE", "schedule_week_returned_to_draft",
        "Published schedule week returned to Draft",
        user_id=actor_user_id, client_id=client_id,
        related_table="schedule_shifts",
        related_id=published_rows[0]["schedule_shift_id"],
        details=(
            f"Client ID: {client_id}\n"
            f"Week start: {week_start.isoformat()}\n"
            f"Week end: {end_date.isoformat()}\n"
            f"Triggering event: {triggering_event}\n"
            f"Published rows returned to Draft: {updated.rowcount}\n"
            f"Previous state: {previous_state}\n"
            f"Resulting state: {resulting_state['state']}\n"
            f"Actor user ID: {actor_user_id}"
        ),
        storyline_visible=False,
    )
    return updated.rowcount


def _format_schedule_time(value):
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.strftime("%I:%M %p").lstrip("0")


def _schedule_week_context(conn, monday, client_id):
    dates = [monday + timedelta(days=offset) for offset in range(7)]
    end_date = dates[-1]
    rows = conn.execute("""
        SELECT ss.schedule_shift_id, ss.client_id, c.client_name,
               ss.shift_date, ss.shift_type,
               ss.planned_start_time, ss.planned_end_time, ss.status,
               ss.notes, u.user_id, u.full_name,
               st.planned_start_time AS worker_planned_start_time,
               st.planned_end_time AS worker_planned_end_time
        FROM schedule_shifts AS ss
        JOIN clients AS c ON c.client_id = ss.client_id
        LEFT JOIN schedule_staff AS st
          ON st.schedule_shift_id = ss.schedule_shift_id
        LEFT JOIN users AS u ON u.user_id = st.user_id
        WHERE ss.client_id = ?
          AND ss.shift_date BETWEEN ? AND ?
        ORDER BY ss.shift_date,
                 CASE ss.shift_type
                     WHEN 'Day' THEN 1
                     WHEN 'Afternoon' THEN 2
                     WHEN 'Overnight' THEN 3
                 END,
                 u.full_name, u.user_id
    """, (client_id, monday.isoformat(), end_date.isoformat())).fetchall()

    by_entry = {}
    for row in rows:
        key = (row["shift_date"], row["shift_type"], row["client_id"])
        slot = by_entry.setdefault(key, {
            "schedule_shift_id": row["schedule_shift_id"],
            "client_id": row["client_id"],
            "client_name": row["client_name"],
            "shift_type": row["shift_type"],
            "start_display": _format_schedule_time(row["planned_start_time"]),
            "end_display": _format_schedule_time(row["planned_end_time"]),
            "overnight_next_day": (
                row["shift_type"] == "Overnight"
                and row["planned_end_time"] <= row["planned_start_time"]
            ),
            "status": row["status"],
            "notes": row["notes"],
            "workers": [],
            "exists": True,
        })
        if row["user_id"] is not None:
            # Phase 6A columns remain nullable during staged deployment. Keep
            # this display-only fallback narrow; create/edit validation remains
            # authoritative and is not bypassed here.
            worker_start = row["worker_planned_start_time"] or row["planned_start_time"]
            worker_end = row["worker_planned_end_time"] or row["planned_end_time"]
            slot["workers"].append({
                "user_id": row["user_id"],
                "full_name": row["full_name"],
                "planned_start_time": worker_start,
                "planned_end_time": worker_end,
                "planned_start_display": _format_schedule_time(worker_start),
                "planned_end_display": _format_schedule_time(worker_end),
                "crosses_midnight": (
                    row["shift_type"] == "Overnight"
                    and worker_end < worker_start
                ),
            })

    for slot in by_entry.values():
        slot["workers"].sort(key=lambda worker: (
            worker["planned_start_time"],
            worker["planned_end_time"],
            worker["full_name"] or "",
            worker["user_id"],
        ))

    return [{
        "date": day,
        "date_iso": day.isoformat(),
        "label": day.strftime("%A"),
        "shifts": [
            {
                "shift_type": shift_type,
                "exists": bool([
                    entry for key, entry in by_entry.items()
                    if key[0] == day.isoformat() and key[1] == shift_type
                ]),
                "entries": [
                    entry for key, entry in by_entry.items()
                    if key[0] == day.isoformat() and key[1] == shift_type
                ],
            }
            for shift_type in SCHEDULE_SHIFT_TYPES
        ],
    } for day in dates]


def _schedule_planned_duration_minutes(shift_type, start_value, end_value):
    """Return a valid planned assignment duration, or None for bad legacy data."""
    try:
        start = datetime.strptime(start_value, "%H:%M")
        end = datetime.strptime(end_value, "%H:%M")
    except (TypeError, ValueError):
        return None

    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    if shift_type in ("Day", "Afternoon"):
        duration = end_minutes - start_minutes
    elif shift_type == "Overnight":
        if end_minutes == start_minutes:
            return None
        duration = (
            end_minutes - start_minutes
            if end_minutes > start_minutes
            else 24 * 60 - start_minutes + end_minutes
        )
    else:
        return None
    return duration if duration > 0 else None


def _schedule_week_staff_summary(conn, week_start, client_id):
    """Calculate management-only planned totals for one client and week."""
    end_date = week_start + timedelta(days=6)
    rows = conn.execute("""
        SELECT st.user_id, u.full_name, ss.shift_type,
               COALESCE(NULLIF(TRIM(st.planned_start_time), ''),
                        ss.planned_start_time) AS planned_start_time,
               COALESCE(NULLIF(TRIM(st.planned_end_time), ''),
                        ss.planned_end_time) AS planned_end_time
        FROM schedule_staff AS st
        JOIN schedule_shifts AS ss
          ON ss.schedule_shift_id = st.schedule_shift_id
        JOIN users AS u ON u.user_id = st.user_id
        WHERE ss.client_id = ?
          AND ss.shift_date BETWEEN ? AND ?
        ORDER BY u.full_name, st.user_id, st.schedule_staff_id
    """, (client_id, week_start.isoformat(), end_date.isoformat())).fetchall()

    totals = {}
    for row in rows:
        duration = _schedule_planned_duration_minutes(
            row["shift_type"],
            row["planned_start_time"],
            row["planned_end_time"],
        )
        if duration is None:
            app.logger.warning(
                "Skipping invalid planned schedule duration for schedule_staff "
                "assignment user_id=%s",
                row["user_id"],
            )
            continue
        total = totals.setdefault(row["user_id"], {
            "user_id": row["user_id"],
            "full_name": row["full_name"],
            "scheduled_minutes": 0,
            "shift_count": 0,
        })
        total["scheduled_minutes"] += duration
        total["shift_count"] += 1

    summary = []
    for total in totals.values():
        total = dict(total)
        hours_display = f"{total['scheduled_minutes'] / 60:.2f}".rstrip("0").rstrip(".")
        total["scheduled_hours_display"] = (
            hours_display if "." in hours_display else f"{hours_display}.0"
        )
        summary.append(total)
    return sorted(summary, key=lambda row: (
        (row["full_name"] or "").casefold(),
        row["user_id"],
    ))


def _schedule_effective_staff_order(conn, client_id, workers):
    """Order a supplied worker set using saved per-client preferences."""
    saved_order = {
        row["user_id"]: row["display_order"]
        for row in conn.execute("""
            SELECT user_id, display_order
            FROM schedule_staff_order
            WHERE client_id = ?
        """, (client_id,)).fetchall()
    }
    ordered_workers = []
    for worker in workers:
        worker = dict(worker)
        worker["display_order"] = saved_order.get(worker["user_id"])
        ordered_workers.append(worker)
    return sorted(ordered_workers, key=lambda worker: (
        worker["display_order"] is None,
        worker["display_order"] if worker["display_order"] is not None else 0,
        (worker.get("full_name") or "").casefold(),
        worker["user_id"],
    ))


def _schedule_staff_view_context(conn, week_start, client_id):
    """Return the read-only Staff View matrix for one client and week."""
    dates = [week_start + timedelta(days=offset) for offset in range(7)]
    end_date = dates[-1]
    workers = {}

    for row in conn.execute("""
        SELECT user_id, full_name, active
        FROM users
        WHERE active = 1 AND role = 'Support Worker'
    """).fetchall():
        workers[row["user_id"]] = {
            "user_id": row["user_id"],
            "full_name": row["full_name"],
            "active": bool(row["active"]),
            "assignments": [[] for _ in dates],
        }

    rows = conn.execute("""
        SELECT st.schedule_staff_id, st.user_id, u.full_name, u.active,
               ss.shift_date, ss.shift_type, ss.status,
               COALESCE(NULLIF(TRIM(st.planned_start_time), ''),
                        ss.planned_start_time) AS planned_start_time,
               COALESCE(NULLIF(TRIM(st.planned_end_time), ''),
                        ss.planned_end_time) AS planned_end_time
        FROM schedule_staff AS st
        JOIN schedule_shifts AS ss
          ON ss.schedule_shift_id = st.schedule_shift_id
        JOIN users AS u ON u.user_id = st.user_id
        WHERE ss.client_id = ?
          AND ss.shift_date BETWEEN ? AND ?
    """, (client_id, week_start.isoformat(), end_date.isoformat())).fetchall()

    date_indexes = {day.isoformat(): index for index, day in enumerate(dates)}
    for row in rows:
        start_minutes = _schedule_matrix_time_minutes(row["planned_start_time"])
        end_minutes = _schedule_matrix_time_minutes(row["planned_end_time"])
        day_index = date_indexes.get(row["shift_date"])
        if start_minutes is None or end_minutes is None or day_index is None:
            app.logger.warning(
                "Skipping invalid Staff View assignment schedule_staff_id=%s",
                row["schedule_staff_id"],
            )
            continue
        worker = workers.setdefault(row["user_id"], {
            "user_id": row["user_id"],
            "full_name": row["full_name"],
            "active": bool(row["active"]),
            "assignments": [[] for _ in dates],
        })
        worker["assignments"][day_index].append({
            "start_display": _format_schedule_time(row["planned_start_time"]),
            "end_display": _format_schedule_time(row["planned_end_time"]),
            "start_minutes": start_minutes,
            "end_minutes": end_minutes,
            "shift_type": row["shift_type"],
            "schedule_staff_id": row["schedule_staff_id"],
            "status": row["status"],
            "overnight": (
                row["shift_type"] == "Overnight"
                and end_minutes < start_minutes
            ),
        })

    matrix_workers = []
    for worker in workers.values():
        day_cells = []
        for assignments in worker.pop("assignments"):
            day_cells.append(sorted(assignments, key=lambda assignment: (
                assignment["start_minutes"],
                assignment["end_minutes"],
                assignment["shift_type"],
                assignment["schedule_staff_id"],
            )))
        worker["days"] = day_cells
        matrix_workers.append(worker)

    ordered_workers = _schedule_effective_staff_order(
        conn, client_id, matrix_workers
    )
    return {
        "dates": [{
            "weekday": day.strftime("%A"),
            "date_display": f"{day.strftime('%b')} {day.day}",
            "date_iso": day.isoformat(),
        } for day in dates],
        "workers": ordered_workers,
        "order_signature": "|".join(
            str(worker["user_id"]) for worker in ordered_workers
        ),
    }


def _schedule_matrix_time_minutes(value):
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError):
        return None
    return parsed.hour * 60 + parsed.minute


def _schedule_staff_matrix_context(conn, week_start, client_id):
    """Return worker rows and day cells for the staff-oriented Schedule PDF."""
    dates = [week_start + timedelta(days=offset) for offset in range(7)]
    end_date = dates[-1]
    workers = {}

    for row in conn.execute("""
        SELECT user_id, full_name
        FROM users
        WHERE active = 1 AND role = 'Support Worker'
        ORDER BY full_name, user_id
    """).fetchall():
        workers[row["user_id"]] = {
            "user_id": row["user_id"],
            "full_name": row["full_name"],
            "assignments": [[] for _ in dates],
        }

    rows = conn.execute("""
        SELECT st.schedule_staff_id, st.user_id, u.full_name,
               ss.shift_date, ss.shift_type,
               COALESCE(NULLIF(TRIM(st.planned_start_time), ''),
                        ss.planned_start_time) AS planned_start_time,
               COALESCE(NULLIF(TRIM(st.planned_end_time), ''),
                        ss.planned_end_time) AS planned_end_time
        FROM schedule_staff AS st
        JOIN schedule_shifts AS ss
          ON ss.schedule_shift_id = st.schedule_shift_id
        JOIN users AS u ON u.user_id = st.user_id
        WHERE ss.client_id = ?
          AND ss.shift_date BETWEEN ? AND ?
        ORDER BY u.full_name, st.user_id, ss.shift_date,
                 st.planned_start_time, st.schedule_staff_id
    """, (client_id, week_start.isoformat(), end_date.isoformat())).fetchall()

    date_indexes = {day.isoformat(): index for index, day in enumerate(dates)}
    for row in rows:
        start_minutes = _schedule_matrix_time_minutes(row["planned_start_time"])
        end_minutes = _schedule_matrix_time_minutes(row["planned_end_time"])
        day_index = date_indexes.get(row["shift_date"])
        if start_minutes is None or end_minutes is None or day_index is None:
            app.logger.warning(
                "Skipping invalid staff matrix assignment schedule_staff_id=%s",
                row["schedule_staff_id"],
            )
            continue
        worker = workers.setdefault(row["user_id"], {
            "user_id": row["user_id"],
            "full_name": row["full_name"],
            "assignments": [[] for _ in dates],
        })
        worker["assignments"][day_index].append({
            "start_display": _format_schedule_time(row["planned_start_time"]),
            "end_display": _format_schedule_time(row["planned_end_time"]),
            "start_minutes": start_minutes,
            "end_minutes": end_minutes,
            "shift_type": row["shift_type"],
            "schedule_staff_id": row["schedule_staff_id"],
            "overnight": (
                row["shift_type"] == "Overnight"
                and end_minutes < start_minutes
            ),
        })

    matrix_workers = []
    for worker in workers.values():
        day_cells = []
        for assignments in worker.pop("assignments"):
            day_cells.append(sorted(assignments, key=lambda assignment: (
                assignment["start_minutes"],
                assignment["end_minutes"],
                assignment["shift_type"],
                assignment["schedule_staff_id"],
            )))
        worker["days"] = day_cells
        matrix_workers.append(worker)

    return {
        "dates": [{
            "weekday": day.strftime("%A"),
            "date_display": f"{day.strftime('%b')} {day.day}",
            "date_iso": day.isoformat(),
        } for day in dates],
        "workers": _schedule_effective_staff_order(
            conn, client_id, matrix_workers
        ),
    }


def _schedule_shift_is_editable(shift_date, status, today=None):
    today = today or datetime.now(VANCOUVER_TIMEZONE).date()
    return shift_date >= today and status in ("Draft", "Published")


def _schedule_active_workers(conn):
    return conn.execute("""
        SELECT user_id, full_name
        FROM users
        WHERE active = 1 AND role = 'Support Worker'
        ORDER BY full_name, user_id
    """).fetchall()


def _schedule_worker_time_values(form, worker_id):
    return (
        (form.get(f"worker_planned_start_time_{worker_id}") or "").strip(),
        (form.get(f"worker_planned_end_time_{worker_id}") or "").strip(),
    )


def _validate_schedule_worker_hours(worker_name, shift_type, start_value, end_value):
    if not start_value or not end_value:
        return f"Planned start and end times are required for {worker_name}."
    try:
        start = datetime.strptime(start_value, "%H:%M").time()
        end = datetime.strptime(end_value, "%H:%M").time()
    except ValueError:
        return f"Planned hours for {worker_name} must use HH:MM."
    if shift_type in ("Day", "Afternoon") and end <= start:
        return (
            f"{shift_type} shift end time for {worker_name} must be later "
            "than the start time."
        )
    if shift_type == "Overnight" and end == start:
        return f"Overnight start and end times for {worker_name} cannot be equal."
    return None


def _schedule_staff_hours_details(
    worker_id, worker_name, previous_start, previous_end, new_start, new_end,
    shift_id, shift_date, shift_type,
):
    return (
        f"Worker user ID: {worker_id}\n"
        f"Worker: {worker_name or 'Unknown'}\n"
        f"Previous planned hours: {previous_start}-{previous_end}\n"
        f"New planned hours: {new_start}-{new_end}\n"
        f"Parent schedule shift ID: {shift_id}\n"
        f"Shift date: {shift_date}\n"
        f"Shift type: {shift_type}"
    )


def _schedule_form_values(conn, form, existing=None, client_id=None):
    errors = []
    if existing is None:
        client = None
        if client_id is None:
            errors.append("A valid client is required.")
        client = conn.execute("""
            SELECT client_id FROM clients WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone() if client_id is not None else None
        if client is None and "A valid client is required." not in errors:
            errors.append("A valid active client is required.")
    else:
        client_id = existing["client_id"]

    shift_date_value = (form.get("shift_date") or "").strip()
    try:
        shift_date = date.fromisoformat(shift_date_value)
    except (TypeError, ValueError):
        shift_date = None
        errors.append("A valid schedule date is required.")

    shift_type = (form.get("shift_type") or "").strip()
    if shift_type not in SCHEDULE_SHIFT_TYPES:
        errors.append("A valid shift type is required.")

    start_value = (form.get("planned_start_time") or "").strip()
    end_value = (form.get("planned_end_time") or "").strip()
    try:
        start_time = datetime.strptime(start_value, "%H:%M").time()
        end_time = datetime.strptime(end_value, "%H:%M").time()
    except (TypeError, ValueError):
        start_time = end_time = None
        errors.append("Start and end times must use HH:MM.")

    status = (form.get("status") or "").strip()
    if status not in ("Draft", "Published", "Closed", "Cancelled"):
        errors.append("A valid status is required.")

    selected_values = form.getlist("worker_ids")
    if len(selected_values) != len(set(selected_values)):
        errors.append("A worker cannot be assigned more than once.")
    selected_ids = set()
    for value in selected_values:
        try:
            selected_ids.add(int(value))
        except (TypeError, ValueError):
            errors.append("Worker assignments are invalid.")
            break
    workers = {
        row["user_id"]: row for row in _schedule_active_workers(conn)
    }
    if not selected_ids.issubset(workers):
        errors.append("Only active Support Workers may be assigned.")

    existing_assignments = {}
    if existing is not None:
        existing_assignments = {
            row["user_id"]: row for row in conn.execute("""
                SELECT schedule_staff_id, user_id, planned_start_time,
                       planned_end_time
                FROM schedule_staff
                WHERE schedule_shift_id = ?
            """, (existing["schedule_shift_id"],)).fetchall()
        }
    worker_times = {}
    for worker_id in sorted(selected_ids):
        worker_start, worker_end = _schedule_worker_time_values(form, worker_id)
        if not worker_start and not worker_end and worker_id not in existing_assignments:
            worker_start, worker_end = start_value, end_value
        worker_times[worker_id] = {
            "planned_start_time": worker_start,
            "planned_end_time": worker_end,
        }
        if worker_id in workers:
            worker_error = _validate_schedule_worker_hours(
                workers[worker_id]["full_name"], shift_type,
                worker_start, worker_end,
            )
            if worker_error:
                errors.append(worker_error)

    today = datetime.now(VANCOUVER_TIMEZONE).date()
    if shift_date is not None and shift_date < today:
        errors.append("Past schedules cannot be edited.")
    if start_time is not None and end_time is not None:
        if shift_type in ("Day", "Afternoon") and end_time <= start_time:
            errors.append("Day and Afternoon shifts must end after they start.")

    return {
        "client_id": client_id,
        "shift_date": shift_date,
        "shift_date_value": shift_date_value,
        "shift_type": shift_type,
        "planned_start_time": start_value,
        "planned_end_time": end_value,
        "status": status,
        "notes": (form.get("notes") or "").strip(),
        "worker_ids": selected_ids,
        "worker_times": worker_times,
        "workers": workers,
        "errors": errors,
    }


def _schedule_form_context(conn, values, existing=None, client=None):
    existing_assignments = {}
    if existing is not None:
        existing_assignments = {
            row["user_id"]: row for row in conn.execute("""
                SELECT schedule_staff_id, user_id, planned_start_time,
                       planned_end_time
                FROM schedule_staff
                WHERE schedule_shift_id = ?
            """, (existing["schedule_shift_id"],)).fetchall()
        }
    worker_times = values.get("worker_times", {})
    workers = []
    for row in _schedule_active_workers(conn):
        worker_id = row["user_id"]
        submitted = worker_times.get(worker_id)
        stored = existing_assignments.get(worker_id)
        if submitted is not None:
            start_value = submitted["planned_start_time"]
            end_value = submitted["planned_end_time"]
        elif stored is not None:
            start_value = stored["planned_start_time"] or ""
            end_value = stored["planned_end_time"] or ""
        else:
            start_value = values.get("planned_start_time", "")
            end_value = values.get("planned_end_time", "")
        workers.append({
            "user_id": worker_id,
            "full_name": row["full_name"],
            "selected": worker_id in values.get("worker_ids", set()),
            "planned_start_time": start_value,
            "planned_end_time": end_value,
        })
    return {
        "values": values,
        "client": client,
        "workers": workers,
        "editing": existing is not None,
        "error": "; ".join(values.get("errors", [])),
    }


def _generate_schedule_pdf(html):
    """Render trusted Schedule HTML through the optional WeasyPrint dependency."""
    try:
        from weasyprint import HTML
    except ImportError as error:
        raise RuntimeError("WeasyPrint is not installed.") from error
    return HTML(
        string=html,
        base_url=os.path.dirname(os.path.abspath(__file__)),
    ).write_pdf()


def _schedule_pdf_filename(client_name, monday):
    safe_client = re.sub(r"[^A-Za-z0-9_-]+", "_", client_name).strip("._-")
    return f"NHPSG_Schedule_{safe_client or 'Client'}_{monday.isoformat()}.pdf"


def convert_vancouver_occurrence_input_to_utc(
    local_input,
    repeated_hour_choice=None,
    now_utc=None
):
    """Validate a Vancouver wall time and return its UTC instant.

    A repeated fall-back wall time requires ``first`` or ``second``.
    Nonexistent spring-forward times and future instants are rejected.
    """
    local_naive = _parse_vancouver_local_input(local_input)
    candidates = _valid_vancouver_utc_candidates(local_naive)

    if not candidates:
        raise ValueError("Occurrence time does not exist in Vancouver time.")

    if len(candidates) == 2:
        if repeated_hour_choice not in ("first", "second"):
            raise ValueError(
                "Repeated Vancouver times require a first or second choice."
            )
        occurrence_utc = candidates[0 if repeated_hour_choice == "first" else 1]
    else:
        occurrence_utc = candidates[0]

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif not isinstance(now_utc, datetime) or now_utc.tzinfo is None:
        raise ValueError("Current UTC instant must include a UTC offset.")
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    if occurrence_utc > now_utc:
        raise ValueError("Occurrence time cannot be in the future.")

    return serialize_behaviour_utc(occurrence_utc)


def validate_behaviour_category_flags(category_flags):
    """Return normalised category flags and require at least one selection."""
    if not isinstance(category_flags, dict):
        raise ValueError("Behaviour category flags are required.")

    if set(category_flags) != set(BEHAVIOUR_CATEGORY_FIELDS):
        raise ValueError("Behaviour category flags are incomplete.")

    normalised = {}
    for field_name in BEHAVIOUR_CATEGORY_FIELDS:
        value = category_flags[field_name]
        if isinstance(value, bool):
            normalised[field_name] = int(value)
        elif type(value) is int and value in (0, 1):
            normalised[field_name] = value
        else:
            raise ValueError("Behaviour category flags must be boolean.")

    if not any(normalised.values()):
        raise ValueError("At least one behaviour category is required.")

    return normalised


def validate_abc_section(form, fields, other_field, details_field, section_name):
    selected = {}
    for field in fields:
        values = form.getlist(field)
        if len(values) > 1 or (values and values[0] != "1"):
            raise ValueError("Behaviour form input is invalid.")
        selected[field] = int(bool(values))
    if not any(selected.values()):
        raise ValueError(f"Select at least one option for {section_name}.")
    details = form.get(details_field, "").strip()
    if selected[other_field] and not details:
        raise ValueError(f"Other details are required for {section_name}.")
    if not selected[other_field] and details:
        raise ValueError(f"Other details are only allowed when Other is selected for {section_name}.")
    return selected, details or None


def validate_abc_submission(form):
    if form.get("record_format") != "ABC":
        raise ValueError("Behaviour form input is invalid.")
    antecedent, antecedent_other = validate_abc_section(
        form, ABC_ANTECEDENT_FIELDS, "antecedent_other",
        "antecedent_other_details", "Before the Behaviour (A)"
    )
    observed, observed_other = validate_abc_section(
        form, ABC_BEHAVIOUR_FIELDS, "behaviour_other",
        "behaviour_other_details", "Behaviour Observed (B)"
    )
    response, response_other = validate_abc_section(
        form, ABC_RESPONSE_FIELDS, "response_other",
        "response_other_details", "Staff Response (C)"
    )
    duration_value = form.get("duration_until_calm_minutes", "").strip()
    if not duration_value:
        raise ValueError("Duration until calm is required.")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)", duration_value):
        raise ValueError("Duration until calm must be a whole number of zero or greater.")
    return {
        **antecedent, **observed, **response,
        "antecedent_other_details": antecedent_other,
        "behaviour_other_details": observed_other,
        "response_other_details": response_other,
        "duration_until_calm_minutes": int(duration_value),
        "calming_description": form.get("calming_description", "").strip() or None,
        "additional_notes": form.get("additional_notes", "").strip() or None,
    }


def get_active_authenticated_user(conn, user_id):
    """Return the current active database user or reject the request."""
    user = conn.execute("""
        SELECT user_id, role, active
        FROM users
        WHERE user_id = ?
          AND active = 1
    """, (user_id,)).fetchone()

    if user is None:
        raise PermissionError("An active authenticated user is required.")

    return user


def validate_active_behaviour_client(conn, client_id):
    """Return an active client or reject the request."""
    client = conn.execute("""
        SELECT client_id, active
        FROM clients
        WHERE client_id = ?
          AND active = 1
    """, (client_id,)).fetchone()

    if client is None:
        raise ValueError("An active client is required.")

    return client


def validate_behaviour_void_authority(conn, user_id):
    """Return a current active management user allowed to void occurrences."""
    user = get_active_authenticated_user(conn, user_id)

    if user["role"] not in BEHAVIOUR_VOID_AUTHORITY_ROLES:
        raise PermissionError("Current user is not allowed to void behaviour.")

    return user


def _require_vancouver_datetime(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("An aware Vancouver datetime is required.")

    return value.astimezone(VANCOUVER_TIMEZONE)


def _parse_vancouver_local_input(local_input):
    if isinstance(local_input, datetime):
        if local_input.tzinfo is not None:
            raise ValueError("Occurrence input must be a Vancouver local wall time.")
        return local_input

    if not isinstance(local_input, str):
        raise ValueError("Occurrence date and time is required.")

    try:
        parsed = datetime.strptime(local_input, "%Y-%m-%dT%H:%M")
    except ValueError as error:
        raise ValueError("Occurrence date and time is invalid.") from error

    if parsed.strftime("%Y-%m-%dT%H:%M") != local_input:
        raise ValueError("Occurrence date and time is invalid.")

    return parsed


def _valid_vancouver_utc_candidates(local_naive):
    candidates = []
    for fold in (0, 1):
        local_aware = local_naive.replace(
            tzinfo=VANCOUVER_TIMEZONE,
            fold=fold
        )
        candidate_utc = local_aware.astimezone(timezone.utc)
        round_trip = candidate_utc.astimezone(VANCOUVER_TIMEZONE)

        if round_trip.replace(tzinfo=None) != local_naive:
            continue
        if candidate_utc not in candidates:
            candidates.append(candidate_utc)

    return sorted(candidates)


def is_vancouver_occurrence_input_ambiguous(local_input):
    """Return whether a valid Vancouver wall time has two UTC instants."""
    return len(_valid_vancouver_utc_candidates(
        _parse_vancouver_local_input(local_input)
    )) == 2


#####################################################################
# FOOD & FLUID V1: WORKER RECORDING HELPERS
#####################################################################

def strip_food_fluid_ascii_whitespace(value):
    """Trim supported ASCII whitespace from a Food & Fluid form value."""
    if not isinstance(value, str):
        raise ValueError("Food & Fluid form input is invalid.")
    return value.strip(FOOD_FLUID_ASCII_WHITESPACE)


def get_active_food_fluid_shift_context(conn, shift_id, user_id):
    """Return authoritative context for an active Support Worker shift."""
    user = get_active_authenticated_user(conn, user_id)
    if user["role"] != "Support Worker":
        raise PermissionError(
            "Only an active Support Worker may record Food & Fluid."
        )

    context = conn.execute("""
        SELECT
            s.shift_id,
            s.client_id,
            s.shift_date,
            s.shift_type,
            s.status AS shift_status,
            c.client_name,
            c.active AS client_active,
            ss.shift_staff_id,
            ss.active AS participation_active,
            u.user_id AS recorded_by_user_id,
            u.role AS recorded_by_role,
            u.active AS recorded_by_active
        FROM shifts s
        JOIN clients c
            ON c.client_id = s.client_id
        JOIN shift_staff ss
            ON ss.shift_id = s.shift_id
           AND ss.user_id = ?
        JOIN users u
            ON u.user_id = ss.user_id
        WHERE s.shift_id = ?
          AND s.status = 'Open'
          AND c.active = 1
          AND ss.active = 1
          AND u.active = 1
          AND u.role = 'Support Worker'
        ORDER BY ss.shift_staff_id
        LIMIT 1
    """, (user["user_id"], shift_id)).fetchone()

    if context is None:
        raise PermissionError(
            "Active participation in this open shift is required."
        )

    return context


def get_active_sleep_shift_context(conn, shift_id, user_id):
    """Return authoritative context for a worker recording Sleep."""
    user = get_active_authenticated_user(conn, user_id)
    if user["role"] != "Support Worker":
        raise PermissionError("Only an active Support Worker may record Sleep.")

    context = conn.execute("""
        SELECT s.shift_id, s.client_id, s.shift_date, s.shift_type,
               s.status AS shift_status, c.client_name,
               ss.shift_staff_id
        FROM shifts s
        JOIN clients c ON c.client_id = s.client_id AND c.active = 1
        JOIN shift_staff ss ON ss.shift_id = s.shift_id
                           AND ss.user_id = ? AND ss.active = 1
        WHERE s.shift_id = ? AND s.status = 'Open'
        LIMIT 1
    """, (user["user_id"], shift_id)).fetchone()
    if context is None:
        raise PermissionError(
            "Active participation in this open shift is required."
        )
    return context


def get_sleep_events(conn, shift_id):
    events = [dict(event) for event in conn.execute("""
        SELECT se.*, u.full_name
        FROM sleep_events se
        JOIN users u ON u.user_id = se.recorded_by_user_id
        WHERE se.shift_id = ?
        ORDER BY se.event_datetime DESC, se.sleep_event_id DESC
    """, (shift_id,)).fetchall()]
    for event in events:
        event["event_local_display"] = behaviour_utc_to_vancouver(
            event["event_datetime"]
        ).strftime("%Y-%m-%d %I:%M %p")
    return events


STORYLINE_FILTERS = {
    "All": None,
    "Sleep": {"sleep_fell_asleep", "sleep_woke_up"},
    "Food & Fluid": {"food_fluid_entry_created", "food_fluid_entry_voided"},
    "Behaviour": {"behaviour_occurrence_created", "behaviour_occurrence_voided"},
    "Toileting": {"toileting_event_created"},
    "Activity": {"shift_activity_created"},
    "Incident": {"incident_created"},
    "Shift Notes": {"shift_note_updated"},
    "Care": {"care_task_updated"},
    "Housekeeping": {"housekeeping_task_updated"},
    "Shift": {"start_shift_completed", "end_shift_completed"},
}

STORYLINE_LABELS = {
    "sleep_fell_asleep": "Sleep",
    "sleep_woke_up": "Sleep",
    "food_fluid_entry_created": "Food & Fluid",
    "food_fluid_entry_voided": "Food & Fluid",
    "behaviour_occurrence_created": "Behaviour",
    "behaviour_occurrence_voided": "Behaviour",
    "toileting_event_created": "Toileting",
    "shift_activity_created": "Activity",
    "incident_created": "Incident",
    "shift_note_updated": "Shift Note",
    "start_shift_completed": "Shift",
    "end_shift_completed": "Shift",
    "care_task_updated": "Care",
    "housekeeping_task_updated": "Housekeeping",
}


def _storyline_label(activity_type):
    if activity_type in STORYLINE_LABELS:
        return STORYLINE_LABELS[activity_type]
    if activity_type.startswith("care_task_"):
        return "Care"
    if activity_type.startswith("housekeeping_task_"):
        return "Housekeeping"
    return "Client activity"


def _storyline_local_datetime(event_datetime, activity_datetime):
    try:
        if event_datetime:
            return behaviour_utc_to_vancouver(event_datetime)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.strptime(
            activity_datetime,
            "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=VANCOUVER_TIMEZONE)
    except (TypeError, ValueError):
        return None


def _storyline_heading(local_datetime):
    try:
        event_date = local_datetime.date()
    except (AttributeError, TypeError, ValueError):
        return "Date unavailable"
    today = datetime.now(VANCOUVER_TIMEZONE).date()
    if event_date == today:
        return "Today"
    if event_date == today - timedelta(days=1):
        return "Yesterday"
    return f"{event_date.strftime('%A, %B')} {event_date.day}, {event_date.year}"


def _storyline_time(local_datetime):
    try:
        return local_datetime.strftime("%H:%M")
    except (AttributeError, TypeError, ValueError):
        return "Time unavailable"


def format_toileting_storyline_details(
    location, bm_size, bm_consistency,
    behaviour_before, behaviour_during, behaviour_after, behaviour_comments,
    general_comments=None
):
    lines = []
    for label, value in (
        ("Location", location),
        ("Size", bm_size),
        ("Consistency", bm_consistency),
    ):
        if value and str(value).strip():
            lines.append(f"{label}: {str(value).strip()}")
    behaviour = "; ".join(
        f"{label}: {value}"
        for label, value in (
            ("Before", behaviour_before),
            ("During", behaviour_during),
            ("After", behaviour_after),
            ("Comments", behaviour_comments),
        ) if value and str(value).strip()
    )
    if behaviour:
        lines.append(f"Behaviour: {behaviour}")
    if general_comments and str(general_comments).strip():
        lines.append(
            f"Additional notes:\n{str(general_comments).strip()}"
        )
    return lines


def format_incident_storyline_details(
    location, injuries, injury_details, actions_taken,
    description, follow_up_required
):
    lines = [
        f"Location: {location}",
        f"Injury: {'Yes' if injuries else 'No'}",
    ]
    if injury_details and str(injury_details).strip():
        lines.append(f"Injury details: {injury_details}")
    if actions_taken and str(actions_taken).strip():
        lines.append(f"Actions taken: {actions_taken}")
    lines.extend((
        f"Description: {description}",
        f"Follow-up required: {'Yes' if follow_up_required else 'No'}",
    ))
    return "\n".join(lines)


def filter_incident_storyline_details(details):
    """Hide obsolete incident labels without rewriting audit snapshots."""
    if not details:
        return details
    suppressed_labels = (
        "Severity:",
        "Police notified:",
        "Medical treatment:",
    )
    return "\n".join(
        line for line in str(details).splitlines()
        if not line.startswith(suppressed_labels)
    )


def format_toileting_local_datetime_display(value):
    """Format the stored Vancouver-local Toileting datetime for management UI."""
    if not isinstance(value, str) or not value.strip():
        return "Date/time unavailable"
    try:
        return datetime.strptime(
            value.strip(), "%Y-%m-%dT%H:%M"
        ).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "Date/time unavailable"


def format_behaviour_storyline_details(category_text, notes=None):
    details = "Categories:\n" + category_text.replace(", ", "\n")
    if notes and notes.strip():
        details += "\n\nNotes:\n" + notes
    return details


def format_abc_behaviour_storyline_details(values):
    """Build the worker-safe Activity Log narrative for an ABC record."""
    sections = []
    for heading, fields, other_field, details_field in (
        ("Before the Behaviour (A):", ABC_ANTECEDENT_FIELDS,
         "antecedent_other", "antecedent_other_details"),
        ("Behaviour Observed (B):", ABC_BEHAVIOUR_FIELDS,
         "behaviour_other", "behaviour_other_details"),
        ("Staff Response (C):", ABC_RESPONSE_FIELDS,
         "response_other", "response_other_details"),
    ):
        lines = [heading]
        lines.extend(ABC_FIELD_LABELS[field] for field in fields if values.get(field))
        if values.get(other_field) and str(values.get(details_field) or "").strip():
            lines.append("Other: " + str(values[details_field]).strip())
        sections.append("\n".join(lines))
    duration = values["duration_until_calm_minutes"]
    unit = "minute" if duration == 1 else "minutes"
    outcome = ["Outcome:", f"Duration until calm: {duration} {unit}"]
    calming = str(values.get("calming_description") or "").strip()
    notes = str(values.get("additional_notes") or "").strip()
    if calming:
        outcome.extend(("How the client calmed down:", calming))
    if notes:
        outcome.extend(("Additional notes:", notes))
    sections.append("\n".join(outcome))
    return "\n\n".join(sections)


def parse_abc_behaviour_storyline_details(details):
    """Return presentation roles for the known ABC Activity Log format."""
    if not details or not details.startswith("Before the Behaviour (A):\n"):
        return None
    lines = details.splitlines()
    headings = {
        "Before the Behaviour (A):",
        "Behaviour Observed (B):",
        "Staff Response (C):",
        "Outcome:",
    }
    subordinate_labels = {
        "How the client calmed down:",
        "Additional notes:",
    }
    if not all(heading in lines for heading in headings):
        return None
    parsed = []
    nested_text = False
    for line in lines:
        if line in headings:
            parsed.append({"text": line, "role": "section-heading"})
            nested_text = False
        elif line in subordinate_labels:
            parsed.append({"text": line, "role": "outcome-label"})
            nested_text = True
        elif line.startswith("Other:"):
            parsed.append({"text": line, "role": "nested-detail"})
            nested_text = True
        elif line.startswith("Duration until calm:"):
            parsed.append({"text": line, "role": "outcome-item"})
            nested_text = False
        elif line:
            parsed.append({
                "text": line,
                "role": "nested-text" if nested_text else "section-item",
            })
    return parsed


def _storyline_access_allowed(conn, client_id, user_id):
    user = get_active_authenticated_user(conn, user_id)
    if user["role"] in STAFF_NOTICE_MANAGEMENT_ROLES:
        return True
    if user["role"] != "Support Worker":
        return False
    return conn.execute("""
        SELECT 1
        FROM shifts s
        JOIN shift_staff ss ON ss.shift_id = s.shift_id
        WHERE s.client_id = ? AND ss.user_id = ? AND ss.active = 1
        LIMIT 1
    """, (client_id, user_id)).fetchone() is not None


def get_food_fluid_shift_window(shift):
    """Return the half-open Vancouver interval for an authoritative shift."""
    try:
        shift_date = date.fromisoformat(shift["shift_date"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("The shift date is invalid.") from error

    shift_type = shift["shift_type"]
    if shift_type == "Day":
        start_time = datetime_time(7, 0)
        end_date = shift_date
        end_time = datetime_time(15, 0)
    elif shift_type == "Afternoon":
        start_time = datetime_time(15, 0)
        end_date = shift_date
        end_time = datetime_time(23, 0)
    elif shift_type == "Overnight":
        start_time = datetime_time(23, 0)
        end_date = shift_date + timedelta(days=1)
        end_time = datetime_time(7, 0)
    else:
        raise ValueError("The shift type is invalid.")

    start_local = datetime.combine(
        shift_date,
        start_time,
        VANCOUVER_TIMEZONE
    )
    end_local = datetime.combine(
        end_date,
        end_time,
        VANCOUVER_TIMEZONE
    )
    return start_local, end_local


def convert_food_fluid_event_input_to_utc(
    shift,
    local_input,
    repeated_hour_choice=None,
    now_utc=None
):
    """Validate a shift-local Food & Fluid event and return canonical UTC."""
    ambiguous = is_vancouver_occurrence_input_ambiguous(local_input)
    if ambiguous:
        if repeated_hour_choice not in ("first", "second"):
            raise ValueError(
                "Repeated Vancouver times require a first or second choice."
            )
    elif repeated_hour_choice:
        raise ValueError(
            "Repeated-hour choice is allowed only for a repeated time."
        )

    event_at_utc = convert_vancouver_occurrence_input_to_utc(
        local_input,
        repeated_hour_choice,
        now_utc
    )
    event_utc = parse_behaviour_utc(event_at_utc)
    start_local, end_local = get_food_fluid_shift_window(shift)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    if not start_utc <= event_utc < end_utc:
        raise ValueError(
            "Event time must fall within the selected shift."
        )

    return event_at_utc


def get_food_fluid_shift_entries(conn, shift_id, limit=None):
    """Return Food & Fluid entries for one shift in display order."""
    sql = """
        SELECT
            ffe.food_fluid_entry_id,
            ffe.shift_id,
            ffe.client_id,
            ffe.event_at_utc,
            ffe.interaction_type,
            ffe.item_description,
            ffe.outcome,
            ffe.physically_thrown,
            ffe.additional_details,
            ffe.submitted_at_utc,
            ffe.status,
            ffe.voided_at_utc,
            ffe.void_reason,
            recorded_by.full_name AS recorded_by_name,
            voided_by.full_name AS voided_by_name
        FROM food_fluid_entries ffe
        JOIN shifts entry_shift
            ON entry_shift.shift_id = ffe.shift_id
           AND entry_shift.client_id = ffe.client_id
        JOIN clients entry_client
            ON entry_client.client_id = entry_shift.client_id
           AND entry_client.active = 1
        JOIN users recorded_by
            ON recorded_by.user_id = ffe.recorded_by_user_id
        LEFT JOIN users voided_by
            ON voided_by.user_id = ffe.voided_by_user_id
        WHERE ffe.shift_id = ?
        ORDER BY
            ffe.event_at_utc DESC,
            ffe.food_fluid_entry_id DESC
    """
    parameters = [shift_id]
    if limit is not None:
        if type(limit) is not int or limit <= 0:
            raise ValueError("Food & Fluid entry limit is invalid.")
        sql += "\nLIMIT ?"
        parameters.append(limit)

    rows = conn.execute(sql, parameters).fetchall()
    entries = []
    for row in rows:
        entry = dict(row)
        entry["event_local_display"] = behaviour_utc_to_vancouver(
            entry["event_at_utc"]
        ).strftime("%Y-%m-%d %H:%M")
        entry["submitted_local_display"] = behaviour_utc_to_vancouver(
            entry["submitted_at_utc"]
        ).strftime("%Y-%m-%d %H:%M")
        entry["voided_local_display"] = None
        if entry["voided_at_utc"]:
            entry["voided_local_display"] = behaviour_utc_to_vancouver(
                entry["voided_at_utc"]
            ).strftime("%Y-%m-%d %H:%M")
        entries.append(entry)

    return entries


def get_food_fluid_management_actor(conn, user_id):
    actor = get_active_authenticated_user(conn, user_id)
    if actor["role"] not in FOOD_FLUID_MANAGEMENT_ROLES:
        raise PermissionError("Current user is not allowed to review Food & Fluid.")
    return actor


def get_food_fluid_management_entry(conn, entry_id):
    row = conn.execute("""
        SELECT
            ffe.food_fluid_entry_id,
            ffe.shift_id,
            ffe.client_id,
            ffe.recorded_by_user_id,
            ffe.event_at_utc,
            ffe.interaction_type,
            ffe.item_description,
            ffe.outcome,
            ffe.physically_thrown,
            ffe.additional_details,
            ffe.submitted_at_utc,
            ffe.status,
            ffe.voided_at_utc,
            ffe.void_reason,
            c.client_name,
            s.shift_date,
            s.shift_type,
            recorder.full_name AS recorded_by_name,
            voider.full_name AS voided_by_name
        FROM food_fluid_entries AS ffe
        JOIN shifts AS s
          ON s.shift_id = ffe.shift_id
         AND s.client_id = ffe.client_id
        JOIN clients AS c ON c.client_id = ffe.client_id
        JOIN users AS recorder ON recorder.user_id = ffe.recorded_by_user_id
        LEFT JOIN users AS voider ON voider.user_id = ffe.voided_by_user_id
        WHERE ffe.food_fluid_entry_id = ?
    """, (entry_id,)).fetchone()
    if row is None:
        return None

    entry = dict(row)
    entry["event_local_display"] = behaviour_utc_to_vancouver(
        entry["event_at_utc"]
    ).strftime("%Y-%m-%d %H:%M")
    entry["submitted_local_display"] = behaviour_utc_to_vancouver(
        entry["submitted_at_utc"]
    ).strftime("%Y-%m-%d %H:%M")
    entry["voided_local_display"] = (
        behaviour_utc_to_vancouver(entry["voided_at_utc"]).strftime(
            "%Y-%m-%d %H:%M"
        )
        if entry["voided_at_utc"]
        else None
    )
    return entry


def get_food_fluid_management_entries(conn):
    rows = conn.execute("""
        SELECT
            ffe.food_fluid_entry_id,
            ffe.event_at_utc,
            ffe.interaction_type,
            ffe.item_description,
            ffe.outcome,
            ffe.status,
            c.client_name,
            s.shift_date,
            s.shift_type,
            recorder.full_name AS recorded_by_name,
            EXISTS (
                SELECT 1
                FROM activity_log AS al
                WHERE al.activity_class = 'FOOD_FLUID'
                  AND al.activity_type = 'food_fluid_entry_viewed'
                  AND al.related_table = 'food_fluid_entries'
                  AND al.related_id = ffe.food_fluid_entry_id
                  AND al.success = 1
            ) AS has_view,
            EXISTS (
                SELECT 1
                FROM acknowledgements AS ack
                WHERE ack.source_table = 'food_fluid_entries'
                  AND ack.source_id = ffe.food_fluid_entry_id
                  AND ack.acknowledgement_type = 'Review'
                  AND ack.active = 1
            ) AS has_review
        FROM food_fluid_entries AS ffe
        JOIN shifts AS s
          ON s.shift_id = ffe.shift_id
         AND s.client_id = ffe.client_id
        JOIN clients AS c ON c.client_id = ffe.client_id
        JOIN users AS recorder ON recorder.user_id = ffe.recorded_by_user_id
        ORDER BY ffe.event_at_utc DESC, ffe.food_fluid_entry_id DESC
    """).fetchall()

    entries = []
    for row in rows:
        entry = dict(row)
        entry["event_local_display"] = behaviour_utc_to_vancouver(
            entry["event_at_utc"]
        ).strftime("%Y-%m-%d %H:%M")
        if entry["has_review"]:
            entry["management_state"] = "Reviewed"
        elif entry["has_view"]:
            entry["management_state"] = "Viewed – Awaiting Review"
        else:
            entry["management_state"] = "Not Viewed"
        entries.append(entry)
    return entries


def record_food_fluid_view(conn, entry, viewer_user_id):
    existing = conn.execute("""
        SELECT activity_id
        FROM activity_log
        WHERE activity_class = 'FOOD_FLUID'
          AND activity_type = 'food_fluid_entry_viewed'
          AND user_id = ?
          AND related_table = 'food_fluid_entries'
          AND related_id = ?
          AND success = 1
        LIMIT 1
    """, (viewer_user_id, entry["food_fluid_entry_id"])).fetchone()
    if existing is not None:
        return

    log_activity(
        conn,
        activity_class="FOOD_FLUID",
        activity_type="food_fluid_entry_viewed",
        summary="Food & Fluid entry viewed",
        user_id=viewer_user_id,
        client_id=entry["client_id"],
        shift_id=entry["shift_id"],
        related_table="food_fluid_entries",
        related_id=entry["food_fluid_entry_id"],
        success=1
    )


def get_food_fluid_view_history(conn, entry_id):
    return conn.execute("""
        SELECT al.activity_datetime, u.full_name AS viewer_name
        FROM activity_log AS al
        JOIN users AS u ON u.user_id = al.user_id
        WHERE al.activity_class = 'FOOD_FLUID'
          AND al.activity_type = 'food_fluid_entry_viewed'
          AND al.related_table = 'food_fluid_entries'
          AND al.related_id = ?
          AND al.success = 1
        ORDER BY al.activity_datetime ASC, al.activity_id ASC
    """, (entry_id,)).fetchall()


def get_food_fluid_review_history(conn, entry_id):
    return conn.execute("""
        SELECT ack.acknowledged_at, ack.user_id, u.full_name AS reviewer_name
        FROM acknowledgements AS ack
        JOIN users AS u ON u.user_id = ack.user_id
        WHERE ack.source_table = 'food_fluid_entries'
          AND ack.source_id = ?
          AND ack.acknowledgement_type = 'Review'
          AND ack.active = 1
        ORDER BY ack.acknowledged_at ASC, ack.acknowledgement_id ASC
    """, (entry_id,)).fetchall()


def get_behaviour_operational_week_range(monday):
    """Return canonical UTC bounds for a named Monday operational week."""
    if not isinstance(monday, date) or monday.weekday() != 0:
        raise ValueError("Behaviour week must start on a Monday.")
    start_local = datetime.combine(
        monday - timedelta(days=1), datetime_time(23, 0), VANCOUVER_TIMEZONE
    )
    end_local = datetime.combine(
        monday + timedelta(days=6), datetime_time(23, 0), VANCOUVER_TIMEZONE
    )
    return serialize_behaviour_utc(start_local), serialize_behaviour_utc(end_local)


def _parse_behaviour_monday(value):
    try:
        monday = date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Behaviour week must be a valid ISO Monday.") from error
    if monday.weekday() != 0:
        raise ValueError("Behaviour week must start on a Monday.")
    return monday


def _behaviour_categories_for_row(row):
    return [BEHAVIOUR_CATEGORY_LABELS[field] for field in BEHAVIOUR_CATEGORY_FIELDS
            if row[field]]


def _behaviour_week_abc_sections(row):
    if row.get("record_format") != "ABC":
        return []
    sections = []
    for heading, fields, other_field, details_field in (
        ("Before the Behaviour (A)", ABC_ANTECEDENT_FIELDS,
         "antecedent_other", "antecedent_other_details"),
        ("Behaviour Observed (B)", ABC_BEHAVIOUR_FIELDS,
         "behaviour_other", "behaviour_other_details"),
        ("Staff Response (C)", ABC_RESPONSE_FIELDS,
         "response_other", "response_other_details"),
    ):
        items = [ABC_FIELD_LABELS[field] for field in fields if row.get(field)]
        if row.get(other_field) and str(row.get(details_field) or "").strip():
            items.append("Other")
            other_details = str(row[details_field]).strip()
        else:
            other_details = None
        sections.append({"heading": heading, "items": items, "other_details": other_details})
    duration = row.get("duration_until_calm_minutes")
    outcome = [{
        "label": "Duration until calm:",
        "value": f"{duration} {'minute' if duration == 1 else 'minutes'}"
    }]
    for label, field in (("How the client calmed down:", "calming_description"),
                         ("Additional notes:", "additional_notes")):
        value = str(row.get(field) or "").strip()
        if value:
            outcome.append({"label": label, "value": value})
    sections.append({"heading": "Outcome", "items": [], "other_details": None, "outcome": outcome})
    return sections


def _behaviour_week_occurrences(conn, monday):
    start_utc, end_utc = get_behaviour_operational_week_range(monday)
    rows = conn.execute("""
        SELECT bo.*, c.client_name, u.full_name AS recorder_name,
               voided_by.full_name AS voided_by_name
        FROM behaviour_occurrences bo
        JOIN clients c ON c.client_id = bo.client_id
        JOIN users u ON u.user_id = bo.recorded_by_user_id
        LEFT JOIN users voided_by ON voided_by.user_id = bo.voided_by_user_id
        WHERE bo.occurred_at_utc >= ? AND bo.occurred_at_utc < ?
        ORDER BY bo.occurred_at_utc, bo.behaviour_occurrence_id
    """, (start_utc, end_utc)).fetchall()
    occurrences = []
    for row in rows:
        item = dict(row)
        local = behaviour_utc_to_vancouver(item["occurred_at_utc"])
        item["local_time"] = local.strftime("%Y-%m-%d %H:%M")
        item["operational_day"] = get_behaviour_operational_day(local)
        item["band"] = get_behaviour_operational_band(local)
        item["categories"] = _behaviour_categories_for_row(row)
        item["abc_sections"] = _behaviour_week_abc_sections(item)
        item["summary"] = ", ".join(
            item["categories"]
        ) if item["record_format"] != "ABC" else ", ".join(
            label for label in (
                ABC_FIELD_LABELS[field]
                for field in ABC_BEHAVIOUR_FIELDS if item.get(field)
            )
        )
        if not item["summary"]:
            item["summary"] = "Behaviour occurrence recorded"
        item["status_label"] = "Voided" if item["status"] == "Voided" else "Active"
        item["shift_type"] = None
        if item.get("shift_id"):
            shift_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='shifts'"
            ).fetchone()
            if shift_table:
                shift = conn.execute(
                    "SELECT shift_type FROM shifts WHERE shift_id = ?",
                    (item["shift_id"],)
                ).fetchone()
                item["shift_type"] = shift["shift_type"] if shift else None
        occurrences.append(item)
    return occurrences


def _behaviour_week_context(conn, monday):
    occurrences = _behaviour_week_occurrences(conn, monday)
    days = []
    for offset in range(7):
        operational_day = monday + timedelta(days=offset)
        bands = []
        for band in ("Night", "Day", "Evening"):
            matching = [item for item in occurrences if item["operational_day"] == operational_day and item["band"] == band]
            counts = {field: sum(item[field] for item in matching if item["status"] != "Voided") for field in BEHAVIOUR_CATEGORY_FIELDS}
            bands.append({"name": band, "counts": counts, "occurrences": matching})
        days.append({
            "date": operational_day,
            "bands": bands,
            "episodes": sorted(
                [item for item in occurrences
                 if item["operational_day"] == operational_day],
                key=lambda item: (item["occurred_at_utc"], item["behaviour_occurrence_id"])
            ),
        })
    return days


def _behaviour_recent_occurrences(conn, client_id):
    if not client_id:
        return []
    rows = conn.execute("""
        SELECT bo.*, u.full_name AS recorder_name FROM behaviour_occurrences bo
        JOIN users u ON u.user_id = bo.recorded_by_user_id
        WHERE bo.client_id = ? AND bo.status != 'Voided'
        ORDER BY bo.occurred_at_utc DESC, bo.behaviour_occurrence_id DESC LIMIT 10
    """, (client_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["local_time"] = behaviour_utc_to_vancouver(item["occurred_at_utc"]).strftime("%Y-%m-%d %H:%M")
        item["categories"] = _behaviour_categories_for_row(row)
        result.append(item)
    return result


def _render_behaviour_record(
    conn, selected_client_id=None, error=None, values=None, shift_context=False,
    submission_token=None, duplicate_warning=None,
    documentation_context=None, documentation_context_alternatives=None
):
    clients = conn.execute("SELECT client_id, client_name FROM clients WHERE active = 1 ORDER BY client_name").fetchall()
    if selected_client_id is not None:
        try:
            validate_active_behaviour_client(conn, selected_client_id)
        except ValueError:
            selected_client_id = None
    values = values or {}
    return render_template("behaviour_record.html", clients=clients,
        selected_client_id=selected_client_id, recent_occurrences=_behaviour_recent_occurrences(conn, selected_client_id),
        submission_token=submission_token or secrets.token_urlsafe(32), error=error,
        duplicate_warning=duplicate_warning,
        category_fields=BEHAVIOUR_CATEGORY_FIELDS, category_labels=BEHAVIOUR_CATEGORY_LABELS,
        abc_antecedent_fields=ABC_ANTECEDENT_FIELDS,
        abc_behaviour_fields=ABC_BEHAVIOUR_FIELDS,
        abc_response_fields=ABC_RESPONSE_FIELDS,
        abc_field_labels=ABC_FIELD_LABELS,
        now_local=datetime.now(VANCOUVER_TIMEZONE).strftime("%Y-%m-%dT%H:%M"),
        values=values, shift_context=shift_context,
        documentation_context=documentation_context,
        documentation_context_alternatives=(
            documentation_context_alternatives or []
        ))


@app.route("/schedule")
def schedule_index():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        user = get_active_authenticated_user(conn, session["user_id"])
        if user["role"] not in SCHEDULE_VIEW_ROLES:
            return "Access denied", 403
        clients = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE active = 1 ORDER BY client_name, client_id
        """).fetchall()
        monday = get_schedule_operational_week_start(
            datetime.now(VANCOUVER_TIMEZONE)
        )
        if len(clients) == 1:
            return redirect(url_for(
                "schedule_week", client_id=clients[0]["client_id"],
                monday=monday.isoformat()
            ))
        return render_template(
            "schedule_client_select.html",
            clients=clients,
            current_monday=monday,
        )
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()


def _schedule_management_user(conn):
    user = get_active_authenticated_user(conn, session["user_id"])
    if user["role"] not in SCHEDULE_MANAGEMENT_ROLES:
        raise PermissionError("Schedule management access is required.")
    return user


def _schedule_form_defaults(request_args):
    shift_date = request_args.get("shift_date", "")
    shift_type = request_args.get("shift_type", "Day")
    if shift_type not in SCHEDULE_SHIFT_TYPES:
        shift_type = "Day"
    return {
        "client_id": "",
        "shift_date": None,
        "shift_date_value": shift_date,
        "shift_type": shift_type,
        "planned_start_time": "",
        "planned_end_time": "",
        "status": "Draft",
        "notes": "",
        "worker_ids": set(),
        "worker_times": {},
        "errors": [],
    }


def _schedule_log_details(values):
    return (
        f"Date: {values['shift_date_value']}\n"
        f"Shift type: {values['shift_type']}\n"
        f"Time: {values['planned_start_time']}-{values['planned_end_time']}\n"
        f"Status: {values['status']}"
    )


@app.route("/schedule/client/<int:client_id>/week")
def schedule_client_index(client_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        user = get_active_authenticated_user(conn, session["user_id"])
        if user["role"] not in SCHEDULE_VIEW_ROLES:
            return "Access denied", 403
        client = conn.execute("""
            SELECT client_id FROM clients WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            return "Client not found", 404
        requested_date = request.args.get("date")
        if requested_date:
            try:
                selected_date = date.fromisoformat(requested_date)
            except ValueError:
                return "Schedule date must be a valid ISO date.", 400
            monday = selected_date - timedelta(days=selected_date.weekday())
        else:
            monday = get_schedule_operational_week_start(
                datetime.now(VANCOUVER_TIMEZONE)
            )
        return redirect(url_for(
            "schedule_week", client_id=client_id, monday=monday.isoformat(),
            view=("staff" if request.args.get("view") == "staff" else None),
        ))
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()


@app.route("/schedule/client/<int:client_id>/shift/new", methods=["GET", "POST"])
def schedule_shift_new(client_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            user = _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        client = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            return "Client not found", 404

        values = _schedule_form_defaults(request.args)
        if request.method == "POST":
            values = _schedule_form_values(conn, request.form, client_id=client_id)
            if not values["errors"]:
                now_utc = serialize_behaviour_utc(
                    datetime.now(timezone.utc).replace(microsecond=0)
                )
                try:
                    conn.execute("BEGIN")
                    previous_publication_state = _schedule_week_publication_state(
                        conn, values["shift_date"] - timedelta(
                            days=values["shift_date"].weekday()
                        ), values["client_id"]
                    )
                    cursor = conn.execute("""
                        INSERT INTO schedule_shifts
                        (client_id, shift_date, shift_type,
                         planned_start_time, planned_end_time, status, notes,
                         created_by, created_at_utc, updated_by, updated_at_utc)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        values["client_id"], values["shift_date_value"],
                        values["shift_type"], values["planned_start_time"],
                        values["planned_end_time"], values["status"],
                        values["notes"], user["user_id"], now_utc,
                        user["user_id"], now_utc,
                    ))
                    schedule_shift_id = cursor.lastrowid
                    log_activity(
                        conn, "SCHEDULE", "schedule_shift_created",
                        f"Schedule shift created: {values['shift_type']} on {values['shift_date_value']}",
                        user_id=user["user_id"], client_id=values["client_id"],
                        related_table="schedule_shifts",
                        related_id=schedule_shift_id,
                        details=_schedule_log_details(values),
                        storyline_visible=False,
                    )
                    for worker_id in sorted(values["worker_ids"]):
                        worker_hours = values["worker_times"][worker_id]
                        assignment = conn.execute("""
                            INSERT INTO schedule_staff
                            (schedule_shift_id, user_id, planned_start_time,
                             planned_end_time, assigned_by, assigned_at_utc)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (schedule_shift_id, worker_id,
                               worker_hours["planned_start_time"],
                               worker_hours["planned_end_time"],
                               user["user_id"], now_utc))
                        log_activity(
                            conn, "SCHEDULE", "schedule_staff_assigned",
                            f"Staff assigned to schedule shift {schedule_shift_id}",
                            user_id=user["user_id"], client_id=values["client_id"],
                            related_table="schedule_staff",
                            related_id=assignment.lastrowid,
                            details=(
                                f"Worker user ID: {worker_id}\n"
                                f"Planned hours: "
                                f"{worker_hours['planned_start_time']}-"
                                f"{worker_hours['planned_end_time']}"
                            ),
                            storyline_visible=False,
                        )
                    _schedule_week_return_to_draft(
                        conn, values["client_id"],
                        values["shift_date"] - timedelta(
                            days=values["shift_date"].weekday()
                        ), user["user_id"],
                        previous_publication_state["state"],
                        "schedule_shift_created",
                    )
                    conn.commit()
                    flash("Scheduled shift created.")
                    monday = values["shift_date"] - timedelta(
                        days=values["shift_date"].weekday()
                    )
                    return redirect(url_for(
                        "schedule_week", client_id=client_id,
                        monday=monday.isoformat()
                    ))
                except sqlite3.IntegrityError:
                    conn.rollback()
                    values["errors"] = [
                        "A schedule already exists for this client, date, and shift type."
                    ]
                except Exception:
                    conn.rollback()
                    return "Schedule could not be saved.", 500
        return render_template(
            "schedule_shift_form.html",
            **_schedule_form_context(conn, values, client=client),
        )
    finally:
        conn.close()


@app.route("/schedule/shift/<int:schedule_shift_id>/edit", methods=["GET", "POST"])
def schedule_shift_edit(schedule_shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            user = _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        existing = conn.execute("""
            SELECT * FROM schedule_shifts WHERE schedule_shift_id = ?
        """, (schedule_shift_id,)).fetchone()
        if existing is None:
            return "Schedule not found", 404
        client = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE client_id = ? AND active = 1
        """, (existing["client_id"],)).fetchone()
        if client is None:
            return "Client not found", 404
        existing_date = date.fromisoformat(existing["shift_date"])
        if not _schedule_shift_is_editable(existing_date, existing["status"]):
            return "Schedule is not editable.", 403

        if request.method == "GET":
            values = {
                "client_id": existing["client_id"],
                "shift_date": existing_date,
                "shift_date_value": existing["shift_date"],
                "shift_type": existing["shift_type"],
                "planned_start_time": existing["planned_start_time"],
                "planned_end_time": existing["planned_end_time"],
                "status": existing["status"],
                "notes": existing["notes"] or "",
                "worker_ids": set(),
                "errors": [],
            }
        else:
            values = _schedule_form_values(conn, request.form, existing)
            if not values["errors"]:
                now_utc = serialize_behaviour_utc(
                    datetime.now(timezone.utc).replace(microsecond=0)
                )
                try:
                    conn.execute("BEGIN")
                    week_start = existing_date - timedelta(
                        days=existing_date.weekday()
                    )
                    previous_publication_state = _schedule_week_publication_state(
                        conn, week_start, existing["client_id"]
                    )
                    conn.execute("""
                        UPDATE schedule_shifts
                        SET planned_start_time = ?, planned_end_time = ?,
                            status = ?, notes = ?, updated_by = ?,
                            updated_at_utc = ?
                        WHERE schedule_shift_id = ?
                    """, (
                        values["planned_start_time"], values["planned_end_time"],
                        values["status"], values["notes"], user["user_id"],
                        now_utc, schedule_shift_id,
                    ))
                    log_activity(
                        conn, "SCHEDULE", "schedule_shift_updated",
                        f"Schedule shift updated: {existing['shift_type']} on {existing['shift_date']}",
                        user_id=user["user_id"], client_id=existing["client_id"],
                        related_table="schedule_shifts", related_id=schedule_shift_id,
                        details=_schedule_log_details(values), storyline_visible=False,
                    )
                    old_rows = conn.execute("""
                        SELECT ss.schedule_staff_id, ss.user_id,
                               ss.planned_start_time, ss.planned_end_time,
                               u.full_name
                        FROM schedule_staff AS ss
                        JOIN users AS u ON u.user_id = ss.user_id
                        WHERE ss.schedule_shift_id = ?
                    """, (schedule_shift_id,)).fetchall()
                    old_by_user = {row["user_id"]: row for row in old_rows}
                    for worker_id in sorted(values["worker_ids"] - old_by_user.keys()):
                        worker_hours = values["worker_times"][worker_id]
                        assignment = conn.execute("""
                            INSERT INTO schedule_staff
                            (schedule_shift_id, user_id, planned_start_time,
                             planned_end_time, assigned_by, assigned_at_utc)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (schedule_shift_id, worker_id,
                               worker_hours["planned_start_time"],
                               worker_hours["planned_end_time"],
                               user["user_id"], now_utc))
                        log_activity(
                            conn, "SCHEDULE", "schedule_staff_assigned",
                            f"Staff assigned to schedule shift {schedule_shift_id}",
                            user_id=user["user_id"], client_id=existing["client_id"],
                            related_table="schedule_staff", related_id=assignment.lastrowid,
                            details=(
                                f"Worker user ID: {worker_id}\n"
                                f"Planned hours: "
                                f"{worker_hours['planned_start_time']}-"
                                f"{worker_hours['planned_end_time']}"
                            ), storyline_visible=False,
                        )
                    for worker_id in sorted(values["worker_ids"] & old_by_user.keys()):
                        old_assignment = old_by_user[worker_id]
                        worker_hours = values["worker_times"][worker_id]
                        old_start = old_assignment["planned_start_time"]
                        old_end = old_assignment["planned_end_time"]
                        if (
                            worker_hours["planned_start_time"] != old_start
                            or worker_hours["planned_end_time"] != old_end
                        ):
                            conn.execute("""
                                UPDATE schedule_staff
                                SET planned_start_time = ?, planned_end_time = ?
                                WHERE schedule_staff_id = ?
                            """, (
                                worker_hours["planned_start_time"],
                                worker_hours["planned_end_time"],
                                old_assignment["schedule_staff_id"],
                            ))
                            log_activity(
                                conn, "SCHEDULE", "schedule_staff_hours_updated",
                                f"Planned hours updated for {old_assignment['full_name']}",
                                user_id=user["user_id"],
                                client_id=existing["client_id"],
                                related_table="schedule_staff",
                                related_id=old_assignment["schedule_staff_id"],
                                details=_schedule_staff_hours_details(
                                    worker_id, old_assignment["full_name"],
                                    old_start, old_end,
                                    worker_hours["planned_start_time"],
                                    worker_hours["planned_end_time"],
                                    schedule_shift_id, existing["shift_date"],
                                    existing["shift_type"],
                                ),
                                storyline_visible=False,
                            )
                    for worker_id in sorted(old_by_user.keys() - values["worker_ids"]):
                        assignment_id = old_by_user[worker_id]["schedule_staff_id"]
                        conn.execute(
                            "DELETE FROM schedule_staff WHERE schedule_staff_id = ?",
                            (assignment_id,)
                        )
                        log_activity(
                            conn, "SCHEDULE", "schedule_staff_removed",
                            f"Staff removed from schedule shift {schedule_shift_id}",
                            user_id=user["user_id"], client_id=existing["client_id"],
                            related_table="schedule_staff", related_id=assignment_id,
                            details=f"Worker user ID: {worker_id}", storyline_visible=False,
                        )
                    _schedule_week_return_to_draft(
                        conn, existing["client_id"], week_start,
                        user["user_id"], previous_publication_state["state"],
                        "schedule_shift_updated",
                    )
                    conn.commit()
                    flash("Scheduled shift updated.")
                    monday = existing_date - timedelta(days=existing_date.weekday())
                    return redirect(url_for(
                        "schedule_week", client_id=existing["client_id"],
                        monday=monday.isoformat()
                    ))
                except sqlite3.IntegrityError:
                    conn.rollback()
                    values["errors"] = ["Schedule could not be updated."]
                except Exception:
                    conn.rollback()
                    return "Schedule could not be saved.", 500

        if request.method == "GET":
            values["worker_ids"] = {
                row["user_id"] for row in conn.execute("""
                    SELECT user_id FROM schedule_staff
                    WHERE schedule_shift_id = ?
                """, (schedule_shift_id,)).fetchall()
            }
        return render_template(
            "schedule_shift_form.html",
            **_schedule_form_context(conn, values, existing, client),
        )
    finally:
        conn.close()


def _schedule_staff_assignment_form_values(form, worker_name, shift_type=None):
    selected_type = shift_type or (form.get("shift_type") or "").strip()
    start_value = (form.get("planned_start_time") or "").strip()
    end_value = (form.get("planned_end_time") or "").strip()
    errors = []
    if selected_type not in SCHEDULE_SHIFT_TYPES:
        errors.append("A valid shift type is required.")
    if not start_value or not end_value:
        errors.append("Planned start and end times are required.")
    else:
        try:
            datetime.strptime(start_value, "%H:%M")
            datetime.strptime(end_value, "%H:%M")
        except ValueError:
            errors.append("Planned hours must use HH:MM.")
    if not errors:
        worker_error = _validate_schedule_worker_hours(
            worker_name, selected_type, start_value, end_value
        )
        if worker_error:
            errors.append(worker_error)
    return {
        "shift_type": selected_type,
        "planned_start_time": start_value,
        "planned_end_time": end_value,
        "errors": errors,
    }


def _schedule_staff_assignment_form_context(
    client, worker, shift_date, monday, values, editing,
):
    return {
        "client": client,
        "worker": worker,
        "shift_date": shift_date,
        "monday": monday,
        "values": values,
        "editing": editing,
        "error": "; ".join(values.get("errors", [])),
    }


@app.route(
    "/schedule/client/<int:client_id>/week/<monday>/staff/<int:user_id>/new/<shift_date>",
    methods=["GET", "POST"],
)
def schedule_staff_assignment_new(client_id, monday, user_id, shift_date):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            user = _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        client = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        worker = conn.execute("""
            SELECT user_id, full_name, active, role FROM users
            WHERE user_id = ?
        """, (user_id,)).fetchone()
        if client is None:
            return "Client not found", 404
        if worker is None or worker["role"] != "Support Worker" or not worker["active"]:
            return "Only active Support Workers may be assigned.", 403
        try:
            week_start = _parse_schedule_monday(monday)
            selected_date = date.fromisoformat(shift_date)
        except ValueError as error:
            return str(error), 404
        if not week_start <= selected_date <= week_start + timedelta(days=6):
            return "Assignment date is outside the selected week.", 400
        if selected_date < datetime.now(VANCOUVER_TIMEZONE).date():
            return "Past schedules cannot be edited.", 403

        if request.method == "GET":
            values = {
                "shift_type": "",
                "planned_start_time": "",
                "planned_end_time": "",
                "errors": [],
            }
        else:
            values = _schedule_staff_assignment_form_values(
                request.form, worker["full_name"]
            )
            if not values["errors"]:
                now_utc = serialize_behaviour_utc(
                    datetime.now(timezone.utc).replace(microsecond=0)
                )
                try:
                    conn.execute("BEGIN")
                    previous_publication_state = _schedule_week_publication_state(
                        conn, week_start, client_id
                    )
                    parent = conn.execute("""
                        SELECT * FROM schedule_shifts
                        WHERE client_id = ? AND shift_date = ? AND shift_type = ?
                    """, (client_id, selected_date.isoformat(), values["shift_type"])).fetchone()
                    if parent is not None:
                        if not _schedule_shift_is_editable(
                            selected_date, parent["status"]
                        ):
                            conn.rollback()
                            return "Schedule is not editable.", 403
                        existing_assignment = conn.execute("""
                            SELECT schedule_staff_id
                            FROM schedule_staff
                            WHERE schedule_shift_id = ? AND user_id = ?
                        """, (parent["schedule_shift_id"], user_id)).fetchone()
                        if existing_assignment is not None:
                            conn.rollback()
                            flash("That assignment already exists. Edit it instead.")
                            return redirect(url_for(
                                "schedule_staff_assignment_edit",
                                client_id=client_id, monday=week_start.isoformat(),
                                schedule_staff_id=existing_assignment["schedule_staff_id"],
                            ))
                    else:
                        cursor = conn.execute("""
                            INSERT INTO schedule_shifts
                            (client_id, shift_date, shift_type,
                             planned_start_time, planned_end_time, status, notes,
                             created_by, created_at_utc, updated_by, updated_at_utc)
                            VALUES (?, ?, ?, ?, ?, 'Draft', NULL, ?, ?, ?, ?)
                        """, (
                            client_id, selected_date.isoformat(), values["shift_type"],
                            values["planned_start_time"], values["planned_end_time"],
                            user["user_id"], now_utc, user["user_id"], now_utc,
                        ))
                        parent_id = cursor.lastrowid
                        log_activity(
                            conn, "SCHEDULE", "schedule_shift_created",
                            f"Schedule shift created: {values['shift_type']} on {selected_date.isoformat()}",
                            user_id=user["user_id"], client_id=client_id,
                            related_table="schedule_shifts", related_id=parent_id,
                            details=(
                                f"Date: {selected_date.isoformat()}\n"
                                f"Shift type: {values['shift_type']}\n"
                                f"Time: {values['planned_start_time']}-{values['planned_end_time']}\n"
                                "Status: Draft"
                            ), storyline_visible=False,
                        )
                        parent = {"schedule_shift_id": parent_id}
                    assignment = conn.execute("""
                        INSERT INTO schedule_staff
                        (schedule_shift_id, user_id, planned_start_time,
                         planned_end_time, assigned_by, assigned_at_utc)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        parent["schedule_shift_id"], user_id,
                        values["planned_start_time"], values["planned_end_time"],
                        user["user_id"], now_utc,
                    ))
                    log_activity(
                        conn, "SCHEDULE", "schedule_staff_assigned",
                        f"Staff assigned to schedule shift {parent['schedule_shift_id']}",
                        user_id=user["user_id"], client_id=client_id,
                        related_table="schedule_staff", related_id=assignment.lastrowid,
                        details=(
                            f"Worker user ID: {user_id}\n"
                            f"Planned hours: {values['planned_start_time']}-"
                            f"{values['planned_end_time']}"
                        ), storyline_visible=False,
                    )
                    _schedule_week_return_to_draft(
                        conn, client_id, week_start, user["user_id"],
                        previous_publication_state["state"],
                        "schedule_staff_assigned",
                    )
                    conn.commit()
                    flash("Staff assignment created.")
                    return redirect(url_for(
                        "schedule_week", client_id=client_id,
                        monday=week_start.isoformat(), view="staff",
                    ))
                except sqlite3.IntegrityError:
                    conn.rollback()
                    values["errors"] = ["That assignment already exists."]
                except Exception:
                    conn.rollback()
                    return "Schedule could not be saved.", 500
        return render_template(
            "schedule_staff_assignment_form.html",
            **_schedule_staff_assignment_form_context(
                client, worker, selected_date, week_start, values, False
            ),
        )
    finally:
        conn.close()


@app.route(
    "/schedule/client/<int:client_id>/week/<monday>/staff-assignment/<int:schedule_staff_id>/edit",
    methods=["GET", "POST"],
)
def schedule_staff_assignment_edit(client_id, monday, schedule_staff_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            user = _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        existing = conn.execute("""
            SELECT st.schedule_staff_id, st.user_id, st.planned_start_time,
                   st.planned_end_time, u.full_name, u.active AS worker_active,
                   ss.schedule_shift_id, ss.client_id, ss.shift_date,
                   ss.shift_type, ss.status, c.client_name, c.active AS client_active
            FROM schedule_staff AS st
            JOIN schedule_shifts AS ss ON ss.schedule_shift_id = st.schedule_shift_id
            JOIN users AS u ON u.user_id = st.user_id
            JOIN clients AS c ON c.client_id = ss.client_id
            WHERE st.schedule_staff_id = ?
        """, (schedule_staff_id,)).fetchone()
        if existing is None:
            return "Assignment not found", 404
        if existing["client_id"] != client_id or not existing["client_active"]:
            return "Assignment not found", 404
        try:
            week_start = _parse_schedule_monday(monday)
            selected_date = date.fromisoformat(existing["shift_date"])
        except ValueError as error:
            return str(error), 404
        if not week_start <= selected_date <= week_start + timedelta(days=6):
            return "Assignment is outside the selected week.", 400
        if not _schedule_shift_is_editable(selected_date, existing["status"]):
            return "Schedule is not editable.", 403
        if request.method == "GET":
            values = {
                "shift_type": existing["shift_type"],
                "planned_start_time": existing["planned_start_time"] or "",
                "planned_end_time": existing["planned_end_time"] or "",
                "errors": [],
            }
        else:
            values = _schedule_staff_assignment_form_values(
                request.form, existing["full_name"], existing["shift_type"]
            )
            if not values["errors"]:
                now_utc = serialize_behaviour_utc(
                    datetime.now(timezone.utc).replace(microsecond=0)
                )
                try:
                    conn.execute("BEGIN")
                    previous_publication_state = _schedule_week_publication_state(
                        conn, week_start, client_id
                    )
                    conn.execute("""
                        UPDATE schedule_staff
                        SET planned_start_time = ?, planned_end_time = ?
                        WHERE schedule_staff_id = ?
                    """, (
                        values["planned_start_time"], values["planned_end_time"],
                        schedule_staff_id,
                    ))
                    hours_changed = (
                        values["planned_start_time"] != existing["planned_start_time"]
                        or values["planned_end_time"] != existing["planned_end_time"]
                    )
                    if hours_changed:
                        log_activity(
                            conn, "SCHEDULE", "schedule_staff_hours_updated",
                            f"Planned hours updated for {existing['full_name']}",
                            user_id=user["user_id"], client_id=client_id,
                            related_table="schedule_staff", related_id=schedule_staff_id,
                            details=_schedule_staff_hours_details(
                                existing["user_id"], existing["full_name"],
                                existing["planned_start_time"], existing["planned_end_time"],
                                values["planned_start_time"], values["planned_end_time"],
                                existing["schedule_shift_id"], existing["shift_date"],
                                existing["shift_type"],
                            ), storyline_visible=False,
                        )
                        _schedule_week_return_to_draft(
                            conn, client_id, week_start, user["user_id"],
                            previous_publication_state["state"],
                            "schedule_staff_hours_updated",
                        )
                    conn.commit()
                    flash("Staff assignment updated.")
                    return redirect(url_for(
                        "schedule_week", client_id=client_id,
                        monday=week_start.isoformat(), view="staff",
                    ))
                except Exception:
                    conn.rollback()
                    return "Schedule could not be saved.", 500
        worker = {
            "user_id": existing["user_id"],
            "full_name": existing["full_name"],
            "active": bool(existing["worker_active"]),
        }
        client = {
            "client_id": existing["client_id"],
            "client_name": existing["client_name"],
        }
        return render_template(
            "schedule_staff_assignment_form.html",
            **_schedule_staff_assignment_form_context(
                client, worker, selected_date, week_start, values, True
            ),
        )
    finally:
        conn.close()


@app.route(
    "/schedule/client/<int:client_id>/week/<monday>/staff-assignment/<int:schedule_staff_id>/remove",
    methods=["POST"],
)
def schedule_staff_assignment_remove(client_id, monday, schedule_staff_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            user = _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        client = conn.execute("""
            SELECT client_id FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            return "Client not found", 404
        try:
            week_start = _parse_schedule_monday(monday)
        except ValueError as error:
            return str(error), 404
        existing = conn.execute("""
            SELECT st.schedule_staff_id, st.user_id, u.full_name,
                   ss.schedule_shift_id, ss.client_id, ss.shift_date,
                   ss.shift_type, ss.status
            FROM schedule_staff AS st
            JOIN schedule_shifts AS ss ON ss.schedule_shift_id = st.schedule_shift_id
            JOIN users AS u ON u.user_id = st.user_id
            WHERE st.schedule_staff_id = ?
        """, (schedule_staff_id,)).fetchone()
        if existing is None or existing["client_id"] != client_id:
            return "Assignment not found", 404
        try:
            selected_date = date.fromisoformat(existing["shift_date"])
        except ValueError:
            return "Assignment not found", 404
        if not week_start <= selected_date <= week_start + timedelta(days=6):
            return "Assignment is outside the selected week.", 400
        if not _schedule_shift_is_editable(selected_date, existing["status"]):
            return "Schedule is not editable.", 403

        try:
            conn.execute("BEGIN IMMEDIATE")
            previous_publication_state = _schedule_week_publication_state(
                conn, week_start, client_id
            )
            deleted = conn.execute(
                "DELETE FROM schedule_staff WHERE schedule_staff_id = ?",
                (schedule_staff_id,),
            )
            if deleted.rowcount != 1:
                conn.rollback()
                return "Assignment not found", 404
            log_activity(
                conn, "SCHEDULE", "schedule_staff_removed",
                f"Staff removed from schedule shift {existing['schedule_shift_id']}",
                user_id=user["user_id"], client_id=client_id,
                related_table="schedule_staff", related_id=schedule_staff_id,
                details=f"Worker user ID: {existing['user_id']}",
                storyline_visible=False,
            )
            _schedule_week_return_to_draft(
                conn, client_id, week_start, user["user_id"],
                previous_publication_state["state"],
                "schedule_staff_removed",
            )
            conn.commit()
            flash("Staff assignment removed.")
            return redirect(url_for(
                "schedule_week", client_id=client_id,
                monday=week_start.isoformat(), view="staff",
            ))
        except Exception:
            conn.rollback()
            return "Schedule could not be updated.", 500
    finally:
        conn.close()


def _schedule_staff_order_redirect(client_id, monday, message=None):
    if message:
        flash(message)
    return redirect(url_for(
        "schedule_week", client_id=client_id, monday=monday,
        view="staff",
    ))


def _schedule_staff_order_move(client_id, user_id, direction):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        client = conn.execute("""
            SELECT client_id FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            return "Client not found", 404
        try:
            monday = _parse_schedule_monday(request.form.get("monday"))
        except ValueError as error:
            return str(error), 400

        staff_view = _schedule_staff_view_context(conn, monday, client_id)
        workers = staff_view["workers"]
        current_signature = staff_view["order_signature"]
        submitted_signature = (request.form.get("expected_order_signature") or "").strip()
        if submitted_signature != current_signature:
            return _schedule_staff_order_redirect(
                client_id, monday.isoformat(),
                "Staff order changed elsewhere. The current order was reloaded.",
            )
        worker_ids = [worker["user_id"] for worker in workers]
        if user_id not in worker_ids:
            return _schedule_staff_order_redirect(
                client_id, monday.isoformat(),
                "That worker is not in the selected Staff View.",
            )
        current_index = worker_ids.index(user_id)
        adjacent_index = current_index + direction
        if adjacent_index < 0 or adjacent_index >= len(worker_ids):
            return _schedule_staff_order_redirect(
                client_id, monday.isoformat(), "Staff order is already at the boundary."
            )

        worker_ids[current_index], worker_ids[adjacent_index] = (
            worker_ids[adjacent_index], worker_ids[current_index]
        )
        existing_rows = conn.execute("""
            SELECT user_id, display_order
            FROM schedule_staff_order
            WHERE client_id = ?
            ORDER BY display_order, user_id
        """, (client_id,)).fetchall()
        final_ids = list(worker_ids)
        final_ids.extend(
            row["user_id"] for row in existing_rows
            if row["user_id"] not in worker_ids
        )
        now_utc = serialize_behaviour_utc(
            datetime.now(timezone.utc).replace(microsecond=0)
        )
        try:
            conn.execute("BEGIN IMMEDIATE")
            offset = max(1000000, len(final_ids) + len(existing_rows) + 100)
            conn.execute("""
                UPDATE schedule_staff_order
                SET display_order = display_order + ?
                WHERE client_id = ?
            """, (offset, client_id))
            existing_user_ids = {row["user_id"] for row in existing_rows}
            for display_order, target_user_id in enumerate(final_ids, start=1):
                if target_user_id in existing_user_ids:
                    conn.execute("""
                        UPDATE schedule_staff_order
                        SET display_order = ?, updated_by = ?, updated_at_utc = ?
                        WHERE client_id = ? AND user_id = ?
                    """, (
                        display_order, session["user_id"], now_utc,
                        client_id, target_user_id,
                    ))
                else:
                    conn.execute("""
                        INSERT INTO schedule_staff_order
                        (client_id, user_id, display_order, updated_by, updated_at_utc)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        client_id, target_user_id, display_order,
                        session["user_id"], now_utc,
                    ))
            log_activity(
                conn, "SCHEDULE", "schedule_staff_order_changed",
                "Staff View worker order changed",
                user_id=session["user_id"], client_id=client_id,
                related_table="schedule_staff_order", related_id=user_id,
                details=(
                    f"Moved worker user ID: {user_id}\n"
                    f"Direction: {'down' if direction > 0 else 'up'}\n"
                    f"New position: {adjacent_index + 1}"
                ), storyline_visible=False,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return _schedule_staff_order_redirect(
            client_id, monday.isoformat(), "Staff order updated."
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        return _schedule_staff_order_redirect(
            client_id, request.form.get("monday", ""),
            "Staff order could not be updated.",
        )
    except Exception:
        conn.rollback()
        return "Staff order could not be updated.", 500
    finally:
        conn.close()


@app.route(
    "/schedule/client/<int:client_id>/staff-order/<int:user_id>/move-up",
    methods=["POST"],
)
def schedule_staff_order_move_up(client_id, user_id):
    return _schedule_staff_order_move(client_id, user_id, -1)


@app.route(
    "/schedule/client/<int:client_id>/staff-order/<int:user_id>/move-down",
    methods=["POST"],
)
def schedule_staff_order_move_down(client_id, user_id):
    return _schedule_staff_order_move(client_id, user_id, 1)


@app.route(
    "/schedule/client/<int:client_id>/week/<monday>/publish",
    methods=["POST"],
)
def schedule_week_publish(client_id, monday):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            user = _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        try:
            week_start = _parse_schedule_monday(monday)
        except ValueError as error:
            flash(str(error))
            return redirect(url_for("schedule_index"))

        client = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            flash("The selected client is not active.")
            return redirect(url_for("schedule_index"))

        current_monday = get_schedule_operational_week_start(
            datetime.now(VANCOUVER_TIMEZONE)
        )
        if week_start < current_monday:
            flash("A past schedule week cannot be published.")
            return redirect(url_for(
                "schedule_week", client_id=client_id,
                monday=week_start.isoformat(),
            ))

        end_date = week_start + timedelta(days=6)
        now_utc = serialize_behaviour_utc(
            datetime.now(timezone.utc).replace(microsecond=0)
        )
        try:
            conn.execute("BEGIN IMMEDIATE")
            client = conn.execute("""
                SELECT client_id, client_name FROM clients
                WHERE client_id = ? AND active = 1
            """, (client_id,)).fetchone()
            if client is None:
                conn.rollback()
                flash("The selected client is no longer active.")
                return redirect(url_for("schedule_index"))

            publication_state = _schedule_week_publication_state(
                conn, week_start, client_id
            )
            if publication_state["state"] != "Draft":
                messages = {
                    "Empty": "This week has no schedule rows to publish.",
                    "Published": "This schedule week is already published.",
                    "Mixed": (
                        "This schedule week has mixed statuses and cannot be published."
                    ),
                }
                conn.rollback()
                flash(messages.get(
                    publication_state["state"],
                    "This schedule week cannot be published.",
                ))
                return redirect(url_for(
                    "schedule_week", client_id=client_id,
                    monday=week_start.isoformat(),
                ))

            first_row = conn.execute("""
                SELECT schedule_shift_id
                FROM schedule_shifts
                WHERE client_id = ? AND shift_date BETWEEN ? AND ?
                  AND status = 'Draft'
                ORDER BY shift_date, schedule_shift_id
                LIMIT 1
            """, (
                client_id, week_start.isoformat(), end_date.isoformat(),
            )).fetchone()
            updated = conn.execute("""
                UPDATE schedule_shifts
                SET status = 'Published', updated_by = ?, updated_at_utc = ?
                WHERE client_id = ? AND shift_date BETWEEN ? AND ?
                  AND status = 'Draft'
            """, (
                user["user_id"], now_utc, client_id,
                week_start.isoformat(), end_date.isoformat(),
            ))
            if first_row is None or updated.rowcount < 1:
                raise RuntimeError("No Draft schedule rows were published.")

            log_activity(
                conn, "SCHEDULE", "schedule_week_published",
                f"Schedule week published for {client['client_name']}",
                user_id=user["user_id"], client_id=client_id,
                related_table="schedule_shifts",
                related_id=first_row["schedule_shift_id"],
                details=(
                    f"Client ID: {client_id}\n"
                    f"Week start: {week_start.isoformat()}\n"
                    f"Week end: {end_date.isoformat()}\n"
                    f"Shifts published: {updated.rowcount}\n"
                    "Previous state: Draft\n"
                    "Resulting state: Published"
                ),
                storyline_visible=False,
            )
            conn.commit()
            flash(
                f"Published {updated.rowcount} schedule shift(s) for "
                f"{client['client_name']}."
            )
            return redirect(url_for(
                "schedule_week", client_id=client_id,
                monday=week_start.isoformat(),
            ))
        except Exception:
            conn.rollback()
            return "Schedule could not be published.", 500
    finally:
        conn.close()


@app.route("/schedule/client/<int:client_id>/week/<monday>")
def schedule_week(client_id, monday):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        user = get_active_authenticated_user(conn, session["user_id"])
        if user["role"] not in SCHEDULE_VIEW_ROLES:
            return "Access denied", 403
        can_manage = user["role"] in SCHEDULE_MANAGEMENT_ROLES
        client = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            return "Client not found", 404
        week_start = _parse_schedule_monday(monday)
        staff_view = can_manage and request.args.get("view") == "staff"
        current_monday = get_schedule_operational_week_start(
            datetime.now(VANCOUVER_TIMEZONE)
        )
        publication_state = _schedule_week_publication_state(
            conn, week_start, client_id
        )
        schedule_visible = (
            can_manage
            or _schedule_week_visible_to_support(publication_state)
        )
        days = (
            _schedule_week_context(conn, week_start, client_id)
            if schedule_visible else None
        )
        staff_view_context = (
            _schedule_staff_view_context(conn, week_start, client_id)
            if staff_view else None
        )
        staff_summary = (
            _schedule_week_staff_summary(conn, week_start, client_id)
            if can_manage else []
        )
        today = datetime.now(VANCOUVER_TIMEZONE).date()
        for day in days or []:
            for shift in day["shifts"]:
                for entry in shift["entries"]:
                    entry["editable"] = (
                        can_manage
                        and _schedule_shift_is_editable(
                            day["date"], entry["status"], today
                        )
                    )
        return render_template(
            "schedule_week.html",
            monday=week_start,
            week_end=week_start + timedelta(days=6),
            client=client,
            client_id=client_id,
            days=days,
            can_manage=can_manage,
            today=today,
            previous_monday=week_start - timedelta(days=7),
            next_monday=week_start + timedelta(days=7),
            current_monday=current_monday,
            staff_summary=staff_summary,
            staff_view=staff_view,
            staff_view_context=staff_view_context,
            publication_state=publication_state,
            schedule_visible=schedule_visible,
        )
    except PermissionError:
        return "Access denied", 403
    except ValueError as error:
        return str(error), 404
    finally:
        conn.close()


@app.route("/schedule/client/<int:client_id>/week/<monday>/pdf")
def schedule_week_pdf(client_id, monday):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            user = _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        try:
            week_start = _parse_schedule_monday(monday)
        except ValueError as error:
            return str(error), 404
        client = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            return "Client not found", 404
        html = render_template(
            "schedule_week_pdf.html",
            client=client,
            client_id=client_id,
            monday=week_start,
            week_end=week_start + timedelta(days=6),
            days=_schedule_week_context(conn, week_start, client_id),
            generated_at=datetime.now(VANCOUVER_TIMEZONE).strftime(
                "%Y-%m-%d %H:%M %Z"
            ),
        )
        try:
            pdf_bytes = _generate_schedule_pdf(html)
        except Exception:
            app.logger.exception("Schedule PDF generation failed.")
            return "Schedule PDF could not be generated.", 500
        response = app.make_response(pdf_bytes)
        response.mimetype = "application/pdf"
        response.headers["Content-Disposition"] = (
            f"inline; filename=\"{_schedule_pdf_filename(client['client_name'], week_start)}\""
        )
        return response
    finally:
        conn.close()


@app.route("/schedule/client/<int:client_id>/week/<monday>/staff-matrix-pdf")
def schedule_staff_matrix_pdf(client_id, monday):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        try:
            week_start = _parse_schedule_monday(monday)
        except ValueError as error:
            return str(error), 404
        client = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            return "Client not found", 404
        html = render_template(
            "schedule_staff_matrix_pdf.html",
            client=client,
            monday=week_start,
            week_end=week_start + timedelta(days=6),
            matrix=_schedule_staff_matrix_context(conn, week_start, client_id),
            generated_at=datetime.now(VANCOUVER_TIMEZONE).strftime(
                "%Y-%m-%d %H:%M %Z"
            ),
        )
        try:
            pdf_bytes = _generate_schedule_pdf(html)
        except Exception:
            app.logger.exception("Staff Matrix PDF generation failed.")
            return "Staff Matrix PDF could not be generated.", 500
        response = app.make_response(pdf_bytes)
        response.mimetype = "application/pdf"
        response.headers["Content-Disposition"] = (
            "inline; filename=\""
            f"NHPSG_Staff_Matrix_{re.sub(r'[^A-Za-z0-9_-]+', '_', client['client_name']).strip('._-') or 'Client'}_"
            f"{week_start.isoformat()}.pdf\""
        )
        return response
    finally:
        conn.close()


@app.route(
    "/schedule/client/<int:client_id>/week/<monday>/copy-previous",
    methods=["POST"],
)
def schedule_copy_previous_week(client_id, monday):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        try:
            user = _schedule_management_user(conn)
        except PermissionError:
            return "Access denied", 403
        try:
            destination_monday = _parse_schedule_monday(monday)
        except ValueError as error:
            flash(str(error))
            return redirect(url_for("schedule_index"))

        client = conn.execute("""
            SELECT client_id, client_name FROM clients
            WHERE client_id = ? AND active = 1
        """, (client_id,)).fetchone()
        if client is None:
            flash("The selected client is not active.")
            return redirect(url_for("schedule_index"))

        current_monday = get_schedule_operational_week_start(
            datetime.now(VANCOUVER_TIMEZONE)
        )
        if destination_monday < current_monday:
            flash("A past schedule week cannot be populated.")
            return redirect(url_for(
                "schedule_week", client_id=client_id,
                monday=destination_monday.isoformat()
            ))

        source_monday = destination_monday - timedelta(days=7)
        destination_sunday = destination_monday + timedelta(days=6)
        source_sunday = source_monday + timedelta(days=6)
        now_utc = serialize_behaviour_utc(
            datetime.now(timezone.utc).replace(microsecond=0)
        )
        try:
            conn.execute("BEGIN IMMEDIATE")
            revalidated_client = conn.execute("""
                SELECT client_id FROM clients
                WHERE client_id = ? AND active = 1
            """, (client_id,)).fetchone()
            if revalidated_client is None:
                conn.rollback()
                flash("The selected client is no longer active.")
                return redirect(url_for("schedule_index"))
            destination_count = conn.execute("""
                SELECT COUNT(*) FROM schedule_shifts
                WHERE client_id = ? AND shift_date BETWEEN ? AND ?
            """, (
                client_id, destination_monday.isoformat(),
                destination_sunday.isoformat(),
            )).fetchone()[0]
            if destination_count:
                conn.rollback()
                flash("The destination week already contains schedule data.")
                return redirect(url_for(
                    "schedule_week", client_id=client_id,
                    monday=destination_monday.isoformat()
                ))

            source_shifts = conn.execute("""
                SELECT * FROM schedule_shifts
                WHERE client_id = ? AND shift_date BETWEEN ? AND ?
                ORDER BY shift_date, schedule_shift_id
            """, (
                client_id, source_monday.isoformat(), source_sunday.isoformat()
            )).fetchall()
            if not source_shifts:
                conn.rollback()
                flash("There is no previous schedule for this client to copy.")
                return redirect(url_for(
                    "schedule_week", client_id=client_id,
                    monday=destination_monday.isoformat()
                ))

            copied_shift_ids = {}
            copied_assignment_count = 0
            for source in source_shifts:
                source_date = date.fromisoformat(source["shift_date"])
                destination_date = source_date + timedelta(days=7)
                cursor = conn.execute("""
                    INSERT INTO schedule_shifts
                    (client_id, shift_date, shift_type, planned_start_time,
                     planned_end_time, status, notes, created_by,
                     created_at_utc, updated_by, updated_at_utc)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    client_id, destination_date.isoformat(), source["shift_type"],
                    source["planned_start_time"], source["planned_end_time"],
                    "Draft", source["notes"], user["user_id"],
                    now_utc, user["user_id"], now_utc,
                ))
                destination_shift_id = cursor.lastrowid
                copied_shift_ids[source["schedule_shift_id"]] = destination_shift_id
                assignments = conn.execute("""
                    SELECT user_id, assignment_note,
                           planned_start_time, planned_end_time
                    FROM schedule_staff
                    WHERE schedule_shift_id = ?
                    ORDER BY schedule_staff_id
                """, (source["schedule_shift_id"],)).fetchall()
                for assignment in assignments:
                    # Legacy assignments may not yet have individual hours;
                    # copy their effective display values without writing NULL.
                    assignment_start = (
                        assignment["planned_start_time"]
                        or source["planned_start_time"]
                    )
                    assignment_end = (
                        assignment["planned_end_time"]
                        or source["planned_end_time"]
                    )
                    conn.execute("""
                        INSERT INTO schedule_staff
                        (schedule_shift_id, user_id, assignment_note,
                         planned_start_time, planned_end_time,
                         assigned_by, assigned_at_utc)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        destination_shift_id, assignment["user_id"],
                        assignment["assignment_note"], assignment_start,
                        assignment_end, user["user_id"], now_utc,
                    ))
                    copied_assignment_count += 1

            first_destination_id = next(iter(copied_shift_ids.values()))
            log_activity(
                conn, "SCHEDULE", "schedule_week_copied",
                f"Schedule week copied for {client['client_name']}",
                user_id=user["user_id"], client_id=client_id, shift_id=None,
                related_table="schedule_shifts", related_id=first_destination_id,
                details=(
                    f"Client: {client['client_name']}\n"
                    f"Source week: {source_monday.isoformat()}\n"
                    f"Destination week: {destination_monday.isoformat()}\n"
                    f"Shifts copied: {len(source_shifts)}\n"
                    f"Staff assignments copied: {copied_assignment_count}"
                ),
                storyline_visible=False,
            )
            conn.commit()
            flash(
                f"Copied {len(source_shifts)} scheduled shift(s) and "
                f"{copied_assignment_count} staff assignment(s)."
            )
            return redirect(url_for(
                "schedule_week", client_id=client_id,
                monday=destination_monday.isoformat()
            ))
        except sqlite3.IntegrityError:
            conn.rollback()
            flash("The previous schedule could not be copied.")
            return redirect(url_for(
                "schedule_week", client_id=client_id,
                monday=destination_monday.isoformat()
            ))
        except Exception:
            conn.rollback()
            return "Schedule copy could not be completed.", 500
    finally:
        conn.close()


@app.route("/schedule/week/<monday>")
def schedule_week_legacy(monday):
    return redirect(url_for("schedule_index"))


@app.route("/behaviour/week/<monday>")
def behaviour_weekly(monday):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        user = get_active_authenticated_user(conn, session["user_id"])
        week_start = _parse_behaviour_monday(monday)
        current_monday = get_behaviour_operational_week_start(
            datetime.now(VANCOUVER_TIMEZONE)
        )
        return render_template("behaviour_weekly.html", monday=week_start,
            days=_behaviour_week_context(conn, week_start),
            previous_monday=week_start - timedelta(days=7),
            next_monday=week_start + timedelta(days=7),
            current_monday=current_monday,
            category_labels=BEHAVIOUR_CATEGORY_LABELS,
            can_void=user["role"] in BEHAVIOUR_VOID_AUTHORITY_ROLES)
    except PermissionError:
        return "Access denied", 403
    except ValueError as error:
        return str(error), 404
    finally:
        conn.close()


@app.route("/behaviour/record", methods=["GET", "POST"])
@app.route("/shift/<int:shift_id>/behaviour", methods=["GET", "POST"])
def behaviour_record(shift_id=None):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        user = get_active_authenticated_user(conn, session["user_id"])
    except PermissionError:
        conn.close()
        return "Access denied", 403

    shift = None
    documentation_context = None
    documentation_context_alternatives = []
    if shift_id is not None:
        if user["role"] != "Support Worker":
            conn.close()
            return "Access denied", 403
        try:
            shift, documentation_context_alternatives = (
                get_worker_documentation_module_context(
                    conn, shift_id, user["user_id"]
                )
            )
        except DocumentationContextUnavailable:
            conn.close()
            return _documentation_context_redirect()
        except PermissionError:
            conn.close()
            return "Access denied", 403
        documentation_context = (
            dict(shift)
            if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
            else None
        )

    if request.method == "GET":
        selected = shift["client_id"] if shift is not None else request.args.get("client_id", type=int)
        response = _render_behaviour_record(
            conn, selected, shift_context=shift is not None,
            documentation_context=documentation_context,
            documentation_context_alternatives=(
                documentation_context_alternatives
            )
        )
        conn.close()
        return response

    values = {}
    try:
        approved_fields = {
            "client_id", "occurrence_local", "repeated_hour_choice", "notes",
            "submission_token", *BEHAVIOUR_CATEGORY_FIELDS
        }
        if shift is not None:
            approved_fields.add("confirm_distinct_episode")
        abc_fields = {
            "record_format", "duration_until_calm_minutes", "calming_description",
            "additional_notes", "antecedent_other_details", "behaviour_other_details",
            "response_other_details", *ABC_ANTECEDENT_FIELDS,
            *ABC_BEHAVIOUR_FIELDS, *ABC_RESPONSE_FIELDS
        }
        is_abc = "record_format" in request.form
        if is_abc:
            approved_fields = {
                "client_id", "occurrence_local", "repeated_hour_choice",
                "submission_token", *abc_fields
            }
            if shift is not None:
                approved_fields.add("confirm_distinct_episode")
        submitted_fields = set(request.form.keys())
        if not submitted_fields.issubset(approved_fields):
            raise ValueError("Behaviour form input is invalid.")
        required_fields = ["occurrence_local", "submission_token"]
        if not is_abc:
            required_fields.insert(1, "notes")
        if shift is None:
            required_fields.insert(0, "client_id")
        for field_name in required_fields:
            if len(request.form.getlist(field_name)) != 1:
                raise ValueError("Behaviour form input is invalid.")
        ambiguity_values = request.form.getlist("repeated_hour_choice")
        if len(ambiguity_values) > 1:
            raise ValueError("Behaviour form input is invalid.")
        values = request.form.to_dict()
        submitted_client = request.form.get("client_id")
        if shift is not None:
            if submitted_client not in (None, "", str(shift["client_id"]),):
                raise ValueError("The shift client cannot be changed.")
            client_id = shift["client_id"]
        else:
            client_id = int(submitted_client or "")
        validate_active_behaviour_client(conn, client_id)
        abc_values = validate_abc_submission(request.form) if is_abc else None
        flags = {}
        for field in BEHAVIOUR_CATEGORY_FIELDS:
            values_for_field = request.form.getlist(field)
            if not values_for_field:
                flags[field] = 0
            elif len(values_for_field) == 1 and values_for_field[0] == "1":
                flags[field] = 1
            else:
                raise ValueError("Behaviour category input is invalid.")
        flags = validate_behaviour_category_flags(flags) if not is_abc else {field: 0 for field in BEHAVIOUR_CATEGORY_FIELDS}
        local_input = request.form.get("occurrence_local", "")
        ambiguity_choice = ambiguity_values[0] if ambiguity_values else ""
        if is_vancouver_occurrence_input_ambiguous(local_input):
            if ambiguity_choice not in ("first", "second"):
                raise ValueError("Repeated Vancouver times require a first or second choice.")
        elif ambiguity_choice:
            raise ValueError("Ambiguity choice is only allowed for a repeated Vancouver time.")
        occurrence_utc = convert_vancouver_occurrence_input_to_utc(
            local_input, ambiguity_choice or None
        )
        notes = request.form.get("notes", "").strip() or None
        if notes and len(notes) > BEHAVIOUR_NOTES_MAX_LENGTH:
            raise ValueError("Behaviour notes cannot exceed 2,000 characters.")
        token = request.form.get("submission_token", "")
        if not BEHAVIOUR_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Behaviour submission token is invalid.")
        if shift_id is not None:
            shift, documentation_context_alternatives = (
                get_worker_documentation_module_context(
                    conn, shift_id, user["user_id"]
                )
            )
            client_id = shift["client_id"]
        confirmation_values = request.form.getlist("confirm_distinct_episode")
        if len(confirmation_values) > 1 or (
            confirmation_values and confirmation_values[0] != "1"
        ):
            raise ValueError("Behaviour confirmation is invalid.")
        confirmed_distinct_episode = confirmation_values == ["1"]
        recorded_utc = serialize_behaviour_utc(datetime.now(timezone.utc).replace(microsecond=0))
        conn.execute("BEGIN IMMEDIATE")
        try:
            prior_episode = None
            if shift_id is not None:
                prior_episode = conn.execute("""
                    SELECT bo.occurred_at_utc, u.full_name AS recorder_name
                    FROM behaviour_occurrences bo
                    JOIN users u ON u.user_id = bo.recorded_by_user_id
                    WHERE bo.shift_id = ? AND bo.client_id = ?
                      AND bo.status != 'Voided'
                    ORDER BY bo.occurred_at_utc DESC,
                             bo.behaviour_occurrence_id DESC
                    LIMIT 1
                """, (shift_id, client_id)).fetchone()
            if prior_episode is not None and not confirmed_distinct_episode:
                conn.rollback()
                duplicate_warning = {
                    "local_time": behaviour_utc_to_vancouver(
                        prior_episode["occurred_at_utc"]
                    ).strftime("%Y-%m-%d %I:%M %p"),
                    "recorder_name": prior_episode["recorder_name"]
                }
                response = _render_behaviour_record(
                    conn, client_id, values=values,
                    shift_context=True, submission_token=token,
                    duplicate_warning=duplicate_warning,
                    documentation_context=documentation_context,
                    documentation_context_alternatives=(
                        documentation_context_alternatives
                    )
                )
                conn.close()
                return response
            if is_abc:
                columns = ["client_id", "shift_id", "occurred_at_utc", "record_format"]
                columns += list(BEHAVIOUR_CATEGORY_FIELDS) + list(ABC_ANTECEDENT_FIELDS)
                columns += list(ABC_BEHAVIOUR_FIELDS) + list(ABC_RESPONSE_FIELDS)
                columns += ["antecedent_other_details", "behaviour_other_details",
                            "response_other_details", "duration_until_calm_minutes",
                            "calming_description", "additional_notes",
                            "recorded_by_user_id", "recorded_at_utc", "submission_token"]
                values_to_store = [client_id, shift_id, occurrence_utc, "ABC"]
                values_to_store += [flags[field] for field in BEHAVIOUR_CATEGORY_FIELDS]
                values_to_store += [abc_values[field] for field in ABC_ANTECEDENT_FIELDS]
                values_to_store += [abc_values[field] for field in ABC_BEHAVIOUR_FIELDS]
                values_to_store += [abc_values[field] for field in ABC_RESPONSE_FIELDS]
                values_to_store += [abc_values[field] for field in (
                    "antecedent_other_details", "behaviour_other_details",
                    "response_other_details", "duration_until_calm_minutes",
                    "calming_description", "additional_notes")]
                values_to_store += [user["user_id"], recorded_utc, token]
                cur = conn.execute(
                    f"INSERT INTO behaviour_occurrences ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    values_to_store
                )
            else:
                cur = conn.execute("""
                    INSERT INTO behaviour_occurrences
                    (client_id, shift_id, occurred_at_utc, aggression_towards_others,
                     injury_to_others, self_harm, injury_to_self, property_damage,
                     notes, recorded_by_user_id, recorded_at_utc, submission_token)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (client_id, shift_id, occurrence_utc, *(flags[field] for field in BEHAVIOUR_CATEGORY_FIELDS),
                      notes, user["user_id"], recorded_utc, token))
            occurrence_id = cur.lastrowid
            category_text = ", ".join(BEHAVIOUR_CATEGORY_LABELS[field] for field in BEHAVIOUR_CATEGORY_FIELDS if flags[field])
            activity_details = (
                format_abc_behaviour_storyline_details(abc_values)
                if is_abc else format_behaviour_storyline_details(category_text, notes)
            )
            log_activity(conn, "BEHAVIOUR", "behaviour_occurrence_created",
                "Behaviour occurrence recorded", user_id=user["user_id"], client_id=client_id,
                shift_id=shift_id,
                related_table="behaviour_occurrences", related_id=occurrence_id,
                details=activity_details, success=1,
                event_datetime=occurrence_utc,
                storyline_visible=True)
            conn.commit()
        except sqlite3.IntegrityError as error:
            conn.rollback()
            if "UNIQUE constraint failed: behaviour_occurrences.submission_token" not in str(error):
                raise
            existing = conn.execute("SELECT occurred_at_utc, recorded_by_user_id FROM behaviour_occurrences WHERE submission_token = ?", (token,)).fetchone()
            if existing and existing["recorded_by_user_id"] == user["user_id"]:
                week = get_behaviour_operational_week_start(behaviour_utc_to_vancouver(existing["occurred_at_utc"]))
                conn.close()
                return redirect(url_for("behaviour_weekly", monday=week.isoformat()))
            raise ValueError("Behaviour submission token is invalid.")
        if shift_id is not None:
            flash("Behaviour occurrence recorded.")
            conn.close()
            return redirect(url_for("shift_dashboard", shift_id=shift_id))
        week = get_behaviour_operational_week_start(behaviour_utc_to_vancouver(occurrence_utc))
        conn.close()
        return redirect(url_for("behaviour_weekly", monday=week.isoformat()))
    except DocumentationContextUnavailable:
        if conn.in_transaction:
            conn.rollback()
        conn.close()
        return _documentation_context_redirect()
    except (ValueError, PermissionError) as error:
        selected_client = (
            shift["client_id"] if shift is not None
            else request.form.get("client_id", type=int)
        )
        response = _render_behaviour_record(
            conn, selected_client, str(error), values,
            shift_context=shift is not None,
            submission_token=values.get("submission_token"),
            documentation_context=documentation_context,
            documentation_context_alternatives=(
                documentation_context_alternatives
            )
        )
        conn.close()
        return response, 400
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        conn.close()
        raise


@app.route("/behaviour/occurrences/<int:occurrence_id>/void", methods=["POST"])
def behaviour_occurrence_void(occurrence_id):
    """Void one incorrect Behaviour occurrence without changing its original data."""
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        if (set(request.form.keys()) != {"void_reason"} or
                len(request.form.getlist("void_reason")) != 1):
            raise ValueError("Behaviour void input is invalid.")
        void_reason = request.form["void_reason"].strip()
        if not void_reason:
            raise ValueError("A behaviour void reason is required.")

        conn.execute("BEGIN IMMEDIATE")
        try:
            actor = validate_behaviour_void_authority(conn, session["user_id"])
            occurrence = conn.execute("""
                SELECT behaviour_occurrence_id, client_id, occurred_at_utc, status,
                       aggression_towards_others, injury_to_others, self_harm,
                       injury_to_self, property_damage
                FROM behaviour_occurrences
                WHERE behaviour_occurrence_id = ?
            """, (occurrence_id,)).fetchone()
            if occurrence is None:
                raise LookupError("Behaviour occurrence not found.")
            if occurrence["status"] != "Recorded":
                raise RuntimeError("Behaviour occurrence has already been voided.")

            voided_at_utc = serialize_behaviour_utc(
                datetime.now(timezone.utc).replace(microsecond=0)
            )
            updated = conn.execute("""
                UPDATE behaviour_occurrences
                SET status = 'Voided', voided_by_user_id = ?,
                    voided_at_utc = ?, void_reason = ?
                WHERE behaviour_occurrence_id = ? AND status = 'Recorded'
            """, (actor["user_id"], voided_at_utc, void_reason, occurrence_id))
            if updated.rowcount != 1:
                raise RuntimeError("Behaviour occurrence has already been voided.")

            categories = ", ".join(
                BEHAVIOUR_CATEGORY_LABELS[field]
                for field in BEHAVIOUR_CATEGORY_FIELDS if occurrence[field]
            )
            log_activity(
                conn, "BEHAVIOUR", "behaviour_occurrence_voided",
                "Behaviour occurrence voided", user_id=actor["user_id"],
                client_id=occurrence["client_id"],
                related_table="behaviour_occurrences",
                related_id=occurrence_id,
                details="Status: Voided", success=1, storyline_visible=True
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise

        week = get_behaviour_operational_week_start(
            behaviour_utc_to_vancouver(occurrence["occurred_at_utc"])
        )
        return redirect(url_for("behaviour_weekly", monday=week.isoformat()))
    except PermissionError:
        return "Access denied", 403
    except LookupError as error:
        return str(error), 404
    except RuntimeError as error:
        return str(error), 409
    except ValueError as error:
        return str(error), 400
    finally:
        conn.close()

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
    success=1,
    storyline_visible=False,
    event_datetime=None
):
    
    local_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    values = (
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
        success,
    )
    if event_datetime is not None:
        event_datetime = serialize_behaviour_utc(
            parse_behaviour_utc(event_datetime)
            if isinstance(event_datetime, str) else event_datetime
        )
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(activity_log)").fetchall()
    }
    if "event_datetime" in columns and "storyline_visible" in columns:
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_class, activity_type, user_id,
             client_id, shift_id, related_table, related_id, summary,
             details, success, storyline_visible, event_datetime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, values + (1 if storyline_visible else 0, event_datetime))
    elif "storyline_visible" in columns:
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_class, activity_type, user_id,
             client_id, shift_id, related_table, related_id, summary,
             details, success, storyline_visible)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, values + (1 if storyline_visible else 0,))
    else:
        conn.execute("""
            INSERT INTO activity_log
            (activity_datetime, activity_class, activity_type, user_id,
             client_id, shift_id, related_table, related_id, summary,
             details, success)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, values)

#####################################################################
# STAFF NOTICES FOUNDATION HELPERS
#####################################################################

STAFF_NOTICE_MANAGEMENT_ROLES = frozenset({
    "Admin",
    "Program Manager",
    "Director"
})

SHIFT_AUTO_SIGN_ON_ROLES = frozenset({
    "Support Worker"
})

SHIFT_CANCELLED_STATUS = "Cancelled"
SHIFT_CANCELLATION_REASON_CODE = "Shift Cancelled"
POST_SHIFT_DOCUMENTATION_WINDOW = timedelta(hours=4)
DOCUMENTATION_CONTEXT_SESSION_KEY = "documentation_shift_id"

DOCUMENTATION_ACCESS_ACTIVE = "active_assignment"
DOCUMENTATION_ACCESS_POST_SHIFT = "post_shift"


class ShiftCancellationConflictError(RuntimeError):
    pass


class UserLifecycleConflictError(RuntimeError):
    pass


def _shift_is_cancelled(shift):
    return shift is not None and shift["status"] == SHIFT_CANCELLED_STATUS


def _documentation_utc_value(value):
    """Return a valid UTC datetime or None without granting access."""
    try:
        return parse_staff_notice_utc_datetime(value)
    except (TypeError, ValueError):
        return None


def get_worker_documentation_assignments(
    conn,
    user_id,
    now_utc=None
):
    """Return all shifts a Support Worker may document against.

    This helper is deliberately read-only and is separate from lifecycle
    authorization.  It uses the worker's own shift_staff assignment as the
    authority, so a parent shift may remain open for another worker.
    """
    user = get_active_authenticated_user(conn, user_id)
    if user["role"] != "Support Worker":
        return []

    current_utc = _documentation_utc_value(
        now_utc if now_utc is not None else get_application_now_utc()
    )
    if current_utc is None:
        return []

    rows = conn.execute("""
        SELECT
            s.shift_id,
            s.client_id,
            c.client_name,
            s.shift_date,
            s.shift_type,
            s.status AS shift_status,
            ss.shift_staff_id,
            ss.user_id,
            ss.active AS assignment_active,
            ss.actual_start_time,
            ss.sign_on_at,
            s.scheduled_end_time,
            ss.actual_end_at_utc,
            ss.sign_off_at
        FROM shift_staff ss
        JOIN shifts s ON s.shift_id = ss.shift_id
        JOIN clients c ON c.client_id = s.client_id
        WHERE ss.user_id = ?
          AND c.active = 1
          AND s.status <> ?
        ORDER BY s.shift_date DESC, s.shift_id DESC,
                 ss.shift_staff_id DESC
    """, (user["user_id"], SHIFT_CANCELLED_STATUS)).fetchall()

    eligible = []
    for row in rows:
        assignment = dict(row)
        has_work_evidence = (
            isinstance(assignment["actual_start_time"], str)
            and bool(assignment["actual_start_time"].strip())
            and assignment["sign_on_at"] is not None
        )
        if not has_work_evidence:
            continue

        if assignment["assignment_active"] == 1:
            if assignment["actual_end_at_utc"] is not None:
                continue
            assignment["documentation_access"] = (
                DOCUMENTATION_ACCESS_ACTIVE
            )
            assignment["documentation_until_utc"] = None
            eligible.append(assignment)
            continue

        if assignment["actual_end_at_utc"] is None:
            continue
        if assignment["sign_off_at"] is None:
            continue

        completed_at_utc = _documentation_utc_value(
            assignment["actual_end_at_utc"]
        )
        if completed_at_utc is None:
            continue

        documentation_until_utc = (
            completed_at_utc + POST_SHIFT_DOCUMENTATION_WINDOW
        )
        if current_utc > documentation_until_utc:
            continue

        assignment["documentation_access"] = (
            DOCUMENTATION_ACCESS_POST_SHIFT
        )
        assignment["documentation_until_utc"] = documentation_until_utc
        eligible.append(assignment)

    return eligible


def get_worker_documentation_shift_context(
    conn,
    shift_id,
    user_id,
    now_utc=None
):
    """Return one exact documentation context, or None if unauthorized."""
    matches = [
        assignment
        for assignment in get_worker_documentation_assignments(
            conn, user_id, now_utc=now_utc
        )
        if assignment["shift_id"] == shift_id
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def can_worker_document_shift(conn, shift_id, user_id, now_utc=None):
    """Return whether the worker may document this exact shift."""
    return get_worker_documentation_shift_context(
        conn, shift_id, user_id, now_utc=now_utc
    ) is not None


def _documentation_context_date(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return ""
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _documentation_context_time(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError:
        return ""
    return parsed.strftime("%I:%M %p").lstrip("0")


def _documentation_context_view(assignment):
    """Return worker-safe presentation data derived from an assignment."""
    context = dict(assignment)
    is_active = (
        context["documentation_access"] == DOCUMENTATION_ACCESS_ACTIVE
    )
    context["period_label"] = "Current" if is_active else "Previous"
    context["date_display"] = _documentation_context_date(
        context.get("shift_date")
    )

    start_display = _documentation_context_time(
        context.get("actual_start_time")
    )
    end_display = _documentation_context_time(
        context.get("scheduled_end_time")
    )
    if not is_active:
        completed_at = _documentation_utc_value(
            context.get("actual_end_at_utc")
        )
        if completed_at is not None:
            end_display = completed_at.astimezone(
                VANCOUVER_TIMEZONE
            ).strftime("%I:%M %p").lstrip("0")
    context["time_display"] = (
        f"{start_display} - {end_display}"
        if start_display and end_display
        else ""
    )
    context["shift_label"] = (
        f"{context['period_label']} {context['shift_type']} Shift"
    )
    return context


def get_worker_documentation_context_state(
    conn,
    user_id,
    selected_shift_id=None,
    now_utc=None
):
    """Resolve the selected context and alternatives authoritatively."""
    assignments = get_worker_documentation_assignments(
        conn, user_id, now_utc=now_utc
    )
    selected = next(
        (
            assignment
            for assignment in assignments
            if selected_shift_id is not None
            and assignment["shift_id"] == selected_shift_id
        ),
        None
    )
    return {
        "selected": (
            _documentation_context_view(selected) if selected else None
        ),
        "available": [
            _documentation_context_view(assignment)
            for assignment in assignments
        ],
        "has_active": any(
            assignment["documentation_access"] == DOCUMENTATION_ACCESS_ACTIVE
            for assignment in assignments
        ),
    }


def _session_documentation_shift_id():
    value = session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _store_documentation_shift_id(shift_id):
    session[DOCUMENTATION_CONTEXT_SESSION_KEY] = int(shift_id)


def _clear_documentation_shift_id():
    session.pop(DOCUMENTATION_CONTEXT_SESSION_KEY, None)


class DocumentationContextUnavailable(PermissionError):
    """The selected worker documentation context is no longer usable."""


class AuthorizedCancelledHousekeepingEntry(LookupError):
    """An authorized owner's Housekeeping entry belongs to a cancelled shift."""


def _documentation_context_redirect():
    _clear_documentation_shift_id()
    flash(
        "Your documentation shift is no longer available. "
        "Please choose another shift."
    )
    return redirect(url_for("documentation_context"))


def get_worker_documentation_module_context(
    conn,
    requested_shift_id,
    user_id,
    active_context_loader=None
):
    """Resolve one worker module context without granting session authority."""
    actor = get_active_authenticated_user(conn, user_id)
    if actor["role"] != "Support Worker":
        raise PermissionError(
            "Only an active Support Worker may record documentation."
        )

    raw_selected_id = session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
    selected_id = _session_documentation_shift_id()
    if raw_selected_id is not None and selected_id is None:
        _clear_documentation_shift_id()
        raise DocumentationContextUnavailable()

    if selected_id is not None:
        if requested_shift_id != selected_id:
            raise DocumentationContextUnavailable()
        state = get_worker_documentation_context_state(
            conn,
            actor["user_id"],
            selected_shift_id=selected_id
        )
        selected = state["selected"]
        if selected is None:
            _clear_documentation_shift_id()
            raise DocumentationContextUnavailable()
        context = dict(selected)
        context["status"] = context.get("shift_status")
        context["recorded_by_user_id"] = actor["user_id"]
        context["editable"] = True
        alternatives = [
            candidate
            for candidate in state["available"]
            if candidate["shift_id"] != selected_id
        ]
        return context, alternatives

    if active_context_loader is not None:
        return active_context_loader(
            conn, requested_shift_id, actor["user_id"]
        ), []

    context = conn.execute("""
        SELECT
            s.*,
            c.client_name,
            c.active AS client_active,
            ss.shift_staff_id,
            ss.active AS assignment_active,
            ? AS recorded_by_user_id,
            1 AS editable
        FROM shifts s
        JOIN clients c ON c.client_id = s.client_id
        JOIN shift_staff ss
          ON ss.shift_id = s.shift_id
         AND ss.user_id = ?
        WHERE s.shift_id = ?
          AND s.status = 'Open'
          AND c.active = 1
          AND ss.active = 1
        LIMIT 1
    """, (actor["user_id"], actor["user_id"], requested_shift_id)).fetchone()
    if context is None:
        raise PermissionError(
            "Active participation in this open shift is required."
        )
    return dict(context), []


def can_edit_shared_shift_note(conn, shift, user_id):
    if shift is None or shift["status"] != "Open":
        return False

    actor = get_active_authenticated_user(conn, user_id)
    if actor["role"] in STAFF_NOTICE_MANAGEMENT_ROLES:
        return True

    return conn.execute("""
        SELECT 1
        FROM shift_staff
        WHERE shift_id = ?
          AND user_id = ?
          AND active = 1
        LIMIT 1
    """, (shift["shift_id"], actor["user_id"])).fetchone() is not None


def get_shift_activity_context(conn, shift_id, user_id):
    """Return Activity context and whether the worker may append entries."""
    actor = get_active_authenticated_user(conn, user_id)
    if actor["role"] != "Support Worker":
        raise PermissionError(
            "Only an active Support Worker may record shift activities."
        )

    context = conn.execute("""
        SELECT
            s.shift_id,
            s.client_id,
            s.shift_date,
            s.shift_type,
            s.status AS shift_status,
            c.client_name,
            c.active AS client_active,
            EXISTS (
                SELECT 1
                FROM shift_staff ss
                WHERE ss.shift_id = s.shift_id
                  AND ss.user_id = ?
                  AND ss.active = 1
            ) AS has_active_assignment
        FROM shifts s
        JOIN clients c ON c.client_id = s.client_id
        WHERE s.shift_id = ?
    """, (actor["user_id"], shift_id)).fetchone()

    if context is None:
        raise LookupError("Shift not found.")

    context = dict(context)
    context["recorded_by_user_id"] = actor["user_id"]
    context["editable"] = bool(
        context["shift_status"] == "Open"
        and context["client_active"] == 1
        and context["has_active_assignment"] == 1
    )
    return context


def get_applicable_care_tasks(conn, shift):
    """Return active Care routines applicable to one exact shift."""
    return conn.execute("""
        SELECT *
        FROM care_tasks
        WHERE active = 1
          AND occurs LIKE ?
        ORDER BY task_name
    """, (f"%{shift['shift_type']}%",)).fetchall()


def get_applicable_housekeeping_tasks(conn, shift):
    """Return active Housekeeping tasks applicable to one exact shift."""
    return conn.execute("""
        SELECT *
        FROM housekeeping_tasks
        WHERE active = 1
          AND occurs LIKE ?
        ORDER BY task_name
    """, (f"%{shift['shift_type']}%",)).fetchall()


def get_care_active_documentation_context(conn, shift_id, user_id):
    """Resolve an exact active Care documentation context."""
    context = get_worker_documentation_shift_context(
        conn,
        shift_id,
        user_id
    )
    if context is None:
        raise PermissionError(
            "Active participation in this open shift is required."
        )
    if context["documentation_access"] != DOCUMENTATION_ACCESS_ACTIVE:
        raise PermissionError(
            "An explicit documentation context is required for this shift."
        )
    context = _documentation_context_view(context)
    context["status"] = context.get("shift_status")
    context["recorded_by_user_id"] = user_id
    context["editable"] = True
    return context


def get_housekeeping_active_documentation_context(
    conn,
    shift_id,
    user_id
):
    """Resolve an exact active Housekeeping documentation context."""
    context = get_worker_documentation_shift_context(
        conn,
        shift_id,
        user_id
    )
    if context is None:
        raise PermissionError(
            "Active participation in this open shift is required."
        )
    if context["documentation_access"] != DOCUMENTATION_ACCESS_ACTIVE:
        raise PermissionError(
            "An explicit documentation context is required for this shift."
        )
    context = _documentation_context_view(context)
    context["status"] = context.get("shift_status")
    context["recorded_by_user_id"] = user_id
    context["editable"] = True
    return context


def get_housekeeping_edit_context(conn, shift_id, entry_id, user_id):
    """Resolve an authorized Current-context Housekeeping edit."""
    raw_selected_id = session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
    selected_id = _session_documentation_shift_id()
    if raw_selected_id is None or selected_id != shift_id:
        raise DocumentationContextUnavailable()

    actor = get_active_authenticated_user(conn, user_id)
    if actor["role"] != "Support Worker":
        raise PermissionError(
            "Only an active Support Worker may edit documentation."
        )

    entry = conn.execute("""
        SELECT
            shte.*,
            ht.task_name,
            ht.comment_required_attempted,
            ht.comment_required_not_completed,
            s.client_id,
            s.status AS shift_status,
            c.client_name,
            c.active AS client_active
        FROM shift_housekeeping_task_entries shte
        JOIN housekeeping_tasks ht
          ON ht.housekeeping_task_id = shte.housekeeping_task_id
        JOIN shifts s
          ON s.shift_id = shte.shift_id
        JOIN clients c
          ON c.client_id = s.client_id
        WHERE shte.entry_id = ?
          AND shte.shift_id = ?
          AND shte.completed_by_user_id = ?
    """, (entry_id, shift_id, actor["user_id"])).fetchone()
    if (
        entry is not None
        and entry["shift_status"] == SHIFT_CANCELLED_STATUS
    ):
        assignment = conn.execute("""
            SELECT 1
            FROM shift_staff
            WHERE shift_id = ?
              AND user_id = ?
              AND active = 1
              AND actual_start_time IS NOT NULL
              AND TRIM(actual_start_time) <> ''
              AND sign_on_at IS NOT NULL
              AND actual_end_at_utc IS NULL
            LIMIT 1
        """, (shift_id, actor["user_id"])).fetchone()
        if entry["client_active"] == 1 and assignment is not None:
            raise AuthorizedCancelledHousekeepingEntry()

    context, _ = get_worker_documentation_module_context(
        conn,
        shift_id,
        user_id,
        active_context_loader=get_housekeeping_active_documentation_context
    )
    if context["documentation_access"] != DOCUMENTATION_ACCESS_ACTIVE:
        raise DocumentationContextUnavailable()

    entry = conn.execute("""
        SELECT
            shte.*,
            ht.task_name,
            ht.comment_required_attempted,
            ht.comment_required_not_completed,
            s.client_id,
            s.status AS shift_status,
            c.client_name,
            c.active AS client_active
        FROM shift_housekeeping_task_entries shte
        JOIN housekeeping_tasks ht
          ON ht.housekeeping_task_id = shte.housekeeping_task_id
        JOIN shifts s
          ON s.shift_id = shte.shift_id
        JOIN clients c
          ON c.client_id = s.client_id
        WHERE shte.entry_id = ?
          AND shte.shift_id = ?
          AND shte.completed_by_user_id = ?
    """, (
        entry_id,
        shift_id,
        context["recorded_by_user_id"]
    )).fetchone()
    if entry is None or entry["client_id"] != context["client_id"]:
        raise LookupError("Housekeeping task entry not found")
    if entry["shift_status"] == SHIFT_CANCELLED_STATUS:
        raise AuthorizedCancelledHousekeepingEntry()
    return context, entry


def require_active_shift_activity_context(conn, shift_id, user_id):
    """Return authoritative context for an Activity V1 append."""
    context = get_shift_activity_context(conn, shift_id, user_id)
    if not context["editable"]:
        raise PermissionError(
            "Active participation in this open shift is required."
        )
    return context


def get_shift_activity_entries(conn, shift_id):
    return conn.execute("""
        SELECT
            sa.shift_activity_id,
            sa.shift_id,
            sa.recorded_by_user_id,
            sa.start_time,
            sa.end_time,
            sa.a_selected,
            sa.t_selected,
            sa.ls_selected,
            sa.activity_description,
            sa.created_at,
            u.full_name AS recorded_by_name
        FROM shift_activities sa
        JOIN users u ON u.user_id = sa.recorded_by_user_id
        WHERE sa.shift_id = ?
        ORDER BY sa.created_at ASC, sa.shift_activity_id ASC
    """, (shift_id,)).fetchall()


def format_food_fluid_storyline_summary(interaction_type, item_description):
    interaction = (interaction_type or "Food & Fluid").strip()
    item = (item_description or "").strip()
    return f"{interaction} — {item}" if item else interaction


def format_food_fluid_storyline_details(outcome, additional_details=None, physically_thrown=0):
    lines = [f"Outcome: {outcome or 'Not recorded'}"]
    if additional_details and additional_details.strip():
        lines.append(f"Additional details: {additional_details.strip()}")
    if physically_thrown:
        lines.append("Physically thrown")
    return "\n".join(lines)


def format_food_fluid_void_storyline_details(outcome, void_reason):
    return "\n".join((
        f"Original outcome: {outcome or 'Not recorded'}",
        f"Void reason: {void_reason or 'Not recorded'}",
    ))


def parse_shift_activity_form(form):
    allowed_fields = {
        "start_time",
        "end_time",
        "activity_description",
        *SHIFT_ACTIVITY_CATEGORY_FIELDS,
    }
    if not set(form).issubset(allowed_fields):
        raise ValueError("Activity form input is invalid.")

    for field_name in (
        "start_time",
        "end_time",
        "activity_description",
    ):
        if len(form.getlist(field_name)) != 1:
            raise ValueError("Activity form input is invalid.")

    values = {
        "start_time": form["start_time"].strip(
            SHIFT_ACTIVITY_ASCII_WHITESPACE
        ),
        "end_time": form["end_time"].strip(
            SHIFT_ACTIVITY_ASCII_WHITESPACE
        ),
        "activity_description": form["activity_description"].strip(
            SHIFT_ACTIVITY_ASCII_WHITESPACE
        ),
    }

    for field_name in SHIFT_ACTIVITY_CATEGORY_FIELDS:
        submitted = form.getlist(field_name)
        if not submitted:
            values[field_name] = 0
        elif submitted == ["1"]:
            values[field_name] = 1
        else:
            raise ValueError("Activity category input is invalid.")

    parsed_times = {}
    for field_name in ("start_time", "end_time"):
        try:
            parsed = datetime.strptime(values[field_name], "%H:%M")
        except ValueError as error:
            raise ValueError(
                "Activity times must use HH:MM."
            ) from error
        if parsed.strftime("%H:%M") != values[field_name]:
            raise ValueError("Activity times must use HH:MM.")
        parsed_times[field_name] = parsed

    if parsed_times["end_time"] <= parsed_times["start_time"]:
        raise ValueError("Activity end time must be later than start time.")
    if not any(values[field] for field in SHIFT_ACTIVITY_CATEGORY_FIELDS):
        raise ValueError("At least one Activity category is required.")
    if not values["activity_description"]:
        raise ValueError("Activity description is required.")

    return values


def get_activity_management_actor(conn, user_id):
    actor = get_active_authenticated_user(conn, user_id)
    if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
        raise PermissionError(
            "Current user is not allowed to review Activities."
        )
    return actor


def _cancelled_shift_response():
    return "Cancelled shifts are historical and cannot be changed.", 409


def _shift_assignment_has_start_or_completion_evidence(assignment):
    return any((
        assignment["sign_on_at"] is not None,
        bool(
            isinstance(assignment["actual_start_time"], str)
            and assignment["actual_start_time"].strip()
        ),
        assignment["actual_end_time"] is not None,
        assignment["actual_end_at_utc"] is not None,
        assignment["sign_off_at"] is not None,
        assignment["start_checklist_completed"] not in (None, 0),
        assignment["end_checklist_completed"] not in (None, 0)
    ))


def _shift_cancellation_audits(conn, shift_id):
    return conn.execute("""
        SELECT *
        FROM activity_log
        WHERE activity_class = 'SHIFT'
          AND activity_type = 'shift_cancelled'
          AND related_table = 'shifts'
          AND related_id = ?
        ORDER BY activity_id
    """, (shift_id,)).fetchall()


def _shift_cancellation_audit_is_consistent(audit, shift):
    return (
        audit["user_id"] is not None
        and audit["client_id"] == shift["client_id"]
        and audit["shift_id"] == shift["shift_id"]
        and audit["related_table"] == "shifts"
        and audit["related_id"] == shift["shift_id"]
        and audit["summary"] == "Shift cancelled"
        and audit["success"] == 1
        and isinstance(audit["details"], str)
        and f"Shift ID: {shift['shift_id']};" in audit["details"]
        and f"Actor User ID: {audit['user_id']};" in audit["details"]
        and f"Client ID: {shift['client_id']};" in audit["details"]
        and "Reason: " in audit["details"]
        and "Effective at UTC: " in audit["details"]
        and "Deactivated assignment IDs: " in audit["details"]
        and "Deactivated assignment count: " in audit["details"]
    )


def cancel_shift_in_transaction(
    conn,
    shift_id,
    actor_user_id,
    reason,
    cancelled_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Shift cancellation requires an active transaction."
        )
    if not _is_valid_staff_notice_identifier(shift_id):
        raise ValueError("A valid shift is required.")
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise PermissionError("An active authorized manager is required.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A cancellation reason is required.")

    reason = reason.strip()
    cancelled_at_utc = format_staff_notice_utc_datetime(
        cancelled_at_utc
    )
    actor = get_active_authenticated_user(conn, actor_user_id)
    if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
        raise PermissionError(
            "Current user is not allowed to cancel shifts."
        )

    shift_row = conn.execute("""
        SELECT *
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()
    if shift_row is None:
        raise LookupError("Shift not found.")
    shift = dict(shift_row)

    cancellation_audits = _shift_cancellation_audits(conn, shift_id)
    if shift["status"] == SHIFT_CANCELLED_STATUS:
        if (
            len(cancellation_audits) == 1
            and _shift_cancellation_audit_is_consistent(
                cancellation_audits[0],
                shift
            )
        ):
            return {
                "cancelled": 0,
                "assignments_deactivated": 0,
                "assignment_ids": (),
                "occurrences_cancelled": 0,
                "deliveries_cancelled": 0,
                "delivery_access_revoked": 0
            }
        raise ShiftCancellationConflictError(
            "The cancelled shift has inconsistent cancellation history."
        )
    if shift["status"] != "Open":
        raise ShiftCancellationConflictError(
            "Only an open shift can be cancelled."
        )
    if cancellation_audits:
        raise ShiftCancellationConflictError(
            "The open shift has inconsistent cancellation history."
        )
    if shift["actual_end_at_utc"] is not None:
        raise ShiftCancellationConflictError(
            "A completed shift cannot be cancelled."
        )

    assignments = [
        dict(row)
        for row in conn.execute("""
            SELECT *
            FROM shift_staff
            WHERE shift_id = ?
            ORDER BY shift_staff_id
        """, (shift_id,)).fetchall()
    ]
    if any(
        _shift_assignment_has_start_or_completion_evidence(assignment)
        for assignment in assignments
    ):
        raise ShiftCancellationConflictError(
            "A shift with genuine start or completion evidence "
            "cannot be cancelled."
        )

    cursor = conn.execute("""
        UPDATE shifts
        SET status = 'Cancelled'
        WHERE shift_id = ?
          AND status = 'Open'
          AND actual_end_at_utc IS NULL
    """, (shift_id,))
    if cursor.rowcount != 1:
        raise ShiftCancellationConflictError(
            "The shift changed while cancellation was being recorded."
        )

    deactivated_assignment_ids = []
    for assignment in assignments:
        if assignment["active"] != 1:
            continue
        cursor = conn.execute("""
            UPDATE shift_staff
            SET active = 0
            WHERE shift_staff_id = ?
              AND active = 1
              AND sign_on_at IS NULL
              AND (
                  actual_start_time IS NULL
                  OR TRIM(actual_start_time) = ''
              )
              AND actual_end_time IS NULL
              AND actual_end_at_utc IS NULL
              AND sign_off_at IS NULL
              AND COALESCE(start_checklist_completed, 0) = 0
              AND COALESCE(end_checklist_completed, 0) = 0
        """, (assignment["shift_staff_id"],))
        if cursor.rowcount != 1:
            raise ShiftCancellationConflictError(
                "A shift assignment changed while cancellation "
                "was being recorded."
            )
        deactivated_assignment_ids.append(
            assignment["shift_staff_id"]
        )

    exact_shift_count = conn.execute("""
        SELECT COUNT(*) AS shift_count
        FROM shifts
        WHERE client_id = ?
          AND shift_date = ?
          AND shift_type = ?
    """, (
        shift["client_id"],
        shift["shift_date"],
        shift["shift_type"]
    )).fetchone()["shift_count"]

    occurrence_parameters = [shift_id]
    unbound_clause = ""
    if exact_shift_count == 1:
        unbound_clause = """
            OR (
                o.shift_id IS NULL
                AND o.planned_client_id = ?
                AND o.occurrence_date = ?
                AND o.planned_shift_type = ?
            )
        """
        occurrence_parameters.extend((
            shift["client_id"],
            shift["shift_date"],
            shift["shift_type"]
        ))

    occurrences = [
        dict(row)
        for row in conn.execute(f"""
            SELECT
                o.*,
                sn.notice_id,
                sn.title,
                sn.client_id
            FROM staff_notice_occurrences o
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            JOIN staff_notices sn
                ON sns.notice_id = sn.notice_id
            WHERE sn.status = 'Published'
              AND (
                  o.shift_id = ?
                  {unbound_clause}
              )
              AND o.occurrence_status IN (
                  'Pending Shift',
                  'Scheduled',
                  'Active',
                  'No Shift Occurred'
              )
            ORDER BY o.occurrence_id
        """, tuple(occurrence_parameters)).fetchall()
    ]

    occurrences_cancelled = 0
    occurrence_ids = []
    for occurrence in occurrences:
        previous_status = occurrence["occurrence_status"]
        if occurrence["shift_id"] is None:
            bind_cursor = conn.execute("""
                UPDATE staff_notice_occurrences
                SET shift_id = ?,
                    shift_bound_at_utc = ?
                WHERE occurrence_id = ?
                  AND shift_id IS NULL
            """, (
                shift_id,
                cancelled_at_utc,
                occurrence["occurrence_id"]
            ))
            if bind_cursor.rowcount != 1:
                raise ShiftCancellationConflictError(
                    "A Staff Notice occurrence changed during "
                    "shift cancellation."
                )
            _log_staff_notice_occurrence_bound(
                conn,
                occurrence,
                occurrence["occurrence_id"],
                shift_id
            )

        cursor = conn.execute("""
            UPDATE staff_notice_occurrences
            SET occurrence_status = 'Cancelled',
                status_reason = 'Shift Cancelled',
                status_changed_at_utc = ?,
                status_changed_by_user_id = ?
            WHERE occurrence_id = ?
              AND occurrence_status = ?
        """, (
            cancelled_at_utc,
            actor_user_id,
            occurrence["occurrence_id"],
            previous_status
        ))
        if cursor.rowcount != 1:
            raise ShiftCancellationConflictError(
                "A Staff Notice occurrence changed during "
                "shift cancellation."
            )
        occurrences_cancelled += 1
        occurrence_ids.append(occurrence["occurrence_id"])
        log_activity(
            conn,
            activity_class="STAFF_NOTICE",
            activity_type="staff_notice_occurrence_status_changed",
            summary=(
                "Staff Notice occurrence cancelled: "
                f"{occurrence['title']}"
            ),
            user_id=actor_user_id,
            client_id=occurrence["client_id"],
            shift_id=shift_id,
            related_table="staff_notice_occurrences",
            related_id=occurrence["occurrence_id"],
            details=(
                f"Notice ID: {occurrence['notice_id']}; "
                f"Occurrence ID: {occurrence['occurrence_id']}; "
                f"Previous status: {previous_status}; "
                "New status: Cancelled; "
                "Reason code: Shift Cancelled; "
                f"Reason: {reason}; Effective at UTC: "
                f"{cancelled_at_utc}"
            ),
            success=1
        )

    deliveries = []
    if occurrence_ids:
        placeholders = ",".join("?" for _ in occurrence_ids)
        deliveries = [
            dict(row)
            for row in conn.execute(f"""
                SELECT
                    d.*,
                    o.shift_id,
                    sn.notice_id,
                    sn.title,
                    sn.client_id
                FROM staff_notice_deliveries d
                JOIN staff_notice_occurrences o
                    ON d.occurrence_id = o.occurrence_id
                JOIN staff_notice_schedules sns
                    ON o.schedule_id = sns.schedule_id
                JOIN staff_notices sn
                    ON sns.notice_id = sn.notice_id
                WHERE d.occurrence_id IN ({placeholders})
                ORDER BY d.delivery_id
            """, tuple(occurrence_ids)).fetchall()
        ]

    deliveries_cancelled = 0
    delivery_access_revoked = 0
    for delivery in deliveries:
        deliveries_cancelled += _cancel_staff_notice_delivery(
            conn,
            delivery,
            actor_user_id,
            reason,
            cancelled_at_utc,
            reason_code=SHIFT_CANCELLATION_REASON_CODE
        )
        delivery_access_revoked += (
            _revoke_staff_notice_delivery_access(
                conn,
                delivery,
                actor_user_id,
                reason,
                cancelled_at_utc,
                reason_code=SHIFT_CANCELLATION_REASON_CODE
            )
        )

    persisted_shift = conn.execute("""
        SELECT status, actual_end_at_utc
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()
    if (
        persisted_shift is None
        or persisted_shift["status"] != SHIFT_CANCELLED_STATUS
        or persisted_shift["actual_end_at_utc"] is not None
    ):
        raise RuntimeError("Shift cancellation verification failed.")
    remaining_active = conn.execute("""
        SELECT COUNT(*) AS active_count
        FROM shift_staff
        WHERE shift_id = ?
          AND active = 1
    """, (shift_id,)).fetchone()["active_count"]
    if remaining_active != 0:
        raise RuntimeError("Shift assignment cancellation verification failed.")
    if occurrence_ids:
        placeholders = ",".join("?" for _ in occurrence_ids)
        remaining_occurrences = conn.execute(f"""
            SELECT COUNT(*) AS occurrence_count
            FROM staff_notice_occurrences
            WHERE occurrence_id IN ({placeholders})
              AND (
                  occurrence_status <> 'Cancelled'
                  OR status_reason <> 'Shift Cancelled'
              )
        """, tuple(occurrence_ids)).fetchone()["occurrence_count"]
        accessible_deliveries = conn.execute(f"""
            SELECT COUNT(*) AS delivery_count
            FROM staff_notice_deliveries
            WHERE occurrence_id IN ({placeholders})
              AND recipient_access <> 0
        """, tuple(occurrence_ids)).fetchone()["delivery_count"]
        if remaining_occurrences or accessible_deliveries:
            raise RuntimeError(
                "Staff Notice shift cancellation verification failed."
            )

    assignment_id_text = (
        ", ".join(str(value) for value in deactivated_assignment_ids)
        if deactivated_assignment_ids else "None"
    )
    log_activity(
        conn,
        activity_class="SHIFT",
        activity_type="shift_cancelled",
        summary="Shift cancelled",
        user_id=actor_user_id,
        client_id=shift["client_id"],
        shift_id=shift_id,
        related_table="shifts",
        related_id=shift_id,
        details=(
            f"Shift ID: {shift_id}; Actor User ID: {actor_user_id}; "
            f"Client ID: {shift['client_id']}; Reason: {reason}; "
            f"Effective at UTC: {cancelled_at_utc}; "
            "Deactivated assignment IDs: "
            f"{assignment_id_text}; Deactivated assignment count: "
            f"{len(deactivated_assignment_ids)}"
        ),
        success=1
    )

    return {
        "cancelled": 1,
        "assignments_deactivated": len(deactivated_assignment_ids),
        "assignment_ids": tuple(deactivated_assignment_ids),
        "occurrences_cancelled": occurrences_cancelled,
        "deliveries_cancelled": deliveries_cancelled,
        "delivery_access_revoked": delivery_access_revoked
    }

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

# Stable UI identifiers.  These deliberately map one-to-one to the existing
# schedule combinations; the scheduling engine remains the authority.
STAFF_NOTICE_GUIDED_SCHEDULES = {
    "none": None,
    "one_time": ("One Time", "Once", "None"),
    "calendar_once": ("Calendar", "Once", "None"),
    "calendar_daily": ("Calendar", "Daily", "None"),
    "calendar_interval": ("Calendar", "Interval Days", "None"),
    "calendar_weekdays": ("Calendar", "Selected Weekdays", "None"),
    "shift_once_every": ("Shift", "Once", "Every Shift"),
    "shift_once_types": ("Shift", "Once", "Selected Shift Types"),
    "shift_once_specific": ("Shift", "Once", "Specific Shift"),
    "shift_daily_every": ("Shift", "Daily", "Every Shift"),
    "shift_daily_types": ("Shift", "Daily", "Selected Shift Types"),
    "shift_interval_every": ("Shift", "Interval Days", "Every Shift"),
    "shift_interval_types": ("Shift", "Interval Days", "Selected Shift Types"),
    "shift_weekdays_every": ("Shift", "Selected Weekdays", "Every Shift"),
    "shift_weekdays_types": ("Shift", "Selected Weekdays", "Selected Shift Types")
}
STAFF_NOTICE_GUIDED_SCHEDULE_LABELS = {
    "none": "No delivery schedule",
    "one_time": "Once at a specific date and time",
    "calendar_once": "Once on a calendar date",
    "calendar_daily": "Every day",
    "calendar_interval": "Every chosen number of days",
    "calendar_weekdays": "On selected weekdays",
    "shift_once_every": "Once for every shift",
    "shift_once_types": "Once for selected shift types",
    "shift_once_specific": "Once for one specific shift",
    "shift_daily_every": "Every day for every shift",
    "shift_daily_types": "Every day for selected shift types",
    "shift_interval_every": "Every chosen number of days for every shift",
    "shift_interval_types": "Every chosen number of days for selected shift types",
    "shift_weekdays_every": "On selected weekdays for every shift",
    "shift_weekdays_types": "On selected weekdays for selected shift types"
}

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
    "guided_schedule_path",
    "guided_calendar_date",
    "guided_shift_client_id",
    "guided_shift_date",
    "guided_shift_type",
    "guided_due_local",
    "guided_interval_days",
    "guided_anchor_date",
    "expected_updated_at_utc"
})

STAFF_NOTICE_CREATE_FORM_KEYS = (
    STAFF_NOTICE_MANAGEMENT_FORM_KEYS
    - {"expected_updated_at_utc"}
)

STAFF_NOTICE_PUBLICATION_FORM_KEYS = frozenset({
    "expected_updated_at_utc"
})

STAFF_NOTICE_REPLACEMENT_FORM_KEYS = frozenset({
    "replacement_reason",
    "confirm_replacement"
})

STAFF_NOTICE_ACKNOWLEDGEMENT_INVALIDATION_FORM_KEYS = frozenset({
    "invalidation_reason",
    "confirm_invalidation"
})

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
    "one_time_due_local",
    "guided_schedule_path",
    "guided_calendar_date",
    "guided_shift_client_id",
    "guided_shift_date",
    "guided_shift_type",
    "guided_due_local",
    "guided_interval_days",
    "guided_anchor_date"
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


class StaffNoticeReplacementConflictError(ValueError):
    pass


class StaffNoticeAcknowledgementInvalidationConflictError(ValueError):
    pass


class StaffNoticeManagementLifecycleConflictError(ValueError):
    pass


class StaffNoticeShiftSignOnError(RuntimeError):
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


def staff_notice_manager_local_datetime_to_utc(
    value,
    ambiguous_occurrence=None
):
    if not isinstance(value, str) or not value:
        raise ValueError(
            "The genuine historical end date and time are required."
        )

    try:
        local_naive_value = datetime.strptime(
            value,
            STAFF_NOTICE_LOCAL_DATETIME_FORMAT
        )
    except ValueError as error:
        raise ValueError(
            "The genuine historical end must use YYYY-MM-DDTHH:MM."
        ) from error

    if (
        local_naive_value.strftime(
            STAFF_NOTICE_LOCAL_DATETIME_FORMAT
        ) != value
    ):
        raise ValueError(
            "The genuine historical end must use YYYY-MM-DDTHH:MM."
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
            "The genuine historical end does not exist in "
            f"{STAFF_NOTICE_TIMEZONE_NAME} because of the spring "
            "daylight-saving transition."
        )

    candidate_offsets = {
        candidate.utcoffset()
        for candidate in valid_candidates
    }
    if len(candidate_offsets) > 1:
        choices = {
            "first": 0,
            "second": 1
        }
        if ambiguous_occurrence not in choices:
            raise ValueError(
                "This Vancouver time occurs twice. Choose the first "
                "PDT occurrence or the second PST occurrence."
            )
        selected_fold = choices[ambiguous_occurrence]
        selected = next(
            candidate
            for candidate in valid_candidates
            if candidate.fold == selected_fold
        )
        return selected.astimezone(timezone.utc)

    return valid_candidates[0].astimezone(timezone.utc)


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


def format_staff_notice_friendly_local_datetime(value):
    """Format a stored UTC Staff Notice timestamp for management display."""
    local_value = staff_notice_utc_datetime_to_local(value)
    hour = local_value.hour % 12 or 12
    meridiem = "AM" if local_value.hour < 12 else "PM"
    return (
        f"{local_value.strftime('%b')} {local_value.day}, "
        f"{local_value.year} at {hour}:{local_value.minute:02d} {meridiem}"
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

    guided_path = scalar_values["guided_schedule_path"].strip()
    if guided_path:
        if guided_path not in STAFF_NOTICE_GUIDED_SCHEDULES:
            raise ValueError("Invalid Staff Notice guided schedule path.")

        combination = STAFF_NOTICE_GUIDED_SCHEDULES[guided_path]
        if combination is None:
            payload["schedule"] = None
            return payload

        occurrence_basis, recurrence_pattern, shift_applicability = combination
        schedule = {
            "occurrence_basis": occurrence_basis,
            "recurrence_pattern": recurrence_pattern,
            "shift_applicability": shift_applicability
        }

        # Guided controls are copied only for the selected path.  This makes
        # stale or tampered hidden values harmless before final normalization.
        if recurrence_pattern == "Interval Days":
            value = scalar_values["guided_interval_days"].strip()
            if value:
                try:
                    schedule["interval_days"] = int(value)
                except ValueError as error:
                    raise ValueError(
                        "Staff Notice interval days must be a whole number."
                    ) from error
        if recurrence_pattern == "Selected Weekdays":
            schedule["weekdays"] = [
                int(value) for value in form.getlist("weekdays")
            ]
            if any(value < 0 or value > 6 for value in schedule["weekdays"]):
                raise ValueError("Invalid Staff Notice weekday.")
        if recurrence_pattern != "Once":
            anchor = scalar_values["guided_anchor_date"].strip()
            if anchor:
                schedule["recurrence_anchor_date"] = anchor
        if shift_applicability == "Selected Shift Types":
            schedule["shift_types"] = form.getlist("shift_types")
        if guided_path == "calendar_once":
            schedule["specific_calendar_date"] = scalar_values[
                "guided_calendar_date"
            ].strip()
        if guided_path == "one_time":
            schedule["one_time_due_local"] = scalar_values[
                "guided_due_local"
            ].strip()
        if guided_path == "shift_once_specific":
            schedule.update({
                "specific_shift_client_id": _staff_notice_form_identifier(
                    scalar_values["guided_shift_client_id"],
                    "Specific-shift client"
                ),
                "specific_shift_date": scalar_values[
                    "guided_shift_date"
                ].strip(),
                "specific_shift_type": scalar_values[
                    "guided_shift_type"
                ].strip()
            })
        payload["schedule"] = schedule
        return payload

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
            updater.full_name AS updated_by,
            publisher.full_name AS published_by,
            withdrawer.full_name AS withdrawn_by,
            replacer.full_name AS replaced_by,
            successor.notice_id AS replacement_notice_id,
            successor.title AS replacement_notice_title,
            successor.status AS replacement_notice_status
        FROM staff_notices sn
        LEFT JOIN clients c
            ON sn.client_id = c.client_id
        JOIN users creator
            ON sn.created_by_user_id = creator.user_id
        LEFT JOIN users updater
            ON sn.updated_by_user_id = updater.user_id
        LEFT JOIN users publisher
            ON sn.published_by_user_id = publisher.user_id
        LEFT JOIN users withdrawer
            ON sn.withdrawn_by_user_id = withdrawer.user_id
        LEFT JOIN users replacer
            ON sn.replaced_by_user_id = replacer.user_id
        LEFT JOIN staff_notices successor
            ON successor.replaces_notice_id = sn.notice_id
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
    combination = (
        schedule.get("occurrence_basis"),
        schedule.get("recurrence_pattern"),
        schedule.get("shift_applicability")
    ) if notice["schedule"] else None
    guided_path = next(
        (
            path for path, value in STAFF_NOTICE_GUIDED_SCHEDULES.items()
            if value == combination
        ),
        "none"
    )
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
        ,"guided_schedule_path": guided_path
        ,"guided_calendar_date": schedule.get("specific_calendar_date") or ""
        ,"guided_shift_client_id": str(schedule.get("specific_shift_client_id") or "")
        ,"guided_shift_date": schedule.get("specific_shift_date") or ""
        ,"guided_shift_type": schedule.get("specific_shift_type") or ""
        ,"guided_due_local": (
            format_staff_notice_local_datetime(
                schedule.get("one_time_due_at_utc"),
                STAFF_NOTICE_LOCAL_DATETIME_FORMAT
            ) if schedule.get("one_time_due_at_utc") else ""
        )
        ,"guided_interval_days": str(schedule.get("interval_days") or "")
        ,"guided_anchor_date": schedule.get("recurrence_anchor_date") or ""
        ,"guided_shift_types": list(notice["shift_types"])
        ,"guided_weekdays": [str(value) for value in notice["weekdays"]]
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

        if shift["status"] == SHIFT_CANCELLED_STATUS:
            if applicability == "Specific Shift":
                _append_staff_notice_preview_message(
                    blocking_errors,
                    "The selected specific shift is cancelled and cannot "
                    "receive Staff Notice requirements."
                )
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


def _log_staff_notice_audience_eligibility_started(
    conn,
    notice,
    eligibility_period_id,
    user_id,
    sources
):
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_audience_eligibility_started",
        summary=(
            "Staff Notice audience eligibility started: "
            f"{notice['title']}"
        ),
        user_id=None,
        client_id=notice["client_id"],
        shift_id=None,
        related_table="staff_notice_audience_eligibility_periods",
        related_id=eligibility_period_id,
        details=(
            f"Notice ID: {notice['notice_id']}; "
            f"Recipient user ID: {user_id}; Sources: {sources}"
        ),
        success=1
    )


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
    notice = preview["notice"]
    candidates = preview["_publication_audience_candidates"]

    for user_id in sorted(candidates):
        sources = ", ".join(dict.fromkeys(
            candidates[user_id]["qualification_sources"]
        ))
        cursor = conn.execute("""
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
            sources,
            actor_user_id,
            opened_at_utc
        ))
        _log_staff_notice_audience_eligibility_started(
            conn,
            notice,
            cursor.lastrowid,
            user_id,
            sources
        )


def _staff_notice_occurrence_status(visible_from_utc, now_utc):
    if (
        visible_from_utc is not None
        and parse_staff_notice_utc_datetime(visible_from_utc) <= now_utc
    ):
        return "Active"

    return "Scheduled"


def _staff_notice_calendar_day_end_utc(local_date):
    next_midnight_local = datetime.combine(
        local_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=STAFF_NOTICE_TIMEZONE
    )
    return next_midnight_local.astimezone(timezone.utc) - timedelta(seconds=1)


def _parse_staff_notice_shift_clock(value, field_name):
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a stored local time.")

    for format_string in ("%H:%M", "%H:%M:%S"):
        try:
            parsed_value = datetime.strptime(value, format_string).time()
        except ValueError:
            continue

        if parsed_value.strftime(format_string) == value:
            return parsed_value

    raise ValueError(
        f"{field_name} must use HH:MM or HH:MM:SS local time."
    )


def _staff_notice_resolve_local_shift_datetime(
    local_date,
    local_clock,
    field_name
):
    local_naive_value = datetime.combine(local_date, local_clock)
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
            f"{field_name} does not exist in "
            f"{STAFF_NOTICE_TIMEZONE_NAME}."
        )
    if len({value.utcoffset() for value in valid_candidates}) > 1:
        raise ValueError(
            f"{field_name} is ambiguous in "
            f"{STAFF_NOTICE_TIMEZONE_NAME}."
        )

    return valid_candidates[0]


def _staff_notice_shift_occurrence_times(shift, notice, now_utc):
    shift_date = datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
    start_clock = _parse_staff_notice_shift_clock(
        shift.get("scheduled_start_time"),
        "Stored scheduled shift start"
    )
    end_clock = _parse_staff_notice_shift_clock(
        shift.get("scheduled_end_time"),
        "Stored scheduled shift end"
    )
    start_local = None
    end_local = None

    if start_clock is not None:
        start_local = _staff_notice_resolve_local_shift_datetime(
            shift_date,
            start_clock,
            "Stored scheduled shift start"
        )

    if end_clock is not None:
        end_date = shift_date
        if shift["shift_type"] == "Overnight" or (
            start_clock is not None and end_clock <= start_clock
        ):
            end_date += timedelta(days=1)
        end_local = _staff_notice_resolve_local_shift_datetime(
            end_date,
            end_clock,
            "Stored scheduled shift end"
        )

    effective_start = parse_staff_notice_utc_datetime(
        notice["effective_start_at_utc"]
    )
    visible_from = None
    if start_local is not None:
        visible_from = max(
            start_local.astimezone(timezone.utc),
            effective_start,
            now_utc
        )

    due_at = (
        end_local.astimezone(timezone.utc)
        if end_local is not None
        else None
    )
    return visible_from, due_at


def _staff_notice_pending_shift_expected_end_at_utc(occurrence):
    try:
        shift_date = datetime.strptime(
            occurrence["occurrence_date"],
            "%Y-%m-%d"
        ).date()
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "The pending Staff Notice occurrence date is invalid."
        ) from error

    shift_type = occurrence.get("planned_shift_type")
    expected_end_clocks = {
        "Day": datetime.strptime("15:00", "%H:%M").time(),
        "Afternoon": datetime.strptime("23:00", "%H:%M").time(),
        "Overnight": datetime.strptime("07:00", "%H:%M").time()
    }
    if shift_type not in expected_end_clocks:
        raise ValueError(
            "The pending Staff Notice occurrence shift type is invalid."
        )
    if shift_type == "Overnight":
        shift_date += timedelta(days=1)

    expected_end_local = _staff_notice_resolve_local_shift_datetime(
        shift_date,
        expected_end_clocks[shift_type],
        "Expected shift end"
    )
    return expected_end_local.astimezone(timezone.utc)


def _log_staff_notice_occurrence_created(
    conn,
    notice,
    occurrence_id,
    occurrence_kind,
    occurrence_date,
    visible_from_at_utc,
    due_at_utc,
    shift_id
):
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_occurrence_created",
        summary=f"Staff Notice occurrence created: {notice['title']}",
        user_id=None,
        client_id=notice["client_id"],
        shift_id=shift_id,
        related_table="staff_notice_occurrences",
        related_id=occurrence_id,
        details=(
            f"Notice ID: {notice['notice_id']}; "
            f"Kind: {occurrence_kind}; Date: {occurrence_date}; "
            f"Visible from: {visible_from_at_utc}; "
            f"Due at: {due_at_utc}"
        ),
        success=1
    )


def _insert_initial_staff_notice_occurrence(
    conn,
    *,
    notice,
    schedule_id,
    occurrence_kind,
    occurrence_date,
    planned_client_id,
    planned_shift_type,
    shift_id,
    is_specific_shift_occurrence,
    visible_from_at_utc,
    due_at_utc,
    due_at_is_provisional,
    occurrence_status,
    created_at_utc,
    shift_bound_at_utc
):
    cursor = conn.execute("""
        INSERT INTO staff_notice_occurrences
        (
            schedule_id,
            occurrence_kind,
            occurrence_date,
            planned_client_id,
            planned_shift_type,
            shift_id,
            is_specific_shift_occurrence,
            visible_from_at_utc,
            due_at_utc,
            due_at_is_provisional,
            due_at_updated_at_utc,
            occurrence_status,
            status_reason,
            created_at_utc,
            shift_bound_at_utc,
            status_changed_at_utc,
            status_changed_by_user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, NULL, NULL)
    """, (
        schedule_id,
        occurrence_kind,
        occurrence_date,
        planned_client_id,
        planned_shift_type,
        shift_id,
        is_specific_shift_occurrence,
        visible_from_at_utc,
        due_at_utc,
        due_at_is_provisional,
        occurrence_status,
        created_at_utc,
        shift_bound_at_utc
    ))
    _log_staff_notice_occurrence_created(
        conn,
        notice,
        cursor.lastrowid,
        occurrence_kind,
        occurrence_date,
        visible_from_at_utc,
        due_at_utc,
        shift_id
    )


def _create_initial_staff_notice_occurrences(
    conn,
    preview,
    created_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice occurrence creation requires an active "
            "transaction."
        )

    notice = preview["notice"]
    schedule = notice["schedule"]
    schedule_id = schedule["schedule_id"]
    now_utc = parse_staff_notice_utc_datetime(created_at_utc)
    effective_start = parse_staff_notice_utc_datetime(
        notice["effective_start_at_utc"]
    )
    occurrence_basis = schedule["occurrence_basis"]

    if occurrence_basis == "One Time":
        visible_from = max(now_utc, effective_start)
        due_at = schedule.get("one_time_due_at_utc")
        if due_at is None and notice["until_withdrawn"] == 0:
            due_at = notice["expires_at_utc"]
        _insert_initial_staff_notice_occurrence(
            conn,
            notice=notice,
            schedule_id=schedule_id,
            occurrence_kind="One Time",
            occurrence_date=None,
            planned_client_id=None,
            planned_shift_type=None,
            shift_id=None,
            is_specific_shift_occurrence=0,
            visible_from_at_utc=format_staff_notice_utc_datetime(visible_from),
            due_at_utc=(
                format_staff_notice_utc_datetime(due_at)
                if due_at is not None else None
            ),
            due_at_is_provisional=0,
            occurrence_status=_staff_notice_occurrence_status(
                visible_from,
                now_utc
            ),
            created_at_utc=created_at_utc,
            shift_bound_at_utc=None
        )
        return

    if occurrence_basis == "Calendar":
        local_date = now_utc.astimezone(STAFF_NOTICE_TIMEZONE).date()
        effective_local_date = effective_start.astimezone(
            STAFF_NOTICE_TIMEZONE
        ).date()
        expires_at = (
            parse_staff_notice_utc_datetime(notice["expires_at_utc"])
            if notice["expires_at_utc"] is not None else None
        )
        expiry_local_date = (
            expires_at.astimezone(STAFF_NOTICE_TIMEZONE).date()
            if expires_at is not None else None
        )
        recurrence_anchor = schedule.get("recurrence_anchor_date")
        recurrence_anchor_date = (
            datetime.strptime(recurrence_anchor, "%Y-%m-%d").date()
            if recurrence_anchor is not None else None
        )

        if (
            effective_local_date <= local_date
            and (
                expiry_local_date is None
                or local_date <= expiry_local_date
            )
            and _staff_notice_schedule_applies_on_date(
                schedule,
                local_date,
                notice.get("weekdays", []),
                recurrence_anchor_date
            )
        ):
            visible_from = max(now_utc, effective_start)
            due_at = _staff_notice_calendar_day_end_utc(local_date)
            _insert_initial_staff_notice_occurrence(
                conn,
                notice=notice,
                schedule_id=schedule_id,
                occurrence_kind="Calendar",
                occurrence_date=local_date.isoformat(),
                planned_client_id=None,
                planned_shift_type=None,
                shift_id=None,
                is_specific_shift_occurrence=0,
                visible_from_at_utc=format_staff_notice_utc_datetime(
                    visible_from
                ),
                due_at_utc=format_staff_notice_utc_datetime(due_at),
                due_at_is_provisional=0,
                occurrence_status=_staff_notice_occurrence_status(
                    visible_from,
                    now_utc
                ),
                created_at_utc=created_at_utc,
                shift_bound_at_utc=None
            )
        return

    is_specific_shift = (
        schedule["shift_applicability"] == "Specific Shift"
    )
    matching_shifts = preview["matching_shifts"]

    if is_specific_shift and not matching_shifts:
        _insert_initial_staff_notice_occurrence(
            conn,
            notice=notice,
            schedule_id=schedule_id,
            occurrence_kind="Shift",
            occurrence_date=schedule["specific_shift_date"],
            planned_client_id=schedule["specific_shift_client_id"],
            planned_shift_type=schedule["specific_shift_type"],
            shift_id=None,
            is_specific_shift_occurrence=1,
            visible_from_at_utc=None,
            due_at_utc=None,
            due_at_is_provisional=0,
            occurrence_status="Pending Shift",
            created_at_utc=created_at_utc,
            shift_bound_at_utc=None
        )
        return

    seen_shift_ids = set()
    for shift in matching_shifts:
        shift_id = shift["shift_id"]
        if shift_id in seen_shift_ids:
            continue
        seen_shift_ids.add(shift_id)
        visible_from, due_at = _staff_notice_shift_occurrence_times(
            shift,
            notice,
            now_utc
        )
        _insert_initial_staff_notice_occurrence(
            conn,
            notice=notice,
            schedule_id=schedule_id,
            occurrence_kind="Shift",
            occurrence_date=shift["shift_date"],
            planned_client_id=shift["client_id"],
            planned_shift_type=shift["shift_type"],
            shift_id=shift_id,
            is_specific_shift_occurrence=int(is_specific_shift),
            visible_from_at_utc=(
                format_staff_notice_utc_datetime(visible_from)
                if visible_from is not None else None
            ),
            due_at_utc=(
                format_staff_notice_utc_datetime(due_at)
                if due_at is not None else None
            ),
            due_at_is_provisional=int(due_at is not None),
            occurrence_status=_staff_notice_occurrence_status(
                visible_from,
                now_utc
            ),
            created_at_utc=created_at_utc,
            shift_bound_at_utc=created_at_utc
        )


def _staff_notice_delivery_eligibility_cutoff(
    occurrence,
    assigned_at_utc
):
    cutoff = occurrence["visible_from_at_utc"] or assigned_at_utc
    return format_staff_notice_utc_datetime(cutoff)


def _load_initial_staff_notice_delivery_user_ids(
    conn,
    notice_id,
    occurrence,
    eligibility_cutoff_at_utc
):
    if occurrence["occurrence_kind"] != "Shift":
        return [
            row["user_id"]
            for row in conn.execute("""
                SELECT DISTINCT ep.user_id
                FROM staff_notice_audience_eligibility_periods ep
                JOIN staff_notice_audiences a
                    ON ep.audience_id = a.audience_id
                JOIN users u
                    ON ep.user_id = u.user_id
                WHERE a.notice_id = ?
                  AND u.active = 1
                  AND ep.eligible_from_at_utc <= ?
                  AND (
                      ep.eligible_until_at_utc IS NULL
                      OR ep.eligible_until_at_utc >= ?
                  )
                ORDER BY ep.user_id
            """, (
                notice_id,
                eligibility_cutoff_at_utc,
                eligibility_cutoff_at_utc
            )).fetchall()
        ]

    if occurrence["shift_id"] is None:
        return []

    return [
        row["user_id"]
        for row in conn.execute("""
            SELECT DISTINCT ss.user_id
            FROM shift_staff ss
            JOIN users u
                ON ss.user_id = u.user_id
            WHERE ss.shift_id = ?
              AND ss.active = 1
              AND u.active = 1
              AND (
                  EXISTS (
                      SELECT 1
                      FROM staff_notice_audiences a
                      JOIN staff_notice_audience_rules ar
                          ON ar.audience_id = a.audience_id
                      WHERE a.notice_id = ?
                        AND ar.rule_type = 'Applicable Shift Staff'
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM staff_notice_audiences a
                      JOIN staff_notice_audience_eligibility_periods ep
                          ON ep.audience_id = a.audience_id
                      WHERE a.notice_id = ?
                        AND ep.user_id = ss.user_id
                        AND ep.eligible_from_at_utc <= ?
                        AND (
                            ep.eligible_until_at_utc IS NULL
                            OR ep.eligible_until_at_utc >= ?
                        )
                  )
              )
            ORDER BY ss.user_id
        """, (
            occurrence["shift_id"],
            notice_id,
            notice_id,
            eligibility_cutoff_at_utc,
            eligibility_cutoff_at_utc
        )).fetchall()
    ]


def _create_initial_staff_notice_deliveries(
    conn,
    preview,
    assigned_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice delivery creation requires an active "
            "transaction."
        )

    notice = preview["notice"]
    occurrences = conn.execute("""
        SELECT o.*
        FROM staff_notice_occurrences o
        WHERE o.schedule_id = ?
        ORDER BY o.occurrence_id
    """, (notice["schedule"]["schedule_id"],)).fetchall()

    for row in occurrences:
        occurrence = dict(row)
        if occurrence["occurrence_status"] == "Pending Shift":
            continue
        eligibility_cutoff_at_utc = (
            _staff_notice_delivery_eligibility_cutoff(
                occurrence,
                assigned_at_utc
            )
        )
        user_ids = _load_initial_staff_notice_delivery_user_ids(
            conn,
            notice["notice_id"],
            occurrence,
            eligibility_cutoff_at_utc
        )

        for user_id in user_ids:
            delivery_created = _assign_staff_notice_delivery(
                conn,
                notice,
                occurrence,
                user_id,
                assigned_at_utc,
                eligibility_cutoff_at_utc
            )
            if delivery_created != 1:
                raise RuntimeError(
                    "Initial Staff Notice delivery assignment did not "
                    "create exactly one delivery."
                )


def _staff_notice_reconciliation_result():
    return {
        "eligibility_started": 0,
        "eligibility_ended": 0,
        "eligibility_sources_updated": 0,
        "occurrences_created": 0,
        "deliveries_assigned": 0
    }


def _merge_staff_notice_reconciliation_result(target, source):
    for key in target:
        target[key] += source[key]


def reconcile_staff_notice_audience_eligibility(
    conn,
    notice,
    reconciled_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice eligibility reconciliation requires an active "
            "transaction."
        )

    result = _staff_notice_reconciliation_result()
    reconciled_at_utc = format_staff_notice_utc_datetime(
        reconciled_at_utc
    )
    audience_id = notice["audience"]["audience_id"]
    candidates, _ = _resolve_staff_notice_audience_candidates(
        conn,
        notice["audience_rules"]
    )
    open_periods = {
        row["user_id"]: dict(row)
        for row in conn.execute("""
            SELECT *
            FROM staff_notice_audience_eligibility_periods
            WHERE audience_id = ?
              AND eligible_until_at_utc IS NULL
            ORDER BY user_id
        """, (audience_id,)).fetchall()
    }
    close_reason = (
        "No longer matches the current non-shift Staff Notice audience."
    )

    for user_id in sorted(set(open_periods) - set(candidates)):
        period = open_periods[user_id]
        cursor = conn.execute("""
            UPDATE staff_notice_audience_eligibility_periods
            SET eligible_until_at_utc = ?,
                closed_by_user_id = NULL,
                close_reason = ?,
                updated_at_utc = ?
            WHERE eligibility_period_id = ?
              AND eligible_until_at_utc IS NULL
        """, (
            reconciled_at_utc,
            close_reason,
            reconciled_at_utc,
            period["eligibility_period_id"]
        ))
        if cursor.rowcount != 1:
            continue

        result["eligibility_ended"] += 1
        log_activity(
            conn,
            activity_class="STAFF_NOTICE",
            activity_type="staff_notice_audience_eligibility_ended",
            summary=(
                "Staff Notice audience eligibility ended: "
                f"{notice['title']}"
            ),
            user_id=None,
            client_id=notice["client_id"],
            shift_id=None,
            related_table="staff_notice_audience_eligibility_periods",
            related_id=period["eligibility_period_id"],
            details=(
                f"Notice ID: {notice['notice_id']}; "
                f"Recipient user ID: {user_id}; "
                f"Reason: {close_reason}"
            ),
            success=1
        )

    for user_id in sorted(candidates):
        sources = ", ".join(dict.fromkeys(
            candidates[user_id]["qualification_sources"]
        ))
        period = open_periods.get(user_id)

        if period is not None:
            if period["eligibility_source_summary"] != sources:
                cursor = conn.execute("""
                    UPDATE staff_notice_audience_eligibility_periods
                    SET eligibility_source_summary = ?,
                        updated_at_utc = ?
                    WHERE eligibility_period_id = ?
                      AND eligible_until_at_utc IS NULL
                      AND eligibility_source_summary <> ?
                """, (
                    sources,
                    reconciled_at_utc,
                    period["eligibility_period_id"],
                    sources
                ))
                result["eligibility_sources_updated"] += cursor.rowcount
            continue

        cursor = conn.execute("""
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
            VALUES (?, ?, ?, NULL, ?, NULL, NULL, NULL, ?, NULL)
            ON CONFLICT DO NOTHING
        """, (
            audience_id,
            user_id,
            reconciled_at_utc,
            sources,
            reconciled_at_utc
        ))
        if cursor.rowcount != 1:
            continue

        eligibility_period_id = cursor.lastrowid
        result["eligibility_started"] += 1
        _log_staff_notice_audience_eligibility_started(
            conn,
            notice,
            eligibility_period_id,
            user_id,
            sources
        )

    return result


def _staff_notice_calendar_occurrence_visible_from(
    occurrence_date,
    notice
):
    local_midnight = datetime.combine(
        occurrence_date,
        datetime.min.time(),
        tzinfo=STAFF_NOTICE_TIMEZONE
    ).astimezone(timezone.utc)
    return max(
        local_midnight,
        parse_staff_notice_utc_datetime(notice["published_at_utc"]),
        parse_staff_notice_utc_datetime(notice["effective_start_at_utc"])
    )


def generate_due_staff_notice_occurrences(
    conn,
    notice,
    reconciled_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice occurrence reconciliation requires an active "
            "transaction."
        )

    result = _staff_notice_reconciliation_result()
    schedule = notice["schedule"]
    if schedule["occurrence_basis"] != "Calendar":
        return result

    now_utc = parse_staff_notice_utc_datetime(reconciled_at_utc)
    created_at_utc = format_staff_notice_utc_datetime(now_utc)
    published_at = parse_staff_notice_utc_datetime(
        notice["published_at_utc"]
    )
    effective_start = parse_staff_notice_utc_datetime(
        notice["effective_start_at_utc"]
    )
    expires_at = (
        parse_staff_notice_utc_datetime(notice["expires_at_utc"])
        if notice["expires_at_utc"] is not None else None
    )
    first_date = max(
        published_at.astimezone(STAFF_NOTICE_TIMEZONE).date(),
        effective_start.astimezone(STAFF_NOTICE_TIMEZONE).date()
    )
    last_date = now_utc.astimezone(STAFF_NOTICE_TIMEZONE).date()
    if expires_at is not None:
        last_date = min(
            last_date,
            expires_at.astimezone(STAFF_NOTICE_TIMEZONE).date()
        )
    recurrence_anchor = schedule.get("recurrence_anchor_date")
    recurrence_anchor_date = (
        datetime.strptime(recurrence_anchor, "%Y-%m-%d").date()
        if recurrence_anchor is not None else None
    )
    occurrence_date = first_date

    while occurrence_date <= last_date:
        if _staff_notice_schedule_applies_on_date(
            schedule,
            occurrence_date,
            notice.get("weekdays", []),
            recurrence_anchor_date
        ):
            visible_from = _staff_notice_calendar_occurrence_visible_from(
                occurrence_date,
                notice
            )
            if visible_from <= now_utc:
                due_at = _staff_notice_calendar_day_end_utc(
                    occurrence_date
                )
                cursor = conn.execute("""
                    INSERT INTO staff_notice_occurrences
                    (
                        schedule_id,
                        occurrence_kind,
                        occurrence_date,
                        visible_from_at_utc,
                        due_at_utc,
                        due_at_is_provisional,
                        occurrence_status,
                        created_at_utc
                    )
                    VALUES (?, 'Calendar', ?, ?, ?, 0, ?, ?)
                    ON CONFLICT DO NOTHING
                """, (
                    schedule["schedule_id"],
                    occurrence_date.isoformat(),
                    format_staff_notice_utc_datetime(visible_from),
                    format_staff_notice_utc_datetime(due_at),
                    _staff_notice_occurrence_status(visible_from, now_utc),
                    created_at_utc
                ))
                if cursor.rowcount == 1:
                    occurrence_id = cursor.lastrowid
                    result["occurrences_created"] += 1
                    _log_staff_notice_occurrence_created(
                        conn,
                        notice,
                        occurrence_id,
                        "Calendar",
                        occurrence_date.isoformat(),
                        format_staff_notice_utc_datetime(visible_from),
                        format_staff_notice_utc_datetime(due_at),
                        None
                    )

        occurrence_date += timedelta(days=1)

    return result


def _assign_staff_notice_delivery(
    conn,
    notice,
    occurrence,
    user_id,
    assigned_at_utc,
    eligibility_cutoff_at_utc,
    *,
    restore_existing=False,
    transition_actor_user_id=None,
    transition_reason=None
):
    cursor = conn.execute("""
        INSERT INTO staff_notice_deliveries
        (
            occurrence_id,
            user_id,
            requirement_status,
            assigned_at_utc,
            eligibility_cutoff_at_utc,
            recipient_access
        )
        VALUES (?, ?, 'Required', ?, ?, 1)
        ON CONFLICT (occurrence_id, user_id) DO NOTHING
    """, (
        occurrence["occurrence_id"],
        user_id,
        assigned_at_utc,
        eligibility_cutoff_at_utc
    ))
    if cursor.rowcount != 1:
        if restore_existing:
            existing = conn.execute("""
                SELECT
                    d.*,
                    o.shift_id,
                    sn.notice_id,
                    sn.title,
                    sn.client_id
                FROM staff_notice_deliveries d
                JOIN staff_notice_occurrences o
                    ON d.occurrence_id = o.occurrence_id
                JOIN staff_notice_schedules sns
                    ON o.schedule_id = sns.schedule_id
                JOIN staff_notices sn
                    ON sns.notice_id = sn.notice_id
                WHERE d.occurrence_id = ?
                  AND d.user_id = ?
            """, (
                occurrence["occurrence_id"],
                user_id
            )).fetchone()
            if existing is not None:
                _restore_staff_notice_delivery_for_shift_assignment(
                    conn,
                    dict(existing),
                    transition_actor_user_id,
                    transition_reason,
                    assigned_at_utc
                )
        return 0

    delivery_id = cursor.lastrowid
    conn.execute("""
        INSERT INTO staff_notice_delivery_history
        (
            delivery_id,
            event_type,
            previous_requirement_status,
            new_requirement_status,
            previous_recipient_access,
            new_recipient_access,
            reason_code,
            reason_text,
            changed_by_user_id,
            changed_at_utc
        )
        VALUES (?, 'Assigned', NULL, 'Required', NULL, 1,
                NULL, NULL, NULL, ?)
    """, (delivery_id, assigned_at_utc))
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_delivery_assigned",
        summary=f"Staff Notice delivery assigned: {notice['title']}",
        user_id=None,
        client_id=notice["client_id"],
        shift_id=None,
        related_table="staff_notice_deliveries",
        related_id=delivery_id,
        details=(
            f"Notice ID: {notice['notice_id']}; "
            f"Occurrence ID: {occurrence['occurrence_id']}; "
            f"Recipient user ID: {user_id}; Eligibility cutoff: "
            f"{eligibility_cutoff_at_utc}"
        ),
        success=1
    )
    return 1


def _load_current_one_time_staff_notice_delivery_user_ids(
    conn,
    notice_id,
    eligibility_cutoff_at_utc
):
    return [
        row["user_id"]
        for row in conn.execute("""
            SELECT DISTINCT ep.user_id
            FROM staff_notice_audience_eligibility_periods ep
            JOIN staff_notice_audiences a
                ON ep.audience_id = a.audience_id
            JOIN users u
                ON ep.user_id = u.user_id
            WHERE a.notice_id = ?
              AND u.active = 1
              AND ep.eligible_from_at_utc <= ?
              AND ep.eligible_until_at_utc IS NULL
            ORDER BY ep.user_id
        """, (
            notice_id,
            eligibility_cutoff_at_utc
        )).fetchall()
    ]


def reconcile_staff_notice_deliveries(
    conn,
    notice,
    reconciled_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice delivery reconciliation requires an active "
            "transaction."
        )

    result = _staff_notice_reconciliation_result()
    now_utc = parse_staff_notice_utc_datetime(reconciled_at_utc)
    assigned_at_utc = format_staff_notice_utc_datetime(now_utc)
    schedule = notice["schedule"]
    occurrence_basis = schedule["occurrence_basis"]

    if occurrence_basis not in {"One Time", "Calendar"}:
        return result

    occurrences = [
        dict(row)
        for row in conn.execute("""
            SELECT *
            FROM staff_notice_occurrences
            WHERE schedule_id = ?
              AND occurrence_kind IN ('One Time', 'Calendar')
            ORDER BY occurrence_id
        """, (schedule["schedule_id"],)).fetchall()
    ]

    for occurrence in occurrences:
        if occurrence["occurrence_kind"] == "One Time":
            visible_from = parse_staff_notice_utc_datetime(
                occurrence["visible_from_at_utc"]
            )
            expires_at = (
                parse_staff_notice_utc_datetime(notice["expires_at_utc"])
                if notice["expires_at_utc"] is not None else None
            )
            if (
                visible_from > now_utc
                or (expires_at is not None and expires_at < now_utc)
            ):
                continue
            eligibility_cutoff_at_utc = assigned_at_utc
            user_ids = (
                _load_current_one_time_staff_notice_delivery_user_ids(
                    conn,
                    notice["notice_id"],
                    eligibility_cutoff_at_utc
                )
            )
        else:
            visible_from = parse_staff_notice_utc_datetime(
                occurrence["visible_from_at_utc"]
            )
            if visible_from > now_utc:
                continue
            eligibility_cutoff_at_utc = format_staff_notice_utc_datetime(
                visible_from
            )
            user_ids = _load_initial_staff_notice_delivery_user_ids(
                conn,
                notice["notice_id"],
                occurrence,
                eligibility_cutoff_at_utc
            )
        for user_id in user_ids:
            result["deliveries_assigned"] += (
                _assign_staff_notice_delivery(
                    conn,
                    notice,
                    occurrence,
                    user_id,
                    assigned_at_utc,
                    eligibility_cutoff_at_utc
                )
            )

    return result


def _staff_notice_applies_to_shift(notice, shift, now_utc):
    schedule = notice["schedule"]
    if schedule is None or schedule["occurrence_basis"] != "Shift":
        return False
    if parse_staff_notice_utc_datetime(notice["published_at_utc"]) > now_utc:
        return False
    if notice["expires_at_utc"] is not None and (
        parse_staff_notice_utc_datetime(notice["expires_at_utc"]) < now_utc
    ):
        return False
    if notice["client_id"] is not None and (
        notice["client_id"] != shift["client_id"]
    ):
        return False

    shift_date = datetime.strptime(shift["shift_date"], "%Y-%m-%d").date()
    effective_date = parse_staff_notice_utc_datetime(
        notice["effective_start_at_utc"]
    ).astimezone(STAFF_NOTICE_TIMEZONE).date()
    published_date = parse_staff_notice_utc_datetime(
        notice["published_at_utc"]
    ).astimezone(STAFF_NOTICE_TIMEZONE).date()
    if shift_date < max(effective_date, published_date):
        return False
    if notice["expires_at_utc"] is not None and shift_date > (
        parse_staff_notice_utc_datetime(notice["expires_at_utc"])
        .astimezone(STAFF_NOTICE_TIMEZONE)
        .date()
    ):
        return False

    applicability = schedule["shift_applicability"]
    if applicability == "Specific Shift":
        if (
            shift["client_id"] != schedule["specific_shift_client_id"]
            or shift["shift_date"] != schedule["specific_shift_date"]
            or shift["shift_type"] != schedule["specific_shift_type"]
        ):
            return False
    elif applicability == "Selected Shift Types":
        if shift["shift_type"] not in notice["shift_types"]:
            return False
    elif applicability != "Every Shift":
        return False

    recurrence_anchor = schedule.get("recurrence_anchor_date")
    recurrence_anchor_date = (
        datetime.strptime(recurrence_anchor, "%Y-%m-%d").date()
        if recurrence_anchor is not None else None
    )
    return _staff_notice_schedule_applies_on_date(
        schedule,
        shift_date,
        notice["weekdays"],
        recurrence_anchor_date
    )


def _log_staff_notice_occurrence_bound(
    conn,
    notice,
    occurrence_id,
    shift_id
):
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_occurrence_bound_to_shift",
        summary=f"Staff Notice occurrence bound to shift: {notice['title']}",
        user_id=None,
        client_id=notice["client_id"],
        shift_id=shift_id,
        related_table="staff_notice_occurrences",
        related_id=occurrence_id,
        details=(
            f"Notice ID: {notice['notice_id']}; "
            f"Occurrence ID: {occurrence_id}; Shift ID: {shift_id}"
        ),
        success=1
    )


def _log_staff_notice_no_shift_correction(
    conn,
    notice,
    occurrence,
    shift_id,
    new_status,
    actor_user_id,
    corrected_at_utc
):
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_no_shift_correction",
        summary=(
            "Staff Notice No Shift Occurred corrected: "
            f"{notice['title']}"
        ),
        user_id=actor_user_id,
        client_id=notice["client_id"],
        shift_id=shift_id,
        related_table="staff_notice_occurrences",
        related_id=occurrence["occurrence_id"],
        details=(
            f"Notice ID: {notice['notice_id']}; "
            f"Occurrence ID: {occurrence['occurrence_id']}; "
            f"Shift ID: {shift_id}; "
            f"Previous status: No Shift Occurred; "
            f"New status: {new_status}; "
            f"Original reason: {occurrence['status_reason']}; "
            f"Effective at UTC: {corrected_at_utc}"
        ),
        success=1
    )


def reconcile_staff_notice_shift_sign_on(
    conn,
    shift_id,
    signed_on_user_id,
    reconciled_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice shift reconciliation requires an active transaction."
        )

    reconciled_at_utc = format_staff_notice_utc_datetime(reconciled_at_utc)
    now_utc = parse_staff_notice_utc_datetime(reconciled_at_utc)
    shift_row = conn.execute("""
        SELECT
            shift_id,
            client_id,
            shift_date,
            shift_type,
            status,
            scheduled_start_time,
            scheduled_end_time
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()
    if shift_row is None:
        raise ValueError("Shift not found for Staff Notice reconciliation.")
    shift = dict(shift_row)
    if shift["status"] != "Open":
        raise StaffNoticeShiftSignOnError(
            "Staff Notice reconciliation requires an open shift."
        )

    assignment = conn.execute("""
        SELECT shift_staff_id, actual_start_time
        FROM shift_staff
        WHERE shift_id = ?
          AND user_id = ?
          AND active = 1
        ORDER BY shift_staff_id
        LIMIT 1
    """, (shift_id, signed_on_user_id)).fetchone()
    if assignment is None:
        raise ValueError(
            "Active shift assignment not found for Staff Notice reconciliation."
        )
    if shift["scheduled_start_time"] is None:
        shift["scheduled_start_time"] = assignment["actual_start_time"]

    result = _staff_notice_reconciliation_result()
    for notice in _load_published_staff_notices_for_reconciliation(conn):
        schedule = notice["schedule"]
        existing_specific_occurrence = None
        if (
            schedule is not None
            and schedule["shift_applicability"] == "Specific Shift"
        ):
            existing_specific_occurrence = conn.execute("""
                SELECT occurrence_id
                FROM staff_notice_occurrences
                WHERE schedule_id = ?
                  AND occurrence_kind = 'Shift'
                  AND shift_id IS NULL
                  AND planned_client_id = ?
                  AND occurrence_date = ?
                  AND planned_shift_type = ?
                  AND occurrence_status IN (
                      'Pending Shift',
                      'No Shift Occurred'
                  )
                ORDER BY occurrence_id
                LIMIT 1
            """, (
                schedule["schedule_id"],
                shift["client_id"],
                shift["shift_date"],
                shift["shift_type"]
            )).fetchone()
        if (
            existing_specific_occurrence is None
            and not _staff_notice_applies_to_shift(
                notice,
                shift,
                now_utc
            )
        ):
            continue

        occurrence_row = conn.execute("""
            SELECT *
            FROM staff_notice_occurrences
            WHERE schedule_id = ?
              AND occurrence_kind = 'Shift'
              AND shift_id = ?
            ORDER BY occurrence_id
            LIMIT 1
        """, (schedule["schedule_id"], shift_id)).fetchone()

        if occurrence_row is None and (
            schedule["shift_applicability"] == "Specific Shift"
        ):
            exact_shift_count = conn.execute("""
                SELECT COUNT(*) AS matching_shift_count
                FROM shifts
                WHERE client_id = ?
                  AND shift_date = ?
                  AND shift_type = ?
            """, (
                shift["client_id"],
                shift["shift_date"],
                shift["shift_type"]
            )).fetchone()["matching_shift_count"]
            pending_row = conn.execute("""
                SELECT *
                FROM staff_notice_occurrences
                WHERE schedule_id = ?
                  AND occurrence_kind = 'Shift'
                  AND shift_id IS NULL
                  AND planned_client_id = ?
                  AND occurrence_date = ?
                  AND planned_shift_type = ?
                  AND occurrence_status IN (
                      'Pending Shift',
                      'No Shift Occurred'
                  )
                ORDER BY occurrence_id
                LIMIT 1
            """, (
                schedule["schedule_id"],
                shift["client_id"],
                shift["shift_date"],
                shift["shift_type"]
            )).fetchone()
            if pending_row is not None and exact_shift_count != 1:
                continue
            if pending_row is not None and exact_shift_count == 1:
                pending_occurrence = dict(pending_row)
                visible_from, due_at = _staff_notice_shift_occurrence_times(
                    shift,
                    notice,
                    now_utc
                )
                visible_from_at_utc = (
                    format_staff_notice_utc_datetime(visible_from)
                    if visible_from is not None else None
                )
                due_at_utc = (
                    format_staff_notice_utc_datetime(due_at)
                    if due_at is not None else None
                )
                new_status = _staff_notice_occurrence_status(
                    visible_from,
                    now_utc
                )
                cursor = conn.execute("""
                    UPDATE staff_notice_occurrences
                    SET shift_id = ?,
                        visible_from_at_utc = ?,
                        due_at_utc = ?,
                        due_at_is_provisional = ?,
                        occurrence_status = ?,
                        shift_bound_at_utc = ?,
                        status_reason = NULL,
                        status_changed_at_utc = ?,
                        status_changed_by_user_id = ?
                    WHERE occurrence_id = ?
                      AND shift_id IS NULL
                      AND occurrence_status = ?
                """, (
                    shift_id,
                    visible_from_at_utc,
                    due_at_utc,
                    int(due_at is not None),
                    new_status,
                    reconciled_at_utc,
                    (
                        reconciled_at_utc
                        if pending_occurrence["occurrence_status"]
                        == "No Shift Occurred"
                        else pending_occurrence["status_changed_at_utc"]
                    ),
                    (
                        signed_on_user_id
                        if pending_occurrence["occurrence_status"]
                        == "No Shift Occurred"
                        else pending_occurrence["status_changed_by_user_id"]
                    ),
                    pending_occurrence["occurrence_id"],
                    pending_occurrence["occurrence_status"]
                ))
                if cursor.rowcount == 1:
                    if (
                        pending_occurrence["occurrence_status"]
                        == "No Shift Occurred"
                    ):
                        _log_staff_notice_no_shift_correction(
                            conn,
                            notice,
                            pending_occurrence,
                            shift_id,
                            new_status,
                            signed_on_user_id,
                            reconciled_at_utc
                        )
                    _log_staff_notice_occurrence_bound(
                        conn,
                        notice,
                        pending_occurrence["occurrence_id"],
                        shift_id
                    )
                occurrence_row = conn.execute("""
                    SELECT *
                    FROM staff_notice_occurrences
                    WHERE occurrence_id = ?
                """, (pending_occurrence["occurrence_id"],)).fetchone()

        if occurrence_row is None:
            visible_from, due_at = _staff_notice_shift_occurrence_times(
                shift,
                notice,
                now_utc
            )
            _insert_initial_staff_notice_occurrence(
                conn,
                notice=notice,
                schedule_id=schedule["schedule_id"],
                occurrence_kind="Shift",
                occurrence_date=shift["shift_date"],
                planned_client_id=shift["client_id"],
                planned_shift_type=shift["shift_type"],
                shift_id=shift_id,
                is_specific_shift_occurrence=int(
                    schedule["shift_applicability"] == "Specific Shift"
                ),
                visible_from_at_utc=(
                    format_staff_notice_utc_datetime(visible_from)
                    if visible_from is not None else None
                ),
                due_at_utc=(
                    format_staff_notice_utc_datetime(due_at)
                    if due_at is not None else None
                ),
                due_at_is_provisional=int(due_at is not None),
                occurrence_status=_staff_notice_occurrence_status(
                    visible_from,
                    now_utc
                ),
                created_at_utc=reconciled_at_utc,
                shift_bound_at_utc=reconciled_at_utc
            )
            result["occurrences_created"] += 1
            occurrence_row = conn.execute("""
                SELECT *
                FROM staff_notice_occurrences
                WHERE schedule_id = ?
                  AND occurrence_kind = 'Shift'
                  AND shift_id = ?
            """, (schedule["schedule_id"], shift_id)).fetchone()

        occurrence = dict(occurrence_row)
        if occurrence["occurrence_status"] in (
            "No Shift Occurred",
            "Cancelled"
        ):
            continue
        eligibility_cutoff_at_utc = (
            _staff_notice_delivery_eligibility_cutoff(
                occurrence,
                reconciled_at_utc
            )
        )
        eligible_user_ids = _load_initial_staff_notice_delivery_user_ids(
            conn,
            notice["notice_id"],
            occurrence,
            eligibility_cutoff_at_utc
        )
        if signed_on_user_id in eligible_user_ids:
            result["deliveries_assigned"] += _assign_staff_notice_delivery(
                conn,
                notice,
                occurrence,
                signed_on_user_id,
                reconciled_at_utc,
                eligibility_cutoff_at_utc,
                restore_existing=True,
                transition_actor_user_id=signed_on_user_id,
                transition_reason="Worker assigned to shift."
            )

    return result


def _log_staff_notice_delivery_transition(
    conn,
    *,
    activity_type,
    summary,
    delivery,
    actor_user_id,
    reason,
    effective_at_utc
):
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type=activity_type,
        summary=f"{summary}: {delivery['title']}",
        user_id=actor_user_id,
        client_id=delivery["client_id"],
        shift_id=delivery["shift_id"],
        related_table="staff_notice_deliveries",
        related_id=delivery["delivery_id"],
        details=(
            f"Notice ID: {delivery['notice_id']}; "
            f"Occurrence ID: {delivery['occurrence_id']}; "
            f"Recipient user ID: {delivery['user_id']}; "
            f"Reason: {reason}; Effective at UTC: {effective_at_utc}"
        ),
        success=1
    )


def _mark_staff_notice_delivery_no_longer_required(
    conn,
    delivery,
    actor_user_id,
    reason,
    effective_at_utc,
    reason_code="Shift Assignment Removed"
):
    cursor = conn.execute("""
        UPDATE staff_notice_deliveries
        SET requirement_status = 'No Longer Required',
            status_changed_at_utc = ?,
            status_changed_by_user_id = ?,
            current_reason_code = ?,
            current_reason_text = ?
        WHERE delivery_id = ?
          AND requirement_status = 'Required'
          AND NOT EXISTS (
              SELECT 1
              FROM acknowledgements ack
              WHERE ack.source_table = 'staff_notice_deliveries'
                AND ack.source_id = staff_notice_deliveries.delivery_id
                AND ack.user_id = staff_notice_deliveries.user_id
                AND ack.active = 1
          )
    """, (
        effective_at_utc,
        actor_user_id,
        reason_code,
        reason,
        delivery["delivery_id"]
    ))
    if cursor.rowcount != 1:
        return 0

    conn.execute("""
        INSERT INTO staff_notice_delivery_history
        (
            delivery_id,
            event_type,
            previous_requirement_status,
            new_requirement_status,
            previous_recipient_access,
            new_recipient_access,
            reason_code,
            reason_text,
            changed_by_user_id,
            changed_at_utc
        )
        VALUES (?, 'No Longer Required', 'Required',
                'No Longer Required', NULL, NULL, ?, ?, ?, ?)
    """, (
        delivery["delivery_id"],
        reason_code,
        reason,
        actor_user_id,
        effective_at_utc
    ))
    _log_staff_notice_delivery_transition(
        conn,
        activity_type="staff_notice_delivery_no_longer_required",
        summary="Staff Notice delivery no longer required",
        delivery=delivery,
        actor_user_id=actor_user_id,
        reason=reason,
        effective_at_utc=effective_at_utc
    )
    return 1


def _revoke_staff_notice_delivery_access(
    conn,
    delivery,
    actor_user_id,
    reason,
    effective_at_utc,
    reason_code="Shift Assignment Removed"
):
    cursor = conn.execute("""
        UPDATE staff_notice_deliveries
        SET recipient_access = 0,
            access_revoked_at_utc = ?
        WHERE delivery_id = ?
          AND recipient_access = 1
    """, (
        effective_at_utc,
        delivery["delivery_id"]
    ))
    if cursor.rowcount != 1:
        return 0

    conn.execute("""
        INSERT INTO staff_notice_delivery_history
        (
            delivery_id,
            event_type,
            previous_requirement_status,
            new_requirement_status,
            previous_recipient_access,
            new_recipient_access,
            reason_code,
            reason_text,
            changed_by_user_id,
            changed_at_utc
        )
        VALUES (?, 'Access Revoked', NULL, NULL, 1, 0, ?, ?, ?, ?)
    """, (
        delivery["delivery_id"],
        reason_code,
        reason,
        actor_user_id,
        effective_at_utc
    ))
    _log_staff_notice_delivery_transition(
        conn,
        activity_type="staff_notice_delivery_access_revoked",
        summary="Staff Notice delivery access revoked",
        delivery=delivery,
        actor_user_id=actor_user_id,
        reason=reason,
        effective_at_utc=effective_at_utc
    )
    return 1


def _restore_staff_notice_delivery_for_shift_assignment(
    conn,
    delivery,
    actor_user_id,
    reason,
    effective_at_utc
):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A Staff Notice reinstatement reason is required.")

    reason = reason.strip()
    effective_at_utc = format_staff_notice_utc_datetime(effective_at_utc)
    result = {
        "deliveries_reinstated": 0,
        "delivery_access_restored": 0
    }

    if delivery["requirement_status"] == "No Longer Required":
        cursor = conn.execute("""
            UPDATE staff_notice_deliveries
            SET requirement_status = 'Required',
                status_changed_at_utc = ?,
                status_changed_by_user_id = ?,
                current_reason_code = 'Shift Assignment Restored',
                current_reason_text = ?
            WHERE delivery_id = ?
              AND requirement_status = 'No Longer Required'
        """, (
            effective_at_utc,
            actor_user_id,
            reason,
            delivery["delivery_id"]
        ))
        if cursor.rowcount == 1:
            conn.execute("""
                INSERT INTO staff_notice_delivery_history
                (
                    delivery_id,
                    event_type,
                    previous_requirement_status,
                    new_requirement_status,
                    previous_recipient_access,
                    new_recipient_access,
                    reason_code,
                    reason_text,
                    changed_by_user_id,
                    changed_at_utc
                )
                VALUES (?, 'Reinstated', 'No Longer Required',
                        'Required', NULL, NULL, ?, ?, ?, ?)
            """, (
                delivery["delivery_id"],
                "Shift Assignment Restored",
                reason,
                actor_user_id,
                effective_at_utc
            ))
            _log_staff_notice_delivery_transition(
                conn,
                activity_type="staff_notice_delivery_reinstated",
                summary="Staff Notice delivery reinstated",
                delivery=delivery,
                actor_user_id=actor_user_id,
                reason=reason,
                effective_at_utc=effective_at_utc
            )
            result["deliveries_reinstated"] = 1

    if (
        delivery["requirement_status"] != "Cancelled"
        and delivery["recipient_access"] == 0
    ):
        cursor = conn.execute("""
            UPDATE staff_notice_deliveries
            SET recipient_access = 1,
                access_revoked_at_utc = NULL
            WHERE delivery_id = ?
              AND recipient_access = 0
              AND requirement_status <> 'Cancelled'
        """, (delivery["delivery_id"],))
        if cursor.rowcount == 1:
            conn.execute("""
                INSERT INTO staff_notice_delivery_history
                (
                    delivery_id,
                    event_type,
                    previous_requirement_status,
                    new_requirement_status,
                    previous_recipient_access,
                    new_recipient_access,
                    reason_code,
                    reason_text,
                    changed_by_user_id,
                    changed_at_utc
                )
                VALUES (?, 'Access Restored', NULL, NULL, 0, 1,
                        ?, ?, ?, ?)
            """, (
                delivery["delivery_id"],
                "Shift Assignment Restored",
                reason,
                actor_user_id,
                effective_at_utc
            ))
            _log_staff_notice_delivery_transition(
                conn,
                activity_type="staff_notice_delivery_access_restored",
                summary="Staff Notice delivery access restored",
                delivery=delivery,
                actor_user_id=actor_user_id,
                reason=reason,
                effective_at_utc=effective_at_utc
            )
            result["delivery_access_restored"] = 1

    return result


def remove_shift_staff_assignment(
    conn,
    shift_staff_id,
    removed_by_user_id,
    reason,
    effective_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Shift staff removal requires an active transaction."
        )
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A worker-removal reason is required.")
    if not _is_valid_staff_notice_identifier(shift_staff_id):
        raise ValueError("A valid shift staff assignment is required.")
    if not _is_valid_staff_notice_identifier(removed_by_user_id):
        raise ValueError("A valid removing user is required.")

    reason = reason.strip()
    effective_at_utc = format_staff_notice_utc_datetime(effective_at_utc)
    assignment = conn.execute("""
        SELECT
            ss.shift_staff_id,
            ss.shift_id,
            ss.user_id,
            ss.active,
            s.client_id,
            s.shift_date,
            s.shift_type,
            s.status AS shift_status,
            u.full_name
        FROM shift_staff ss
        JOIN shifts s
            ON ss.shift_id = s.shift_id
        JOIN users u
            ON ss.user_id = u.user_id
        WHERE ss.shift_staff_id = ?
    """, (shift_staff_id,)).fetchone()
    if assignment is None:
        raise LookupError("Shift staff assignment not found.")
    if assignment["shift_status"] != "Open":
        raise ShiftCancellationConflictError(
            "Cancelled or non-open shifts cannot be restaffed."
        )
    if assignment["active"] != 1:
        return {
            "assignments_removed": 0,
            "deliveries_no_longer_required": 0,
            "delivery_access_revoked": 0
        }

    cursor = conn.execute("""
        UPDATE shift_staff
        SET active = 0
        WHERE shift_staff_id = ?
          AND active = 1
    """, (shift_staff_id,))
    if cursor.rowcount != 1:
        return {
            "assignments_removed": 0,
            "deliveries_no_longer_required": 0,
            "delivery_access_revoked": 0
        }

    deliveries = [
        dict(row)
        for row in conn.execute("""
            SELECT
                d.*,
                o.shift_id,
                sn.notice_id,
                sn.title,
                sn.client_id
            FROM staff_notice_deliveries d
            JOIN staff_notice_occurrences o
                ON d.occurrence_id = o.occurrence_id
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            JOIN staff_notices sn
                ON sns.notice_id = sn.notice_id
            WHERE o.shift_id = ?
              AND d.user_id = ?
            ORDER BY d.delivery_id
        """, (
            assignment["shift_id"],
            assignment["user_id"]
        )).fetchall()
    ]
    result = {
        "assignments_removed": 1,
        "deliveries_no_longer_required": 0,
        "delivery_access_revoked": 0
    }

    for delivery in deliveries:
        result["deliveries_no_longer_required"] += (
            _mark_staff_notice_delivery_no_longer_required(
                conn,
                delivery,
                removed_by_user_id,
                reason,
                effective_at_utc
            )
        )
        result["delivery_access_revoked"] += (
            _revoke_staff_notice_delivery_access(
                conn,
                delivery,
                removed_by_user_id,
                reason,
                effective_at_utc
            )
        )

    log_activity(
        conn,
        activity_class="SHIFT",
        activity_type="shift_staff_removed",
        summary=f"Worker removed from shift: {assignment['full_name']}",
        user_id=removed_by_user_id,
        client_id=assignment["client_id"],
        shift_id=assignment["shift_id"],
        related_table="shift_staff",
        related_id=shift_staff_id,
        details=(
            f"Reason: {reason}; Effective at UTC: {effective_at_utc}"
        ),
        success=1
    )
    return result


class ShiftStaffCompletionError(ValueError):
    pass


def _shift_staff_recorded_start_at_utc(assignment):
    try:
        shift_date = datetime.strptime(
            assignment["shift_date"],
            "%Y-%m-%d"
        ).date()
    except (TypeError, ValueError) as error:
        raise ShiftStaffCompletionError(
            "The recorded shift date is invalid and requires repair."
        ) from error

    try:
        start_clock = _parse_staff_notice_shift_clock(
            assignment["actual_start_time"],
            "Recorded genuine shift start"
        )
    except ValueError as error:
        raise ShiftStaffCompletionError(
            "The recorded genuine start is missing or invalid and "
            "requires repair."
        ) from error
    if start_clock is None:
        raise ShiftStaffCompletionError(
            "The recorded genuine start is missing or invalid and "
            "requires repair."
        )

    shift_type = assignment["shift_type"]
    start_hour = start_clock.hour
    if shift_type == "Day":
        valid_clock = 7 <= start_hour < 15
    elif shift_type == "Afternoon":
        valid_clock = 15 <= start_hour < 23
    elif shift_type == "Overnight":
        valid_clock = start_hour >= 23 or start_hour < 7
        if start_hour < 7:
            shift_date += timedelta(days=1)
    else:
        valid_clock = False

    if not valid_clock:
        raise ShiftStaffCompletionError(
            "The recorded genuine start is inconsistent with the "
            "shift type and requires repair."
        )

    try:
        start_local = _staff_notice_resolve_local_shift_datetime(
            shift_date,
            start_clock,
            "Recorded genuine shift start"
        )
    except ValueError as error:
        raise ShiftStaffCompletionError(
            f"{error} The assignment requires separate repair."
        ) from error

    return start_local.astimezone(timezone.utc)


def complete_shift_staff_assignment(
    conn,
    shift_staff_id,
    actual_end_at_utc,
    sign_off_at_utc,
    actor_user_id,
    end_checklist_completed,
    finalization_reason=None,
    after_transition=None
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Shift staff completion requires an active transaction."
        )
    if not _is_valid_staff_notice_identifier(shift_staff_id):
        raise ShiftStaffCompletionError(
            "A valid shift staff assignment is required."
        )
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise ShiftStaffCompletionError("A valid completing user is required.")
    if end_checklist_completed not in (0, 1):
        raise ShiftStaffCompletionError(
            "Invalid end-checklist completion state."
        )

    actual_end_at_utc = format_staff_notice_utc_datetime(
        actual_end_at_utc
    )
    sign_off_at_utc = format_staff_notice_utc_datetime(
        sign_off_at_utc
    )
    assignment_row = conn.execute("""
        SELECT
            ss.*,
            s.client_id,
            s.shift_date,
            s.shift_type,
            s.status AS shift_status,
            s.closed_at,
            u.full_name
        FROM shift_staff ss
        JOIN shifts s
            ON ss.shift_id = s.shift_id
        JOIN users u
            ON ss.user_id = u.user_id
        WHERE ss.shift_staff_id = ?
    """, (shift_staff_id,)).fetchone()
    if assignment_row is None:
        raise LookupError("Shift staff assignment not found.")
    assignment = dict(assignment_row)
    stored_actual_end = assignment["actual_end_at_utc"]
    if assignment["shift_status"] != "Open":
        raise ShiftStaffCompletionError(
            "Cancelled or non-open shifts cannot be completed."
        )

    if assignment["active"] != 1:
        if stored_actual_end is None:
            raise ShiftStaffCompletionError(
                "This assignment was deactivated without a genuine "
                "actual end and requires separate repair."
            )
        if format_staff_notice_utc_datetime(
            stored_actual_end
        ) != actual_end_at_utc:
            raise ShiftStaffCompletionError(
                "This assignment is already completed with a different "
                "actual end. Use a future authorized correction workflow."
            )
        return {
            "assignment_completed": 0,
            "assignment": assignment,
            "whole_shift_end_at_utc": None,
            "staff_notice_finalization": None
        }

    if stored_actual_end is not None:
        raise ShiftStaffCompletionError(
            "This active assignment already has an actual end and "
            "requires separate repair."
        )

    cursor = conn.execute("""
        UPDATE shift_staff
        SET actual_end_at_utc = ?,
            sign_off_at = ?,
            end_checklist_completed = ?,
            active = 0
        WHERE shift_staff_id = ?
          AND active = 1
          AND actual_end_at_utc IS NULL
    """, (
        actual_end_at_utc,
        sign_off_at_utc,
        end_checklist_completed,
        shift_staff_id
    ))
    if cursor.rowcount != 1:
        raise ShiftStaffCompletionError(
            "The assignment changed while it was being completed. "
            "Please retry."
        )

    if after_transition is not None:
        after_transition()

    active_assignment = conn.execute("""
        SELECT 1
        FROM shift_staff
        WHERE shift_id = ?
          AND active = 1
        LIMIT 1
    """, (assignment["shift_id"],)).fetchone()
    whole_shift_end_at_utc = None
    finalization = None
    if active_assignment is None:
        row = conn.execute("""
            SELECT MAX(actual_end_at_utc) AS actual_end_at_utc
            FROM shift_staff
            WHERE shift_id = ?
              AND active = 0
              AND actual_end_at_utc IS NOT NULL
        """, (assignment["shift_id"],)).fetchone()
        whole_shift_end_at_utc = row["actual_end_at_utc"]
        if whole_shift_end_at_utc is not None:
            finalization = finalize_shift_notice_due_at(
                conn,
                assignment["shift_id"],
                whole_shift_end_at_utc,
                actor_user_id,
                sign_off_at_utc,
                finalization_reason
            )

    assignment["actual_end_at_utc"] = actual_end_at_utc
    assignment["sign_off_at"] = sign_off_at_utc
    assignment["end_checklist_completed"] = end_checklist_completed
    assignment["active"] = 0
    return {
        "assignment_completed": 1,
        "assignment": assignment,
        "whole_shift_end_at_utc": whole_shift_end_at_utc,
        "staff_notice_finalization": finalization
    }


def _staff_notice_deadline_adjustment_details(
    occurrence,
    old_due_at_utc,
    new_due_at_utc,
    old_due_was_provisional,
    reconciled_at_utc,
    reason
):
    return (
        f"Notice ID: {occurrence['notice_id']}; "
        f"Occurrence ID: {occurrence['occurrence_id']}; "
        f"Shift ID: {occurrence['shift_id']}; "
        f"Old due at: {old_due_at_utc}; "
        f"New due at: {new_due_at_utc}; "
        f"Prior deadline provisional: {old_due_was_provisional}; "
        f"Reason: {reason}; Effective at UTC: {reconciled_at_utc}"
    )


def _log_staff_notice_occurrence_due_at_adjusted(
    conn,
    occurrence,
    old_due_at_utc,
    new_due_at_utc,
    old_due_was_provisional,
    actor_user_id,
    reconciled_at_utc,
    reason
):
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_occurrence_due_at_adjusted",
        summary=(
            "Staff Notice occurrence deadline adjusted: "
            f"{occurrence['title']}"
        ),
        user_id=actor_user_id,
        client_id=occurrence["client_id"],
        shift_id=occurrence["shift_id"],
        related_table="staff_notice_occurrences",
        related_id=occurrence["occurrence_id"],
        details=_staff_notice_deadline_adjustment_details(
            occurrence,
            old_due_at_utc,
            new_due_at_utc,
            old_due_was_provisional,
            reconciled_at_utc,
            reason
        ),
        success=1
    )


def _log_staff_notice_acknowledgement_classification_changed(
    conn,
    occurrence,
    acknowledgement,
    old_classification,
    new_classification,
    old_due_at_utc,
    new_due_at_utc,
    actor_user_id,
    reconciled_at_utc,
    reason
):
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type=(
            "staff_notice_acknowledgement_classification_changed"
        ),
        summary=(
            "Staff Notice acknowledgement classification changed: "
            f"{occurrence['title']}"
        ),
        user_id=actor_user_id,
        client_id=occurrence["client_id"],
        shift_id=occurrence["shift_id"],
        related_table="acknowledgements",
        related_id=acknowledgement["acknowledgement_id"],
        details=(
            f"Notice ID: {occurrence['notice_id']}; "
            f"Acknowledgement ID: "
            f"{acknowledgement['acknowledgement_id']}; "
            f"Delivery ID: {acknowledgement['delivery_id']}; "
            f"Occurrence ID: {occurrence['occurrence_id']}; "
            f"Shift ID: {occurrence['shift_id']}; "
            f"Old classification: {old_classification}; "
            f"New classification: {new_classification}; "
            f"Old due at: {old_due_at_utc}; "
            f"New due at: {new_due_at_utc}; "
            f"Acknowledged at: "
            f"{acknowledgement['acknowledged_at']}; "
            f"Reason: {reason}; "
            f"Effective at UTC: {reconciled_at_utc}"
        ),
        success=1
    )


def finalize_shift_notice_due_at(
    conn,
    shift_id,
    actual_end_at_utc,
    actor_user_id,
    reconciled_at_utc,
    reason=None
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Shift deadline finalization requires an active transaction."
        )
    if not _is_valid_staff_notice_identifier(shift_id):
        raise ValueError("A valid shift is required.")
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise ValueError("A valid actor is required.")
    if reason is None:
        reason = "Actual shift end recorded."
    elif not isinstance(reason, str) or not reason.strip():
        raise ValueError(
            "A supplied shift-end correction reason cannot be empty."
        )
    else:
        reason = reason.strip()

    actual_end_at_utc = format_staff_notice_utc_datetime(
        actual_end_at_utc
    )
    reconciled_at_utc = format_staff_notice_utc_datetime(
        reconciled_at_utc
    )
    shift = conn.execute("""
        SELECT shift_id, client_id, actual_end_at_utc
        FROM shifts
        WHERE shift_id = ?
    """, (shift_id,)).fetchone()
    if shift is None:
        raise LookupError("Shift not found.")

    shift_cursor = conn.execute("""
        UPDATE shifts
        SET actual_end_at_utc = ?
        WHERE shift_id = ?
          AND actual_end_at_utc IS NOT ?
    """, (
        actual_end_at_utc,
        shift_id,
        actual_end_at_utc
    ))
    result = {
        "shift_end_updated": shift_cursor.rowcount,
        "occurrences_adjusted": 0,
        "acknowledgement_classifications_changed": 0
    }
    occurrences = [
        dict(row)
        for row in conn.execute("""
            SELECT
                o.*,
                sn.notice_id,
                sn.title,
                sn.client_id
            FROM staff_notice_occurrences o
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            JOIN staff_notices sn
                ON sns.notice_id = sn.notice_id
            WHERE o.occurrence_kind = 'Shift'
              AND o.shift_id = ?
            ORDER BY o.occurrence_id
        """, (shift_id,)).fetchall()
    ]

    for occurrence in occurrences:
        old_due_at_utc = occurrence["due_at_utc"]
        old_due_was_provisional = occurrence["due_at_is_provisional"]
        acknowledgements = [
            dict(row)
            for row in conn.execute("""
                SELECT
                    ack.acknowledgement_id,
                    ack.acknowledged_at,
                    d.delivery_id,
                    d.requirement_status,
                    d.first_viewed_at_utc
                FROM staff_notice_deliveries d
                JOIN acknowledgements ack
                    ON ack.source_table = 'staff_notice_deliveries'
                   AND ack.source_id = d.delivery_id
                   AND ack.user_id = d.user_id
                   AND ack.active = 1
                WHERE d.occurrence_id = ?
                ORDER BY d.delivery_id, ack.acknowledgement_id
            """, (occurrence["occurrence_id"],)).fetchall()
        ]
        old_classifications = {
            acknowledgement["acknowledgement_id"]:
                get_recipient_staff_notice_status(
                    active_acknowledgement_at_utc=(
                        acknowledgement["acknowledged_at"]
                    ),
                    due_at_utc=old_due_at_utc,
                    requirement_status=(
                        acknowledgement["requirement_status"]
                    ),
                    first_viewed_at_utc=(
                        acknowledgement["first_viewed_at_utc"]
                    )
                )
            for acknowledgement in acknowledgements
        }
        occurrence_cursor = conn.execute("""
            UPDATE staff_notice_occurrences
            SET due_at_utc = ?,
                due_at_is_provisional = 0,
                due_at_updated_at_utc = ?
            WHERE occurrence_id = ?
              AND (
                  due_at_utc IS NOT ?
                  OR due_at_is_provisional <> 0
              )
        """, (
            actual_end_at_utc,
            reconciled_at_utc,
            occurrence["occurrence_id"],
            actual_end_at_utc
        ))
        if occurrence_cursor.rowcount != 1:
            continue

        result["occurrences_adjusted"] += 1
        _log_staff_notice_occurrence_due_at_adjusted(
            conn,
            occurrence,
            old_due_at_utc,
            actual_end_at_utc,
            old_due_was_provisional,
            actor_user_id,
            reconciled_at_utc,
            reason
        )

        for acknowledgement in acknowledgements:
            new_classification = get_recipient_staff_notice_status(
                active_acknowledgement_at_utc=(
                    acknowledgement["acknowledged_at"]
                ),
                due_at_utc=actual_end_at_utc,
                requirement_status=(
                    acknowledgement["requirement_status"]
                ),
                first_viewed_at_utc=(
                    acknowledgement["first_viewed_at_utc"]
                )
            )
            old_classification = old_classifications[
                acknowledgement["acknowledgement_id"]
            ]
            if new_classification == old_classification:
                continue

            _log_staff_notice_acknowledgement_classification_changed(
                conn,
                occurrence,
                acknowledgement,
                old_classification,
                new_classification,
                old_due_at_utc,
                actual_end_at_utc,
                actor_user_id,
                reconciled_at_utc,
                reason
            )
            result["acknowledgement_classifications_changed"] += 1

    return result


def _load_published_staff_notices_for_reconciliation(conn):
    notice_ids = [
        row["notice_id"]
        for row in conn.execute("""
            SELECT sn.notice_id
            FROM staff_notices sn
            WHERE sn.status = 'Published'
            ORDER BY sn.notice_id
        """).fetchall()
    ]
    return [
        _load_staff_notice_publish_record(conn, notice_id)
        for notice_id in notice_ids
    ]


def reconcile_staff_notice_non_shift_requirements_in_transaction(
    conn,
    now_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice request reconciliation requires an active "
            "transaction."
        )

    now_utc = parse_staff_notice_utc_datetime(now_utc)
    reconciled_at_utc = format_staff_notice_utc_datetime(now_utc)
    result = _staff_notice_reconciliation_result()

    for notice in _load_published_staff_notices_for_reconciliation(conn):
        if parse_staff_notice_utc_datetime(
            notice["published_at_utc"]
        ) > now_utc:
            continue

        _merge_staff_notice_reconciliation_result(
            result,
            reconcile_staff_notice_audience_eligibility(
                conn,
                notice,
                reconciled_at_utc
            )
        )
        _merge_staff_notice_reconciliation_result(
            result,
            generate_due_staff_notice_occurrences(
                conn,
                notice,
                reconciled_at_utc
            )
        )
        _merge_staff_notice_reconciliation_result(
            result,
            reconcile_staff_notice_deliveries(
                conn,
                notice,
                reconciled_at_utc
            )
        )

    return result


def reconcile_staff_notice_user_lifecycle_in_transaction(
    conn,
    user_id,
    actor_user_id,
    effective_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice user lifecycle reconciliation requires an "
            "active transaction."
        )
    actor = get_active_authenticated_user(conn, actor_user_id)
    if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
        raise PermissionError(
            "Current user is not allowed to manage users."
        )
    target = conn.execute("""
        SELECT user_id, role, active
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()
    if target is None:
        raise LookupError("User not found for Staff Notice reconciliation.")

    result = reconcile_staff_notice_non_shift_requirements_in_transaction(
        conn,
        effective_at_utc
    )
    verified_target = conn.execute("""
        SELECT user_id, role, active
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()
    if verified_target is None or tuple(verified_target) != tuple(target):
        raise RuntimeError(
            "User lifecycle reconciliation verification failed."
        )
    return result


def reconcile_staff_notice_non_shift_requirements(now_utc=None):
    if now_utc is None:
        now_utc = get_application_now_utc()
    else:
        now_utc = parse_staff_notice_utc_datetime(now_utc)

    conn = None
    primary_error = None

    try:
        conn = get_db()
        conn.execute("BEGIN IMMEDIATE")

        result = reconcile_staff_notice_non_shift_requirements_in_transaction(
            conn,
            now_utc
        )
        conn.commit()
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
                    "Staff Notice reconciliation rollback also failed: "
                    f"{rollback_error}"
                )

        raise

    finally:
        if conn is not None:
            try:
                conn.close()
            except BaseException as close_error:
                if primary_error is None:
                    raise

                _preserve_staff_notice_cleanup_error(
                    primary_error,
                    "staff_notice_close_error",
                    close_error,
                    "Staff Notice database close also failed: "
                    f"{close_error}"
                )


def reconcile_staff_notice_tracking_in_transaction(
    conn,
    notice,
    now_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice tracking reconciliation requires an active "
            "transaction."
        )
    if notice["status"] != "Published":
        raise ValueError(
            "Only published Staff Notices can be reconciled for tracking."
        )

    now_utc = parse_staff_notice_utc_datetime(now_utc)
    result = _staff_notice_reconciliation_result()
    if parse_staff_notice_utc_datetime(
        notice["published_at_utc"]
    ) > now_utc:
        return result

    reconciled_at_utc = format_staff_notice_utc_datetime(now_utc)
    _merge_staff_notice_reconciliation_result(
        result,
        reconcile_staff_notice_audience_eligibility(
            conn,
            notice,
            reconciled_at_utc
        )
    )
    _merge_staff_notice_reconciliation_result(
        result,
        generate_due_staff_notice_occurrences(
            conn,
            notice,
            reconciled_at_utc
        )
    )
    _merge_staff_notice_reconciliation_result(
        result,
        reconcile_staff_notice_deliveries(
            conn,
            notice,
            reconciled_at_utc
        )
    )
    return result


def _staff_notice_tracking_context(occurrence):
    if occurrence["occurrence_kind"] == "Shift":
        shift_date = (
            occurrence["shift_date"]
            or occurrence["occurrence_date"]
        )
        shift_type = (
            occurrence["shift_type"]
            or occurrence["planned_shift_type"]
        )
        client_name = (
            occurrence["shift_client_name"]
            or occurrence["planned_client_name"]
        )
        parts = [
            part
            for part in (client_name, shift_date, shift_type)
            if part
        ]
        if occurrence["shift_id"] is None:
            parts.append("Pending Shift")
        return " — ".join(parts)
    if occurrence["occurrence_date"] is not None:
        return occurrence["occurrence_date"]
    return "General"


def _load_staff_notice_tracking(conn, notice_id, now_utc):
    now_utc = parse_staff_notice_utc_datetime(now_utc)
    notice = _load_staff_notice_admin_record(conn, notice_id)
    if notice is None or notice["status"] not in (
        "Published",
        "Withdrawn",
        "Replaced"
    ):
        return None

    notice["summary"] = build_staff_notice_plain_language_summary(notice)
    for field_name in (
        "created_at_utc",
        "published_at_utc",
        "effective_start_at_utc",
        "expires_at_utc",
        "withdrawn_at_utc",
        "replaced_at_utc"
    ):
        display_name = field_name.replace("_at_utc", "_display")
        notice[display_name] = (
            format_staff_notice_local_datetime(notice[field_name])
            if notice[field_name] is not None
            else None
        )

    occurrences = [
        dict(row)
        for row in conn.execute("""
            SELECT
                o.*,
                s.shift_date,
                s.shift_type,
                shift_client.client_name AS shift_client_name,
                planned_client.client_name AS planned_client_name,
                (
                    SELECT COUNT(*)
                    FROM shifts exact_shift
                    WHERE exact_shift.client_id = o.planned_client_id
                      AND exact_shift.shift_date = o.occurrence_date
                      AND exact_shift.shift_type = o.planned_shift_type
                ) AS exact_matching_shift_count
            FROM staff_notice_occurrences o
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            LEFT JOIN shifts s
                ON o.shift_id = s.shift_id
            LEFT JOIN clients shift_client
                ON s.client_id = shift_client.client_id
            LEFT JOIN clients planned_client
                ON o.planned_client_id = planned_client.client_id
            WHERE sns.notice_id = ?
            ORDER BY
                COALESCE(
                    o.visible_from_at_utc,
                    o.occurrence_date,
                    o.created_at_utc
                ),
                o.occurrence_id
        """, (notice_id,)).fetchall()
    ]
    occurrence_by_id = {}
    for occurrence in occurrences:
        occurrence["context"] = _staff_notice_tracking_context(occurrence)
        occurrence["visible_from_display"] = (
            format_staff_notice_local_datetime(
                occurrence["visible_from_at_utc"]
            )
            if occurrence["visible_from_at_utc"] is not None
            else None
        )
        occurrence["due_at_display"] = (
            format_staff_notice_local_datetime(
                occurrence["due_at_utc"]
            )
            if occurrence["due_at_utc"] is not None
            else None
        )
        occurrence["deliveries"] = []
        occurrence["can_confirm_no_shift_occurred"] = False
        occurrence["expected_shift_end_display"] = None
        if (
            occurrence["occurrence_kind"] == "Shift"
            and occurrence["is_specific_shift_occurrence"] == 1
            and occurrence["shift_id"] is None
            and occurrence["occurrence_status"] == "Pending Shift"
        ):
            expected_shift_end = (
                _staff_notice_pending_shift_expected_end_at_utc(occurrence)
            )
            occurrence["expected_shift_end_display"] = (
                format_staff_notice_local_datetime(expected_shift_end)
            )
            occurrence["can_confirm_no_shift_occurred"] = (
                notice["status"] == "Published"
                and occurrence["exact_matching_shift_count"] == 0
                and expected_shift_end < now_utc
            )
        occurrence_by_id[occurrence["occurrence_id"]] = occurrence

    deliveries = [
        dict(row)
        for row in conn.execute("""
            SELECT
                d.*,
                o.due_at_utc,
                o.occurrence_status,
                o.shift_id,
                recipient.full_name AS recipient_name,
                recipient.role AS recipient_role,
                recipient.active AS recipient_active,
                viewer.full_name AS viewer_name,
                ack.acknowledgement_id,
                ack.acknowledged_at,
                ack.acknowledgement_type,
                (
                    SELECT MIN(ss.shift_staff_id)
                    FROM shift_staff ss
                    WHERE ss.shift_id = o.shift_id
                      AND ss.user_id = d.user_id
                      AND ss.active = 1
                ) AS active_shift_staff_id,
                (
                    SELECT COUNT(*)
                    FROM shift_staff ss
                    WHERE ss.shift_id = o.shift_id
                      AND ss.user_id = d.user_id
                      AND ss.active = 1
                ) AS active_shift_staff_count
            FROM staff_notice_deliveries d
            JOIN staff_notice_occurrences o
                ON d.occurrence_id = o.occurrence_id
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            JOIN users recipient
                ON d.user_id = recipient.user_id
            LEFT JOIN users viewer
                ON d.viewed_by_user_id = viewer.user_id
            LEFT JOIN acknowledgements ack
                ON ack.source_table = 'staff_notice_deliveries'
               AND ack.source_id = d.delivery_id
               AND ack.user_id = d.user_id
               AND ack.active = 1
            WHERE sns.notice_id = ?
            ORDER BY d.delivery_id
        """, (notice_id,)).fetchall()
    ]
    delivery_by_id = {
        delivery["delivery_id"]: delivery
        for delivery in deliveries
    }

    for delivery in deliveries:
        delivery["derived_status"] = get_recipient_staff_notice_status(
            active_acknowledgement_at_utc=delivery["acknowledged_at"],
            due_at_utc=delivery["due_at_utc"],
            requirement_status=delivery["requirement_status"],
            first_viewed_at_utc=delivery["first_viewed_at_utc"]
        )
        delivery["overdue"] = (
            delivery["acknowledged_at"] is None
            and delivery["requirement_status"] == "Required"
            and delivery["due_at_utc"] is not None
            and parse_staff_notice_utc_datetime(
                delivery["due_at_utc"]
            ) < now_utc
        )
        delivery["outstanding"] = (
            delivery["derived_status"] in (
                "Not Viewed",
                "Viewed – Awaiting Acknowledgement"
            )
            and delivery["requirement_status"] == "Required"
        )
        delivery["can_remove_shift_assignment"] = (
            delivery["shift_id"] is not None
            and delivery["active_shift_staff_count"] == 1
            and delivery["occurrence_status"]
            not in ("No Shift Occurred", "Cancelled")
        )
        delivery["can_reinstate"] = (
            delivery["can_remove_shift_assignment"]
            and delivery["recipient_active"] == 1
            and delivery["requirement_status"] != "Cancelled"
            and (
                delivery["requirement_status"] == "No Longer Required"
                or delivery["recipient_access"] == 0
            )
        )
        delivery["can_mark_no_longer_required"] = (
            notice["status"] == "Published"
            and delivery["requirement_status"] == "Required"
            and delivery["acknowledgement_id"] is None
            and delivery["occurrence_status"]
            not in ("No Shift Occurred", "Cancelled")
        )
        delivery["can_invalidate_acknowledgement"] = (
            delivery["acknowledgement_id"] is not None
            and delivery["acknowledgement_type"] == "Acknowledgement"
            and notice["status"] == "Published"
            and delivery["requirement_status"] == "Required"
            and delivery["recipient_access"] == 1
            and delivery["first_viewed_at_utc"] is not None
            and delivery["viewed_by_user_id"] == delivery["user_id"]
            and delivery["occurrence_status"]
            not in ("No Shift Occurred", "Cancelled")
        )
        for field_name, display_name in (
            ("assigned_at_utc", "assigned_display"),
            (
                "eligibility_cutoff_at_utc",
                "eligibility_cutoff_display"
            ),
            ("first_viewed_at_utc", "first_viewed_display"),
            ("acknowledged_at", "acknowledged_display"),
            ("due_at_utc", "due_display"),
            ("status_changed_at_utc", "status_changed_display"),
            ("access_revoked_at_utc", "access_revoked_display")
        ):
            delivery[display_name] = (
                format_staff_notice_local_datetime(delivery[field_name])
                if delivery[field_name] is not None
                else None
            )
        delivery["context"] = occurrence_by_id[
            delivery["occurrence_id"]
        ]["context"]
        delivery["history"] = []
        delivery["acknowledgement_history"] = []
        occurrence_by_id[
            delivery["occurrence_id"]
        ]["deliveries"].append(delivery)

    if deliveries:
        placeholders = ", ".join("?" for _ in deliveries)
        delivery_ids = tuple(delivery_by_id)
        history_rows = conn.execute(f"""
            SELECT
                h.*,
                actor.full_name AS changed_by_name
            FROM staff_notice_delivery_history h
            LEFT JOIN users actor
                ON h.changed_by_user_id = actor.user_id
            WHERE h.delivery_id IN ({placeholders})
            ORDER BY
                h.changed_at_utc,
                h.delivery_history_id
        """, delivery_ids).fetchall()
        for row in history_rows:
            history = dict(row)
            history["changed_at_display"] = (
                format_staff_notice_local_datetime(
                    history["changed_at_utc"]
                )
            )
            delivery_by_id[history["delivery_id"]]["history"].append(
                history
            )

        acknowledgement_rows = conn.execute(f"""
            SELECT
                ack.*,
                invalidator.full_name AS invalidated_by_name
            FROM acknowledgements ack
            LEFT JOIN users invalidator
                ON ack.invalidated_by_user_id = invalidator.user_id
            WHERE ack.source_table = 'staff_notice_deliveries'
              AND ack.source_id IN ({placeholders})
            ORDER BY
                ack.acknowledged_at,
                ack.acknowledgement_id
        """, delivery_ids).fetchall()
        for row in acknowledgement_rows:
            acknowledgement = dict(row)
            acknowledgement["acknowledged_at_display"] = (
                format_staff_notice_local_datetime(
                    acknowledgement["acknowledged_at"]
                )
            )
            acknowledgement["invalidated_at_display"] = (
                format_staff_notice_local_datetime(
                    acknowledgement["invalidated_at_utc"]
                )
                if acknowledgement["invalidated_at_utc"] is not None
                else None
            )
            delivery_by_id[
                acknowledgement["source_id"]
            ]["acknowledgement_history"].append(acknowledgement)

    status_counts = {
        status: 0
        for status in (
            "Not Viewed",
            "Viewed – Awaiting Acknowledgement",
            "Acknowledged",
            "Acknowledged Late",
            "No Longer Required",
            "Cancelled"
        )
    }
    for delivery in deliveries:
        status_counts[delivery["derived_status"]] += 1

    current_deliveries = sorted(
        (delivery for delivery in deliveries if delivery["outstanding"]),
        key=lambda delivery: (
            0 if delivery["overdue"] else 1,
            delivery["due_at_utc"] or "9999-12-31T23:59:59Z",
            delivery["recipient_name"].casefold(),
            delivery["occurrence_id"],
            delivery["delivery_id"]
        )
    )
    historical_deliveries = sorted(
        (delivery for delivery in deliveries if not delivery["outstanding"]),
        key=lambda delivery: (
            delivery["assigned_at_utc"],
            delivery["delivery_id"]
        ),
        reverse=True
    )
    return {
        "notice": notice,
        "occurrences": occurrences,
        "current_deliveries": current_deliveries,
        "historical_deliveries": historical_deliveries,
        "counts": {
            "occurrences": len(occurrences),
            "deliveries": len(deliveries),
            "outstanding": len(current_deliveries),
            "overdue": sum(
                1 for delivery in deliveries if delivery["overdue"]
            ),
            **status_counts
        }
    }


class StaffNoticeRecipientError(ValueError):
    pass


def _get_authenticated_staff_notice_recipient(conn):
    user_id = session.get("user_id")
    if not _is_valid_staff_notice_identifier(user_id):
        raise PermissionError("Staff Notice recipient login required.")

    user = conn.execute("""
        SELECT user_id, full_name, role, active
        FROM users
        WHERE user_id = ?
          AND active = 1
    """, (user_id,)).fetchone()
    if user is None:
        raise PermissionError("Staff Notice recipient access denied.")
    return user


def _load_recipient_staff_notice_deliveries(
    conn,
    user_id,
    now_utc
):
    now_utc = parse_staff_notice_utc_datetime(now_utc)
    rows = conn.execute("""
        SELECT
            d.*,
            o.occurrence_kind,
            o.occurrence_date,
            o.planned_shift_type,
            o.shift_id,
            o.due_at_utc,
            o.occurrence_status,
            sn.notice_id,
            sn.title,
            sn.notice_text,
            sn.priority,
            sn.client_id,
            sn.status AS notice_status,
            sn.effective_start_at_utc,
            sn.expires_at_utc,
            sn.published_at_utc,
            s.shift_date,
            s.shift_type,
            ack.acknowledgement_id,
            ack.acknowledged_at
        FROM staff_notice_deliveries d
        JOIN staff_notice_occurrences o
            ON d.occurrence_id = o.occurrence_id
        JOIN staff_notice_schedules sns
            ON o.schedule_id = sns.schedule_id
        JOIN staff_notices sn
            ON sns.notice_id = sn.notice_id
        LEFT JOIN shifts s
            ON o.shift_id = s.shift_id
        LEFT JOIN acknowledgements ack
            ON ack.source_table = 'staff_notice_deliveries'
           AND ack.source_id = d.delivery_id
           AND ack.user_id = d.user_id
           AND ack.active = 1
        WHERE d.user_id = ?
        ORDER BY d.delivery_id
    """, (user_id,)).fetchall()
    deliveries = []

    for row in rows:
        delivery = dict(row)
        delivery["derived_status"] = get_recipient_staff_notice_status(
            active_acknowledgement_at_utc=delivery["acknowledged_at"],
            due_at_utc=delivery["due_at_utc"],
            requirement_status=delivery["requirement_status"],
            first_viewed_at_utc=delivery["first_viewed_at_utc"]
        )
        delivery["readable"] = (
            delivery["recipient_access"] == 1
            and delivery["notice_status"] == "Published"
            and delivery["occurrence_status"]
            not in ("No Shift Occurred", "Cancelled")
        )
        delivery["overdue"] = (
            delivery["acknowledged_at"] is None
            and delivery["requirement_status"] == "Required"
            and delivery["due_at_utc"] is not None
            and parse_staff_notice_utc_datetime(
                delivery["due_at_utc"]
            ) < now_utc
        )
        expires_at = (
            parse_staff_notice_utc_datetime(
                delivery["expires_at_utc"]
            )
            if delivery["expires_at_utc"] is not None
            else None
        )
        effective_start = parse_staff_notice_utc_datetime(
            delivery["effective_start_at_utc"]
        )
        delivery["notice_is_current"] = (
            effective_start <= now_utc
            and (expires_at is None or expires_at >= now_utc)
            and delivery["notice_status"] == "Published"
        )
        delivery["context"] = None
        if delivery["occurrence_kind"] == "Shift":
            context_date = (
                delivery["shift_date"]
                or delivery["occurrence_date"]
            )
            context_type = (
                delivery["shift_type"]
                or delivery["planned_shift_type"]
            )
            delivery["context"] = (
                f"{context_type} shift — {context_date}"
            )
        elif delivery["occurrence_date"] is not None:
            delivery["context"] = delivery["occurrence_date"]
        delivery["due_at_display"] = (
            format_staff_notice_local_datetime(
                delivery["due_at_utc"]
            )
            if delivery["due_at_utc"] is not None
            else None
        )
        delivery["first_viewed_at_display"] = (
            format_staff_notice_local_datetime(
                delivery["first_viewed_at_utc"]
            )
            if delivery["first_viewed_at_utc"] is not None
            else None
        )
        delivery["acknowledged_at_display"] = (
            format_staff_notice_local_datetime(
                delivery["acknowledged_at"]
            )
            if delivery["acknowledged_at"] is not None
            else None
        )
        deliveries.append(delivery)

    return deliveries


def _staff_notice_dashboard_sort_key(delivery):
    status = delivery["derived_status"]
    acknowledged = status in ("Acknowledged", "Acknowledged Late")
    if delivery["overdue"] and not acknowledged:
        group = 0
    elif (
        delivery["notice_is_current"]
        and status == "Not Viewed"
    ):
        group = 1
    elif (
        delivery["notice_is_current"]
        and status == "Viewed – Awaiting Acknowledgement"
    ):
        group = 2
    elif (
        delivery["notice_is_current"]
        and delivery["requirement_status"] == "Required"
        and not acknowledged
    ):
        group = 3
    elif delivery["notice_is_current"] and acknowledged:
        group = 4
    else:
        group = 5

    priority_rank = {
        "Urgent": 0,
        "Important": 1,
        "Normal": 2
    }[delivery["priority"]]
    due_at = delivery["due_at_utc"] or "9999-12-31T23:59:59Z"
    published_at_rank = (
        -parse_staff_notice_utc_datetime(
            delivery["published_at_utc"]
        ).timestamp()
        if delivery["published_at_utc"] is not None
        else float("inf")
    )
    return (
        group,
        priority_rank,
        due_at,
        published_at_rank,
        delivery["delivery_id"]
    )


def _get_staff_notice_recipient_collections(
    conn,
    user_id,
    now_utc
):
    deliveries = _load_recipient_staff_notice_deliveries(
        conn,
        user_id,
        now_utc
    )
    dashboard = sorted(
        (
            delivery
            for delivery in deliveries
            if delivery["readable"]
        ),
        key=_staff_notice_dashboard_sort_key
    )[:5]
    current = sorted(
        (
            delivery
            for delivery in deliveries
            if delivery["derived_status"] in (
                "Not Viewed",
                "Viewed – Awaiting Acknowledgement"
            )
            and delivery["requirement_status"] == "Required"
        ),
        key=_staff_notice_dashboard_sort_key
    )
    history = sorted(
        (
            delivery
            for delivery in deliveries
            if delivery not in current
        ),
        key=lambda delivery: (
            delivery["assigned_at_utc"],
            delivery["delivery_id"]
        ),
        reverse=True
    )
    return {
        "dashboard": dashboard,
        "current": current,
        "history": history,
        "all": deliveries,
        "outstanding_count": len(current)
    }


def _load_management_staff_notice_dashboard(user_id, now_utc=None):
    conn = None
    try:
        conn = get_db()
        conn.execute("BEGIN IMMEDIATE")
        if now_utc is None:
            now_utc = get_application_now_utc()
        actor = get_active_authenticated_user(conn, user_id)
        if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
            raise PermissionError(
                "An active management user is required."
            )
        reconcile_staff_notice_non_shift_requirements_in_transaction(
            conn,
            now_utc
        )
        collections = _get_staff_notice_recipient_collections(
            conn,
            actor["user_id"],
            now_utc
        )
        conn.commit()
        return collections
    except BaseException:
        if conn is not None:
            try:
                conn.rollback()
            except BaseException:
                pass
        raise
    finally:
        if conn is not None:
            conn.close()


def _load_staff_notice_recipient_delivery(
    conn,
    delivery_id,
    user_id,
    now_utc
):
    deliveries = _load_recipient_staff_notice_deliveries(
        conn,
        user_id,
        now_utc
    )
    return next(
        (
            delivery
            for delivery in deliveries
            if delivery["delivery_id"] == delivery_id
        ),
        None
    )


def _record_staff_notice_first_view(
    conn,
    delivery,
    viewer_user_id,
    viewed_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice viewing requires an active transaction."
        )
    if (
        delivery["first_viewed_at_utc"] is None
        and delivery["viewed_by_user_id"] is not None
    ) or (
        delivery["first_viewed_at_utc"] is not None
        and delivery["viewed_by_user_id"] is None
    ):
        raise StaffNoticeRecipientError(
            "This delivery has inconsistent viewing history."
        )
    if (
        delivery["acknowledgement_id"] is not None
        and (
            delivery["first_viewed_at_utc"] is None
            or parse_staff_notice_utc_datetime(
                delivery["acknowledged_at"]
            ) < parse_staff_notice_utc_datetime(
                delivery["first_viewed_at_utc"]
            )
        )
    ):
        raise StaffNoticeRecipientError(
            "This delivery has inconsistent acknowledgement history."
        )
    if delivery["first_viewed_at_utc"] is not None:
        return 0

    viewed_at_utc = format_staff_notice_utc_datetime(viewed_at_utc)
    cursor = conn.execute("""
        UPDATE staff_notice_deliveries
        SET first_viewed_at_utc = ?,
            viewed_by_user_id = ?
        WHERE delivery_id = ?
          AND user_id = ?
          AND first_viewed_at_utc IS NULL
          AND viewed_by_user_id IS NULL
          AND recipient_access = 1
    """, (
        viewed_at_utc,
        viewer_user_id,
        delivery["delivery_id"],
        viewer_user_id
    ))
    if cursor.rowcount != 1:
        current = conn.execute("""
            SELECT first_viewed_at_utc, viewed_by_user_id
            FROM staff_notice_deliveries
            WHERE delivery_id = ?
              AND user_id = ?
        """, (
            delivery["delivery_id"],
            viewer_user_id
        )).fetchone()
        if (
            current is not None
            and current["first_viewed_at_utc"] is not None
            and current["viewed_by_user_id"] == viewer_user_id
        ):
            return 0
        raise StaffNoticeRecipientError(
            "The notice became unavailable while it was being opened."
        )

    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_viewed",
        summary=f"Staff Notice viewed: {delivery['title']}",
        user_id=viewer_user_id,
        client_id=delivery["client_id"],
        shift_id=delivery["shift_id"],
        related_table="staff_notice_deliveries",
        related_id=delivery["delivery_id"],
        details=(
            f"Notice ID: {delivery['notice_id']}; "
            f"Occurrence ID: {delivery['occurrence_id']}; "
            f"Delivery ID: {delivery['delivery_id']}; "
            f"Recipient User ID: {viewer_user_id}; "
            f"Viewer User ID: {viewer_user_id}; "
            f"Viewed at UTC: {viewed_at_utc}"
        ),
        success=1
    )
    return 1


def _acknowledge_staff_notice_delivery(
    conn,
    delivery,
    user_id,
    acknowledged_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice acknowledgement requires an active transaction."
        )
    if delivery["user_id"] != user_id:
        raise PermissionError("Staff Notice delivery access denied.")
    if not delivery["readable"]:
        raise StaffNoticeRecipientError(
            "This Staff Notice is no longer available to acknowledge."
        )
    if delivery["requirement_status"] != "Required":
        raise StaffNoticeRecipientError(
            "This Staff Notice no longer requires acknowledgement."
        )
    if delivery["first_viewed_at_utc"] is None:
        raise StaffNoticeRecipientError(
            "Open the full Staff Notice before acknowledging it."
        )
    if delivery["acknowledgement_id"] is not None:
        return {
            "acknowledgement_id": delivery["acknowledgement_id"],
            "created": 0
        }

    acknowledged_at_utc = format_staff_notice_utc_datetime(
        acknowledged_at_utc
    )
    details = (
        f"Notice ID: {delivery['notice_id']}; "
        f"Occurrence ID: {delivery['occurrence_id']}; "
        f"Delivery ID: {delivery['delivery_id']}; "
        f"Recipient User ID: {user_id}; "
        f"Actor User ID: {user_id}; "
        f"Acknowledged at UTC: {acknowledged_at_utc}"
    )
    acknowledgement_id = create_acknowledgement(
        conn,
        "staff_notice_deliveries",
        delivery["delivery_id"],
        user_id,
        acknowledgement_type="Acknowledgement",
        client_id=delivery["client_id"],
        shift_id=delivery["shift_id"],
        acknowledged_at=acknowledged_at_utc,
        activity_details=details
    )
    return {
        "acknowledgement_id": acknowledgement_id,
        "created": 1
    }


def _publish_staff_notice_in_transaction(
    conn,
    notice_id,
    actor_user_id,
    now_utc,
    expected_updated_at_utc=None
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

    if (
        expected_updated_at_utc is not None
        and _staff_notice_draft_token(preview["notice"])
        != expected_updated_at_utc
    ):
        raise StaffNoticeStalePublicationError(
            "This Staff Notice draft changed after publication review. "
            "Reload it and review the current draft before publishing."
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
    _create_initial_staff_notice_occurrences(
        conn,
        preview,
        published_at_utc
    )
    _create_initial_staff_notice_deliveries(
        conn,
        preview,
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

    publication_counts = conn.execute("""
        SELECT
            (
                SELECT COUNT(*)
                FROM staff_notice_audience_eligibility_periods ep
                JOIN staff_notice_audiences a
                    ON ep.audience_id = a.audience_id
                WHERE a.notice_id = ?
            ) AS eligibility_count,
            (
                SELECT COUNT(*)
                FROM staff_notice_occurrences o
                JOIN staff_notice_schedules s
                    ON o.schedule_id = s.schedule_id
                WHERE s.notice_id = ?
            ) AS occurrence_count,
            (
                SELECT COUNT(*)
                FROM staff_notice_deliveries d
                JOIN staff_notice_occurrences o
                    ON d.occurrence_id = o.occurrence_id
                JOIN staff_notice_schedules s
                    ON o.schedule_id = s.schedule_id
                WHERE s.notice_id = ?
            ) AS delivery_count
    """, (notice_id, notice_id, notice_id)).fetchone()
    notice = preview["notice"]
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_published",
        summary=f"Staff Notice published: {notice['title']}",
        user_id=actor_user_id,
        client_id=notice["client_id"],
        shift_id=None,
        related_table="staff_notices",
        related_id=notice_id,
        details=(
            f"Priority: {notice['priority']}; "
            "Eligibility periods: "
            f"{publication_counts['eligibility_count']}; "
            f"Occurrences: {publication_counts['occurrence_count']}; "
            f"Deliveries: {publication_counts['delivery_count']}"
        ),
        success=1
    )

    return {
        "notice_id": notice_id,
        "published_by_user_id": actor_user_id,
        "published_at_utc": published_at_utc,
        "_publication_preview": preview
    }


def publish_staff_notice(
    notice_id,
    actor_user_id,
    expected_updated_at_utc=None
):
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
            get_application_now_utc(),
            expected_updated_at_utc
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
        ),
        "guided_schedule_paths": STAFF_NOTICE_GUIDED_SCHEDULE_LABELS
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
                sn.published_at_utc,
                c.client_name,
                creator.full_name AS created_by,
                publisher.full_name AS published_by
            FROM staff_notices sn
            LEFT JOIN clients c
                ON sn.client_id = c.client_id
            JOIN users creator
                ON sn.created_by_user_id = creator.user_id
            LEFT JOIN users publisher
                ON sn.published_by_user_id = publisher.user_id
            ORDER BY
                CASE sn.status
                    WHEN 'Draft' THEN 0
                    WHEN 'Published' THEN 1
                    WHEN 'Withdrawn' THEN 2
                    WHEN 'Replaced' THEN 3
                END,
                sn.draft_active DESC,
                COALESCE(
                    sn.published_at_utc,
                    sn.updated_at_utc,
                    sn.created_at_utc
                ) DESC,
                sn.notice_id DESC
        """).fetchall()
        notices = [dict(row) for row in notices]
        for notice in notices:
            notice["created_at_display"] = (
                format_staff_notice_friendly_local_datetime(
                    notice["created_at_utc"]
                )
            )
            notice["updated_at_display"] = (
                format_staff_notice_friendly_local_datetime(
                    notice["updated_at_utc"]
                ) if notice["updated_at_utc"] else None
            )
    finally:
        conn.close()

    return render_template(
        "staff_notice_admin_list.html",
        notices=notices,
        publication_result=request.args.get("publication_result")
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
            "weekdays": [],
            "guided_schedule_path": "none",
            "guided_calendar_date": "",
            "guided_shift_client_id": "",
            "guided_shift_date": "",
            "guided_shift_type": "",
            "guided_due_local": "",
            "guided_interval_days": "",
            "guided_anchor_date": "",
            "guided_shift_types": [],
            "guided_weekdays": []
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


@app.route("/staff-notices/<int:notice_id>/tracking")
def staff_notice_tracking(notice_id):
    access_response = _staff_notice_management_access_response()

    if access_response is not None:
        return access_response

    conn = get_db()
    now_utc = get_application_now_utc()
    try:
        conn.execute("BEGIN IMMEDIATE")
        notice = _load_staff_notice_publish_record(conn, notice_id)
        if notice is None or notice["status"] not in (
            "Published",
            "Withdrawn",
            "Replaced"
        ):
            conn.rollback()
            return "Staff Notice not found", 404

        if notice["status"] == "Published":
            reconcile_staff_notice_tracking_in_transaction(
                conn,
                notice,
                now_utc
            )
        tracking = _load_staff_notice_tracking(
            conn,
            notice_id,
            now_utc
        )
        if tracking is None:
            raise RuntimeError(
                "Published Staff Notice tracking became unavailable."
            )
        conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "Staff Notice recipient tracking could not be loaded. "
            "Please retry.",
            503
        )
    finally:
        conn.close()

    return render_template(
        "staff_notice_tracking.html",
        recipient_change_result=request.args.get(
            "recipient_change_result"
        ),
        withdrawal_result=request.args.get("withdrawal_result"),
        replacement_result=request.args.get("replacement_result"),
        acknowledgement_invalidation_result=request.args.get(
            "acknowledgement_invalidation_result"
        ),
        manual_requirement_result=request.args.get(
            "manual_requirement_result"
        ),
        no_shift_result=request.args.get("no_shift_result"),
        **tracking
    )


def _load_staff_notice_acknowledgement_invalidation_context(
    conn,
    acknowledgement_id
):
    acknowledgement_row = conn.execute("""
        SELECT *
        FROM acknowledgements
        WHERE acknowledgement_id = ?
    """, (acknowledgement_id,)).fetchone()
    if acknowledgement_row is None:
        raise LookupError("Staff Notice acknowledgement not found.")
    acknowledgement = dict(acknowledgement_row)
    if acknowledgement["source_table"] != "staff_notice_deliveries":
        raise LookupError("Staff Notice acknowledgement not found.")

    delivery_row = conn.execute("""
        SELECT
            d.*,
            o.shift_id,
            o.occurrence_status,
            sns.notice_id,
            sn.title,
            sn.client_id,
            sn.status AS notice_status
        FROM staff_notice_deliveries d
        JOIN staff_notice_occurrences o
            ON d.occurrence_id = o.occurrence_id
        JOIN staff_notice_schedules sns
            ON o.schedule_id = sns.schedule_id
        JOIN staff_notices sn
            ON sns.notice_id = sn.notice_id
        WHERE d.delivery_id = ?
    """, (acknowledgement["source_id"],)).fetchone()
    if delivery_row is None:
        raise StaffNoticeAcknowledgementInvalidationConflictError(
            "The acknowledgement has no valid Staff Notice delivery."
        )
    delivery = dict(delivery_row)
    if (
        acknowledgement["source_id"] != delivery["delivery_id"]
        or acknowledgement["user_id"] != delivery["user_id"]
        or acknowledgement["acknowledgement_type"] != "Acknowledgement"
    ):
        raise StaffNoticeAcknowledgementInvalidationConflictError(
            "The acknowledgement does not match its Staff Notice delivery."
        )
    return acknowledgement, delivery


def invalidate_staff_notice_acknowledgement(
    conn,
    acknowledgement_id,
    actor_user_id,
    invalidation_reason,
    invalidated_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice acknowledgement invalidation requires an "
            "active transaction."
        )
    if not _is_valid_staff_notice_identifier(acknowledgement_id):
        raise ValueError("A valid acknowledgement is required.")
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise PermissionError("Staff Notice management access denied.")
    if not isinstance(invalidation_reason, str) or not (
        invalidation_reason.strip()
    ):
        raise ValueError("An invalidation reason is required.")

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

    invalidation_reason = invalidation_reason.strip()
    invalidated_at_utc = format_staff_notice_utc_datetime(
        invalidated_at_utc
    )
    acknowledgement, delivery = (
        _load_staff_notice_acknowledgement_invalidation_context(
            conn,
            acknowledgement_id
        )
    )
    invalidation_values = (
        acknowledgement["invalidated_at_utc"],
        acknowledgement["invalidated_by_user_id"],
        acknowledgement["invalidation_reason"]
    )

    if acknowledgement["active"] == 0:
        valid_invalidation_history = (
            all(value is not None for value in invalidation_values)
            and _is_valid_staff_notice_identifier(
                acknowledgement["invalidated_by_user_id"]
            )
            and isinstance(acknowledgement["invalidation_reason"], str)
            and acknowledgement["invalidation_reason"].strip()
        )
        if valid_invalidation_history:
            try:
                valid_invalidation_history = (
                    format_staff_notice_utc_datetime(
                        acknowledgement["invalidated_at_utc"]
                    )
                    == acknowledgement["invalidated_at_utc"]
                )
            except ValueError:
                valid_invalidation_history = False
        if valid_invalidation_history:
            invalidator = conn.execute("""
                SELECT user_id
                FROM users
                WHERE user_id = ?
            """, (
                acknowledgement["invalidated_by_user_id"],
            )).fetchone()
            valid_invalidation_history = invalidator is not None
        if valid_invalidation_history:
            return {
                "invalidated": 0,
                "acknowledgement_id": acknowledgement_id,
                "delivery_id": delivery["delivery_id"],
                "notice_id": delivery["notice_id"]
            }
        raise StaffNoticeAcknowledgementInvalidationConflictError(
            "The inactive acknowledgement has incomplete invalidation "
            "history."
        )
    if acknowledgement["active"] != 1 or any(
        value is not None for value in invalidation_values
    ):
        raise StaffNoticeAcknowledgementInvalidationConflictError(
            "The acknowledgement has inconsistent invalidation history."
        )
    if (
        delivery["notice_status"] != "Published"
        or delivery["requirement_status"] != "Required"
        or delivery["recipient_access"] != 1
        or delivery["first_viewed_at_utc"] is None
        or delivery["viewed_by_user_id"] != delivery["user_id"]
        or delivery["occurrence_status"]
        in ("No Shift Occurred", "Cancelled")
    ):
        raise StaffNoticeAcknowledgementInvalidationConflictError(
            "This acknowledgement cannot be invalidated because its "
            "delivery is not available for re-acknowledgement."
        )

    active_count = conn.execute("""
        SELECT COUNT(*) AS count
        FROM acknowledgements
        WHERE source_table = 'staff_notice_deliveries'
          AND source_id = ?
          AND user_id = ?
          AND active = 1
    """, (
        delivery["delivery_id"],
        delivery["user_id"]
    )).fetchone()["count"]
    if active_count != 1:
        raise StaffNoticeAcknowledgementInvalidationConflictError(
            "The delivery has inconsistent active acknowledgement history."
        )

    cursor = conn.execute("""
        UPDATE acknowledgements
        SET active = 0,
            invalidated_at_utc = ?,
            invalidated_by_user_id = ?,
            invalidation_reason = ?
        WHERE acknowledgement_id = ?
          AND source_table = 'staff_notice_deliveries'
          AND source_id = ?
          AND user_id = ?
          AND active = 1
          AND invalidated_at_utc IS NULL
          AND invalidated_by_user_id IS NULL
          AND invalidation_reason IS NULL
    """, (
        invalidated_at_utc,
        actor_user_id,
        invalidation_reason,
        acknowledgement_id,
        delivery["delivery_id"],
        delivery["user_id"]
    ))
    if cursor.rowcount != 1:
        raise StaffNoticeAcknowledgementInvalidationConflictError(
            "The acknowledgement changed before it could be invalidated."
        )

    verified_acknowledgement, verified_delivery = (
        _load_staff_notice_acknowledgement_invalidation_context(
            conn,
            acknowledgement_id
        )
    )
    preserved_fields = (
        "acknowledgement_id",
        "source_table",
        "source_id",
        "user_id",
        "acknowledged_at",
        "comment",
        "acknowledgement_type"
    )
    if any(
        verified_acknowledgement[field] != acknowledgement[field]
        for field in preserved_fields
    ) or (
        verified_acknowledgement["active"] != 0
        or verified_acknowledgement["invalidated_at_utc"]
        != invalidated_at_utc
        or verified_acknowledgement["invalidated_by_user_id"]
        != actor_user_id
        or verified_acknowledgement["invalidation_reason"]
        != invalidation_reason
        or verified_delivery != delivery
    ):
        raise RuntimeError(
            "Staff Notice acknowledgement invalidation verification failed."
        )
    remaining_active = conn.execute("""
        SELECT COUNT(*) AS count
        FROM acknowledgements
        WHERE source_table = 'staff_notice_deliveries'
          AND source_id = ?
          AND user_id = ?
          AND active = 1
    """, (
        delivery["delivery_id"],
        delivery["user_id"]
    )).fetchone()["count"]
    if remaining_active != 0:
        raise RuntimeError(
            "Staff Notice acknowledgement remained active after "
            "invalidation."
        )

    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_acknowledgement_invalidated",
        summary=(
            "Staff Notice acknowledgement invalidated: "
            f"{delivery['title']}"
        ),
        user_id=actor_user_id,
        client_id=delivery["client_id"],
        shift_id=delivery["shift_id"],
        related_table="acknowledgements",
        related_id=acknowledgement_id,
        details=(
            f"Notice ID: {delivery['notice_id']}; Occurrence ID: "
            f"{delivery['occurrence_id']}; Delivery ID: "
            f"{delivery['delivery_id']}; Recipient User ID: "
            f"{delivery['user_id']}; Acknowledgement ID: "
            f"{acknowledgement_id}; Acknowledged at UTC: "
            f"{acknowledgement['acknowledged_at']}; Invalidated by User "
            f"ID: {actor_user_id}; Reason: {invalidation_reason}; "
            f"Invalidated at UTC: {invalidated_at_utc}"
        ),
        success=1
    )
    return {
        "invalidated": 1,
        "acknowledgement_id": acknowledgement_id,
        "delivery_id": delivery["delivery_id"],
        "notice_id": delivery["notice_id"]
    }


@app.route(
    "/staff-notices/acknowledgement/<int:acknowledgement_id>/invalidate",
    methods=["POST"]
)
def staff_notice_acknowledgement_invalidate(acknowledgement_id):
    access_response = _staff_notice_management_access_response()
    if access_response is not None:
        return access_response
    if (
        set(request.form.keys())
        != STAFF_NOTICE_ACKNOWLEDGEMENT_INVALIDATION_FORM_KEYS
    ):
        return "Invalid acknowledgement invalidation form.", 400
    try:
        invalidation_reason = _staff_notice_single_form_value(
            request.form,
            "invalidation_reason",
            required=True
        )
        confirmation = _staff_notice_single_form_value(
            request.form,
            "confirm_invalidation",
            required=True
        )
    except ValueError as error:
        return str(error), 400
    if confirmation != "yes":
        return "Confirm the acknowledgement invalidation.", 400

    conn = get_db()
    result = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        invalidated_at_utc = get_application_now_utc()
        result = invalidate_staff_notice_acknowledgement(
            conn,
            acknowledgement_id,
            session["user_id"],
            invalidation_reason,
            invalidated_at_utc
        )
        conn.commit()
    except LookupError:
        try:
            conn.rollback()
        except BaseException:
            pass
        return "Staff Notice acknowledgement not found.", 404
    except PermissionError:
        try:
            conn.rollback()
        except BaseException:
            pass
        return "Access denied", 403
    except StaffNoticeAcknowledgementInvalidationConflictError as error:
        try:
            conn.rollback()
        except BaseException:
            pass
        return str(error), 409
    except ValueError as error:
        try:
            conn.rollback()
        except BaseException:
            pass
        return str(error), 400
    except BaseException:
        try:
            conn.rollback()
        except BaseException:
            pass
        return (
            "The Staff Notice acknowledgement could not be invalidated. "
            "No changes were made. Please retry.",
            503
        )
    finally:
        conn.close()

    return redirect(url_for(
        "staff_notice_tracking",
        notice_id=result["notice_id"],
        acknowledgement_invalidation_result=(
            "invalidated" if result["invalidated"] else "unchanged"
        )
    ))


def _cancel_staff_notice_delivery(
    conn,
    delivery,
    actor_user_id,
    reason,
    effective_at_utc,
    *,
    reason_code
):
    cursor = conn.execute("""
        UPDATE staff_notice_deliveries
        SET requirement_status = 'Cancelled',
            status_changed_at_utc = ?,
            status_changed_by_user_id = ?,
            current_reason_code = ?,
            current_reason_text = ?
        WHERE delivery_id = ?
          AND requirement_status = 'Required'
          AND NOT EXISTS (
              SELECT 1
              FROM acknowledgements ack
              WHERE ack.source_table = 'staff_notice_deliveries'
                AND ack.source_id = staff_notice_deliveries.delivery_id
                AND ack.user_id = staff_notice_deliveries.user_id
                AND ack.active = 1
          )
    """, (
        effective_at_utc,
        actor_user_id,
        reason_code,
        reason,
        delivery["delivery_id"]
    ))
    if cursor.rowcount != 1:
        return 0

    conn.execute("""
        INSERT INTO staff_notice_delivery_history
        (
            delivery_id,
            event_type,
            previous_requirement_status,
            new_requirement_status,
            previous_recipient_access,
            new_recipient_access,
            reason_code,
            reason_text,
            changed_by_user_id,
            changed_at_utc
        )
        VALUES (?, 'Cancelled', 'Required', 'Cancelled',
                NULL, NULL, ?, ?, ?, ?)
    """, (
        delivery["delivery_id"],
        reason_code,
        reason,
        actor_user_id,
        effective_at_utc
    ))
    _log_staff_notice_delivery_transition(
        conn,
        activity_type="staff_notice_delivery_cancelled",
        summary="Staff Notice delivery cancelled",
        delivery=delivery,
        actor_user_id=actor_user_id,
        reason=reason,
        effective_at_utc=effective_at_utc
    )
    return 1


def _withdraw_staff_notice_in_transaction(
    conn,
    notice_id,
    actor_user_id,
    withdrawal_reason,
    withdrawn_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice withdrawal requires an active transaction."
        )
    if not isinstance(withdrawal_reason, str) or not (
        withdrawal_reason.strip()
    ):
        raise ValueError("A withdrawal reason is required.")
    if not _is_valid_staff_notice_identifier(notice_id):
        raise ValueError("A valid Staff Notice is required.")
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise ValueError("A valid withdrawing user is required.")

    withdrawal_reason = withdrawal_reason.strip()
    withdrawn_at_utc = format_staff_notice_utc_datetime(
        withdrawn_at_utc
    )
    notice_row = conn.execute("""
        SELECT notice_id, title, client_id, status
        FROM staff_notices
        WHERE notice_id = ?
    """, (notice_id,)).fetchone()
    if notice_row is None:
        raise LookupError("Staff Notice not found.")
    notice = dict(notice_row)
    if notice["status"] == "Withdrawn":
        return {
            "withdrawn": 0,
            "occurrences_cancelled": 0,
            "deliveries_cancelled": 0,
            "delivery_access_revoked": 0
        }
    if notice["status"] != "Published":
        raise ValueError(
            "Only a published Staff Notice can be withdrawn."
        )

    cursor = conn.execute("""
        UPDATE staff_notices
        SET status = 'Withdrawn',
            withdrawn_by_user_id = ?,
            withdrawn_at_utc = ?,
            withdrawal_reason = ?
        WHERE notice_id = ?
          AND status = 'Published'
    """, (
        actor_user_id,
        withdrawn_at_utc,
        withdrawal_reason,
        notice_id
    ))
    if cursor.rowcount != 1:
        raise RuntimeError(
            "The Staff Notice changed while it was being withdrawn."
        )

    occurrences = [
        dict(row)
        for row in conn.execute("""
            SELECT
                o.*,
                sn.notice_id,
                sn.title,
                sn.client_id
            FROM staff_notice_occurrences o
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            JOIN staff_notices sn
                ON sns.notice_id = sn.notice_id
            WHERE sns.notice_id = ?
            ORDER BY o.occurrence_id
        """, (notice_id,)).fetchall()
    ]
    occurrences_cancelled = 0
    for occurrence in occurrences:
        previous_status = occurrence["occurrence_status"]
        if previous_status not in ("Pending Shift", "Scheduled"):
            continue
        cursor = conn.execute("""
            UPDATE staff_notice_occurrences
            SET occurrence_status = 'Cancelled',
                status_reason = 'Notice Withdrawn',
                status_changed_at_utc = ?,
                status_changed_by_user_id = ?
            WHERE occurrence_id = ?
              AND occurrence_status = ?
        """, (
            withdrawn_at_utc,
            actor_user_id,
            occurrence["occurrence_id"],
            previous_status
        ))
        if cursor.rowcount != 1:
            continue
        occurrences_cancelled += 1
        log_activity(
            conn,
            activity_class="STAFF_NOTICE",
            activity_type="staff_notice_occurrence_status_changed",
            summary=(
                "Staff Notice occurrence cancelled: "
                f"{notice['title']}"
            ),
            user_id=actor_user_id,
            client_id=notice["client_id"],
            shift_id=occurrence["shift_id"],
            related_table="staff_notice_occurrences",
            related_id=occurrence["occurrence_id"],
            details=(
                f"Notice ID: {notice_id}; Occurrence ID: "
                f"{occurrence['occurrence_id']}; Previous status: "
                f"{previous_status}; New status: Cancelled; "
                "Reason code: Notice Withdrawn; "
                f"Reason: {withdrawal_reason}; Effective at UTC: "
                f"{withdrawn_at_utc}"
            ),
            success=1
        )

    deliveries = [
        dict(row)
        for row in conn.execute("""
            SELECT
                d.*,
                o.shift_id,
                sn.notice_id,
                sn.title,
                sn.client_id
            FROM staff_notice_deliveries d
            JOIN staff_notice_occurrences o
                ON d.occurrence_id = o.occurrence_id
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            JOIN staff_notices sn
                ON sns.notice_id = sn.notice_id
            WHERE sns.notice_id = ?
            ORDER BY d.delivery_id
        """, (notice_id,)).fetchall()
    ]
    deliveries_cancelled = 0
    delivery_access_revoked = 0
    for delivery in deliveries:
        deliveries_cancelled += (
            _cancel_staff_notice_delivery(
                conn,
                delivery,
                actor_user_id,
                withdrawal_reason,
                withdrawn_at_utc,
                reason_code="Notice Withdrawn"
            )
        )
        delivery_access_revoked += (
            _revoke_staff_notice_delivery_access(
                conn,
                delivery,
                actor_user_id,
                withdrawal_reason,
                withdrawn_at_utc,
                reason_code="Notice Withdrawn"
            )
        )

    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_withdrawn",
        summary=f"Staff Notice withdrawn: {notice['title']}",
        user_id=actor_user_id,
        client_id=notice["client_id"],
        shift_id=None,
        related_table="staff_notices",
        related_id=notice_id,
        details=(
            f"Notice ID: {notice_id}; Reason: {withdrawal_reason}; "
            f"Withdrawn at UTC: {withdrawn_at_utc}; "
            f"Occurrences cancelled: {occurrences_cancelled}; "
            f"Deliveries cancelled: {deliveries_cancelled}; "
            f"Delivery access revoked: {delivery_access_revoked}"
        ),
        success=1
    )
    return {
        "withdrawn": 1,
        "occurrences_cancelled": occurrences_cancelled,
        "deliveries_cancelled": deliveries_cancelled,
        "delivery_access_revoked": delivery_access_revoked
    }


@app.route("/staff-notices/<int:notice_id>/withdraw", methods=["POST"])
def staff_notice_withdraw(notice_id):
    access_response = _staff_notice_management_access_response()
    if access_response is not None:
        return access_response

    if request.form.get("confirm_withdrawal") != "yes":
        return "Confirm the Staff Notice withdrawal.", 400
    withdrawal_reason = request.form.get("withdrawal_reason", "")
    if not withdrawal_reason.strip():
        return "A withdrawal reason is required.", 400

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = _withdraw_staff_notice_in_transaction(
            conn,
            notice_id,
            session["user_id"],
            withdrawal_reason,
            get_application_now_utc()
        )
        conn.commit()
    except LookupError:
        if conn.in_transaction:
            conn.rollback()
        return "Staff Notice not found.", 404
    except ValueError as error:
        if conn.in_transaction:
            conn.rollback()
        return str(error), 409
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "The Staff Notice could not be withdrawn. No changes were "
            "made. Please retry.",
            503
        )
    finally:
        conn.close()

    return redirect(url_for(
        "staff_notice_tracking",
        notice_id=notice_id,
        withdrawal_result=(
            "withdrawn" if result["withdrawn"] else "unchanged"
        )
    ))


def _copy_staff_notice_replacement_configuration(
    conn,
    original_notice,
    actor_user_id,
    replacement_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice replacement copying requires an active transaction."
        )

    cursor = conn.execute("""
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
            replaces_notice_id,
            created_by_user_id,
            created_at_utc
        )
        VALUES (?, ?, ?, ?, 'Draft', 1, ?, ?, ?, ?, ?, ?, ?)
    """, (
        original_notice["title"],
        original_notice["notice_text"],
        original_notice["priority"],
        original_notice["client_id"],
        original_notice["effective_start_at_utc"],
        original_notice["expires_at_utc"],
        original_notice["until_withdrawn"],
        original_notice["version_number"] + 1,
        original_notice["notice_id"],
        actor_user_id,
        replacement_at_utc
    ))
    replacement_notice_id = cursor.lastrowid

    original_audiences = conn.execute("""
        SELECT audience_id
        FROM staff_notice_audiences
        WHERE notice_id = ?
        ORDER BY audience_id
    """, (original_notice["notice_id"],)).fetchall()
    for original_audience in original_audiences:
        cursor = conn.execute("""
            INSERT INTO staff_notice_audiences
            (notice_id, created_at_utc)
            VALUES (?, ?)
        """, (replacement_notice_id, replacement_at_utc))
        replacement_audience_id = cursor.lastrowid
        rules = conn.execute("""
            SELECT rule_type, role_name, user_id
            FROM staff_notice_audience_rules
            WHERE audience_id = ?
            ORDER BY audience_rule_id
        """, (original_audience["audience_id"],)).fetchall()
        for rule in rules:
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
                replacement_audience_id,
                rule["rule_type"],
                rule["role_name"],
                rule["user_id"],
                replacement_at_utc
            ))

    original_schedules = conn.execute("""
        SELECT *
        FROM staff_notice_schedules
        WHERE notice_id = ?
        ORDER BY schedule_id
    """, (original_notice["notice_id"],)).fetchall()
    for schedule in original_schedules:
        cursor = conn.execute("""
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
            replacement_notice_id,
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
            replacement_at_utc
        ))
        replacement_schedule_id = cursor.lastrowid
        shift_types = conn.execute("""
            SELECT shift_type
            FROM staff_notice_schedule_shift_types
            WHERE schedule_id = ?
            ORDER BY schedule_shift_type_id
        """, (schedule["schedule_id"],)).fetchall()
        for shift_type in shift_types:
            conn.execute("""
                INSERT INTO staff_notice_schedule_shift_types
                (schedule_id, shift_type)
                VALUES (?, ?)
            """, (
                replacement_schedule_id,
                shift_type["shift_type"]
            ))
        weekdays = conn.execute("""
            SELECT weekday_number
            FROM staff_notice_schedule_weekdays
            WHERE schedule_id = ?
            ORDER BY schedule_weekday_id
        """, (schedule["schedule_id"],)).fetchall()
        for weekday in weekdays:
            conn.execute("""
                INSERT INTO staff_notice_schedule_weekdays
                (schedule_id, weekday_number)
                VALUES (?, ?)
            """, (
                replacement_schedule_id,
                weekday["weekday_number"]
            ))

    return replacement_notice_id


def _replace_staff_notice_in_transaction(
    conn,
    notice_id,
    actor_user_id,
    replacement_reason,
    replacement_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Staff Notice replacement requires an active transaction."
        )
    if not _is_valid_staff_notice_identifier(notice_id):
        raise ValueError("A valid Staff Notice is required.")
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise PermissionError("Staff Notice management access denied.")
    if not isinstance(replacement_reason, str) or not (
        replacement_reason.strip()
    ):
        raise ValueError("A replacement reason is required.")

    replacement_reason = replacement_reason.strip()
    replacement_at_utc = format_staff_notice_utc_datetime(
        replacement_at_utc
    )
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

    notice_row = conn.execute("""
        SELECT *
        FROM staff_notices
        WHERE notice_id = ?
    """, (notice_id,)).fetchone()
    if notice_row is None:
        raise LookupError("Staff Notice not found.")
    notice = dict(notice_row)
    successors = conn.execute("""
        SELECT notice_id
        FROM staff_notices
        WHERE replaces_notice_id = ?
        ORDER BY notice_id
    """, (notice_id,)).fetchall()

    if notice["status"] == "Replaced":
        if len(successors) == 1:
            return {
                "replaced": 0,
                "replacement_notice_id": successors[0]["notice_id"],
                "occurrences_cancelled": 0,
                "deliveries_cancelled": 0,
                "delivery_access_revoked": 0
            }
        raise StaffNoticeReplacementConflictError(
            "The replaced Staff Notice has an inconsistent successor."
        )
    if notice["status"] != "Published":
        raise StaffNoticeReplacementConflictError(
            "Only a published Staff Notice can be replaced."
        )
    if successors:
        raise StaffNoticeReplacementConflictError(
            "The Staff Notice already has a replacement successor."
        )

    cursor = conn.execute("""
        UPDATE staff_notices
        SET status = 'Replaced',
            replaced_by_user_id = ?,
            replaced_at_utc = ?,
            replacement_reason = ?
        WHERE notice_id = ?
          AND status = 'Published'
          AND NOT EXISTS (
              SELECT 1
              FROM staff_notices successor
              WHERE successor.replaces_notice_id = staff_notices.notice_id
          )
    """, (
        actor_user_id,
        replacement_at_utc,
        replacement_reason,
        notice_id
    ))
    if cursor.rowcount != 1:
        raise StaffNoticeReplacementConflictError(
            "The Staff Notice changed while it was being replaced."
        )

    replacement_notice_id = (
        _copy_staff_notice_replacement_configuration(
            conn,
            notice,
            actor_user_id,
            replacement_at_utc
        )
    )

    occurrences = [
        dict(row)
        for row in conn.execute("""
            SELECT o.*
            FROM staff_notice_occurrences o
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            WHERE sns.notice_id = ?
            ORDER BY o.occurrence_id
        """, (notice_id,)).fetchall()
    ]
    occurrences_cancelled = 0
    for occurrence in occurrences:
        previous_status = occurrence["occurrence_status"]
        if previous_status not in ("Pending Shift", "Scheduled"):
            continue
        cursor = conn.execute("""
            UPDATE staff_notice_occurrences
            SET occurrence_status = 'Cancelled',
                status_reason = 'Notice Replaced',
                status_changed_at_utc = ?,
                status_changed_by_user_id = ?
            WHERE occurrence_id = ?
              AND occurrence_status = ?
        """, (
            replacement_at_utc,
            actor_user_id,
            occurrence["occurrence_id"],
            previous_status
        ))
        if cursor.rowcount != 1:
            continue
        occurrences_cancelled += 1
        log_activity(
            conn,
            activity_class="STAFF_NOTICE",
            activity_type="staff_notice_occurrence_status_changed",
            summary=(
                "Staff Notice occurrence cancelled: "
                f"{notice['title']}"
            ),
            user_id=actor_user_id,
            client_id=notice["client_id"],
            shift_id=occurrence["shift_id"],
            related_table="staff_notice_occurrences",
            related_id=occurrence["occurrence_id"],
            details=(
                f"Notice ID: {notice_id}; Occurrence ID: "
                f"{occurrence['occurrence_id']}; Previous status: "
                f"{previous_status}; New status: Cancelled; "
                "Reason code: Notice Replaced; "
                f"Reason: {replacement_reason}; Effective at UTC: "
                f"{replacement_at_utc}"
            ),
            success=1
        )

    deliveries = [
        dict(row)
        for row in conn.execute("""
            SELECT
                d.*,
                o.shift_id,
                sn.notice_id,
                sn.title,
                sn.client_id
            FROM staff_notice_deliveries d
            JOIN staff_notice_occurrences o
                ON d.occurrence_id = o.occurrence_id
            JOIN staff_notice_schedules sns
                ON o.schedule_id = sns.schedule_id
            JOIN staff_notices sn
                ON sns.notice_id = sn.notice_id
            WHERE sns.notice_id = ?
            ORDER BY d.delivery_id
        """, (notice_id,)).fetchall()
    ]
    deliveries_cancelled = 0
    delivery_access_revoked = 0
    for delivery in deliveries:
        deliveries_cancelled += _cancel_staff_notice_delivery(
            conn,
            delivery,
            actor_user_id,
            replacement_reason,
            replacement_at_utc,
            reason_code="Notice Replaced"
        )
        delivery_access_revoked += _revoke_staff_notice_delivery_access(
            conn,
            delivery,
            actor_user_id,
            replacement_reason,
            replacement_at_utc,
            reason_code="Notice Replaced"
        )

    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_replacement_created",
        summary=(
            "Staff Notice replacement draft created: "
            f"{notice['title']}"
        ),
        user_id=actor_user_id,
        client_id=notice["client_id"],
        shift_id=None,
        related_table="staff_notices",
        related_id=replacement_notice_id,
        details=(
            f"Original notice ID: {notice_id}; Replacement notice ID: "
            f"{replacement_notice_id}; Version number: "
            f"{notice['version_number'] + 1}; Reason: "
            f"{replacement_reason}; Created at UTC: {replacement_at_utc}"
        ),
        success=1
    )
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_replaced",
        summary=f"Staff Notice replaced: {notice['title']}",
        user_id=actor_user_id,
        client_id=notice["client_id"],
        shift_id=None,
        related_table="staff_notices",
        related_id=notice_id,
        details=(
            f"Notice ID: {notice_id}; Replacement notice ID: "
            f"{replacement_notice_id}; Reason: {replacement_reason}; "
            f"Replaced at UTC: {replacement_at_utc}; "
            f"Occurrences cancelled: {occurrences_cancelled}; "
            f"Deliveries cancelled: {deliveries_cancelled}; "
            f"Delivery access revoked: {delivery_access_revoked}"
        ),
        success=1
    )
    return {
        "replaced": 1,
        "replacement_notice_id": replacement_notice_id,
        "occurrences_cancelled": occurrences_cancelled,
        "deliveries_cancelled": deliveries_cancelled,
        "delivery_access_revoked": delivery_access_revoked
    }


@app.route(
    "/staff-notices/<int:notice_id>/replace",
    methods=["GET", "POST"]
)
def staff_notice_replace(notice_id):
    access_response = _staff_notice_management_access_response()
    if access_response is not None:
        return access_response

    if request.method == "GET":
        conn = get_db()
        try:
            notice = _load_staff_notice_admin_record(conn, notice_id)
            successors = conn.execute("""
                SELECT notice_id
                FROM staff_notices
                WHERE replaces_notice_id = ?
                ORDER BY notice_id
            """, (notice_id,)).fetchall()
        finally:
            conn.close()
        if notice is None:
            return "Staff Notice not found.", 404
        if notice["status"] == "Replaced":
            if len(successors) == 1:
                return redirect(url_for(
                    "staff_notice_admin_detail",
                    notice_id=successors[0]["notice_id"]
                ))
            return (
                "The replaced Staff Notice has an inconsistent successor.",
                409
            )
        if notice["status"] != "Published":
            return "Only a published Staff Notice can be replaced.", 409
        return render_template(
            "staff_notice_replace.html",
            notice=notice,
            summary=build_staff_notice_plain_language_summary(notice),
            error=None
        )

    if set(request.form.keys()) != STAFF_NOTICE_REPLACEMENT_FORM_KEYS:
        return "Invalid Staff Notice replacement form.", 400
    if request.form.get("confirm_replacement") != "yes":
        return "Confirm the Staff Notice replacement.", 400
    replacement_reason = request.form.get("replacement_reason", "")
    if not replacement_reason.strip():
        return "A replacement reason is required.", 400

    conn = get_db()
    result = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        replacement_at_utc = get_application_now_utc()
        result = _replace_staff_notice_in_transaction(
            conn,
            notice_id,
            session["user_id"],
            replacement_reason,
            replacement_at_utc
        )
        conn.commit()
    except LookupError:
        try:
            conn.rollback()
        except BaseException:
            pass
        return "Staff Notice not found.", 404
    except StaffNoticeReplacementConflictError as error:
        try:
            conn.rollback()
        except BaseException:
            pass
        return str(error), 409
    except PermissionError:
        try:
            conn.rollback()
        except BaseException:
            pass
        return "Access denied", 403
    except ValueError as error:
        try:
            conn.rollback()
        except BaseException:
            pass
        return str(error), 400
    except BaseException:
        try:
            conn.rollback()
        except BaseException:
            pass
        return (
            "The Staff Notice could not be replaced. No changes were "
            "made. Please retry.",
            503
        )
    finally:
        conn.close()

    return redirect(url_for(
        "staff_notice_admin_detail",
        notice_id=result["replacement_notice_id"],
        replacement_result=(
            "created" if result["replaced"] else "unchanged"
        )
    ))


def _log_staff_notice_no_shift_occurred(
    conn,
    occurrence,
    actor_user_id,
    reason,
    changed_at_utc
):
    log_activity(
        conn,
        activity_class="STAFF_NOTICE",
        activity_type="staff_notice_no_shift_occurred",
        summary=(
            "Staff Notice confirmed with no shift occurrence: "
            f"{occurrence['title']}"
        ),
        user_id=actor_user_id,
        client_id=occurrence["planned_client_id"],
        shift_id=None,
        related_table="staff_notice_occurrences",
        related_id=occurrence["occurrence_id"],
        details=(
            f"Notice ID: {occurrence['notice_id']}; "
            f"Occurrence ID: {occurrence['occurrence_id']}; "
            f"Planned client ID: {occurrence['planned_client_id']}; "
            f"Occurrence date: {occurrence['occurrence_date']}; "
            f"Planned shift type: {occurrence['planned_shift_type']}; "
            "Previous status: Pending Shift; "
            "New status: No Shift Occurred; "
            f"Reason: {reason}; Effective at UTC: {changed_at_utc}"
        ),
        success=1
    )


def confirm_staff_notice_no_shift_occurred(
    conn,
    occurrence_id,
    actor_user_id,
    reason,
    changed_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "No Shift Occurred confirmation requires an active transaction."
        )
    if not _is_valid_staff_notice_identifier(occurrence_id):
        raise ValueError("A valid Staff Notice occurrence is required.")
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise ValueError("A valid management actor is required.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A No Shift Occurred reason is required.")

    reason = reason.strip()
    changed_at_utc = format_staff_notice_utc_datetime(changed_at_utc)
    now_utc = parse_staff_notice_utc_datetime(changed_at_utc)
    occurrence_row = conn.execute("""
        SELECT
            o.*,
            sns.notice_id,
            sn.title,
            sn.status AS notice_status
        FROM staff_notice_occurrences o
        JOIN staff_notice_schedules sns
            ON o.schedule_id = sns.schedule_id
        JOIN staff_notices sn
            ON sns.notice_id = sn.notice_id
        WHERE o.occurrence_id = ?
    """, (occurrence_id,)).fetchone()
    if occurrence_row is None:
        raise LookupError("Staff Notice occurrence not found.")
    occurrence = dict(occurrence_row)
    if occurrence["occurrence_status"] == "No Shift Occurred":
        return {
            "occurrence_changed": 0,
            "notice_id": occurrence["notice_id"]
        }
    if (
        occurrence["notice_status"] != "Published"
        or occurrence["occurrence_kind"] != "Shift"
        or occurrence["is_specific_shift_occurrence"] != 1
        or occurrence["shift_id"] is not None
        or occurrence["occurrence_status"] != "Pending Shift"
    ):
        raise StaffNoticeManagementLifecycleConflictError(
            "This Staff Notice occurrence cannot be marked No Shift "
            "Occurred."
        )

    expected_end_at_utc = (
        _staff_notice_pending_shift_expected_end_at_utc(occurrence)
    )
    if now_utc <= expected_end_at_utc:
        raise StaffNoticeManagementLifecycleConflictError(
            "The expected shift end has not passed."
        )
    exact_matching_shift_count = conn.execute("""
        SELECT COUNT(*) AS matching_shift_count
        FROM shifts
        WHERE client_id = ?
          AND shift_date = ?
          AND shift_type = ?
    """, (
        occurrence["planned_client_id"],
        occurrence["occurrence_date"],
        occurrence["planned_shift_type"]
    )).fetchone()["matching_shift_count"]
    if exact_matching_shift_count != 0:
        raise StaffNoticeManagementLifecycleConflictError(
            "A matching shift exists. Reload and reconcile the occurrence."
        )

    cursor = conn.execute("""
        UPDATE staff_notice_occurrences
        SET occurrence_status = 'No Shift Occurred',
            status_reason = ?,
            status_changed_at_utc = ?,
            status_changed_by_user_id = ?
        WHERE occurrence_id = ?
          AND shift_id IS NULL
          AND occurrence_status = 'Pending Shift'
    """, (
        reason,
        changed_at_utc,
        actor_user_id,
        occurrence_id
    ))
    if cursor.rowcount != 1:
        current = conn.execute("""
            SELECT occurrence_status
            FROM staff_notice_occurrences
            WHERE occurrence_id = ?
        """, (occurrence_id,)).fetchone()
        if (
            current is not None
            and current["occurrence_status"] == "No Shift Occurred"
        ):
            return {
                "occurrence_changed": 0,
                "notice_id": occurrence["notice_id"]
            }
        raise StaffNoticeManagementLifecycleConflictError(
            "The Staff Notice occurrence changed. Reload and retry."
        )

    _log_staff_notice_no_shift_occurred(
        conn,
        occurrence,
        actor_user_id,
        reason,
        changed_at_utc
    )
    return {
        "occurrence_changed": 1,
        "notice_id": occurrence["notice_id"]
    }


@app.route(
    "/staff-notices/occurrence/<int:occurrence_id>/no-shift-occurred",
    methods=["POST"]
)
def staff_notice_no_shift_occurred(occurrence_id):
    access_response = _staff_notice_management_access_response()
    if access_response is not None:
        return access_response

    if request.form.get("confirm_no_shift_occurred") != "yes":
        return "Confirm that no shift occurred before continuing.", 400
    reason = request.form.get("reason", "")
    if not reason.strip():
        return "A No Shift Occurred reason is required.", 400

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed_at_utc = get_application_now_utc()
        result = confirm_staff_notice_no_shift_occurred(
            conn,
            occurrence_id,
            session["user_id"],
            reason,
            changed_at_utc
        )
        conn.commit()
    except LookupError:
        try:
            conn.rollback()
        except BaseException:
            pass
        return "Staff Notice occurrence not found", 404
    except StaffNoticeManagementLifecycleConflictError as error:
        try:
            conn.rollback()
        except BaseException:
            pass
        return str(error), 409
    except ValueError as error:
        try:
            conn.rollback()
        except BaseException:
            pass
        return str(error), 400
    except BaseException:
        try:
            conn.rollback()
        except BaseException:
            pass
        return (
            "No Shift Occurred could not be confirmed. No changes were "
            "made. Please retry.",
            503
        )
    finally:
        conn.close()

    return redirect(url_for(
        "staff_notice_tracking",
        notice_id=result["notice_id"],
        no_shift_result=(
            "confirmed" if result["occurrence_changed"] else "unchanged"
        )
    ))


def _load_staff_notice_shift_delivery_management_context(
    conn,
    delivery_id
):
    row = conn.execute("""
        SELECT
            d.*,
            o.shift_id,
            o.occurrence_status,
            sns.notice_id,
            sn.title,
            sn.status AS notice_status,
            sn.client_id,
            (
                SELECT MIN(ack.acknowledgement_id)
                FROM acknowledgements ack
                WHERE ack.source_table = 'staff_notice_deliveries'
                  AND ack.source_id = d.delivery_id
                  AND ack.user_id = d.user_id
                  AND ack.active = 1
            ) AS active_acknowledgement_id,
            (
                SELECT MIN(ss.shift_staff_id)
                FROM shift_staff ss
                WHERE ss.shift_id = o.shift_id
                  AND ss.user_id = d.user_id
                  AND ss.active = 1
            ) AS active_shift_staff_id,
            (
                SELECT COUNT(*)
                FROM shift_staff ss
                WHERE ss.shift_id = o.shift_id
                  AND ss.user_id = d.user_id
                  AND ss.active = 1
            ) AS active_shift_staff_count
        FROM staff_notice_deliveries d
        JOIN staff_notice_occurrences o
            ON d.occurrence_id = o.occurrence_id
        JOIN staff_notice_schedules sns
            ON o.schedule_id = sns.schedule_id
        JOIN staff_notices sn
            ON sns.notice_id = sn.notice_id
        WHERE d.delivery_id = ?
    """, (delivery_id,)).fetchone()
    return dict(row) if row is not None else None


def mark_staff_notice_delivery_no_longer_required(
    conn,
    delivery_id,
    actor_user_id,
    reason,
    effective_at_utc
):
    if not conn.in_transaction:
        raise RuntimeError(
            "Manual Staff Notice requirement changes require an active "
            "transaction."
        )
    if not _is_valid_staff_notice_identifier(delivery_id):
        raise ValueError("A valid Staff Notice delivery is required.")
    if not _is_valid_staff_notice_identifier(actor_user_id):
        raise ValueError("A valid management actor is required.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A No Longer Required reason is required.")

    reason = reason.strip()
    effective_at_utc = format_staff_notice_utc_datetime(effective_at_utc)
    delivery = _load_staff_notice_shift_delivery_management_context(
        conn,
        delivery_id
    )
    if delivery is None:
        raise LookupError("Staff Notice delivery not found.")
    if delivery["requirement_status"] == "No Longer Required":
        return {
            "delivery_changed": 0,
            "notice_id": delivery["notice_id"]
        }
    if (
        delivery["notice_status"] != "Published"
        or delivery["occurrence_status"] in (
            "No Shift Occurred",
            "Cancelled"
        )
        or delivery["requirement_status"] != "Required"
        or delivery["active_acknowledgement_id"] is not None
    ):
        raise StaffNoticeManagementLifecycleConflictError(
            "This Staff Notice delivery is not an outstanding requirement."
        )

    changed = _mark_staff_notice_delivery_no_longer_required(
        conn,
        delivery,
        actor_user_id,
        reason,
        effective_at_utc,
        reason_code="Manual No Longer Required"
    )
    if changed != 1:
        current = _load_staff_notice_shift_delivery_management_context(
            conn,
            delivery_id
        )
        if (
            current is not None
            and current["requirement_status"] == "No Longer Required"
        ):
            return {
                "delivery_changed": 0,
                "notice_id": current["notice_id"]
            }
        raise StaffNoticeManagementLifecycleConflictError(
            "The Staff Notice delivery changed. Reload and retry."
        )
    return {
        "delivery_changed": 1,
        "notice_id": delivery["notice_id"]
    }


@app.route(
    "/staff-notices/delivery/<int:delivery_id>/manual-no-longer-required",
    methods=["POST"]
)
def staff_notice_delivery_manual_no_longer_required(delivery_id):
    access_response = _staff_notice_management_access_response()
    if access_response is not None:
        return access_response

    if request.form.get("confirm_no_longer_required") != "yes":
        return "Confirm the requirement change before continuing.", 400
    reason = request.form.get("reason", "")
    if not reason.strip():
        return "A No Longer Required reason is required.", 400

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed_at_utc = get_application_now_utc()
        result = mark_staff_notice_delivery_no_longer_required(
            conn,
            delivery_id,
            session["user_id"],
            reason,
            changed_at_utc
        )
        conn.commit()
    except LookupError:
        try:
            conn.rollback()
        except BaseException:
            pass
        return "Staff Notice delivery not found", 404
    except StaffNoticeManagementLifecycleConflictError as error:
        try:
            conn.rollback()
        except BaseException:
            pass
        return str(error), 409
    except ValueError as error:
        try:
            conn.rollback()
        except BaseException:
            pass
        return str(error), 400
    except BaseException:
        try:
            conn.rollback()
        except BaseException:
            pass
        return (
            "The Staff Notice requirement could not be changed. No changes "
            "were made. Please retry.",
            503
        )
    finally:
        conn.close()

    return redirect(url_for(
        "staff_notice_tracking",
        notice_id=result["notice_id"],
        manual_requirement_result=(
            "changed" if result["delivery_changed"] else "unchanged"
        )
    ))


@app.route(
    "/staff-notices/delivery/<int:delivery_id>/no-longer-required",
    methods=["POST"]
)
def staff_notice_delivery_no_longer_required(delivery_id):
    access_response = _staff_notice_management_access_response()
    if access_response is not None:
        return access_response

    if request.form.get("confirm_removal") != "yes":
        return "Confirm the worker removal before continuing.", 400
    reason = request.form.get("reason", "")
    if not reason.strip():
        return "A worker-removal reason is required.", 400

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        delivery = _load_staff_notice_shift_delivery_management_context(
            conn,
            delivery_id
        )
        if delivery is None:
            conn.rollback()
            return "Staff Notice delivery not found", 404
        if (
            delivery["notice_status"] != "Published"
            or delivery["shift_id"] is None
            or delivery["occurrence_status"]
            in ("No Shift Occurred", "Cancelled")
        ):
            conn.rollback()
            return "This delivery cannot be removed from a shift.", 409
        if delivery["active_shift_staff_count"] != 1:
            conn.rollback()
            return (
                "The active shift assignment changed. Reload and retry.",
                409
            )

        result = remove_shift_staff_assignment(
            conn,
            delivery["active_shift_staff_id"],
            session["user_id"],
            reason,
            get_application_now_utc()
        )
        if result["assignments_removed"] != 1:
            conn.rollback()
            return (
                "The active shift assignment changed. Reload and retry.",
                409
            )
        conn.commit()
        return redirect(url_for(
            "staff_notice_tracking",
            notice_id=delivery["notice_id"],
            recipient_change_result="removed"
        ))
    except (ValueError, LookupError):
        if conn.in_transaction:
            conn.rollback()
        return "The worker removal request is invalid.", 400
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "The worker could not be removed. No changes were made. "
            "Please retry.",
            503
        )
    finally:
        conn.close()


@app.route(
    "/staff-notices/delivery/<int:delivery_id>/reinstate",
    methods=["POST"]
)
def staff_notice_delivery_reinstate(delivery_id):
    access_response = _staff_notice_management_access_response()
    if access_response is not None:
        return access_response

    if request.form.get("confirm_reinstatement") != "yes":
        return "Confirm the reinstatement before continuing.", 400
    reason = request.form.get("reason", "")
    if not reason.strip():
        return "A reinstatement reason is required.", 400

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        delivery = _load_staff_notice_shift_delivery_management_context(
            conn,
            delivery_id
        )
        if delivery is None:
            conn.rollback()
            return "Staff Notice delivery not found", 404
        if (
            delivery["notice_status"] != "Published"
            or delivery["shift_id"] is None
            or delivery["occurrence_status"]
            in ("No Shift Occurred", "Cancelled")
            or delivery["requirement_status"] == "Cancelled"
        ):
            conn.rollback()
            return "This delivery cannot be reinstated.", 409
        if delivery["active_shift_staff_count"] != 1:
            conn.rollback()
            return (
                "An active eligible shift assignment is required.",
                409
            )
        notice = _load_staff_notice_publish_record(
            conn,
            delivery["notice_id"]
        )
        eligible_user_ids = _load_initial_staff_notice_delivery_user_ids(
            conn,
            delivery["notice_id"],
            {
                "occurrence_kind": "Shift",
                "shift_id": delivery["shift_id"]
            },
            delivery["eligibility_cutoff_at_utc"]
        )
        if (
            notice is None
            or delivery["user_id"] not in eligible_user_ids
        ):
            conn.rollback()
            return (
                "The worker is not eligible for this Staff Notice "
                "occurrence.",
                409
            )

        result = _restore_staff_notice_delivery_for_shift_assignment(
            conn,
            delivery,
            session["user_id"],
            reason,
            get_application_now_utc()
        )
        if not any(result.values()):
            conn.rollback()
            return redirect(url_for(
                "staff_notice_tracking",
                notice_id=delivery["notice_id"],
                recipient_change_result="unchanged"
            ))
        conn.commit()
        return redirect(url_for(
            "staff_notice_tracking",
            notice_id=delivery["notice_id"],
            recipient_change_result="reinstated"
        ))
    except ValueError:
        if conn.in_transaction:
            conn.rollback()
        return "The reinstatement request is invalid.", 400
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "The delivery could not be reinstated. No changes were made. "
            "Please retry.",
            503
        )
    finally:
        conn.close()


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
        expected_updated_at_utc=_staff_notice_draft_token(
            preview["notice"]
        ),
        publication_result=request.args.get("publication_result"),
        **preview
    )


@app.route(
    "/staff-notices/<int:notice_id>/publish",
    methods=["POST"]
)
def staff_notice_publish(notice_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    try:
        if set(request.form.keys()) != STAFF_NOTICE_PUBLICATION_FORM_KEYS:
            raise ValueError("Invalid Staff Notice publication form.")

        expected_updated_at_utc = _staff_notice_single_form_value(
            request.form,
            "expected_updated_at_utc",
            required=True
        )
        result = publish_staff_notice(
            notice_id,
            session["user_id"],
            expected_updated_at_utc
        )
    except StaffNoticePublicationCommittedCloseError as error:
        result = {"notice_id": error.notice_id}
    except PermissionError:
        return "Access denied", 403
    except StaffNoticeNotFoundError:
        return redirect(url_for(
            "staff_notice_admin_list",
            publication_result="not_found"
        ))
    except (StaffNoticeNotEditableError, StaffNoticeStalePublicationError):
        return redirect(url_for(
            "staff_notice_admin_list",
            publication_result="conflict"
        ))
    except StaffNoticePublicationNotReadyError:
        return redirect(url_for(
            "staff_notice_publish_review",
            notice_id=notice_id,
            publication_result="blocked"
        ))
    except ValueError:
        return redirect(url_for(
            "staff_notice_publish_review",
            notice_id=notice_id,
            publication_result="invalid_form"
        ))
    except Exception:
        return redirect(url_for(
            "staff_notice_publish_review",
            notice_id=notice_id,
            publication_result="failed"
        ))

    return redirect(url_for(
        "staff_notice_admin_list",
        publication_result="published",
        notice_id=result["notice_id"]
    ))


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


@app.route("/staff-notices")
def staff_notice_history():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    now_utc = get_application_now_utc()
    try:
        conn.execute("BEGIN IMMEDIATE")
        recipient = _get_authenticated_staff_notice_recipient(conn)
        reconcile_staff_notice_non_shift_requirements_in_transaction(
            conn,
            now_utc
        )
        notices = _get_staff_notice_recipient_collections(
            conn,
            recipient["user_id"],
            now_utc
        )
        conn.commit()
    except PermissionError:
        if conn.in_transaction:
            conn.rollback()
        return "Access denied", 403
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "Staff Notices could not be loaded. Please retry.",
            503
        )
    finally:
        conn.close()

    return render_template(
        "staff_notice_history.html",
        current_notices=notices["current"],
        historical_notices=notices["history"],
        outstanding_count=notices["outstanding_count"]
    )


@app.route("/staff-notices/delivery/<int:delivery_id>")
def staff_notice_detail(delivery_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    now_utc = get_application_now_utc()
    try:
        conn.execute("BEGIN IMMEDIATE")
        recipient = _get_authenticated_staff_notice_recipient(conn)
        reconcile_staff_notice_non_shift_requirements_in_transaction(
            conn,
            now_utc
        )
        delivery = _load_staff_notice_recipient_delivery(
            conn,
            delivery_id,
            recipient["user_id"],
            now_utc
        )
        if delivery is None:
            conn.rollback()
            return "Staff Notice delivery not found", 404
        if not delivery["readable"]:
            conn.rollback()
            return "Staff Notice delivery is not accessible", 403

        _record_staff_notice_first_view(
            conn,
            delivery,
            recipient["user_id"],
            now_utc
        )
        delivery = _load_staff_notice_recipient_delivery(
            conn,
            delivery_id,
            recipient["user_id"],
            now_utc
        )
        conn.commit()
    except PermissionError:
        if conn.in_transaction:
            conn.rollback()
        return "Access denied", 403
    except StaffNoticeRecipientError as error:
        if conn.in_transaction:
            conn.rollback()
        return str(error), 409
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "The Staff Notice could not be opened. Please retry.",
            503
        )
    finally:
        conn.close()

    return render_template(
        "staff_notice_detail.html",
        delivery=delivery,
        acknowledged=request.args.get("acknowledged") == "1"
    )


@app.route(
    "/staff-notices/delivery/<int:delivery_id>/acknowledge",
    methods=["POST"]
)
def acknowledge_staff_notice(delivery_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    if (
        set(request.form.keys()) != {"acknowledge"}
        or request.form.get("acknowledge") != "yes"
    ):
        return "Explicit acknowledgement confirmation is required.", 400

    conn = get_db()
    now_utc = get_application_now_utc()
    try:
        conn.execute("BEGIN IMMEDIATE")
        recipient = _get_authenticated_staff_notice_recipient(conn)
        reconcile_staff_notice_non_shift_requirements_in_transaction(
            conn,
            now_utc
        )
        delivery = _load_staff_notice_recipient_delivery(
            conn,
            delivery_id,
            recipient["user_id"],
            now_utc
        )
        if delivery is None:
            conn.rollback()
            return "Staff Notice delivery not found", 404

        _acknowledge_staff_notice_delivery(
            conn,
            delivery,
            recipient["user_id"],
            now_utc
        )
        conn.commit()
    except PermissionError:
        if conn.in_transaction:
            conn.rollback()
        return "Access denied", 403
    except StaffNoticeRecipientError as error:
        if conn.in_transaction:
            conn.rollback()
        return str(error), 409
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "The Staff Notice acknowledgement could not be recorded. "
            "Please retry.",
            503
        )
    finally:
        conn.close()

    return redirect(url_for(
        "staff_notice_detail",
        delivery_id=delivery_id,
        acknowledged=1
    ))


def get_current_shift_type(current_datetime=None):
    if current_datetime is None:
        current_datetime = datetime.now(VANCOUVER_TIMEZONE)
    hour = current_datetime.hour

    if 7 <= hour < 15:
        return "Day"
    elif 15 <= hour < 23:
        return "Afternoon"
    else:
        return "Overnight"
    

def get_current_shift_date(current_datetime=None):
    """Return the start date for the current Vancouver shift."""
    if current_datetime is None:
        current_datetime = datetime.now(VANCOUVER_TIMEZONE)

    shift_date = current_datetime.date()
    if current_datetime.hour < 7:
        shift_date -= timedelta(days=1)
    return shift_date


def get_active_shift_staff():
    current_datetime = datetime.now(VANCOUVER_TIMEZONE)
    shift_type = get_current_shift_type(current_datetime)
    shift_date = get_current_shift_date(current_datetime).isoformat()

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

def get_management_inbox(current_user_id):
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
              AND ack.user_id = ?
              AND ack.active = 1
        )
        ORDER BY sn.shift_date DESC  -- CHANGED from note_date
        LIMIT 5
    """, (current_user_id,)).fetchall()

    activities_to_review_list = conn.execute("""
        SELECT
            sa.shift_activity_id,
            sa.start_time,
            sa.end_time,
            sa.activity_description,
            s.shift_date,
            s.shift_type,
            u.full_name
        FROM shift_activities sa
        JOIN shifts s ON s.shift_id = sa.shift_id
        JOIN users u ON u.user_id = sa.recorded_by_user_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM acknowledgements ack
            WHERE ack.source_table = 'shift_activities'
              AND ack.source_id = sa.shift_activity_id
              AND ack.user_id = ?
              AND ack.acknowledgement_type = 'Review'
              AND ack.active = 1
        )
        ORDER BY sa.created_at DESC, sa.shift_activity_id DESC
        LIMIT 5
    """, (current_user_id,)).fetchall()

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
        "activities_to_review_list": [
            dict(r) for r in activities_to_review_list
        ],
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

    pending_leave_count = conn.execute("""
        SELECT COUNT(*)
        FROM leave_requests
        WHERE status = 'PENDING'
    """).fetchone()[0]

    if pending_leave_count:
        request_word = "request" if pending_leave_count == 1 else "requests"
        alerts.append({
            "level": "warning",
            "title": "Pending Leave Requests",
            "message": (
                f"{pending_leave_count} pending leave {request_word} "
                + ("requires" if pending_leave_count == 1 else "require")
                + " review."
            ),
            "action_url": url_for(
                "leave_request_review_list",
                status="PENDING"
            ),
            "action_label": "Review Pending Leave Requests"
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
            conn = get_db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                login_at_utc = get_application_now_utc()
                current_user = conn.execute("""
                    SELECT *
                    FROM users
                    WHERE user_id = ?
                      AND active = 1
                """, (user["user_id"],)).fetchone()
                if current_user is None:
                    raise PermissionError(
                        "The user is no longer active."
                    )
                log_activity(
                    conn,
                    activity_class="LOGIN",
                    activity_type="user_login",
                    summary=(
                        f"User logged in: {current_user['full_name']}"
                    ),
                    user_id=current_user["user_id"],
                    success=1
                )
                reconcile_staff_notice_non_shift_requirements_in_transaction(
                    conn,
                    login_at_utc
                )
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                error = (
                    "Login could not be completed. Please retry."
                )
            else:
                session["user_id"] = current_user["user_id"]
                session["full_name"] = current_user["full_name"]
                session["role"] = current_user["role"]
                session["last_activity"] = time.time()

                if current_user["must_change_password"] == 1:
                    return redirect(url_for("change_password"))

                return redirect(url_for("dashboard"))
            finally:
                conn.close()

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

@app.route("/documentation-context", methods=["GET", "POST"])
def documentation_context():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        user = get_active_authenticated_user(conn, session["user_id"])
        if user["role"] not in SHIFT_AUTO_SIGN_ON_ROLES:
            return "Access denied", 403
        selected_id = _session_documentation_shift_id()
        state = get_worker_documentation_context_state(
            conn,
            user["user_id"],
            selected_shift_id=selected_id
        )
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()

    if selected_id is not None and state["selected"] is None:
        _clear_documentation_shift_id()
        flash(
            "Your selected documentation shift is no longer available."
        )

    if request.method == "POST":
        action = request.form.get("action", "select")
        requested_shift_id = request.form.get("shift_id", type=int)

        if action == "start_new_shift":
            if state["has_active"]:
                return "A current shift is already active.", 409
            _clear_documentation_shift_id()
            try:
                new_shift_id, start_checklist_completed = auto_sign_on_user(
                    session["user_id"]
                )
            except StaffNoticeShiftSignOnError as error:
                flash(str(error))
                return redirect(url_for("dashboard"))
            _store_documentation_shift_id(new_shift_id)
            if not start_checklist_completed:
                return redirect(url_for(
                    "start_checklist", shift_id=new_shift_id
                ))
            return redirect(url_for(
                "shift_dashboard", shift_id=new_shift_id
            ))

        conn = get_db()
        try:
            authorized = get_worker_documentation_shift_context(
                conn,
                requested_shift_id,
                session["user_id"]
            ) if requested_shift_id is not None else None
        finally:
            conn.close()

        if authorized is None:
            _clear_documentation_shift_id()
            flash(
                "That documentation shift is no longer available. "
                "Please choose another shift."
            )
            return redirect(url_for("documentation_context"))

        _store_documentation_shift_id(requested_shift_id)
        return redirect(url_for(
            "shift_dashboard", shift_id=requested_shift_id
        ))

    if not state["available"]:
        return redirect(url_for("dashboard"))

    return render_template(
        "documentation_context.html",
        contexts=state["available"],
        has_active=state["has_active"]
    )

def get_dashboard_stats(current_user_id):
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
              AND ack.user_id = ?
              AND ack.active = 1
        )
    """, (current_user_id,)).fetchone()["count"]

    activities_to_review = conn.execute("""
        SELECT COUNT(*) AS count
        FROM shift_activities sa
        WHERE NOT EXISTS (
            SELECT 1
            FROM acknowledgements ack
            WHERE ack.source_table = 'shift_activities'
              AND ack.source_id = sa.shift_activity_id
              AND ack.user_id = ?
              AND ack.acknowledgement_type = 'Review'
              AND ack.active = 1
        )
    """, (current_user_id,)).fetchone()["count"]

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
        "activities_to_review": activities_to_review,
        "open_incidents": open_incidents,
        "recent_activity": recent_activity
    }

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        current_user = get_active_authenticated_user(
            conn,
            session["user_id"]
        )
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()

    if current_user["role"] in STAFF_NOTICE_MANAGEMENT_ROLES:
        try:
            staff_notice_collection = (
                _load_management_staff_notice_dashboard(
                    current_user["user_id"]
                )
            )
        except BaseException:
            return (
                "Staff Notices could not be loaded. Please retry.",
                503
            )
        stats = get_dashboard_stats(current_user["user_id"])
        inbox = get_management_inbox(current_user["user_id"])
        active_staff = get_active_shift_staff()
        manager_alerts = get_manager_alerts()

        return render_template(
            "admin_dashboard.html",
            active_staff=active_staff,
            manager_alerts=manager_alerts,
            staff_notices=staff_notice_collection["dashboard"],
            staff_notice_outstanding_count=(
                staff_notice_collection["outstanding_count"]
            ),
            **stats,
            **inbox
        )

    if current_user["role"] not in SHIFT_AUTO_SIGN_ON_ROLES:
        return redirect(url_for("staff_notice_history"))

    conn = get_db()
    try:
        raw_selected_id = session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
        selected_id = _session_documentation_shift_id()
        if raw_selected_id is not None and selected_id is None:
            _clear_documentation_shift_id()
        state = get_worker_documentation_context_state(
            conn,
            current_user["user_id"],
            selected_shift_id=selected_id
        )
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()

    if selected_id is not None and state["selected"] is None:
        _clear_documentation_shift_id()
        flash(
            "Your selected documentation shift is no longer available."
        )

    if state["selected"] is not None:
        return redirect(url_for(
            "shift_dashboard",
            shift_id=state["selected"]["shift_id"]
        ))

    if state["available"]:
        if len(state["available"]) == 1 and state["has_active"]:
            active_context = state["available"][0]
            _store_documentation_shift_id(active_context["shift_id"])
            return redirect(url_for(
                "shift_dashboard", shift_id=active_context["shift_id"]
            ))
        return redirect(url_for("documentation_context"))

    try:
        shift_id, start_checklist_completed = auto_sign_on_user(
            current_user["user_id"]
        )
    except StaffNoticeShiftSignOnError as error:
        return str(error), 503

    _store_documentation_shift_id(shift_id)

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

    authorization_conn = get_db()
    try:
        actor = get_active_authenticated_user(
            authorization_conn,
            session["user_id"]
        )
    except PermissionError:
        return "Access denied", 403
    finally:
        authorization_conn.close()
    if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
        return "Access denied", 403

    error = None
    error_status = 400

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
                conn.execute("BEGIN IMMEDIATE")
                changed_at_utc = get_application_now_utc()
                actor = get_active_authenticated_user(
                    conn,
                    session["user_id"]
                )
                if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
                    raise PermissionError(
                        "Current user is not allowed to manage users."
                    )
                cur = conn.execute("""
                    INSERT INTO users
                    (username, password_hash, full_name, role, active,
                     must_change_password)
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
                    user_id=actor["user_id"],
                    related_table="users",
                    related_id=new_user_id,
                    details=f"Username: {username}; Role: {role}",
                    success=1
                )
                reconcile_staff_notice_user_lifecycle_in_transaction(
                    conn,
                    new_user_id,
                    actor["user_id"],
                    changed_at_utc
                )
                created_user = conn.execute("""
                    SELECT username, full_name, role, active
                    FROM users
                    WHERE user_id = ?
                """, (new_user_id,)).fetchone()
                if (
                    created_user is None
                    or created_user["username"] != username
                    or created_user["full_name"] != full_name
                    or created_user["role"] != role
                    or created_user["active"] != 1
                ):
                    raise RuntimeError(
                        "User creation verification failed."
                    )
                conn.commit()
                return redirect(url_for("users"))
            except sqlite3.IntegrityError:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                error = "That username already exists."
            except PermissionError:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                return "Access denied", 403
            except BaseException:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                error = (
                    "The user could not be created. No changes were made. "
                    "Please retry."
                )
                error_status = 503
            finally:
                conn.close()

    return render_template(
        "user_new.html",
        error=error
    ), error_status if error else 200

@app.route("/user/edit/<int:user_id>", methods=["GET", "POST"])
def user_edit(user_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    status_filter = request.args.get(
        "status",
        "all"
    ).strip().lower()

    if status_filter not in ["all", "active", "inactive"]:
        status_filter = "all"

    conn = get_db()

    try:
        actor = get_active_authenticated_user(
            conn,
            session["user_id"]
        )
    except PermissionError:
        conn.close()
        return "Access denied", 403
    if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
        conn.close()
        return "Access denied", 403

    user = conn.execute("""
        SELECT user_id, username, full_name, role, active
        FROM users
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    if user is None:
        conn.close()
        return "User not found", 404

    error = None
    error_status = 400

    if request.method == "POST":
        username = request.form["username"].strip()
        full_name = request.form["full_name"].strip()
        role = request.form["role"]
        active = 1 if "active" in request.form else 0

        if not username or not full_name:
            error = "Username and full name are required."
        else:
            try:
                conn.execute("BEGIN IMMEDIATE")
                changed_at_utc = get_application_now_utc()
                actor = get_active_authenticated_user(
                    conn,
                    session["user_id"]
                )
                if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
                    raise PermissionError(
                        "Current user is not allowed to manage users."
                    )
                current_user = conn.execute("""
                    SELECT user_id, username, full_name, role, active
                    FROM users
                    WHERE user_id = ?
                """, (user_id,)).fetchone()
                if current_user is None:
                    raise LookupError("User not found.")
                if (
                    current_user["username"] != user["username"]
                    or current_user["full_name"] != user["full_name"]
                    or current_user["role"] != user["role"]
                    or current_user["active"] != user["active"]
                ):
                    raise UserLifecycleConflictError(
                        "The user changed while this request was being "
                        "processed. Reload and retry."
                    )
                if (
                    current_user["username"] == username
                    and current_user["full_name"] == full_name
                    and current_user["role"] == role
                    and current_user["active"] == active
                ):
                    conn.rollback()
                    conn.close()
                    return redirect(
                        url_for("users", status=status_filter)
                    )

                cursor = conn.execute("""
                    UPDATE users
                    SET username = ?, full_name = ?, role = ?, active = ?
                    WHERE user_id = ?
                      AND username = ?
                      AND full_name = ?
                      AND role = ?
                      AND active = ?
                """, (
                    username,
                    full_name,
                    role,
                    active,
                    user_id,
                    current_user["username"],
                    current_user["full_name"],
                    current_user["role"],
                    current_user["active"]
                ))
                if cursor.rowcount != 1:
                    raise UserLifecycleConflictError(
                        "The user changed while this request was being "
                        "processed. Reload and retry."
                    )

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
                    user_id=actor["user_id"],
                    related_table="users",
                    related_id=user_id,
                    details=details,
                    success=1
                )

                if (
                    current_user["role"] != role
                    or current_user["active"] != active
                ):
                    reconcile_staff_notice_user_lifecycle_in_transaction(
                        conn,
                        user_id,
                        actor["user_id"],
                        changed_at_utc
                    )
                verified_user = conn.execute("""
                    SELECT username, full_name, role, active
                    FROM users
                    WHERE user_id = ?
                """, (user_id,)).fetchone()
                if (
                    verified_user is None
                    or verified_user["username"] != username
                    or verified_user["full_name"] != full_name
                    or verified_user["role"] != role
                    or verified_user["active"] != active
                ):
                    raise RuntimeError("User update verification failed.")
                conn.commit()
                conn.close()

                return redirect(url_for("users", status=status_filter))

            except sqlite3.IntegrityError:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                error = "That username already exists."
            except PermissionError:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                conn.close()
                return "Access denied", 403
            except LookupError:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                conn.close()
                return "User not found", 404
            except UserLifecycleConflictError as caught_error:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                error = str(caught_error)
                error_status = 409
            except BaseException:
                try:
                    conn.rollback()
                except BaseException:
                    pass
                error = (
                    "The user could not be updated. No changes were made. "
                    "Please retry."
                )
                error_status = 503

    conn.close()

    return render_template(
        "user_edit.html",
        user=user,
        error=error,
        status_filter=status_filter
    ), error_status if error else 200

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

@app.route("/shifts/manage")
def shift_management():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        actor = get_active_authenticated_user(conn, session["user_id"])
        if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
            return "Access denied", 403
        shifts = conn.execute("""
            SELECT
                s.*,
                c.client_name,
                COUNT(ss.shift_staff_id) AS assignment_count,
                SUM(CASE WHEN ss.active = 1 THEN 1 ELSE 0 END)
                    AS active_assignment_count
            FROM shifts s
            JOIN clients c
                ON s.client_id = c.client_id
            LEFT JOIN shift_staff ss
                ON s.shift_id = ss.shift_id
            GROUP BY s.shift_id
            ORDER BY s.shift_date DESC, s.shift_type, s.shift_id DESC
        """).fetchall()
        return render_template("shift_management.html", shifts=shifts)
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()


@app.route("/shift/<int:shift_id>/cancel", methods=["GET", "POST"])
def shift_cancel(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    shift = None
    assignments = []
    error = None
    error_status = 400
    reason = request.form.get("reason", "").strip()

    try:
        actor = get_active_authenticated_user(conn, session["user_id"])
        if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
            return "Access denied", 403
        shift = conn.execute("""
            SELECT s.*, c.client_name
            FROM shifts s
            JOIN clients c
                ON s.client_id = c.client_id
            WHERE s.shift_id = ?
        """, (shift_id,)).fetchone()
        if shift is None:
            return "Shift not found", 404
        assignments = conn.execute("""
            SELECT ss.*, u.full_name, u.role
            FROM shift_staff ss
            JOIN users u
                ON ss.user_id = u.user_id
            WHERE ss.shift_id = ?
            ORDER BY ss.shift_staff_id
        """, (shift_id,)).fetchall()

        if request.method == "POST":
            if request.form.get("confirm") != "yes":
                raise ValueError(
                    "You must explicitly confirm shift cancellation."
                )
            if not reason:
                raise ValueError("A cancellation reason is required.")

            conn.execute("BEGIN IMMEDIATE")
            cancelled_at_utc = get_application_now_utc()
            result = cancel_shift_in_transaction(
                conn,
                shift_id,
                session["user_id"],
                reason,
                cancelled_at_utc
            )
            conn.commit()
            return redirect(url_for(
                "shift_management",
                cancellation_result=(
                    "cancelled" if result["cancelled"] else "unchanged"
                )
            ))
    except PermissionError:
        try:
            conn.rollback()
        except BaseException:
            pass
        return "Access denied", 403
    except LookupError:
        try:
            conn.rollback()
        except BaseException:
            pass
        return "Shift not found", 404
    except ShiftCancellationConflictError as caught_error:
        try:
            conn.rollback()
        except BaseException:
            pass
        error = str(caught_error)
        error_status = 409
    except ValueError as caught_error:
        try:
            conn.rollback()
        except BaseException:
            pass
        error = str(caught_error)
        error_status = 400
    except BaseException:
        try:
            conn.rollback()
        except BaseException:
            pass
        error = (
            "The shift could not be cancelled. No changes were made. "
            "Please retry."
        )
        error_status = 503
    finally:
        conn.close()

    return render_template(
        "shift_cancel.html",
        shift=shift,
        assignments=assignments,
        reason=reason,
        error=error
    ), error_status if error else 200


def _find_cancelled_matching_shift(conn, client_id, shift_date, shift_type):
    return conn.execute("""
        SELECT shift_id
        FROM shifts
        WHERE client_id = ?
          AND shift_date = ?
          AND shift_type = ?
          AND status = 'Cancelled'
        ORDER BY shift_id
        LIMIT 1
    """, (client_id, shift_date, shift_type)).fetchone()


def auto_sign_on_user(user_id):
    current_datetime = datetime.now(VANCOUVER_TIMEZONE)
    shift_type = get_current_shift_type(current_datetime)
    shift_date = get_current_shift_date(current_datetime).isoformat()
    actual_start_time = current_datetime.strftime("%H:%M")

    conn = get_db()

    try:
        conn.execute("BEGIN IMMEDIATE")
        operational_user = get_active_authenticated_user(conn, user_id)
        if operational_user["role"] not in SHIFT_AUTO_SIGN_ON_ROLES:
            raise PermissionError(
                "This role cannot automatically sign on to a shift."
            )
        active_clients = [dict(row) for row in conn.execute("""
            SELECT client_id
            FROM clients
            WHERE active = 1
            ORDER BY client_id
        """).fetchall()]
        if len(active_clients) != 1:
            raise RuntimeError(
                "Automatic sign-on requires exactly one active client."
            )
        client_id = active_clients[0]["client_id"]
        shift = conn.execute("""
            SELECT shift_id
            FROM shifts
            WHERE client_id = ?
              AND shift_date = ?
              AND shift_type = ?
              AND status = 'Open'
        """, (client_id, shift_date, shift_type)).fetchone()

        if shift is None:
            if _find_cancelled_matching_shift(
                conn,
                client_id,
                shift_date,
                shift_type
            ) is not None:
                raise ShiftCancellationConflictError(
                    "This shift was cancelled. A replacement cannot be "
                    "created through sign-on."
                )
            cur = conn.execute("""
                INSERT INTO shifts
                (client_id, shift_date, shift_type, status)
                VALUES (?, ?, ?, 'Open')
            """, (client_id, shift_date, shift_type))

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

            reconcile_staff_notice_shift_sign_on(
                conn,
                shift_id,
                user_id,
                current_datetime.astimezone(timezone.utc)
            )
            log_activity(
                conn,
                activity_class="SHIFT",
                activity_type="auto_sign_on",
                summary=f"User automatically signed onto {shift_type} shift",
                user_id=user_id,
                client_id=client_id,
                shift_id=shift_id,
                related_table="shift_staff",
                related_id=shift_staff_id,
                success=1
            )
        else:
            shift_staff_id = existing["shift_staff_id"]
            start_checklist_completed = existing["start_checklist_completed"]

        conn.commit()
    except Exception as error:
        if conn.in_transaction:
            conn.rollback()
        raise StaffNoticeShiftSignOnError(
            "Shift sign-on could not be completed. Please try again."
        ) from error
    finally:
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

            try:
                conn.execute("BEGIN IMMEDIATE")

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
                    if _find_cancelled_matching_shift(
                        conn,
                        1,
                        shift_date,
                        shift_type
                    ) is not None:
                        raise ShiftCancellationConflictError(
                            "This shift was cancelled. A replacement "
                            "cannot be created through sign-on."
                        )
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
                    conn.rollback()
                    return redirect(
                        url_for("shift_dashboard", shift_id=shift_id)
                    )

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
                reconcile_staff_notice_shift_sign_on(
                    conn,
                    shift_id,
                    session["user_id"],
                    get_application_now_utc()
                )
                conn.commit()
                return redirect(
                    url_for("shift_dashboard", shift_id=shift_id)
                )
            except ShiftCancellationConflictError as caught_error:
                if conn.in_transaction:
                    conn.rollback()
                error = str(caught_error)
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                error = (
                    "Shift sign-on could not be completed. "
                    "Please try again."
                )
            finally:
                conn.close()

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

    now_utc = get_application_now_utc()
    try:
        recipient = _get_authenticated_staff_notice_recipient(conn)
        if not _shift_is_cancelled(shift):
            conn.execute("BEGIN IMMEDIATE")
            reconcile_staff_notice_non_shift_requirements_in_transaction(
                conn,
                now_utc
            )
        staff_notice_collection = (
            _get_staff_notice_recipient_collections(
                conn,
                recipient["user_id"],
                now_utc
            )
        )
        if conn.in_transaction:
            conn.commit()
    except PermissionError:
        if conn.in_transaction:
            conn.rollback()
        conn.close()
        return "Access denied", 403
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        conn.close()
        return (
            "Staff Notices could not be loaded. Please retry.",
            503
        )

    food_fluid_authorized = False
    recent_food_fluid_entries = []
    try:
        get_active_food_fluid_shift_context(
            conn,
            shift_id,
            session["user_id"]
        )
        food_fluid_authorized = True
        recent_food_fluid_entries = get_food_fluid_shift_entries(
            conn,
            shift_id,
            limit=5
        )
    except PermissionError:
        pass

    sleep_authorized = False
    recent_sleep_events = []
    try:
        get_active_sleep_shift_context(conn, shift_id, session["user_id"])
        sleep_authorized = True
        recent_sleep_events = get_sleep_events(conn, shift_id)[:5]
    except PermissionError:
        pass

    staff = conn.execute("""
        SELECT ss.*, u.full_name, u.role
        FROM shift_staff ss
        JOIN users u ON ss.user_id = u.user_id
        WHERE ss.shift_id = ?
            AND (
                ? = 'Cancelled'
                OR ss.active = 1
            )
            AND u.role = 'Support Worker'
        ORDER BY ss.sign_on_at
    """, (shift_id, shift["status"])).fetchall()

    notes = conn.execute("""
        SELECT sn.*, u.full_name
        FROM shift_notes sn
        JOIN users u ON sn.user_id = u.user_id
        WHERE sn.shift_date = ?
        AND sn.shift_type = ?
        AND sn.client_id = ?
        ORDER BY sn.created_at DESC
        LIMIT 1
    """, (
        shift["shift_date"],
        shift["shift_type"],
        shift["client_id"]
    )).fetchall()
    shift_notes_editable = can_edit_shared_shift_note(
        conn,
        shift,
        session["user_id"]
    )
    
    care_tasks = get_applicable_care_tasks(conn, shift)

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

    housekeeping_tasks = get_applicable_housekeeping_tasks(conn, shift)

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

    documentation_context = None
    documentation_context_alternatives = []
    if session.get("role") in SHIFT_AUTO_SIGN_ON_ROLES:
        selected_id = _session_documentation_shift_id()
        context_state = get_worker_documentation_context_state(
            conn,
            session["user_id"],
            selected_shift_id=selected_id
        )
        if selected_id is not None and context_state["selected"] is None:
            _clear_documentation_shift_id()
            flash(
                "Your selected documentation shift is no longer available."
            )
        elif (
            context_state["selected"] is not None
            and context_state["selected"]["shift_id"] == shift_id
        ):
            documentation_context = context_state["selected"]
            documentation_context_alternatives = [
                context
                for context in context_state["available"]
                if context["shift_id"] != shift_id
            ]

    if documentation_context is not None:
        food_fluid_authorized = True
        recent_food_fluid_entries = get_food_fluid_shift_entries(
            conn, shift_id, limit=5
        )
        sleep_authorized = True
        recent_sleep_events = get_sleep_events(conn, shift_id)[:5]

    conn.close()

    return render_template(
        "shift_dashboard.html",
        shift=shift,
        staff=staff,
        notes=notes,
        shift_notes_editable=shift_notes_editable,
        shift_notes_saved=request.args.get("notes_saved") == "1",
        food_fluid_authorized=food_fluid_authorized,
        recent_food_fluid_entries=recent_food_fluid_entries,
        sleep_authorized=sleep_authorized,
        recent_sleep_events=recent_sleep_events,
        staff_notices=staff_notice_collection["dashboard"],
        staff_notice_outstanding_count=(
            staff_notice_collection["outstanding_count"]
        ),

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
        remaining_housekeeping_tasks=remaining_housekeeping_tasks,
        documentation_context=documentation_context,
        documentation_context_alternatives=(
            documentation_context_alternatives
        )
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

    cancelled_shift = conn.execute(
        "SELECT shift_id, status FROM shifts WHERE shift_id = ?",
        (shift_id,)
    ).fetchone()
    if cancelled_shift and _shift_is_cancelled(cancelled_shift):
        conn.close()
        return _cancelled_shift_response()

    try:
        shift, documentation_context_alternatives = (
            get_worker_documentation_module_context(
                conn, shift_id, session["user_id"]
            )
        )
    except DocumentationContextUnavailable:
        conn.close()
        return _documentation_context_redirect()
    except PermissionError:
        conn.close()
        return "Access denied", 403

    if _shift_is_cancelled(shift):
        conn.close()
        return _cancelled_shift_response()

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
                general_comments=general_comments,
                documentation_context=(
                    shift
                    if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
                    else None
                ),
                documentation_context_alternatives=(
                    documentation_context_alternatives
                )
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

        try:
            shift, documentation_context_alternatives = (
                get_worker_documentation_module_context(
                    conn, shift_id, session["user_id"]
                )
            )
        except DocumentationContextUnavailable:
            conn.close()
            return _documentation_context_redirect()

        conn.execute("BEGIN IMMEDIATE")
        try:
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

            toileting_details = format_toileting_storyline_details(
                location, bm_size, bm_consistency,
                behaviour_before, behaviour_during, behaviour_after,
                behaviour_comments, general_comments
            )

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
                details="\n".join(toileting_details) or None,
                success=1,
                event_datetime=convert_vancouver_occurrence_input_to_utc(
                    event_datetime
                ),
                storyline_visible=True
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
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
        shift=shift,
        documentation_context=(
            shift
            if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
            else None
        ),
        documentation_context_alternatives=(
            documentation_context_alternatives
        )
    )


#####################################################################
# FOOD & FLUID V1: WORKER WORKFLOWS
#####################################################################

@app.route("/shift/<int:shift_id>/sleep", methods=["GET", "POST"])
def sleep_events(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        context, documentation_context_alternatives = (
                get_worker_documentation_module_context(
                    conn,
                    shift_id,
                    session["user_id"],
                    active_context_loader=get_active_sleep_shift_context
                )
        )
    except DocumentationContextUnavailable:
        conn.close()
        return _documentation_context_redirect()
    except PermissionError:
        conn.close()
        return "Active participation in this open shift is required.", 403

    error = None
    values = {
        "event_local": datetime.now(VANCOUVER_TIMEZONE).strftime(
            "%Y-%m-%dT%H:%M"
        ),
        "note": "",
    }
    if request.method == "POST":
        event_type = request.form.get("event_type", "")
        event_local = request.form.get("event_local", "")
        note = request.form.get("note", "").strip() or None
        values["event_local"] = event_local
        values["note"] = request.form.get("note", "")
        if event_type not in ("fell_asleep", "woke_up"):
            error = "Sleep event type is invalid."
        else:
            try:
                local_naive = _parse_vancouver_local_input(event_local)
                candidates = _valid_vancouver_utc_candidates(local_naive)
                if len(candidates) != 1:
                    raise ValueError("Sleep event date and time is invalid.")
                event_utc = candidates[0]
                if event_utc > datetime.now(timezone.utc) + timedelta(minutes=5):
                    raise ValueError("Sleep event cannot be unreasonably in the future.")
                event_datetime = event_utc.isoformat().replace("+00:00", "Z")
                context, documentation_context_alternatives = (
                    get_worker_documentation_module_context(
                        conn,
                        shift_id,
                        session["user_id"],
                        active_context_loader=get_active_sleep_shift_context
                    )
                )
                cursor = conn.execute("""
                    INSERT INTO sleep_events
                    (client_id, shift_id, event_type, event_datetime,
                     recorded_by_user_id, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    context["client_id"], shift_id, event_type,
                    event_datetime,
                    session["user_id"], note
                ))
                event_id = cursor.lastrowid
                activity_type = (
                    "sleep_fell_asleep"
                    if event_type == "fell_asleep"
                    else "sleep_woke_up"
                )
                summary = (
                    "Client fell asleep"
                    if event_type == "fell_asleep"
                    else "Client woke up"
                )
                activity_details = f"Note: {note}" if note else None
                log_activity(
                    conn,
                    activity_class="SLEEP",
                    activity_type=activity_type,
                    summary=summary,
                    user_id=session["user_id"],
                    client_id=context["client_id"],
                    shift_id=shift_id,
                    related_table="sleep_events",
                    related_id=event_id,
                    details=activity_details,
                    success=1,
                    event_datetime=event_datetime,
                    storyline_visible=bool(note)
                )
                conn.commit()
                return redirect(url_for("sleep_events", shift_id=shift_id, created=1))
            except DocumentationContextUnavailable:
                if conn.in_transaction:
                    conn.rollback()
                conn.close()
                return _documentation_context_redirect()
            except (ValueError, sqlite3.IntegrityError) as caught_error:
                if conn.in_transaction:
                    conn.rollback()
                error = str(caught_error)

    events = get_sleep_events(conn, shift_id)
    conn.close()
    return render_template(
        "sleep_events.html",
        shift=context,
        events=events,
        error=error,
        values=values,
        documentation_context=(
            context if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
            else None
        ),
        documentation_context_alternatives=(
            documentation_context_alternatives
        )
    )


@app.route("/client/<int:client_id>/storyline")
def client_storyline(client_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    client = conn.execute(
        "SELECT client_id, client_name, active FROM clients WHERE client_id = ?",
        (client_id,)
    ).fetchone()
    if client is None:
        conn.close()
        return "Client not found", 404
    if not _storyline_access_allowed(conn, client_id, session["user_id"]):
        conn.close()
        return "Access denied", 403

    selected_filter = request.args.get("filter", "All")
    if selected_filter not in STORYLINE_FILTERS:
        selected_filter = "All"
    page = max(request.args.get("page", 1, type=int), 1)
    page_size = 25
    filter_types = STORYLINE_FILTERS[selected_filter]
    where = [
        "al.client_id = ?",
        "al.storyline_visible = 1",
        "al.success = 1",
        "al.client_id IS NOT NULL",
    ]
    parameters = [client_id]
    if filter_types is not None:
        placeholders = ", ".join("?" for _ in filter_types)
        ordered_types = sorted(filter_types)
        where.append(f"al.activity_type IN ({placeholders})")
        parameters.extend(ordered_types)
        if selected_filter == "Care":
            where[-1] = f"(al.activity_type IN ({placeholders}) OR al.activity_type LIKE ?)"
            parameters.append("care_task_%")
        elif selected_filter == "Housekeeping":
            where[-1] = f"(al.activity_type IN ({placeholders}) OR al.activity_type LIKE ?)"
            parameters.append("housekeeping_task_%")

    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM activity_log al WHERE {where_sql}",
        parameters
    ).fetchone()[0]
    page_count = max((total + page_size - 1) // page_size, 1)
    page = min(page, page_count)
    activity_log_columns = {
        row[1] for row in conn.execute('PRAGMA table_info("activity_log")')
    }
    event_datetime_select = (
        "al.event_datetime" if "event_datetime" in activity_log_columns
        else "NULL AS event_datetime"
    )
    events = conn.execute(f"""
        SELECT al.activity_id, al.activity_datetime, {event_datetime_select},
               al.activity_type, al.summary, al.details
        FROM activity_log al
        WHERE {where_sql}
        ORDER BY al.activity_id DESC
    """, parameters).fetchall()
    prepared_events = []
    for event in events:
        event = dict(event)
        event["label"] = _storyline_label(event["activity_type"])
        event["storyline_details"] = None
        event["storyline_behaviour_lines"] = None
        if event["activity_type"] in {
            "food_fluid_entry_created", "food_fluid_entry_voided"
        } and event["details"]:
            if event["details"].startswith(("Outcome:", "Original outcome:")):
                event["storyline_details"] = event["details"]
        elif event["activity_type"] == "shift_activity_created" and event["details"]:
            event["storyline_details"] = event["details"]
        elif event["activity_type"] in {
            "sleep_fell_asleep", "sleep_woke_up"
        } and event["details"]:
            event["storyline_details"] = event["details"]
        elif event["activity_type"] == "toileting_event_created" and event["details"]:
            event["storyline_details"] = event["details"]
        elif event["activity_type"] == "incident_created" and event["details"]:
            event["storyline_details"] = filter_incident_storyline_details(
                event["details"]
            )
        elif event["activity_type"] == "shift_note_updated" and event["details"]:
            event["storyline_details"] = event["details"]
        elif event["activity_type"] == "behaviour_occurrence_created" and event["details"]:
            event["storyline_details"] = event["details"]
            event["storyline_behaviour_lines"] = parse_abc_behaviour_storyline_details(
                event["details"]
            )
        elif event["activity_type"] == "behaviour_occurrence_voided" and event["details"]:
            event["storyline_details"] = event["details"]
        event["storyline_detail_lines"] = (
            event["storyline_details"].splitlines()
            if event["storyline_details"] else []
        )
        event["local_datetime"] = _storyline_local_datetime(
            event["event_datetime"], event["activity_datetime"]
        )
        event["heading"] = _storyline_heading(event["local_datetime"])
        event["event_date"] = (
            event["local_datetime"].date().isoformat()
            if event["local_datetime"] else ""
        )
        event["event_time"] = _storyline_time(event["local_datetime"])
        prepared_events.append(event)

    prepared_events.sort(
        key=lambda event: (
            event["local_datetime"] is not None,
            event["local_datetime"] or datetime.min.replace(
                tzinfo=VANCOUVER_TIMEZONE
            ),
            event["activity_id"]
        ),
        reverse=True
    )
    page_start = (page - 1) * page_size
    page_events = prepared_events[page_start:page_start + page_size]
    grouped_events = []
    for event in page_events:
        if not grouped_events or grouped_events[-1]["heading"] != event["heading"]:
            grouped_events.append({"heading": event["heading"], "events": []})
        grouped_events[-1]["events"].append(event)

    conn.close()

    return render_template(
        "client_storyline.html",
        client=client,
        grouped_events=grouped_events,
        filters=STORYLINE_FILTERS,
        selected_filter=selected_filter,
        page=page,
        page_count=page_count,
        total=total,
    )


@app.route("/shift/<int:shift_id>/food-fluid")
def food_fluid_shift_list(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        shift_context, documentation_context_alternatives = (
            get_worker_documentation_module_context(
                conn,
                shift_id,
                session["user_id"],
                active_context_loader=get_active_food_fluid_shift_context
            )
        )
        entries = get_food_fluid_shift_entries(conn, shift_id)
        return render_template(
            "food_fluid_shift_list.html",
            shift=shift_context,
            entries=entries,
            documentation_context=(
                shift_context
                if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
                else None
            ),
            documentation_context_alternatives=(
                documentation_context_alternatives
            )
        )
    except DocumentationContextUnavailable:
        return _documentation_context_redirect()
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()


@app.route(
    "/shift/<int:shift_id>/food-fluid/new",
    methods=["GET", "POST"]
)
def food_fluid_entry_new(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    shift_context = None
    values = {}

    try:
        shift_context, documentation_context_alternatives = (
            get_worker_documentation_module_context(
                conn,
                shift_id,
                session["user_id"],
                active_context_loader=get_active_food_fluid_shift_context
            )
        )

        if request.method == "GET":
            return render_template(
                "food_fluid_entry_new.html",
                shift=shift_context,
                values=values,
                error=None,
                interaction_types=FOOD_FLUID_INTERACTION_TYPES,
                outcomes=FOOD_FLUID_OUTCOMES,
                documentation_context=(
                    shift_context
                    if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
                    else None
                ),
                documentation_context_alternatives=(
                    documentation_context_alternatives
                ),
                default_event_local=datetime.now(
                    VANCOUVER_TIMEZONE
                ).strftime("%Y-%m-%dT%H:%M")
            )

        approved_fields = {
            "event_local",
            "repeated_hour_choice",
            "interaction_type",
            "item_description",
            "outcome",
            "physically_thrown",
            "additional_details",
        }
        if not set(request.form).issubset(approved_fields):
            raise ValueError("Food & Fluid form input is invalid.")

        required_fields = (
            "event_local",
            "interaction_type",
            "item_description",
            "outcome",
        )
        for field_name in required_fields:
            if len(request.form.getlist(field_name)) != 1:
                raise ValueError("Food & Fluid form input is invalid.")

        for field_name in (
            "repeated_hour_choice",
            "additional_details",
        ):
            if len(request.form.getlist(field_name)) > 1:
                raise ValueError("Food & Fluid form input is invalid.")

        thrown_values = request.form.getlist("physically_thrown")
        if not thrown_values:
            physically_thrown = 0
        elif thrown_values == ["1"]:
            physically_thrown = 1
        else:
            raise ValueError("Physically thrown input is invalid.")

        values = request.form.to_dict()
        event_local = strip_food_fluid_ascii_whitespace(
            request.form["event_local"]
        )
        repeated_hour_choice = strip_food_fluid_ascii_whitespace(
            request.form.get("repeated_hour_choice", "")
        )
        interaction_type = strip_food_fluid_ascii_whitespace(
            request.form["interaction_type"]
        )
        item_description = strip_food_fluid_ascii_whitespace(
            request.form["item_description"]
        )
        outcome = strip_food_fluid_ascii_whitespace(
            request.form["outcome"]
        )
        additional_details = strip_food_fluid_ascii_whitespace(
            request.form.get("additional_details", "")
        ) or None

        if interaction_type not in FOOD_FLUID_INTERACTION_TYPES:
            raise ValueError(
                "Interaction type must be Offered or Requested."
            )
        if not item_description:
            raise ValueError("Food or beverage item is required.")
        if outcome not in FOOD_FLUID_OUTCOMES:
            raise ValueError("A valid outcome is required.")
        if (
            outcome == "Item not available"
            and interaction_type != "Requested"
        ):
            raise ValueError(
                "Item not available is valid only for a request."
            )
        if (
            physically_thrown
            and outcome not in FOOD_FLUID_THROWN_OUTCOMES
        ):
            raise ValueError(
                "Physically thrown is valid only when partially "
                "consumed or refused."
            )

        conn.execute("BEGIN IMMEDIATE")
        try:
            shift_context, documentation_context_alternatives = (
                get_worker_documentation_module_context(
                    conn,
                    shift_id,
                    session["user_id"],
                    active_context_loader=get_active_food_fluid_shift_context
                )
            )
            event_at_utc = convert_food_fluid_event_input_to_utc(
                shift_context,
                event_local,
                repeated_hour_choice or None
            )
            submitted_at_utc = serialize_behaviour_utc(
                datetime.now(timezone.utc).replace(microsecond=0)
            )
            submission_token = secrets.token_urlsafe(32)

            cursor = conn.execute("""
                INSERT INTO food_fluid_entries
                (
                    shift_id,
                    client_id,
                    recorded_by_user_id,
                    event_at_utc,
                    interaction_type,
                    item_description,
                    outcome,
                    physically_thrown,
                    additional_details,
                    submitted_at_utc,
                    submission_token
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                shift_context["shift_id"],
                shift_context["client_id"],
                shift_context["recorded_by_user_id"],
                event_at_utc,
                interaction_type,
                item_description,
                outcome,
                physically_thrown,
                additional_details,
                submitted_at_utc,
                submission_token,
            ))
            entry_id = cursor.lastrowid

            log_activity(
                conn,
                activity_class="FOOD_FLUID",
                activity_type="food_fluid_entry_created",
                summary=format_food_fluid_storyline_summary(
                    interaction_type, item_description
                ),
                user_id=shift_context["recorded_by_user_id"],
                client_id=shift_context["client_id"],
                shift_id=shift_context["shift_id"],
                related_table="food_fluid_entries",
                related_id=entry_id,
                storyline_visible=True,
                details=format_food_fluid_storyline_details(
                    outcome, additional_details, physically_thrown
                ),
                success=1,
                event_datetime=event_at_utc
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise

        return redirect(
            url_for(
                "food_fluid_entry_new",
                shift_id=shift_context["shift_id"],
                created=1
            )
        )

    except DocumentationContextUnavailable:
        if conn.in_transaction:
            conn.rollback()
        return _documentation_context_redirect()
    except PermissionError:
        if conn.in_transaction:
            conn.rollback()
        return "Access denied", 403
    except ValueError as error:
        if conn.in_transaction:
            conn.rollback()
        return render_template(
            "food_fluid_entry_new.html",
            shift=shift_context,
            values=values,
            error=str(error),
            interaction_types=FOOD_FLUID_INTERACTION_TYPES,
            outcomes=FOOD_FLUID_OUTCOMES,
            documentation_context=(
                shift_context
                if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
                else None
            ),
            documentation_context_alternatives=(
                documentation_context_alternatives
            ),
            default_event_local=datetime.now(
                VANCOUVER_TIMEZONE
            ).strftime("%Y-%m-%dT%H:%M")
        ), 400
    finally:
        conn.close()


@app.route("/shift-staff/<int:shift_staff_id>/manager-sign-off", methods=["GET", "POST"])
def manager_sign_off(shift_staff_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"] not in ["Admin", "Program Manager", "Director"]:
        return "Access denied", 403

    conn = get_db()
    values = {
        "actual_end_date": request.form.get("actual_end_date", "").strip(),
        "actual_end_time": request.form.get("actual_end_time", "").strip(),
        "ambiguous_occurrence": request.form.get(
            "ambiguous_occurrence",
            ""
        ).strip(),
        "reason": request.form.get("reason", "").strip()
    }
    error = None
    error_status = 400
    staff_shift = None

    try:
        staff_shift = conn.execute("""
            SELECT
                ss.*,
                u.full_name,
                s.shift_date,
                s.shift_type,
                s.client_id,
                s.status
            FROM shift_staff ss
            JOIN users u
                ON ss.user_id = u.user_id
            JOIN shifts s
                ON ss.shift_id = s.shift_id
            WHERE ss.shift_staff_id = ?
        """, (shift_staff_id,)).fetchone()
        if staff_shift is None:
            return "Shift staff record not found", 404
        if _shift_is_cancelled(staff_shift):
            return _cancelled_shift_response()

        if request.method == "POST":
            if not values["reason"]:
                raise ShiftStaffCompletionError(
                    "A correction reason is required."
                )
            local_end_value = (
                f"{values['actual_end_date']}T"
                f"{values['actual_end_time']}"
            )
            actual_end = staff_notice_manager_local_datetime_to_utc(
                local_end_value,
                values["ambiguous_occurrence"] or None
            )
            correction_entry = get_application_now_utc()
            if actual_end > correction_entry:
                raise ShiftStaffCompletionError(
                    "The genuine historical end cannot be future-dated."
                )
            recorded_start = _shift_staff_recorded_start_at_utc(
                staff_shift
            )
            if actual_end < recorded_start:
                raise ShiftStaffCompletionError(
                    "The genuine historical end cannot precede the "
                    "recorded genuine start."
                )

            conn.execute("BEGIN IMMEDIATE")
            result = complete_shift_staff_assignment(
                conn,
                shift_staff_id,
                actual_end,
                correction_entry,
                session["user_id"],
                0,
                values["reason"]
            )
            if result["assignment_completed"] == 1:
                actual_end_at_utc = format_staff_notice_utc_datetime(
                    actual_end
                )
                correction_entry_at_utc = (
                    format_staff_notice_utc_datetime(correction_entry)
                )
                log_activity(
                    conn,
                    activity_class="SHIFT",
                    activity_type="manager_signed_staff_off",
                    summary=(
                        "Manager manually signed off "
                        f"{staff_shift['full_name']}"
                    ),
                    user_id=session["user_id"],
                    client_id=staff_shift["client_id"],
                    shift_id=staff_shift["shift_id"],
                    related_table="shift_staff",
                    related_id=shift_staff_id,
                    details=(
                        f"Shift Staff ID: {shift_staff_id}; "
                        f"Shift ID: {staff_shift['shift_id']}; "
                        f"Actor User ID: {session['user_id']}; "
                        f"Genuine actual end UTC: {actual_end_at_utc}; "
                        "Correction entry UTC: "
                        f"{correction_entry_at_utc}; "
                        f"Reason: {values['reason']}"
                    ),
                    success=1
                )
            conn.commit()
            return redirect(url_for("dashboard"))
    except (ShiftStaffCompletionError, ValueError) as caught_error:
        if conn.in_transaction:
            conn.rollback()
        error = str(caught_error)
        if "already completed with a different" in error:
            error_status = 409
        elif "deactivated without a genuine" in error:
            error_status = 409
        elif "active assignment already" in error:
            error_status = 409
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        error = (
            "The manager sign-off could not be completed. "
            "Please retry."
        )
        error_status = 500
    finally:
        conn.close()

    return render_template(
        "manager_sign_off.html",
        staff_shift=staff_shift,
        values=values,
        error=error
    ), error_status if error else 200

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
    if _shift_is_cancelled(shift):
        conn.close()
        return _cancelled_shift_response()

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
            success=1,
            storyline_visible=True
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
    error = None
    error_status = 400
    shift = None

    try:
        shift = conn.execute("""
            SELECT *
            FROM shifts
            WHERE shift_id = ?
        """, (shift_id,)).fetchone()
        if shift is None:
            conn.close()
            return "Shift not found", 404
        if _shift_is_cancelled(shift):
            conn.close()
            return _cancelled_shift_response()

        if request.method == "POST":
            completed_at = get_application_now_utc()
            conn.execute("BEGIN IMMEDIATE")
            assignments = conn.execute("""
                SELECT *
                FROM shift_staff
                WHERE shift_id = ?
                  AND user_id = ?
                ORDER BY shift_staff_id DESC
            """, (shift_id, session["user_id"])).fetchall()
            active_assignments = [
                assignment
                for assignment in assignments
                if assignment["active"] == 1
            ]
            if len(active_assignments) > 1:
                raise ShiftStaffCompletionError(
                    "Multiple active assignments require repair before "
                    "this shift can be completed."
                )

            if not active_assignments:
                completed_assignment = next(
                    (
                        assignment
                        for assignment in assignments
                        if assignment["actual_end_at_utc"] is not None
                    ),
                    None
                )
                if completed_assignment is None:
                    raise ShiftStaffCompletionError(
                        "No active assignment is available to complete."
                    )
                conn.commit()
                conn.close()
                session.clear()
                return redirect(url_for("login"))

            assignment = active_assignments[0]
            if assignment["actual_end_at_utc"] is not None:
                raise ShiftStaffCompletionError(
                    "This active assignment already has an actual end "
                    "and requires separate repair."
                )

            result = complete_shift_staff_assignment(
                conn,
                assignment["shift_staff_id"],
                completed_at,
                completed_at,
                session["user_id"],
                1,
                after_transition=lambda: save_shift_task_entries(
                    conn,
                    shift_id,
                    "END_SHIFT",
                    session["user_id"],
                    request.form
                )
            )
            if result["assignment_completed"] == 1:
                log_activity(
                    conn,
                    activity_class="SHIFT",
                    activity_type="end_shift_completed",
                    summary="End of Shift completed",
                    user_id=session["user_id"],
                    client_id=shift["client_id"],
                    shift_id=shift_id,
                    related_table="shift_staff",
                    related_id=assignment["shift_staff_id"],
                    success=1,
                    storyline_visible=True
                )
            conn.commit()
            conn.close()
            session.clear()
            return redirect(url_for("login"))
    except ShiftStaffCompletionError as caught_error:
        if conn.in_transaction:
            conn.rollback()
        error = str(caught_error)
        error_status = 409
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        error = "The shift could not be completed. Please retry."
        error_status = 500

    try:
        shift_tasks = conn.execute("""
            SELECT *
            FROM shift_tasks
            WHERE task_stage = 'END_SHIFT'
              AND active = 1
            ORDER BY task_name
        """).fetchall()
        return render_template(
            "end_shift.html",
            shift=shift,
            shift_tasks=shift_tasks,
            error=error
        ), error_status if error else 200
    finally:
        conn.close()

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

    try:
        editable = can_edit_shared_shift_note(
            conn,
            shift,
            session["user_id"]
        )
    except PermissionError:
        conn.close()
        return "Access denied", 403

    note = conn.execute("""
        SELECT *
        FROM shift_notes
        WHERE client_id = ?
          AND shift_date = ?
          AND shift_type = ?
        ORDER BY created_at DESC, note_id DESC
        LIMIT 1
    """, (
        shift["client_id"],
        shift["shift_date"],
        shift["shift_type"]
    )).fetchone()

    if request.method == "POST":
        if not editable:
            conn.close()
            return "Access denied", 403

        note_text = request.form.get("note_text", "").strip()
        if not note_text:
            conn.close()
            return "Staff notes are required.", 400

        if note is None:
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
                VALUES (?, ?, ?, ?, ?, 0)
            """, (
                shift["client_id"],
                session["user_id"],
                shift["shift_date"],
                shift["shift_type"],
                note_text
            ))
            shift_note_id = cur.lastrowid
        else:
            shift_note_id = note["note_id"]
            conn.execute("""
                UPDATE shift_notes
                SET user_id = ?,
                    note_text = ?,
                    created_at = CURRENT_TIMESTAMP
                WHERE note_id = ?
            """, (
                session["user_id"],
                note_text,
                shift_note_id
            ))

        log_activity(
            conn,
            activity_class="NOTE",
            activity_type="shift_note_updated",
            summary="Updated staff notes for shift",
            user_id=session["user_id"],
            client_id=shift["client_id"],
            shift_id=shift_id,
            related_table="shift_notes",
            related_id=shift_note_id,
            details=note_text,
            success=1,
            storyline_visible=True
        )

        conn.commit()
        conn.close()

        return redirect(url_for(
            "shift_dashboard",
            shift_id=shift_id,
            notes_saved=1
        ))

    conn.close()

    return render_template(
        "shift_add_note.html",
        shift=shift,
        note=note,
        editable=editable
    )


@app.route("/shift/<int:shift_id>/activity", methods=["GET", "POST"])
def shift_activities(shift_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    values = {}

    try:
        context, documentation_context_alternatives = (
            get_worker_documentation_module_context(
                conn,
                shift_id,
                session["user_id"],
                active_context_loader=get_shift_activity_context
            )
        )

        if request.method == "POST":
            if not context["editable"]:
                raise PermissionError(
                    "Active participation in this open shift is required."
                )
            values = request.form.to_dict()
            parsed = parse_shift_activity_form(request.form)

            conn.execute("BEGIN IMMEDIATE")
            try:
                context, documentation_context_alternatives = (
                    get_worker_documentation_module_context(
                        conn,
                        shift_id,
                        session["user_id"],
                        active_context_loader=get_shift_activity_context
                    )
                )
                cursor = conn.execute("""
                    INSERT INTO shift_activities
                    (
                        shift_id,
                        recorded_by_user_id,
                        start_time,
                        end_time,
                        a_selected,
                        t_selected,
                        ls_selected,
                        activity_description
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    context["shift_id"],
                    context["recorded_by_user_id"],
                    parsed["start_time"],
                    parsed["end_time"],
                    parsed["a_selected"],
                    parsed["t_selected"],
                    parsed["ls_selected"],
                    parsed["activity_description"],
                ))
                activity_id = cursor.lastrowid
                selected_categories = ", ".join(
                    field.removesuffix("_selected").upper()
                    for field in SHIFT_ACTIVITY_CATEGORY_FIELDS
                    if parsed[field]
                )
                log_activity(
                    conn,
                    activity_class="ACTIVITY",
                    activity_type="shift_activity_created",
                    summary=parsed["activity_description"],
                    user_id=context["recorded_by_user_id"],
                    client_id=context["client_id"],
                    shift_id=context["shift_id"],
                    related_table="shift_activities",
                    related_id=activity_id,
                    storyline_visible=True,
                    details=selected_categories,
                    success=1,
                    event_datetime=serialize_behaviour_utc(
                        datetime.combine(
                            date.fromisoformat(context["shift_date"]),
                            datetime.strptime(
                                parsed["start_time"], "%H:%M"
                            ).time(),
                            VANCOUVER_TIMEZONE
                        )
                    )
                )
                conn.commit()
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise

            return redirect(url_for(
                "shift_activities",
                shift_id=shift_id,
                created=1
            ))

        entries = get_shift_activity_entries(conn, shift_id)
        return render_template(
            "shift_activities.html",
            shift=context,
            entries=entries,
            values=values,
            error=None,
            created=request.args.get("created") == "1",
            documentation_context=(
                context
                if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
                else None
            ),
            documentation_context_alternatives=(
                documentation_context_alternatives
            )
        )
    except DocumentationContextUnavailable:
        if conn.in_transaction:
            conn.rollback()
        return _documentation_context_redirect()
    except LookupError:
        return "Shift not found", 404
    except PermissionError:
        if conn.in_transaction:
            conn.rollback()
        return "Access denied", 403
    except ValueError as error:
        if conn.in_transaction:
            conn.rollback()
        entries = get_shift_activity_entries(conn, shift_id)
        return render_template(
            "shift_activities.html",
            shift=context,
            entries=entries,
            values=values,
            error=str(error),
            created=False,
            documentation_context=(
                context
                if session.get(DOCUMENTATION_CONTEXT_SESSION_KEY)
                else None
            ),
            documentation_context_alternatives=(
                documentation_context_alternatives
            )
        ), 400
    finally:
        conn.close()
    
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

        injuries = 1 if "injury" in request.form else 0
        police_notified = 1 if "police_notified" in request.form else 0
        medical_treatment = 1 if "medical_treatment" in request.form else 0
        follow_up_required = 1 if "follow_up_required" in request.form else 0

        try:
            incident_event_datetime = convert_vancouver_occurrence_input_to_utc(
                f"{incident_date}T{incident_time}"
            )
        except ValueError as error:
            message = str(error)
            if message == "Occurrence time cannot be in the future.":
                message = "Incident date and time cannot be in the future."
            return render_template(
                "incident_new.html",
                error=message,
            ), 400

        conn = get_db()

        active_shift = conn.execute("""
            SELECT shift_id, client_id
            FROM shift_staff
            JOIN shifts USING (shift_id)
            WHERE user_id = ?
                AND active = 1
        """, (session["user_id"],)).fetchone()

        shift_id = active_shift["shift_id"] if active_shift else None
        client_id = active_shift["client_id"] if active_shift else None
        if client_id is None:
            conn.close()
            return "An active client shift is required to record an incident.", 400


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
            client_id,
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
            client_id=client_id,
            shift_id=shift_id,
            related_table="incident_reports",
            related_id=incident_id,
            details=format_incident_storyline_details(
                location, injuries, injury_details, actions_taken,
                description, follow_up_required
            ),
            success=1,
            event_datetime=incident_event_datetime,
            storyline_visible=True
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

    current_behaviour_week_monday = get_behaviour_operational_week_start(
        datetime.now(VANCOUVER_TIMEZONE)
    ).isoformat()

    return render_template(
        "manager_review_hub.html",
        current_behaviour_week_monday=current_behaviour_week_monday
    )


@app.route("/manager-review/activities")
def activity_review_list():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        actor = get_activity_management_actor(
            conn,
            session["user_id"]
        )
        entries = conn.execute("""
            SELECT
                sa.*,
                s.shift_date,
                s.shift_type,
                c.client_name,
                u.full_name AS recorded_by_name
            FROM shift_activities sa
            JOIN shifts s ON s.shift_id = sa.shift_id
            JOIN clients c ON c.client_id = s.client_id
            JOIN users u ON u.user_id = sa.recorded_by_user_id
            ORDER BY sa.created_at DESC, sa.shift_activity_id DESC
        """).fetchall()
        reviews = conn.execute("""
            SELECT
                ack.source_id AS shift_activity_id,
                ack.acknowledged_at,
                ack.user_id,
                u.full_name AS reviewed_by
            FROM acknowledgements ack
            JOIN users u ON u.user_id = ack.user_id
            WHERE ack.source_table = 'shift_activities'
              AND ack.acknowledgement_type = 'Review'
              AND ack.active = 1
            ORDER BY ack.acknowledged_at, ack.acknowledgement_id
        """).fetchall()
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()

    reviews_by_activity = {}
    reviewed_by_current_user = set()
    for review in reviews:
        activity_id = review["shift_activity_id"]
        reviews_by_activity.setdefault(activity_id, []).append(review)
        if review["user_id"] == actor["user_id"]:
            reviewed_by_current_user.add(review["shift_activity_id"])

    return render_template(
        "activity_review_list.html",
        entries=entries,
        reviews_by_activity=reviews_by_activity,
        reviewed_by_current_user=reviewed_by_current_user
    )


@app.route("/manager-review/activities/<int:activity_id>")
def activity_review_detail(activity_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        actor = get_activity_management_actor(
            conn,
            session["user_id"]
        )
        entry = conn.execute("""
            SELECT
                sa.*,
                s.shift_date,
                s.shift_type,
                s.client_id,
                c.client_name,
                u.full_name AS recorded_by_name
            FROM shift_activities sa
            JOIN shifts s ON s.shift_id = sa.shift_id
            JOIN clients c ON c.client_id = s.client_id
            JOIN users u ON u.user_id = sa.recorded_by_user_id
            WHERE sa.shift_activity_id = ?
        """, (activity_id,)).fetchone()
        if entry is None:
            return "Activity not found", 404

        reviews = conn.execute("""
            SELECT
                ack.acknowledgement_id,
                ack.acknowledged_at,
                ack.acknowledgement_type,
                ack.user_id,
                u.full_name AS reviewed_by
            FROM acknowledgements ack
            JOIN users u ON u.user_id = ack.user_id
            WHERE ack.source_table = 'shift_activities'
              AND ack.source_id = ?
              AND ack.acknowledgement_type = 'Review'
              AND ack.active = 1
            ORDER BY ack.acknowledged_at, ack.acknowledgement_id
        """, (activity_id,)).fetchall()
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()

    return render_template(
        "activity_review_detail.html",
        entry=entry,
        reviews=reviews,
        current_user_reviewed=any(
            review["user_id"] == actor["user_id"]
            for review in reviews
        )
    )


@app.route(
    "/manager-review/activities/<int:activity_id>/review",
    methods=["POST"]
)
def review_shift_activity(activity_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    if request.form:
        return "Invalid review request", 400

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        actor = get_activity_management_actor(
            conn,
            session["user_id"]
        )
        entry = conn.execute("""
            SELECT
                sa.shift_activity_id,
                sa.shift_id,
                s.client_id
            FROM shift_activities sa
            JOIN shifts s ON s.shift_id = sa.shift_id
            WHERE sa.shift_activity_id = ?
        """, (activity_id,)).fetchone()
        if entry is None:
            conn.rollback()
            return "Activity not found", 404

        create_acknowledgement(
            conn,
            source_table="shift_activities",
            source_id=activity_id,
            user_id=actor["user_id"],
            acknowledgement_type="Review",
            client_id=entry["client_id"],
            shift_id=entry["shift_id"]
        )
        conn.commit()
    except PermissionError:
        if conn.in_transaction:
            conn.rollback()
        return "Access denied", 403
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()

    return redirect(url_for(
        "activity_review_detail",
        activity_id=activity_id
    ))


FOOD_FLUID_REVIEW_FILTERS = frozenset((
    "all",
    "not_viewed",
    "awaiting_review",
    "reviewed",
    "voided",
))


def get_food_fluid_review_filter():
    requested = request.args.get("state", "all")
    return requested if requested in FOOD_FLUID_REVIEW_FILTERS else "all"


@app.route("/manager-review/food-fluid")
def food_fluid_review_list():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        get_food_fluid_management_actor(conn, session["user_id"])
        entries = get_food_fluid_management_entries(conn)
    except PermissionError:
        conn.close()
        return "Access denied", 403

    state_filter = get_food_fluid_review_filter()
    if state_filter == "not_viewed":
        entries = [
            entry for entry in entries
            if entry["management_state"] == "Not Viewed"
        ]
    elif state_filter == "awaiting_review":
        entries = [
            entry for entry in entries
            if entry["management_state"] == "Viewed – Awaiting Review"
        ]
    elif state_filter == "reviewed":
        entries = [
            entry for entry in entries
            if entry["management_state"] == "Reviewed"
        ]
    elif state_filter == "voided":
        entries = [
            entry for entry in entries
            if entry["status"] == "Voided"
        ]

    conn.close()
    return render_template(
        "food_fluid_review_list.html",
        entries=entries,
        state_filter=state_filter
    )


@app.route("/manager-review/food-fluid/<int:entry_id>")
def food_fluid_review_detail(entry_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        actor = get_food_fluid_management_actor(conn, session["user_id"])
        entry = get_food_fluid_management_entry(conn, entry_id)
        if entry is None:
            conn.rollback()
            conn.close()
            return "Food & Fluid entry not found", 404
        record_food_fluid_view(conn, entry, actor["user_id"])
        conn.commit()

        view_history = get_food_fluid_view_history(conn, entry_id)
        review_history = get_food_fluid_review_history(conn, entry_id)
    except PermissionError:
        conn.rollback()
        conn.close()
        return "Access denied", 403
    except Exception:
        conn.rollback()
        conn.close()
        raise

    reviewed_by_current_user = any(
        review["user_id"] == actor["user_id"]
        for review in review_history
    )
    entry["management_state"] = (
        "Reviewed" if review_history else "Viewed – Awaiting Review"
    )
    conn.close()
    return render_template(
        "food_fluid_review_detail.html",
        entry=entry,
        view_history=view_history,
        review_history=review_history,
        reviewed_by_current_user=reviewed_by_current_user,
        state_filter=get_food_fluid_review_filter()
    )


@app.route(
    "/manager-review/food-fluid/<int:entry_id>/review",
    methods=["POST"]
)
def review_food_fluid_entry(entry_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    if request.form:
        return "Invalid review request", 400

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        actor = get_food_fluid_management_actor(conn, session["user_id"])
        entry = get_food_fluid_management_entry(conn, entry_id)
        if entry is None:
            conn.rollback()
            conn.close()
            return "Food & Fluid entry not found", 404

        create_acknowledgement(
            conn,
            source_table="food_fluid_entries",
            source_id=entry_id,
            user_id=actor["user_id"],
            acknowledgement_type="Review",
            client_id=entry["client_id"],
            shift_id=entry["shift_id"]
        )
        conn.commit()
    except PermissionError:
        conn.rollback()
        conn.close()
        return "Access denied", 403
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.close()
    return redirect(url_for(
        "food_fluid_review_detail",
        entry_id=entry_id,
        state=get_food_fluid_review_filter()
    ))


@app.route(
    "/manager-review/food-fluid/<int:entry_id>/void",
    methods=["GET", "POST"]
)
def void_food_fluid_entry(entry_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = get_db()
    try:
        if request.method == "GET":
            get_food_fluid_management_actor(conn, session["user_id"])
            entry = get_food_fluid_management_entry(conn, entry_id)
            if entry is None:
                raise LookupError("Food & Fluid entry not found.")
            if entry["status"] != "Recorded":
                raise RuntimeError("Food & Fluid entry has already been voided.")
            return render_template(
                "food_fluid_void_confirm.html",
                entry=entry,
                state_filter=get_food_fluid_review_filter()
            )

        conn.execute("BEGIN IMMEDIATE")
        try:
            actor = get_food_fluid_management_actor(
                conn,
                session["user_id"]
            )
            if (
                set(request.form.keys()) != {"void_reason"}
                or len(request.form.getlist("void_reason")) != 1
            ):
                raise ValueError("Food & Fluid void input is invalid.")

            void_reason = request.form["void_reason"].strip()
            if not void_reason:
                raise ValueError("A Food & Fluid void reason is required.")

            entry = get_food_fluid_management_entry(conn, entry_id)
            if entry is None:
                raise LookupError("Food & Fluid entry not found.")
            if entry["status"] != "Recorded":
                raise RuntimeError(
                    "Food & Fluid entry has already been voided."
                )

            voided_at_utc = serialize_behaviour_utc(
                datetime.now(timezone.utc).replace(microsecond=0)
            )
            updated = conn.execute("""
                UPDATE food_fluid_entries
                SET
                    status = 'Voided',
                    voided_by_user_id = ?,
                    voided_at_utc = ?,
                    void_reason = ?
                WHERE food_fluid_entry_id = ?
                  AND status = 'Recorded'
            """, (
                actor["user_id"],
                voided_at_utc,
                void_reason,
                entry_id
            ))
            if updated.rowcount != 1:
                raise RuntimeError(
                    "Food & Fluid entry has already been voided."
                )

            log_activity(
                conn,
                activity_class="FOOD_FLUID",
                activity_type="food_fluid_entry_voided",
                summary=(
                    "Voided "
                    + format_food_fluid_storyline_summary(
                        entry["interaction_type"], entry["item_description"]
                    )
                ),
                user_id=actor["user_id"],
                client_id=entry["client_id"],
                shift_id=entry["shift_id"],
                related_table="food_fluid_entries",
                related_id=entry_id,
                storyline_visible=True,
                details=format_food_fluid_void_storyline_details(
                    entry["outcome"], void_reason
                ),
                success=1
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise

        return redirect(url_for(
            "food_fluid_review_detail",
            entry_id=entry_id,
            state=get_food_fluid_review_filter()
        ))
    except PermissionError:
        return "Access denied", 403
    except LookupError as error:
        return str(error), 404
    except RuntimeError as error:
        return str(error), 409
    except ValueError as error:
        return str(error), 400
    finally:
        conn.close()


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

    entries = [dict(entry) for entry in entries]
    for entry in entries:
        entry["event_local_display"] = format_toileting_local_datetime_display(
            entry["event_datetime"]
        )

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

    entry = dict(entry)
    entry["event_local_display"] = format_toileting_local_datetime_display(
        entry["event_datetime"]
    )

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

    entry = dict(entry)
    entry["event_local_display"] = format_toileting_local_datetime_display(
        entry["event_datetime"]
    )

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
        f"Event date and time: {entry['event_local_display']}\n"
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
    comment=None,
    client_id=None,
    shift_id=None,
    acknowledged_at=None,
    activity_details=None
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

    if acknowledged_at is None:
        acknowledged_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    else:
        acknowledged_at = format_staff_notice_utc_datetime(
            acknowledged_at
        )

    try:
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
    except sqlite3.IntegrityError:
        raced_existing = conn.execute("""
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
        if raced_existing is None:
            raise
        return raced_existing["acknowledgement_id"]

    acknowledgement_id = cur.lastrowid

    log_activity(
        conn,
        activity_class="ACKNOWLEDGEMENT",
        activity_type="record_acknowledged",
        summary=f"{acknowledgement_type} acknowledgement recorded",
        user_id=user_id,
        client_id=client_id,
        shift_id=shift_id,
        related_table="acknowledgements",
        related_id=acknowledgement_id,
        details=(
            activity_details
            if activity_details is not None
            else f"{source_table} #{source_id}"
        ),
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
    context = None
    task = None
    status = request.form.get("status", "")
    comment = request.form.get("comment", "").strip()

    def render_record(error=None, response_status=200):
        return render_template(
            "shift_care_task_record.html",
            shift=context,
            task=task,
            error=error,
            selected_status=status,
            comment=comment,
            documentation_context=context,
            documentation_context_alternatives=(
                documentation_context_alternatives
            )
        ), response_status

    try:
        context, documentation_context_alternatives = (
            get_worker_documentation_module_context(
                conn,
                shift_id,
                session["user_id"],
                active_context_loader=(
                    get_care_active_documentation_context
                )
            )
        )

        applicable_tasks = get_applicable_care_tasks(conn, context)
        task = next(
            (
                candidate
                for candidate in applicable_tasks
                if candidate["care_task_id"] == care_task_id
            ),
            None
        )
        if task is None:
            return "Care task not found or not applicable", 404

        if request.method == "POST":
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
                return render_record(error, 400)

            conn.execute("BEGIN IMMEDIATE")
            try:
                context, documentation_context_alternatives = (
                    get_worker_documentation_module_context(
                        conn,
                        shift_id,
                        session["user_id"],
                        active_context_loader=(
                            get_care_active_documentation_context
                        )
                    )
                )
                applicable_tasks = get_applicable_care_tasks(
                    conn,
                    context
                )
                task = next(
                    (
                        candidate
                        for candidate in applicable_tasks
                        if candidate["care_task_id"] == care_task_id
                    ),
                    None
                )
                if task is None:
                    raise LookupError(
                        "Care task not found or not applicable."
                    )

                existing = conn.execute("""
                    SELECT entry_id
                    FROM shift_care_task_entries
                    WHERE shift_id = ?
                      AND care_task_id = ?
                """, (
                    context["shift_id"],
                    care_task_id
                )).fetchone()

                if existing:
                    if (
                        context["documentation_access"]
                        == DOCUMENTATION_ACCESS_POST_SHIFT
                    ):
                        raise PermissionError(
                            "This Care result already exists and cannot "
                            "be edited through post-shift documentation."
                        )
                    conn.rollback()
                    return redirect(
                        url_for(
                            "shift_care_task_entry_edit",
                            shift_id=context["shift_id"],
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
                    context["shift_id"],
                    care_task_id,
                    status,
                    comment,
                    context["recorded_by_user_id"]
                ))

                entry_id = cur.lastrowid

                log_activity(
                    conn,
                    activity_class="CARE",
                    activity_type=(
                        f"care_task_{status.lower().replace(' ', '_')}"
                    ),
                    summary=f"{task['task_name']} - {status}",
                    user_id=context["recorded_by_user_id"],
                    client_id=context["client_id"],
                    shift_id=context["shift_id"],
                    related_table="shift_care_task_entries",
                    related_id=entry_id,
                    storyline_visible=True,
                    details=comment,
                    success=1
                )
                conn.commit()
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise

            return redirect(
                url_for(
                    "shift_dashboard",
                    shift_id=context["shift_id"]
                )
            )

        return render_record()
    except DocumentationContextUnavailable:
        if conn.in_transaction:
            conn.rollback()
        return _documentation_context_redirect()
    except LookupError as error:
        if conn.in_transaction:
            conn.rollback()
        if str(error) == "Care task not found or not applicable.":
            return str(error), 404
        return "Shift not found", 404
    except PermissionError as error:
        if conn.in_transaction:
            conn.rollback()
        if str(error).startswith("This Care result already exists"):
            return render_record(str(error), 409)
        cancelled_shift = conn.execute("""
            SELECT status
            FROM shifts
            WHERE shift_id = ?
        """, (shift_id,)).fetchone()
        if (
            cancelled_shift is not None
            and cancelled_shift["status"] == SHIFT_CANCELLED_STATUS
        ):
            return _cancelled_shift_response()
        return "Access denied", 403
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return "The Care result could not be recorded. Please retry.", 500
    finally:
        conn.close()

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
            shifts.client_id,
            shifts.status AS shift_status
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
    if entry["shift_status"] == SHIFT_CANCELLED_STATUS:
        conn.close()
        return _cancelled_shift_response()

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
            storyline_visible=True,
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
    context = None
    task = None
    status = request.form.get("status", "")
    comment = request.form.get("comment", "").strip()

    def render_record(error=None, response_status=200):
        return render_template(
            "shift_housekeeping_task_record.html",
            shift=context,
            task=task,
            error=error,
            selected_status=status,
            comment=comment,
            documentation_context=context,
            documentation_context_alternatives=(
                documentation_context_alternatives
            )
        ), response_status

    try:
        context, documentation_context_alternatives = (
            get_worker_documentation_module_context(
                conn,
                shift_id,
                session["user_id"],
                active_context_loader=(
                    get_housekeeping_active_documentation_context
                )
            )
        )

        applicable_tasks = get_applicable_housekeeping_tasks(conn, context)
        task = next(
            (
                candidate
                for candidate in applicable_tasks
                if candidate["housekeeping_task_id"]
                == housekeeping_task_id
            ),
            None
        )
        if task is None:
            return "Housekeeping task not found or not applicable", 404

        if request.method == "POST":
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
                return render_record(error, 400)

            conn.execute("BEGIN IMMEDIATE")
            try:
                context, documentation_context_alternatives = (
                    get_worker_documentation_module_context(
                        conn,
                        shift_id,
                        session["user_id"],
                        active_context_loader=(
                            get_housekeeping_active_documentation_context
                        )
                    )
                )
                applicable_tasks = get_applicable_housekeeping_tasks(
                    conn,
                    context
                )
                task = next(
                    (
                        candidate
                        for candidate in applicable_tasks
                        if candidate["housekeeping_task_id"]
                        == housekeeping_task_id
                    ),
                    None
                )
                if task is None:
                    raise LookupError(
                        "Housekeeping task not found or not applicable."
                    )

                existing = conn.execute("""
                    SELECT entry_id
                    FROM shift_housekeeping_task_entries
                    WHERE shift_id = ?
                      AND housekeeping_task_id = ?
                """, (
                    context["shift_id"],
                    housekeeping_task_id
                )).fetchone()

                if existing:
                    if (
                        context["documentation_access"]
                        == DOCUMENTATION_ACCESS_POST_SHIFT
                    ):
                        raise PermissionError(
                            "This Housekeeping result already exists and "
                            "cannot be edited through post-shift "
                            "documentation."
                        )
                    conn.rollback()
                    return redirect(
                        url_for(
                            "shift_housekeeping_task_entry_edit",
                            shift_id=context["shift_id"],
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
                    context["shift_id"],
                    housekeeping_task_id,
                    status,
                    comment,
                    context["recorded_by_user_id"]
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
                    user_id=context["recorded_by_user_id"],
                    client_id=context["client_id"],
                    shift_id=context["shift_id"],
                    related_table="shift_housekeeping_task_entries",
                    related_id=entry_id,
                    storyline_visible=True,
                    details=comment,
                    success=1
                )
                conn.commit()
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise

            return redirect(
                url_for(
                    "shift_dashboard",
                    shift_id=context["shift_id"]
                )
            )

        return render_record()
    except DocumentationContextUnavailable:
        if conn.in_transaction:
            conn.rollback()
        return _documentation_context_redirect()
    except LookupError as error:
        if conn.in_transaction:
            conn.rollback()
        if str(error) == "Housekeeping task not found or not applicable.":
            return str(error), 404
        return "Shift not found", 404
    except PermissionError as error:
        if conn.in_transaction:
            conn.rollback()
        if str(error).startswith("This Housekeeping result already exists"):
            return render_record(str(error), 409)
        cancelled_shift = conn.execute("""
            SELECT status
            FROM shifts
            WHERE shift_id = ?
        """, (shift_id,)).fetchone()
        if (
            cancelled_shift is not None
            and cancelled_shift["status"] == SHIFT_CANCELLED_STATUS
        ):
            return _cancelled_shift_response()
        return "Access denied", 403
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "The Housekeeping result could not be recorded. Please retry.",
            500
        )
    finally:
        conn.close()


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
    context = None
    entry = None
    outcome = request.form.get("status", "")
    comment = request.form.get("comment", "").strip()

    def render_edit(error=None, response_status=200):
        displayed_comment = (
            comment
            if request.method == "POST" or error is not None
            else entry["comment"]
        )
        selected_status = (
            outcome
            if request.method == "POST" or error is not None
            else entry["outcome"]
        )
        return render_template(
            "shift_housekeeping_task_edit.html",
            entry=entry,
            shift=context,
            error=error,
            selected_status=selected_status,
            comment=displayed_comment
        ), response_status

    def resolve_edit_context():
        nonlocal context, entry
        context, entry = get_housekeeping_edit_context(
            conn,
            shift_id,
            entry_id,
            session["user_id"]
        )

    try:
        if request.method == "POST":
            conn.execute("BEGIN IMMEDIATE")
            try:
                resolve_edit_context()

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
                    conn.rollback()
                    return render_edit(error, 400)

                updated = conn.execute("""
                    UPDATE shift_housekeeping_task_entries
                    SET outcome = ?,
                        comment = ?
                    WHERE entry_id = ?
                      AND shift_id = ?
                      AND housekeeping_task_id = ?
                      AND completed_by_user_id = ?
                """, (
                    outcome,
                    comment,
                    entry_id,
                    context["shift_id"],
                    entry["housekeeping_task_id"],
                    context["recorded_by_user_id"]
                ))
                if updated.rowcount != 1:
                    raise LookupError("Housekeeping task entry not found")

                log_activity(
                    conn,
                    activity_class="HOUSEKEEPING",
                    activity_type="housekeeping_task_updated",
                    summary=(
                        f"{entry['task_name']} updated to "
                        f"'{outcome}'"
                    ),
                    user_id=context["recorded_by_user_id"],
                    client_id=context["client_id"],
                    shift_id=context["shift_id"],
                    related_table="shift_housekeeping_task_entries",
                    related_id=entry_id,
                    storyline_visible=True,
                    details=comment,
                    success=1
                )
                conn.commit()
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise

            return redirect(
                url_for(
                    "shift_dashboard",
                    shift_id=context["shift_id"]
                )
            )

        resolve_edit_context()
        return render_edit()
    except AuthorizedCancelledHousekeepingEntry:
        if conn.in_transaction:
            conn.rollback()
        return _cancelled_shift_response()
    except (DocumentationContextUnavailable, PermissionError, LookupError):
        if conn.in_transaction:
            conn.rollback()
        return "Housekeeping task entry not found", 404
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        return (
            "The Housekeeping task entry could not be updated. Please retry.",
            500
        )
    finally:
        conn.close()



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
# WORKER RESOURCES / LEAVE REQUESTS
#####################################################################

def _leave_authenticated_actor(conn, user_id):
    get_active_authenticated_user(conn, user_id)
    return conn.execute("""
        SELECT user_id, full_name, role, active
        FROM users
        WHERE user_id = ? AND active = 1
    """, (user_id,)).fetchone()


def _leave_management_actor(conn, user_id):
    actor = _leave_authenticated_actor(conn, user_id)
    if actor["role"] not in STAFF_NOTICE_MANAGEMENT_ROLES:
        raise PermissionError("Current user is not allowed to review leave requests.")
    return actor


def _leave_utc_now():
    return format_staff_notice_utc_datetime(get_application_now_utc())


def _leave_display_timestamp(value):
    try:
        return parse_staff_notice_utc_datetime(value).astimezone(
            VANCOUVER_TIMEZONE
        ).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return value or ""


def _leave_request_values(form):
    leave_type = form.get("leave_type", "").strip()
    other_reason = form.get("other_reason", "").strip()
    start_date = form.get("start_date", "").strip()
    end_date = form.get("end_date", "").strip()
    day_part = form.get("day_part", "").strip()
    start_time = form.get("start_time", "").strip()
    end_time = form.get("end_time", "").strip()
    employee_comments = form.get("employee_comments", "").strip()

    if leave_type not in LEAVE_TYPES:
        raise ValueError("Select a valid leave type.")
    if leave_type == "Other" and not other_reason:
        raise ValueError("An explanation is required for Other leave.")
    if len(other_reason) > LEAVE_OTHER_REASON_MAX_LENGTH:
        raise ValueError("The Other explanation is too long.")
    if len(employee_comments) > LEAVE_COMMENT_MAX_LENGTH:
        raise ValueError("Employee comments are too long.")

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as error:
        raise ValueError("Leave dates must use YYYY-MM-DD.") from error
    if end < start:
        raise ValueError("The end date cannot be before the start date.")
    if day_part not in LEAVE_DAY_PARTS:
        raise ValueError("Select full-day or partial-day leave.")

    requested_days = None
    requested_hours = None
    if day_part == "FULL_DAY":
        start_time = None
        end_time = None
        requested_days = float((end - start).days + 1)
    else:
        if start != end:
            raise ValueError("Partial-day leave must use the same start and end date.")
        if not start_time or not end_time:
            raise ValueError("Partial-day leave requires start and end times.")
        try:
            parsed_start = datetime.strptime(start_time, "%H:%M")
            parsed_end = datetime.strptime(end_time, "%H:%M")
        except ValueError as error:
            raise ValueError("Leave times must use HH:MM.") from error
        if parsed_end <= parsed_start:
            raise ValueError("The end time must be later than the start time.")
        requested_hours = round(
            (parsed_end - parsed_start).total_seconds() / 3600,
            2
        )

    return {
        "leave_type": leave_type,
        "other_reason": other_reason or None,
        "start_date": start_date,
        "end_date": end_date,
        "day_part": day_part,
        "start_time": start_time,
        "end_time": end_time,
        "requested_days": requested_days,
        "requested_hours": requested_hours,
        "employee_comments": employee_comments or None,
    }


def _leave_form_values(entry=None):
    if entry is None:
        return {
            "leave_type": "",
            "other_reason": "",
            "start_date": "",
            "end_date": "",
            "day_part": "FULL_DAY",
            "start_time": "",
            "end_time": "",
            "employee_comments": "",
        }
    return {
        "leave_type": entry["leave_type"],
        "other_reason": entry["other_reason"] or "",
        "start_date": entry["start_date"],
        "end_date": entry["end_date"],
        "day_part": entry["day_part"],
        "start_time": entry["start_time"] or "",
        "end_time": entry["end_time"] or "",
        "employee_comments": entry["employee_comments"] or "",
    }


def _leave_prepare_entries(entries):
    prepared = []
    for entry in entries:
        item = dict(entry)
        item["submitted_display"] = _leave_display_timestamp(
            item.get("submitted_at_utc")
        )
        item["updated_display"] = _leave_display_timestamp(
            item.get("updated_at_utc")
        )
        item["reviewed_display"] = _leave_display_timestamp(
            item.get("reviewed_at_utc")
        )
        prepared.append(item)
    return prepared


def _leave_log_details(values, actor_user_id, previous_status=None, new_status=None):
    details = [
        f"Leave type: {values['leave_type']}",
        f"Dates: {values['start_date']} to {values['end_date']}",
        f"Day part: {values['day_part']}",
        f"Actor user ID: {actor_user_id}",
    ]
    if previous_status is not None:
        details.append(f"Previous status: {previous_status}")
    if new_status is not None:
        details.append(f"New status: {new_status}")
    return "\n".join(details)


def _leave_log(conn, activity_type, summary, actor_user_id, request_id,
               values, previous_status=None, new_status=None):
    log_activity(
        conn,
        activity_class="LEAVE",
        activity_type=activity_type,
        summary=summary,
        user_id=actor_user_id,
        client_id=None,
        shift_id=None,
        related_table="leave_requests",
        related_id=request_id,
        details=_leave_log_details(
            values, actor_user_id, previous_status, new_status
        ),
        success=1,
        storyline_visible=False,
    )


def _leave_overlapping_request_exists(conn, user_id, values, exclude_id=None):
    parameters = [user_id, values["end_date"], values["start_date"]]
    sql = """
        SELECT 1
        FROM leave_requests
        WHERE user_id = ?
          AND status <> 'CANCELLED'
          AND start_date <= ?
          AND end_date >= ?
    """
    if exclude_id is not None:
        sql += " AND leave_request_id <> ?"
        parameters.append(exclude_id)
    sql += " LIMIT 1"
    return conn.execute(sql, parameters).fetchone() is not None


def _leave_get_owned_request(conn, request_id, user_id):
    return conn.execute("""
        SELECT lr.*, u.full_name, u.role,
               reviewer.full_name AS reviewer_name
        FROM leave_requests lr
        JOIN users u ON u.user_id = lr.user_id
        LEFT JOIN users reviewer ON reviewer.user_id = lr.reviewed_by_user_id
        WHERE lr.leave_request_id = ? AND lr.user_id = ?
    """, (request_id, user_id)).fetchone()


@app.route("/worker-resources")
def worker_resources():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        _leave_authenticated_actor(conn, session["user_id"])
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()
    return render_template("worker_resources.html")


@app.route("/leave-requests")
def leave_requests():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        actor = _leave_authenticated_actor(conn, session["user_id"])
        entries = conn.execute("""
            SELECT lr.*, reviewer.full_name AS reviewer_name
            FROM leave_requests lr
            LEFT JOIN users reviewer ON reviewer.user_id = lr.reviewed_by_user_id
            WHERE lr.user_id = ?
            ORDER BY lr.start_date DESC, lr.leave_request_id DESC
        """, (actor["user_id"],)).fetchall()
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()
    return render_template(
        "leave_request_list.html",
        entries=_leave_prepare_entries(entries),
    )


def _render_leave_form(values, actor, error=None, entry=None):
    return render_template(
        "leave_request_form.html",
        values=values,
        error=error,
        entry=entry,
        actor=actor,
        leave_types=LEAVE_TYPES,
        token=secrets.token_hex(32) if entry is None else None,
    )


@app.route("/leave-requests/new", methods=["GET", "POST"])
def leave_request_new():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        actor = _leave_authenticated_actor(conn, session["user_id"])
        if request.method == "GET":
            return _render_leave_form(_leave_form_values(), actor)

        try:
            values = _leave_request_values(request.form)
        except ValueError as error:
            return _render_leave_form(
                dict(request.form), actor, str(error)
            ), 400

        token = request.form.get("submission_token", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            return _render_leave_form(
                dict(request.form), actor,
                "A valid submission token is required."
            ), 400

        now = _leave_utc_now()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("""
                INSERT INTO leave_requests (
                    user_id, leave_type, other_reason, start_date, end_date,
                    day_part, start_time, end_time, requested_days,
                    requested_hours, employee_comments, status,
                    submitted_at_utc, updated_at_utc, submission_token
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?)
            """, (
                actor["user_id"], values["leave_type"], values["other_reason"],
                values["start_date"], values["end_date"], values["day_part"],
                values["start_time"], values["end_time"], values["requested_days"],
                values["requested_hours"], values["employee_comments"],
                now, now, token,
            ))
            request_id = cursor.lastrowid
            _leave_log(
                conn,
                "leave_request_created",
                f"Leave request submitted: {values['leave_type']}, "
                f"{values['start_date']} to {values['end_date']}",
                actor["user_id"], request_id, values,
                new_status="PENDING",
            )
            conn.commit()
        except sqlite3.IntegrityError:
            if conn.in_transaction:
                conn.rollback()
            existing = conn.execute("""
                SELECT leave_request_id, user_id
                FROM leave_requests WHERE submission_token = ?
            """, (token,)).fetchone()
            if existing and existing["user_id"] == actor["user_id"]:
                flash("This leave request was already submitted.")
                return redirect(url_for("leave_requests"))
            return _render_leave_form(
                dict(request.form), actor,
                "This submission could not be recorded."
            ), 409
        if _leave_overlapping_request_exists(conn, actor["user_id"], values, request_id):
            flash("Warning: this request overlaps another non-cancelled request.")
        flash("Leave request submitted.")
        return redirect(url_for("leave_requests"))
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()


@app.route("/leave-requests/<int:leave_request_id>")
def leave_request_detail(leave_request_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        actor = _leave_authenticated_actor(conn, session["user_id"])
        entry = _leave_get_owned_request(
            conn, leave_request_id, actor["user_id"]
        )
        if entry is None:
            return "Leave request not found", 404
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()
    return render_template(
        "leave_request_detail.html",
        entry=_leave_prepare_entries([entry])[0],
    )


@app.route("/leave-requests/<int:leave_request_id>/edit", methods=["GET", "POST"])
def leave_request_edit(leave_request_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        actor = _leave_authenticated_actor(conn, session["user_id"])
        entry = _leave_get_owned_request(
            conn, leave_request_id, actor["user_id"]
        )
        if entry is None:
            return "Leave request not found", 404
        if entry["status"] != "PENDING":
            return "Only pending leave requests can be edited.", 409
        if request.method == "GET":
            return _render_leave_form(
                _leave_form_values(entry), actor, entry=entry
            )
        try:
            values = _leave_request_values(request.form)
        except ValueError as error:
            return _render_leave_form(
                dict(request.form), actor, str(error), entry
            ), 400
        now = _leave_utc_now()
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute("""
            UPDATE leave_requests
            SET leave_type = ?, other_reason = ?, start_date = ?, end_date = ?,
                day_part = ?, start_time = ?, end_time = ?, requested_days = ?,
                requested_hours = ?, employee_comments = ?, updated_at_utc = ?
            WHERE leave_request_id = ? AND user_id = ? AND status = 'PENDING'
        """, (
            values["leave_type"], values["other_reason"], values["start_date"],
            values["end_date"], values["day_part"], values["start_time"],
            values["end_time"], values["requested_days"], values["requested_hours"],
            values["employee_comments"], now, leave_request_id, actor["user_id"],
        ))
        if updated.rowcount != 1:
            conn.rollback()
            flash("This leave request changed and could not be edited.")
            return redirect(url_for("leave_requests"))
        _leave_log(
            conn,
            "leave_request_updated",
            f"Leave request updated: {values['leave_type']}, "
            f"{values['start_date']} to {values['end_date']}",
            actor["user_id"], leave_request_id, values,
            previous_status="PENDING", new_status="PENDING",
        )
        conn.commit()
        flash("Leave request updated.")
        return redirect(url_for("leave_requests"))
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()


@app.route("/leave-requests/<int:leave_request_id>/cancel", methods=["GET", "POST"])
def leave_request_cancel(leave_request_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        actor = _leave_authenticated_actor(conn, session["user_id"])
        entry = _leave_get_owned_request(
            conn, leave_request_id, actor["user_id"]
        )
        if entry is None:
            return "Leave request not found", 404
        if entry["status"] != "PENDING":
            return "Only pending leave requests can be cancelled.", 409
        if request.method == "GET":
            return render_template("leave_request_cancel_confirm.html", entry=entry)
        conn.execute("BEGIN IMMEDIATE")
        now = _leave_utc_now()
        updated = conn.execute("""
            UPDATE leave_requests
            SET status = 'CANCELLED', updated_at_utc = ?,
                cancelled_at_utc = ?, cancelled_by_user_id = ?
            WHERE leave_request_id = ? AND user_id = ? AND status = 'PENDING'
        """, (now, now, actor["user_id"], leave_request_id, actor["user_id"]))
        if updated.rowcount != 1:
            conn.rollback()
            flash("This leave request changed and could not be cancelled.")
            return redirect(url_for("leave_requests"))
        values = dict(entry)
        _leave_log(
            conn,
            "leave_request_cancelled",
            f"Leave request cancelled: {entry['leave_type']}, "
            f"{entry['start_date']} to {entry['end_date']}",
            actor["user_id"], leave_request_id, values,
            previous_status="PENDING", new_status="CANCELLED",
        )
        conn.commit()
        flash("Leave request cancelled.")
        return redirect(url_for("leave_requests"))
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()


@app.route("/manager-review/leave-requests")
def leave_request_review_list():
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        _leave_management_actor(conn, session["user_id"])
        status_filter = request.args.get("status", "ALL")
        if status_filter not in ("ALL", *LEAVE_STATUSES):
            status_filter = "ALL"
        if status_filter == "ALL":
            entries = conn.execute("""
                SELECT lr.*, submitter.full_name, submitter.role,
                       reviewer.full_name AS reviewer_name
                FROM leave_requests lr
                JOIN users submitter ON submitter.user_id = lr.user_id
                LEFT JOIN users reviewer ON reviewer.user_id = lr.reviewed_by_user_id
                ORDER BY CASE lr.status WHEN 'PENDING' THEN 0 ELSE 1 END,
                         lr.start_date DESC, lr.leave_request_id DESC
            """).fetchall()
        else:
            entries = conn.execute("""
                SELECT lr.*, submitter.full_name, submitter.role,
                       reviewer.full_name AS reviewer_name
                FROM leave_requests lr
                JOIN users submitter ON submitter.user_id = lr.user_id
                LEFT JOIN users reviewer ON reviewer.user_id = lr.reviewed_by_user_id
                WHERE lr.status = ?
                ORDER BY lr.start_date DESC, lr.leave_request_id DESC
            """, (status_filter,)).fetchall()
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()
    return render_template(
        "leave_request_review_list.html",
        entries=_leave_prepare_entries(entries),
        status_filter=status_filter,
        statuses=LEAVE_STATUSES,
    )


def _leave_review_entry(conn, request_id):
    return conn.execute("""
        SELECT lr.*, submitter.full_name, submitter.role,
               reviewer.full_name AS reviewer_name
        FROM leave_requests lr
        JOIN users submitter ON submitter.user_id = lr.user_id
        LEFT JOIN users reviewer ON reviewer.user_id = lr.reviewed_by_user_id
        WHERE lr.leave_request_id = ?
    """, (request_id,)).fetchone()


@app.route("/manager-review/leave-requests/<int:leave_request_id>")
def leave_request_review_detail(leave_request_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        _leave_management_actor(conn, session["user_id"])
        entry = _leave_review_entry(conn, leave_request_id)
        if entry is None:
            return "Leave request not found", 404
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()
    return render_template(
        "leave_request_review_detail.html",
        entry=_leave_prepare_entries([entry])[0],
    )


def _leave_review_decision(leave_request_id, decision):
    if "user_id" not in session:
        return redirect(url_for("login"))
    conn = get_db()
    try:
        actor = _leave_management_actor(conn, session["user_id"])
        entry = _leave_review_entry(conn, leave_request_id)
        if entry is None:
            return "Leave request not found", 404
        if entry["user_id"] == actor["user_id"]:
            return "A manager cannot decide their own leave request.", 403
        management_comments = request.form.get("management_comments", "").strip()
        if len(management_comments) > LEAVE_COMMENT_MAX_LENGTH:
            return "Management comments are too long.", 400
        if decision == "DECLINED" and not management_comments:
            return "A decline comment is required.", 400
        now = _leave_utc_now()
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute("""
            UPDATE leave_requests
            SET status = ?, updated_at_utc = ?, reviewed_by_user_id = ?,
                reviewed_at_utc = ?, management_comments = ?
            WHERE leave_request_id = ? AND status = 'PENDING'
        """, (
            decision, now, actor["user_id"], now,
            management_comments or None, leave_request_id,
        ))
        if updated.rowcount != 1:
            conn.rollback()
            flash("This leave request has already been decided.")
            return redirect(url_for("leave_request_review_detail", leave_request_id=leave_request_id))
        values = dict(entry)
        _leave_log(
            conn,
            f"leave_request_{decision.lower()}",
            f"Leave request {decision.lower()}: {entry['leave_type']}, "
            f"{entry['start_date']} to {entry['end_date']}",
            actor["user_id"], leave_request_id, values,
            previous_status="PENDING", new_status=decision,
        )
        conn.commit()
        flash(f"Leave request {decision.lower()}.")
        return redirect(url_for("leave_request_review_detail", leave_request_id=leave_request_id))
    except PermissionError:
        return "Access denied", 403
    finally:
        conn.close()


@app.route("/manager-review/leave-requests/<int:leave_request_id>/approve", methods=["POST"])
def leave_request_approve(leave_request_id):
    return _leave_review_decision(leave_request_id, "APPROVED")


@app.route("/manager-review/leave-requests/<int:leave_request_id>/decline", methods=["POST"])
def leave_request_decline(leave_request_id):
    return _leave_review_decision(leave_request_id, "DECLINED")


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
