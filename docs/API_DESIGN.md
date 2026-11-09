# AgentHub API 实际契约

最后更新：2026-11-09。仅记录当前实现；认证、知识库和 Agent API 尚未实现。

## 公共约定

- 本地地址 `http://127.0.0.1:8000`；JSON 响应。
- 当前健康接口无需认证，无请求体或查询参数。
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
| 422 | `validation_error` | FastAPI 请求校验失败；当前健康端点无可校验业务参数 |
| 500 | `internal_error` | 响应开始前的未处理异常 |
| 503 | `not_ready` | PostgreSQL、vector 扩展或 Redis 不可用/超时 |

其他显式 HTTPException 使用 `http_<状态码>` 和标准 HTTP 状态说明。后续业务异常应设计公开错误码，不将上游异常原文直接返回。响应开始后的流式异常无法替换为 JSON 错误；当前无流式业务接口。

实现入口：[路由](../src/agenthub/api/health.py)、[错误处理](../src/agenthub/core/errors.py)、[中间件](../src/agenthub/core/middleware.py)。OpenAPI 中声明 200、422、500，以及 ready 的 503；路由不存在/方法不匹配的全局 404、405 由本文说明。
