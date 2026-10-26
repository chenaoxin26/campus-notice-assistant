"""Prepare and reconcile human-sent, uniquely numbered QQ reliability trials."""
import argparse
from contextlib import closing
import csv
import json
from pathlib import Path
import re
import sqlite3
import uuid
from collector import ROOT, DATA_ROOT
from continuous import DB, now


def prepare(groups, per_phase=1):
    if len(groups)!=len(set(groups)) or not groups or per_phase<1:
        raise ValueError('群名必须非空、不重复，条数至少1')
    trial_id = 'T3-'+__import__('datetime').datetime.now().strftime('%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:3]
    folder = DATA_ROOT/'trials'/trial_id
    folder.mkdir(parents=True)
    expected=[]
    for phase, condition in [('A','minimized'),('B','display_off')]:
        for index, group in enumerate(groups,1):
            for number in range(1,per_phase+1):
                token=f'{trial_id}-{phase}{index}{number}'
                expected.append(dict(token=token,group=group,phase=phase,condition=condition,
                    send_status='planned',sent_at='',note=''))
    manifest=dict(trial_id=trial_id,created_at=now(),groups=groups,run_ids=[],expected=expected)
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    with (folder/'发送核对表.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(expected[0]));writer.writeheader();writer.writerows(expected)
    lines=['# 离机消息采集真实测试',f'测试编号：{trial_id}',
        '每个编号必须作为一条单独的QQ消息发送，不合并、不让采集程序发送。',
        '先在测试界面停止旧采集器。本轮可能正常打开会话、前置QQ并改变未读状态。',
        'A：把QQ最小化后离开电脑，仅从手机发送以下A编号，记下实际发送成功情况。',
        'B：回来恢复QQ普通窗口，再关闭显示器并离开（不要锁屏、睡眠或合盖）。仅从手机发送B编号。',
        '最后开屏返回。若不方便关屏，可跳过B，但必须标记跳过；没有系统关屏通知不算已验证关屏。',
        '尽量不要在发送期间切换电脑会话。每条发送间隔约3秒，A和B之间分别留60秒供轮读。','']
    for row in expected: lines.append(f"- {row['phase']} / {row['group']}：`{row['token']}`")
    group_args=' '.join('--group "'+g.replace('"','')+'"' for g in groups)
    lines += ['', '在本工具目录启动（不会自动恢复最小化QQ）：', '```powershell',
        f'python rotation.py {group_args} --seconds 600 --interval 3 --idle-seconds 10 --sweep-seconds 30 --stop-file "{(folder/"STOP").resolve()}" --trial-folder "{folder.resolve()}"',
        '```', 'run_id自动关联到清单，核对时只使用这些真实采集运行。',
        '只在确认实际已发送后，将发送核对表中对应行 send_status 改为 confirmed_sent；没发用 not_sent，发失败用 failed。',
        '不填写发送时间时保留空值。不要把计划条数当作已发送条数。',
        '修改CSV后先执行 confirm --from-csv，再执行 evaluate（均需 --folder 指向本测试目录）。']
    (folder/'真实测试步骤.md').write_text('\n'.join(lines),'utf-8')
    return folder


def confirm(folder, tokens=None, all_sent=False, note='user_confirmed'):
    path=Path(folder)/'manifest.json'; m=json.loads(path.read_text('utf-8'))
    selected=set(tokens or [])
    valid={r['token'] for r in m['expected']}
    if not selected<=valid: raise ValueError('未知测试编号')
    for row in m['expected']:
        if all_sent or row['token'] in selected:
            row.update(send_status='confirmed_sent',note=note)
    path.write_text(json.dumps(m,ensure_ascii=False,indent=2),'utf-8')
    sync_csv(folder,m)


def sync_csv(folder,manifest):
    fields=['token','group','phase','condition','send_status','sent_at','note']
    with (Path(folder)/'发送核对表.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows({key:row.get(key,'') for key in fields} for row in manifest['expected'])


def confirm_csv(folder):
    folder=Path(folder); path=folder/'manifest.json'; m=json.loads(path.read_text('utf-8'))
    known={r['token']:r for r in m['expected']}
    with (folder/'发送核对表.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.DictReader(stream))
    if {r['token'] for r in rows}!=set(known) or len(rows)!=len(known): raise ValueError('CSV编号缺失/重复/未知')
    for row in rows:
        original=known[row['token']]
        if row['group']!=original['group'] or row['condition']!=original['condition']: raise ValueError('不得改变群名和条件来提高通过率')
        if row['send_status'] not in {'planned','confirmed_sent','not_sent','failed'}: raise ValueError('无效发送状态')
        original.update(send_status=row['send_status'],sent_at=row['sent_at'],note=row['note'])
    path.write_text(json.dumps(m,ensure_ascii=False,indent=2),'utf-8')
    sync_csv(folder,m)


def evaluate(folder, run_ids=None, db=DB, label='result'):
    folder=Path(folder); m=json.loads((folder/'manifest.json').read_text('utf-8'))
    run_ids=run_ids or m['run_ids']
    if not re.fullmatch(r'[A-Za-z0-9_-]+',label): raise ValueError('无效输出标签')
    if not run_ids: raise ValueError('必须指定本次真实采集的run_id，不能用全部历史记录冒充本轮结果')
    with closing(sqlite3.connect(db)) as con:
        con.row_factory=sqlite3.Row
        params=','.join('?'*len(run_ids))
        existing={r[0] for r in con.execute(f'SELECT id FROM capture_runs WHERE id IN ({params})',run_ids)}
        if existing!=set(run_ids): raise ValueError('存在未知run_id')
        observations=[dict(r) for r in con.execute(f'''SELECT m.*,o.observed_at,o.minimized,o.foreground,o.display_state,o.run_id
            FROM rotation_observed_messages o JOIN messages m ON o.dedup_key=m.dedup_key
            WHERE o.run_id IN ({params}) AND m.source='QQ' ''',run_ids)]
        events=[dict(r) for r in con.execute(f'SELECT run_id,conversation,event,detail_json FROM rotation_events WHERE run_id IN ({params})',run_ids)]
    modes={json.loads(e['detail_json']).get('mode','unknown') for e in events if e['event']=='rotation_start'}
    if len(modes)>1: raise ValueError('不能将持续轮读和晚间补读混算；请分别指定run-id并使用不同label')
    rows=[]
    for item in m['expected']:
        matches=[r for r in observations if r['conversation']==item['group'] and r['content'].strip()==item['token'] and r['record_kind']=='text_ui_message']
        state_matches=[r for r in matches if (item['condition']=='minimized' and r['minimized']) or (item['condition']=='display_off' and r['display_state']=='off')]
        group_events=[e for e in events if e['conversation']==item['group']]
        preview_seen=any(item['token'] in json.loads(e['detail_json']).get('parts',[]) for e in group_events if e['event']=='selected_preview')
        raw_seen=any(item['token'] in r['metadata_json'] for r in observations if r['conversation']==item['group'])
        if matches: reason='完整正文已读并存在本地数据库'
        elif raw_seen: reason='编号出现在原始UI片段中，但未得到合格的普通消息正文；需复核解析'
        elif preview_seen: reason='只有会话摘要证据，未保存合格完整正文'
        elif any(e['event']=='deferred_user_active' for e in group_events): reason='该群有用户操作延后记录；是否为直接漏采原因仍需对照日志'
        elif any(e['event']=='read_failed' for e in group_events): reason='该群存在读取失败；客户端接收及更具体原因未确定'
        else: reason='本轮未见该编号；无法确定未发送、未同步、UI未暴露或其他原因'
        if item['send_status'] in {'not_sent','failed'} and not matches:
            reason='用户跳过或确认未发成功，不计入应采和遗漏'
        failures=[json.loads(e['detail_json']).get('reason') for e in group_events if e['event']=='read_failed']
        rows.append({**item,'saved':bool(matches),'saved_observation_count':len(matches),
            'condition_observed':bool(state_matches),'first_observed_at':matches[0]['observed_at'] if matches else None,
            'reason':reason,'preview_only_seen':preview_seen,'recent_read_failures':failures[-3:]})
    confirmed=[r for r in rows if r['send_status']=='confirmed_sent']
    unconfirmed=[r for r in rows if r['send_status']=='planned']
    summary=dict(trial_id=m['trial_id'],run_ids=run_ids,capture_modes=sorted(modes),planned=len(rows),
        confirmed_expected=len(confirmed),success=sum(r['saved'] for r in confirmed),
        missing=sum(not r['saved'] for r in confirmed),condition_observed=sum(r['condition_observed'] for r in confirmed),
        unconfirmed=len(unconfirmed),overall_missing_count=None if unconfirmed else sum(not r['saved'] for r in confirmed),
        saved_planned_tokens=sum(r['saved'] for r in rows),
        phases={phase:{'planned':sum(r['phase']==phase for r in rows),
            'confirmed_expected':sum(r['phase']==phase for r in confirmed),
            'success':sum(r['saved'] for r in confirmed if r['phase']==phase),
            'missing':sum(not r['saved'] for r in confirmed if r['phase']==phase),
            'condition_observed':sum(r['condition_observed'] for r in confirmed if r['phase']==phase)} for phase in sorted({r['phase'] for r in rows})},
        qualification='有未确认发送项，不能把计划未匹配数当成遗漏' if unconfirmed else '只对人工确认发送的唯一编号核对；不证明全天可靠')
    result=dict(summary=summary,rows=rows)
    json_name='result.json' if label=='result' else label+'-result.json'
    md_name='真实测试结果.md' if label=='result' else label+'-真实测试结果.md'
    (folder/json_name).write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    lines=['# 真实测试核对结果',f"测试编号：{m['trial_id']}",
        f"计划 {summary['planned']} 条；人工确认应采 {summary['confirmed_expected']} 条；保存成功 {summary['success']} 条；确认发送但未匹配 {summary['missing']} 条；仍未确认发送 {summary['unconfirmed']} 条。",
        f"不论发送确认状态，本轮实际匹配到计划编号的完整正文 {summary['saved_planned_tokens']} 条。",
        summary['qualification'], '窗口/关屏条件观察符合的确认消息：'+str(summary['condition_observed'])+' 条；不自动等于发送时条件也符合。',
        '', '|群聊|编号|发送确认|完整正文保存|读取条件符合|说明|','|---|---|---|---|---|---|']
    for r in rows: lines.append('|'+ '|'.join([r['group'],r['token'],r['send_status'],'是' if r['saved'] else '否','是' if r['condition_observed'] else '否',r['reason']])+'|')
    (folder/md_name).write_text('\n'.join(lines),'utf-8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','confirm','evaluate'])
    p.add_argument('--group',action='append');p.add_argument('--folder',type=Path)
    p.add_argument('--run-id',action='append');p.add_argument('--token',action='append')
    p.add_argument('--all-sent',action='store_true');p.add_argument('--per-phase',type=int,default=1)
    p.add_argument('--from-csv',action='store_true')
    p.add_argument('--label',default='result')
    args=p.parse_args()
    if args.command=='prepare': print(prepare(args.group or [],args.per_phase))
    elif args.command=='confirm':
        if args.from_csv: confirm_csv(args.folder)
        else: confirm(args.folder,args.token,args.all_sent)
    else: print(json.dumps(evaluate(args.folder,args.run_id,label=args.label)['summary'],ensure_ascii=True))


if __name__=='__main__': main()
