"""Policy tiers. The pipeline core is identical for all tiers; only these knobs differ."""
from dataclasses import dataclass, asdict

from .errors import DataPipeError


@dataclass(frozen=True)
class Policy:
    name: str
    max_file_bytes: int
    schema_mode: str        # "infer" | "confirm" | "registered"
    extra_columns: str      # "warn" | "block"   (columns in the file that the schema does not know)
    row_errors: str         # "quarantine" | "block"
    max_error_rate: float   # in quarantine mode: block the run if quarantined/total exceeds this
    mask_pii: bool          # mask PII in outputs AND keep PII columns out of the analysis engine
    require_signoff: bool   # results stay PENDING_SIGNOFF until a *different* person approves
    llm: str                # advisory flag for a future LLM layer: "cloud" | "cloud_masked" | "none"

    def as_dict(self):
        return asdict(self)


MB = 1024 * 1024

POLICIES = {
    "low": Policy("low", 100 * MB, "infer", "warn", "quarantine", 1.0, False, False, "cloud"),
    "business": Policy("business", 100 * MB, "confirm", "warn", "quarantine", 0.05, True, False, "cloud_masked"),
    "regulated": Policy("regulated", 50 * MB, "registered", "block", "block", 0.0, True, True, "none"),
}


def get_policy(name: str) -> Policy:
    try:
        return POLICIES[name]
    except KeyError:
        raise DataPipeError(f"unknown policy {name!r}; choose one of {sorted(POLICIES)}")
