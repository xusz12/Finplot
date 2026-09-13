"""Extend only the disposable synthetic fixture used for frontend QA."""
import sqlite3, sys
from pathlib import Path
path=Path(sys.argv[1]).resolve()
if path.name != 'synthetic.db': raise SystemExit('Expected disposable synthetic.db')
with sqlite3.connect(path) as c:
    for i in range(10,17):
        c.execute('INSERT OR IGNORE INTO categories VALUES(?,?,?,?,?,?,?,?)',(i,f'exp_qa_{i}',1,f'合成分类{i}', '支出','日常',1,i))
    for i in range(100,170):
        c.execute('INSERT OR IGNORE INTO transactions VALUES(?,?,?,?,?,?)',(i,f'2026-09-{i%10+1:02d} 12:00:00','支出',10000+(i-100)*123,10+i%7,'synthetic UI QA'))
    for i,when,direction,amount,category in [(200,'2026-09-01 09:00:00','收入',300000,2),(201,'2026-08-01 09:00:00','收入',400000,2),(202,'2026-08-02 09:00:00','支出',800000,1),(203,'2026-08-03 09:00:00','收入',100000,3),(204,'2026-08-04 09:00:00','支出',30000,4)]:
        c.execute('INSERT OR IGNORE INTO transactions VALUES(?,?,?,?,?,?)',(i,when,direction,amount,category,'synthetic UI QA'))
    c.execute('INSERT OR IGNORE INTO transaction_tags(transaction_id,tag_id) VALUES(100,1)')
    c.execute('INSERT OR IGNORE INTO transaction_tags(transaction_id,tag_id) VALUES(100,2)')
    print('synthetic rows:',c.execute('SELECT count(*) FROM transactions').fetchone()[0])
