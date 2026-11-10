# 模型与会话模块：第四课

日期：2026-11-10。状态：P3 基础代码与测试交付，DeepSeek 真实模型及认证 API 验收通过，Embedding Flash 已真实验收，P3 已完成。

## 职责与入口

- `src/agenthub/chat/provider.py`：ChatProvider.generate 将文本请求转换为兼容 Chat Completions HTTP 协议，解析普通 JSON 或 SSE；Chunk 只承载增量与可选用量。协议没有绑定具体模型供应商。
- `src/agenthub/chat/models.py`：Conversation、Message、ModelCall 持久化业务会话、消息状态与模型调用元数据。
- `src/agenthub/chat/service.py`：owned 校验归属；prepare 准备消息、上下文及租约；finish 持久化终态。
- `src/agenthub/api/chat.py`：认证、分页、普通输出与 SSE，execute 负责总时限、错误转换和取消收尾。
- `src/agenthub/main.py`：生命周期创建/关闭共享异步 HTTP 客户端，连接可复用；退出时释放资源。

依赖 P2 的 CurrentPrincipal、SQLAlchemy Session、P1 的配置/错误/线程池；无需 Redis，也没有 worker、LangGraph 或前端。适配器只承诺文本/流式，工具调用与结构化输出明确声明 false，不能拿这个接口代替后续 Agent 工具协议。

## 如何配置

在被 Git 忽略的本机 `.env` 设置 AGENTHUB_MODEL_BASE_URL、AGENTHUB_MODEL_NAME、AGENTHUB_MODEL_API_KEY（服务不要求密钥时可空）。base URL 已包含供应商要求的版本前缀，例如 `/v1`，代码只追加 `/chat/completions`。不在聊天或文档填写真实密钥。已选定 https://api.deepseek.com 与 deepseek-flash，官方文档与本机真实调用均已核验。

其他选项见 `.env.example`：总时限默认 60 秒、输出 max_tokens 默认 1024、上下文字符上限默认 24000；model_include_usage 默认 false，只有服务确认支持 stream_options.include_usage 后开启。超出上下文上限明确拒绝，字符限制并非模型 token 窗口的准确换算。Embedding 为百炼北京 qwen3.7-text-embedding-flash 1024 维，独立接入见 EMBEDDINGS.md，聊天模块不调用 Embedding。

配置后按 DATABASE.md 先备份并应用迁移 0002，再重建并启动 API。业务库已备份后升级 0002，业务容器已重建并确认模型配置与 ready。Swagger 登录后创建会话，再向 messages POST；流式请求用能携带 Authorization 的 fetch 客户端读取 SSE。原生 EventSource 不便直接附加 Bearer，也不支持本接口的 POST 请求体。

## 真实代码的执行过程

1. send_message 通过 P2 验证 Bearer，取 owner_id，回滚认证读取事务以释放连接。
2. 在线程池执行 prepare：owned 的查询同时过滤会话 ID 和 owner_id，再锁会话行。同一会话未过期的 busy_until 会返回 409。
3. prepare 用 position 排序组装此前成功的 user/assistant 对；失败回答对应的用户消息留在历史供查看，但不重复进入提示词。超出字符上限在写入新消息前拒绝。
4. 同一事务写入 completed 用户消息、pending 助手消息、ModelCall，分配 position 并设置模型总时限 + 30 秒租约后 commit。
5. execute 异步等待 ChatProvider.generate，网络等待期间不占用数据库连接。普通 JSON 完成后返回最终 Message；SSE 依次发 start、delta，最终 done 或 error。
6. finally 在线程池执行 finish，shield 防止断线取消打断短暂的落库过程。成功 completed；上游故障 failed；流式取消 cancelled，尽可能保存部分内容及可获得用量。数据库本身不可用仍可能收尾失败，不能承诺绝对落库。
7. 硬退出没有 finally：pending 在下次发送、租约过期后被标记 generation_interrupted。finish 只修改仍 pending 的消息，因此迟到的旧结果不会覆盖新生成或清除新租约。只读查询本身不会触发恢复。

## 为什么这样设计与替代方案

HTTP 客户端采用已在项目锁文件中的 httpx2 2.16.0，由开发依赖移入运行依赖，没有新增 SDK 体系。小适配器将供应商协议与业务持久化分离；未来可以增加 SDK 适配器或 Provider Protocol，当前没有必要构造多供应商注册框架。

