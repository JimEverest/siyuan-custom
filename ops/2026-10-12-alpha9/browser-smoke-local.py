import argparse,json,os,time,urllib.parse
from pathlib import Path
import paramiko
from playwright.sync_api import sync_playwright

p=argparse.ArgumentParser()
p.add_argument('--expected',required=True)
p.add_argument('--output',required=True)
a=p.parse_args()
ssh=paramiko.SSHClient();ssh.load_host_keys(str(Path.home()/'.ssh/known_hosts'));ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
ssh.connect('20.192.24.1',username='nl',password=os.environ.pop('SIYUAN_TASK_SSH_PASSWORD'),allow_agent=False,look_for_keys=False,timeout=20)
_,out,err=ssh.exec_command('sudo -n docker inspect siyuan_custom')
info=json.loads(out.read())[0]
auth=next(x.split('=',1)[1] for x in info['Config']['Cmd'] if x.startswith('--accessAuthCode='))
ssh.close()
errors=[];responses=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda e:errors.append(type(e).__name__))
    page.on('response',lambda r: responses.append({'path':r.url.split('?',1)[0].split('.com',1)[-1],'status':r.status}) if '/api/' in r.url else None)
    page.goto('https://obslinux.centralindia.cloudapp.azure.com/',wait_until='networkidle',timeout=60000)
    assert page.locator('#authCode').count()==1,'Expected authentication form'
    page.locator('#authCode').fill(auth)
    page.get_by_role('button',name='Unlock access').click()
    page.wait_for_function('typeof window.siyuan === "object" && !!window.siyuan.config',timeout=120000)
    page.wait_for_timeout(15000)
    explicit_open=False
    if page.locator('.protyle-wysiwyg [data-node-id]').count()==0:
        document_id=page.evaluate('''async () => {
          const r=await fetch('/api/query/sql',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stmt:"SELECT id FROM blocks WHERE type='d' ORDER BY id LIMIT 1"})});
          const j=await r.json(); if(r.status!==200 || j.code!==0 || !j.data?.length) throw new Error('No indexed document available');
          return j.data[0].id;
        }''')
        page.goto('https://obslinux.centralindia.cloudapp.azure.com/?id='+urllib.parse.quote(document_id,safe=''),wait_until='networkidle',timeout=60000)
        page.wait_for_function('document.querySelectorAll(".protyle-wysiwyg [data-node-id]").length>0',timeout=60000)
        explicit_open=True
    summary=page.evaluate('''async () => {
      const r = await fetch('/api/system/version'); const j = await r.json();
      return {version:j.data, configLoaded:!!window.siyuan.config,
        editorCount:document.querySelectorAll('.protyle:not(.fn__none)').length,
        blockCount:document.querySelectorAll('.protyle-wysiwyg [data-node-id]').length,
        authFormAbsent:!document.getElementById('authCode')};
    }''')
    summary['page_error_count']=len(errors)
    summary['explicit_document_open']=explicit_open
    summary['successful_document_responses']=sum(r['status']==200 and any(x in r['path'] for x in ['/api/filetree/getDoc','/api/filetree/getDocOutline','/api/block/getBlockInfo']) for r in responses)
    summary['api_error_count']=sum(r['status']>=500 for r in responses)
    summary['passed']=summary['version']==a.expected and summary['configLoaded'] and summary['authFormAbsent'] and summary['editorCount']>0 and summary['blockCount']>0 and summary['api_error_count']==0
    Path(a.output).write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))
    browser.close()
assert summary['passed'],'Browser validation failed'
