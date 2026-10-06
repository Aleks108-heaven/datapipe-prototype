"""The API key for OpenAI-compatible servers (LM Studio, Ollama behind a proxy, hosted free tiers), saved from the app's
Settings page so it does not have to be typed into every terminal.

It is kept in a small file in the user's own profile folder, never in the work folder (that one can sit inside a git
repository or a synced folder) and never in settings.json. The file is plain text, protected only by the operating
system's file permissions, like the key stores of editor extensions. The page can set and remove the key but is never
sent it back; nothing here writes the key to a log, an error message, the audit log or a report."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ENV_VAR = "DATAPIPE_LLM_API_KEY"
CONFIG_DIR_ENV = "DATAPIPE_CONFIG_DIR"
MAX_KEY_LEN = 512
_lock = threading.Lock()


def config_dir():
    """Per-user folder: %LOCALAPPDATA%\\datapipe (does not roam), ~/Library/Application Support/datapipe, or
    $XDG_CONFIG_HOME/datapipe (~/.config/datapipe). DATAPIPE_CONFIG_DIR overrides it (used by the tests)."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override)
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "datapipe"


def key_path():
    return config_dir() / "llm.json"


def check_key(raw):
    """Return the key trimmed, or raise ValueError. Only visible ASCII is allowed: the key goes into an HTTP header, so a
    space, a line break or any other control character would be a header-injection risk, and real keys never contain them.
    The message never contains the key."""
    if not isinstance(raw, str):
        raise ValueError("the key must be text")
    key = raw.strip()
    if not key:
        raise ValueError("enter a key")
    if len(key) > MAX_KEY_LEN:
        raise ValueError(f"a key is at most {MAX_KEY_LEN} characters")
    if not all(0x21 <= ord(c) <= 0x7E for c in key):
        raise ValueError("a key has only visible ASCII characters, with no spaces or line breaks (check what you pasted)")
    return key


def load_key():
    """The saved key, or None (missing, unreadable or damaged file: treated as not set)."""
    try:
        stored = json.loads(key_path().read_text(encoding="utf-8"))
        return check_key(stored["api_key"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_key(raw):
    key = check_key(raw)
    path = key_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")      # created owner-only (0600) on Linux and macOS
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"api_key": key}) + "\n")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def clear_key():
    with _lock:
        key_path().unlink(missing_ok=True)


def status():
    """What the page may know: whether a key is saved, whether the environment variable overrides it, and where the file is."""
    return {"saved": load_key() is not None, "environment": bool(os.environ.get(ENV_VAR)), "path": str(key_path())}
