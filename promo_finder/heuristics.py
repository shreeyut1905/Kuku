"""Cheap rule-based signals (no AI).

Used three ways:
  1. sanity flags on every promo (e.g. "first 3 seconds are only a camera shot"),
  2. a small tie-breaker in the ranking,
  3. a rules-only baseline (`--no-llm`), so we can measure what the model adds and
     still get *something* if no API is available.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .clips import MAX_PROMO, MIN_PROMO, Promo, Segment, snap
from .screenplay import Block, Episode, blocks_between

HOOK_WORDS = re.compile(
    r"\b(slap\w*|strike\w*|struck|punch\w*|hit[s]?|shove\w*|grab\w*|seiz\w*|drag\w*|scream\w*|shout\w*|gasp\w*|"
    r"blood\w*|knife|dagger|sword|blade|gun|kill\w*|dead|death|die[sd]?|hang\w*|gallows|execut\w*|"
    r"poison\w*|arsenic|witch\w*|fire|flames?|burn\w*|blaze|explo\w*|chain\w*|shackl\w*|kneel\w*|"
    r"kiss\w*|betray\w*|liar|thief|traitor|murder\w*|whore|disown\w*|humiliat\w*|marry|marrying|wedding|duke|duchess|"
    r"crown|guards?|arrest\w*|throw\w*|thrown|tear\w*|rip\w*|smash\w*|shatter\w*|crash\w*)\b",
    re.I,
)
INTENSE_TAGS = re.compile(
    r"(furious|outraged|enraged|rage|scream|shout|yell|gloat|smug|cruel|sneer|menac|threat|venom|cold|icy|"
    r"shock|horrif|devastat|sob|cry|crying|desperate|plead|terrif|panic|defiant|accus|mock|scorn|contempt|vicious)",
    re.I,
)
CLIFF_TEXT = re.compile(r"(cut to black|smash cut|freeze|cliffhanger|ominous|swells|sting|silence falls|to be continued)", re.I)


def block_heat(b: Block) -> float:
    """How 'loud' a single block is: violence/stakes words, angry tags, exclamations."""
    s = 0.0
    words = len(HOOK_WORDS.findall(b.text))
    s += min(words, 3) * 1.0
    if b.kind == "DIALOGUE":
        s += 0.5
        if INTENSE_TAGS.search(b.tags or ""):
            s += 1.0
        s += min(b.text.count("!"), 2) * 0.5
    if b.kind == "SFX":
        s += 0.5
    if b.kind == "ACTION":
        s += 0.3
    return s


def hook_signal(ep: Episode, start: float, window: float = 3.0) -> float:
    bl = blocks_between(ep, start, start + window)
    if not bl:
        return 0.0
    has_voice_or_action = any(b.kind in ("DIALOGUE", "ACTION", "SFX") for b in bl)
    return sum(block_heat(b) for b in bl) + (1.0 if has_voice_or_action else -1.0)


def cliff_signal(ep: Episode, end: float, window: float = 4.0) -> float:
    bl = blocks_between(ep, max(0.0, end - window), end)
    s = 0.0
    for b in bl:
        if b.kind == "DIALOGUE":
            if b.text.rstrip('"”\' ').endswith("?"):
                s += 1.5
            if b.text.rstrip('"”\' ').endswith(("...", "…", "—", "-")):
                s += 1.0
        if CLIFF_TEXT.search(b.text):
            s += 2.0
        s += 0.5 * block_heat(b)
    if ep.duration - end <= 3.0:     # episodes are written to end on a cliffhanger
        s += 2.0
    return s


def signals_for(promo: Promo, episodes: Dict[int, Episode]) -> Dict:
    first, last = promo.segments[0], promo.segments[-1]
    ep0, ep1 = episodes[first.episode], episodes[last.episode]
    opening = blocks_between(ep0, first.start, first.start + 3.0)
    only_camera = bool(opening) and all(b.kind == "CAMERA" for b in opening)
    spoken = [b for s in promo.segments for b in blocks_between(episodes[s.episode], s.start, s.end) if b.kind == "DIALOGUE"]
    speakers = sorted({b.speaker for b in spoken})
    return {
        "hook_signal": round(hook_signal(ep0, first.start), 2),
        "cliff_signal": round(cliff_signal(ep1, last.end), 2),
        "opens_on_camera_only": only_camera,
        "ends_at_episode_end": ep1.duration - last.end <= 3.0,
        "lines_of_dialogue": len(spoken),
        "speakers": speakers,
    }


_QUOTE = re.compile(r"""(?:^|[\s(:,—-])["“'‘]([^"”'’]{6,}?)["”'’](?=[\s.,!?;:)—-]|$)""")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t.lower())).strip()


def _quote_found(q: str, window_text: str) -> bool:
    qn, wn = _norm(q), _norm(window_text)
    if not qn:
        return True
    if qn in wn:
        return True
    qw = [w for w in qn.split() if len(w) > 2]
    return bool(qw) and sum(w in wn.split() for w in qw) / len(qw) >= 0.7


def description_mismatch(promo: Promo, episodes: Dict[int, Episode]) -> List[str]:
    """The model's hook/cliffhanger text sometimes quotes a line that is NOT where it says
    (e.g. after code trimmed the clip, or the model described the wrong moment). Flag it."""
    out = []
    first, last = promo.segments[0], promo.segments[-1]
    head = " ".join(b.text for b in blocks_between(episodes[first.episode], first.start, first.start + 5.0))
    tail = " ".join(b.text for b in blocks_between(episodes[last.episode], max(last.start, last.end - 8.0), last.end))
    hq = _QUOTE.findall(promo.hook or "")
    cq = _QUOTE.findall(promo.cliffhanger or "")
    if hq and not any(_quote_found(q, head) for q in hq):
        out.append("hook text quotes a line that is not in the first seconds")
    if cq and not any(_quote_found(q, tail) for q in cq):
        out.append("cliffhanger text quotes a line that is not at the cut")
    return out


def flags_for(promo: Promo, sig: Dict) -> List[str]:
    f = []
    if sig.get("opens_on_camera_only"):
        f.append("first 3s: picture only (no line, action or sound)")
    if sig.get("lines_of_dialogue", 0) == 0:
        f.append("no dialogue at all")
    if len(sig.get("speakers", [])) >= 6:
        f.append(f"{len(sig['speakers'])} speaking characters (may confuse a new viewer)")
    d = promo.duration
    if d < 25 or d > 50:
        f.append(f"length {d:.0f}s is outside the 25-50s sweet spot")
    return f


# ---------------------------------------------------------------- deterministic fixes


def _valid_start_blocks(ep: Episode, lo: float, hi: float) -> List[Block]:
    return [b for b in ep.blocks if lo - 0.01 <= b.start <= hi + 0.01 and b.kind in ("DIALOGUE", "ACTION", "SFX", "CAMERA")]


def autofix_length(ep: Episode, seg: Segment) -> Tuple[Optional[Segment], str]:
    """Small models are bad at timestamp arithmetic: a great pick that runs 62 s is
    rescued here instead of being thrown away. We keep the model's cliffhanger (end)
    whenever possible and move the start to the strongest hook that fits; if that
    fails we keep the hook and move the end to the strongest cliffhanger that fits."""
    d = seg.duration
    if MIN_PROMO <= d <= MAX_PROMO:
        return seg, ""
    tries = []
    if d > MAX_PROMO:
        # A) later start, same end: look in the first 15 s of the allowed window
        lo = seg.end - MAX_PROMO + 0.5
        for b in _valid_start_blocks(ep, lo, lo + 15):
            tries.append(("start", b.start, seg.end, hook_signal(ep, b.start) - 0.03 * (b.start - lo)))
        # B) same start, earlier end: look in the last 15 s of the allowed window
        hi = seg.start + MAX_PROMO - 0.5
        for b in ep.blocks:
            if hi - 15 <= b.end <= hi and b.end > seg.start + MIN_PROMO:
                tries.append(("end", seg.start, b.end, cliff_signal(ep, b.end) - 0.03 * (hi - b.end) - 0.5))
    else:
        # C) earlier start (more set-up), same end
        need_lo, need_hi = seg.end - 45.0, seg.end - MIN_PROMO - 0.5
        for b in _valid_start_blocks(ep, max(0.0, need_lo), need_hi):
            tries.append(("start", b.start, seg.end, hook_signal(ep, b.start) - 0.02 * abs(need_hi - b.start)))
        # D) same start, later end
        for b in ep.blocks:
            if seg.start + MIN_PROMO + 0.5 <= b.end <= seg.start + 45.0:
                tries.append(("end", seg.start, b.end, cliff_signal(ep, b.end) - 0.02 * (b.end - seg.start - MIN_PROMO) - 0.5))
    tries.sort(key=lambda t: -t[3])
    for which, s, e, _ in tries:
        s2, e2 = snap(ep, s, e)
        if MIN_PROMO <= e2 - s2 <= MAX_PROMO:
            sb = next((b for b in ep.blocks if b.start >= s2 - 0.01), ep.blocks[0])
            eb = max((b for b in ep.blocks if b.end <= e2 + 0.01), key=lambda b: (b.end, b.start), default=ep.blocks[-1])
            note = (f"auto-{'trimmed' if d > MAX_PROMO else 'extended'} by code: model picked {d:.1f}s, "
                    f"moved the {'start' if which == 'start' else 'end'}")
            return Segment(ep.number, s2, e2, sb.bid, eb.bid), note
    return None, f"could not fit {d:.1f}s into 20-60s"


def tighten_hook(ep: Episode, seg: Segment) -> Tuple[Segment, str]:
    """If the first 3 s are picture-only, start on the shot where the first line/action
    lands (when that is within 6 s and the clip stays >= 20 s)."""
    opening = blocks_between(ep, seg.start, seg.start + 3.0)
    if not opening or any(b.kind != "CAMERA" for b in opening):
        return seg, ""
    nxt = next((b for b in ep.blocks if b.kind in ("DIALOGUE", "ACTION", "SFX")
                and seg.start + 3.0 <= b.start <= seg.start + 6.0), None)
    if not nxt:
        return seg, ""
    shot = max((b for b in ep.blocks if b.is_shot and b.start <= nxt.start + 0.01), key=lambda b: b.start, default=None)
    new_start = shot.start if shot and nxt.start - shot.start <= 1.6 else nxt.start
    if seg.end - new_start < MIN_PROMO or new_start <= seg.start:
        return seg, ""
    sb = next((b for b in ep.blocks if b.start >= new_start - 0.01), None)
    return (Segment(ep.number, round(new_start, 2), seg.end, sb.bid if sb else seg.start_block, seg.end_block),
            f"start moved {new_start - seg.start:.1f}s later by code so the first line lands in the first 3s")


def fix_moment(ep: Episode, seg: Segment, lo: float = 3.0, hi: float = 15.0) -> Optional[Segment]:
    """Bring a trailer 'moment' into 3-20 s (prompt asks 3-15 s), with block IDs that match the new times."""
    if lo <= seg.duration <= 20.0:
        return seg
    if seg.duration < lo:
        cands = [(seg.start, b.end) for b in ep.blocks if b.end >= seg.start + lo]     # later ends
        cands += [(b.start, seg.end) for b in reversed(ep.blocks) if b.start <= seg.end - lo]  # earlier starts
    else:
        cands = [(max(seg.start, seg.end - hi), seg.end), (seg.start, min(seg.end, seg.start + hi))]
    for s0, e0 in cands[:40]:
        s2, e2 = snap(ep, s0, e0)
        if lo <= e2 - s2 <= 20.0:
            sb = next((b for b in ep.blocks if b.start >= s2 - 0.01), ep.blocks[0])
            eb = max((b for b in ep.blocks if b.end <= e2 + 0.01), key=lambda b: (b.end, b.start), default=ep.blocks[-1])
            return Segment(ep.number, s2, e2, sb.bid, eb.bid)
    return None


def script_hook_line(promo: Promo, episodes: Dict[int, Episode]) -> str:
    seg = promo.segments[0]
    bl = blocks_between(episodes[seg.episode], seg.start, min(seg.end, seg.start + 3.0))
    for kind in ("DIALOGUE", "ACTION", "SFX", "CAMERA"):
        for b in bl:
            if b.kind == kind:
                who = f"{b.speaker}: " if kind == "DIALOGUE" else f"[{kind.lower()}] "
                return f"{who}{b.text}"
    return ""


def script_cliff_line(promo: Promo, episodes: Dict[int, Episode]) -> str:
    seg = promo.segments[-1]
    bl = [b for b in blocks_between(episodes[seg.episode], max(seg.start, seg.end - 8.0), seg.end)]
    for kind in ("DIALOGUE", "ACTION", "SFX", "CAMERA"):
        cands = [b for b in bl if b.kind == kind]
        if cands:
            b = max(cands, key=lambda x: (x.end, x.start))
            who = f"{b.speaker}: " if kind == "DIALOGUE" else f"[{kind.lower()}] "
            return f"{who}{b.text}"
    return ""


# ---------------------------------------------------------------- rules-only baseline


def rules_candidates(ep: Episode, per_episode: int = 2) -> List[Segment]:
    """Pick end points with strong cliffhanger signals, then the best hook 20-60 s earlier."""
    ends = []
    for b in ep.blocks:
        if b.kind in ("DIALOGUE", "SFX", "CAMERA", "ACTION"):
            ends.append((cliff_signal(ep, b.end), b))
    ends.sort(key=lambda t: -t[0])
    picked: List[Segment] = []
    for cs, eb in ends[:25]:
        best = None
        for sb in ep.blocks:
            d = eb.end - sb.start
            if MIN_PROMO + 2 <= d <= MAX_PROMO - 8:
                hs = hook_signal(ep, sb.start) + (0.5 if 25 <= d <= 45 else 0.0)
                if best is None or hs > best[0]:
                    best = (hs, sb)
        if not best:
            continue
        s, e = snap(ep, best[1].start, eb.end)
        if not (MIN_PROMO <= e - s <= MAX_PROMO):
            continue
        seg = Segment(ep.number, s, e, best[1].bid, eb.bid)
        if all(min(seg.end, p.end) - max(seg.start, p.start) < 0.3 * min(seg.duration, p.duration) for p in picked):
            picked.append(seg)
        if len(picked) >= per_episode:
            break
    return picked
