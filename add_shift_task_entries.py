import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS shift_task_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,

    shift_id INTEGER NOT NULL,
    shift_task_id INTEGER NOT NULL,
    completed_by_user_id INTEGER NOT NULL,

    input_value TEXT,

    completed_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()

print("shift_task_entries table created.")