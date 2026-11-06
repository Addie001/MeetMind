"""Check remote metadata for dual-agent traces, without fetching inputs or outputs."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from meetmind.server import load_env
load_env()
from langsmith import Client

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('experiment',choices=['dual-agent-v1','dual-agent-v2'])
parser.add_argument('--split',choices=['dev','test'],default='dev')
args=parser.parse_args()
folder=ROOT/'evaluation/runs'/args.experiment
local=[json.loads(p.read_text()) for p in sorted((folder/args.split).glob('*.json')) if '.review.' not in p.name]
client=Client();select=['id','name','run_type','parent_run_id','start_time','end_time','error','prompt_tokens','completion_tokens']
ids=[r['langsmith_run_id'] for r in local]
roots=list(client.list_runs(run_ids=ids,select=select,limit=len(ids)))
report={'expected_roots':len(ids),'received_roots':len(roots),'checks':[]}
for row in local:
    children=list(client.list_runs(trace_id=row['langsmith_trace_id'],run_type='llm',select=select,limit=20))
    usage={key:sum(getattr(r,key) or 0 for r in children) for key in ['prompt_tokens','completion_tokens']}
    good=len(children)==row['extra_call_count'] and all(usage[key]==row['extra_'+key] for key in usage)
    report['checks'].append({'case_id':row['case_id'],'trace_id':row['langsmith_trace_id'],
                             'expected_llm_calls':row['extra_call_count'],'received_llm_calls':len(children),
                             'usage':usage,'matches_local':good})
path=folder/('langsmith-'+args.split+'.json')
path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False))
