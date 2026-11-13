# AgentHub 开发记录

## 2026-11-13 完成 P8 自动化评测与回归

- 新增版本化评测集、受控批次 API 与 worker、规则评分、人工复核、终态报告与逐例对比。使用 Alembic `20261113_0007` 持久化评测。P5 共用生成逻辑，P7 共用图和账本。语料漂移可定位，取消和中断保留已执行结果，输出 token 按样例预留。
- 固定公开集的真实 DeepSeek / 百炼对比为基线 4/4、候选 3/4，定位到计算工具选择。独立 Compose 真实评测由排队转完成，业务开发库沿用上次记录。回归证据、指标及验证边界见 [P8 验收](acceptance/P8_2026-11-13.md)。
- GitHub Actions 已提供无外网模型回归和显式付费作业，独立 PostgreSQL 回归 230 passed、6 skipped，静态检查通过。第九课已提供，下一步接入 P9 的评测页面。

## 2026-11-13 完成 P7 LangGraph Agent 与工作流运行

- 新增固定图 v1、模型原生工具协议、Agent API 与独立 worker。复用 P5 私有检索和 P6 工具执行；增加审批、暂停、取消、恢复、用量及事件续读。
- 迁移 `20261113_0006` 新增配置、运行、步骤、审批、事件、LangGraph 检查点及工具开关。会话级 advisory lock 串行推进；执行账本保存外部调用意图与结果。
- 独立 PostgreSQL 完整回归 220 passed、4 skipped，真实云样本、HTTPS MCP 写入审批与中断边界、独立 Compose worker 重建验证通过。业务开发库本轮未升级。 [P7 验收](acceptance/P7_2026-11-13.md)
- 下一阶段 P8 建立固定 RAG / Agent 样本和可重复对比的评测记录。

## 2026-11-12 完成 P6 工具注册与 MCP 集成

- 新增统一调用服务、工具目录、管理员注册和调用审计，迁移 `20261112_0005` 新增三表。内置检索与原 P5 路由共用 Embedding 和 pgvector 路径。
- 固定 MCP SDK 2.3.0 与协议 2026-07-28。用传输层拒绝重定向，并在 TCP 连接固定地址时保留原 TLS 身份。本地工具独立运行于网络隔离容器。
- 独立数据库和 Redis 回归通过。实际停启 MCP 容器验证 API 保持就绪，远端 HTTPS 用独立测试进程验证 TLS、服务令牌、超时和定义漂移。 [P6 验收](acceptance/P6_2026-11-12.md)
- 下一阶段接 LangGraph 运行状态、工具允许列表、预算与审批。既有开发库按部署文档升级 P6。

## 2026-11-12 完成 P5 检索问答与引用

复用 P3 模型适配器、会话租约和 P4 向量数据，新增私有知识库检索、会话问答与引用历史。实现 SQL 精确余弦排序、Top-K/阈值、序列化上下文预算、证据不足、SSE、引用校验和删除复查。Alembic 0004 新增 `rag_runs`。

完整独立 PostgreSQL 回归 189 passed、4 skipped。单独启用真实百炼 Flash 与 DeepSeek，三种文件各命中目标且引用可定位，无证据与越权样本通过。Ruff、mypy、语法和打包检查通过。本地 API 与 worker 构建，业务库先备份后升 0004，ready 和路由烟测通过。 [P5 验收](acceptance/P5_2026-11-12.md)

已交付第六课与练习。下一阶段为 P6 工具注册与 MCP；更大规模的标注集与质量回归由 P8 扩展。

## 2026-11-10：完成 P4 知识库、文档处理与向量入库

新增knowledge模块、私有知识库/文档/原文/任务/分块API、0003四表迁移及vector(1024)。原文件UUID存储与SHA-256去重，支持UTF-8 TXT/MD/文本PDF，保存规范化页码/字符来源、固定模型空间和处理版本。上传请求与后台ETL分离，独立worker以PostgreSQL持久任务/租约领取，redis-py仅有界唤醒；整份向量原子发布，失败显式版本重试，删除墓碑/版本检查阻止迟到发布，物理删除和一小时孤儿清理补偿文件一致性。

