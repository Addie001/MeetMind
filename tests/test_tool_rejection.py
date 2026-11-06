"""Only protocol-level tool dispatch rejection is safe to retry manually."""
import unittest
from unittest.mock import patch
import httpx
from exceptiongroup import ExceptionGroup
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from meetmind import mcp, integrations


def rejected(message='Unknown tool: create_task', code=-32602):
    return McpError(ErrorData(code=code, message=message))


class ToolRejectionTests(unittest.TestCase):
    def test_exact_protocol_rejection_preserves_type_through_task_groups(self):
        for error in (rejected(), ExceptionGroup('session', [rejected()]),
                      rejected('Tool "create_task" not found', -32601),
                      rejected('Invalid tool name: create_task')):
            with self.subTest(error=error), patch('meetmind.mcp._request', side_effect=error) as remote:
                with self.assertRaises(mcp.ToolNotFoundError):
                    mcp.request('dida', 'tools/call', {'name': 'create_task'})
                remote.assert_called_once()

    def test_other_errors_and_ambiguous_messages_remain_uncertain(self):
        request = httpx.Request('POST', 'https://example.invalid')
        server_error = httpx.HTTPStatusError('Unknown tool: create_task', request=request,
                                             response=httpx.Response(503, request=request))
        errors = [TimeoutError(), httpx.ConnectError('network'), server_error,
                  rejected(code=-32603), rejected('Unknown tool: different_tool'),
                  rejected('backend failed: Unknown tool: create_task'),
                  ValueError('Unknown tool: create_task'),
                  ExceptionGroup('mixed', [rejected(), TimeoutError()])]
        for error in errors:
            with self.subTest(error=error), patch('meetmind.mcp._request', side_effect=error):
                with self.assertRaises(ValueError) as caught:
                    mcp.request('dida', 'tools/call', {'name': 'create_task'})
                self.assertNotIsInstance(caught.exception, mcp.ToolNotFoundError)

    @patch.dict('os.environ', {'DIDA_BACKEND': 'mcp', 'DIDA_MCP_CREATE_TOOL': 'create_task',
                              'DIDA_MCP_TASK_MAPPING': '{"task":"$payload"}'})
    def test_refresh_once_invalidate_stale_cache_and_never_replay_write(self):
        schema = {'type': 'object'}
        old = [{'name': 'create_task', 'inputSchema': schema}]
        new = [{'name': 'list_columns', 'inputSchema': schema}]
        with integrations.tool_schema_cache():
            with patch('meetmind.mcp.request', side_effect=[{'tools': old}, mcp.ToolNotFoundError('rejected'),
                                                           {'tools': new}]) as remote:
                with self.assertRaises(integrations.NotSentError):
                    integrations.create_task({'title': 'task', 'projectId': 'project'})
                self.assertEqual([call.args[1] for call in remote.call_args_list],
                                 ['tools/list', 'tools/call', 'tools/list'])
            # The upper sync loop can catch the failure and continue. It must
            # now see the refreshed list, not the stale create_task entry.
            with patch('meetmind.mcp.request') as remote:
                integrations.prepare_call('dida', 'list_columns', {})
                with self.assertRaisesRegex(ValueError, '没有配置的工具'):
                    integrations.prepare_call('dida', 'create_task', {})
                remote.assert_not_called()

    def test_failed_refresh_leaves_cache_empty_for_next_call(self):
        tools = [{'name': 'list_columns', 'inputSchema': {'type': 'object'}}]
        with integrations.tool_schema_cache(), patch('meetmind.mcp.request', side_effect=[
                {'tools': tools}, mcp.ToolNotFoundError('missing'), TimeoutError(),
                {'tools': tools}, {'structuredContent': {'result': []}}]) as remote:
            with self.assertRaises(integrations.NotSentError):
                integrations.checked_call('dida', 'list_columns', {})
            self.assertEqual(integrations.checked_call('dida', 'list_columns', {}), [])
            self.assertEqual([c.args[1] for c in remote.call_args_list],
                             ['tools/list', 'tools/call', 'tools/list', 'tools/list', 'tools/call'])

    @patch.dict('os.environ', {'DIDA_BACKEND': 'mcp', 'DIDA_MCP_CREATE_TOOL': 'create_task'})
    def test_timeout_during_task_creation_does_not_refresh_or_become_not_sent(self):
        with patch('meetmind.integrations.prepare_call', return_value={'name': 'create_task', 'arguments': {}}), \
                patch('meetmind.mcp.request', side_effect=TimeoutError()) as remote:
            with self.assertRaises(TimeoutError):
                integrations.create_task({'title': 'task'})
            remote.assert_called_once()
