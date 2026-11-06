import unittest
from unittest.mock import patch
from meetmind import core, integrations


class DidaTopicTests(unittest.TestCase):
    def test_schema_discovery_once_per_batch_and_refresh_next_batch(self):
        tools = [{'name': 'list_columns', 'inputSchema': {'type': 'object'}}]
        with patch('meetmind.mcp.request', return_value={'tools': tools}) as remote:
            for _ in range(2):
                with integrations.tool_schema_cache():
                    integrations.prepare_call('dida', 'list_columns', {})
                    integrations.prepare_call('dida', 'list_columns', {})
            self.assertEqual(remote.call_count, 2)
            integrations.prepare_call('dida', 'list_columns', {})
            self.assertEqual(remote.call_count, 3)

    def test_nested_rate_limit_is_reported_without_replaying_write(self):
        from exceptiongroup import ExceptionGroup
        from meetmind import mcp
        error = ExceptionGroup('task group', [ExceptionGroup('session', [RuntimeError('Rate limit exceeded')])])
        with patch('meetmind.mcp._request', side_effect=error) as remote:
            with self.assertRaisesRegex(ValueError, '服务端限流'):
                mcp.request('dida', 'tools/call', {'name': 'create_task'})
            self.assertEqual(remote.call_count, 1)

    def test_payload_keeps_task_details_but_never_attaches_evidence(self):
        action=core.new_action('接口联调','提交接口文档',['p1'],'小林','2026-11-06','覆盖登录接口')
        meeting={'title':'项目周会','paragraphs':[{'id':'p1','speaker':'小林','timestamp':'00:15','text':'仅应留在本地的原始引用'}]}
        task=core.task_payload(action,meeting,'project1')
        self.assertNotIn('原始引用',task['content'])
        self.assertNotIn('原文证据',task['content'])
        self.assertNotIn('00:15',task['content'])
        self.assertIn('小林',task['content'])
        self.assertIn('覆盖登录接口',task['content'])
        self.assertEqual(task['_topic'],'接口联调')
        self.assertTrue(task['dueDate'].startswith('2026-11-06'))

    @patch.dict('os.environ',{'DIDA_BACKEND':'mcp','DIDA_MCP_CREATE_TOOL':'create_task','DIDA_MCP_TASK_MAPPING':'{"task":"$payload"}'})
    def test_same_topic_reuses_column_and_different_topic_creates_own_column(self):
        columns=[];tasks=[];created=[]
        schema={'type':'object','properties':{'task':{'type':'object','required':['title','projectId','columnId']}},'required':['task']}
        tools=[{'name':'list_columns','inputSchema':{'type':'object','required':['project_id']}},
               {'name':'create_column','inputSchema':{'type':'object','required':['project_id','column']}},
               {'name':'create_task','inputSchema':schema}]
        def remote(service,method,params=None):
            if method=='tools/list':return {'tools':tools}
            name,args=params['name'],params['arguments']
            if name=='list_columns':return {'structuredContent':{'result':list(columns)}}
            if name=='create_column':
                column={'id':'column'+str(len(columns)+1),**args['column']}
                columns.append(column);created.append(column['name'])
                return {'structuredContent':column}
            if name=='create_task':
                tasks.append(args['task']);return {'structuredContent':{'id':'task'+str(len(tasks))}}
            raise AssertionError(name)
        with patch('meetmind.mcp.request',side_effect=remote):
            for topic in ('接口联调','接口联调','上线验收'):
                integrations.create_task({'title':'提交文档','projectId':'project1','content':'验收标准：完成文档','_topic':topic})
        self.assertEqual(created,['接口联调','上线验收'])
        self.assertEqual([task['columnId'] for task in tasks],['column1','column1','column2'])
        self.assertTrue(all('_topic' not in task for task in tasks))

    def test_duplicate_column_names_do_not_choose_arbitrarily(self):
        with patch('meetmind.integrations.checked_call',return_value=[{'id':'one','name':'议题'},{'id':'two','name':'议题'}]):
            with self.assertRaises(ValueError):integrations.ensure_topic_column('project','议题')

    @patch.dict('os.environ',{'DIDA_BACKEND':'mcp'})
    def test_group_creation_failure_never_sends_task_creation(self):
        with patch('meetmind.integrations.checked_call',side_effect=[[],ValueError('分组创建超时')]), patch('meetmind.mcp.request') as remote:
            with self.assertRaises(integrations.NotSentError):
                integrations.create_task({'title':'任务','projectId':'project','content':'说明','_topic':'议题'})
            remote.assert_not_called()

    @patch.dict('os.environ',{'DIDA_BACKEND':'mcp','DIDA_MCP_TASK_MAPPING':'{"title":"title","project_id":"projectId","content":"content"}'})
    def test_mapping_cannot_drop_column_id(self):
        with patch('meetmind.integrations.ensure_topic_column',return_value='column1'),patch('meetmind.mcp.request') as remote:
            with self.assertRaises(integrations.NotSentError):
                integrations.create_task({'title':'任务','projectId':'project','content':'说明','_topic':'议题'})
            remote.assert_not_called()

    @patch.dict('os.environ',{'DIDA_BACKEND':'api'})
    def test_api_mode_does_not_silently_create_ungrouped_task(self):
        with patch('meetmind.integrations.dida') as remote:
            with self.assertRaises(integrations.NotSentError):
                integrations.create_task({'title':'任务','projectId':'project','content':'说明','_topic':'议题'})
            remote.assert_not_called()
