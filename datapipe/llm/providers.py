import hashlib
import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..errors import DataPipeError
from .prompt import SYSTEM, render_user


class ProviderError(DataPipeError):
    """Provider failure. Messages never include request or response bodies."""


@dataclass
class ProviderResult:
    mappings: list                      # [{"source","target","confidence","rationale"}]
    prompt_sha256: str = None
    response_sha256: str = None
    malformed_items: int = 0


class MappingProvider:
    name = "base"
    locality = "offline"                # "offline": nothing leaves the machine; "cloud": request is sent out
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(2_000_000)
        except urllib.error.HTTPError as exc:
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


_ = field
