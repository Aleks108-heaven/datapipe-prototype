import hashlib
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from ..errors import DataPipeError
from . import keystore
from .prompt import SYSTEM, render_user


class ProviderError(DataPipeError):
    """Provider failure. Messages never include request or response bodies."""


_LOOPBACK = ("localhost", "127.0.0.1", "::1")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib follows 301/302/303 on a POST and re-sends the request headers - including x-api-key / Authorization - to
    whatever host the Location names. An LLM endpoint has no reason to redirect, so a redirect is an error."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _check_base_url(base_url):
    """https everywhere; plain http only to this machine. Returns the lower-cased host. Keys must never travel in clear text."""
    parts = urllib.parse.urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host:
        return host
    if parts.scheme == "http" and host in _LOOPBACK:
        return host
    raise ProviderError("the LLM base URL must use https (plain http is accepted only for localhost / 127.0.0.1)")


def locality_of(base_url, model):
    """('local' | 'cloud', url_is_on_this_machine). Ollama (and similar) can proxy a model whose name ends in "cloud"
    (e.g. "gpt-oss:120b-cloud") to a remote service while the URL stays localhost: that is cloud egress, so it must not be
    recorded as "nothing left the machine"."""
    on_this_machine = _check_base_url(base_url) in _LOOPBACK
    proxies_to_cloud = re.search(r"(^|[:\-_/])cloud$", (model or "").lower()) is not None
    return ("local" if on_this_machine and not proxies_to_cloud else "cloud"), on_this_machine


def _discard(err):
    """Close the response an HTTPError still holds when we are not going to read it. Its socket and temporary file would otherwise wait
    for garbage collection, which Python 3.14 reports as a ResourceWarning (and an app that keeps running should not leave handles around)."""
    try:
        err.close()
    except Exception:                                  # an HTTPError built without a response has nothing to close
        pass


@dataclass
class ProviderResult:
    mappings: list                      # [{"source","target","confidence","rationale"}]
    prompt_sha256: str = None
    response_sha256: str = None
    malformed_items: int = 0


class MappingProvider:
    name = "base"
    locality = "offline"                # "offline": no model; "local": a model on this machine; "cloud": request is sent out
    model = None

    def propose(self, request: dict) -> ProviderResult:  # pragma: no cover - interface
        raise NotImplementedError


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def parse_mappings(text: str):
    """Strictly parse an LLM reply. Returns (mappings, malformed_count). Anything unexpected -> ProviderError."""
    t = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if fence:
        t = fence.group(1)
    try:
        doc = json.loads(t)
    except ValueError:
        raise ProviderError("LLM reply is not valid JSON")
    if not isinstance(doc, dict) or not isinstance(doc.get("mappings"), list):
        raise ProviderError("LLM reply does not have a 'mappings' list")
    out, bad = [], 0
    for item in doc["mappings"]:
        if not isinstance(item, dict):
            bad += 1
            continue
        src, tgt, conf = item.get("source"), item.get("target"), item.get("confidence")
        if not isinstance(src, str) or not isinstance(tgt, str) or isinstance(conf, bool) \
                or not isinstance(conf, (int, float)) or conf != conf:
            bad += 1
            continue
        why = item.get("rationale")
        out.append({"source": src, "target": tgt, "confidence": min(1.0, max(0.0, float(conf))),
                    "rationale": _CTRL.sub(" ", why)[:200] if isinstance(why, str) else ""})
    return out, bad


