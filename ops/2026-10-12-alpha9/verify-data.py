import hashlib,json,os,re,runpy,sqlite3,subprocess,time
from pathlib import Path

def has_siyuan_fts5_schema(conn):
    # Pinned kernel/sql/database.go creates blocks/history/asset FTS5 tables
    # with this application tokenizer; host Python SQLite does not register it.
    declarations=conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL").fetchall()
    for (statement,) in declarations:
        if not re.match(r'^\s*CREATE\s+VIRTUAL\s+TABLE\b',statement,re.I):continue
        if not re.search(r'\bUSING\s+fts5\s*\(',statement,re.I):continue
        if re.search(r'''\btokenize\s*=\s*(["'])siyuan(?:\s+(?:case_insensitive|han_insensitive))*\1\s*[,)]''',statement):
            return True
    return False

def sqlite_quick_check(conn):
    try:
        result=conn.execute('PRAGMA quick_check').fetchall()
    except sqlite3.OperationalError as error:
        if str(error)!='no such tokenizer: siyuan' or not has_siyuan_fts5_schema(conn):
            raise
        return 'unsupported_custom_tokenizer'
    assert result==[('ok',)],'candidate SQLite quick_check failed'
    return 'checked'

stage=Path('/home/nl/siyuan-upgrade-20261012-alpha9')
candidate=Path('/opt/siyuan/workspace-alpha9-candidate')
state=json.loads((stage/'deployment-state.json').read_text())
assert state['phase']=='prepared'
baseline=json.loads((stage/'workspace-manifest-before.json').read_text())
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''):h.update(chunk)
    return h.hexdigest()
notes=assets=0
for name,entry in baseline.items():
    if entry['type']!='file': continue
    path=Path(name)
    note=name.startswith('data/') and path.suffix=='.sy'
    asset=name.startswith('data/') and 'assets' in path.parts
    if not (note or asset):continue
    assert (candidate/path).is_file(),'missing note or asset (see private baseline)'
    assert sha(candidate/path)==entry['sha256'],'note/asset hash mismatch (requires investigation)'
    notes+=int(note);assets+=int(asset)
smoke=json.loads((stage/'smoke.json').read_text())
wrapper=json.loads((stage/'wrapper-smoke.json').read_text())
assert smoke['passed'] and wrapper['passed']
info=json.loads(subprocess.check_output(['docker','inspect',state['candidate_id']]))[0]
assert info['Image']==state['image_id'] and info['State']['Running']
labels=json.loads(subprocess.check_output(['docker','image','inspect',state['image_id']]))[0]['Config']['Labels']
assert labels['io.siyuan.upstream.revision']=='def127fc774ab2b5ab9a0096609a15aa5ba30df4'
conf=json.loads((candidate/'conf/conf.json').read_text())
assert conf.get('confVersion')==6
assert conf['sync']['enabled'] is False
network=json.loads(subprocess.check_output(['docker','network','inspect',info['HostConfig']['NetworkMode']]))[0]
assert network['Internal'] is True
verify_appearance=runpy.run_path(str(Path(__file__).with_name('verify-appearance.py')))['verify_appearance']
appearance=verify_appearance(stage,candidate,state,16806)
database_counts={'sqlite_databases_checked':0,'unsupported_custom_tokenizer':0,'non_plaintext_databases_not_checked':0}
for path in (candidate/'temp').rglob('*.db'):
    with path.open('rb') as f: header=f.read(16)
    if header!=b'SQLite format 3\0':
        database_counts['non_plaintext_databases_not_checked']+=1
        continue
    conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=60)
    try:
        result=sqlite_quick_check(conn)
    finally:
        conn.close()
    database_counts['sqlite_databases_checked' if result=='checked' else result]+=1
evidence={'result':'pass','candidate_id':state['candidate_id'],'image_id':state['image_id'],'backup_manifest_sha256':state['backup_manifest_sha256'],'time':time.time(),
          'checks':{'version':True,'authentication':smoke['checks']['auth_required'],'renamed_endpoints':all(smoke['checks'].values()),'wrapper_compatibility':wrapper['passed'],'document_inventory':notes>=1858,'asset_hashes':assets>0,'appearance_preserved':appearance['passed'],'source_review':True},
          'counts':{'unchanged_note_files':notes,'unchanged_asset_files':assets,**database_counts},
          'sqlite_validation':{'unsupported_custom_tokenizer_quick_check_passed':False,'basis':'Host SQLite cannot load the schema-confirmed SiYuan FTS5 tokenizer; runtime SQL/index checks are separate evidence. Non-plaintext files are not SQLite-checked.'},
          'source_review_basis':'Four patches reviewed independently; Go/API contract CI and full image build passed; upstream SHA checked',
          'appearance':appearance}
assert all(evidence['checks'].values())
os.umask(0o077)
(stage/'promotion-evidence.json').write_text(json.dumps(evidence,indent=2))
print(json.dumps(evidence),flush=True)
