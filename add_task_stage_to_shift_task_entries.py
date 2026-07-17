import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
ALTER TABLE shift_task_entries
ADD COLUMN task_stage TEXT
""")

conn.commit()
conn.close()

print("task_stage added to shift_task_entries.")