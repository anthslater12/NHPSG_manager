import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
ALTER TABLE shift_tasks
ADD COLUMN requires_input INTEGER DEFAULT 0
""")

cur.execute("""
ALTER TABLE shift_tasks
ADD COLUMN input_label TEXT
""")

cur.execute("""
ALTER TABLE shift_tasks
ADD COLUMN input_type TEXT DEFAULT 'text'
""")

conn.commit()
conn.close()

print("Shift task input fields added.")