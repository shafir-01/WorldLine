"""Edge concerns for the public verifier, as one pure-ASGI middleware.

Order of work per HTTP request:

1. Request id: honour a sane ``X-Request-ID`` from the caller, otherwise generate one; echo it
   on the response and put it in the log line.
2. Rate limit: ``POST /api/*`` (and ``GET /api/verify/release``) draws from an in-memory token bucket per client IP and gets
   ``429`` with ``Retry-After`` when the bucket is empty.
3. Response headers: security headers on everything (CSP, nosniff, referrer, permissions,
   HSTS when the request arrived over https) and a ``Cache-Control`` chosen by path.
4. One structured JSON log line (method, path, status, latency, request id, client ip).
   Bodies and query strings are never logged.

Pure ASGI (not BaseHTTPMiddleware) so request bodies keep streaming and the 5 MB cap in the
handler still sees the raw stream.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

ACCESS_LOGGER = "worldline.access"
REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._:@/+=-]{1,128}$")

# ---------------------------------------------------------------------------------- logging


class JsonFormatter(logging.Formatter):
    """One JSON object per line, in the shape Cloud Logging parses (``severity``, ``message``,
    ``httpRequest``). Extra structured fields ride on ``record.json_fields``."""

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "json_fields", None)
        if isinstance(fields, dict):
            doc.update(fields)
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc, separators=(",", ":"), default=str)


_logging_configured = False


def configure_logging() -> None:
    """Send the service's own log lines and uvicorn's error log to stdout as JSON.

    Idempotent. Uvicorn's access log is meant to be disabled (``--no-access-log``); this
    middleware writes the access line instead."""
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True
    level = os.environ.get("WORLDLINE_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    for name in ("worldline", "uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.setLevel(level)
        lg.propagate = False
    # In case someone forgets --no-access-log: keep it quiet rather than double-logging.
    logging.getLogger("uvicorn.access").disabled = True


# ------------------------------------------------------------------------------ rate limit


class TokenBucket:
    """Per-key token buckets: ``capacity`` tokens, refilled at ``capacity / period_s`` per second.

    In-memory and per process: with N instances x W workers the effective ceiling is N*W times
    the configured rate. That is fine for abuse control on a stateless public verifier; it is not
    a quota system."""

    def __init__(self, capacity: int, period_s: float, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity = float(capacity)
        self.rate = capacity / period_s
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """Take one token. Returns 0.0 when allowed, else the seconds until a token is available."""
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                if len(self._buckets) > self.max_keys:
                    self._prune(now)
                return 0.0
            self._buckets[key] = (tokens, now)
            return (1.0 - tokens) / self.rate

    def _prune(self, now: float) -> None:
        # Drop buckets that would be full again anyway; if that is not enough, drop the oldest.
        full_after = self.capacity / self.rate
        for k in [k for k, (_, last) in self._buckets.items() if now - last >= full_after]:
            del self._buckets[k]
        if len(self._buckets) > self.max_keys:
            for k, _ in sorted(self._buckets.items(), key=lambda kv: kv[1][1])[: len(self._buckets) - self.max_keys]:
                del self._buckets[k]

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# -------------------------------------------------------------------------------- headers

# The viewer: own scripts only, Google Fonts for CSS + font files, same-origin fetches only.
# 'unsafe-inline' for styles covers React style attributes; scripts stay strictly 'self'.
VIEWER_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
# /api/docs is FastAPI's stock Swagger UI: assets from jsDelivr plus one inline bootstrap script.
DOCS_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.jsdelivr.net",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
])
STATIC_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")

CACHE_IMMUTABLE = b"public, max-age=31536000, immutable"
CACHE_DATA = b"public, max-age=60"
CACHE_REVALIDATE = b"no-cache"
CACHE_NO_STORE = b"no-store"


def cache_control_for(path: str) -> bytes | None:
    if path.startswith("/api/"):
        # /api/evidence carries an ETag; "no-cache" lets clients revalidate it (304) instead of
        # refetching. Everything else under /api is per-request and must not be stored.
        return CACHE_REVALIDATE if path == "/api/evidence" else CACHE_NO_STORE
    if path.startswith("/assets/"):
        return CACHE_IMMUTABLE  # Vite content-hashed file names
    if path.startswith("/data/") and path.endswith(".json"):
        return CACHE_DATA
    if path == "/" or path.endswith(".html"):
        return CACHE_REVALIDATE
    return None


def _is_https(scope: Scope, headers: dict[bytes, bytes]) -> bool:
    if scope.get("scheme") == "https":
        return True
    proto = headers.get(b"x-forwarded-proto", b"").split(b",")[0].strip().lower()
    return proto == b"https"


# ----------------------------------------------------------------------------- middleware


class EdgeMiddleware:
    def __init__(self, app: ASGIApp, *, limiter: TokenBucket, trusted_proxy_hops: int = 1,
                 docs_paths: tuple[str, ...] = ("/api/docs",),
                 rate_limited_get_prefix: str = "/api/verify/release") -> None:
        self.app = app
        self.limiter = limiter
        self.trusted_proxy_hops = max(0, trusted_proxy_hops)
        self.docs_paths = docs_paths
        # GET /api/verify/release triggers outbound fetches, so it draws from the same bucket.
        self.rate_limited_get_prefix = rate_limited_get_prefix
        self.log = logging.getLogger(ACCESS_LOGGER)

    def client_ip(self, scope: Scope, headers: dict[bytes, bytes]) -> str:
        """The client address as seen by the last trusted proxy.

        Cloud Run's front end appends the caller's address to X-Forwarded-For, so the entry
        ``trusted_proxy_hops`` from the right is the one a client cannot forge. (Uvicorn's own
        ``--proxy-headers`` with ``*`` takes the leftmost entry, which a client controls.)"""
        xff = headers.get(b"x-forwarded-for")
        if xff and self.trusted_proxy_hops:
            hops = [h.strip() for h in xff.decode("latin-1").split(",") if h.strip()]
            if hops:
                return hops[-min(self.trusted_proxy_hops, len(hops))]
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        incoming = headers.get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1").strip()
        request_id = incoming if _REQUEST_ID_OK.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        ip = self.client_ip(scope, headers)
        https = _is_https(scope, headers)
        status_holder = {"status": 500}

        csp = DOCS_CSP if path in self.docs_paths else VIEWER_CSP
        cache = cache_control_for(path)

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = list(message.get("headers", []))
                present = {k.lower() for k, _ in raw}
                raw.append((b"x-request-id", request_id.encode("latin-1")))
                raw.append((b"content-security-policy", csp.encode()))
                raw.extend(h for h in STATIC_HEADERS if h[0] not in present)
                if https:
                    raw.append(HSTS)
                if cache is not None and (b"cache-control" not in present or path.startswith("/api/")):
                    raw = [(k, v) for k, v in raw if k.lower() != b"cache-control"]
                    raw.append((b"cache-control", cache))
                message = {**message, "headers": raw}
            await send(message)

        try:
            if (method == "POST" and path.startswith("/api/")) or path.startswith(self.rate_limited_get_prefix):
                wait = self.limiter.take(ip)
                if wait > 0:
                    retry = str(max(1, math.ceil(wait)))
                    body = json.dumps({"error": f"rate limit exceeded; retry in {retry}s"}).encode()
                    await send_wrapper({"type": "http.response.start", "status": 429, "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", retry.encode()),
                    ]})
                    await send_wrapper({"type": "http.response.body", "body": body})
                    return
            await self.app(scope, receive, send_wrapper)
        finally:
            status = status_holder["status"]
            latency = time.perf_counter() - start
            ua = headers.get(b"user-agent", b"").decode("latin-1")[:256]
            if status >= 500:
                level = logging.ERROR
            elif status in (408, 413, 429):
                level = logging.WARNING
            elif path in ("/api/healthz", "/api/readyz"):
                level = logging.DEBUG  # probe traffic: keep it out of the default log stream
            else:
                level = logging.INFO
            self.log.log(
                level,
                "%s %s %d %.1fms", method, path, status, latency * 1000,
                extra={"json_fields": {
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status": status,
                    "latency_ms": round(latency * 1000, 2),
                    "client_ip": ip,
                    # Cloud Logging's HttpRequest shape, so the console shows method/status/latency.
                    "httpRequest": {
                        "requestMethod": method,
                        "requestUrl": path,
                        "status": status,
                        "latency": f"{latency:.6f}s",
                        "remoteIp": ip,
                        "userAgent": ua,
                    },
                }},
            )


class PathScopedCORS:
    """Starlette's CORSMiddleware, applied only to ``/api/*``; the viewer stays same-origin."""

    def __init__(self, app: ASGIApp, prefix: str = "/api/", **cors_kwargs: Any) -> None:
        from starlette.middleware.cors import CORSMiddleware

        self.app = app
        self.cors_app = CORSMiddleware(app, **cors_kwargs)
        self.prefix = prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith(self.prefix):
            await self.cors_app(scope, receive, send)
        else:
            await self.app(scope, receive, send)
