"""Run the frozen baseline once, preserving outcomes without task synchronization."""
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    from meetmind.server import load_env
    load_env()
    if urlsplit(os.environ['LLM_BASE_URL']).hostname != 'api.deepseek.com':
        raise ValueError('模型目的地与授权不符')
    if urlsplit(os.getenv('LANGSMITH_ENDPOINT', 'https://api.smith.langchain.com')).hostname != 'api.smith.langchain.com':
        raise ValueError('追踪目的地与授权不符')
    if not os.getenv('LLM_MODEL') or not os.getenv('LLM_API_KEY'):
        raise ValueError('缺少真实模型配置')
    os.environ['LLM_MAX_RETRIES'] = '0'
    from meetmind.benchmark import run_case
    lock_path = ROOT/'evaluation/data/frozen/v1/lock.json'
    lock = json.loads(lock_path.read_text())
    for path, expected in lock['artifacts'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest() != expected:
            raise ValueError('冻结文件变化: '+path)
    folder = ROOT/'evaluation/runs/single-agent-v1'
    folder.mkdir(parents=True, exist_ok=False)
    plan = {'model': os.environ['LLM_MODEL'], 'max_retries': 0,
            'dataset_lock_sha256': hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            'annotation_status': lock['annotation_status'],
            'tracing_requested': os.getenv('LANGSMITH_TRACING'),
            'code_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                           ['meetmind/core.py','meetmind/benchmark.py','meetmind/telemetry.py','meetmind/evaluation.py']}}
    (folder/'experiment.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2)+'\n')
    outcomes = []
    for split in ('dev', 'test'):
        output = folder/split
        output.mkdir()
        for line in (ROOT/f'evaluation/data/frozen/v1/{split}.jsonl').read_text().splitlines():
            case = json.loads(line)
            result = run_case(case)
            (output/(case['case_id']+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
            review = {'case_id': case['case_id'], 'run_id': result['run_id'],
                      'review_origin':'pending', 'human_review_status':'pending',
                      'judgments':[{'prediction_id':a['id'], 'gold_id':None, 'evidence_supported':None} for a in result['actions']]}
            (output/(case['case_id']+'.review.json')).write_text(json.dumps(review, ensure_ascii=False, indent=2)+'\n')
            outcome = {k:result.get(k) for k in ('case_id','run_id','status','mode','error_type','latency_seconds','call_count','prompt_tokens','completion_tokens','langsmith_run_id','langsmith_trace_id')}
            outcome['split'] = split
            outcomes.append(outcome)
            (folder/'execution-summary.json').write_text(json.dumps(outcomes, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
    if os.getenv('LANGSMITH_TRACING','').lower()=='true':
        from langsmith.run_trees import get_cached_client
        get_cached_client().flush()


if __name__ == '__main__':
    main()
