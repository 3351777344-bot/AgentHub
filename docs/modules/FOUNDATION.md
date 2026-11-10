# P1 基础运行模块与第一课

最后更新：2026-11-10。状态：P1 代码、本地 Python、真实容器依赖、停止/恢复及数据保留验收均完成；[证据](../acceptance/P1_2026-11-09.md)。P2 身份与业务会话已实现，见[第三课](IDENTITY.md)；AI业务模块仍未实现。

逐项解释全部 P1 技术与代码阅读顺序，见 [P1 全技术学习指南](../learning/P1_TECHNICAL_GUIDE.md)。本文保留模块入口、执行流程与两课练习。

## 职责与入口

本模块给全部业务提供 HTTP 入口、配置、生命周期、依赖状态、请求追踪和安全错误边界。当前只依赖 Python 库、环境变量及 PostgreSQL/Redis；不依赖知识库或 Agent，也不创建空业务模块。

| 文件 | 关键对象 | 职责 |
| --- | --- | --- |
| [main.py](../../src/agenthub/main.py) | `create_app()`、`lifespan()` | 组装应用、启动探测、退出清理 |
| [config.py](../../src/agenthub/core/config.py) | `Settings` | 加载并校验配置，保护敏感字段 |
| [middleware.py](../../src/agenthub/core/middleware.py) | `RequestContextMiddleware` | 请求 ID、上下文隔离、响应头、意外异常、请求日志 |
| [logging.py](../../src/agenthub/core/logging.py) | `JsonFormatter`、`request_id_context` | JSON 日志白名单、ContextVar |
| [errors.py](../../src/agenthub/core/errors.py) | `ErrorResponse`、`error_response()`、`register_error_handlers()` | HTTP/校验错误标准化 |
| [dependencies.py](../../src/agenthub/core/dependencies.py) | `ReadinessChecker`、`DependencyChecks` | 真实只读探针、超时、Redis 客户端清理 |
| [health.py](../../src/agenthub/api/health.py) | `liveness()`、`readiness()`、`get_readiness_checker()` | 健康 API 与最小依赖注入边界 |

## 核心代码如何执行、模块如何交互

1. Uvicorn 使用 `agenthub.main:create_app --factory` 调用工厂；`Settings()` 按初始化参数、环境变量、`.env`、默认值的优先级加载配置。端口或 URL 不合法就拒绝启动，不在请求时才发现。
2. 工厂配置日志、创建 FastAPI、注册中间件与路由。`src` 布局通过安装项目让 `agenthub` 可导入，`__version__` 读取安装包元数据；只复制源码而不安装不属于支持的运行方式。
3. lifespan 在服务开始接请求前创建 `DependencyChecks`，保存至 `app.state.dependencies` 并执行一次真实探测。P2 另外创建 SQLAlchemy 引擎/Session factory；退出时 dispose 业务引擎并调用 `aclose()` 关闭 Redis 池。数据库探针仍单独使用上下文管理器关闭连接，不复用业务 Session。
4. 请求进入中间件：校验或生成请求 ID，放到 `request.state` 和 `ContextVar`；路由返回时补 `X-Request-ID`，记录方法、路由模板、状态和耗时，最后恢复上下文。并发协程各有自己的 ContextVar，不使用全局字符串保存当前请求。
5. `/health/live` 直接返回进程状态；`/health/ready` 的 `Depends(get_readiness_checker)` 从应用状态取 checker。测试可以只替换这个接口，生产实例始终调用真实探针。
6. `DependencyChecks.check()` 用 `asyncio.gather()` 并发检查：PostgreSQL 读取 `pg_extension`，Redis 发送 PING。同步 psycopg 放入 AnyIO 工作线程，避免阻塞事件循环，也避免 Windows 异步驱动事件循环兼容问题。`asyncio.timeout()` 限制每个探针的等待时间。
7. ready 只有在全部探针成功时返回 200；失败返回带检查状态的统一 503。HTTPException 和输入校验由注册的处理器转换；意外异常在中间件生成 500，保留请求 ID。

