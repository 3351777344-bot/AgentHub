# AgentHub 数据库

最后更新：2026-11-12。迁移头版本为 `20261112_0004`。业务表覆盖身份、会话、知识库与检索问答。升级前备份和验证见 [P5 验收](acceptance/P5_2026-11-12.md)。

## 模型与迁移入口

- ORM：[identity/models.py](../src/agenthub/identity/models.py)。公共 Base、引擎和会话：[db/session.py](../src/agenthub/db/session.py)。
- 迁移：[首个版本](../migrations/versions/20261110_0001_identity.py)、[迁移环境](../migrations/env.py)、[alembic.ini](../alembic.ini)。
- PostgreSQL UUID 主键由 Python `uuid4()` 生成；时间为带时区时间。数据库创建时间默认 `now()`；身份有效期判断使用数据库 `clock_timestamp()`；P4 worker 租约使用进程的 UTC 时钟，当前同机部署，多机扩展需校时与时钟偏差验证。

## 已实现表

| 表 | 字段 | 约束与索引 |
| --- | --- | --- |
| roles | name varchar(16) | 主键；CHECK 仅允许 admin/member；迁移插入两个角色 |
| users | id UUID、username varchar(64)、password_hash varchar(256)、role_name varchar(16)、is_active boolean、created_at timestamptz | username 唯一且匹配小写 ASCII 名称；role_name 外键并建索引；is_active 默认 true |
| login_sessions | id UUID、user_id UUID、token_hash varchar(64)、created_at / expires_at / revoked_at timestamptz | user_id 外键并建索引；token_hash 唯一；expires_at 索引；CHECK expires_at > created_at；revoked_at 可空 |
| audit_events | id UUID、actor_id / target_user_id UUID、action varchar(64)、request_id varchar(64)、created_at timestamptz | actor/target 外键可空；created_at 索引；无密码、原始令牌或任意请求体字段 |
| alembic_version | version_num | Alembic 自己管理，不是业务 ORM 模型 |

以下可空字段说明针对 P2 身份表：除三个可空字段 `revoked_at`、`actor_id`、`target_user_id` 外，业务字段均非空。所有外键采用 `ON DELETE RESTRICT`，不提供用户硬删除 API；账号使用禁用，保留审计关联。UUID 的 Python 默认值不等于数据库服务端默认值，直接 SQL 写入需显式提供 UUID。

关系：`roles 1:N users`，`users 1:N login_sessions`；`audit_events` 的操作者与目标分别指向 users。当前每个用户只有一个角色，没有组织表或共享授权表。

## 会话与事务

`create_database_engine()` 使用同步 SQLAlchemy 2 + psycopg、`pool_pre_ping`、默认 5 个连接、无 overflow。连接等待由依赖超时配置控制；SQL 10 秒、锁等待 5 秒。FastAPI 身份路由为 `def`，在工作线程执行阻塞数据库/哈希操作。

`get_session()` 为每个请求创建 Session，关闭时回滚未提交事务。服务明确调用 `commit()` 后才返回成功，避免在响应已发送后才提交。`create_user()` 先 flush 获取主键，再插入审计，最后一次 commit；审计失败则整体回滚。SQL 参数被隐藏，不打印带口令的连接 URL。

管理员写操作与初始化锁定 `roles.admin` 行；等待完成后重新检查操作者与会话，避免两个管理员互相并发禁用后无人可管理。登录、改密和修改目标用户时锁定用户行；改密或角色/状态变动在同一事务中撤销已有会话。普通读取不锁定整个业务流程，撤销不取消已经通过认证的在途只读请求。

## 迁移操作

新环境或升级：先备份有价值的数据，构建镜像，启动依赖，显式迁移，再启动 API：

```powershell
docker compose build api
docker compose up -d --wait postgres redis
docker compose run --rm --no-deps api /app/.venv/bin/alembic upgrade head
docker compose up -d --wait
docker compose exec api /app/.venv/bin/alembic current
```

