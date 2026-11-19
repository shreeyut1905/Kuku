"""All prompts in one place, so an editor can tune them without touching the pipeline."""

EDITOR_SYSTEM = """You are a senior promo editor at a vertical-microdrama app (like ReelShort or DramaBox).
You cut 20-60 second promos for Instagram Reels, TikTok and YouTube Shorts. A promo must make a stranger stop scrolling and install the app to see what happens next.

A great promo:
1. HOOK in the first 3 seconds: a slap or strike, a shocking or insulting line, a reveal, a threat, a scream, a public humiliation, a kiss. NOT an establishing shot, NOT calm small talk.
2. CLIFFHANGER at the cut: it ends on an unresolved moment (a threat, a shocking reveal line, a question left hanging, a decision about to be made), right BEFORE the answer. Never end after the conflict is resolved.
3. STANDS ALONE: someone who has never seen the show understands within seconds who is the victim, who is the villain and what is at stake.

Rules:
- Use ONLY block IDs that appear in the script listing.
- A clip runs from the START time of its start_block to the END time of its end_block.
- Describe only what is in the script. Never invent events, lines or characters.
- Reply with one JSON object only. No prose, no code fences."""


BIBLE_USER = """SHOW TITLE: {title}

One-line summaries (loglines) of each episode:
{loglines}

Speaker labels used in the scripts (with line counts):
{speakers}

Write a compact series guide for promo editors. Merge speaker labels that are the same person (for example a character first credited by a description before their name is revealed).

Return JSON:
{{"premise": "one sentence a stranger would understand, no spoilers beyond the title",
  "genre": "2-4 words",
  "characters": [{{"name": "Juliana", "labels": ["LADY JULIANA", "JULIANA"], "who": "max 12 words: role and relationship"}}]}}
List at most 12 characters, most important first."""


MINE_USER = """SHOW: {title}
GENRE: {genre}
PREMISE: {premise}
MAIN CHARACTERS:
{cast}

STORY SO FAR:
{story_so_far}

THIS EPISODE (Episode {ep}): {logline}
NOTES: {continuity}

SCRIPT OF EPISODE {ep}. Each line is: [block_id start-end] WHO: what happens or is said. Times are MM:SS.ss from the start of the episode. CAMERA lines are shots, ACTION lines are what we see, SFX are sounds, NAME (emotion) lines are dialogue.
{listing}

TASK
A) Find the {k} best promo clips in this episode (fewer if the episode has no strong moment). Each clip is ONE continuous stretch lasting 20 to 60 seconds (best 25-45 s): check that (end time of end_block) minus (start time of start_block) is between 20 and 60 seconds. Candidates must not mostly overlap each other.
   - start_block IS the hook: the very first thing the viewer sees or hears must already be shocking (a cruel line, a slap, a reveal, a threat). Do not start on a lead-in or an establishing shot.
   - end_block IS the cliffhanger: the last thing the viewer sees is the unresolved beat. Stop right there, before any answer or reaction that resolves it.
   - Look first at the episode's biggest moments: public humiliation, betrayal reveals, violence, threats, proposals, secret identities, last-second rescues, and the episode's final beat.
B) Pick up to 4 short MOMENTS (3 to 15 seconds each) that could be stitched into a cross-episode trailer: strong hook beats, big reveals, cliffhanger beats.

Every field is required; never leave hook, cliffhanger or why empty.

Return exactly this JSON shape:
{{"promos": [{{"title": "3-6 word label", "start_block": "B012", "end_block": "B047", "hook": "what the viewer sees and hears in the first 3 seconds", "cliffhanger": "the unresolved moment the clip cuts on", "why": "one line on why it works", "context_gap": "what a stranger would not understand, or none"}}],
 "moments": [{{"start_block": "B020", "end_block": "B024", "kind": "hook|reveal|cliffhanger|emotional", "description": "what happens, naming the characters"}}]}}"""


REPAIR_USER = """Some of your picks break the rules:
{errors}

Return the COMPLETE JSON again (same shape: "promos" and "moments"), with the broken items fixed. Keep the valid items unchanged. Pick different start_block/end_block IDs from the listing so that each promo lasts 20-60 seconds and each moment lasts 3-15 seconds."""