依赖新增pypdf6.21.4、pgvector0.4.2（numpy2.5.4）、python-multipart0.0.33，已锁定；新增受限解析子进程、共享document_data卷、UID999 worker、512MiB/1CPU限制，保持既有聊天接口。重要决策见ADR-016，学习课程/题目见KNOWLEDGE.md/JOBS.md，掌握情况仍待回答与练习。

验证：全套真实独立PostgreSQL回归163 passed/3 skipped，P4包含22单元与20集成；Ruff/format87文件、strict mypy41源文件、compileall及打包通过。真实Flash单独1 passed，固定公开TXT/MD/PDF经三个独立worker进程生成3条1024维向量，用量81 token；不记录实际账单。故障测试覆盖批次失败原子性、删除并发、实际进程终止回收、提交不确定性、清理失败重试、无Content-Length大小上限等。

业务库pg_dump备份后升0003，API/worker启动，ready、共享卷、真实Redis唤醒及alembic check通过。文档全套同步；未做业务库降级/清空、备份恢复、性能/召回评测或新DeepSeek调用。后续P5实现权限检索、上下文/引用及无证据问答。证据与边界见acceptance/P4_2026-11-10.md。

本文件记录重要任务的实际结果。计划与未来功能见 [开发计划](DEVELOPMENT_PLAN.md)，当前状态见 [项目状态](PROJECT_STATUS.md)。

## 2026-11-04：P0 文档与开发基线

### 任务与范围

阅读现有项目，补齐文档索引、项目状态，规划从零实现完整 AgentHub 的阶段任务。用户确认首版本地 Docker、单组织使用，后续逐步补齐企业能力。

### 实际盘点

- 开始时只有根目录 AGENTS.md，以及两个 0 字节文件 docs/PROJECT_STATUS.md、docs/LEARNING_LOG.md。
- docs/README.md 不存在，未发现业务代码、测试、依赖清单、容器配置或 .git 目录。
- PATH 可发现 Git、rg 和位于 WindowsApps 的 python.exe；本次未实际执行 Python，也未确认解释器版本。
- PATH 未发现 docker 命令。没有据此断言 Docker 未安装，也没有验证 Docker Desktop 状态。

### 主要修改

| 文件 | 变化 |
| --- | --- |
| docs/README.md | 新增文档导航、阅读顺序、现有/计划文档边界和状态规则 |
| docs/PROJECT_STATUS.md | 从空文件补充真实进度、环境待核验项、P1 下一步和验证边界 |
| docs/DEVELOPMENT_PLAN.md | 新增 P0—P10 任务、依赖、数据库影响、验收、完整首版标准及学习安排 |
| docs/ARCHITECTURE.md | 新增尚未实现的目标架构、模块职责、数据流和部署边界 |
| docs/TECH_DECISIONS.md | 新增用户确认范围、设计取舍、官方依据与待决事项 |
| docs/DEVELOPMENT_LOG.md | 新增本次开发记录 |
| docs/LEARNING_LOG.md | 从空文件补充本次学习材料、问题与后续练习，未标记已掌握 |

### 关键问题与处理

1. 项目没有实际实现，不能补写虚构的 API 和数据库文档；在索引中记录其创建时机。
2. “完整项目”需要可以操作的产品链路，而不只是架构图，因此规划包含界面、真实模型/MCP、评测、部署和恢复验收。
3. 认证、权限、日志和测试从早期建立；队列、检查点与工具副作用的恢复风险纳入对应阶段。
4. 已查阅 FastAPI、LangGraph、pgvector 和 MCP 官方资料校正技术边界；未安装运行库，也未宣称具体版本兼容。

### 实际验证

- 完成目录与文件长度盘点，阅读 AGENTS.md。
- 完成 PATH 命令可发现性检查；此检查不等于运行环境已经可用。
- 使用 Node.js 通过标准输入执行只读文档检查，退出码为 0：7 份文档、28 个本地链接、11 个阶段、68 条阶段任务；发现问题 0 个。
- 检查覆盖非空内容、主标题、替换字符、代码围栏配对、本地链接存在性、阶段完整性、任务编号唯一性与状态一致性。5 条 P0 规划任务完成，63 条实施任务未开始。
- 完成文档维护清单与实际文件对应检查；未改动 AGENTS.md。
- 没有业务代码，未执行单元/集成/接口/类型检查；未启动 Docker、数据库、Redis、MCP 或模型服务。

