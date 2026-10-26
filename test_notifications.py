import json,tempfile,unittest
from pathlib import Path
from core import CampusDB
from notification_source import NotificationSource

def row(**kw):
    r=dict(notification_id=1,notification_created_at='2026-10-26T05:00:00Z',app_id='QQ',app_name='QQ',source_verified=True,identity_error=None,group='测试群',body='请提交实验报告，时间地点另行通知。',texts=['测试群','请提交实验报告，时间地点另行通知。'],scope_verified=True)
    r.update(kw);return r
class AI:
    def extract(self,n):return {'tasks':[dict(title='提交实验报告',category='学习任务',deadline_text='待确认',location='待确认',requirements=[],steps=[],importance='待确认',evidence='请提交实验报告',is_update=False)]}
class Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.db=CampusDB(Path(self.t.name)/'a.sqlite3')
        self.source=NotificationSource(self.db,{'groups':['测试群'],'qq_app_ids':[]},Path(self.t.name)/'settings.json','no-runtime')
    def test_same_os_card_not_duplicated(self):
        self.assertEqual(self.source.ingest([row()]),1);self.assertEqual(self.source.ingest([row()]),0);self.assertEqual(len(self.db.snapshot()['notices']),1)
    def test_identical_body_different_os_ids_retained(self):
        self.source.ingest([row(),row(notification_id=2)]);self.db.analyze(AI());self.assertEqual(len(self.db.snapshot()['notices']),2);self.assertEqual(len(self.db.snapshot()['tasks']),2)
    def test_updated_card_retains_revisions(self):
        self.source.ingest([row(),row(body='请提交实验报告，更正日期待通知。',texts=['测试群','请提交实验报告，更正日期待通知。'])]);self.assertEqual(self.source.snapshot()['saved_cards'],2)
    def test_non_selected_groups_rejected(self):
        self.source.ingest([row(group='非授权群')]);self.assertEqual(self.db.snapshot()['notices'],[])
    def test_other_apps_not_collected(self):
        self.source.ingest([row(app_id='other',app_name='其他应用')]);self.assertEqual(self.source.snapshot()['saved_cards'],0)
    def test_unknown_app_not_analyzed_until_confirmed(self):
        self.source.ingest([row(app_id=None,app_name=None,source_verified=False,identity_error='unavailable')]);self.assertEqual(self.db.pending(),[])
        key=self.source.snapshot()['cards'][0]['event_key'];self.source.confirm(key);self.assertEqual(len(self.db.pending()),1)
    def test_notification_time_not_used_as_send_time(self):
        self.source.ingest([row()]);notice=self.db.snapshot()['notices'][0];self.assertIsNone(notice['sent_at']);self.assertIsNone(notice['sent_time_text'])
    def test_notification_task_always_reviewed(self):
        self.source.ingest([row()]);self.db.analyze(AI());t=self.db.snapshot()['tasks'][0];self.assertTrue(t['needs_review']);self.assertTrue(any('Windows通知' in r for r in t['review_reasons']));self.assertEqual(t['deadline_text'],'待确认')
    def test_completed_task_survives_poll_and_restart(self):
        self.source.ingest([row()]);self.db.analyze(AI());tid=self.db.snapshot()['tasks'][0]['id'];self.db.edit_task(tid,{'status':'done'});self.source.ingest([row()]);self.db.analyze(AI());self.assertEqual(CampusDB(self.db.path).snapshot()['counts']['done'],1)
    def test_notification_reader_never_navigates_qq(self):
        text=Path(__file__).with_name('notification_source.py').read_text('utf-8')
        self.assertNotIn('rotation.py',text);self.assertNotIn('Probe.exe',text);self.assertNotIn('activate-qq',text)

if __name__=='__main__':unittest.main(verbosity=2)
