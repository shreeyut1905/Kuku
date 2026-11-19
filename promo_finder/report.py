"""Write the outputs an editor actually uses: ranked Markdown, CSV sheet, HTML viewer, JSON."""
from __future__ import annotations

import csv
import html
import json
import os
import time
from typing import Dict, List

from .clips import Promo, render_after, render_clip
from .screenplay import Episode, fmt_ts


def _segments_str(p: Promo) -> str:
    return "; ".join(f"Ep {s.episode} {fmt_ts(s.start)}-{fmt_ts(s.end)}" for s in p.segments)


def _judge_str(p: Promo) -> str:
    j = p.judge or {}
    if not j:
        return "not judged"
    if j.get("error"):
        return f"judge error: {j['error'][:100]}"
    return (f"hook {j['hook_score']}/10 · cliffhanger {j['cliffhanger_score']}/10 · stands alone "
            f"{j['standalone_score']}/10 · verdict **{j.get('verdict', '?')}**")


def _md_promo(p: Promo, full: bool) -> str:
    out = [f"### {p.rank}. {p.title or p.pid}  ·  `{p.pid}`  ·  {p.duration:.1f}s  ·  score {p.score:.2f}"]
    out.append("")
    out.append("| Segment | Episode | Start | End | Length |")
    out.append("|---|---|---|---|---|")
    for k, s in enumerate(p.segments, start=1):
        out.append(f"| {k} | {s.episode} | {fmt_ts(s.start)} | {fmt_ts(s.end)} | {s.duration:.1f}s |")
    out.append("")
    out.append(f"- **Hook (first 3 s):** {p.hook}")
    if p.script_hook:
        out.append(f"  - script, first 3 s: _{p.script_hook}_")
    out.append(f"- **Cliffhanger:** {p.cliffhanger}")
    if p.script_cliff:
        out.append(f"  - script, last line before the cut: _{p.script_cliff}_")
    out.append(f"- **Why it works:** {p.why}")
    if p.notes:
        out.append(f"- **Changed by code:** " + "; ".join(p.notes))
    if full:
        out.append(f"- **Why post it here:** {p.rank_reason}")
        out.append(f"- **Blind judge:** {_judge_str(p)}")
        probs = (p.judge or {}).get("problems") or []
        if probs:
            out.append(f"- **Judge's concerns:** " + "; ".join(probs))
        if p.context_gap and p.context_gap.lower() not in ("none", "none.", "-", ""):
            out.append(f"- **Context a stranger lacks:** {p.context_gap}")
    if p.flags:
        out.append(f"- **Flags:** " + "; ".join(p.flags))
    out.append("")
    return "\n".join(out)


