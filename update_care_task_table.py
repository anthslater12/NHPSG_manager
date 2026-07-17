import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

# Add simplified "occurs" field
try:
    cur.execute("ALTER TABLE care_tasks ADD COLUMN occurs TEXT DEFAULT 'Anytime'")
except sqlite3.OperationalError:
    pass

# Add more flexible comment requirement fields
try:
    cur.execute("ALTER TABLE care_tasks ADD COLUMN comment_required_attempted INTEGER NOT NULL DEFAULT 1")
except sqlite3.OperationalError:
    pass

try:
    cur.execute("ALTER TABLE care_tasks ADD COLUMN comment_required_not_completed INTEGER NOT NULL DEFAULT 1")
except sqlite3.OperationalError:
    pass

conn.commit()
conn.close()

print("Care Task table updated.")