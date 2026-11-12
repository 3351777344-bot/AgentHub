# AgentHub API 实际契约

最后更新：2026-11-12。已实现健康、身份认证、用户管理、聊天、知识库/文档/任务、RAG 检索与会话问答。

## 公共约定

- 本地地址 `http://127.0.0.1:8000`；JSON 响应。
- 当前健康接口无需认证，无请求体或查询参数。
- 业务路径前缀 `/api/v1`；除登录外均使用 `Authorization: Bearer <token>`。业务响应统一 `Cache-Control: no-store`，不设置认证 Cookie。
- 可提供 `X-Request-ID`：1—64 个 ASCII 字母、数字、点、下划线或连字符。缺失或不合规时生成 UUID 十六进制字符串；所有正常 HTTP 响应均携带该响应头。它只用于追踪，不是身份凭据。
- `/docs`、`/redoc` 和 `/openapi.json` 由 FastAPI 生成，不属于业务 API。测试专用 `/_test/*` 路由仅在测试实例中注册，不会出现在正式应用。

## 已实现端点

| 方法 | 路径 | 请求参数 | 认证 | 成功响应 | 失败响应 |
| --- | --- | --- | --- | --- | --- |
| GET | `/health/live` | 无 | 无 | 200：`{"status":"ok"}` | 意外错误 500 |
| GET | `/health/ready` | 无 | 无 | 200：见下文 | 依赖不可用 503；意外错误 500 |

`live` 仅表明 API 进程能够处理请求；不连接数据库或 Redis。`ready` 每次实际执行 PostgreSQL 查询并确认当前数据库安装了 vector 扩展，同时执行 Redis PING；两个探针并发运行，各受配置超时限制。

就绪成功：

```json
{"status":"ready","checks":{"postgresql":"ok","redis":"ok"}}
```

依赖故障示例（503；`details` 中两个依赖按实际结果分别返回 `ok` 或 `error`）：

```json
{
  "error": {
    "code": "not_ready",
    "message": "Required dependencies are unavailable",
    "request_id": "probe-42",
    "details": {"postgresql": "error", "redis": "ok"}
  }
}
```

## 统一错误响应

所有应用层错误使用 `error` 包装，包含 `code`、通用 `message`、`request_id` 和 `details` 对象。普通错误的 `details` 为空，不回显原始输入、异常文本或连接配置。HTTP 异常保留 `Allow`、`WWW-Authenticate` 等协议响应头。

| 状态码 | code | 触发条件 |
| --- | --- | --- |
| 404 | `http_404` | 路径不存在 |
| 405 | `http_405` | 方法不支持 |
| 422 | `validation_error` | FastAPI 请求参数/schema 校验失败，不回显输入 |
| 500 | `internal_error` | 响应开始前的未处理异常 |
| 503 | `not_ready` | PostgreSQL、vector 扩展或 Redis 不可用/超时 |

其他显式 HTTPException 使用 `http_<状态码>` 和标准 HTTP 状态说明。后续业务异常应设计公开错误码，不将上游异常原文直接返回。响应开始后的流式异常无法替换为 JSON 错误；当前无流式业务接口。

实现入口：[路由](../src/agenthub/api/health.py)、[错误处理](../src/agenthub/core/errors.py)、[中间件](../src/agenthub/core/middleware.py)。OpenAPI 中声明 200、422、500，以及 ready 的 503；路由不存在/方法不匹配的全局 404、405 由本文说明。

## 身份与用户端点

实现：[api/identity.py](../src/agenthub/api/identity.py)、[schemas.py](../src/agenthub/identity/schemas.py)。除路径与表中列出的参数外，不接受额外 JSON 字段。用户名先 trim 并转小写，校验 3—64 位 ASCII 名称（首位字母/数字/下划线，后续另允许点和连字符）。密码原样校验，不 trim；新密码 12—128 字符且不可全为空白，登录/旧密码 1—128 字符。

| 方法 | 路径 | 请求 | 权限 | 成功 |
| --- | --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | JSON username、password | 公开 | 200 TokenOutput |
| GET | `/api/v1/auth/me` | 无 | 已登录且账号启用 | 200 UserOutput |
| POST | `/api/v1/auth/logout` | 无 | 有效令牌 | 204，无响应体；撤销当前令牌 |
| POST | `/api/v1/auth/password` | JSON current_password、new_password | 有效令牌、旧密码正确 | 204，无响应体；撤销全部会话，需重新登录 |
| POST | `/api/v1/users` | JSON username、password、role（可选，默认 member） | admin | 201 UserOutput |
| GET | `/api/v1/users` | limit 默认20、1—100；offset 默认0、0—100000 | admin | 200 UserOutput 数组，按 created_at/id 升序 |
| GET | `/api/v1/users/{user_id}` | UUID 路径参数 | 本人或 admin | 200 UserOutput |
| PATCH | `/api/v1/users/{user_id}` | JSON role 和/或 is_active | admin，仅其他用户 | 200 UserOutput；发生变化时撤销目标全部会话 |
| GET | `/api/v1/audit-events` | limit/offset 同用户列表 | admin | 200 AuditOutput 数组，按 created_at/id 降序 |

