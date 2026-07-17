import sqlite3
conn = sqlite3.connect("nhpsg.db")
for line in conn.iterdump():
    if "CREATE TABLE" in line:
        print(line)
conn.close()