import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector import Store, message_rows


def sample():
    nodes = [
        {"index": 0, "parent": -1, "type": "ControlType.Custom"},
        {"index": 1, "parent": 0, "type": "ControlType.Window", "name": "消息列表"},
        {"index": 2, "parent": 1, "type": "ControlType.Custom"},
        {"index": 3, "parent": 2, "type": "ControlType.Custom"},
    ]
    for i in range(2):
        base = 4 + i * 3
        nodes.extend([
            {"index": base, "parent": 3, "type": "ControlType.Custom", "runtime_id": [42, i]},
            {"index": base+1, "parent": base, "type": "ControlType.Text", "name": "10:30"},
            {"index": base+2, "parent": base, "type": "ControlType.Text", "name": "相同内容"},
        ])
    return {"collected_at": "2026-10-26T10:30:00+08:00", "result": {
        "conversation": "测试群", "scope_verified": True, "truncated": False,
        "pid": 123, "foreground": False, "minimized": True, "nodes": nodes}}


class CollectorTests(unittest.TestCase):
    def test_identical_content_separate_rows_not_collapsed(self):
        rows = message_rows(sample())
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["dedup_key"], rows[1]["dedup_key"])
        self.assertIsNone(rows[0]["sender"])
        self.assertIsNone(rows[0]["sent_at"])
        self.assertEqual(rows[0]["sent_time_text"], "10:30")

    def test_refuses_wrong_conversation_partial_and_error(self):
        for field, value in [("conversation", "其他群"), ("scope_verified", False), ("truncated", True)]:
            snap = sample(); snap["result"][field] = value
            with self.assertRaises(ValueError): message_rows(snap)
        snap = sample(); snap["result"]["nodes"][0]["error"] = "unavailable"
        with self.assertRaises(ValueError): message_rows(snap)

    def test_baseline_and_persistent_dedup(self):
        work = Path(tempfile.gettempdir())
        work.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as d:
            path = Path(d) / "test.db"
            store = Store(path)
            rows = message_rows(sample())
            self.assertEqual(store.ingest(rows[:1], baseline=True), 0)
            self.assertEqual(store.ingest(rows), 1)
            self.assertEqual(store.ingest(rows), 0)
            store.db.close()
            reopened = Store(path)
            self.assertEqual(reopened.ingest(rows), 0)
            self.assertEqual(reopened.db.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
            reopened.db.close()

    def test_runtime_id_changes_do_not_duplicate(self):
        first = sample()
        second = sample()
        second["result"]["pid"] = 999
        for n in second["result"]["nodes"]:
            if "runtime_id" in n:
                n["runtime_id"] = [777, n["index"]]
        self.assertEqual([r["dedup_key"] for r in message_rows(first)],
                         [r["dedup_key"] for r in message_rows(second)])

    def test_evening_can_materialize_intentionally_skipped_baseline(self):
        store=Store(':memory:')
        rows=message_rows(sample())
        self.assertEqual(store.ingest(rows,baseline=True),0)
        self.assertEqual(store.ingest(rows),0)
        self.assertEqual(store.ingest(rows,materialize_baseline=True),2)
        self.assertEqual(store.ingest(rows,materialize_baseline=True),0)
        store.db.close()


if __name__ == "__main__":
    unittest.main()
