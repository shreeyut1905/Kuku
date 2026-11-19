"""Print a compact view of a run's ranked promos: python3 tools/show_results.py outputs/<run> [N]"""
import json
import sys

run = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 999
d = json.load(open(f"{run}/promos.json"))
for p in d["promos"][:n]:
    j = p.get("judge") or {}
    segs = "; ".join("Ep{} {}-{}".format(s["episode"], s["start"], s["end"]) for s in p["segments"])
    print("#{:<3d}{:8s}{:8s}{:5.2f} {:5.1f}s  {:44s} H{}/C{}/S{} {:7s}| {}".format(
        p["rank"], p["id"], p["kind"], p["score"], p["duration_seconds"], segs,
        j.get("hook_score"), j.get("cliffhanger_score"), j.get("standalone_score"), str(j.get("verdict")), p["title"][:40]))
    print("      first3:", p.get("script_first_3s", "")[:110])
    print("      last  :", p.get("script_last_line", "")[:110])
    if p.get("code_notes"):
        print("      notes :", "; ".join(p["code_notes"])[:160])
    if j.get("problems"):
        print("      judge :", " | ".join(j["problems"])[:220])
s = d["summary"]
print("\nsummary:", json.dumps({k: s[k] for k in ("promos_ranked", "ranked_by_source", "top10_by_source", "near_duplicates_removed", "mining", "wall_clock_seconds")}, indent=None)[:900])
print("llm:", s.get("llm"))