prepare/finish 两段短事务避免慢模型长期占用连接池；会话行锁加租约支持多 API 进程共享的数据库一致性。可以用任务队列/执行记录状态机做断线后继续生成和可恢复事件流，但需要独立 worker、事件保存及权限设计，当前选择断线取消并保留可查询状态。只用 Python 内存锁在多进程间不起作用；把网络调用放在行锁事务里则会耗尽连接。

自动重试次数为 0：不确定上游是否已经开始计费的 POST 不透明重发。将来可以对明确可重试的错误设计总预算、退避和幂等策略。未知 token 用量存 NULL，不能当作免费或 0 token；没有单价数据不估算费用。

## 常见错误与面试考察

- SSE 响应开始后再抛普通 HTTP 异常，无法改变已经发送的 200；应发 error 终止事件。面试会问 HTTP 状态与业务流终态如何区分。
- 把网络 EOF 或孤立 [DONE] 当成功，会把半截回答标为 completed；适配器要求正常 finish_reason 与 [DONE]。
- 只校验登录而不在 owned 中过滤 owner_id，会造成 UUID 越权；管理员的用户管理权限不自动等于会话读取权限。
- 没有 finally/shield 会留下永久占用；仅有 finally 又无法处理 kill -9，因此还需要租约。面试常追问优雅取消与进程崩溃的区别。
- 让 SQLAlchemy 同步查询在 async 路由直接执行，会阻塞事件循环；这里使用 run_in_threadpool。面试常追问异步 I/O 与数据库事务边界。
- 将字符数当 token 数或把缺失用量补 0，都会使预算判断失真。

## 限制和验证边界

仅环境配置一个模型、纯文本、POST SSE，无删除/编辑/显式取消/断点续流/自动重试、全局并发/金额限额、精确 token 预算。普通非流式请求断线不保证中止生成。部分内容在收尾保存，强制杀进程可能丢失未提交的增量。业务会话重启读取用第二个应用实例验证；DeepSeek 真实模型与本地容器升级已验收，完整产品链路仍未完成。

测试明确区分：provider 的 MockTransport 协议/异常；真实 PostgreSQL 的权限/并发/持久化；真实 TCP 客户端断开后的取消落库。这两类的模型仍为替身；供应商兼容性由另外显式开启的真实模型脚本和认证 API 测试证明。

## 学习检验（待回答，未标记掌握）

1. prepare 为什么必须先 commit 再等待模型？只把路由改成 async def 能解决连接池占满吗？
2. 为什么 SSE 的 HTTP 200 后还需要 error 事件？浏览器断线后从哪里确认消息状态？
3. 已有 finally，为什么仍需要 busy_until？旧生成迟到时 finish 如何保护新生成？

代码练习：为上下文字符上限增加边界测试，验证恰好达到上限成功、超出上限返回 422，并确认拒绝时不增加消息或调用记录。先自行尝试。

项目思考：P7 如果要求浏览器断线后 Agent 继续调用工具，哪些执行逻辑必须从本请求生命周期移出？事件与工具副作用怎样避免恢复时重复执行？先给出你的设计，不急着写框架。

## DeepSeek 配置补充与教学

新增 AGENTHUB_MODEL_THINKING_MODE：provider_default（通用默认，不发送扩展参数）或 disabled（发送 thinking.type=disabled）。DeepSeek 默认会思考，本轮基础文本模式关闭思考，以免小 max_tokens 额度消耗在不可见思考上。model_include_usage=true 让流式末尾返回用量；这与“每个增量都有 token 数”不同。没有将 DeepSeek 地址硬编码到业务适配器；专用验收脚本则固定检查获准的地址/模型，避免发错服务。

真实验证入口：scripts/verify_p3_model.py 固定公开提示、最多两次 POST、每次输出最多 128 token；tests/test_chat_real_model.py 额外两次认证 API 调用并检查用量与新应用历史读取，需要显式开启付费测试标志。10 元为用户授权的本轮人工验收预算，不是已实现的平台金额配额；实际计费以供应商账单为准。本轮只有四次调用，记录 77 输入/5 输出 token。

禁止发送个人隐私、真实账号密码/API Key、未经授权商业机密和不适合第三方的原始文件。认证凭据只放 HTTP 认证头，应用不会将它们拼入 messages。这个原则并不代表已实现自动隐私检测，操作人仍须确保输入符合授权。

补充理解题：为什么兼容 OpenAI 格式仍可能需要 thinking 这样的供应商扩展？修改练习：为 disabled 模式补充不支持该参数的供应商错误测试。原三道理解题、上下文练习和 Agent 思考题继续待完成，不标记掌握。
