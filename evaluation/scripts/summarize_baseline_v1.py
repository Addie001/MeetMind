"""Summarize execution metadata only; never inspect held-out predictions."""
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[2]
folder=ROOT/'evaluation/runs/single-agent-v1'
rows=json.loads((folder/'execution-summary.json').read_text())
lines=['# 单 Agent v1 执行基线','', '模型：deepseek-flash。原始提取 prompt，关闭重试；未引入第二个 Agent。', '',
       '质量标注和预测匹配尚待人工复核，本报告不提供准确率/召回率等质量结论。保留测试集只统计运行元数据，不读取预测内容。', '',
       '| 范围 | 成功/总数 | 模型调用 | 提取总耗时（秒） | 单场中位数（秒） | 输入 token | 输出 token |',
       '| --- | --- | --- | --- | --- | --- | --- |']
for name,subset in [('dev',[r for r in rows if r['split']=='dev']),('test',[r for r in rows if r['split']=='test']),('all',rows)]:
    latency=[r['latency_seconds'] for r in subset]
    tokens=lambda key:sum(r[key] for r in subset) if all(r[key] is not None for r in subset) else '未知'
    lines.append(f"| {name} | {sum(r['status']=='ok' for r in subset)}/{len(subset)} | {sum(r['call_count'] for r in subset)} | {sum(latency):.2f} | {statistics.median(latency):.2f} | {tokens('prompt_tokens')} | {tokens('completion_tokens')} |")
lines+=['','耗时是本地逐场提取耗时，不含导入、人工审核、同步和追踪上传等待。12份小样本不适合给稳定 p95/并发吞吐结论。token 采用服务实际返回值，无价格假设。', '',
        '所有样本的预测和待审核模板位于 dev/、test/；错误运行也保留，不自动重跑。LangSmith 本地 ID 只能证明创建了追踪，远端接收情况另见 langsmith-verification.json。', '']
(folder/'REPORT.md').write_text('\n'.join(lines)+'\n')
print('\n'.join(lines))
