"""Schema and seed data for structured Behaviour Setting Events."""

import sqlite3


DB_NAME = "nhpsg.db"

OPTION_TABLE = "behaviour_setting_event_options"
JUNCTION_TABLE = "behaviour_occurrence_setting_events"

OPTION_COLUMNS = {
    "setting_event_option_id",
    "code",
    "category",
    "label",
    "display_order",
    "active",
    "option_kind",
}

JUNCTION_COLUMNS = {
    "behaviour_occurrence_setting_event_id",
    "behaviour_occurrence_id",
    "setting_event_option_id",
    "other_text",
}

OPTION_COLUMN_METADATA = {
    "setting_event_option_id": ("INTEGER", 0, 1),
    "code": ("TEXT", 1, 0),
    "category": ("TEXT", 1, 0),
    "label": ("TEXT", 1, 0),
    "display_order": ("INTEGER", 1, 0),
    "active": ("INTEGER", 1, 0),
    "option_kind": ("TEXT", 1, 0),
}

JUNCTION_COLUMN_METADATA = {
    "behaviour_occurrence_setting_event_id": ("INTEGER", 0, 1),
    "behaviour_occurrence_id": ("INTEGER", 1, 0),
    "setting_event_option_id": ("INTEGER", 1, 0),
    "other_text": ("TEXT", 0, 0),
}

def _seed_rows(category, labels, other_code=None):
    return tuple(
        (
            code,
            category,
            label,
            index * 10,
            "OTHER" if code == other_code else "NORMAL",
        )
        for index, (code, label) in enumerate(labels, start=1)
    )


OPTION_SEEDS = (
    _seed_rows(
        "PHYSIOLOGICAL_BIOLOGICAL",
        (
            ("POOR_SLEEP", "Poor or insufficient sleep"),
            ("SLEEP_ROUTINE_CHANGE", "Change in sleep routine"),
            ("HUNGER", "Hunger"),
            ("THIRST", "Thirst"),
            ("MEAL_ROUTINE_CHANGE", "Missed or delayed meal / change in eating routine"),
            ("DIETARY_CHANGE", "Dietary change or possible food sensitivity"),
            ("PAIN_DISCOMFORT", "Pain or physical discomfort"),
            ("FEELING_UNWELL", "Feeling unwell or illness symptoms"),
            ("BOWEL_CONCERNS", "Bowel movement or constipation concerns"),
            ("OTHER_HEALTH_PHYSICAL", "Other health or physical factor"),
        ),
        other_code="OTHER_HEALTH_PHYSICAL",
    )
    + _seed_rows(
        "PHYSICAL_ENVIRONMENTAL",
        (
            ("BRIGHT_LIGHT", "Bright light or glare"),
            ("LOUD_UNEXPECTED_NOISE", "Loud or unexpected noise"),
            ("TOO_HOT", "Too hot or overheated"),
            ("WET_CLOTHING", "Wet clothing or footwear"),
            ("TOO_COLD", "Too cold"),
            ("SEATING_LOCATION_CHANGE", "Preferred seating or location unavailable or changed"),
            ("LIMITED_SENSORY_INPUT", "Limited access to preferred sensory input or deep pressure"),
            ("BUSY_CLUTTERED_ENVIRONMENT", "Busy, crowded, cluttered, or highly active environment"),
            ("UNPREDICTABLE_ENVIRONMENT", "Unpredictable environment or routine"),
            ("HIGH_LANGUAGE_DEMANDS", "High verbal/language demands"),
            ("LIMITED_MOVEMENT", "Limited access to movement or movement breaks"),
            ("LONG_NONPREFERRED_ACTIVITY", "Long period of quiet or non-preferred activity"),
            ("UNFAMILIAR_DIFFICULT_ACTIVITY", "Unfamiliar or difficult activity"),
            ("OTHER_ENVIRONMENTAL", "Other environmental factor"),
        ),
        other_code="OTHER_ENVIRONMENTAL",
    )
    + _seed_rows(
        "ROUTINE_TRANSITION",
        (
            ("UNEXPECTED_ROUTINE_CHANGE", "Unexpected schedule or routine change"),
            ("PREFERRED_ACTIVITY_ENDING", "Preferred activity ending"),
            ("PREFERRED_TO_NONPREFERRED", "Transition from preferred to non-preferred activity"),
            ("INSUFFICIENT_TRANSITION_TIME", "Insufficient transition time"),
            ("WAITING_DELAY", "Waiting or delay"),
            ("ROUTINE_INTERRUPTED", "Routine interrupted"),
            ("OTHER_ROUTINE_TRANSITION", "Other transition/routine factor"),
        ),
        other_code="OTHER_ROUTINE_TRANSITION",
    )
    + _seed_rows(
        "SOCIAL_INTERPERSONAL",
        (
            ("REQUEST_UNAVAILABLE", "Request or preferred activity unavailable/refused"),
            ("CORRECTION_REPRIMAND", "Correction, discipline, or reprimand"),
            ("STAFFING_CHANGE", "Staffing change"),
            ("FAMILIAR_STAFF_UNAVAILABLE", "Familiar staff unavailable or on break"),
            ("DIFFICULT_INTERACTION", "Difficult interaction with another person"),
            ("LIMITED_POSITIVE_INTERACTION", "Limited positive social interaction"),
            ("PERSONAL_CARE_HYGIENE", "Personal care or hygiene routine"),
            ("OTHER_SOCIAL_INTERPERSONAL", "Other social/interpersonal factor"),
        ),
        other_code="OTHER_SOCIAL_INTERPERSONAL",
    )
    + (
        ("NONE_OBSERVED", "SPECIAL", "None observed", 10, "NONE"),
        ("INFORMATION_UNKNOWN", "SPECIAL", "Information not known or unavailable", 20, "UNKNOWN"),
    )
)


