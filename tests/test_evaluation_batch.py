import unittest
from meetmind.evaluation_batch import aggregate, reviewed_score


class BatchEvaluationTests(unittest.TestCase):
    def test_micro_uses_counts_not_mean_of_percentages(self):
        def result(tp, predicted, gold):
            return {'counts': {'tp':tp,'fp':predicted-tp,'fn':gold-tp,'predicted':predicted,
                               'gold':gold,'supported':tp,'field_correct':{'owner':tp,'due_date':tp,'acceptance_criteria':0}}}
        combined = aggregate([result(1,1,1),result(1,9,9)])
        self.assertEqual(combined['metrics']['precision'], .2)
        self.assertEqual(combined['metrics']['recall'], .2)

    def test_empty_sample_still_requires_explicit_review(self):
        gold={'case_id':'x','actions':[]}
        prediction={'case_id':'x','run_id':'r','actions':[]}
        review={'case_id':'x','run_id':'r','judgments':[],'review_origin':'pending'}
        with self.assertRaises(ValueError):reviewed_score(gold,prediction,review)
        review.update(review_origin='agent',review_complete=True)
        self.assertIsNone(reviewed_score(gold,prediction,review)['metrics']['precision'])

    def test_cross_run_review_rejected(self):
        with self.assertRaises(ValueError):
            reviewed_score({'case_id':'x'}, {'case_id':'x','run_id':'a'}, {'case_id':'x','run_id':'b'})
