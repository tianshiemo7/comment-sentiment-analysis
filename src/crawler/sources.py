"""采集来源登记；追加记录，保留历史采集信息。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SOURCE_NOTE_PATH = Path(__file__).resolve().parents[2] / 'data' / 'raw' / '来源说明.md'
SOURCE_FIELDS = ('站点或数据集', 'URL', '采集时间', '条数', '标注方式', '备注')


@dataclass
class SourceRecord:
    name: str
    url: str
    collected_at: str
    count: int
    labeled: bool = True
    note: str = ''


def _cell(value) -> str:
    return str(value).replace('&', '&amp;').replace('|', '&#124;').replace('<', '&lt;').replace('>', '&gt;').replace('\r\n', '<br>').replace('\n', '<br>').replace('\r', '<br>')


def render_source_table(records: list[SourceRecord]) -> str:
    lines = ['| ' + ' | '.join(SOURCE_FIELDS) + ' |', '| ' + ' | '.join(['---'] * len(SOURCE_FIELDS)) + ' |']
    for row in records:
        cells = (row.name, row.url, row.collected_at, row.count,
                 '已有标注' if row.labeled else '未标注（待人工标注）', row.note)
        lines.append('| ' + ' | '.join(_cell(value) for value in cells) + ' |')
    return '\n'.join(lines) + '\n'


def write_source_note(records: list[SourceRecord], path: str | Path = SOURCE_NOTE_PATH) -> Path:
    """追加一次采集记录，不覆盖历史记录。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    table = render_source_table(records)
    if not previous.strip():
        text = '# 数据来源说明\n\n采集策略以各次记录为准；空标签表示未标注，不代表负面情感。\n\n' + table
    elif '| ' + ' | '.join(SOURCE_FIELDS) + ' |' in previous and previous.rstrip().endswith('|'):
        text = previous.rstrip() + '\n' + '\n'.join(table.splitlines()[2:]) + '\n'
    else:
        text = previous.rstrip() + '\n\n' + table
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)
    return path
