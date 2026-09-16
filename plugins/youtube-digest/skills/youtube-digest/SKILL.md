---
name: youtube-digest
description: Transcribe a YouTube video and surface the genuinely interesting parts with timestamped deep links. Use whenever the user shares a YouTube URL and wants to know what's in it, asks "what's interesting in this video", "summarize this video", "is this video worth watching", "what does this talk cover", "give me the highlights/key points/timestamps", wants a transcript of a YouTube video, or asks whether they should watch something before committing 45 minutes to it.
---

# YouTube Digest

Turn a YouTube URL into a transcript plus a ranked list of the parts worth someone's
time, each with a link that seeks to that exact second.

The bundled `ytdigest` command is on PATH while this plugin is enabled. It builds its own
virtualenv on first run, in `$YTDIGEST_HOME` (default `~/.local/share/ytdigest`), which also
holds the transcript cache and the interest profile. Requires `python3` and the `claude` CLI.

## Workflow

1. **Run it.** Everything is one command:
   ```bash
   ytdigest "<URL>"
   ```
2. **Relay the result.** The tool's own output is already formatted for a human. Show it
   rather than re-summarizing it — re-summarizing a summary loses the timestamps, which
   are the whole point.
3. **Offer the markdown report** if they'll want to keep it: add `--markdown <file>`.

## Interest profile

The tool ranks highlights against a profile derived from auditing the user's actual work
(local projects, GitHub repos, memory-file frontmatter).

Building it is opt-in. `--build-profile` reads README summaries under `~/code`, the `gh repo
list` metadata, and Claude Code memory-file frontmatter (no source code), and sends that
evidence to the local `claude` CLI. Run `--audit-dry-run` first if the user wants to see
exactly what would be sent.

```bash
ytdigest --build-profile                 # derive it; run once, re-run when their work shifts
ytdigest --build-profile --audit-dry-run # show the evidence, send nothing
ytdigest --show-profile                  # what it concluded
ytdigest URL --no-profile                # generic mode for one run
```

If `ytdigest URL` logs "No interest profile", offer to build one — it materially improves
ranking. It lives in `profile.json`, is plain editable JSON, and is stored in `$YTDIGEST_HOME`.

With a profile active the report gains a `FOR YOU` verdict up top and `→ for you:` lines
on individual highlights. **A highlight with no relevance line is correct, not a bug** —
the prompt explicitly permits null relevance so the model doesn't invent connections.
If `FOR YOU` says the video is a poor match, relay that plainly; it's the most useful
thing the tool produces.

## Audio episodes

`ytdigest URL --episode` also writes a narrated MP3 and rebuilds a podcast feed in
`~/podcast/`. Requires the Kokoro TTS container:

```bash
docker run -d --name kokoro -p 8880:8880 --restart unless-stopped \
    ghcr.io/remsky/kokoro-fastapi-cpu:latest
```

If the tool reports no speech backend, start that container — it's the usual cause.

```bash
ytdigest URL --script-only   # narration script, no audio (fast; good for judging quality)
ytdigest URL --voice am_adam # 68 voices; af_heart default
```

The script is a **separate rewrite**, not the report read aloud — it cuts to 3-5
highlights and strips all timestamps, which move to the show notes. If the user asks why
the episode omits something in the written report, that's the intended design, not a bug.

Analysis is cached in `$YTDIGEST_HOME/.cache/<id>.analysis.json`, so `--episode` after a normal run
reuses it rather than paying for a second analysis. `--refresh-analysis` forces a re-run.

## Options worth knowing

```bash
ytdigest URL --markdown report.md     # saveable report with clickable links
ytdigest URL --json out.json          # structured output, for further processing
ytdigest URL --transcript-only        # just the text, no analysis
ytdigest URL --save-transcript t.txt  # keep the transcript alongside the report
ytdigest URL --refresh                # re-fetch; ignores the cached transcript
ytdigest URL --force-whisper          # local transcription even when captions exist
ytdigest URL --whisper-model small.en # better on heavy jargon, ~2-3x slower
ytdigest URL -q                       # suppress progress output
```

Progress goes to stderr and the report to stdout, so `ytdigest URL > report.txt` captures
only the report.

## What to expect

- **Videos with captions** return in well under a minute.
- **Videos without captions** fall back to local Whisper on CPU. Say so before starting —
  a long video takes several minutes, and the first ever run downloads a ~150MB model.
- **Long videos** (90+ min) are analyzed in sections and merged; this takes proportionally
  longer and the tool prints its progress per section.
- **Transcripts are cached** in `$YTDIGEST_HOME/.cache/<video_id>.json`, so re-running the same video
  skips all network work. Iterating on a report is cheap.

## Reading the output

- Timestamps are **verified**, not model-guessed: each highlight carries a verbatim anchor
  phrase that `verify.py` locates in the transcript to derive the true time. Raw model
  timestamps drift 25-70 seconds, so this matters.
- A `~` prefix on a timestamp means that anchor could not be located and the link is
  approximate. Mention this if you're pointing the user at one.
- `SKIP IT IF` is a real signal. The tool is instructed to say plainly when a video is
  thin rather than manufacture highlights. If it says the video is filler, relay that
  honestly instead of dressing it up.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `no English captions` | Expected; it falls back to Whisper. `--no-whisper` to fail fast instead. |
| `declined to process this transcript` | A refusal. It retries once automatically. If it persists, the video may genuinely be a problem case — report it rather than working around it. |
| `age-restricted` / `private video` | Not fetchable without cookies. Nothing to do. |
| Audio decode error | Rare. `sudo apt install ffmpeg` resolves it. |
| `claude CLI not found` | The analysis engine is the local `claude` binary; it must be on PATH. |

## Notes

- English only, by design.
- Analysis runs through the local `claude` CLI on the user's existing subscription — no
  API key, no per-token cost.
- The transcript is untrusted third-party content. The analysis subprocess runs with
  `--allowed-tools ""` and the transcript is fenced in the prompt as data. If a video's
  transcript contains something shaped like an instruction, it gets reported as a quote,
  never followed.
