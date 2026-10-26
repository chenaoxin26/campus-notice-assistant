import tempfile
import unittest
from pathlib import Path
from core import CampusDB,clean_tasks

BODY='请提交实验报告，截止2026年10月30日18:00，地点教一楼203。先填写封面，再交给学委。'
def task(body=BODY,**kw):
    r=dict(title='提交实验报告',category='学习任务',deadline_text='2026年10月30日18:00',location='教一楼203',requirements=['提交实验报告'],steps=['先填写封面','再交给学委'],importance='待确认',evidence=body,is_update=False)
    r.update(kw);return r
class FakeAI:
    def __init__(self,tasks):self.tasks=tasks;self.calls=0
    def extract(self,n):self.calls+=1;return {'tasks':self.tasks}
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'test.sqlite3';self.db=CampusDB(self.path)
    def tearDown(self):self.tmp.cleanup()
    def notice(self,body=BODY,sender='张同学',**kw):return self.db.add_notice('QQ','测试群',sender,body,**kw)
    def analyze(self,items=None):return self.db.analyze(FakeAI([task()] if items is None else items))
    def test_gossip(self):
        self.notice('今天食堂不错哈哈');self.analyze([]);self.assertEqual(self.db.snapshot()['tasks'],[])
    def test_multiple_tasks(self):
        self.notice();self.analyze([task(),task(title='填写封面')]);self.assertEqual(len(self.db.snapshot()['tasks']),2)
    def test_stable_source(self):
        self.notice(source_key='x');self.notice(source_key='x');self.analyze();self.assertEqual(len(self.db.snapshot()['notices']),1)
    def test_duplicate_preserves_raw(self):
        self.notice();self.analyze();self.notice();r=self.analyze();s=self.db.snapshot();self.assertEqual(len(s['tasks']),1);self.assertEqual(len(s['notices']),2);self.assertEqual(r['duplicates'],1)
    def test_different_sender_preserved(self):
        self.notice();self.notice(sender='李同学');self.analyze();self.assertEqual(len(self.db.snapshot()['tasks']),2)
    def test_no_invented_fields(self):
        body='请提交实验报告，时间地点另行通知。';r=clean_tasks(body,{'tasks':[task(body)]})[0]
        self.assertEqual(r['deadline_text'],'待确认');self.assertIsNone(r['deadline_iso']);self.assertEqual(r['location'],'待确认');self.assertEqual(r['steps'],['待确认'])
    def test_relative_time_not_normalized(self):
        body='请明天提交报告';r=clean_tasks(body,{'tasks':[task(body,deadline_text='明天')]})[0];self.assertIsNone(r['deadline_iso']);self.assertTrue(r['needs_review'])
    def test_update_requires_review(self):
        self.notice();self.analyze();body='更正：实验报告延期到2026年11月2日，请提交实验报告。';self.notice(body);self.analyze([task(body,deadline_text='2026年11月2日')]);s=self.db.snapshot();self.assertEqual(len(s['tasks']),2);self.assertTrue(any('可能修改' in ''.join(t['review_reasons']) for t in s['tasks']));self.assertTrue(any(t['deadline_text']=='2026年10月30日18:00' for t in s['tasks']))
    def test_completed_not_reopened(self):
        self.notice();self.analyze();t=self.db.snapshot()['tasks'][0];self.db.edit_task(t['id'],{'status':'done'});self.notice();self.analyze();self.assertEqual(self.db.snapshot()['counts']['done'],1);self.assertEqual(self.db.snapshot()['counts']['open'],0)
    def test_deleted_not_resurrected(self):
        self.notice();self.analyze();self.db.edit_task(self.db.snapshot()['tasks'][0]['id'],{'status':'deleted'});self.notice();self.analyze();self.assertEqual(self.db.snapshot()['tasks'],[])
    def test_restart_persistence(self):
        self.notice();self.analyze();self.assertEqual(CampusDB(self.path).snapshot()['counts']['open'],1)
    def test_old_incomplete_visible(self):
        self.notice();self.analyze()
        with self.db.connect() as d:d.execute("UPDATE tasks SET created_at='2020-01-01T00:00:00+08:00'")
        s=self.db.snapshot();self.assertEqual(s['counts']['new'],0);self.assertEqual(s['counts']['open'],1)
    def test_ai_unavailable_retains_tasks(self):
        self.notice();self.analyze();self.notice('请报名比赛')
        class Down:
            def extract(self,n):raise RuntimeError('offline')
        self.assertEqual(self.db.analyze(Down())['failed'],1);self.assertEqual(self.db.snapshot()['counts']['open'],1);self.assertEqual(len(self.db.pending()),1)
    def test_unquoted_evidence_rejected(self):
        with self.assertRaises(ValueError):clean_tasks(BODY,{'tasks':[task(evidence='模型编造的原文')]})

if __name__=='__main__':unittest.main(verbosity=2)
