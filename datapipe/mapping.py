"""LLM-assisted schema mapping: propose -> verify deterministically -> human approval -> versioned schema.

The LLM (or heuristic) only *suggests* which source column corresponds to which target column.
It never sees more than the policy allows, never touches the analysis, and its output is never trusted:
every suggestion is checked against the real values, and nothing takes effect without a second person's approval.
"""
import re
import string
from datetime import datetime, timezone

from .audit import AuditLog, default_actor
from .errors import DataPipeError
from .identity import clean_name, same_person
from .llm.heuristic import name_score
from .llm.providers import MappingProvider
from .pipeline import canonical, sha256_of
from .schema import PII_HINT_RE, infer_type, present_values, schema_from_dict

MAX_COLUMNS = 200
MAX_NAME = 80
PROPOSAL_VERSION = 1
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+")
_NUMERIC_ID_RE = re.compile(r"\d[\d\s().+-]{7,}\d")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_PUNCT = set(string.punctuation)


# ------------------------------------------------------------------ what may leave the machine
def egress_mode(policy, provider: MappingProvider) -> str:
    """'none' | 'shapes' | 'shapes+samples' - and refuse when the policy forbids sending data out."""
    if provider.locality == "offline":
        return "none"
    if policy.llm == "none":
        raise DataPipeError(f"policy {policy.name!r} forbids sending data to an external LLM "
                            "(use the offline heuristic provider)")
    if provider.locality == "local":          # a model on this machine: shapes only, but the payload is still recorded
        return "local"
    return "shapes+samples" if policy.llm == "cloud" else "shapes"


def clean_name(name):
    return _CTRL.sub(" ", str(name))[:MAX_NAME]


def shape_of(value) -> str:
    """Character-class signature of a value, e.g. '2026-01-05' -> '9{4}-9{2}-9{2}'. Never contains letters or digits."""
    text = value if isinstance(value, str) else (format(value, "f") if hasattr(value, "as_tuple") else str(value))
    text = text[:60]
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if "0" <= c <= "9":
            j = i
            while j < len(text) and "0" <= text[j] <= "9":
                j += 1
            out.append("9" if j - i == 1 else f"9{{{j - i}}}")
            i = j
            continue
        if "a" <= c <= "z" or "A" <= c <= "Z" or (c.isalpha() and ord(c) > 127):
            kind = "a" if c.islower() and ord(c) < 128 else ("A" if c.isupper() and ord(c) < 128 else "L")
            j = i
            while j < len(text) and text[j].isalpha() and (
                    ("a" if text[j].islower() and ord(text[j]) < 128 else
                     "A" if text[j].isupper() and ord(text[j]) < 128 else "L") == kind):
                j += 1
            out.append(kind + "+")
            i = j
            continue
        out.append(" " if c.isspace() else (c if c in _PUNCT else "?"))
        i += 1
    return "".join(out)[:40]


def _sensitive(name, values):
    if PII_HINT_RE.search(name):
        return True
    return any(isinstance(v, str) and (_EMAIL_RE.search(v) or _NUMERIC_ID_RE.search(v)) for v in values)


