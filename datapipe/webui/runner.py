"""'Run a file' logic behind the web UI - no HTTP in here, so it is unit-testable.

The browser never sends a path: it picks from lists this module builds from folders the user named, and every pick is
checked against a fresh scan of those folders. One run at a time (a run can use gigabytes of RAM). Results are read back
from the run folder, so a finished run can be reopened later.
"""
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from ..analyze import AnalysisError, _check_single_select, _run_query, build_engine, load_analysis
from ..errors import DataPipeError
from ..identity import clean_name
from ..ingest import read_source
from ..audit import default_actor
from ..pipeline import RUN_ID_RE, STREAM_AUTO_BYTES, _formula_risk, run_pipeline
from ..policy import POLICIES, get_policy
from ..schema import infer_schema, load_schema
from . import filedialog
from .service import ApiError
from .settings import SettingsStore

MAX_METRIC_CHECKS = 12              # metrics files tested against the chosen schema before a run
MAX_PICKED = 20                    # files the person chose from anywhere that the app remembers, newest first
RECENT_FILE = "recent-files.json"  # in the work folder: just the paths, so the list is still there after a restart
DATA_SUFFIXES = {".csv", ".tsv", ".jsonl", ".json", ".sql"}
DOWNLOADS = {"clean.csv": "text/csv; charset=utf-8", "quarantine.csv": "text/csv; charset=utf-8",
             "report.md": "text/markdown; charset=utf-8", "result.json": "application/json; charset=utf-8",
             "issues.json": "application/json; charset=utf-8"}
MAX_LISTED = 300
MAX_CONFIG_BYTES = 2_000_000
MAX_METRIC_ROWS = 200


def _kind_of_json(path):
    """'schema' | 'analysis' | 'proposal' | None for a small JSON file."""
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    if isinstance(doc.get("metrics"), list):
        return "analysis"
    if isinstance(doc.get("columns"), list) and isinstance(doc.get("name"), str):
        return "schema"
    if "proposal_sha256" in doc:
        return "proposal"
    return None


def _rel_size(n):
    return f"{n / 1024 ** 2:.1f} MB" if n >= 1024 ** 2 else f"{max(1, n // 1024)} KB"


