"""Offline baseline mapper. NOT an LLM: token/synonym/type matching only.

Useful as (a) a provider that never sends data anywhere (allowed even under the strictest policy),
(b) an independent second opinion used to corroborate LLM proposals, and (c) a deterministic test double.
"""
import difflib
import re

from .providers import MappingProvider, ProviderResult

SYNONYMS = {
    "id": {"id", "no", "num", "number", "key", "code", "ref", "reference"},
    "amount": {"amount", "total", "price", "sum", "value", "revenue", "cost", "eur", "usd", "gbp", "sale"},
    "date": {"date", "dt", "day", "time", "timestamp"},
    "email": {"email", "mail", "emailaddress"},
    "region": {"region", "area", "territory", "zone", "country", "market"},
    "paid": {"paid", "settled", "payment", "ispaid"},
    "customer": {"customer", "buyer", "client", "user", "purchaser", "account"},
    "order": {"order", "purchase", "transaction", "txn"},
}
_CONCEPT = {w: c for c, words in SYNONYMS.items() for w in words}
_STOP = {"on", "of", "the", "in", "at", "a", "an", "to", "for"}
MIN_PAIR_SCORE = 0.5


def _tokens(name):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    out = []
    for part in re.split(r"[^A-Za-z0-9]+", s.lower()):
        if not part or part in _STOP:
            continue
        if part not in _CONCEPT:
            if len(part) > 3 and part.endswith("s"):
                part = part[:-1]
            if len(part) > 4 and part.endswith("ed"):
                part = part[:-2]
        out.append(_CONCEPT.get(part, part))
    return out


def name_score(source_name, target_name):
    """0..1 similarity of two column names, ignoring types and data."""
    a, b = _tokens(source_name), _tokens(target_name)
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    jaccard = len(sa & sb) / len(sa | sb)
    ratio = difflib.SequenceMatcher(None, "".join(a), "".join(b)).ratio()
    return round(max(jaccard, 0.9 * ratio if ratio >= 0.8 else 0.0), 4)


def _type_compatible(src_type, tgt_type):
    return src_type == tgt_type or tgt_type == "string" or (src_type == "integer" and tgt_type == "decimal")


class HeuristicProvider(MappingProvider):
    name = "heuristic"
    locality = "offline"
    model = None

    def propose(self, request):
        targets = request["target_columns"]
        sources = request["source_columns"]
        pairs = []
        for si, s in enumerate(sources):
            for ti, t in enumerate(targets):
                score = name_score(s["name"], t["name"])
                if score == 0:
                    continue
                if _type_compatible(s.get("inferred_type"), t["type"]):
                    score = min(1.0, score + 0.2)
                else:
                    score *= 0.5
                if score >= MIN_PAIR_SCORE:
                    pairs.append((-score, si, ti))
        pairs.sort()
        used_s, used_t, out = set(), set(), []
        for neg, si, ti in pairs:
            if si in used_s or ti in used_t:
                continue
            used_s.add(si)
            used_t.add(ti)
            s, t = sources[si], targets[ti]
            out.append({"source": s["name"], "target": t["name"], "confidence": round(-neg, 2),
                        "rationale": "name similarity and type compatibility (offline heuristic)"})
        return ProviderResult(mappings=out, prompt_sha256=None, response_sha256=None)
