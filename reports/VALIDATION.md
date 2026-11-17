# v0.2 工具调用升级验证 2026-11-17

- 真实模型：DeepSeek deepseek-flash，用户在本机页面配置；未读取其他项目密钥，未写入磁盘/报告/Git。
- 模型实际通过 tool_calls 选择日志/文档检索、相邻行、过滤计数；服务端只读执行并校验最终引用。
- Windows完整测试50 passed，4.11s；Linux容器完整测试50 passed（最终输出见本次工具执行记录）。保留一个第三方弃用警告。
- 固定六条行为任务的真实对照：keyword 5/6、vector+LLM 4/6、hybrid+LLM 5/6、动态Agent 6/6。Agent P50 7321.188ms，输入55002、输出3856 Token，两个被拒绝调用恢复；完整结果见 agent_comparison_results.json。
- 本轮费用未知；按官方峰时无缓存价格作保守估算，Agent部分约$0.0211278，不是实际账单，不包含开发探索。
- 开发探索：首条中文复杂任务round_limit；之后两条简短实际任务完成；修复长度约束与错误反馈后，同一中文问题真实复测completed，4轮API请求、12375总Token、9156.41ms。全部探索记录见 agent_exploration_results.json。
- 测量版本标记 agent-measured-20261117。检查轨迹发现时间被误脱敏，随后已修复IPv6校验和递归脱敏并加入回归断言；付费对照未为该修复重复运行，本轮不评价时间推理。
- Docker镜像已构建，非root/只读Compose继续保留。8765为最终源码容器；8766为已配置密钥、保留本次实际结果的演示服务，包含测量版本的脱敏局限，正常重启源码服务后使用最终修复且需重新配置密钥。
- 已更新投递PDF，保留原版式并继续删除两个项目日期。项目2不修改、不合并。

以下是v0.1的历史交付记录，其“无真实调用”“23项测试”仅描述当时状态。

---

# 本机交付验证 — 2026-11-17

## 已实际完成

- 独立 Git 仓库位于本目录；D 盘旧 LLM Security Audit Gateway 未修改、未合并。
- 固定 Loghub 提交，六个源文件 SHA256 验证通过。2000/2000 行导入，拒绝行0，脱敏触发32行。
- Windows / Python 3.13.9 完整测试：23 passed，2.20s；JUnit 原始结果见 tests.xml。
- Linux Docker / Python 3.13 完整测试：23 passed，3.05s；一次已知 Starlette/AnyIO deprecated alias 警告，不影响本轮通过。
- Docker Engine 29.8.0，Compose 配置校验通过。首次 Docker Hub 鉴权连接超时；通过 Google 公共缓存及固定摘要基础镜像成功构建，无修改全局镜像/代理设置。
- 镜像构建摘要：sha256:89ec36767c9987d1774f3e60d0cc140d9862bb4f93db1daef937e6eaf725e7e5。
- Compose 容器已启动，127.0.0.1:8765 本机映射；GET /health、/stats、日志筛选及 POST /diagnose 实际 HTTP 通过。原始响应见 http_smoke.json。
- Chrome 中点击诊断并看到真实日志行号、<IP>、官方文档来源、只读工具轨迹；截图 demo.jpg。
- VS Code 已打开该独立仓库。原项目窗口与文件保留。
- 本机离线基线评测全部运行，45 个系统/任务组合，各3次热运行，逐题输出见 offline_results.json。
- 自有代码编译检查、待提交文本常见密钥模式检查通过。上游文件保留原始 CRLF/空白字节并在 Git 中关闭文本归一化与文本 diff；自有文件执行 whitespace 检查。未发现常见密钥模式，不代表通用秘密检测保证。

## 明确未完成的外部验证

- 无真实 LLM API 密钥，未进行付费真实模型调用；API 路径通过 MockTransport 测试，真实服务商兼容性仍需配置后验证。
- 默认 LSA 已测；可选 SentenceTransformer 与本地词项重排中的神经模型替代未测。当前重排本身为本地词项覆盖率实现。
- GitHub CI 配置已提供，未推送仓库、未实际在 GitHub Actions 运行。
- 没有生产部署、企业事故标注、根因准确率、负载/SLA 测试或故障修复成绩。

## 复现基线

12 条人工正例覆盖六类上游 EventId，3 条人工拒答负例。keyword 日志 Hit@5=0.666667，vector=0.75，hybrid=0.75；任务成功分别11/15、12/15、12/15。Hybrid P50=8.181ms、P95=9.55ms（本轮本机热运行、无真实 LLM）。引用 ID 有效率1.0，但 Hybrid 引用主题相关率0.614583，表明合法引用不一定相关，报告保留这一不足。

这不是独立盲测，不证明 Hybrid 相对向量有提升。全部 API Token=0、API费用=$0，只表示本轮离线，不能用于估算真实模型成本。完整分母、指标定义与限制见 offline_report.md。

最终 Compose 容器健康检查状态：healthy，FailingStreak=0。