依赖故障只让 ready 失败，API 和 live 继续响应，方便观察与恢复；这不表示故障期间业务请求已经具备成功条件。

## 核心技术与设计理由

- **FastAPI / ASGI**：路由表达 HTTP 契约，中间件包围通用请求流程；使用纯 ASGI 中间件保持上下文与响应发送边界清晰。
- **应用工厂**：`create_app(settings)` 允许测试注入独立配置；避免导入模块时就建立真实外部连接。也可以直接创建全局 app，适合简单演示，但隔离多组配置更困难。
- **Pydantic Settings**：把字符串环境变量变成有范围约束的字段。`SecretStr` 保护常规 repr/JSON；不是加密，调用 `get_secret_value()` 仍能读到真实值，因此禁止输出整个配置或驱动异常。
- **依赖注入**：健康路由只依赖 `ReadinessChecker.check()`；协议在这里用于隔离探针实现与路由测试，没有建立多余的通用框架。也可以在路由中直接建连接，但会混杂 HTTP、资源管理和探测逻辑。
- **异步与线程**：Redis 使用异步客户端，数据库同步探针在线程中执行。P2 业务 SQLAlchemy 使用同步 def 路由在线程池执行；一次性探针与业务数据访问层分开。
- **结构化日志与错误边界**：日志记录可筛选的字段；输入、URL 查询、原始异常不会自动序列化。客户端拿请求 ID 报错时，可以据此找对应事件。
- **环境隔离与锁文件**：`.venv` 隔离依赖，`uv.lock` 固定包版本和哈希；Docker 则隔离运行时与服务网络，命名卷让数据独立于容器生命周期。两者解决不同层面的问题。

另一种就绪策略是依赖故障时直接拒绝 API 启动；它能立即暴露启动失败，但不便在同一进程提供诊断。当前通过 ready 和容器健康状态表达可接流量条件；Compose 的健康状态本身不等于自动重启或负载均衡摘流。

## 当前限制、常见错误与面试方向

- 不要把 live=200 当作数据库或 RAG 可用，也不要用固定成功 JSON 替代 ready 探针。
- 不要在 async 路由中直接调用同步数据库操作；线程探针取消等待后，底层线程可能继续运行到驱动超时，不适合直接充当高频业务查询。P2 已增加业务池和 SQL/锁超时，实际吞吐仍待后续测量。
- 不要让日志回显未知路径、查询参数、请求体或异常文本。当前记录异常类型而不保留原始堆栈/消息，定位细节有限，后续需设计受控脱敏诊断；白名单不会自动保护未来开发者主动写进日志 message 的秘密。
- 不要开启 debug 或依赖默认错误页面处理服务端异常。已经开始发送的流式响应不能改成完整 JSON 500，后续 SSE 需要单独设计错误事件。
- 不要把 `.env` 打包或提交；不要认为修改初始化脚本会影响已有数据库卷。
- 面试可能让你解释：工厂函数怎样支持隔离测试；ASGI 中间件和异常处理器的边界；async 与线程的差别；ContextVar 为什么适用于请求上下文；存活/就绪检查怎样影响故障恢复。

测试代码可对照 [测试文档](../TESTING.md)。架构、接口与部署分别见 [架构](../ARCHITECTURE.md)、[API](../API_DESIGN.md)、[部署](../DEPLOYMENT.md)。

## 学习检验：先尝试，不附答案

理解问题：

1. 对照 `main.py` 的 `lifespan()`：为什么 Redis 客户端在这里创建并在 `finally` 中关闭，而不是在每次 ready 请求时创建？
2. 对照 `api/health.py`：数据库不可用时，live 和 ready 各应返回什么？如果二者共用同一个数据库探针，可能造成什么运维问题？
3. 对照 `middleware.py`：并发两个请求时，普通全局变量与 ContextVar 保存请求 ID 有什么区别？`reset(token)` 有什么作用？

简单代码修改任务：给 `LivenessResponse` 增加 `version` 字段，值来自 `agenthub.__version__`，检查并更新相关接口、真实 HTTP 冒烟和验收脚本断言及 API 文档，再执行 pytest 和 Ruff。请自己先尝试，不预先替你完成。

