import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
ALTER TABLE acknowledgements
ADD COLUMN acknowledgement_type TEXT DEFAULT 'Read'
""")

cur.execute("""
ALTER TABLE acknowledgements
ADD COLUMN active INTEGER DEFAULT 1
""")

conn.commit()
conn.close()

print("Acknowledgement fields added.")