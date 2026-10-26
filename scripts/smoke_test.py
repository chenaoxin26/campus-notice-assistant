"""Real local HTTP lifecycle test using fake AI only; never reads QQ/notifications."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, re, socket, subprocess, sys, tempfile, threading, time, urllib.request
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]

def main():
    request_counts={'tags':0,'chat':0}
    class FakeOllama(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,obj):
            raw=json.dumps(obj,ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            request_counts['tags']+=1
            self.reply({'models':[{'name':'fake-model-for-tests'}]})
        def do_POST(self):
            request_counts['chat']+=1
            self.rfile.read(int(self.headers['Content-Length']))
            item=dict(title='提交虚构报告',category='学习任务',deadline_text='待确认',location='待确认',requirements=[],steps=[],importance='待确认',evidence='请提交虚构报告',is_update=False)
            self.reply({'done':True,'message':{'content':json.dumps({'tasks':[item]},ensure_ascii=False)}})
    fake=ThreadingHTTPServer(('127.0.0.1',0),FakeOllama)
    threading.Thread(target=fake.serve_forever,daemon=True).start()
    process=None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    checks=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append(name)
    with tempfile.TemporaryDirectory(prefix='campus-release-smoke-') as temp:
        folder=Path(temp);data=folder/'private-data';data.mkdir()
        config=json.loads((ROOT/'settings.example.json').read_text('utf-8'))
        config.update(api_url=f'http://127.0.0.1:{fake.server_port}',model='fake-model-for-tests')
        (data/'settings.json').write_text(json.dumps(config),'utf-8')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        def request(path,body=None,token=None):
            headers={}
            if body is not None:headers.update({'Content-Type':'application/json','X-Campus-Token':token or ''})
            req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            with opener.open(req,timeout=10) as response:
                raw=response.read()
                return json.loads(raw) if response.headers.get_content_type()=='application/json' else raw.decode('utf-8')
        def start():
            nonlocal process
            process=subprocess.Popen([sys.executable,str(ROOT/'app.py'),'--data-dir',str(data),'--port',str(port),'--no-browser'],cwd=folder,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            for _ in range(100):
                try:
                    request('/api/health')
                    html=request('/')
                    return re.search(r'<meta name="campus-token" content="([^"]+)"',html).group(1)
                except (OSError,AttributeError):
                    if process.poll() is not None:raise RuntimeError(process.stderr.read().decode(errors='replace'))
                    time.sleep(.1)
            raise RuntimeError('HTTP startup timed out')
        def job(kind,token):
            job_id=request('/api/job',{'kind':kind},token)['job_id']
            for _ in range(100):
                state=request('/api/state')
                result=next((j for j in state['jobs'] if j['id']==job_id),None)
                if result and result['status']!='running':return result
                time.sleep(.05)
            raise RuntimeError('job timed out')
        try:
            token=start()
            state=request('/api/state')
            check('fresh_http_empty_inactive',state['tasks']==[] and state['groups']==[] and not state['notification']['running'])
            check('no_implicit_model_analysis',request_counts['chat']==0)
            text='请提交虚构报告，具体时间地点另行通知。'
            check('manual_import',request('/api/import',{'source':'QQ_manual','group':'虚构群','sender':'虚构同学','body':text},token)['imported']==1)
            check('local_api_analysis',job('analyze',token)['status']=='done')
            state=request('/api/state');task=state['tasks'][0]
            check('unknown_time_location_not_invented',task['deadline_text']=='待确认' and task['location']=='待确认')
            request('/api/task',{'id':task['id'],'patch':{'status':'done'}},token)
            request('/api/import',{'source':'QQ_manual','group':'虚构群','sender':'虚构同学','body':text},token)
            job('analyze',token)
            check('repeat_does_not_reopen',request('/api/state')['counts']['done']==1 and request_counts['chat']==1)
            request('/api/groups',{'groups':['虚构群']},token)
            request('/api/groups',{'groups':[]},token)
            check('whitelist_can_be_revoked',request('/api/state')['groups']==[])
            request('/api/exit',{},token);process.wait(timeout=10)
            # Make the configured model unreachable without stopping anyone's real API.
            config['api_url']='http://127.0.0.1:1'
            (data/'settings.json').write_text(json.dumps(config),'utf-8')
            token=start();state=request('/api/state')
            check('restart_persists_completed_task',state['counts']['done']==1 and state['counts']['open']==0)
            check('state_available_without_model',not state['model']['available'])
            result=job('export',token)
            check('http_export_without_model',result['status']=='done')
            exported=Path(json.loads(result['detail_json'])['result']['file'])
            wb=load_workbook(exported)
            check('xlsx_values_and_sources',wb['校园待办']['F4'].value=='已完成' and wb['消息来源']['G2'].value==text)
            wb.close()
            request('/api/exit',{},token);process.wait(timeout=10)
            check('graceful_shutdown',process.returncode==0)
        finally:
            if process and process.poll() is None:process.terminate();process.wait(timeout=10)
            if process and process.stderr:process.stderr.close()
            fake.shutdown();fake.server_close()
    print(json.dumps({'passed':len(checks),'checks':checks,'model':'fake local API, not Qwen inference','qq_collection':'not performed'},ensure_ascii=True))

if __name__=='__main__':main()