def write_reports(out_dir: str, title: str, ranked: List[Promo], alternates: List[Promo], summary: Dict,
                  episodes: Dict[int, Episode], bible: Dict) -> None:
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    llm = summary.get("llm") or {}

    # ---------- JSON (everything)
    with open(os.path.join(out_dir, "promos.json"), "w", encoding="utf-8") as f:
        json.dump({"show": title, "generated": stamp, "summary": summary,
                   "promos": [p.as_dict() for p in ranked],
                   "near_duplicates": [p.as_dict() for p in alternates]}, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # ---------- CSV (editor's review sheet)
    with open(os.path.join(out_dir, "promos.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "id", "type", "title", "episodes", "segments", "duration_s", "hook", "cliffhanger",
                    "why_it_works", "script_first_3s", "script_last_line", "why_this_rank", "judge_hook",
                    "judge_cliffhanger", "judge_standalone", "judge_verdict", "judge_concerns", "flags",
                    "changed_by_code", "score", "editor_verdict", "editor_notes"])
        for p in ranked:
            j = p.judge or {}
            w.writerow([p.rank, p.pid, p.kind, p.title, " + ".join(map(str, p.episodes)), _segments_str(p),
                        p.duration, p.hook, p.cliffhanger, p.why, p.script_hook, p.script_cliff, p.rank_reason,
                        j.get("hook_score"), j.get("cliffhanger_score"), j.get("standalone_score"), j.get("verdict"),
                        " | ".join(j.get("problems") or []), " | ".join(p.flags), " | ".join(p.notes), p.score, "", ""])

    # ---------- Markdown (the main human-readable output)
    m = summary.get("mining", {})
    lines = [f"# Promo picks: {title}", ""]
    if llm:
        cost = llm.get("cost_usd")
        fresh = llm.get("per_show_if_run_fresh") or {}
        fc = fresh.get("cost_usd")
        lines.append(f"Model **{llm.get('model')}** via `{llm.get('base_url')}` · this run: {llm.get('live_calls')} live calls "
                     f"+ {llm.get('cache_hits')} answered from cache, cost {'$%.4f' % cost if cost is not None else 'unknown'} · "
                     f"whole show if run fresh: {fresh.get('calls')} calls, {fresh.get('prompt_tokens', 0):,} input / "
                     f"{fresh.get('completion_tokens', 0):,} output tokens, {fresh.get('model_seconds', 0) / 60:.0f} min of model time, "
                     f"{'$%.4f' % fc if fc is not None else 'cost unknown'} · generated {stamp}")
    else:
        lines.append(f"Rules-only mode (no AI) · generated {stamp}")
    jl = summary.get("judge_llm") or {}
    if jl:
        jc = jl.get("cost_usd")
        lines.append(f"Judge model **{jl.get('model')}** · {jl.get('live_calls')} live calls · cost "
                     f"{'$%.4f' % jc if jc is not None else 'unknown'}")
    lines.append("")
    lines.append(f"{summary['episodes']} episodes ({summary['runtime_minutes']} min) · {summary['promos_ranked']} promos ranked · "
                 f"{summary['near_duplicates_removed']} near-duplicates set aside. Every timestamp comes from the script's own "
                 f"timings; cuts are snapped to shot changes and never fall mid-line.")
    if bible.get("premise") and llm:
        lines += ["", f"**Premise (AI series guide):** {bible['premise']}"]
    src = summary.get("top10_by_source") or {}
    if src:
        lines += ["", "Top 10 by source: " + ", ".join(f"{v} {k}" for k, v in src.items())
                  + " (single = one AI-picked stretch, montage = AI trailer across moments, rules = keyword baseline)."]
    lines += ["", "## Post these first", ""]
    for p in ranked[:10]:
        lines.append(_md_promo(p, full=True))
    lines += ["## All other promos, ranked", "",
              "| # | ID | Segments | Length | Title | Hook | Cliffhanger | Judge (H/C/S, verdict) | Score | Flags |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for p in ranked[10:]:
        j = p.judge or {}
        js = f"{j.get('hook_score')}/{j.get('cliffhanger_score')}/{j.get('standalone_score')} {j.get('verdict', '')}" if j and not j.get("error") else "-"
        cells = [str(p.rank), p.pid, _segments_str(p), f"{p.duration:.0f}s", p.title, p.hook, p.cliffhanger, js,
                 f"{p.score:.2f}", "; ".join(p.flags)]
        lines.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    lines += ["", "## Where this run may have picked badly (automatic flags)", ""]
    flagged = [p for p in ranked if p.flags]
    counts: Dict[str, int] = {}
    for p in ranked:
        for fl in p.flags:
            key = fl.split(" (")[0] if not fl.startswith("length") else "length outside 25-50s sweet spot"
            key = "near-duplicate" if key.startswith("near-duplicate") else key
            counts[key] = counts.get(key, 0) + 1
    lines.append(f"- {len(flagged)} of {len(ranked)} ranked promos carry at least one flag:")
    for k, v in sorted(counts.items(), key=lambda t: -t[1]):
        lines.append(f"  - {v} × {k}")
    if m:
        lines.append(f"- Mining: the model proposed {m.get('promos_proposed')} promos and {m.get('moments_proposed')} moments. "
                     f"{m.get('lengths_fixed_by_code')} picks had a length outside the rules and were fixed by code; "
                     f"{m.get('rule_breaks_first_try')} picks had problems code could not fix (e.g. a block ID that does not exist) "
                     f"and went back to the model in {m.get('episodes_needing_repair')} repair calls; "
                     f"{m.get('rule_breaks_after_repair')} were still broken after that and were dropped.")
        if m.get("failed_episodes"):
            lines.append(f"- Episodes where mining failed entirely: {m['failed_episodes']}")
    tr = summary.get("trailers") or {}
    if tr:
        lines.append(f"- Trailers: {len(tr.get('errors_first_try', []))} rule breaks on first try, "
                     f"{len(tr.get('errors_after_repair', []))} after repair.")
    lines += ["", "## Run details", "", "```json", json.dumps(summary, indent=2, ensure_ascii=False)[:4000], "```", ""]
    with open(os.path.join(out_dir, "promos.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ---------- HTML viewer (script excerpt per promo)
    cards = []
    for p in ranked:
        j = p.judge or {}
        segs = "".join(f"<li>Ep {s.episode}: <b>{fmt_ts(s.start)} – {fmt_ts(s.end)}</b> ({s.duration:.1f}s)</li>" for s in p.segments)
        flags = "".join(f"<span class='flag'>{html.escape(fl)}</span>" for fl in p.flags)
        jtxt = html.escape(_judge_str(p).replace("**", ""))
        cards.append(f"""
<div class="card {'top' if p.rank <= 10 else ''}">
  <div class="hdr"><span class="rank">#{p.rank}</span> <b>{html.escape(p.title or p.pid)}</b>
    <span class="meta">{p.pid} · {p.kind} · {p.duration:.1f}s · score {p.score:.2f}</span></div>
  <ul class="segs">{segs}</ul>
  <p><b>Hook:</b> {html.escape(p.hook)}<br><span class="judge">script, first 3 s: <i>{html.escape(p.script_hook)}</i></span></p>
  <p><b>Cliffhanger:</b> {html.escape(p.cliffhanger)}<br><span class="judge">script, last line: <i>{html.escape(p.script_cliff)}</i></span></p>
  <p><b>Why it works:</b> {html.escape(p.why)}</p>
  {('<p class="judge"><b>Changed by code:</b> ' + html.escape('; '.join(p.notes)) + '</p>') if p.notes else ''}
  <p class="why"><b>Rank reason:</b> {html.escape(p.rank_reason)}</p>
  <p class="judge"><b>Blind judge:</b> {jtxt}{(' — ' + html.escape('; '.join(j.get('problems') or []))) if j.get('problems') else ''}</p>
  <div>{flags}</div>
  <details><summary>Script of this clip (what the viewer sees/hears)</summary>
  <pre>{html.escape(render_clip(p, episodes))}</pre>
  <p class="after"><b>Right after the cut (not in promo):</b></p><pre class="after">{html.escape(render_after(p, episodes))}</pre></details>
</div>""")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Promos: {html.escape(title)}</title>
<style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1d1d1f;background:#fafafa}}
h1{{font-size:22px}} .sub{{color:#555;font-size:13px}}
.card{{background:#fff;border:1px solid #e3e3e3;border-radius:10px;padding:12px 16px;margin:12px 0}}
.card.top{{border-left:5px solid #2f6fde}}
.hdr{{font-size:16px}} .rank{{font-weight:700;color:#2f6fde;margin-right:6px}} .meta{{color:#777;font-size:12px;margin-left:8px}}
.segs{{margin:6px 0 6px 18px;padding:0}} p{{margin:4px 0;font-size:14px;line-height:1.4}}
.why{{color:#244}} .judge{{color:#444;font-size:13px}}
.flag{{display:inline-block;background:#fff3e0;color:#8a4b00;border-radius:6px;padding:1px 7px;margin:3px 4px 0 0;font-size:12px}}
pre{{white-space:pre-wrap;font-size:12px;background:#f4f4f6;padding:8px;border-radius:6px}} pre.after{{background:#f9f3f3}}
</style></head><body>
<h1>Promo picks: {html.escape(title)}</h1>
<div class="sub">{summary['episodes']} episodes · {summary['promos_ranked']} promos · model {html.escape(str(llm.get('model', 'rules-only')))} · generated {stamp}.
Timestamps are exact script timings (MM:SS.ss from the start of each episode), snapped to shot changes.</div>
{''.join(cards)}
</body></html>"""
    with open(os.path.join(out_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(page)