INDEXES = (
    ("idx_behaviour_setting_event_options_category_order", OPTION_TABLE + "(category, display_order, setting_event_option_id)"),
    ("idx_behaviour_occurrence_setting_events_occurrence", JUNCTION_TABLE + "(behaviour_occurrence_id)"),
    ("idx_behaviour_occurrence_setting_events_option", JUNCTION_TABLE + "(setting_event_option_id)"),
)


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _foreign_keys(conn, table_name):
    return {
        (row[3], row[2], row[4], row[6].upper())
        for row in conn.execute(f'PRAGMA foreign_key_list("{table_name}")')
    }


def _validate_column_metadata(conn, table_name, expected):
    rows = {
        row[1]: row
        for row in conn.execute(f'PRAGMA table_info("{table_name}")')
    }
    if set(rows) != set(expected):
        raise RuntimeError(
            f"Existing {table_name} table has incompatible columns."
        )

    for name, (declared_type, not_null, primary_key) in expected.items():
        row = rows[name]
        if (
            row[2].upper() != declared_type
            or row[3] != not_null
            or row[5] != primary_key
        ):
            raise RuntimeError(
                f"Existing {table_name}.{name} column has incompatible metadata."
            )


def _validate_autoincrement_primary_key(conn, table_name, column_name):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    normalized_sql = " ".join(row[0].upper().split()) if row else ""
    expected_definition = (
        f"{column_name.upper()} INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    if expected_definition not in normalized_sql:
        raise RuntimeError(
            f"Existing {table_name} table has an incompatible identity column."
        )


def _validate_indexes(conn):
    for index_name, indexed_columns in INDEXES:
        row = conn.execute(
            "SELECT tbl_name FROM sqlite_master "
            "WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if row is None:
            continue

        table_name, columns = indexed_columns.split("(", 1)
        expected_columns = tuple(
            column.strip().rstrip(")")
            for column in columns.split(",")
        )
        actual_columns = tuple(
            item[2]
            for item in conn.execute(
                "SELECT * FROM pragma_index_info(?) ORDER BY seqno",
                (index_name,),
            )
        )
        index_metadata = conn.execute(
            "SELECT * FROM pragma_index_list(?) WHERE name = ?",
            (row[0], index_name),
        ).fetchone()
        if (
            row[0] != table_name
            or index_metadata is None
            or index_metadata[2] != 0
            or actual_columns != expected_columns
        ):
            raise RuntimeError(
                f"Existing {index_name} index has an incompatible schema."
            )


def _validate_option_table(conn):
    if not _table_exists(conn, OPTION_TABLE):
        return

    _validate_column_metadata(
        conn, OPTION_TABLE, OPTION_COLUMN_METADATA
    )
    _validate_autoincrement_primary_key(
        conn, OPTION_TABLE, "setting_event_option_id"
    )

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (OPTION_TABLE,),
    ).fetchone()[0].upper()
    required_fragments = (
        "CODE TEXT NOT NULL UNIQUE",
        "CATEGORY TEXT NOT NULL",
        "LABEL TEXT NOT NULL",
        "DISPLAY_ORDER INTEGER NOT NULL",
        "ACTIVE INTEGER NOT NULL DEFAULT 1",
        "OPTION_KIND TEXT NOT NULL",
        "LENGTH(TRIM(CODE)) > 0",
        "LENGTH(TRIM(CATEGORY)) > 0",
        "LENGTH(TRIM(LABEL)) > 0",
        "DISPLAY_ORDER >= 0",
        "ACTIVE IN (0, 1)",
        "OPTION_KIND IN ('NORMAL', 'OTHER', 'NONE', 'UNKNOWN')",
    )
    if any(fragment not in sql for fragment in required_fragments):
        raise RuntimeError(
            f"Existing {OPTION_TABLE} table has incompatible constraints."
        )


def _validate_junction_table(conn):
    if not _table_exists(conn, JUNCTION_TABLE):
        return

    _validate_column_metadata(
        conn, JUNCTION_TABLE, JUNCTION_COLUMN_METADATA
    )
    _validate_autoincrement_primary_key(
        conn,
        JUNCTION_TABLE,
        "behaviour_occurrence_setting_event_id",
    )

    required_foreign_keys = {
        (
            "behaviour_occurrence_id",
            "behaviour_occurrences",
            "behaviour_occurrence_id",
            "CASCADE",
        ),
        (
            "setting_event_option_id",
            OPTION_TABLE,
            "setting_event_option_id",
            "RESTRICT",
        ),
    }
    if not required_foreign_keys.issubset(_foreign_keys(conn, JUNCTION_TABLE)):
        raise RuntimeError(
            f"Existing {JUNCTION_TABLE} table has incompatible foreign keys."
        )

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (JUNCTION_TABLE,),
    ).fetchone()[0].upper()
    required_fragments = (
        "UNIQUE (BEHAVIOUR_OCCURRENCE_ID, SETTING_EVENT_OPTION_ID)",
        "OTHER_TEXT IS NULL",
        "LENGTH(TRIM(OTHER_TEXT)) > 0",
        "LENGTH(OTHER_TEXT) <= 1000",
    )
    if any(fragment not in sql for fragment in required_fragments):
        raise RuntimeError(
            f"Existing {JUNCTION_TABLE} table has incompatible constraints."
        )


