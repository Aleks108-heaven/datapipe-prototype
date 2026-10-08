"""Identifiers must be exact. Found by CodeQL on the first public CI run (py/http-response-splitting): a regex ending in $ also matches just before a
trailing newline, and \\d also matches digits of other scripts. Nothing reached a header or a path that way, but an id check that accepts "id\\n" or
Arabic-Indic digits is not an id check."""
import threading

import pytest

from datapipe.pipeline import RUN_ID_RE
from datapipe.schema import NAME_RE
from datapipe.webui import make_server, server as srv
from datapipe.webui.service import ID_RE
from test_webui_run import TOKEN, Client

RUN = "20261007T120000Z-abcdef"
PID = "a" * 64
ARABIC = "".join(chr(0x0660 + int(c)) for c in "20261007")           # eight digits of another script
FULLWIDTH = "".join(chr(0xFF10 + int(c)) for c in "20261007")


@pytest.mark.parametrize("bad", [RUN + "\n", RUN + "\r\n", "\n" + RUN, RUN + " ", RUN + "\x00", RUN[:-1], RUN.upper(), RUN.replace("Z", "z"),
                                 ARABIC + RUN[8:], FULLWIDTH + RUN[8:], RUN.replace("abcdef", "abcdeg"), ""])
def test_a_run_id_is_exactly_eight_ascii_digits_a_time_and_six_hex_digits(bad):
    assert RUN_ID_RE.match(bad) is None


def test_the_good_run_id_still_matches():
    assert RUN_ID_RE.match(RUN) and RUN_ID_RE.match("20261231T235959Z-0123ab")


@pytest.mark.parametrize("bad", [PID + "\n", "\n" + PID, PID + " ", PID[:-1], PID + "a", PID.upper(), "g" * 64, ""])
def test_a_proposal_id_is_exactly_64_lowercase_hex_digits(bad):
    assert ID_RE.match(bad) is None
    assert ID_RE.match(PID)


@pytest.mark.parametrize("bad", ["name\n", "name\r\n", "\nname", "1name", "na me", "naïve", "é", "", "a-b", "a.b", "na\x00me"])
def test_a_schema_column_name_is_letters_digits_and_underscores_only(bad):
    assert NAME_RE.match(bad) is None


@pytest.mark.parametrize("good", ["order_id", "_x", "A1", "customer_email"])
def test_ordinary_column_names_still_pass(good):
    assert NAME_RE.match(good)


ROUTES = [(srv._ROUTE_ONE, f"/api/proposals/{PID}"), (srv._ROUTE_ACT, f"/api/proposals/{PID}/approve"),
          (srv._ROUTE_DOWNLOAD, f"/api/run/download/{RUN}/clean.csv"), (srv._ROUTE_METRIC, f"/api/run/metrics/{RUN}/by_region.csv"),
          (srv._ROUTE_METRIC, f"/api/run/metrics/{RUN}/all.zip")]


@pytest.mark.parametrize("route,good", ROUTES)
def test_every_route_pattern_accepts_its_path_and_refuses_a_trailing_newline(route, good):
    assert route.match(good)
    for bad in (good + "\n", good + "\r\n", good + "\x00", good + " ", good + "/", good.replace("/api/", "/api//")):
        assert route.match(bad) is None, repr(bad)


# ---------------------------------------------------------------- header values
def test_a_header_value_can_never_carry_a_control_character_but_keeps_everything_a_real_value_has():
    clean = srv._header_value
    for real in ('attachment; filename="a.csv"', "/#/run", "dp_session=Abc-_123; HttpOnly; SameSite=Strict; Path=/",
                 "text/csv; charset=utf-8", "default-src 'none'; frame-ancestors 'none'"):
        assert clean(real) == real, real                                           # nothing a real header holds is changed
    assert clean("x\r\nSet-Cookie: stolen=1") == "x%0D%0ASet-Cookie: stolen=1"    # the line break that would have started a second header is encoded
    assert clean("a\nb\rc\x00d\x1fe\x7ff") == "a%0Ab%0Dc%00d%1Fe%7Ff"
    assert clean("é") == "%C3%A9" and clean(5) == "5" and clean(None) == "None"
    for hostile in ("\r\n", "a\r\nb: c", "\x00\x01\x02", "line\u0085next", "\u2028"):
        assert not any(ch in clean(hostile) for ch in "\r\n\x00"), repr(hostile)


def test_the_handler_sends_only_sanitised_extra_headers():
    handler = srv.Handler.__new__(srv.Handler)
    sent = []
    handler.send_header = lambda name, value: sent.append((name, value))
    handler._headers("text/plain", {"Content-Disposition": 'attachment; filename="x\r\nInjected: 1.csv"', "X-Other": "ok"})
    extra = dict(sent)
    assert extra["Content-Disposition"] == 'attachment; filename="x%0D%0AInjected: 1.csv"' and extra["X-Other"] == "ok"
    assert all("\r" not in v and "\n" not in v for _, v in sent)
    assert ("X-Frame-Options", "DENY") in sent and ("Cache-Control", "no-store") in sent        # the fixed security headers are still sent


