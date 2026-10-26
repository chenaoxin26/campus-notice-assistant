"""Continuous selected-conversation collector; no window switching or chat input."""
import argparse
import json
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collector import ROOT, DATA_ROOT, Store, message_rows, probe

DB = DATA_ROOT / 'messages.sqlite3'


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat()


class Continuous:
    def __init__(self, groups, interval=5, include_existing=True, emit=None, db=DB, api=probe):
        self.groups = list(dict.fromkeys(s.strip() for s in groups if s.strip()))
        if not self.groups:
            raise ValueError('请明确指定至少一个测试群。')
        if interval < 1:
            raise ValueError('轮询间隔至少 1 秒。')
        self.interval, self.include_existing = interval, include_existing
        self.emit = emit or (lambda event: None)
        self.api, self.path = api, Path(db)
        self.stop = threading.Event()
        self.failures = 0
        self.last_capture = None
        self.last_group = None

    def open(self):
        self.store = Store(self.path)
        self.store.db.executescript('''
        CREATE TABLE IF NOT EXISTS capture_groups (
          source TEXT, conversation TEXT, initialized INTEGER DEFAULT 0,
          last_success TEXT, last_status TEXT, PRIMARY KEY(source,conversation));
        CREATE TABLE IF NOT EXISTS capture_gaps (
          id INTEGER PRIMARY KEY, source TEXT, conversation TEXT,
          started_at TEXT, recovered_at TEXT, reason TEXT,
          completeness TEXT NOT NULL DEFAULT 'unknown');
        CREATE TABLE IF NOT EXISTS capture_observations (
          id INTEGER PRIMARY KEY, source TEXT, conversation TEXT, observed_at TEXT,
          client_received TEXT, ui_available INTEGER, storage_status TEXT,
          visible_rows INTEGER, added_rows INTEGER, window_handle INTEGER,
          foreground INTEGER, minimized INTEGER, detail TEXT);
        CREATE TABLE IF NOT EXISTS capture_runs (
          id TEXT PRIMARY KEY, started_at TEXT, stopped_at TEXT, groups_json TEXT,
          failures INTEGER DEFAULT 0);
        ''')
        self.run_id = uuid.uuid4().hex
        with self.store.db:
            self.store.db.execute('INSERT INTO capture_runs(id,started_at,groups_json) VALUES (?,?,?)',
                                  (self.run_id, now(), json.dumps(self.groups, ensure_ascii=False)))
            for group in self.groups:
                self.store.db.execute('INSERT OR IGNORE INTO capture_groups(source,conversation) VALUES (?,?)', ('QQ', group))

    def notify(self, status, group=None, detail='', **extra):
        total = self.store.db.execute('SELECT count(*) FROM messages').fetchone()[0]
        selected = self.store.db.execute('SELECT count(*) FROM messages WHERE source=? AND conversation IN (%s)' % ','.join('?' * len(self.groups)), ['QQ', *self.groups]).fetchone()[0]
        self.emit(dict(status=status, group=group, detail=detail, failures=self.failures,
                       saved_total=total, selected_saved=selected, latest_capture=self.last_capture,
                       at=now(), **extra))

    def failure(self, group, reason):
        self.failures += 1
        at = now()
        with self.store.db:
            existing = self.store.db.execute('SELECT id FROM capture_gaps WHERE source=? AND conversation=? AND recovered_at IS NULL', ('QQ', group)).fetchone()
            if not existing:
                previous = self.store.db.execute('SELECT last_success FROM capture_groups WHERE source=? AND conversation=?', ('QQ', group)).fetchone()
                self.store.db.execute('INSERT INTO capture_gaps(source,conversation,started_at,reason) VALUES (?,?,?,?)', ('QQ', group, previous[0] or at, reason))
            self.store.db.execute('UPDATE capture_groups SET last_status=? WHERE source=? AND conversation=?', ('unavailable', 'QQ', group))
            self.store.db.execute('''INSERT INTO capture_observations
              (source,conversation,observed_at,client_received,ui_available,storage_status,detail)
              VALUES (?,?,?,'unknown',0,'not_saved',?)''', ('QQ', group, at, reason))
        self.store.event('continuous_unavailable', json.dumps({'group': group, 'reason': reason}, ensure_ascii=False))
        self.notify('unavailable', group, reason)

    def cycle(self):
        try:
            inventory = self.api('qq-windows')['result']
            if not inventory['desktop_accessible']:
                raise RuntimeError('输入桌面不可访问：可能锁屏/安全桌面/权限不足；不尝试绕过。')
            windows = inventory['windows']
        except Exception as exc:
            for group in self.groups:
                self.failure(group, str(exc))
            return
        for group in self.groups:
            if self.stop.is_set():
                return
            self.notify('reading', group, '只检查已暴露的界面，不自动打开或切换群聊')
            candidates, reasons = [], []
            for window in windows:
                if self.stop.is_set():
                    return
                try:
                    snapshot = self.api('read-qq', window['handle'], group)
                    rows = message_rows(snapshot, allowed={group})
                    candidates.append((snapshot, rows, window['handle']))
                except Exception as exc:
                    reasons.append(f"窗口 {window['handle']}: {exc}")
            if len(candidates) != 1:
                reason = '多个窗口同时匹配同名群，无法确认唯一来源；请只保留一个对应窗口。' if len(candidates) > 1 else '指定群未出现在可读取消息区域。' + ' | '.join(reasons)
                self.failure(group, reason)
                continue
            snapshot, rows, handle = candidates[0]
            r = snapshot['result']
            initialized = self.store.db.execute('SELECT initialized FROM capture_groups WHERE source=? AND conversation=?', ('QQ', group)).fetchone()[0]
            gap = self.store.db.execute('SELECT id FROM capture_gaps WHERE source=? AND conversation=? AND recovered_at IS NULL', ('QQ', group)).fetchone()
            status = 'recovered_visible_candidate' if gap else 'initial_visible' if not initialized else 'saved_ui_observation'
            for row in rows:
                row.update(capture_status=status, window_handle=handle,
                           client_received='unknown', ui_observed=True)
            # Message writes commit before success observation. A DB failure is never success.
            try:
                n = self.store.ingest(rows, baseline=not initialized and not self.include_existing)
                with self.store.db:
                    self.store.db.execute('UPDATE capture_groups SET initialized=1,last_success=?,last_status=? WHERE source=? AND conversation=?', (snapshot['collected_at'], 'read_ok', 'QQ', group))
                    if gap:
                        self.store.db.execute('UPDATE capture_gaps SET recovered_at=? WHERE id=?', (now(), gap[0]))
                    self.store.db.execute('''INSERT INTO capture_observations
                      (source,conversation,observed_at,client_received,ui_available,storage_status,
                       visible_rows,added_rows,window_handle,foreground,minimized,detail)
                      VALUES (?,?,?,'unknown',1,?,?,?,?,?,?,?)''',
                      ('QQ', group, snapshot['collected_at'], 'baseline_only' if not initialized and not self.include_existing else 'saved_or_already_seen', len(rows), n, handle, int(r['foreground']), int(r['minimized']), status))
                self.last_capture = snapshot['collected_at']
                self.last_group = group
                self.store.event('continuous_read_ok', json.dumps({'group': group, 'visible': len(rows), 'added': n, 'minimized': r['minimized']}, ensure_ascii=False))
                self.notify('read_ok', group, f'可读 {len(rows)} 行；本轮新增保存 {n} 行；QQ 收到消息时间未知', added=n,
                            visible_rows=len(rows), minimized=r['minimized'], foreground=r['foreground'], capture_status=status)
            except Exception as exc:
                self.failure(group, '本地保存失败: ' + str(exc))

    def run(self, seconds=None):
        self.open()
        deadline = time.monotonic() + seconds if seconds else None
        self.notify('started', detail='只采集所选群在 UIA 中实际暴露的消息')
        try:
            while not self.stop.is_set() and (deadline is None or time.monotonic() < deadline):
                self.cycle()
                delay = self.interval if deadline is None else min(self.interval, max(0, deadline-time.monotonic()))
                self.stop.wait(delay)
        finally:
            with self.store.db:
                self.store.db.execute('UPDATE capture_runs SET stopped_at=?,failures=? WHERE id=?', (now(), self.failures, self.run_id))
                for group in self.groups:
                    gap = self.store.db.execute('SELECT id FROM capture_gaps WHERE source=? AND conversation=? AND recovered_at IS NULL', ('QQ', group)).fetchone()
                    if not gap:
                        self.store.db.execute('INSERT INTO capture_gaps(source,conversation,started_at,reason) VALUES (?,?,?,?)', ('QQ', group, now(), 'collector_stopped; completeness after stop unknown'))
            self.notify('stopped', self.last_group, '已停止；中断期间完整性未知')
            self.store.db.close()


