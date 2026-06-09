import sqlite3
c = sqlite3.connect("data/jobs.db")
for r in c.execute("SELECT id, title FROM jobs WHERE score > 0.2 ORDER BY score DESC LIMIT 3"):
    print(r[0][:8], "...", r[1][:60])