class RunService:
    def __init__(self, workdir, data_dirs=(), config_dirs=(), settings=None, chooser=None):
        self.workdir = Path(workdir).resolve()
        self.settings = settings or SettingsStore(self.workdir)
        self.data_dirs = [Path(d).resolve() for d in data_dirs]
        self.config_dirs = [self.workdir / "schemas"] + [Path(d).resolve() for d in list(config_dirs) + list(data_dirs)]
        self._lock = threading.Lock()
        self._job = {"state": "idle"}
        self._chooser = chooser or filedialog.choose_file      # the native window; tests pass a stand-in
        self._can_browse = filedialog.available() if chooser is None else True
        self._dialog_open = threading.Lock()
        self._picked = self._load_recent()                     # files the person chose from anywhere, oldest first (the last one is the newest)

    # ------------------------------------------------------------ the files the person chose, remembered between runs of the app
    def _load_recent(self):
        """The saved paths. A missing, damaged or hand-edited file means fewer paths, never an error. Whether a file still exists is
        not decided here: a file on a USB stick that is not plugged in is hidden until it is back, and every listed file is checked
        again by _scan like any other."""
        try:
            doc = json.loads((self.workdir / RECENT_FILE).read_text(encoding="utf-8"))
            raw = doc["files"]
        except (OSError, ValueError, KeyError, TypeError):
            return []
        out = []
        for entry in raw[:200] if isinstance(raw, list) else []:
            if not isinstance(entry, str) or not entry or len(entry) > 1000 or "\0" in entry:
                continue
            path = Path(entry)
            if path.is_absolute() and path.suffix.lower() in DATA_SUFFIXES and path not in out:
                out.append(path)
        return out[-MAX_PICKED:]

    def _save_recent(self):
        """Caller holds the lock. Written whole and replaced in one step, so a crash cannot leave half a file. Failing to save
        costs only the memory between runs, so it is not an error for the person."""
        try:
            self.workdir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.workdir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps({"files": [str(p) for p in self._picked]}, indent=1) + "\n")
                os.replace(tmp, self.workdir / RECENT_FILE)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError:
            pass

    def forget_files(self, payload=None):
        """Empty the list of chosen files. Only the list: the files themselves are not touched."""
        with self._lock:
            n = len(self._picked)
            self._picked.clear()
            self._save_recent()
        return {"forgotten": n}

    # ------------------------------------------------------------ listing
    def _scan(self):
        data, schemas, analyses = [], [], []
        seen, count = set(), 0

        def add(path, where, as_data, chosen=False):
            nonlocal count
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in DATA_SUFFIXES:
                return
            count += 1
            st = path.stat()
            item = {"id": hashlib.sha256(str(path).encode()).hexdigest()[:16], "name": path.name, "where": where,
                    "size": _rel_size(st.st_size), "bytes": st.st_size, "_path": path,
                    "chosen": chosen, "folder": (path.parent.name or str(path.parent)) if chosen else ""}
            kind = _kind_of_json(path) if path.suffix.lower() == ".json" else None
            if kind == "schema":
                schemas.append(item)
            elif kind == "analysis":
                analyses.append(item)
            elif kind == "proposal":
                return
            elif as_data:
                data.append(item)

        listed = set()
        for d in self.config_dirs + self.data_dirs:
            if not d.is_dir() or d in seen:
                continue
            seen.add(d)
            for path in sorted(d.iterdir()):
                if count >= MAX_LISTED:
                    break
                listed.add(path)
                add(path, d.name or str(d), d in self.data_dirs)
        with self._lock:
            picked = list(reversed(self._picked))              # newest first
        for path in picked:                                    # chosen by the person, possibly outside every folder above
            if path not in listed:
                add(path, "chosen by you", True, chosen=True)
        return data, schemas, analyses

    @staticmethod
    def _public(items):
        return [{"id": x["id"], "name": x["name"], "where": x["where"], "size": x["size"], "bytes": x["bytes"],
                 "chosen": x["chosen"], "folder": x["folder"]} for x in items]

    def options(self):
        data, schemas, analyses = self._scan()
        gb = 1024 ** 3
        return {"files": self._public(data), "schemas": self._public(schemas), "analyses": self._public(analyses),
                "policies": [{"name": p.name, "max_file_mb": p.max_file_bytes // 1024 ** 2, "mask_pii": p.mask_pii,
                              "needs_signoff": p.require_signoff, "max_memory_gb": round(p.max_memory_bytes / gb, 2)}
                             for p in POLICIES.values()],
                "stream_above_mb": STREAM_AUTO_BYTES // 1024 ** 2, "can_browse": self._can_browse,
                "chosen_count": len(self._picked), "max_chosen": MAX_PICKED,
                "default_actor": default_actor(), "recent": self.recent(), "workdir": str(self.workdir),
                "folders": [str(d) for d in self.data_dirs if d.is_dir()], "settings": self.settings.get()}

    # ------------------------------------------------------------ a file from anywhere on this computer
    def add_file(self, payload):
        """Add one file the person points at. Either a typed/pasted full path, or (no path given) the native file window.
        The file then gets an id like every other listed file; running still takes only ids, never a path."""
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        typed = payload.get("path")
        if typed is None:
            path = self._ask_window()
            if path is None:
                return {"added": None}                         # cancelled: not an error
        else:
            if not isinstance(typed, str) or not typed.strip() or len(typed) > 1000 or "\0" in typed:
                raise ApiError(400, "type or paste the full path of a file")
            path = Path(typed.strip().strip('"'))
        if not path.is_absolute():
            raise ApiError(400, "use the full path, starting with the drive letter (for example C:\\Users\\you\\file.csv)")
        if path.is_symlink() or not path.is_file():
            raise ApiError(400, "that is not a regular file on this computer")
        if path.suffix.lower() not in DATA_SUFFIXES:
            raise ApiError(400, "only " + ", ".join(sorted(DATA_SUFFIXES)) + " files can be used")
        path = path.resolve()
        if path.suffix.lower() == ".json" and _kind_of_json(path) == "proposal":
            raise ApiError(400, "that file does not look like a data, schema or metrics file")
        with self._lock:
            if path in self._picked:
                self._picked.remove(path)
            self._picked.append(path)                          # the newest goes last
            del self._picked[:-MAX_PICKED]
            self._save_recent()
        data, schemas, analyses = self._scan()
        for kind, items in (("file", data), ("schema", schemas), ("analysis", analyses)):
            item = next((x for x in items if x["_path"] == path), None)
            if item:
                return {"added": {"kind": kind, "id": item["id"], "name": item["name"]}}
        raise ApiError(400, "that file does not look like a data, schema or metrics file")

    def _ask_window(self):
        if not self._can_browse:
            raise ApiError(409, "no file window is available on this computer; paste the full path instead")
        if not self._dialog_open.acquire(blocking=False):
            raise ApiError(409, "a file window is already open; look for it behind this page")
        try:
            return self._chooser()
        except filedialog.DialogUnavailable as exc:
            self._can_browse = False
            raise ApiError(409, str(exc))
        finally:
            self._dialog_open.release()

    @staticmethod
    def _pick(items, ident, what):
        for x in items:
            if x["id"] == ident:
                return x["_path"]
        raise ApiError(400, f"choose a {what} from the list")

    # ------------------------------------------------------------ results
    def _runs_dir(self):
        return self.workdir / "runs"

    def recent(self, limit=8):
        out = []
        d = self._runs_dir()
        if not d.is_dir():
            return out
        for run in sorted((p for p in d.iterdir() if RUN_ID_RE.match(p.name)), reverse=True)[:limit]:
            f = run / "result.json"
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"run_id": run.name, "status": doc.get("status"), "file": (doc.get("source") or {}).get("name"),
                        "counts": doc.get("counts")})
        return out

    def result(self, run_id):
        if not RUN_ID_RE.match(run_id or ""):
            raise ApiError(404, "run not found")
        run = self._runs_dir() / run_id
        try:
            doc = json.loads((run / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ApiError(404, "run not found")
        metrics = {}
        for name, m in ((doc.get("results") or {}).get("metrics") or {}).items():
            metrics[name] = {"columns": m["columns"], "rows": [[("" if v is None else str(v)) for v in r] for r in m["rows"][:MAX_METRIC_ROWS]],
                             "total_rows": len(m["rows"])}
        recon = (doc.get("results") or {}).get("reconciliation") or {}
        files = [n for n in DOWNLOADS if (run / n).is_file()]
        return {"run_id": run_id, "status": doc.get("status"), "reasons": doc.get("reasons") or [],
                "warnings": doc.get("warnings") or [], "counts": doc.get("counts"), "source": (doc.get("source") or {}).get("name"),
                "policy": (doc.get("policy") or {}).get("name"), "actor": doc.get("actor"),
                "outputs": doc.get("outputs"), "metrics": metrics, "results_sha256": doc.get("results_sha256"),
                "reconciliation": {"checks": recon.get("checks"), "mismatches": len(recon.get("mismatches") or [])},
                "files": files, "folder": str(run)}

    def download_path(self, run_id, name):
        if not RUN_ID_RE.match(run_id or "") or name not in DOWNLOADS:
            raise ApiError(404, "not found")
        path = self._runs_dir() / run_id / name
        if path.is_symlink() or not path.is_file():
            raise ApiError(404, "not found")
        return path, DOWNLOADS[name]

    # ------------------------------------------------------------ metrics as CSV (opens in Excel / Numbers)
    def _metrics(self, run_id):
        if not RUN_ID_RE.match(run_id or ""):
            raise ApiError(404, "run not found")
        try:
            doc = json.loads((self._runs_dir() / run_id / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ApiError(404, "run not found")
        return (doc.get("results") or {}).get("metrics") or {}

    @staticmethod
    def _csv_bytes(metric):
        """UTF-8 with a byte-order mark (Excel on Windows otherwise garbles non-ASCII text). Numbers are written exactly;
        text that a spreadsheet would run as a formula gets a leading apostrophe."""
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(metric["columns"])
        for row in metric["rows"]:
            w.writerow(["" if v is None else ("'" + v if isinstance(v, str) and _formula_risk(v) else v) for v in row])
        return ("\ufeff" + buf.getvalue()).encode("utf-8")

    def metric_csv(self, run_id, name):
        metrics = self._metrics(run_id)
        if name not in metrics:
            raise ApiError(404, "metric not found")
        return self._csv_bytes(metrics[name])

    def metrics_zip(self, run_id):
        metrics = self._metrics(run_id)
        if not metrics:
            raise ApiError(404, "this run has no metrics")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name, metric in metrics.items():
                z.writestr(f"{re.sub(r'[^A-Za-z0-9_-]+', '_', name)[:80]}.csv", self._csv_bytes(metric))
        return out.getvalue()

    # ------------------------------------------------------------ starting
    def status(self):
        with self._lock:
            job = dict(self._job)
        if job["state"] == "running":
            job["elapsed"] = int(time.time() - job["started"])
        job.pop("started", None)
        return job

    def start(self, payload):
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        data, schemas, analyses = self._scan()
        file = self._pick(data, str(payload.get("file", "")), "file")
        schema = self._pick(schemas, str(payload.get("schema", "")), "schema")
        analysis = self._pick(analyses, str(payload.get("analysis", "")), "metrics file") if payload.get("analysis") else None
        policy = payload.get("policy")
        if policy not in POLICIES:
            raise ApiError(400, "choose a policy")
        actor = payload.get("actor")
        if not isinstance(actor, str) or len(actor) > 80:
            raise ApiError(400, "enter your name (1-80 characters)")
        actor = clean_name(actor) or default_actor()
        with self._lock:
            if self._job["state"] == "running":
                raise ApiError(409, "a run is already in progress; wait for it to finish (one at a time keeps memory use predictable)")
            self._job = {"state": "running", "started": time.time(), "file": file.name}
        limits = self.settings.get()
        threading.Thread(target=self._work, args=(file, schema, analysis, policy, actor, limits), daemon=True).start()
        return {"state": "running"}

    def _work(self, file, schema, analysis, policy, actor, limits=None):
        limits = limits or {}
        try:
            res = run_pipeline(file, workdir=self.workdir, policy_name=policy, schema_path=schema, analysis_path=analysis, actor=actor,
                               max_file_mb=limits.get("max_file_mb"), max_memory_gb=limits.get("max_memory_gb"))
            outcome = {"state": "done", "file": file.name, "run_id": res.run_id}
        except DataPipeError as exc:
            outcome = {"state": "error", "file": file.name, "error": str(exc)[:400]}
        except Exception as exc:
            outcome = {"state": "error", "file": file.name, "error": f"unexpected internal error ({type(exc).__name__}); see the terminal"}
        with self._lock:
            self._job = outcome

    # ------------------------------------------------------------ does this schema fit this file?
    @staticmethod
    def _header(path):
        """Column names of a CSV/TSV file from its first line only (cheap even for a 1 GB file); None for other formats."""
        if path.suffix.lower() not in (".csv", ".tsv"):
            return None
        try:
            with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
                first = fh.readline()
        except OSError:
            return None
        counts = {d: first.count(d) for d in ",;\t|"}
        delim = max(counts, key=counts.get) if max(counts.values()) > 0 else ","
        try:
            return [h.strip() for h in next(csv.reader([first], delimiter=delim))]
        except (csv.Error, StopIteration):
            return None

    @staticmethod
    def _metrics_problem(schema, spec, policy):
        """None when every metric of the file can be set up against the schema's columns, else which metric cannot and why.
        Each query runs on an EMPTY table with the schema's columns (as the real run would build it, with personal columns left out
        when the policy masks them), so nothing is read from the person's data. Only a missing column or table, or SQL that does not
        parse, counts: that is a mismatch the run would stop on. Anything else is not our business here."""
        con, _ = build_engine(schema, [], policy)
        try:
            for m in spec.metrics:
                try:
                    _check_single_select(con, m.sql)
                    _run_query(con, m.sql)
                except AnalysisError as exc:
                    text = str(exc)
                    if "Binder Error" in text or "Catalog Error" in text or "does not parse" in text:
                        col = re.search(r'Referenced column "([^"]{1,80})" not found', text)
                        col = col.group(1) if col else None
                        return {"metric": str(m.name)[:80], "column": col,
                                "hidden": bool(col and col in {c.name for c in schema.columns}),      # the schema has it, the policy keeps it out
                                "message": text[:200]}
        finally:
            con.close()
        return None

    def _check_metrics(self, schemas, analyses, payload):
        """Does the chosen metrics file fit the chosen schema, and which other one does? None until a schema is chosen."""
        schema_item = next((s for s in schemas if s["id"] == str(payload.get("schema", ""))), None)
        if schema_item is None or not analyses:
            return None
        policy_name = payload.get("policy")
        policy = POLICIES[policy_name] if isinstance(policy_name, str) and policy_name in POLICIES else get_policy("business")
        try:
            schema = load_schema(schema_item["_path"])
        except DataPipeError:
            return None
        problems = {}
        for item in analyses[:MAX_METRIC_CHECKS]:
            try:
                problems[item["id"]] = self._metrics_problem(schema, load_analysis(item["_path"]), policy)
            except DataPipeError:
                continue                                                   # a metrics file that cannot even be read is not offered
        chosen_id = str(payload.get("analysis", ""))
        chosen = {"id": chosen_id, "ok": problems[chosen_id] is None, **(problems[chosen_id] or {})} if chosen_id in problems else None
        fitting = [a for a in analyses if a["id"] in problems and problems[a["id"]] is None]
        twin = "analysis_" + schema_item["name"][len("schema_"):] if schema_item["name"].startswith("schema_") else None
        best = next((a for a in fitting if a["name"] == twin), None) or (fitting[0] if fitting else None)
        return {"chosen": chosen, "best": {"id": best["id"], "name": best["name"]} if best else None}

    def check(self, payload):
        """Compare the file's header with each schema: how many schema columns the file has, which required ones are missing,
        and which schema fits best; and whether the chosen metrics file fits the chosen schema. Statistics and column names of
        the schema only - no file values."""
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        data, schemas, analyses = self._scan()
        metrics = self._check_metrics(schemas, analyses, payload)           # needs the schema, not the file
        if not str(payload.get("file", "")):
            return {"known": False, "metrics": metrics}
        file = self._pick(data, str(payload.get("file", "")), "file")
        header = self._header(file)
        if header is None:
            return {"known": False, "metrics": metrics}
        have = set(header)
        fits = []
        for item in schemas:
            try:
                sch = load_schema(item["_path"])
            except DataPipeError:
                continue
            cols = sch.columns
            missing = [c.name for c in cols if c.src not in have]
            required_missing = [c.name for c in cols if c.required and c.src not in have]
            known = {c.src for c in cols}
            fits.append({"id": item["id"], "name": item["name"], "schema_columns": len(cols), "matched": len(cols) - len(missing),
                         "missing_required": required_missing[:12], "missing_required_count": len(required_missing),
                         "extra_in_file": sum(1 for h in header if h not in known)})
        fits.sort(key=lambda f: (f["missing_required_count"], -f["matched"], f["extra_in_file"], "-DRAFT" in f["name"], f["name"]))   # on a tie, a reviewed schema beats a machine draft
        by_id = {f["id"]: f for f in fits}
        chosen = by_id.get(str(payload.get("schema", "")))
        best = fits[0] if fits else None
        return {"known": True, "file_columns": len(header), "chosen": chosen, "best": best, "metrics": metrics}

    # ------------------------------------------------------------ a fake file to try the app with
    def make_sample(self):
        from ..sample import generate
        folders = [d for d in self.data_dirs if d.is_dir()]
        if not folders:
            raise ApiError(409, "there is no data folder to put the sample file in")
        target, n = folders[0], 1
        out = target / "buyers_sample.csv"
        while out.exists():
            n += 1
            out = target / f"buyers_sample-{n}.csv"
        try:
            rows, _ = generate(out, mb=2)
        except OSError as exc:
            raise ApiError(409, f"could not write the sample file: {exc.strerror or exc}")
        data, _, _ = self._scan()
        item = next((x for x in data if x["name"] == out.name), None)
        return {"name": out.name, "id": item["id"] if item else "", "rows": rows}

    # ------------------------------------------------------------ drafting a schema
    def draft_schema(self, payload):
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        data, _, _ = self._scan()
        file = self._pick(data, str(payload.get("file", "")), "file")
        low = get_policy("low")
        try:
            tbl = read_source(file, max_bytes=low.max_file_bytes, max_memory_bytes=low.max_memory_bytes)
            schema = infer_schema(tbl, file.stem)
        except DataPipeError as exc:
            raise ApiError(400, str(exc)[:300])
        out = self.workdir / "schemas" / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', file.stem)[:60] or 'schema'}-DRAFT.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(schema.to_dict(), indent=2) + "\n", encoding="utf-8")
        return {"saved_as": str(out), "columns": len(schema.columns),
                "note": "A draft guessed from the file. Open it, check every type, which columns are required and which are personal data, then choose it."}
