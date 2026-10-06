"""Writing the run's output files. Shared by the in-memory and the streaming pipeline so both produce identical files."""
import csv
import hashlib
import json
import re
from datetime import date
from decimal import Decimal

from .validate import MASK


def csv_safe(value):
    """Neutralise spreadsheet formula injection in exported cells."""
    text = "" if value is None else str(value)
    return "'" + text if formula_risk(text) else text          # plain numbers such as -10.00 are shown exactly as they were in the file


_PLAIN_SIGNED = re.compile(r"[+-][\d\s().\-]*")     # "+49 (0) 30-1234", "-12": a number or phone, not a formula


def formula_risk(text):
    """True for text a spreadsheet would execute when the CSV is opened (= @ tab CR, or +/- followed by more than a number)."""
    if not text:
        return False
    if text[0] in "=@\t\r":
        return True
    return text[0] in "+-" and not _PLAIN_SIGNED.fullmatch(text)


def clean_cell(col, value, policy):
    if value is None:
        return ""
    if col.pii and policy.mask_pii:
        return MASK
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if col.type == "string":
        return "'" + value if formula_risk(value) else value       # numbers are never altered, only risky text
    return str(value)


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


MAX_ISSUES_FILE = 1000


def write_quarantine_files(run_dir, columns, schema, policy, quarantined):
    """quarantine.csv (one line per rejected row: the row, the rules it broke, the messages, then the original cells) and
    issues.json (the first 1000 problems). `quarantined` yields objects with .row, .raw (dict or None) and .issues, in row order."""
    pii_src = {c.src for c in schema.columns if c.pii}
    issues = []
    with open(run_dir / "quarantine.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["row", "rules", "messages"] + list(columns))
        for q in quarantined:
            raw = q.raw or {}
            cells = []
            for c in columns:
                v = raw.get(c)
                cells.append(MASK if (policy.mask_pii and c in pii_src and v not in (None, "")) else csv_safe(v))
            w.writerow([q.row, ";".join(sorted({i.rule for i in q.issues})),
                        csv_safe(" | ".join(f"{i.column or 'row'}: {i.message}" for i in q.issues))] + cells)
            if len(issues) < MAX_ISSUES_FILE:
                issues.extend(i.as_dict() for i in q.issues)
    (run_dir / "issues.json").write_text(json.dumps(issues[:MAX_ISSUES_FILE], indent=2) + "\n", encoding="utf-8")