role 只允许 `admin`、`member`；PATCH 的 is_active 必须为 JSON 布尔值，不接受字符串/整数。PATCH 至少有一个非 null 的有效修改字段；相同值不会产生变更审计或撤销会话。列表返回数组，没有总数、游标或包装对象。没有公开注册、用户硬删除、管理员改密或角色字典管理 API。

UserOutput：`id`（UUID 字符串）、`username`、`role_name`、`is_active`、`created_at`（带时区 ISO 时间）。请求字段叫 role，响应/模型字段叫 role_name；不会返回 password_hash。

TokenOutput 示例（令牌仅为占位）：

```json
{"access_token":"<仅登录成功返回的随机令牌>","token_type":"bearer","expires_at":"2026-11-10T09:00:00Z"}
```

有效期配置 `AGENTHUB_SESSION_TTL_SECONDS` 默认3600秒，绝对到期、无续期。数据库记录撤销后立即拒绝后续请求；重新启用账号不会恢复旧令牌。密码修改成功后，当前令牌也失效。

AuditOutput：`id`、`actor_id`（UUID或null）、`target_user_id`（UUID或null）、`action`、`request_id`、`created_at`。审计动作及已知范围见 [安全边界](SECURITY.md)。

## 业务错误

同样使用上文统一 error 包装；业务 details 为空。401 均带 `WWW-Authenticate: Bearer`。数据库不可用可以先于业务验证返回503；不可用时不承诺认证成功。

| 状态 | code | 条件 |
| --- | --- | --- |
| 401 | invalid_credentials | 登录用户名/密码错误、账号禁用；改密的旧密码错误 |
| 401 | invalid_session | 未带 Bearer、令牌格式错误/不存在/过期/撤销、当前用户禁用 |
| 403 | forbidden | member 操作管理 API，或读取其他用户记录（即便目标不存在也为403） |
| 404 | user_not_found | 获准访问的用户 ID 不存在 |
| 409 | username_exists | 规范化后用户名重复 |
| 409 | self_update_forbidden | 管理员 PATCH 自己的角色或状态 |
| 422 | empty_update | PATCH 中没有非 null 的 role/is_active |
| 422 | validation_error | UUID、分页、密码/用户名/角色或布尔字段不合法，或额外 JSON 字段 |
| 503 | database_unavailable | 数据库连接、SQL/锁等待或连接池超时等可用性错误 |
| 500 | internal_error | 其他意外错误，包括未完成迁移造成的编程/结构错误；不回显原文 |

CLI 的 already_initialized 是本地错误，不是公开 HTTP 接口。状态码409表示业务冲突，401表示身份凭据无效，403表示身份已知但权限不足。

## P3 聊天接口（全部需要 Bearer）

| 方法 | 路径（前缀 /api/v1） | 参数 | 成功响应 |
| --- | --- | --- | --- |
| GET | /models | 无 | model、configured、capabilities（text/streaming=true，tool_calls/structured_output=false）、max_output_tokens；不回传 URL/密钥 |
| POST | /conversations | JSON title 可选，默认新会话，1–200 字符 | 201：id/title/created_at |
| GET | /conversations | limit=20 (1–100)、offset=0 (0–100000) | 当前用户列表，created_at 倒序、id 稳定排序 |
| GET | /conversations/{id}/messages | 相同分页参数 | 消息数组，position 升序 |
| POST | /conversations/{id}/messages | JSON content 1–16000 字符；query stream=false | 普通模式 200 助手消息；stream=true 为 text/event-stream |

Message 字段：id、conversation_id、position、role、content、status、error_code、created_at。失败用户输入仍保留，助手状态 failed；成功 completed；流式断线 cancelled，部分文本在收尾事务保存，流式 delta 不逐条持久化。无自动重发、删除、修改或显式取消接口。

普通成功示例请求：`{"content":"你好"}`。私有会话即使管理员也无权跨用户读取或发送；不存在/不属于本人统一 404 conversation_not_found；未认证 401。分页/content/title 错误 422 validation_error；上下文超字符限额 422 context_limit_exceeded；已有生成 409 conversation_busy；未配置模型 503 model_not_configured；上游限流 429 model_rate_limited；超时 504 model_timeout；其他上游 HTTP 错误 502 model_upstream_error；网络错误 502 model_connection_error；不完整/非法结果 502 model_invalid_response；输出过大 502 model_output_limit；未分类生成错误 502 generation_failed。数据库故障沿用 503 database_unavailable。未配置检查发生在新增消息之前，不创建空失败消息。

