"""One-time Agent adjudication after final implementation freeze. No tuning."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from meetmind.evaluation_batch import reviewed_score, aggregate

freeze=json.loads((ROOT/'evaluation/runs/dual-agent-v2/final-config.json').read_text())
for name,sha in freeze['files_sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha
DECISIONS={
 'single-agent-v1':{'tencent-07':[('g1',True),('g2',True),('g3',True),('g4',True)],
                    'tencent-08':[('g1',True),(None,False)],
                    'text-04':[(None,True),(None,True),('g1',False),('g2',True)]},
 'dual-agent-v2':{'tencent-07':[('g1',True),('g2',True),('g3',True),('g4',True)],
                  'tencent-08':[('g1',True),(None,False)],'text-04':[('g1',True),('g2',True)]}}
metrics=[]
for experiment,decisions in DECISIONS.items():
 folder=ROOT/'evaluation/runs'/experiment
 per_case={};runs=[]
 for cid,labels in decisions.items():
  pred=json.loads((folder/'test'/(cid+'.json')).read_text());assert len(pred['actions'])==len(labels)
  gold=json.loads((ROOT/'evaluation/data/annotations/v1'/(cid+'.json')).read_text())
  rows=[]
  for action,(gid,support) in zip(pred['actions'],labels):
   reason='完整转写支持行动及非空断言；缺失字段单独计分。'
   if cid=='tencent-08' and gid is None:reason='“再排期，现在说不准”是未形成确定交付的条件性考虑；预测省去不确定性，当作已接受承诺。'
   if cid=='text-04' and gid is None:reason='原文支持暂缓决定，但冻结口径不把已停止的发版/部署当新增工作交付。'
   if cid=='text-04' and gid=='g1' and not support:reason='明天是被取消的演示日期，不是通知客户延期的截止日。'
   rows.append({'prediction_id':action['id'],'gold_id':gid,'evidence_supported':support,'reason':reason})
  review={'case_id':cid,'run_id':pred['run_id'],'review_origin':'agent','review_complete':True,
          'human_review_status':'pending','judgments':rows,'phase':'held-out after final-config freeze'}
  path=folder/'test'/(cid+'.review.json')
  backup=folder/'test-review-templates'/path.name;backup.parent.mkdir(exist_ok=True)
  if not backup.exists():backup.write_bytes(path.read_bytes())
  path.write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n')
  per_case[cid]=reviewed_score(gold,pred,review);runs.append(pred)
 report={'experiment':experiment,'phase':'held-out final','review_origin':'agent',
         'gold_human_review_status':'pending','per_case':per_case,'micro':aggregate(per_case.values()),
         'calls':sum(r['call_count'] for r in runs),'paired_latency_seconds':sum(r['latency_seconds'] for r in runs),
         'prompt_tokens':sum(r['prompt_tokens'] for r in runs),'completion_tokens':sum(r['completion_tokens'] for r in runs)}
 path=folder/'test-quality.agent.json'
 with path.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
 metrics.append(report)
lines=['# 冻结方案后的保留集检验','','3份输入、7条预先冻结初标。实现先在final-config.json固定，再打开保留集预测；本轮没有依据保留集错误调参。所有裁定由Agent完成，待独立人工复核。','',
       '| 配置 | precision | recall | 证据支持率 | 负责人严格匹配 | 日期严格匹配 | 调用数 | 配对耗时秒 |',
       '| --- | --- | --- | --- | --- | --- | --- | --- |']
for r in metrics:
 m=r['micro']['metrics'];fmt=lambda k:f'{m[k]*100:.2f}%'
 lines.append(f"| {r['experiment']} | {fmt('precision')} | {fmt('recall')} | {fmt('evidence_support_rate')} | {fmt('owner_accuracy_on_matched')} | {fmt('due_date_accuracy_on_matched')} | {r['calls']} | {r['paired_latency_seconds']:.2f} |")
lines+=['','## 结论与未解决问题','',
        '- 双Agent保留了全部7条初标有效行动，修正了同名场景的两处负责人漏填和将被取消演示日期误作通知截止日的问题。',
        '- 仍错误保留tencent-08的“如果坚持再排期，现在说不准”事项；核验Agent把尚不确定的考虑当作已接受条件承诺，未要求修正。不能用开发集100%宣称可靠拦截所有条件幻觉。',
        '- 单Agent的两条暂停/停止事项有原文依据，却不属于冻结的有效交付，因此是范围/状态误提，不是凭空编造。',
        '- 修正后工作流能改正部分字段错误，也可能遗漏自己的判断错误；人工确认边界必须保留。',
        '- 共用同一次初始提取，成本为原始提取加新增调用，不是完整冷启动重跑。只有3份保留输入，百分比很敏感，暂不作统计显著性或泛化保证。',
        '- 该保留集现已打开；后续若依据这些错误改进，它就只能用于回归，新的独立结论应另补未见样本。',
        '- 原始输入、gold及已冻结实现未因本次结果改动。']
(ROOT/'evaluation/runs/dual-agent-v2/HELDOUT.md').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines[:9]))
