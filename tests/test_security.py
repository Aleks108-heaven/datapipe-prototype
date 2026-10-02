"""Regression tests for the findings of the 2026-10-02 security audit. Each one was reproduced as a working attack first."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from datapipe import ingest
from datapipe.coerce import parse_typed
from datapipe.errors import IngestError, SchemaError
from datapipe.llm import AnthropicProvider, OpenAICompatProvider, ProviderError
from datapipe.schema import schema_from_dict


# ---------------------------------------------------------------- API keys must stay with the endpoint they were meant for
@pytest.fixture
def redirecting_endpoint(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    seen = []

    class Thief(BaseHTTPRequestHandler):
        def handle_any(self):
            seen.append({k.lower(): v for k, v in self.headers.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        do_GET = do_POST = handle_any

        def log_message(self, *a):
            pass

    thief = ThreadingHTTPServer(("127.0.0.1", 0), Thief)

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{thief.server_port}/steal")
            self.end_headers()

        def log_message(self, *a):
            pass

    redirector = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for s in (thief, redirector):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{redirector.server_port}", seen
    for s in (thief, redirector):
        s.shutdown()


def test_a_redirect_never_carries_the_api_key_to_another_host(redirecting_endpoint):
    url, seen = redirecting_endpoint
    for provider in (AnthropicProvider(model="m", api_key="SECRET-CANARY", base_url=url),
                     OpenAICompatProvider(model="m", api_key="SECRET-CANARY", base_url=url + "/v1")):
        with pytest.raises(ProviderError, match="30[0-9]"):
            provider._complete("s", "u")
    assert seen == [], "a request reached the redirect target"


@pytest.mark.parametrize("cls", [AnthropicProvider, OpenAICompatProvider])
def test_cleartext_http_is_refused_except_for_this_machine(cls, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    for bad in ("http://api.example.com", "ftp://127.0.0.1", "file:///etc/passwd", "https:///nohost", "api.example.com"):
        with pytest.raises(ProviderError, match="https"):
            cls(model="m", api_key="k", base_url=bad)
    for good in ("http://127.0.0.1:1234/v1", "http://localhost:11434", "https://api.example.com/v1"):
        cls(model="m", api_key="k", base_url=good)


def test_userinfo_and_lookalike_hosts_are_not_treated_as_local():
    for url in ("https://127.0.0.1@evil.example/v1", "https://localhost.evil.example/v1"):
        assert OpenAICompatProvider(model="m", api_key="k", base_url=url).locality == "cloud"
    with pytest.raises(ProviderError):
        OpenAICompatProvider(model="m", api_key="k", base_url="http://127.0.0.1@evil.example/v1")


@pytest.mark.parametrize("model", ["gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "x:cloud", "CLOUD"])
def test_a_cloud_model_behind_a_localhost_url_is_not_recorded_as_local(model):
    p = OpenAICompatProvider(model=model, base_url="http://127.0.0.1:11434/v1")      # no key needed: ollama signs in itself
    assert p.locality == "cloud"


@pytest.mark.parametrize("model", ["llama3.2", "qwen2.5-coder:14b-instruct-q4_K_M", "cloudy-7b", "hf.co/ibm-granite/granite-4.2-3b-GGUF:Q4_K_M"])
def test_ordinary_local_model_names_stay_local(model):
    assert OpenAICompatProvider(model=model, base_url="http://localhost:11434/v1").locality == "local"


# ---------------------------------------------------------------- hostile numbers must be refused, not crash the run
@pytest.mark.parametrize("raw", ["1e999999999", "-1e999999999", "9" * 5000 + ".00", "1" + "0" * 40])
def test_extreme_decimals_are_a_clean_value_error(raw):
    from decimal import Decimal
    value = Decimal(raw) if "e" in raw else raw
    with pytest.raises(ValueError):
        parse_typed("decimal", value, scale=2)


def test_wide_decimal_with_extra_places_is_a_clean_value_error():
    from decimal import Decimal
    with pytest.raises(ValueError):
        parse_typed("decimal", Decimal("1234567890123456789012345678.1234567"), scale=2)


def test_normal_decimals_still_parse():
    from decimal import Decimal
    assert parse_typed("decimal", "120.50", scale=2) == Decimal("120.50")
    assert parse_typed("decimal", Decimal("0"), scale=2) == 0
    assert parse_typed("decimal", "999999999999999999999999999999999999", scale=0) == Decimal("999999999999999999999999999999999999")


# ---------------------------------------------------------------- schema regexes: no catastrophic-backtracking shapes
def _schema(pattern):
    return schema_from_dict({"name": "t", "version": 1, "columns": [{"name": "a", "type": "string", "pattern": pattern}]})


@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(a*)*b", r"^(\w+\s?)*$", r"(.*a)+", r"((ab)+)*", r"(?:x+y*)*z"])
def test_nested_unbounded_repeats_are_refused(pattern):
    with pytest.raises(SchemaError, match="exponential"):
        _schema(pattern)


@pytest.mark.parametrize("pattern", [r"^[A-Z]{2}\d{6}$", r"^\d+$", r"^(\d{3}-){2}\d{4}$", r"^[a-z]+(@[a-z]+)?$", r"^(ab|cd)$", r"(a{1,5}){1,5}"])
def test_ordinary_patterns_are_accepted(pattern):
    _schema(pattern)


# ---------------------------------------------------------------- SQL dumps must not expand into memory bombs
def test_tiny_dump_cannot_store_an_enormous_database(monkeypatch):
    monkeypatch.setattr(ingest, "SQL_MAX_DB_BYTES", 1024 * 1024)
    bomb = "CREATE TABLE t(a); INSERT INTO t SELECT hex(randomblob(2000000)); INSERT INTO t SELECT hex(randomblob(2000000));"
    with pytest.raises(IngestError):
        ingest.parse_sql_dump(bomb)


def test_single_huge_value_is_refused_where_sqlite_limits_are_settable(monkeypatch):
    import sqlite3
    if not hasattr(sqlite3.Connection, "setlimit"):
        pytest.skip("Connection.setlimit needs Python 3.11+ (the database-size cap above still applies)")
    monkeypatch.setattr(ingest, "SQL_MAX_VALUE_BYTES", 1024 * 1024)
    with pytest.raises(IngestError):
        ingest.parse_sql_dump("CREATE TABLE t(a); INSERT INTO t SELECT hex(randomblob(2000000));")


def test_ordinary_dump_still_loads():
    tbl = ingest.parse_sql_dump("CREATE TABLE t(a INTEGER, b TEXT); INSERT INTO t VALUES (1,'x'),(2,'y');")
    assert tbl.columns == ["a", "b"] and len(tbl.rows) == 2


# ---------------------------------------------------------------- the analysis engine cannot be pushed past its memory cap
def test_runaway_metric_fails_instead_of_exhausting_memory(monkeypatch):
    from datapipe import analyze
    from datapipe.errors import AnalysisError
    from datapipe.policy import get_policy
    monkeypatch.setattr(analyze, "MEMORY_LIMIT", "200MB")
    schema = schema_from_dict({"name": "t", "version": 1, "columns": [{"name": "a", "type": "integer"}]})
    con, _ = analyze.build_engine(schema, [{"_row": 1, "a": 1}], get_policy("business"))
    try:
        with pytest.raises(AnalysisError):
            analyze.run_metrics(con, analyze.AnalysisSpec([analyze.Metric(
                "m", "SELECT length(list(repeat('x', 1000000) || CAST(i AS VARCHAR))::VARCHAR) FROM range(2000) t(i)")]))
    finally:
        con.close()


_ = json
