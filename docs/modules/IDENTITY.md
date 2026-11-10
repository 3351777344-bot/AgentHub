# P2 身份与持久化模块：实现与第三课

最后更新：2026-11-10。学习状态：已提供讲解，待用户回答与独立修改验证。

## 实现了什么，位于哪里

P2 为 P3 会话、P4 知识库、P7 Agent 提供真实身份、连接池、事务、迁移和基础权限。当前交付用户管理与可撤销登录，没有实现后续资源表或多租户。

| 文件 | 核心对象 | 职责 |
| --- | --- | --- |
| [db/session.py](../../src/agenthub/db/session.py) | Base、create_database_engine、get_session | 结构化连接 URL、连接池、每请求 Session、关闭回滚 |
| [identity/models.py](../../src/agenthub/identity/models.py) | Role/User/LoginSession/AuditEvent | ORM 映射、主外键、唯一/检查约束和索引 |
| [identity/security.py](../../src/agenthub/identity/security.py) | hash_password、verify_password、new_token、token_digest | Argon2id、随机令牌、SHA-256 摘要 |
| [identity/schemas.py](../../src/agenthub/identity/schemas.py) | 输入与输出模型 | 用户名规范化、密码边界、角色枚举、公共字段白名单 |
| [identity/dependencies.py](../../src/agenthub/identity/dependencies.py) | get_principal/get_admin | 从 Bearer 令牌加载身份，校验管理角色 |
| [identity/service.py](../../src/agenthub/identity/service.py) | bootstrap_admin/login/authenticate/update_user/change_password | 业务流程、行锁、失效策略、事务与审计 |
| [api/identity.py](../../src/agenthub/api/identity.py) | router | HTTP 参数、权限依赖、响应契约、分页 |
| [identity/cli.py](../../src/agenthub/identity/cli.py) | main | 本地首位管理员初始化；禁止已有身份时覆盖 |
| [迁移](../../migrations/versions/20261110_0001_identity.py) | upgrade/downgrade | 从空库建立结构；测试库可演练降级 |

配置和生命周期复用 P1：`main.lifespan` 创建 SQLAlchemy 引擎/Session factory，退出在线程中 dispose；探针仍独立管理。`core/errors.py` 处理业务错误与脱敏数据库 503，`core/middleware.py` 为业务响应加 no-store。

## 跟着真实代码执行一次

1. 执行 Alembic：`env.py` 从 Settings 创建引擎，`upgrade()` 建四张表、约束和角色种子。迁移是版本化结构变更，不是普通业务 Session，也不会自动运行在 API 启动中。
2. 初始化：CLI 隐藏读取密码，校验 `CreateUserInput`；`bootstrap_admin()` 锁 admin 角色行，检查 users 为空。`create_user()` 哈希密码、flush 用户、插入审计、commit。
3. 登录：`POST /api/v1/auth/login` → `LoginInput` 先规范化用户名 → `get_session()` → `service.login()` 锁用户行 → 验证 Argon2id → 生成随机令牌 → 保存摘要/到期时间和登录审计 → commit → 返回原始令牌一次。
4. 查询自己：`GET /auth/me` → HTTPBearer 提取头 → `get_principal()` → `authenticate()` 按摘要关联 users 与 login_sessions，检查未撤销、未过期且用户启用 → 返回 Principal → UserOutput 只返回公共字段。
5. 管理用户：`AdminPrincipal` 检查角色，写操作再锁 admin 角色行并重新验证操作者；修改目标用户时锁该行。角色/状态改变撤销目标全部会话，并和审计一起提交。
6. 退出/改密：退出更新当前会话 revoked_at；改密需旧密码正确，锁用户、写新哈希并撤销全部会话。之后相同令牌再次请求得到 401。
7. 异常：审计或 SQL 失败时不返回成功；关闭 Session 回滚未提交的修改，错误处理器返回统一 JSON，请求日志只保留路由、状态、请求 ID 等白名单字段。

模块交互是 API → 身份依赖/业务服务 → SQLAlchemy → psycopg → PostgreSQL。Redis 当前不参与认证，数据库是身份与撤销状态的事实来源。

## 核心技术、设计理由与替代方式

**Session 和事务。** ORM 的 Session 管理本次工作中的对象与事务，不是“用户登录会话”；`LoginSession` 才是业务登录记录。`flush()` 发 SQL 取得约束结果和主键，其他事务尚不能看到已提交结果；`commit()` 才完成事务。连接池可以共享，但 Session 不能在并发请求间共享。显式提交让接口成功具有明确含义。

**认证与授权。** `authenticate()` 回答你是谁、凭据还有效吗；`require_admin()` 和 `/users/{id}` 的归属判断回答你能做什么。知道某个 UUID 不代表能读取它。后续资源列表必须在 SQL/检索条件中约束所有者，不能读出所有数据再过滤展示。