### 文档维护清单

- PROJECT_STATUS：已补齐，只有 P0 标记完成。
- ARCHITECTURE：已创建，全文明确为目标设计，非现有实现。
- DATABASE：不适用，本次没有模型或迁移；P2 创建。
- API_DESIGN：不适用，本次没有接口；P1 创建。
- modules/：不适用，本次没有业务模块或实际代码入口；按阶段创建。
- DEVELOPMENT_LOG：已记录本次重要规划任务。
- LEARNING_LOG：已提供规划学习材料与练习，掌握情况待验证。
- TECH_DECISIONS：已记录设计基线及待决选型。
- 一致性：索引、状态、计划、架构和记录均区分已完成文档与未开始业务功能。

### 后续事项

下一阶段 P1：核实运行环境，建立工程底座、Docker 配置、基础 API、测试及配套操作文档。没有将任何业务功能标记为已实现。

## 2026-11-09：P1 第一次代码开发——工程底座

### 范围与实际环境

按既有路线图实现公共运行层，不提前实现业务表、认证、RAG 或 Agent。核实 Python 3.13.12 和 uv 0.11.27 可运行；Git 已初始化但无提交，纠正旧状态中“无 .git、Python 未验证”的记录。创建 codex/p1-foundation 分支，并先提交原始规划基线 e84a282。

PATH 无 docker，常规 Docker CLI 路径不存在，C:\Program Files\Docker 为空；未发现 Docker 服务或 docker_engine 管道。当前无法执行容器验收，未安装系统组件。

### 主要修改

- 新增 src/agenthub：可安装包、FastAPI 工厂、Pydantic Settings、生命周期、请求 ID、JSON 日志、统一 HTTP/校验/意外错误。
- 新增 GET /health/live 与 GET /health/ready：真实 PostgreSQL SELECT/vector 扩展检查、Redis PING、并发与超时；依赖失败返回 503，API 继续提供诊断。
- 新增 pyproject.toml、uv.lock、.python-version、.env.example、.gitignore、.dockerignore；运行依赖按当前阶段引入，不提前安装业务框架。
- 新增 Dockerfile、compose.yaml、docker/postgres/init-vector.sql：非 root API、内部数据库/缓存、持久化卷、就绪依赖顺序、空卷 vector 初始化；无业务表或 Alembic 迁移。
- 新增 tests/conftest.py、test_config.py、test_dependencies.py、test_errors.py、test_health.py、test_http_smoke.py、test_integration.py。
- 新增根 README、API_DESIGN、DEPLOYMENT、TESTING、modules/FOUNDATION；更新索引、进度、架构、路线、决策和学习记录。

### 关键问题与处理

1. 初轮 mypy 发现 HTTPException.headers 类型为 Mapping，修正错误响应函数签名；初轮日志测试捕获了错误的 stdout 生命周期，改用专用 StringIO 捕获真实 JSON 日志并验证脱敏与请求 ID。
2. 当前 Starlette 1.10.0 提示 httpx 测试依赖弃用；核对官方说明后改用 HTTPX2 2.16.0，修正版本范围并重新锁定，最终测试无该警告。
3. 采用同步 psycopg 探针 + AnyIO 线程兼容本机 Windows，外层超时及驱动/SQL 超时共同限制等待；取消等待不能立即停止底层线程，该限制已记录。
4. 严格区分探针测试替身、真实 TCP 故障测试、真实依赖成功和 Docker 运行。未用替身结果冒充真实基础设施验收。
5. API 日志只记录异常类型与允许字段，不输出原始堆栈/消息；诊断深度有限，后续再设计受控脱敏诊断。

### 实际测试与质量验证

