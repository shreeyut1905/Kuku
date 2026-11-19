"""Export anonymised clip scripts for a BLIND review (reviewers don't know the rank).

Takes the top-N ranked promos plus K low-ranked 'controls', shuffles them
deterministically, writes eval/clips/C01.txt ... and a private _key.json mapping
clip IDs back to promo IDs and ranks.

Usage: python3 tools/export_clips_for_review.py outputs/<run>/promos.json "input_pack/<show>" --top 15 --controls 5
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promo_finder.clips import Promo, Segment, render_after, render_clip  # noqa: E402
from promo_finder.screenplay import blocks_between, load_show  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("promos")
ap.add_argument("show")
ap.add_argument("--top", type=int, default=15)
ap.add_argument("--controls", type=int, default=5)
ap.add_argument("--out", default="eval/clips")
ap.add_argument("--seed", type=int, default=7)
ap.add_argument("--all", action="store_true", help="export every candidate (ranked + near-duplicates)")
a = ap.parse_args()

data = json.load(open(a.promos))
ranked = data["promos"]
eps = {e.number: e for e in load_show(a.show)}
if a.all:
    chosen = [("all", p) for p in ranked + data.get("near_duplicates", [])]
    controls = []
else:
    top = ranked[: a.top]
    bottom_third = ranked[len(ranked) * 2 // 3:]
    step = max(1, len(bottom_third) // max(1, a.controls))
    controls = bottom_third[::step][: a.controls]
    chosen = [("top", p) for p in top] + [("control", p) for p in controls]
rng = random.Random(a.seed)
rng.shuffle(chosen)

os.makedirs(a.out, exist_ok=True)
key = {}
for i, (group, p) in enumerate(chosen, start=1):
    cid = f"C{i:03d}" if len(chosen) >= 100 else f"C{i:02d}"
    segs = [Segment(s["episode"], s["start_seconds"], s["end_seconds"], s["start_block"], s["end_block"]) for s in p["segments"]]
    promo = Promo(pid=p["id"], kind=p["kind"], title="", segments=segs)
    s0 = segs[0]
    first3 = blocks_between(eps[s0.episode], s0.start, s0.start + 3.0)
    first3_txt = "\n".join(f"[{max(0.0, b.start - s0.start):4.1f}s] {b.label()}: {b.text}" for b in first3)
    text = (f"CLIP {cid}: a {promo.duration:.1f}-second promo cut from a vertical microdrama "
            f"({'one continuous stretch' if len(segs) == 1 else str(len(segs)) + ' segments joined by jump cuts'}).\n"
            f"Times are seconds from the start of the clip. CAMERA = what is on screen, ACTION = what people do, "
            f"SFX = sound, NAME (emotion) = dialogue.\n\n"
            f"FIRST 3 SECONDS:\n{first3_txt}\n\nFULL CLIP:\n{render_clip(promo, eps)}\n"
            f"=== CUT: the promo ends here ===\n\nRIGHT AFTER THE CUT (NOT shown to viewers):\n{render_after(promo, eps)}\n")
    with open(os.path.join(a.out, f"{cid}.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    key[cid] = {"promo_id": p["id"], "rank": p["rank"], "group": group, "kind": p["kind"],
                "judge": p.get("judge"), "score": p["score"]}
with open(os.path.join(a.out, "_key.json"), "w", encoding="utf-8") as f:
    json.dump(key, f, indent=2, ensure_ascii=False)
print(f"wrote {len(chosen)} clips to {a.out} ({'all candidates' if a.all else str(a.top) + ' top + ' + str(len(controls)) + ' controls'}, shuffled)")
