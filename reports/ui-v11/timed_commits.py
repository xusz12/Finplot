import sqlite3,time,json,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[3]
log=[]
for i in range(20):
    with sqlite3.connect(root/'qa-runtime/synthetic.db') as c:
        c.execute('INSERT OR REPLACE INTO transactions VALUES(?,?,?,?,?,?)',(1000+i,'2026-09-13 12:00:00','支出',1000+i,11,'synthetic timed QA'))
        c.commit()
        epoch=time.time_ns()//1000000
    version=json.load(urllib.request.urlopen('http://127.0.0.1:8770/api/version'))['version']
    log.append(dict(index=i+1,commit_epoch=epoch,version=version))
    Path(__file__).with_name('commit-times.json').write_text(json.dumps(log,indent=2))
    print(i+1,flush=True)
    time.sleep(3)
