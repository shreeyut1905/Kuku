# Reviewer prompts used in the evaluation

The independent checks in the README were done by **Claude Haiku 5.5 reviewers** run from Claude Code. They are not a different human, but they are a different model family from the tool's model, and they never saw the tool's ranking. These are the exact instructions, so the checks can be re-run with any capable model or agent. Give each reviewer the files listed. The required output format is described at the end of each prompt.

---

## A. Independent best-clip picks (recall check)

One reviewer per batch of about 6 episodes. Output → `eval/independent_picks_haiku.json` as `{"picks": [...]}`. Then validate it with `python3 tools/check_reviewer_picks.py "<show folder>" eval/independent_picks_haiku.json`.

```
You are an experienced promo editor for vertical microdrama apps (ReelShort / DramaBox style). You choose 20-60 second clips from episodes to run as paid ads on TikTok, Instagram Reels and YouTube Shorts.

Read each of these screenplay files completely (read only; do not create, edit or run anything):
- Episode <n>: <path>
...

Each file is one episode of the same English-language show. Every camera shot, action, sound effect and line of dialogue has a timestamp line like "Start: (00:13.50) | End: (00:14.30)" (MM:SS.ss from the start of that episode).

For EACH episode listed, pick the single best promo clip: one continuous stretch, 20 to 60 seconds long, that
1. opens on a HOOK: something in the first 3 seconds that makes a scrolling viewer stop (a slap, a reveal, a shocking or cruel line, a threat);
2. ends on a CLIFFHANGER: it cuts at an unresolved moment, so the viewer needs to know what happens next;
3. makes sense on its own to someone who has never seen the show.

Rules:
- start = a "Start" time copied exactly from the script (the first shot/line of your clip); end = an "End" time copied exactly from the script (the last shot/line of your clip). End minus start must be 20-60 seconds; double-check the arithmetic.
- hook_line and cliffhanger_line must quote or closely describe what is actually in the script at those times.
- promo_worthiness 1-10: how well this clip would perform as a paid social ad compared with the best moments of typical hit microdramas. Be strict and use the full range: most episodes are 4-7; only truly thumb-stopping clips get 9-10.

Return one pick per episode listed, as JSON:
{"picks": [{"episode": 1, "start": "MM:SS.ss", "end": "MM:SS.ss", "hook_line": "...", "cliffhanger_line": "...", "promo_worthiness": 1-10, "why": "..."}]}
```

---

## B. Blind “ad-buyer” review of clips (ranking check)

First export anonymised, shuffled clips: `python3 tools/export_clips_for_review.py <run>/promos.json "<show folder>" --all --out eval/clips_<name>`. The private key file `_key.json` maps clip IDs back to promos, and reviewers must not open it. Use about 17 clips per reviewer. Save all reviews as `{"reviews": [...]}`, then analyse with `python3 tools/analyze_review_all.py <reviews.json> eval/clips_<name>/_key.json <run>/promos.json <summary.json>`.

```
You are a skeptical performance-marketing editor who buys TikTok / Instagram Reels / YouTube Shorts ads for a vertical microdrama app. Your job is to stop weak promos from wasting ad money.

Read each of these clip files completely (read only; do not create, edit or run anything, and do not open any other file in that folder):
- C001: <path>
...

Each file is ONE promo clip cut from a microdrama, written out as a timed script (CAMERA = on screen, ACTION = what people do, SFX = sound, NAME (emotion) = dialogue), followed by what happens right after the cut, which viewers do NOT see.

Judge EACH clip on its own (clips may come from the same show; do not compare them or let one influence another). Try hard to find reasons it fails; if unsure, mark the criterion as failing:
1. hook_pass: within the first 3 seconds there is something that would make a scrolling stranger stop (a slap or strike, a reveal, a shocking or cruel line, a threat). A quiet establishing shot, a calm line or small talk fails.
2. cliffhanger_pass: the clip cuts at an unresolved moment, BEFORE the payoff, so a viewer needs to know what happens next. Ending on a resolution, a calm beat, or after the answer fails.
3. standalone_pass: a stranger who has never seen the show understands who is who and what is at stake from this clip alone.
Melodrama, clichés and big tropes (betrayal, humiliation, secret identities, revenge) are exactly what this genre sells: never fail a clip for being melodramatic or clichéd. Script labels such as "LADY IN GOLD GOWN" are not on-screen text, so do not complain that a character is unnamed.

Then: would_post = true only if all three pass AND you would spend ad money on it; score 1-10 (use the full range: 1-3 bad, 4-6 usable, 7-8 strong, 9-10 exceptional); biggest_problem = one short line ("none" if none).
Return exactly one review per clip, as JSON:
{"reviews": [{"clip_id": "C001", "hook_pass": true, "cliffhanger_pass": true, "standalone_pass": true, "would_post": true, "score": 7, "biggest_problem": "..."}]}
```

The hook re-cut A/B used the same prompt on `tools/export_recut_pairs.py` output: originals and re-cuts shuffled together.

---

## C. Code review (correctness)

Three reviewers, each given one group of files: parser and clips; heuristics and pipeline; client, reports and CLI. Rules: find real bugs with a concrete failure scenario, verify by running offline snippets where possible, never run the tool without `--no-llm` while another run uses the API, and edit nothing. Their findings and the fixes are summarised in the README, §3 step 6.
