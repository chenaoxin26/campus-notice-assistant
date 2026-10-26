"""Loopback-only Windows campus assistant. Python standard library, existing Ollama."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
import uuid
import urllib.request
from datetime import datetime
from core import CampusDB, LocalAI, now, CATEGORIES

HERE=Path(__file__).resolve().parent
APP_ID='campus-local-assistant-github'
# GUI launcher uses pythonw; collector child needs console stdout for JSON evidence.
CHILD_PYTHON=str(Path(sys.executable).with_name('python.exe')) if Path(sys.executable).name.lower()=='pythonw.exe' else sys.executable

def capture_error_message(raw):
    if 'QQ_GROUP_LABEL_NOT_UNIQUE' in raw or 'Preview requires one exact' in raw:
        return '无法唯一定位群聊：请确认完整群名正确、该群出现在QQ会话列表中；重复的可访问性节点或同名会话也可能影响定位。未读取或保存该群消息。'
    if 'QQ_CONVERSATION_LIST_UNAVAILABLE' in raw or 'Conversation list not exposed' in raw:
        return 'QQ未暴露唯一可读会话列表，请打开QQ主窗口的消息页后重试。'
    if 'QQ_NATIVE_DOCUMENT_AMBIGUOUS' in raw:
        return 'QQ暴露多个渲染文档，无法安全判断目标会话；已停止读取，请恢复普通主窗口后重试。'
    if 'QQ_ACCESSIBILITY_SCAN_INCOMPLETE' in raw:
        return 'QQ界面扫描未完整结束，为避免错读其他会话，本次未采集；请稍后重试或手动粘贴。'
    if '需要可访问桌面和唯一QQ窗口' in raw:
        return '请解锁电脑并从托盘打开唯一的QQ普通主窗口，再点击补采；隐藏到托盘与普通最小化不同。'
    return raw


class Service:
    def __init__(self,data):
        from notification_source import NotificationSource
        from chat_source import ChatSource
        self.data=Path(data).resolve();self.data.mkdir(parents=True,exist_ok=True)
        self.config_path=self.data/'settings.json'
        if not self.config_path.exists():self.config_path.write_text((HERE/'settings.example.json').read_text('utf-8'),'utf-8')
        self.config=json.loads(self.config_path.read_text('utf-8'))
        collector = Path(self.config.get('collector_root') or 'collector')
        self.config['collector_root'] = str((collector if collector.is_absolute() else HERE/collector).resolve())
        self.config['collector_data_dir'] = str(self.data/'collector')
        self.db=CampusDB(self.data/'campus.sqlite3');self.ai=LocalAI(self.config,self.data)
        self.busy=False;self.lock=threading.Lock()
        self.source=NotificationSource(self.db,self.config,self.config_path,HERE/'NotificationBridge.exe')
        self.chat=ChatSource(self.data,self.db,self.config)
        self.chat.busy=False
        with self.db.connect() as db:db.execute("UPDATE jobs SET status='interrupted',updated_at=? WHERE status='running'",(now(),))
        self.db.flag_cross_source_candidates()
    def state(self):
        value=self.db.snapshot()
        value.update(model=self.ai.health(),groups=self.config['groups'],busy=self.busy,daytime_running=self.source.running(),
            categories=CATEGORIES,notification=self.source.snapshot(),version="final",
            chat_daytime_running=self.chat.day is not None and self.chat.day.poll() is None,
            export_files=[p.name for p in (self.data.parent/'导出').glob('*.xlsx')] if (self.data.parent/'导出').exists() else [])
        return value
    def start_job(self,kind):
        with self.lock:
            if self.busy:raise ValueError('已有操作进行中，请等待完成')
            self.busy=True
        job_id=uuid.uuid4().hex;self.db.job(job_id,kind,'running',{'message':'准备中'})
        def progress(text):self.db.job(job_id,kind,'running',{'message':text})
        def work():
            try:
                result={}
                warnings=[]
                if kind in {'capture','chat'}:
                    try:
                        progress('正在读取Windows当前保留通知，不操作QQ窗口')
                        result['notification']=self.source.scan()
                    except Exception as exc:warnings.append(str(exc))
                if kind=='chat':
                    progress('正在停止本程序的聊天实验轮读，再补充读取所选群')
                    self.chat.stop_daytime()
                    result['capture']=self.chat.capture(progress)
                    self.db.sync_collector(Path(self.config['collector_data_dir'])/'messages.sqlite3')
                if kind in {'capture','chat','analyze'}:
                    result['analysis']=self.db.analyze(self.ai,progress)
                    self.db.flag_cross_source_candidates()
                elif kind=='export':result['file']=self.export()
                message='操作结束；通知和近期聊天均可能遗漏，请核对原文'
                status='done'
                if warnings:result['warnings']=warnings;status='partial';message+='；通知读取异常：'+'；'.join(warnings)
                if result.get('analysis',{}).get('failed'):status='partial';message+='；部分AI分析失败，原文保留待重试'
                if kind=='chat':
                    failed=sum(r['status']=='本次未读到' for r in result['capture'])
                    if failed:status='error' if failed==len(result['capture']) else 'partial';message+=f'；{failed}个群未读到，见群聊覆盖结果'
                self.db.job(job_id,kind,status,{'message':message,'result':result})
            except Exception as exc:
                self.db.job(job_id,kind,'error',{'message':str(exc)})
                (self.data/'last-error.txt').write_text(traceback.format_exc(),'utf-8')
            finally:self.busy=False
        threading.Thread(target=work,daemon=True).start();return job_id
    def start_daytime(self):
        if self.busy:raise ValueError('请等待当前操作完成')
        self.source.start()
    def stop_daytime(self):
        self.source.close()

    def export(self):
        export_dir=self.data.parent/'导出';export_dir.mkdir(exist_ok=True)
        from excel_export import export_snapshot
        target=export_dir/('校园待办-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.xlsx')
        export_snapshot(self.db.snapshot(), target)
        return str(target)



def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,default=HERE/'data')
    p.add_argument('--port',type=int,default=8769);p.add_argument('--no-browser',action='store_true');args=p.parse_args()
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{args.port}/api/health',timeout=1) as response:
            existing=json.load(response)
        if existing.get('app_id')==APP_ID:
            if not args.no_browser:webbrowser.open(f'http://127.0.0.1:{args.port}')
            return
    except Exception:pass
    service=Service(args.data_dir);token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,status,data,kind='application/json; charset=utf-8'):
            raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def valid_host(self): return self.headers.get('Host') in {f'127.0.0.1:{args.port}',f'localhost:{args.port}'}
        def do_GET(self):
            if not self.valid_host(): return self.reply(403,{'error':'仅允许本机访问'})
            try:
                if self.path=='/':
                    page=(HERE/'index.html').read_text('utf-8').replace('__CSRF_TOKEN__',token)
                    return self.reply(200,page.encode(),'text/html; charset=utf-8')
                if self.path=='/api/health': return self.reply(200,{'app_id':APP_ID})
                if self.path=='/api/state': return self.reply(200,service.state())
                return self.reply(404,{'error':'未找到'})
            except Exception as exc: return self.reply(500,{'error':str(exc)})
        def do_POST(self):
            if not self.valid_host() or not hmac.compare_digest(self.headers.get('X-Campus-Token',''),token): return self.reply(403,{'error':'无效请求'})
            if self.headers.get('Origin') not in {None,f'http://127.0.0.1:{args.port}',f'http://localhost:{args.port}'}: return self.reply(403,{'error':'不允许跨站操作'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length>100000: raise ValueError('请求过大')
                body=json.loads(self.rfile.read(length) or b'{}')
                if self.path=='/api/job':
                    if body.get('kind') not in {'capture','chat','analyze','export'}: raise ValueError('无效操作')
                    return self.reply(200,{'job_id':service.start_job(body['kind'])})
                if self.path=='/api/task': service.db.edit_task(body['id'],body['patch']);return self.reply(200,{'ok':True})
                if self.path=='/api/import':
                    source=body.get('source')
                    if source not in {'WeChat_manual','QQ_manual'}: raise ValueError('来源无效')
                    parts=__import__('re').split(r'\n\s*---+\s*\n',body['body'].strip());count=0
                    for text in parts:
                        _,added=service.db.add_notice(source,body['group'],body.get('sender'),text,kind='manual_notification_text')
                        count+=added
                    return self.reply(200,{'imported':count})
                if self.path=='/api/notification/confirm':
                    service.source.confirm(body['event_key']);return self.reply(200,{'ok':True})
                if self.path=='/api/groups':
                    if service.busy or service.source.running() or (service.chat.day and service.chat.day.poll() is None): raise ValueError('请先停止采集再修改群名单')
                    groups=list(dict.fromkeys(s.strip() for s in body['groups'] if isinstance(s,str) and s.strip()))
                    if len(groups)>30 or any(len(s)>100 for s in groups): raise ValueError('请填写至多30个完整群名；留空可停止授权采集')
                    service.config['groups']=groups;service.config_path.write_text(json.dumps(service.config,ensure_ascii=False,indent=2),'utf-8')
                    return self.reply(200,{'ok':True})
                if self.path=='/api/chat-daytime':
                    if service.busy:raise ValueError('请等待当前操作完成')
                    if body.get('start'):service.chat.start_daytime()
                    else:service.chat.stop_daytime()
                    return self.reply(200,{'ok':True})
                if self.path=='/api/daytime':
                    if body.get('start'): service.start_daytime()
                    else: service.stop_daytime()
                    return self.reply(200,{'ok':True})
                if self.path=='/api/exit':
                    if service.busy: raise ValueError('请等待当前操作结束再退出')
                    service.stop_daytime();service.chat.stop_daytime();self.reply(200,{'ok':True});threading.Thread(target=server.shutdown,daemon=True).start();return
                return self.reply(404,{'error':'未找到操作'})
            except Exception as exc: return self.reply(400,{'error':str(exc)})
    try: server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError:
        print('端口已占用，请检查是否已有本程序运行。',flush=True);return
    print(f'http://127.0.0.1:{args.port}',flush=True)
    if not args.no_browser: webbrowser.open(f'http://127.0.0.1:{args.port}')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=='__main__': main()
