---
name: lex-transcript-search
description: Find where a word, topic, quote, or moment comes up in a Lex Fridman Podcast episode, using the official transcripts on lexfridman.com, and return timestamped YouTube deep links with verbatim quotes. Use whenever the user mentions a Lex Fridman podcast/episode and asks "where did they talk about X", "when does <guest> mention X", "find the part about X", "what did <guest> say about X", wants timestamps for a topic, wants to find a quote or moment, or says they can't listen to or watch the whole episode.
---

# Lex Transcript Search

Lex Fridman publishes a human-written, timestamped transcript for every recent
episode at `https://lexfridman.com/<guest-slug>-transcript/`. Each paragraph
carries its speaker, a `(hh:mm:ss)` start time, and a link into the full
YouTube episode. `search.py` fetches that page, parses it into segments, and
greps them, so every timestamp you report comes straight from the page.

For non-Lex videos, or for a full "what's interesting in this" summary rather
than finding a specific topic, use the `youtube-digest` skill instead.

## Workflow

### 1. Identify the episode

- WebSearch `Lex Fridman <guest>` (or `Lex Fridman podcast latest episode`).
  You want the `lexfridman.com/<slug>-transcript/` URL. The episode page
  `lexfridman.com/<slug>/` also works, because the script follows its transcript link.
- Slugs are the guest's name, and repeat guests get `-2`, `-3`, and so on
  (`dhh-2-transcript`, `sam-altman-2-transcript`). If the user says "the latest
  DHH episode", make sure you have the highest-numbered one.
- Check that you have the right episode: the script prints the page title with
  the episode number (e.g. `Lex Fridman Podcast #501`). If that doesn't match
  the user's description (guest, rough date, topic), say so before answering.

### 2. Search: the literal word plus sensible variants

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/lex-transcript-search/search.py" <URL> <term> [<term> ...] [-c N] [--json]
```

- Terms are **case-insensitive regexes**, and all of them are searched at once.
  A plain stem matches every form: `inferenc` finds inference and inferencing.
  Use `\bword\b` when a short word would match inside other words (e.g. `\bAI\b`, `\bcompute\b`).
- For a **concept** (not an exact word), add synonyms, related names, and
  spelling variants in the same call. For "inference" that could be
  `inferenc 'open.weight' Fireworks 'token'`. For "security" it could be
  `secur vulnerab exploit hack`. Use `.` or `[- ]?` for hyphen/space variants
  (`open.source`), since the transcripts are typed by people and aren't consistent.
- `-c 1` or `-c 2` prints neighbouring segments, so you can tell what the
  conversation was about. `--json` gives structured output.
- Clips in the "Episode highlight" cold open at 0:00 are skipped by default,
  because they replay material from later in the episode. The script says how
  many it skipped. Use `--include-highlights` to show them.
- If there are no matches, the script says so and suggests variants. Try a
  shorter stem or synonyms before telling the user the topic never comes up.
- Pages are cached in `~/.cache/lex-transcript-search/`. Use `--refresh` if a
  transcript was published or corrected recently.

### 3. Read around the hits

Run again with `-c 2` on the interesting hits (or read the `context` field in
`--json`) so that you understand the actual discussion. A keyword hit in
passing is not the same as a real discussion. Tell those apart for the user.

### 4. Answer

For each relevant moment, give:

- **Timestamp range and deep link**, copied exactly from the script output:
  `02:40:06–02:40:43` → `https://www.youtube.com/watch?v=NYFGCESmikA&t=9606s`
- **Speaker** and a **short verbatim quote** (one or two sentences) containing the term.
- **A plain-English explanation** of what was being discussed and why it matters.
  Add a sentence of background if the user is unlikely to know the topic.

Rules:

- **Never invent or estimate timestamps.** Use only the ones the script printed.
  If the script can't find a video id, give the transcript timestamps without links.
- The timestamp is the **start of the paragraph**. A long paragraph can run
  about a minute, so the word may come 30–60s after the link starts. Say so
  when the quote is near the end of the paragraph. The `->` end time is when the next segment begins.
- Group adjacent hits (back-to-back segments on the same thread) into one moment.
- Quote verbatim. The transcript is human-made and may have small errors, so
  don't "fix" names or model versions without saying that you did.

## Limitations

- This only works for episodes that have a lexfridman.com transcript. Older or
  very new episodes may not have one yet. In that case fall back to `youtube-digest`.
- It matches text, not meaning, so the quality of your synonyms decides how much you find.
- The video id is the most-linked one on the page (the full episode). Clips or
  shorts on other channels have different timings.

## Tests

`cd "${CLAUDE_PLUGIN_ROOT}/skills/lex-transcript-search" && python3 -m unittest test_search.py` (offline, synthetic HTML).
