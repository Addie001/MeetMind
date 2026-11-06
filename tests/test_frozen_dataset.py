"""Verify the real v1 freeze and the explicitly requested cleaning boundary."""
import hashlib
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class FrozenDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT/'evaluation/data/frozen/v1/lock.json').exists():
            raise unittest.SkipTest('私有冻结评估集未包含在仓库中；恢复 evaluation/data 后可运行')

    def test_locked_artifacts_unchanged(self):
        lock=json.loads((ROOT/'evaluation/data/frozen/v1/lock.json').read_text())
        for path,digest in lock['artifacts'].items():
            self.assertEqual(hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),digest,path)

    def test_group_isolation_and_counts(self):
        groups={}
        for split,count in [('dev',9),('test',3)]:
            rows=[json.loads(l) for l in (ROOT/f'evaluation/data/frozen/v1/{split}.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),count)
            for row in rows:groups.setdefault(row['group_id'],set()).add(split)
        self.assertTrue(all(len(s)==1 for s in groups.values()))
        self.assertEqual(len(groups),11)

    def test_cleaning_is_suffix_without_rewriting(self):
        original=json.loads((ROOT/'evaluation/data/raw/tencent/tencent-07.json').read_text())
        clean=json.loads((ROOT/'evaluation/data/processed/tencent-07.clean.v1.json').read_text())
        self.assertEqual(clean['paragraphs'],original['paragraphs'][21:])
        self.assertEqual(len(clean['paragraphs']),13)

    def test_gold_references_and_human_review_status(self):
        for split in ('dev','test'):
            for line in (ROOT/f'evaluation/data/frozen/v1/{split}.jsonl').read_text().splitlines():
                case=json.loads(line)
                gold=json.loads((ROOT/f'evaluation/data/annotations/v1/{case["case_id"]}.json').read_text())
                self.assertEqual(gold['human_review_status'],'pending')
                ids={p['id'] for p in case['paragraphs']}
                for action in gold['actions']:
                    self.assertTrue(set(action['evidence_ref'])<=ids)
                    self.assertTrue(action['evidence_ref'])
