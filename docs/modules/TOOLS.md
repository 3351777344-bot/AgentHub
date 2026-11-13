# 工具注册与 MCP

P6 提供版本化工具目录、一次调用和调用审计。应用内置 `add_integers@1` 和 `knowledge_search@1`。Compose 提供独立 MCP 工具 `multiply_integers`，管理员登记后可在平台调用。使用 [API 契约](../API_DESIGN.md#p6-工具与-mcp) 和 [部署说明](../DEPLOYMENT.md#p6-工具与-mcp) 复现。

## 用 Swagger 调一次本地工具

1. 执行数据库迁移，启动 API 与 `mcp-local`，用管理员 Bearer 登录 Swagger。
2. 调 `POST /api/v1/mcp/services`，提交 `{"key":"local"}`。再调 `GET /api/v1/mcp/services/local/tools`，检查 `multiply_integers` 的输入、输出和说明，复制 `definition_digest`。
3. 调 `POST /api/v1/tool-registrations`，提交以下 JSON，把 `COPY_FROM_DISCOVERY` 换成刚复制的值。

```json
{
  "name": "local_multiply",
  "version": 1,
  "service_key": "local",
  "remote_name": "multiply_integers",
  "definition_digest": "COPY_FROM_DISCOVERY",
  "description": "计算两个有界整数的乘积",
  "required_role": "member",
  "effect": "read_only",
  "timeout_seconds": 10
}
```

4. 调 `POST /api/v1/tools/local_multiply/1/calls`，提交 `{"arguments":{"left":6,"right":7}}`。响应中的 `result.total` 应为 42。`GET /api/v1/tool-calls` 可查自己的调用，也可用响应头 `X-Request-ID` 筛选失败记录。
5. `knowledge_search@1` 接收 `knowledge_base_id`、`question` 和可选的 `top_k`、`min_similarity`，返回 P5 搜索分块、来源和检索统计。用有 `ready` 文档的私有知识库和已配置的 Embedding 服务体验。

## 第七课　一次调用从 JSON 到真实执行

| 入口 | 职责 |
| --- | --- |
| [api/tools.py](../../src/agenthub/api/tools.py) | 从 Bearer 依赖拿到真实用户，区分目录、管理和执行请求，释放认证事务后才调用执行器。 |
| [tools/registry.py](../../src/agenthub/tools/registry.py) | 提供内置工具描述；将管理员审阅的 MCP 定义、参数约束、角色和副作用等级固定在一个工具版本上。 |
| [tools/executor.py](../../src/agenthub/tools/executor.py) | `start_call` 读取当前角色和注册配置，提交 `started` 审计；`execute` 检查 JSON、调处理器、校验结果；`finish_call` 写终态。 |
| [rag/search.py](../../src/agenthub/rag/search.py) | 内置工具和 P5 API 共用检索流程。先查所有权才请求 Embedding，实际 pgvector 查询再次带所有权过滤。 |
| [tools/mcp.py](../../src/agenthub/tools/mcp.py) | 用 SDK 调 `tools/list` 和 `tools/call`。每次执行前复查远端定义摘要，底层会话只发送一次工具调用。 |

工具描述告诉调用者有哪些参数和返回值。比如 `add_integers` 的 `left` 必须是有界整数；请求传 `"6"` 会在处理器运行前得到 422。JSON Schema 描述数据结构，Pydantic 再执行业务输入约束，例如去掉搜索问题两端空白、检查空字符。未来模型产生的参数也须过这条执行路径。真实身份来自服务端认证，`knowledge_base_id` 只表示调用者想访问哪个资源。

MCP 把函数调用放到另一个进程。管理员先查看远端目录并登记某一份定义；运行时再获取一次定义，发现约束变化就返回 409。调用期间的数据库连接已经释放，慢服务不会占用认证事务。进程异常中止时，已提交的 `started` 可用于排查；它也表示结果未知，不能用来推断远端是否执行过。

本地使用 Unix socket，方便让工具容器彻底脱离网络，仍可用同一套 Streamable HTTP 客户端。另一种实现是 stdio，由客户端启动工具子进程，适用于单机插件，需要单独设计可执行文件、环境变量和文件权限边界。远端连接把 HTTPS 主机名和 TCP 地址分别处理，主机名用于 TLS 验证，TCP 直接连已批准的 IP。服务端提示可辅助管理员审核，调用权限和副作用策略由平台执行。[MCP 传输规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)

常见错误包括把工具返回文本当成可信指令、将平台 Bearer 转发给 MCP、调用后才检查资源所有权、使用超时直接判定远端执行失败，以及在远端调用时持续持有数据库锁。面试时可结合 `start_call` 和 `finish_call` 解释为什么超时与副作用幂等是两个问题，再说明为什么 HTTPS URL 校验还要配合实际 TCP 地址固定。

## 当前边界

直接执行仅开放 `read_only`，显式声明写入或破坏性的远端工具也无法登记成只读。P7 允许管理员为 `write` 或 `high` 版本单独启用 Agent 执行，每次调用需要运行所有者审批。管理员须审核服务的实际行为；服务端可以保持 schema 不变而修改实现。支持 2026-07-28、结构化 JSON 对象和受控 JSON Schema 子集。 [Agent 模块](AGENTS.md) · [安全说明](../SECURITY.md#p6-工具与-mcp-信任边界)

## 学习检验

1. 为什么 `knowledge_search` 需要在请求 Embedding 之前验证所有权，而且 SQL 查询还要再次过滤？
2. 如果 MCP 工具把 `left.maximum` 从 10 改成 20，同一个工具版本在哪一步返回 409？怎样发布新版本？
3. 一条审计一直是 `started`。你能判断 MCP 是否已执行吗？你会查哪些信息？

**代码练习**　给内置 `add_integers` 收紧右操作数上限，在 `tests/test_tools_integration.py` 加一条边界断言，验证目录 schema 和实际执行都体现同一约束。

**项目思考**　P7 审批了一个写工具，工具已执行，API 在保存结果前崩溃。你会怎样设计幂等键、审批绑定和恢复策略？
