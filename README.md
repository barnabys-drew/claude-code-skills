# claude-code-skills

Claude Code plugins I use day to day: cleaning up GitHub accounts, previewing markdown
beside an agent session, and pulling the useful minutes out of long videos and podcasts.

| Plugin | What it does | Needs |
|---|---|---|
| [repo-triage](plugins/repo-triage) | Walks your GitHub repos stalest-first, recommends keep / archive / delete, acts on your choice, and plans next steps for keepers | [`gh`](https://cli.github.com/) |
| [mdp](plugins/mdp) | Opens a markdown file as a live-reloading preview in a pane beside your session | [Herdr](https://github.com/herdrdev/herdr), [Glow](https://github.com/charmbracelet/glow) |
| [lex-transcript-search](plugins/lex-transcript-search) | Finds where a word or topic comes up in a Lex Fridman Podcast episode, with deep links and quotes | Python 3 (stdlib only) |
| [youtube-digest](plugins/youtube-digest) | Transcribes a YouTube video and ranks the parts worth watching, with verified timestamps | Python 3, the `claude` CLI; optional Docker for audio episodes |

## Install

```text
/plugin marketplace add barnabys-drew/claude-code-skills
/plugin install repo-triage@barnabys-drew
/plugin install lex-transcript-search@barnabys-drew
```

Install whichever plugins you want. Skills trigger on their own when a request matches
(for example "where in the Lex episode with DHH do they talk about inference?"), or you
can ask for one by name.

Without the plugin system, copy a skill folder into `~/.claude/skills/`. `mdp` and
`youtube-digest` also ship a command in their `bin/` directory that needs to be on your
`PATH`.

## Example prompts

- **repo-triage:** "go through my GitHub repos and tell me which ones I still need"
- **mdp:** "/mdp README.md"
- **lex-transcript-search:** "find the part of the latest Lex Fridman episode where they talk about open-weight models"
- **youtube-digest:** "is this video worth 45 minutes? https://youtu.be/..."

## Design notes

**Timestamps are located, never trusted.** Language models asked to cite a position in a
long transcript drift by 25 to 70 seconds. Both video skills avoid that: `youtube-digest`
has the model return a verbatim anchor phrase and finds that phrase in the transcript to
get the real time, and `lex-transcript-search` never asks a model for a time at all. It
searches the published transcript and reports the timestamps printed there.

**Archive before delete.** `repo-triage` treats archiving as the default cleanup because it
is reversible, and never batches deletions: each one is confirmed individually.

**Transcripts are untrusted input.** `youtube-digest` runs its analysis through
`claude -p` with every tool disabled and the transcript fenced as data, so instructions
spoken in a video are reported as quotes rather than followed.

**Personalization is opt-in and inspectable.** `youtube-digest --build-profile` derives an
interest profile from README summaries, `gh repo list` metadata and Claude Code memory
frontmatter. `--audit-dry-run` shows exactly what would be sent, and the result is a plain
JSON file you can edit.

## Development

```bash
# lex-transcript-search (offline tests)
cd plugins/lex-transcript-search/skills/lex-transcript-search && python3 -m unittest test_search.py

# youtube-digest
cd plugins/youtube-digest && pip install -r requirements.txt && PYTHONPATH=. python -m pytest -q tests

# manifests
claude plugin validate .
```

## Also worth installing

These are other people's work that pairs well with the above:

- [mattpocock/skills](https://github.com/mattpocock/skills): grilling a plan, TDD, domain modeling, diagnosing bugs
- [Herdr](https://github.com/herdrdev/herdr): its own skill installs with `herdr integration install claude`

## License

[MIT](LICENSE)
