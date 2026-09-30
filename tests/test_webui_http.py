import http.client
import json
import re
import socket
import threading

import pytest

from datapipe.webui import make_server
from helpers import GOOD, make_proposal

TOKEN = "test-token-abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def srv(wd):
    prop, _ = make_proposal(wd)
    server = make_server(wd, port=0, token=TOKEN)
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    server.pid = prop["proposal_sha256"]
    server.wd = wd
    yield server
    server.shutdown()
    server.server_close()


class Client:
    def __init__(self, server, host=None):
        self.server = server
        self.host = host if host is not None else f"127.0.0.1:{server.port}"
        self.cookie = None

    def req(self, method, path, body=None, headers=None, raw_body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        h = {"Host": self.host}
        if self.cookie:
            h["Cookie"] = f"dp_session={self.cookie}"
        h.update(headers or {})
        payload = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        if payload is not None and not any(k.lower() == "content-length" for k in h):
            h["Content-Length"] = str(len(payload))
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in h.items():
            conn.putheader(k, v)
        conn.endheaders(payload)
        r = conn.getresponse()
        data = r.read()
        out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data)
        conn.close()
        return out

    def login(self):
        status, headers, _ = self.req("GET", f"/?t={TOKEN}")
        assert status == 303
        self.cookie = re.search(r"dp_session=([^;]+)", headers["set-cookie"]).group(1)
        return self

    def post(self, path, body, csrf=None, **kw):
        h = {"Content-Type": "application/json"}
        if csrf is not False:
            h["X-DataPipe-CSRF"] = csrf or self.server.csrf
        h.update(kw.pop("headers", {}))
        return self.req("POST", path, body=body, headers=h, **kw)


def approve_path(server):
    return f"/api/proposals/{server.pid}/approve"


# ---------------------------------------------------------------- binding & authentication
def test_binds_to_loopback_only(srv):
    assert srv.server_address[0] == "127.0.0.1"


def test_no_cookie_no_access_and_wrong_token_is_useless(srv):
    c = Client(srv)
    assert c.req("GET", "/")[0] == 401
    assert c.req("GET", "/api/proposals")[0] == 401
    assert c.req("GET", "/?t=wrong")[0] == 401
    assert c.req("GET", "/?t=")[0] == 401
    assert c.req("GET", f"/api/proposals/{srv.pid}")[0] == 401
    c.cookie = "wrong"
    assert c.req("GET", "/api/proposals")[0] == 401
    c.cookie = TOKEN[:-1]
    assert c.req("GET", "/api/proposals")[0] == 401


def test_token_exchange_sets_a_locked_down_cookie_and_hides_the_token(srv):
    status, headers, _ = Client(srv).req("GET", f"/?t={TOKEN}")
    cookie = headers["set-cookie"]
    assert status == 303 and headers["location"] == "/"
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert TOKEN not in headers["location"]


def test_login_then_everything_works(srv):
    c = Client(srv).login()
    status, _, body = c.req("GET", "/api/proposals")
    assert status == 200 and json.loads(body)["proposals"][0]["id"] == srv.pid
    assert c.req("GET", f"/api/proposals/{srv.pid}")[0] == 200
    assert c.req("GET", "/")[0] == 200


def test_host_header_must_be_loopback_with_our_port(srv):
    for host in ("evil.example", f"evil.example:{srv.port}", "127.0.0.1", f"127.0.0.1:{srv.port + 1}", ""):
        c = Client(srv, host=host)
        assert c.req("GET", f"/?t={TOKEN}")[0] == 421, host
    ok = Client(srv, host=f"localhost:{srv.port}")
    assert ok.req("GET", f"/?t={TOKEN}")[0] == 303
    c = Client(srv).login()
    c.host = f"attacker.test:{srv.port}"                       # DNS-rebinding style request with a valid cookie
    assert c.req("GET", "/api/proposals")[0] == 421
    assert c.post(approve_path(srv), {"reviewer": "bob"})[0] == 421


# ---------------------------------------------------------------- CSRF & request hygiene
def test_post_requires_csrf_header(srv):
    c = Client(srv).login()
    assert c.post(approve_path(srv), {"reviewer": "bob"}, csrf=False)[0] == 403
    assert c.post(approve_path(srv), {"reviewer": "bob"}, csrf="wrong")[0] == 403
    assert c.post(approve_path(srv), {"reviewer": "bob"}, csrf=TOKEN)[0] == 403       # session token != csrf token
    status, _, body = c.req("GET", f"/api/proposals/{srv.pid}")
    assert json.loads(body)["state"]["state"] == "pending"


def test_post_requires_auth_even_with_valid_csrf(srv):
    assert Client(srv).post(approve_path(srv), {"reviewer": "bob"})[0] == 401


def test_cross_origin_posts_are_refused(srv):
    c = Client(srv).login()
    for origin in ("http://evil.example", "http://127.0.0.1:1", "null", f"https://127.0.0.1:{srv.port}"):
        assert c.post(approve_path(srv), {"reviewer": "bob"}, headers={"Origin": origin})[0] == 403, origin
    assert c.post(approve_path(srv), {"reviewer": "bob"}, headers={"Origin": f"http://127.0.0.1:{srv.port}"})[0] == 200


