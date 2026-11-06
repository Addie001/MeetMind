"""Persist explicit Agent adjudication of v1 dev outputs; never opens test predictions."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
folder=ROOT/'evaluation/runs/single-agent-v1/dev'
# Gold matches are authored by reading each prediction and frozen source, not title similarity.
DECISIONS={
 'tencent-01': [('g1',True,[],'原文明确重试修改和当日提测。'),('g2',True,['owner_missing'],'跨段接受UI任务；负责人漏填属于信息缺失，不是编造。')],
 'tencent-02': [('g1',True,[],'Mock调通及今日下午得到直接支持。'),('g2',False,['unsupported_due_date'],'后天只是预计资质通过时间，2026-11-06不能作为确定截止日；行动及审批条件本身有支持。')],
 'tencent-03': [('g1',True,['owner_missing'],'CSS任务和当天截止有效，但漏填直接被分派的小李。'),('g2',True,[],'老段对接第三方并在18点前反馈，日期粒度正确。'),('g3',True,['owner_missing'],'验收字段保留老段确认后才修改的条件；负责人小李漏填。')],
 'tencent-04': [('g1',True,[],'初版壳子和今晚交付均有证据，未强加坐标准确要求。'),('g2',True,[],'发送Demo并说明初版有支持，排版确认意图未充分表达，另列信息缺失。'),('g3',True,['owner_missing'],'客户确认条件保留，日期正确；漏填原文直接分派的小李。')],
 'tencent-05': [],
 'tencent-06': [('g3',True,[],'固定格式Mock和当天发送有直接支持；老谢与谢昀松由本场发言可对应。'),('g4',True,[],'拿到Mock后测试、周四截止保留在标题中。'),('g1',True,['owner_alias_exact_mismatch'],'实际昵称早该睡觉了与初标角色老段在本场可对应；仅字符串不一致。'),('g5',True,[],'实际发言人对主持人说发你，Dawn是该回合接收者。'),('g2',True,[],'两天内画完规则界面获得支持。')],
 'text-01': [('g1',True,[],'修复提测和当日截止有直接支持。'),('g2',True,[],'小李明确接受明日UI交付。')],
 'text-02': [(None,True,['out_of_work_scope'],'点外卖有原文承诺，但冻结口径只提工作行动，作为FP；不能标为证据幻觉。'),(None,True,['out_of_work_scope'],'取外卖有请求和接受，但属于工作范围外，作为FP。')],
 'text-03': [('g1',True,[],'编号原文仍明确老李与周五截止。'),('g2',True,['due_date_missing'],'日期漏填；周三为评估锚点当天，缺失不属于无证据断言。'),('g3',True,[],'小李提出埋点且主持人同意。')],
}
for cid, decisions in DECISIONS.items():
 p=folder/(cid+'.json');pred=json.loads(p.read_text())
 assert len(pred['actions'])==len(decisions)
 review_path=folder/(cid+'.review.json')
 template=review_path.read_text()
 backup=ROOT/'evaluation/runs/single-agent-v1/review-templates'/review_path.name
 backup.parent.mkdir(exist_ok=True)
 if not backup.exists():backup.write_text(template)
 judgments=[]
 for action,(gid,supported,tags,note) in zip(pred['actions'],decisions):
  judgments.append({'prediction_id':action['id'],'gold_id':gid,'evidence_supported':supported,
                    'error_tags':tags,'reason':note})
 result={'case_id':cid,'run_id':pred['run_id'],'review_origin':'agent','review_complete':True,
         'human_review_status':'pending','authorization':'用户要求按冻结口径逐条Agent裁定后汇总',
         'judgments':judgments}
 if not decisions:result['negative_sample_reason']='提议未形成确定承诺，最终仍为备选/暂缓；预测空与冻结初标一致。'
 review_path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print('9 development cases adjudicated by Agent; test predictions untouched.')
