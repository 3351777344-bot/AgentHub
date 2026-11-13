# Agent 与 LangGraph 工作流

P7 将 P3 模型、P5 私有检索和 P6 工具接入一个固定版本的工作流。用户创建私有 Agent，指定部署已配置的模型、系统提示、知识库、工具版本和三项运行预算。API 接收运行后返回 202，由独立 `agent-worker` 推进。

## 从 Swagger 运行一次

1. 按部署文档应用迁移并启动 API 与 `agent-worker`，在 Swagger 通过 Bearer 登录。选用已配置的模型名称。 [部署](../DEPLOYMENT.md#p7-agent-worker)
2. 用 `POST /api/v1/agents` 创建配置。最小工具示例是 `{"name":"计算助手","model_name":"deepseek-flash","system_prompt":"先用授权工具计算，再回答。","tools":[{"name":"add_integers","version":1}]}`。知识库工作流可另加 `knowledge_search@1` 和自己的 `knowledge_base_ids`。
3. 用 `POST /api/v1/agents/{agent_id}/runs` 提交 `{"prompt":"请调用工具求 23 加 19。"}`。读取 `GET /api/v1/agent-runs/{run_id}` 与同路径下的 `/steps` 查看最终答案和步骤。事件流可用支持 Bearer 的客户端读取，重连时传 `Last-Event-ID`。 [API 契约](../API_DESIGN.md#p7-agent-与工作流)
4. 对 `waiting_approval`，用运行返回的 `approval_id` 查看登记版本、影响等级和完整参数。确认后提交该审批的 `digest` 与 `approve` 或 `reject`。拒绝会作为工具结果反馈给模型。

## 第八课　沿一次运行读代码

| 入口 | 执行过程 |
| --- | --- |
| [api/agents.py](../../src/agenthub/api/agents.py) 与 [agents/service.py](../../src/agenthub/agents/service.py) | 认证后过滤资源所有者，创建运行时快照化 Agent 配置。运行 ID 标识 API 资源，可选会话 ID 只作归属与关联，独立生成的图线程 ID 索引检查点。 |
| [agents/worker.py](../../src/agenthub/agents/worker.py) | 从 PostgreSQL 读取待运行项，用会话级 advisory lock 排除并发领取。锁和 checkpointer 共用一条连接。API 查询、审批及步骤状态只占用短业务事务。 |
| [agents/workflow.py](../../src/agenthub/agents/workflow.py) | `model` 生成最终答案或一批调用，条件边选择结束或进入 `plan`。`plan` 校验调用并路由到 `approval` 或 `tool`。工具节点逐个处理调用，追加反馈，再回到模型。 |
| [agents/provider.py](../../src/agenthub/agents/provider.py) | 用真实 Chat Completions `tools`、`tool_calls` 和 `tool_call_id` 对话；检查返回的用量及协议结构。DeepSeek 官方端点的 Agent 调用使用非思考模式。 |
| [agents/ledger.py](../../src/agenthub/agents/ledger.py) 与 [tools/executor.py](../../src/agenthub/tools/executor.py) | 每个模型或工具尝试占一个顺序号。调用前持久化意图，完成后保存结果。工具重用角色检查、远端指纹、JSON Schema、RAG 与调用审计。 |

`State.messages` 用 `operator.add` 作为 reducer，节点只返回要追加的消息。其余状态字段由最新节点更新，例如 `ordinal` 和 `pending`。当工具有写入影响时，`plan` 先保存审批，随后独立的 `approval` 节点调用 `interrupt()`。恢复时 LangGraph 从节点开头执行，`Command(resume=...)` 向它提交已经持久化的决定；执行前账本再次校验归属、摘要和计划，避免在可重放节点里重复创建审批。 [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

检查点保留图状态，步骤账本保留外部调用结果。假设远端已返回 42、账本已提交，worker 随后在写图检查点前退出，恢复可直接重用账本里的 42。若远端已收到写入而账本只有 `started`，运行进入结果未知，原步骤禁止重放。支持幂等键的远端可在未来扩展查询和安全重试协议。只存聊天历史很难知道该从哪个节点继续，手写状态机则需要自行维护检查点和重放规则。 [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

常见错误包括直接执行模型返回的工具名、仅在 UI 中校验审批、每次重连都重新发起运行、把模型超时理解为工具端撤销、在网络请求期间持有数据库行锁。面试可沿两个事务讲解：为什么 PostgreSQL 的持久化检查点和远端 HTTPS 写入无法放进同一个本地事务，以及系统如何处理收到响应前、账本提交前和检查点写入前的三种中断。

## 当前边界

- 每次运行使用固定工作流 v1 和独立上下文。会话 ID 只用于业务关联，Agent 不读取或追加普通聊天历史。Agent 的来源提示为模型输出中的文件名和分块 ID，P5 单独提供结构化、校验后的引用。
- 时间预算累计 worker 执行段，主动排队、暂停、等待审批不计入。硬退出到回收间隔会计入原执行段。步数涵盖模型和工具尝试，包括被拒绝的请求。输出 token 预算按模型报告的 completion tokens 累计，并传剩余上限；输入 token 单独记录。Embedding 和金额无此预算。
- 运行暂停在下一个外部步骤边界生效。取消会保存终态并请求停止本地执行，已发出的远端调用可能继续。结果未知的步骤需结合工具审计和远端记录处理。
- P7 支持一个已部署模型和固定模板。检查点、步骤参数、结果和答案可能含原文，持久化保留与清理属于部署治理。管理员默认关闭有副作用的工具，启用后每次仍需审批。 [安全边界](../SECURITY.md#p7-agent-权限与审批)

## 学习检验

1. 为什么 `run_id`、`conversation_id`、`thread_id` 各自独立？SSE 续读使用哪个 ID 和哪个游标？
2. `plan` 和 `approval` 为什么分开？审批拒绝后，下一次模型请求收到什么？
3. 账本有 `completed` 和只有 `started` 时，worker 各会怎样处理？

**代码练习**　给图新增显式的拒绝处理分支，保留当前 `tool_call_id` 的反馈规则，再用 `test_approval_resume_against_real_mcp` 验证拒绝分支没有发出写调用。

**项目思考**　假如要允许重新提交一次退款写操作，MCP 服务应提供哪些去重和状态查询能力？审批、运行账本和远端幂等键分别保存哪些字段？