**密码与随机令牌采用不同哈希。** 密码可被人猜测，Argon2id 故意增加计算和内存成本，随机盐让相同密码产生不同结果。令牌由 32 随机字节生成，使用快速 SHA-256 摘要便于索引查询；不能把这个方案反过来用 SHA-256 存用户密码。SecretStr 仅防意外打印，不提供密码加密。

**有状态令牌。** 数据库会话使退出、禁用、改密后后续请求立即按新状态判断，并在 API 进程重启后保留。代价是每次认证查库。JWT 可减少部分读取，但即时撤销通常仍需黑名单或版本校验；签名只证明未被篡改，不代表用户仍有权限。Cookie 是另一种令牌传输方式，采用时还需设计 CSRF 与浏览器策略，当前只用显式 Bearer 头。

**并发与行锁。** 只检查“还有管理员”再更新会产生竞态。`lock_admin_operation()` 用已存在的 admin 角色行作为共同锁点，串行化低频管理写入；获取锁后重新检查操作者，而不是相信等待前读到的角色。也可用 advisory lock 或更高事务隔离级别并重试，当前行锁易解释且能真实测试。它不适合作为全平台高频业务的全局锁。

**同步路由。** 当前使用同步 psycopg/SQLAlchemy，`def` 路由由 FastAPI 在线程池运行，避免在 async 路由直接阻塞事件循环，也沿用 Windows 已验证驱动。异步 SQLAlchemy 是替代方案，但需要重新验证驱动、事务与事件循环，不因使用 async 就自动获得更高吞吐。

## 常见错误与面试考察

| 常见错误 | 本项目的防护 / 可考察的问题 |
| --- | --- |
| 只改 ORM 模型，不生成迁移 | Alembic check 对比结构；面试可能要求设计存量字段迁移，而非简单 create_all |
| 把 flush 当作 commit，响应发出后才提交 | 服务显式提交，审计失败回滚测试；追问事务边界和异常后的 Session 状态 |
| 明文密码、快速密码哈希、日志回显 token | Argon2id/摘要/输出白名单；追问盐、慢哈希与令牌熵的区别 |
| 只校验“已登录”，忽略资源归属 | member 只能读取自己的用户记录；追问 IDOR、SQL 过滤与后台任务权限 |
| JWT 包含 admin 就一直信任 | 每请求读当前状态，角色改变撤销会话；追问撤销机制与在途请求边界 |
| 管理员数量检查与更新分开执行 | 共同锁点和锁后重验证；追问两个事务交错顺序、死锁和锁超时 |
| 用 SQLite 测试替代 PostgreSQL 锁/约束 | 独立真实 PostgreSQL；追问测试证据能证明什么、哪些还没测 |

## 验证、当前限制和后续依赖

离线密码/schema/API 拒绝测试与独立真实数据库测试分开，后者验证迁移、持久化、过期/撤销、所有权、审计回滚和并发管理。真实 TCP 用例启动并停止一个 Uvicorn 进程，再以新进程读取原令牌。测试统计与执行边界见 [P2 验收记录](../acceptance/P2_2026-11-10.md)。

当前没有共享资源模型、前端、密码找回、登录限流、会话清理、MFA/SSO、多租户或审计防篡改。后续 P3/P4 使用 `CurrentPrincipal`，在每个资源查询和后台任务中落实实际归属；不能仅复用登录依赖就宣称资源权限已经完成。完整边界见 [SECURITY](../SECURITY.md)。

## 先自己尝试的学习检验

理解问题：

1. `create_user()` 中 flush 已成功，随后 audit 报错，为什么数据库里仍应没有新用户？如果在 dependency 的 yield 之后才 commit，会有什么问题？
2. 为什么数据库只保存 token 的 SHA-256 摘要，却不能也用 SHA-256 保存用户密码？退出后旧 token 的摘要还存在，为何认证仍失败？
3. 两名管理员同时禁用对方时，`lock_admin_operation()` 的角色行锁和锁后重新验证分别解决什么问题？删掉其中一个会发生什么？

代码修改任务：在 `tests/test_identity_integration.py` 新增一个越权测试：创建 member_a、member_b，用 member_a 的令牌读取 member_b，断言 403，并检查响应中没有 member_b 的用户名。先自己写，再运行独立数据库测试。

项目思考题：未来加入组织级权限后，除了 users 增加组织字段，会话、知识库检索、Redis 缓存、worker 任务和 LangGraph 恢复入口分别需要携带、验证什么？

当前未收到回答或独立代码修改，不标记已掌握。无需现在一次回答全部问题，可以先跟踪 `login()` 与 `authenticate()` 的执行。
