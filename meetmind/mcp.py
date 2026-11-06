"""Official MCP SDK transport; credentials never enter graph state or trace payloads."""
import asyncio
import json
import os
import platform
import re
from datetime import timedelta
import httpx
from mcp import ClientSession
from mcp.shared.exceptions import McpError
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

READ_TOOLS = {'get_meeting', 'get_meeting_by_code', 'get_user_meetings',
              'get_user_ended_meetings', 'get_records_list', 'get_transcripts_paragraphs',
              'get_transcripts_details', 'get_meeting_participants'}


class ToolNotFoundError(ValueError):
    """A protocol-level rejection of this tool name, before tool execution."""


def rejected_tool_name(error, name):
    children = getattr(error, 'exceptions', ())
    if children:
        # Mixed failures (e.g. a disconnect during teardown) remain uncertain.
        return all(rejected_tool_name(child, name) for child in children)
    if not isinstance(error, McpError) or error.error.code not in (-32601, -32602):
        return False
    tool = r"['\"]?" + re.escape(name) + r"['\"]?"
    return any(re.fullmatch(pattern, error.error.message, re.IGNORECASE) for pattern in (
        r'(?:Unknown tool|No such tool|Invalid tool name):?\s+' + tool,
        r'Tool\s+' + tool + r'\s+(?:not found|does not exist)',
    ))


async def _request(service, method, params):
    prefix = service.upper()
    url = os.getenv(prefix + '_MCP_URL')
    if not url:
        raise ValueError(f'请配置 {prefix}_MCP_URL')
    transport = os.getenv(prefix + '_MCP_TRANSPORT', 'streamable_http')
    if transport not in ('sse', 'streamable_http', 'jsonrpc') or (transport == 'jsonrpc' and service != 'tencent'):
        raise ValueError('MCP transport 必须为 sse 或 streamable_http；腾讯官方代理另支持 jsonrpc')
    headers = {}
    if service == 'tencent':
        token = os.getenv('TENCENT_MEETING_TOKEN')
        if not token:
            raise ValueError('请配置 TENCENT_MEETING_TOKEN')
        headers = {'X-Tencent-Meeting-Token': token,
                   'X-Skill-Version': os.getenv('TENCENT_SKILL_VERSION', '')}
    else:
        token = os.getenv(prefix + '_MCP_BEARER_TOKEN') or os.getenv(prefix + '_MCP_API_KEY')
        auth_type = os.getenv(prefix + '_MCP_AUTH', 'bearer' if service == 'dida' else 'api_key')
        if auth_type not in ('bearer', 'api_key'):
            raise ValueError('MCP_AUTH 必须为 bearer 或 api_key')
        if token:
            headers = {'Authorization': 'Bearer ' + token} if auth_type == 'bearer' else {'x-api-key': token}
    if service == 'tencent' and method == 'tools/call':
        params = {**params, 'arguments': {**params.get('arguments', {}),
                  '_client_info': {'os': platform.system() + '-' + platform.release(),
                                   'agent': 'meetmind', 'model': os.getenv('LLM_MODEL') or 'rules'}}}
    async with httpx.AsyncClient(headers=headers, timeout=60) as client:
        if transport == 'jsonrpc':
            if method not in ('tools/list', 'tools/call'):
                raise ValueError('不支持的腾讯 JSON-RPC 方法')
            response = await client.post(url, json={'jsonrpc': '2.0', 'method': method,
                                                   'params': params, 'id': 1})
            response.raise_for_status()
            data = response.json()
            result = data.get('result')
            if data.get('error') or not isinstance(result, dict) or result.get('error') or result.get('isError'):
                raise ValueError('腾讯 MCP 拒绝请求，请检查凭据、工具参数与权限')
            return result
        context = (sse_client(url, headers=headers, timeout=60, sse_read_timeout=90)
                   if transport == 'sse' else streamable_http_client(url, http_client=client))
        async with context as streams:
            async with ClientSession(streams[0], streams[1], read_timeout_seconds=timedelta(seconds=90)) as session:
                await session.initialize()
                if method == 'tools/list':
                    tools, cursor = [], None
                    while True:
                        result = await session.list_tools(cursor=cursor)
                        tools.extend(t.model_dump(by_alias=True, exclude_none=True) for t in result.tools)
                        cursor = result.nextCursor
                        if not cursor:
                            return {'tools': tools}
                if method != 'tools/call':
                    raise ValueError('不支持的 MCP 方法')
                result = await session.call_tool(params['name'], params.get('arguments', {}))
                if result.isError:
                    raise ValueError('MCP 工具返回错误，请检查参数和权限')
                return result.model_dump(by_alias=True, exclude_none=True)


def request(service, method, params=None):
    try:
        return asyncio.run(_request(service, method, params or {}))
    except ValueError:
        raise
    except Exception as exc:
        if (service == 'dida' and method == 'tools/call' and
                rejected_tool_name(exc, (params or {}).get('name', ''))):
            raise ToolNotFoundError('MCP 服务明确拒绝了不存在或无效的工具名') from None
        def is_rate_limit(error):
            return ('rate limit' in str(error).lower() or
                    isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429 or
                    any(is_rate_limit(child) for child in getattr(error, 'exceptions', ())))
        if is_rate_limit(exc):
            raise ValueError(f'{service} MCP 请求过于频繁，服务端限流，请稍后再试') from None
        raise ValueError(f'{service} MCP 调用失败，请检查地址、传输方式、凭据与工具参数') from None


def invoke(method, params=None):
    if method == 'tools/call' and (params or {}).get('name') not in READ_TOOLS:
        raise ValueError('首版仅允许调用会议和转写只读工具')
    return request('tencent', method, params)


def result_json(result):
    if result.get('structuredContent') is not None:
        value = result['structuredContent']
        # FastMCP wraps non-object results in {result: ...}.
        return value['result'] if isinstance(value, dict) and set(value) == {'result'} else value
    for block in result.get('content', []):
        if block.get('type') == 'text':
            try:
                return json.loads(block['text'])
            except (ValueError, KeyError):
                continue
    raise ValueError('MCP 工具未返回结构化 JSON，无法安全转换')
