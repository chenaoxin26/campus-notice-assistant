import tempfile,unittest,json
from pathlib import Path
from core import CampusDB
from migrate import merge_databases
from notification_source import NotificationSource
class AI:
    def extract(self,n):return {'tasks':[dict(title='提交报告',category='学习任务',deadline_text='待确认',location='待确认',requirements=[],steps=[],importance='待确认',evidence='请提交报告',is_update=False)]}
class MergeTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup);self.root=Path(self.t.name)
        self.a=CampusDB(self.root/'v1.db');self.b=CampusDB(self.root/'v2.db')
    def add(self,db,key='shared',source='QQ',body='请提交报告'):
        db.add_notice(source,'测试群','老师',body,source_key=key);db.analyze(AI());return db.snapshot()['tasks'][0]['id']
    def merge(self):
        report=merge_databases(self.root/'final.db',[self.a.path,self.b.path]);return CampusDB(self.root/'final.db'),report
    def test_shared_task_and_raw_not_duplicated(self):
        self.add(self.a);self.add(self.b);db,r=self.merge();self.assertEqual(len(db.snapshot()['tasks']),1);self.assertEqual(len(db.snapshot()['notices']),1)
    def test_new_tasks_from_both_versions_preserved(self):
        self.add(self.a,'a');self.add(self.b,'b');db,r=self.merge();self.assertEqual(len(db.snapshot()['tasks']),2)
    def test_latest_edit_wins(self):
        tid=self.add(self.a);self.add(self.b)
        with self.a.connect() as c:c.execute("UPDATE tasks SET title='新修改',updated_at='2026-10-26T15:00:00+08:00' WHERE id=?",(tid,))
        with self.b.connect() as c:c.execute("UPDATE tasks SET updated_at='2026-10-26T14:00:00+08:00' WHERE id=?",(tid,))
        db,r=self.merge();self.assertEqual(db.snapshot()['tasks'][0]['title'],'新修改')
    def test_equal_timestamp_completed_stays_completed(self):
        tid=self.add(self.a);self.add(self.b)
        with self.a.connect() as c:c.execute("UPDATE tasks SET status='done',updated_at='2026-10-26T15:00:00+08:00'")
        with self.b.connect() as c:c.execute("UPDATE tasks SET updated_at='2026-10-26T15:00:00+08:00'")
        db,r=self.merge();self.assertEqual(db.snapshot()['counts']['done'],1)
    def test_newer_deletion_not_resurrected(self):
        self.add(self.a);self.add(self.b)
        with self.a.connect() as c:c.execute("UPDATE tasks SET status='deleted',updated_at='2030-01-01T00:00:00+08:00'")
        db,r=self.merge();self.assertEqual(db.snapshot()['tasks'],[])
    def test_conflicting_edits_retained_for_audit(self):
        tid=self.add(self.a);self.add(self.b);self.b.edit_task(tid,{'notes':'另一个版本的备注'})
        db,r=self.merge()
        with db.connect() as c:self.assertGreater(c.execute('SELECT COUNT(*) FROM migration_preserved').fetchone()[0],0);self.assertGreater(c.execute('SELECT COUNT(*) FROM task_audit').fetchone()[0],0)
    def test_cannot_overwrite_existing_final(self):
        self.add(self.a);self.merge()
        with self.assertRaises(ValueError):self.merge()
    def test_cross_source_only_flags_not_auto_merges(self):
        self.add(self.a,'chat');self.add(self.a,'toast','QQ_windows_notification')
        self.a.flag_cross_source_candidates();s=self.a.snapshot();self.assertEqual(len(s['tasks']),2);self.assertTrue(all(any('另一采集入口' in r for r in t['review_reasons']) for t in s['tasks']))

if __name__=='__main__':unittest.main(verbosity=2)
