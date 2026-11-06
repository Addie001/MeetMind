import unittest
from unittest.mock import patch
from meetmind.evaluation import score
from meetmind.benchmark import run_case
from meetmind import core


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.gold = {'actions': [{'id': 'g1', 'owner': '王', 'due_date': '', 'acceptance_criteria': ''}]}
        self.pred = {'actions': [{'id': 'p1', 'owner': '', 'due_date': '', 'acceptance_criteria': ''}, {'id': 'p2'}]}
        self.review = {'judgments': [{'prediction_id': 'p1', 'gold_id': 'g1', 'evidence_supported': True},
                                     {'prediction_id': 'p2', 'gold_id': None, 'evidence_supported': False}]}

    def test_metrics_separate_matching_fields_and_evidence(self):
        metrics = score(self.gold, self.pred, self.review)['metrics']
        self.assertEqual(metrics['precision'], .5)
        self.assertEqual(metrics['recall'], 1)
        self.assertEqual(metrics['owner_accuracy_on_matched'], 0)
        self.assertEqual(metrics['evidence_support_rate'], .5)

    def test_duplicate_match_rejected(self):
        self.review['judgments'][1]['gold_id'] = 'g1'
        with self.assertRaises(ValueError):
            score(self.gold, self.pred, self.review)

    def test_unreviewed_rejected(self):
        self.review['judgments'][0]['evidence_supported'] = None
        with self.assertRaises(ValueError):
            score(self.gold, self.pred, self.review)

    def test_empty_denominators_are_unknown(self):
        result = score({'actions': []}, {'actions': []}, {'judgments': []})
        self.assertIsNone(result['metrics']['precision'])

    @patch.dict('os.environ', {'LLM_BASE_URL': 'https://example.invalid', 'LLM_MODEL': 'test',
                               'LLM_MAX_RETRIES': '1', 'LANGSMITH_TRACING': 'false'})
    def test_retry_is_measured_and_usage_not_fabricated(self):
        response = {'choices': [{'message': {'content': '{"actions":[]}'}}]}
        with patch('meetmind.core.http_json', side_effect=[core.TransientRequestError('timeout'), response]) as remote, patch('meetmind.core.time.sleep'):
            result = run_case({'case_id': 'x', 'paragraphs': [], 'meeting_date': '2026-11-04'})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['call_count'], 2)
        self.assertIsNone(result['prompt_tokens'])
        self.assertEqual(remote.call_count, 2)

    @patch.dict('os.environ', {'LLM_BASE_URL': 'https://example.invalid', 'LLM_MODEL': 'test', 'LLM_MAX_RETRIES': '2'})
    def test_permanent_errors_are_not_retried(self):
        with patch('meetmind.core.http_json', side_effect=ValueError('auth')) as remote:
            result = run_case({'case_id': 'x', 'paragraphs': [], 'meeting_date': '2026-11-04'})
        self.assertEqual(result['status'], 'error')
        self.assertEqual(remote.call_count, 1)


if __name__ == '__main__':
    unittest.main()
