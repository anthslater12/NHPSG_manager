import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

try:
    cur.execute("ALTER TABLE checklist_template_items ADD COLUMN timing_type TEXT DEFAULT 'Anytime'")
except sqlite3.OperationalError:
    pass

try:
    cur.execute("ALTER TABLE checklist_template_items ADD COLUMN due_time TEXT")
except sqlite3.OperationalError:
    pass

conn.commit()
conn.close()

print("Checklist timing fields updated.")