"""Parse Kuku-style timestamped microdrama screenplays into a clean timeline.

Plain code, no AI. Every promo timestamp the tool outputs comes from here, so the
model never has to type (or invent) a timestamp; it only points at block IDs.

Expected format (case-insensitive, tolerant of small variations):

    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: ...
    CONTINUITY NOTES: ...
    --- SCRIPT START ---
    SCENE 01
    Scene heading: INT. BALLROOM – NIGHT
    Scene start: 00:00.00
    Scene end: 01:45.00
    Actions and dialogue:
    **CAMERA:**
    Start: (00:00.00) | End: (00:03.20)
    Wide shot of the crowded ballroom ...
    **MAYA (Shocked, Whispering):**
    Start: (00:03.40) | End: (00:04.60)
    "You lied to me."
    --- SCRIPT END ---
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------- data model


@dataclass
class Block:
    bid: str            # "B001" - stable ID inside one episode, used in prompts
    kind: str           # CAMERA | ACTION | SFX | DIALOGUE | TEXT | OTHER
    start: float        # seconds from episode start
    end: float
    text: str
    speaker: str = ""   # for DIALOGUE: "MAYA"
    tags: str = ""      # for DIALOGUE: "Shocked, Whispering" / "O.S." etc.
    scene: int = 0      # scene number this block belongs to

    @property
    def is_shot(self) -> bool:
        return self.kind == "CAMERA"

    def label(self) -> str:
        if self.kind == "DIALOGUE":
            t = f" ({self.tags})" if self.tags else ""
            return f"{self.speaker}{t}"
        return self.kind


@dataclass
class Scene:
    number: int
    heading: str
    start: Optional[float]
    end: Optional[float]


@dataclass
class Episode:
    number: int
    title: str
    logline: str
    continuity: str
    path: str
    scenes: List[Scene] = field(default_factory=list)
    blocks: List[Block] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        ends = [b.end for b in self.blocks] + [s.end for s in self.scenes if s.end is not None]
        return max(ends) if ends else 0.0

    def block(self, bid: str) -> Optional[Block]:
        bid = normalize_bid(bid)
        for b in self.blocks:
            if b.bid == bid:
                return b
        return None

    def index_of(self, bid: str) -> int:
        bid = normalize_bid(bid)
        for i, b in enumerate(self.blocks):
            if b.bid == bid:
                return i
        return -1


# ---------------------------------------------------------------- helpers

_TS = r"(\d{1,2}:)?\d{1,3}:\d{2}(?:[.,]\d+)?"
RE_TS_LINE = re.compile(
    rf"^\s*start\s*:\s*\(?\s*(?P<s>{_TS})\s*\)?\s*\|\s*end\s*:\s*\(?\s*(?P<e>{_TS})\s*\)?\s*$",
    re.I,
)
# "**NAME (tags):**" and also "**NAME (tags)**:" (colon outside the bold)
RE_HEADER = re.compile(r"^\s*\*\*\s*(?P<h>[^*]+?)\s*(?::\s*\*\*|\*\*\s*:)\s*$")
RE_SCENE = re.compile(r"^\s*\**\s*SCENE\s+(?P<n>\d+)\s*\**\s*:?\s*$", re.I)
RE_SCENE_FIELD = re.compile(r"^\s*scene\s+(?P<k>heading|start|end)\s*:\s*(?P<v>.*)$", re.I)
RE_META = re.compile(r"^\s*(?P<k>EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES|EPISODE TITLE)\s*:\s*(?P<v>.*)$", re.I)

KIND_ALIASES = {
    "CAMERA": "CAMERA", "SHOT": "CAMERA", "CAM": "CAMERA",
    "ACTION": "ACTION", "ACTIONS": "ACTION",
    "SOUND FX": "SFX", "SOUND": "SFX", "SFX": "SFX", "MUSIC": "SFX", "SOUND EFFECT": "SFX",
    "TEXT": "TEXT", "TEXT ON SCREEN": "TEXT", "SUPER": "TEXT", "TITLE": "TEXT", "CAPTION": "TEXT",
}


def parse_ts(s: str) -> float:
    """'01:02.50' -> 62.5 ; '1:01:02.5' -> 3662.5"""
    s = s.strip().replace(",", ".")
    parts = s.split(":")
    secs = float(parts[-1])
    mins = int(parts[-2]) if len(parts) >= 2 else 0
    hrs = int(parts[-3]) if len(parts) >= 3 else 0
    return hrs * 3600 + mins * 60 + secs


def fmt_ts(t: float) -> str:
    """62.5 -> '01:02.50' (same style as the screenplays)."""
    t = round(max(0.0, t), 2)          # round first, so 59.996 becomes 01:00.00, never 00:60.00
    m = int(t // 60)
    s = round(t - m * 60, 2)
    return f"{m:02d}:{s:05.2f}"


def normalize_bid(bid: str) -> str:
    m = re.search(r"(\d+)", str(bid))
    return f"B{int(m.group(1)):03d}" if m else str(bid).strip().upper()


def _split_header(h: str):
    """'MAYA (O.S.) (Shocked, Whispering)' -> ('DIALOGUE', 'MAYA', 'O.S.; Shocked, Whispering')"""
    key = re.sub(r"\s+", " ", h).strip()
    up = key.upper()
    if up in KIND_ALIASES:
        return KIND_ALIASES[up], "", ""
    base = re.split(r"[(:]", key, maxsplit=1)[0].strip()      # "CAMERA: Close-up" / "CAMERA (wide)"
    if base.upper() in KIND_ALIASES:
        return KIND_ALIASES[base.upper()], "", ""
    tags = "; ".join(t.strip() for t in re.findall(r"\(([^)]*)\)", key) if t.strip())
    return "DIALOGUE", base.upper(), tags


# ---------------------------------------------------------------- parser


def parse_episode(path: str) -> Episode:
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    meta = {}
    for ln in lines[:40]:
        m = RE_META.match(ln)
        if m:
            meta[m.group("k").upper()] = m.group("v").strip()

    fname = os.path.basename(path)
    num = None
    for src in (meta.get("EPISODE NUMBER", ""), fname):
        m = re.search(r"(\d+)", src)
        if m:
            num = int(m.group(1))
            break
    ep = Episode(
        number=num or 0,
        title=meta.get("EPISODE TITLE", "") or meta.get("EPISODE NUMBER", "") or fname,
        logline=meta.get("EPISODE LOGLINE", ""),
        continuity=meta.get("CONTINUITY NOTES", ""),
        path=path,
    )

    raw = []  # (kind, speaker, tags, start, end, text, scene)
    scene_no = 0
    cur_scene: Optional[Scene] = None
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        ms = RE_SCENE.match(ln)
        if ms:
            scene_no = int(ms.group("n"))
            cur_scene = Scene(scene_no, "", None, None)
            ep.scenes.append(cur_scene)
            i += 1
            continue
        mf = RE_SCENE_FIELD.match(ln)
        if mf and cur_scene is not None:
            k, v = mf.group("k").lower(), mf.group("v").strip()
            if k == "heading":
                cur_scene.heading = v
            else:
                try:
                    setattr(cur_scene, k, parse_ts(v))
                except ValueError:
                    ep.warnings.append(f"bad scene {k} time '{v}' (scene {scene_no})")
            i += 1
            continue
        mh = RE_HEADER.match(ln)
        if mh:
            kind, speaker, tags = _split_header(mh.group("h"))
            # next non-empty line should be the timestamp line
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            mt = RE_TS_LINE.match(lines[j]) if j < n else None
            if not mt:
                ep.warnings.append(f"header without timestamp at line {i + 1}: {ln.strip()[:60]}")
                i += 1
                continue
            start, end = parse_ts(mt.group("s")), parse_ts(mt.group("e"))
            k = j + 1
            text_lines = []
            while k < n and lines[k].strip() and not RE_HEADER.match(lines[k]) \
                    and not RE_SCENE.match(lines[k]) and not lines[k].startswith("---"):
                text_lines.append(lines[k].strip())
                k += 1
            text = " ".join(text_lines).strip()
            if end < start:
                ep.warnings.append(f"end<start at line {j + 1}; swapped")
                start, end = end, start
            raw.append((kind, speaker, tags, start, end, text, scene_no))
            i = k
            continue
        i += 1

    # Stable order: by start time, shots first (a cut "opens" the moment), then file order.
    order = {"CAMERA": 0, "ACTION": 1, "SFX": 2, "TEXT": 3, "DIALOGUE": 4, "OTHER": 5}
    raw_sorted = sorted(enumerate(raw), key=lambda t: (round(t[1][3], 2), order.get(t[1][0], 9), t[0]))
    for idx, (_, r) in enumerate(raw_sorted, start=1):
        kind, speaker, tags, start, end, text, sc = r
        ep.blocks.append(Block(f"B{idx:03d}", kind, start, end, text, speaker, tags, sc))

    if not ep.blocks:
        ep.warnings.append("no timestamped blocks found - check the file format")
    return ep


def load_show(folder: str) -> List[Episode]:
    files = sorted(
        os.path.join(folder, f) for f in os.listdir(folder)
        if f.lower().endswith((".txt", ".md", ".fountain")) and not f.startswith(".")
    )
    eps = [parse_episode(p) for p in files]
    eps.sort(key=lambda e: e.number)
    return eps


# ---------------------------------------------------------------- rendering for prompts


def render_blocks(blocks: List[Block], max_camera_chars: int = 140) -> str:
    """Compact, ID-tagged listing the model reads, e.g.
    [B012 00:39.10-00:41.20] MAYA (Shocked, Whispering): "You lied to me."
    """
    out = []
    for b in blocks:
        txt = b.text
        if b.kind == "CAMERA" and len(txt) > max_camera_chars:
            txt = txt[: max_camera_chars - 1].rstrip() + "…"
        out.append(f"[{b.bid} {fmt_ts(b.start)}-{fmt_ts(b.end)}] {b.label()}: {txt}")
    return "\n".join(out)


def blocks_between(ep: Episode, start: float, end: float) -> List[Block]:
    """Blocks that overlap [start, end) by at least 0.05 s."""
    return [b for b in ep.blocks if b.end > start + 0.05 and b.start < end - 0.05]
