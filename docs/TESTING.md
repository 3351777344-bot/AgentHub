# AgentHub 测试与质量检查

最后更新：2026-11-10。

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

P2 后宿主机默认测试为 **48 passed、24 skipped**；独立真实 PostgreSQL 身份测试显式启用后 **23 passed**，P1 真实依赖测试在容器内另行执行。跳过不计作通过。Ruff、mypy、语法与打包记录见 [P2 验收](acceptance/P2_2026-11-10.md)。mypy strict 检查 `src` 与 `scripts`；测试/迁移通过 pytest、Ruff 和语法检查，不声称已经严格类型检查这些目录。`uv.lock` 锁定具体包与哈希，更新后运行检查并检查差异。

## 测试分层

| 文件 | 验证内容 | 是否需要外部服务 |
| --- | --- | --- |
| tests/test_config.py | 环境变量优先级、配置校验、敏感字段表示 | 否 |
| tests/test_health.py | 健康响应、依赖各种故障组合、OpenAPI 契约 | 否；明确使用替身 |
| tests/test_errors.py | 404/405/422/500、协议头、请求 ID、日志脱敏、并发上下文隔离 | 否；测试路由只注册在测试应用 |
| tests/test_dependencies.py | PostgreSQL 查询/扩展判断、探针超时、Redis 故障与资源关闭、启动检查 | 否；驱动/探针使用替身 |
| tests/test_http_smoke.py | 启动真实 Uvicorn 进程，经 TCP 验证 live=200、依赖失败 ready=503、docs/OpenAPI 可达 | 否；依赖指向保留但不监听的本地端口，无服务替身 |
| tests/test_integration.py | PostgreSQL + vector 与 Redis 真实成功链路 | 是；默认跳过 |
| tests/test_identity_security.py | Argon2id、随机令牌/摘要、schema、无凭据拒绝、OpenAPI、脱敏503 | 否；503 分支使用明确标识的异常注入 |
| tests/test_identity_integration.py | Alembic升降级/元数据一致、真实身份SQL、撤销/权限、原子审计、并发管理、CLI、真实TCP进程重启与日志脱敏 | 是；独立真实PostgreSQL，默认跳过；仅替换无关健康探针 |

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

真实构建启动、依赖停止/恢复、vector 运算、命名卷和 Redis AOF 数据保留已验收通过，证据见 [P1](acceptance/P1_2026-11-09.md)。P2 已增加真实业务数据库测试。Compose schema 检查只验证结构，不能替代运行验证。当前没有真实模型、MCP、性能或完整产品端到端测试，按后续阶段补齐。

## 一键复验 P1 本地开发栈

先启动服务，再执行：

```powershell
uv run --locked python scripts/verify_p1.py
```

脚本会短暂停止 PostgreSQL/Redis，再执行不带删卷参数的 down/up。它在随机命名的测试 schema/Redis 键中写入标记，验证同一数据在容器重建后仍可读取；结束时清理自身标记并恢复服务，JSON 报告写入忽略的 data/acceptance/。请仅对本项目本地开发栈运行。正常停止与重建验证不等于断电恢复或备份验收。

脚本无需新增依赖，使用标准库和 Docker CLI。它也检查 API 非 root、依赖没有主机端口、日志请求 ID 与数据库密码不泄露；相关教学见 [第二课](modules/FOUNDATION.md)。

## P2 独立真实数据库验收

只使用专用项目 `agenthub-p2-test` 与数据库 `agenthub_p2_test`。以下脚本生成临时密码、启动真实PostgreSQL、执行测试，并在finally清理测试容器/网络；没有挂载业务卷。测试内明确验证当前库名后才允许迁移降级和清空身份测试数据。

```powershell
$env:AGENTHUB_TEST_DATABASE_PASSWORD = [guid]::NewGuid().ToString('N')
$env:AGENTHUB_RUN_P2_INTEGRATION = '1'
try {
    docker compose -f compose.test.yaml up -d --wait
    if ($LASTEXITCODE -ne 0) { throw 'P2 test database startup failed' }
    uv run --locked pytest tests/test_identity_integration.py -q
    if ($LASTEXITCODE -ne 0) { throw 'P2 integration tests failed' }
} finally {
    docker compose -f compose.test.yaml down
    Remove-Item Env:AGENTHUB_TEST_DATABASE_PASSWORD,Env:AGENTHUB_RUN_P2_INTEGRATION -ErrorAction SilentlyContinue
}
```

23项通过，含空库升级→降级→升级、重复升级与`alembic check`、真实数据约束、密码/令牌存储、管理员CLI及并发初始化、错误/过期/撤销、改密/禁用/角色变更、所有权/管理越权、分页、审计失败回滚、并发互相禁用、日志凭据脱敏。TCP用例用真实Uvicorn进程登录，终止后启动新进程，原令牌仍可读取当前用户。

身份TestClient用例只替换无关的P1健康探针，不替换SQLAlchemy、数据库、哈希或身份服务；TCP用例不替换探针，独立库无需vector/Redis即可测试身份。P1真实vector/Redis检查在业务容器网络另行执行，不能混为一项测试。

不支持pytest-xdist并行执行本文件：用例共用专用测试库并逐例清理。降级/清理只能在上述可丢弃库进行；不对用户数据执行。
