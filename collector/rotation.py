"""Selected QQ groups: preview-triggered, idle-gated normal UIA navigation.
Opening conversations can clear unread state or bring QQ forward. Never sends input.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import time
from collector import ROOT, DATA_ROOT, message_rows, probe
from continuous import Continuous, DB, now


def preview_parts(snapshot):
    r = snapshot['result']
    if r.get('complete_message') is not False or r.get('record_kind') != 'conversation_preview_only':
        raise ValueError('不是会话列表摘要，拒绝用于触发判断')
    if r['truncated'] or any('error' in n for n in r['nodes']):
        raise ValueError('会话摘要不完整')
    return [n['name'] for n in r['nodes'] if n.get('name') and n['type'] == 'ControlType.Text']


def fingerprint(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def latest_hint(parts):
    # Conservative text hint only; never an unread count or a full-message substitute.
    candidates=[p.strip() for p in parts[1:] if len(p.strip())>=4
        and not p.strip().endswith(('：',':'))
        and not p.strip().startswith('[特别关心]')
        and not re.fullmatch(r'[\d:/ .-]+',p.strip())]
    return candidates[-1].rstrip('…').removesuffix('...') if candidates else None


@contextmanager
def rotation_lock():
    import msvcrt
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    stream = (DATA_ROOT/'rotation.lock').open('a+b')
    if stream.tell() == 0:
        stream.write(b'0'); stream.flush()
    stream.seek(0)
    try:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        stream.close()
        raise RuntimeError('已有自动轮读实例运行；请先停止它。')
    try:
        yield
    finally:
        stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1); stream.close()


class Rotation(Continuous):
    def __init__(self, groups, interval=5, idle_seconds=60, sweep_seconds=120,
                 mode='watch', emit=None, db=DB, api=probe, monitor=True, evidence=None,
                 restore_minimized=False, trial_folder=None):
        super().__init__(groups, interval, True, emit, db, api)
        if idle_seconds < 0 or sweep_seconds < 0:
            raise ValueError('空闲阈值/定期补扫间隔不能为负')
        self.idle_seconds, self.sweep_seconds, self.mode = idle_seconds, sweep_seconds, mode
        self.monitor = monitor
        self.restore_minimized = restore_minimized
        self.trial_folder = Path(trial_folder) if trial_folder else None
        if self.trial_folder:
            manifest = json.loads((self.trial_folder/'manifest.json').read_text('utf-8'))
            if set(manifest['groups']) != set(self.groups):
                raise ValueError('真实测试群与本轮白名单不一致')
        self.evidence = Path(evidence) if evidence else DATA_ROOT/'evidence'/'stage3'/'runs'
        self.targets = {g: {'hash': None, 'full_at': 0.0, 'attempt_at': 0.0, 'ever_read': False} for g in self.groups}
        self.power = None
        self.power_stream = None

    def open(self):
        super().open()
        self.folder = self.evidence/self.run_id
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.trial_folder:
            path = self.trial_folder/'manifest.json'
            manifest = json.loads(path.read_text('utf-8'))
            manifest['run_ids'] = list(dict.fromkeys([*manifest['run_ids'], self.run_id]))
            path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
        self.store.db.executescript('''
        CREATE TABLE IF NOT EXISTS rotation_events (
          id INTEGER PRIMARY KEY, run_id TEXT, at TEXT, conversation TEXT,
          event TEXT, detail_json TEXT);
        CREATE TABLE IF NOT EXISTS rotation_reads (
          id INTEGER PRIMARY KEY, run_id TEXT, at TEXT, conversation TEXT,
          rows_read INTEGER, rows_added INTEGER, minimized INTEGER,
          foreground INTEGER, display_state TEXT, evidence_path TEXT);
        CREATE TABLE IF NOT EXISTS rotation_observed_messages (
          id INTEGER PRIMARY KEY, run_id TEXT, conversation TEXT, dedup_key TEXT,
          observed_at TEXT, minimized INTEGER, foreground INTEGER,
          display_state TEXT, metadata_json TEXT);
        ''')
        if self.monitor:
            self.power_stream = (self.folder/'power-events.jsonl').open('w', encoding='utf-8')
            try:
                self.power = subprocess.Popen([str(ROOT/'PowerWatch.exe'), str(self.folder/'power-state.json')],
                    stdout=self.power_stream, stderr=self.power_stream,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as exc:
                self.event('power_observer_unavailable', detail={'reason': str(exc)})
        self.event('rotation_start', detail={'groups': self.groups, 'mode': self.mode,
            'idle_seconds': self.idle_seconds, 'sweep_seconds': self.sweep_seconds,
            'restore_minimized': self.restore_minimized,
            'unread_may_change': True, 'no_complete_coverage_claim': True})
        (self.folder/'run.json').write_text(json.dumps({'run_id': self.run_id, 'groups': self.groups,
            'mode': self.mode, 'started_at': now(), 'idle_seconds': self.idle_seconds,
            'code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                ['rotation.py','collector.py','continuous.py','Probe.cs','Probe.exe','PowerWatch.cs','PowerWatch.exe']}}, ensure_ascii=False, indent=2), 'utf-8')

    def event(self, kind, group=None, detail=None):
        detail = detail or {}
        record = {'at': now(), 'run_id': self.run_id, 'event': kind, 'group': group, **detail}
        with self.store.db:
            self.store.db.execute('INSERT INTO rotation_events(run_id,at,conversation,event,detail_json) VALUES (?,?,?,?,?)',
                (self.run_id, record['at'], group, kind, json.dumps(detail, ensure_ascii=False)))
        with (self.folder/'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False)+'\n')
        self.emit(record)

    def power_state(self):
        try:
            state = json.loads((self.folder/'power-state.json').read_text('utf-8-sig'))
            age = (datetime.fromisoformat(now())-datetime.fromisoformat(state['observed_at'])).total_seconds()
            if age < 0 or age > 8:
                return {'display_state': 'unknown', 'reason': 'observer_heartbeat_stale', 'age_seconds': age}
            return state
        except (OSError, ValueError, KeyError):
            return {'display_state': 'unknown', 'reason': 'no_verified_display_notification'}

    def read(self, handle, group, reason, preview_hash, hint=None):
        self.targets[group]['attempt_at'] = time.monotonic()
        try:
            # Read first: if this group is already exposed, no navigation is necessary.
            try:
                snapshot = self.api('read-qq', handle, group)
            except Exception:
                desktop = self.api('desktop-state')['result']
                if not desktop['desktop_accessible']:
                    raise RuntimeError('输入桌面不可访问；不切换、不解锁')
                if desktop['idle_ms'] < self.idle_seconds*1000 or desktop['idle_ms'] < 0:
                    self.event('deferred_user_active', group, {'idle_ms': desktop['idle_ms'], 'reason': reason})
                    self.failure(group, '用户正在使用桌面，延后会话切换；期间完整性未知')
                    return False
                before = self.api('read-preview', handle, group)
                if before['result']['minimized'] and self.restore_minimized:
                    desktop = self.api('desktop-state')['result']
                    if not desktop['desktop_accessible'] or desktop['idle_ms'] < self.idle_seconds*1000:
                        raise RuntimeError('恢复最小化前桌面/空闲状态变化，取消恢复')
                    self.event('restore_minimized_requested', group, {'unread_may_change': True,
                        'note': '后续读取不算最小化成功；可能前置QQ'})
                    self.api('state', handle, 'restore')
                    if self.stop.wait(.8): return False
                self.event('navigation_attempt', group, {'trigger': reason, 'unread_may_change': True,
                    'preview_before': preview_parts(before), 'power': self.power_state()})
                outcome = self.api('activate-qq', handle, group, self.idle_seconds)
                self.event('navigation_invoked', group, outcome['result'])
                snapshot = None
                for attempt in range(3):
                    if self.stop.wait(.8):
                        return False
                    try:
                        snapshot = self.api('read-qq', handle, group)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                try:
                    after = preview_parts(self.api('read-preview', handle, group))
                    self.event('preview_after_navigation', group, {'parts': after,
                        'unread_change_confirmed': None, 'note': '预览变化不能证明准确未读数量'})
                except Exception as exc:
                    self.event('preview_after_unavailable', group, {'reason': str(exc)})
            rows = message_rows(snapshot, allowed={group})
            reconciled = None if not hint else any(''.join(hint.split()) in ''.join(row['content'].split()) for row in rows)
            power = self.power_state()
            for row in rows:
                row.update(capture_status='evening_visible_history' if self.mode == 'evening' else 'rotated_ui_observation',
                    capture_run_id=self.run_id, client_received='unknown', ui_observed=True,
                    power_observation=power, window_handle=handle)
            added = self.store.ingest(rows, materialize_baseline=self.mode == 'evening')
            stamp = snapshot['collected_at']
            r = snapshot['result']
            filename = hashlib.sha256((group+stamp).encode()).hexdigest()[:20]+'.json'
            path = self.folder/filename
            path.write_text(json.dumps(snapshot, ensure_ascii=False), 'utf-8')
            with self.store.db:
                self.store.db.execute('INSERT INTO rotation_reads(run_id,at,conversation,rows_read,rows_added,minimized,foreground,display_state,evidence_path) VALUES (?,?,?,?,?,?,?,?,?)',
                    (self.run_id, stamp, group, len(rows), added, int(r['minimized']), int(r['foreground']), power['display_state'], str(path)))
                for row in rows:
                    # Includes rows already present in messages: they were observed in THIS run.
                    self.store.db.execute('INSERT INTO rotation_observed_messages(run_id,conversation,dedup_key,observed_at,minimized,foreground,display_state,metadata_json) VALUES (?,?,?,?,?,?,?,?)',
                        (self.run_id, group, row['dedup_key'], stamp, int(r['minimized']), int(r['foreground']), power['display_state'], json.dumps(row, ensure_ascii=False)))
                self.store.db.execute('UPDATE capture_groups SET initialized=1,last_success=?,last_status=? WHERE source=? AND conversation=?', (stamp, 'rotation_read_ok', 'QQ', group))
                if reconciled is not False:
                    self.store.db.execute('UPDATE capture_gaps SET recovered_at=? WHERE source=? AND conversation=? AND recovered_at IS NULL', (now(), 'QQ', group))
            self.last_group, self.last_capture = group, stamp
            self.targets[group].update(full_at=time.monotonic(), ever_read=True)
            if reconciled is not False: self.targets[group]['hash']=preview_hash
            self.event('full_message_read_saved', group, {'visible_rows': len(rows), 'added_rows': added,
                'minimized': r['minimized'], 'foreground': r['foreground'], 'power': power,
                'client_received': 'unknown', 'coverage_complete': None,
                'preview_hint_reconciled': reconciled})
            if reconciled is False:
                self.event('latest_preview_not_in_full_list', group, {'hint': hint,
                    'note': '正文区可读但未见摘要线索，不清除待采触发，也不声称补齐'})
                self.failure(group,'正文区可读，但未见摘要新文字；可能为旧界面或尚未加载，完整性未知')
            return reconciled is not False
        except Exception as exc:
            self.event('read_failed', group, {'reason': str(exc)})
            self.failure(group, str(exc))
            return False

    def cycle(self):
        try:
            inv = self.api('qq-windows')['result']
            if not inv['desktop_accessible']:
                raise RuntimeError('输入桌面不可访问，不绕过锁屏')
            if len(inv['windows']) != 1:
                raise RuntimeError('自动轮读要求唯一 QQ 顶层窗口；当前数量='+str(len(inv['windows'])))
            handle = inv['windows'][0]['handle']
        except Exception as exc:
            for group in self.groups:
                self.failure(group, str(exc))
            self.event('cycle_unavailable', detail={'reason': str(exc)})
            return
        pending = []
        for group in self.groups:
            if self.stop.is_set():
                return
            state = self.targets[group]
            try:
                snap = self.api('read-preview', handle, group)
                parts = preview_parts(snap)
                digest = fingerprint(parts)
                hint = latest_hint(parts)
                # Numeric text can also be a message body. Never call it a verified badge count.
                self.event('selected_preview', group, {'parts': parts,
                    'numeric_candidates_not_verified_unread': [p for p in parts[1:] if p.isdecimal()],
                    'complete_message': False, 'minimized': snap['result']['minimized']})
            except Exception as exc:
                self.event('preview_unavailable', group, {'reason': str(exc)})
                digest = None
                hint = None
            reason = 'initial_sweep' if not state['ever_read'] else 'preview_changed' if digest != state['hash'] else 'periodic_sweep' if time.monotonic()-state['full_at'] >= self.sweep_seconds else None
            if reason:
                pending.append((state['attempt_at'], group, reason, digest, hint))
        # Fair rotation. Only one possible navigation per cycle to reduce interference.
        if pending:
            _, group, reason, digest, hint = min(pending, key=lambda x: x[0])
            self.read(handle, group, reason, digest, hint)
        else:
            self.event('no_pending_change', detail={'groups': self.groups})

    def run(self, seconds=None):
        try:
            super().run(seconds)
        finally:
            if self.power:
                self.power.terminate()
                try: self.power.wait(timeout=5)
                except subprocess.TimeoutExpired: self.power.kill(); self.power.wait()
            if self.power_stream: self.power_stream.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--group', action='append')
    p.add_argument('--groups-file', type=Path, help='使用原中文界面保存的selected列表；不读取candidates列表')
    p.add_argument('--mode', choices=['watch', 'evening'], default='watch')
    p.add_argument('--seconds', type=float, default=300)
    p.add_argument('--interval', type=float, default=5)
    p.add_argument('--idle-seconds', type=int, default=60)
    p.add_argument('--sweep-seconds', type=float, default=120)
    p.add_argument('--stop-file', type=Path)
    p.add_argument('--restore-minimized', action='store_true', help='允许把QQ从最小化恢复；读取成功不算最小化测试通过')
    p.add_argument('--trial-folder', type=Path, help='将本轮run_id关联到真实测试清单')
    p.add_argument('--summary', action='store_true', help='控制台只显示简要状态；详细日志仍写入证据目录')
    args = p.parse_args()
    if args.seconds <= 0: p.error('seconds must be positive')
    if args.groups_file:
        if args.group: p.error('group与groups-file只能选一个')
        args.group=json.loads(args.groups_file.read_text('utf-8-sig')).get('selected',[])
    if not args.group or not all(isinstance(g,str) and g.strip() for g in args.group):
        p.error('请明确指定测试群；也可以先在原中文界面选择并开始/停止一次，以保存selected列表')
    def emit(event):
        if not args.summary:
            print(json.dumps(event,ensure_ascii=True),flush=True)
        elif event.get('event') in {'rotation_start','full_message_read_saved','read_failed','deferred_user_active','latest_preview_not_in_full_list'}:
            print(f"{event.get('at','')} {event.get('group') or ''} {event.get('event')} "+
                (f"可读{event['visible_rows']}行，本轮新增{event['added_rows']}行" if 'visible_rows' in event else str(event.get('reason',''))),flush=True)
        elif event.get('status')=='stopped': print('采集已停止。',flush=True)
    print('注意：自动打开会话可能前置QQ并改变未读状态；不发送或修改消息。', flush=True)
    worker = Rotation(args.group, args.interval, args.idle_seconds, args.sweep_seconds,
        args.mode, emit=emit,
        restore_minimized=args.restore_minimized, trial_folder=args.trial_folder)
    if args.mode == 'evening':
        # A single pass still respects idle gating and independently logs each failed group.
        with rotation_lock():
            worker.open()
            try:
                inv = worker.api('qq-windows')['result']
                if not inv['desktop_accessible'] or len(inv['windows']) != 1:
                    raise RuntimeError('需要可访问桌面和唯一QQ窗口')
                for group in worker.groups:
                    worker.read(inv['windows'][0]['handle'], group, 'explicit_evening_pass', None)
            finally:
                with worker.store.db:
                    worker.store.db.execute('UPDATE capture_runs SET stopped_at=?,failures=? WHERE id=?', (now(), worker.failures, worker.run_id))
                worker.store.db.close()
                if worker.power: worker.power.terminate(); worker.power.wait(timeout=5)
                if worker.power_stream: worker.power_stream.close()
    else:
        if args.stop_file:
            import threading
            def stop_on_file():
                while not worker.stop.wait(.5):
                    if args.stop_file.exists(): worker.stop.set(); return
            threading.Thread(target=stop_on_file, daemon=True).start()
        try:
            with rotation_lock(): worker.run(args.seconds)
        except KeyboardInterrupt: worker.stop.set()


if __name__ == '__main__': main()
