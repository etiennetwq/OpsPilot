# OpsPilot 实测评测报告

运行时间：2026-11-17T02:12:26.842689+00:00；Python 3.13.9；向量：lsa。

索引冷启动：245.524 ms；任务：12 正例 + 3 拒答负例。

|方法|日志 Hit@5|P@5|Recall@5|MRR@5|文档 Hit@3|任务成功|P50 ms|P95 ms|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|keyword|0.666667|0.666667|0.132212|0.666667|1|0.733333|6.209|7.166|
|vector|0.75|0.75|0.132751|0.75|1|0.8|4.965|5.294|
|hybrid|0.75|0.716667|0.132692|0.75|1|0.8|8.181|9.55|

## 结果解释与限制

- 12 author-written positive queries across 6 upstream event labels, 3 author-written negative queries. No incident/root-cause labels.
- Same corpus is intentionally searched; queries and relevance mappings were authored after inspecting templates. No independent held-out benchmark; no significance or production accuracy claim.
- Dense default is local char TF-IDF/SVD, not pretrained neural semantic embeddings.
- All offline systems share an extractive answer formatter. Vector+LLM and hybrid+LLM are NOT measured unless live_llm_requested=true and successful API usage is reported.
- Task success means top-1 matching event plus relevant document citations, or correct abstention. It is not incident resolution.
- Citation validity checks ID existence; relevance is event/document annotation, not natural-language entailment.
- Latency excludes startup; no concurrency load test. Costs use provider token usage and operator-supplied prices; failed call cost may be unknown.

离线调用 Token 为 0，API 费用为 $0；这不是付费 LLM 的成本估计。真实 LLM 的质量、延迟、Token 和费用在未调用时均未测量。
小样本及重复模板可产生很高的检索分数，不能据此声称 Hybrid 优于其他方法。保留逐任务失败记录，不选择性隐藏。

## 标注依据

日志相关性使用上游 structured.csv 的 EventId；运行时仅导入原始日志，标签不参与索引。文档相关性由本项目作者按主题建立，见 eval/doc_relevance.json；需要独立人工复核。

Recall@5 分母为该事件全部相关日志行，重复事件很多，因此数值可能很低。统计按 query 宏平均；Hit/MRR/P/R 和文档指标只含正例，任务成功含全部 15 例。P95 为排序样本的 floor(0.95*(n-1)) 分位。

## Token / Cost

|Method|Prompt tokens|Completion tokens|USD|Provider failures|
|---|---:|---:|---:|---:|
|keyword|0|0|0.0|0|
|vector|0|0|0.0|0|
|hybrid|0|0|0.0|0|