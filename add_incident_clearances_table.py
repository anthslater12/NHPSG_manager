"""Create the separate Program Manager Incident clearance state."""


TABLE_NAME = "incident_clearances"
ACTIVE_UNIQUE_INDEX_NAME = "ux_incident_clearances_active_incident"


CREATE_TABLE_SQL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        incident_clearance_id INTEGER PRIMARY KEY,
        incident_id INTEGER NOT NULL,
        cleared_by_user_id INTEGER NOT NULL,
        cleared_at_utc TEXT NOT NULL,
        clearance_note TEXT,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),

        FOREIGN KEY (incident_id)
            REFERENCES incident_reports(incident_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (cleared_by_user_id)
            REFERENCES users(user_id)
            ON DELETE RESTRICT
    )
"""

CREATE_ACTIVE_UNIQUE_INDEX_SQL = f"""
    CREATE UNIQUE INDEX IF NOT EXISTS {ACTIVE_UNIQUE_INDEX_NAME}
    ON {TABLE_NAME}(incident_id)
    WHERE active = 1
"""


def migrate(conn):
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_ACTIVE_UNIQUE_INDEX_SQL)