import json
import os
import secrets
import sqlite3
import threading
import uuid
import time
from datetime import date
from contextlib import asynccontextmanager

import uvicorn
from fastapi import HTTPException, Request, Depends
from fastapi.responses import FileResponse
from .web import create_app
from . import schemas
from pathlib import Path
from .core import analyze, parse_transcript
from .mcp import invoke, READ_TOOLS, request
from . import integrations, tencent
from .workflow import MeetingWorkflow

ROOT = Path(__file__).resolve().parent.parent
LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(32)
DB = None
TENCENT_CHOICES = {}
RUNNING_ANALYSES = set()


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


def connect():
    return sqlite3.connect(DB)


def save(m):
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO meetings VALUES (?, ?)', (m['id'], json.dumps(m, ensure_ascii=False)))


def get(mid):
    with connect() as db:
        row = db.execute('SELECT body FROM meetings WHERE id=?', (mid,)).fetchone()
    if not row:
        raise ValueError('会议不存在')
    return json.loads(row[0])


def all_meetings():
    with connect() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM meetings ORDER BY rowid DESC')]


def workflow():
    analyzer = analyze
    mode = os.getenv('MEETMIND_ANALYSIS_MODE', 'single')
    if mode == 'dual':
        from .durable_agents import analyze as analyze_dual
        analyzer = lambda paragraphs, day: analyze_dual(paragraphs, day, str(DB) + '.agents')
    elif mode != 'single':
        raise ValueError('MEETMIND_ANALYSIS_MODE 必须为 single 或 dual')
    return MeetingWorkflow(str(DB) + '.checkpoints', get, save, LOCK, analyzer)


def run_analysis(mid):
    with LOCK:
        RUNNING_ANALYSES.add(mid)
    try:
        workflow().start(mid)
    except Exception as exc:
        with LOCK:
            m = get(mid)
            m.update(status='error', error=str(exc) if isinstance(exc, ValueError) else '分析失败，请检查配置后重试')
            save(m)
    finally:
        with LOCK:
            RUNNING_ANALYSES.discard(mid)


def create_meeting(body):
    title = str(body.get('title', '')).strip()
    if not title or len(title) > 200:
        raise ValueError('请输入 1–200 字的会议主题')
    day = body.get('date') or date.today().isoformat()
    date.fromisoformat(day)
    m = dict(id=uuid.uuid4().hex, title=title, date=day, project_id=None,
             paragraphs=[], source=body.get('source') or {'type': 'local'}, status='new', report=None, error='')
    if not isinstance(m['source'], dict) or m['source'].get('type') not in ('local', 'tencent'):
        raise ValueError('不支持的转写来源')
    if m['source']['type'] == 'tencent':
        if not m['source'].get('meeting_id') and not isinstance(m['source'].get('arguments'), dict):
            raise ValueError('请提供 get_transcripts_details 的 arguments')
    else:
        m['paragraphs'] = parse_transcript(body.get('transcript', ''))
    save(m)
    return m