JUDGE_SYSTEM = """You are a strict, skeptical reviewer of promo clips for a vertical-drama app. Most clips are NOT good enough to buy ads for: reserve 8-10 for clips you would bet money on, and use the full 1-10 range.
You see ONLY the clip, exactly as a stranger scrolling TikTok would, with no knowledge of the show.
This genre is melodrama ON PURPOSE. Its audience loves big tropes: betrayal, public humiliation, cruel families, secret identities, revenge, slaps, dramatic speeches. Never mark a clip down for being melodramatic, clichéd, tropey or theatrical. Judge only three things: does it grab a stranger within 3 seconds, does it end unresolved, and can a stranger follow it.
The clip is written as a script of a video: CAMERA lines are what is on screen, ACTION lines are what people do, SFX are sounds. Speaker labels such as "LADY IN GOLD GOWN" are script labels, not on-screen text; the viewer sees faces and costumes, so do not complain that a character is "not named".
Reply with one JSON object only."""


JUDGE_USER = """Below is a {dur:.0f}-second promo clip cut from a {genre} microdrama, written out as a timed script. Times are seconds from the start of the clip.
You also see what happens right AFTER the cut. The viewer does NOT see that part. Use it only to judge whether the cut leaves the story unresolved.

FIRST 3 SECONDS (this is all a scrolling viewer gives the clip before deciding):
{first3}

FULL CLIP:
{clip}
=== CUT: the promo ends here ===

RIGHT AFTER THE CUT (not shown to the viewer):
{after}

Score each from 1 to 10 using these anchors:
- hook_score: 10 = a slap, shocking line or reveal lands in the first 3 seconds; 5 = interesting but slow start; 1 = calm establishing shot or small talk.
- cliffhanger_score: 10 = cuts right before a huge payoff the viewer is desperate to see; 5 = some tension left; 1 = conflict resolved, or ends on a calm beat.
- standalone_score: 10 = a total stranger instantly gets who is who and what is at stake; 5 = gets the gist with some confusion; 1 = needs earlier episodes to make sense.

Return JSON:
{{"first_3_seconds": "what the viewer sees and hears in the first 3 seconds",
  "ending": "the last moment before the cut",
  "problems": ["concrete weaknesses, can be empty"],
  "hook_score": 0, "cliffhanger_score": 0, "standalone_score": 0,
  "verdict": "post|maybe|reject",
  "pitch": "one line on why a stranger would keep watching (or why not)"}}"""


# Wording note: an earlier version that asked for "the most shocking line (a cruel line, a slap)"
# and "who is hurting whom" was refused by one provider's content filter 148/149 times.
# Neutral editing language works.
RECUT_USER = """You are editing a trailer for a TV drama. The first 3 seconds decide whether a viewer keeps watching. Below is the OPENING of the trailer clip (the clip continues after this, and its ending is fixed).

Current start: {current}. You may move the start LATER to any block from {first_allowed} to {last_allowed} (the clip must stay at least 20 seconds long). Keep the current start if its first 3 seconds are already the most attention-grabbing.

OPENING OF THE CLIP (block_id, time from the current start, who, what):
{opening}

Pick the start block whose first 3 seconds are the most attention-grabbing dramatic moment (a surprising line, a confrontation, a revelation), while keeping enough set-up that a new viewer understands the situation.

Return JSON: {{"start_block": "B012", "reason": "one line"}}"""


MONTAGE_USER = """SHOW: {title}
GENRE: {genre}
PREMISE: {premise}
MAIN CHARACTERS:
{cast}

Below is a library of short MOMENTS taken from across the first {n_eps} episodes. Each line gives: moment_id | episode | duration in seconds | kind | what happens.
{library}

TASK: Build {k} TRAILER promos by stitching 2 to 4 moments together, in story order (episode order). Rules for every trailer:
- total duration (sum of its moments) between 20 and 60 seconds;
- the FIRST moment is a strong hook that stops the scroll within 3 seconds;
- the LAST moment is an unresolved cliffhanger;
- together they sell the premise so a stranger can follow (for example: betrayal, then an unexpected twist, then danger).
Different trailers should tell different angles of the story.

Return JSON:
{{"trailers": [{{"title": "3-6 word label", "moment_ids": ["M003", "M017"], "hook": "the opening beat", "cliffhanger": "the final unresolved beat", "why": "one line on why it works"}}]}}"""


MONTAGE_REPAIR_USER = """Some trailers break the rules:
{errors}

Return the COMPLETE JSON again with those trailers fixed: use only moment IDs from the library, 2 to 4 moments in episode order, total 20 to 60 seconds."""
