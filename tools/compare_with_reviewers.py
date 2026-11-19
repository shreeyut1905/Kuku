"""Agreement between the tool and independent reviewers who never saw the tool's output.

Usage: python3 tools/compare_with_reviewers.py outputs/<run>/promos.json eval/independent_picks_haiku.json \
           --show "input_pack/<show>" [--out eval/agreement.json]

Measures
  * candidate recall: the reviewer's best clip for an episode overlaps (>50% of the
    shorter clip) at least one clip the tool proposed for that episode
  * top-pick agreement: ... overlaps the tool's highest-ranked clip for that episode
  * strong-episode coverage: of episodes the reviewer rated >= 7/10, how many have a
    tool promo in the tool's top 20
  * Spearman correlation: reviewer's per-episode rating vs the tool's best score per episode
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def ts(s):
    m, sec = s.split(":")
    return int(m) * 60 + float(sec)


def overlap(a, b):
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / max(0.01, min(a[1] - a[0], b[1] - b[0]))


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else float("nan")


ap = argparse.ArgumentParser()
ap.add_argument("promos")
ap.add_argument("picks")
ap.add_argument("--show", help="show folder, for exact episode lengths (chance baseline)")
ap.add_argument("--out")
args = ap.parse_args()
tool = json.load(open(args.promos))
picks = json.load(open(args.picks))["picks"]
ranked = tool["promos"]
alts = tool.get("near_duplicates", [])

single_by_ep = {}
for p in ranked + alts:
    for s in p["segments"]:
        if p["kind"] == "montage":
            continue
        single_by_ep.setdefault(s["episode"], []).append((p, (s["start_seconds"], s["end_seconds"])))

top20_eps = {s["episode"] for p in ranked[:20] for s in p["segments"]}
rows, hits, top_hits = [], 0, 0
rev_scores, tool_scores = [], []
for r in sorted(picks, key=lambda x: x["episode"]):
    ep = r["episode"]
    rv = (ts(r["start"]), ts(r["end"]))
    cands = single_by_ep.get(ep, [])
    best_overlap = max((overlap(rv, seg) for _, seg in cands), default=0.0)
    ranked_eps = [(p, seg) for p, seg in cands if p in ranked]
    top = min(ranked_eps, key=lambda t: t[0]["rank"], default=None)
    top_ov = overlap(rv, top[1]) if top else 0.0
    hit, top_hit = best_overlap > 0.5, top_ov > 0.5
    hits += hit
    top_hits += top_hit
    best_score = max((p["score"] for p, _ in ranked_eps), default=0.0)
    rev_scores.append(r["promo_worthiness"])
    tool_scores.append(best_score)
    rows.append({"episode": ep, "reviewer": f"{r['start']}-{r['end']}", "reviewer_worth": r["promo_worthiness"],
                 "found_by_tool": hit, "tool_top_pick_agrees": top_hit, "best_overlap": round(best_overlap, 2),
                 "tool_best_rank": top[0]["rank"] if top else None, "tool_best_score": best_score,
                 "reviewer_hook": r["hook_line"][:120]})

# Chance level: how often would a RANDOM clip (same length as the tool's top pick, placed
# uniformly in the episode) overlap the reviewer's clip by >50%?  Episodes are short
# (~2 min) and clips long (20-60 s), so chance agreement is far from zero.
ep_len = {}
if args.show:
    from promo_finder.screenplay import load_show
    ep_len = {e.number: e.duration for e in load_show(args.show)}
else:
    for p in ranked + alts:
        for s in p["segments"]:
            ep_len[s["episode"]] = max(ep_len.get(s["episode"], 0.0), s["end_seconds"])
chance = []
for r, row in zip(sorted(picks, key=lambda x: x["episode"]), rows):
    rv = (ts(r["start"]), ts(r["end"]))
    L = ep_len.get(r["episode"], rv[1])
    L = max(L, rv[1])
    tl = 35.0
    tops = [p for p, _ in single_by_ep.get(r["episode"], []) if p in ranked]
    if tops:
        tl = min(tops, key=lambda p: p["rank"])["duration_seconds"]
    starts = [i * 0.5 for i in range(int(max(0.0, L - tl) / 0.5) + 1)]
    chance.append(sum(overlap(rv, (s0, s0 + tl)) > 0.5 for s0 in starts) / max(1, len(starts)))
chance_top = sum(chance) / max(1, len(chance))

strong = [r for r in picks if r["promo_worthiness"] >= 7]
strong_cov = sum(1 for r in strong if r["episode"] in top20_eps)
summary = {
    "episodes_compared": len(picks),
    "candidate_recall": f"{hits}/{len(picks)}",
    "top_pick_agreement": f"{top_hits}/{len(picks)}",
    "top_pick_agreement_by_chance": f"{chance_top * len(picks):.1f}/{len(picks)}",
    "reviewer_strong_episodes_(>=7)": len(strong),
    "strong_episodes_in_tool_top20": f"{strong_cov}/{len(strong)}",
    "spearman_reviewer_vs_tool": round(spearman(rev_scores, tool_scores), 3),
}
print(json.dumps(summary, indent=2))
print("\nEpisodes where the reviewer's clip is NOT among the tool's candidates (possible misses):")
for r in rows:
    if not r["found_by_tool"]:
        print(f"  Ep {r['episode']:2d} reviewer {r['reviewer']} worth={r['reviewer_worth']} | tool best rank={r['tool_best_rank']} | {r['reviewer_hook']}")
if args.out:
    json.dump({"summary": summary, "rows": rows}, open(args.out, "w"), indent=2, ensure_ascii=False)
