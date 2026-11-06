"""Aggregate explicitly reviewed development predictions; no model/API calls."""
import argparse
import hashlib
import json
from pathlib import Path

from .evaluation import score, ratio


def aggregate(results):
    counts = {'tp': 0, 'fp': 0, 'fn': 0, 'predicted': 0, 'gold': 0, 'supported': 0,
              'field_correct': {k: 0 for k in ('owner', 'due_date', 'acceptance_criteria')}}
    for result in results:
        for key in counts:
            if key != 'field_correct':
                counts[key] += result['counts'][key]
        for field in counts['field_correct']:
            counts['field_correct'][field] += result['counts']['field_correct'][field]
    tp, predicted, gold = counts['tp'], counts['predicted'], counts['gold']
    return {'counts': counts, 'metrics': {
        'precision': ratio(tp, predicted), 'recall': ratio(tp, gold),
        'f1': ratio(2*tp, predicted+gold),
        'evidence_support_rate': ratio(counts['supported'], predicted),
        'unsupported_rate': ratio(predicted-counts['supported'], predicted),
        **{f'{k}_accuracy_on_matched': ratio(v, tp) for k, v in counts['field_correct'].items()}}}


def reviewed_score(gold, prediction, review):
    if not (gold['case_id'] == prediction['case_id'] == review['case_id']):
        raise ValueError('case_id 不一致')
    if prediction['run_id'] != review['run_id']:
        raise ValueError('审核与预测 run_id 不一致')
    # An empty judgments array is not proof that a negative sample was reviewed.
    if review.get('review_origin') not in ('human', 'agent'):
        raise ValueError('请明确 review_origin=human 或 agent，不能使用 pending')
    if review.get('review_complete') is not True:
        raise ValueError('请在完成逐条裁定后设置 review_complete=true')
    if review['review_origin'] == 'human' and review.get('human_review_status') != 'completed':
        raise ValueError('人工审核状态尚未完成')
    return score(gold, prediction, review)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True, help='Development prediction directory')
    parser.add_argument('--gold-dir', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True, help='Frozen dev.jsonl')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text(encoding='utf-8'))
    cases = [json.loads(line) for line in args.dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not cases or any(c.get('split') != 'dev' for c in cases):
        raise ValueError('本命令只接受非空开发集，保留集不在此处评分')
    if len({c['case_id'] for c in cases}) != len(cases):
        raise ValueError('重复 case_id')
    pending, results, fingerprints = [], {}, {}
    for case in cases:
        cid = case['case_id']
        paths = [args.gold_dir/(cid+'.json'), args.run_dir/(cid+'.json'), args.run_dir/(cid+'.review.json')]
        for path in paths:
            fingerprints[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        gold, prediction, review = map(read, paths)
        expected = hashlib.sha256(json.dumps({'paragraphs': case['paragraphs'], 'meeting_date': case['meeting_date']}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if prediction['input_sha256'] != expected:
            raise ValueError('预测输入指纹不一致: '+cid)
        try:
            results[cid] = reviewed_score(gold, prediction, review)
        except ValueError as exc:
            pending.append({'case_id': cid, 'reason': str(exc)})
    if pending:
        print(json.dumps({'status': 'incomplete', 'unscored': pending}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    report = {'status': 'scored', 'case_count': len(cases), 'per_case': results,
              'micro': aggregate(results.values()), 'source_sha256': fingerprints,
              'review_origins': sorted({read(args.run_dir/(c['case_id']+'.review.json'))['review_origin'] for c in cases}),
              'note': '字段为严格字符串匹配；证据支持与工作范围匹配分别计分。Agent裁定不等于人工审核。'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps(report['micro'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
