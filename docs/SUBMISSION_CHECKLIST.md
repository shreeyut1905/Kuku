# Submission checklist (Kuku form)

| Form item | What to submit | Where it is |
|---|---|---|
| Problem chosen | Problem 1: Find the Promos | |
| **Solution doc** (PDF, ≤ 2 pages) | `docs/solution.pdf`: **edit it into your own words first**, fill in your name, roll no. and links, then re-export | `docs/solution.html` → PDF |
| **Prototype link** | GitHub repo with the code and README (run steps). `.gitignore` keeps out `input_pack/`, `eval/`, `.env`, the solution doc and every run except `outputs/full_run/`, which is committed with its model-reply cache `.cache/llm/` | this folder |
| **Raw outputs** | `outputs/full_run/` **as-is** (unedited), committed in the repo: https://github.com/shreeyut1905/Kuku/tree/main/outputs/full_run | `outputs/full_run/` |
| Extra evidence (optional, a Google Drive folder → "Anyone with the link can view") | `eval/`: independent reviewer picks, blind review, judge stability, agreement numbers | `eval/` |
| Walkthrough video (optional, recommended) | ≤ 3 min, your own voice: see `docs/walkthrough_talking_points.md` | Loom / unlisted YouTube |

## Before you push to GitHub

1. Check that nothing confidential is staged:
   ```bash
   git init && git add . && git status    # input_pack/, eval/, .env must NOT be listed; from outputs/ and .cache/ only full_run/ and llm/ may be
   ```
2. **Rotate your Qubrid key after the assignment.** It was pasted into a chat. It lives only in `.env`, which is git-ignored.
3. Make sure every link opens for "anyone with the link" (the brief requires this).

## Confidentiality (from the brief)

The screenplays are Kuku's content. `eval/` quotes them and stays out of the repo (Drive link only). `outputs/full_run/` and `.cache/llm/` quote them too and are committed to this public repo (decided on 2026-11-20).

## Interview prep: they will run it on a NEW show

```bash
python3 -m promo_finder parse "<new show folder>"          # 1. format check (warnings per episode)
python3 -m promo_finder run   "<new show folder>"          # 2. full run (free tier: ~1-2 h; use --episodes 1-10 for a quick demo)
python3 -m promo_finder run   "<new show>" --model <their model> --base-url <their API> --workers 8   # 3. with their API
```
If their scripts use a different layout, `parse` shows it immediately. `tools/test_parser_variants.py` lists the variations already handled.
