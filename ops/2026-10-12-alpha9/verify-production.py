import hashlib,json,os,runpy,subprocess,time,urllib.request,urllib.error
from pathlib import Path
stage=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
workspace=Path('/opt/siyuan/workspace')
state=json.loads((stage/'deployment-state.json').read_text())
assert state['phase']=='promoted'
baseline=json.loads((stage/'workspace-manifest-before.json').read_text())
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()
notes=assets=0
for name,entry in baseline.items():
    if entry['type']!='file':continue
    path=Path(name)
    note=name.startswith('data/') and path.suffix=='.sy'
    asset=name.startswith('data/') and 'assets' in path.parts
    if note or asset:
        assert (workspace/path).is_file() and sha(workspace/path)==entry['sha256'],'Production file hash mismatch'
        notes+=int(note);assets+=int(asset)
old=json.loads((stage/'production-conf-before.json').read_text())
new=json.loads((workspace/'conf/conf.json').read_text())
assert new['api']['token']==old['api']['token'],'API token changed'
assert new['sync']['enabled'] is False
settings=[]
for key,value in old['sync'].items():
    if key in ('enabled','stat','synced'):continue
    # New schema adds fields; require all pre-existing storage settings unchanged.
    actual=new['sync'].get(key)
    if isinstance(value,dict) and isinstance(actual,dict):
        assert all(actual.get(k)==v for k,v in value.items()),'Existing sync storage configuration changed'
    else:assert actual==value,'Existing sync configuration changed'
    settings.append(key)
smoke=json.loads((stage/'production-smoke.json').read_text())
wrapper=json.loads((stage/'production-wrapper-smoke.json').read_text())
browser=json.loads((stage/'production-browser-smoke.json').read_text())
assert smoke['passed'] and smoke['url']=='https://obslinux.centralindia.cloudapp.azure.com'
assert wrapper['passed'] and wrapper['port']=='6806'
assert browser['passed'] and browser['version']=='3.8.7-alpha.9'
verify_appearance=runpy.run_path(str(Path(__file__).with_name('verify-appearance.py')))['verify_appearance']
appearance=verify_appearance(stage,workspace,state,6806)
containers={}
for cid in subprocess.check_output(['docker','ps','-q'],text=True).split():
    item=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
    if item['Id']!=state['production_id']:containers[item['Name']]={'id':item['Id'],'image':item['Image']}
assert containers==state['unrelated_containers'],'Unrelated service container changed'
http={}
for url in ['https://json-validator.centralindia.cloudapp.azure.com/health','https://json-formatter.centralindia.cloudapp.azure.com/']:
    with urllib.request.urlopen(url,timeout=30) as r:
        assert r.status==200
        http[url]=r.status
evidence={'result':'pass','production_id':state['production_id'],'image_id':state['image_id'],'backup_manifest_sha256':state['backup_manifest_sha256'],'time':time.time(),
 'checks':{**{k:True for k in ['public_https','authentication','version','renamed_endpoints','wrapper_compatibility','document_inventory','asset_hashes','unrelated_services']},'appearance_preserved':appearance['passed']},
 'counts':{'unchanged_note_files':notes,'unchanged_asset_files':assets,'unchanged_other_containers':len(containers)},'sync_setting_keys_preserved':settings,'http':http,'browser':browser,'appearance':appearance}
os.umask(0o077)
(stage/'production-evidence.json').write_text(json.dumps(evidence,indent=2))
print(json.dumps(evidence),flush=True)
