import argparse
import json
import sys
from pathlib import Path

from .audit import AuditLog
from .errors import DataPipeError
from .ingest import read_source
from .llm import AnthropicProvider, HeuristicProvider
from .mapping import approve_mapping, build_request, egress_mode, propose_mapping
from .pipeline import run_pipeline, signoff
from .policy import POLICIES, get_policy
from .schema import infer_schema, load_schema


def build_parser():
    p = argparse.ArgumentParser(prog="datapipe", description="Policy-driven, auditable data pipeline (prototype)")
    p.add_argument("--workdir", default="./work", help="where runs/ and audit.jsonl live (default ./work)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="process one or more files")
    r.add_argument("inputs", nargs="+")
    r.add_argument("--policy", choices=sorted(POLICIES), default="business")
    r.add_argument("--schema")
    r.add_argument("--analysis")
    r.add_argument("--format", choices=["csv", "json", "jsonl", "sql"])
    r.add_argument("--encoding")
    r.add_argument("--delimiter")
    r.add_argument("--table", help="table name for SQL dumps with several tables")
    r.add_argument("--records-path", help="dotted key of the record list in a JSON document")
    r.add_argument("--accept-inferred", action="store_true")
    r.add_argument("--actor")
    r.add_argument("--json", action="store_true", help="print the full result document")

    i = sub.add_parser("infer", help="print a proposed schema for a file")
    i.add_argument("input")
    i.add_argument("--format", choices=["csv", "json", "jsonl", "sql"])
    i.add_argument("--table")

    s = sub.add_parser("signoff", help="approve a PENDING_SIGNOFF run (reviewer must differ from the runner)")
    s.add_argument("run_id")
    s.add_argument("--reviewer", required=True)
    s.add_argument("--note", default="")

    m = sub.add_parser("map", help="propose a mapping of a new file's columns onto a registered schema")
    m.add_argument("input")
    m.add_argument("--schema", required=True, help="target (registered) schema")
    m.add_argument("--policy", choices=sorted(POLICIES), default="business")
    m.add_argument("--provider", choices=["heuristic", "anthropic"], default="heuristic",
                   help="heuristic = offline, nothing leaves this machine (default)")
    m.add_argument("--model", help="LLM model id (or env DATAPIPE_LLM_MODEL)")
    m.add_argument("--base-url", default="https://api.anthropic.com")
    m.add_argument("--min-confidence", type=float, default=0.8)
    m.add_argument("--format", choices=["csv", "json", "jsonl", "sql"])
    m.add_argument("--table")
    m.add_argument("--out", help="where to write the proposal (default: <workdir>/mappings/)")
    m.add_argument("--dry-run", action="store_true", help="print exactly what would be sent to the LLM, send nothing")
    m.add_argument("--actor")

    a = sub.add_parser("approve-mapping", help="approve a proposal and write the mapped schema")
    a.add_argument("proposal")
    a.add_argument("--reviewer", required=True)
    a.add_argument("--accept-review", action="store_true", help="also include items marked needs_review")
    a.add_argument("--out", required=True, help="where to write the mapped schema")

    rv = sub.add_parser("review", help="start the local web UI for reviewing mapping proposals")
    rv.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (0 = pick a free one)")
    rv.add_argument("--dir", action="append", default=[], help="extra folder with proposal JSON files (repeatable)")
    rv.add_argument("--data-dir", action="append", default=[],
                    help="folder holding the original data files (needed to verify manual remapping; "
                         "default: the current folder). Only a file with the exact recorded hash is used.")
    rv.add_argument("--reviewer", help="lock the reviewer name for this session (otherwise the reviewer types it)")
    rv.add_argument("--verbose", action="store_true")

    sub.add_parser("verify-audit", help="check the audit log hash chain")
    sub.add_parser("policies", help="show the policy tiers")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "policies":
            for pol in POLICIES.values():
                print(json.dumps(pol.as_dict()))
            return 0
        if args.cmd == "verify-audit":
            ok, n, msg = AuditLog(Path(args.workdir) / "audit.jsonl").verify()
            print(("OK  " if ok else "FAIL ") + f"{n} records: {msg}")
            return 0 if ok else 1
        if args.cmd == "signoff":
            out = signoff(args.workdir, args.run_id, args.reviewer, args.note)
            print(f"signed off {out['run_id']} by {out['reviewer']} (audit {out['audit_hash'][:12]})")
            return 0
        if args.cmd == "review":
            return _cmd_review(args)
        if args.cmd == "map":
            return _cmd_map(args)
        if args.cmd == "approve-mapping":
            return _cmd_approve(args)
        if args.cmd == "infer":
            tbl = read_source(args.input, max_bytes=100 * 1024 * 1024, fmt=args.format, table=args.table)
            print(json.dumps(infer_schema(tbl, Path(args.input).stem).to_dict(), indent=2))
            return 0
        worst = 0
        for path in args.inputs:
            res = run_pipeline(path, workdir=args.workdir, policy_name=args.policy, schema_path=args.schema,
                               analysis_path=args.analysis, fmt=args.format, encoding=args.encoding,
                               delimiter=args.delimiter, table=args.table, records_path=args.records_path,
                               accept_inferred=args.accept_inferred, actor=args.actor)
            print(f"{Path(path).name}: {res.status}  [{res.run_dir}]")
            for reason in res.reasons:
                print(f"  - {reason}")
            c = res.document.get("counts")
            if c:
                print(f"  rows: total={c['rows_total']} valid={c['valid']} quarantined={c['quarantined']}")
            if args.json:
                print(json.dumps(res.document, indent=2, default=str))
            worst = max(worst, res.exit_code)
        return worst
    except DataPipeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _cmd_map(args):
    policy = get_policy(args.policy)
    target = load_schema(args.schema)
    tbl = read_source(args.input, max_bytes=policy.max_file_bytes, fmt=args.format, table=args.table)
    if args.provider == "anthropic":
        provider = AnthropicProvider(model=args.model, base_url=args.base_url)
    else:
        provider = HeuristicProvider()
    mode = egress_mode(policy, provider)          # raises if the policy forbids this provider
    if args.dry_run:
        payload = build_request(tbl, target, "shapes+samples" if mode == "shapes+samples" else "shapes")
        print(f"egress mode: {mode}  provider: {provider.name}  policy: {policy.name}")
        print("payload that WOULD be sent" + (" (nothing is sent for an offline provider)" if mode == "none" else "") + ":")
        print(json.dumps(payload, indent=2))
        return 0
    audit = AuditLog(Path(args.workdir) / "audit.jsonl")
    read_options = {k: v for k, v in (("fmt", args.format), ("table", args.table)) if v}
    proposal = propose_mapping(tbl, target, provider, policy, input_name=Path(args.input).name,
                               actor=args.actor, audit=audit, min_confidence=args.min_confidence,
                               read_options=read_options)
    out = Path(args.out) if args.out else Path(args.workdir) / "mappings" / f"mapping-{proposal['proposal_sha256'][:10]}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
    print(f"proposal: {out}   provider={provider.name} egress={mode}")
    for it in proposal["items"]:
        ev = it["evidence"] or {}
        rate = f" fit={ev['parse_rate']:.0%}" if ev.get("parse_rate") is not None else ""
        why = f"  ({'; '.join(it['reasons'])})" if it["reasons"] else ""
        print(f"  {it['status']:<12} {it['source']!r} -> {it['target']}  conf={it['confidence']:.2f}{rate}{why}")
    for t in proposal["unmapped_targets"]:
        print(f"  UNMAPPED     target {t['target']}{' (required)' if t['required'] else ''}")
    for w in proposal["warnings"]:
        print(f"  warning: {w}")
    print("next: a different person reviews it, then: datapipe approve-mapping <proposal> --reviewer NAME --out schema.json")
    return 0


def _cmd_approve(args):
    proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
    schema = approve_mapping(proposal, reviewer=args.reviewer, accept_review=args.accept_review, workdir=args.workdir)
    Path(args.out).write_text(json.dumps(schema.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(f"approved by {args.reviewer}; mapped schema written to {args.out} (fingerprint {schema.fingerprint()[:12]})")
    return 0


def _cmd_review(args):
    from .webui import make_server
    server = make_server(args.workdir, port=args.port, extra_dirs=args.dir, reviewer=args.reviewer,
                         verbose=args.verbose, data_dirs=args.data_dir or [Path.cwd()])
    print("Mapping review UI (local only - it listens on 127.0.0.1 and nowhere else).")
    print(f"Open this link in your browser:  {server.url}")
    print("The link contains a secret that is valid until you stop the server; do not share it. Press Ctrl-C to stop.")
    print("To use it from another machine, tunnel the port over SSH instead of exposing it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0
