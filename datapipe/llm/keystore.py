"""The API key for OpenAI-compatible servers (LM Studio, Ollama behind a proxy, hosted free tiers), saved from the app's
Settings page so it does not have to be typed into every terminal.

Where it is kept:
  * On Windows, in the Windows Credential Manager: the system's own secret store, encrypted for the person's Windows login. No file
    holds the key, so a copy of the user folder, a backup or another Windows user does not get it.
  * Everywhere else, and on Windows when the Credential Manager refuses (or DATAPIPE_KEY_STORE=file), in a small file in the user's own
    profile folder, protected only by the operating system's file permissions (0600 on Linux and macOS), like the key stores of editor
    extensions. macOS Keychain and the Linux Secret Service are not used: their command-line tools would put the key on a command line,
    which any program on the machine can read.
Never in the work folder (that one can sit inside a git repository or a synced folder) and never in settings.json. A key found in the
old file is moved into the Credential Manager the first time it is read there, and the file is deleted once the move is verified.

Neither place stops a program that runs as the same person from asking for the key: this protects against copied files and backups,
not against malware already running as you. The page can set and remove the key but is never sent it back; nothing here writes the
key to a log, an error message, the audit log or a report."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ENV_VAR = "DATAPIPE_LLM_API_KEY"
CONFIG_DIR_ENV = "DATAPIPE_CONFIG_DIR"
STORE_ENV = "DATAPIPE_KEY_STORE"                      # "auto" (default) or "file"
TARGET_ENV = "DATAPIPE_CREDENTIAL_TARGET"             # the Credential Manager entry's name (the tests use their own)
DEFAULT_TARGET = "datapipe: LLM API key"
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


# ------------------------------------------------------------------ the Windows Credential Manager (ctypes, no extra package)
_CRED_TYPE_GENERIC, _CRED_PERSIST_LOCAL_MACHINE, _ERROR_NOT_FOUND = 1, 2, 1168


def _target():
    return os.environ.get(TARGET_ENV) or DEFAULT_TARGET


def _cm_available():
    return sys.platform == "win32" and os.environ.get(STORE_ENV, "auto").lower() != "file"


def _cm_api():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    adv.CredWriteW.restype = wintypes.BOOL
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredDeleteW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    adv.CredFree.restype = None
    return ctypes, CREDENTIAL, adv


def _cm_read():
    """The stored key, or None when there is none. OSError when Windows refuses."""
    ctypes, CREDENTIAL, adv = _cm_api()
    found = ctypes.POINTER(CREDENTIAL)()
    if not adv.CredReadW(_target(), _CRED_TYPE_GENERIC, 0, ctypes.byref(found)):
        err = ctypes.get_last_error()
        if err == _ERROR_NOT_FOUND:
            return None
        raise OSError(err, "the Windows Credential Manager could not be read")
    try:
        blob = ctypes.string_at(found.contents.CredentialBlob, found.contents.CredentialBlobSize)
    finally:
        adv.CredFree(found)
    return blob.decode("utf-8", errors="replace")


def _cm_write(key):
    ctypes, CREDENTIAL, adv = _cm_api()
    blob = key.encode("utf-8")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL()
    cred.Type, cred.TargetName, cred.UserName = _CRED_TYPE_GENERIC, _target(), "datapipe"
    cred.CredentialBlobSize, cred.CredentialBlob = len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = _CRED_PERSIST_LOCAL_MACHINE              # stays on this computer: not copied to other machines with a roaming profile
    if not adv.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(ctypes.get_last_error(), "the Windows Credential Manager could not be written")


def _cm_delete():
    ctypes, _, adv = _cm_api()
    if not adv.CredDeleteW(_target(), _CRED_TYPE_GENERIC, 0):
        err = ctypes.get_last_error()
        if err != _ERROR_NOT_FOUND:
            raise OSError(err, "the Windows Credential Manager entry could not be removed")


# ------------------------------------------------------------------ the file
def _file_load():
    try:
        stored = json.loads(key_path().read_text(encoding="utf-8"))
        return check_key(stored["api_key"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _file_save(key):
    path = key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")      # created owner-only (0600) on Linux and macOS
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"api_key": key}) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _file_clear():
    key_path().unlink(missing_ok=True)


# ------------------------------------------------------------------ what the rest of the program uses
def _cm_load_checked():
    """The key from the Credential Manager, or None (nothing there, damaged, or Windows refuses)."""
    if not _cm_available():
        return None
    try:
        raw = _cm_read()
        return check_key(raw) if raw is not None else None
    except (OSError, ValueError):
        return None


def load_key():
    """The saved key, or None (missing, unreadable or damaged: treated as not set). A key still sitting in the old file is moved into
    the Credential Manager here, and the file is removed only after the key has been read back from there."""
    with _lock:
        key = _cm_load_checked()
        if key is not None:
            return key
        key = _file_load()
        if key is not None and _cm_available():
            try:
                _cm_write(key)
                if _cm_load_checked() == key:
                    _file_clear()
            except OSError:
                pass                                          # the Credential Manager refused: the file keeps working as before
        return key


def save_key(raw):
    key = check_key(raw)
    with _lock:
        if _cm_available():
            try:
                _cm_write(key)
                if _cm_load_checked() == key:
                    _file_clear()                             # no plain-text copy is left behind
                    return
            except OSError:
                pass                                          # fall back to the file rather than losing the key
        _file_save(key)


def clear_key():
    with _lock:
        _file_clear()
        if _cm_available():
            try:
                _cm_delete()
            except OSError:
                pass


def status():
    """What the page may know: whether a key is saved, whether the environment variable overrides it, where it is kept
    ("credential-manager" or "file"; where the next save would go if none is saved) and the file's path."""
    key = load_key()
    in_cm = _cm_available() and _cm_load_checked() is not None
    store = "credential-manager" if (in_cm or (key is None and _cm_available())) else "file"
    return {"saved": key is not None, "environment": bool(os.environ.get(ENV_VAR)), "store": store, "path": str(key_path())}
