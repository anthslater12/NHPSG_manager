import sqlite3

db = r"c:\NHPSG_Manager\nhpsg.db"

conn = sqlite3.connect(db)

columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
print(columns)

if "must_change_password" not in columns:
    conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")

if "password_reset_at" not in columns:
    conn.execute("ALTER TABLE users ADD COLUMN password_reset_at TEXT")

if "password_reset_by" not in columns:
    conn.execute("ALTER TABLE users ADD COLUMN password_reset_by INTEGER")

if "last_password_changed_at" not in columns:
    conn.execute("ALTER TABLE users ADD COLUMN last_password_changed_at TEXT")

conn.commit()

print(conn.execute("PRAGMA table_info(users)").fetchall())

conn.close()