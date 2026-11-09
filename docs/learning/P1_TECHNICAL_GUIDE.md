# P1 全技术学习指南：从 Python 代码到可恢复的容器服务

日期：2026-11-09。依据当前 P1 实际代码编写；学习状态为“已提供讲解，待回答和独立练习验证”。本指南不新增业务功能，也不将后续阶段内容记为已实现。

## 1. 先把系统串起来

P1 建立后续知识库、RAG 和 Agent 共用的运行基础。它包含四个层面：

| 层面 | 本项目中的内容 | 解决的问题 |
| --- | --- | --- |
| Python 工程 | src 包、uv、锁文件、构建、类型检查 | 怎样安装、导入、复现和检查代码 |
| HTTP 服务 | Uvicorn、FastAPI、配置、生命周期、路由、日志、错误 | 请求怎样进入系统并得到可追踪的响应 |
| 外部依赖 | PostgreSQL、pgvector、Redis、客户端、超时 | 怎样判断系统真正具备依赖条件 |
| 部署与验证 | WSL 2、Docker、Compose、网络、卷、测试与 Git | 怎样启动、排错、恢复和保留数据 |

一次 ready 请求的业务链路是：客户端 → Uvicorn → FastAPI/中间件 → health 路由 → DependencyChecks → PostgreSQL 与 Redis → 响应与日志。主入口是 [main.py](../../src/agenthub/main.py)，依赖检查在 [dependencies.py](../../src/agenthub/core/dependencies.py)。

P1 尚未实现 SQLAlchemy、Alembic、登录授权、Embedding、RAG、MCP、LangGraph、业务 worker 或前端。安装 vector 扩展为以后提供数据库能力，不会自动生成知识向量或回答问题。

## 2. Python 项目为什么要这样组织

### 2.1 解释器、第三方库与虚拟环境

Python 解释器负责执行代码；FastAPI 等第三方包提供能力；.venv 为本项目保存独立的一组包。项目 A 更新依赖时，不应悄悄改变项目 B 的环境。

本项目支持 Python 3.13，.python-version 记录本次环境 3.13.12；pyproject 的 requires-python 约束支持范围。它们各自承担“选择版本”和“声明兼容范围”的职责，不能只改一个文件就认为升级完成。