| 命令/检查 | 结果 |
| --- | --- |
| uv lock --default-index https://pypi.org/simple；uv sync --locked | 成功生成与安装锁定依赖 |
| uv run --locked pytest | 33 passed、1 skipped；skip 为真实依赖成功测试 |
| uv run --locked ruff check . | 通过 |
| uv run --locked ruff format --check . | 通过 |
| uv run --locked mypy | strict 源码检查通过 |
| uv run --locked python -m compileall -q src tests | 通过 |
| uv build | wheel 和 sdist 构建成功 |
| uv run --isolated --no-project --with ./dist/agenthub-0.1.0-py3-none-any.whl python 执行导入与 OpenAPI 断言 | 成功从 wheel 安装至独立环境，版本 0.1.0，两个公开健康路由符合预期 |
| uvx check-jsonschema --schemafile https://raw.githubusercontent.com/compose-spec/compose-spec/main/schema/compose-spec.json compose.yaml | check-jsonschema 0.39.0：ok；工具隔离安装，未加入项目依赖 |
| 本地 Markdown/阶段一致性检查 | 12 份 Markdown、57 个本地链接、68 条阶段任务；问题 0 个 |
| git diff --check 与敏感字段差异检查 | 无空白错误；未发现真实凭据，.env 与缓存/构建产物均忽略 |

真实 HTTP 冒烟测试启动独立 Uvicorn，通过 TCP 验证 live=200、依赖失败 ready=503 与一致请求 ID、docs/OpenAPI 可达；使用保留但不监听的本地端口，不访问真实数据库，不保留常驻进程。

未执行：Docker config/build/up、镜像拉取、真实 PostgreSQL/vector/Redis 成功、依赖停止恢复与命名卷持久化。schema 校验只验证结构，不代表这些运行检查通过。业务接口、迁移、模型/MCP、性能和完整产品端到端尚未实现，也未测试。

### 文档维护检查与交付状态

- PROJECT_STATUS、ARCHITECTURE、API_DESIGN、modules/FOUNDATION：已按实际代码更新。
- README/索引、DEVELOPMENT_PLAN、DEPLOYMENT、TESTING：已同步启动流程、实际进度与验证边界。
- TECH_DECISIONS：新增 ADR-006/007，记录版本/包布局与真实探针/启动策略。
- DEVELOPMENT_LOG、LEARNING_LOG：已记录开发与第一课，学习掌握情况待验证。
- DATABASE：不适用，当前无业务模型/迁移；唯一 vector 基础设施扩展初始化在 DEPLOYMENT 记录，P2 创建业务数据库文档。
- SECURITY：本次未建立身份权限模块，P2 创建；本地边界及当前限制已在部署和状态文档说明。
- 不同文档均保持 P0 完成、P1 开发中、P2—P10 未开始；完成配置不等于完成容器验收。

后续先补 P1 容器与真实依赖成功验收，再进入 P2 数据持久化和身份认证。用户的第一课练习待回答与独立修改验证。

## 2026-11-09：P1 Docker 环境配置与真实验收完成

### 任务与实际结果

按用户要求配置 Docker，并完成上次遗留的真实依赖、停止/恢复和数据保留验收。**P1 本次满足阶段验收条件，标记完成；P2 仍未开始。** 宿主机原有 WSL 2.7.11 和虚拟化已就绪，安装官方签名 Docker Desktop 4.98.0 当前用户版，Engine/CLI 29.9.2、Compose 5.6.1，数据放在 E:\DockerData，无需提权或重启。

完整安装签名/哈希、版本、镜像摘要、HTTP 结果、容器 ID 与恢复证据统一保存在 [P1 真实验收记录](acceptance/P1_2026-11-09.md)，避免重复维护原始明细。

### 主要修改

- 新增 scripts/verify_p1.py：真实 Compose 栈验收、隔离标记数据、依赖 stop/start、API ID/错误/日志检查、保留卷 down/up、清理恢复与本地 JSON 报告，无新增依赖。
- 修改 pyproject.toml：strict mypy 纳入 scripts；业务应用代码和 API 契约未改动，Dockerfile/Compose 原配置已经成功通过实际运行。
- 本机创建忽略的 .env，生成随机 PostgreSQL 密码，未输出或提交真实值；安装器与原始机器验收报告在忽略的 data/ 下。
- 新增 docs/acceptance/P1_2026-11-09.md；更新根 README、文档索引、状态、计划、架构、部署、测试、基础模块、技术决策与学习记录。

### 关键问题与处理

