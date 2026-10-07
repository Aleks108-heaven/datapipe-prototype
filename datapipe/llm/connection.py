"""The saved model-server connection (address and model name): what `datapipe map --provider openai-compat` uses when
--base-url / --model are not given, and what the app's Settings page edits.

Kept in the user's own profile folder next to the API key, but in a separate file, because it holds nothing secret.
`list_models` is the "is the server there, and which models does it have?" check. It runs here, not in the browser, so
CORS cannot get in the way, and it follows the same rules as a real mapping call: https everywhere (plain http only to
this machine), no redirects, short timeouts, and no response body ever copied into a message."""
import json
import os
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import keystore
from .providers import _OPENER, ProviderError, _check_base_url, locality_of

PRESETS = [
    {"id": "ollama", "name": "Ollama", "base_url": "http://127.0.0.1:11434/v1"},
    {"id": "lmstudio", "name": "LM Studio", "base_url": "http://127.0.0.1:1234/v1"},
    {"id": "llamacpp", "name": "llama.cpp server", "base_url": "http://127.0.0.1:8080/v1"},
]
MAX_URL, MAX_MODEL, MAX_MODELS = 300, 200, 200
CHECK_TIMEOUT = 6
_lock = threading.Lock()
_CTRL = re.compile(r"[\x00-\x20\x7f]")


def path():
    return keystore.config_dir() / "llm-connection.json"


def check_url(raw):
    """The address, normalised (no trailing slash), or ValueError with a message a person can act on."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("enter the server address, for example http://127.0.0.1:11434/v1")
    url = raw.strip().rstrip("/")
    if len(url) > MAX_URL or _CTRL.search(url):
        raise ValueError("that address is not valid (no spaces or line breaks, at most %d characters)" % MAX_URL)
    if "://" not in url:
        raise ValueError("start the address with http:// (a server on this computer) or https:// (a remote server)")
    parts = urllib.parse.urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("the address must not contain a user name, password, '?' or '#'")
    try:
        parts.port
        _check_base_url(url)
    except (ProviderError, ValueError) as exc:
        raise ValueError(str(exc) if isinstance(exc, ProviderError) else "the port in the address is not a valid number")
    return url


def check_model(raw):
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("the model name must be text")
    model = raw.strip()
    if not model:
        return None
    if len(model) > MAX_MODEL or _CTRL.search(model):
        raise ValueError("the model name has no spaces or line breaks and is at most %d characters" % MAX_MODEL)
    return model


def load():
    """{'base_url': str | None, 'model': str | None}. A missing or damaged file means nothing is saved."""
    try:
        doc = json.loads(path().read_text(encoding="utf-8"))
        url = check_url(doc["base_url"])
        return {"base_url": url, "model": check_model(doc.get("model"))}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"base_url": None, "model": None}


def save(base_url, model):
    url, name = check_url(base_url), check_model(model)
    target = path()
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"base_url": url, "model": name}) + "\n")
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return {"base_url": url, "model": name}


def clear():
    with _lock:
        path().unlink(missing_ok=True)


def describe(conn):
    """What the page shows for a connection: the values plus where the data would go."""
    if not conn.get("base_url"):
        return {**conn, "locality": None, "on_this_machine": None}
    loc, here = locality_of(conn["base_url"], conn.get("model"))
    return {**conn, "locality": loc, "on_this_machine": here}


def _clean(name):
    return _CTRL.sub(" ", name).strip()[:120]


def list_models(base_url, model=None):
    """Ask the server for its models. Never raises: the result says what happened.
    {'state': ok | no_models | needs_key | not_found | unreachable | timeout | http_error | bad_response,
     'message': str, 'models': [str], 'ms': int, 'locality': 'local' | 'cloud'}"""
    url = check_url(base_url)
    loc, _ = locality_of(url, model)
    headers = {"accept": "application/json"}
    key = os.environ.get(keystore.ENV_VAR) or keystore.load_key()
    if key:
        headers["authorization"] = "Bearer " + key
    started = time.monotonic()

    def result(state, message, models=()):
        return {"state": state, "message": message, "models": list(models), "locality": loc,
                "ms": int((time.monotonic() - started) * 1000)}
    try:
        with _OPENER.open(urllib.request.Request(url + "/models", headers=headers), timeout=CHECK_TIMEOUT) as resp:
            raw = resp.read(1_000_000)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return result("needs_key", "The server asked for a key (HTTP %d). Save the key below, then check again." % exc.code)
        if exc.code == 404:
            return result("not_found", "The server answered, but not at this address (HTTP 404). The address usually ends with /v1.")
        return result("http_error", "The server answered with an error (HTTP %d)." % exc.code)
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            return result("timeout", "The server did not answer within %d seconds." % CHECK_TIMEOUT)
        if isinstance(exc.reason, ConnectionRefusedError):
            return result("unreachable", "Nothing is listening at this address. Is the model server running?")
        return result("unreachable", "Could not reach the server (%s). Check the address and your network." % type(exc.reason).__name__)
    except (socket.timeout, TimeoutError):
        return result("timeout", "The server did not answer within %d seconds." % CHECK_TIMEOUT)
    except OSError as exc:
        return result("unreachable", "Could not reach the server (%s)." % type(exc).__name__)
    try:
        doc = json.loads(raw)
        items = doc["data"] if isinstance(doc.get("data"), list) else doc["models"]
        names = []
        for it in items:
            if not isinstance(it, (str, dict)):
                continue
            name = it if isinstance(it, str) else (it.get("id") or it.get("name") or it.get("model"))
            if isinstance(name, str) and _clean(name) and _clean(name) not in names:
                names.append(_clean(name))
    except (ValueError, KeyError, TypeError, AttributeError):
        return result("bad_response", "The server answered, but not with a list of models. Is this the right address?")
    names = names[:MAX_MODELS]
    if not names:
        return result("no_models", "The server is there, but it has no models yet. Download or load one in the server's own app.")
    return result("ok", "Connected: %d model%s found." % (len(names), "" if len(names) == 1 else "s"), names)
