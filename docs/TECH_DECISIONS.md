# AgentHub 技术决策

最后更新：2026-11-10。

## ADR-017 P5 精确检索与会话引用

2026-11-12。P4 已有同一模型空间的 pgvector 分块。P5 选择在原库使用带所有权、状态和版本约束的精确余弦 Top-K，配置最低分阈值。现有小样本可直接核对距离、排名与证据，便于为后续容量与召回比较建立基线。数据量增大时，精确查询会增加数据库负载。HNSW、混合搜索和重排留待 P8 对比质量、延迟和成本。 [pgvector 精确与近似搜索](https://github.com/pgvector/pgvector#indexing)

问答复用会话租约，通过一张与助手消息一对一的表记录引用位置和检索观测数据。文档删除时，引用历史保留位置元数据并实时标为不可用。保存原文快照会让历史永久持有额外正文，单独的引用外键则会与分块物理删除相冲突。结构化输出可在生成模型能力验证后评估。现阶段内联标签只校验引用身份；证据与答案的语义一致性由样本评测检验。

## ADR-016：P4 持久任务、Redis 唤醒与原子向量发布

日期：2026-11-10。状态：已实现并验证。背景：上传涉及文件、数据库与队列双写，模型调用较慢且超时后是否计费不确定；删除/重试不能让迟到任务复活文档。

选择：复用已锁定 redis-py 的有界唤醒列表，不新增通用队列框架。PostgreSQL ingestion_jobs 是持久队列，SKIP LOCKED 领取，租约记录执行期，过期明确失败，用户显式重试。每文档版本任务唯一；固定模型名/北京端点/1024维/分块处理版本；全部向量有界收集，单事务发布。KB→document→job锁顺序、删除墓碑及版本/租约检查防止迟到发布。原文件先落 UUID 文件，提交不确定时保留，后续清理超过一小时无引用的文件。

备选：RQ/Celery/Dramatiq；Redis Streams可靠消息；纯PostgreSQL定时轮询；每批向量暂存后切换有效版本。当前单一低吞吐任务复用 redis-py 可减少一套框架状态、依赖和恢复规则，数据库事实不依赖 Redis 投递成功。仍保留 Redis 及时唤醒，以免忙轮询。代价是自有领取/租约/清理逻辑需要维护，无优先级/定时任务/管理面板，不能承诺云推理 exactly-once。规模或任务类型增加时重评框架，保留业务事实与幂等边界。

其他取舍：pypdf6.21.4文本提取，解析子进程30秒、容器512MiB限制，不实现OCR或完整恶意文档沙箱；pgvector0.4.2引入numpy2.5.4作为向量客户端依赖；multipart0.0.33支持文件上传，并在解析前限制实际body。字符分块容易验证偏移，但会切断语义，P5再用样本评估。只支持vector(1024)，修改维度需要新schema迁移和重建，不能仅改环境变量。

优点：提交与通知部分失败可恢复、网络不占DB事务、失败不暴露半份向量、删除可立即阻止访问。代价：重试重算全部分块、内存与单文档上限、墓碑保留、需要worker在线才能回收/物理清理。没有改动既有身份/聊天表或API契约。

依据：[Redis BLPOP](https://redis.io/docs/latest/commands/blpop/)、[pgvector Python/SQLAlchemy](https://github.com/pgvector/pgvector-python)、[pypdf extraction](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)。流程与限制见 modules/KNOWLEDGE.md、JOBS.md，验证见 P4 验收。


本文件区分用户确认的产品边界、当前采用的设计方案和待决选型。设计已记录不代表代码已经落地；具体依赖版本在实施时核验并锁定。

## ADR-001：本地 Docker、单组织作为首版范围

- 日期：2026-11-04。
- 状态：用户已确认；部署与业务实现尚未开始。
- 背景：当前无业务代码，需要把完整平台拆成可验证、可学习的阶段。
- 方案：首版使用 Docker/Compose 本地部署，单组织内提供多用户和基础角色；后续逐步扩展企业能力。
- 备选：从首版实现多租户 SaaS；优先做无工程约束的演示原型。
- 原因：减少早期基础设施与隔离复杂度，同时保留真实权限、持久化、评测和运维交付。
- 优点：容易复现、排错与教学；完整业务链路可以先得到验证。
- 缺点：首版不能作为多租户 SaaS 直接上线；本地机器资源会限制并发和模型部署。
- 后续影响：多租户需要正式的数据隔离与迁移设计；完全离线推理仍需单独确认模型与硬件。

## ADR-002：模块化单体，API 与 worker 分进程

- 日期：2026-11-04。
- 状态：采用为开发基线；P1 已实现 API 基础运行层，worker 和业务模块尚未实现。
- 背景：平台涉及多个模块，但尚无证明需要微服务的负载或团队边界。
- 方案：同一代码库按业务模块组织，FastAPI 提供接口，独立 worker 处理耗时任务，共享服务逻辑与数据库约定。
- 备选：所有处理都放在 API 请求内；从第一天拆分微服务。
- 原因：兼顾结构清晰、调试成本和任务隔离，不提前承担跨服务事务与部署复杂度。
- 优点：便于端到端理解；入库与评测可独立执行；减少业务逻辑重复。
- 缺点：仍需约束模块依赖；共享数据库和 worker 部署需要协调升级。
- 后续影响：P4 前选择队列库并验证投递/重试语义；P7 明确长运行的执行位置。
- 依据：[FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) 对重型后台任务的说明。独立 worker 是本项目的设计选择，而不是 FastAPI 强制要求。

## ADR-003：沿用 PostgreSQL + pgvector，先测量再优化

- 日期：2026-11-04。
- 状态：沿用 AGENTS.md 指定技术栈；P1 扩展初始化、真实数据库/向量读写及持久化已验收，业务模型/索引未实现。
- 背景：业务元数据与知识库向量需要权限关联、一致性管理和备份。
- 方案：关系数据和向量均使用 PostgreSQL + pgvector；SQLAlchemy 管理业务访问，Alembic 管理业务迁移。先建立精确检索基线，再根据评测决定近似索引。
- 备选：独立向量数据库；无数据库的内存向量索引；直接为所有向量建立近似索引。
- 原因：遵循现有技术栈，降低首版本地部署负担，优先确保权限和检索质量可验证。
- 优点：业务数据可关联；备份与运维对象较少；精确检索便于建立质量参照。
- 缺点：数据库同时承担业务与检索负载；大规模索引需要额外内存与调优。
- 后续影响：固定 Embedding 模型/维度与索引策略；模型改变需要受控重建，不能悄悄混用旧向量。
- 依据：[pgvector 官方说明](https://github.com/pgvector/pgvector) 中的精确检索与近似索引取舍。索引选型必须在本项目数据上验证。

## ADR-004：LangGraph 管理执行状态，平台管理业务与授权

- 日期：2026-11-04。
- 状态：目标设计，P7 实施。
- 背景：Agent 需要循环、分支、人工审批和故障恢复，业务系统也需要运行追踪和权限控制。
- 方案：使用 LangGraph 组织节点和状态，使用持久化 checkpointer；平台另外维护运行、步骤、审批和工具执行审计，所有恢复请求验证用户归属。
- 备选：手写循环状态机；只保存聊天消息；只依赖内存检查点。
- 原因：符合指定技术栈，复用图执行机制，同时保持业务记录和执行快照的职责清楚。
- 优点：执行路径可解释，可在明确的状态边界恢复。
- 缺点：要管理业务记录与图状态的关联、版本变化及框架自有表的升级。
- 后续影响：P7 需验证副作用幂等、审批内容绑定和检查点保留；不能把恢复等同于外部工具恰好执行一次。
- 依据：[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)。具体 checkpointer 包及初始化方式在实施时核对。

## ADR-005：工具和 MCP 经统一的授权与执行入口

- 日期：2026-11-04。
- 状态：目标设计，P6—P7 实施。
- 背景：模型提出调用请求，并不代表模型拥有执行权限；MCP 服务可能接触文件、网络和外部凭据。
- 方案：统一进行参数验证、权限/预算检查、超时与审计；管理员管理受信任服务，本地执行做权限收敛，远程调用做地址与适用认证校验；高副作用操作经过审批。
- 备选：模型可任意调用全部发现的工具；直接执行模型生成的代码或 Shell。
- 原因：可追踪、可限制，避免把协议连通性误认为安全授权。
- 优点：内置工具与 MCP 共用控制边界；故障容易关联具体运行。
- 缺点：需要额外设计服务配置、凭据边界和审批恢复逻辑，不能承诺任意 MCP 即插即用。
- 后续影响：MCP SDK 与协议版本需锁定；服务或工具定义变化需重新检查；不可信本地服务不能只靠应用层白名单隔离。
- 依据：[MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)。

## 待决事项

| 事项 | 决策截止阶段 | 判断依据 |
| --- | --- | --- |
| 容器发布产物不可变性与更新策略 | P10 发布 | P1 实际构建与运行已验收并记录摘要，配置尚未锁 digest |
| 生成模型、Embedding、供应商、是否离线 | P3 | 可用凭据、工具调用/流式能力、数据要求、质量、资源和预算 |
| Redis 任务队列库与可靠投递方案 | P4 | worker 兼容、超时、重试、幂等、故障恢复与维护成本 |
| 解析与分块库 | P4 | TXT/Markdown/文本 PDF 样本表现、许可证、依赖体积 |
| 向量维度与索引 | P4 固定维度；P5 建立基线后选索引 | 模型输出、数据量、权限过滤、召回与延迟 |
| MCP SDK、协议与认证实现 | P6 | 官方规范、服务兼容、受控真实集成验证 |
| LangGraph/checkpointer 版本与状态升级策略 | P7 | 持久化、恢复测试及对既有运行的兼容性 |
| 前端方案 | P9 | 管理表单、流式聊天、运行追踪、维护与学习成本 |
| 指标/追踪组件与性能/恢复门槛 | P10；基础日志自 P1 开始 | 指定环境、实际负载和故障演练 |

做出以上决定时补充新的决策记录，不在没有验证的情况下把待决事项写成已采用配置。

## ADR-006：Python 3.13、uv 锁定与 src 包布局

- 日期：2026-11-09。
- 状态：已采用并在 Windows/Python 和真实 Linux 容器验证，P1 验收通过。
- 背景：P1 需要可运行、可复现、便于测试隔离的工程底座；当前可用 Python 3.13.12 和 uv 0.11.27。
- 方案：支持 Python `>=3.13,<3.14`，`.python-version` 固定本机版本；`src/agenthub` 可安装包，Hatchling 构建；pyproject 声明依赖范围，uv.lock 锁定具体版本/哈希，日常 `uv sync/run --locked`。
- 实测核心版本：FastAPI 0.162.2、Starlette 1.10.0、Pydantic 2.14.3、pydantic-settings 2.16.2、psycopg 3.3.8、redis-py 8.1.0、Uvicorn 0.54.0。开发工具实际版本见锁文件；Starlette 新版 TestClient 使用 HTTPX2 2.16.0。
- 容器配置：Python 3.13.12-slim、uv 0.11.27、pgvector 0.8.7/PostgreSQL 17 bookworm、Redis 7.4.12 bookworm。已实际 pull/build 并验证 PostgreSQL 17.11、vector 0.8.7、Redis 7.4.12；显式标签未锁 digest，本次摘要见验收记录。
- 备选：requirements + pip/venv、Poetry、根目录 app 包、多 Python 版本支持。
- 原因：复用现有环境，减少工具与兼容矩阵；src 布局要求安装后导入，避免工作目录意外掩盖打包问题。SQLAlchemy/Alembic/LangGraph 等按阶段引入。
- 优点：开发、质量检查和打包入口一致；改动依赖时可以审查锁文件差异。
- 缺点：暂不支持 Python 3.9 等其他版本；需要 uv；镜像标签仍可变化。
- 后续影响：升级 Python 需同步运行约束、锁文件、Dockerfile 与验证矩阵；P10 再固定发布产物并建设 CI。
- 依据：[uv Docker 指南](https://docs.astral.sh/uv/guides/integration/docker/)、[Starlette TestClient 官方源码文档](https://github.com/Kludex/starlette/blob/main/docs/testclient.md)、[pgvector 官方镜像标签](https://github.com/pgvector/pgvector#docker)、[Redis 官方镜像](https://hub.docker.com/_/redis)。实际运行证据见 [P1 验收](acceptance/P1_2026-11-09.md)。

## ADR-007：真实依赖探针与可诊断的启动策略

- 日期：2026-11-09。
- 状态：已实现；本地测试、真实服务成功、依赖停止/恢复和无 API 重启恢复均已验收。
- 背景：进程存活不等于数据库可用；依赖故障时仍需查看 API 状态，且不得用固定成功响应掩盖问题。
- 方案：live 不访问依赖；ready 并发查询 PostgreSQL 并确认已安装 vector，同时执行 Redis PING，各有超时。lifespan 执行启动探测并管理资源；依赖故障仅报告状态，允许 API 启动。
- 备选：依赖故障直接终止启动、仅检查 TCP 端口、一个接口同时表示存活和就绪。
- 原因：SQL/PING 比端口连通更接近可用性；分开状态便于诊断和恢复。同步 psycopg 使用 AnyIO 线程，避免 Windows 异步事件循环差异，P2 再引入业务会话和连接池。
- 优点：故障恢复后无需重启 API 就能重新 ready；只读检查不修改数据库；错误和日志不输出驱动异常文本。
- 缺点：每次检查新建 PostgreSQL 连接；取消等待不能立刻停止底层线程，仍依赖驱动连接与 SQL 超时；日志只保存异常类型，诊断细节有限。
- 后续影响：P1 真实 Docker 停止/恢复演练已完成；业务探针频率、连接池与各依赖的就绪范围随 P2—P7 再评估。当前无流式接口，后续 SSE 需另行设计响应开始后的错误事件。
- 依据：[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)、[FastAPI 异常处理](https://fastapi.tiangolo.com/tutorial/handling-errors/)、[Redis 异步资源清理](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)、[Compose 启动顺序](https://docs.docker.com/compose/how-tos/startup-order/)。

## ADR-008：Windows 用户级 Docker / WSL 2 与本地验收边界

- 日期：2026-11-09。状态：已采用并完成 P1 验收。
- 背景：本机已有 WSL 2 和虚拟化，但缺少 Docker；C 盘余量少于 E 盘，需要可维护的本地 Linux 容器环境。
- 方案：官方 Docker Desktop 4.98.0 当前用户安装，WSL 2 后端，数据位于 E:\DockerData；保留已有代理与凭据配置。通过实际 SQL/vector、Redis 读写、依赖 stop/start 与保留卷的容器重建验证，而不是只检查进程或 schema。
- 备选：在独立 Linux WSL 发行版手动安装 Engine/Compose、Hyper-V 后端、仅交付未执行的 Docker 配置。
- 原因：当前 Windows 家庭版与已有 WSL 环境适合 WSL 2；用户级安装无需提权或变更 Windows 容器组件，官方工具统一管理引擎与 Compose。
- 优点：实际可运行、可复验；数据位置清晰，验收脚本只清理自己创建的标记。
- 缺点：依赖 Docker Desktop 与本机网络；许可证适用范围应按实际使用主体确认。正常停止验收不代表断电、磁盘损坏或独立备份恢复通过。
- 后续影响：新终端加载更新后的 PATH；旧会话需补 CLI/凭据助手目录。P10 继续做备份、不可变产物、安全与容量治理，不能用命名卷替代备份。
- 依据：[Docker 官方 Windows 安装](https://docs.docker.com/desktop/setup/install/windows-install/)、[本项目真实验收记录](acceptance/P1_2026-11-09.md)。

## ADR-009：Argon2id 与数据库可撤销 Bearer 会话

- 日期：2026-11-10。状态：P2 已实现，离线与真实 PostgreSQL 验证通过。
- 背景：需要多用户登录、退出、禁用与改密后的撤销，同时保持本地单组织实现清晰。
- 方案：argon2-cffi 的 Argon2id（64 MiB、3次、并行度4）；随机32字节 Bearer 令牌，数据库保存 SHA-256 摘要、用户、绝对到期时间和撤销时间，每请求查询当前身份。
- 备选：自包含 JWT；Cookie 会话；Redis 作为唯一登录存储；自行实现密码算法。
- 选择原因：业务已经依赖 PostgreSQL，数据库会话直接满足撤销/重启持久化；避免为首版引入签名密钥轮换和 JWT 撤销系统。Cookie 是传输方案，留待前端与 CSRF 一并设计。
- 优点：原始凭据不入库；状态变更下一次认证生效；审计与会话可共享事务；测试可明确控制过期与撤销。
- 缺点：每次认证查库；Argon2 占用线程和内存；当前缺少限流、清理与会话数量上限；数据库不可用时认证不可用。
- 后续影响：P9 明确浏览器凭据保存，P10 完善限流/TLS/保留策略；后台任务和外部工具不能只相信创建时的角色快照。
- 依据：[OWASP 密码存储](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)、[OWASP 会话管理](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)、[argon2-cffi API](https://argon2-cffi.readthedocs.io/en/stable/api.html)。本次复核后锁定 argon2-cffi 25.1.0。

## ADR-010：同步 SQLAlchemy、显式事务与管理员行锁

- 日期：2026-11-10。状态：已实现，真实 PostgreSQL 迁移/回滚/并发测试通过。
- 背景：P1 已验证同步 psycopg 跨 Windows/Linux 使用；P2 需要连接池、业务事务、迁移和不会丢失全部管理员的并发边界。
- 方案：SQLAlchemy 2.0.57 + Alembic 1.20.1，每请求 Session，服务在成功响应前 commit；事务内同时写管理变更与审计。管理员写入/初始化统一锁 roles.admin 行，锁后重验证操作者；禁止管理员更改自己的角色/状态。
- 备选：异步 SQLAlchemy；原生 SQL；自动 create_all；advisory lock；SERIALIZABLE 加重试；只计数管理员不加锁。
- 原因：同步 def API 由 FastAPI 线程池运行，沿用已验证驱动；迁移与运行分开；低频管理写入使用现成角色行作为明确锁点，容易教学与验证。
- 优点：事务结果可解释；审计失败可回滚业务；避免并发管理员相互禁用；测试使用真实 PostgreSQL 而非替代数据库。
- 缺点：管理写入串行；需要稳定锁顺序；同步线程/连接池存在容量上限；API ready 只检查基础依赖，不自动确认迁移版本。
- 后续影响：后续资源权限不能机械复用全局管理锁；新增模型必须交迁移，存量数据升级需另行设计与演练；部署先显式迁移再启动业务。
- 依据：[SQLAlchemy Session Basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)、[Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)、[FastAPI 同步/异步](https://fastapi.tiangolo.com/async/)。本次已对照官方资料核验 API 并锁定版本。

## ADR-011：兼容 Chat Completions 与短事务会话租约

日期：2026-11-10。状态：基础实现与测试通过，真实供应商待验收。

背景：P3 需要可被 RAG/Agent 复用的模型与会话基础，用户选择 OpenAI 兼容接口但尚未指定服务/模型。选择小型文本适配器、现有 httpx2 异步连接池、短事务 + PostgreSQL 会话行锁/租约，输出错误不泄露上游正文，重试为 0。替代方案是供应商 SDK、统一多模型框架、长事务锁或立即引入队列。

优点：依赖增量小、供应商与持久化分离、网络不占 DB 连接、多进程共享并发边界。缺点：需要手动验证服务协议差异；纯文本能力；无持久化事件续流、精确 token 预算或费用限额。后续工具调用不能沿用文本解析，P7 长任务需独立运行生命周期。

依据：[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Streaming events](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[HTTPX async](https://www.python-httpx.org/async/)。上游官方协议用于设计，实际已锁定 httpx2 2.16.0 API 通过本地测试验证。

## ADR-012：DeepSeek 文本模式与验收数据边界

日期：2026-11-10。用户选择 https://api.deepseek.com、deepseek-flash、10 元验收预算；当时 Embedding 模型/维度待定，后续选择见 ADR-013/014。仅公开/脱敏测试文本可发送，不含隐私、真实凭据、商业机密或不适合上传的原始文件。

基础聊天使用配置化 thinking.type=disabled；其他服务默认省略扩展，避免破坏通用兼容。备选是沿用供应商默认思考或增加完整推理内容管理；当前仅保存最终文本，关闭思考可降低小额度验收的空回答风险，代价是暂不暴露推理模式功能。无需数据库迁移，客户端 API 不变。

依据：[官方接入](https://api-docs.deepseek.com/quick_start/pricing)、[官方参数](https://api-docs.deepseek.com/api/create-chat-completion/)。真实普通/流式适配及认证 API 均已通过。

## ADR-013：Embedding 服务与 1024 维选择

日期：2026-11-10。状态：用户已采纳云端方案并授权独立 10 元、公开/脱敏数据验收；配置/适配器已实现，实际 URL/密钥由用户稍后填写，未调用真实服务。

建议公开/脱敏知识库使用百炼北京 qwen3.7-text-embedding、1024 维，Flash 和 v4 做对照；敏感数据优先本地 Qwen3-Embedding-0.6B、1024 维。理由是首版云端接入减少 GPU 运维，同时为已有本机 8 GB 显存保留本地方案；相同维度不代表可混用。同价新模型的官方优势需经项目召回样本验证，不宣称已经提升效果。代价是新增供应商与数据出域边界；本地方案则增加推理维护。国际站 ZDR 不自动适用于北京调用。

具体价格、来源、重建与迁移原则见 [选型分析](modules/EMBEDDING_SELECTION.md)。不修改现有 schema；P3-01 的选型已确定，实际认证与真实接入仍待验收，不将整个阶段记为完成。

## ADR-014：独立 Embedding 适配器与整批输出校验

日期：2026-11-10。复用 httpx2，与生成模型按请求隔离凭据；只允许北京业务空间 HTTPS 兼容端点。采用一个有界同步批次、0 次自动重试，完整检查模型/维度/数量/索引/有限非零数值并 L2 归一化，才交给未来 P4 入库。替代方案是原生 DashScope API/SDK、自动拆分或在数据库报错后处理；前者增加本次范围，后两者使计费与部分结果难以追踪。兼容接口当前不能使用官方原生 text_type/instruct，明确记录这一检索优化限制。没有新增依赖、数据库和公开 API。详见 [接入模块](modules/EMBEDDINGS.md)。

## ADR-015：Embedding 切换 Flash 并完成 P3

日期：2026-11-10。状态：用户明确授权，真实验收通过，更新 ADR-013 的最终模型选择。用户截图中的免费额度属于 qwen3.7-text-embedding-flash，原完整版实际请求被 insufficient_quota 拒绝；当前最终模型为百炼北京 Flash、1024 维、L2 归一化。替代方案是为完整版恢复付费额度，当前为接入学习与小规模公开数据验收优先使用可用的 Flash。官方标准价 0.125 元/百万输入 token，验收仅两次共 115 token，免费抵扣以账号账单为准。

没有既有知识库向量，无需数据库迁移或数据重建；未来换模型即使维度相同也必须重新向量化。已同步脚本模型守卫、价格快照、配置默认值和协议测试；未新增依赖或 API。官方依据：[Flash 模型](https://help.aliyun.com/en/model-studio/qwen3-7-text-embedding-flash)。
