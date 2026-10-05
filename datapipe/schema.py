"""Schema definition, loading, inference and drift detection."""
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from .coerce import TYPES, DEFAULT_SCALE, parse_typed
from .errors import SchemaError

try:                                  # the regex parser is a private module; if it is unavailable the check is skipped
    import re._parser as _sre_parse   # Python 3.11+
except ImportError:                   # pragma: no cover - Python 3.10
    import sre_parse as _sre_parse

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _nested_unbounded_repeat(pattern):
    """True for the classic catastrophic-backtracking shape: an unbounded repeat whose body contains another unbounded
    repeat, e.g. (a+)+ or (.*a)*. Not a complete ReDoS detector (overlapping alternations such as (a|a)* pass); it removes
    the common footguns. Schema regexes remain trusted input - see the README."""
    unbounded = _sre_parse.MAXREPEAT
    repeats = tuple(getattr(_sre_parse, n) for n in ("MAX_REPEAT", "MIN_REPEAT") if hasattr(_sre_parse, n))

    def has_unbounded(items):
        for op, arg in items:
            if op in repeats and arg[1] == unbounded:
                return True
            for sub in _children(op, arg):
                if has_unbounded(sub):
                    return True
        return False

    def _children(op, arg):
        if op in repeats:
            return [arg[2]]
        if str(op) in ("SUBPATTERN",):
            return [arg[-1]]
        if str(op) == "BRANCH":
            return list(arg[1])
        if str(op) in ("ASSERT", "ASSERT_NOT"):
            return [arg[1]]
        return []

    def walk(items):
        for op, arg in items:
            if op in repeats and arg[1] == unbounded and has_unbounded(arg[2]):
                return True
            if any(walk(sub) for sub in _children(op, arg)):
                return True
        return False

    try:
        return walk(_sre_parse.parse(pattern))
    except Exception:                 # unknown parser internals: do not block the schema
        return False
PII_HINT_RE = re.compile(r"(e-?mail|phone|mobile|ssn|passport|birth|dob|address|iban|card|salary|name)", re.I)
_TOP_KEYS = {"name", "version", "null_tokens", "columns", "provenance"}
_COL_KEYS = {"name", "type", "source", "required", "unique", "pii", "min", "max", "scale",
             "format", "pattern", "max_length", "allowed", "description"}


@dataclass
class Column:
    name: str
    type: str = "string"
    source: str = None
    required: bool = False
    unique: bool = False
    pii: bool = False
    min: object = None
    max: object = None
    scale: int = None
    format: str = None
    pattern: str = None
    max_length: int = None
    allowed: list = None
    description: str = None

    @property
    def src(self):
        return self.source or self.name

    def parse(self, raw):
        return parse_typed(self.type, raw, fmt=self.format, scale=self.scale)

    @property
    def effective_scale(self):
        return DEFAULT_SCALE if self.scale is None else self.scale


@dataclass
class Schema:
    name: str
    version: int
    columns: list
    null_tokens: tuple = ("",)
    inferred: bool = False
    provenance: dict = None      # e.g. which approved mapping proposal produced this schema

    def to_dict(self):
        cols = []
        for c in self.columns:
            d = {"name": c.name, "type": c.type}
            if c.source:
                d["source"] = c.source
            for key in ("required", "unique", "pii"):
                if getattr(c, key):
                    d[key] = True
            for key in ("min", "max", "scale", "format", "pattern", "max_length"):
                v = getattr(c, key)
                if v is not None:
                    d[key] = _ser(v)
            if c.allowed is not None:
                d["allowed"] = [_ser(v) for v in c.allowed]
            if c.description:
                d["description"] = c.description
            cols.append(d)
        out = {"name": self.name, "version": self.version, "null_tokens": list(self.null_tokens), "columns": cols}
        if self.provenance:
            out["provenance"] = self.provenance
        return out

    def fingerprint(self):
        canon = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()


