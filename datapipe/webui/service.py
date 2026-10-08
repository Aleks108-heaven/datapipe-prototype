"""Review logic behind the web UI - no HTTP in here, so it is unit-testable.

The server never trusts the browser: proposals are re-read from disk on every call, their hash seal is
re-verified, and all approval rules (four-eyes, required targets, one decision per proposal) run in the core.
"""
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from ..audit import AuditLog
from ..errors import AuditError, DataPipeError
from ..identity import clean_name
from ..ingest import read_source
from ..mapping import (build_approved_schema, log_refusal, proposal_state, record_decision, reject_mapping,
                       verify_manual_pair, verify_proposal_integrity)
from ..policy import get_policy
from ..schema import schema_from_dict

ID_RE = re.compile(r"^[0-9a-f]{64}\Z")
MAX_FILES = 500
MAX_PROPOSAL_BYTES = 5_000_000
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _looks_like_proposal(doc):
    return (isinstance(doc, dict) and isinstance(doc.get("proposal_sha256"), str)
            and ID_RE.match(doc["proposal_sha256"]) and isinstance(doc.get("items"), list)
            and isinstance(doc.get("actor"), str) and isinstance(doc.get("source"), dict)
            and isinstance(doc.get("target_schema"), dict) and isinstance(doc.get("provider"), dict))