def initialize():
    global DB
    load_env()
    folder = ROOT / 'data'
    folder.mkdir(mode=0o700, exist_ok=True)
    DB = folder / 'meetmind.sqlite3'
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS meetings (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
    os.chmod(DB, 0o600)
    for m in all_meetings():
        if m['status'] == 'analyzing':
            m.update(status='error', error='服务重启中断分析，请重试')
        if m['report']:
            for a in m['report']['actions']:
                if a['status'] == 'syncing':
                    a.update(status='uncertain', sync_error='服务重启中断同步，请到滴答清单核实')
        save(m)


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


def require_token(request: Request):
    if not secrets.compare_digest(request.headers.get('X-MeetMind-Token', ''), TOKEN):
        raise HTTPException(403, '无效请求，请刷新页面')


app = create_app(lifespan)


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/app.js')
def javascript():
    return FileResponse(ROOT / 'static' / 'app.js', media_type='text/javascript')


@app.get('/style.css')
def stylesheet():
    return FileResponse(ROOT / 'static' / 'style.css', media_type='text/css')


@app.get('/api/state')
def state():
    return {'token': TOKEN, 'meetings': all_meetings(),
            'config': {'llm': bool(os.getenv('LLM_BASE_URL') and os.getenv('LLM_MODEL')),
                       'dida': bool(os.getenv('DIDA_MCP_URL')) if integrations.dida_mode() == 'mcp' else bool(os.getenv('DIDA_ACCESS_TOKEN')),
                       'tencent': bool(os.getenv('TENCENT_MCP_URL') and os.getenv('TENCENT_MEETING_TOKEN'))}}


@app.get('/api/projects')
def projects():
    return integrations.list_projects()


@app.post('/api/tencent/meetings', dependencies=[Depends(require_token)])
def tencent_meetings(body: schemas.TencentQuery):
    b = body.model_dump(exclude_unset=True)
    rows = tencent.list_meetings(b.get('days', 3))
    choices = []
    with LOCK:
        now = time.monotonic()
        for key in list(TENCENT_CHOICES):
            if TENCENT_CHOICES[key]['expires'] < now:
                del TENCENT_CHOICES[key]
        imported = {m.get('source', {}).get('remote_key'): m['id'] for m in all_meetings()}
        for row in rows:
            choice_id = uuid.uuid4().hex
            TENCENT_CHOICES[choice_id] = {'meeting': row, 'expires': now + 900}
            choices.append({k: row[k] for k in ('title', 'date', 'time', 'available', 'reason')})
            choices[-1].update(choice_id=choice_id, imported_id=imported.get(row['remote_key']))
    return choices


@app.post('/api/tencent/import', dependencies=[Depends(require_token)])
def import_tencent(body: schemas.TencentImport):
    b = body.model_dump(exclude_unset=True)
    with LOCK:
        choice = TENCENT_CHOICES.get(b.get('choice_id'))
        if not choice or choice['expires'] < time.monotonic():
            raise ValueError('会议选择已过期，请重新查询')
        row = choice['meeting']
        if not row['available']:
            raise ValueError(row['reason'])
        for existing in all_meetings():
            if existing.get('source', {}).get('remote_key') == row['remote_key']:
                return existing
        m = create_meeting({'title': row['title'], 'date': row['date'],
                            'source': {'type': 'tencent', 'meeting_id': row['remote_id'], 'remote_key': row['remote_key'],
                                       'sub_meeting_id': row.get('sub_meeting_id', '')}})
        m.update(status='analyzing')
        save(m)
        threading.Thread(target=run_analysis, args=(m['id'],), daemon=True).start()
    return m


@app.post('/api/mcp/dida-tools', dependencies=[Depends(require_token)])
def dida_tools(b: dict):
    return request('dida', 'tools/list')['tools']


@app.post('/api/mcp/tools', dependencies=[Depends(require_token)])
def tencent_tools(b: dict):
    result = invoke('tools/list')
    return [t for t in result.get('tools', []) if t['name'] in READ_TOOLS]


@app.post('/api/mcp/call', dependencies=[Depends(require_token)])
def call_tool(body: schemas.ToolCall):
    b = body.model_dump(exclude_unset=True)
    return invoke('tools/call', {'name': b['name'], 'arguments': b.get('arguments', {})})


@app.post('/api/meetings', dependencies=[Depends(require_token)])
def new_meeting(body: schemas.NewMeeting):
    b = body.model_dump(exclude_unset=True)
    with LOCK:
        return create_meeting(b)


@app.post('/api/analyze', dependencies=[Depends(require_token)])
def analyze_meeting(body: schemas.MeetingRequest):
    b = body.model_dump(exclude_unset=True)
    with LOCK:
        m = get(b['meeting_id'])
        if m['status'] == 'analyzing':
            raise ValueError('分析正在进行')
        if m['report']:
            raise ValueError('已有报告；为保留人工修改和同步记录，请新建会议重新分析')
        m.update(status='analyzing', error='')
        save(m)
        threading.Thread(target=run_analysis, args=(m['id'],), daemon=True).start()
        return m


@app.post('/api/review', dependencies=[Depends(require_token)])
def review_action(body: schemas.ReviewRequest):
    b = body.model_dump(exclude_unset=True)
    with LOCK:
        m = get(b['meeting_id'])
        if not m['report']:
            raise ValueError('请先分析')
        actions = m['report']['actions']
        a = next((a for a in actions if a['id'] == b['action_id']), None)
        if not a or a['status'] in ('synced', 'syncing', 'uncertain'):
            raise ValueError('此条目不存在、已同步或同步状态待核实')
        if b.get('delete'):
            actions.remove(a)
        else:
            values = b.get('values', {})
            editable = ('title', 'topic', 'owner', 'due_date', 'acceptance_criteria')
            changed = any(k in values and values[k] != a[k] for k in editable)
            for k in editable:
                if k in values:
                    if not isinstance(values[k], str) or len(values[k]) > 5000:
                        raise ValueError('字段长度或类型不正确')
                    a[k] = values[k].strip()
            if not a['title'] or not a['topic']:
                raise ValueError('行动内容和议题不能为空')
            if a['due_date']:
                date.fromisoformat(a['due_date'])
            if changed:
                a.update(confirmed=False, selected=False)
            else:
                if 'confirmed' in b:
                    a['confirmed'] = b['confirmed'] is True
                if 'selected' in b:
                    a['selected'] = b['selected'] is True
                if not a['confirmed']:
                    a['selected'] = False
        save(m)
        return m


@app.post('/api/sync', dependencies=[Depends(require_token)])
def sync_tasks(body: schemas.SyncRequest):
    b = body.model_dump(exclude_unset=True)
    with LOCK:
        m = get(b['meeting_id'])
        workflow().resume(m['id'], b.get('action_ids', []), b.get('project_id'))
        return get(m['id'])


@app.post('/api/resolve-uncertain', dependencies=[Depends(require_token)])
def resolve_uncertain(body: schemas.ResolveUncertainRequest):
    with LOCK:
        if body.meeting_id in RUNNING_ANALYSES:
            raise ValueError('会议正在处理中，请完成后再核实')
        return workflow().resolve_uncertain(body.meeting_id, body.action_id, body.resolution)


@app.delete('/api/meetings/{mid}', dependencies=[Depends(require_token)])
def delete_meeting(mid: str):
    with LOCK:
        try:
            m = get(mid)
        except ValueError:
            raise HTTPException(404, '会议不存在')
        if mid in RUNNING_ANALYSES or m['status'] == 'analyzing' or any(
            a['status'] == 'syncing' for a in (m.get('report') or {}).get('actions', [])
        ):
            raise HTTPException(409, '会议正在处理中，请处理完成后再删除')
        workflow().forget(mid)
        with connect() as db:
            db.execute('DELETE FROM meetings WHERE id=?', (mid,))
    return {'deleted_id': mid}


def main():
    load_env()
    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('PORT', '8765')))


if __name__ == '__main__':
    main()
