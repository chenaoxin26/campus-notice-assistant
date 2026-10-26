import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from test_collector import sample
from collector import Store, message_rows
from continuous import Continuous, import_wechat, inspect_previews


class ContinuousTests(unittest.TestCase):
    def setUp(self):
        work = Path(tempfile.gettempdir())
        work.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'records.sqlite3'
        self.active='测试群3'
        self.events=[]

    def api(self,*args):
        if args[0]=='qq-windows':
            return {'result':{'desktop_accessible':True,'windows':[{'handle':42}]}}
        if args[2]!=self.active: raise RuntimeError('exact target composer missing')
        snapshot=sample(); snapshot['result']['conversation']=self.active
        return snapshot

    def engine(self,groups,**kwargs):
        e=Continuous(groups,db=self.path,emit=self.events.append,api=self.api,**kwargs)
        e.open(); self.addCleanup(e.store.db.close)
        return e

    def test_only_exposed_selected_group_saved(self):
        e=self.engine(['测试群','测试群3'])
        e.cycle()
        self.assertEqual(e.store.db.execute('SELECT DISTINCT conversation FROM messages').fetchall(), [('测试群3',)])
        observations=e.store.db.execute('SELECT conversation,client_received,ui_available FROM capture_observations').fetchall()
        self.assertEqual(observations,[('测试群','unknown',0),('测试群3','unknown',1)])
        self.assertEqual(e.failures,1)

    def test_gap_recovery_never_claims_completeness(self):
        e=self.engine(['测试群2'])
        e.cycle(); self.active='测试群2'; e.cycle(); e.cycle()
        gap=e.store.db.execute('SELECT recovered_at,completeness FROM capture_gaps').fetchone()
        self.assertIsNotNone(gap[0]); self.assertEqual(gap[1],'unknown')
        self.assertEqual(e.store.db.execute('SELECT count(*) FROM messages').fetchone()[0],2)
        self.assertEqual(e.store.db.execute('SELECT DISTINCT capture_status FROM messages').fetchall(), [('recovered_visible_candidate',)])

    def test_restart_does_not_repeat_baseline(self):
        e=self.engine(['测试群3'],include_existing=False); e.cycle()
        self.assertEqual(e.store.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        e.store.db.close()
        e2=self.engine(['测试群3'],include_existing=False)
        original=self.api
        def changed(*args):
            snap=original(*args)
            if args[0]=='read-qq': snap['result']['nodes'][-1]['name']='中断期间新文字'
            return snap
        e2.api=changed; e2.cycle()
        self.assertEqual(e2.store.db.execute('SELECT count(*) FROM messages').fetchone()[0],1)

    def test_manual_import_provenance_and_identical_text_preserved(self):
        import_wechat('相同通知','文件传输助手','甲',self.path)
        import_wechat('相同通知','文件传输助手','乙',self.path)
        with sqlite3.connect(self.path) as db:
            rows=db.execute('SELECT source,sender,sent_at,capture_status FROM messages').fetchall()
        db.close()
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r[0]=='WeChat_manual' and r[2] is None and r[3]=='manual_import' for r in rows))

    def test_sender_and_time_like_body_are_not_erased(self):
        snapshot=sample()
        snapshot['result']['nodes'][5]['name']='10:30'
        for i,nickname in enumerate(['甲','乙']):
            base=4+i*3
            snapshot['result']['nodes'].append({'index':100+i,'parent':base,'type':'ControlType.Custom','name':nickname})
        rows=message_rows(snapshot)
        self.assertEqual({r['sender'] for r in rows},{'甲','乙'})
        self.assertNotEqual(rows[0]['dedup_key'],rows[1]['dedup_key'])
        self.assertIn('10:30',rows[0]['content'])
        self.assertIsNone(rows[0]['sent_at'])

    def test_inaccessible_desktop_records_failure_without_reading(self):
        e=self.engine(['测试群3'])
        calls=[]
        def locked(*args):
            calls.append(args)
            return {'result':{'desktop_accessible':False,'windows':[{'handle':42}]}}
        e.api=locked; e.cycle()
        self.assertEqual(calls,[('qq-windows',)])
        self.assertEqual(e.store.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(e.failures,1)

    def test_ambiguous_same_name_windows_are_refused(self):
        e=self.engine(['测试群3'])
        original=self.api
        def two_windows(*args):
            if args[0]=='qq-windows':
                return {'result':{'desktop_accessible':True,'windows':[{'handle':1},{'handle':2}]}}
            return original(*args)
        e.api=two_windows; e.cycle()
        self.assertEqual(e.store.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        self.assertEqual(e.failures,1)

    def test_previews_never_count_as_full_messages(self):
        def api(*args):
            if args[0]=='qq-windows': return {'result':{'desktop_accessible':True,'windows':[{'handle':42}]}}
            return {'collected_at':'2026-10-26T10:00:00+08:00','result':{'truncated':False,'minimized':True,'nodes':[{'type':'ControlType.Text','name':args[2]},{'type':'ControlType.Text','name':'最后一条摘要'}]}}
        result=inspect_previews(['测试群3'],self.path,api)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM conversation_previews').fetchone()[0],1)
        db.close()
        self.assertFalse(result[0]['complete_message'])


if __name__=='__main__': unittest.main()
