import sqlite3
from werkzeug.security import generate_password_hash
import add_behaviour_occurrences_table
import add_schedule_tables

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS clients (
    client_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS shift_notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    shift_date TEXT NOT NULL,
    shift_type TEXT NOT NULL,
    note_text TEXT NOT NULL,
    follow_up_required INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS activity_log (
    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_datetime TEXT,
    activity_class TEXT,
    activity_type TEXT,
    user_id INTEGER,
    client_id INTEGER,
    shift_id INTEGER,
    related_table TEXT,
    related_id INTEGER,
    summary TEXT,
    details TEXT,
    success INTEGER NOT NULL DEFAULT 1,
    storyline_visible INTEGER NOT NULL DEFAULT 0,
    event_datetime TEXT NULL
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS sleep_events (
    sleep_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    shift_id INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('fell_asleep', 'woke_up')),
    event_datetime TEXT NOT NULL,
    recorded_by_user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
INSERT OR IGNORE INTO users (username, password_hash, full_name, role)
VALUES (?, ?, ?, ?)
""", (
    "admin",
    generate_password_hash("admin123"),
    "Administrator",
    "Admin"
))

cur.execute("""
INSERT OR IGNORE INTO clients (client_id, client_name, active)
VALUES (1, 'Neville', 1)
""")

add_behaviour_occurrences_table.migrate(conn)
add_schedule_tables.migrate(conn)

conn.commit()
conn.close()

print("Database created.")
print("Login: admin")
print("Password: admin123")
