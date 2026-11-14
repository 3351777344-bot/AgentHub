# P10 第十一课 · 运维、安全和恢复

## 顺着一次请求看整个系统

浏览器从 FastAPI 下载同源控制台。对外时先经 NGINX TLS 入口。浏览器把当前页内存中的 Bearer 附给 API。中间件建立请求 ID，身份模块查数据库会话和用户角色。对文档上传，API 把 UUID 命名的原始文件写入卷，同时创建记录和入库任务。Redis 只发送唤醒信号。入库 worker 领取 PostgreSQL 中的持久任务，提取纯文本、分块并调用 Embedding，最后在一个事务发布向量。

问答 API 查询属于当前用户和同一模型空间的向量，将选中的分块发给生成模型，并将可定位引用随答案保存。Agent 路径把请求 ID、模型快照和允许工具保存在 `agent_runs`，独立 worker 用 PostgreSQL advisory lock 领取。LangGraph 逐步写检查点，应用步骤表记录工具意图和结果。评测 worker 按固定数据和配置运行同一 RAG 或 Agent 逻辑，保存逐例结果和报告。原始文本、模型提示、检查点和恢复归档都需要对应的数据权限。

### 日志、指标与限流

从 [请求中间件](../../src/agenthub/core/middleware.py) 开始读。它把 `X-Request-ID` 同时放进 `request.state` 和 ContextVar，形成 HTTP 日志关联。跨进程时，[Agent API](../../src/agenthub/api/agents.py) 把 ID 明确写进运行表，[Agent worker](../../src/agenthub/agents/worker.py) 从数据库取出后再写日志。入库使用旧有任务字段，评测子运行继承批次的 ID。日志用字段白名单和稳定事件名。带有供应商原始正文的错误留在可信处理边界。

[运维 API](../../src/agenthub/api/operations.py) 把队列表按状态聚合，管理员能据此观察积压与失败。日志用来追踪特定请求，聚合指标用来发现整体偏移。若跨主机增加多个独立组件，可升级到 OpenTelemetry span 与集中日志；现阶段先使用持久 ID、数据库时间戳和业务步骤排查。一个常见错误是把 UUID 当成指标标签，导致采集端时间序列无限增长。

[登录限流](../../src/agenthub/core/rate_limit.py) 在耗费 Argon2 资源前用 Redis Lua 一次完成检查、增加计数和设置过期。所有并发 API 进程共享计数。代理只转发可信来源字段。常见错误是直接信任客户端的 `X-Forwarded-For`，或把 INCR 与 EXPIRE 分成可被中断的两次命令。固定窗口简单，会出现窗口边界突发。滑动窗口或代理层的限流可用于需要更平滑的负载。

### 发布与恢复

[Compose](../../compose.yaml) 让零退出码的迁移容器解除业务启动依赖，ready 再确认真实依赖及 schema。进程健康、可接流量与业务操作成功属于独立观察结果。升级前关入口再排空 worker，可以减少在途写入。 [停机处理](../../src/agenthub/core/shutdown.py)

[备份脚本](../../scripts/ops_backup.py) 在停写窗口导出 PostgreSQL，再归档同一窗口的原始文件。向量、身份、调用与 LangGraph 检查点进入同一个数据库归档。原文同时进入文件归档，清单记录哈希，恢复后重新读实际卷校验。大型生产环境可用 PostgreSQL PITR 加配套的版本化对象存储与一致性协议。文件恢复成功也要通过应用实读验证，避免只是 tar 能列目录。

面试可能追问 Redis 停止后已授权会话和新的登录分别发生什么，为什么数据库恢复不会撤销已经发出的外部写操作，以及如何设计带明确负载和恢复目标的验收。准备时请拿 P10 的日志、角色和检查点表举例。

## 学习检验

1. 只按 HTTP 请求 ID 过滤时，怎样找到评测批次内执行的 Agent？你会核对哪两个日志字段？
2. 停掉 Redis 后，为什么 `/health/live`、`/health/ready` 和新登录会返回不同的状态？
3. 备份恢复后准备批准一个待审批调用，你如何检查检查点、台账和外部服务是否处在可以继续的状态？

**独立代码练习**　在 [queue_metrics](../../src/agenthub/api/operations.py) 为每类任务增加 `queued` 的零值，使没有排队任务时仍返回 `queued: 0`。复用现有 SQL 聚合，在独立环境检查管理员能看到该字段且成员仍返回 403。提交你的 diff 和测试结果。

**项目思考题**　如果要求 RPO 15 分钟，但文档持续上传且每小时只能停一次写，你会怎样重构写入或存储流程，并如何证明数据库、原始文件和检查点属于同一可恢复时点？

学习状态为待回答和独立实践验证，见 [学习记录](../LEARNING_LOG.md)。
