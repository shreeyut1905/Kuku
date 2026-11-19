"""Audit every clip boundary against the script: hard rules the report claims.

  * 20-60 s total length
  * no cut in the middle of a spoken line (start or end)
  * every segment lies inside its episode
  * segments of a trailer are in story order

Usage: python3 tools/audit_cuts.py outputs/<run>/promos.json "input_pack/<show>"
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promo_finder.screenplay import load_show  # noqa: E402

d = json.load(open(sys.argv[1]))
eps = {e.number: e for e in load_show(sys.argv[2])}
promos = d["promos"] + d.get("near_duplicates", [])
problems = []
for p in promos:
    total = sum(s["end_seconds"] - s["start_seconds"] for s in p["segments"])
    if not (20 - 0.01 <= total <= 60 + 0.01):
        problems.append((p["id"], f"length {total:.2f}s"))
    order = [(s["episode"], s["start_seconds"]) for s in p["segments"]]
    if order != sorted(order):
        problems.append((p["id"], "segments out of story order"))
    for s in p["segments"]:
        ep = eps[s["episode"]]
        a, b = s["start_seconds"], s["end_seconds"]
        if a < -0.01 or b > ep.duration + 0.01 or b <= a:
            problems.append((p["id"], f"segment outside episode {a}-{b} (ep len {ep.duration})"))
        for bl in ep.blocks:
            if bl.kind != "DIALOGUE":
                continue
            if bl.start < a - 0.1 and bl.end > a + 0.1:
                problems.append((p["id"], f"starts mid-line at {a:.2f}: {bl.speaker} {bl.text[:50]!r} ({bl.start}-{bl.end})"))
            if bl.start < b - 0.1 and bl.end > b + 0.1:
                problems.append((p["id"], f"ends mid-line at {b:.2f}: {bl.speaker} {bl.text[:50]!r} ({bl.start}-{bl.end})"))
n = len(promos)
bad = len({pid for pid, _ in problems})
print(f"{n} clips audited ({len(d['promos'])} ranked + {len(d.get('near_duplicates', []))} near-duplicates): "
      f"{bad} with a problem, {len(problems)} problems")
for pid, msg in problems:
    print(f"  {pid}: {msg}")
