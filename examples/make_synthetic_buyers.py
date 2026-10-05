"""Make a FAKE "buyers and their preferences" CSV of any size, to try datapipe without real data.

    python examples/make_synthetic_buyers.py buyers.csv --mb 120          # about 120 MB
    python examples/make_synthetic_buyers.py buyers.csv --rows 2000000
    python datapipe run buyers.csv --policy business --schema examples/schema_buyers.json \\
        --analysis examples/analysis_buyers.json --actor me

(The same generator is built in: `datapipe sample buyers.csv --mb 120`.) A small share of the rows is deliberately wrong
(--bad-percent, default 0.5) so the quarantine path is exercised. Random but reproducible (--seed); names and emails are invented.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))      # run from a checkout without installing
from datapipe.sample import generate  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    size = ap.add_mutually_exclusive_group(required=True)
    size.add_argument("--mb", type=float, help="approximate file size in MB")
    size.add_argument("--rows", type=int)
    ap.add_argument("--bad-percent", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    n, size_bytes = generate(args.out, mb=args.mb, rows=args.rows, bad_percent=args.bad_percent, seed=args.seed)
    print(f"wrote {n:,} rows, {size_bytes / 1024 ** 2:.1f} MB -> {args.out}")


if __name__ == "__main__":
    sys.exit(main())
