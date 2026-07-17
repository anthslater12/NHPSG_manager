import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS acknowledgements (
    acknowledgement_id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_table TEXT NOT NULL,
    source_id INTEGER NOT NULL,

    user_id INTEGER NOT NULL,

    acknowledged_at TEXT DEFAULT CURRENT_TIMESTAMP,

    comment TEXT,

    UNIQUE(source_table, source_id, user_id)
)
""")

conn.commit()
conn.close()

print("acknowledgements table created.")