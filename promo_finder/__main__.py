"""Command line entry point.

    python3 -m promo_finder run  <folder of episode .txt files>  [--out outputs/my-show]
    python3 -m promo_finder models            # list the provider's models, cheapest first
    python3 -m promo_finder parse <folder>    # check that a new show's scripts parse

Swap the model:  --model Qwen/Qwen3.5-Flash   or  LLM_MODEL=... in .env
Swap provider:   --base-url https://api.openai.com/v1 --model gpt-4.1-mini  (key in LLM_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

from .llm import LLMConfig, list_models, load_dotenv


def _parse_eps(spec: str):
    from .runner import parse_episode_spec
    return parse_episode_spec(spec)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "show"


def main(argv=None) -> int:
    load_dotenv(os.path.join(os.getcwd(), ".env"))
    ap = argparse.ArgumentParser(prog="promo_finder", description="Find and rank 20-60 s promos in microdrama screenplays.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="find promos in a folder of episode scripts")
    r.add_argument("input", help="folder with one .txt screenplay per episode")
    r.add_argument("--out", help="output folder (default outputs/<show-name>)")
    r.add_argument("--model", help="model id (default: LLM_MODEL or zai-org/GLM-4.7-Flash)")
    r.add_argument("--base-url", help="OpenAI-compatible base URL (default: LLM_BASE_URL or Qubrid)")
    r.add_argument("--workers", type=int, help="parallel AI requests (Qubrid free tier: 1)")
    r.add_argument("--per-episode", type=int, default=3, help="max promo candidates per episode (default 3)")
    r.add_argument("--trailers", type=int, default=4, help="cross-episode trailers to build (default 4, 0 = off)")
    r.add_argument("--episodes", help="subset, e.g. '1-10' or '1,5,9'")
    r.add_argument("--no-llm", action="store_true", help="rules-only baseline, no AI calls")
    r.add_argument("--no-judge", action="store_true", help="skip the blind judge pass")
    r.add_argument("--recut-hooks", action="store_true",
                   help="extra AI pass: move each clip's start to its strongest first line (1 call per clip)")
    r.add_argument("--judge-model", help="use a different (e.g. stronger) model for the blind judge")
    r.add_argument("--fallback-model", help="model to retry with when the main model refuses an episode on "
                                            "content-policy grounds (e.g. zai-org/GLM-4.7-Flash)")
    r.add_argument("--rules-pool", type=int, default=1,
                   help="extra keyword-rule picks per episode sent to the judge alongside the AI picks (default 1, 0 = off)")
    r.add_argument("--thinking", action="store_true", help="turn on the model's reasoning mode (much slower on Qubrid)")
    r.add_argument("--max-cost", type=float, help="hard budget in $ for this run; live AI calls stop once it is reached")
    r.add_argument("--price-in", type=float, help="$ per 1M input tokens (auto for Qubrid)")
    r.add_argument("--price-out", type=float, help="$ per 1M output tokens (auto for Qubrid)")

    m = sub.add_parser("models", help="list models from the provider with prices, cheapest first")
    m.add_argument("--base-url")

    p = sub.add_parser("parse", help="parse a show and print a summary (no AI)")
    p.add_argument("input")

    w = sub.add_parser("serve", help="start the web UI (http://127.0.0.1:8765)")
    w.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 only when hosting (then set PROMO_UI_REQUIRE_KEY=1)")
    w.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    w.add_argument("--open", action="store_true", help="open the browser")

    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve
        serve(args.host, args.port, args.open)
        return 0

    if args.cmd == "parse":
        from .screenplay import load_show, render_blocks
        eps = load_show(args.input)
        for e in eps:
            print(f"Ep {e.number:3d}: {len(e.scenes)} scenes, {len(e.blocks):3d} blocks, {e.duration:6.1f}s"
                  + (f"  WARN {e.warnings[:2]}" if e.warnings else ""))
        print(f"{len(eps)} episodes, {sum(len(e.blocks) for e in eps)} blocks, {sum(e.duration for e in eps) / 60:.1f} min")
        if eps:
            print("\nFirst lines of episode", eps[0].number, "as the model sees them:\n" + render_blocks(eps[0].blocks[:8]))
        return 0

    if args.cmd == "models":
        cfg = LLMConfig.from_env(base_url=args.base_url.rstrip("/") if args.base_url else None)
        models = list_models(cfg.base_url, cfg.api_key)
        rows = []
        for md in models:
            pr = md.get("pricing") or {}
            try:
                pin, pout = float(pr.get("input")), float(pr.get("output"))
            except (TypeError, ValueError):
                pin = pout = float("nan")
            rows.append((pin + pout, pin, pout, md.get("id"), md.get("context_length")))
        rows.sort(key=lambda t: (t[0] != t[0], t[0]))  # NaN last
        print(f"{'model':55s} {'$/1M in':>9s} {'$/1M out':>9s} {'context':>9s}")
        for _, pin, pout, mid, ctx in rows:
            print(f"{str(mid):55s} {pin:9.3f} {pout:9.3f} {str(ctx):>9s}")
        return 0

    # ---- run
    from .pipeline import run
    title = os.path.basename(os.path.normpath(args.input))
    out_dir = args.out or os.path.join("outputs", _slug(title))
    llm = None
    judge_llm = None
    if not args.no_llm:
        from .runner import SetupError, build_llms, fresh_log_path
        try:
            llm, judge_llm = build_llms(
                model=args.model, base_url=args.base_url, judge_model=args.judge_model,
                fallback_model=args.fallback_model, workers=args.workers, max_cost=args.max_cost,
                thinking=args.thinking, price_in=args.price_in, price_out=args.price_out,
                log_path=fresh_log_path(out_dir))
        except SetupError as e:
            print(str(e), file=sys.stderr)
            return 2
    summary = run(args.input, out_dir, llm, per_episode=args.per_episode, trailers=args.trailers,
                  episodes_filter=_parse_eps(args.episodes), judge=not args.no_judge,
                  workers=(llm.cfg.workers if llm else 1), rules_pool=args.rules_pool, judge_llm=judge_llm,
                  recut_hooks=args.recut_hooks)
    print(json.dumps({k: summary[k] for k in ("promos_ranked", "wall_clock_seconds")}, indent=None))
    print(f"Open {os.path.join(out_dir, 'promos.md')} (ranked list) or report.html (with script excerpts).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
