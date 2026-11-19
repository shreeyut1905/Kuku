"""Test-retest check of the blind judge: re-judge the top-N promos at a higher temperature
and compare with the original scores. Tells you how much of the ranking is judge noise.

Usage: python3 tools/judge_stability.py outputs/<run>/promos.json "input_pack/<show>" --top 15 [--temperature 0.7]
(makes N extra AI calls; run it when no other run is using the same model)"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promo_finder.clips import Promo, Segment  # noqa: E402
from promo_finder.llm import LLM, LLMConfig, load_dotenv  # noqa: E402
from promo_finder.pipeline import judge_promo  # noqa: E402
from promo_finder.screenplay import load_show  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("promos")
ap.add_argument("show")
ap.add_argument("--top", type=int, default=15)
ap.add_argument("--temperature", type=float, default=0.7)
ap.add_argument("--out", default="eval/judge_stability.json")
a = ap.parse_args()

load_dotenv(".env")
d = json.load(open(a.promos))
genre = "romantic drama"
guide = os.path.join(os.path.dirname(a.promos), "raw", "series_guide.json")
if os.path.exists(guide):
    genre = json.load(open(guide)).get("genre", genre)
eps = {e.number: e for e in load_show(a.show)}
llm = LLM(LLMConfig.from_env(), log_path=os.path.join(os.path.dirname(a.out), "judge_stability_calls.jsonl"))
rows = []
for p in d["promos"][: a.top]:
    j0 = p.get("judge") or {}
    if j0.get("error") or j0.get("hook_score") is None:
        continue
    segs = [Segment(s["episode"], s["start_seconds"], s["end_seconds"], s["start_block"], s["end_block"]) for s in p["segments"]]
    promo = Promo(pid=p["id"], kind=p["kind"], title=p["title"], segments=segs)
    j1 = judge_promo(llm, promo, eps, genre, temperature=a.temperature, tag_suffix=":retest")
    if j1.get("error"):
        print(p["id"], "retest failed:", j1["error"])
        continue
    row = {"id": p["id"], "rank": p["rank"]}
    for k in ("hook_score", "cliffhanger_score", "standalone_score"):
        row[k] = (j0[k], j1[k])
    row["verdict"] = (j0.get("verdict"), j1.get("verdict"))
    rows.append(row)
    print(row)
n = len(rows)
summary = {"n": n, "temperature": a.temperature}
for k in ("hook_score", "cliffhanger_score", "standalone_score"):
    diffs = [abs(r[k][0] - r[k][1]) for r in rows]
    summary[f"{k}_mean_abs_diff"] = round(sum(diffs) / max(1, n), 2)
    summary[f"{k}_within_1"] = f"{sum(x <= 1 for x in diffs)}/{n}"
summary["verdict_same"] = f"{sum(r['verdict'][0] == r['verdict'][1] for r in rows)}/{n}"
print(json.dumps(summary, indent=2))
os.makedirs(os.path.dirname(a.out), exist_ok=True)
json.dump({"summary": summary, "rows": rows}, open(a.out, "w"), indent=2)
