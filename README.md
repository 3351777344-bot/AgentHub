# AgentHub

企业级 AI Agent 平台的项目实践。**P0—P7 已完成，支持私有知识库、RAG、受控工具、LangGraph Agent 和可恢复工作流。** [项目状态](docs/PROJECT_STATUS.md) · [P7 验收](docs/acceptance/P7_2026-11-13.md)

## 本地 Python 启动

要求 Python 3.13、uv；本次实际验证版本为 Python 3.13.12、uv 0.11.27。以下命令在项目根目录 PowerShell 执行：

```powershell
uv sync --locked
Copy-Item .env.example .env
uv run --locked uvicorn agenthub.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

仅首次创建 `.env` 时执行复制，已有配置请直接编辑。无需激活虚拟环境，uv 自动使用项目 `.venv`。不要提交 `.env`。日志仅记录路由模板、方法、耗时与状态；禁用 Uvicorn 默认 access log，避免其中的完整 URL 泄露查询参数。

另开终端验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

`/health/live` 返回 `{"status":"ok"}`。未配置可用 PostgreSQL（已启用 vector 扩展）和 Redis 时，`/health/ready` **应返回 503**，PowerShell 会报告 HTTP 错误；这是真实依赖检查。启动时也检查依赖，但依赖故障不会阻止 API 启动，便于诊断和恢复。接口说明见 [API 文档](docs/API_DESIGN.md)，交互文档位于 [Swagger UI](http://127.0.0.1:8000/docs)。

## Docker 启动

需要 Docker Engine/Desktop（Linux containers）和支持 Compose Specification 的 Compose CLI。本机已安装 Docker Desktop 4.98.0、Engine 29.9.2、Compose 5.6.1，采用 WSL 2，数据放在 E:\DockerData。新开终端可使用安装后 PATH；当前 `.env` 已生成密码，不要覆盖。其他新环境首次复制 `.env.example` 为 `.env`，设置非空、独立的 `POSTGRES_PASSWORD`，然后：

```powershell
docker compose config --quiet
docker compose build api worker agent-worker mcp-local
docker compose up -d --wait postgres redis
docker compose run --rm --no-deps api /app/.venv/bin/alembic upgrade head
docker compose up -d --wait
```

仅 API 绑定 `127.0.0.1:8000`。数据库和 Redis 使用内部连接，本地 MCP 使用 Unix socket。已有数据的升级步骤见 [部署说明](docs/DEPLOYMENT.md)。启动后可访问 [Swagger UI](http://127.0.0.1:8000/docs)。

Agent 由独立 `agent-worker` 执行，可在 Swagger 创建 Agent 和运行，查看步骤、审批与状态。 [使用步骤](docs/modules/AGENTS.md)

在 Swagger 创建知识库，以 multipart `file` 上传公开或脱敏的 TXT、Markdown、文本 PDF。文档变为 `ready` 后，可调用 `POST /api/v1/knowledge-bases/{kb_id}/search` 检索。创建自己的会话后，调用 `POST /api/v1/conversations/{conversation_id}/rag`，提交 `knowledge_base_id` 和 `question`。可加 `?stream=true` 接收 SSE，以最终 `done` 为准。通过返回的文档 ID、分块 ID 和页码定位原文。步骤与配置见 [RAG 模块](docs/modules/RAG.md)。

首次设置管理员（无默认账号/密码，隐藏输入，已有用户时拒绝覆盖）：

```powershell
docker compose exec api /app/.venv/bin/python -m agenthub.identity.cli init-admin --username admin
```

密码需 12–128 字符且不能全为空白。然后在 Swagger 执行 `POST /api/v1/auth/login`（JSON username/password），复制返回的 access_token，在 **Authorize** 中输入令牌，调用 `/api/v1/auth/me` 或用户管理接口。退出只撤销当前令牌，改密撤销全部会话。不要将真实令牌粘贴到代码或 Git。契约与边界见 [API](docs/API_DESIGN.md)、[安全说明](docs/SECURITY.md)。

登录后可直接调用 `POST /api/v1/tools/add_integers/1/calls`，提交 `{"arguments":{"left":6,"right":7}}`。通过管理员注册 `local` 服务和乘法工具，可复验真实 MCP 调用。 [工具模块](docs/modules/TOOLS.md)

主机 Python 模式需要自行提供可达 PostgreSQL；在对应配置下先执行 `uv run --locked alembic upgrade head`，再初始化管理员。默认 Compose 不向主机发布业务数据库端口，不能直接在 Windows 上使用容器内部主机名 `postgres`。

在本地开发栈复验（会短暂停止依赖并重建项目容器，保留卷，只清理自身标记数据）：

```powershell
uv run --locked python scripts/verify_p1.py
```

## 开发检查

```powershell
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked python -m compileall -q src tests
```

测试中的替身只用于确定性分支验证，不代表真实依赖接通。真实依赖测试的启用方法见 [测试说明](docs/TESTING.md)。项目路线见 [文档索引](docs/README.md)，实现说明见 [基础模块](docs/modules/FOUNDATION.md)。