def _ser(v):
    if isinstance(v, (date,)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return format(v, "f")
    return v


# ------------------------------------------------------------------ loading
def load_schema(path) -> Schema:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SchemaError(f"cannot read schema file: {exc}")
    return schema_from_dict(doc)


def schema_from_dict(doc) -> Schema:
    if not isinstance(doc, dict):
        raise SchemaError("schema must be a JSON object")
    unknown = set(doc) - _TOP_KEYS
    if unknown:
        raise SchemaError(f"unknown schema keys: {sorted(unknown)}")
    if not isinstance(doc.get("columns"), list) or not doc["columns"]:
        raise SchemaError("schema needs a non-empty 'columns' list")
    cols, seen = [], set()
    for raw in doc["columns"]:
        if not isinstance(raw, dict):
            raise SchemaError("each column must be an object")
        unknown = set(raw) - _COL_KEYS
        if unknown:
            raise SchemaError(f"unknown column keys {sorted(unknown)} in column {raw.get('name')!r}")
        name = raw.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise SchemaError(f"invalid column name {name!r} (letters, digits, underscore; not starting with a digit; "
                              "ASCII only - for a file column with another spelling use the 'source' key)")
        if name in seen:
            raise SchemaError(f"duplicate column name {name!r}")
        seen.add(name)
        _check_column_types(name, raw)
        col = Column(**raw)
        if col.type not in TYPES:
            raise SchemaError(f"column {name!r}: unknown type {col.type!r}; choose from {TYPES}")
        if col.scale is not None and not (isinstance(col.scale, int) and 0 <= col.scale <= 18):
            raise SchemaError(f"column {name!r}: scale must be an integer 0..18")
        if col.scale is not None and col.type != "decimal":
            raise SchemaError(f"column {name!r}: scale only applies to decimal columns")
        if col.format is not None and col.type != "date":
            raise SchemaError(f"column {name!r}: format only applies to date columns")
        try:
            for key in ("min", "max"):
                v = getattr(col, key)
                if v is not None:
                    if col.type not in ("integer", "decimal", "date"):
                        raise SchemaError(f"column {name!r}: {key} only applies to numeric/date columns")
                    setattr(col, key, col.parse(v))
            if col.allowed is not None:
                col.allowed = [col.parse(v) for v in col.allowed]
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise SchemaError(f"column {name!r}: bad constraint value ({exc})")
        if col.pattern is not None:
            try:
                re.compile(col.pattern)
            except re.error as exc:
                raise SchemaError(f"column {name!r}: invalid pattern ({exc})")
            if _nested_unbounded_repeat(col.pattern):
                raise SchemaError(f"column {name!r}: pattern repeats a group that itself repeats without limit "
                                  "(e.g. '(a+)+'); this can take exponential time on crafted input. Use bounded repeats.")
        cols.append(col)
    prov = doc.get("provenance")
    if prov is not None and not isinstance(prov, dict):
        raise SchemaError("provenance must be an object")
    schema_name = doc.get("name", "unnamed")
    if not isinstance(schema_name, str) or not schema_name.strip():
        raise SchemaError("schema 'name' must be non-empty text")
    version = doc.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise SchemaError("schema 'version' must be a whole number (0 or more)")
    tokens = doc.get("null_tokens", [""])
    if not isinstance(tokens, list) or not all(isinstance(t, str) for t in tokens):
        raise SchemaError("'null_tokens' must be a list of text values, e.g. [\"\", \"NA\"]")
    return Schema(name=schema_name, version=version, columns=cols, null_tokens=tuple(tokens), provenance=prov)


_BOOL_KEYS = ("required", "unique", "pii")
_TEXT_KEYS = ("source", "format", "pattern", "description")


def _check_column_types(name, raw):
    """A value of the wrong JSON type must be an error, never a silent change of meaning
    (e.g. "required": "false" is a non-empty string, which Python treats as true)."""
    for key in _BOOL_KEYS:
        if key in raw and not isinstance(raw[key], bool):
            raise SchemaError(f"column {name!r}: '{key}' must be true or false (without quotes)")
    for key in _TEXT_KEYS:
        if key in raw and (not isinstance(raw[key], str) or (key != "description" and not raw[key])):
            raise SchemaError(f"column {name!r}: '{key}' must be text")
    if "max_length" in raw and (isinstance(raw["max_length"], bool) or not isinstance(raw["max_length"], int)
                                or raw["max_length"] < 1):
        raise SchemaError(f"column {name!r}: 'max_length' must be a whole number of 1 or more")
    if "scale" in raw and isinstance(raw["scale"], bool):
        raise SchemaError(f"column {name!r}: scale must be an integer 0..18")
    if "allowed" in raw and (not isinstance(raw["allowed"], list) or not raw["allowed"]):
        raise SchemaError(f"column {name!r}: 'allowed' must be a non-empty list")


# ------------------------------------------------------------------ inference
def _sanitize(name, taken):
    # ASCII only, because the schema loader requires it; the real header is kept in the column's 'source' key
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^0-9A-Za-z]+", "_", plain).strip("_").lower() or "col"
    if base[0].isdigit():
        base = "_" + base
    candidate, i = base, 2
    while candidate in taken:
        candidate, i = f"{base}_{i}", i + 1
    return candidate


def _all_parse(type_, values, **kw):
    try:
        for v in values:
            parse_typed(type_, v, **kw)
        return True
    except ValueError:
        return False


def infer_type(present):
    """Conservative type guess from non-empty values (str or already-typed JSON/SQL values)."""
    if present and all(isinstance(v, bool) or (isinstance(v, str) and v.lower() in ("true", "false")) for v in present):
        return "boolean"
    if present and _all_parse("integer", present):
        return "integer"
    if present and _all_parse("decimal", present):
        return "decimal"
    if present and _all_parse("date", present):
        return "date"
    return "string"


def present_values(values):
    out = []
    for v in values:
        v = v.strip() if isinstance(v, str) else v
        if v is not None and v != "":
            out.append(v)
    return out


def infer_schema(table, name="inferred") -> Schema:
    """Best-effort proposal. A human must review it: inference cannot know meaning, PII or uniqueness."""
    taken, cols = set(), []
    for src in table.columns:
        values = [r.get(src) for r in table.rows]
        present = present_values(values)
        cname = _sanitize(src, taken)
        taken.add(cname)
        cols.append(Column(
            name=cname, type=infer_type(present), source=src if src != cname else None,
            required=len(present) == len(values) and len(values) > 0,
            pii=bool(PII_HINT_RE.search(src)),
        ))
    return Schema(name=name, version=0, columns=cols, inferred=True)


# ------------------------------------------------------------------ drift
def compare_columns(schema: Schema, file_columns):
    known = {c.src for c in schema.columns}
    missing = [c for c in schema.columns if c.src not in file_columns]
    extra = [c for c in file_columns if c not in known]
    return missing, extra