`uv sync --locked` 创建/同步环境；`uv run --locked pytest` 使用项目环境运行测试，不要求先激活 .venv。--locked 要求锁文件与项目声明一致，不能在执行检查时悄悄重新解析依赖。详见 [uv 官方说明](https://docs.astral.sh/uv/concepts/projects/sync/)。

### 2.2 pyproject.toml 与 uv.lock

[pyproject.toml](../../pyproject.toml) 声明项目名称、版本、Python 范围、直接依赖、开发依赖和工具配置；[uv.lock](../../uv.lock) 记录解析出的具体依赖版本、关联关系和下载哈希。

例如 fastapi>=0.115,<1 是允许范围，锁文件中当前采用的是具体版本。开发机和容器都按锁文件同步，减少“我这里能运行”的环境差异。锁文件需要提交，.venv 不提交。

运行依赖包括 FastAPI、Uvicorn、Pydantic/Settings、AnyIO、psycopg 和 redis-py；开发组包含 pytest、HTTPX2、Ruff、mypy。Docker 使用 --no-dev，减少运行镜像中的非必要工具。依赖还会引入它们需要的间接包，不应仅凭包总数判断项目直接引入了多少能力。

锁文件没有固定整个操作系统、容器标签或所有未来构建条件；升级仍需审查和验证。

### 2.3 src 布局、包与构建

业务代码位于 src/agenthub，导入名是 agenthub。__init__.py 建立常规包边界。安装项目后，可以从其他工作目录导入，避免只在仓库根目录偶然找到源码。

开发安装通常可编辑，源码修改可直接反映到导入；容器使用 --no-editable 安装构建产物。Hatchling 是 pyproject 指定的构建后端，uv build 调用它生成 wheel 与 sdist：前者是安装分发包，后者是源码分发包。它们不包含完整 Python 解释器，也不等于 Docker 镜像。

[包入口](../../src/agenthub/__init__.py) 使用 importlib.metadata.version("agenthub") 读取安装元数据。因此本项目的规范流程是先安装，再运行；只复制源码、没有安装包，可能读不到版本。

psycopg[binary] 选择预编译驱动组件，减少本机编译需求；它是数据库客户端，不会替你启动 PostgreSQL 服务。

### 2.4 忽略规则

.gitignore 控制哪些未跟踪文件不进入 Git；.dockerignore 控制哪些文件不进入 Docker 构建上下文。二者应分别配置，Git 忽略不意味着 Docker 自动忽略。

本项目 .env、虚拟环境、缓存、构建产物、下载包和原始验收报告不提交；.env.example 与 uv.lock 提交。已经跟踪的文件不会因为后来写进 .gitignore 就自动从 Git 历史消失。

## 3. HTTP、Uvicorn、ASGI、FastAPI 各做什么

HTTP 请求包含方法、路径、头和可选请求体；响应包含状态码、头和响应体。JSON 是当前 API 使用的数据表达格式。

以 GET /health/ready 为例：GET 是方法，/health/ready 是路径，X-Request-ID 是可选头，返回的 checks 是 JSON 数据。

- **Uvicorn** 监听端口并处理网络协议，把请求交给 Python 应用。
- **ASGI** 约定服务器和应用如何交换异步事件，支持 HTTP、WebSocket 与生命周期事件。
- **Starlette** 提供 FastAPI 使用的请求/响应、中间件和测试基础。
- **FastAPI** 负责路由、依赖注入、Pydantic 集成及 OpenAPI 生成。

服务器与框架不是同一个对象。启动命令中的 agenthub.main:create_app 指向模块和函数，--factory 表示调用函数取得应用；--host/--port 指定监听地址；--no-access-log 关闭默认访问日志，避免默认完整 URL 与项目自己的日志策略冲突。

[health.py](../../src/agenthub/api/health.py) 的 APIRouter 把 /health 前缀和健康接口集中组织，再通过 app.include_router 注册到应用。装饰器 @router.get 在应用导入/组装期间注册路由，函数体在请求时执行。

Pydantic 的 LivenessResponse、ReadinessResponse 描述响应字段；response_model 参与响应校验/序列化和 OpenAPI 描述。输出不符合契约属于服务端问题，不能一概归为客户端 422。

OpenAPI 是可供工具读取的接口说明；Swagger UI 的 /docs 把这份说明变成交互页面。它便于开发测试，但知识库上传、聊天和 Agent 管理界面仍需后续实现。

## 4. 应用工厂、生命周期与资源管理

[create_app()](../../src/agenthub/main.py) 负责加载设置、配置日志、创建 FastAPI、注册异常/中间件/路由并返回应用。调用工厂可以创建不同配置的测试实例，避免导入模块时就连接真实数据库。

工厂中的 lifespan 使用 @asynccontextmanager，执行顺序为：

1. 进入 lifespan，创建 DependencyChecks，保存到 app.state.dependencies。
2. await dependencies.check() 做启动检查。
3. 执行到 yield，应用开始对外处理请求。
4. 正常关闭或上下文退出时进入 finally，await dependencies.aclose() 释放 Redis 连接池。

yield 在这里标出“应用运行期间”。try/finally 使资源退出逻辑集中可见；进程被强制终止、断电时仍不能承诺 Python finally 一定执行。生命周期官方用法见 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)。

三种状态保存位置要分开理解：app.state 保存应用实例共享资源；request.state 保存本次请求数据；ContextVar 保存当前执行上下文中的请求关联信息。

当前启动检查失败时记录状态并允许服务启动，之后 ready=503。另一种设计是失败即退出；当前方案便于持续诊断与依赖恢复。两种策略应由服务的可用性需求决定。

## 5. Pydantic Settings：配置与运行时校验

