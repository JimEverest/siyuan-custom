import json,shutil,subprocess
from pathlib import Path
stage=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
for name in ['preparation-result.json','deployment-state.json','smoke.json','wrapper-smoke.json','promotion-evidence.json','production-smoke.json','production-wrapper-smoke.json','production-evidence.json']:
    path=stage/name
    if not path.exists():continue
    value=json.loads(path.read_text())
    if name=='deployment-state.json':
        keep=['phase','image_id','candidate_id','production_id','snapshot_id','backup_file_count','backup_manifest_sha256','started_at_utc','downtime_started_at_utc','production_ready_at_utc','sync_restored','sync_disabled_after_failure']
        value={key:value[key] for key in keep if key in value}
    print(name,json.dumps(value),flush=True)
print('free_bytes',shutil.disk_usage('/').free,flush=True)
print(subprocess.check_output(['docker','ps','--format','{{.Names}} {{.Status}}'],text=True),flush=True)
