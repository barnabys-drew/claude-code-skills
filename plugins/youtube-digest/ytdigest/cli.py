"""Command-line entry point — wires the four stages together.

    URL -> fetch -> normalize -> analyze -> render
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analyze import AnalysisError, DEFAULT_MODEL, analyze
from .profile import render_for_prompt
from .fetch import FetchError, get_transcript, parse_video_id
from .render import render_markdown, render_terminal
from .transcript import (
    analysis_fingerprint,
    as_plain_text,
    chunk_segments,
    load_analysis,
    load_cache,
    save_analysis,
    save_cache,
    word_count,
)
from .profile import DATA_HOME, DEFAULT_PROFILE_PATH, ProfileError, describe, load as load_profile
from .script import DEFAULT_STYLE, STYLES
from .speak import DEFAULT_BASE_URL, DEFAULT_VOICE, SpeechError
from .verify import verify_analysis
from .whisper import DEFAULT_MODEL as WHISPER_DEFAULT, MODELS as WHISPER_MODELS

DEFAULT_CACHE = DATA_HOME / ".cache"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ytdigest",
        description="Transcribe a YouTube video and surface the interesting bits.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  ytdigest --build-profile              audit your work to learn what you care about
  ytdigest --show-profile               see what it concluded
  ytdigest "https://youtu.be/dQw4w9WgXcQ"
  ytdigest URL --markdown report.md     save a markdown report
  ytdigest URL --episode                also make a narrated audio episode
  ytdigest URL --script-only            just the narration script, no audio
  ytdigest URL --transcript-only        just dump the transcript
  ytdigest URL --refresh                ignore the cached transcript
  ytdigest URL --force-whisper          transcribe locally even if captions exist
""",
    )

    # Optional so --build-profile and --show-profile work without a URL.
    parser.add_argument(
        "url", nargs="?", help="YouTube URL or bare 11-character video ID"
    )
    parser.add_argument("--version", action="version", version=f"ytdigest {__version__}")

    output = parser.add_argument_group("output")
    output.add_argument("--markdown", metavar="FILE", help="write a markdown report")
    output.add_argument("--json", metavar="FILE", help="write the raw analysis JSON")
    output.add_argument(
        "--transcript-only",
        action="store_true",
        help="print the transcript and skip analysis entirely",
    )
    output.add_argument(
        "--save-transcript", metavar="FILE", help="write the full transcript to a file"
    )
    output.add_argument("-q", "--quiet", action="store_true", help="suppress progress messages")

    transcript = parser.add_argument_group("transcript")
    transcript.add_argument(
        "--refresh", action="store_true", help="re-fetch even if a cached transcript exists"
    )
    transcript.add_argument(
        "--refresh-analysis",
        action="store_true",
        help="re-run the analysis even if a cached one matches",
    )
    transcript.add_argument(
        "--no-whisper",
        action="store_true",
        help="fail instead of falling back to local transcription",
    )
    transcript.add_argument(
        "--force-whisper",
        action="store_true",
        help="always transcribe locally, ignoring YouTube captions",
    )
    transcript.add_argument(
        "--whisper-model",
        default=WHISPER_DEFAULT,
        choices=WHISPER_MODELS,
        help=f"Whisper model size (default: {WHISPER_DEFAULT})",
    )
    transcript.add_argument(
        "--keep-audio", action="store_true", help="don't delete downloaded audio afterwards"
    )
    transcript.add_argument(
        "--cache-dir", type=Path, default=DEFAULT_CACHE, help="where to cache transcripts"
    )

    profile_group = parser.add_argument_group("interest profile")
    profile_group.add_argument(
        "--build-profile",
        action="store_true",
        help="audit your projects, repos, and notes to derive an interest profile, then exit",
    )
    profile_group.add_argument(
        "--show-profile", action="store_true", help="print the current profile and exit"
    )
    profile_group.add_argument(
        "--no-profile", action="store_true", help="ignore the profile for this run"
    )
    profile_group.add_argument(
        "--profile-path", type=Path, default=DEFAULT_PROFILE_PATH, help="where the profile lives"
    )
    profile_group.add_argument(
        "--audit-dry-run",
        action="store_true",
        help="with --build-profile, print the gathered evidence instead of sending it",
    )
    profile_group.add_argument(
        "--code-root", type=Path, default=Path.home() / "code", help="projects to audit"
    )
    profile_group.add_argument(
        "--no-github", action="store_true", help="skip GitHub repos when auditing"
    )

    episode = parser.add_argument_group("audio episode")
    episode.add_argument(
        "--episode",
        action="store_true",
        help="also write a narrated audio episode and rebuild the podcast feed",
    )
    episode.add_argument(
        "--episode-dir",
        type=Path,
        default=Path.home() / "podcast",
        help="where episodes and feed.xml live (default: ~/podcast)",
    )
    episode.add_argument(
        "--voice", default=DEFAULT_VOICE, help=f"TTS voice (default: {DEFAULT_VOICE})"
    )
    episode.add_argument(
        "--tts-url", default=DEFAULT_BASE_URL, help=f"TTS endpoint (default: {DEFAULT_BASE_URL})"
    )
    episode.add_argument(
        "--speed", type=float, default=1.0, help="narration speed multiplier (default: 1.0)"
    )
    episode.add_argument(
        "--feed-base-url",
        default="",
        help="public base URL the audio will be served from; defaults to the local dir",
    )
    episode.add_argument(
        "--script-only",
        action="store_true",
        help="write the narration script and stop, without synthesizing audio",
    )
    episode.add_argument(
        "--style",
        default=DEFAULT_STYLE,
        choices=STYLES,
        help=(
            "narrative = 5-8 min, story-shaped, signposted transitions; "
            f"brief = 2-3 min, numbered facts, no color (default: {DEFAULT_STYLE})"
        ),
    )

    notes = parser.add_argument_group("facts document")
    notes.add_argument(
        "--notes",
        action="store_true",
        help="add this video's facts to the running reference doc",
    )
    notes.add_argument(
        "--no-notes",
        action="store_true",
        help="skip the facts doc even when making an episode",
    )
    notes.add_argument(
        "--notes-file",
        type=Path,
        default=None,
        help="reference doc path (default: <episode-dir>/facts.md)",
    )

    analysis = parser.add_argument_group("analysis")
    analysis.add_argument("--model", default=DEFAULT_MODEL, help=f"default: {DEFAULT_MODEL}")
    analysis.add_argument(
        "--max-words",
        type=int,
        default=12000,
        help="words per analysis chunk; longer videos are split (default: 12000)",
    )
    analysis.add_argument(
        "--timeout", type=int, default=600, help="seconds per analysis call (default: 600)"
    )

    return parser


