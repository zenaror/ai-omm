"""Optional static bearer-token guard for the Streamable HTTP MCP endpoint."""
from __future__ import annotations

import hmac
import json


class BearerTokenMiddleware:
    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in {"/mcp", "/mcp/"}:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"authorization", b"")
        prefix = b"Bearer "
        candidate = supplied[len(prefix):] if supplied[:len(prefix)].lower() == prefix.lower() else b""
        if candidate and hmac.compare_digest(candidate, self.token):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"error": "Autenticação necessária."}, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"www-authenticate", b'Bearer realm="OMM"'),
            (b"cache-control", b"no-store"),
        ]})
        await send({"type": "http.response.body", "body": body})
