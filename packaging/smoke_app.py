"""Smoke-test a BUILT program (not Python): start it as the app, check it serves the page and finds the example schemas that were bundled into it,
make the fake sample file, run it end to end, and stop it with the page's own Stop request.

    python packaging/smoke_app.py <path to datapipe.exe | datapipe | datapipe.app/Contents/MacOS/datapipe> [--unchanged FOLDER]

--unchanged FOLDER also proves the program writes nothing into that folder (give it the install folder: Program Files is read-only for
ordinary users, so a program that wrote there would work for the administrator who tested it and fail for everybody else).
Standard library only, so it runs wherever the program does."""
import http.client
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def snapshot(folder):
    return {str(p.relative_to(folder)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(Path(folder).rglob("*")) if p.is_file()}


class Failed(Exception):
    pass


def check(condition, message):
    if not condition:
        raise Failed(message)
    print("ok  ", message, flush=True)


def main(argv):
    program = argv[1]
    unchanged = argv[argv.index("--unchanged") + 1] if "--unchanged" in argv else None
    before = snapshot(unchanged) if unchanged else None
    work = Path(tempfile.mkdtemp(prefix="datapipe-smoke-"))
    (work / "files").mkdir()
    env = {**os.environ, "DATAPIPE_NO_BROWSER": "1", "DATAPIPE_CONFIG_DIR": str(work / "config"), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([program, "--workdir", str(work / "work"), "app", "--port", "0", "--data-dir", str(work / "files")],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env, cwd=work)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout], daemon=True).start()
    seen = []
    try:
        link, deadline = None, time.time() + 90
        while time.time() < deadline and not link:
            try:
                line = lines.get(timeout=1)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            seen.append(line.rstrip())
            link = re.search(r"http://127\.0\.0\.1:(\d+)/\?t=([A-Za-z0-9_-]+)", line)
        check(link, "the program started and printed its link")
        port, token = int(link.group(1)), link.group(2)

        def call(method, path, body=None, cookie=None, csrf=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
            headers = {"Host": f"127.0.0.1:{port}"}
            if cookie:
                headers["Cookie"] = f"dp_session={cookie}"
            data = None
            if body is not None:
                data = json.dumps(body).encode()
                headers.update({"Content-Type": "application/json", "X-DataPipe-CSRF": csrf or "", "Content-Length": str(len(data))})
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
            conn.close()
            return out

        status, headers, _ = call("GET", f"/?t={token}")
        check(status == 303 and "dp_session=" in headers.get("set-cookie", ""), "the secret link signs the browser in")
        cookie = re.search(r"dp_session=([^;]+)", headers["set-cookie"]).group(1)
        status, _, page = call("GET", "/", cookie=cookie)
        check(status == 200 and b"datapipe" in page, "the page is served")
        csrf = re.search(rb'name="csrf" content="([^"]+)"', page).group(1).decode()
        check(call("GET", "/")[0] == 401, "the page is refused without the secret")

        def get_json(path):
            s, _, b = call("GET", path, cookie=cookie)
            return s, json.loads(b)

        def post_json(path, body):
            s, _, b = call("POST", path, body, cookie, csrf)
            return s, json.loads(b)

        status, options = get_json("/api/run/options")
        names = {x["name"] for x in options["schemas"]}
        check(status == 200 and "schema_buyers.json" in names and any(a["name"] == "analysis_buyers.json" for a in options["analyses"]),
              "the example schemas bundled into the program are found: " + ", ".join(sorted(names)))
        status, sample = post_json("/api/run/sample", {})
        check(status == 200 and sample["id"], f"the sample file was made ({sample.get('rows')} rows)")
        _, options = get_json("/api/run/options")
        schema = next(x["id"] for x in options["schemas"] if x["name"] == "schema_buyers.json")
        analysis = next(x["id"] for x in options["analyses"] if x["name"] == "analysis_buyers.json")
        status, _ = post_json("/api/run/start", {"file": sample["id"], "schema": schema, "analysis": analysis, "policy": "low", "actor": "smoke"})
        check(status == 200, "a run started")
        outcome, deadline = {}, time.time() + 240
        while time.time() < deadline:
            _, outcome = get_json("/api/run/status")
            if outcome["state"] != "running":
                break
            time.sleep(0.5)
        check(outcome.get("state") == "done", f"the run finished ({outcome.get('state')} {outcome.get('error', '')})")
        _, result = get_json(f"/api/run/result?run={outcome['run_id']}")
        check(result["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS") and result["counts"]["valid"] > 0 and "clean.csv" in result["files"],
              f"the result is good: {result['status']}, {result['counts']['valid']} valid rows, clean.csv written")
        s, h, body = call("GET", f"/api/run/download/{outcome['run_id']}/clean.csv", cookie=cookie)
        check(s == 200 and len(body) > 1000, "clean.csv can be downloaded")
        check(post_json("/api/page/alive", {"id": "smoke-page-1"})[0] == 200, "the page reports that it is open")

        status, answer = post_json("/api/quit", {})
        check(status == 200, "the Stop request was accepted")
        try:
            code = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            raise Failed("the program did not stop after the Stop request")
        check(code == 0, f"the program stopped by itself with exit code {code}")
        if unchanged:
            after = snapshot(unchanged)
            check(after == before, f"nothing was written into {unchanged}" if after == before else
                  f"the program changed {unchanged}: " + ", ".join(sorted(set(after) ^ set(before) | {k for k in after if k in before and after[k] != before[k]})[:10]))
        print("SMOKE TEST PASSED", flush=True)
        return 0
    except Failed as exc:
        print("FAILED:", exc, flush=True)
        print("--- program output so far ---")
        while not lines.empty():
            seen.append(lines.get().rstrip())
        print("\n".join(seen[-40:]))
        return 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
