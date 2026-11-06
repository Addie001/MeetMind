"""Paired dev experiment: same frozen V1 extraction, verifier, at most one repair."""
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from meetmind.server import load_env
load_env()
from meetmind import agents
from meetmind.benchmark import run_case


def dump(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def main():
    assert urlsplit(os.environ['LLM_BASE_URL']).hostname=='api.deepseek.com'
    assert urlsplit(os.getenv('LANGSMITH_ENDPOINT','https://api.smith.langchain.com')).hostname=='api.smith.langchain.com'
    os.environ['LLM_MAX_RETRIES']='0'
    lock_path=ROOT/'evaluation/data/frozen/v1/lock.json'
    lock=json.loads(lock_path.read_text())
    for path,sha in lock['artifacts'].items():
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=sha:raise ValueError('冻结数据变化')
    folder=ROOT/'evaluation/runs/dual-agent-v1'
    folder.mkdir(parents=True,exist_ok=True)
    source_files=['meetmind/agents.py','meetmind/core.py','meetmind/benchmark.py']
    plan={'method':'paired reuse of V1 initial extraction; no gold sent to agents','max_repairs':1,
          'model':os.environ['LLM_MODEL'],'dataset_lock_sha256':hashlib.sha256(lock_path.read_bytes()).hexdigest(),
          'code_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in source_files}}
    experiment=folder/'experiment.json'
    if experiment.exists() and json.loads(experiment.read_text())!=plan:raise ValueError('拒绝混用实验版本')
    dump(experiment,plan)
    for name in ['dev','verify-only']:(folder/name).mkdir(exist_ok=True)
    for line in (ROOT/'evaluation/data/frozen/v1/dev.jsonl').read_text().splitlines():
        case=json.loads(line);cid=case['case_id'];path=folder/'dev'/(cid+'.json')
        if path.exists():continue
        baseline=json.loads((ROOT/'evaluation/runs/single-agent-v1/dev'/(cid+'.json')).read_text())
        initial={k:baseline[k] for k in ('summary','actions','mode')}
        def analyze(paragraphs,day):return agents.analyze(paragraphs,day,initial_report=initial,max_repairs=1)
        result=run_case(case,analyzer=analyze)
        result['initial_extraction_run_id']=baseline['run_id']
        result['cost_method']='paired cost includes saved baseline extraction; extra_* are actual new invocations'
        for key in ('call_count','latency_seconds','prompt_tokens','completion_tokens'):
            result['extra_'+key]=result[key]
            result[key]=result[key]+baseline[key] if result[key] is not None and baseline[key] is not None else None
        dump(path,result)
        first=next((h for h in result.get('agent_history',[]) if h['stage']=='verify'),None)
        if first:
            verify_only=agents.finish({'report':initial,'judgments':first['judgments'],'history':[first],
                                      'repair_count':0,'verification_status':'completed'})['report']
            view={**result,**verify_only,'run_id':result['run_id']+'-verify-only','derived_from_run_id':result['run_id']}
            calls=result['model_calls'][:1]
            view['model_calls']=calls
            view['call_count']=baseline['call_count']+len(calls)
            view['latency_seconds']=baseline['latency_seconds']+sum(c['latency_seconds'] for c in calls)
            view['timing_note']='derived first verifier arm: request time plus saved initial extraction; excludes graph overhead'
            for field in ('prompt_tokens','completion_tokens'):
                usage=[c.get('usage',{}).get(field) for c in calls]
                view[field]=baseline[field]+sum(usage) if baseline[field] is not None and all(type(v) is int for v in usage) else None
            for field in ('extra_call_count','extra_latency_seconds','extra_prompt_tokens','extra_completion_tokens'):view.pop(field,None)
            dump(folder/'verify-only'/(cid+'.json'),view)
        else:
            dump(folder/'verify-only'/(cid+'.json'),{**result,'comparison_note':'verification incomplete; retain original for manual review'})
        for arm in ('dev','verify-only'):
            pred=json.loads((folder/arm/(cid+'.json')).read_text())
            dump(folder/arm/(cid+'.review.json'),{'case_id':cid,'run_id':pred['run_id'],'review_origin':'pending',
                  'human_review_status':'pending','judgments':[{'prediction_id':a['id'],'gold_id':None,'evidence_supported':None} for a in pred['actions']]})
        print(json.dumps({'case_id':cid,'status':result['status'],'verification':result.get('verification_status'),
                          'repairs':result.get('repair_count'),'new_calls':result['extra_call_count']},ensure_ascii=False),flush=True)
    from langsmith.run_trees import get_cached_client
    get_cached_client().flush()


if __name__=='__main__':main()
