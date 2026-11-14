# AgentHub 0.1.0 运维手册

## 启动与停机

在项目根目录准备独立 `.env`。回环部署使用 `compose.yaml`。它发布 `127.0.0.1:${AGENTHUB_API_PORT:-8000}`，数据库和 Redis 只使用容器网络。首次启动使用 `docker compose build api postgres` 和 `docker compose up -d --no-build --wait --wait-timeout 120`。每个应用进程使用同一个镜像。PostgreSQL 就绪后运行单次 `migrate`，随后才启 API 和 worker。API ready 还会检查数据库扩展及 Alembic head、Redis PING。MCP 在无网络环境启动，Unix socket 先通过健康检查。

维护前关闭入口，先停止 edge（如启用）和 API，再停止三个 worker，保留 PostgreSQL、Redis 和本地 MCP 直到 worker 停止。worker 收到 SIGTERM 后完成当前周期，Compose 给应用 45 秒退出窗口。时间更长的任务应先通过界面取消、暂停或等到终态。超过窗口的任务按各自中断规则处理，详见 [入库](modules/JOBS.md)、[Agent](modules/AGENTS.md) 和 [评测](modules/EVALUATIONS.md)。

## 对外部署

`compose.edge.yaml` 和本地配置叠加后，API 不映射宿主机端口。NGINX 仅从 TLS 入口转发到 API，由部署者提供 DNS 名、证书链和私钥。缺省仍绑定回环方便测试。外部监听需要明确设置 `AGENTHUB_EDGE_BIND` 并通过部署网络放行指定来源。凭据文件放在仓库之外，确保 NGINX UID 1000 或 Redis 的运行用户可读取，其他用户不可读。直接连接 NGINX 的部署使用其观察到的来源地址。入口替换转发头和请求 ID，API 只信任 ingress 网络上的固定代理地址。代理保留 Host 并禁用流缓冲以支持 SSE。网段与本机 VPN 冲突时，成对调整代理地址和 Uvicorn 信任列表。更多前置配置见 [.env.example](../.env.example)。

1. 独立设置 `AGENTHUB_RUNTIME_DATABASE_USER`、运行口令与 PostgreSQL 管理口令。Redis 私有配置文件写入 `requirepass`，连接 URL 中口令使用 URL 编码。设置证书路径和对外绑定。
2. 用下方文件组合及同一个项目名运行 `build api postgres edge`，然后运行 `up -d --no-build --wait postgres redis` 和 `run --rm migrate`。
3. 从维护终端 `exec postgres psql -X -U agenthub -d agenthub`。创建运行登录角色，例如 `CREATE ROLE agenthub_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;`，用 `\password agenthub_runtime` 隐藏输入设置口令。随后从主机将 [runtime-grants.sql](../docker/postgres/runtime-grants.sql) 输入 `exec -T postgres psql -X -v runtime_role=agenthub_runtime -U agenthub -d agenthub`。恢复后按相同流程重配权限。
4. 执行 `up -d --no-build --wait --wait-timeout 120`，再通过 HTTPS 验证证书主机名、ready、首页和身份。Swagger 与 OpenAPI 在对外模式关闭。首位管理员通过现有受控 CLI 创建。

```text
docker compose -f compose.yaml -f compose.edge.yaml -p agenthub-edge <命令>
```

应用容器以固定 UID 999、只读根文件系统、无 Linux capabilities 运行。备份目录、外部证书与凭据分别受部署权限管理。单栈模型预算按调用和运行配置生效。登录按来源 IP 使用固定窗口。公网 DDoS、帐号分布式撞库、供应商账单上限和公网 CA 运转由站点环境管理。

## 观察与保留

使用 `docker compose logs --no-color api worker agent-worker evaluation-worker`。API 日志记录路由模板、状态和耗时。将响应的 `X-Request-ID` 与入库的 `job_id`、Agent 的 `agent_run_id` 或评测的 `evaluation_run_id` 对照。评测子 Agent 继承批次建立请求 ID。常见故障先查 ready，再看队列状态、最近错误码和对应任务或步骤账本。SSE 的 HTTP 200 可能随后收到失败帧，故障归因应结合终止事件和持久状态。

