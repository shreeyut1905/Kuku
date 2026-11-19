# 🎬 Promo Finder: AI promo picking for vertical microdramas

**Kuku AI Generalist assignment · Problem 1: "Find the Promos"**

Promo Finder reads the timestamped screenplays of a microdrama and returns a **ranked shortlist of 20–60 second promo clips**. For each clip you get the episode, the exact start and end of every segment, the hook in the first 3 seconds, the cliffhanger it cuts on, why it works, and why it sits at that rank. It comes as a **command-line tool** and as a **web app styled like Kuku's own product**, where an editor can review every clip (✓ post / ✗ skip) and export the list for the edit.

```bash
python3 -m promo_finder serve                                   # web app → http://127.0.0.1:8765
python3 -m promo_finder run "input_pack/<show folder>"          # same thing from the terminal
python3 -m promo_finder run examples/demo_show --no-llm         # try it now on the included demo show, no API key
```

Plain Python 3.9+ standard library: **nothing to install**. Every run, check and evaluation step, with exact commands, is in **[RUNBOOK.md](RUNBOOK.md)**. The default model is the **cheapest model on Qubrid AI (GLM-4.7-Flash, $0)**, and any OpenAI-compatible model can be swapped in.

---

## Contents

1. [Results at a glance](#1-results-at-a-glance)
2. [Where it started: the brief](#2-where-it-started-the-brief)
3. [How it was built, step by step](#3-how-it-was-built-step-by-step)
4. [How it works](#4-how-it-works)
5. [How I know it's good (and where it isn't)](#5-how-i-know-its-good-and-where-it-isnt)
6. [Model comparison, cost and scale](#6-model-comparison-cost-and-scale)
7. [Using it: CLI, web app, swapping models](#7-using-it)
8. [AI tools used, and where they got it wrong](#8-ai-tools-used-and-where-they-got-it-wrong)
9. [Assumptions, limits, safety](#9-assumptions-limits-safety)
10. [Repository map](#10-repository-map)

---

## 1. Results at a glance

Official run on the full input pack (50 episodes, 102.7 minutes of drama, 3,727 timed lines) with the **free** model:

| | |
|---|---|
| Candidates found and judged | **155**: 122 AI picks + 3 cross-episode trailers + 30 keyword-rules picks; **149** ranked after removing near-duplicates |
| Cost | **$0** (GLM-4.7-Flash on Qubrid) · 209 AI calls · 620k input + 68k output tokens |
| Time | ≈1.8 h of model time, because Qubrid's free tier serves one request at a time. A paid model with 8 parallel requests did the same show in **2.8–4.4 min** for **$0.10–0.65** |
| Hard rules | **0** clips outside 20–60 s, **0** cut mid-line, **0** timestamps written by the AI (all computed by code from the script) |
| Moments found | The tool's candidates contained the independent reviewer's favourite moment in **48 of 50 episodes** (keyword baseline: 41/50) |
| Ranking quality (blind review) | Blind reviewers would post **8 of the top 10** and 70% of the top 30, but only 22% of the bottom 74 (tool score vs reviewer score: ρ = 0.53) |

The ranked list itself quotes Kuku's confidential scripts, so it is not in this public repo. It is in the raw-outputs folder shared privately with the submission.

---

## 2. Where it started: the brief

Kuku's brief offered three problems. You pick one, spend 2–3 hours, and submit within 48 hours. The deliverables are a 2-page doc, a runnable prototype, raw outputs on the real input, and an optional video. The brief's rules shaped every decision here:

- *"Make assumptions instead of asking."* Assumptions are listed in [§9](#9-assumptions-limits-safety).
- *"Free tiers are enough… build a smaller version for free."* That led to the cheapest model, which is $0.
- *"Build something you can run again… a solution where the model is easy to swap will shine."* That led to one command for any new show, and models chosen by flag or by setting.
- *"How you know it's good, backed by real runs and honest failures."* That led to [§5](#5-how-i-know-its-good-and-where-it-isnt).

**Why Problem 1.** An editor currently watches every episode of every new show to hand-pick promos, every week. That is the top of the acquisition funnel and a repeatable bottleneck. The scripts already contain everything needed (every shot, action and line has a timestamp), so it is a judgment task over text, which AI is good at, wrapped in exact rules, which code is good at. And the input pack meant I could test on a real show instead of guessing.

---

## 3. How it was built, step by step

This is a log of what actually happened, including the parts that went wrong.

### Step 1: Understand the input
The input pack was a zip of 50 episode screenplays. Before writing any AI code I parsed it and counted:

- Every episode has a header (`EPISODE NUMBER`, `EPISODE LOGLINE`, `CONTINUITY NOTES`), one or more scenes, and **timed blocks**: `**CAMERA:**`, `**ACTION:**`, `**SOUND FX:**` or `**CHARACTER (emotion):**`, each followed by `Start: (MM:SS.ss) | End: (MM:SS.ss)`.
- In total: **3,727 timed blocks** (1,754 camera shots, 1,487 lines of dialogue, 372 actions, 114 sound effects), **102.7 minutes**, with episodes from 70 s to 201 s long.
- One episode wrote its scene fields in capitals (`SCENE START:`), so the parser had to tolerate variations from day one.

Key insight: **the script is already a precise timeline.** If the AI only has to *point at* lines, code can produce exact, never-invented timestamps.

### Step 2: Choose the model
Qubrid AI was provided as the API, with the instruction to use **the cheapest model**. Its `/models` endpoint lists 70 models with prices; two are free. I chose `zai-org/GLM-4.7-Flash` ($0, 200K context, the newer of the two). The first live test surprised me:

- A tiny request took **68 seconds**, because the model "thinks" by default. Turning that off (`enable_thinking: false`) brought it to **2.5 seconds**.
- Sending 4 requests at once returned **HTTP 429 for 3 of them**. The free tier serves **one request at a time** per model, so the tool runs sequentially by default and retries politely.

### Step 3: Decide what the AI does and what code does
- **AI:** reads each episode and proposes promo moments **by block ID** (`B012 → B047`), with a hook, a cliffhanger and why it works. A separate **blind judge** later scores each clip.
- **Code:** turns IDs into exact times, enforces 20–60 s, never cuts mid-line, snaps cuts to camera changes, removes duplicates, ranks, and writes the reports.

### Step 4: First test on episodes 1–3, and three things that broke
1. **The model can't count time.** It found the show's strongest opening scene but made the clip 62.6 s long. Asked to fix it, it **returned the identical clip**. So code now trims or extends lengths itself, keeping the model's hook or cliffhanger and moving the other end to the strongest line that fits. Across the full show, **46% of the free model's picks** needed this.
2. **The judge disliked the genre.** It marked clips down as "melodramatic" and "cliché", which is exactly what microdrama sells. One sentence in the judge prompt fixed it.
3. **The judge read script labels as on-screen text.** It complained a character was "never named", but a viewer sees faces, not labels. Another one-line fix.

### Step 5: Full run, plus an independent yardstick
While the free model worked through 50 episodes, I built an **independent reference**. Nine Claude Haiku reviewers, who never saw the tool's output, each read about 6 episodes and picked the best promo in each one. All 50 of their timestamps were checked against the script and were valid. I also added a **keyword-rules baseline** (no AI) and a **chance baseline**. Episodes are short and clips are long, so even a random clip overlaps the best moment surprisingly often, and that has to be measured.

### Step 6: Hardening: reviews, audits and stress tests
- **Code review by three AI reviewers** found 17 issues. The serious ones: a malformed model reply (a bare list, `Infinity`, prose around the JSON) could crash a run *after* all the API calls, and a network timeout on Python 3.9 escaped the retry loop. All were fixed, and an **offline test with a deliberately broken fake model** proves the run survives.
- **A cut audit** re-checks every clip against the script. It caught a real bug: when two characters' lines overlap by a fraction of a second, a cut could land 0.2 s inside a line. Fixed by treating overlapping speech as one span. A **stress test of 46,253 possible cuts** now finds **0** mid-line cuts.
- **Parser stress test:** the same episode reformatted 8 ways (Windows line endings, no parentheses, hours, comma decimals, lowercase headers, `**SFX:**`, no scene blocks) parses to an identical timeline.
- I restarted the run on the fixed code. Every model reply is **cached on disk**, so nothing already paid for or waited for was lost.

### Step 7: A blind second opinion on every candidate
All candidates were exported anonymously, shuffled, and reviewed by skeptical "ad-buyer" reviewers (Claude Haiku). They decided whether each clip passes the brief's three tests (hook in 3 s, cliffhanger, stands alone) and whether they would spend ad money on it. This showed two things:
- **The ranking works.** Reviewers would post most of the top of the list and few from the bottom. Numbers are in [§5](#5-how-i-know-its-good-and-where-it-isnt).
- **The weak spot is the opening.** Only about half of all candidates hook a stranger in the first 3 seconds, while 96% make sense on their own.

### Step 8: Testing stronger models (paid, $0.86 of a $4 budget)
To test the "easy to swap" claim properly, I ran the whole show on two paid Qubrid models. A hard **budget cap** (`--max-cost`) was added first.
- **Speed:** Qwen3.5-Flash processed all 50 episodes in **2.8 min** ($0.10). DeepSeek-V4-Pro took **4.4 min** ($0.65). The free model takes hours.
- **Quality:** DeepSeek's picks broke the length rule far less often (26% vs 46%), and its ranking agreed best with the blind reviewers (ρ = 0.79).
- **A surprise: content refusals.** This show is cruel and violent by design. Both paid models **refused some episodes** ("Input text data may contain inappropriate content", "falls outside … content policy"); the free model refused none. A stricter model would silently lose episodes, so I added **refusal detection plus `--fallback-model`**. Refused calls are re-sent to a more tolerant model. In testing, that recovered every episode for under $0.01.
- **An experiment that didn't work:** an extra AI pass to "re-cut each clip to its strongest first line". A blind A/B on 22 clips showed **no real gain** (hooks 11 → 12 of 22, average score unchanged), so it stays off by default (`--recut-hooks`). Qwen also refused 148 of 149 of those short prompts, because short excerpts of cruel dialogue trip filters more often than full episodes do.

### Step 9: Make it usable: web app in Kuku's style
I built a web app on the standard library. It lets you pick or upload a show, choose models, run with live progress and a cost counter, then review the promos. The design follows **Kuku's visual language** (Figtree font, black canvas, Kuku red, orange ranked numbers, gold badges, green call-to-action buttons):
- Promos appear as **9:16 vertical posters**, with the opening line of the clip as the thumbnail text.
- A **"Top 10 to post first"** row uses big ranked numbers, like Kuku's "Top Shows".
- Clicking a poster opens a **phone-frame storyboard** of the clip, with the first 3 seconds highlighted and what happens after the cut shown faded. Next to it are the segments with episode timelines, the judge's scores, and **✓ Post / ✗ Skip** buttons with notes. The review is saved and exported to CSV: that is the human in the loop.

---

## 4. How it works

```
 scripts (.txt per episode)
   │
   ▼  parse (code): every shot / action / sound / line with start-end time and an ID (B001…)
   │
   ▼  series guide (AI, 1 call): premise and cast list from the episode loglines
   │
   ▼  find (AI, 1 call per episode): ≤3 promos + 4 short "moments", by block ID only
   │     └─ code: IDs → exact times · enforce 20–60 s and fix lengths · never cut mid-line ·
   │              snap to camera changes · repair call only for invalid IDs
   ▼  trailers (AI, 1–2 calls): stitch 2–4 moments across episodes, in story order (code checks)
   │  + keyword-rules picks (code): a baseline the judge also scores
   ▼  blind judge (AI, 1 call per clip): sees ONLY the clip and what happens after the cut,
   │     like a stranger on TikTok → hook / cliffhanger / stands-alone (1–10), verdict, concerns
   ▼  rank (code): 0.40·hook + 0.35·cliffhanger + 0.25·stands-alone + verdict bonus
   │     + small keyword signals + early-episode bonus − penalties; near-duplicates removed
   ▼  outputs: promos.md · report.html · promos.csv · promos.json · run_summary.json · raw/ (every AI call)
```

**Why these choices**
- **The model never types a timestamp.** It points at IDs and code computes the times, so a model cannot invent one, and every cut can be audited against the script.
- **The judge is blind on purpose.** Cold viewers don't know the show, so the judge isn't told about it either. That makes "does it stand alone?" a real test.
- **Early-episode bonus.** Anyone who taps a promo starts at Episode 1, so clips that sell the premise get a small push. It is a business assumption, written down, and tunable.
- **Caching.** Every model reply is stored, so re-running a show is free and gives identical results. Changing one prompt re-runs only the calls it affects.
- **Human in the loop.** The editor makes the final ✓/✗. Reviewing the top 10 takes about 10 minutes, against about 100 minutes of watching episodes.

---

## 5. How I know it's good (and where it isn't)

| Check | Result |
|---|---|
| **Hard rules**, audited against the script (`tools/audit_cuts.py`) | Every clip in every run: 20–60 s, inside its episode, in story order, never mid-line. 0 problems. |
| **Cut-snapping stress test** | 46,253 possible cuts across all 50 episodes: 0 land inside a line |
| **Parser robustness** (`tools/test_parser_variants.py`) | 8 of 8 reformatted variants parse to an identical timeline |
| **Broken-model test** (`tools/test_malformed_replies.py`) | A run survives bare lists, wrong types, invented IDs, `Infinity`, prose around the JSON, and duplicated trailer moments |
| **Recall vs independent reviewers** (`tools/compare_with_reviewers.py`) | Reviewer's best moment found among the tool's candidates: **48/50** (free model), 49/50 (DeepSeek), 47/50 (Qwen); keyword baseline 41/50 |
| **Blind review of every candidate** (`tools/analyze_review_all.py`) | Share the reviewers would post, by the tool's rank (free model): top 10 **8/10** · 11–30: 13/20 · 31–75: 20/45 · 76+: 16/74. Stronger paid models separate more sharply (top 10: 9/10 and 8/10; bottom: 8/70 and 4/67) |
| **Judge stability** (`tools/judge_stability.py`) | I re-judged the free run's top 15 at a higher temperature to stress the judge. Scores moved **±2.1** (hook, cliffhanger) and ±1.2 (stands alone) on average, and the post/maybe/reject verdict changed for **9 of 15**. The cheap judge is noisy, so close ranks are ties. |

**Where it picks badly** (from the reviews, not guesses)
1. **Openings.** Only about half of the candidates hook in the first 3 seconds. The model often starts a few seconds early, on the lead-in. The ranking pushes the good ones up, but the pool is weak, and the re-cut experiment didn't fix it.
2. **Cutting on the wrong side of the payoff.** Some clips end just *after* the answer (the ring is already found), and some end one beat *before* the twist lands.
3. **It reads text, not video.** It can't see whether a slap lands or an actor sells a line.
4. **The model's descriptions sometimes quote the wrong moment.** These are flagged automatically ("cliffhanger text quotes a line that is not at the cut").
5. **A noisy judge, so ranks near each other are ties.** Re-judging moved scores by about ±2 and flipped half the verdicts in the top 15, and clips ranked 30–75 often score close to the top. That's why a human picks the final 3–5. For production, average 2–3 judge votes or use a stronger `--judge-model` (DeepSeek's ranking matched the blind reviewers best, ρ = 0.79).
6. **Stricter models refuse content.** Handled with `--fallback-model`, but worth knowing before choosing a model.

---

## 6. Model comparison, cost and scale

Same pipeline, same show; only `--model` changed:

| | GLM-4.7-Flash (default) | Qwen3.5-Flash | DeepSeek-V4-Pro |
|---|---|---|---|
| Cost per 50-episode show | **$0** | $0.10 | $0.65 |
| Time | ≈1.8 h (1 request at a time) | **2.8 min** (8 parallel) | 4.4 min (8 parallel) |
| Episodes refused by content filters | 0 | 4 → recovered by fallback | 1 → recovered |
| Picks breaking the 20–60 s rule (fixed by code) | 46% | 43% | 26% |
| Top 10 the blind reviewers would post | 8/10 | 9/10 | 8/10 |
| Tool score vs blind reviewer score (ρ) | 0.53 | 0.70 | 0.79 |

**At Kuku's volume:** about 650k input and 70k output tokens per 50-episode show. That is $0 on the free model, about $0.10 on a flash-class model, and about $0.65 on a frontier open model, so a new show every week costs **single-digit dollars a year**. The real cost is editor time, and that drops from watching about 100 minutes to reviewing a 10-clip shortlist. For volume, use a paid model with `--workers 8` (minutes per show), a cheap model to find candidates plus a stronger `--judge-model`, a `--fallback-model` for refusals, and a `--max-cost` cap.

---

## 7. Using it

### Web app
```bash
python3 -m promo_finder serve            # http://127.0.0.1:8765  (add --open to launch the browser)
```
- **New run:** pick a show folder on this computer or upload a `.zip` / `.txt` scripts. You see the episode count, runtime and any format warnings straight away. Choose the provider, model, judge model, fallback, parallel requests and budget cap, then press **Find promos**. Progress is live per step, along with calls, cache hits and money spent, and you can cancel.
- **Promos:** the hero banner, the Top 10 row, trailers and every clip as a poster, with search and filters (type, review state, judge picks, no warnings). Click a poster for the storyboard, the timestamps (⧉ copy), the judge's scores and the review buttons. **⬇ Export** downloads the CSV with your ✓/✗ and notes.
- Links like `#run=<run>&promo=<id>` open one specific promo, which is handy to share with an editor.

### Command line
```bash
cp .env.example .env                                   # add QUBRID_API_KEY (or any provider's key)
python3 -m promo_finder parse "<show folder>"          # check a new show's format first
python3 -m promo_finder run   "<show folder>"          # → outputs/<show>/
python3 -m promo_finder models                         # provider's models, cheapest first
```

| Flag | What it does |
|---|---|
| `--model`, `--base-url` | any OpenAI-compatible model or provider (OpenAI, Groq, Gemini, OpenRouter, Ollama, LiteLLM…) |
| `--judge-model` | a different, e.g. stronger, model for the blind judge |
| `--fallback-model` | where to re-send calls a model refuses on content-policy grounds |
| `--workers N` | parallel requests (paid tiers): minutes instead of hours |
| `--max-cost 1.00` | hard budget: live AI calls stop at $1.00 |
| `--episodes 1-10`, `--per-episode 3`, `--trailers 4` | scope |
| `--no-llm` | keyword-rules baseline, offline and instant |
| `--recut-hooks` | experimental opening re-cut (no measurable gain, off by default) |

**Outputs** (`outputs/<run>/`): `promos.md` (ranked list), `report.html` (static report with the script of each clip), `promos.csv` (editor sheet), `promos.json`, `run_summary.json` (calls, tokens, cost, time, rule breaks), `raw/` (every prompt and unedited reply, the series guide, per-episode picks, the trailer library).

---

## 8. AI tools used, and where they got it wrong

**Who did what.** Problem choice, scope, the provider and model choice, the testing budget, the frontend and its Kuku styling were my decisions. **Claude Code (Claude Opus 5.5)** was my pair programmer: it wrote and tested the code and ran the experiments with me. **Claude Haiku 5.5** acted as independent reviewers (picks, blind reviews, code review). **GLM-4.7-Flash** on Qubrid is the model inside the tool.

**Where they got it wrong:**
1. GLM made a 62.6 s clip of the best scene and, asked to fix it, returned the identical clip. Now code fixes lengths.
2. The first judge prompt (drafted with Claude) penalised "melodrama" and "clichés", which is the genre's selling point. Fixed with one sentence.
3. The first cut-snapping rule (written with Claude) could start a clip 0.2 s inside a line when two speakers overlap. An audit script caught it.
4. AI code reviewers found that one malformed model reply could crash a run after all the API calls. Fixed and covered by an offline test.
5. The re-cut prompt (written with Claude) was refused 148/149 times by Qwen's content filter. Rewording didn't help; the fallback model did.

---

## 9. Assumptions, limits, safety

**Assumptions** (the brief was silent on these):
- Script timings equal the final edit's timings.
- The audience is cold US scrollers who start at Episode 1.
- Promos are 20–60 s (25–50 s is the sweet spot) and are never cut mid-line.
- Trailers are 2–4 moments in story order.
- Spoilers are acceptable when the title already reveals the premise.

**Limits:** see "Where it picks badly" above. The free tier is slow (one request at a time). The judge is a small model and is noisy, so treat the output as a **shortlist for an editor**.

**Safety and confidentiality**
- The input pack, outputs, evaluation files, uploads and `.env` are **git-ignored**. The scripts are Kuku's confidential content, and the outputs quote them.
- The web app binds to `127.0.0.1` by default.
- Uploaded zips are treated as untrusted: only text files are extracted, paths are flattened (no `../` escapes), and sizes are capped.
- The server's own API key is **never** sent to a provider URL typed into the UI. `PROMO_UI_REQUIRE_KEY=1` makes every user bring their own key when hosting.
- Model and script text is inserted into the page as plain text, never as HTML.

---

## 10. Repository map

```
promo_finder/
  screenplay.py   parse scripts → timed blocks with IDs (tolerant of format variations)
  clips.py        segments, cut snapping (never mid-line, snap to shots), clip rendering
  heuristics.py   keyword signals, length auto-fix, flags, rules baseline, description checks
  prompts.py      every prompt in one place (miner, judge, trailers, re-cut)
  pipeline.py     the run: guide → mine → trailers → judge → rank (+ progress events)
  llm.py          OpenAI-compatible client: cache, retries, refusals → fallback, budget cap, cancel
  runner.py       model setup shared by CLI and web app
  report.py       promos.md / promos.csv / promos.json / report.html
  server.py       web app backend (standard library http.server)
  web/index.html  web app frontend (single file, no build step)
  __main__.py     CLI: run · serve · parse · models
tools/            audits, robustness tests, evaluation scripts (all offline except judge_stability);
                  review_prompts.md = exact instructions given to the independent AI reviewers
RUNBOOK.md        how to run every step (main run, web app, checks, evaluations, PDF), and what's pending
examples/demo_show/  a short ORIGINAL 3-episode demo show in the same format (safe to share)
docs/             submission checklist, walkthrough-video talking points
```