class ReviewService:
    def __init__(self, workdir, extra_dirs=(), fixed_reviewer=None, data_dirs=()):
        self.workdir = Path(workdir).resolve()
        self.dirs = [self.workdir / "mappings"] + [Path(d).resolve() for d in extra_dirs]
        self.data_dirs = [Path(d).resolve() for d in data_dirs]
        self.fixed_reviewer = clean_name(fixed_reviewer) if fixed_reviewer else fixed_reviewer
        self._lock = threading.Lock()
        self._tables = {}                     # pid -> ((path, mtime_ns, size), RawTable)  (small cache)

    # ------------------------------------------------------------ reading
    def _scan(self):
        found, skipped = {}, []
        count = 0
        for d in self.dirs:
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.json")):
                count += 1
                if count > MAX_FILES:
                    skipped.append({"file": "(more files)", "error": f"only the first {MAX_FILES} files are scanned"})
                    return found, skipped
                if path.is_symlink() or not path.is_file():
                    continue
                try:
                    if path.stat().st_size > MAX_PROPOSAL_BYTES:
                        raise ValueError("file too large")
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    if not _looks_like_proposal(doc):
                        raise ValueError("not a mapping proposal")
                except (OSError, ValueError) as exc:
                    skipped.append({"file": path.name, "error": str(exc)[:100]})
                    continue
                found.setdefault(doc["proposal_sha256"], (path, doc))
        return found, skipped

    def _records(self):
        try:
            return AuditLog(self.workdir / "audit.jsonl").records()
        except AuditError as exc:
            raise ApiError(500, str(exc))

    @staticmethod
    def _integrity(doc):
        try:
            verify_proposal_integrity(doc)
            return True
        except DataPipeError:
            return False

    def list_proposals(self):
        found, skipped = self._scan()
        records = self._records()
        rows = []
        for pid, (path, doc) in found.items():
            state = proposal_state(records, pid)
            rows.append({
                "id": pid, "file": path.name, "created": doc.get("created"), "actor": doc["actor"],
                "policy": doc.get("policy"), "source_name": doc["source"].get("name"),
                "provider": doc["provider"].get("name"), "model": doc["provider"].get("model"),
                "egress_mode": (doc.get("egress") or {}).get("mode"),
                "summary": doc.get("summary", {}), "state": state["state"],
                "integrity_ok": self._integrity(doc),
                "required_unmapped": sum(1 for t in doc.get("unmapped_targets", []) if t.get("required")),
            })
        rows.sort(key=lambda r: r["created"] or "", reverse=True)
        return {"proposals": rows, "skipped": skipped, "reviewer_fixed": self.fixed_reviewer,
                "mappings_dir": str(self.dirs[0])}

    def get(self, pid):
        if not ID_RE.match(pid or ""):
            raise ApiError(404, "not found")
        found, _ = self._scan()
        if pid not in found:
            raise ApiError(404, "proposal not found")
        path, doc = found[pid]
        table, reason = self._source_table(doc)
        return {"proposal": doc, "file": path.name, "integrity_ok": self._integrity(doc),
                "state": proposal_state(self._records(), pid), "reviewer_fixed": self.fixed_reviewer,
                "manual_remap": {"available": table is not None, "reason": reason,
                                 "columns": list(doc["source"].get("columns", []))}}

    # ------------------------------------------------------------ source file (for manual remapping)
    def _candidates(self, proposal):
        """Regular, non-symlink files in the data folders whose base name equals the proposal's source name."""
        name = proposal["source"].get("name")
        if not isinstance(name, str) or not name or name in (".", "..") or any(c in name for c in ("/", "\\", "\0")):
            return None, "the proposal does not name a usable source file"
        if not self.data_dirs:
            return None, "no data folder configured: start the review with --data-dir"
        found = []
        for d in self.data_dirs:
            path = d / name
            if not path.is_symlink() and path.is_file():
                found.append(path)
        if not found:
            return None, f"source file not found in the data folder(s): {name[:80]}"
        return found, None

    def _source_table(self, proposal):
        """(RawTable | None, reason). Only a file with the exact recorded sha256 is ever used, and it is parsed
        with the read options recorded in the proposal. Parsed tables are cached per (path, mtime, size)."""
        candidates, reason = self._candidates(proposal)
        if candidates is None:
            return None, reason
        pid, want = proposal["proposal_sha256"], proposal["source"].get("sha256")
        differs = False
        for path in candidates:
            st = path.stat()
            key = (str(path), st.st_mtime_ns, st.st_size)
            cached = self._tables.get(pid)
            if cached and cached[0] == key:
                return cached[1], None
            if hashlib.sha256(path.read_bytes()).hexdigest() != want:
                differs = True
                continue
            opts = proposal["source"].get("read_options") or {}
            try:
                policy = get_policy(proposal.get("policy", "business"))
                table = read_source(path, max_bytes=policy.max_file_bytes, fmt=opts.get("fmt"), table=opts.get("table"),
                                    max_memory_bytes=policy.max_memory_bytes)
            except DataPipeError as exc:
                return None, f"the source file could not be read: {str(exc)[:120]}"
            if len(self._tables) >= 4:
                self._tables.pop(next(iter(self._tables)))
            self._tables[pid] = (key, table)
            return table, None
        return None, ("a file with this name exists but its content differs from the one the proposal was made from"
                      if differs else "source file not available")

    def check_manual(self, pid, payload):
        """Verify one manual mapping candidate against the real data. Returns statistics only, never values."""
        if not isinstance(payload, dict) or not isinstance(payload.get("target"), str) \
                or not isinstance(payload.get("source"), str):
            raise ApiError(400, "target and source are required")
        proposal = self._load(pid)
        self._check_pending(pid)
        table, reason = self._source_table(proposal)
        if table is None:
            raise ApiError(409, reason)
        try:
            evidence = verify_manual_pair(table, schema_from_dict(proposal["target_schema"]), payload["source"],
                                          payload["target"], min_parse_rate=proposal["thresholds"]["min_parse_rate"])
        except DataPipeError as exc:
            raise ApiError(400, str(exc))
        return {"target": payload["target"], "source": payload["source"], "evidence": evidence}

    # ------------------------------------------------------------ deciding
    def _reviewer(self, payload):
        name = self.fixed_reviewer if self.fixed_reviewer else payload.get("reviewer")
        if not isinstance(name, str) or _CTRL.search(name):
            raise ApiError(400, "enter your name (1-80 characters)")
        name = clean_name(name)
        if not name or len(name) > 80:
            raise ApiError(400, "enter your name (1-80 characters)")
        return name

    @staticmethod
    def _targets(value, field):
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 500 or not all(isinstance(x, str) and len(x) <= 200 for x in value):
            raise ApiError(400, f"'{field}' must be a list of column names")
        return value

    @staticmethod
    def _manual(value):
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > 500:
            raise ApiError(400, "'manual' must be a list of {target, source}")
        out = []
        for entry in value:
            if not isinstance(entry, dict) or not isinstance(entry.get("target"), str) \
                    or not isinstance(entry.get("source"), str):
                raise ApiError(400, "each manual mapping needs a target and a source")
            out.append({"target": entry["target"], "source": entry["source"]})   # anything else (e.g. evidence) is dropped
        return out

    @staticmethod
    def _note(payload):
        note = payload.get("note", "")
        if not isinstance(note, str) or len(note) > 500:
            raise ApiError(400, "note must be text of at most 500 characters")
        return _CTRL.sub(" ", note).strip()

    def _load(self, pid):
        if not ID_RE.match(pid or ""):
            raise ApiError(404, "not found")
        found, _ = self._scan()
        if pid not in found:
            raise ApiError(404, "proposal not found")
        return found[pid][1]

    def _check_pending(self, pid, proposal=None, reviewer=None, event="mapping_approval_refused"):
        state = proposal_state(self._records(), pid)
        if state["state"] != "pending":
            msg = f"this proposal was already {state['state']} by {state['by']}"
            log_refusal(str(self.workdir), event, proposal or {"proposal_sha256": pid}, reviewer, msg)
            raise ApiError(409, msg)

    def approve(self, pid, payload):
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        reviewer = self._reviewer(payload)
        include = set(self._targets(payload.get("include"), "include"))
        exclude = set(self._targets(payload.get("exclude"), "exclude"))
        note = self._note(payload)
        manual = self._manual(payload.get("manual"))
        if (include or exclude or manual) and not note:
            raise ApiError(400, "a note is required when you override the verification result "
                                "(including a needs-review item, excluding an accepted one, or mapping by hand)")
        with self._lock:
            proposal = self._load(pid)
            self._check_pending(pid, proposal, reviewer)
            verify = None
            if manual:
                table, reason = self._source_table(proposal)
                if table is None:
                    raise ApiError(409, "manual mappings cannot be verified: " + reason)
                target_schema = schema_from_dict(proposal["target_schema"])
                min_rate = proposal["thresholds"]["min_parse_rate"]

                def verify(t, src):      # always recomputed here; nothing the browser claims about evidence is used
                    return verify_manual_pair(table, target_schema, src, t, min_parse_rate=min_rate)
            try:
                schema = build_approved_schema(proposal, reviewer=reviewer, include=include, exclude=exclude,
                                               note=note, manual=manual, verify_manual=verify)
            except DataPipeError as exc:
                log_refusal(str(self.workdir), "mapping_approval_refused", proposal, reviewer, exc)
                raise ApiError(400, str(exc))
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", schema.name)[:60] or "schema"
            rel = Path("schemas") / f"{safe}-mapped-{pid[:10]}.json"
            target = self.workdir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(schema.to_dict(), indent=2) + "\n")
                # the guarded audit append decides who wins; only the winner renames its file into place
                record_decision(str(self.workdir), "mapping_approved", proposal, reviewer, schema=schema,
                                schema_file=rel.as_posix(), note=note)
                os.replace(tmp_name, target)
            except DataPipeError as exc:
                log_refusal(str(self.workdir), "mapping_approval_refused", proposal, reviewer, exc)
                raise ApiError(409, str(exc))
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
        return {"schema_file": rel.as_posix(), "schema_path": str(target.resolve()), "workdir": str(self.workdir.resolve()),
                "fingerprint": schema.fingerprint(), "schema": schema.to_dict()}

    def reject(self, pid, payload):
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        reviewer = self._reviewer(payload)
        note = self._note(payload)
        with self._lock:
            proposal = self._load(pid)
            self._check_pending(pid, proposal, reviewer, "mapping_rejection_refused")
            try:
                reject_mapping(proposal, reviewer=reviewer, note=note, workdir=str(self.workdir))     # logs its own refusals
            except DataPipeError as exc:
                raise ApiError(400, str(exc))
        return {"state": "rejected"}