def _create_option_table(conn):
    conn.execute(f"""
        CREATE TABLE {OPTION_TABLE} (
            setting_event_option_id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE
                CHECK (length(trim(code)) > 0),
            category TEXT NOT NULL
                CHECK (length(trim(category)) > 0),
            label TEXT NOT NULL
                CHECK (length(trim(label)) > 0),
            display_order INTEGER NOT NULL
                CHECK (display_order >= 0),
            active INTEGER NOT NULL DEFAULT 1
                CHECK (active IN (0, 1)),
            option_kind TEXT NOT NULL
                CHECK (option_kind IN ('NORMAL', 'OTHER', 'NONE', 'UNKNOWN'))
        )
    """)


def _create_junction_table(conn):
    conn.execute(f"""
        CREATE TABLE {JUNCTION_TABLE} (
            behaviour_occurrence_setting_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            behaviour_occurrence_id INTEGER NOT NULL,
            setting_event_option_id INTEGER NOT NULL,
            other_text TEXT NULL
                CHECK (
                    other_text IS NULL
                    OR (
                        other_text = trim(other_text)
                        AND length(trim(other_text)) > 0
                        AND length(other_text) <= 1000
                    )
                ),
            FOREIGN KEY (behaviour_occurrence_id)
                REFERENCES behaviour_occurrences(behaviour_occurrence_id)
                ON DELETE CASCADE,
            FOREIGN KEY (setting_event_option_id)
                REFERENCES behaviour_setting_event_options(setting_event_option_id)
                ON DELETE RESTRICT,
            UNIQUE (behaviour_occurrence_id, setting_event_option_id)
        )
    """)