配置写在 [config.py](../../src/agenthub/core/config.py)。BaseSettings 从环境和文件等来源构造有类型的设置；本项目常用优先顺序是显式初始化参数 → 环境变量 → .env → 默认值。

例如 database_port: int = Field(default=5432, ge=1, le=65535)：字段应转成整数，默认 5432，范围 1—65535。传入无法解析的端口会在创建 Settings 时失败，尽早发现环境错误。[官方来源优先级说明](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)。

| 配置项 | 本项目的用途 |
| --- | --- |
| env_prefix="AGENTHUB_" | database_host 对应 AGENTHUB_DATABASE_HOST |
| env_file=".env" | 本地从 UTF-8 文件读取设置 |
| extra="ignore" | 允许同一 .env 包含 POSTGRES_PASSWORD 等 Compose 专用字段 |
| frozen=True | 防止运行期间随意给设置字段重新赋值，不等于把所有对象都深度冻结 |
| hide_input_in_errors=True | 格式化校验错误时不回显输入，不代表任意错误序列化都自动安全 |
| SecretStr / repr=False | 减少密码和含凭据 URL 在常规表示中的泄露 |

redis_url 的 @field_validator 使用 urlsplit 检查协议、主机与端口。允许 redis:// 和 rediss:// 不表示当前本地 Redis 已配置 TLS；当前 Compose 使用普通内部连接。

SecretStr 没有加密能力；调用 get_secret_value() 能读取原值，.env 本身也仍是明文文件。代码通过“不打印整个配置、不输出驱动异常、不提交 .env”建立边界。

还有两个易混的配置过程：Compose 读取 .env 为 YAML 的变量替换提供值；Pydantic 读取 .env 生成 Python 设置。Compose 不会自动把文件中每个值都注入容器，本项目通过 environment 显式注入内部主机名和密码。

## 6. 依赖注入、接口协议与 Python 类型

ready 函数声明：

```python
checker: Annotated[ReadinessChecker, Depends(get_readiness_checker)]
```

