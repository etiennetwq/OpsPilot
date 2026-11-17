# 数据卡与知识库来源

数据集：[logpai/loghub Apache](https://github.com/logpai/loghub/tree/dd61d0952749ee7963bde24220d1be5ede023033/Apache)。提交、下载 URL、六个文件 SHA256 均在 `data/manifest.json`。来源 README 表述为运行 Apache 的 Linux 系统错误日志，进一步来源为 Public Security Log Sharing Site；本项目未独立验证最初采集机构或生产真实性。

本项目使用仓库公开 2k 样本，不冒充 Loghub 完整 56,481 行 Apache 数据，更不冒充自己采集的企业生产日志。日志日期为 2005 年，包含 JK2 消息；现代 httpd 2.4 和 mod_jk 文档只能提供背景，不能用于确定历史 JK2 error state 6 的原因。

## 样本与标签

- 原始 2000 行均解析成功；32 行客户端 IP 被替换。原始文件保留用于 checksum 验证；应用索引只保存脱敏消息。
- 级别：error 595，notice 1405。级别本身不是事故或异常标签。
- EventId E1–E6 是上游日志模板标注，用于相关性评分，不是根因、严重程度、故障恢复结果或服务 SLA 标签。
- 完整许可证和引文分别保存在 `data/source/LICENSE` 与 `data/source/CITATION`。LICENSE 要求研究/学术用途及保留声明、注明仓库 URL、适用时引用论文；不是 Apache-2.0 数据许可。
- 数据来源名 Apache 不等于数据采用 Apache License。自有代码 MIT 不能覆盖第三方数据。

## 脱敏核查

`redact` 处理 IPv4、常见 IPv6、邮件、password/token/API key/authorization 等键值。导入报告在 `reports/ingest.json`。工具只读索引，UI 和发给 API 的内容均来自脱敏索引；原始日志仍含公开 IP，不能称整仓库完全匿名。没有扫描或上传用户原有 D 盘日志、代码或秘密。

只对公开样本做了有限模式核查，不能保证上游已完成所有隐私处理。未来导入个人真实数据需要单独隐私审查、路径/主机名/账户等脱敏，以及服务商数据政策评估；当前没有开放任意文件导入接口。

## 知识库

`kb/documents.json` 是六条项目作者撰写的阅读笔记/调查约束，链接 Apache error log、autoindex、scoreboard、Tomcat worker 配置与 Loghub 说明。不是大规模技术文档抓取库。`kb/provenance.json` 保存访问状态、抓取时间和页面哈希（页面原文不再分发）。每条笔记有稳定 D:ID、标题、来源 URL、section 和适用版本说明。

这些笔记整合了日志模板的观察措辞，因此评测容易出现主题重叠。文档 relevance 属作者主题判断，应由独立评审复核，不能称上游文档标注。

## 引用

Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*. ISSRE, 2023. https://arxiv.org/abs/2008.06448
