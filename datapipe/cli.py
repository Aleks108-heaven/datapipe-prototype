import argparse
import json
import os
import sys
from pathlib import Path

from .audit import AuditLog
from .errors import DataPipeError
from .ingest import read_source
from .llm import AnthropicProvider, HeuristicProvider, OpenAICompatProvider
from .mapping import approve_mapping, build_request, egress_mode, propose_mapping
from .pipeline import run_pipeline, signoff
from .policy import POLICIES, get_policy
from .schema import infer_schema, load_schema


EXIT_USAGE = 64      # wrong command line; 2 means "blocked by policy" and must stay unambiguous for scripts


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser():
    p = _Parser(prog="datapipe", description="Policy-driven, auditable data pipeline (prototype)")
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
    r.add_argument("--max-file-mb", type=float, help="override the policy's file-size limit for this run (recorded in the result)")
    r.add_argument("--max-memory-gb", type=float,
                   help="override the policy's memory limit (estimated RAM the parsed file may need; default 6 GB) - "
                        "only raise it on a computer that has the RAM")

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
    m.add_argument("--provider", choices=["heuristic", "anthropic", "openai-compat"], default="heuristic",
                   help="heuristic = offline, nothing leaves this machine (default); openai-compat = Ollama, LM Studio, "
                        "llama.cpp or a hosted free tier (loopback URL = stays local)")
    m.add_argument("--model", help="LLM model id (or env DATAPIPE_LLM_MODEL)")
    m.add_argument("--base-url", help="API base URL (default: https://api.anthropic.com, or http://127.0.0.1:11434/v1 "
                                      "for openai-compat, which is Ollama's default)")
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

    sm = sub.add_parser("sample", help="write a FAKE buyers file (with a few deliberate errors) to try datapipe without real data")
    sm.add_argument("out")
    size = sm.add_mutually_exclusive_group()
    size.add_argument("--mb", type=float, help="approximate size in MB (default 5)")
    size.add_argument("--rows", type=int)
    sm.add_argument("--bad-percent", type=float, default=0.5)
    sm.add_argument("--seed", type=int, default=1)

    ap = sub.add_parser("app", help="start the local app in your browser: run a file, see the results, review mappings")
    ap.add_argument("--port", type=int, default=8765, help="port on 127.0.0.1 (0 = pick a free one)")
    ap.add_argument("--data-dir", action="append", default=[],
                    help="folder with your data files (repeatable; default: the current folder)")
    ap.add_argument("--dir", action="append", default=[], help="extra folder with proposal JSON files (repeatable)")
    ap.add_argument("--reviewer", help="lock the reviewer name for this session")
    ap.add_argument("--no-browser", action="store_true", help="only print the link")
    ap.add_argument("--verbose", action="store_true")

    sub.add_parser("verify-audit", help="check the audit log hash chain")
    sub.add_parser("policies", help="show the policy tiers")
    return p


def _double_click_args():
    """What the standalone program does when it is started with no arguments (a double-click): open the app, with the user's
    files in ~/datapipe/files and the results in ~/datapipe/work."""
    home = Path.home() / "datapipe"
    (home / "files").mkdir(parents=True, exist_ok=True)
    return ["--workdir", str(home / "work"), "app", "--data-dir", str(home / "files")]