主机已有可达数据库时可用 `uv run --locked alembic upgrade head`。连接配置统一来自 Settings，不在 alembic.ini 存放密码；API 启动和 ready 都不会自动建表/迁移。P4 迁移还会 CREATE EXTENSION IF NOT EXISTS vector，兼容独立空库；迁移不删除已有扩展。

新增版本：修改模型 → `uv run --locked alembic revision --autogenerate -m "描述"` → 人工检查生成 SQL 与存量数据兼容性 → 独立测试库验证 → `upgrade head`。已经应用的版本不原地修改，用新版本演进。

`downgrade base` 会删除身份、聊天和知识库的全部十一张业务表，**只在可丢弃测试库演练**；本次没有在现有开发库执行降级。真实数据回滚需备份恢复或前向修复，不能把提供 downgrade 函数当作无损回滚承诺。

## 本次迁移与验证记录

| 版本 | 变化 | 验证 |
| --- | --- | --- |
| 20261110_0001 | roles/users/login_sessions/audit_events，角色种子与约束索引 | 独立 PostgreSQL 空库升级、降级后升级、重复升级、Alembic check 与 ORM 一致性；本地 Docker 开发库执行 upgrade head |

独立测试使用 [compose.test.yaml](../compose.test.yaml) 的 `agenthub_p2_test` 数据库与 tmpfs。端口默认仅绑定回环 55432，可用 `AGENTHUB_TEST_DATABASE_PORT` 覆盖。测试在迁移、清理前断言 `current_database()`。

未实现：会话/审计保留清理任务、运行账号与迁移账号分离、数据库级审计防篡改、多租户隔离、备份恢复演练与业务性能基线。

## P3 会话迁移 20261110_0002

| 表 | 字段 | 约束与索引 |
| --- | --- | --- |
| conversations | id UUID、owner_id UUID、title varchar(200)、next_position int、busy_until timestamptz 可空、created_at timestamptz | owner 外键 users RESTRICT；(owner_id, created_at, id) 索引；next_position Python 默认 1 |
| messages | id UUID、conversation_id UUID、position int、role varchar(16)、content text、status varchar(16)、error_code varchar(64) 可空、created_at timestamptz | conversation 外键 RESTRICT；(conversation_id, position) 唯一并支持历史顺序查询；role CHECK user/assistant；status CHECK pending/completed/failed/cancelled |
| model_calls | id UUID、assistant_message_id UUID、model varchar(200)、request_id varchar(64)、prompt_tokens/completion_tokens/duration_ms int 可空 | assistant_message 外键 RESTRICT 且唯一；失败或服务未提供用量时保留 NULL，不伪造 0 |

users 1:N conversations 1:N messages；助手消息与 model_calls 1:1。UUID 仍由 Python 生成；content Python 默认空串；不保存密钥或供应商原始错误。会话仅创建和查询，无删除接口。模型调用标识为发送时配置的模型名。

迁移文件：`migrations/versions/20261110_0002_chat.py`，前驱 0001。已在独立可丢弃 PostgreSQL 验证空库到 head、降级至 base 后重新升级、重复升级及 alembic check。业务开发库已在备份后执行 upgrade head，未执行降级。部署时按上面的备份/显式 upgrade head 流程应用 0002；降级会删除三个 P3 表，只允许可丢弃测试库演练，已有聊天数据不能无损回滚。

准备事务锁 conversations 行，为同一会话分配连续两个 position 并设置 busy_until。生成期间无 DB 事务；收尾同样先锁会话再更新消息和用量。硬退出留下 pending，下次发送在租约过期后标记 failed。查询不会自动恢复，直接读取时可能仍看到 pending。

## P4 知识库迁移 20261110_0003

ORM 为 `src/agenthub/knowledge/models.py`，迁移为 `20261110_0003`，前驱是 0002。该迁移新增四张表。P5 在这些向量上执行精确检索。

