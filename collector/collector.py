"""Read-only UIA feasibility probe. Python standard library only; no chat DB access."""
import argparse
import os
import hashlib
import json
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get("CAMPUS_COLLECTOR_DATA_DIR", str(ROOT.parent / "data" / "collector"))).resolve()
ALLOWED = {"QQ": "测试群", "Weixin": "文件传输助手"}


def probe(*args):
    # Each UIA call runs in a killable child: provider calls can block indefinitely.
    p = subprocess.run([str(ROOT / "Probe.exe"), *map(str, args)],
                       capture_output=True, timeout=30)
    if p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8-sig", errors="replace").strip())
    return json.loads(p.stdout.decode("utf-8-sig"))


def save(name, value):
    path = DATA_ROOT / "evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def children(nodes, parent):
    return [n for n in nodes if n.get("parent") == parent]


def message_rows(snapshot, allowed=None):
    """QQ 9.9.12 observed layout. Preserve UI text; never infer unknown sender/date."""
    r = snapshot["result"]
    if allowed is None:
        allowed = {ALLOWED["QQ"]}
    if not r.get("scope_verified") or r.get("conversation") not in allowed:
        raise ValueError("Selected QQ conversation not verified")
    if r.get("truncated") or any("error" in n for n in r["nodes"]):
        raise ValueError("Incomplete UIA snapshot; refusing message parsing")
    nodes = r["nodes"]
    lists = [n for n in nodes if n.get("name") == "消息列表" and n["type"] == "ControlType.Window"]
    if len(lists) != 1:
        raise ValueError("Expected one scoped message list")
    outer = children(nodes, lists[0]["index"])
    if not outer:
        return []
    inner = children(nodes, outer[0]["index"])
    if not inner:
        return []
    rows = children(nodes, inner[0]["index"])
    result = []
    occurrences = {}
    for row in rows:
        ids = {row["index"]}
        while True:
            expanded = ids | {n["index"] for n in nodes if n.get("parent") in ids}
            if expanded == ids:
                break
            ids = expanded
        subtree = [n for n in nodes if n["index"] in ids]
        # Keep leaf text in UI traversal order. This is raw UI, not a verified chat schema.
        named = [n for n in subtree if n.get("name") and n["type"] in
                 ("ControlType.Text", "ControlType.Image")]
        parts = [n["name"] for n in named]
        if not parts:
            continue
        times = [s for s in parts if re.fullmatch(r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+)?\d{1,2}:\d{2}(?::\d{2})?", s)]
        raw = "\n".join(parts)
        def descendant_text(index):
            subset = {index}
            while True:
                extra = subset | {n["index"] for n in subtree if n.get("parent") in subset}
                if extra == subset:
                    return [n for n in named if n["index"] in subset]
                subset = extra
        senders = []
        sender_nodes = []
        for n in subtree:
            if n.get("name") and n["type"] == "ControlType.Custom" and not descendant_text(n["index"]):
                siblings = [s for s in subtree if s.get("parent") == n.get("parent") and s["index"] != n["index"]]
                if any(descendant_text(s["index"]) for s in siblings):
                    senders.append(n["name"])
                    sender_nodes.append((n, siblings))
        sender = senders[0] if len(senders) == 1 else None
        if sender:
            body_nodes = []
            for sibling in sender_nodes[0][1]:
                body_nodes.extend(descendant_text(sibling['index']))
            body_ids = {n['index'] for n in body_nodes}
            content = '\n'.join(n['name'] for n in body_nodes)
            # A message whose body is literally '10:30' is text, not a send timestamp.
            times = [n['name'] for n in named if n['index'] not in body_ids and n['name'] in times]
        else:
            content = raw
        # UIA Runtime IDs proved unstable across separate client processes in this test.
        # Content + sender + visible-time + per-snapshot occurrence survives re-enumeration.
        # Sliding windows and equal messages on later days remain ambiguous without server IDs.
        signature = json.dumps([r["conversation"], sender, content, times], ensure_ascii=False)
        occurrences[signature] = occurrences.get(signature, 0) + 1
        identity = json.dumps(["QQ", signature, occurrences[signature]], ensure_ascii=False)
        key = hashlib.sha256(identity.encode()).hexdigest()
        result.append({"dedup_key": key, "source": "QQ", "conversation": r["conversation"],
                       "content": content, "sender": sender, "sent_at": None,
                       "sent_time_text": times[0] if len(times) == 1 else None,
                       "collected_at": snapshot["collected_at"],
                       "record_kind": "text_ui_message" if sender else "unparsed_ui_message_row", "parts": parts,
                       "raw_ui_text": raw, "dedup_method": "content_sender_visible_time_occurrence",
                       "runtime_id": row.get("runtime_id"), "pid": r["pid"],
                       "foreground": r["foreground"], "minimized": r["minimized"]})
    return result


class Store:
    def __init__(self, path):
        if str(path) != ":memory:": Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS seen (dedup_key TEXT PRIMARY KEY)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages (
          dedup_key TEXT PRIMARY KEY, source TEXT NOT NULL, conversation TEXT NOT NULL,
          content TEXT NOT NULL, sender TEXT, sent_at TEXT, sent_time_text TEXT,
          collected_at TEXT NOT NULL, record_kind TEXT NOT NULL, metadata_json TEXT NOT NULL)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (at TEXT, status TEXT, detail TEXT)")
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(messages)')}
        if 'capture_status' not in columns:
            self.db.execute("ALTER TABLE messages ADD COLUMN capture_status TEXT NOT NULL DEFAULT 'legacy_saved'")
        self.db.commit()

    def ingest(self, rows, baseline=False, materialize_baseline=False):
        added = 0
        with self.db:
            for row in rows:
                if not self.db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (row["dedup_key"],)).rowcount:
                    if not materialize_baseline or self.db.execute('SELECT 1 FROM messages WHERE dedup_key=?',(row['dedup_key'],)).fetchone():
                        continue
                if baseline:
                    continue
                repeats = self.db.execute('SELECT count(*) FROM messages WHERE source=? AND conversation=? AND sender IS ? AND content=?',
                    (row['source'], row['conversation'], row.get('sender'), row['content'])).fetchone()[0]
                if repeats:
                    # Preserve repeats; flag ambiguity instead of deleting equal bodies.
                    row['repeat_review_required'] = True
                    row['same_sender_body_existing_rows'] = repeats
                self.db.execute("INSERT INTO messages (dedup_key,source,conversation,content,sender,sent_at,sent_time_text,collected_at,record_kind,metadata_json,capture_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    *[row[k] for k in ("dedup_key", "source", "conversation", "content", "sender",
                                      "sent_at", "sent_time_text", "collected_at", "record_kind")],
                    json.dumps(row, ensure_ascii=False), row.get('capture_status', 'saved_ui_observation')))
                added += 1
        return added

    def event(self, status, detail):
        with self.db:
            self.db.execute("INSERT INTO events VALUES (?,?,?)", (
                datetime.now(timezone.utc).isoformat(), status, detail))


