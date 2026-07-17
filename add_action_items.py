import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS action_items (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT,

    title TEXT NOT NULL,
    description TEXT,

    status TEXT DEFAULT 'Open',
    priority TEXT DEFAULT 'Medium',

    source_table TEXT,
    source_id INTEGER,

    assigned_to_user_id INTEGER,
    created_by_user_id INTEGER,

    due_date TEXT,

    acknowledged_at TEXT,
    completed_at TEXT,
    closed_at TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()

print("action_items table created.")