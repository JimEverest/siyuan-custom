import json,os,signal,socket,time
from pathlib import Path
s=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
state=json.loads((s/'deployment-state.json').read_text())
assert state['phase']=='finalized' and state['sync_restored']
assert json.loads((s/'final-evidence.json').read_text())['passed']
receipt=json.loads((s/'candidate-proxy.json').read_text())
assert receipt['candidate_id']==state['candidate_id'] and receipt['listen']=='127.0.0.1:16806'
pid=receipt['pid'];assert type(pid) is int and pid>1
p=Path('/proc')/str(pid)/'cmdline'
if p.exists():
 args=p.read_bytes().split(b'\0')
 assert str(s/'candidate-loopback-proxy.py').encode() in args
 os.kill(pid,signal.SIGTERM)
 for _ in range(20):
  try:
   with socket.create_connection(('127.0.0.1',16806),timeout=.2):pass
  except ConnectionRefusedError:break
  time.sleep(.1)
 else:raise RuntimeError('Owned loopback proxy still listening')
receipt['stopped_after_final_validation']=True
receipt['stopped_at_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
os.umask(0o077)
(s/'candidate-proxy.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps({'passed':True,'loopback_proxy_stopped':True}))
