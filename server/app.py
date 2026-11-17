#!/usr/bin/env python3
"""Local web server for the standalone research/report timeline.

Standard library only. Serves one page and a small JSON API; starts a
background scheduler for public research APIs.
"""
from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import research
from db import DB_PATH, connect, init_db

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
VERSION = "1.0.0"
LOG = logging.getLogger("research-server")

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8770"))
NO_FETCH = os.environ.get("RESEARCH_NO_FETCH", "").lower() in ("1", "true", "yes")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

CSP = (
    "default-src 'self'; img-src 'self' https: data:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


class Handler(BaseHTTPRequestHandler):
    server_version = "GlobalResearchTimeline/" + VERSION
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _error(self, code: int, message: str) -> None:
        self._json({"ok": False, "error": message}, code=code)

    def _admin_allowed(self) -> bool:
        if ADMIN_TOKEN:
            return hmac.compare_digest(self.headers.get("X-Admin-Token", ""), ADMIN_TOKEN)
        client = (self.client_address[0] or "").lower()
        if client in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return True
        # Docker publishes the port through a bridge network, so the peer is
        # the gateway rather than loopback. Still only trust requests whose
        # Host header is local, which keeps a reverse-proxied public domain out.
        host = (urlsplit("//" + (self.headers.get("Host") or "")).hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1")

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/research.html"
        target = (WEB / path.lstrip("/")).resolve()
        if WEB.resolve() not in target.parents and target != WEB.resolve():
            self._error(403, "Forbidden")
            return
        if not target.is_file():
            self._error(404, "Not found")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif target.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/health":
            conn = connect()
            try:
                self._json({"ok": True, "version": VERSION, **research.stats(conn)})
            finally:
                conn.close()
            return
        if path == "/api/research":
            def one(name: str, default: str = "") -> str:
                return str((query.get(name) or [default])[0])

            conn = connect()
            try:
                items, total, facets = research.query_items(
                    conn,
                    {
                        "q": one("q"),
                        "source": one("source"),
                        "doc_type": one("doc_type"),
                        "days": one("days", "90"),
                        "limit": one("limit", "50"),
                        "offset": one("offset", "0"),
                    },
                )
                self._json(
                    {
                        "ok": True,
                        "items": items,
                        "total": total,
                        "facets": facets,
                        "stats": research.stats(conn),
                        "refreshing": research._refreshing,
                        "last": research._last_result,
                    }
                )
            finally:
                conn.close()
            return
        if path == "/api/research/sources":
            conn = connect()
            try:
                self._json(
                    {
                        "ok": True,
                        "sources": research.source_list(conn),
                        "stats": research.stats(conn),
                        "refreshing": research._refreshing,
                        "last": research._last_result,
                    }
                )
            finally:
                conn.close()
            return
        if path.startswith("/api/"):
            self._error(404, "Unknown API endpoint")
            return
        self._static(path)

    def do_HEAD(self) -> None:
        path = urlsplit(self.path).path
        if path in ("/", "/research.html"):
            ctype = "text/html; charset=utf-8"
        elif path.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif path.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif path.endswith(".svg"):
            ctype = "image/svg+xml"
        elif path == "/api/health":
            ctype = "application/json; charset=utf-8"
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        if path == "/api/research/refresh":
            if not self._admin_allowed():
                self._error(403, "需要管理员令牌（ADMIN_TOKEN）")
                return
            self._json({"ok": True, **research.refresh_async(force=bool(payload.get("force", True)))})
            return
        self._error(404, "Unknown API endpoint")

    def log_message(self, fmt: str, *args) -> None:
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            LOG.warning("%s - %s", self.address_string(), fmt % args)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    conn = connect()
    try:
        init_db(conn)
    finally:
        conn.close()
    research.bootstrap()
    if not NO_FETCH:
        threading.Thread(target=research.scheduler_loop, daemon=True, name="research-scheduler").start()
    else:
        LOG.info("RESEARCH_NO_FETCH set; research scheduler disabled")

    server = None
    port = PORT
    for candidate in range(PORT, PORT + 20):
        try:
            server = ThreadingHTTPServer((HOST, candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if server is None:
        LOG.error("No free port in %s-%s", PORT, PORT + 19)
        return 1
    print()
    print("  GLOBAL RESEARCH TIMELINE")
    print(f"  http://{HOST}:{port}")
    print(f"  database: {DB_PATH}")
    print("  Ctrl+C to stop")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
