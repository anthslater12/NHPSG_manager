import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS incident_reports (
    incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    reported_by_user_id INTEGER NOT NULL,

    incident_date TEXT NOT NULL,
    incident_time TEXT NOT NULL,
    location TEXT NOT NULL,

    incident_type TEXT NOT NULL,
    severity TEXT DEFAULT 'Normal',

    description TEXT NOT NULL,
    actions_taken TEXT,
    follow_up_required INTEGER NOT NULL DEFAULT 0,

    witnesses TEXT,
    injuries INTEGER NOT NULL DEFAULT 0,
    injury_details TEXT,

    police_notified INTEGER NOT NULL DEFAULT 0,
    medical_treatment INTEGER NOT NULL DEFAULT 0,

    status TEXT NOT NULL DEFAULT 'Awaiting Review',
    reviewed_by_user_id INTEGER,
    reviewed_at TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()

print("Incident reports table created.")