def window(source):
    inv = probe("inventory")
    candidates = [p for p in inv["result"]["processes"] if p["source"] == source and p["handle"]]
    if len(candidates) != 1:
        raise RuntimeError(f"{source}: expected exactly one process main window, got {len(candidates)}")
    return candidates[0]["handle"]


def watch(seconds, interval, include_existing=False):
    handle = window("QQ")
    store = Store(DATA_ROOT / "messages.sqlite3")
    first = probe("read-qq", handle, ALLOWED["QQ"])
    save("qq-watch-baseline.json", first)
    n = store.ingest(message_rows(first), baseline=not include_existing)
    store.event("baseline", f"existing rows stored={n}; include_existing={include_existing}")
    print(json.dumps({"status": "watch_started", "seconds": seconds, "existing_stored": n}), flush=True)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            snap = probe("read-qq", handle, ALLOWED["QQ"])
            n = store.ingest(message_rows(snap))
            if n:
                save("qq-watch-latest-new.json", snap)
                print(json.dumps({"new_rows": n, "at": snap["collected_at"]}), flush=True)
            store.event("poll_ok", f"new_rows={n}; minimized={snap['result']['minimized']}")
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            # Locked desktop, target switch or stale window: stop storing, retain explicit evidence.
            store.event("capture_refused", str(e))
            print(json.dumps({"capture_refused": str(e)}, ensure_ascii=False), flush=True)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
    store.event("watch_stopped", "time limit reached")
    store.db.close()


def matrix():
    handles = {s: window(s) for s in ALLOWED}
    initial = {s: probe("snapshot", h)["result"] for s, h in handles.items()}
    outcomes = []
    try:
        for source, h in handles.items():
            other = next(v for k, v in handles.items() if k != source)
            for state in ("foreground", "background", "minimized"):
                probe("state", h, "minimize" if state == "minimized" else "foreground")
                time.sleep(.8)
                if state == "background":
                    probe("state", other, "foreground")
                    time.sleep(.8)
                snap = probe("snapshot", h, ALLOWED[source])
                save(f"{source.lower()}-{state}-metadata.json", snap)
                r = snap["result"]
                actual = "minimized" if r["minimized"] else "foreground" if r["foreground"] else "background"
                record = {"source": source, "requested": state, "actual": actual,
                          "nodes": len(r["nodes"]), "truncated": r["truncated"]}
                if source == "QQ":
                    try:
                        messages = probe("read-qq", h, ALLOWED[source])
                        save(f"qq-{state}-scoped.json", messages)
                        record["ui_rows"] = len(message_rows(messages))
                        record["scoped_actual_foreground"] = messages["result"]["foreground"]
                        record["scoped_actual_minimized"] = messages["result"]["minimized"]
                    except Exception as e:
                        record["scoped_error"] = str(e)
                outcomes.append(record)
                save("matrix.json", outcomes)
                print(json.dumps(record, ensure_ascii=False), flush=True)
    finally:
        for s, h in handles.items():
            probe("state", h, "minimize" if initial[s]["minimized"] else "restore")
        for s, h in handles.items():
            if initial[s]["foreground"]:
                probe("state", h, "foreground")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["inventory", "matrix", "watch"])
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--interval", type=float, default=3)
    ap.add_argument("--include-existing", action="store_true", help="Store currently visible rows as existing UI observations")
    args = ap.parse_args()
    if args.seconds <= 0 or args.interval < 1:
        ap.error("seconds must be positive and interval >= 1")
    if args.command == "inventory":
        result = probe("inventory"); save("inventory.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "matrix":
        matrix()
    else:
        watch(args.seconds, args.interval, args.include_existing)


if __name__ == "__main__":
    main()
