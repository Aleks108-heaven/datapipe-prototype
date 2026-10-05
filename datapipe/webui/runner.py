"""'Run a file' logic behind the web UI - no HTTP in here, so it is unit-testable.

The browser never sends a path: it picks from lists this module builds from folders the user named, and every pick is
checked against a fresh scan of those folders. One run at a time (a run can use gigabytes of RAM). Results are read back
from the run folder, so a finished run can be reopened later.
"""
import csv
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from ..errors import DataPipeError
from ..identity import clean_name
from ..ingest import read_source
from ..audit import default_actor
from ..pipeline import RUN_ID_RE, run_pipeline
from ..policy import POLICIES, get_policy
from ..schema import infer_schema, load_schema
from .service import ApiError

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
    def __init__(self, workdir, data_dirs=(), config_dirs=()):
        self.workdir = Path(workdir).resolve()
        self.data_dirs = [Path(d).resolve() for d in data_dirs]
        self.config_dirs = [self.workdir / "schemas"] + [Path(d).resolve() for d in list(config_dirs) + list(data_dirs)]
        self._lock = threading.Lock()
        self._job = {"state": "idle"}

    # ------------------------------------------------------------ listing
    def _scan(self):
        data, schemas, analyses = [], [], []
        seen, count = set(), 0
        for i, d in enumerate(self.config_dirs + self.data_dirs):
            if not d.is_dir() or d in seen:
                continue
            seen.add(d)
            for path in sorted(d.iterdir()):
                if count >= MAX_LISTED:
                    break
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in DATA_SUFFIXES:
                    continue
                count += 1
                item = {"id": hashlib.sha256(str(path).encode()).hexdigest()[:16], "name": path.name, "where": d.name or str(d),
                        "size": _rel_size(path.stat().st_size), "_path": path}
                kind = _kind_of_json(path) if path.suffix.lower() == ".json" else None
                if kind == "schema":
                    schemas.append(item)
                elif kind == "analysis":
                    analyses.append(item)
                elif kind == "proposal":
                    continue
                elif d in self.data_dirs:
                    data.append(item)
        return data, schemas, analyses

    @staticmethod
    def _public(items):
        return [{"id": x["id"], "name": x["name"], "where": x["where"], "size": x["size"]} for x in items]

    def options(self):
        data, schemas, analyses = self._scan()
        return {"files": self._public(data), "schemas": self._public(schemas), "analyses": self._public(analyses),
                "policies": [{"name": p.name, "max_file_mb": p.max_file_bytes // 1024 ** 2, "mask_pii": p.mask_pii,
                              "needs_signoff": p.require_signoff} for p in POLICIES.values()],
                "default_actor": default_actor(), "recent": self.recent(), "workdir": str(self.workdir)}

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
        threading.Thread(target=self._work, args=(file, schema, analysis, policy, actor), daemon=True).start()
        return {"state": "running"}

    def _work(self, file, schema, analysis, policy, actor):
        try:
            res = run_pipeline(file, workdir=self.workdir, policy_name=policy, schema_path=schema, analysis_path=analysis, actor=actor)
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

    def check(self, payload):
        """Compare the file's header with each schema: how many schema columns the file has, which required ones are missing,
        and which schema fits best. Statistics and column names of the schema only - no file values."""
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        data, schemas, _ = self._scan()
        file = self._pick(data, str(payload.get("file", "")), "file")
        header = self._header(file)
        if header is None:
            return {"known": False}
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
        return {"known": True, "file_columns": len(header), "chosen": chosen, "best": best}

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
