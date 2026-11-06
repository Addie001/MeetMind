"""Explicit Agent adjudication for both paired dev arms, no test access."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
folder=ROOT/'evaluation/runs/dual-agent-v2'
MATCHES={'tencent-01':['g1','g2'],'tencent-02':['g1','g2'],'tencent-03':['g1','g2','g3'],
         'tencent-04':['g1','g2','g3'],'tencent-05':[],'tencent-06':['g3','g4','g1','g5','g2'],
         'text-01':['g1','g2'],'text-02':[],'text-03':['g1','g2','g3']}
for arm in ['dev','verify-only']:
 for cid,matches in MATCHES.items():
  p=folder/arm/(cid+'.json');d=json.loads(p.read_text());assert len(matches)==len(d['actions'])
  rows=[]
  for a,gid in zip(d['actions'],matches):
   unsupported=cid=='tencent-02' and gid=='g2' and bool(a['due_date'])
   rows.append({'prediction_id':a['id'],'gold_id':gid,'evidence_supported':not unsupported,
                'reason':'原文为预计审批时间，不能当确定交付截止日' if unsupported else
                         '按冻结原文与口径独立核对，行动及非空断言有证据支持；字段缺失/别名另按字段指标统计。'})
  review={'case_id':cid,'run_id':d['run_id'],'review_origin':'agent','review_complete':True,
          'human_review_status':'pending','judgments':rows}
  if not rows:review['negative_sample_reason']='原文无确定工作行动，空结果符合冻结口径。'
  (folder/arm/(cid+'.review.json')).write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
