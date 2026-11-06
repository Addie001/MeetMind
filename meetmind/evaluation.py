"""Offline, human-adjudicated extraction evaluation. Never calls external tools."""
import argparse
import json
from pathlib import Path


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def score(gold, prediction, review):
    """Review maps prediction IDs to gold IDs, independently of field correctness.

    Every prediction needs a human evidence_supported bool; null means unreviewed.
    Duplicate predictions cannot both match the same gold action.
    """
    if prediction.get('status', 'ok') != 'ok':
        raise ValueError('失败运行不能作为成功提取评分；请单独统计运行失败率')
    expected = {a['id']: a for a in gold['actions']}
    actual = {a['id']: a for a in prediction['actions']}
    if len(expected) != len(gold['actions']) or len(actual) != len(prediction['actions']):
        raise ValueError('行动项 ID 必须唯一')
    judged = {a['prediction_id']: a for a in review['judgments']}
    if len(judged) != len(review['judgments']) or set(judged) != set(actual):
        raise ValueError('每个预测必须有且只有一条人工裁定')
    used = set()
    fields = {key: 0 for key in ('owner', 'due_date', 'acceptance_criteria')}
    supported = 0
    for pid, judgment in judged.items():
        if type(judgment.get('evidence_supported')) is not bool:
            raise ValueError('证据支持关系尚未人工裁定')
        supported += judgment['evidence_supported']
        gid = judgment.get('gold_id')
        if gid is None:
            continue
        if gid not in expected or gid in used:
            raise ValueError('匹配必须指向存在的 golden ID，且为一对一')
        used.add(gid)
        for key in fields:
            # Missing gold fields are annotation errors, not implicit empty labels.
            if not isinstance(expected[gid].get(key), str):
                raise ValueError(f'gold 缺少字符串字段 {key}；未知值请显式标注空字符串')
            fields[key] += actual[pid].get(key, '') == expected[gid][key]
    tp, count, total = len(used), len(actual), len(expected)
    return {'counts': {'tp': tp, 'fp': count-tp, 'fn': total-tp,
                       'predicted': count, 'gold': total, 'supported': supported,
                       'field_correct': fields},
            'metrics': {'precision': ratio(tp, count), 'recall': ratio(tp, total),
                        'f1': ratio(2*tp, count+total),
                        'evidence_support_rate': ratio(supported, count),
                        'unsupported_rate': ratio(count-supported, count),
                        **{f'{key}_accuracy_on_matched': ratio(value, tp)
                           for key, value in fields.items()}}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gold', type=Path)
    parser.add_argument('prediction', type=Path)
    parser.add_argument('review', type=Path)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text(encoding='utf-8'))
    gold, prediction, review = map(read, (args.gold, args.prediction, args.review))
    if not (gold['case_id'] == prediction['case_id'] == review['case_id']):
        raise ValueError('case_id 不一致')
    if review['run_id'] != prediction['run_id']:
        raise ValueError('裁定不属于本次预测')
    print(json.dumps(score(gold, prediction, review), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