项目思考题：未来知识库依赖 PostgreSQL、Agent 还依赖模型服务，如果模型服务故障，整个 API 都应该 ready=503 吗？请列出你认为应继续可用的接口，并说明健康检查的依赖范围应如何设计。

学习状态：已提供代码讲解与练习，**待回答、待练习、待验证**。

## 第二课：Docker、真实依赖与持久化验收

本次验收入口为 [verify_p1.py](../../scripts/verify_p1.py)。它操作本项目本地开发栈，会短暂停止依赖、移除并重建项目容器；不会删除卷。具体运行结果在开发记录与验收报告中记录，脚本存在本身不代表验收通过。

### 执行流程与模块交互

1. Docker Desktop 的 WSL 2 后端提供 Linux 容器运行环境；Compose 创建 API、PostgreSQL 与 Redis，并通过服务名连接内部网络。
2. Dockerfile 安装 uv.lock 中的依赖与项目包，以非 root 用户启动应用。Compose 注入容器内连接地址；主机 Python 的 localhost 配置不适用于其他容器。
3. 脚本先要求真实 ready=200，读取 PostgreSQL、vector 和 Redis 版本，检查 API 用户与依赖端口，再通过容器网络运行真实集成测试。
4. 脚本创建带随机运行 ID 的 PostgreSQL 测试 schema/向量记录与 Redis 专属键。测试数据只用于验收，不是业务模型，不纳入 Alembic；结束后只清理本次创建的数据。
5. 分别 stop/start Redis 与 PostgreSQL，验证 live=200、ready=503→200，检查请求 ID 与日志脱敏，并核对 API 容器 ID 不变。
6. 执行不带删卷参数的 compose down/up，确认三个容器 ID 都变化，再读取同一批标记和向量距离；这才证明测试数据独立于旧容器保存。
7. finally 尝试恢复服务并清理自己的测试标记，将证据和清理状态写入忽略的 data/acceptance JSON 报告。失败的步骤不能记录为通过。

### 为什么这样设计、替代方式与限制

命名卷挂载 PostgreSQL 数据目录和 Redis AOF 目录，容器替换后再次挂载原卷；Redis AOF 让内存中的写入能够重放恢复。只重启 API 或检查容器仍在运行，不能验证这条数据保留链路。也可以使用宿主机 bind mount，但需要承担 Windows/Linux 路径、权限与文件系统性能差异；当前用命名卷减少这些环境差异。

本次 stop 是正常停机，验证的是依赖停止/启动与容器替换后的恢复，不证明断电、磁盘损坏或任意时间点恢复能力。命名卷也不是备份：P10 仍需建立独立备份与恢复演练。

常见错误：用 localhost 连接另一个容器；以为 depends_on 能持续监督和自动修复依赖；执行 down 时带删卷参数；以为初始化 SQL 每次重启都会执行；把 Redis 内存成功响应当作持久化成功；只看返回 200 而没有核对相同数据和容器 ID。

面试可能要求解释：镜像/容器/卷的区别，Compose 网络中的服务发现，依赖故障时存活与就绪的行为，以及 Redis AOF 与数据库备份的区别。

### 学习检验

1. 为什么脚本在 down/up 前后既检查容器 ID，又检查相同的标记数据？只做其中一个会漏掉什么？
2. 对照 compose.yaml，为什么 API 连接 postgres:5432，而 Windows 进程不能直接使用这个地址？
3. 为什么 Redis 配置 AOF 后仍需要独立备份？本次正常 stop 验收没有证明哪些故障场景？

代码练习：在 verify_p1.py 的报告中增加 Redis AOF 开关检查，实际读取 CONFIG GET appendonly，并只在值为 yes 时记录通过；保持错误退出行为，运行 Ruff 后再在本地开发栈验证。

项目思考题：未来 PostgreSQL 元数据与原始文档卷都要持久化，如果只恢复其中一个，会产生什么业务不一致？你会怎样确定一次备份的边界？

学习状态：讲解与练习已提供，**待用户回答和实践验证**。
