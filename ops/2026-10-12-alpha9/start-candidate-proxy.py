import json,os,subprocess,sys,urllib.request
from pathlib import Path
stage=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
info=json.loads(subprocess.check_output(['docker','inspect','siyuan_alpha9_candidate_20261012']))[0]
assert info['State']['Running']
nets=info['NetworkSettings']['Networks'];assert len(nets)==1
name,net=next(iter(nets.items()))
assert json.loads(subprocess.check_output(['docker','network','inspect',name]))[0]['Internal'] is True
address=net['IPAddress'];assert address.startswith('172.')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://'+address+':6806/api/system/version',timeout=5) as r:
 value=json.load(r);assert value['data']=='3.8.7-alpha.9'
source='''import socket,socketserver,threading
ADDRESS='''+repr(address)+'''
class Handler(socketserver.BaseRequestHandler):
 def handle(self):
  peer=socket.create_connection((ADDRESS,6806),timeout=10)
  peer.settimeout(120);self.request.settimeout(120)
  def pump(source,target):
   try:
    while True:
     data=source.recv(65536)
     if not data:break
     target.sendall(data)
   except OSError:pass
   finally:
    try:target.shutdown(socket.SHUT_WR)
    except OSError:pass
  t=threading.Thread(target=pump,args=(self.request,peer),daemon=True);t.start()
  try:pump(peer,self.request);t.join(2)
  finally:peer.close()
class Server(socketserver.ThreadingTCPServer):
 allow_reuse_address=True
 daemon_threads=True
with Server(('127.0.0.1',16806),Handler) as server:server.serve_forever()
'''
os.umask(0o077)
path=stage/'candidate-loopback-proxy.py';assert not path.exists()
path.write_text(source)
with (stage/'candidate-proxy.private.log').open('ab') as log:
 p=subprocess.Popen([sys.executable,str(path)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
receipt={'pid':p.pid,'candidate_id':info['Id'],'listen':'127.0.0.1:16806','upstream_ip':address}
(stage/'candidate-proxy.json').write_text(json.dumps(receipt))
print(json.dumps(receipt))
