"""HTTP plumbing: responses, static file serving, body parsing, rate limiting."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

MAX_BODY_BYTES = 64 * 1024

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# Pretty URLs -> files inside web/
PAGE_ROUTES = {
    "/": "index.html",
    "/index": "index.html",
    "/checkout": "checkout.html",
    "/order": "order.html",
    "/orders": "order.html",
    "/admin": "admin.html",
    "/track": "order.html",
}


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "application/json; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def json_response(payload: Any, status: int = 200,
                  headers: dict[str, str] | None = None) -> Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    merged = {"Cache-Control": "no-store"}
    merged.update(headers or {})
    return Response(status, body, "application/json; charset=utf-8", merged)


def error_response(status: int, message: str, code: str | None = None,
                   headers: dict[str, str] | None = None) -> Response:
    return json_response({"error": {"message": message, "code": code}}, status, headers)


def text_response(text: str, status: int = 200,
                  content_type: str = "text/plain; charset=utf-8") -> Response:
    return Response(status, text.encode("utf-8"), content_type)


def parse_json_body(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "Request body must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ApiError(400, "Request body must be a JSON object")
    return parsed


def static_response(web_dir: Path, url_path: str) -> Response:
    """Serve a file from ``web_dir`` with traversal protection."""
    raw_path = unquote(urlparse(url_path).path)
    if raw_path in PAGE_ROUTES:
        relative = PAGE_ROUTES[raw_path]
    else:
        relative = raw_path.lstrip("/")
    if not relative:
        relative = "index.html"

    root = web_dir.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return error_response(404, "Not found", "not_found")
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return error_response(404, "Not found", "not_found")

    body = target.read_bytes()
    mime = MIME_TYPES.get(target.suffix.lower(), "application/octet-stream")
    cache = "no-cache" if target.suffix.lower() in {".html", ".js", ".css"} else "public, max-age=3600"
    return Response(200, body, mime, {"Cache-Control": cache})


class RateLimiter:
    """Fixed-window-ish limiter: keeps the last N hits per key and prunes them."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            if len(self._hits) > 4096:  # crude memory guard
                for stale_key in [k for k, v in self._hits.items() if not v]:
                    del self._hits[stale_key]
            return True

    def retry_after(self, key: str, window_seconds: float) -> int:
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return 0
            return max(1, int(window_seconds - (time.monotonic() - bucket[0])) + 1)