def _obtain_transcript(args, video_id: str, log):
    """Get `(meta, segments, source)` from cache, captions, or Whisper.

    Precedence: --force-whisper beats the cache beats captions beats Whisper.
    """
    from .whisper import transcribe_video

    if not args.refresh and not args.force_whisper:
        cached = load_cache(args.cache_dir, video_id)
        if cached is not None:
            meta, segments = cached
            log(f"  Using cached transcript ({len(segments)} segments).")
            return meta, segments, meta.get("source", "cache")

    if args.force_whisper:
        return transcribe_video(
            video_id,
            args.cache_dir,
            model_name=args.whisper_model,
            keep_audio=args.keep_audio,
            verbose=not args.quiet,
        )

    log("  Fetching video info and captions...")
    result = get_transcript(video_id)

    if result is not None:
        return result

    if args.no_whisper:
        raise FetchError(
            "This video has no English captions, and --no-whisper is set.\n"
            "Drop --no-whisper to transcribe it locally instead."
        )

    return transcribe_video(
        video_id,
        args.cache_dir,
        model_name=args.whisper_model,
        keep_audio=args.keep_audio,
        verbose=not args.quiet,
    )


DEFAULT_MEMORY_ROOT = Path.home() / ".claude" / "projects"


def _make_episode(args, meta, analysis, video_id, log) -> int:
    """Narration script -> audio -> manifest -> feed. Returns an exit code."""
    from .feed import load_manifest, make_episode, save_manifest, upsert_episode, write_feed
    from .script import estimate_duration, write_script
    from .speak import audio_duration, health_check, synthesize, tag_audio

    log(f"  Writing the {args.style} narration script...")
    script = write_script(
        meta, analysis, model=args.model, timeout=args.timeout, style=args.style
    )

    root = args.episode_dir
    root.mkdir(parents=True, exist_ok=True)
    # Non-default styles get their own filename so both can coexist for A/B
    # comparison rather than one silently overwriting the other.
    stem = video_id if args.style == DEFAULT_STYLE else f"{video_id}-{args.style}"
    script_path = root / f"{stem}.txt"
    script_path.write_text(script, encoding="utf-8")

    words = len(script.split())
    estimated = estimate_duration(script)
    log(f"  Script: {words} words, ~{estimated // 60}m{estimated % 60:02d}s spoken.")
    log(f"  Saved to {script_path}")

    if args.script_only:
        print(script)
        return 0

    # Fail before spending time on a long synthesis we can't complete.
    if not health_check(args.tts_url):
        print(
            f"error: no speech backend at {args.tts_url}.\n"
            "Start it with:\n"
            "  docker run -d --name kokoro -p 8880:8880 --restart unless-stopped \\\n"
            "      ghcr.io/remsky/kokoro-fastapi-cpu:latest",
            file=sys.stderr,
        )
        return 1

    audio_path = root / f"{stem}.mp3"
    log(f"  Synthesizing audio with voice {args.voice!r}...")
    try:
        synthesize(
            script,
            audio_path,
            base_url=args.tts_url,
            voice=args.voice,
            speed=args.speed,
            verbose=not args.quiet,
        )
    except SpeechError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Prefer the real duration over the word-count estimate — Kokoro narrates
    # faster than the estimate assumes, and a wrong itunes:duration is visible.
    measured = audio_duration(audio_path)
    duration = measured if measured is not None else estimated

    size_mb = audio_path.stat().st_size / 1_000_000
    log(f"  Audio: {audio_path} ({size_mb:.1f} MB, {duration // 60}m{duration % 60:02d}s)")

    # Show notes carry the timestamped deep links, which are useless spoken but
    # are the whole point once you're back at a keyboard.
    description = _show_notes(meta, analysis, video_id)

    tag_audio(
        audio_path,
        title=meta.get("title", "Untitled"),
        artist=meta.get("uploader", "ytdigest"),
        comment=description,
    )

    episode_title = meta.get("title", "Untitled")
    if args.style != DEFAULT_STYLE:
        episode_title = f"{episode_title} [{args.style}]"

    episodes = upsert_episode(
        load_manifest(root),
        make_episode(
            video_id=stem,
            title=episode_title,
            description=description,
            audio_path=audio_path,
            duration_seconds=duration,
            channel=meta.get("uploader", ""),
        ),
    )
    save_manifest(root, episodes)

    base_url = args.feed_base_url or root.as_uri()
    feed_path = write_feed(root, episodes, base_url)
    log(f"  Feed: {feed_path} ({len(episodes)} episode(s))")

    _update_notes(args, meta, analysis, video_id, log)
    return 0


