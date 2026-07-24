"""Behaviour Module V1 schema migration."""

import sqlite3


DB_NAME = "nhpsg.db"


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS behaviour_occurrences (
    behaviour_occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(client_id),
    occurred_at_utc TEXT NOT NULL CHECK (
        typeof(occurred_at_utc) = 'text'
        AND length(occurred_at_utc) = 20
        AND occurred_at_utc GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
        AND julianday(occurred_at_utc) IS NOT NULL
        AND strftime('%Y-%m-%dT%H:%M:%SZ', julianday(occurred_at_utc)) = occurred_at_utc
    ),
    aggression_towards_others INTEGER NOT NULL DEFAULT 0
        CHECK (aggression_towards_others IN (0, 1)),
    injury_to_others INTEGER NOT NULL DEFAULT 0
        CHECK (injury_to_others IN (0, 1)),
    self_harm INTEGER NOT NULL DEFAULT 0
        CHECK (self_harm IN (0, 1)),
    injury_to_self INTEGER NOT NULL DEFAULT 0
        CHECK (injury_to_self IN (0, 1)),
    property_damage INTEGER NOT NULL DEFAULT 0
        CHECK (property_damage IN (0, 1)),
    notes TEXT,
    recorded_by_user_id INTEGER NOT NULL REFERENCES users(user_id),
    recorded_at_utc TEXT NOT NULL CHECK (
        typeof(recorded_at_utc) = 'text'
        AND length(recorded_at_utc) = 20
        AND recorded_at_utc GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
        AND julianday(recorded_at_utc) IS NOT NULL
        AND strftime('%Y-%m-%dT%H:%M:%SZ', julianday(recorded_at_utc)) = recorded_at_utc
    ),
    submission_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'Recorded'
        CHECK (status IN ('Recorded', 'Voided')),
    voided_by_user_id INTEGER REFERENCES users(user_id),
    voided_at_utc TEXT,
    void_reason TEXT,
    CHECK (
        aggression_towards_others + injury_to_others + self_harm +
        injury_to_self + property_damage >= 1
    ),
    CHECK (
        (status = 'Recorded' AND voided_by_user_id IS NULL
         AND voided_at_utc IS NULL AND void_reason IS NULL)
        OR
        (status = 'Voided' AND voided_by_user_id IS NOT NULL
         AND voided_at_utc IS NOT NULL
         AND length(trim(void_reason)) > 0)
    )
)
"""


INDEX_SQL = (
    """
    CREATE INDEX IF NOT EXISTS idx_behaviour_occurrences_client_occurred_at
    ON behaviour_occurrences (client_id, occurred_at_utc)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_behaviour_occurrences_status_occurred_at
    ON behaviour_occurrences (status, occurred_at_utc)
    """,
)


def migrate(conn):
    """Create the Behaviour V1 table and its required indexes."""
    conn.execute("PRAGMA foreign_keys = ON")
    with conn:
        conn.execute(CREATE_TABLE_SQL)
        for index_sql in INDEX_SQL:
            conn.execute(index_sql)


def main():
    conn = sqlite3.connect(DB_NAME)
    try:
        migrate(conn)
    finally:
        conn.close()

    print("Behaviour occurrences table migration completed.")


if __name__ == "__main__":
    main()