| 表 | 字段 | 约束与索引 |
| --- | --- | --- |
| knowledge_bases | id/owner_id UUID，name varchar(200)，embedding_model varchar(200)，embedding_base_url varchar(512)，dimensions int，chunk_size/overlap int，pipeline_version varchar(64)，deleted_at 可空，created_at | owner → users RESTRICT；(owner_id, created_at, id) 索引；CHECK dimensions=1024；chunk_size 100–4000，0≤overlap<size |
| documents | id/knowledge_base_id UUID，filename varchar(200)，file_type varchar(8)，checksum varchar(64)，size_bytes int，status varchar(16)，version/chunk_count int，error_code varchar(64) 可空，deleted_at/file_removed_at 可空，created_at | KB 外键 RESTRICT；(KB, created_at, id) 索引；有效记录 (KB,checksum) 部分唯一索引 WHERE deleted_at IS NULL；status CHECK queued/processing/ready/failed/deleted |
| ingestion_jobs | id/document_id UUID，version int，status varchar(16)，request_id varchar(64)，lease_until/finished_at 可空，error_code 可空，total_tokens int 可空，completed_batches int，created_at | document 外键 RESTRICT；(document_id,version) 唯一；(status,created_at) 索引；status CHECK queued/processing/completed/failed/cancelled |
| document_chunks | id/document_id/job_id UUID，position/page_number/char_start/char_end int，content text，embedding vector(1024) | document/job 外键 RESTRICT；(document_id,position) 唯一；CHECK char_start≥0 且 char_end>char_start |

关系：users → knowledge_bases → documents → ingestion_jobs；每个 chunk 同时关联 document 与生成它的 job。分块模型空间和策略来自知识库，job.version 与 document.version 通过服务锁和发布校验绑定；没有数据库跨表版本 CHECK 或自动级联删除。页码从 1、位置从 0，偏移针对 NFC/LF 后页内字符，右端不包含。

模型/端点/维度和 pipeline_version 固定；同维不同模型不兼容。原文件名可相同，SHA-256 相同的有效文档拒绝重复；删除后可重新上传相同内容并生成新 UUID。失败重试保留旧 job，新建递增 version；没有更新原文的接口。

领取任务只锁 job；跨资源写操作统一锁 KB → document → job，避免删除/发布循环等待。每批网络前后续租并保存已知用量，全部向量以单个事务发布。删除同时设置墓碑、删分块、取消待执行任务；原文异步清理成功才写 file_removed_at。

迁移前先备份，构建 api/worker 镜像，显式 upgrade head，再启动两个服务。降级到 0002 会删除 P4 全部四张表而保留原文卷，只能用于可丢弃测试库；业务环境优先前向修复或经验证的备份恢复。独立 PostgreSQL 已执行空库升级、降级/重新升级、重复升级和 alembic check，真实 vector_dims/cosine 运算及约束测试通过。

## P5 问答迁移 20261112_0004

迁移前驱为 `20261110_0003`，新增一张 `rag_runs` 表。ORM 位于 `src/agenthub/rag/models.py`。

| 字段 | 数据库语义 |
| --- | --- |
| `message_id` | UUID 主键，外键指向 `messages.id`，ON DELETE RESTRICT。每个 RAG 助手消息一条记录。 |
| `knowledge_base_id` | 非空 UUID，外键指向 `knowledge_bases.id`，ON DELETE RESTRICT。P4 逻辑删除保留知识库墓碑。 |
| `embedding_model`、`request_id` | 非空字符串，分别为 200 和 64 字符。 |
| `outcome` | 可空 varchar(32)，CHECK 约束有效值为 `answered`、`insufficient_evidence`。生命周期沿用助手消息的 `status` 与 `error_code`。 |
| `citations` | 非空 JSONB，由 ORM 初始化为空数组。保存本轮实际引用及位置，不保存分块正文。分块删除后，历史引用位置仍可识别。 |
| `embedding_ms`、`embedding_tokens`、`search_ms`、`retrieved_count`、`context_count` | 可空整数，记录执行过的检索阶段与已知用量。生成用量和耗时沿用 `model_calls`。 |

主键自带索引。现有知识库、向量、消息表结构不变，无需重新入库。降级到 0003 会删除问答追踪与引用，通用 `messages` 中的历史消息仍在，只在可丢弃库演练。业务升级按 [部署说明](DEPLOYMENT.md) 执行。
