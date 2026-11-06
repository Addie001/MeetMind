"""MCP adapters. Explicit mappings avoid guessing third-party tool contracts."""
import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from jsonschema import validate, ValidationError
from . import mcp
from .core import dida


def mapped_arguments(payload, mapping):
    """{"project_id":"projectId"} maps a tool argument to a canonical payload key."""
    if not mapping:
        return payload
    result = {}
    for arg, field in mapping.items():
        if field == '$payload':
            result[arg] = payload
        elif field in payload:
            result[arg] = payload[field]
    return result


class NotSentError(ValueError):
    """Preflight failed; no task creation request was sent."""


_tool_schemas = ContextVar('tool_schemas', default=None)


@contextmanager
def tool_schema_cache():
    """Discover tools once per sync batch, without retaining credentials or stale schemas."""
    token = _tool_schemas.set({})
    try:
        yield
    finally:
        _tool_schemas.reset(token)


def prepare_call(service, name, arguments):
    if not name:
        raise ValueError(f'请配置 {service.upper()} MCP 工具名')
    cache = _tool_schemas.get()
    if cache is not None and service in cache:
        tools = cache[service]
    else:
        tools = mcp.request(service, 'tools/list')['tools']
        if cache is not None:
            cache[service] = tools
    tool = next((t for t in tools if t['name'] == name), None)
    if tool is None:
        raise ValueError(f'MCP 服务没有配置的工具：{name}')
    try:
        validate(arguments, tool['inputSchema'])
    except ValidationError:
        raise ValueError('MCP 参数与工具 schema 不匹配，请检查字段映射') from None
    return {'name': name, 'arguments': arguments}


def checked_call(service, name, arguments):
    params = prepare_call(service, name, arguments)
    return call_prepared(service, params)


def call_prepared(service, params):
    try:
        return mcp.result_json(mcp.request(service, 'tools/call', params))
    except mcp.ToolNotFoundError as exc:
        # sync() catches errors and continues to the next item within the same
        # cache scope. Drop stale schemas BEFORE attempting one discovery refresh.
        cache = _tool_schemas.get()
        if cache is not None:
            cache.pop(service, None)
        try:
            tools = mcp.request(service, 'tools/list')['tools']
            if cache is not None:
                cache[service] = tools
            message = '工具不存在或工具名无效，已刷新工具列表；请检查工具配置后手动重新同步'
        except Exception:
            message = '工具不存在或工具名无效，刷新工具列表失败；请稍后手动重新同步'
        # Never replay a write here. Only a positively identified protocol
        # rejection qualifies; timeout/network/business errors propagate unchanged.
        raise NotSentError(message) from exc


def dida_mode():
    mode = os.getenv('DIDA_BACKEND', 'mcp')
    if mode not in ('mcp', 'api'):
        raise ValueError('DIDA_BACKEND 必须是 mcp 或 api')
    return mode


def list_projects():
    if dida_mode() == 'api':
        return dida('/project')
    result = checked_call('dida', os.getenv('DIDA_MCP_PROJECTS_TOOL', ''), {})
    if isinstance(result, dict):
        result = result.get('projects', result.get('data'))
    if not isinstance(result, list) or any(not isinstance(p, dict) or not p.get('id') or not p.get('name') for p in result):
        raise ValueError('滴答工具未返回清单数组（id/name）')
    return result


def ensure_topic_column(project_id, topic):
    """Columns group tasks within a project; project groups would group whole lists."""
    columns = checked_call('dida', 'list_columns', {'project_id': project_id})
    if not isinstance(columns, list) or any(not isinstance(c, dict) for c in columns):
        raise ValueError('滴答未返回有效分组列表')
    matches = [c for c in columns if c.get('name') == topic]
    if len(matches) > 1:
        raise ValueError('目标清单存在多个同名议题分组，请先在滴答中合并或重命名')
    if matches:
        column = matches[0]
    else:
        column = checked_call('dida', 'create_column',
                              {'project_id': project_id, 'column': {'name': topic, 'projectId': project_id}})
    if not isinstance(column, dict) or not column.get('id'):
        raise ValueError('滴答未返回有效分组 ID')
    if column.get('projectId') and column['projectId'] != project_id:
        raise ValueError('滴答返回的分组不属于目标清单')
    return str(column['id'])


def create_task(payload):
    payload = dict(payload)
    topic = payload.pop('_topic', None)
    if dida_mode() == 'api':
        if topic is not None:
            raise NotSentError('按议题分组写入需要使用滴答 MCP；没有创建任务')
        if not os.getenv('DIDA_ACCESS_TOKEN'):
            raise NotSentError('尚未配置 DIDA_ACCESS_TOKEN；没有发送创建任务请求')
        return dida('/task', payload)
    try:
        if topic is not None:
            if not isinstance(topic, str) or not topic.strip():
                raise ValueError('议题名称不能为空')
            payload['columnId'] = ensure_topic_column(payload['projectId'], topic.strip())
        mapping = json.loads(os.getenv('DIDA_MCP_TASK_MAPPING', '{}'))
        if mapping and '$payload' not in mapping.values():
            required = {'title', 'projectId', 'content'} | ({'dueDate'} if payload.get('dueDate') else set()) | ({'columnId'} if payload.get('columnId') else set())
            if not required.issubset(set(mapping.values())):
                raise ValueError('滴答字段映射必须保留标题、清单、任务描述、分组和已设置的截止日期')
        params = prepare_call('dida', os.getenv('DIDA_MCP_CREATE_TOOL', ''), mapped_arguments(payload, mapping))
    except Exception as exc:
        raise NotSentError('未发送创建任务请求：' + (str(exc) if isinstance(exc, ValueError) else '请检查滴答连接和配置')) from exc
    result = call_prepared('dida', params)
    if isinstance(result, dict):
        result = result.get('task', result.get('data', result))
    if not isinstance(result, dict) or not result.get('id'):
        raise ValueError('滴答 MCP 未返回任务 ID')
    return result


def fetch_transcripts(source):
    from . import tencent
    if source.get('meeting_id'):
        return tencent.fetch_meeting(source['meeting_id'], source.get('sub_meeting_id', ''))
    return tencent.fetch_record(source['arguments'])

