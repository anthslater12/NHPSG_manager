import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS shifts (
    shift_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    shift_date TEXT NOT NULL,
    shift_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Open',

    scheduled_start_time TEXT,
    scheduled_end_time TEXT,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    closed_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS shift_staff (
    shift_staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,

    actual_start_time TEXT NOT NULL,
    actual_end_time TEXT,

    sign_on_at TEXT DEFAULT CURRENT_TIMESTAMP,
    sign_off_at TEXT,

    active INTEGER NOT NULL DEFAULT 1
)
""")

conn.commit()
conn.close()

print("Shift tables created.")