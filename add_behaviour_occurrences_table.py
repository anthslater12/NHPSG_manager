"""Behaviour occurrence schema, including legacy V1 and ABC records."""

import sqlite3

DB_NAME = "nhpsg.db"

LEGACY_COLUMNS = (
    "behaviour_occurrence_id", "client_id", "occurred_at_utc",
    "aggression_towards_others", "injury_to_others", "self_harm",
    "injury_to_self", "property_damage", "notes", "recorded_by_user_id",
    "recorded_at_utc", "submission_token", "status", "voided_by_user_id",
    "voided_at_utc", "void_reason",
)

COMPLETION_COLUMNS = (
    "completed_at_utc", "completed_by_user_id",
)

LIFECYCLE_STATUSES = (
    "In Progress", "Completed", "Recorded", "Voided",
)

ABC_BOOLEAN_COLUMNS = (
    "antecedent_transition_activities", "antecedent_denied_access",
    "antecedent_delayed_access", "antecedent_given_instruction",
    "antecedent_end_activity", "antecedent_preferred_activity_alone",
    "antecedent_transition_locations", "antecedent_other",
    "behaviour_crying_yelling_screaming", "behaviour_resisting_prompt",
    "behaviour_grabbing_object", "behaviour_throwing_objects",
    "behaviour_physical_aggression", "behaviour_verbal_aggression",
    "behaviour_other", "response_ignored_walked_away",
    "response_followed_instruction", "response_adult_attention",
    "response_removed_preferred_activity", "response_gave_preferred_activity",
    "response_blocked_behaviour", "response_redirected_activity",
    "response_other",
)

ABC_TEXT_COLUMNS = (
    "antecedent_other_details", "behaviour_other_details",
    "response_other_details", "calming_description", "additional_notes",
)

ALL_NEW_COLUMNS = ("shift_id", "record_format",) + ABC_BOOLEAN_COLUMNS + ABC_TEXT_COLUMNS + ("duration_until_calm_minutes",)


def _create_table(conn):
    timestamp_check = "CHECK (typeof({0}) = 'text' AND length({0}) = 20 AND {0} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z' AND julianday({0}) IS NOT NULL AND strftime('%Y-%m-%dT%H:%M:%SZ', julianday({0})) = {0})"
    columns = [
        "behaviour_occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT",
        "client_id INTEGER NOT NULL REFERENCES clients(client_id)",
        "occurred_at_utc TEXT NOT NULL " + timestamp_check.format("occurred_at_utc"),
        *(f"{name} INTEGER NOT NULL DEFAULT 0 CHECK ({name} IN (0, 1))" for name in (
            "aggression_towards_others", "injury_to_others", "self_harm",
            "injury_to_self", "property_damage",
        )),
        "notes TEXT", "recorded_by_user_id INTEGER NOT NULL REFERENCES users(user_id)",
        "recorded_at_utc TEXT NOT NULL " + timestamp_check.format("recorded_at_utc"), "submission_token TEXT NOT NULL UNIQUE",
        "status TEXT NOT NULL DEFAULT 'Recorded' CHECK (status IN ('In Progress', 'Completed', 'Recorded', 'Voided'))",
        "voided_by_user_id INTEGER REFERENCES users(user_id)",
        "voided_at_utc TEXT", "void_reason TEXT",
        "completed_at_utc TEXT CHECK (completed_at_utc IS NULL OR "
        + timestamp_check.format("completed_at_utc")[6:] + ")",
        "completed_by_user_id INTEGER REFERENCES users(user_id)",
        "shift_id INTEGER", "record_format TEXT NOT NULL DEFAULT 'V1' CHECK (record_format IN ('V1', 'ABC'))",
        *(f"{name} INTEGER NOT NULL DEFAULT 0 CHECK ({name} IN (0, 1))" for name in ABC_BOOLEAN_COLUMNS),
        "antecedent_other_details TEXT", "behaviour_other_details TEXT",
        "response_other_details TEXT", "duration_until_calm_minutes INTEGER",
        "calming_description TEXT", "additional_notes TEXT",
        "CHECK (record_format = 'ABC' OR aggression_towards_others + injury_to_others + self_harm + injury_to_self + property_damage >= 1)",
        "CHECK (record_format = 'V1' OR (duration_until_calm_minutes IS NOT NULL AND duration_until_calm_minutes >= 0))",
        "CHECK ("
        "((status IN ('In Progress', 'Recorded') "
        "AND completed_at_utc IS NULL "
        "AND completed_by_user_id IS NULL "
        "AND voided_by_user_id IS NULL "
        "AND voided_at_utc IS NULL "
        "AND void_reason IS NULL) "
        "OR (status = 'Completed' "
        "AND completed_at_utc IS NOT NULL "
        "AND completed_by_user_id IS NOT NULL "
        "AND voided_by_user_id IS NULL "
        "AND voided_at_utc IS NULL "
        "AND void_reason IS NULL) "
        "OR (status = 'Voided' "
        "AND completed_at_utc IS NULL "
        "AND completed_by_user_id IS NULL "
        "AND voided_by_user_id IS NOT NULL "
        "AND voided_at_utc IS NOT NULL "
        "AND length(trim(void_reason)) > 0))"
        ")",
    ]
    conn.execute("CREATE TABLE behaviour_occurrences (" + ",\n".join(columns) + ")")


