import hashlib,importlib.util,json,os,re,shutil,subprocess,time,urllib.request
from pathlib import Path
stage=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
workspace=Path('/opt/siyuan/workspace')
state=json.loads((stage/'deployment-state.json').read_text())
assert state['phase']=='finalized' and state['sync_restored'] is True
conf=json.loads((workspace/'conf/conf.json').read_text())
old=json.loads((stage/'production-conf-before.json').read_text())
assert conf['sync']['enabled']==old['sync']['enabled']
assert conf['sync']['provider']==old['sync']['provider']
assert conf['api']['token']==old['api']['token']
assert conf['lang']==old['lang']
def api(path,payload=None):
 req=urllib.request.Request('http://127.0.0.1:6806'+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Authorization':'Token '+conf['api']['token'],'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=60) as r:
  value=json.load(r);assert r.status==200 and value['code']==0;return value['data']
assert api('/api/system/version')=='3.8.7-alpha.9'
assert api('/api/system/bootProgress')['progress']>=100
notebooks=api('/api/notebook/lsNotebooks',{})['notebooks']
assert len(notebooks)>=33
user=api('/api/setting/getCloudUser',{'cached':True})
assert user['userSiYuanProExpireTime']==-1 and user['userSiYuanOneTimePayStatus']==1
sync=api('/api/sync/getSyncInfo',{})
spec=importlib.util.spec_from_file_location('migration',stage/'deploy.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
assert driver.manifest(stage/'workspace-before')==json.loads((stage/'workspace-manifest-before.json').read_text()),'Protected old workspace changed'
before=json.loads(subprocess.check_output(['docker','inspect',state['old_id']]))[0]
assert not before['State']['Running'] and before['Name']=='/siyuan_custom_before_20261012_alpha9'
assert before['HostConfig']['RestartPolicy']['Name']=='no'
assert json.loads(subprocess.check_output(['docker','image','inspect',state['snapshot_id']]))[0]['Id']==state['snapshot_id']
prod=json.loads(subprocess.check_output(['docker','inspect',state['production_id']]))[0]
assert prod['State']['Running'] and not prod['State']['OOMKilled'] and prod['Image']==state['image_id']
assert prod['HostConfig']['RestartPolicy']['Name']=='always'
assert prod['Mounts'][0]['Source']==str(workspace)
assert driver.baseline_summary({state['production_id']})==state['unrelated_containers']
log=subprocess.run(['docker','logs','--since',state['production_ready_at_utc'],state['production_id']],capture_output=True)
os.umask(0o077)
(stage/'production-after-release.private.log').write_bytes(log.stdout+log.stderr)
fatal_count=len(re.findall(rb'panic:|fatal error:|runtime: out of memory',log.stdout+log.stderr))
assert fatal_count==0
summary={'passed':True,'time':time.time(),'version':'3.8.7-alpha.9','notebooks':len(notebooks),'sync_enabled_restored':conf['sync']['enabled'],'sync_provider_preserved':conf['sync']['provider'],'sync_last_attempt_timestamp':sync['synced'],'sync_roundtrip_tested':False,'api_token_preserved':True,'language_preserved':conf['lang'],'original_workspace_manifest_still_exact':True,'old_container_preserved_stopped':True,'private_snapshot_preserved':True,'new_container_running':True,'fatal_runtime_log_count':fatal_count,'unrelated_container_count':len(state['unrelated_containers']),'free_disk_bytes':shutil.disk_usage('/').free,'image_id':state['image_id'],'image_reference':state['image_reference'],'backup_path':str(stage/'workspace-before'),'downtime_started_at_utc':state['downtime_started_at_utc'],'production_ready_at_utc':state['production_ready_at_utc']}
(stage/'final-evidence.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary),flush=True)
