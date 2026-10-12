import json,subprocess,time,os
from pathlib import Path
s=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
read=lambda n:json.loads((s/n).read_text())
state=read('deployment-state.json');assert state['phase']=='finalized'
final=read('final-evidence.json');assert final['passed']
prod=json.loads(subprocess.check_output(['docker','inspect',state['production_id']]))[0]
logs=subprocess.run(['docker','logs','--since',state['production_ready_at_utc'],state['production_id']],capture_output=True)
raw=logs.stdout+logs.stderr
conf=json.loads(Path('/opt/siyuan/workspace/conf/conf.json').read_text())
summary={'final':final,'candidate_counts':read('promotion-evidence.json')['counts'],
 'production_counts':read('production-evidence.json')['counts'],'browser':read('production-browser-smoke.json'),
 'configuration_identity_counts':read('production-config-identity-evidence.json')['counts'],
 'settings_unchanged_fields':read('production-settings-evidence.json')['unchanged_fields'],
 'appearance':read('production-evidence.json')['appearance']['selection_preserved'],
 'ocr':{k:v for k,v in read('ocr-worker-smoke.json').items() if k in ('passed','runtime_seconds','elapsed_seconds','recognized_rows','synthetic_file_cleaned')},
 'backup':{'snapshot_id':state['snapshot_id'],'snapshot_reference':state['snapshot_reference'],'manifest_sha256':state['backup_manifest_sha256'],'file_count':state['backup_file_count']},
 'production':{'id':prod['Id'],'restart':prod['HostConfig']['RestartPolicy']['Name'],'log_config':prod['HostConfig']['LogConfig'],'image_id':prod['Image']},
 'sync_observation':{'enabled':conf['sync']['enabled'],'provider':conf['sync']['provider'],'synced':conf['sync']['synced'],'successful_sync_phase_log_count':raw.count(b'sync data repo phases ['),'sync_data_repo_failure_count':raw.count(b'sync data repo failed:'),'observed_at':time.time(),'roundtrip_tested':False},
 'proxy_stopped':read('candidate-proxy.json').get('stopped_after_final_validation') is True}
os.umask(0o077)
(s/'deployment-summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary),flush=True)