SSE 每帧 `event: 名称\ndata: JSON\n\n`：start 返回 message_id；delta 返回 message_id/content 增量；done 返回最终助手 Message；error 返回 message_id/code/request_id。连接可用时 done/error 二选一为终止事件；开始响应后上游失败使用 error 事件，HTTP 已经是 200。认证/配置/所有权/并发/输入失败发生在发 SSE 头之前，使用普通 JSON 错误。上游原始正文不透传。

客户端断线后用 GET 消息列表查状态；断线本身无法再发送终止事件。进程硬退出的 pending 等待租约过期后由下一次发送标为 failed，不能自动续流。普通 JSON 请求的客户端断线不保证停止上游调用。Token 用量/耗时目前在 model_calls 中记录，没有独立查询 API；没有金额估算或费用配额。

P3 同日续验：已配置 DeepSeek deepseek-flash；thinking.type=disabled 是受控环境配置，仅影响上游请求，不改变上述客户端 API。模型/消息接口、错误和数据库 schema 本次续验均无新增。

## P4 知识库与文档 API

以下路径全部带 `/api/v1/knowledge-bases` 前缀，要求 Bearer 登录且资源归本人所有。管理员也不能读取他人知识库。越权或已删除资源返回 404；未登录/会话失效返回 401。JSON 错误沿用 error.code/message/request_id/details，响应 no-store。检索与问答契约见本页 P5。

| 方法 | 后缀 | 请求 | 成功响应 |
| --- | --- | --- | --- |
| POST | 空 | JSON name（1–200 非空白字符）、chunk_size（100–4000，默认800）、chunk_overlap（≥0且<size，默认100） | 201 KnowledgeBase |
| GET | 空 | limit/offset | 200 KnowledgeBase[]，创建时间倒序/id稳定排序 |
| GET / DELETE | /{kb_id} | UUID 路径 | 200 KnowledgeBase / 204，删除含其文档、分块和未完成任务 |
| POST | /{kb_id}/documents | multipart/form-data，file 字段；公开/脱敏的 txt/md/pdf 文件 | 202 Document，已持久化 queued 任务，尚未完成解析/向量化 |
| GET | /{kb_id}/documents | limit/offset | 200 Document[]，排除墓碑，创建时间倒序 |
| GET / DELETE | /{kb_id}/documents/{doc_id} | UUID 路径 | 200 Document / 204 |
| GET | /{kb_id}/documents/{doc_id}/source | 无 | 200 application/octet-stream，attachment 原文件，nosniff |
| POST | /{kb_id}/documents/{doc_id}/retry | 无请求体，仅 failed 可操作 | 202 Job，新版本任务；不改变原文 |
| GET | /{kb_id}/documents/{doc_id}/jobs | limit/offset | 200 Job[]，版本倒序 |
| GET | /{kb_id}/documents/{doc_id}/chunks | limit/offset | 200 Chunk[]，position 升序，未 ready 时为空 |

分页统一 limit=20，范围1–100；offset=0，范围0–100000。不返回总数，数组是实际数据；并发新增/删除期间 offset 分页没有快照一致性承诺。

KnowledgeBase：id/name/embedding_model/dimensions/chunk_size/chunk_overlap/pipeline_version/created_at。Document：id/knowledge_base_id/filename/file_type/checksum/size_bytes/status/version/chunk_count/error_code/created_at。Job：id/document_id/version/status/request_id/error_code/total_tokens/completed_batches/created_at/finished_at。Chunk：id/document_id/position/page_number/char_start/char_end/content。UUID/日期为 JSON 字符串；可空错误、用量及完成时间为 null；不暴露磁盘路径、端点、凭据和原始向量。

TXT/MD 仅 UTF-8，可带 BOM；PDF 必须有可提取文本层，不含 OCR。默认文件5 MB、提取文本20万字符、200页、500分块、解析30秒；这些环境配置在 OpenAPI 中不展开。正文请求上限为文件上限+64KiB multipart 开销，实际文件仍受5 MB限制。用户文件名不能带路径/控制字符，后缀以内容解析二次验证。

