# RUNBOOK: every run, how to do it, and what is still pending

All commands run from the repo root on Python 3.9+, with no installs. `<SHOW>` means the show folder, e.g. `input_pack/<show name>` (the confidential pack, not in git) or `examples/demo_show` (the demo included in the repo).

---

## 0. Status

| Item | Status |
|---|---|
| Tool (CLI), web app, tests, README | ✅ done, in this repo |
| Official free-model run on the full pack (`outputs/full_run`, 155 candidates → 149 ranked, $0) | ✅ done. Its original live log is kept as `raw/llm_calls_live_run.jsonl`; the reports were regenerated from cache with the final code (identical clips and ranking) |
| Paid-model comparison runs (Qwen3.5-Flash, DeepSeek-V4-Pro) and their blind reviews | ✅ done ($0.86 of a $4 test budget) |
| Evaluation: independent picks, blind review of every candidate, judge stability | ✅ done (`eval/`, kept private) |
| README numbers | ✅ filled |
| 2-page solution PDF | ✅ built locally as `docs/solution.pdf` (git-ignored because it quotes the scripts). **Edit it into your own words** (step 9 to rebuild) |
| Submission (Drive upload of `outputs/full_run` + `eval/`, form, optional video) | ⏳ you: see `docs/SUBMISSION_CHECKLIST.md` |

Re-running anything below is optional. Every step is reproducible with the commands given.

---

## 1. Setup (once)

```bash
cp .env.example .env          # then put your key in it:  QUBRID_API_KEY=...
python3 -m promo_finder models    # check the key works; lists models, cheapest first
```
To use another provider, set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env` (examples in `.env.example`), or pass `--base-url` and `--model`.

---

## 2. The official run (raw outputs for the submission)

```bash
python3 -m promo_finder parse "<SHOW>"                        # format check: episodes, lines, warnings
python3 -u -m promo_finder run "<SHOW>" --out outputs/full_run
```
- Default model: `zai-org/GLM-4.7-Flash` on Qubrid ($0). The free tier allows **one request at a time**, so a 50-episode show takes **about 1.5–2.5 h**. Don't run two free-model jobs at once: they block each other (HTTP 429).
- **It can be resumed.** Every model reply is cached in `.cache/llm/`. If it stops, run the same command again and finished calls come back instantly.
- To **regenerate the reports** later (after a code update) **without losing the original run log**:
  ```bash
  cp outputs/full_run/raw/llm_calls.jsonl  outputs/full_run/raw/llm_calls_live_run.jsonl
  cp outputs/full_run/run_summary.json     outputs/full_run/run_summary_live_run.json
  python3 -m promo_finder run "<SHOW>" --out outputs/full_run      # all answers from cache: seconds, $0
  ```
- Results: `outputs/full_run/promos.md`, `report.html`, `promos.csv`, `promos.json`, `run_summary.json`, `raw/`.
- Quick look in the terminal: `python3 tools/show_results.py outputs/full_run 15`

---

## 3. Web app

```bash
python3 -m promo_finder serve --open          # http://127.0.0.1:8765
```
- **Promos** tab: pick a run from the top-right menu. **New run** tab: pick or upload a show, choose models, press *Find promos*. **How it works**: the explainer.
- Runs started in the web app land in `outputs/<show>-<model>-<time>/`. Runs started from the terminal show up in the web app too.
- Hosting it for others: `PROMO_UI_REQUIRE_KEY=1 python3 -m promo_finder serve --host 0.0.0.0 --port 8765`. Every visitor then has to bring their own API key. Never host it with the confidential scripts or outputs on the machine.

---

## 4. Demo show (safe to share, no confidential data)

```bash
python3 -m promo_finder run examples/demo_show --no-llm --out outputs/demo_rules     # instant, offline
python3 -m promo_finder run examples/demo_show --out outputs/demo                    # with AI (free model, ~10 min)
```

---

## 5. Offline quality checks (no API calls)

```bash
python3 tools/audit_cuts.py outputs/full_run/promos.json "<SHOW>"                     # 20–60 s, inside the episode, story order, never mid-line
python3 tools/test_parser_variants.py "<SHOW>/<one episode>.txt"                      # 8 reformatted variants must parse identically
python3 tools/test_malformed_replies.py "<SHOW>"                                      # fake model with broken replies: run must survive
python3 -m promo_finder run "<SHOW>" --no-llm --out outputs/_rules_baseline            # keyword-rules baseline (for comparison)
```

---

## 6. Evaluation against independent reviewers

The reviewer instructions are in `tools/review_prompts.md`. Reviewers' outputs live in `eval/`, which is git-ignored because it quotes the scripts.

**a) Recall: is the reviewer's best clip among the tool's candidates?**
```bash
python3 tools/check_reviewer_picks.py "<SHOW>" eval/independent_picks_haiku.json      # validates the reviewer picks' timestamps
python3 tools/compare_with_reviewers.py outputs/full_run/promos.json eval/independent_picks_haiku.json \
        --show "<SHOW>" --out eval/agreement_tool.json
