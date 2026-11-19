"""Validate independent reviewer picks against the script: do the timestamps exist, is the
clip 20-60 s? Usage: python3 tools/check_reviewer_picks.py <show folder> eval/independent_picks_haiku.json"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promo_finder.screenplay import load_show, parse_ts  # noqa: E402

eps = {e.number: e for e in load_show(sys.argv[1])}
picks = json.load(open(sys.argv[2]))["picks"]
bad_ts = bad_len = 0
for p in sorted(picks, key=lambda x: x["episode"]):
    ep = eps[p["episode"]]
    try:
        s, e = parse_ts(p["start"]), parse_ts(p["end"])
    except Exception:
        print("unparseable", p)
        continue
    s_ok = any(abs(b.start - s) < 0.06 for b in ep.blocks)
    e_ok = any(abs(b.end - e) < 0.06 for b in ep.blocks)
    d = e - s
    if not (s_ok and e_ok):
        bad_ts += 1
    if not (20 <= d <= 60):
        bad_len += 1
    p["_valid_timestamps"], p["_duration"] = s_ok and e_ok, round(d, 2)
    print(f"Ep {p['episode']:2d} {p['start']}-{p['end']} {d:5.1f}s ts_in_script={'Y' if s_ok and e_ok else 'N'} worth={p['promo_worthiness']}")
print(f"\n{len(picks)} picks: {bad_ts} with a timestamp not found in the script, {bad_len} outside 20-60 s")
json.dump({"picks": picks}, open(sys.argv[2], "w"), indent=2, ensure_ascii=False)
