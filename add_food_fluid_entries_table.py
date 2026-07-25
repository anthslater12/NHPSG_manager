"""Food & Fluid Version 1 schema migration.

This module deliberately does not select or open a database. A caller must
provide the SQLite connection that should receive the schema.
"""


CREATE_SHIFT_CLIENT_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_shifts_shift_client
ON shifts (shift_id, client_id)
"""


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS food_fluid_entries (
    food_fluid_entry_id INTEGER PRIMARY KEY AUTOINCREMENT,

    shift_id INTEGER NOT NULL,
    client_id INTEGER NOT NULL,
    recorded_by_user_id INTEGER NOT NULL,

    event_at_utc TEXT NOT NULL CHECK (
        typeof(event_at_utc) = 'text'
        AND length(event_at_utc) = 20
        AND event_at_utc GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
        AND julianday(event_at_utc) IS NOT NULL
        AND strftime(
            '%Y-%m-%dT%H:%M:%SZ',
            julianday(event_at_utc)
        ) = event_at_utc
    ),

    interaction_type TEXT NOT NULL CHECK (
        interaction_type IN ('Offered', 'Requested')
    ),

    item_description TEXT NOT NULL CHECK (
        length(trim(
            item_description,
            ' ' || char(9) || char(10) || char(11) || char(12) || char(13)
        )) > 0
    ),

    outcome TEXT NOT NULL CHECK (
        outcome IN (
            'All consumed',
            'Partially consumed',
            'Refused',
            'Item not available'
        )
    ),

    physically_thrown INTEGER NOT NULL DEFAULT 0 CHECK (
        physically_thrown IN (0, 1)
    ),

    additional_details TEXT,

    submitted_at_utc TEXT NOT NULL CHECK (
        typeof(submitted_at_utc) = 'text'
        AND length(submitted_at_utc) = 20
        AND submitted_at_utc GLOB
            '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
        AND julianday(submitted_at_utc) IS NOT NULL
        AND strftime(
            '%Y-%m-%dT%H:%M:%SZ',
            julianday(submitted_at_utc)
        ) = submitted_at_utc
    ),

    submission_token TEXT NOT NULL UNIQUE CHECK (
        length(trim(
            submission_token,
            ' ' || char(9) || char(10) || char(11) || char(12) || char(13)
        )) > 0
    ),

    status TEXT NOT NULL DEFAULT 'Recorded' CHECK (
        status IN ('Recorded', 'Voided')
    ),

    voided_by_user_id INTEGER,
    voided_at_utc TEXT,
    void_reason TEXT,

    FOREIGN KEY (shift_id, client_id)
        REFERENCES shifts (shift_id, client_id),
    FOREIGN KEY (client_id)
        REFERENCES clients (client_id),
    FOREIGN KEY (recorded_by_user_id)
        REFERENCES users (user_id),
    FOREIGN KEY (voided_by_user_id)
        REFERENCES users (user_id),

    CHECK (
        outcome <> 'Item not available'
        OR interaction_type = 'Requested'
    ),

    CHECK (
        physically_thrown = 0
        OR outcome IN ('Partially consumed', 'Refused')
    ),

    CHECK (
        voided_at_utc IS NULL
        OR (
            typeof(voided_at_utc) = 'text'
            AND length(voided_at_utc) = 20
            AND voided_at_utc GLOB
                '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
            AND julianday(voided_at_utc) IS NOT NULL
            AND strftime(
                '%Y-%m-%dT%H:%M:%SZ',
                julianday(voided_at_utc)
            ) = voided_at_utc
        )
    ),

    CHECK (
        (
            status = 'Recorded'
            AND voided_by_user_id IS NULL
            AND voided_at_utc IS NULL
            AND void_reason IS NULL
        )
        OR
        (
            status = 'Voided'
            AND voided_by_user_id IS NOT NULL
            AND voided_at_utc IS NOT NULL
            AND length(trim(
                void_reason,
                ' ' || char(9) || char(10) || char(11) || char(12)
                    || char(13)
            )) > 0
        )
    )
)
"""


INDEX_SQL = (
    """
    CREATE INDEX IF NOT EXISTS idx_food_fluid_entries_shift_event
    ON food_fluid_entries (shift_id, event_at_utc)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_food_fluid_entries_client_event
    ON food_fluid_entries (client_id, event_at_utc)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_food_fluid_entries_status_event
    ON food_fluid_entries (status, event_at_utc)
    """,
)


def migrate(conn):
    """Create the Food & Fluid V1 table and indexes on ``conn``."""
    conn.execute("PRAGMA foreign_keys = ON")

    with conn:
        conn.execute(CREATE_SHIFT_CLIENT_INDEX_SQL)
        conn.execute(CREATE_TABLE_SQL)

        for index_sql in INDEX_SQL:
            conn.execute(index_sql)
