"""Explicit QQ UIA supplement and optional idle rotation; no sends or chat DB access."""
import os,sys,json,subprocess,threading,time,uuid
from pathlib import Path
from core import now
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


class ChatSource:
    def __init__(self,data,db,config):
        self.data=Path(data);self.db=db;self.config=config;self.busy=False;self.day=None;self.day_log=None
    def capture(self,progress):
        groups=self.config['groups']
        if not groups: raise ValueError('请先填写允许补采的QQ群')
        root=Path(self.config['collector_root'])
        if not (root/'Probe.exe').is_file(): raise RuntimeError('QQ采集器尚未构建，请先运行 build.cmd')
        args=[CHILD_PYTHON,str(root/'rotation.py'),'--mode','evening','--idle-seconds','0','--restore-minimized']
        for group in groups: args.extend(['--group',group])
        log_path=self.data/('capture-'+uuid.uuid4().hex[:8]+'.jsonl')
        progress('正在逐个打开所选群读取近期可访问消息；未读状态可能改变')
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
            env={**os.environ,'PYTHONIOENCODING':'utf-8','CAMPUS_COLLECTOR_DATA_DIR':self.config['collector_data_dir']},
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        run_id=None;reads={};errors={}
        with log_path.open('w',encoding='utf-8') as log:
            for raw in process.stdout:
                text=raw.decode('utf-8-sig',errors='replace');log.write(text);log.flush()
                try: event=json.loads(text)
                except ValueError: continue
                if event.get('event')=='rotation_start': run_id=event['run_id']
                group=event.get('group')
                if group: progress('正在补采：'+group+'（不代表今日完整）')
                if event.get('event')=='full_message_read_saved': reads[group]=event
                if event.get('event')=='read_failed': errors[group]=event.get('reason','读取失败')
        code=process.wait()
        global_error=''
        if code and not reads:
            tail=log_path.read_text('utf-8',errors='replace')[-3000:]
            if '需要可访问桌面和唯一QQ窗口' in tail:global_error=capture_error_message('需要可访问桌面和唯一QQ窗口')
        result=[]
        with self.db.connect() as db:
            for group in groups:
                r=reads.get(group);status='近期消息区可读' if r else '本次未读到'
                error=errors.get(group,'') if r else errors.get(group,f'未收到成功读取证据；进程退出码{code}，详见{log_path.name}')
                error=global_error or capture_error_message(error)
                row={'group':group,'status':status,'readable_rows':r['visible_rows'] if r else 0,
                    'added_rows':r['added_rows'] if r else 0,'possible_missing':True,'error':error}
                db.execute('INSERT INTO capture_results(at,run_id,group_name,readable_rows,added_rows,status,error) VALUES (?,?,?,?,?,?,?)',
                    (now(),run_id,group,row['readable_rows'],row['added_rows'],status,error))
                result.append(row)
        return result

    def start_daytime(self):
        if not self.config['groups']: raise ValueError('请先填写允许采集的QQ群')
        if not (Path(self.config['collector_root'])/'Probe.exe').is_file(): raise RuntimeError('请先运行 build.cmd 构建QQ采集器')
        if self.day and self.day.poll() is None: raise ValueError('白天实验采集已运行')
        if self.busy: raise ValueError('请等待当前操作结束')
        stop=self.data/'chat-daytime.stop'
        if stop.exists(): stop.unlink() # Own ephemeral control flag, never a chat/model file.
        args=[CHILD_PYTHON,str(Path(self.config['collector_root'])/'rotation.py'),
            '--seconds','28800','--idle-seconds','60','--stop-file',str(stop)]
        for group in self.config['groups']: args.extend(['--group',group])
        self.day_log=(self.data/'chat-daytime-console.log').open('ab')
        self.day=subprocess.Popen(args,stdout=self.day_log,stderr=self.day_log,
            env={**os.environ,'PYTHONIOENCODING':'utf-8','CAMPUS_COLLECTOR_DATA_DIR':self.config['collector_data_dir']},
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def sync_loop():
            while self.day and self.day.poll() is None:
                try: self.db.sync_collector(Path(self.config['collector_data_dir'])/'messages.sqlite3')
                except Exception: pass
                time.sleep(8)
        threading.Thread(target=sync_loop,daemon=True).start()

    def stop_daytime(self):
        if self.day and self.day.poll() is None:
            (self.data/'chat-daytime.stop').write_text('user requested stop','utf-8')
            self.day.wait(timeout=45)
        if self.day_log: self.day_log.close();self.day_log=None