# ---------------------------------------------------------------- against the live server
@pytest.fixture
def app(tmp_path):
    server = make_server(tmp_path / "work", port=0, token=TOKEN, data_dirs=[])
    server.runner.opened = []
    server.runner._opener = server.runner.opened.append
    folder = tmp_path / "work" / "runs" / RUN
    folder.mkdir(parents=True)
    (folder / "clean.csv").write_bytes(b"a\n1\n")                                              # exact bytes: write_text would turn \n into \r\n on Windows
    (folder / "result.json").write_text('{"status": "COMPLETED", "counts": {"valid": 1}, "policy": {"name": "low"}}')
    threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_an_id_with_an_encoded_newline_is_refused_everywhere_it_could_be_used(app):
    c = Client(app).login()
    assert c.req("GET", f"/api/run/preview?run={RUN}")[0] == 200                              # the plain id works
    for tail in ("%0A", "%0D%0A", "%00", "%20"):
        assert c.req("GET", f"/api/run/preview?run={RUN}{tail}")[0] == 404, tail
        assert c.req("GET", f"/api/run/result?run={RUN}{tail}")[0] == 404, tail
        assert c.req("GET", f"/api/run/download/{RUN}{tail}/clean.csv")[0] == 404, tail
        assert c.req("GET", f"/api/run/download/{RUN}/clean.csv{tail}")[0] == 404, tail
        assert c.req("GET", f"/api/run/metrics/{RUN}{tail}/all.zip")[0] == 404, tail
        assert c.req("GET", f"/api/proposals/{PID}{tail}")[0] == 404, tail
        assert c.req("POST", f"/api/proposals/{PID}{tail}/approve", {})[0] in (404, 400), tail
    status, _, _ = c.req("POST", "/api/run/open-folder", {"run": RUN + "\n"})
    assert status == 404 and app.runner.opened == []                                          # a JSON body gets the same exactness
    assert c.req("POST", "/api/run/open-folder", {"run": RUN})[0] == 200 and len(app.runner.opened) == 1


def test_a_download_response_carries_exactly_one_content_disposition_and_no_injected_header(app):
    c = Client(app).login()
    status, headers, body = c.req("GET", f"/api/run/download/{RUN}/clean.csv")
    assert status == 200 and headers["content-disposition"] == f'attachment; filename="{RUN}-clean.csv"'
    assert "injected" not in headers and body == b"a\n1\n"


# ---------------------------------------------------------------- download names come from names the app holds
def test_a_download_name_is_built_from_the_apps_own_names_and_refuses_anything_it_does_not_hold(app, tmp_path):
    import json
    from datapipe.webui.runner import DOWNLOADS
    from datapipe.webui.service import ApiError
    runner = app.runner
    run = tmp_path / "work" / "runs" / RUN
    (run / "result.json").write_text(json.dumps({"status": "COMPLETED", "results": {"metrics": {"by_region": {"columns": ["a"], "rows": [[1]]}}}}))
    for key in DOWNLOADS:
        assert runner.download_name(RUN, "file", key) == f"{RUN}-{key}"
    assert runner.download_name(RUN, "metric", "by_region") == f"{RUN}-by_region.csv"
    assert runner.download_name(RUN, "zip") == f"{RUN}-metrics.zip"
    for kind, name in (("file", "evil.csv"), ("file", "clean.csv\r\nX: 1"), ("file", "../clean.csv"), ("file", None), ("metric", "no_such_metric"),
                       ("metric", "by_region\n"), ("metric", ""), ("metric", None)):
        with pytest.raises(ApiError) as exc:
            runner.download_name(RUN, "metric" if kind == "metric" else kind, name)
        assert exc.value.status == 404, (kind, name)
    for bad_run in (RUN + "\n", "20261007T120000Z-ffffff", "x", "", None, "../" + RUN, ARABIC + RUN[8:]):
        for kind in ("file", "metric", "zip"):
            with pytest.raises(ApiError) as exc:
                runner.download_name(bad_run, kind, "clean.csv" if kind == "file" else "by_region")
            assert exc.value.status == 404, (bad_run, kind)


def test_a_symlinked_run_folder_gets_no_download_name(app, tmp_path):
    from datapipe.webui.service import ApiError
    real = tmp_path / "elsewhere"
    real.mkdir()
    link_id = "20261008T120000Z-abcdef"
    try:
        (tmp_path / "work" / "runs" / link_id).symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    with pytest.raises(ApiError):
        app.runner.download_name(link_id, "zip")


def test_the_downloads_still_carry_the_expected_names_end_to_end(app, tmp_path):
    import json
    c = Client(app).login()
    (tmp_path / "work" / "runs" / RUN / "result.json").write_text(
        json.dumps({"status": "COMPLETED", "counts": {"valid": 1}, "policy": {"name": "low"},
                    "results": {"metrics": {"by_region": {"columns": ["a"], "rows": [[1]]}}}}))
    assert c.req("GET", f"/api/run/download/{RUN}/clean.csv")[1]["content-disposition"] == f'attachment; filename="{RUN}-clean.csv"'
    assert c.req("GET", f"/api/run/download/{RUN}/result.json")[1]["content-disposition"] == f'attachment; filename="{RUN}-result.json"'
    assert c.req("GET", f"/api/run/metrics/{RUN}/by_region.csv")[1]["content-disposition"] == f'attachment; filename="{RUN}-by_region.csv"'
    assert c.req("GET", f"/api/run/metrics/{RUN}/all.zip")[1]["content-disposition"] == f'attachment; filename="{RUN}-metrics.zip"'
    assert c.req("GET", f"/api/run/metrics/{RUN}/missing.csv")[0] == 404
    assert c.req("GET", f"/api/run/download/{RUN}/nothing.csv")[0] == 404
