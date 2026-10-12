"""Resume this exact stopped candidate after the inspected appearance migration failure."""
import fcntl,importlib.util,json,os,shutil,signal,subprocess,time,urllib.request,uuid
from pathlib import Path
STAGE=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
def module(name,file):
 spec=importlib.util.spec_from_file_location(name,STAGE/file)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
d=module('migration','deploy.py')
appearance=module('appearance_migration','migrate-appearance.py')
runner=module('runner','prepare-and-test.py')

def content(item):
 if item is None:return None
 return {k:item[k] for k in ('type','bytes','sha256','target') if k in item}

def main():
 os.umask(0o077)
 def interrupted(signum,frame):raise KeyboardInterrupt()
 signal.signal(signal.SIGTERM,interrupted)
 with (STAGE/'preparation.lock').open('a') as plock, (STAGE/'deployment.lock').open('a') as dlock:
  fcntl.flock(plock,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(dlock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  state=d.load_json(d.STATE)
  d.require(state['phase']=='rolled-back','Resume requires completed rollback')
  previous_attempt=state.get('attempt',1)
  d.require(previous_attempt==3,'This reviewed resume expects completed attempt 3')
  next_attempt=previous_attempt+1
  old_info=d.inspect_container(state['old_id'],size=True)
  d.validate_original(old_info)
  d.space_required(max(old_info.get('SizeRw') or 0,1024**3)+d.logical_bytes(d.ORIGINAL/'conf')+128*1024**2)
  candidate=d.inspect_container(state['candidate_id']);d.stopped(state['candidate_id'])
  d.require(candidate['Image']==state['image_id'] and candidate['Name']=='/'+d.CANDIDATE_NAME,'Candidate identity changed')
  d.require(candidate['HostConfig']['NetworkMode']==d.NETWORK,'Candidate network changed')
  d.require(json.loads(d.docker('network','inspect',d.NETWORK))[0]['Internal'] is True,'Candidate is not isolated')
  d.require(candidate['Mounts'][0]['Source']==str(d.CANDIDATE),'Candidate workspace changed')
  d.no_other_writers({state['old_id']},(d.ORIGINAL,d.CANDIDATE,d.BACKUP))
  archive=STAGE/('attempt-'+str(previous_attempt));d.require(not archive.exists(),'Attempt archive already exists');archive.mkdir(mode=0o700)
  # Preserve first-attempt receipts, but never replace a lock inode.
  names=['deployment-state.json','workspace-manifest-before.json','production-conf-before.json','index-baseline.json','preparation-result.json','smoke.json','wrapper-smoke.json','promotion-evidence.json','ocr-worker-smoke.json','config-identity-evidence.json','settings-evidence.json','appearance-aliases.json']
  for name in names:
   p=STAGE/name
   if p.exists():
    if name=='workspace-manifest-before.json':os.rename(p,archive/name)
    else:shutil.copy2(p,archive/name)
  for name in ['smoke.json','wrapper-smoke.json','promotion-evidence.json','ocr-worker-smoke.json','config-identity-evidence.json','settings-evidence.json']:
   p=STAGE/name
   if p.exists():p.unlink() # The exact receipt is retained in attempt-1.
  state['attempt']=next_attempt;state['operation_id']=str(uuid.uuid4())
  state['first_attempt_downtime_started_at_utc']=state.get('downtime_started_at_utc')
  state['resume_reason']='Preserve legacy icon packages and decode all original configuration sections'
  d.save_state(state,'preparing')
  status={'phase':'resuming','attempt':next_attempt,'operation_id':state['operation_id'],'started_at':time.time()}
  def status_save(phase):
   status['phase']=phase;status['updated_at']=time.time();d.write_json(STAGE/'preparation-result.json',status)
  def test(file,*args,timeout=1800):
   status_save(file)
   with (STAGE/(file+'.attempt'+str(next_attempt)+'.private.log')).open('wb') as log:
    r=subprocess.run(['python3',str(STAGE/file),*args],stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
   d.require(r.returncode==0,file+' failed')
  try:
   old_conf=d.load_json(d.ORIGINAL/'conf/conf.json')
   req=urllib.request.Request('http://127.0.0.1:6806/api/query/sql',data=json.dumps({'stmt':"SELECT count(*) AS n FROM blocks WHERE type='d'"}).encode(),headers={'Authorization':'Token '+old_conf['api']['token'],'Content-Type':'application/json'})
   with urllib.request.urlopen(req,timeout=30) as r:
    j=json.load(r);d.require(j['code']==0,'Original index query failed')
   d.write_json(STAGE/'index-baseline.json',{'document_index_count':j['data'][0]['n'],'recorded_at':time.time()})
   d.docker('update','--restart','no',state['old_id'])
   state['downtime_started_at_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());d.save_state(state)
   d.stop_clean(state['old_id']);status_save('refreshing-baseline')
   state['snapshot_reference']='siyuan-custom-private:before-20261012-alpha9-retry'+str(next_attempt)
   d.require(d.run(['docker','image','inspect',state['snapshot_reference']],check=False).returncode!=0,'Retry snapshot tag is occupied')
   d.docker('commit','--pause=false',state['old_id'],state['snapshot_reference'],timeout=1200)
   state['snapshot_id']=json.loads(d.docker('image','inspect',state['snapshot_reference']))[0]['Id']
   baseline=d.manifest(d.ORIGINAL)
   d.write_json(STAGE/'workspace-manifest-before.json',baseline)
   state['backup_manifest_sha256']=d.file_hash(STAGE/'workspace-manifest-before.json')
   state['backup_file_count']=sum(v['type']=='file' for v in baseline.values());d.save_state(state,'old-stopped')
   status_save('checking-persistent-data')
   existing=d.manifest(d.CANDIDATE)
   diffs=[]
   appearance_evidence=d.load_json(STAGE/'appearance-preservation.json')
   # Alpha.9 creates these package roots. Any unexpected child still fails below.
   allowed_extra={'data/icons','data/themes'}
   for package in appearance_evidence['packages']:
    if package['status']=='copied':
     base='data/'+package['kind']+'/'+package['name']
     allowed_extra.update(base if rel=='.' else base+'/'+rel for rel in package['target_manifest'])
   if state.get('appearance_aliases_sha256'):
    appearance.verify_aliases(STAGE,d.CANDIDATE,state,d.load_json(STAGE/'production-conf-before.json'))
    for package in d.load_json(STAGE/'appearance-aliases.json')['packages']:
     base='data/icons/'+package['target_name']
     allowed_extra.add(base)
     allowed_extra.update(base+'/'+rel for rel in package['target_manifest'])
   for name in sorted(set(baseline)|set(existing)):
    if name=='.' or name.split('/')[0] in ('conf','temp'):continue
    before,after=baseline.get(name),existing.get(name)
    if content(before)==content(after):continue
    if name=='repo/lock-sync' and before and after and before['type']==after['type']=='file':
     # DejaVu sync_lock.go writes only {deviceID,time} here on each sync.
     # Preserve the candidate record, then copy this latest stopped-source file.
     source=d.ORIGINAL/name;target=d.CANDIDATE/name
     d.require(not source.is_symlink() and not target.is_symlink(),'Sync lock must be a regular file')
     prior=d.load_json(target);latest=d.load_json(source)
     d.require(set(prior)==set(latest)=={'deviceID','time'},'Unexpected sync lock schema')
     device=d.load_json(d.ORIGINAL/'conf/conf.json')['system']['id']
     d.require(prior['deviceID']==latest['deviceID']==device,'Sync lock device identity changed')
     d.require(all(type(v['time']) is int and v['time']>0 for v in (prior,latest)),'Invalid sync lock timestamp')
     shutil.copy2(target,archive/'candidate-lock-sync')
     shutil.copy2(source,target);os.chown(target,before['uid'],before['gid'])
     d.require(d.file_hash(target)==before['sha256'],'Latest sync lock copy failed')
     state['sync_lock_refreshed_from_stopped_original']=True
     continue
    if before is None and name in allowed_extra:continue
    diffs.append({'path':name,'before':before,'after':after})
   d.write_json(STAGE/('attempt'+str(next_attempt)+'-persistent-differences.private.json'),diffs)
   d.require(not diffs,'Unexplained persistent data difference before resuming')
   # Preserve the entire failed configuration, then restore the complete original.
   d.rename_directory(d.CANDIDATE/'conf',archive/'candidate-conf')
   d.run(['cp','-a','--reflink=never','--',str(d.ORIGINAL/'conf'),str(d.CANDIDATE/'conf')],timeout=180)
   original_conf=d.load_json(d.ORIGINAL/'conf/conf.json')
   d.write_json(STAGE/'production-conf-before.json',original_conf)
   updated=appearance.migrate_conf(original_conf)
   updated['sync']['enabled']=False
   if isinstance(updated['sync'].get('lan'),dict):updated['sync']['lan']['enabled']=False
   p=d.CANDIDATE/'conf/conf.json';info=p.stat();d.write_json(p,updated);os.chown(p,info.st_uid,info.st_gid);os.chmod(p,info.st_mode & 0o777)
   alias=appearance.prepare_aliases(STAGE,d.CANDIDATE,state,original_conf)
   state['appearance_aliases_sha256']=d.file_hash(STAGE/'appearance-aliases.json')
   d.space_required();d.save_state(state,'candidate-created')
   d.docker('start',state['candidate_id']);status_save('waiting-for-workspace')
   d.version_check(16806,d.CANDIDATE,state['expected_version'],timeout=300)
   current=d.load_json(d.CANDIDATE/'conf/conf.json');runner.wait_for_workspace(current['api']['token'])
   d.save_state(state,'prepared')
   test('smoke.py',timeout=300);test('wrapper-smoke.py',timeout=240)
   test('verify-data.py',timeout=1800)
   test('ocr-worker-smoke.py','--output',str(STAGE/'ocr-worker-smoke.json'),timeout=180)
   status['passed']=True;status_save('candidate-verified');return 0
  except BaseException as error:
   status['passed']=False;status['error']=type(error).__name__+': '+str(error)
   try:status['recovery']=d.rollback()
   except BaseException as recovery:status['recovery_error']=type(recovery).__name__+': '+str(recovery)
   status_save('failed');return 1

if __name__=='__main__':raise SystemExit(main())
