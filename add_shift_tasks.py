import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS shift_tasks (
    shift_task_id INTEGER PRIMARY KEY AUTOINCREMENT,

    task_name TEXT NOT NULL,
    instructions TEXT,

    task_stage TEXT NOT NULL,   -- BEGINNING or END

    required INTEGER DEFAULT 1,
    active INTEGER DEFAULT 1,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()

print("shift_tasks table created.")