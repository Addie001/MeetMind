"""Freeze v1 input snapshots and pre-prediction Agent annotations; no model calls."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from meetmind.core import parse_transcript


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if path.exists() and path.read_text() != content:
        raise ValueError(f'拒绝覆盖已生成快照: {path}')
    path.write_text(content)


def main():
    manifest = json.loads((ROOT/'evaluation/data/manifests/samples.json').read_text())
    cases = {}
    for row in manifest['cases']:
        cid = row['case_id']
        if row['source_type'] == 'tencent_recording':
            source = ROOT/row['transcript_path']
            assert digest(source) == row['transcript_sha256']
            original = json.loads(source.read_text())
            paragraphs = original['paragraphs']
            if cid == 'tencent-07':
                boundary = '2117951013215756289:21'
                start = next(i for i,p in enumerate(paragraphs) if p['id']==boundary)
                assert paragraphs[start]['text'].startswith('今天对一下联调排期')
                clean = {**original, 'paragraphs': paragraphs[start:],
                         'cleaning': {'source_path':row['transcript_path'], 'source_sha256':digest(source),
                                      'first_retained_id':boundary,'removed_ids':[p['id'] for p in paragraphs[:start]],
                                      'reason':'用户要求仅保留重新开始后的正式内容；不改写段落或重新编号'}}
                write(ROOT/'evaluation/data/processed/tencent-07.clean.v1.json',clean)
                paragraphs = clean['paragraphs']
            day = original['meeting_date']
        else:
            source = ROOT/row['reference_path']
            assert digest(source) == row['reference_sha256']
            paragraphs = parse_transcript(source.read_text())
            day = '2026-11-04'
        cases[cid] = {'case_id':cid,'split':row['split_intent'],'group_id':row['group_id'],
                      'meeting_date':day,'paragraphs':paragraphs}
    gold = {}
    def add(cid,title,owner,due,indices,criteria='',condition='',note=''):
        rows = cases[cid]['paragraphs']
        item = {'id':f'g{len(gold.setdefault(cid,[]))+1}', 'title':title,'owner':owner,
                'due_date':due,'acceptance_criteria':criteria,
                'evidence_ref':[rows[i]['id'] for i in indices], 'condition':condition,'annotation_note':note}
        gold[cid].append(item)
    add('tencent-01','修改短信网关重试机制并修复验证码问题提测','李素芬','2026-11-02',[1,2])
    add('tencent-01','按设计稿调整注册界面 UI','李素芬','2026-11-03',[3,4,5,6])
    add('tencent-02','用 Mock 数据调通前端 UI 交互','李素芬','2026-11-04',[3,4])
    add('tencent-02','资质通过后换真实接口验证并通知老谢测试','李素芬','',[2,5,6],condition='企业资质通过后，接口验证无问题再通知测试',note='后天是预计审批时间，不是确定行动截止日；验证与通知作为同一交付链')
    add('tencent-03','完成 CSS 截断并发版','小李','2026-11-04',[7,8,9,13])
    add('tencent-03','对接第三方查明支付回调延迟原因并反馈','老段','2026-11-04',[9,10,11,13],note='下午6点属于时间粒度，当前 schema 仅支持日期')
    add('tencent-03','延迟问题确认后在前端添加处理中提示','小李','',[11,12],condition='老段确认存在延迟并给出结论后')
    add('tencent-04','制作公章功能页面 Demo','李素芬','2026-11-04',[5,6,7,9],criteria='初版页面壳子即可，不要求公章坐标准确')
    add('tencent-04','把初版 Demo 发给客户确认排版位置','谢昀松','2026-11-05',[7,8,9],criteria='告知客户只是初版，确认排版位置',condition='拿到 Demo 后')
    add('tencent-04','客户确认后实现真正的盖章功能','小李','2026-11-10',[9],condition='客户确认后',note='只保留原文角色名，不从脚本补充缺失应答；明确分派且未撤回')
    gold['tencent-05']=[]
    add('tencent-06','完成 Redis 缓存','老段','2026-11-09',[4,5,6,16])
    add('tencent-06','两天内完成规则提取界面','李素芬','2026-11-06',[7,8,16])
    add('tencent-06','提供固定格式 Mock 文本给老谢','早该睡觉了','2026-11-04',[10,11,12,16])
    add('tencent-06','使用 Mock 文本完成核心边界测试','谢昀松','2026-11-05',[10,13,16],condition='收到 Mock 文本后')
    add('tencent-06','提交流式输出动画 Demo','李素芬','2026-11-05',[14,15,16])
    # Cleaned tencent-07 indices are relative to retained paragraphs, original IDs unchanged.
    add('tencent-07','提供字段文档初版','早该睡觉了','2026-11-04',[2,3,4,8,9],note='本周三为会议当天；完整版可能周五仍不确定，不另列明确承诺')
    add('tencent-07','拿到字段初版后开展登录页联调','李素芬','2026-11-04',[0,1,4,5],condition='收到字段文档初版后')
    add('tencent-07','完成导出接口','早该睡觉了','2026-11-11',[6,7,8,9],note='最终排期下周三覆盖此前下周一')
    add('tencent-07','按导出接口新排期开展对应联调','李素芬','',[10,11],condition='导出接口就绪后，按下周三的新排期衔接',note='接口日期不是前端联调完成日期')
    add('tencent-08','出差回来后联系客户预约对接时间','谢昀松','',[4,5,6,8],condition='出差回来后',note='研发明确排不开；条件性再考虑方案不属于已接受交付')
    add('text-01','修复验证码 Bug 并提测','小李','2026-11-04',[1,2])
    add('text-01','修改新用户界面 UI','小李','2026-11-05',[4,5])
    gold['text-02']=[]
    add('text-03','整理季度汇报材料','老李','2026-11-06',[1],note='原始编号文本包含姓名，即使 V1 parser 标为未知发言人，gold 仍依据可见输入')
    add('text-03','提供季度汇报数据部分','小李','2026-11-04',[1,2])
    add('text-03','为登录日志增加埋点','小李','',[6,7])
    add('text-04','通知客户演示延期','老谢','',[5,6,7])
    add('text-04','继续编写文档','小李','',[8,9,10])
    split_cases = {}
    artifacts=[]
    for split in ('dev','test'):
        selected = [c for c in cases.values() if c['split']==split]
        destination = ROOT/f'evaluation/data/frozen/v1/{split}.jsonl'
        destination.parent.mkdir(parents=True,exist_ok=True)
        content=''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in selected)
        if destination.exists() and destination.read_text()!=content:raise ValueError('冻结输入已存在')
        destination.write_text(content);artifacts.append(destination)
        split_cases[split]=[c['case_id'] for c in selected]
    for cid,actions in gold.items():
        path=ROOT/f'evaluation/data/annotations/v1/{cid}.json'
        write(path,{'case_id':cid,'annotation_version':'v1','annotation_origin':'agent_pre_prediction',
                    'human_review_status':'pending','actions':actions})
        artifacts.append(path)
    groups={}
    for c in cases.values():groups.setdefault(c['group_id'],set()).add(c['split'])
    assert all(len(s)==1 for s in groups.values())
    for cid,actions in gold.items():
        refs={p['id'] for p in cases[cid]['paragraphs']}
        assert all(a['evidence_ref'] and set(a['evidence_ref'])<=refs for a in actions)
    artifacts.extend([ROOT/'evaluation/data/processed/tencent-07.clean.v1.json',ROOT/'evaluation/data/annotations/POLICY.v1.md'])
    write(ROOT/'evaluation/data/frozen/v1/lock.json',{'version':'v1','annotation_status':'agent_initial_pending_human_review',
          'split_frozen':True,'case_ids':split_cases,'group_count':len(groups),
          'artifacts':{str(p.relative_to(ROOT)):digest(p) for p in artifacts}})
    print(json.dumps({'split_counts':{k:len(v) for k,v in split_cases.items()},'gold_actions':{k:len(v) for k,v in gold.items()}},ensure_ascii=False))


if __name__=='__main__':main()
