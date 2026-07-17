import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS care_task_categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

default_categories = [
    "Personal Care",
    "Household",
    "Health Monitoring",
    "Activities & Community",
    "Documentation",
    "Safety & Security",
    "Other"
]

for category in default_categories:
    cur.execute("""
        INSERT OR IGNORE INTO care_task_categories
        (category_name, active)
        VALUES (?, 1)
    """, (category,))

conn.commit()
conn.close()

print("Care task categories created.")