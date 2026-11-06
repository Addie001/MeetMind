"""Read only trace metadata for this experiment; exclude transcript inputs/outputs."""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from meetmind.server import load_env
load_env()
from langsmith import Client
folder=ROOT/'evaluation/runs/single-agent-v1'
rows=json.loads((folder/'execution-summary.json').read_text())
ids=[r['langsmith_run_id'] for r in rows if r.get('langsmith_run_id')]
client=Client()
select=['id','name','run_type','parent_run_id','start_time','end_time','error','total_tokens','prompt_tokens','completion_tokens']
remote=list(client.list_runs(run_ids=ids,select=select,limit=12))
found={str(r.id) for r in remote}
report={'expected_root_count':len(ids),'received_root_count':len(found),'missing_root_ids':sorted(set(ids)-found),
        'roots':[{'id':str(r.id),'name':r.name,'error':r.error,'start_time':str(r.start_time),'end_time':str(r.end_time)} for r in remote]}
children=[]
for rid in ids:
    for r in client.list_runs(parent_run_id=rid,run_type='llm',select=select,limit=5):
        children.append({'id':str(r.id),'parent_run_id':str(r.parent_run_id),'total_tokens':r.total_tokens,
                         'prompt_tokens':r.prompt_tokens,'completion_tokens':r.completion_tokens,'error':r.error})
report['llm_children']=children
(folder/'langsmith-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'received_roots':len(found),'expected_roots':len(ids),'llm_children':len(children)},ensure_ascii=False))
