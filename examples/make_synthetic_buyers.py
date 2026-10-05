"""Make a FAKE "buyers and their preferences" CSV of any size, to try datapipe without real data.

    python examples/make_synthetic_buyers.py buyers.csv --mb 120          # about 120 MB
    python examples/make_synthetic_buyers.py buyers.csv --rows 2000000
    python datapipe run buyers.csv --policy business --schema examples/schema_buyers.json \\
        --analysis examples/analysis_buyers.json --actor me

A small share of the rows is deliberately wrong (--bad-percent, default 0.5) so the quarantine path is exercised.
Everything is random but reproducible (--seed). Names and emails are invented and use example.com.
"""
import argparse
import random
import sys
from pathlib import Path

COUNTRIES = ["US", "CA", "MX", "BR", "GB", "DE", "FR", "ES", "IT", "PL", "IN", "JP", "AU", "ZA"]
AGES = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
CATEGORIES = ["electronics", "fashion", "home", "beauty", "sports", "books", "toys", "grocery", "travel", "garden"]
CHANNELS = ["email", "sms", "app", "store", "phone"]
FIRST = ["Ana", "Ben", "Chen", "Dina", "Eli", "Fatima", "Gus", "Hana", "Ivo", "Jana", "Kofi", "Lea", "Mika", "Noor", "Omar", "Pia"]
LAST = ["Silva", "Novak", "Okafor", "Kim", "Rossi", "Haddad", "Meyer", "Sato", "Costa", "Ivanova", "Lopez", "Singh", "O'Brien", "Müller"]
NOTES = ["", "", "", "likes discounts", 'asked for "express" delivery', "prefers weekends, evenings", "allergic to wool",
         "gift buyer, birthdays", "second order this month", "reads reviews first", "very loyal; wants early access",
         "line one\nline two", "café + books lover", "купує онлайн", "価格重視"]


def quote(text):
    return '"' + text.replace('"', '""') + '"' if any(c in text for c in ',"\n\r') else text


def row(i, rnd, bad):
    first, last = rnd.choice(FIRST), rnd.choice(LAST)
    fields = {
        "buyer_id": str(i), "full_name": f"{first} {last}", "email": f"{first.lower()}.{i}@example.com",
        "country": rnd.choice(COUNTRIES), "age_group": rnd.choice(AGES), "preferred_category": rnd.choice(CATEGORIES),
        "preferred_channel": rnd.choice(CHANNELS), "preference_score": f"{rnd.uniform(0, 100):.2f}",
        "monthly_budget": f"{rnd.uniform(5, 2500):.2f}", "last_purchase_date": f"{rnd.randint(2023, 2026)}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}",
        "newsletter": rnd.choice(["true", "false"]), "notes": rnd.choice(NOTES),
    }
    if bad:
        kind = rnd.randint(0, 5)
        if kind == 0:
            fields["preferred_category"] = "spaceships"
        elif kind == 1:
            fields["monthly_budget"] = "-10.00"
        elif kind == 2:
            fields["last_purchase_date"] = "03/04/2026"
        elif kind == 3:
            fields["preference_score"] = "101.00"
        elif kind == 4:
            fields["email"] = "not-an-email"
        else:
            fields["buyer_id"] = str(max(1, i - 1))        # duplicate of the previous id
    return ",".join(quote(v) for v in fields.values()) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    size = ap.add_mutually_exclusive_group(required=True)
    size.add_argument("--mb", type=float, help="approximate file size in MB")
    size.add_argument("--rows", type=int)
    ap.add_argument("--bad-percent", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args(argv)
    rnd = random.Random(args.seed)
    target = int(args.mb * 1024 * 1024) if args.mb else None
    header = "buyer_id,full_name,email,country,age_group,preferred_category,preferred_channel,preference_score,monthly_budget,last_purchase_date,newsletter,notes\n"
    written, i = 0, 0
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        fh.write(header)
        written += len(header.encode())
        buf = []
        while (written < target) if target else (i < args.rows):
            i += 1
            line = row(i, rnd, rnd.random() * 100 < args.bad_percent)
            buf.append(line)
            written += len(line.encode())
            if len(buf) >= 20_000:
                fh.write("".join(buf))
                buf.clear()
        fh.write("".join(buf))
    print(f"wrote {i:,} rows, {Path(args.out).stat().st_size / 1024 ** 2:.1f} MB -> {args.out}")


if __name__ == "__main__":
    sys.exit(main())
