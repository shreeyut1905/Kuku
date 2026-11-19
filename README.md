# Promo Finder: find and rank 20–60 s promos in microdrama screenplays

Kuku AI Generalist assignment, **Problem 1 (Find the Promos)**.

Give it a folder of timestamped episode screenplays. It returns a **ranked list of promo clips**. For each clip you get the episode, the exact start and end time of every segment, the hook in the first 3 seconds, the cliffhanger it cuts on, one line on why it works, why it sits at that rank, and flags for where it may be weak.

```bash
cp .env.example .env            # add QUBRID_API_KEY (or any OpenAI-compatible provider)
python3 -m promo_finder run "input_pack/<Show name>"      # → outputs/<show-name>/
```

Requires Python 3.9+ and **nothing else**: the standard library only, no `pip install`.

## What you get (in `outputs/<show>/`)

| File | For whom | What |
|---|---|---|
| `promos.md` | editor / reviewer | Ranked list: top 10 in full, the rest in a table, then automatic "where it may have picked badly" flags |
| `report.html` | editor | Same list as cards; each card opens the **script of the clip** and what happens right after the cut |
| `promos.csv` | editor | Review sheet with empty `editor_verdict` / `editor_notes` columns for the human pass |
| `promos.json` | engineers | Everything, machine-readable |
| `run_summary.json` | ops | Calls, tokens, cost, time, how many picks broke rules and were fixed |
| `raw/llm_calls.jsonl` | audit | Every prompt and every unedited model reply |
| `raw/mining_by_episode.json`, `raw/trailers.json`, `raw/series_guide.json`, `raw/moment_library.json` | audit | Raw model outputs per step, before any validation |

## How it works

```
screenplays ──► parse (code) ──► series guide (AI, 1 call)
                                      │
             ┌────────────────────────┘
             ▼
  per episode: AI picks up to 3 promos + 4 short "moments" by BLOCK ID   (1 call / episode)
             │   code turns IDs into exact timestamps, enforces 20–60 s, fixes lengths,
             │   never cuts mid-line, snaps cuts to shot changes  (repair call only if an ID is invalid)
             ▼
  cross-episode trailers: AI stitches 2–4 moments (1–2 calls), code checks order + length
  + keyword-rule picks (code) as a baseline the judge also scores
             ▼
  blind judge (AI, 1 call / promo): sees ONLY the clip + what happens after the cut,
  no show context, the way a stranger on TikTok would → hook / cliffhanger / stands-alone
  scores, verdict, concerns
             ▼
  rank (code): weighted judge scores + small rule signals + "early episode" bonus,
  near-duplicates removed, repeated episodes lightly penalised → reports
```

**What the AI does vs. what code does**

- **AI:** reading the story, choosing moments, writing the hook / cliffhanger / why lines, judging clips.
- **Code:** every timestamp, every length check, cut snapping, de-duplication, the ranking maths, cost tracking, caching.
- The model **never types a timestamp**. It points at block IDs (`B012`) from the parsed script, so a model cannot invent a time.
- Small models are bad at timestamp arithmetic. When the model picks a 62.6 s clip, code trims it to the strongest hook or cliffhanger that fits, instead of throwing the pick away.

**Human in the loop.** The editor opens `report.html`, reads the script excerpt of the top 10 (about 10 minutes, instead of watching about 100 minutes of episodes) and marks `editor_verdict` in `promos.csv`. Those verdicts are the data for tuning the prompts and weights in `promo_finder/prompts.py` and `pipeline.py`.

## Swapping the model or provider (no code changes)

The default is **`zai-org/GLM-4.7-Flash` on Qubrid AI, the cheapest model there ($0 per 1M tokens)**. List what's available, cheapest first:

```bash
python3 -m promo_finder models
```

Any OpenAI-compatible endpoint works through flags or `.env`. See `.env.example` for OpenAI, Groq, Gemini, OpenRouter, Ollama, or a LiteLLM proxy in front of Claude and other vendors:

