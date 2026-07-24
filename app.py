#####################################################################
# IMPORTS / APPLICATION CONFIGURATION
#####################################################################

from flask import Flask, render_template, request, redirect, session, url_for
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, time as datetime_time, timedelta, timezone
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

#####################################################################
# DATABASE & CORE HELPER FUNCTIONS
#####################################################################

def get_db():
    print("Using database:", os.path.abspath(DB_NAME))
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


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
