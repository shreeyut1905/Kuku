"""Provider-agnostic chat client (any OpenAI-compatible /chat/completions API).

Swap models or providers with environment variables (or CLI flags); no code changes:

    LLM_BASE_URL   default https://platform.qubrid.com/v1   (Qubrid AI)
    LLM_API_KEY    falls back to QUBRID_API_KEY
    LLM_MODEL      default zai-org/GLM-4.7-Flash             (cheapest on Qubrid: $0)
    LLM_EXTRA_BODY JSON merged into every request, default {"enable_thinking": false}
    LLM_WORKERS    parallel requests (Qubrid free tier allows 1)

Works the same with OpenAI, Groq, Together, OpenRouter, Gemini's OpenAI endpoint,
Ollama/vLLM (local), or a LiteLLM proxy in front of any other vendor.

Stdlib only. Every response is cached on disk, so re-running a show is instant and
reproducible. Every call is logged to raw/llm_calls.jsonl, unedited.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_BASE_URL = "https://platform.qubrid.com/v1"
DEFAULT_MODEL = "zai-org/GLM-4.7-Flash"
DEFAULT_EXTRA = {"enable_thinking": False}
_LOG_LOCK = threading.Lock()   # several LLM instances (miner + judge) may share one log file


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Never overrides real env vars."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class LLMConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    extra_body: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_EXTRA))
    temperature: float = 0.3
    max_tokens: int = 2500
    timeout: int = 240
    max_retries: int = 6
    workers: int = 1
    cache_dir: str = ".cache/llm"
    price_in: Optional[float] = None   # $ per 1M input tokens (None = look up / unknown)
    price_out: Optional[float] = None
    max_cost: Optional[float] = None   # hard budget in $ for live calls in this run (None = no cap)

    @classmethod
    def from_env(cls, **overrides) -> "LLMConfig":
        extra_raw = os.environ.get("LLM_EXTRA_BODY")
        base = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        if extra_raw is not None:
            extra = json.loads(extra_raw) if extra_raw.strip() else {}
        else:
            # enable_thinking is a Qubrid/vLLM-style switch; don't send it elsewhere by default
            extra = dict(DEFAULT_EXTRA) if "qubrid" in base else {}
        cfg = cls(
            base_url=base,
            api_key=os.environ.get("LLM_API_KEY") or os.environ.get("QUBRID_API_KEY", ""),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
            extra_body=extra,
            workers=int(os.environ.get("LLM_WORKERS", "1")),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        return cfg


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    """The provider or the model declined the content (moderation filter / policy refusal).
    Retrying the same model is pointless; a fallback model may accept it."""


_REFUSAL = re.compile(
    r"(content policy|inappropriate content|can(?:no|')t (?:help|assist|comply|generate)|unable to (?:help|assist|"
    r"comply|generate|provide)|i(?: a|')m sorry,? (?:but )?(?:i|this)|violates? (?:our|the) (?:usage )?polic)", re.I)


def looks_like_refusal(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and "{" not in t[:200] and bool(_REFUSAL.search(t[:400]))


class LLM:
    def __init__(self, cfg: LLMConfig, log_path: Optional[str] = None, fallback: Optional["LLM"] = None):
        self.cfg = cfg
        self.log_path = log_path
        self.fallback = fallback          # used only when this model refuses (content policy / moderation)
        self.cancel_event = threading.Event()   # set by the web UI's Cancel button
        self.refusals = 0
        self.fallback_used = 0
        self._lock = threading.Lock()
        self.calls = 0
        self.cache_hits = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.seconds = 0.0
        self.failures = 0
        # what cached answers originally cost (so a re-run from cache still reports the true per-show cost)
        self.cached_prompt_tokens = 0
        self.cached_completion_tokens = 0
        self.cached_seconds = 0.0
        os.makedirs(cfg.cache_dir, exist_ok=True)
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # ------------------------------------------------------------------ core
    def _key(self, messages, temperature, max_tokens) -> str:
        blob = json.dumps(
            [self.cfg.base_url, self.cfg.model, self.cfg.extra_body, messages, temperature, max_tokens],
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def chat(self, messages: List[Dict[str, str]], *, tag: str = "", temperature: Optional[float] = None,
             max_tokens: Optional[int] = None, use_cache: bool = True) -> str:
        temperature = self.cfg.temperature if temperature is None else temperature
        max_tokens = self.cfg.max_tokens if max_tokens is None else max_tokens
        key = self._key(messages, temperature, max_tokens)
        cpath = os.path.join(self.cfg.cache_dir, key + ".json")
        if use_cache and os.path.exists(cpath):
            with open(cpath, encoding="utf-8") as f:
                rec = json.load(f)
            with self._lock:
                self.cache_hits += 1
                u = rec.get("usage") or {}
                self.cached_prompt_tokens += int(u.get("prompt_tokens") or 0)
                self.cached_completion_tokens += int(u.get("completion_tokens") or 0)
                self.cached_seconds += float(rec.get("seconds") or 0.0)
            self._log(tag, messages, rec, cached=True)
            return rec["content"]

        if self.cancel_event.is_set():
            raise LLMError("run cancelled by the user")
        if self.cfg.max_cost is not None and self.spent() >= self.cfg.max_cost:
            raise LLMError(f"budget cap reached (${self.spent():.4f} >= ${self.cfg.max_cost:.2f}); no more live calls")
        body = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        body.update(self.cfg.extra_body or {})
        data = json.dumps(body).encode("utf-8")
        url = self.cfg.base_url + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        last_err = ""
        for attempt in range(self.cfg.max_retries + 1):
            t0 = time.time()
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                dt = time.time() - t0
                choice = (payload.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                content = msg.get("content") or ""
                if not content.strip():
                    raise LLMError(f"empty content (finish_reason={choice.get('finish_reason')})")
                usage = payload.get("usage") or {}
                rec = {
                    "content": content,
                    "finish_reason": choice.get("finish_reason"),
                    "usage": usage,
                    "model": payload.get("model", self.cfg.model),
                    "seconds": round(dt, 2),
                }
                with self._lock:
                    self.calls += 1
                    self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
                    self.completion_tokens += int(usage.get("completion_tokens") or 0)
                    self.seconds += dt
                tmp = cpath + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(rec, f, ensure_ascii=False)
                os.replace(tmp, cpath)
                self._log(tag, messages, rec, cached=False)
                return content
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {detail}"
                retryable = e.code in (408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524)
                if e.code in (400, 403, 422) and _REFUSAL.search(detail or ""):
                    with self._lock:
                        self.failures += 1
                    self._log(tag, messages, {"error": "refused: " + last_err}, cached=False)
                    raise LLMRefusal(f"refused by provider/model [{tag}]: {last_err}")
                if not retryable:
                    break
                wait = _retry_after(e) or min(60.0, 3.0 * (2 ** attempt)) + random.uniform(0, 1.5)
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, LLMError, json.JSONDecodeError) as e:
                last_err = f"{type(e).__name__}: {e}"
                wait = min(60.0, 3.0 * (2 ** attempt)) + random.uniform(0, 1.5)
            if attempt < self.cfg.max_retries:
                time.sleep(wait)
        with self._lock:
            self.failures += 1
        self._log(tag, messages, {"error": last_err}, cached=False)
        raise LLMError(f"LLM call failed after retries [{tag}]: {last_err}")

    def chat_json(self, messages: List[Dict[str, str]], *, tag: str = "", **kw) -> Any:
        """Chat and parse a JSON object; one automatic 'fix your JSON' retry. If the model or
        provider refuses the content, hand the same request to the fallback model (if any)."""
        try:
            return self._chat_json_once(messages, tag=tag, **kw)
        except LLMRefusal:
            with self._lock:
                self.refusals += 1
            if self.fallback is None:
                raise
            with self._lock:
                self.fallback_used += 1
            return self.fallback.chat_json(messages, tag=tag + f":fallback({self.fallback.cfg.model})", **kw)

    def _chat_json_once(self, messages: List[Dict[str, str]], *, tag: str = "", **kw) -> Any:
        text = self.chat(messages, tag=tag, **kw)
        if looks_like_refusal(text):
            raise LLMRefusal(f"model refused [{tag}]: {text[:120]}")
        try:
            return extract_json(text)
        except ValueError as e:
            fix = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"That was not valid JSON ({e}). Reply again with ONLY the corrected JSON object, no prose, no code fences."},
            ]
            text2 = self.chat(fix, tag=tag + ":jsonfix", **kw)
            if looks_like_refusal(text2):
                raise LLMRefusal(f"model refused [{tag}]: {text2[:120]}")
            return extract_json(text2)

    def spent(self) -> float:
        """$ spent on live calls so far (0 if prices are unknown, so set prices when capping)."""
        if self.cfg.price_in is None or self.cfg.price_out is None:
            return 0.0
        return (self.prompt_tokens * self.cfg.price_in + self.completion_tokens * self.cfg.price_out) / 1e6

    # ------------------------------------------------------------------ logging / stats
    def _log(self, tag, messages, rec, cached):
        if not self.log_path:
            return
        entry = {
            "tag": tag, "model": self.cfg.model, "base_url": self.cfg.base_url, "cached": cached,
            "request_messages": messages, "response": rec,
        }
        with _LOG_LOCK:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def stats(self) -> Dict[str, Any]:
        cost = None
        all_in = self.prompt_tokens + self.cached_prompt_tokens
        all_out = self.completion_tokens + self.cached_completion_tokens
        full_cost = None
        if self.cfg.price_in is not None and self.cfg.price_out is not None:
            cost = (self.prompt_tokens * self.cfg.price_in + self.completion_tokens * self.cfg.price_out) / 1e6
            full_cost = (all_in * self.cfg.price_in + all_out * self.cfg.price_out) / 1e6
        return {
            "per_show_if_run_fresh": {"calls": self.calls + self.cache_hits, "prompt_tokens": all_in,
                                      "completion_tokens": all_out,
                                      "model_seconds": round(self.seconds + self.cached_seconds, 1),
                                      "cost_usd": None if full_cost is None else round(full_cost, 4)},
            "model": self.cfg.model, "base_url": self.cfg.base_url,
            "live_calls": self.calls, "cache_hits": self.cache_hits, "failed_calls": self.failures,
            "refusals": self.refusals, "sent_to_fallback": self.fallback_used,
            "fallback_model": self.fallback.cfg.model if self.fallback else None,
            "fallback_stats": self.fallback.stats() if self.fallback else None,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "llm_seconds": round(self.seconds, 1),
            "price_per_1M_in": self.cfg.price_in, "price_per_1M_out": self.cfg.price_out,
            "cost_usd": None if cost is None else round(cost, 4),
        }


def _retry_after(e: urllib.error.HTTPError) -> Optional[float]:
    try:
        v = e.headers.get("Retry-After")
        return float(v) if v else None
    except Exception:
        return None


def extract_json(text: str) -> Any:
    """Pull the JSON object out of a model reply (handles ```json fences, prose before/after,
    stray braces in the prose, trailing commas). Prefers an object over a bare array."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    dec = json.JSONDecoder()
    if t.startswith("["):                      # the whole reply is an array: keep it whole
        try:
            obj, _ = dec.raw_decode(t)
            return obj
        except json.JSONDecodeError:
            pass
    found_list = None
    for variant in (t, re.sub(r",\s*([}\]])", r"\1", t)):
        for i, ch in enumerate(variant):
            if ch not in "{[":
                continue
            try:
                obj, _ = dec.raw_decode(variant[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
            if found_list is None and isinstance(obj, list):
                found_list = obj
    if found_list is not None:
        return found_list
    raise ValueError("no parseable JSON object found")


def list_models(base_url: str, api_key: str) -> List[Dict[str, Any]]:
    """GET /models; Qubrid includes pricing per 1M tokens, which we use for cost estimates."""
    req = urllib.request.Request(base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, list):
        return payload
    return payload.get("data", []) if isinstance(payload, dict) else []


def lookup_price(base_url: str, api_key: str, model: str):
    try:
        for m in list_models(base_url, api_key):
            pr = m.get("pricing") if isinstance(m, dict) else None
            if m.get("id") == model and isinstance(pr, dict) and pr.get("input") is not None and pr.get("output") is not None:
                return float(pr["input"]), float(pr["output"])
    except Exception:
        pass
    return None, None