1. 旧会话 PATH 未刷新，完整路径启动 Docker 后仍找不到 docker-credential-desktop。只给当前命令补安装目录，不清空已有凭据配置；安装器已更新用户 PATH。
2. 首次 API 构建的 Docker Hub 认证直连超时。基础镜像通过引擎预拉取，构建命令使用本机已启用代理后成功；未修改系统代理或改用未知镜像源。
3. 用户要求验证真实功能，故不以 schema、进程启动或替身成功作为验收：同时写入/读取 PostgreSQL 向量和 Redis 数据，重建前后检查容器 ID 及同一数据。
4. 脚本在随机测试 schema/Redis 键内操作，自身标记最终已清理。没有新增持久业务表、SQLAlchemy 模型或迁移；未删卷、删库，也未碰其他项目数据。
5. 新脚本 strict 类型检查发现动态 JSON 标注过窄，已在 HTTP JSON 边界改用明确的动态字典类型，最终 src + scripts 共 11 个源码文件检查通过。

### 实际验证

| 检查 | 结果 |
| --- | --- |
| 安装包 SHA-256 / Authenticode / 安装退出码 | 与官方源一致 / Valid Docker Inc. / 0 |
| docker version、compose version、config --quiet | 引擎与 Compose 可用，配置有效 |
| 实际镜像 pull、API build、up -d --wait | 通过；三个服务 healthy |
| 宿主机 uv run --locked pytest | 33 passed、1 skipped；真实依赖成功在容器内单独执行 |
| 容器内显式启用 integration pytest | 1 passed，真实 PostgreSQL/vector 与 Redis |
| uv run --locked python scripts/verify_p1.py | passed=true，11 项事件通过，cleanup_errors=[] |
| 依赖停止/恢复 | 两种依赖分别 live=200、ready=503→200；API ID 不变 |
| 保留卷的 down/up | 三个容器 ID 全变；同一 PostgreSQL/vector 和 Redis AOF 标记保留 |
| 网络、用户、清理 | UID 999；依赖无主机端口；测试 schema 和 Redis 键均已不存在 |
| Ruff check / format、strict mypy、compileall src tests scripts | 通过 |
| 文档本地链接/状态/任务检查、git diff --check | 13 份 Markdown、72 个本地链接、68 条阶段任务，问题 0 个；无差异空白错误 |

验收脚本运行于 16:14—16:15（Asia/Shanghai），运行 ID a3b17351f002492bb481b892d72d6bf8。原始 JSON 位于 data/acceptance/p1-a3b17351f002492bb481b892d72d6bf8.json。服务保留运行，最终 live/ready=200。

文档与 mypy 配置同步后，再次构建最终 API 镜像并通过 up --wait 与真实 ready=200 检查，运行环境与当前仓库配置一致。

### 文档维护与边界

已同步 PROJECT_STATUS、DEVELOPMENT_PLAN、ARCHITECTURE、README/索引、DEPLOYMENT、TESTING、modules/FOUNDATION、TECH_DECISIONS（ADR-008）、DEVELOPMENT_LOG、LEARNING_LOG 和验收证据。API_DESIGN 不变：路径、请求响应与错误契约均未变化。DATABASE 仍不适用：无持久业务模型或迁移，临时验收 schema 创建后已删除；基础扩展继续在部署文档记录。AGENTS.md 未改动。

本次正常 stop/down/up 验收不代表断电、磁盘损坏或独立备份恢复已通过；业务认证/迁移、模型、RAG/Agent、MCP、性能和完整产品验收均属后续。教学已补真实代码流程、设计取舍、常见错误与面试方向；用户理解和实践仍待验证。

下一步为 P2 数据持久化、身份认证和权限的需求与迁移方案设计。

## 2026-11-09：P1 全技术系统教学

