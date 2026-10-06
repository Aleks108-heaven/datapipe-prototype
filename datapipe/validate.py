"""Row validation. Bad rows are quarantined with reasons - never silently repaired or dropped."""
import re
from collections import defaultdict
from dataclasses import dataclass, field

MASK = "<masked>"


@dataclass
class Issue:
    row: int
    column: str
    rule: str
    message: str
    value: str = None

    def as_dict(self):
        return {"row": self.row, "column": self.column, "rule": self.rule, "message": self.message, "value": self.value}


@dataclass
class Quarantined:
    row: int
    raw: dict            # source column -> raw value (None when the row could not be split into fields)
    issues: list


@dataclass
class ValidationResult:
    rows_total: int
    valid_rows: list = field(default_factory=list)      # dicts: {"_row": n, <col>: typed value}
    quarantined: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def issues(self):
        return [i for q in self.quarantined for i in q.issues]

    def counts_by_rule(self):
        out = defaultdict(int)
        for i in self.issues:
            out[i.rule] += 1
        return dict(sorted(out.items()))


@dataclass
class Validated:
    """What the pipeline needs to know after validation, whether the rows are in memory or were streamed through."""
    rows_total: int
    n_valid: int
    n_quarantined: int
    by_rule: dict

    def counts_by_rule(self):
        return dict(sorted(self.by_rule.items()))


def _show(value, col, policy):
    if col is not None and col.pii and policy.mask_pii:
        return MASK
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "..."


def validate_row(row_no, raw_row, schema, present_cols, null_tokens, policy):
    """Type and check one row. Returns (typed row, issues). The in-memory and the streaming pipeline both use this."""
    typed = {"_row": row_no}
    issues = []
    for col in schema.columns:
        if col.src not in present_cols:
            typed[col.name] = None           # absent optional column (drift already checked upstream)
            continue
        raw = raw_row.get(col.src)
        if isinstance(raw, str):
            raw = raw.strip()
        if raw is None or (isinstance(raw, str) and raw in null_tokens):
            typed[col.name] = None
            if col.required:
                issues.append(Issue(row_no, col.name, "required", "required value is missing"))
            continue
        try:
            value = col.parse(raw)
        except ValueError as exc:
            issues.append(Issue(row_no, col.name, "type", str(exc), _show(raw, col, policy)))
            typed[col.name] = None
            continue
        issues.extend(_constraints(row_no, col, value, raw, policy))
        typed[col.name] = value
    return typed, issues


def duplicate_issue(row_no, col, members, value, policy):
    return Issue(row_no, col.name, "unique", f"duplicate value shared by {members} rows", _show(value, col, policy))


def validate(table, schema, policy) -> ValidationResult:
    result = ValidationResult(rows_total=len(table.rows) + len(table.structural_issues))
    present_cols = set(table.columns)
    null_tokens = set(schema.null_tokens)
    per_row_issues = {}                  # row_no -> [Issue]
    typed_rows = {}                      # row_no -> dict
    raw_by_row = {}

    for row_no, message in table.structural_issues:
        per_row_issues[row_no] = [Issue(row_no, None, "structure", message)]
        raw_by_row[row_no] = None

    for row_no, raw_row in zip(table.row_numbers, table.rows):
        raw_by_row[row_no] = raw_row
        typed, issues = validate_row(row_no, raw_row, schema, present_cols, null_tokens, policy)
        typed_rows[row_no] = typed
        if issues:
            per_row_issues.setdefault(row_no, []).extend(issues)

    # uniqueness across rows: every member of a duplicate group is quarantined (we cannot know which is right)
    for col in schema.columns:
        if not col.unique:
            continue
        groups = defaultdict(list)
        for row_no, typed in typed_rows.items():
            v = typed.get(col.name)
            if v is not None:
                groups[v].append(row_no)
        for value, members in groups.items():
            if len(members) > 1:
                for row_no in members:
                    per_row_issues.setdefault(row_no, []).append(duplicate_issue(row_no, col, len(members), value, policy))

    for row_no in sorted(set(typed_rows) | set(per_row_issues)):
        if row_no in per_row_issues:
            result.quarantined.append(Quarantined(row_no, raw_by_row.get(row_no), per_row_issues[row_no]))
        else:
            result.valid_rows.append(typed_rows[row_no])
    return result


def _constraints(row_no, col, value, raw, policy):
    out = []

    def bad(rule, msg):
        out.append(Issue(row_no, col.name, rule, msg, _show(raw, col, policy)))

    if col.min is not None and value < col.min:
        bad("min", f"below minimum {col.min}")
    if col.max is not None and value > col.max:
        bad("max", f"above maximum {col.max}")
    if col.allowed is not None and value not in col.allowed:
        bad("allowed", "not one of the allowed values")
    if col.type == "string":
        if col.max_length is not None and len(value) > col.max_length:
            bad("max_length", f"longer than {col.max_length} characters")
        if col.pattern is not None and not re.fullmatch(col.pattern, value):
            bad("pattern", "does not match the required pattern")
    return out
