import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
ALTER TABLE action_items
ADD COLUMN shift_id INTEGER
""")

conn.commit()
conn.close()

print("shift_id added to action_items.")