def import_wechat(text, conversation, sender=None, path=DB):
    if not text.strip() or not conversation.strip():
        raise ValueError('请填写来源会话和要手动导入的文字。')
    row = dict(dedup_key='manual:' + uuid.uuid4().hex, source='WeChat_manual',
               conversation=conversation.strip(), content=text.strip(), sender=sender or None,
               sent_at=None, sent_time_text=None, collected_at=datetime.now().astimezone().isoformat(),
               record_kind='manual_notification_text', capture_status='manual_import',
               provenance='user_provided_text', client_received='not_observed')
    store = Store(path)
    try:
        store.ingest([row]); store.event('manual_import', 'WeChat_manual; user supplied text; no automatic access')
    finally:
        store.db.close()
    return row['dedup_key']


def inspect_previews(groups, path=DB, api=probe):
    """Explicit diagnostic: store ONLY selected sidebar rows, never as full messages."""
    store=Store(path)
    store.db.execute('''CREATE TABLE IF NOT EXISTS conversation_previews (
      id INTEGER PRIMARY KEY, conversation TEXT, observed_at TEXT, raw_parts_json TEXT,
      window_handle INTEGER, minimized INTEGER, kind TEXT DEFAULT 'preview_only')''')
    results=[]
    try:
        inventory=api('qq-windows')['result']
        if not inventory['desktop_accessible']: raise RuntimeError('输入桌面不可访问，不采集预览')
        for group in dict.fromkeys(groups):
            record={'conversation':group,'complete_message':False,'client_received_at':None}
            for window in inventory['windows']:
                try:
                    snap=api('read-preview',window['handle'],group); r=snap['result']
                    if r['truncated'] or any('error' in n for n in r['nodes']): raise ValueError('预览不完整')
                    parts=[n['name'] for n in r['nodes'] if n.get('name') and n['type']=='ControlType.Text']
                    with store.db:
                        store.db.execute('INSERT INTO conversation_previews(conversation,observed_at,raw_parts_json,window_handle,minimized) VALUES (?,?,?,?,?)', (group,snap['collected_at'],json.dumps(parts,ensure_ascii=False),window['handle'],int(r['minimized'])))
                    record.update(status='preview_saved',parts=parts,minimized=r['minimized'],at=snap['collected_at'])
                    break
                except Exception as exc: record.update(status='unavailable',error=str(exc))
            results.append(record)
        return results
    finally: store.db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--group', action='append', required=True)
    p.add_argument('--interval', type=float, default=5)
    p.add_argument('--seconds', type=float)
    p.add_argument('--baseline-only', action='store_true')
    p.add_argument('--log', type=Path)
    p.add_argument('--preview-only', action='store_true', help='只检查所选群的会话列表预览，结果不计入完整消息')
    args = p.parse_args()
    if args.preview_only:
        results=inspect_previews(args.group)
        if args.log: args.log.write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf-8')
        print(json.dumps(results,ensure_ascii=True)); return
    def emit(event):
        line = json.dumps(event, ensure_ascii=True)
        print(line, flush=True)
        if args.log:
            with args.log.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False)+'\n')
    worker = Continuous(args.group, args.interval, not args.baseline_only, emit)
    try:
        worker.run(args.seconds)
    except KeyboardInterrupt:
        worker.stop.set()


if __name__ == '__main__':
    main()