def main(argv=None):
    if argv is None and len(sys.argv) == 1 and getattr(sys, "frozen", False):
        argv = _double_click_args()
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
        if args.cmd == "sample":
            from .sample import generate
            n, size = generate(args.out, mb=None if args.rows else (args.mb or 5), rows=args.rows, bad_percent=args.bad_percent, seed=args.seed)
            print(f"wrote {n:,} rows, {size / 1024 ** 2:.1f} MB of fake data -> {args.out}")
            return 0
        if args.cmd in ("review", "app"):
            return _cmd_review(args)
        if args.cmd == "map":
            return _cmd_map(args)
        if args.cmd == "approve-mapping":
            return _cmd_approve(args)
        if args.cmd == "infer":
            low = get_policy("low")
            tbl = read_source(args.input, max_bytes=low.max_file_bytes, fmt=args.format, table=args.table,
                              max_memory_bytes=low.max_memory_bytes)
            print(json.dumps(infer_schema(tbl, Path(args.input).stem).to_dict(), indent=2))
            return 0
        worst = 0
        for path in args.inputs:
            res = run_pipeline(path, workdir=args.workdir, policy_name=args.policy, schema_path=args.schema,
                               analysis_path=args.analysis, fmt=args.format, encoding=args.encoding,
                               delimiter=args.delimiter, table=args.table, records_path=args.records_path,
                               accept_inferred=args.accept_inferred, actor=args.actor,
                               max_file_mb=args.max_file_mb, max_memory_gb=args.max_memory_gb)
            print(f"{Path(path).name}: {res.status}  [{res.run_dir}]")
            for reason in res.reasons:
                print(f"  - {reason}")
            c = res.document.get("counts")
            if c:
                print(f"  rows: total={c['rows_total']} valid={c['valid']} quarantined={c['quarantined']}")
                if c["quarantined"]:
                    print(f"  bad rows and reasons: {res.run_dir / 'quarantine.csv'}")
            out = (res.document.get("outputs") or {}).get("clean_csv")
            if out:
                print(f"  cleaned data: {res.run_dir / out['file']}  ({out['rows']} rows)")
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
    tbl = read_source(args.input, max_bytes=policy.max_file_bytes, fmt=args.format, table=args.table,
                      max_memory_bytes=policy.max_memory_bytes)
    if args.provider == "anthropic":
        provider = AnthropicProvider(model=args.model, base_url=args.base_url or "https://api.anthropic.com")
    elif args.provider == "openai-compat":
        kw = {"base_url": args.base_url} if args.base_url else {}
        provider = OpenAICompatProvider(model=args.model, **kw)
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


def _examples_dir():
    """Example schemas and metrics: the bundled copy inside the standalone program, else ./examples."""
    bundled = getattr(sys, "_MEIPASS", None)
    return (Path(bundled) / "examples") if bundled else Path.cwd() / "examples"


def _data_dirs(args, is_app):
    """Folders the app may read data files from. The app also has an 'inbox' inside the work folder: drop a file there
    (from Downloads, a USB stick, a mail attachment...) and it appears in the list after a refresh."""
    dirs = [Path(d) for d in args.data_dir] or [Path.cwd()]
    if is_app:
        inbox = Path(args.workdir) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        dirs.append(inbox)
    return dirs


def _cmd_review(args):
    from .webui import make_server
    is_app = args.cmd == "app"
    examples = _examples_dir()
    try:
        server = make_server(args.workdir, port=args.port, extra_dirs=args.dir, reviewer=args.reviewer,
                             verbose=args.verbose, data_dirs=_data_dirs(args, is_app),
                             config_dirs=[examples] if is_app and examples.is_dir() else [])
    except OSError as exc:
        raise DataPipeError(f"cannot listen on 127.0.0.1:{args.port} ({exc.strerror or exc}). Another review may already be "
                            "running there - stop it, or use --port 0 to pick a free port.")
    link = server.url + ("&go=run" if is_app else "")
    print("datapipe app (local only - it listens on 127.0.0.1 and nowhere else)." if is_app
          else "Mapping review UI (local only - it listens on 127.0.0.1 and nowhere else).")
    print(f"Open this link in your browser:  {link}", flush=True)
    if is_app and not args.no_browser and not os.environ.get("DATAPIPE_NO_BROWSER"):     # the variable is for tests and CI
        try:
            import webbrowser
            webbrowser.open(link)
        except Exception:
            pass
    print("The link contains a secret that is valid until you stop the server; do not share it. Press Ctrl-C to stop.")
    print("To use it from another machine, tunnel the port over SSH instead of exposing it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0
