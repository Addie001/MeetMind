"""Run extraction only; writes immutable artifacts, never syncs tasks."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from .telemetry import model_calls
from langsmith import traceable, get_current_run_tree


@traceable(name="MeetMind extraction benchmark", run_type="chain")
def run_case(case, analyzer=None):
    from . import core
    analyzer = analyzer or core.analyze
    calls = []
    token = model_calls.set(calls)
    started = time.perf_counter()
    result = {'case_id': case['case_id'], 'run_id': uuid.uuid4().hex,
              'created_at': datetime.now(timezone.utc).isoformat(),
              'input_sha256': hashlib.sha256(json.dumps(
                  {'paragraphs': case['paragraphs'], 'meeting_date': case['meeting_date']},
                  sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              'model': os.getenv('LLM_MODEL'), 'base_url': os.getenv('LLM_BASE_URL'),
              'extractor_sha256': hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
              'max_retries': os.getenv('LLM_MAX_RETRIES', '0'),
              'timeout_seconds': os.getenv('LLM_TIMEOUT_SECONDS', '90')}
    run = get_current_run_tree()
    if run:
        result['langsmith_run_id'] = str(run.id)
        result['langsmith_trace_id'] = str(run.trace_id)
        run.add_metadata({k: result[k] for k in ('run_id', 'case_id', 'input_sha256', 'extractor_sha256', 'model')})
    try:
        report = analyzer(case['paragraphs'], case['meeting_date'])
        result.update(status='ok', **report)
    except Exception as exc:
        # No raw exception text: provider messages may contain sensitive data.
        result.update(status='error', error_type=type(exc).__name__, actions=[])
    finally:
        model_calls.reset(token)
    result.update(latency_seconds=time.perf_counter()-started, model_calls=calls,
                  call_count=len(calls))
    for key in ('prompt_tokens', 'completion_tokens'):
        result[key] = (sum(c['usage'][key] for c in calls)
                       if calls and all(type(c.get('usage', {}).get(key)) is int for c in calls)
                       else None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path, help='JSONL, each line is one meeting')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Load only inputs; gold labels never enter the extraction prompt.
    cases = [json.loads(line) for line in args.dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
    ids = [c['case_id'] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError('case_id 必须唯一')
    args.output.mkdir(parents=True, exist_ok=True)
    for case in cases:
        result = run_case(case)
        path = args.output / (result['run_id'] + '.json')
        with path.open('x', encoding='utf-8') as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
        review = {'case_id': case['case_id'], 'run_id': result['run_id'],
                  'judgments': [{'prediction_id': a['id'], 'gold_id': None,
                                 'evidence_supported': None} for a in result['actions']]}
        with path.with_suffix('.review.json').open('x', encoding='utf-8') as output:
            json.dump(review, output, ensure_ascii=False, indent=2)
        print(json.dumps({'case_id': case['case_id'], 'status': result['status'], 'artifact': str(path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
