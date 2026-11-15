"""Local-only HTTP server for the dashboard.

- Binds 127.0.0.1 only. Nothing is fetched from or sent to the network; the page loads no external
  resources (CSP ``default-src 'self'``).
- Every /api/ request needs the random per-run token from the URL the CLI opens, so other local
  apps or web pages can't read your activity.
- The Host header must be 127.0.0.1/localhost on our port (blocks DNS-rebinding pages).
- The DB is opened read-only-by-convention per request via ``data.db.open_existing`` (never creates a
  DB or key) and closed after; nothing is cached on disk.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dashboard import views

STATIC_DIR = Path(__file__).with_name("static")
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/style.css": ("style.css", "text/css")}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
Connect = Callable[[], Any]


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, connect: Connect, token: str | None = None):
        super().__init__(("127.0.0.1", port), Handler)
        self.connect = connect
        self.token = token or secrets.token_urlsafe(24)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"


class Handler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, format: str, *args: Any) -> None:  # URLs carry the token; don't print them
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"):
            return self._json(403, {"error": "bad host"})
        url = urlsplit(self.path)
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            return self._send(200, (STATIC_DIR / name).read_bytes(), ctype)
        if not url.path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not secrets.compare_digest(q.get("t", ""), self.server.token):
            return self._json(403, {"error": "missing or wrong token"})
        try:
            conn = self.server.connect()
        except Exception as e:  # missing DB/key: tell the page, don't crash the server
            return self._json(503, {"error": f"{type(e).__name__}: {e}"})
        try:
            if url.path == "/api/days":
                return self._json(200, {"days": views.list_days(conn)})
            if url.path == "/api/day":
                day = q.get("d", "")
                if len(day) != 10:
                    return self._json(400, {"error": "d=YYYY-MM-DD required"})
                return self._json(200, views.day_view(conn, day))
            if url.path == "/api/live":
                return self._json(200, views.live(conn))
            return self._json(404, {"error": "not found"})
        finally:
            conn.close()
