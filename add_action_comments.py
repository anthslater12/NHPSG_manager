import sqlite3

conn = sqlite3.connect("nhpsg.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS action_comments (

    comment_id INTEGER PRIMARY KEY AUTOINCREMENT,

    action_id INTEGER NOT NULL,

    user_id INTEGER NOT NULL,

    comment TEXT NOT NULL,

    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(action_id) REFERENCES action_items(action_id),

    FOREIGN KEY(user_id) REFERENCES users(user_id)

)
""")

conn.commit()
conn.close()

print("action_comments table created.")