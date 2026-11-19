"""Collect every number quoted in the solution doc from the run + eval files, in one place,
so the doc never drifts from the data. Usage: python3 tools/doc_numbers.py outputs/full_run"""
import json
import os
import sys
from collections import Counter

run = sys.argv[1]
d = json.load(open(os.path.join(run, "promos.json")))
s = d["summary"]
ranked = d["promos"]
llm = s.get("llm") or {}
calls = [json.loads(line) for line in open(os.path.join(run, "raw", "llm_calls.jsonl"))]
by_step = Counter(c["tag"].split(":")[0] for c in calls)
live_secs = [c["response"].get("seconds") or 0 for c in calls if not c["cached"] and "error" not in c["response"]]
# tokens per step from the cache records (live + cached replies both carry usage)
tok = Counter()
for c in calls:
    u = c["response"].get("usage") or {}
    step = c["tag"].split(":")[0]
    tok[step + "_in"] += int(u.get("prompt_tokens") or 0)
    tok[step + "_out"] += int(u.get("completion_tokens") or 0)
total_in = sum(v for k, v in tok.items() if k.endswith("_in"))
total_out = sum(v for k, v in tok.items() if k.endswith("_out"))
judged = [p for p in ranked if p.get("judge") and not p["judge"].get("error")]
verdicts = Counter(p["judge"]["verdict"] for p in judged)
out = {
    "episodes": s["episodes"], "runtime_min": s["runtime_minutes"],
    "promos_ranked": s["promos_ranked"], "near_duplicates": s["near_duplicates_removed"],
    "ranked_by_source": s.get("ranked_by_source"), "top10_by_source": s.get("top10_by_source"),
    "mining": s["mining"], "trailers": {k: (len(v) if isinstance(v, list) else v) for k, v in (s.get("trailers") or {}).items()},
    "calls_by_step": dict(by_step), "total_calls_logged": len(calls),
    "tokens_in_total_all_steps": total_in, "tokens_out_total_all_steps": total_out,
    "tokens_by_step": dict(tok),
    "llm_stats_this_run": llm,
    "avg_live_call_seconds": round(sum(live_secs) / max(1, len(live_secs)), 1),
    "judge_verdicts": dict(verdicts),
    "judged": len(judged), "judge_errors": sum(1 for p in ranked if (p.get("judge") or {}).get("error")),
    "flags_top10": [p["flags"] for p in ranked[:10]],
    "cost_estimates_usd_per_show": {
        name: round(total_in / 1e6 * pin + total_out / 1e6 * pout, 3)
        for name, (pin, pout) in {
            "GLM-4.7-Flash (free)": (0.0, 0.0),
            "Nemotron-3.5-Lightning ($0.06/$0.25)": (0.06, 0.25),
            "Qwen3.5-Flash ($0.10/$0.40)": (0.10, 0.40),
            "gpt-oss-120b ($0.12/$0.48)": (0.12, 0.48),
            "Claude Haiku 5.5 ($0.10/$0.50)": (0.10, 0.50),
            "DeepSeek-V4-Pro ($0.76/$2.28)": (0.759, 2.277),
        }.items()
    },
}
for k in ("agreement_tool.json", "agreement_rules_baseline.json", "blind_review_all_summary.json", "judge_stability.json"):
    p = os.path.join("eval", k)
    if os.path.exists(p):
        out["eval_" + k.replace(".json", "")] = json.load(open(p)).get("summary")
print(json.dumps(out, indent=2))
