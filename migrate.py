"""One-time union of consistent V1/V2 SQLite backups. Original DBs untouched."""
from datetime import datetime
from pathlib import Path
import json,sqlite3
from core import CampusDB,now
from notification_source import NotificationSource

def stamp(value):
    try:return datetime.fromisoformat(value).timestamp()
    except (TypeError,ValueError):return 0

def merge_databases(target,sources):
    target=Path(target)
    if target.exists():raise ValueError('最终版数据库已存在，拒绝覆盖；请使用新的迁移目标')
    db=CampusDB(target);NotificationSource(db,{'groups':[]},'unused','unused')
    report={'at':now(),'sources':[],'task_conflicts':[],'raw_conflicts':[]}
    with db.connect() as out:
        out.execute('CREATE TABLE IF NOT EXISTS migration_preserved(id INTEGER PRIMARY KEY,origin TEXT,table_name TEXT,record_key TEXT,row_json TEXT)')
        for path in map(Path,sources):
            src=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);src.row_factory=sqlite3.Row
            try:
                names={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                item={'path':str(path),'tables':{}}
                for table,pk in [('notices','id'),('tasks','id'),('jobs','id'),('windows_notifications','event_key')]:
                    if table not in names:continue
                    rows=[dict(r) for r in src.execute('SELECT * FROM '+table)];item['tables'][table]=len(rows)
                    cols=[r[1] for r in out.execute('PRAGMA table_info('+table+')')]
                    for raw in rows:
                        row={k:v for k,v in raw.items() if k in cols};old=out.execute('SELECT * FROM '+table+' WHERE '+pk+'=?',(row[pk],)).fetchone()
                        replace=False
                        if old and dict(old)!=row:
                            out.execute('INSERT INTO migration_preserved(origin,table_name,record_key,row_json) VALUES (?,?,?,?)',(str(path),table,str(row[pk]),json.dumps(row,ensure_ascii=False)))
                            if table=='tasks':
                                priority={'open':0,'done':1,'deleted':2}
                                replace=(stamp(row.get('updated_at')),priority.get(row.get('status'),0))>(stamp(old['updated_at']),priority.get(old['status'],0))
                                report['task_conflicts'].append({'id':row[pk],'chosen':'incoming' if replace else 'existing','reason':'newest_edit_then_completed_or_deleted_on_equal_time'})
                            elif table=='jobs':replace=stamp(row.get('updated_at'))>stamp(old['updated_at'])
                            elif table=='notices':
                                rank={'new':0,'error':1,'ignored_system':2,'duplicate':3,'no_task':4,'done':5}
                                if row['body']!=old['body']:report['raw_conflicts'].append({'id':row[pk],'origin':str(path)})
                                else:replace=rank.get(row['analysis_status'],0)>rank.get(old['analysis_status'],0)
                            elif table=='windows_notifications':
                                replace=(row['source_verified'],stamp(row['last_seen_at']))>(old['source_verified'],stamp(old['last_seen_at']))
                                if old['source_verified'] and not row['source_verified']:replace=False
                        if old and replace:
                            keys=[k for k in row if k!=pk]
                            out.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in keys)+' WHERE '+pk+'=?',[*[row[k] for k in keys],row[pk]])
                        elif not old:
                            out.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
                for table in ['task_sources','task_audit','capture_results','notification_scan_events']:
                    if table not in names:continue
                    rows=[dict(r) for r in src.execute('SELECT * FROM '+table)];item['tables'][table]=len(rows)
                    cols=[r[1] for r in out.execute('PRAGMA table_info('+table+')') if r[1]!='id']
                    for raw in rows:
                        row={k:raw[k] for k in cols if k in raw}
                        exists=out.execute('SELECT 1 FROM '+table+' WHERE '+' AND '.join(k+' IS ?' for k in row),list(row.values())).fetchone()
                        if not exists:out.execute('INSERT INTO '+table+'('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
                report['sources'].append(item)
            finally:src.close()
        report['final_counts']={table:out.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ['notices','tasks','task_sources','windows_notifications']}
        report['task_statuses']={r[0]:r[1] for r in out.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status')}
    return report
