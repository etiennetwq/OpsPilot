# 运行与排错

## 日常操作

在仓库根目录运行 README 中的命令。建议使用 VS Code 的“打开文件夹”选本仓库，Python 解释器指向本仓库 `.venv`。默认不需要 VS Code 扩展。网页服务器重启后索引重新在内存构建；SQLite 若不存在则先验源数据再导入。

用 `python -m opspilot.cli ingest` 强制重建 SQLite（仅本仓库 data/index.sqlite），一次事务内替换旧表内容。工具层之后只读。数据损坏时应先定位 manifest mismatch，不能跳过 checksum 校验。

```powershell
# 本机诊断与重排
.\.venv\Scripts\python -m opspilot.cli diagnose "Directory index forbidden by rule" --mode hybrid --rerank
# 验证并重新下载固定来源，需要网络
.\.venv\Scripts\python scripts/fetch_data.py
# 默认离线评测
.\.venv\Scripts\python -m opspilot.cli evaluate
```

退出服务：在启动它的终端 Ctrl+C。端口已占用时可直接访问现有 OpsPilot，或 `python -m uvicorn opspilot.api:app --host 127.0.0.1 --port 8766`。

## API 配置

环境变量在服务启动时生效；`.env.example` 是示例，程序不自动加载 `.env`。不要把密钥写到代码、README、截图或 Git。CLI `--llm` 是显式 API 请求；网页还要求服务端 `OPSPILOT_LLM=api`。

真实 API 需支持 JSON 输出参数。若服务商不兼容该参数、超时或认证错误，返回 provider_error 并降级到离线证据。费用缺少价格或 provider usage 时为 null；usage 由 provider 返回而非本地字符数估算。超时不自动重试，避免重复计费。

需要检查网络时只检查目标服务域名可达性，不输出密钥。错误响应体不向用户回传。真实调用可能仍被 provider 计费，即使应用未收到完整 usage。

## Docker

`docker info` 验证引擎；`docker compose config` 验证配置；`docker compose up --build -d` 构建启动；`docker compose logs` 查看启动日志；`docker compose down` 停止本项目容器。没有挂载用户目录。镜像构建包含固定公开 source 文件和 SHA256 验证。

Compose 默认离线。如果需要 API，请显式将所需环境变量加入服务环境引用，禁止 COPY .env 到镜像。不要在无认证状态下改成公网端口映射。

## 结果解释

1. 先看 generator 和 provider_error，确认是否真的调用模型。
2. 逐条检查 L 行证据文本、D 文档 URL 与版本。
3. 相邻行只用于调查，不证明不同错误有共同根因。
4. 检索失败要记录；不能用改写问题后成功替代原问题的评测记录。
5. README 与报告中的任务成功只是预设的证据检索规则，不是生产排障成功率。
