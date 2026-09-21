#!/usr/bin/env python3
"""BGMI UC store - zero-dependency web server.

Run it::

    python server.py                 # http://127.0.0.1:8080
    python server.py --port 9000
    python server.py --set-admin-password

Standard library only.  Optional: ``qrcode`` + ``Pillow`` for live UPI QR codes
(falls back to your own static QR image when they are not installed).
"""

from __future__ import annotations

import argparse
import getpass
import sys
import threading
import time
import traceback
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import auth
from app.config import DATA_DIR, WEB_DIR, Config, load_config
from app.db import Database, iso
from app.http_utils import (
    ApiError,
    Response,
    RateLimiter,
    error_response,
    json_response,
    parse_json_body,
)
from app.routes import Ctx, handle

MAX_BODY_BYTES = 64 * 1024

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
}


class StoreHandler(BaseHTTPRequestHandler):
    server_version = "UCStore/1.0"
    protocol_version = "HTTP/1.1"

    config: Config
    db: Database
    limiter: RateLimiter
    quiet: bool = False

    # -- HTTP verbs ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(
            Response(
                204,
                b"",
                "text/plain; charset=utf-8",
                {"Allow": "GET, HEAD, POST, OPTIONS"},
            )
        )

    # -- internals -------------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        try:
            response = self._build_response(method)
        except Exception:  # pragma: no cover - last-resort safety net
            self.log_error("Unhandled error:\n%s", traceback.format_exc())
            response = error_response(500, "Internal server error", "internal_error")
        self._send(response)

    def _build_response(self, method: str) -> Response:
        parsed = urlparse(self.path)
        path = parsed.path or "/"

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return error_response(400, "Invalid Content-Length", "bad_request")
        if length > MAX_BODY_BYTES:
            return error_response(413, "Request body too large", "payload_too_large")

        raw_body = self.rfile.read(length) if length > 0 else b""

        cookies: dict[str, str] = {}
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            jar = SimpleCookie()
            try:
                jar.load(cookie_header)
            except Exception:
                jar = SimpleCookie()
            cookies = {key: morsel.value for key, morsel in jar.items()}

        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        headers = {key.lower(): value for key, value in self.headers.items()}

        ctx_method = "GET" if method == "HEAD" else method
        ctx = Ctx(
            config=self.config,
            db=self.db,
            limiter=self.limiter,
            method=ctx_method,
            path=path,
            query=query,
            headers=headers,
            cookies=cookies,
            body={},
            client_ip=self.client_address[0] if self.client_address else "0.0.0.0",
        )

        is_api = path.startswith("/api/")
        if is_api and ctx_method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                ctx.body = parse_json_body(raw_body)
            except ApiError as exc:
                return error_response(exc.status, exc.message, exc.code)

        try:
            response = handle(ctx)
        except ApiError as exc:
            return error_response(exc.status, exc.message, exc.code)

        if method == "HEAD":
            response.body = b""
        return response

    def _send(self, response: Response) -> None:
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            payload_headers = {key.lower() for key in response.headers}
            for key, value in SECURITY_HEADERS.items():
                if key.lower() not in payload_headers:
                    self.send_header(key, value)
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, fmt: str, *args) -> None:  # keep the console readable
        if not self.quiet:
            sys.stderr.write(
                "%s - %s\n" % (time.strftime("%H:%M:%S"), fmt % args)
            )


class StoreServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def expiry_worker(db: Database, stop: threading.Event, interval: float = 30.0) -> None:
    """Background sweep: mark unpaid orders as expired once their window closes."""
    while not stop.wait(interval):
        try:
            for row in db.orders_needing_expiry():
                db.execute(
                    "UPDATE orders SET status = 'expired', updated_at = ? "
                    "WHERE id = ? AND status = 'pending_payment'",
                    [iso(), row["id"]],
                )
                db.add_event(row["id"], "expired", "Payment window closed")
        except Exception:  # pragma: no cover - never kill the worker
            traceback.print_exc()


def build_server(config: Config, db: Database, host: str, port: int, quiet: bool = False) -> StoreServer:
    StoreHandler.config = config
    StoreHandler.db = db
    StoreHandler.limiter = RateLimiter()
    StoreHandler.quiet = quiet
    server = StoreServer((host, port), StoreHandler)
    server.daemon_threads = True
    return server


def prepare(config: Config, db_path: Path | None = None) -> Database:
    db = Database(db_path or config.db_path)
    db.init()
    seeded = auth.ensure_admin_seed(
        db,
        str(config.admin.get("username", "anmol7050")),
        str(config.admin.get("password", "oldisgold")),
    )
    if seeded:
        print(
            "[setup] Admin account created with username "
            f"'{config.admin.get('username', 'admin')}'."
        )
    auth.purge_sessions(db)
    return db


def set_admin_password(config: Config, db: Database, password: str | None) -> int:
    if not password:
        password = getpass.getpass("New admin password: ")
        confirm = getpass.getpass("Repeat password: ")
        if password != confirm:
            print("Passwords did not match. Nothing changed.", file=sys.stderr)
            return 1
    if len(password) < 8:
        print("Use at least 8 characters for the admin password.", file=sys.stderr)
        return 1
    username = str(config.admin.get("username", "admin"))
    auth.set_admin_password(db, username, password)
    print(f"Admin password updated for '{username}'. All sessions were signed out.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the BGMI UC store.")
    parser.add_argument("--host", help="Interface to bind (default from config.json)")
    parser.add_argument("--port", type=int, help="Port to bind (default from config.json)")
    parser.add_argument("--db", help="Override the SQLite database path")
    parser.add_argument("--open", action="store_true", help="Open the store in a browser")
    parser.add_argument("--quiet", action="store_true", help="Do not log every request")
    parser.add_argument(
        "--set-admin-password",
        nargs="?",
        const="",
        default=None,
        help="Change the admin password (prompts when no value is given)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()

    if not WEB_DIR.is_dir():
        print(f"Frontend folder missing: {WEB_DIR}", file=sys.stderr)
        return 1

    db_path = Path(args.db) if args.db else None
    db = prepare(config, db_path)

    if args.set_admin_password is not None:
        return set_admin_password(config, db, args.set_admin_password or None)

    host = args.host or str(config.server.get("host", "127.0.0.1"))
    port = int(args.port or config.server.get("port", 8080))

    for warning in config.warnings:
        print(f"[warn] {warning}")

    server = build_server(config, db, host, port, quiet=args.quiet)
    stop = threading.Event()
    worker = threading.Thread(target=expiry_worker, args=(db, stop), daemon=True)
    worker.start()

    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{port}/"
    print(f"\n  {config.site.get('name', 'UC Store')} is live")
    print(f"  Store     : {url}")
    print(f"  Admin     : {url}admin")
    print(f"  Database  : {db.path}")
    print(f"  UPI ID    : {config.payment.get('upi_id')}")
    print("  Stop with Ctrl+C\n")

    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        db.close_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
