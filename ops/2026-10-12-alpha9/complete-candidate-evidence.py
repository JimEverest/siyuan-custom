import hashlib,json,os
from datetime import datetime
from pathlib import Path
s=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
read=lambda name:json.loads((s/name).read_text())
sha=lambda name:hashlib.sha256((s/name).read_bytes()).hexdigest()
state=read('deployment-state.json')
assert state['phase']=='prepared'
prep=read('preparation-result.json')
assert prep['passed'] and prep['phase']=='candidate-verified' and prep['operation_id']==state['operation_id']
evidence=read('promotion-evidence.json')
assert evidence['result']=='pass' and all(evidence['checks'].values())
for key in ('candidate_id','image_id','backup_manifest_sha256'):assert evidence[key]==state[key]
cutoff=datetime.fromisoformat(state['downtime_started_at_utc'].replace('Z','+00:00')).timestamp()
receipts={}
for name in ['config-identity-evidence.json','settings-evidence.json','ocr-worker-smoke.json']:
 value=read(name)
 assert value['passed'] is True and (s/name).stat().st_mtime>=cutoff
 receipts[name]=sha(name)
identity=read('config-identity-evidence.json')
assert all(identity['checks'].values()) and identity['counts']['checked_fields']>=95
settings=read('settings-evidence.json')
assert settings['old_conf_sha256']==sha('production-conf-before.json')
ocr=read('ocr-worker-smoke.json')
assert ocr['candidate_id']==state['candidate_id'] and ocr['image_id']==state['image_id'] and ocr['synthetic_file_cleaned']
evidence['additional_receipts_sha256']=receipts
evidence['checks'].update({'configuration_identity':True,'existing_preferences':True,'actual_ocr_inference':True})
os.umask(0o077)
(s/'promotion-evidence.json').write_text(json.dumps(evidence,indent=2))
print(json.dumps({'passed':True,'operation_id':state['operation_id'],'counts':evidence['counts'],'extra_checks':list(receipts)}))
