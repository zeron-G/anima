import sqlite3
conn = sqlite3.connect('D:/data/code/github/anima/data/anima.db')
c = conn.cursor()
c.execute('SELECT type, content, importance FROM episodic_memories ORDER BY rowid DESC LIMIT 30')
rows = c.fetchall()
for r in rows:
    print(r[0], "|", str(r[2]), "|", str(r[1])[:130])
conn.close()
