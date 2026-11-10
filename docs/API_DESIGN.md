# AgentHub API 实际契约

最后更新：2026-11-10。仅记录当前实现；健康、身份认证和用户管理已实现，知识库和 Agent API 尚未实现。

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
