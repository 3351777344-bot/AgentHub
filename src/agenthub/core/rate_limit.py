"""Atomic login rate limit shared by all API processes on the stack."""

from hashlib import sha256

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from agenthub.core.errors import error_response

WINDOW = 60
INCREMENT = """
local count = tonumber(redis.call('GET', KEYS[1]) or '0')
if count >= tonumber(ARGV[1]) then
    return math.max(1, redis.call('TTL', KEYS[1]))
end
if redis.call('INCR', KEYS[1]) == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


class LoginLimitMiddleware:
    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != "/api/v1/auth/login"
        ):
            await self.app(scope, receive, send)
            return
        # ASGI supplies the peer. The TLS ingress replaces forwarded headers and
        # Uvicorn accepts them only from the configured ingress address.
        peer = scope.get("client")
        key = sha256((peer[0] if peer else "unknown").encode()).hexdigest()
        redis: Redis = scope["app"].state.dependencies.redis
        request_id = scope["state"]["request_id"]
        try:
            retry_after = await redis.eval(
                INCREMENT, 1, f"agenthub:auth:login:v1:{key}", self.limit, WINDOW
            )
        except RedisError:
            response = error_response(
                503, "login_limit_unavailable", "Login temporarily unavailable", request_id
            )
            await response(scope, receive, send)
            return
        if retry_after:
            response = error_response(
                429,
                "login_rate_limited",
                "Too many login attempts",
                request_id,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class SameOriginMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None and origin != f"{scope['scheme']}://{headers.get('host', '')}":
                response = error_response(
                    403,
                    "origin_forbidden",
                    "Cross-origin request refused",
                    scope["state"]["request_id"],
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