- 任务：按用户要求讲解 P1 全部技术，结合当前代码、实际验证与后续阶段边界。
- 交付：新增 docs/learning/P1_TECHNICAL_GUIDE.md，16 个主题覆盖 Python 工程、API、配置/类型/生命周期、异步与线程、追踪/错误、数据库/缓存、容器与存储、测试/Git/文档；同步索引、模块学习入口与 LEARNING_LOG。
- 解释重点：运行时与静态校验、异步与同步阻塞、上下文与全局变量、敏感信息表示与加密、内部服务发现与主机端口、持久化与独立备份。保留真实代码入口、替代方式、常见错误、面试方向及 3 个理解题、1 个代码任务、1 个项目思考题。
- 教学模拟：在当前聊天中演示 API/PostgreSQL/Redis 条件对 live/ready 的影响，不连接或操作真实服务。
- 实际验证：14 份 Markdown、91 个本地链接、16 个教学主题检查，问题 0 个；git diff --check 通过。模拟的 JavaScript 在最小 DOM 测试环境下验证 12 种状态组合、9 个唯一元素 ID，全部通过；未声称完成浏览器视觉检查。
- 测试边界：业务代码、配置和接口未变，未重复运行 pytest、Docker 故障或持久化验收；教材明确引用前次真实结果，不把它们记作本轮新测试。
- 文档适用性：PROJECT_STATUS、ARCHITECTURE、DATABASE、API_DESIGN、DEPLOYMENT、TESTING、TECH_DECISIONS 无功能或决策变化，无需修改；P1 仍已完成，P2 未开始。代码练习还未由用户完成，掌握情况待验证。

## 2026-11-09：C 盘空间诊断与 uv 缓存迁移