def test_content_type_length_and_body_rules(srv):
    c = Client(srv).login()
    path = approve_path(srv)
    assert c.post(path, {"reviewer": "bob"}, headers={"Content-Type": "text/plain"})[0] == 415
    assert c.post(path, {"reviewer": "bob"}, headers={"Content-Type": "application/x-www-form-urlencoded"})[0] == 415
    assert c.post(path, None, raw_body=b"{not json")[0] == 400
    assert c.post(path, None, raw_body=b"x" * (64 * 1024 + 1))[0] == 413
    with socket.create_connection(("127.0.0.1", srv.port), timeout=5) as s:          # no Content-Length at all
        s.sendall((f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{srv.port}\r\nCookie: dp_session={c.cookie}\r\n"
                   f"X-DataPipe-CSRF: {srv.csrf}\r\nContent-Type: application/json\r\n\r\n").encode())
        assert b" 411 " in s.recv(4096).split(b"\r\n")[0] + b" "


def test_unknown_routes_and_methods(srv):
    c = Client(srv).login()
    assert c.req("GET", "/nope")[0] == 404
    assert c.req("GET", "/api/proposals/../../etc/passwd")[0] == 404
    assert c.req("GET", "/api/proposals/" + "z" * 64)[0] == 404
    assert c.post("/api/proposals/" + "0" * 64 + "/approve", {"reviewer": "bob"})[0] == 404
    assert c.post(f"/api/proposals/{srv.pid}/delete", {})[0] == 404
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        assert c.req(method, "/api/proposals")[0] == 405


def test_no_cors_headers_ever(srv):
    c = Client(srv).login()
    for method, path in (("GET", "/api/proposals"), ("GET", "/"), ("OPTIONS", "/api/proposals")):
        _, headers, _ = c.req(method, path, headers={"Origin": "http://evil.example"})
        assert not any(k.startswith("access-control-") for k in headers)


# ---------------------------------------------------------------- headers, CSP, page content
def test_security_headers_on_page_and_api(srv):
    c = Client(srv).login()
    for path in ("/", "/api/proposals"):
        _, h, _ = c.req("GET", path)
        assert h["x-content-type-options"] == "nosniff" and h["cache-control"] == "no-store"
        assert h["referrer-policy"] == "no-referrer" and h["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in h["content-security-policy"]


def test_csp_is_strict_with_a_fresh_nonce_per_response(srv):
    c = Client(srv).login()
    nonces = []
    for _ in range(2):
        _, h, body = c.req("GET", "/")
        csp = h["content-security-policy"]
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "*" not in csp
        assert "default-src 'none'" in csp and "connect-src 'self'" in csp
        n = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
        assert f'nonce="{n}"' in body.decode()
        nonces.append(n)
    assert nonces[0] != nonces[1]


def test_page_source_avoids_dangerous_sinks(srv):
    _, _, body = Client(srv).login().req("GET", "/")
    text = body.decode()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                   "javascript:", "srcdoc", "http://", "https://", "<iframe", "<img", "localStorage"):
        assert banned not in text, banned


def test_fixed_reviewer_is_html_escaped_in_the_page(wd):
    make_proposal(wd)
    evil = '"><script>window.__xss=1</script>'
    server = make_server(wd, port=0, token=TOKEN, reviewer=evil)
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    try:
        text = Client(server).login().req("GET", "/")[2].decode()
        assert "<script>window.__xss" not in text and "&lt;script&gt;" in text
    finally:
        server.shutdown()
        server.server_close()


def test_internal_errors_do_not_leak_details(srv, monkeypatch, capsys):
    def boom():
        raise RuntimeError("SECRET-STACK-DETAIL")
    monkeypatch.setattr(srv.service, "list_proposals", boom)
    status, _, body = Client(srv).login().req("GET", "/api/proposals")
    assert status == 500 and b"SECRET" not in body and json.loads(body) == {"error": "internal error"}
    assert "SECRET-STACK-DETAIL" in capsys.readouterr().err                # detail stays in the server log


# ---------------------------------------------------------------- end to end over HTTP
def test_approve_over_http_then_conflict(srv):
    c = Client(srv).login()
    status, _, body = c.post(approve_path(srv), {"reviewer": "bob"})
    assert status == 200 and json.loads(body)["schema_file"].startswith("schemas/")
    assert c.post(approve_path(srv), {"reviewer": "carol"})[0] == 409
    assert c.post(f"/api/proposals/{srv.pid}/reject", {"reviewer": "carol", "note": "x"})[0] == 409


def test_four_eyes_error_is_a_clean_400_over_http(srv):
    status, _, body = Client(srv).login().post(approve_path(srv), {"reviewer": "alice"})
    assert status == 400 and "four-eyes" in json.loads(body)["error"]


_ = GOOD
