import sqlite3


DB_NAME = "nhpsg.db"


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS toileting_events (
    toileting_event_id INTEGER PRIMARY KEY AUTOINCREMENT,

    shift_id INTEGER NOT NULL,
    client_id INTEGER NOT NULL,
    recorded_by_user_id INTEGER NOT NULL,

    event_type TEXT NOT NULL,
    event_datetime TEXT NOT NULL,

    location TEXT NOT NULL,
    location_other TEXT,

    bm_size TEXT,
    bm_consistency TEXT,
    bm_colour TEXT,
    estimated_bristol_type INTEGER,

    bm_blood_observed INTEGER NOT NULL DEFAULT 0,
    bm_mucus_observed INTEGER NOT NULL DEFAULT 0,
    bm_unusual_colour INTEGER NOT NULL DEFAULT 0,

    urine_volume TEXT,
    urine_colour TEXT,

    urine_blood_observed INTEGER NOT NULL DEFAULT 0,
    urine_strong_odour INTEGER NOT NULL DEFAULT 0,
    urine_unusual_colour INTEGER NOT NULL DEFAULT 0,

    pain_or_distress INTEGER NOT NULL DEFAULT 0,
    other_concern INTEGER NOT NULL DEFAULT 0,
    concern_details TEXT,

    behaviour_before TEXT,
    behaviour_during TEXT,
    behaviour_after TEXT,
    behaviour_comments TEXT,

    general_comments TEXT,

    correction_of_event_id INTEGER,
    correction_reason TEXT,

    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    bm_unusual_details TEXT,
    urine_unusual_details TEXT
)
"""


ALTER_COLUMN_DEFINITIONS = {
    "shift_id": "INTEGER",
    "client_id": "INTEGER",
    "recorded_by_user_id": "INTEGER",
    "event_type": "TEXT",
    "event_datetime": "TEXT",
    "location": "TEXT",
    "location_other": "TEXT",
    "bm_size": "TEXT",
    "bm_consistency": "TEXT",
    "bm_colour": "TEXT",
    "estimated_bristol_type": "INTEGER",
    "bm_blood_observed": "INTEGER NOT NULL DEFAULT 0",
    "bm_mucus_observed": "INTEGER NOT NULL DEFAULT 0",
    "bm_unusual_colour": "INTEGER NOT NULL DEFAULT 0",
    "urine_volume": "TEXT",
    "urine_colour": "TEXT",
    "urine_blood_observed": "INTEGER NOT NULL DEFAULT 0",
    "urine_strong_odour": "INTEGER NOT NULL DEFAULT 0",
    "urine_unusual_colour": "INTEGER NOT NULL DEFAULT 0",
    "pain_or_distress": "INTEGER NOT NULL DEFAULT 0",
    "other_concern": "INTEGER NOT NULL DEFAULT 0",
    "concern_details": "TEXT",
    "behaviour_before": "TEXT",
    "behaviour_during": "TEXT",
    "behaviour_after": "TEXT",
    "behaviour_comments": "TEXT",
    "general_comments": "TEXT",
    "correction_of_event_id": "INTEGER",
    "correction_reason": "TEXT",
    "active": "INTEGER NOT NULL DEFAULT 1",
    "created_at": "TEXT",
    "bm_unusual_details": "TEXT",
    "urine_unusual_details": "TEXT"
}


INDEXES = {
    "idx_toileting_events_shift_id": "shift_id",
    "idx_toileting_events_client_datetime": (
        "client_id, event_datetime"
    ),
    "idx_toileting_events_event_type": "event_type",
    "idx_toileting_events_recorded_by": "recorded_by_user_id",
    "idx_toileting_events_active": "active",
    "idx_toileting_events_correction": "correction_of_event_id"
}


def get_column_names(conn):
    return {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(toileting_events)"
        ).fetchall()
    }


def migrate(conn):
    with conn:
        conn.execute(CREATE_TABLE_SQL)

        existing_columns = get_column_names(conn)

        if "toileting_event_id" not in existing_columns:
            raise RuntimeError(
                "The existing toileting_events table does not have "
                "toileting_event_id. This migration will not rebuild "
                "or overwrite an existing table automatically."
            )

        for column_name, column_definition in (
            ALTER_COLUMN_DEFINITIONS.items()
        ):
            if column_name in existing_columns:
                continue

            conn.execute(
                f"ALTER TABLE toileting_events "
                f"ADD COLUMN {column_name} {column_definition}"
            )

            existing_columns.add(column_name)

        for index_name, indexed_columns in INDEXES.items():
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name} "
                f"ON toileting_events ({indexed_columns})"
            )


def main():
    conn = sqlite3.connect(DB_NAME)

    try:
        migrate(conn)
    finally:
        conn.close()

    print("Toileting events table migration completed.")


if __name__ == "__main__":
    main()
