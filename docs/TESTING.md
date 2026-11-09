# AgentHub 测试与质量检查

最后更新：2026-11-09。

## 环境与命令

在项目根目录使用 Python 3.13 和 uv：

```powershell
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked python -m compileall -q src tests
uv build
```

宿主机默认测试为 **33 passed、1 skipped**，真实依赖成功测试在容器内显式启用后 **1 passed**；未将宿主机的跳过计作通过。Ruff、mypy、语法检查与打包记录见 [开发记录](DEVELOPMENT_LOG.md)。mypy strict 检查 `src` 与 `scripts`；测试通过 pytest 与 Ruff 验证，不声称测试目录已完成严格类型检查。`uv.lock` 锁定具体包与哈希，依赖更新时重新锁定、运行检查并检查差异。

## 测试分层

| 文件 | 验证内容 | 是否需要外部服务 |
| --- | --- | --- |
| tests/test_config.py | 环境变量优先级、配置校验、敏感字段表示 | 否 |
| tests/test_health.py | 健康响应、依赖各种故障组合、OpenAPI 契约 | 否；明确使用替身 |
| tests/test_errors.py | 404/405/422/500、协议头、请求 ID、日志脱敏、并发上下文隔离 | 否；测试路由只注册在测试应用 |
| tests/test_dependencies.py | PostgreSQL 查询/扩展判断、探针超时、Redis 故障与资源关闭、启动检查 | 否；驱动/探针使用替身 |
| tests/test_http_smoke.py | 启动真实 Uvicorn 进程，经 TCP 验证 live=200、依赖失败 ready=503、docs/OpenAPI 可达 | 否；依赖指向保留但不监听的本地端口，无服务替身 |
| tests/test_integration.py | PostgreSQL + vector 与 Redis 真实成功链路 | 是；默认跳过 |

测试替身验证控制流，不证明真实 PostgreSQL、Redis、vector 或容器已经可用。HTTP 冒烟测试使用临时目录避免读取用户 `.env`，结束时终止自己创建的服务进程；不接触真实业务数据库。

## 启用真实依赖测试

对已有可达的独立开发服务，在 `.env` 或环境变量中设置连接信息，PostgreSQL 需安装 vector。然后：

```powershell
$env:AGENTHUB_RUN_INTEGRATION = '1'
uv run --locked pytest -m integration
Remove-Item Env:AGENTHUB_RUN_INTEGRATION
```

flag 未启用时明确 skip；启用后服务不可用必须失败。此测试只读探针，不建表、不删数据。默认 Compose 未暴露数据库端口，因此可在 Docker 环境通过容器网络执行：

```powershell
docker compose run --rm --user root -v "${PWD}/tests:/app/tests:ro" -e AGENTHUB_RUN_INTEGRATION=1 api /bin/uv run --locked pytest -m integration
```

这会启动一次性测试容器、挂载测试并安装锁定的开发依赖；生产 API 镜像默认没有 pytest，不要直接调用不存在的测试工具。该容器测试方式已在本机运行成功，验收脚本将目标收窄为 tests/test_integration.py，结果 1 passed。后续 CI 可复用同一组检查；当前尚未创建远程 CI 工作流。

## 容器验收边界

真实构建启动、依赖停止/恢复、vector 运算、命名卷和 Redis AOF 数据保留已验收通过，证据见 [验收记录](acceptance/P1_2026-11-09.md)。Compose schema 检查仍只验证结构，不能替代这些运行验证。当前没有真实模型、MCP、业务数据库、性能或端到端产品测试，按后续阶段补齐。

## 一键复验 P1 本地开发栈

先启动服务，再执行：

```powershell
uv run --locked python scripts/verify_p1.py
```

脚本会短暂停止 PostgreSQL/Redis，再执行不带删卷参数的 down/up。它在随机命名的测试 schema/Redis 键中写入标记，验证同一数据在容器重建后仍可读取；结束时清理自身标记并恢复服务，JSON 报告写入忽略的 data/acceptance/。请仅对本项目本地开发栈运行。正常停止与重建验证不等于断电恢复或备份验收。

脚本无需新增依赖，使用标准库和 Docker CLI。它也检查 API 非 root、依赖没有主机端口、日志请求 ID 与数据库密码不泄露；相关教学见 [第二课](modules/FOUNDATION.md)。
