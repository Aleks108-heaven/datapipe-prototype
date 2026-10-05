"""User settings for the local app, kept in <work folder>/settings.json (not in the browser: the app picks a random port, so
browser storage would be lost every time it restarts). Everything is validated; a damaged or hand-edited file falls back to
defaults instead of breaking the app."""
import json
import math
import os
import tempfile
import threading
from pathlib import Path

from ..identity import clean_name
from ..policy import POLICIES
from .service import ApiError

THEMES = ("system", "light", "dark")
DEFAULTS = {"actor": "", "policy": "business", "max_file_mb": None, "max_memory_gb": None, "theme": "system"}
FILE_MB_RANGE = (1, 100_000)
MEMORY_GB_RANGE = (0.5, 1024)


def _number(value, lo, hi, label):
    """None/'' = not set (use the policy's own limit). Otherwise a finite number inside lo..hi."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ApiError(400, f"{label} must be a number, or empty to use the policy's own limit")
    if not lo <= value <= hi:
        raise ApiError(400, f"{label} must be between {lo:g} and {hi:g}")
    return value


def validate(payload):
    if not isinstance(payload, dict):
        raise ApiError(400, "invalid request")
    actor = payload.get("actor", "")
    if not isinstance(actor, str) or len(actor) > 80:
        raise ApiError(400, "your name must be text of at most 80 characters")
    policy = payload.get("policy", "business")
    if policy not in POLICIES:
        raise ApiError(400, "choose one of the policies")
    theme = payload.get("theme", "system")
    if theme not in THEMES:
        raise ApiError(400, "theme must be system, light or dark")
    return {"actor": clean_name(actor), "policy": policy, "theme": theme,
            "max_file_mb": _number(payload.get("max_file_mb"), *FILE_MB_RANGE, "the file-size limit (MB)"),
            "max_memory_gb": _number(payload.get("max_memory_gb"), *MEMORY_GB_RANGE, "the memory limit (GB)")}


class SettingsStore:
    def __init__(self, workdir):
        self.path = Path(workdir) / "settings.json"
        self._lock = threading.Lock()

    def get(self):
        """Stored settings merged over the defaults; anything invalid in the file is ignored, key by key."""
        out = dict(DEFAULTS)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return out
        if not isinstance(stored, dict):
            return out
        for key in DEFAULTS:
            if key in stored:
                try:
                    out[key] = validate({**DEFAULTS, **out, key: stored[key]})[key]
                except ApiError:
                    pass
        return out

    def update(self, payload):
        new = validate(payload)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(new, indent=2) + "\n")
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return new