def _table_sql(conn):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='behaviour_occurrences'"
    ).fetchone()
    return row[0] if row is not None else ""


def _needs_lifecycle_upgrade(conn, column_names):
    table_sql = _table_sql(conn)
    return (
        any(name not in column_names for name in COMPLETION_COLUMNS)
        or not all(
            f"'{status}'" in table_sql
            for status in LIFECYCLE_STATUSES
        )
        or "completed_at_utc IS NULL OR" not in table_sql
        or "status = 'Completed'" not in table_sql
        or "completed_at_utc IS NOT NULL" not in table_sql
        or "completed_by_user_id IS NOT NULL" not in table_sql
    )


def _rebuild_for_lifecycle(conn, existing_column_names):
    """Rebuild only the Behaviour table to replace its SQLite status CHECK."""
    legacy_table = "behaviour_occurrences_lifecycle_legacy"
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (legacy_table,)
    ).fetchone() is not None:
        raise RuntimeError(
            "An unfinished Behaviour lifecycle migration was found."
        )

    conn.execute(
        "ALTER TABLE behaviour_occurrences RENAME TO " + legacy_table
    )
    _create_table(conn)

    new_column_names = {
        row[1]
        for row in conn.execute("PRAGMA table_info(behaviour_occurrences)")
    }
    copy_columns = [
        name for name in existing_column_names if name in new_column_names
    ]
    column_list = ", ".join(copy_columns)
    conn.execute(
        "INSERT INTO behaviour_occurrences (" + column_list + ") "
        "SELECT " + column_list + " FROM " + legacy_table
    )
    conn.execute("DROP TABLE " + legacy_table)


def migrate(conn):
    """Idempotently create or upgrade the Behaviour occurrence table."""
    conn.execute("PRAGMA foreign_keys = ON")
    existing = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='behaviour_occurrences'").fetchone()
    with conn:
        if existing is None:
            _create_table(conn)
        else:
            names = {row[1] for row in conn.execute("PRAGMA table_info(behaviour_occurrences)")}
            if "record_format" not in names:
                conn.execute("ALTER TABLE behaviour_occurrences RENAME TO behaviour_occurrences_v1_legacy")
                _create_table(conn)
                copy_columns = ", ".join(LEGACY_COLUMNS)
                conn.execute(f"INSERT INTO behaviour_occurrences ({copy_columns}) SELECT {copy_columns} FROM behaviour_occurrences_v1_legacy")
                conn.execute("DROP TABLE behaviour_occurrences_v1_legacy")
            else:
                if _needs_lifecycle_upgrade(conn, names):
                    _rebuild_for_lifecycle(conn, names)
                    names = {
                        row[1]
                        for row in conn.execute(
                            "PRAGMA table_info(behaviour_occurrences)"
                        )
                    }

                missing = [name for name in ALL_NEW_COLUMNS if name not in names]
                for name in missing:
                    if name in ABC_BOOLEAN_COLUMNS:
                        conn.execute(f"ALTER TABLE behaviour_occurrences ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")
                    elif name == "record_format":
                        conn.execute("ALTER TABLE behaviour_occurrences ADD COLUMN record_format TEXT NOT NULL DEFAULT 'V1'")
                    elif name == "duration_until_calm_minutes":
                        conn.execute("ALTER TABLE behaviour_occurrences ADD COLUMN duration_until_calm_minutes INTEGER")
                    else:
                        conn.execute(f"ALTER TABLE behaviour_occurrences ADD COLUMN {name} TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_behaviour_occurrences_client_occurred_at ON behaviour_occurrences (client_id, occurred_at_utc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_behaviour_occurrences_status_occurred_at ON behaviour_occurrences (status, occurred_at_utc)")


def main():
    conn = sqlite3.connect(DB_NAME)
    try:
        migrate(conn)
    finally:
        conn.close()
    print("Behaviour occurrences table migration completed.")


if __name__ == "__main__":
    main()
