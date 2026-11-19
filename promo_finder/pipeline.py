"""The promo-finding pipeline.

    parse (code) -> series guide (1 AI call) -> per-episode mining (1 AI call/episode,
    + 1 repair call if any pick breaks a rule) -> cross-episode trailers (1-2 AI calls)
    -> blind judge (1 AI call per promo) -> score, de-duplicate, rank (code) -> reports

The AI only ever chooses block IDs; code turns them into timestamps, checks the
20-60 s rule, snaps cuts to shot changes and does the ranking arithmetic.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from . import prompts as P
from .clips import (MAX_MOMENT, MAX_PROMO, MIN_MOMENT, MIN_PROMO, Moment, Promo, Segment,
                    check_promo_duration, explain_fix, make_segment, overlap_ratio, render_after, render_clip, snap)
from .heuristics import (autofix_length, description_mismatch, fix_moment, flags_for, rules_candidates,
                         script_cliff_line, script_hook_line, signals_for, tighten_hook)
from .llm import LLM, LLMError
from .screenplay import Episode, blocks_between, fmt_ts, render_blocks


# Listeners receive every log line and progress event (the web UI registers one).
LISTENERS: List = []


def emit(event: Dict) -> None:
    for fn in list(LISTENERS):
        try:
            fn(event)
        except Exception:  # noqa: BLE001 - a broken listener must never break a run
            pass


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)
    emit({"type": "log", "time": time.strftime("%H:%M:%S"), "msg": msg})


def progress(phase: str, done: int = 0, total: int = 0) -> None:
    emit({"type": "progress", "phase": phase, "done": done, "total": total})


# ---------------------------------------------------------------- 1. series guide

def build_bible(llm: LLM, title: str, episodes: List[Episode]) -> Dict:
    loglines = "\n".join(f"Ep {e.number}: {e.logline}" for e in episodes if e.logline)
    speakers = Counter(b.speaker for e in episodes for b in e.blocks if b.kind == "DIALOGUE")
    sp = ", ".join(f"{k} ({v})" for k, v in speakers.most_common(40))
    msgs = [{"role": "system", "content": "You write compact series guides for promo editors. Reply with one JSON object only."},
            {"role": "user", "content": P.BIBLE_USER.format(title=title, loglines=loglines, speakers=sp)}]
    try:
        bible = llm.chat_json(msgs, tag="bible", max_tokens=1800)
    except (LLMError, ValueError) as e:
        log(f"[bible] failed ({e}); continuing without a cast list")
        bible = {}
    bible.setdefault("premise", episodes[0].logline if episodes else "")
    bible.setdefault("genre", "romantic drama")
    bible.setdefault("characters", [])
    return bible


def cast_text(bible: Dict) -> str:
    lines = []
    for c in bible.get("characters", [])[:12]:
        if not isinstance(c, dict):
            continue
        labels = ", ".join(c.get("labels", [])[:4]) if isinstance(c.get("labels"), list) else ""
        lines.append(f"- {c.get('name', '?')}" + (f" [{labels}]" if labels else "") + f": {c.get('who', '')}")
    return "\n".join(lines) or "(not available)"


# ---------------------------------------------------------------- 2. per-episode mining

def _as_obj(data, list_key: str) -> Dict:
    """Models sometimes return a bare list, a single item, or fields of the wrong type.
    Normalise to {list_key: [...], ...} so one odd reply never crashes a run."""
    if isinstance(data, list):
        data = {list_key: [x for x in data if isinstance(x, dict)]}
    if not isinstance(data, dict):
        return {list_key: []}
    if list_key not in data and ("start_block" in data or "moment_ids" in data):
        data = {list_key: [data]}
    out = dict(data)
    for k in (list_key, "moments"):
        v = out.get(k)
        out[k] = [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
    return out


def _validate_mined(ep: Episode, data: Dict):
    """Returns (promos, moments, errors_for_the_model, code_fixes).
    promos/moments are lists of (model_item, Segment, notes). Length problems are fixed
    by code (models are bad at timestamp arithmetic); only problems code cannot fix
    (e.g. made-up block IDs) are sent back to the model."""
    promos, moments, errors, fixes = [], [], [], []
    for n, p in enumerate(data.get("promos", []) or [], start=1):
        if not isinstance(p, dict):
            continue
        seg, err = make_segment(ep, p.get("start_block", ""), p.get("end_block", ""))
        label = f"Promo {n} \"{p.get('title', '')}\" ({p.get('start_block')}->{p.get('end_block')})"
        if err:
            errors.append(f"- {label}: {err}")
            continue
        notes = []
        derr = check_promo_duration(seg.duration)
        if derr:
            fixed, note = autofix_length(ep, seg)
            if fixed is None:
                errors.append(f"- {label}: {derr}; {explain_fix(ep, seg)}")
                continue
            fixes.append(f"{label}: {derr} -> {note}")
            seg = fixed
            notes.append(note)
        seg2, note2 = tighten_hook(ep, seg)
        if note2:
            seg = seg2
            notes.append(note2)
        promos.append((p, seg, notes))
    for n, m in enumerate(data.get("moments", []) or [], start=1):
        if not isinstance(m, dict):
            continue
        seg, err = make_segment(ep, m.get("start_block", ""), m.get("end_block", ""))
        label = f"Moment {n} ({m.get('start_block')}->{m.get('end_block')})"
        if err:
            errors.append(f"- {label}: {err}")
            continue
        if not (MIN_MOMENT <= seg.duration <= MAX_MOMENT):
            fixed = fix_moment(ep, seg)
            if fixed is None:
                errors.append(f"- {label}: lasts {seg.duration:.1f}s, must be 3-15s")
                continue
            fixes.append(f"{label}: {seg.duration:.1f}s -> {fixed.duration:.1f}s by code")
            seg = fixed
        moments.append((m, seg, []))
    return promos, moments, errors, fixes


def _dedupe(items: List[Tuple], thresh: float) -> List[Tuple]:
    kept: List[Tuple] = []
    for it in items:
        if all(overlap_ratio(it[1], k[1]) < thresh for k in kept):
            kept.append(it)
    return kept


def mine_episode(llm: LLM, ep: Episode, bible: Dict, story_so_far: str, title: str, k: int) -> Dict:
    t0 = time.time()
    user = P.MINE_USER.format(
        title=title, genre=bible.get("genre", ""), premise=bible.get("premise", ""), cast=cast_text(bible),
        story_so_far=story_so_far or "(this is the first episode)", ep=ep.number, logline=ep.logline,
        continuity=ep.continuity or "-", listing=render_blocks(ep.blocks), k=k,
    )
    msgs = [{"role": "system", "content": P.EDITOR_SYSTEM}, {"role": "user", "content": user}]
    rec = {"episode": ep.number, "proposed_promos": 0, "proposed_moments": 0, "length_fixes_by_code": [],
           "errors_first_try": [], "repaired": False, "errors_after_repair": [], "raw_first": None, "raw_repair": None}
    try:
        data = _as_obj(llm.chat_json(msgs, tag=f"mine:ep{ep.number:02d}"), "promos")
    except (LLMError, ValueError) as e:
        rec["fatal"] = str(e)
        log(f"[mine] Ep {ep.number:02d}: FAILED ({e})")
        return {"promos": [], "moments": [], "record": rec}
    rec["raw_first"] = data
    rec["proposed_promos"] = len(data.get("promos", []) or [])
    rec["proposed_moments"] = len(data.get("moments", []) or [])
    promos, moments, errors, fixes = _validate_mined(ep, data)
    rec["length_fixes_by_code"] = fixes
    rec["errors_first_try"] = errors
    if errors:
        fix_msgs = msgs + [{"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
                           {"role": "user", "content": P.REPAIR_USER.format(errors="\n".join(errors))}]
        try:
            data2 = _as_obj(llm.chat_json(fix_msgs, tag=f"mine-repair:ep{ep.number:02d}"), "promos")
            rec["raw_repair"] = data2
            rec["repaired"] = True
            p2, m2, e2, f2 = _validate_mined(ep, data2)
            rec["errors_after_repair"] = e2
            rec["length_fixes_by_code"] += f2
            promos = promos + p2
            moments = moments + m2
        except (LLMError, ValueError) as e:
            rec["errors_after_repair"] = [f"repair call failed: {e}"]
    promos = _dedupe(promos, 0.75)[: k + 1]
    moments = _dedupe(moments, 0.6)[:5]
    log(f"[mine] Ep {ep.number:02d}: {len(promos)} promos, {len(moments)} moments"
        f"{' | ' + str(len(fixes)) + ' lengths fixed by code' if fixes else ''}"
        f"{' | repair call for ' + str(len(errors)) + ' bad picks' if errors else ''} in {time.time() - t0:.1f}s")
    return {"promos": promos, "moments": moments, "record": rec}


# ---------------------------------------------------------------- 3. cross-episode trailers

def build_trailers(llm: LLM, moments: List[Moment], bible: Dict, title: str, n_eps: int, k: int) -> Tuple[List[Promo], Dict]:
    rec: Dict = {"requested": k, "errors_first_try": [], "errors_after_repair": []}
    if len(moments) < 2 or k <= 0:
        return [], rec
    by_id = {m.mid: m for m in moments}
    library = "\n".join(
        f"{m.mid} | Ep {m.segment.episode} | {m.segment.duration:.1f}s | {m.kind} | {m.description}" for m in moments)
    msgs = [{"role": "system", "content": P.EDITOR_SYSTEM},
            {"role": "user", "content": P.MONTAGE_USER.format(title=title, genre=bible.get("genre", ""),
                                                             premise=bible.get("premise", ""), cast=cast_text(bible),
                                                             n_eps=n_eps, library=library, k=k)}]

    def validate(data) -> Tuple[List[Promo], List[str]]:
        out, errs = [], []
        for n, t in enumerate(_as_obj(data, "trailers")["trailers"], start=1):
            raw_ids = t.get("moment_ids")
            label0 = f"- Trailer {n} \"{t.get('title', '')}\""
            if not isinstance(raw_ids, list):
                errs.append(f"{label0}: moment_ids must be a list of IDs")
                continue
            ids = [str(x).strip().upper() for x in raw_ids]
            ids = [re.sub(r"^M(\d+)$", lambda mm: f"M{int(mm.group(1)):03d}", x) for x in ids]
            bad = [x for x in ids if x not in by_id]
            label = f"Trailer {n} \"{t.get('title', '')}\""
            if bad:
                errs.append(f"- {label}: unknown moment ids {bad}")
                continue
            if not 2 <= len(ids) <= 4:
                errs.append(f"- {label}: uses {len(ids)} moments, must use 2-4")
                continue
            if len(set(ids)) != len(ids):
                errs.append(f"- {label}: uses the same moment twice")
                continue
            segs = [by_id[x].segment for x in ids]
            if any(overlap_ratio(a, b) > 0 for i, a in enumerate(segs) for b in segs[i + 1:]):
                errs.append(f"- {label}: two moments overlap (repeated footage)")
                continue
            order = [(s.episode, s.start) for s in segs]
            if order != sorted(order):
                segs = sorted(segs, key=lambda s: (s.episode, s.start))
            total = sum(s.duration for s in segs)
            if not (MIN_PROMO <= total <= MAX_PROMO):
                errs.append(f"- {label}: total {total:.1f}s, must be 20-60s (moment lengths: "
                            + ", ".join(f"{x}={by_id[x].segment.duration:.1f}s" for x in ids) + ")")
                continue
            out.append(Promo(pid="", kind="montage", title=str(t.get("title", "")).strip() or "Trailer",
                             segments=[Segment(s.episode, s.start, s.end, s.start_block, s.end_block) for s in segs],
                             hook=str(t.get("hook", "")), cliffhanger=str(t.get("cliffhanger", "")),
                             why=str(t.get("why", "")), context_gap=""))
        return out, errs

    try:
        data = llm.chat_json(msgs, tag="trailers", max_tokens=2000)
        rec["raw_first"] = data
        out, errs = validate(data)
    except (LLMError, ValueError, TypeError, AttributeError, KeyError) as e:
        rec["fatal"] = f"{type(e).__name__}: {e}"
        return [], rec
    rec["errors_first_try"] = errs
    if errs:
        fix = msgs + [{"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
                      {"role": "user", "content": P.MONTAGE_REPAIR_USER.format(errors="\n".join(errs))}]
        try:
            data2 = llm.chat_json(fix, tag="trailers-repair", max_tokens=2000)
            rec["raw_repair"] = data2
            out2, errs2 = validate(data2)
            rec["errors_after_repair"] = errs2
            seen = {tuple((s.episode, s.start) for s in p.segments) for p in out}
            out += [p for p in out2 if tuple((s.episode, s.start) for s in p.segments) not in seen]
        except (LLMError, ValueError, TypeError, AttributeError, KeyError) as e:
            rec["errors_after_repair"] = [f"repair call failed: {type(e).__name__}: {e}"]
    return out[:k], rec


# ---------------------------------------------------------------- 3b. optional AI hook re-cut

def recut_hook(llm: LLM, promo: Promo, episodes: Dict[int, Episode]) -> Optional[str]:
    """Ask the model where a single-stretch clip should START so the strongest line lands in
    the first 3 s. Code limits the choice to blocks that keep the clip >= 20 s, and snaps."""
    if len(promo.segments) != 1:
        return None
    seg = promo.segments[0]
    ep = episodes[seg.episode]
    latest = seg.end - MIN_PROMO - 0.05
    cands = [b for b in ep.blocks if seg.start - 0.01 <= b.start <= min(seg.start + 15.0, latest)]
    if len(cands) < 2:
        return None
    opening = "\n".join(f"[{b.bid} +{max(0.0, b.start - seg.start):4.1f}s] {b.label()}: {b.text}"
                        for b in blocks_between(ep, seg.start, min(seg.end, seg.start + 18.0)))
    msgs = [{"role": "system", "content": P.EDITOR_SYSTEM},
            {"role": "user", "content": P.RECUT_USER.format(current=cands[0].bid, first_allowed=cands[0].bid,
                                                           last_allowed=cands[-1].bid, opening=opening)}]
    try:
        j = llm.chat_json(msgs, tag=f"recut:{promo.pid}", temperature=0.1, max_tokens=300)
    except (LLMError, ValueError):
        return None
    if isinstance(j, list):
        j = next((x for x in j if isinstance(x, dict)), None)
    if not isinstance(j, dict):
        return None
    b = ep.block(str(j.get("start_block", "")))
    if b is None or b.bid not in {c.bid for c in cands} or b.start <= seg.start + 0.05:
        return None
    s2, e2 = snap(ep, b.start, seg.end)
    if e2 - s2 < MIN_PROMO or s2 <= seg.start + 0.05:
        return None
    moved = s2 - seg.start
    promo.segments[0] = Segment(ep.number, s2, e2, b.bid, seg.end_block)
    promo.hook = ""   # the old hook text described the old opening; the judge re-describes it
    note = f"start moved {moved:.1f}s later by the AI hook re-cut ({str(j.get('reason', ''))[:90]})"
    promo.notes.append(note)
    return note


# ---------------------------------------------------------------- 4. blind judge

def _num(x, lo=1, hi=10) -> Optional[int]:
    try:
        v = int(round(float(str(x).strip().split("/")[0])))
        return max(lo, min(hi, v))
    except (TypeError, ValueError, OverflowError):
        return None


def _first3(promo: Promo, episodes: Dict[int, Episode]) -> str:
    seg = promo.segments[0]
    ep = episodes[seg.episode]
    from .screenplay import blocks_between
    bl = blocks_between(ep, seg.start, min(seg.end, seg.start + 3.0))
    return "\n".join(f"[{max(0.0, b.start - seg.start):4.1f}s] {b.label()}: {b.text}" for b in bl) or "(nothing)"


def judge_promo(llm: LLM, promo: Promo, episodes: Dict[int, Episode], genre: str, temperature: float = 0.1,
                tag_suffix: str = "") -> Dict:
    msgs = [{"role": "system", "content": P.JUDGE_SYSTEM},
            {"role": "user", "content": P.JUDGE_USER.format(dur=promo.duration, genre=genre or "romantic drama",
                                                           first3=_first3(promo, episodes),
                                                           clip=render_clip(promo, episodes),
                                                           after=render_after(promo, episodes))}]
    try:
        j = llm.chat_json(msgs, tag=f"judge:{promo.pid}{tag_suffix}", temperature=temperature, max_tokens=800)
    except (LLMError, ValueError) as e:
        return {"error": str(e)}
    if isinstance(j, list):
        j = next((x for x in j if isinstance(x, dict)), None)
    if not isinstance(j, dict):
        return {"error": "judge reply was not a JSON object"}
    out = {
        "first_3_seconds": str(j.get("first_3_seconds", "")),
        "ending": str(j.get("ending", "")),
        "problems": [str(x) for x in (j.get("problems") if isinstance(j.get("problems"), list) else
                                      ([j["problems"]] if j.get("problems") else [])) if str(x).strip()][:5],
        "hook_score": _num(j.get("hook_score")),
        "cliffhanger_score": _num(j.get("cliffhanger_score")),
        "standalone_score": _num(j.get("standalone_score")),
        "verdict": str(j.get("verdict", "")).strip().lower().split("|")[0],
        "pitch": str(j.get("pitch", "")),
    }
    if None in (out["hook_score"], out["cliffhanger_score"], out["standalone_score"]):
        out["error"] = "judge returned non-numeric scores"
    return out


# ---------------------------------------------------------------- 5. scoring and ranking

WEIGHTS = {"hook": 0.40, "cliff": 0.35, "standalone": 0.25}


def score_promo(p: Promo) -> float:
    j, s = p.judge, p.signals
    if j and not j.get("error"):
        base = (WEIGHTS["hook"] * j["hook_score"] + WEIGHTS["cliff"] * j["cliffhanger_score"]
                + WEIGHTS["standalone"] * j["standalone_score"])
        base += {"post": 0.3, "maybe": 0.0, "reject": -0.7}.get(j.get("verdict", ""), 0.0)
    else:  # no judge: fall back to rule signals (rough 1-10 scale)
        base = 3.0 + 0.5 * min(6.0, max(0.0, s.get("hook_signal", 0))) + 0.4 * min(6.0, max(0.0, s.get("cliff_signal", 0)))
    base += 0.05 * min(6.0, max(0.0, s.get("hook_signal", 0))) + 0.05 * min(6.0, max(0.0, s.get("cliff_signal", 0)))
    first_ep = p.segments[0].episode
    if first_ep <= 5:          # new viewers start at episode 1: premise promos convert best
        base += 0.3
    elif first_ep <= 15:
        base += 0.15
    if s.get("opens_on_camera_only"):
        base -= 0.3
    if not (25 <= p.duration <= 50):
        base -= 0.15
    return round(base, 3)


def rank_reason(p: Promo) -> str:
    j = p.judge or {}
    bits = []
    if j and not j.get("error"):
        bits.append(f"hook {j['hook_score']}/10, cliffhanger {j['cliffhanger_score']}/10, stands alone {j['standalone_score']}/10")
        if j.get("pitch"):
            bits.append(j["pitch"].rstrip(".") + ".")
    else:
        bits.append("rule signals only (judge unavailable)")
    if p.kind == "montage":
        bits.append("Trailer cut across episodes: sells the whole premise.")
    elif p.segments[0].episode <= 5:
        bits.append(f"From Ep {p.segments[0].episode}: a new viewer who taps through lands right on this story.")
    return " ".join(bits)


REPEAT_EPISODE_PENALTY = 0.5   # per promo already placed from the same episode, beyond the first


def rank(promos: List[Promo]) -> Tuple[List[Promo], List[Promo]]:
    for p in promos:
        p.score = score_promo(p)
    ordered = sorted(promos, key=lambda p: -p.score)
    unique: List[Promo] = []
    alternates: List[Promo] = []
    for p in ordered:
        dup = next((q for q in unique if any(overlap_ratio(a, b) > 0.5 for a in p.segments for b in q.segments)
                    and (p.kind == "montage") == (q.kind == "montage")), None)
        if dup:
            p.flags.append(f"near-duplicate of {dup.pid}")
            alternates.append(p)
        else:
            unique.append(p)

    # Greedy ranking with a small penalty for repeating an episode, so the posting
    # list spreads across different story beats instead of one scene three times.
    def adjusted(p: Promo, placed: List[Promo]) -> float:
        if p.kind == "montage":
            return p.score
        same = sum(1 for q in placed if q.kind != "montage" and q.segments[0].episode == p.segments[0].episode)
        return p.score - REPEAT_EPISODE_PENALTY * max(0, same - 1)

    final: List[Promo] = []
    remaining = list(unique)
    while remaining:
        best = max(remaining, key=lambda p: (adjusted(p, final), p.score))
        final.append(best)
        remaining.remove(best)
    for i, p in enumerate(final, start=1):
        p.rank = i
        p.rank_reason = rank_reason(p)
    return final, alternates


# ---------------------------------------------------------------- orchestration

def run(input_dir: str, out_dir: str, llm: Optional[LLM], *, per_episode: int = 3, trailers: int = 4,
        episodes_filter: Optional[List[int]] = None, judge: bool = True, workers: int = 1,
        rules_pool: int = 1, judge_llm: Optional[LLM] = None, recut_hooks: bool = False) -> Dict:
    from .screenplay import load_show

    t_start = time.time()
    title = os.path.basename(os.path.normpath(input_dir))
    episodes = load_show(input_dir)
    if episodes_filter:
        episodes = [e for e in episodes if e.number in episodes_filter]
    if not episodes:
        raise SystemExit(f"No episodes found in {input_dir}")
    by_num = {e.number: e for e in episodes}
    os.makedirs(os.path.join(out_dir, "raw"), exist_ok=True)
    parse_warnings = {e.number: e.warnings for e in episodes if e.warnings}
    progress("parse", 1, 1)
    log(f"Parsed {len(episodes)} episodes, {sum(len(e.blocks) for e in episodes)} timed blocks, "
        f"{sum(e.duration for e in episodes) / 60:.1f} min of runtime. Parse warnings: {sum(len(v) for v in parse_warnings.values())}")

    promos: List[Promo] = []
    moments: List[Moment] = []
    mining_records: List[Dict] = []
    trailer_rec: Dict = {}
    bible: Dict = {"premise": episodes[0].logline, "genre": "romantic drama", "characters": []}

    def add_rules_picks(max_per_ep: int) -> int:
        added = 0
        for e in episodes:
            taken = [s for p in promos for s in p.segments if s.episode == e.number]
            n = 0
            for seg in rules_candidates(e, per_episode=3):
                if n >= max_per_ep:
                    break
                if any(overlap_ratio(seg, t) > 0.5 for t in taken):
                    continue
                n += 1
                added += 1
                promos.append(Promo(pid=f"E{e.number:02d}-R{n}", kind="rules", title=f"Rules pick, Ep {e.number}",
                                    segments=[seg], hook="", cliffhanger="",
                                    why="picked by keyword/emotion rules (no AI), kept so the judge can compare"))
                taken.append(seg)
        return added

    if llm is None:
        log("Rules-only mode (no AI): picking clips from hook/cliffhanger keyword signals")
        add_rules_picks(min(2, per_episode))
    else:
        progress("guide", 0, 1)
        bible = build_bible(llm, title, episodes)
        progress("guide", 1, 1)
        with open(os.path.join(out_dir, "raw", "series_guide.json"), "w", encoding="utf-8") as f:
            json.dump(bible, f, indent=2, ensure_ascii=False)
        log(f"Series guide: {bible.get('premise', '')[:120]}")

        all_eps = load_show(input_dir) if episodes_filter else episodes
        def story_so_far(e: Episode) -> str:
            prev = [x for x in all_eps if x.number < e.number][-3:]
            return "\n".join(f"Ep {x.number}: {x.logline}" for x in prev)

        results: Dict[int, Dict] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futs = {pool.submit(mine_episode, llm, e, bible, story_so_far(e), title, per_episode): e.number for e in episodes}
            progress("mine", 0, len(futs))
            for fu in as_completed(futs):
                progress("mine", len(results) + 1, len(futs))
                try:
                    results[futs[fu]] = fu.result()
                except Exception as e:  # noqa: BLE001 - one bad episode must not kill the run
                    log(f"[mine] Ep {futs[fu]:02d}: crashed ({type(e).__name__}: {e}); skipped")
                    results[futs[fu]] = {"promos": [], "moments": [],
                                         "record": {"episode": futs[fu], "fatal": f"{type(e).__name__}: {e}"}}
        mid = 0
        for num in sorted(results):
            r = results[num]
            mining_records.append(r["record"])
            for n, (p, seg, notes) in enumerate(r["promos"], start=1):
                promos.append(Promo(pid=f"E{num:02d}-P{n}", kind="single", title=str(p.get("title", "")).strip(),
                                    segments=[seg], hook=str(p.get("hook", "") or ""),
                                    cliffhanger=str(p.get("cliffhanger", "") or ""),
                                    why=str(p.get("why", "") or ""), context_gap=str(p.get("context_gap", "") or ""),
                                    notes=list(notes)))
            for m, seg, _ in r["moments"]:
                mid += 1
                moments.append(Moment(f"M{mid:03d}", seg, str(m.get("kind", "")), str(m.get("description", ""))))
        with open(os.path.join(out_dir, "raw", "mining_by_episode.json"), "w", encoding="utf-8") as f:
            json.dump(mining_records, f, indent=2, ensure_ascii=False)
        with open(os.path.join(out_dir, "raw", "moment_library.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": m.mid, "kind": m.kind, "description": m.description, **m.segment.as_dict()} for m in moments],
                      f, indent=2, ensure_ascii=False)

        if trailers > 0 and len(episodes) > 1:
            progress("trailers", 0, 1)
            tr, trailer_rec = build_trailers(llm, moments, bible, title, len(episodes), trailers)
            progress("trailers", 1, 1)
            for n, p in enumerate(tr, start=1):
                p.pid = f"T{n}"
                promos.append(p)
            with open(os.path.join(out_dir, "raw", "trailers.json"), "w", encoding="utf-8") as f:
                json.dump(trailer_rec, f, indent=2, ensure_ascii=False)
            log(f"[trailers] {len(tr)} cross-episode trailers built")
        if rules_pool > 0:
            n_rules = add_rules_picks(rules_pool)
            log(f"[rules] added {n_rules} keyword-rule picks to the judged pool (baseline for comparison)")

    recut_count = 0
    if llm is not None and recut_hooks:
        targets = [p for p in promos if p.kind in ("single", "rules")]
        log(f"[recut] asking the model for a stronger opening on {len(targets)} clips")
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for i, note in enumerate(pool.map(lambda p: recut_hook(llm, p, by_num), targets), start=1):
                recut_count += bool(note)
                progress("recut", i, len(targets))
        log(f"[recut] {recut_count} clips now start later, on a stronger first line")

    for p in promos:
        p.signals = signals_for(p, by_num)
        p.flags = flags_for(p, p.signals)
        p.script_hook = script_hook_line(p, by_num)
        p.script_cliff = script_cliff_line(p, by_num)

    if llm is not None and judge:
        log(f"[judge] reviewing {len(promos)} promos blind (no show context)")
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futs = {pool.submit(judge_promo, judge_llm or llm, p, by_num, bible.get("genre", "")): p for p in promos}
            done = 0
            progress("judge", 0, len(promos))
            for fu in as_completed(futs):
                try:
                    futs[fu].judge = fu.result()
                except Exception as e:  # noqa: BLE001
                    futs[fu].judge = {"error": f"{type(e).__name__}: {e}"}
                done += 1
                progress("judge", done, len(promos))
                if done % 10 == 0 or done == len(promos):
                    log(f"[judge] {done}/{len(promos)}")
        for p in promos:
            j = p.judge
            if j.get("error"):
                p.flags.append("judge failed: " + j["error"][:80])
            else:
                # fill fields the miner left empty (and all fields of rules picks) from the judge's reading
                if not p.hook.strip():
                    p.hook = j.get("first_3_seconds", "")
                    p.notes.append("hook text written by the judge")
                if not p.cliffhanger.strip():
                    p.cliffhanger = j.get("ending", "")
                    p.notes.append("cliffhanger text written by the judge")
                if p.kind == "rules" and j.get("pitch"):
                    p.why = j["pitch"]
                if j.get("verdict") == "reject":
                    p.flags.append("judge says reject")
                if (j.get("standalone_score") or 10) <= 4:
                    p.flags.append("judge: hard to follow without earlier episodes")
                if (j.get("hook_score") or 10) <= 4:
                    p.flags.append("judge: weak first 3 seconds")
                if (j.get("cliffhanger_score") or 10) <= 4:
                    p.flags.append("judge: ending feels resolved")

    for p in promos:   # model descriptions that quote a line which is not actually at the start / the cut
        if p.kind != "rules":
            p.flags += description_mismatch(p, by_num)
    for p in promos:   # no judge (rules-only or --no-judge): describe hook/ending from the script itself
        if not p.hook.strip():
            p.hook = p.script_hook
        if not p.cliffhanger.strip():
            p.cliffhanger = p.script_cliff

    ranked, alternates = rank(promos)
    by_kind = Counter(p.kind for p in ranked)
    top10_kinds = Counter(p.kind for p in ranked[:10])
    progress("rank", 0, 1)
    summary = {
        "show": title,
        "input_dir": os.path.abspath(input_dir),
        "episodes": len(episodes),
        "runtime_minutes": round(sum(e.duration for e in episodes) / 60, 1),
        "mode": "rules-only" if llm is None else "llm",
        "llm": llm.stats() if llm else None,
        "judge_llm": judge_llm.stats() if judge_llm else None,
        "promos_ranked": len(ranked),
        "ranked_by_source": dict(by_kind),
        "top10_by_source": dict(top10_kinds),
        "near_duplicates_removed": len(alternates),
        "hook_recuts": recut_count if recut_hooks else None,
        "mining": {
            "episodes_mined": len(mining_records),
            "promos_proposed": sum(r.get("proposed_promos", 0) for r in mining_records),
            "moments_proposed": sum(r.get("proposed_moments", 0) for r in mining_records),
            "lengths_fixed_by_code": sum(len(r.get("length_fixes_by_code", [])) for r in mining_records),
            "rule_breaks_first_try": sum(len(r.get("errors_first_try", [])) for r in mining_records),
            "rule_breaks_after_repair": sum(len(r.get("errors_after_repair", [])) for r in mining_records),
            "episodes_needing_repair": sum(1 for r in mining_records if r.get("repaired")),
            "failed_episodes": [r["episode"] for r in mining_records if r.get("fatal")],
        },
        "trailers": {k: v for k, v in trailer_rec.items() if k not in ("raw_first", "raw_repair")},
        "parse_warnings": parse_warnings,
        "wall_clock_seconds": round(time.time() - t_start, 1),
    }
    from .report import write_reports
    write_reports(out_dir, title, ranked, alternates, summary, by_num, bible)
    progress("done", 1, 1)
    log(f"Done: {len(ranked)} ranked promos -> {out_dir}  ({summary['wall_clock_seconds']}s)")
    return summary
