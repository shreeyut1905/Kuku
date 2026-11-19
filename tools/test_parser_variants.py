"""Robustness check: the interview will use a NEW show, possibly formatted slightly differently.
Re-format one real episode several ways and check the parser still recovers the same timeline.
Usage: python3 tools/test_parser_variants.py <episode.txt>"""
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promo_finder.screenplay import parse_episode  # noqa: E402

src = open(sys.argv[1], encoding="utf-8").read()
variants = {
    "original": src,
    "windows_line_endings": src.replace("\n", "\r\n"),
    "no_parentheses": re.sub(r"Start: \(([^)]*)\) \| End: \(([^)]*)\)", r"Start: \1 | End: \2", src),
    "with_hours": re.sub(r"\((\d\d:\d\d\.\d\d)\)", r"(0:\1)", src),
    "lowercase_headers": src.replace("**CAMERA:**", "**Camera:**").replace("**ACTION:**", "**Action:**").replace("**SOUND FX:**", "**SFX:**"),
    "blank_line_after_header": re.sub(r"(\*\*[^\n]*:\*\*)\n(Start:)", r"\1\n\n\2", src),
    "comma_decimals": re.sub(r"(\d\d:\d\d)\.(\d\d)", r"\1,\2", src),
    "no_scene_blocks": re.sub(r"(?m)^(SCENE \d+|Scene (heading|start|end):.*)$", "", src),
}
results = {}
tmp = tempfile.mkdtemp()
for name, text in variants.items():
    p = os.path.join(tmp, f"episode-001-{name}.txt")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    ep = parse_episode(p)
    kinds = {}
    for b in ep.blocks:
        kinds[b.kind] = kinds.get(b.kind, 0) + 1
    results[name] = (len(ep.blocks), round(ep.duration, 2), kinds, ep.warnings[:2])
ref = results["original"]
ok = True
for name, r in results.items():
    same = r[0] == ref[0] and abs(r[1] - ref[1]) < 0.01
    ok &= same
    print(f"{'OK ' if same else 'BAD'} {name:26s} blocks={r[0]} duration={r[1]} kinds={r[2]} warnings={r[3]}")
print("ALL VARIANTS PARSE THE SAME" if ok else "SOME VARIANTS DIFFER")