def build_request(table, target, mode):
    """The exact payload an external LLM would receive. `mode` is 'shapes' or 'shapes+samples'."""
    if len(table.columns) > MAX_COLUMNS:
        raise DataPipeError(f"file has more than {MAX_COLUMNS} columns; mapping is not attempted")
    cols = []
    for name in table.columns:
        raw = [r.get(name) for r in table.rows]
        present = present_values(raw)
        counts = {}
        for v in present:
            s = shape_of(v)
            counts[s] = counts.get(s, 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        entry = {
            "name": clean_name(name),
            "inferred_type": infer_type(present),
            "null_rate": round(1 - len(present) / len(raw), 3) if raw else 0.0,
            "distinct_ratio": round(len({str(v) for v in present}) / len(present), 3) if present else 0.0,
            "shapes": [{"shape": s, "share": round(n / len(present), 3)} for s, n in top],
        }
        if mode == "shapes+samples":
            if _sensitive(name, [v for v in present[:200]]):
                entry["samples"] = []
                entry["samples_withheld"] = "possibly personal data"
            else:
                seen, samples = set(), []
                for v in present:
                    sv = str(v)[:30]
                    if sv not in seen:
                        seen.add(sv)
                        samples.append(sv)
                    if len(samples) == 3:
                        break
                entry["samples"] = samples
        cols.append(entry)
    targets = []
    for c in target.columns:
        t = {"name": c.name, "type": c.type, "required": c.required}
        if c.description:
            t["description"] = c.description[:200]
        if c.type == "date" and c.format:
            t["format"] = c.format
        if c.allowed:
            t["allowed_values"] = [str(a) for a in c.allowed][:20]
        if c.unique:
            t["unique"] = True
        targets.append(t)
    return {"task": "map source columns onto target columns", "mode": mode,
            "target_columns": targets, "source_columns": cols}


# ------------------------------------------------------------------ deterministic verification
def _evidence(table, source, col, null_tokens):
    values = []
    for r in table.rows:
        v = r.get(source)
        if isinstance(v, str):
            v = v.strip()
        if v is None or (isinstance(v, str) and v in null_tokens):
            continue
        values.append(v)
    ok = 0
    for v in values:
        try:
            col.parse(v)
            ok += 1
        except ValueError:
            pass
    distinct = len({str(v) for v in values})
    return {"non_null": len(values), "parse_rate": round(ok / len(values), 4) if values else None,
            "distinct_ratio": round(distinct / len(values), 4) if values else None}


def verify_items(raw_items, table, target, *, min_confidence, min_parse_rate):
    targets = {c.name: c for c in target.columns}
    null_tokens = set(target.null_tokens)
    sources = list(table.columns)
    cache = {}

    def ev(source, col):
        key = (source, col.name)
        if key not in cache:
            cache[key] = _evidence(table, source, col, null_tokens)
        return cache[key]

    used_s, used_t, items = set(), set(), []
    for raw in sorted(raw_items, key=lambda m: -m["confidence"]):     # stable: ties keep provider order
        item = {"source": raw["source"][:MAX_NAME * 4], "target": clean_name(raw["target"]),
                "confidence": round(raw["confidence"], 3), "rationale": raw["rationale"],
                "evidence": None, "status": None, "reasons": []}
        src_name = raw["source"] if raw["source"] in sources else next(
            (s for s in sources if clean_name(s) == raw["source"]), None)
        if src_name is None:
            item.update(status="rejected", reasons=["source column does not exist in the file"])
        elif raw["target"] not in targets:
            item.update(status="rejected", reasons=["target column does not exist in the schema"])
        elif src_name in used_s or raw["target"] in used_t:
            item["source"] = src_name
            item.update(status="rejected", reasons=["conflicts with a higher-confidence mapping"])
        else:
            item["source"] = src_name                # the EXACT header, so the mapped schema matches the file
            col = targets[raw["target"]]
            e = ev(src_name, col)
            item["evidence"] = dict(e)
            item["evidence"]["name_score"] = name_score(src_name, col.name)
            reasons = []
            if e["non_null"] == 0:
                status = "needs_review"
                reasons.append("source column has no values to verify against")
            elif e["parse_rate"] < min_parse_rate:
                status = "rejected"
                reasons.append(f"only {e['parse_rate']:.0%} of values fit the target type/format")
            else:
                status = "accepted"
                alternatives = [s for s in sources if s != src_name and ev(s, col)["non_null"] and
                                ev(s, col)["parse_rate"] == 1.0] if e["parse_rate"] == 1.0 else []
                item["evidence"]["alternatives"] = list(alternatives)[:5]
                if item["confidence"] < min_confidence:
                    status = "needs_review"
                    reasons.append(f"confidence {item['confidence']:.2f} is below {min_confidence:.2f}")
                if col.unique and e["distinct_ratio"] < 1.0:
                    status = "needs_review"
                    reasons.append("target must be unique but the source column contains duplicates")
                if alternatives and item["evidence"]["name_score"] < 0.3:
                    status = "needs_review"
                    reasons.append("several columns fit the target type and the names do not corroborate this pair")
            item["status"], item["reasons"] = status, reasons
            if status != "rejected":
                used_s.add(src_name)
                used_t.add(raw["target"])
        items.append(item)
    return items, used_s, used_t


def verify_manual_pair(table, target, source, target_name, *, min_parse_rate):
    """Deterministic gate for a mapping chosen by a human: same evidence rules as a proposed one."""
    if source not in table.columns:
        raise DataPipeError("that column does not exist in the file")
    col = next((c for c in target.columns if c.name == target_name), None)
    if col is None:
        raise DataPipeError("that target column does not exist in the schema")
    e = _evidence(table, source, col, set(target.null_tokens))
    if e["non_null"] == 0:
        raise DataPipeError("that column has no values, so the mapping cannot be verified")
    if e["parse_rate"] < min_parse_rate:
        raise DataPipeError(f"only {e['parse_rate']:.0%} of its values fit the target type/format "
                            f"(at least {min_parse_rate:.0%} are required)")
    warnings = []
    if col.unique and e["distinct_ratio"] < 1.0:
        warnings.append("the target must be unique but this column contains duplicates")
    ns = name_score(source, target_name)
    if ns < 0.3:
        warnings.append("the names do not look alike: double-check that the meaning is the same")
    return {**e, "name_score": ns, "warnings": warnings}


# ------------------------------------------------------------------ proposal
def propose_mapping(table, target, provider, policy, *, input_name, actor=None, audit=None,
                    min_confidence=0.8, min_parse_rate=0.98, read_options=None):
    actor = clean_name(actor) or default_actor()
    mode = egress_mode(policy, provider)
    request = build_request(table, target, "shapes+samples" if mode == "shapes+samples" else "shapes")
    result = provider.propose(request)

    items, used_s, used_t = verify_items(result.mappings, table, target,
                                         min_confidence=min_confidence, min_parse_rate=min_parse_rate)
    warnings = []
    if result.malformed_items:
        warnings.append(f"{result.malformed_items} malformed item(s) in the provider reply were ignored")
    by_status = {k: sum(1 for i in items if i["status"] == k) for k in ("accepted", "needs_review", "rejected")}
    body = {
        "version": PROPOSAL_VERSION,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
        "policy": policy.name,
        "source": {"name": input_name, "sha256": table.source_sha256, "format": table.format,
                   "columns": list(table.columns), "read_options": dict(read_options or {})},
        "target_schema": target.to_dict(),
        "target_fingerprint": target.fingerprint(),
        "provider": {"name": provider.name, "model": provider.model, "locality": provider.locality},
        "egress": {"mode": mode, "payload": request if mode != "none" else None},
        "llm": {"prompt_sha256": result.prompt_sha256, "response_sha256": result.response_sha256},
        "thresholds": {"min_confidence": min_confidence, "min_parse_rate": min_parse_rate},
        "items": items,
        "unmapped_targets": [{"target": c.name, "required": c.required} for c in target.columns if c.name not in used_t],
        "unmapped_sources": [s for s in table.columns if s not in used_s],
        "warnings": warnings,
        "summary": by_status,
    }
    body["proposal_sha256"] = sha256_of(body)
    if audit is not None:
        audit.append("mapping_proposed", None, {
            "input": input_name, "source_sha256": table.source_sha256, "provider": provider.name,
            "model": provider.model, "locality": provider.locality, "egress_mode": mode,
            "payload_sha256": sha256_of(request) if mode != "none" else None,
            "prompt_sha256": result.prompt_sha256, "response_sha256": result.response_sha256,
            "proposal_sha256": body["proposal_sha256"], **by_status}, actor)
    return body


def verify_proposal_integrity(proposal):
    body = {k: v for k, v in proposal.items() if k != "proposal_sha256"}
    if sha256_of(body) != proposal.get("proposal_sha256"):
        raise DataPipeError("proposal was modified after it was created (hash mismatch)")


# ------------------------------------------------------------------ approval
def proposal_state(records, proposal_sha256):
    """Decision state of a proposal, derived from the audit log (the single source of truth)."""
    for rec in records:
        if rec.get("event") in ("mapping_approved", "mapping_rejected") \
                and rec["data"].get("proposal_sha256") == proposal_sha256:
            return {"state": "approved" if rec["event"] == "mapping_approved" else "rejected",
                    "by": rec["actor"], "ts": rec["ts"], "note": rec["data"].get("note", ""),
                    "schema_file": rec["data"].get("schema_file"),
                    "schema_fingerprint": rec["data"].get("schema_fingerprint")}
    return {"state": "pending"}


def build_approved_schema(proposal, *, reviewer, accept_review=False, include=None, exclude=None, note="",
                          manual=None, verify_manual=None):
    """Pure step (no side effects): turn a verified proposal plus the reviewer's decisions into a schema.

    include: set of targets whose needs_review items the reviewer explicitly accepts
    exclude: set of targets whose accepted items the reviewer drops
    accept_review: shorthand for include = every needs_review target (CLI flag)
    manual: [{"target", "source"}] mappings chosen by the reviewer. Each is re-verified by `verify_manual(target,
            source)` (which must raise DataPipeError to refuse); a manual mapping supersedes any proposed item
            for the same target, and its source may not also be used by another mapping.
    """
    verify_proposal_integrity(proposal)
    reviewer = clean_name(reviewer)
    if not reviewer or same_person(reviewer, proposal["actor"]):
        raise DataPipeError("four-eyes rule: the reviewer must be a different person than the one who proposed it")
    include, exclude = set(include or ()), set(exclude or ())
    known = {}
    for i in proposal["items"]:          # a target can carry one live item plus rejected claims: the live one wins
        if i["target"] not in known or known[i["target"]]["status"] == "rejected":
            known[i["target"]] = i
    for t in include | exclude:
        if t not in known:
            raise DataPipeError("decision refers to a target that is not in the proposal")
    for t in include:
        if known[t]["status"] == "rejected":
            raise DataPipeError("a mapping rejected by verification cannot be included")
    schema = schema_from_dict(proposal["target_schema"])
    if schema.fingerprint() != proposal["target_fingerprint"]:
        raise DataPipeError("embedded target schema does not match its recorded fingerprint")
    manual = list(manual or [])
    if manual and verify_manual is None:
        raise DataPipeError("manual mappings cannot be verified here (the source file is needed)")
    by_target = {c.name: c for c in schema.columns}
    file_columns = set(proposal["source"]["columns"])
    manual_items, seen_t, seen_s = [], set(), set()
    for mm in manual:
        t, src = mm.get("target"), mm.get("source")
        if t not in by_target:
            raise DataPipeError("a manual mapping refers to a target that is not in the schema")
        if src not in file_columns:
            raise DataPipeError("a manual mapping refers to a column that is not in the file")
        if t in seen_t:
            raise DataPipeError("a target was mapped manually more than once")
        if src in seen_s:
            raise DataPipeError("a file column was used for more than one manual mapping")
        seen_t.add(t)
        seen_s.add(src)
        manual_items.append({"target": t, "source": src, "evidence": verify_manual(t, src)})
    chosen = []
    for item in proposal["items"]:
        if item["target"] in seen_t:
            continue                                        # superseded by the reviewer's own mapping
        if item["status"] == "accepted" and item["target"] not in exclude:
            chosen.append(item)
        elif item["status"] == "needs_review" and (accept_review or item["target"] in include):
            chosen.append(item)
    for item in chosen:
        if item["source"] in seen_s:
            raise DataPipeError(f"column {item['source'][:60]!r} is already used by the proposed mapping for "
                                f"{item['target']!r}: exclude that mapping first")
    for item in chosen:
        col = by_target[item["target"]]
        col.source = item["source"] if item["source"] != col.name else None
    for mm in manual_items:
        col = by_target[mm["target"]]
        col.source = mm["source"] if mm["source"] != col.name else None
    mapped = {i["target"] for i in chosen} | seen_t
    missing = [c.name for c in schema.columns if c.required and c.name not in mapped]
    if missing:
        raise DataPipeError(f"required target columns are not mapped: {missing} "
                            "(review the needs_review items, or fix the mapping by hand)")
    schema.provenance = {
        "mapping_proposal_sha256": proposal["proposal_sha256"],
        "proposed_by": proposal["actor"], "approved_by": reviewer,
        "provider": proposal["provider"]["name"], "model": proposal["provider"]["model"],
        "mapped_targets": sorted(mapped),
        "manual_mappings": [{"target": mm["target"], "source": mm["source"],
                             "superseded_source": next((i["source"] for i in proposal["items"]
                                                        if i["target"] == mm["target"] and i["status"] != "rejected"), None),
                             "evidence": {k: v for k, v in mm["evidence"].items()}} for mm in manual_items],
        "included_needs_review": [i["target"] for i in chosen if i["status"] == "needs_review"],
        "excluded_accepted": sorted(t for t in exclude if known[t]["status"] == "accepted"),
        "source_sha256": proposal["source"]["sha256"],
    }
    if note:
        schema.provenance["review_note"] = note[:500]
    return schema


def record_decision(workdir, event, proposal, reviewer, *, schema=None, schema_file=None, note=""):
    """Append the decision to the audit log. Each proposal can be decided exactly once - the check and the
    append happen under the log's file lock, so concurrent reviewers (threads or processes) cannot both win."""
    audit = AuditLog(f"{workdir}/audit.jsonl")

    def only_if_pending(records):
        state = proposal_state(records, proposal["proposal_sha256"])
        if state["state"] != "pending":
            raise DataPipeError(f"proposal was already {state['state']} by {state['by']}")

    data = {"proposal_sha256": proposal["proposal_sha256"], "note": note[:500]}
    if schema is not None:
        prov = schema.provenance
        data.update(schema_fingerprint=schema.fingerprint(), schema_file=schema_file,
                    mapped=prov["mapped_targets"], included_needs_review=prov["included_needs_review"],
                    excluded_accepted=prov["excluded_accepted"], manual_mappings=prov["manual_mappings"])
    return audit.append(event, None, data, actor=reviewer, guard=only_if_pending)


def approve_mapping(proposal, *, reviewer, accept_review=False, include=None, exclude=None, note="",
                    workdir=None, schema_file=None, manual=None, verify_manual=None):
    reviewer = clean_name(reviewer)
    schema = build_approved_schema(proposal, reviewer=reviewer, accept_review=accept_review,
                                   include=include, exclude=exclude, note=note,
                                   manual=manual, verify_manual=verify_manual)
    if workdir is not None:
        record_decision(workdir, "mapping_approved", proposal, reviewer, schema=schema,
                        schema_file=schema_file, note=note)
    return schema


def reject_mapping(proposal, *, reviewer, note, workdir):
    verify_proposal_integrity(proposal)
    reviewer = clean_name(reviewer)
    if not reviewer:
        raise DataPipeError("a reviewer name is required")
    if not note or not note.strip():
        raise DataPipeError("a reason is required when rejecting a proposal")
    return record_decision(workdir, "mapping_rejected", proposal, reviewer, note=note.strip())


_ = canonical
