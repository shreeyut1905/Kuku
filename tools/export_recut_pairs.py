"""Blind A/B for the hook re-cut: export the ORIGINAL and the RE-CUT version of every clip
whose start was moved, shuffled together and anonymised.

Usage: python3 tools/export_recut_pairs.py outputs/<run_without_recut>/promos.json outputs/<run_with_recut>/promos.json "input_pack/<show>" --out eval/clips_recut_pairs
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
ap.add_argument("base")
ap.add_argument("recut")
ap.add_argument("show")
ap.add_argument("--out", default="eval/clips_recut_pairs")
a = ap.parse_args()
eps = {e.number: e for e in load_show(a.show)}


def load(p):
    d = json.load(open(p))
    return {x["id"]: x for x in d["promos"] + d.get("near_duplicates", [])}


base, rec = load(a.base), load(a.recut)
pairs = [(pid, base[pid], rec[pid]) for pid in rec
         if pid in base and any("hook re-cut" in n for n in rec[pid].get("code_notes", []))]
items = [(pid, "original", b) for pid, b, _ in pairs] + [(pid, "recut", r) for pid, _, r in pairs]
random.Random(11).shuffle(items)
os.makedirs(a.out, exist_ok=True)
key = {}
for i, (pid, version, p) in enumerate(items, start=1):
    cid = f"C{i:02d}"
    segs = [Segment(s["episode"], s["start_seconds"], s["end_seconds"], s["start_block"], s["end_block"]) for s in p["segments"]]
    promo = Promo(pid=pid, kind=p["kind"], title="", segments=segs)
    s0 = segs[0]
    first3 = "\n".join(f"[{max(0.0, b.start - s0.start):4.1f}s] {b.label()}: {b.text}"
                       for b in blocks_between(eps[s0.episode], s0.start, s0.start + 3.0))
    text = (f"CLIP {cid}: a {promo.duration:.1f}-second promo cut from a vertical microdrama (one continuous stretch).\n"
            f"Times are seconds from the start of the clip. CAMERA = what is on screen, ACTION = what people do, "
            f"SFX = sound, NAME (emotion) = dialogue.\n\nFIRST 3 SECONDS:\n{first3}\n\nFULL CLIP:\n{render_clip(promo, eps)}\n"
            f"=== CUT: the promo ends here ===\n\nRIGHT AFTER THE CUT (NOT shown to viewers):\n{render_after(promo, eps)}\n")
    open(os.path.join(a.out, f"{cid}.txt"), "w", encoding="utf-8").write(text)
    key[cid] = {"promo_id": pid, "version": version, "start": p["segments"][0]["start"], "duration": p["duration_seconds"]}
json.dump(key, open(os.path.join(a.out, "_key.json"), "w"), indent=2)
print(f"{len(pairs)} re-cut clips -> {len(items)} files (original + re-cut, shuffled) in {a.out}")
