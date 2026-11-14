# AgentHub 本地部署

最后更新：2026-11-14。既有开发库的最近部署记录为 P5 `20261112_0004`。P9 在独立 Compose 项目验收到 `20261113_0007`。 [P9 验收](acceptance/P9_2026-11-14.md)

## P9 控制台

使用当前 Docker 流程构建 API、启动依赖、应用已有迁移并启动 workers。前端文件随 Python 包进入 API 镜像，使用现有 `127.0.0.1:8000` 地址。首次按 [项目启动](../README.md#docker-启动) 初始化管理员，再访问 [控制台](http://127.0.0.1:8000/)。评测、Agent 和入库依赖对应 worker，若持续排队，请在控制台读取任务或运行 ID，再通过 `docker compose logs worker agent-worker evaluation-worker` 排查。MCP 服务地址和模型密钥只在部署端配置。 [控制台使用](modules/CONSOLE.md)

## P7 Agent worker

已有库先用 `pg_dump -Fc` 保存业务库并检查归档目录。使用同一份 Compose 配置构建服务，在启动 Agent worker 前显式迁移：

```powershell
docker compose config --quiet
docker compose build api worker agent-worker mcp-local
docker compose up -d --wait postgres redis
docker compose run --rm --no-deps api /app/.venv/bin/alembic upgrade head
docker compose up -d --wait
docker compose exec -T api /app/.venv/bin/alembic current
```

API 与 agent-worker 共享模型、Embedding、数据库和受控 MCP 配置；两者都只读挂载本地 socket。远端需要自有 CA 时，应给两者挂载相同 CA 并传入相同路径。文档 worker 独立，Agent worker 使用 PostgreSQL 轮询，空闲间隔沿用 `AGENTHUB_WORKER_POLL_SECONDS`。Windows 主机也可用 `uv run --locked python -m agenthub.agents.worker`，它会选择 psycopg 支持的事件循环。主机模式需要可达的数据库与模型；本地 Unix socket 建议使用 Compose。

用 `docker compose logs agent-worker` 查看 worker 状态。API ready 检查 PostgreSQL 和 Redis，运行状态与事件可区分仍在排队、等待审批和执行失败。P7 验收用独立项目实际重建 worker；现有开发数据库应按本节操作升级。 [数据库](DATABASE.md#p7-agent-工作流与检查点)

## P6 工具与 MCP

历史 P6 迁移 `20261112_0005` 新增三张表。当前分支部署请用上方 P7 步骤，以下命令记录 P6 当时的构建与升级流程。

```powershell
docker compose config --quiet
docker compose build api worker mcp-local
docker compose up -d --wait postgres redis
docker compose run --rm --no-deps api /app/.venv/bin/alembic upgrade head
docker compose up -d --wait
docker compose exec -T api /app/.venv/bin/alembic current
```

Compose 通过 `/run/agenthub-mcp/local.sock` 接入网络隔离的 `mcp-local`。使用管理员身份登记 `local` 后可发现并登记乘法工具，见 [使用步骤](modules/TOOLS.md)。`docker compose logs mcp-local` 可查看本地服务状态，`/health/ready` 继续反映 PostgreSQL/Redis。工具服务故障由调用端点和 `tool_calls` 反映。

远端服务可通过 `.env` 提供 `AGENTHUB_MCP_REMOTE_TARGETS`，用 JSON 将键映射到 `{url, ip, auth}`。`url` 是最终 HTTPS MCP 路径，`ip` 是部署方批准的公网 IPv4，`auth` 为 `none` 或 `bearer`。使用 Bearer 时，用 `AGENTHUB_MCP_REMOTE_TOKENS` 将同一键映射到该服务专用令牌。API 和 agent-worker 读取远端配置。默认使用公有 CA；自有 CA 通过部署覆盖只读挂载并设置 `AGENTHUB_MCP_REMOTE_CA_FILE`。更换 URL、IP 或认证方式时用新服务键及新工具版本；同目标令牌可轮换后重建 API 和 agent-worker。

主机 Python 模式默认无本地 socket。使用 Docker 体验内置 MCP，或在 Unix 环境提供与 API 同路径的受控 socket。当前 P6 在隔离环境验收，本机既有开发库实查为 P5 `20261112_0004`。 [P6 验收](acceptance/P6_2026-11-12.md)

## P5 部署与操作

使用下节的构建和迁移顺序。升级前用 `pg_dump -Fc` 备份现有业务库，本轮备份位于 `data/backups/agenthub-pre-p5-20261112.dump`，已检查归档目录。0004 新增 `rag_runs`，继续使用 P4 的向量和原文卷。验证迁移为 head、`alembic check` 无差异、`/health/ready` 正常及 `/openapi.json` 中的三个 RAG 路径。

Compose 读取 `AGENTHUB_RAG_TOP_K`、`AGENTHUB_RAG_MIN_SIMILARITY` 和 `AGENTHUB_RAG_CONTEXT_CHARACTERS`。问答需配置 Embedding 和生成模型，纯检索只使用 Embedding。健康检查验证 PostgreSQL 与 Redis，真实模型可通过独立云验收用例验证。独立测试库回环端口可通过 `AGENTHUB_TEST_DATABASE_PORT` 配置。

## P4 部署与操作

先备份，再升级；API/worker 使用同一构建源、配置和 document_data 卷。不要使用 `down -v` 清除有价值的数据。

```powershell
docker compose build api worker
docker compose up -d --wait postgres redis
docker compose run --rm --no-deps api /app/.venv/bin/alembic upgrade head
docker compose up -d --wait api worker
docker compose exec api /app/.venv/bin/alembic current
docker compose logs --tail 50 worker
```

本轮先用 pg_dump custom 格式备份业务库至被 Git 忽略的 `data/backups/agenthub-pre-p4-20261110.dump`（16041字节），archive list 可读；随后升级0003。没有业务库降级/清空，没有恢复演练。降级会删知识库四表，不能作为有数据环境的无损回滚。

API/worker 均 UID999。镜像预创建并 chown 原文目录，新命名卷继承权限；本轮通过 API 写入/worker读取并移除临时探针文件验证共享卷。已有自定义卷权限不正确时需核对目录所有者后修复，不能扩大整个宿主文件系统权限。

Compose 原文路径固定 `/app/data/documents`；宿主默认 `data/documents`。上限通过 `.env.example` 的 DOCUMENT_MAX_BYTES/CHARACTERS/CHUNKS/PAGES、PARSE_TIMEOUT_SECONDS 与 WORKER_POLL_SECONDS 调整，两个服务共享相同配置。worker 内存512MiB、CPU1核。数据库/Redis不发布主机端口，worker 可访问配置的百炼服务；公开/脱敏原文会发送第三方模型。

`GET /health/ready` 仍只检查 PostgreSQL/vector 与 Redis，不能证明 worker 正在工作。确认后台服务可用需结合 compose ps、日志、任务状态；没有健康心跳或监控告警。worker失败重启后扫描持久任务；租约过期任务标 failed/worker_interrupted，显式 `/retry` 才创建新版本。

业务库完成 alembic current=20261110_0003、alembic check 无差异，API ready。真实 Redis 唤醒在独立验收键上通过 LPUSH/LTRIM/BLPOP 验证，键已消费清空。三类文件真实云入库使用专用可丢弃测试数据库及本机独立 worker 子进程；没有将该测试冒充业务容器中已登录用户的完整产品验收。

数据库备份与原文卷要配套：只恢复表会得到找不到原文的文档；只恢复文件不能恢复权限和向量关联。P10 需要演练一致性备份/恢复、空间告警和留存策略；本轮仅验证备份归档可读取，没有完整恢复证明。


## 当前环境与服务

本机为 Windows 11 家庭中文版、WSL 2.7.11.0、约 16 GB 内存。已安装 Docker Desktop 4.98.0（当前用户模式）、Engine/CLI 29.9.2、Compose 5.6.1，Linux containers / desktop-linux 上下文；Linux 容器数据位于 E:\DockerData。Python 3.13.12、uv 0.11.27 可用。

| 服务 | 实际镜像/构建 | 网络 | 持久化 |
| --- | --- | --- | --- |
| api | Dockerfile；Python 3.13.12、uv 0.11.27、uv.lock | 仅 127.0.0.1:8000 | agenthub_document_data，共享原文 |
| worker | 同一 Dockerfile，python -m agenthub.knowledge.worker | 无主机端口，访问模型服务 | agenthub_document_data |
| postgres | pgvector/pgvector:0.8.7-pg17-bookworm；实测 PostgreSQL 17.11 / vector 0.8.7 | 仅 Compose 网络 | agenthub_postgres_data |
| redis | redis:7.4.12-bookworm；AOF 已确认开启 | 仅 Compose 网络 | agenthub_redis_data |

镜像使用显式版本标签，实际拉取摘要见验收记录，尚未锁定 digest。API 以非 root UID 999 运行。数据库用户是镜像初始化用户，仅限本地开发；P2/P10 再落实业务账号权限与生产配置。Redis 没有密码，依赖无宿主机端口的本地网络边界，不适用于直接公开服务。

## Docker 安装与旧会话 PATH

本次从官方地址下载 4.98.0 安装包，校验 SHA-256 与 Docker Inc. 的有效签名，按 [官方安装说明](https://docs.docker.com/desktop/setup/install/windows-install/) 使用 --user、--quiet、--accept-license、--backend=wsl-2、--no-windows-containers、--wsl-default-data-root=E:\DockerData 安装，成功退出码 0。系统已有 WSL/虚拟化，无需提权、安装额外 Linux 发行版或重启；其他机器需先核实自身前置条件。

安装位置为 %LOCALAPPDATA%\Programs\DockerDesktop。安装器已更新用户 PATH，旧终端/Codex 进程可能仍继承旧值。新开终端，或仅在当前 PowerShell 会话执行：

```powershell
$dockerBin = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
$env:Path = "$dockerBin;$env:Path"
docker version
docker compose version
```

只通过完整路径调用 docker.exe 而不补 PATH，可能导致 docker-credential-desktop 找不到。不要因此清空 Docker 凭据配置。本次 Docker Hub 认证曾直连超时，预拉取基础镜像并让构建命令使用本机已有代理后成功；没有修改系统代理或镜像源。其他机器仅在需要时按自己的代理配置处理，不照搬本机代理端口。

## 本机磁盘占用与 uv 缓存迁移

2026-11-09 在 P1 完成后排查 C 盘并迁移缓存。以下为本机当时的快照，容量使用 GiB（1024³ 字节）；没有开发前的完整快照，不能把现存占用全部归因于 P1。

| 项目 | 实测位置与大小 | 判断与处理 |
| --- | --- | --- |
| Docker Desktop 程序 | `%LOCALAPPDATA%\Programs\DockerDesktop`，约 3.22 GiB | 当天 16:10 安装，是本次明确新增的 C 盘占用；保留原安装位置 |
| Docker CLI 插件 | `%USERPROFILE%\.docker\cli-plugins`，文件长度合计约 0.67 GiB | 与安装目录文件为硬链接，不再单独累加为占用 |
| Docker Linux 磁盘 | `E:\DockerData`，两个 VHDX 文件长度合计约 3.42 GiB | WSL 注册路径与实际文件均在 E 盘，无需再次迁移 |
| uv 缓存 | 原 `%LOCALAPPDATA%\uv\cache`，约 7.96 GiB | 只有约 0.16 GiB 文件的创建日期为当天；其余大多为历史缓存，现迁至 `E:\DevCache\uv` |
| Python 项目环境 | `E:\AgentHub\.venv`，约 0.10 GiB | 已在 E 盘，保留 |
| Windows 分页文件 | `C:\pagefile.sys`，文件长度约 19.51 GiB；Win32_PageFileUsage 报告已分配 13,318 MiB，当前使用约 1.2 GiB | 不同接口的统计口径不同；Windows 自动管理，不能把文件长度当作当前内存使用或 P1 新增量 |
| WSL swap | C 盘 Temp 下的 `swap.vhdx`，文件长度约 36 MiB | 当前体积较小，本轮未调整 |

分页文件最后修改时间约为当天 16:14，与容器验收时间接近；内存压力触发自动扩容是可能原因，但缺少之前的尺寸记录，无法确认扩容量或仅归因于 Docker。系统提交内存快照约 24.6 GiB，物理内存约 15.2 GiB，确实存在分页需求。微软说明系统管理的分页文件会随提交内存和崩溃转储需求调整，见 [分页文件容量说明](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/how-to-determine-the-appropriate-page-file-size-for-64-bit-versions-of-windows)。本轮没有关闭分页、修改其位置或重启 Windows。

### 已生效的缓存位置

本机原来没有用户级 uv 配置。本轮新增 `%APPDATA%\uv\uv.toml`：

```toml
cache-dir = "E:\\DevCache\\uv"
```

同时保留 `%LOCALAPPDATA%\uv\cache` 到 `E:\DevCache\uv` 的 NTFS Junction。它是路径入口，缓存内容实际在 E 盘；旧进程即使仍使用默认 C 盘路径，也能访问迁移后的目录。统计 C 盘目录时不要跟随该入口再次累加 E 盘内容。

用户级配置影响当前 Windows 用户的 uv 默认缓存位置，不写入项目的 pyproject.toml；显式命令行参数、环境变量或项目配置可以覆盖它。验证当前实际位置：

```powershell
uv cache dir
```

预期为 `E:\DevCache\uv`。用户级配置无需重新打开终端即可在后续 uv 命令中读取。详见 [uv 配置发现与优先级](https://docs.astral.sh/uv/concepts/configuration-files/) 和 [uv 缓存说明](https://docs.astral.sh/uv/concepts/cache/)。缓存与本项目虚拟环境同处 E 盘，有利于后续硬链接复用；其他盘上的环境可能需要跨盘复制。

### 迁移与验证记录

1. 确认无运行中的 uv/Python 缓存任务，将原缓存复制至 E 盘；52,492 个文件、8,542,018,186 字节逐一通过 SHA-256 校验。
2. 设置用户级配置，验证现有项目测试与健康接口，再通过 `uv cache prune --cache-dir E:\DevCache\uv` 清理约 50.7 MiB 无用条目，通过 `uv cache clean --cache-dir` 显式指定原 C 盘缓存清理；不使用强制忽略锁参数。
3. 原路径由 uv 清理完成后已不存在，在该空缺位置创建 Junction，不覆盖已有目录。先前含目录重命名/回滚删除的组合命令被自动审批拦截，未执行；实际采用的是上述官方缓存管理流程。
4. 在忽略的 `data/storage/verification-venv` 创建独立环境，按原 uv.lock 安装 35 个包成功；`uv sync --locked --offline --reinstall` 再次成功，证明当前项目可使用迁移后的缓存完成离线重装。该验证环境位于 E 盘，未替换原 `.venv`。
5. 现有环境与独立环境 pytest 均为 33 passed、1 skipped；Ruff 通过。真实运行的三个容器均 healthy，live/ready 返回成功，PostgreSQL 与 Redis 检查为 ok。未重新执行会停服务的完整 P1 验收。

初次按包名、以及单独 wheel URL 做临时 `--with` 离线安装均未命中对应缓存，不能宣称所有临时依赖均可离线解析；未进一步追溯该缓存键差异。最终以项目实际使用的 uv.lock 独立安装与离线重装作为验证，不把早期失败记录为通过。

C 盘可用空间约从 **17.09 GiB 增至 25.08 GiB**，实测差值约 **7.99 GiB**；后台程序也可能引入少量波动。复制日志、SHA-256 校验及前后快照在忽略的 `data/storage/`，不提交机器缓存或临时环境。

Docker 程序本体仍在 C 盘。进一步迁移需先核实安装器在当前用户模式下的自定义目录支持与备份恢复流程，不能直接剪切安装目录。Windows 分页文件需通过系统设置调整并按提示重启，还需保留足够提交内存与转储支持；本轮只诊断，未改动。不要把 uv 缓存、Docker 数据磁盘和 Windows 分页文件当成同一种存储。

## 启动与配置

当前项目 .env 已生成非空随机 POSTGRES_PASSWORD 并受 Git 忽略；已有文件不要覆盖。新克隆环境首次复制 .env.example 为 .env，再设置独立密码。不要打印带密码的完整解析配置：

```powershell
docker compose config --quiet
docker compose build api
docker compose up -d --wait postgres redis
docker compose run --rm --no-deps api /app/.venv/bin/alembic upgrade head
docker compose up -d --wait --wait-timeout 120
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

本次上述配置、构建和运行已成功。API 访问地址为 http://127.0.0.1:8000，Swagger UI 在 /docs。验收结束后三个服务保留运行且 healthy。

Compose 强制要求非空 POSTGRES_PASSWORD，同时传给 PostgreSQL 与 API；容器内主机名使用 postgres、redis。API 不加载宿主机整份 .env，配置按需显式注入。depends_on 的 service_healthy 等待数据库与 Redis 初始健康；API lifespan 再检查依赖。故障后依靠每次 ready 实际探测恢复，depends_on 不会持续监督或自动修复依赖。容器健康检查使用 ready。

## vector 初始化与数据保留

[init-vector.sql](../docker/postgres/init-vector.sql) 只在 PostgreSQL **新空卷首次初始化**时安装 vector 扩展，不创建业务表。已有卷不会重跑初始化 SQL；缺少扩展时，在确认目标为本地开发库后执行：

```powershell
docker compose exec postgres psql -U agenthub -d agenthub -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

ready 只读 pg_extension，不会偷偷修改结构。P2 已加入 SQLAlchemy 身份模型、Alembic 与业务索引，需显式迁移再启动业务。修改 .env 中密码不会自动修改已有数据库密码，应按已有实例的凭据管理流程处理。

本次真实验证：写入独立 PostgreSQL vector(3) 记录与 Redis 键，分别停止/恢复依赖，再保留卷执行 down/up。新容器 ID 全部变化，相同记录与键仍可读取，向量距离仍为 0，Redis appendonly=yes。测试标记已清理，未留下业务表或测试 schema。

docker compose stop 停止服务；docker compose down 移除容器/网络，保留命名卷。**不要附加删卷参数**。卷独立于容器，但不等于备份；此次正常停止和重建验收没有证明断电恢复、磁盘损坏或主机重装后的恢复能力。

## 可重复的真实验收

仅对本项目本地开发栈执行（会短暂停服务与重建容器）：

```powershell
uv run --locked python scripts/verify_p1.py
```

脚本验证成功依赖、API 非 root/依赖端口不发布、容器内集成 pytest、两个依赖分别 stop/start 时的 live/ready 和 API ID、日志请求 ID/数据库密码边界，以及 down/up 后 PostgreSQL/vector/Redis AOF 数据保留。finally 尝试恢复服务并只清理自身随机标记，将证据保存到忽略的 data/acceptance JSON。任何失败需检查报告，不得当作成功。

只手工检查 Redis 时可使用 stop redis、curl.exe -i 访问两个健康端点、start redis；PostgreSQL 同理。手动请求反映当次状态，Docker 健康标记要等下一次 healthcheck 才更新。不要在有业务运行的环境随意演练。

## 主机开发与容器网络

根 README 的 Python 模式不自动启动数据库；默认 Compose 没有数据库主机端口，因此 Windows Python 不能直接连接 postgres:5432。若以后需要主机调试数据库，应另行提供仅回环绑定的开发端口映射并配置实际连接信息，不把内部服务名当作 Windows DNS 名。

真实集成测试已在一次性容器内通过，方法见 [测试文档](TESTING.md)。查看应用日志用 docker compose logs api；不要输出整个配置或原始凭据。P10 再完成独立备份恢复、发布升级、生产治理和性能验收。

## P2 数据库与管理员初始化（2026-11-10）

本轮引擎最初未运行，启动提示缺失安装注册信息。同一份 Docker Desktop 4.98.0 本地安装包经有效签名校验后，以原 user/WSL2/E:\DockerData 参数重新安装恢复，退出码0；引擎恢复为29.9.2。未删除原 PostgreSQL/Redis 命名卷，原依赖容器仍可启动；没有手写注册表、重置 Docker 或执行删卷操作。

当前四张身份表迁移版本为 `20261110_0001`。API镜像已包含 alembic.ini/migrations；按上文“构建 → 依赖 → 显式迁移 → API”顺序部署。API启动/ready不自动建表或检查迁移版本；缺迁移时业务可能500，因此部署必须先迁移。

```powershell
docker compose exec api /app/.venv/bin/alembic current
docker compose exec api /app/.venv/bin/python -m agenthub.identity.cli init-admin --username admin
```

首位管理员需用户隐藏输入12—128字符密码；本轮没有在本地开发库创建管理员或默认凭据。空库初始化流程已在独立测试库真实验证，已有用户时CLI拒绝覆盖。

业务配置通过 Compose 注入。`AGENTHUB_SESSION_TTL_SECONDS=3600`（60—86400），`AGENTHUB_DATABASE_POOL_SIZE=5`（1—20，无 overflow）。密码和完整令牌不作为命令行参数。API 保持回环 8000，独立测试库回环端口默认 55432。测试容器、网络和 tmpfs 的清理与业务卷隔离。

升级/回滚、表结构见 [DATABASE](DATABASE.md)，权限与部署限制见 [SECURITY](SECURITY.md)。P1故障/重建复验脚本不是迁移工具；它保留业务卷，但也不能替代备份。

## P3 模型配置与升级

`.env.example` 新增 AGENTHUB_MODEL_*，compose.yaml 显式透传模型配置。地址是受控服务的 base URL，代码追加 /chat/completions，密钥不进入 API 响应。默认留空会返回 model_not_configured；健康探针不调用付费模型。生产依赖已包含现有 httpx2。环境代理不自动继承，若网络必须使用代理需另行设计明确配置。

配置确认后先按 DATABASE.md 备份、构建、执行 alembic upgrade head 应用 0002，再 `docker compose up -d --wait api`。业务栈升级结果见下。

2026-11-10 续验：业务库先执行 pg_dump -Fc，归档目录可被 pg_restore --list 读取，备份保存在被 Git 忽略的 data/backups/pre-p3-20261110-33d03c75a7a242cd913b1b8bee4eb517.dump（9985 字节；未做恢复演练）。随后真实构建镜像、一次性迁移容器 upgrade head、重建 API；alembic current=20261110_0002，/health/ready 的 PostgreSQL/Redis 均 ok，容器内确认 deepseek-flash、密钥已配置和 thinking=disabled。没有清理业务卷、创建默认管理员或执行数据库降级。

## Embedding 环境配置

独立参数为 AGENTHUB_EMBEDDING_BASE_URL/API_KEY/MODEL/DIMENSIONS/TIMEOUT_SECONDS/BATCH_SIZE/MAX_CHARACTERS，完整值及北京业务空间 URL 格式见 [接入模块](modules/EMBEDDINGS.md)。用户稍后填写 URL 和密钥，其余已补入本机 .env；Compose 显式透传，不复用 DeepSeek 密钥。无需新依赖或数据库迁移。配置后先运行 verify_p3_embedding.py --check，再显式运行付费验收；API 容器读取环境需重建/重建容器，简单 restart 不加载新 Compose 环境。

Embedding 本轮真实重建 API 镜像并执行 compose up -d --wait api，服务 ready 成功。容器配置为 qwen3.7-text-embedding/1024，URL/密钥仍空；填写后重新创建 API 容器使环境生效，host 验收脚本直接读取 .env 无需先启动容器。没有新增数据库迁移。

Embedding URL/密钥现已填写，URL 已从纯业务空间 ID 补齐为完整北京 HTTPS 地址。真实模型列表可见但推理被 403 insufficient_quota 拒绝；需在账号控制台核对当前模型额度/仅免费策略/余额，重建或重试代码无法修复额度问题。

2026-11-10 最终切换：Embedding 模型为 qwen3.7-text-embedding-flash，1024 维，地址/密钥不变；compose up -d --wait api 重新创建容器，容器配置检查与 ready 通过。本轮业务镜像中的适配器无需变更，不重建相同镜像；没有数据库迁移、删卷或业务数据清理。

## P8 评测 worker

评测模块使用与 API、Agent worker 一致的模型、Embedding、PostgreSQL 和 MCP 目标配置。本地 MCP socket 挂载为只读。若远端使用私有 CA，给 `evaluation-worker` 同步挂载和设置对应路径。

```powershell
docker compose build api worker agent-worker evaluation-worker mcp-local
docker compose up -d --wait postgres redis
docker compose run --rm --no-deps api /app/.venv/bin/alembic upgrade head
docker compose up -d --wait
docker compose exec -T api /app/.venv/bin/alembic current
```

升级先备份业务库并在维护窗口切换 API 和 worker。P8 head 为 `20261113_0007`。若评测长期排队，检查 `docker compose logs evaluation-worker`、迁移版本和正在运行的评测数。主机启动命令为 `uv run --locked python -m agenthub.evaluations.worker`，使用可达的 PostgreSQL 与模型服务。P8 在独立栈验收，部署证据见 [P8 验收](acceptance/P8_2026-11-13.md)。
