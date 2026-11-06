"""Compare paired dev experiments without accessing held-out predictions."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
RUNS=ROOT/'evaluation/runs'
variants=[('单Agent V1','single-agent-v1','dev','dev-quality.agent-v1.json'),
          ('加核验（V1派生）','dual-agent-v1','verify-only','quality-verify.agent.json'),
          ('核验+一次修正 V1','dual-agent-v1','dev','quality-repair.agent.json'),
          ('核验+一次修正 V2','dual-agent-v2','dev','quality-repair.agent.json')]
lines=['# 固定开发集对照','', '9份开发数据、20条冻结初标；由Agent独立裁定，尚非独立人工评测。保留测试不参与调参。', '',
       '| 配置 | precision | recall | 证据支持 | 负责人严格匹配 | 日期严格匹配 | 总调用数 | 输入/输出token | 累计提取秒数 |',
       '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
summary=[]
for label,experiment,arm,quality_file in variants:
    folder=RUNS/experiment
    quality=json.loads((folder/quality_file).read_text())['micro']
    rows=[json.loads(p.read_text()) for p in sorted((folder/arm).glob('*.json')) if '.review.' not in p.name]
    assert len(rows)==9
    def total(key):return sum(r[key] for r in rows) if all(r.get(key) is not None for r in rows) else None
    metrics=quality['metrics'];fmt=lambda k:f'{metrics[k]*100:.2f}%'
    lines.append(f"| {label} | {fmt('precision')} | {fmt('recall')} | {fmt('evidence_support_rate')} | {fmt('owner_accuracy_on_matched')} | {fmt('due_date_accuracy_on_matched')} | {total('call_count')} | {total('prompt_tokens')}/{total('completion_tokens')} | {total('latency_seconds'):.2f} |")
    summary.append({'variant':label,'counts':quality['counts'],'metrics':metrics,
                    'paired_calls':total('call_count'),'paired_latency_seconds':total('latency_seconds'),
                    'prompt_tokens':total('prompt_tokens'),'completion_tokens':total('completion_tokens')})
lines+=['', '## 解释结果','',
        '- 双Agent复用同一份已落盘V1提取结果，不向模型发送gold。核验组派生自首次核验，修正组最多一次修正后再核验。各组成本均加回原提取调用；不是把缓存后的新调用数冒充完整流程成本。',
        '- 只核验时，明确不成立的外卖行动被排除；日期错误虽被标记仍保留给人工，所以证据支持率按19/20计算。加修正后才真正清空预计审批日期、补上漏掉的明确日期。',
        '- V2跳过空候选核验，且仅有reject时直接排除，不调用提取Agent重写；修正仍需要revise触发。保留被排除项及证据，便于查误拦截。',
        '- 负责人严格指标包含实际昵称/角色名差异。当前仍存在负责人漏填；不得用证据支持100%宣称字段完整，也不能把严格字符串不一致全部叫幻觉。',
        '- V2的显式应答证据校验仅在修正结果上生效，V1校验未修改；若修正模型没有引用应答段落，仍不会保留负责人。第二次核验把残留问题标为需审查，不无限循环。',
        '- 不同轮模型调用存在随机性，少量样本不能单独证明某一细微改动的因果收益。派生核验组时延是首次请求时间加原提取时间，不含图开销，其余为实际新增流程时间加原提取时间。',
        '- 验收标准严格字符串匹配不适合作为语义正确率，保留原指标口径但不以其优化prompt。',
        '', '## 产品接入','',
        '设置 MEETMIND_ANALYSIS_MODE=dual 后，新会议使用双Agent；single 保留原流程，默认不改已有报告。页面展示核验状态、重点审查项和建议排除项。所有写入仍需用户确认。',
        '双Agent子图使用本地SQLite节点检查点；输入、模型、地址或实现版本改变会使用新断点。外部模型请求在崩溃边界仍可能重发，不承诺调用exactly-once；子图从不写滴答。',
        '', '## 后续提高效果','',
        '下一步优先统一负责人称呼及引用完整性，然后用独立的单Agent prompt改进组检验哪些收益仅靠prompt就能获得。当前双Agent的工作范围规则是额外规则输入，因此不能声称两条外卖修复只能靠多Agent实现。',
        '正式结论必须结合保留集的一次性最终检验；不得根据保留集错误继续修改本轮实现。']
folder=RUNS/'dual-agent-v2'
(folder/'COMPARISON.md').write_text('\n'.join(lines)+'\n')
(folder/'comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print('\n'.join(lines[:11]))