JSON 日志每容器 10 MiB、3 个文件滚动。由运维侧按实际需求收集到访问受限的日志服务。正常事件内容限于服务生成的元数据。原文、模型提示、授权头和上游错误文本属于敏感数据。运维指标 `GET /api/v1/operations/metrics` 需要管理员 Bearer，按状态聚合三类任务。HTTP P95 从请求耗时统计，监控容量时同时留意 DB 连接和队列积压。

首版检查点随业务数据库保留，暂停、待审批或可恢复运行保留完整检查点。终态暂保留，备份也可能含历史原文和状态。运维每周评估表和磁盘增长、每月核对组织的留存要求。未实施自动清理 LangGraph 数据，后续清理需定于单独维护窗口，核对步骤账本和审计后实施。

## 一致性备份与恢复

仅在单实例维护窗口操作。停止所有应用写入者和外部客户端，阻止定时任务和手动启动在操作窗口重新写入。备份脚本会检查 Compose 写入者和活动数据库连接。用 `pg_dump -Fc` 保存包括 Alembic、向量、步骤和 LangGraph 检查点在内的一个数据库快照，同窗口归档原文卷，校验有效原文与数据库 SHA-256，写入成对清单。Redis 的唤醒队列和限流计数可从空实例起步，待处理任务由 PostgreSQL 持久状态重建。

```powershell
docker compose -p <源项目> stop api
docker compose -p <源项目> stop worker agent-worker evaluation-worker
uv run --locked python scripts/ops_backup.py backup <新备份目录> --project <源项目>
docker compose -p <新项目> up -d --no-build --wait postgres redis
uv run --locked python scripts/ops_backup.py restore <备份目录> --project <新项目>
docker compose -p <新项目> up -d --no-build --wait --wait-timeout 120
```

命令须使用各项目对应的口令与端口，可为脚本传 `--env-file` 和多个 `--file`。恢复要求不同项目、独立新数据库和新原文卷。先校验归档摘要与路径，然后用单事务 `pg_restore` 写目标数据库、解包到空卷，回读实际原文并校验。两次操作跨库和文件系统，出现部分恢复失败时保留目标诊断，重新准备全新目标后重跑。按恢复时点需要的版本配置启栈，检查原文下载、检索、业务所有权和检查点再切流。备份包含私有原文、会话摘要和工作流内容，应使用访问受控的加密存储及独立介质。清单仅提供完整性校验，归档来源需要信任。 [PostgreSQL pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html)

依赖停机验收和恢复时长见 [P10 验收](acceptance/P10_2026-11-14.md)。备份期间的维护窗口为一致性边界。恢复点取实际最近合格备份时刻，定时备份和异机保管需要部署方安排。

## 升级、回滚与发布

维护前保存上一版 Compose 配置和镜像，按上述流程获得备份并在另一隔离项目试恢复。更新镜像后运行 `docker compose run --rm migrate` 和 `docker compose run --rm --no-deps migrate /app/.venv/bin/alembic check`。验证成功后重建应用，完成数据库、TLS、控制台和 worker 冒烟，再恢复流量。

`20261114_0008` 仅增加两个可空关联字段。应用回退前先验证旧代码与当前库兼容；本次实际验证了独立库的升降迁移和用户保留。需要回退数据时，停止应用后恢复到经验证的旧版快照与镜像。单独降至 `20261113_0007` 会移除已记下的请求 ID。跨越其他迁移需要查表结构和恢复边界。旧快照之后发出的外部写操作应先在服务端对账，待确认运行保持停用。范围更大的恢复见 [数据库手册](DATABASE.md)。

`uv build --no-sources` 生成 wheel 和源码包。应用镜像包含 Alembic 迁移、API 与控制台。Compose 源码还包含网关、PostgreSQL/pgvector 和本地 MCP。固定基础镜像 digest，系统安全包来自当前发行版仓库，保留已验证的镜像摘要及归档可重复部署。 [P10 验收与发行状态](acceptance/P10_2026-11-14.md)
