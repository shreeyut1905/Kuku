# Walkthrough video: talking points (max 3 min, record in your own voice)

These are prompts for you, not a script to read out. Say it your way.

**0:00–0:20 · The problem.** An editor watches every episode of every new show to pick promo clips by hand. That's about 100 minutes of footage per 50-episode show, every week. My tool reads the screenplays and hands the editor a ranked shortlist with exact timestamps.

**0:20–0:50 · Run it live.**
```
python3 -m promo_finder parse "input_pack/<show>"     # format check: 50 episodes, 3,727 timed lines
python3 -m promo_finder run   "input_pack/<show>"     # cached, so it finishes instantly on camera
```
Mention the model: GLM-4.7-Flash on Qubrid, the cheapest model there at $0. `--model` swaps in any other model.

**0:50–1:40 · Show the output.** Open `outputs/full_run/report.html`:
- Rank #1: read out its hook line and its cliffhanger line, then open "Script of this clip".
- Point out that the timestamps come from the script, not the AI. The model only points at line IDs, and code computes times, enforces 20–60 s and never cuts mid-line.
- Show one trailer that is stitched across episodes.
- Show the flags (for example "judge: ending feels resolved") and `promos.csv` with the empty `editor_verdict` column, which is the human-in-the-loop step.

**1:40–2:30 · How I know it's good, and where it fails.**
- A blind judge sees only the clip, the way a stranger on TikTok would.
- Independent reviewers (a different model) picked the best clip per episode without seeing my output. Give the agreement number versus the chance level.
- A blind review of the top 15 against 5 low-ranked controls.
- One concrete failure: the model made a 62.6 s clip and its own "fix" returned the same clip, so code now trims it. Another: the judge marked clips down as "cliché", which is the genre's selling point, so I fixed the prompt.

**2:30–3:00 · Scale.** It's $0 on the free tier. A paid model costs cents per show and runs in minutes with `--workers`. The same command works for every new show, and the editor's ✓/✗ in the CSV becomes data for tuning the prompts.
