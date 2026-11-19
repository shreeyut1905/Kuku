"""Compare the tool against a blind second-opinion review of EVERY candidate.

Usage: python3 tools/analyze_review_all.py eval/blind_review_all_raw.json eval/clips_all/_key.json outputs/full_run/promos.json [out.json]

Answers:
  * Does the tool's ranking agree with an independent reviewer? (would-post rate by rank bucket, Spearman)
  * Does the tool's own judge agree with the independent reviewer? (post vs not-post agreement, Cohen's kappa)
  * Do AI picks beat the keyword-rules baseline when judged blind? (by source)
"""
import json
import sys

reviews = {r["clip_id"]: r for r in json.load(open(sys.argv[1]))["reviews"]}
key = json.load(open(sys.argv[2]))
run = json.load(open(sys.argv[3]))
final = {p["id"]: p for p in run["promos"] + run.get("near_duplicates", [])}


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
    return round(cov / (va * vb), 3) if va and vb else float("nan")


rows = []
for cid, k in key.items():
    r = reviews.get(cid)
    p = final.get(k["promo_id"])
    if not r or not p:
        continue
    j = p.get("judge") or {}
    rows.append({
        "clip": cid, "id": p["id"], "kind": p["kind"], "rank": p.get("rank") or 999, "tool_score": p["score"],
        "near_duplicate": p["id"] not in {q["id"] for q in run["promos"]},
        "judge_verdict": j.get("verdict"), "judge_mean": (sum(j[x] for x in ("hook_score", "cliffhanger_score", "standalone_score")) / 3
                                                          if j.get("hook_score") is not None else None),
        "rev_score": r["score"], "rev_post": bool(r["would_post"]), "rev_hook": bool(r["hook_pass"]),
        "rev_cliff": bool(r["cliffhanger_pass"]), "rev_alone": bool(r["standalone_pass"]), "rev_problem": r["biggest_problem"],
    })

ranked_rows = [r for r in rows if not r["near_duplicate"]]


def bucket_stats(rs):
    n = len(rs)
    return {"n": n, "would_post": f"{sum(r['rev_post'] for r in rs)}/{n}",
            "mean_reviewer_score": round(sum(r["rev_score"] for r in rs) / max(1, n), 2),
            "hook_pass": f"{sum(r['rev_hook'] for r in rs)}/{n}", "cliff_pass": f"{sum(r['rev_cliff'] for r in rs)}/{n}",
            "stands_alone": f"{sum(r['rev_alone'] for r in rs)}/{n}"}


buckets = {"top 10": (1, 10), "11-30": (11, 30), "31-75": (31, 75), "76+": (76, 10 ** 6)}
summary = {"clips_compared": len(rows),
           "by_tool_rank": {name: bucket_stats([r for r in ranked_rows if lo <= r["rank"] <= hi]) for name, (lo, hi) in buckets.items()},
           "spearman_tool_score_vs_reviewer_score": spearman([r["tool_score"] for r in ranked_rows], [r["rev_score"] for r in ranked_rows])}
jr = [r for r in rows if r["judge_mean"] is not None]
summary["spearman_judge_mean_vs_reviewer_score"] = spearman([r["judge_mean"] for r in jr], [r["rev_score"] for r in jr])
both = [r for r in rows if r["judge_verdict"]]
a = sum(1 for r in both if (r["judge_verdict"] == "post") == r["rev_post"])
p_j = sum(1 for r in both if r["judge_verdict"] == "post") / max(1, len(both))
p_r = sum(1 for r in both if r["rev_post"]) / max(1, len(both))
pe = p_j * p_r + (1 - p_j) * (1 - p_r)
po = a / max(1, len(both))
summary["judge_vs_reviewer_post_agreement"] = f"{a}/{len(both)}"
summary["judge_post_rate"] = round(p_j, 2)
summary["reviewer_post_rate"] = round(p_r, 2)
summary["cohens_kappa"] = round((po - pe) / (1 - pe), 3) if pe < 1 else None
summary["by_source"] = {k: bucket_stats([r for r in ranked_rows if r["kind"] == k]) for k in ("single", "montage", "rules")}
print(json.dumps(summary, indent=2))

print("\nTool top 10 the blind reviewer would NOT post:")
for r in sorted(ranked_rows, key=lambda x: x["rank"])[:10]:
    if not r["rev_post"]:
        print(f"  #{r['rank']:<3d} {r['id']:7s} reviewer {r['rev_score']}/10 | {r['rev_problem'][:140]}")
print("\nLow-ranked clips (rank > 30) the blind reviewer scored >= 8 (possible misses in the ranking):")
for r in sorted(ranked_rows, key=lambda x: -x["rev_score"]):
    if r["rank"] > 30 and r["rev_score"] >= 8:
        print(f"  #{r['rank']:<3d} {r['id']:7s} reviewer {r['rev_score']}/10 tool score {r['tool_score']} judge {r['judge_verdict']}")
out_path = sys.argv[4] if len(sys.argv) > 4 else "eval/blind_review_all_summary.json"
json.dump({"summary": summary, "rows": rows}, open(out_path, "w"), indent=2, ensure_ascii=False)
