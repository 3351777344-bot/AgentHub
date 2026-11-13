# AgentHub 测试与质量检查

最后更新：2026-11-13。

## P7 Agent 工作流回归

按本文的独立 PostgreSQL 流程启动 `compose.test.yaml`，设置 `AGENTHUB_RUN_P2_INTEGRATION=1`，然后运行：

```powershell
uv run --locked pytest -q tests/test_agents_integration.py
uv run --locked pytest -q
```

该用例组合使用真实 PostgreSQL、LangGraph saver 与 HTTPS MCP，并用确定性的 HTTP 模型响应覆盖分支、预算与中断。设置 `AGENTHUB_RUN_P7_CLOUD_ACCEPTANCE=1` 后额外运行已配置的 DeepSeek Flash 和百炼 Flash 公开样本，产生模型用量。共享独立数据库的 pytest 进程依次执行。 [P7 验收](acceptance/P7_2026-11-13.md)

## P6 工具与 MCP 回归

完整回归为 **209 passed、3 skipped**，包含真实 PostgreSQL/pgvector 和 Redis 就绪测试。三个跳过项为既有付费云测试。

沿用独立 PostgreSQL 测试配置，设置 `AGENTHUB_RUN_P2_INTEGRATION=1`，共享测试库的 pytest 进程依次运行。P6 测试会执行 Alembic 升级、降级与 `alembic check`；禁止将该测试指向业务数据库。

```powershell
uv run --locked pytest tests/test_tool_contracts.py tests/test_tools_integration.py tests/test_rag_integration.py -q
```

`test_tools_integration.py` 启动独立 HTTPS MCP SDK 进程，使用临时证书、回环地址和测试专用 Bearer。无需公网服务。`test_rag_integration.py` 使用实际 pgvector 和文件，Embedding 由确定性 HTTP 响应提供。本地 MCP 容器可按模块步骤调用，停启 `mcp-local` 验证 API 就绪和请求 ID 审计。完整运行证据见 [P6 验收](acceptance/P6_2026-11-12.md)。

## P5 回归与真实问答

完整独立 PostgreSQL 回归为 **189 passed、4 skipped**。`test_rag.py` 覆盖预算、输入与引用，`test_rag_integration.py` 使用真实 PostgreSQL / pgvector、文件和 API，并用受控 HTTP 替身覆盖错误、隔离、竞态和 SSE。现有迁移测试包含 `rag_runs`，共十二张业务表。

独立数据库端口默认绑定回环 55432。如果 Windows 保留该端口，在启动 Compose 和运行 pytest 的同一个终端设置 `AGENTHUB_TEST_DATABASE_PORT`。本次使用 15432。沿用 `AGENTHUB_TEST_DATABASE_PASSWORD` 和 `AGENTHUB_RUN_P2_INTEGRATION=1`，共享库的 pytest 进程依次运行。

真实 P5 用例只在显式启用后运行。已配置百炼北京 Flash 和 DeepSeek Flash 时，可在同一测试终端复验。固定数据最多触发三次入库 Embedding、七次在线 Embedding、四次生成。

```powershell
$env:AGENTHUB_RUN_P5_RAG_ACCEPTANCE = '1'
uv run --locked pytest tests/test_rag_real.py -q -s
Remove-Item Env:AGENTHUB_RUN_P5_RAG_ACCEPTANCE
```

真实验收为 1 passed。标注样本、实际耗时和用量见 [P5 验收](acceptance/P5_2026-11-12.md)。

## P4 历史回归与真实入库

最终完整回归：163 passed / 3 skipped。启用独立真实PostgreSQL，关闭P3/P4云调用标志；跳过P1独立外部依赖、P3真实DeepSeek和P4真实Flash。Ruff、format（87文件）、strict mypy（41源文件）、compileall与uv build通过。P4真实Flash单独1 passed，3份固定公开文件共81 token，不能以HTTP替身冒充该证据。

新增 tests/test_knowledge_parsing.py（22项，真实本地解析/分块/文件）、test_knowledge_integration.py（20项，真实PostgreSQL/vector/API/文件，云HTTP替身与明确故障注入）、test_knowledge_real_embedding.py（显式云验收，3个真正独立worker子进程）。详细场景与边界见 [P4验收](acceptance/P4_2026-11-10.md)。

继续沿用compose.test.yaml和AGENTHUB_RUN_P2_INTEGRATION，名称保留以避免误选业务库。迁移/清理断言数据库为agenthub_p2_test；新增四张P4表一并TRUNCATE，schema断言包含十一张业务表。测试共享库，不用xdist或同时开两个pytest进程；只在可丢弃库降级。

