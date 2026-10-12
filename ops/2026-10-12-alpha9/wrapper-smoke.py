import argparse,json,os,subprocess
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument('--port',default='16806')
p.add_argument('--workspace',default='/opt/siyuan/workspace-alpha9-candidate')
p.add_argument('--output',default='/home/nl/siyuan-upgrade-20261012-alpha9/wrapper-smoke.json')
a=p.parse_args()
conf=json.loads((Path(a.workspace)/'conf/conf.json').read_text())
wrapper=json.loads(subprocess.check_output(['docker','inspect','siyuan-wrapper']))[0]
override_script="import json,yaml; print(json.dumps(yaml.safe_load(open('/app/config/config.yaml'))['siyuan'].get('endpoint_overrides',{})))"
overrides=json.loads(subprocess.check_output(['docker','exec','siyuan-wrapper','python','-c',override_script]))
assert overrides.get('/api/asset/upload')=='/api/asset/ass_ocr'
assert overrides.get('/api/file/putFile')=='/api/file/p_ocr'
settings={'base_url':'http://127.0.0.1:'+a.port,'api_token':conf['api']['token'],'endpoint_overrides':overrides}
program='settings='+repr(settings)+'\n'+r'''
import asyncio, hashlib, importlib, json, secrets, sys, types
# Load the real image client modules without importing the Git Sync scheduler/routes.
pkg=types.ModuleType('src.git_sync'); pkg.__path__=['/app/src/git_sync']; sys.modules['src.git_sync']=pkg
from src.git_sync.siyuan_api import GitSyncSiyuanClient
async def main():
    client=GitSyncSiyuanClient(**settings)
    unique=secrets.token_hex(12)
    content=b'Wrapper upgrade round trip '+unique.encode()
    path='/data/assets/wrapper-upgrade-'+unique+'.txt'
    created=[]
    checks={}
    completed=False
    try:
        assert len(await client.list_notebooks())>=33
        checks['notebooks']=True
        await client.put_file(path,content)
        created.append(path)
        assert await client.get_file(path)==content
        checks['put_file_read_hash']=True
        asset=await client.upload_asset('wrapper-asset-'+unique+'.txt',content+b'-asset')
        assert asset.startswith('assets/')
        created.append('/data/'+asset)
        assert await client.get_file('/data/'+asset)==content+b'-asset'
        checks['upload_asset_read_hash']=True
        completed=True
    finally:
        for f in set(created):
            await client._request('/api/file/removeFile',{'path':f})
        checks['synthetic_files_cleaned']=True
        await client.close()
    print(json.dumps({'passed':completed and all(checks.values()),'checks':checks}))
asyncio.run(main())
'''
result=subprocess.run(['docker','run','--rm','-i','--network','host','--entrypoint','python',wrapper['Image'],'-'],input=program.encode(),capture_output=True,timeout=180)
if result.returncode:
    # Preserve diagnostics privately; the script input contains a token and is never saved.
    os.umask(0o077)
    Path(a.output+'.private.log').write_bytes(result.stderr+result.stdout)
    raise RuntimeError('Wrapper compatibility test failed; inspect private diagnostics')
summary=json.loads(result.stdout)
summary['wrapper_image_id']=wrapper['Image']
summary['port']=a.port
os.umask(0o077)
Path(a.output).write_text(json.dumps(summary,indent=2))
print(json.dumps(summary),flush=True)
assert summary['passed']
