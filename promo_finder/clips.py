"""Clip maths: block IDs -> exact timestamps, snapping to clean cut points, validation,
and rendering a clip back into text for the judge. Plain code, no AI."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

from .screenplay import Block, Episode, blocks_between, fmt_ts, normalize_bid

MIN_PROMO, MAX_PROMO = 20.0, 60.0
MIN_MOMENT, MAX_MOMENT = 3.0, 20.0     # library moments for montages (prompt asks 3-15 s)
SNAP_WINDOW = 1.6                       # max seconds we move a cut to land on a shot change
HOOK_WINDOW = 3.0


@dataclass
class Segment:
    episode: int
    start: float
    end: float
    start_block: str
    end_block: str

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)

    def as_dict(self) -> Dict:
        return {
            "episode": self.episode,
            "start": fmt_ts(self.start), "end": fmt_ts(self.end),
            "start_seconds": round(self.start, 2), "end_seconds": round(self.end, 2),
            "duration_seconds": self.duration,
            "start_block": self.start_block, "end_block": self.end_block,
        }


@dataclass
class Promo:
    pid: str
    kind: str                      # single | montage | rules
    title: str
    segments: List[Segment]
    hook: str = ""
    cliffhanger: str = ""
    why: str = ""
    context_gap: str = ""
    script_hook: str = ""          # verbatim first line/action in the first 3 s (from the script, by code)
    script_cliff: str = ""         # verbatim last line/action before the cut (from the script, by code)
    notes: List[str] = field(default_factory=list)   # what code changed (auto-trim etc.)
    flags: List[str] = field(default_factory=list)
    judge: Dict = field(default_factory=dict)
    signals: Dict = field(default_factory=dict)
    score: float = 0.0
    rank: int = 0
    rank_reason: str = ""

    @property
    def duration(self) -> float:
        return round(sum(s.duration for s in self.segments), 2)

    @property
    def episodes(self) -> List[int]:
        out = []
        for s in self.segments:
            if s.episode not in out:
                out.append(s.episode)
        return out

    def as_dict(self) -> Dict:
        d = {
            "id": self.pid, "rank": self.rank, "score": round(self.score, 2), "kind": self.kind,
            "title": self.title, "episodes": self.episodes, "duration_seconds": self.duration,
            "segments": [s.as_dict() for s in self.segments],
            "hook": self.hook, "cliffhanger": self.cliffhanger, "why_it_works": self.why,
            "script_first_3s": self.script_hook, "script_last_line": self.script_cliff,
            "context_gap": self.context_gap, "rank_reason": self.rank_reason,
            "code_notes": self.notes, "flags": self.flags, "judge": self.judge, "signals": self.signals,
        }
        return d


@dataclass
class Moment:
    mid: str
    segment: Segment
    kind: str
    description: str


# ---------------------------------------------------------------- snapping


def line_at(ep: Episode, t: float, tol: float = 0.1) -> Optional[Block]:
    """The spoken line in progress at time t (strictly inside it), if any."""
    for b in ep.blocks:
        if b.kind == "DIALOGUE" and b.start < t - tol and b.end > t + tol:
            return b
    return None


def speech_spans(ep: Episode, tol: float = 0.1) -> List[Tuple[float, float]]:
    """Dialogue lines merged into continuous spans (overlapping speakers = one span)."""
    spans: List[List[float]] = []
    for s, e in sorted((b.start, b.end) for b in ep.blocks if b.kind == "DIALOGUE"):
        if spans and s < spans[-1][1] - tol:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    return [(s, e) for s, e in spans]


def span_at(spans: List[Tuple[float, float]], t: float, tol: float = 0.1) -> Optional[Tuple[float, float]]:
    for s, e in spans:
        if s < t - tol and e > t + tol:
            return (s, e)
    return None


def snap(ep: Episode, start: float, end: float) -> Tuple[float, float]:
    """Move cut points so we never cut mid-line and, when close, land on a shot change.
    Shot snapping is only used when it does not land inside speech; the 'never mid-line'
    rule is applied last so it always wins."""
    spans = speech_spans(ep)
    # START: snap back to the beginning of the shot on screen, if it began just before
    shots = [b for b in ep.blocks if b.is_shot and b.start <= start + 0.01 and b.end > start]
    if shots:
        s0 = max(shots, key=lambda b: b.start).start
        if start - s0 <= SNAP_WINDOW and span_at(spans, s0) is None:
            start = s0
    sp = span_at(spans, start)
    if sp:
        start = sp[0] if start - sp[0] <= 3.0 else sp[1]      # include the whole line, or skip it
    # END: snap forward to the end of the shot on screen, if it ends just after and no new line starts first
    shots = [b for b in ep.blocks if b.is_shot and b.start < end and b.end >= end - 0.01]
    if shots:
        e0 = min(shots, key=lambda b: b.end).end
        new_line_starts = any(s0_ > end - 0.05 and s0_ < e0 - 0.1 for s0_, _ in spans)
        if e0 - end <= SNAP_WINDOW and not new_line_starts and span_at(spans, e0) is None:
            end = e0
    sp = span_at(spans, end)
    if sp:
        end = sp[1] if sp[1] - end <= 3.0 else sp[0]          # finish the line, or cut before it
    return round(start, 2), round(min(end, ep.duration), 2)


def make_segment(ep: Episode, start_bid: str, end_bid: str, do_snap: bool = True) -> Tuple[Optional[Segment], str]:
    """Resolve two block IDs into a Segment, or return (None, error)."""
    sb, eb = ep.block(start_bid), ep.block(end_bid)
    if sb is None or eb is None:
        missing = [x for x, b in ((start_bid, sb), (end_bid, eb)) if b is None]
        return None, f"unknown block id(s) {', '.join(map(str, missing))} (episode has B001-B{len(ep.blocks):03d})"
    i, j = ep.index_of(sb.bid), ep.index_of(eb.bid)
    if j < i:
        sb, eb = eb, sb
    start = sb.start
    end = max(b.end for b in ep.blocks[min(i, j):max(i, j) + 1])  # include overlapping blocks in range
    end = max(end, eb.end)
    if do_snap:
        start, end = snap(ep, start, end)
    if end - start <= 0.05:
        return None, f"empty clip ({sb.bid}->{eb.bid} covers no time)"
    return Segment(ep.number, start, end, sb.bid, eb.bid), ""


def check_promo_duration(seg_total: float) -> str:
    if seg_total < MIN_PROMO:
        return f"too short ({seg_total:.1f}s, need at least {MIN_PROMO:.0f}s)"
    if seg_total > MAX_PROMO:
        return f"too long ({seg_total:.1f}s, max {MAX_PROMO:.0f}s)"
    return ""


def explain_fix(ep: Episode, seg: Segment) -> str:
    """Concrete hint for the repair prompt: which IDs would make it fit."""
    d = seg.duration
    if d > MAX_PROMO:
        latest_end = seg.start + MAX_PROMO
        earliest_start = seg.end - MAX_PROMO
        return (f"it runs {fmt_ts(seg.start)}-{fmt_ts(seg.end)} = {d:.1f}s. Either end before {fmt_ts(latest_end)} "
                f"or start after {fmt_ts(earliest_start)}.")
    if d < MIN_PROMO:
        need = MIN_PROMO - d
        return (f"it runs {fmt_ts(seg.start)}-{fmt_ts(seg.end)} = {d:.1f}s. Add at least {need:.1f}s: start before "
                f"{fmt_ts(max(0, seg.start - need))} or end after {fmt_ts(min(ep.duration, seg.end + need))}.")
    return ""


# ---------------------------------------------------------------- rendering for the judge


def _line(b: Block, t0: float, offset: float) -> str:
    rs, re_ = b.start - t0 + offset, b.end - t0 + offset
    return f"[{max(0.0, rs):5.1f}s-{re_:5.1f}s] {b.label()}: {b.text}"


def render_clip(promo: Promo, episodes: Dict[int, Episode]) -> str:
    out = []
    offset = 0.0
    multi = len(promo.segments) > 1
    for k, seg in enumerate(promo.segments, start=1):
        ep = episodes[seg.episode]
        if multi:
            out.append(f"--- segment {k} of {len(promo.segments)} (jump cut) ---")
        for b in blocks_between(ep, seg.start, seg.end):
            out.append(_line(b, seg.start, offset))
        offset += seg.duration
    return "\n".join(out)


def render_after(promo: Promo, episodes: Dict[int, Episode], seconds: float = 10.0, max_lines: int = 8) -> str:
    seg = promo.segments[-1]
    ep = episodes[seg.episode]
    nxt = [b for b in ep.blocks if b.start >= seg.end - 0.05 and b.start < seg.end + seconds][:max_lines]
    if not nxt:
        return "(the episode ends here)"
    return "\n".join(f"[+{b.start - seg.end:4.1f}s after cut] {b.label()}: {b.text}" for b in nxt)


def hook_window_blocks(promo: Promo, episodes: Dict[int, Episode]) -> List[Block]:
    seg = promo.segments[0]
    ep = episodes[seg.episode]
    return blocks_between(ep, seg.start, min(seg.end, seg.start + HOOK_WINDOW))


def overlap_ratio(a: Segment, b: Segment) -> float:
    if a.episode != b.episode:
        return 0.0
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    shorter = max(0.01, min(a.duration, b.duration))
    return inter / shorter
