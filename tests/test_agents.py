import unittest
from unittest.mock import patch
from meetmind import agents, core


class AgentsTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'id':'p1','speaker':'李','text':'我负责提交文档。','timestamp':'00:01'}]
        self.report={'mode':'llm','summary':'','actions':[core.new_action('文档','提交文档',['p1'],'李')]}
        self.aid=self.report['actions'][0]['id']

    def judgment(self,verdict):
        return {'judgments':[{'action_id':self.aid,'verdict':verdict,'issues':[] if verdict=='pass' else ['需处理'],'evidence_ref':['p1']}]}

    def test_verifier_failure_retains_actions_and_marks_unverified(self):
        with patch.object(agents,'model_json',side_effect=TimeoutError):
            result=agents.analyze(self.rows,'2026-11-04',initial_report=self.report)
        self.assertEqual(result['verification_status'],'incomplete')
        self.assertEqual(len(result['actions']),1)
        self.assertEqual(result['actions'][0]['verification']['verdict'],'uncertain')

    def test_reject_without_repair_keeps_audit(self):
        with patch.object(agents,'model_json',return_value=self.judgment('reject')):
            result=agents.analyze(self.rows,'2026-11-04',initial_report=self.report,max_repairs=0)
        self.assertEqual(result['actions'],[])
        self.assertEqual(len(result['rejected_actions']),1)

    def test_unknown_verification_ids_fail_closed_to_manual(self):
        j=self.judgment('reject');j['judgments'][0]['action_id']='unknown'
        with patch.object(agents,'model_json',return_value=j):
            result=agents.analyze(self.rows,'2026-11-04',initial_report=self.report)
        self.assertEqual(len(result['actions']),1)
        self.assertEqual(result['verification_status'],'incomplete')

    def test_at_most_one_repair_and_second_verification(self):
        correction={'actions':[{'title':'提交文档','owner':'李','evidence_ref':['p1']}]}
        def response(prompt,data):
            if prompt==agents.REPAIR_PROMPT:return correction
            aid=data['report']['actions'][0]['id']
            return {'judgments':[{'action_id':aid,'verdict':'revise','issues':['日期不明'],'evidence_ref':['p1']}]}
        with patch.object(agents,'model_json',side_effect=response) as call:
            result=agents.analyze(self.rows,'2026-11-04',initial_report=self.report)
        self.assertEqual(call.call_count,3)
        self.assertEqual(result['repair_count'],1)
        self.assertEqual(result['actions'][0]['verification']['verdict'],'revise')

    def test_duplicate_judgments_rejected(self):
        j=self.judgment('pass');j['judgments']*=2
        with self.assertRaises(ValueError):agents.validate_judgments(j,self.report['actions'],self.rows)


class AgentOptimizationTests(unittest.TestCase):
    def test_explicit_assent_owner_is_preserved_only_in_v2(self):
        rows=[{'id':'p1','speaker':'主持','text':'你明天把UI改完。'},
              {'id':'p2','speaker':'李','text':'没问题。'}]
        output={'actions':[{'title':'修改UI','owner':'李','evidence_ref':['p1','p2']}]}
        self.assertEqual(core.validate_report(output,rows)['actions'][0]['owner'],'')
        self.assertEqual(agents.validate_repair(output,rows)['actions'][0]['owner'],'李')
        output['actions'][0]['owner']='无关的人'
        self.assertEqual(agents.validate_repair(output,rows)['actions'][0]['owner'],'')

    def test_empty_report_skips_model(self):
        with patch.object(agents,'model_json') as remote:
            report=agents.analyze([], '2026-11-04', initial_report={'mode':'llm','actions':[]})
        remote.assert_not_called()
        self.assertEqual(report['verification_status'],'completed')

    def test_only_rejections_do_not_call_repair(self):
        state={'verification_status':'completed','judgments':[{'verdict':'reject'}],'repair_count':0,'max_repairs':1}
        self.assertEqual(agents.route(state),'finish')
