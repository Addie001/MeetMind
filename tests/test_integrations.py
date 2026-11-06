import unittest
from unittest.mock import patch, Mock
from meetmind import integrations, core


class AdapterTests(unittest.TestCase):
    @patch.dict('os.environ', {'DIDA_BACKEND':'mcp', 'DIDA_MCP_CREATE_TOOL':'create_task',
                               'DIDA_MCP_TASK_MAPPING':'{"title":"title","project_id":"projectId","content":"content"}'})
    def test_dida_maps_fields_and_preserves_task_id(self):
        schema={'type':'object', 'required':['title','project_id'],
                'properties':{'title':{'type':'string'},'project_id':{'type':'string'},'content':{'type':'string'}}, 'additionalProperties':False}
        with patch('meetmind.mcp.request', side_effect=[{'tools':[{'name':'create_task','inputSchema':schema}]},
                                                       {'structuredContent':{'id':'task1'}}]) as remote:
            result=integrations.create_task({'title':'文档','projectId':'list1','content':'证据'})
            self.assertEqual(result['id'], 'task1')
            self.assertEqual(remote.call_args.args[2]['arguments'], {'title':'文档','project_id':'list1','content':'证据'})

    def test_bad_schema_does_not_call_write_tool(self):
        with patch('meetmind.mcp.request', return_value={'tools':[{'name':'create','inputSchema':{'type':'object','required':['project_id']}}]}) as remote:
            with self.assertRaises(ValueError):
                integrations.checked_call('dida','create',{'title':'task'})
            remote.assert_called_once()

    def test_tencent_only_fetches_raw_transcripts(self):
        rows=[{'id':'pid-7','timestamp':'00:01','speaker':'小林','text':'我负责提交文档。'}]
        with patch('meetmind.mcp.invoke', return_value={'structuredContent':{'paragraphs':rows}}) as remote:
            self.assertEqual(integrations.fetch_transcripts({'arguments':{'pid':'pid-7'}}),rows)
            self.assertEqual(remote.call_args.args[1]['name'],'get_transcripts_details')

    def test_tencent_rejects_lossy_structure(self):
        with patch('meetmind.mcp.invoke', return_value={'structuredContent':{'summary':'摘要'}}):
            with self.assertRaises(ValueError):
                integrations.fetch_transcripts({'arguments':{}})

    @patch.dict('os.environ', {'LLM_BASE_URL':'https://example.invalid/v1','LLM_MODEL':'test','LLM_API_KEY':'test-secret','LANGSMITH_TRACING':'false'})
    def test_tracing_records_actual_usage(self):
        run=Mock()
        result={'choices':[], 'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}}
        with patch('meetmind.core.get_current_run_tree', return_value=run), patch('meetmind.core.http_json', return_value=result):
            self.assertEqual(core.call_model({'model':'test','messages':[]}),result)
        self.assertEqual(run.set.call_args.kwargs['usage_metadata'], {'input_tokens':12,'output_tokens':5,'total_tokens':17})


if __name__ == '__main__':
    unittest.main()
