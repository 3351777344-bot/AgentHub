# AgentHub 数据库

最后更新：2026-11-10。当前版本：`20261110_0001`。首版为单组织；只有身份与审计表，尚无知识库、消息或 Agent 表。

## 模型与迁移入口

- ORM：[identity/models.py](../src/agenthub/identity/models.py)。公共 Base、引擎和会话：[db/session.py](../src/agenthub/db/session.py)。
- 迁移：[首个版本](../migrations/versions/20261110_0001_identity.py)、[迁移环境](../migrations/env.py)、[alembic.ini](../alembic.ini)。
- PostgreSQL UUID 主键由 Python `uuid4()` 生成；时间为带时区时间。数据库创建时间默认 `now()`；有效期判断使用数据库 `clock_timestamp()`。

## 已实现表

| 表 | 字段 | 约束与索引 |
| --- | --- | --- |
| roles | name varchar(16) | 主键；CHECK 仅允许 admin/member；迁移插入两个角色 |
| users | id UUID、username varchar(64)、password_hash varchar(256)、role_name varchar(16)、is_active boolean、created_at timestamptz | username 唯一且匹配小写 ASCII 名称；role_name 外键并建索引；is_active 默认 true |
| login_sessions | id UUID、user_id UUID、token_hash varchar(64)、created_at / expires_at / revoked_at timestamptz | user_id 外键并建索引；token_hash 唯一；expires_at 索引；CHECK expires_at > created_at；revoked_at 可空 |
| audit_events | id UUID、actor_id / target_user_id UUID、action varchar(64)、request_id varchar(64)、created_at timestamptz | actor/target 外键可空；created_at 索引；无密码、原始令牌或任意请求体字段 |
| alembic_version | version_num | Alembic 自己管理，不是业务 ORM 模型 |

除三个可空字段 `revoked_at`、`actor_id`、`target_user_id` 外，业务字段均非空。所有外键采用 `ON DELETE RESTRICT`，不提供用户硬删除 API；账号使用禁用，保留审计关联。UUID 的 Python 默认值不等于数据库服务端默认值，直接 SQL 写入需显式提供 UUID。

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

主机已有可达数据库时可用 `uv run --locked alembic upgrade head`。连接配置统一来自 Settings，不在 alembic.ini 存放密码；API 启动和 ready 都不会自动建表/迁移。vector 扩展仍由基础设施初始化，迁移不删除它。

新增版本：修改模型 → `uv run --locked alembic revision --autogenerate -m "描述"` → 人工检查生成 SQL 与存量数据兼容性 → 独立测试库验证 → `upgrade head`。已经应用的版本不原地修改，用新版本演进。

`downgrade base` 会删除全部四张身份业务表，**只在可丢弃测试库演练**；本次没有在现有开发库执行降级。真实数据回滚需备份恢复或前向修复，不能把提供 downgrade 函数当作无损回滚承诺。

## 本次迁移与验证记录

| 版本 | 变化 | 验证 |
| --- | --- | --- |
| 20261110_0001 | roles/users/login_sessions/audit_events，角色种子与约束索引 | 独立 PostgreSQL 空库升级、降级后升级、重复升级、Alembic check 与 ORM 一致性；本地 Docker 开发库执行 upgrade head |

独立测试使用 [compose.test.yaml](../compose.test.yaml)：单独项目、`agenthub_p2_test` 数据库、仅回环 55432、tmpfs、不挂载业务命名卷。测试在迁移/清理前断言 `current_database()`；显式启用后失败就报错，不用 SQLite 替代 PostgreSQL 约束与锁。

未实现：会话/审计保留清理任务、运行账号与迁移账号分离、数据库级审计防篡改、多租户隔离、备份恢复演练与业务性能基线。
