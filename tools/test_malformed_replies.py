"""Offline robustness test: run the whole pipeline with a FAKE model that returns the kinds
of broken replies real models produce (bare lists, wrong types, made-up IDs, Infinity,
prose around JSON, empty fields). The run must finish and every output must obey the rules.

Usage: python3 tools/test_malformed_replies.py "input_pack/<show>"      (no API calls)
"""
import itertools
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from promo_finder import pipeline  # noqa: E402
from promo_finder.llm import LLMError, extract_json  # noqa: E402

MINE_REPLIES = itertools.cycle([
    'Sure! Output format {id}: {"promos": [{"title": "A", "start_block": "B001", "end_block": "B030", "hook": "h", '
    '"cliffhanger": "", "why": "w"}], "moments": [{"start_block": "B010", "end_block": "B012", "kind": "hook", "description": "d"}]}',
    '[{"title": "bare list", "start_block": "B005", "end_block": "B040"}]',          # bare list of promos
    '{"promos": 5, "moments": "none"}',                                               # wrong types
    '{"promos": [{"start_block": "B999", "end_block": "B1000"}, "junk", {"start_block": "B-3"}]}',  # made-up IDs
    '{"promos": [{"start_block": "B001", "end_block": "B200"}]}',                    # far too long
    'no json at all',
])
JUDGE_REPLIES = itertools.cycle([
    '{"hook_score": Infinity, "cliffhanger_score": 5, "standalone_score": 5, "verdict": "post"}',
    '[{"hook_score": 8, "cliffhanger_score": "7/10", "standalone_score": 6, "verdict": "maybe", "problems": "one string"}]',
    '"just a string"',
    '{"hook_score": 9, "cliffhanger_score": 9, "standalone_score": 9, "verdict": "post|maybe", "problems": [], "pitch": "p"}',
])
TRAILER_REPLY = ('{"trailers": [{"title": "dup", "moment_ids": ["M001", "M001"]}, {"title": "int", "moment_ids": 5}, '
                 '{"title": "ok", "moment_ids": ["M001", "M002", "M003"]}]}')


class FakeLLM:
    """Same interface as promo_finder.llm.LLM, no network."""
    class cfg:
        workers = 1
        model = "fake"

    def chat_json(self, messages, tag="", **kw):
        if tag.startswith("bible"):
            text = '{"premise": "p", "genre": "drama", "characters": "not a list"}'
        elif tag.startswith("mine"):
            text = next(MINE_REPLIES)
        elif tag.startswith("trailers"):
            text = TRAILER_REPLY
        elif tag.startswith("judge"):
            text = next(JUDGE_REPLIES)
        else:
            raise LLMError("unexpected tag " + tag)
        return extract_json(text)

    def stats(self):
        return {"model": "fake", "live_calls": 0, "cache_hits": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "cost_usd": 0.0, "base_url": "-"}


out = tempfile.mkdtemp(prefix="malformed_")
try:
    summary = pipeline.run(sys.argv[1], out, FakeLLM(), per_episode=3, trailers=2, episodes_filter=list(range(1, 13)))
    data = json.load(open(os.path.join(out, "promos.json")))
    bad = [p["id"] for p in data["promos"] if not (20 <= p["duration_seconds"] <= 60)]
    print("run finished:", summary["promos_ranked"], "promos ranked;", "length violations:", bad or "none")
    print("mining record:", json.dumps(summary["mining"]))
    print("trailers record:", json.dumps(summary["trailers"])[:400])
    os.system(f'python3 "{os.path.dirname(os.path.abspath(__file__))}/audit_cuts.py" "{out}/promos.json" "{sys.argv[1]}"')
finally:
    shutil.rmtree(out, ignore_errors=True)
