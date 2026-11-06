import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from meetmind import server
from meetmind.core import parse_transcript, validate_report, analyze


class EvidenceTests(unittest.TestCase):
    def test_natural_self_reference_keeps_proposed_speaker_owner(self):
        ps = parse_transcript('[00:33] 李素芬：我今天上午改一下重试机制，今天下班前提测。')
        action = dict(title='修复重试机制', owner='李素芬', evidence_ref=['p1'])
        self.assertEqual(validate_report({'actions': [action]}, ps)['actions'][0]['owner'], '李素芬')
        ps[0]['text'] = '我们今天讨论一下重试机制。'
        self.assertEqual(validate_report({'actions': [action]}, ps)['actions'][0]['owner'], '')

    def test_original_is_preserved(self):
        p = parse_transcript('[00:12] 小林：我负责交付。')[0]
        self.assertEqual((p['timestamp'], p['speaker'], p['text']), ('00:12', '小林', '我负责交付。'))

    def test_reject_invalid_evidence_and_fabricated_owner(self):
        ps = parse_transcript('[00:12] 小林：需要完成文档。')
        action = dict(title='文档', topic='开发', owner='小周', evidence_ref=['p1'])
        result = validate_report({'actions': [action]}, ps)
        self.assertEqual(result['actions'][0]['owner'], '')
        action['evidence_ref'] = ['nonexistent']
        with self.assertRaises(ValueError):
            validate_report({'actions': [action]}, ps)

    @patch.dict('os.environ', {'LLM_BASE_URL': '', 'LLM_MODEL': ''})
    def test_no_invented_date_or_owner(self):
        report = analyze(parse_transcript('[00:12] 主持人：需要完成文档，周五交付。'), '2026-10-29')
        self.assertEqual(len(report['actions']), 1)
        self.assertEqual(report['actions'][0]['owner'], '')
        self.assertEqual(report['actions'][0]['due_date'], '')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict('os.environ', {'LLM_BASE_URL': '', 'LLM_MODEL': '', 'LANGSMITH_TRACING': 'false'})
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        server.DB = Path(self.tmp.name) / 'test.db'
        with server.connect() as db:
            db.execute('CREATE TABLE meetings (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.client = TestClient(server.app, base_url='http://127.0.0.1')
        self.m = server.create_meeting({'title': '周会', 'date':'2026-10-29', 'transcript': '[00:12] 小林：我负责提交文档。'})
        server.run_analysis(self.m['id'])
        self.m = server.get(self.m['id'])
        self.a = self.m['report']['actions'][0]

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def post(self, path, body, token=server.TOKEN):
        response = self.client.post(path, json={'meeting_id': self.m['id'], **body},
                                    headers={'X-MeetMind-Token': token})
        return response.status_code, response.json()

    def review(self, **extra):
        return self.post('/api/review', {'action_id': self.a['id'], **extra})

    @patch('meetmind.integrations.create_task', return_value={'id': 'remote-task'})
    def test_confirmation_edit_invalidation_and_no_duplicate(self, remote):
        body={'project_id':'list1', 'action_ids':[self.a['id']]}
        self.assertEqual(self.post('/api/sync', body)[0], 400)
        self.review(confirmed=True, selected=True)
        self.review(values={'owner':'小周'}, confirmed=True, selected=True)
        a = server.get(self.m['id'])['report']['actions'][0]
        self.assertFalse(a['confirmed'])
        self.assertFalse(a['selected'])
        self.assertEqual(self.post('/api/sync', body)[0], 400)
        self.review(confirmed=True, selected=True)
        self.assertEqual(self.post('/api/sync', body)[0], 200)
        self.assertEqual(self.post('/api/sync', body)[0], 400)
        self.assertEqual(remote.call_count, 1)
        self.assertNotIn('原文证据', remote.call_args.args[0]['content'])
        self.assertNotIn('小林：我负责提交文档。', remote.call_args.args[0]['content'])
        self.assertEqual(remote.call_args.args[0]['_topic'], self.a['topic'])
        self.assertEqual(self.review(values={'title':'changed'})[0], 400)

    @patch('meetmind.integrations.create_task', side_effect=ValueError('timeout'))
    def test_uncertain_write_cannot_retry(self, remote):
        self.review(confirmed=True, selected=True)
        body={'project_id':'list1', 'action_ids':[self.a['id']]}
        self.assertEqual(self.post('/api/sync', body)[0], 200)
        self.assertEqual(server.get(self.m['id'])['report']['actions'][0]['status'], 'uncertain')
        self.assertEqual(self.post('/api/sync', body)[0], 400)
        self.assertEqual(remote.call_count, 1)

    @patch('meetmind.integrations.create_task', side_effect=server.integrations.NotSentError('未发送'))
    def test_preflight_failure_remains_retryable(self, remote):
        self.review(confirmed=True, selected=True)
        self.post('/api/sync', {'project_id':'list1','action_ids':[self.a['id']]})
        self.assertEqual(server.get(self.m['id'])['report']['actions'][0]['status'], 'pending')
        self.assertEqual(server.workflow().pending(self.m['id']), ('sync_tasks',))

    def test_graph_pause_survives_recreation(self):
        self.assertEqual(server.workflow().pending(self.m['id']), ('sync_tasks',))
        self.assertEqual(server.get(self.m['id'])['report']['actions'][0]['status'], 'pending')
        with patch('meetmind.integrations.create_task', return_value={'id': 'after-restart'}) as remote:
            self.review(confirmed=True, selected=True)
            # Each workflow instance opens a fresh SQLite connection (like a restart).
            server.workflow().resume(self.m['id'], [self.a['id']], 'list1')
            self.assertEqual(server.workflow().pending(self.m['id']), ())
            remote.assert_called_once()

    def test_empty_report_ends_without_sync(self):
        m = server.create_meeting({'title': '闲聊', 'transcript': '[00:12] 小林：今天天气不错。'})
        with patch('meetmind.integrations.create_task') as remote:
            server.run_analysis(m['id'])
            self.assertEqual(server.workflow().pending(m['id']), ())
            self.assertEqual(server.get(m['id'])['report']['actions'], [])
            remote.assert_not_called()

    def test_partial_sync_returns_to_pause(self):
        m = server.create_meeting({'title':'两项', 'transcript':'[00:01] 甲：我负责提交文档。\n[00:02] 乙：我负责提交测试。'})
        server.run_analysis(m['id'])
        m = server.get(m['id'])
        a, b = m['report']['actions']
        for item in m['report']['actions']:
            item.update(confirmed=True, selected=True)
        server.save(m)
        with patch('meetmind.integrations.create_task', side_effect=[{'id':'one'}, {'id':'two'}]) as remote:
            server.workflow().resume(m['id'], [a['id']], 'list1')
            self.assertEqual(server.workflow().pending(m['id']), ('sync_tasks',))
            server.workflow().resume(m['id'], [b['id']], 'list1')
            self.assertEqual(server.workflow().pending(m['id']), ())
            self.assertEqual(remote.call_count, 2)

    def test_legacy_report_can_resume_without_reextracting(self):
        m = server.create_meeting({'title':'旧数据', 'transcript':'[00:01] 甲：我负责提交文档。'})
        m['report'] = analyze(m['paragraphs'], m['date'])
        a = m['report']['actions'][0]
        a.update(confirmed=True, selected=True)
        server.save(m)
        with patch('meetmind.integrations.create_task', return_value={'id':'legacy'}) as remote:
            server.workflow().resume(m['id'], [a['id']], 'list1')
            remote.assert_called_once()

    def test_request_token_required(self):
        self.assertEqual(self.post('/api/analyze', {}, token='invalid')[0], 403)

    def make_uncertain(self):
        self.review(confirmed=True, selected=True)
        with patch('meetmind.integrations.create_task', side_effect=TimeoutError('timeout')):
            self.post('/api/sync', {'project_id': 'list1', 'action_ids': [self.a['id']]})
        self.assertEqual(server.workflow().pending(self.m['id']), ())

    def test_manual_created_records_verification_and_never_writes_again(self):
        self.make_uncertain()
        original = server.get(self.m['id'])['report']['actions'][0]['sync_error']
        with patch('meetmind.mcp.request') as tools, patch('meetmind.integrations.create_task') as create:
            code, meeting = self.post('/api/resolve-uncertain', {'action_id': self.a['id'], 'resolution': 'created'})
            self.assertEqual(code, 200)
            action = meeting['report']['actions'][0]
            self.assertEqual(action['status'], 'synced')
            self.assertIsNone(action['external_id'])
            record = action['verification_history'][0]
            self.assertEqual(record['method'], 'manual')
            self.assertEqual(record['previous_sync_error'], original)
            self.assertTrue(record['verified_at'].endswith('+08:00'))
            self.assertEqual(record['topic'], action['topic'])
            self.assertEqual(self.post('/api/sync', {'project_id': 'list1', 'action_ids': [self.a['id']]})[0], 400)
            self.assertEqual(self.post('/api/resolve-uncertain', {'action_id': self.a['id'], 'resolution': 'not_created'})[0], 400)
            create.assert_not_called()
            tools.assert_not_called()

    def test_manual_not_created_rearms_finished_graph_but_waits_for_user_sync(self):
        self.make_uncertain()
        with patch('meetmind.mcp.request') as tools, patch('meetmind.integrations.create_task', return_value={'id': 'verified-retry'}) as create:
            code, meeting = self.post('/api/resolve-uncertain', {'action_id': self.a['id'], 'resolution': 'not_created'})
            self.assertEqual(code, 200)
            action = meeting['report']['actions'][0]
            self.assertEqual(action['status'], 'pending')
            self.assertEqual(action['sync_error'], '')
            self.assertEqual(action['verification_history'][0]['resolution'], 'not_created')
            create.assert_not_called()
            tools.assert_not_called()
            self.assertEqual(server.workflow().pending(self.m['id']), ('sync_tasks',))
            body = {'project_id': 'list1', 'action_ids': [self.a['id']]}
            self.assertEqual(self.post('/api/sync', body)[0], 200)
            self.assertEqual(server.get(self.m['id'])['report']['actions'][0]['external_id'], 'verified-retry')
            self.assertEqual(self.post('/api/sync', body)[0], 400)
            create.assert_called_once()

    def test_manual_resolution_requires_token_uncertain_and_valid_choice(self):
        body = {'action_id': self.a['id'], 'resolution': 'created'}
        self.assertEqual(self.post('/api/resolve-uncertain', body, token='invalid')[0], 403)
        self.assertEqual(self.post('/api/resolve-uncertain', body)[0], 400)
        self.make_uncertain()
        self.assertEqual(self.post('/api/resolve-uncertain', {**body, 'resolution': 'auto'})[0], 400)
        self.assertEqual(server.get(self.m['id'])['report']['actions'][0]['status'], 'uncertain')

    def test_manual_retry_preserves_other_synced_tasks(self):
        self.make_uncertain()
        m = server.get(self.m['id'])
        other = {**m['report']['actions'][0], 'id': 'other', 'status': 'synced', 'external_id': 'existing'}
        m['report']['actions'].append(other)
        server.save(m)
        self.post('/api/resolve-uncertain', {'action_id': self.a['id'], 'resolution': 'not_created'})
        with patch('meetmind.integrations.create_task', return_value={'id': 'new'}) as create:
            self.post('/api/sync', {'project_id': 'list1', 'action_ids': [self.a['id']]})
            create.assert_called_once()
        self.assertEqual(server.get(m['id'])['report']['actions'][1]['external_id'], 'existing')

    def test_manual_resolution_preserves_pause_when_other_tasks_are_pending(self):
        m = server.get(self.m['id'])
        first = m['report']['actions'][0]
        first.update(confirmed=True, selected=True)
        second = {**first, 'id': 'second'}
        m['report']['actions'].append(second)
        server.save(m)
        with patch('meetmind.integrations.create_task', side_effect=TimeoutError()):
            self.post('/api/sync', {'project_id': 'list1', 'action_ids': [first['id']]})
        self.assertEqual(server.workflow().pending(m['id']), ('sync_tasks',))
        code, _ = self.post('/api/resolve-uncertain', {'action_id': first['id'], 'resolution': 'not_created'})
        self.assertEqual(code, 200)
        self.assertEqual(server.workflow().pending(m['id']), ('sync_tasks',))
        with patch('meetmind.integrations.create_task', side_effect=[{'id': 'first'}, {'id': 'second'}]) as create:
            code, _ = self.post('/api/sync', {'project_id': 'list1', 'action_ids': [first['id'], second['id']]})
            self.assertEqual(code, 200)
            self.assertEqual(create.call_count, 2)
        self.assertTrue(all(a['status'] == 'synced' for a in server.get(m['id'])['report']['actions']))

    def test_tool_dispatch_rejection_returns_pending_refreshes_and_waits_for_next_sync(self):
        from mcp.shared.exceptions import McpError
        from mcp.types import ErrorData
        self.review(confirmed=True, selected=True)
        body = {'project_id': 'list1', 'action_ids': [self.a['id']]}
        tools = [{'name': 'create_task', 'inputSchema': {'type': 'object'}}]
        events = []
        async def request(service, method, params):
            events.append(method)
            if method == 'tools/list':
                return {'tools': tools}
            if events.count('tools/call') == 1:
                raise McpError(ErrorData(code=-32602, message='Unknown tool: create_task'))
            return {'structuredContent': {'id': 'after-manual-retry'}}
        with patch.dict('os.environ', {'DIDA_BACKEND': 'mcp', 'DIDA_MCP_CREATE_TOOL': 'create_task',
                                      'DIDA_MCP_TASK_MAPPING': '{"task":"$payload"}'}), \
                patch('meetmind.integrations.ensure_topic_column', return_value='column1'), \
                patch('meetmind.mcp._request', side_effect=request):
            self.assertEqual(self.post('/api/sync', body)[0], 200)
            action = server.get(self.m['id'])['report']['actions'][0]
            self.assertEqual(action['status'], 'pending')
            self.assertIn('已刷新工具列表', action['sync_error'])
            self.assertEqual(events, ['tools/list', 'tools/call', 'tools/list'])
            self.assertEqual(self.post('/api/sync', body)[0], 200)
            self.assertEqual(server.get(self.m['id'])['report']['actions'][0]['status'], 'synced')
            self.assertEqual(events.count('tools/call'), 2)

    def test_local_web_page(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('MeetMind', response.text)



if __name__ == '__main__':
    unittest.main()
