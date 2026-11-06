"""Tencent read-only meeting discovery and lossless transcript normalization."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from . import mcp

TZ = ZoneInfo('Asia/Shanghai')


def unwrap(value):
    for _ in range(6):
        if not isinstance(value, dict):
            break
        if 'error_info' in value or value.get('error'):
            raise ValueError('腾讯会议查询失败，请检查账号权限或稍后重试')
        if isinstance(value.get('data'), dict) and 'body' in value['data']:
            data = value['data']
            if data.get('status_code', 200) != 200:
                raise ValueError('腾讯会议暂时无法提供此数据，请检查录制权限和处理状态')
            value = data['body']
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except ValueError:
                    raise ValueError('腾讯返回的数据无法解析，请稍后重试') from None
        elif isinstance(value.get('data'), dict):
            value = value['data']
        else:
            break
    return value


def call(name, arguments):
    return unwrap(mcp.result_json(mcp.invoke('tools/call', {'name': name, 'arguments': arguments})))


def pages(name, arguments):
    seen = set()
    for _ in range(100):
        data = call(name, arguments)
        if not isinstance(data, dict):
            raise ValueError('腾讯分页数据格式不正确')
        yield data
        if not data.get('has_more'):
            return
        token = data.get('next_page_token')
        if not token or token in seen:
            raise ValueError('腾讯分页未能完整读取，请重试')
        seen.add(token)
        arguments = {**arguments, 'page_token': token}
    raise ValueError('会议数据过多，请缩短查询范围')


def meeting_time(value):
    try:
        if str(value).isdigit():
            number = int(value)
            return datetime.fromtimestamp(number / (1000 if number > 10**11 else 1), TZ)
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=TZ) if result.tzinfo is None else result.astimezone(TZ)
    except (ValueError, TypeError, OverflowError, OSError):
        raise ValueError('会议时间格式无法识别，请稍后重试') from None


def list_meetings(days, now=None):
    if type(days) is not int or days not in (1, 3, 7):
        raise ValueError('请选择最近 1、3 或 7 天')
    now = now or datetime.now(TZ)
    arguments = {'start_time': (now - timedelta(days=days)).isoformat(timespec='seconds'),
                 'end_time': now.isoformat(timespec='seconds'), 'page_size': 30, 'timezone': 'Asia/Shanghai'}
    meetings = {}
    for data in pages('get_user_ended_meetings', arguments):
        rows = data.get('meeting_info_list', [])
        if not isinstance(rows, list):
            raise ValueError('腾讯会议列表格式无法识别')
        for row in rows:
            if not row.get('meeting_id'):
                continue
            start = meeting_time(row.get('start_time'))
            key = str(row['meeting_id']) + ':' + str(row.get('sub_meeting_id') or '')
            recordings = row.get('records_total_count')
            available = recordings != 0
            meetings[key] = {'remote_id': str(row['meeting_id']), 'remote_key': key,
                             'sub_meeting_id': str(row.get('sub_meeting_id') or ''),
                             'title': str(row.get('subject') or '未命名会议'), 'date': start.date().isoformat(),
                             'time': start.strftime('%Y-%m-%d %H:%M'), 'start_time': start.isoformat(),
                             'available': available, 'reason': '' if available else '无云录制，暂无法导入原始转写'}
    return sorted(meetings.values(), key=lambda m: m['start_time'], reverse=True)


def recording_ids(meeting_id, sub_meeting_id=''):
    ids = []
    def visit(value):
        if isinstance(value, dict):
            rid = value.get('record_file_id')
            if rid and str(rid).isdigit() and int(rid) != 0:
                if str(rid) not in ids:
                    ids.append(str(rid))
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    for data in pages('get_records_list', {'meeting_id': meeting_id, 'page_size': 30}):
        if sub_meeting_id and sub_meeting_id != '0':
            groups = data.get('record_meetings', [])
            for group in groups:
                if str(group.get('sub_meeting_id', '')) == sub_meeting_id:
                    visit(group)
        else:
            visit(data)
    if not ids:
        raise ValueError('这场会议暂无可用云录制，请等待处理完成或确认录制访问权限')
    return ids


def speaker_name(row):
    # MCP uses speaker.user_name; REST responses use speaker_info.username.
    for field in ('speaker', 'speaker_info'):
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for key in ('user_name', 'username'):
                name = value.get(key)
                if isinstance(name, str) and name.strip():
                    return name.strip()
    return '未知发言人'


def normalize_paragraph(row):
    if all(k in row for k in ('id', 'timestamp', 'speaker', 'text')):
        if not isinstance(row['text'], str) or not row['text'].strip():
            raise ValueError('转写段落为空')
        return {**{k: str(row[k]) for k in ('id', 'timestamp', 'text')}, 'speaker': speaker_name(row)}
    if 'pid' not in row or 'start_time' not in row:
        raise ValueError('腾讯转写缺少段落 ID 或时间信息')
    text = ''.join(word.get('text', '') for sentence in row.get('sentences', []) for word in sentence.get('words', []))
    if not text.strip():
        text = row.get('text', '')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('腾讯转写不含可读取的原文')
    seconds = int(row['start_time']) // 1000
    h, rest = divmod(seconds, 3600)
    mm, ss = divmod(rest, 60)
    stamp = f'{h:02}:{mm:02}:{ss:02}' if h else f'{mm:02}:{ss:02}'
    return {'id': str(row['pid']), 'timestamp': stamp, 'speaker': speaker_name(row), 'text': text}


def fetch_record(arguments):
    args = {'pid': '0', 'limit': 100, **arguments}
    result, seen_cursors = {}, set()
    for _ in range(1000):
        data = call('get_transcripts_details', args)
        if not isinstance(data, dict):
            raise ValueError('腾讯转写数据结构无法识别')
        minutes = data.get('minutes') or data
        rows = minutes.get('paragraphs') if isinstance(minutes, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ValueError('这场会议暂无可用转写，可能尚在生成或未开启录制转写')
        for row in rows:
            p = normalize_paragraph(row)
            if p['id'] in result and result[p['id']] != p:
                raise ValueError('腾讯返回的段落 ID 冲突，已停止导入')
            result[p['id']] = p
        if not (data.get('more') or data.get('has_more')):
            return list(result.values())
        cursor = data.get('next_pid') or data.get('next_page_token')
        if not cursor:
            last = str(rows[-1].get('pid', rows[-1].get('id', '')))
            if not last.isdigit():
                raise ValueError('转写分页缺少下一段位置，请稍后重试')
            cursor = str(int(last) + 1)
        cursor = str(cursor)
        if cursor in seen_cursors or cursor == args.get('pid'):
            raise ValueError('转写分页未前进，已停止导入以避免遗漏')
        seen_cursors.add(cursor)
        args = {**args, 'pid': cursor}
    raise ValueError('转写过长，未完整导入')


def fetch_meeting(meeting_id, sub_meeting_id=''):
    ids = recording_ids(meeting_id, sub_meeting_id)
    paragraphs = []
    for rid in ids:
        for p in fetch_record({'meeting_id': meeting_id, 'record_file_id': rid}):
            paragraphs.append({**p, 'id': rid + ':' + p['id'], 'record_file_id': rid, 'original_pid': p['id']})
    return paragraphs
