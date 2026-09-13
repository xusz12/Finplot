"""QA-only proxy: commit between analytics and versioned details when armed."""
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from pathlib import Path
import sqlite3,json,time
root=Path(__file__).parent
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path.startswith('/api/dashboard?') and (root/'arm-race').exists():
   (root/'arm-race').unlink()
   with sqlite3.connect(root/'synthetic.db') as c:
    c.execute('INSERT OR REPLACE INTO transactions VALUES(?,?,?,?,?,?)',(3000,'2026-09-13 12:00:00','支出',12345,11,'QA inter-request race'))
   (root/'race-evidence.json').write_text(json.dumps({'path':self.path,'commit_epoch':time.time_ns()//1000000}))
  try:r=urlopen(Request('http://127.0.0.1:8770'+self.path))
  except HTTPError as e:r=e
  self.send_response(r.status)
  for k,v in r.headers.items():
   if k.lower() not in ['transfer-encoding','connection','content-length']:self.send_header(k,v)
  body=r.read();self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  if r.status==409:
   with (root/'race-409.log').open('a') as f:f.write(str(self.path)+'\n')
 def log_message(self,*args):pass
ThreadingHTTPServer(('127.0.0.1',8771),Handler).serve_forever()
