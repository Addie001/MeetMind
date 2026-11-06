import json
import unittest
from datetime import datetime
from unittest.mock import patch
from meetmind import tencent, server
import test_workflow


class TencentDataTests(unittest.TestCase):
    def test_mcp_speaker_names_survive_import(self):
        names = ['李素芬', 'Dawn', '李素芬', 'Dawn', '李素芬', 'Dawn', '李素芬']
        rows = [{'pid': str(i), 'start_time': 18000 + i * 7000,
                 'speaker': {'user_name': name},
                 'sentences': [{'words': [{'text': '我负责提交测试结果。'}]}]}
                for i, name in enumerate(names)]
        with patch('meetmind.tencent.call', return_value={'minutes': {'paragraphs': rows}}):
            paragraphs = tencent.fetch_record({'record_file_id': '7'})
        self.assertEqual([p['speaker'] for p in paragraphs], names)
        from meetmind.core import analyze
        with patch.dict('os.environ', {'LLM_BASE_URL': '', 'LLM_MODEL': ''}):
            report = analyze(paragraphs, '2026-11-02')
        self.assertEqual([a['owner'] for a in report['actions']], names)

    def test_speaker_formats_and_missing_names(self):
        for value, expected in [({'user_name': 'Dawn'}, 'Dawn'), ('小李', '小李'),
                                ({'user_name': ''}, '未知发言人'), (None, '未知发言人')]:
            with self.subTest(value=value):
                row = {'id': '0', 'timestamp': '00:18', 'speaker': value, 'text': '原话'}
                self.assertEqual(tencent.normalize_paragraph(row)['speaker'], expected)
        self.assertEqual(tencent.speaker_name({'speaker': {}, 'speaker_info': {'username': '小林'}}), '小林')

    def test_gateway_wrapper_and_meeting_pagination(self):
        row={'meeting_id':'42','subject':'周会','start_time':'2026-11-01T10:00:00+08:00','records_total_count':1}
        wrapped={'data':{'status_code':200,'body':json.dumps({'meeting_info_list':[row],'has_more':True,'next_page_token':'next'})}}
        second={'meeting_info_list':[{**row,'meeting_id':'43','records_total_count':0}],'has_more':False}
        with patch('meetmind.tencent.mcp.invoke',side_effect=[{'structuredContent':wrapped},{'structuredContent':second}]) as remote:
            rows=tencent.list_meetings(3,datetime(2026,11,1,12,tzinfo=tencent.TZ))
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['date'],'2026-11-01')
            self.assertTrue(rows[0]['available'])
            self.assertFalse(rows[1]['available'])
            self.assertEqual(remote.call_args.args[1]['arguments']['page_token'],'next')
            self.assertIn('2026-10-29',remote.call_args_list[0].args[1]['arguments']['start_time'])

    def test_transcript_words_speaker_timestamp_and_pagination(self):
        def p(pid,text):
            return {'pid':pid,'start_time':65000,'speaker_info':{'username':'小林'},
                    'sentences':[{'words':[{'text':text}]}]}
        with patch('meetmind.tencent.call',side_effect=[{'minutes':{'paragraphs':[p('0','我负责文档。')]},'more':True},
                                                       {'minutes':{'paragraphs':[p('1','周五完成。')]},'more':False}]) as remote:
            rows=tencent.fetch_record({'record_file_id':'7'})
        self.assertEqual(rows[0],{'id':'0','timestamp':'01:05','speaker':'小林','text':'我负责文档。'})
        self.assertEqual(rows[1]['id'],'1')
        self.assertEqual(remote.call_args.args[1]['pid'],'1')

    def test_recording_ids_are_unique(self):
        data={'record_meetings':[{'record_files':[{'record_file_id':'7'},{'record_file_id':'7'},{'record_file_id':'8'}]}]}
        with patch('meetmind.tencent.pages',return_value=iter([data])):
            self.assertEqual(tencent.recording_ids('42'),['7','8'])

    def test_recurring_recordings_do_not_mix_occurrences(self):
        data={'record_meetings':[
            {'sub_meeting_id':'one','record_files':[{'record_file_id':'7'}]},
            {'sub_meeting_id':'two','record_files':[{'record_file_id':'8'}]}]}
        with patch('meetmind.tencent.pages',return_value=iter([data])):
            self.assertEqual(tencent.recording_ids('42','two'),['8'])
        with patch('meetmind.tencent.pages',return_value=iter([{'record_meetings':[{'record_files':[{'record_file_id':'7'}]}]}])):
            with self.assertRaises(ValueError):
                tencent.recording_ids('42','two')

    def test_no_partial_import_on_pagination_error(self):
        data={'minutes':{'paragraphs':[{'id':'abc','timestamp':'00:01','speaker':'甲','text':'原文'}]},'more':True}
        with patch('meetmind.tencent.call',return_value=data):
            with self.assertRaises(ValueError):
                tencent.fetch_record({'record_file_id':'7'})

    def test_record_id_namespaces_evidence(self):
        with patch('meetmind.tencent.recording_ids',return_value=['7','8']),patch('meetmind.tencent.fetch_record',return_value=[{'id':'0','timestamp':'00:01','speaker':'甲','text':'原文'}]):
            rows=tencent.fetch_meeting('42')
            self.assertEqual([p['id'] for p in rows],['7:0','8:0'])
            self.assertEqual(rows[1]['original_pid'],'0')

    def test_invalid_range_and_gateway_error(self):
        with self.assertRaises(ValueError): tencent.list_meetings(30)
        with self.assertRaises(ValueError): tencent.unwrap({'data':{'status_code':403,'body':'{}'}})


class ImportEndpointTests(unittest.TestCase):
    setUp = test_workflow.WorkflowTests.setUp
    tearDown = test_workflow.WorkflowTests.tearDown
    post = test_workflow.WorkflowTests.post
    def test_server_owned_metadata_and_duplicate_import(self):
        row={'remote_id':'42','remote_key':'42:','title':'自动主题','date':'2026-11-01',
             'time':'2026-11-01 10:00','available':True,'reason':''}
        with patch('meetmind.server.tencent.list_meetings',return_value=[row]):
            code,rows=self.post('/api/tencent/meetings',{'days':3})
        self.assertEqual(code,200)
        with patch('meetmind.server.run_analysis'):
            code,m=self.post('/api/tencent/import',{'choice_id':rows[0]['choice_id'],'title':'伪造主题'})
            self.assertEqual(code,200)
            self.assertEqual(m['title'],'自动主题')
            self.assertEqual(m['date'],'2026-11-01')
            _,again=self.post('/api/tencent/import',{'choice_id':rows[0]['choice_id']})
            self.assertEqual(again['id'],m['id'])
        self.assertEqual(self.post('/api/tencent/import',{'choice_id':'invalid'})[0],400)

    def test_no_recording_is_not_importable(self):
        row={'remote_id':'42','remote_key':'42:','title':'无录制','date':'2026-11-01',
             'time':'2026-11-01 10:00','available':False,'reason':'无云录制'}
        with patch('meetmind.server.tencent.list_meetings',return_value=[row]):
            _,rows=self.post('/api/tencent/meetings',{'days':3})
        self.assertEqual(self.post('/api/tencent/import',{'choice_id':rows[0]['choice_id']})[0],400)