```bash
python3 -m promo_finder run "<show>" --model Qwen/Qwen3.5-Flash                              # another Qubrid model
LLM_API_KEY=... python3 -m promo_finder run "<show>" --base-url https://api.openai.com/v1 --model gpt-4.1-mini
python3 -m promo_finder run "<show>" --judge-model <stronger-model>   # cheap model finds candidates, stronger model judges
python3 -m promo_finder run "<show>" --workers 8      # paid models accept parallel requests: a whole show in minutes
python3 -m promo_finder run "<show>" --max-cost 1.00  # hard budget: live AI calls stop at $1.00
python3 -m promo_finder run "<show>" --model <strict-model> --fallback-model zai-org/GLM-4.7-Flash   # see below
python3 -m promo_finder run "<show>" --no-llm         # rules-only baseline, offline, instant
```

**Content refusals.** Microdramas are full of cruelty and violence. In testing, two stronger Qubrid models declined some episodes. DeepSeek-V4-Pro hit a provider content filter (HTTP 400 "inappropriate content"). Qwen3.5-Flash hit the same filter and also replied "falls outside … content policy". The free GLM-4.7-Flash refused nothing. The tool recognises refusals and does not retry them pointlessly. With `--fallback-model`, it re-sends just those calls to a more tolerant model, so no episode is silently lost.

Other useful flags: `--episodes 1-10` (subset), `--per-episode 3`, `--trailers 4`, `--no-judge`, `--rules-pool 1`, `--thinking`.

All model replies are cached in `.cache/llm/`. Re-running a show costs nothing and gives identical results. Changing a prompt re-runs only the affected calls.

## Checking a new show's format first

```bash
python3 -m promo_finder parse "<new show folder>"                     # blocks, durations, warnings per episode
python3 tools/test_parser_variants.py "<one episode.txt>"             # parser survives reformatting (CRLF, no parens, hours, comma decimals, lowercase headers...)
```

## Evaluation helpers (`tools/`)

- `show_results.py <run>`: compact view of a run's ranking.
- `audit_cuts.py <run>/promos.json <show>`: re-checks every clip against the script (20–60 s, inside the episode, story order, never mid-line).
- `test_parser_variants.py`, `test_malformed_replies.py`: robustness tests (re-formatted scripts; a fake model that returns broken replies). No API calls.
- `check_reviewer_picks.py`, `compare_with_reviewers.py`: recall against independent reviewers' per-episode picks, with a chance baseline.
- `export_clips_for_review.py`, `analyze_review_all.py`: anonymised blind review of every candidate; agreement with the tool's ranking and judge.
- `judge_stability.py`: re-judges the top N to measure judge noise.
- `doc_numbers.py`: every number quoted in the solution doc, computed from the run and eval files.

## Model comparison on the full input pack (same pipeline, only `--model` changed)

| | GLM-4.7-Flash (default) | Qwen3.5-Flash | DeepSeek-V4-Pro |
|---|---|---|---|
| Cost per 50-episode show | **$0** | $0.10 | $0.65 |
| Wall-clock time | ≈2.5 h (free tier: 1 request at a time) | **2.8 min** (`--workers 8`) | 4.4 min (`--workers 8`) |
| Episodes refused by content filters | 0 | 4 (recovered with `--fallback-model`) | 1 (recovered) |
| Picks that broke the 20–60 s rule (fixed by code) | 46% | 43% | 26% |

The blind-review quality numbers for each model are in the solution doc and in `eval/`.

## Limits (honest)

- It reads **scripts, not video**. Visual hooks such as a slap's impact, an actor's face or music are judged from the script's descriptions. Timestamps assume the final edit follows the script's timings.
- **Weak openings are the main failure.** In blind reviews only about half of all candidates hook a stranger in the first 3 seconds. The ranking pushes the good ones to the top, but the pool is weak. An AI "re-cut the opening" pass (`--recut-hooks`, experimental, off by default) made no measurable difference in a blind A/B on 22 clips.
- The free model is small. Its picks need the code safety net, and its judge scores are noisy. Treat the ranking as a **shortlist for an editor**, not a final answer.
- The Qubrid free tier serves **one request at a time per model**, so a show takes hours. A paid model with `--workers 8` takes minutes, for about $0.10–0.65.
- Stricter models refuse some episodes of a cruel or violent drama. Use `--fallback-model`.

## Confidentiality

`input_pack/`, `outputs/` and `.env` are git-ignored. The screenplays are Kuku's confidential content. Outputs quote them, so share outputs privately (for example a Drive folder), never in a public repo.
