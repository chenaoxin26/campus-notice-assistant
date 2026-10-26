import json
import csv
from pathlib import Path
import tempfile
import unittest
from test_collector import sample
from rotation import Rotation
from trial import evaluate, confirm


class NoWait:
    def is_set(self): return False
    def wait(self, seconds): return False


class RotationTests(unittest.TestCase):
    def setUp(self):
        work = Path(tempfile.gettempdir()); work.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work); self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name); self.db=self.folder/'messages.db'
        self.active='三'; self.idle=100000; self.freeze=False; self.calls=[]
        self.previews={g:'相同内容' for g in ['一','二','三']}
        self.events=[]

    def api(self,*args):
        self.calls.append(args)
        if args[0]=='qq-windows': return {'result':{'desktop_accessible':True,'windows':[{'handle':1}]}}
        if args[0]=='desktop-state': return {'result':{'desktop_accessible':True,'idle_ms':self.idle,'foreground_handle':999}}
        if args[0]=='read-preview':
            return {'result':{'truncated':False,'record_kind':'conversation_preview_only','complete_message':False,'minimized':False,
                'nodes':[{'type':'ControlType.Text','name':args[2]},{'type':'ControlType.Text','name':self.previews[args[2]]}]}}
        if args[0]=='activate-qq':
            if not self.freeze: self.active=args[2]
            return {'result':{'conversation':args[2],'invoked':True,'selection_verified':False}}
        if args[0]=='read-qq':
            if args[2]!=self.active: raise RuntimeError('target composer missing')
            snap=sample();snap['result']['conversation']=args[2]; return snap
        raise AssertionError(args)

    def engine(self,groups):
        e=Rotation(groups,db=self.db,api=self.api,monitor=False,evidence=self.folder/'evidence',emit=self.events.append)
        e.open();e.stop=NoWait();self.addCleanup(e.store.db.close);return e

    def test_idle_gate_and_fair_queue(self):
        self.idle=0
        e=self.engine(['一','二','三'])
        for _ in range(3): e.cycle()
        self.assertFalse(any(c[0]=='activate-qq' for c in self.calls))
        self.assertEqual(e.store.db.execute('SELECT DISTINCT conversation FROM rotation_reads').fetchall(),[('三',)])
        deferred=[x['group'] for x in self.events if x.get('event')=='deferred_user_active']
        self.assertEqual(deferred,['一','二'])

    def test_invoke_is_not_read_success(self):
        self.freeze=True
        e=self.engine(['一']);e.cycle()
        self.assertTrue(any(c[0]=='activate-qq' for c in self.calls))
        self.assertEqual(e.store.db.execute('SELECT count(*) FROM rotation_reads').fetchone()[0],0)
        self.assertTrue(any(x.get('event')=='read_failed' for x in self.events))

    def test_changed_preview_routes_only_that_group(self):
        e=self.engine(['一','二','三'])
        for _ in range(3): e.cycle()
        count=len([x for x in self.events if x.get('event')=='full_message_read_saved'])
        e.cycle()
        self.assertEqual(len([x for x in self.events if x.get('event')=='full_message_read_saved']),count)
        self.previews['二']='另一条摘要'
        e.cycle()
        attempts=[x for x in self.events if x.get('event')=='navigation_attempt']
        self.assertEqual(attempts[-1]['group'],'二')
        self.assertEqual(attempts[-1]['trigger'],'preview_changed')

    def test_ground_truth_counts_only_confirmed_full_bodies_in_run(self):
        e=self.engine(['一'])
        manifest={'trial_id':'fixture','groups':['一'],'run_ids':[e.run_id],'expected':[
            {'token':'TOKEN-A','group':'一','phase':'A','condition':'minimized','send_status':'confirmed_sent'},
            {'token':'TOKEN-B','group':'一','phase':'B','condition':'display_off','send_status':'confirmed_sent'},
            {'token':'TOKEN-C','group':'一','phase':'B','condition':'display_off','send_status':'planned'}]}
        trial=self.folder/'trial';trial.mkdir();(trial/'manifest.json').write_text(json.dumps(manifest),'utf-8')
        row=dict(dedup_key='key',source='QQ',conversation='一',content='TOKEN-A',sender='甲',sent_at=None,
            sent_time_text=None,collected_at='2026-10-26T12:00:00+08:00',record_kind='text_ui_message')
        e.store.ingest([row])
        for _ in range(2):
            e.store.db.execute('INSERT INTO rotation_observed_messages(run_id,conversation,dedup_key,observed_at,minimized,foreground,display_state,metadata_json) VALUES (?,?,?,?,?,?,?,?)',
                (e.run_id,'一','key',row['collected_at'],1,0,'on',json.dumps(row)))
        e.store.db.commit()
        result=evaluate(trial,db=self.db)
        self.assertEqual(result['summary']['confirmed_expected'],2)
        self.assertEqual(result['summary']['success'],1)
        self.assertEqual(result['summary']['missing'],1)
        self.assertEqual(result['summary']['condition_observed'],1)
        self.assertEqual(result['summary']['unconfirmed'],1)
        self.assertIsNone(result['summary']['overall_missing_count'])

    def test_stale_full_list_does_not_clear_new_preview_trigger(self):
        e=self.engine(['三']); self.previews['三']='新出现的唯一文字'
        e.cycle()
        self.assertIsNone(e.targets['三']['hash'])
        self.assertEqual(e.store.db.execute('SELECT completeness FROM capture_gaps').fetchone()[0],'unknown')
        self.assertTrue(any(x.get('event')=='latest_preview_not_in_full_list' for x in self.events))

    def test_confirmation_csv_is_synced_and_unknown_tokens_rejected(self):
        folder=self.folder/'trial';folder.mkdir()
        row={'token':'TOKEN-A','group':'一','phase':'A','condition':'minimized','send_status':'planned','sent_at':'','note':''}
        (folder/'manifest.json').write_text(json.dumps({'expected':[row]}),'utf-8')
        confirm(folder,['TOKEN-A'])
        with (folder/'发送核对表.csv').open(encoding='utf-8-sig',newline='') as stream:
            self.assertEqual(next(csv.DictReader(stream))['send_status'],'confirmed_sent')
        with self.assertRaises(ValueError): confirm(folder,['UNKNOWN'])

    def test_evening_and_watch_cannot_be_combined_for_pass_rate(self):
        e=self.engine(['一'])
        with e.store.db:
            e.store.db.execute('INSERT INTO capture_runs(id,started_at,groups_json) VALUES (?,?,?)',('evening','2026-10-26T12:00:00+08:00','[]'))
            e.store.db.execute('INSERT INTO rotation_events(run_id,event,detail_json) VALUES (?,?,?)',('evening','rotation_start',json.dumps({'mode':'evening'})))
        folder=self.folder/'trial';folder.mkdir()
        (folder/'manifest.json').write_text(json.dumps({'trial_id':'mixed','expected':[],'run_ids':[e.run_id,'evening']}),'utf-8')
        with self.assertRaisesRegex(ValueError,'不能将'): evaluate(folder,db=self.db)


if __name__=='__main__': unittest.main()
