# 单 Agent 基线评估

## 仓库包含范围

本仓库发布评估代码及汇总报告，不上传含实际会议转写、人员和录制标识的 `evaluation/data/`，也不上传逐次预测、审核明细、追踪记录或运行代码快照。被忽略的文件仍保留在原本的本地目录。

历史状态和报告中的数据、JSON 产物及审核入口路径指向本地私有文件；克隆仓库后这些文件不存在。可以自行准备下面格式的数据运行基准；若要复核原实验，需要先恢复完整的私有 `evaluation/data/` 和 `evaluation/runs/`。`tests/test_frozen_dataset.py` 在冻结锁文件不存在时跳过，其他测试使用独立样例，不依赖原始会议库。

已发布的汇总报告：[单 Agent 执行基线](runs/single-agent-v1/REPORT.md)、[开发集发现](runs/single-agent-v1/DEV-FINDINGS.md)、[双 Agent 对照](runs/dual-agent-v2/COMPARISON.md)、[保留集检验](runs/dual-agent-v2/HELDOUT.md)。以下内容保留原基线的使用方法；后续进展见 `STATUS.md` 和上述报告。

最新进度、冻结数据和标注入口见 [STATUS.md](STATUS.md)。

当前实现只运行提取，不读取生产会议库、不创建滴答任务、不包含核验 Agent。
真实数据尚未到位，当前没有质量或性能改善结论。

## 输入与标注

dataset.jsonl 每行一个会议，示例（仅说明格式，不代表真实数据）：

```json
{"case_id":"meeting-001","split":"dev","meeting_date":"2026-11-04","paragraphs":[{"id":"p1","speaker":"小王","timestamp":"00:01","text":"我负责提交接口文档。"}]}
```

gold.json 单独保存人工标注，未知字段显式使用空字符串：

```json
{"case_id":"meeting-001","actions":[{"id":"g1","title":"提交接口文档","owner":"小王","due_date":"","acceptance_criteria":"","evidence_ref":["p1"]}]}
```

先冻结会议 ID、dev/test 划分和人工 gold，再跑模型。保留测试集不用于调参；命令对传入文件全部运行，请将两个 split 分文件保存。gold 不会传入模型。

## 运行

配置环境变量 LLM_BASE_URL、LLM_MODEL、LLM_API_KEY 后执行（命令不会自动加载 .env）：

```sh
.venv/bin/python -m meetmind.benchmark evaluation/dev.jsonl --output evaluation/runs/v1
```

未配置模型时运行离线规则，产物 mode=rules；不能混入 LLM 基线。每个会议输出独立 run_id.json 和 run_id.review.json，不覆盖历史运行。失败也保留记录；无 token usage 时记录 null，不能当作零成本。模型 API 可能在失败前已消耗 token，因此未知用量保持未知。

结果包含输入 SHA256、提取代码 SHA256、模型与服务地址、超时/重试配置、总延迟和逐次模型调用耗时。当前是提取基准，不包含腾讯导入、人工操作和滴答同步耗时；逐次调用记录是本地测量，不是 LangSmith 远端联调证明。比较时固定数据、模型和服务配置，并保留依赖锁文件；代码指纹变动需要作为新实验版本。

## 人工裁定与评分

审核预测的每一项，填写 review：

- gold_id：对应 gold 行动 ID；额外或重复提取项填 null。同一 gold 只能匹配一次。匹配按行动语义判定，不能因负责人或日期错误而拒绝匹配，否则字段错误会被重复惩罚。
- evidence_supported：人工判断引用上下文是否支持完整结论，填写 true/false；null 表示尚未审核，评分会拒绝。
- 发现 gold 漏标先修订标注版本，再让所有实验组在同一版本重新评分；不能仅修改有利于某一实验组的分母。

```sh
.venv/bin/python -m meetmind.evaluation evaluation/gold.json evaluation/runs/v1/RUN_ID.json evaluation/runs/v1/RUN_ID.review.json
```

指标定义：precision=匹配数/预测数；recall=匹配数/gold 数；F1=2×匹配数/(预测数+gold 数)。负责人、日期、验收标准准确率只在匹配项上计算，采用字符串精确比较，包含正确留空。证据支持率=人工判定支持的预测数/预测数；unsupported_rate 为其补集，包含证据不足和矛盾，不能直接等同于狭义“编造率”。分母为零时输出 null。匹配准确不代表字段正确，证据引用存在不代表语义支持。

失败运行拒绝质量评分，需单独报告失败数/总运行数，不能静默排除失败然后宣传整体质量。当前输出单场指标，汇总时用 counts 求和后计算 micro 指标，不直接平均不同会议的百分比。暂未实现批量汇总、对照报告或回归门禁，待真实数据指标口径确认后补齐。

## 容错配置

LLM_MAX_RETRIES 默认 0，可设 0–3；LLM_TIMEOUT_SECONDS 默认 90，可设 (0,300]。仅模型请求的网络超时及 HTTP 408/429/500/502/503/504 可以重试，退避为 1、2、4 秒。认证错误、结构解析/证据校验错误不重试。本轮不修改 prompt、不增加修复调用或隐式规则降级，保持 V1 提取逻辑可作为基线。

总调用上限为 1+重试次数，每次有超时；这不是严格的端到端 deadline。开启重试应另建实验目录，并计入调用次数和延迟。滴答写入没有新增自动重试。

分块恢复、并发、缓存及锁改造尚未实施；先拿真实基线定位瓶颈，再作为独立配置验证质量、延迟和成本。

## 批量开发集评分

每份 review 需填完整 judgments，并设置 `review_origin` 为 `human` 或 `agent`、`review_complete=true`。人工审核还需 `human_review_status=completed`。Agent裁定仍保留人工复核待完成状态；即使没有预测，负样本也需要明确完成审核。

```sh
.venv/bin/python -m meetmind.evaluation_batch --run-dir evaluation/runs/single-agent-v1/dev --gold-dir evaluation/data/annotations/v1 --dataset evaluation/data/frozen/v1/dev.jsonl --output evaluation/runs/single-agent-v1/dev-quality.NEW.json
```

按 counts 汇总 micro 指标，不平均单场百分比；输出拒绝覆盖历史评分。本命令限定开发集，审核不完整时返回退出码2，不生成部分样本的乐观质量报告。