```powershell
# 先设置专用测试口令，启动独立测试库；不要使用业务库连接配置。
docker compose -f compose.test.yaml up -d --wait
$env:AGENTHUB_RUN_P2_INTEGRATION = '1'
uv run --locked pytest -q
```

真实Flash仅在本机.env配置已完成且允许公开数据云调用时另行执行：

```powershell
$env:AGENTHUB_RUN_P4_EMBEDDING_ACCEPTANCE = '1'
uv run --locked pytest tests/test_knowledge_real_embedding.py -q -s
Remove-Item Env:AGENTHUB_RUN_P4_EMBEDDING_ACCEPTANCE
```

该用例最多三份固定公开短文/三批请求，不读取用户文件，不自动重试。只检查真实文档ready、job和pgvector维度/分块API，不做质量/账单推断。默认pytest不会发送新付费请求。实际进程终止回收测试只在专用库领取任务、不调用云；过期时间由测试明确调整，不冒充已验证真实等待整个租约或断电恢复。

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

## P3 模型与会话验证

新增 tests/test_chat_provider.py（9 项，无外网 HTTP 替身）与 tests/test_chat_integration.py（6 项，独立真实 PostgreSQL，模型为替身）。沿用上面 P2 专用库启动和清理命令，将 pytest 目标改成 `tests/test_identity_integration.py tests/test_chat_integration.py`；保持顺序执行，不用 xdist。P2 清理和 schema 断言已包含三张新增表。

本轮默认 57 passed、30 skipped；显式真实数据库 29 passed。P3 增加真实 TCP SSE 断线取消和模型总超时验证，模型替身不证明供应商接通。详细边界见 [P3 记录](acceptance/P3_2026-11-10.md)。

## P3 真实 DeepSeek 验收（显式付费）

配置本机 .env 后：`uv run --locked python scripts/verify_p3_model.py` 只验证模型适配，最多两次固定公开提示请求。认证 API 真实模型测试需同时设置 AGENTHUB_RUN_P2_INTEGRATION=1、AGENTHUB_TEST_DATABASE_PASSWORD（对应专用库）、AGENTHUB_RUN_P3_MODEL_ACCEPTANCE=1，再运行 `uv run --locked pytest tests/test_chat_real_model.py -q -s`，额外两次请求。每次输出上限 128，thinking disabled，不重试。默认 CI/pytest 不发付费请求；不要在不知情的回归中启用付费标志。

本轮这两种真实模型验收均通过，共 4 次；测试程序不自动读取或上传项目文件。模型错误矩阵仍用可控替身。新增思考扩展单元测试，默认无外部服务统计变为 58 passed、31 skipped。

## Embedding 适配器

新增 tests/test_embedding_provider.py，30 项替身协议/错误/生命周期测试，不调用云端。默认全套 88 passed、31 skipped。scripts/verify_p3_embedding.py --check 只检查配置；不带 --check 才发送两次固定公开文本请求，需要北京业务空间地址和密钥。当前 --check 返回 embedding_not_configured，用户明确稍后填写，因此真实验收未执行。没有把测试替身或配置检查记为真实接通。

## Embedding 额度错误续验

2026-11-10：真实配置检查通过、模型列表 200 且模型可见，两个公开文本向量请求均失败，诊断 403 insufficient_quota。新增三项替身回归检查额度错误分类/脱敏/不重试，Embedding 33 passed；默认全套 91 passed / 31 skipped。未重新运行付费 DeepSeek 或真实数据库回归，因为仅改变 Embedding 的 403 分类；未把模型列表成功当成推理成功。

## P3 阶段最终验证（Flash）

协议测试现以实际选用的 qwen3.7-text-embedding-flash 校验 payload/model；Embedding 33 项。新增真实 Uvicorn 进程重启后历史读取（test_chat_integration.py），创建消息时用明确模型替身，两个子进程只读真实 PostgreSQL，无模型凭据或新增付费请求。聊天集成由 6 项增至 7 项，身份与聊天共 30 项。

完整 pytest 显式启用独立 PostgreSQL、关闭付费标志：121 passed / 2 skipped；跳过 P1 外部依赖探针与独立付费 DeepSeek 测试（后者已在前轮单独通过）。Flash 真实脚本两次请求通过，共 115 token、5 条 1024 维向量和两个正确 Top1；不是完整检索质量评测。
