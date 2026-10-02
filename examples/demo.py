"""Runs the datapipe demo end to end in a scratch folder and narrates each step.

    python examples/demo.py              # run straight through
    python examples/demo.py --pause      # wait for Enter between steps (for presenting)
    python examples/demo.py --review     # finish by opening the browser review UI on a fresh proposal

Needs: pip install duckdb. The browser step needs nothing extra (the page is self-contained).
Everything is written under a temporary folder; nothing in the repository changes.
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", action="store_true")
    ap.add_argument("--review", action="store_true")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="datapipe-demo-"))
    failures = []

    def say(title, why):
        print(f"\n{'=' * 78}\n{title}\n  -> {why}\n{'-' * 78}")
        if args.pause:
            input("  [Enter to run] ")

    def dp(*a, expect=0):
        cmd = [sys.executable, "-m", "datapipe", "--workdir", str(work), *map(str, a)]
        print("$ datapipe " + " ".join(map(str, a)))
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out = (r.stdout + r.stderr).rstrip()
        print("\n".join("    " + line for line in out.splitlines()[:14]))
        mark = "ok" if r.returncode == expect else f"UNEXPECTED (wanted exit {expect})"
        print(f"  exit {r.returncode}  [{mark}]")
        if r.returncode != expect:
            failures.append(" ".join(map(str, a[:2])))
        return out

    try:
        say("1. A new file arrives with different column names",
            "'Order No' is not 'order_id'. The offline matcher proposes; nothing leaves this machine.")
        out = dp("map", EX / "sales_renamed.csv", "--schema", EX / "schema_sales.json", "--policy", "business")
        proposal = re.search(r"proposal: (\S+\.json)", out).group(1)

        say("2. A different person approves the mapping (four-eyes)",
            "One column was only 70% sure; the reviewer must accept it knowingly. The proposal is hash-sealed.")
        mapped = work / "mapped_schema.json"
        dp("approve-mapping", proposal, "--reviewer", "bob", "--accept-review", "--out", mapped)

        say("3. Run the renamed file under the approved schema",
            "Same results as the original file: the hash is reproducible across CSV/JSON/SQL.")
        dp("run", EX / "sales_renamed.csv", "--policy", "business", "--schema", mapped,
           "--analysis", EX / "analysis_sales.json", "--actor", "alice")

        say("4. Dirty data under the regulated policy",
            "8 of 10 rows fail validation. Regulated allows none, so the run is BLOCKED (exit 2), never repaired.")
        dp("run", EX / "sales_dirty.csv", "--policy", "regulated", "--schema", EX / "schema_sales.json",
           "--actor", "alice", expect=2)

        say("5. Clean data under regulated: waits for a second person",
            "Results exist but are not final until someone other than the runner signs off.")
        out = dp("run", EX / "sales.csv", "--policy", "regulated", "--schema", EX / "schema_sales.json",
                 "--analysis", EX / "analysis_sales.json", "--actor", "alice")
        run_id = re.search(r"\[.*[\\/]([^\\/\]]+)\]", out).group(1)

        say("6. The runner tries to sign off their own run", "Refused: four-eyes is enforced by the tool, not by convention.")
        dp("signoff", run_id, "--reviewer", "alice", expect=1)

        say("7. A different reviewer signs off", "Recorded in the audit log with the reviewer's note.")
        dp("signoff", run_id, "--reviewer", "bob", "--note", "totals checked")

        say("8. Verify the audit log", "Every record chains to the previous one by hash.")
        dp("verify-audit")

        say("9. Someone edits the log", "Changing one word breaks the chain and is detected.")
        log = work / "audit.jsonl"
        lines = log.read_text(encoding="utf-8").splitlines()
        lines[2] = lines[2].replace("alice", "mallory")
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        dp("verify-audit", expect=1)

        if args.review:
            print("\nOpening the review UI on a fresh proposal (Ctrl+C to stop)...")
            work = Path(tempfile.mkdtemp(prefix="datapipe-demo-review-"))      # the first folder holds a deliberately tampered log
            dp("map", EX / "sales_renamed.csv", "--schema", EX / "schema_sales.json", "--policy", "business")
            print("Review as someone other than your OS user; approve and watch the schema appear under", work / "schemas")
            subprocess.run([sys.executable, "-m", "datapipe", "--workdir", str(work), "review",
                            "--data-dir", str(EX)], cwd=ROOT)
    finally:
        print(f"\nScratch folder: {work}")
    print("\nRESULT: " + ("all steps behaved as expected" if not failures else "UNEXPECTED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
