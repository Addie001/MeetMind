import tempfile
import unittest
from unittest.mock import patch
from meetmind import agents, core, durable_agents


class DurableAgentTests(unittest.TestCase):
    def test_completed_version_reused_and_date_change_invalidates(self):
        report={'mode':'llm','actions':[]}
        with tempfile.TemporaryDirectory() as folder, patch.object(core,'analyze',return_value=report) as extract, patch.object(agents,'model_json') as verifier:
            first=durable_agents.analyze([], '2026-11-04',folder+'/checkpoints.sqlite')
            second=durable_agents.analyze([], '2026-11-04',folder+'/checkpoints.sqlite')
            self.assertEqual(first,second)
            self.assertEqual(extract.call_count,1)
            durable_agents.analyze([], '2026-11-05',folder+'/checkpoints.sqlite')
            self.assertEqual(extract.call_count,2)
            verifier.assert_not_called()

    def test_resume_after_extract_does_not_repeat_model_extraction(self):
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        from contextlib import closing
        rows=[{'id':'p1','speaker':'李','text':'我负责文档。'}]
        action=core.new_action('文档','提交文档',['p1'],'李')
        report={'mode':'llm','actions':[action]}
        judgment={'judgments':[{'action_id':action['id'],'verdict':'pass','issues':[],'evidence_ref':['p1']}]}
        config={'configurable':{'thread_id':'resume-test'}}
        with tempfile.TemporaryDirectory() as folder, patch.object(core,'analyze',return_value=report) as extract, patch.object(agents,'model_json',return_value=judgment) as verify:
            with closing(sqlite3.connect(folder+'/state.sqlite',check_same_thread=False)) as conn:
                graph=agents.build_graph(SqliteSaver(conn))
                graph.invoke({'paragraphs':rows,'meeting_date':'2026-11-04','max_repairs':1},config,interrupt_after=['extract'])
                self.assertEqual(graph.get_state(config).next,('verify',))
            with closing(sqlite3.connect(folder+'/state.sqlite',check_same_thread=False)) as conn:
                graph=agents.build_graph(SqliteSaver(conn))
                result=graph.invoke(None,config)
                self.assertEqual(result['report']['verification_status'],'completed')
            self.assertEqual(extract.call_count,1)
            self.assertEqual(verify.call_count,1)
