"""Shared test helpers (importable because pytest puts tests/ on sys.path)."""
import json
import os
from pathlib import Path

from datapipe.ingest import parse_csv, read_source
from datapipe.llm import MappingProvider, ProviderResult
from datapipe.mapping import propose_mapping
from datapipe.policy import get_policy
from datapipe.schema import load_schema

def try_symlink(target, link):
    """Create a symlink; False if the OS refuses (Windows needs Developer Mode or admin), so callers can skip that part."""
    try:
        os.symlink(target, link)
        return True
    except (OSError, NotImplementedError):
        return False


ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
TARGET = load_schema(EX / "schema_sales.json")


class Scripted(MappingProvider):
    """Test double standing in for an LLM: returns whatever the test dictates."""
    def __init__(self, mappings, locality="cloud"):
        self.name, self.locality, self.model = "scripted", locality, "test-model"
        self._mappings, self.seen = mappings, []

    def propose(self, request):
        self.seen.append(request)
        return ProviderResult(self._mappings, "p" * 64, "r" * 64)


def m(source, target, conf=0.95, rationale="test"):
    return {"source": source, "target": target, "confidence": conf, "rationale": rationale}


GOOD = [m("Order No", "order_id"), m("Buyer Email", "customer_email"), m("Area", "region"),
        m("Total (EUR)", "amount"), m("Ordered On", "order_date"), m("Paid?", "paid")]


def make_proposal(workdir, mappings=GOOD, *, tbl=None, actor="alice", policy="business", name=None,
                  target=None, read_options=None):
    """Create a proposal file in <workdir>/mappings and return (proposal dict, path)."""
    tbl = tbl or read_source(EX / "sales_renamed.csv", max_bytes=10 ** 8)
    prop = propose_mapping(tbl, target or TARGET, Scripted(mappings), get_policy(policy),
                           input_name=name or "sales_renamed.csv", actor=actor, read_options=read_options)
    d = Path(workdir) / "mappings"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"mapping-{prop['proposal_sha256'][:10]}.json"
    path.write_text(json.dumps(prop, indent=2), encoding="utf-8")
    return prop, path


_ = parse_csv
