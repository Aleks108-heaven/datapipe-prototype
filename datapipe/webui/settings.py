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
from ..llm import connection, keystore
from ..llm.providers import locality_of
from ..policy import POLICIES
from .i18n import LANGUAGES
from .service import ApiError

THEMES = ("system", "light", "dark")
DEFAULTS = {"actor": "", "policy": "business", "max_file_mb": None, "max_memory_gb": None, "theme": "system", "language": "system"}
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
    language = payload.get("language", "system")
    if language not in LANGUAGES:
        raise ApiError(400, "language must be system, en or uk")
    return {"actor": clean_name(actor), "policy": policy, "theme": theme, "language": language,
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

    # The LLM API key is not part of settings.json (that file lives in the work folder): it goes to a per-user file through
    # llm/keystore.py. These methods are the only way the page touches it, and none of them returns the key.
    @staticmethod
    def llm_key_status():
        return keystore.status()

    @staticmethod
    def set_llm_key(payload):
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        try:
            keystore.save_key(payload.get("key"))
        except ValueError as exc:
            raise ApiError(400, str(exc))
        except OSError:
            raise ApiError(500, "the key could not be saved (is the per-user folder writable?)")
        return keystore.status()

    @staticmethod
    def clear_llm_key():
        try:
            keystore.clear_key()
        except OSError:
            raise ApiError(500, "the saved key could not be removed")
        return keystore.status()

    # The saved model-server connection (address + model name): per user, like the key, but nothing secret in it.
    @staticmethod
    def llm_connection():
        return {"connection": connection.describe(connection.load()), "presets": connection.PRESETS}

    @staticmethod
    def _llm_fields(payload):
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid request")
        try:
            return connection.check_url(payload.get("base_url")), connection.check_model(payload.get("model"))
        except ValueError as exc:
            raise ApiError(400, str(exc))

    def save_llm_connection(self, payload):
        """A server that is not on this computer (or a model that runs remotely) needs an explicit yes first: the reply says so
        instead of saving, and the page asks again with confirm_remote."""
        url, model = self._llm_fields(payload)
        locality, _ = locality_of(url, model)
        if locality == "cloud" and payload.get("confirm_remote") is not True:
            return {"saved": False, "needs_confirmation": True, "connection": connection.describe({"base_url": url, "model": model})}
        try:
            saved = connection.save(url, model)
        except OSError:
            raise ApiError(500, "the connection could not be saved (is the per-user folder writable?)")
        return {"saved": True, "needs_confirmation": False, "connection": connection.describe(saved)}

    @staticmethod
    def clear_llm_connection():
        try:
            connection.clear()
        except OSError:
            raise ApiError(500, "the saved connection could not be removed")
        return {"connection": connection.describe(connection.load())}

    def check_llm_connection(self, payload):
        url, model = self._llm_fields(payload)
        return connection.list_models(url, model)

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
