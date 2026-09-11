import sqlite3, sys
from pathlib import Path

SCHEMA = '''PRAGMA foreign_keys=ON;CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE category_groups(id INTEGER PRIMARY KEY,code TEXT UNIQUE,name TEXT,direction TEXT,active INTEGER,sort_order INTEGER);CREATE TABLE categories(id INTEGER PRIMARY KEY,code TEXT UNIQUE,group_id INTEGER,name TEXT,direction TEXT,nature TEXT,active INTEGER,sort_order INTEGER);CREATE TABLE transactions(id INTEGER PRIMARY KEY,occurred_at TEXT,direction TEXT,amount_cents INTEGER,category_id INTEGER,note TEXT);CREATE TABLE tags(id INTEGER PRIMARY KEY,code TEXT UNIQUE,name TEXT,active INTEGER);CREATE TABLE transaction_tags(transaction_id INTEGER,tag_id INTEGER,PRIMARY KEY(transaction_id,tag_id));'''
def make(path):
 p=Path(path); p.unlink(missing_ok=True); c=sqlite3.connect(p); c.executescript(SCHEMA); c.execute("INSERT INTO metadata VALUES('schema_version','3')")
 c.executemany('INSERT INTO category_groups VALUES(?,?,?,?,?,?)',[(1,'exp_life','生活','支出',1,1),(2,'inc_work','工作','收入',1,1)])
 c.executemany('INSERT INTO categories VALUES(?,?,?,?,?,?,?,?)',[(1,'exp_food',1,'餐饮','支出','日常',1,1),(2,'inc_salary',2,'工资','收入','日常',1,1),(3,'inc_investment_gain',2,'投资收益','收入','投资',1,2),(4,'exp_investment_loss',1,'投资损失','支出','投资',1,2)])
 c.executemany('INSERT INTO tags VALUES(?,?,?,?)',[(1,'ai','AI',1),(2,'subscription','订阅',1)])
 c.executemany('INSERT INTO transactions VALUES(?,?,?,?,?,?)',[(1,'2024-02-29 00:00:00','支出',1,1,''),(2,'2026-01-01 00:00:00','收入',10000000,2,''),(3,'2026-08-31 23:59:59','支出',1299,1,''),(4,'2026-09-01 00:00:00','收入',5000,3,''),(5,'2026-09-02 00:00:00','支出',2000,4,'')])
 c.executemany('INSERT INTO transaction_tags VALUES(?,?)',[(3,1),(3,2),(4,1)]); c.commit(); c.close()
if __name__ == '__main__': make(sys.argv[1])
