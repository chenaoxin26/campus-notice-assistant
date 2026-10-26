"""Windows official listener snapshots; no QQ window operation or chat database access."""
import json,subprocess,threading,time,uuid
from pathlib import Path
from core import digest,now

class NotificationSource:
    def __init__(self,db,config,config_path,bridge):
        self.db=db;self.config=config;self.config_path=Path(config_path);self.bridge=Path(bridge)
        self.lock=threading.Lock();self.stop=threading.Event();self.thread=None
        self.last={'status':'尚未读取','access_status':'未检测','last_scan':None,'error':None}
        with db.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS windows_notifications(
            event_key TEXT PRIMARY KEY,notification_id TEXT,notification_created_at TEXT,app_id TEXT,app_name TEXT,
            group_name TEXT,body TEXT,texts_json TEXT,source_verified INTEGER,verification_method TEXT,
            first_seen_at TEXT,last_seen_at TEXT,notice_id TEXT,identity_error TEXT);
            CREATE TABLE IF NOT EXISTS notification_scan_events(id INTEGER PRIMARY KEY,at TEXT,status TEXT,detail_json TEXT);''')
    def running(self):return self.thread is not None and self.thread.is_alive()
    def event(self,status,detail):
        with self.db.connect() as c:c.execute('INSERT INTO notification_scan_events(at,status,detail_json) VALUES (?,?,?)',(now(),status,json.dumps(detail,ensure_ascii=False)))
    def scan(self):
        if not self.config['groups']: raise ValueError('请先填写允许采集的QQ群')
        with self.lock:
            try:
                p=subprocess.run([str(self.bridge),str(self.config_path)],capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                response=json.loads(p.stdout.decode('utf-8-sig'))
                if not response.get('ok'):
                    raise RuntimeError('Windows通知接口不可用：'+str(response.get('access_status') or response.get('error'))+'；不会修改权限或改用QQ窗口采集')
                saved=self.ingest(response.get('rows',[]))
                previous=self.last.get('status');self.last={'status':'已读取当前保留通知','access_status':response['access_status'],'last_scan':now(),'error':None,
                    'current_matching_cards':len(response['rows']),'new_records':saved,'unavailable_count':response.get('unavailable_count',0),'coverage_complete':False}
                if saved or previous!='已读取当前保留通知':self.event('scan_ok',self.last)
                return dict(self.last)
            except Exception as exc:
                previous=self.last.get('error');self.last={'status':'读取失败，期间可能遗漏','access_status':'未确认','last_scan':now(),'error':str(exc),'coverage_complete':False}
                if previous!=str(exc):self.event('scan_error',self.last)
                raise
    def ingest(self,rows):
        saved=0
        for r in rows:
            # Trust neither arbitrary title matches in body nor non-QQ applications.
            if not r.get('scope_verified') or r.get('group') not in self.config['groups']:continue
            if r.get('app_name') not in {None,'QQ'} and r.get('app_id') not in self.config.get('qq_app_ids',[]):continue
            key=digest([r.get('app_id'),r['notification_id'],r['notification_created_at'],r['texts']])
            stamp=now();verified=bool(r.get('source_verified')) and (r.get('app_name')=='QQ' or r.get('app_id') in self.config.get('qq_app_ids',[]));body=r.get('body','')
            with self.db.connect() as c:
                old=c.execute('SELECT source_verified,notice_id FROM windows_notifications WHERE event_key=?',(key,)).fetchone()
                if old:
                    c.execute('UPDATE windows_notifications SET last_seen_at=? WHERE event_key=?',(stamp,key))
                else:c.execute('''INSERT INTO windows_notifications(event_key,notification_id,notification_created_at,app_id,app_name,group_name,body,texts_json,source_verified,verification_method,first_seen_at,last_seen_at,identity_error)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',(key,str(r['notification_id']),r['notification_created_at'],r.get('app_id'),r.get('app_name'),r['group'],body,json.dumps(r['texts'],ensure_ascii=False),int(verified),'windows_app_identity' if verified else 'pending_user_confirmation',stamp,stamp,r.get('identity_error')))
            if old:
                if old['source_verified'] and not old['notice_id']:self.materialize(key)
                continue
            saved+=1
            if verified:self.materialize(key)
        return saved
    def materialize(self,key):
        with self.db.connect() as c:r=c.execute('SELECT * FROM windows_notifications WHERE event_key=?',(key,)).fetchone()
        if not r or not r['source_verified']:raise ValueError('通知来源未经确认，不交给AI')
        if not r['body'].strip():return
        nid,_=self.db.add_notice('QQ_windows_notification',r['group_name'],None,r['body'],source_key='windows-notification:'+key,
            collected_at=r['first_seen_at'],kind='windows_notification_text')
        with self.db.connect() as c:c.execute('UPDATE windows_notifications SET notice_id=? WHERE event_key=?',(nid,key))
    def confirm(self,key):
        with self.db.connect() as c:
            r=c.execute('SELECT * FROM windows_notifications WHERE event_key=?',(key,)).fetchone()
            if not r:raise ValueError('没有该通知')
            if r['group_name'] not in self.config['groups']:raise ValueError('该群已不在授权名单')
            c.execute("UPDATE windows_notifications SET source_verified=1,verification_method='user_confirmed_QQ' WHERE event_key=?",(key,))
        self.materialize(key);self.event('source_confirmed',{'event_key':key})
    def start(self):
        if not self.config['groups']: raise ValueError('请先填写允许采集的QQ群')
        if not self.bridge.is_file(): raise RuntimeError('通知桥接程序尚未构建，请先运行 build.cmd')
        if self.running():raise ValueError('通知监听已运行')
        self.stop.clear();self.event('monitor_started',{'interval_seconds':self.config.get('notification_poll_seconds',3),'coverage_complete':False})
        def work():
            while not self.stop.is_set():
                try:self.scan()
                except Exception:pass
                self.stop.wait(max(2,int(self.config.get('notification_poll_seconds',3))))
        self.thread=threading.Thread(target=work,daemon=True);self.thread.start()
    def close(self):
        was_running=self.running();self.stop.set()
        if self.thread:self.thread.join(timeout=23)
        if was_running:self.event('monitor_stopped',{'coverage_complete':False})
    def snapshot(self):
        with self.db.connect() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM windows_notifications ORDER BY first_seen_at DESC')]
            events=[dict(r) for r in c.execute('SELECT * FROM notification_scan_events ORDER BY id DESC LIMIT 20')]
        return {'running':self.running(),'last':self.last,'saved_cards':len(rows),'pending_source':sum(not r['source_verified'] for r in rows),
            'cards':rows,'events':events,'coverage_complete':False}
