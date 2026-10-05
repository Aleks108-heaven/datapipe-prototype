"""Loopback-only HTTP server for the review UI.

Defences: binds to 127.0.0.1 only; secret link -> HttpOnly SameSite=Strict cookie; per-run CSRF header on every POST;
Host and Origin checks (DNS-rebinding / cross-site requests); strict CSP with per-response nonce; JSON-only API;
size-limited bodies; no wildcard CORS; generic 500s (no stack traces to the browser).
"""
import hmac
import html
import json
import os
import re
import secrets
import sys
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .page import render_page
from .service import ApiError, ReviewService

MAX_BODY = 64 * 1024
_ID = r"[0-9a-f]{64}"
_ROUTE_ONE = re.compile(rf"^/api/proposals/({_ID})$")
_ROUTE_ACT = re.compile(rf"^/api/proposals/({_ID})/(approve|reject|check)$")
COOKIE = "dp_session"


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Linux/macOS SO_REUSEADDR only lets a restart reuse a port in TIME_WAIT. On Windows it lets a SECOND server bind a
    # port that is already being served, so the printed link silently talks to the wrong process (401 on every try).
    allow_reuse_address = os.name != "nt"

    def __init__(self, addr, service, token, csrf, verbose=False):
        self.service, self.token, self.csrf, self.verbose = service, token, csrf, verbose
        super().__init__(addr, Handler)
        self.port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/?t={self.token}"


class Handler(BaseHTTPRequestHandler):
    server_version = "datapipe-review"
    sys_version = ""
    timeout = 15                      # a client that declares a body and never sends it must not hold a thread forever

    # ------------------------------------------------------------ plumbing
    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write("review: " + (fmt % args) + "\n")

    def _headers(self, ctype, extra=None, csp="default-src 'none'; frame-ancestors 'none'"):
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", csp)
        for k, v in (extra or {}).items():
            self.send_header(k, v)

    def _send(self, status, body: bytes, ctype, extra=None, csp="default-src 'none'; frame-ancestors 'none'"):
        self.send_response(status)
        self._headers(ctype, extra, csp)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _error(self, status, message):
        self._json(status, {"error": message})

    def _host_ok(self):
        return (self.headers.get("Host") or "") in self.server.allowed_hosts

    def _authed(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        try:
            jar = SimpleCookie(raw)
        except Exception:
            return False
        morsel = jar.get(COOKIE)
        return bool(morsel) and hmac.compare_digest(morsel.value.encode(), self.server.token.encode())

    # ------------------------------------------------------------ GET
    def do_GET(self):
        try:
            if not self._host_ok():
                return self._error(421, "unexpected Host header")
            url = urlsplit(self.path)
            if url.path == "/":
                supplied = parse_qs(url.query).get("t", [""])[0]
                if supplied and hmac.compare_digest(supplied.encode(), self.server.token.encode()):
                    return self._redirect_with_cookie()
                if not self._authed():
                    return self._send(401, b"Open the link printed by 'datapipe review' to use this page.\n",
                                      "text/plain; charset=utf-8")
                nonce = secrets.token_urlsafe(16)
                fixed = self.server.service.fixed_reviewer or ""
                page = render_page(nonce, self.server.csrf, html.escape(fixed, quote=True))
                csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                       "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
                return self._send(200, page.encode(), "text/html; charset=utf-8", csp=csp)
            if not self._authed():
                return self._error(401, "not authenticated")
            if url.path == "/api/proposals":
                return self._json(200, self.server.service.list_proposals())
            m = _ROUTE_ONE.match(url.path)
            if m:
                return self._json(200, self.server.service.get(m.group(1)))
            return self._error(404, "not found")
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except Exception:
            self._internal()

    def _redirect_with_cookie(self):
        self.send_response(303)
        self._headers("text/plain; charset=utf-8", {
            "Location": "/",
            "Set-Cookie": f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"})
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ------------------------------------------------------------ POST
    def _slurp(self):
        """Read the declared body (bounded) before any check, so an early 4xx never closes the socket with unread
        bytes - on Windows that sends a reset that can destroy the response. Validation still happens afterwards."""
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return b""
        if length <= 0 or length > 2 * MAX_BODY:
            return b""
        try:
            return self.rfile.read(length)
        except OSError:
            return b""

    def do_POST(self):
        try:
            raw = self._slurp()
            if not self._host_ok():
                return self._error(421, "unexpected Host header")
            if not self._authed():
                return self._error(401, "not authenticated")
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {f"http://{h}" for h in self.server.allowed_hosts}:
                return self._error(403, "cross-origin request refused")
            supplied = self.headers.get("X-DataPipe-CSRF") or ""
            if not hmac.compare_digest(supplied.encode(), self.server.csrf.encode()):
                return self._error(403, "missing or wrong CSRF token")
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                return self._error(415, "JSON body required")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._error(411, "Content-Length required")
            if length < 0 or length > MAX_BODY:
                return self._error(413, "request too large")
            try:
                payload = json.loads(raw or b"null")
            except ValueError:
                return self._error(400, "body is not valid JSON")
            m = _ROUTE_ACT.match(urlsplit(self.path).path)
            if not m:
                return self._error(404, "not found")
            pid, action = m.groups()
            svc = self.server.service
            handler = {"approve": svc.approve, "reject": svc.reject, "check": svc.check_manual}[action]
            return self._json(200, handler(pid, payload))
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except Exception:
            self._internal()

    def _internal(self):
        traceback.print_exc(file=sys.stderr)          # details stay in the server log
        try:
            self._error(500, "internal error")
        except Exception:
            pass

    def _method_not_allowed(self):
        self._error(405, "method not allowed")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed


def make_server(workdir, *, port=0, extra_dirs=(), reviewer=None, token=None, verbose=False, data_dirs=()):
    service = ReviewService(workdir, extra_dirs=extra_dirs, fixed_reviewer=reviewer, data_dirs=data_dirs)
    return ReviewServer(("127.0.0.1", port), service, token or secrets.token_urlsafe(24),
                        secrets.token_urlsafe(24), verbose=verbose)
