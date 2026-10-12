"""Run on the VM against the isolated candidate or production; print only summaries."""
import argparse, base64, hashlib, json, os, secrets, struct, time, urllib.request, urllib.error, zlib
from pathlib import Path

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--url',default='http://127.0.0.1:16806')
    p.add_argument('--workspace',default='/opt/siyuan/workspace-alpha9-candidate')
    p.add_argument('--output',default='/home/nl/siyuan-upgrade-20261012-alpha9/smoke.json')
    a=p.parse_args()
    workspace=Path(a.workspace)
    config=json.loads((workspace/'conf/conf.json').read_text())
    token=config['api']['token']
    checks={}
    completed=False
    created=[]
    def call(path,payload=None,auth=True,raw=None,ctype=None,method=None):
        headers={}
        if auth: headers['Authorization']='Token '+token
        if raw is not None:
            data=raw;headers['Content-Type']=ctype
        elif payload is not None:
            data=json.dumps(payload).encode();headers['Content-Type']='application/json'
        else: data=None
        req=urllib.request.Request(a.url+path,data=data,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=90) as r: return r.status,r.read()
        except urllib.error.HTTPError as e: return e.code,e.read()
    def api(path,payload):
        status,data=call(path,payload)
        assert status==200, path+' HTTP '+str(status)
        value=json.loads(data)
        assert value.get('code')==0,path+' application code '+str(value.get('code'))
        return value.get('data')
    def multipart(fields,field,filename,data):
        boundary='siyuanUpgrade'+secrets.token_hex(12)
        chunks=[]
        for key,value in fields.items():
            chunks.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+key+'"\r\n\r\n'+str(value)+'\r\n').encode())
        chunks.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+field+'"; filename="'+filename+'"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+data+b'\r\n')
        chunks.append(('--'+boundary+'--\r\n').encode())
        return b''.join(chunks),'multipart/form-data; boundary='+boundary
    def put(path,fields,field,filename,data):
        body,ctype=multipart(fields,field,filename,data)
        status,response=call(path,raw=body,ctype=ctype)
        assert status==200,path+' HTTP '+str(status)
        result=json.loads(response)
        assert result.get('code')==0,path+' application code '+str(result.get('code'))
        return result
    try:
        status,data=call('/api/system/version')
        version=json.loads(data)['data']
        assert status==200 and version=='3.8.7-alpha.9','version mismatch'
        checks['version']=True
        boot=api('/api/system/bootProgress',{})
        checks['boot_completed']=isinstance(boot,dict) and boot.get('progress')==100
        assert checks['boot_completed'],'kernel database and notebook initialization is incomplete'
        status,_=call('/api/notebook/lsNotebooks',{},auth=False)
        assert status in (401,403),'anonymous access not denied'
        checks['auth_required']=True
        notebooks=api('/api/notebook/lsNotebooks',{})['notebooks']
        checks['notebooks_readable']=len(notebooks)>=33
        assert checks['notebooks_readable'],'notebook count regression'
        user=api('/api/setting/getCloudUser',{'cached':True})
        assert user and user.get('userSiYuanProExpireTime')==-1 and user.get('userSiYuanOneTimePayStatus')==1,'local account unavailable'
        checks['local_account']=True
        api('/api/system/checkUpdate',{'showMsg':True})
        current=json.loads((workspace/'conf/conf.json').read_text())
        assert current['system']['downloadInstallPkg'] is False,'automatic package download enabled'
        checks['update_api_and_download_setting']=True
        unique=secrets.token_hex(6)
        content=b'SiYuan custom upgrade verification '+unique.encode()+b'\n'
        file_path='/data/assets/siyuan-upgrade-'+unique+'.txt'
        put('/api/file/p_ocr',{'path':file_path,'isDir':'false'},'file','siyuan-upgrade-'+unique+'.txt',content)
        created.append(file_path)
        status,download=call('/api/file/getFile',{'path':file_path})
        assert status==200 and download==content,'put/get mismatch'
        checks['file_put_read_hash']=True
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4DwQACfsD/fteaysAAAAASUVORK5CYII=')
        # Unique bytes prevent deduplication from returning a user's pre-existing asset.
        text_chunk=b'tEXt'+b'SiYuanUpgrade\0'+unique.encode()
        png=png[:-12]+struct.pack('>I',len(text_chunk)-4)+text_chunk+struct.pack('>I',zlib.crc32(text_chunk))+png[-12:]
        for route,label in [('/api/asset/ass_ocr','asset_upload_hash'),('/u_OCR','editor_upload_hash')]:
            filename='siyuan-upgrade-'+unique+'-'+label+'.png'
            result=put(route,{},'file[]',filename,png)
            paths=result.get('data',{}).get('succMap',{})
            assert paths,route+' missing succMap'
            value=next(iter(paths.values()))
            assert value.startswith('assets/'),route+' unexpected path format'
            created.append('/data/'+value)
            status,download=call('/api/file/getFile',{'path':'/data/'+value})
            assert status==200 and hashlib.sha256(download).digest()==hashlib.sha256(png).digest(),route+' hash mismatch'
            checks[label]=True
        for route in ['/upload','/api/file/putFile','/api/asset/upload']:
            status,_=call(route,{})
            assert status==404,'legacy route still registered: '+route
        checks['old_upload_routes_absent']=True
        for route in ['/u_OCR','/api/file/p_ocr','/api/asset/ass_ocr']:
            status,_=call(route,{},auth=False)
            assert status in (401,403),'unauthenticated upload allowed: '+route
        checks['upload_auth_required']=True
        docrows=api('/api/query/sql',{'stmt':"SELECT count(*) AS n FROM blocks WHERE type='d'"})
        baseline=json.loads((Path(a.output).parent/'index-baseline.json').read_text())
        checks['document_index_readable']=bool(docrows and docrows[0]['n']>=baseline['document_index_count'])
        assert checks['document_index_readable'],'document index count regressed'
        completed=True
    finally:
        cleanup=True
        for path in set(created):
            try: api('/api/file/removeFile',{'path':path})
            except Exception: cleanup=False
        checks['synthetic_files_cleaned']=cleanup
        summary={'url':a.url,'checks':checks,'passed':completed and all(checks.values()),'time':time.time()}
        os.umask(0o077)
        Path(a.output).write_text(json.dumps(summary,indent=2))
        print(json.dumps(summary),flush=True)
    assert summary['passed'],'smoke checks failed'

if __name__=='__main__':
    main()
