# AgentHub

企业级 AI Agent 平台的项目实践。可在本地控制台使用私有知识库、RAG、受控工具、可恢复 Agent 工作流和自动化评测。 [项目状态](docs/PROJECT_STATUS.md) · [P9 验收](docs/acceptance/P9_2026-11-14.md)

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
docker compose build api postgres
docker compose up -d --no-build --wait --wait-timeout 120
```

API 和控制台共用 `127.0.0.1:8000`。数据库和 Redis 使用内部连接，本地 MCP 使用 Unix socket。上述命令面向新环境。已有数据先按运维手册备份并停机升级。 [运维手册](docs/OPERATIONS.md)

首次设置管理员（无默认账号/密码，隐藏输入，已有用户时拒绝覆盖）：

```powershell
docker compose exec api /app/.venv/bin/python -m agenthub.identity.cli init-admin --username admin
```

密码需 12–128 字符且不能全为空白。在 [控制台](http://127.0.0.1:8000/) 登录后，从左侧进入业务页面。

1. 在知识库上传公开或脱敏的 TXT、Markdown 或文本 PDF。文档可用后，选「在对话中使用」并新建会话；回答后可定位引用片段。
2. 在 Agent 页面选择工具和预算，发起运行并查看步骤。需要人审时，先复核工具版本与参数，再批准或拒绝。
3. 管理员可登记 `local` 服务、发现并注册本地 MCP 乘法工具。在评测页面创建版本化数据集，用两组配置运行并比较报告。

退出会撤销当前会话，改密撤销全部会话。更多操作见 [控制台](docs/modules/CONSOLE.md)。接口调试入口为 [Swagger UI](http://127.0.0.1:8000/docs)。

主机 Python 模式需要自行提供可达 PostgreSQL；在对应配置下先执行 `uv run --locked alembic upgrade head`，再初始化管理员。默认 Compose 不向主机发布业务数据库端口，不能直接在 Windows 上使用容器内部主机名 `postgres`。

P10 发布验收在隔离 Compose 项目复验。脚本和已知限制见 [测试说明](docs/TESTING.md#p10-运维与发行验收)。

```powershell
uv run --locked python scripts/verify_ops.py http://127.0.0.1:18100
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
