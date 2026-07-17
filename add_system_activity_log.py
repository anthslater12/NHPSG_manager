import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS system_activity_log (
    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,

    activity_datetime TEXT DEFAULT CURRENT_TIMESTAMP,

    activity_class TEXT NOT NULL,
    activity_type TEXT NOT NULL,

    user_id INTEGER,
    client_id INTEGER,
    shift_id INTEGER,

    related_table TEXT,
    related_id INTEGER,

    summary TEXT NOT NULL,
    details TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()

print("System activity log table created.")