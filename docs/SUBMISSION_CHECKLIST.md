# Submission checklist (Kuku form)

| Form item | What to submit | Where it is |
|---|---|---|
| Problem chosen | Problem 1: Find the Promos | |
| **Solution doc** (PDF, ≤ 2 pages) | `docs/solution.pdf`: **edit it into your own words first**, fill in your name, roll no. and links, then re-export | `docs/solution.html` → PDF |
| **Prototype link** | GitHub repo with the code and README (run steps). **Public is OK only because** `.gitignore` keeps out `input_pack/`, `outputs/`, `eval/`, `.env`, `.cache/` and the solution doc | this folder |
| **Raw outputs** | Upload `outputs/full_run/` **as-is** (unedited) to a Google Drive folder → "Anyone with the link can view" | `outputs/full_run/` |
| Extra evidence (optional, same Drive folder) | `eval/`: independent reviewer picks, blind review, judge stability, agreement numbers | `eval/` |
| Walkthrough video (optional, recommended) | ≤ 3 min, your own voice: see `docs/walkthrough_talking_points.md` | Loom / unlisted YouTube |

## Before you push to GitHub

1. Check that nothing confidential is staged:
   ```bash
   git init && git add . && git status    # input_pack/, outputs/, .env, .cache/ must NOT be listed
   ```
2. **Rotate your Qubrid key after the assignment.** It was pasted into a chat. It lives only in `.env`, which is git-ignored.
3. Make sure every link opens for "anyone with the link" (the brief requires this).

## Confidentiality (from the brief)

The screenplays are Kuku's content. `outputs/` and `eval/` quote them, so share them only through the private Drive link in the form, never in a public repo.

## Interview prep: they will run it on a NEW show

```bash
python3 -m promo_finder parse "<new show folder>"          # 1. format check (warnings per episode)
python3 -m promo_finder run   "<new show folder>"          # 2. full run (free tier: ~1-2 h; use --episodes 1-10 for a quick demo)
python3 -m promo_finder run   "<new show>" --model <their model> --base-url <their API> --workers 8   # 3. with their API
```
If their scripts use a different layout, `parse` shows it immediately. `tools/test_parser_variants.py` lists the variations already handled.
