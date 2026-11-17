# 真实 DeepSeek 工具调用对照评测

UTC: 2026-11-17T03:42:58.989032+00:00；模型：deepseek-flash；每种方法6题、每题1次。

|方法|任务成功|Hit@5|MRR@5|P50 ms|P95 ms|输入 Token|输出 Token|工具拒绝|
|---|---:|---:|---:|---:|---:|---:|---:|---:|
|keyword|5|1|1.0|19.822|20.217|0|0|0|
|vector|4|1|1.0|2976.601|3301.643|4935|708|0|
|hybrid|5|1|1.0|3088.2405|3232.065|4912|676|0|
|agent|6|1|1.0|7321.188|8794.513|55002|3856|2|

## 任务定义与限制

- Six author-written behavior tasks, one run per system/task, no independent holdout or statistical significance.
- All model systems use the same configured model and question suffix; context sizes and number of API requests differ. Keyword is offline; vector/hybrid use fixed retrieval then actual LLM; agent uses model-selected tools.
- No root-cause labels. Success means labeled event/document citations plus requested context/statistic evidence, or correct abstention. It does not measure incident resolution or natural-language entailment.
- Agent retrieved evidence order is first-seen accumulated tool output, not a single globally ranked list; its Hit/P/R/MRR metrics are descriptive and not a fair ranking superiority claim.
- Provider-returned prompt/completion tokens are measured; no configured billing price, so dollar cost is unknown. Cache and time-dependent prices prevent equating tokens with a flat exact charge.
- Only loopback HTTP, no load testing. Latency includes provider round trips. Earlier exploratory failures are retained separately in live_agent_smoke.json.

all_counts 真值为样本2000行；directory_count_context 真值由上游 E4 计数得到32行。日志相关性用上游 EventId，文档相关性用人工主题映射。失败不隐藏，逐题工具轨迹及结果见 JSON。
费用为未知，不能写成零费用；本报告中的原始 Token 才是实际观测值。
同为6题的比较是有意覆盖工具行为的小样本演示，不能据此推断通用Agent优于固定工作流。

## 价格口径

实际账单金额未知。按2026-11-17官方公布的 deepseek-flash 峰时、输入未命中缓存价格作保守估算（输入$0.3/百万Token、输出$1.2/百万Token）：

|方法|保守估算 USD|
|---|---:|
|keyword|0.0|
|vector|0.0023301|
|hybrid|0.0022848|
|agent|0.0211278|

此表不是实际扣费，也不包含开发中的探索调用；实际值可能因缓存及非峰时更低。价格来源：https://api-docs.deepseek.com/quick_start/pricing/ 。原Token统计及全部失败记录一并保留。

## 版本记录

本轮评测实现已提交并标记为 agent-measured-20261117（661a168）。检查轨迹发现工具结果的HH:MM:SS曾被宽泛IPv6规则误脱敏；本轮未测量时间推理。之后已修复为真实IPv6地址校验，并递归脱敏字符串而不是整段JSON，新增回归断言。修复后完整测试通过；本轮付费评测未因该修改重复运行，结果应按上述测量版本引用。