def _load_seed_options(conn):
    codes = tuple(seed[0] for seed in OPTION_SEEDS)
    placeholders = ", ".join("?" for _ in codes)
    return {
        row[0]: row
        for row in conn.execute(
            f"SELECT code, category, label, display_order, active, option_kind "
            f"FROM {OPTION_TABLE} WHERE code IN ({placeholders})",
            codes,
        )
    }


def _validate_seed_identity(conn, existing_rows=None):
    existing_rows = (
        _load_seed_options(conn)
        if existing_rows is None else existing_rows
    )
    for code, _category, _label, _display_order, _option_kind in OPTION_SEEDS:
        row = existing_rows.get(code)
        if row is None:
            raise RuntimeError(
                f"Setting Event seed option {code} was not created."
            )
        if row[1] != _category or row[5] != _option_kind:
            raise RuntimeError(
                f"Setting Event seed option {code} has incompatible identity."
            )


def _seed_options(conn):
    existing_rows = _load_seed_options(conn)
    for code, category, label, display_order, option_kind in OPTION_SEEDS:
        row = existing_rows.get(code)
        if row is not None:
            if row[1] != category or row[5] != option_kind:
                raise RuntimeError(
                    f"Setting Event seed option {code} has incompatible identity."
                )
            continue
        conn.execute(
            f"""
            INSERT INTO {OPTION_TABLE}
                (code, category, label, display_order, option_kind)
            VALUES (?, ?, ?, ?, ?)
            """,
            (code, category, label, display_order, option_kind),
        )


def migrate(conn):
    """Create Setting Events schema and seed missing options safely."""
    conn.execute("PRAGMA foreign_keys = ON")
    if not _table_exists(conn, "behaviour_occurrences"):
        raise RuntimeError(
            "behaviour_occurrences must exist before Setting Events migration."
        )

    # Validate every existing object before creating anything. The savepoint
    # also preserves a transaction owned by the caller.
    _validate_option_table(conn)
    _validate_junction_table(conn)
    _validate_indexes(conn)
    savepoint = "behaviour_setting_events_migration"
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    else:
        conn.execute(f"SAVEPOINT {savepoint}")

    try:
        if not _table_exists(conn, OPTION_TABLE):
            _create_option_table(conn)
        if not _table_exists(conn, JUNCTION_TABLE):
            _create_junction_table(conn)

        _seed_options(conn)
        _validate_seed_identity(conn)
        for index_name, indexed_columns in INDEXES:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON {indexed_columns}"
            )

        if owns_transaction:
            conn.commit()
        else:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        if owns_transaction:
            conn.rollback()
        else:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def main():
    conn = sqlite3.connect(DB_NAME)
    try:
        migrate(conn)
    finally:
        conn.close()
    print("Behaviour Setting Events migration completed.")


if __name__ == "__main__":
    main()
