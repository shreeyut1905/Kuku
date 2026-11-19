"""Model setup shared by the command line and the web UI (one place, same behaviour)."""
from __future__ import annotations

import copy
import os
import re
from typing import List, Optional, Tuple

from .llm import LLM, LLMConfig, lookup_price


class SetupError(ValueError):
    pass


def build_llms(*, model: Optional[str] = None, base_url: Optional[str] = None, api_key: Optional[str] = None,
               judge_model: Optional[str] = None, fallback_model: Optional[str] = None,
               workers: Optional[int] = None, max_cost: Optional[float] = None, thinking: bool = False,
               price_in: Optional[float] = None, price_out: Optional[float] = None,
               log_path: Optional[str] = None, say=print) -> Tuple[LLM, Optional[LLM]]:
    """Return (main LLM, optional separate judge LLM). The API key is used in memory only."""
    cfg = LLMConfig.from_env(model=model or None, base_url=base_url.rstrip("/") if base_url else None,
                             workers=workers)
    if api_key:
        cfg.api_key = api_key.strip()
    if thinking:
        cfg.extra_body = dict(cfg.extra_body or {}, enable_thinking=True)
        cfg.max_tokens = max(cfg.max_tokens, 6000)
    if not cfg.api_key and "localhost" not in cfg.base_url and "127.0.0.1" not in cfg.base_url:
        raise SetupError("No API key: set QUBRID_API_KEY (or LLM_API_KEY) in .env, enter one in the UI, or use rules-only mode.")
    if price_in is not None and price_out is not None:
        cfg.price_in, cfg.price_out = price_in, price_out
    else:
        cfg.price_in, cfg.price_out = lookup_price(cfg.base_url, cfg.api_key, cfg.model)
    if max_cost is not None:
        if cfg.price_in is None or cfg.price_out is None:
            raise SetupError("A budget cap needs prices: this provider does not publish them, so pass prices explicitly.")
        cfg.max_cost = max_cost

    def clone(model_id: str) -> LLMConfig:
        c = copy.deepcopy(cfg)
        c.model = model_id
        c.price_in, c.price_out = lookup_price(c.base_url, c.api_key, c.model)
        c.max_cost = max_cost if (max_cost is not None and c.price_in is not None and c.price_out is not None) else None
        return c

    fallback = None
    if fallback_model and fallback_model != cfg.model:
        fcfg = clone(fallback_model)
        fcfg.workers = 1
        fallback = LLM(fcfg, log_path=log_path)
        say(f"Fallback model (used only on content-policy refusals): {fcfg.model}")
    llm = LLM(cfg, log_path=log_path, fallback=fallback)
    say(f"Model: {cfg.model} @ {cfg.base_url}  price/1M tokens: in={cfg.price_in} out={cfg.price_out}  workers={cfg.workers}")
    judge_llm = None
    if judge_model and judge_model != cfg.model:
        jcfg = clone(judge_model)
        judge_llm = LLM(jcfg, log_path=log_path, fallback=fallback)
        say(f"Judge model: {jcfg.model}  price/1M tokens: in={jcfg.price_in} out={jcfg.price_out}")
    return llm, judge_llm


def fresh_log_path(out_dir: str) -> str:
    os.makedirs(os.path.join(out_dir, "raw"), exist_ok=True)
    log_path = os.path.join(out_dir, "raw", "llm_calls.jsonl")
    if os.path.exists(log_path):
        os.remove(log_path)  # one log per run; cached answers are re-logged with cached=true
    return log_path


def parse_episode_spec(spec: str) -> Optional[List[int]]:
    """'1-10' / '1,5,9' / '' -> list of episode numbers (None = all)."""
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if re.fullmatch(r"\d+\s*-\s*\d+", part):
            a, b = (int(x) for x in part.split("-"))
            out.update(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return sorted(out) or None
