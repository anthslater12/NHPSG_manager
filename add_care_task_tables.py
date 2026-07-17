import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS care_tasks (
    care_task_id INTEGER PRIMARY KEY AUTOINCREMENT,

    task_name TEXT NOT NULL,
    category TEXT,
    instructions TEXT,

    schedule_type TEXT NOT NULL DEFAULT 'Daily',
    occurs_morning INTEGER NOT NULL DEFAULT 0,
    occurs_afternoon INTEGER NOT NULL DEFAULT 0,
    occurs_evening INTEGER NOT NULL DEFAULT 0,
    occurs_overnight INTEGER NOT NULL DEFAULT 0,
    occurs_anytime INTEGER NOT NULL DEFAULT 0,

    timing_type TEXT NOT NULL DEFAULT 'Anytime',
    due_time TEXT,

    days_of_week TEXT,

    required INTEGER NOT NULL DEFAULT 1,
    comment_required_if_unsuccessful INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS shift_care_task_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,

    shift_id INTEGER NOT NULL,
    care_task_id INTEGER NOT NULL,

    outcome TEXT NOT NULL,
    comment TEXT,

    completed_by_user_id INTEGER NOT NULL,
    completed_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()
conn.close()

print("Care Task tables created.")