```

**b) Blind review of every candidate: does the ranking put the good clips on top?**
```bash
# the clips to review (already exported for the free run to eval/clips_all; to redo it from the cache:)
python3 -m promo_finder run "<SHOW>" --no-judge --out /tmp/candidates
python3 tools/export_clips_for_review.py /tmp/candidates/promos.json "<SHOW>" --all --out eval/clips_all
# reviewers (prompt B in tools/review_prompts.md) -> eval/blind_review_all_raw.json, then:
python3 tools/analyze_review_all.py eval/blind_review_all_raw.json eval/clips_all/_key.json \
        outputs/full_run/promos.json eval/blind_review_all_summary.json
```

**c) Judge consistency.** Re-judges the top 15 at a higher temperature. That is 15 free-model calls, about 10 min. Run it only when no other free-model run is active:
```bash
python3 tools/judge_stability.py outputs/full_run/promos.json "<SHOW>" --top 15
```

---

## 7. Model comparison runs (optional, paid: about $0.10–0.65 each)

```bash
python3 -m promo_finder run "<SHOW>" --model Qwen/Qwen3.5-Flash         --workers 8 --max-cost 0.3 \
        --fallback-model zai-org/GLM-4.7-FlashX --out outputs/test_qwen3.5-flash
python3 -m promo_finder run "<SHOW>" --model deepseek-ai/DeepSeek-V4-Pro --workers 8 --max-cost 1.5 \
        --fallback-model zai-org/GLM-4.7-FlashX --out outputs/test_deepseek-v4-pro
# evaluate each one exactly as in step 6 (export with --all, review, analyze; compare_with_reviewers)
```
Optional hook re-cut experiment: add `--recut-hooks` and write to a new `--out`, then run `tools/export_recut_pairs.py <run without>/promos.json <run with>/promos.json "<SHOW>"` and review the pairs blind.

---

## 8. Numbers for the README and the solution doc

```bash
python3 tools/doc_numbers.py outputs/full_run          # every number quoted in the docs, computed from the files
```
Fill the README's ⟦FREE_…⟧ markers (sections 1, 5 and 6) from that output, `eval/agreement_tool.json`, `eval/blind_review_all_summary.json` and `eval/judge_stability.json`.

---

## 9. The 2-page solution PDF

The doc quotes the scripts, so its source (`docs/solution_template.html` + `docs/solution_body_draft.html`) is **git-ignored** and stays on this computer. After filling in its `[[…]]` placeholders:
```bash
python3 -c "t=open('docs/solution_template.html').read(); b=open('docs/solution_body_draft.html').read(); open('docs/solution.html','w').write(t.replace('{{BODY}}', b))"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --no-pdf-header-footer \
        --print-to-pdf=docs/solution.pdf "file://$PWD/docs/solution.html"
python3 -c "from pypdf import PdfReader; print(len(PdfReader('docs/solution.pdf').pages), 'pages')"   # must be ≤ 2
```

---

## 10. Pushing updates

```bash
git add -A && git status --short          # input_pack/, outputs/, eval/, uploads/, .env, docs/solution* must NOT appear
git commit -m "…" && git push
```
