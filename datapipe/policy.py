"""Policy tiers. The pipeline core is identical for all tiers; only these knobs differ."""
from dataclasses import dataclass, asdict, replace

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
    max_memory_bytes: int = 6 * 1024 ** 3   # estimated RAM the parsed file may need (rows and columns, not just bytes)

    def as_dict(self):
        return asdict(self)

    def with_limits(self, max_file_mb=None, max_memory_gb=None):
        """A copy with explicit size limits (command-line override). The effective limits end up in result.json."""
        changes = {}
        if max_file_mb is not None:
            if max_file_mb <= 0:
                raise DataPipeError("--max-file-mb must be positive")
            changes["max_file_bytes"] = int(max_file_mb * MB)
        if max_memory_gb is not None:
            if max_memory_gb <= 0:
                raise DataPipeError("--max-memory-gb must be positive")
            changes["max_memory_bytes"] = int(max_memory_gb * 1024 ** 3)
        return replace(self, **changes) if changes else self


MB = 1024 * 1024

# max_file_bytes bounds the bytes read; max_memory_bytes bounds the *estimated* RAM for the parsed rows, which is what
# actually runs out first (a file of many tiny rows needs ~300x its size, a normal table ~25x). 6 GB suits a 16 GB laptop.
POLICIES = {
    "low": Policy("low", 300 * MB, "infer", "warn", "quarantine", 1.0, False, False, "cloud"),
    "business": Policy("business", 300 * MB, "confirm", "warn", "quarantine", 0.05, True, False, "cloud_masked"),
    "regulated": Policy("regulated", 150 * MB, "registered", "block", "block", 0.0, True, True, "none"),
}


def get_policy(name: str) -> Policy:
    try:
        return POLICIES[name]
    except KeyError:
        raise DataPipeError(f"unknown policy {name!r}; choose one of {sorted(POLICIES)}")