class LLMProvider(MappingProvider):
    locality = "cloud"

    def _complete(self, system: str, user: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def propose(self, request):
        user = render_user(request)
        prompt_hash = hashlib.sha256((SYSTEM + "\n" + user).encode()).hexdigest()
        reply = self._complete(SYSTEM, user)
        mappings, bad = parse_mappings(reply)
        return ProviderResult(mappings, prompt_hash, hashlib.sha256(reply.encode()).hexdigest(), bad)


class AnthropicProvider(LLMProvider):
    """Calls the Anthropic Messages API over HTTPS. The model must be chosen explicitly by the operator."""
    name = "anthropic"

    def __init__(self, model=None, api_key=None, base_url="https://api.anthropic.com", timeout=60, max_tokens=2000):
        self.model = model or os.environ.get("DATAPIPE_LLM_MODEL")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.model:
            raise ProviderError("no model configured: pass --model or set DATAPIPE_LLM_MODEL")
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        self.base_url = base_url.rstrip("/")
        _check_base_url(self.base_url)
        self.timeout = timeout
        self.max_tokens = max_tokens

    def _complete(self, system, user):
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens, "temperature": 0,
                           "system": system, "messages": [{"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(
            self.base_url + "/v1/messages", data=body, method="POST",
            headers={"content-type": "application/json", "x-api-key": self.api_key,
                     "anthropic-version": "2023-06-01"})
        try:
            with _OPENER.open(req, timeout=self.timeout) as resp:
                raw = resp.read(2_000_000)
        except urllib.error.HTTPError as exc:
            _discard(exc)
            raise ProviderError(f"LLM API returned HTTP {exc.code}")
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise ProviderError(f"LLM API request failed ({type(exc).__name__})")
        try:
            doc = json.loads(raw)
            text = "".join(b.get("text", "") for b in doc["content"] if b.get("type") == "text")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError("LLM API returned an unexpected response shape")
        if not text:
            raise ProviderError("LLM API returned no text")
        return text


class OpenAICompatProvider(LLMProvider):
    """Calls any OpenAI-compatible /chat/completions endpoint: Ollama, LM Studio, llama.cpp, vLLM, and hosted free tiers
    (Groq, OpenRouter, Gemini's compatible endpoint). A loopback base URL means the data stays on this machine
    (locality "local"); any other host is treated as "cloud". The api key is optional for local servers."""
    name = "openai-compat"

    def __init__(self, model=None, api_key=None, base_url="http://127.0.0.1:11434/v1", timeout=120, max_tokens=2000):
        self.model = model or os.environ.get("DATAPIPE_LLM_MODEL")
        # the explicit argument wins, then the environment variable, then the key saved on the app's Settings page
        self.api_key = api_key or os.environ.get(keystore.ENV_VAR) or keystore.load_key()
        if not self.model:
            raise ProviderError("no model configured: pass --model or set DATAPIPE_LLM_MODEL")
        self.base_url = base_url.rstrip("/")
        self.locality, on_this_machine = locality_of(self.base_url, self.model)
        if not on_this_machine and not self.api_key:
            raise ProviderError("no API key (needed for a non-local endpoint): save one on the app's Settings page "
                                "or set DATAPIPE_LLM_API_KEY")
        self.timeout = timeout
        self.max_tokens = max_tokens

    # Small local models often emit almost-valid JSON (a stray bracket, prose around it). The parser stays strict; instead the
    # server is asked to constrain the output to this schema (Ollama, LM Studio and several hosted tiers support it).
    _SCHEMA = {"type": "json_schema", "json_schema": {"name": "column_mappings", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["mappings"],
        "properties": {"mappings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["source", "target", "confidence", "rationale"],
            "properties": {"source": {"type": "string"}, "target": {"type": "string"},
                           "confidence": {"type": "number"}, "rationale": {"type": "string"}}}}}}}}

    def _post(self, payload):
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(payload).encode(),
                                     method="POST", headers=headers)
        with _OPENER.open(req, timeout=self.timeout) as resp:
            return resp.read(2_000_000)

    def _complete(self, system, user):
        payload = {"model": self.model, "max_tokens": self.max_tokens, "temperature": 0, "stream": False,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "response_format": self._SCHEMA}
        try:
            try:
                raw = self._post(payload)
            except urllib.error.HTTPError as exc:
                if exc.code not in (400, 422):
                    raise
                _discard(exc)
                del payload["response_format"]                   # this server does not support constrained output: ask once more
                raw = self._post(payload)
        except urllib.error.HTTPError as exc:
            _discard(exc)
            hint = (" - the server rejected the API key: save a valid one on the app's Settings page or set DATAPIPE_LLM_API_KEY"
                    if exc.code in (401, 403) else "")
            raise ProviderError(f"LLM API returned HTTP {exc.code}{hint}")
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise ProviderError(f"LLM API request failed ({type(exc).__name__})")
        try:
            text = json.loads(raw)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("LLM API returned an unexpected response shape")
        if not isinstance(text, str) or not text:
            raise ProviderError("LLM API returned no text")
        return text


_ = field