def _update_notes(args, meta, analysis, video_id, log) -> None:
    """Add this video to the running facts doc, unless told not to."""
    if args.no_notes:
        return

    from .notes import update_notes

    doc_path = args.notes_file or (args.episode_dir / "facts.md")
    path, total = update_notes(doc_path, video_id, meta, analysis)
    log(f"  Facts doc: {path} ({total} video(s))")


def _show_notes(meta: dict, analysis: dict, video_id: str) -> str:
    """Episode description: the deep links the narration deliberately omits."""
    from .fetch import timestamp_url
    from .render import item_seconds
    from .transcript import format_timestamp

    lines = [analysis.get("tldr", ""), ""]

    if analysis.get("for_you"):
        lines += [analysis["for_you"], ""]

    lines.append(f"Source: https://youtu.be/{video_id}")
    lines.append("")

    for item in analysis.get("highlights") or []:
        if not isinstance(item, dict):
            continue
        seconds, _ = item_seconds(item)
        lines.append(
            f"{format_timestamp(seconds)} — {item.get('title', '')} "
            f"{timestamp_url(video_id, seconds)}"
        )

    return "\n".join(lines).strip()


def _run_audit(args, log) -> int:
    """Gather evidence about the user and derive an interest profile."""
    from .audit import build_profile, format_evidence, gather_evidence
    from .profile import save as save_profile

    log(f"  Auditing {args.code_root} and your GitHub repos...")
    evidence = gather_evidence(
        args.code_root, DEFAULT_MEMORY_ROOT, use_github=not args.no_github
    )

    counts = {key: len(value) for key, value in evidence.items()}
    log(
        f"  Evidence: {counts['local_projects']} local projects, "
        f"{counts['github_repos']} repos, {counts['memories']} notes."
    )

    if not any(counts.values()):
        print(
            f"error: found nothing to audit under {args.code_root}.\n"
            "Pass --code-root to point at your projects.",
            file=sys.stderr,
        )
        return 1

    # Let people see exactly what would be sent before it's sent.
    if args.audit_dry_run:
        print(format_evidence(evidence))
        return 0

    log("  Deriving profile...")
    try:
        profile = build_profile(evidence, model=args.model, timeout=args.timeout)
    except AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    save_profile(profile, args.profile_path)
    print(describe(profile))
    print(f"\nSaved to {args.profile_path} — edit it freely; it's plain JSON.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Progress goes to stderr so `ytdigest URL > report.txt` captures only the
    # report itself. Same reason you keep logging off stdout in a shell filter.
    def log(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr, flush=True)

    if args.build_profile:
        return _run_audit(args, log)

    try:
        existing = load_profile(args.profile_path)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.show_profile:
        if existing is None:
            print(
                f"No profile at {args.profile_path}. Build one with:\n  ytdigest --build-profile",
                file=sys.stderr,
            )
            return 1
        print(describe(existing))
        return 0

    if not args.url:
        print("error: a YouTube URL is required.", file=sys.stderr)
        return 2

    try:
        video_id = parse_video_id(args.url)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        meta, segments, source = _obtain_transcript(args, video_id, log)
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not segments:
        print("error: transcript came back empty.", file=sys.stderr)
        return 1

    # Persist for next time. `source` rides along in meta so a cache hit can
    # still report where the text originally came from.
    save_cache(args.cache_dir, video_id, {**meta, "source": source}, segments)

    words = word_count(segments)
    log(f"  Transcript: {words:,} words, {len(segments)} segments ({source}).")

    if args.save_transcript:
        Path(args.save_transcript).write_text(as_plain_text(segments), encoding="utf-8")
        log(f"  Transcript written to {args.save_transcript}")

    if args.transcript_only:
        print(as_plain_text(segments))
        return 0

    chunks = chunk_segments(segments, max_words=args.max_words)
    if len(chunks) > 1:
        log(f"  Long video — analyzing in {len(chunks)} sections.")
    else:
        log("  Analyzing...")

    profile_text = None
    if existing is not None and not args.no_profile:
        profile_text = render_for_prompt(existing)
        log("  Ranking against your interest profile.")
    elif existing is None and not args.no_profile:
        log("  No interest profile — run ytdigest --build-profile to personalize.")

    fingerprint = analysis_fingerprint(args.model, profile_text, args.max_words)
    analysis = None
    if not args.refresh_analysis:
        analysis = load_analysis(args.cache_dir, video_id, fingerprint)
        if analysis is not None:
            log("  Using cached analysis.")

    if analysis is None:
        try:
            analysis = analyze(
                meta,
                chunks,
                model=args.model,
                timeout=args.timeout,
                verbose=not args.quiet,
                profile_text=profile_text,
            )
        except AnalysisError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        save_analysis(args.cache_dir, video_id, analysis, fingerprint)

    # The model's own timestamps drift by a minute or more on long videos, so
    # we relocate each highlight by finding its anchor phrase in the transcript.
    analysis, stats = verify_analysis(analysis, segments)
    if stats["unverified"]:
        log(
            f"  Timestamps: {stats['verified']} verified, "
            f"{stats['unverified']} unverified (shown with a ~ prefix)."
        )

    print(render_terminal(meta, analysis, video_id, source))

    if args.markdown:
        Path(args.markdown).write_text(
            render_markdown(meta, analysis, video_id, source), encoding="utf-8"
        )
        log(f"  Markdown report written to {args.markdown}")

    if args.json:
        Path(args.json).write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        log(f"  Analysis JSON written to {args.json}")

    if args.episode or args.script_only:
        try:
            return _make_episode(args, meta, analysis, video_id, log)
        except AnalysisError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    # --notes works on its own, so you can build the reference doc from a
    # normal run without paying for audio synthesis.
    if args.notes:
        _update_notes(args, meta, analysis, video_id, log)

    return 0


if __name__ == "__main__":
    # sys.exit sets the process exit code, so `ytdigest URL && echo ok` behaves.
    sys.exit(main())