- 任务：排查 P1 后 C 盘空间减少，迁移适合放到 E 盘的开发缓存；不改业务代码、数据库、API 或 Docker 服务配置。
- 诊断：Docker 程序本体当天新增约 3.22 GiB；Docker/WSL 数据已在 E:\DockerData。uv 历史缓存约 7.96 GiB，其中当天创建约 0.16 GiB；CLI 插件为硬链接，避免重复累加。分页文件存在较大占用，文件长度与 WMI 已分配值不同，无开发前快照，增长原因和增量不能确证。
- 实施：复制缓存至 E:\DevCache\uv，52,492 个文件逐一通过 SHA-256；新增本机用户级 uv.toml，通过 uv 官方 prune/clean 命令清理无用及原缓存，再在原空缺路径创建 Junction。C 盘可用空间约从 17.09 GiB 增至 25.08 GiB，实测增加约 7.99 GiB。完整步骤与边界见 [部署文档](DEPLOYMENT.md#本机磁盘占用与-uv-缓存迁移)。
- 问题记录：含重命名/回滚删除的组合命令被自动审批拦截，未执行，改用官方缓存管理流程完成。临时 `--with` 离线安装未命中缓存，未记作通过；项目锁定依赖的独立安装与离线重装均通过，具体临时缓存键差异未追溯。
- 验证：现有环境和 E 盘独立验证环境 pytest 均 33 passed、1 skipped；新环境按 uv.lock 安装 35 个包、离线强制重装成功；Ruff 通过。三个原运行容器均 healthy，live/ready 成功且真实 PostgreSQL/Redis 为 ok；未停服务或重复执行完整故障/持久化验收。原始机器记录和一次性校验脚本均在忽略的 data/storage/。
- 文档：更新 DEPLOYMENT、PROJECT_STATUS、DEVELOPMENT_LOG、LEARNING_LOG。ARCHITECTURE、API_DESIGN、modules、TESTING、TECH_DECISIONS 无架构、协议、业务职责、测试规范或重要技术选型变化；DATABASE 仍无模型或迁移变化，不新建。P1 已完成、P2 未开始。
- 后续：监控 C 盘余量与内存；Docker 程序迁移需先核实安装器方案，系统分页位置调整需走 Windows 设置与重启流程，本轮未实施。
- 文档检查：4 份变更文档、19 个本地文件链接，失效链接 0；git diff --check 通过。最终复查三个服务仍 healthy、ready 的两个真实依赖均 ok；可用空间随后台活动有少量波动。

## 2026-11-10：P2 数据持久化、身份认证与权限基础

- 新增db/identity、版本化身份API、SQLAlchemy连接池/每请求Session、显式事务；新增roles/users/login_sessions/audit_events及Alembic初始迁移。管理变更与审计同事务，异常回滚。
- 新增Argon2id、随机Bearer令牌摘要存储、绝对到期与退出撤销、改密撤销全部会话、角色/状态变更撤销；首位管理员隐藏输入CLI，无公开注册或默认口令。
- 新增admin/member、用户本人读取限制、管理审计列表；角色行锁串行化管理写入并在锁后重验证，禁止修改自身角色/状态，防止并发管理员相互禁用导致全部失效。
- 新增compose.test.yaml真实隔离测试库（tmpfs），降级/清理前校验当前库；Dockerfile包含迁移文件，Compose新增会话有效期/池配置。
- 依赖检查后仅新增三个直接依赖并锁定：SQLAlchemy2.0.57、Alembic1.20.1、argon2-cffi25.1.0；没有引入JWT/ORM封装/全套认证框架。
- 环境：Docker安装注册信息缺失；校验现有安装包有效签名，以原参数重装恢复成功，原命名卷保留。开发库已upgrade head；API/PG/Redis恢复healthy。开发库users=0、roles=2，首位管理员留给用户设置。
- 测试：默认48 passed/24 skipped；P2独立PostgreSQL最终23 passed，含真实TCP进程重启。中途测试发现用户名规范化顺序问题已修正；日志捕获测试曾因handler在fixture阶段绑定旧stdout失败，修正测试捕获后全部通过。质量/容器最终记录见[P2验收](acceptance/P2_2026-11-10.md)。
- 文档：新增DATABASE/SECURITY/IDENTITY教学/P2验收，更新状态、路线图、架构、API、决策、启动/部署/测试与导航；保留任务前四份文档的缓存迁移修改。
- 后续：用户设置管理员；P3前确认模型/供应商/Embedding、出域与预算。登录限流、TLS、会话清理、SSO等未实现，不记为生产加固完成。

## 2026-11-10：阶段提交与 Git 历史规范化

- 根据用户授权，阶段代码、测试和文档全部完成后才提交，标题使用中文 `P阶段编号:说明`；P2 标题为 `P2:完成数据持久化、身份认证与权限基础`。
- 仓库没有远程；将原 P0 基线重命名为 `P0:完成项目规划与开发基线`，将 P1 实现、真实验收和教学三次提交合并为 `P1:完成工程底座与本地基础设施`，P2 单独提交。保留各阶段作者信息和对应原始作者时间，提交哈希因历史整理而变化。
- 将 P2 开始前已有的四份环境维护文档变更归入 P1：DEPLOYMENT、DEVELOPMENT_LOG、LEARNING_LOG、PROJECT_STATUS，共新增71行，无删除；未丢弃用户已有内容。
- 整理前的完整历史及 P2 文件快照保存为 `data/git-backups/before-phase-history-20261110-a395534f.bundle`，已通过 Git bundle verify；恢复清单位于同目录 `phase-history-20261110-a395534f.json`。这些本机备份受 data/ 忽略规则保护，不提交到仓库。
- 本轮复核：pytest 48 passed、24 skipped；Ruff、格式、strict mypy、git diff --check 通过；63个Git候选文件按本机配置秘密值检查无泄露。真实数据库23项与容器依赖1项沿用P2已记录证据，本轮没有重复执行。
- 校验重新组织后的最终文件树与整理前 P2 完整快照一致；仅随后补充本条 Git 交付记录。未修改业务逻辑、接口或数据库，未推送远程。PROJECT_STATUS、ARCHITECTURE、DATABASE、API_DESIGN 与模块教学无需因提交哈希变化修改；学习状态仍待用户练习验证。

## 2026-11-10：启动 P3 模型、会话与消息

- 新增 chat 模型/适配/service/schema、API、0002 迁移；模型环境配置和 Compose 透传；httpx2 从 dev 移为 runtime，锁文件同步。
- 普通/SSE 输出、严格私有会话、分页、并发租约、消息失败/取消、用量记录。使用短事务与异步网络避免锁/连接等待模型；断线 shield 收尾，崩溃租约恢复。
- 默认 57 passed、30 skipped；独立 PostgreSQL 的 P2/P3 29 项。真实 TCP 断线测试使用模型替身；真实供应商、Embedding、预算仍待确认。具体证据与质量检查见 acceptance/P3_2026-11-10.md。
- 已同步状态、架构、数据库、API、安全、部署配置、测试、模块教学及决策。不做中间 Git 提交；业务库与容器保持原状，P3 未标记完成。

## 2026-11-10：P3 DeepSeek 真实接入与本地升级

- 用户确认地址/模型、10 元预算与数据边界，随后自行填写 .env 密钥；只修改其余模型配置，未输出/提交密钥。
- 新增思考模式可选配置（DeepSeek 使用 disabled）、两次固定公开提示的 scripts/verify_p3_model.py、显式付费 tests/test_chat_real_model.py。数据库与客户端 API 无新增变更。
- 真实适配器 2 次和认证 API 2 次均通过；累计输入 77/输出 5 token，实际金额以供应商账单为准。认证测试使用独立 PostgreSQL，并在新应用实例读取历史；不是业务库测试账号。
- 业务库先 pg_dump 备份并检查归档目录，再构建、迁移 0002、重建 API；ready 成功。备份未恢复演练，无业务降级、删卷或默认管理员。
- Embedding 仍待选型，不提交 P3 阶段节点。验证统计与边界见 P3 验证记录的同日续验。

## 2026-11-10：Embedding 选型研究

核对百炼完整版/Flash/v4 官方价格、维度与隐私政策适用范围，读取本机内存和 nvidia-smi 资源信息；给出云端 qwen3.7-text-embedding 1024 维与本地 Qwen3-Embedding-0.6B 建议。新增 EMBEDDING_SELECTION 教学/方案，更新索引、项目状态、技术决策和学习记录。没有新增代码、数据库变更或付费调用；无业务测试需要重跑，文档链接及差异检查另行执行。实际质量、吞吐与账号隐私条款仍待验证。

## 2026-11-10：实现百炼 Embedding 接入（真实验收待配置）

用户采纳百炼北京 qwen3.7-text-embedding 1024 维、10 元、仅公开/脱敏验收，并说明约有 1M 免费 token。新增内部 embeddings/provider、独立配置与生命周期接线、Compose/.env.example、公开验收脚本、30 项协议测试；无新依赖、数据库表/迁移和公开 API。

测试覆盖索引顺序/数量/维度/模型一致性、有限非零向量、用量、归一化、大小/输入限制、超时、网络与上游错误脱敏。默认 88 passed/31 skipped，真实身份/会话回归另外执行。用户选择稍后填写 URL/密钥，--check 明确未配置，未发送任何百炼请求。实际免费额度/扣费未知，不声称验收已免费通过。教学与理解题、练习见 EMBEDDINGS.md，P3 不做中间提交。

Embedding 本轮最终验证补充：独立身份/会话回归 29 passed；Ruff/format（69 文件）/mypy（31 源文件）/语法/打包通过。API 镜像重建并 ready 成功，真实百炼仍待用户填写配置，无新迁移。

## 2026-11-10：Embedding 真验收受供应商额度限制

用户填写配置后发现 URL 仅为业务空间 ID，按官方北京格式补齐，密钥保留未输出。--check 通过，GET /models=200 且目标模型可见。两次公开文本 POST 无成功向量，诊断 403 insufficient_quota，上游提示免费额度不可用；停止继续请求，没有修改账号的仅免费/付费设置。

provider 新增有界解析 quota code，映射 503 embedding_quota_exhausted，保留原始错误正文脱敏。新增三项测试；Embedding 33 passed，默认 91 passed / 31 skipped，Ruff/mypy 通过。同步状态、模块、验收、部署和学习文档；数据库和公开 API 无变化，未做阶段提交。后续需用户恢复该模型可调用额度，再做成功路径验收。

## 2026-11-10：切换 Flash，完成 P3 阶段

用户确认改用百炼北京 qwen3.7-text-embedding-flash，保持 1024 维。更新本机 .env、.env.example、Compose 默认值、公开验收脚本模型守卫/0.125 元价格快照及协议测试。真实两次公开文本请求成功，共 115 token、5 条有效向量，两个查询匹配预期文档；准确免费抵扣/扣费未查询。

补充真实 Uvicorn 进程重启读取聊天历史验证，不新增云调用。启用独立 PostgreSQL完整回归 121 passed / 2 skipped，模型两类真实验收分开记录。容器重建配置生效并 ready。模型切换无新 schema 或 API，尚无知识库向量，无需重算。文档清理当前额度阻塞状态，保留历史失败；P3 全部任务完成，P4 尚未开始。完成最终文档/秘密扫描后按阶段节点提交。