FastAPI 在处理请求时调用 get_readiness_checker，从应用状态取出检查器并传给路由。因此路由可以专注于“检查结果如何变成 HTTP 响应”。测试可用 app.dependency_overrides 替换这个入口，不必改生产代码。[FastAPI 依赖注入说明](https://fastapi.tiangolo.com/tutorial/dependencies/)。

ReadinessChecker 是 Protocol，声明“有一个可 await 的 check 方法，返回依赖状态字典”。实现类不必显式继承它，符合结构即可。它用于类型检查和职责边界，不会自动建立网络连接或在运行时验证对象。

本次代码中常见的 Python 语法可以这样读：

| 语法 | 读法与项目实例 |
| --- | --- |
| str、float、dict[str, ...] | 字段或函数的类型意图，供工具检查 |
| Settings \| None | 可以是设置实例，也可以是 None |
| Literal["ok", "error"] | 将允许值描述为有限选项；Pydantic 模型可据此运行时校验，普通变量标注本身不会校验 |
| Annotated[T, ...] | 在类型 T 上附加供框架读取的元数据 |
| cast(T, value) | 告诉类型检查器如何理解 value；运行时不转换或检查它 |
| Callable、Awaitable | 可调用对象、可等待结果，用于探针函数接口 |
| Mapping[str, str] | 可读的键值映射接口，错误响应头不必限定为具体 dict |
| Any | JSON 等动态边界允许的类型，不宜用它绕过全部检查 |
| default_factory=dict | 构造错误模型时创建自己的 details 字典 |
| def 内嵌函数 | 闭包可访问外层 settings、request_id 等值 |
| nonlocal | send_with_id 修改外层的 response_started/status_code |
| 参数列表中的 * | 后面的参数要求通过名称传入，如 details=... |

mypy 进行静态检查；Pydantic 执行模型构造时的运行时校验。二者要结合使用。

## 7. async/await、线程、并发与超时

网络请求经常在等待外部响应。异步 I/O 允许协程在等待时把事件循环交还给其他任务；不是把每条 Python 指令都变成并行执行。

async def 定义协程函数，调用后得到协程对象；await 驱动/等待它。若在 async def 内直接执行同步阻塞调用，事件循环仍会被占住。官方基础见 [Python asyncio](https://docs.python.org/3.13/library/asyncio-task.html)。

[DependencyChecks](../../src/agenthub/core/dependencies.py) 对两个驱动采用不同处理：Redis 的 ping 是异步操作，直接 await；psycopg 当前使用同步连接，将整个同步探针交给 anyio.to_thread.run_sync，在线程里执行，兼顾 Windows 事件循环兼容。

asyncio.gather 同时推进两个探针；耗时通常更接近较慢的一个，另有调度和处理开销。它不表示 SQL 查询在 GPU 上并行，也不解决 CPU 密集计算的扩展问题。

探针设置了多层超时：asyncio.timeout 限制外层等待，psycopg connect_timeout 限制连接，statement_timeout 限制 SQL 执行，Redis 有连接与 socket 超时。各层控制不同的等待，不应只写一个无限等待的客户端。

abandon_on_cancel=True 表示等待者取消后可以停止等待线程，但不能强行终止 Python 工作线程；底层工作还可能继续到驱动超时。因此不能把外层超时说成“数据库操作已经在同一瞬间停止”。[AnyIO 线程语义](https://anyio.readthedocs.io/en/stable/api.html#anyio.to_thread.run_sync)。

普通探针异常被转换为 error；取消请求的 CancelledError 不应被当作普通业务成功吞掉。Redis 使用异步 Retry、零额外重试，让本次失败及时暴露；之后的 ready 请求重新检查，从而在依赖恢复后重新成功。

本次没有业务连接池/事务层：Redis 客户端自带的连接池在应用生命周期内复用，PostgreSQL 每次探针新建连接并在 with 退出时关闭。后续高频业务访问在 P2 设计 SQLAlchemy 会话和连接池。

## 8. HTTP 中间件、请求 ID、ContextVar 与日志

[RequestContextMiddleware](../../src/agenthub/core/middleware.py) 是纯 ASGI 中间件。scope 描述请求/连接上下文，receive 获取事件，send 发送事件；__call__ 使中间件实例能像函数一样被服务器调用。非 HTTP 事件直接转交下层。

中间件在路由前后处理共同逻辑：

1. 读取 X-Request-ID；只接受 1—64 个指定 ASCII 字符，不合规时生成 uuid4().hex。
2. 保存到 request.state 和 ContextVar，开始 perf_counter 计时。
3. 调用下层应用，用 send_with_id 拦截 http.response.start，补响应头并记录状态。
4. 响应开始前的意外异常转换成统一 500；已经发送头时不能再伪造第二个完整响应。
5. finally 记录方法、路由模板、状态、毫秒耗时，并 reset(token) 恢复上下文。

ContextVar 的变量对象定义在模块级，值保存在当前执行上下文。中间件显式 set/reset，配合异步任务上下文管理，避免并发请求 A/B 共享同一个全局字符串。子任务存在上下文继承规则，并非每个库调用都会自动建立全新的请求上下文。[Python ContextVar](https://docs.python.org/3.13/library/contextvars.html)。

请求 ID 用于关联日志；客户端可重复提供同一个 ID，因此它不是身份凭据，也不能直接当作业务幂等键。

[JsonFormatter](../../src/agenthub/core/logging.py) 将日志输出为一行 JSON：UTC 时间、级别、logger、事件、请求 ID，以及允许的附加字段。结构化字段便于检索和后续采集；stdout 适合容器通过 docker compose logs 统一查看。perf_counter 用于测时，UTC 时间用于记录事件发生时刻，两者用途不同。

日志采用字段白名单，不自动输出请求体、查询字符串、未知路径或原始异常。记录路由模板而非用户提交的完整 URL，降低敏感信息泄露风险。开发者将秘密直接写入日志 message 时，白名单仍救不了它。

当前仅保留异常类型，排错细节有限，这是明确取舍；尚未接入集中日志、指标、完整链路追踪或告警平台。

## 9. 统一异常与 API 契约

[errors.py](../../src/agenthub/core/errors.py) 使用 ErrorResponse 包装 code、message、request_id、details。客户端可以稳定处理错误，而不必猜测不同路由的返回形式。

| 状态码 | 本项目含义 |
| --- | --- |
| 200 | 当前端点成功处理 |
| 404 / 405 | 路径不存在 / 方法不支持 |
| 422 | 请求输入没有通过框架校验；健康接口本身没有业务参数 |
| 500 | 响应开始前的未处理应用异常 |
| 503 | 必要依赖不满足，当前未就绪 |

HTTPException 由异常处理器转成公开状态说明，保留 Allow、WWW-Authenticate 等协议头；RequestValidationError 统一返回通用错误，避免直接把 .errors() 中的原始输入或校验上下文输出给用户。意外异常由本项目中间件捕获并补请求 ID。

details 只放明确可公开的内容，例如依赖的 ok/error，不放带密码的连接字符串。debug=False 避免用调试错误页作为公开异常响应。

中间件与异常处理器可以配合：前者建立请求上下文并包围执行，后者转换可识别的 HTTP/校验错误。底层 Starlette 还存在自己的错误/异常中间件层；不要简化成“任何位置的异常都由同一个函数处理”。后续 SSE 的响应开始后错误需要专门设计事件，当前尚未实现。

## 10. 健康与就绪：系统具备哪些条件

live 只检查 API 能处理请求；ready 实际验证 PostgreSQL 查询、vector 扩展与 Redis PING。两者分开可以在依赖故障时继续查看和诊断 API，而不因依赖故障就必须反复重启 API。

| 条件 | live | ready |
| --- | --- | --- |
| API 与依赖正常 | 200 | 200 |
| Redis 停止 | 200 | 503，redis=error |
| PostgreSQL 停止 | 200 | 503，postgresql=error |
| 数据库可连但未启用 vector | 200 | 503，postgresql=error |
| API 进程未提供服务 | 无 HTTP 响应 | 无 HTTP 响应 |

ready=200 证明当前探针条件满足，不保证下一次请求永远成功，也不证明尚未实现的业务能力可用。探针范围随着业务演进需重新评估，例如模型故障是否应影响全部 API。

## 11. PostgreSQL、SQL、pgvector 与 Redis

### 11.1 PostgreSQL 和 psycopg

PostgreSQL 是独立数据库服务；psycopg 是 Python 与它通信的驱动。连接需要主机、端口、数据库名、用户和密码。这些不是同一个概念；一个服务器里可以有多个数据库。

探针的 SQL 同时返回常量 1 和 EXISTS 查询结果：前者说明 SQL 能执行，后者在当前数据库 pg_extension 中确认 vector 已启用。端口可连接、pg_isready 通过、业务用户可执行查询，代表不同层次的条件。

autocommit=True 让这里不需要维护跨语句事务；它不等于数据库只读模式。当前探针没有写入，是因为执行的 SQL 只有 SELECT。业务写入需要正式事务边界，留到 P2。

### 11.2 pgvector 到底提供了什么

pgvector 给 PostgreSQL 增加向量类型和距离运算等能力。镜像包含扩展文件后，还需在目标数据库 CREATE EXTENSION vector；安装在服务器文件系统与在数据库启用是两个动作。

[初始化 SQL](../../docker/postgres/init-vector.sql) 安装扩展。验收脚本在隔离 schema 中使用 vector(3) 保存测试向量；SQL 的 <-> 运算符计算 L2 距离，相同向量距离为 0。[pgvector 官方说明](https://github.com/pgvector/pgvector)。

这次 [1,2,3] 是基础设施测试数据，没有冒充真实 Embedding。以后模型负责把文本转成向量，pgvector 负责保存与比较；模型标识、维度和权限过滤还需 P4/P5 实现。扩展不会自动解决这些问题。

schema 是数据库内的命名空间。验收使用随机 schema 隔离自己的临时表，清理后没有留下业务结构；正式业务表在 P2 使用迁移管理，不复用临时测试 SQL 作为业务设计。

### 11.3 Redis 与持久化

Redis 提供键值等数据结构能力，数据主要在内存中操作。P1 使用 PING 判断服务能响应，用 SET NX、GET 验证独立标记的读写；NX 限制只在键不存在时写入，避免碰撞后覆盖已有键。

Compose 开启 appendonly yes，并把 /data 挂到命名卷。AOF 记录写操作，使重启后可以重放恢复；写日志、fsync 策略、文件系统和正常/异常停机方式都会影响耐久性。RDB 快照是另一种持久化方式，但本次没有对它单独验收。[Redis 持久化官方说明](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)。

Redis 本次尚未承担业务任务队列；队列库在 P4 再选择。缓存、队列协调与 PostgreSQL 中的持久业务事实应按职责设计，不能仅因 Redis 开了 AOF 就随意替换业务数据库。

## 12. Docker、WSL 2、镜像构建与运行用户

### 12.1 Windows 上的 Docker 组成

本机 Docker Desktop 使用 WSL 2 提供 Linux 内核环境，Linux 容器运行在其中。docker CLI 是客户端，Engine 管理镜像、容器和网络，Compose 按 YAML 组织多个服务，BuildKit 执行镜像构建，凭据助手负责配合注册表认证。Docker Desktop 集成和管理这些组件。

镜像是可分发的文件系统/元数据模板；容器是从镜像启动的运行实例，带进程、网络和可写层。每个容器无需各自启动一整台 Linux 虚拟机，但共享内核不代表没有隔离边界。命名卷管理需要跨容器生命周期保留的数据。

安装器对用户 PATH 的更新不会回写已经运行进程的环境，因此旧 Codex/终端需要补路径或重新打开。只找到 docker.exe，仍可能找不到它调用的 docker-credential-desktop。本次认证超时也说明 DNS、代理、TLS、镜像仓库访问是与 Python 代码不同的排错层；不要因网络错误随意重写应用。

安装前校验 SHA-256 检查下载是否与可信来源记录一致，校验数字签名检查发布者与签名有效性。哈希和签名回答不同的问题。安装采用官方支持的当前用户模式，未要求系统级安装；[官方安装说明](https://docs.docker.com/desktop/setup/install/windows-install/)。

### 12.2 读懂 Dockerfile

| 指令/参数 | 本项目含义 |
| --- | --- |
| 两个 FROM / COPY --from=uv | 从 uv 阶段只复制工具到 Python 最终基础镜像 |
| WORKDIR /app | 后续工作目录 |
| 先复制 pyproject/lock/README | 将依赖安装与频繁变化的源码分层 |
| --locked --no-dev --no-install-project | 安装锁定运行依赖，先不装项目包 |
| COPY src / --no-editable | 复制源码后安装正式项目包 |
| USER agenthub | 构建后用非 root 身份运行服务，实测 UID 999 |
| EXPOSE 8000 | 描述容器端口，不会自动向宿主机发布 |
| JSON 数组形式 CMD | 直接运行 Uvicorn，减少额外 shell 层对参数/信号的影响 |

分层可让仅修改源码的构建复用先前依赖层；任何相关输入变化都可能使缓存失效，不是承诺每次都不下载。镜像标签可能被更新，本项目记录了实际 RepoDigest，但尚未把所有镜像固定到 digest。

环境变量也有具体作用：PYTHONUNBUFFERED 让标准流及时输出；PYTHONDONTWRITEBYTECODE 禁止解释器自动写 pyc；UV_COMPILE_BYTECODE 在构建期显式预编译；UV_LINK_MODE=copy 避免跨文件系统硬链接问题；UV_PYTHON_DOWNLOADS=never 要求使用已有基础镜像解释器。

非 root 可以缩小权限范围，但没有替代认证、授权、网络治理或完整容器安全设计。

## 13. Compose：服务发现、端口、健康顺序和卷

[compose.yaml](../../compose.yaml) 声明 api、postgres、redis 三个服务。默认网络让容器通过服务名发现对方，API 连接 postgres:5432、redis:6379；容器内 localhost 表示当前容器自己。[Compose 网络说明](https://docs.docker.com/compose/how-tos/networking/)。

API 容器内监听 0.0.0.0:8000，使容器网络能够访问服务；宿主机端口映射 127.0.0.1:8000:8000 限制本地发布。容器监听地址和宿主机发布地址属于不同网络位置，不能只看其中一个判断暴露范围。

PostgreSQL、Redis 没有配置主机端口，所以 Windows Python 不能直接使用容器内部服务名连接它们。同一网络中的容器仍可访问它们，未发布端口也不等于对所有威胁完成隔离。

depends_on: service_healthy 管理启动等待；healthcheck 定期执行检查，retries、interval、timeout、start_period 决定标记时机。API 的检查调用 ready，数据库使用 pg_isready，Redis 使用 redis-cli ping。运行期间 unhealthy 不代表 Compose 会自动重启容器，当前也没有因此自动摘除流量的负载均衡器。

两种挂载承担不同职责：命名卷将 PostgreSQL 数据目录、Redis /data 保存在 Docker 管理的存储中；初始化 SQL 使用只读 bind mount，把仓库文件交给容器初始化入口读取。Windows 下命名卷的数据位于 Docker 的 Linux 虚拟磁盘中，本机磁盘放在 E:\DockerData，不是直接变成仓库里的普通业务文件夹。

默认命名卷的生命周期独立于容器，普通 down 后再 up 可以复用原卷。[Docker volumes](https://docs.docker.com/engine/storage/volumes/)。不要附加删卷参数。bind mount 也能保存数据，但需要额外处理主机路径、权限和文件系统行为，当前数据库数据采用命名卷。

PostgreSQL 镜像的初始化目录仅在新空数据目录初始化时执行。修改 init-vector.sql 或 POSTGRES_PASSWORD，不会自动更新已有卷里的数据库结构或密码；这正是后续迁移和运维流程要解决的事情。

卷不会自动产生一份独立备份；主机磁盘丢失、删卷、错误写入等仍可能损失数据。本次验收只证明正常 stop/start 与保留卷重建后的恢复。

## 14. 测试、质量门禁与证据

### 14.1 分层测试

| 测试层 | 本项目实例 | 证明什么 |
| --- | --- | --- |
| 配置/组件测试 | test_config、test_dependencies、test_errors | 校验、分支、超时、日志与错误规则 |
| 进程内 API | TestClient、HTTPX2、test_health | 路由/响应/中间件协作；不经过真实 TCP |
| 真实 HTTP 冒烟 | test_http_smoke | 启动 Uvicorn，经 TCP 验证正常 API 和依赖失败路径 |
| 真实依赖集成 | test_integration | 真正连接 PostgreSQL/vector 与 Redis，验证 ready 成功 |
| 环境验收 | verify_p1.py | 停服务、恢复、容器重建、数据保留与清理 |

fixtures 负责准备和收尾，TestClient 用 with 才会执行 lifespan；monkeypatch 替换环境或方法并在退出后恢复；AsyncMock 模拟可 await 的结果；parametrize 用多组输入覆盖规则。

测试替身帮助稳定触发故障，但不能证明真实外部服务可用。conftest 的默认应用 fixture 在启动时也替换探针，真实集成测试则不使用这个 fixture。AGENTHUB_RUN_INTEGRATION=1 显式启用真实测试，没启用时 skip，启用后服务不可用应失败。

HTTP 冒烟测试用子进程、临时工作目录及保留但不监听的端口，避免访问真实数据库或读取用户 .env；最终只终止自己创建的进程。subprocess 使用参数列表，减少跨 shell 引号与意外命令解释问题。

真实依赖测试在一次性容器里挂载 tests 并安装开发依赖，让它使用 Compose 网络；生产镜像默认不带 pytest。当前安装的 Starlette TestClient 使用锁定的 HTTPX2，阅读旧教程时要留意包名和版本差异。

### 14.2 这些检查不能互相替代

| 工具 | 检查范围 |
| --- | --- |
| Ruff check / format | 静态规则、导入顺序、常见错误与排版 |
| mypy strict | 类型使用是否自洽，当前覆盖 src 与 scripts |
| compileall | Python 源码能否编译，不能证明业务或外部连接正确 |
| pytest | 断言描述的运行行为 |
| uv build / 独立 wheel 安装 | 构建和安装导入边界 |
| Compose schema / config --quiet | 结构和配置解析，不证明镜像能拉取、服务能运行 |
| 实际 Docker 验收 | 指定机器、配置与操作条件下的真实结果 |

P1 的已记录结果是宿主机 33 passed、1 skipped，容器集成 1 passed；11 个验收事件成功，清理无错误。它们来自前次实际执行，本轮教学没有重复运行这些服务测试。详见 [验收记录](../acceptance/P1_2026-11-09.md)。没有测量覆盖率百分比、性能或断电恢复，不编造相应结论。

### 14.3 为什么验收脚本检查容器 ID

[verify_p1.py](../../scripts/verify_p1.py) 先写入随机测试 schema 和 Redis 键，分别停止/启动依赖，核对 API ID 不变；之后保留卷 down/up，核对三个容器 ID 都变化，并读取同一 token 与向量距离。

ID 变化证明实例被替换，同一数据仍在证明数据保留，两类证据一起才有说服力。finally 恢复服务、清理本次测试标记，并写出带运行 ID、版本、事件与错误的 JSON。测试失败、跳过与清理失败各自有不同含义，不能混为通过。

## 15. Git、文档与后续学习

Git 保存源码与文档的变更历史，分支提供独立工作线，commit 保存可审查快照。本项目保留了规划基线、P1 工程底座、真实容器验收三个有意义的节点；git status 查看未提交状态，git diff 查看差异。

代码回滚不会自动回滚数据库、Docker 卷、外部服务副作用或 Git 历史中已经泄露的秘密。没有记录在 Git 的数据恢复需要另行设计。

项目状态记录已完成/未完成，架构记录模块协作，API 文档记录契约，部署与测试记录运行方式，开发日志记录发生过的事实，ADR 记录取舍，学习日志记录讲解与掌握证据。这些文档和测试共同帮助以后解释“为什么这样做、如何证明它有效”。

建议读代码顺序：main → config → api/health → dependencies → middleware/logging/errors → Dockerfile/compose → tests → verify_p1。先跟通一个请求，再理解并发、异常和部署边界，不必一次背下所有库的 API。

面试练习可按五条主线准备：讲清一次请求；解释同步 I/O 为什么会阻塞；解释配置与敏感信息边界；解释进程存活与依赖就绪；说明怎样证明容器重建后数据仍在。

## 16. 学习检验：本轮先做一组

理解问题（请先自己回答，不附标准答案）：

1. 浏览器请求 ready 时，从 Uvicorn 开始，依次会经过哪些本项目模块？在哪里创建请求 ID，在哪里访问外部服务？
2. 如果把 psycopg.connect 直接放进 async 路由，async def 能消除它的阻塞吗？当前代码如何处理，取消等待还有什么限制？
3. 新容器启动后 PostgreSQL 中的记录仍在，这件事分别依赖什么配置？还缺什么验证才能说具备独立备份恢复能力？

代码练习：给 LivenessResponse 增加 version 字段，来源为 agenthub.__version__；检查并同步接口测试、真实 HTTP 冒烟测试、验收脚本中受影响的断言和 API 文档，执行 pytest 与 Ruff。先独立修改，再检查结果。本轮不替你完成练习。

项目思考题：未来模型服务故障时，知识库管理、历史会话查询、Agent 运行各该如何表现？你会怎样划分就绪检查的依赖范围，避免一个外部故障影响所有功能？

状态：以上是学习材料与练习安排，尚无用户回答/独立练习证据，不标记已掌握。API、数据库与业务实现未因本指南发生变化。
