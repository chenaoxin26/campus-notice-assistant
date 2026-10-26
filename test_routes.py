import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from app import Service
class ImmediateThread:
    def __init__(self,target,**kw):self.target=target
    def start(self):self.target()
class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=Service(Path(self.tmp.name))
        self.s.db.analyze=lambda *args:{'failed':0};self.s.db.sync_collector=lambda *args:0
    def job(self,kind):
        with patch('app.threading.Thread',ImmediateThread):self.s.start_job(kind)
        j=self.s.db.snapshot()['jobs'][0];return j,json.loads(j['detail_json'])
    def test_notification_button_never_calls_chat(self):
        self.s.source.scan=lambda:{'ok':True}
        self.s.chat.capture=lambda p:(_ for _ in ()).throw(AssertionError('unexpected QQ navigation'))
        j,d=self.job('capture');self.assertEqual(j['status'],'done');self.assertNotIn('capture',d['result'])
    def test_notification_failure_does_not_block_chat(self):
        self.s.source.scan=lambda:(_ for _ in ()).throw(RuntimeError('denied'))
        self.s.chat.capture=lambda p:[{'status':'近期消息区可读','group':'测试群'}]
        j,d=self.job('chat');self.assertEqual(j['status'],'partial');self.assertEqual(d['result']['capture'][0]['status'],'近期消息区可读')
    def test_chat_failure_retains_notification_result(self):
        self.s.source.scan=lambda:{'new_records':1}
        self.s.chat.capture=lambda p:[{'status':'本次未读到','group':'测试群'}]
        j,d=self.job('chat');self.assertEqual(j['status'],'error');self.assertEqual(d['result']['notification']['new_records'],1)

if __name__=='__main__':unittest.main(verbosity=2)
