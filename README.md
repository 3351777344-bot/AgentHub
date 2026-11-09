# AgentHub

企业级 AI Agent 平台的项目实践。**P1 工程底座已完成真实 Docker、依赖故障恢复与持久化验收**；知识库、RAG、Agent、认证和前端尚未实现。当前进度见 [项目状态](docs/PROJECT_STATUS.md)，验证证据见 [P1 验收记录](docs/acceptance/P1_2026-11-09.md)。

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
docker compose up --build -d --wait
```

仅 API 绑定 `127.0.0.1:8000`；数据库和 Redis 无主机端口。Compose 会为 API 注入内部连接配置。完整验证、已有卷扩展初始化和故障演练见 [部署说明](docs/DEPLOYMENT.md)。本次验收结束后服务保留运行，可直接访问 [Swagger UI](http://127.0.0.1:8000/docs)。

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