| 状态 | code | 含义 |
| --- | --- | --- |
| 404 | knowledge_base_not_found / document_not_found / document_source_missing | 不存在、无权、已删除或原文缺失 |
| 409 | duplicate_document | 同知识库已有相同 SHA-256 有效原文 |
| 409 | document_not_failed | queued/processing/ready 文档不允许重试 |
| 409 | embedding_space_mismatch | 当前模型/端点/维度/处理版本不兼容固定知识库 |
| 413 | document_size_limit / http_413 | 文件或实际请求体过大 |
| 415 | unsupported_document_type | 非 txt/md/pdf 后缀 |
| 422 | invalid_filename / empty_document / unsupported_embedding_configuration / validation_error | 文件名、空上传、非1024维/分块超过模型字符上限或请求校验错误 |
| 503 | embedding_not_configured / database_unavailable | 配置不足或数据库故障 |

异步失败不会改写上传的202响应，需查询 Document/Job：document_encoding_error、invalid_pdf、encrypted_pdf、pdf_no_text、document_page_limit/text_limit/chunk_limit/parse_timeout/parse_failed、document_checksum_mismatch、document_source_missing、owner_inactive、worker_interrupted、ingestion_failed，以及 P3 的 embedding_* 错误。只有明确失败可显式重试，重试可能重复产生部分云推理费用；超时不证明供应商未执行。删除不会撤回已发送的模型请求。

## P5 检索与会话问答

路径以 `/api/v1` 开头，使用 Bearer 登录。知识库和会话均须属于调用者，管理员遵循同一规则。所有响应使用 `Cache-Control: no-store`。

| 方法与路径 | 请求 | 200 响应 |
| --- | --- | --- |
| POST `/knowledge-bases/{kb_id}/search` | JSON `question`，可选 `top_k`、`min_similarity` | `hits`、`retrieval`。每项命中含 `source` 和分块 `content`。无命中返回空数组。 |
| POST `/conversations/{conversation_id}/rag` | JSON `knowledge_base_id` 和同样的检索参数。查询参数 `stream` 默认 false | `RagOutput`，或 `text/event-stream` |
| GET `/conversations/{conversation_id}/messages/{message_id}/rag` | `message_id` 为 RAG 助手消息 ID | `RagOutput`，历史引用的 `available` 按当前来源状态计算 |

`question` 去除首尾空白后为 1–16000 字符，还受 Embedding 输入上限约束，问答另受模型上下文预算约束。每次提交完整问题，问答生成只读取本轮证据。请求拒绝未知字段。`top_k` 为 1–20，服务默认 5。`min_similarity` 为 0–1，服务默认 0.45，边界分数可入选。可用环境变量覆盖这两个服务默认值。按余弦相似度降序，分数相同时按分块 ID 升序。

- `source` 含 `document_id`、`document_version`、`filename`、`chunk_id`、`position`、`page_number`、`char_start`、`char_end` 和 `similarity`。用 `GET /knowledge-bases/{kb_id}/documents/{doc_id}/chunks` 定位分块，或用同一文档的 `/source` 下载原文。页码为 1 起始，字符偏移是清洗后页文本的半开区间。
- `RagOutput` 含 `message`（与普通消息相同的结构）、`knowledge_base_id`、`outcome`、`citations` 和 `retrieval`。完成后 `outcome` 为 `answered` 或 `insufficient_evidence`。挂起、失败、取消时为 null。每个引用含 `label`、`source`、`available`。来源删除后历史答案保留，引用标为不可用，原文接口返回 404。
- `retrieval` 含 `embedding_ms`、`embedding_tokens`、`search_ms`、`retrieved_count`、`context_count`。阶段未执行或供应商未返回用量时相关字段为 null，纯检索的 `context_count` 为 null。没有合适分块、预算容不下分块或模型判断证据不足时，`citations=[]` 并返回证据不足的答复。
- SSE 顺序为 `start`（`message_id`）、零到多个 `delta`（`message_id`、草稿 `content`），以及 `done`（完整 `RagOutput`）或 `error`（`message_id`、`code`、`request_id`）。客户端应以 `done` 内容替换草稿，在 `error` 或断连时丢弃草稿，并可查询历史状态。发送响应头前的失败沿用 JSON HTTP 错误。

| 状态 | 主要错误码及含义 |
| --- | --- |
| 401 / 404 | `invalid_session`，`knowledge_base_not_found`、`conversation_not_found`、`rag_message_not_found` |
| 409 | `embedding_space_mismatch`（模型空间不兼容），`conversation_busy`（已有生成），`rag_source_unavailable`（证据或知识库在生成期间失效） |
| 422 | `validation_error`、`embedding_invalid_input`、`context_limit_exceeded` |
| 429 / 502 / 503 / 504 | 沿用 Embedding、生成模型及数据库错误码。引用缺失或非法为 502 `rag_invalid_citations`，RAG 结果提交失败为 503 `rag_persistence_failed`。 |
