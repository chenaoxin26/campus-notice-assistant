"""Portable local XLSX export. No Excel, Node or Codex installation required."""
from pathlib import Path
import math
import re


def safe_text(value):
    """Keep notification text as literal strings, never spreadsheet formulas."""
    text = '' if value is None else str(value)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    if len(text) > 32767:
        text = text[:32720] + '\n【Excel单元格长度限制，完整原文请查本地数据库】'
    return text


def export_snapshot(snapshot, target):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError as exc:
        raise RuntimeError('Excel导出需要openpyxl，请运行setup.cmd或安装requirements.txt；已有任务未受影响') from exc

    notices = {n['id']: n for n in snapshot['notices']}
    tasks = [t for t in snapshot['tasks'] if t['status'] != 'deleted']
    wb = Workbook()
    sheet = wb.active
    sheet.title = '校园待办'
    sheet.sheet_view.showGridLines = False
    sheet.merge_cells('A1:J1')
    sheet['A1'] = 'AI校园通知整理助手 待办表'
    sheet['A1'].font = Font(name='Microsoft YaHei', size=18, bold=True, color='172B4D')
    sheet.row_dimensions[1].height = 34
    sheet.merge_cells('A2:J2')
    sheet['A2'] = 'SQLite为长期记录，Excel为快照；缺失字段待确认，通知与近期聊天均可能遗漏。'
    sheet['A2'].font = Font(name='Microsoft YaHei', size=10, color='596B83')
    sheet.row_dimensions[2].height = 25
    headers = ['任务名称','类别','截止时间','地点','具体要求','完成状态','消息来源','核对状态','操作步骤','我的备注']
    sheet.append(headers)
    raw = wb.create_sheet('消息来源')
    raw.sheet_view.showGridLines = False
    raw.append(['任务名称','来源软件','群聊或会话','发送者','原始发送时间','采集时间','原始通知'])
    for task in tasks:
        sources = [notices[nid] for nid in task['sources'] if nid in notices]
        sheet.append([safe_text(v) for v in [task['title'],task['category'],task['deadline_text'],task['location'],
            task['requirements'],'已完成' if task['status']=='done' else '未完成',
            '\n'.join(f"{n['source']} / {n['group_name']} / {n.get('sender') or '昵称待确认'}" for n in sources),
            '待确认' if task['needs_review'] else '已人工核对','\n'.join(task['steps']),task.get('notes','')]])
        for n in sources:
            raw.append([safe_text(v) for v in [task['title'],n['source'],n['group_name'],n.get('sender') or '待确认',
                n.get('sent_at') or n.get('sent_time_text') or '待确认',n['collected_at'],n['body']]])

    thin = Side(style='thin', color='D9E2F0')
    def style(ws, header, widths, name):
        from openpyxl.utils import get_column_letter
        ws.freeze_panes = f'A{header+1}'
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
        for row in ws.iter_rows(min_row=header):
            for cell in row:
                cell.font = Font(name='Microsoft YaHei', size=11, color='172B4D')
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                cell.border = Border(bottom=thin)
                if row[0].row == header:
                    cell.fill = PatternFill('solid', fgColor='2563EB')
                    cell.font = Font(name='Microsoft YaHei', size=11, bold=True, color='FFFFFF')
                    cell.alignment = Alignment(vertical='center', horizontal='center', wrap_text=True)
                elif row[0].row % 2 == 0:
                    cell.fill = PatternFill('solid', fgColor='F1F6FF')
                # Setting the data type explicitly prevents =, +, -, @ or whitespace
                # prefixes in untrusted notification text from becoming formulas.
                if isinstance(cell.value, str):
                    cell.data_type = 's'
            if row[0].row == header:
                ws.row_dimensions[header].height = 30
            else:
                lines = max(sum(max(1, math.ceil(len(part)*1.7/max(1,widths[i]-2)))
                    for part in str(c.value or '').split('\n')) for i,c in enumerate(row))
                ws.row_dimensions[row[0].row].height = min(360, max(42, lines*17+10))
        if ws.max_row > header:
            table = Table(displayName=name, ref=f'A{header}:{get_column_letter(len(widths))}{ws.max_row}')
            table.tableStyleInfo = TableStyleInfo(name='TableStyleMedium2', showRowStripes=True)
            ws.add_table(table)
        else:
            ws.auto_filter.ref = f'A{header}:{get_column_letter(len(widths))}{header}'
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_title_rows = f'{header}:{header}'
    style(sheet, 3, [30,16,29,22,55,14,45,16,45,35], 'CampusTasks')
    style(raw, 1, [30,24,25,20,29,29,95], 'NoticeSources')
    for row in sheet.iter_rows(min_row=4):
        if row[7].value == '待确认': row[7].fill = PatternFill('solid', fgColor='FFF0CC')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target)
    return target
