---
name: repo-triage
description: Use this skill when the user wants to review, clean up, audit, or "spring clean" their GitHub repositories — e.g. "go through my repos", "which repos do I still need", "triage my repos", "audit my github", "help me clean up old projects", "are these repos still needed". Enumerates the user's repos, gathers signals to judge whether each is still useful, asks the user repo-by-repo what to do, acts on the decision (keep / archive / delete), and for repos worth keeping kicks off a conversation about concrete next steps.
---

# Repo Triage

Walk through the user's GitHub repositories, help decide which are still worth
keeping, take the agreed-upon action, and for the keepers surface concrete next
steps and offer to start the work.

## Operating principles (read first)

- **Archive is the default cleanup, not delete.** Archiving is reversible and
  loses nothing. Only delete when the user explicitly chooses it.
- **Deletion always requires per-repo explicit confirmation.** Never batch a
  delete. Restate the repo name and that it is irreversible before running
  `gh repo delete`. If the token lacks the `delete_repo` scope, the command
  fails — tell the user to run `gh auth refresh -s delete_repo`.
- **Don't overwhelm.** Triage in small batches (3–4 repos per question round),
  worst-offenders (most stale) first, not one giant list.
- **Be decisive, not exhaustive.** For each repo give a one-line recommendation
  with the reason, then let the user choose. Don't dump raw JSON at them.

## Phase 1 — Enumerate & gather signals

Confirm which account, then pull every repo in one call. Default to the active
`gh` account; if the user owns repos under multiple owners/orgs, ask which.

```bash
OWNER=$(gh api user --jq .login)
gh repo list "$OWNER" --limit 300 --json \
  name,description,visibility,isFork,isArchived,pushedAt,updatedAt,createdAt,primaryLanguage,diskUsage,stargazerCount,url \
  > /tmp/repo-triage.json
```

Then compute, per repo, the signals that indicate whether it's still alive:

- **Staleness** — days since `pushedAt`. This is the strongest signal.
- **Fork?** — forks are often disposable experiments.
- **Already archived?** — skip from active triage (just note them).
- **Size / language / description** — empty description + tiny `diskUsage` +
  no stars often means a throwaway.
- **Stars / forks** — others may depend on it; weigh against deleting.

Sort by staleness (stalest first) and bucket them by **activity**:

| Activity | Rule of thumb |
|--------|---------------|
| 🟢 Active | pushed < 30 days |
| 🟡 Dormant | pushed 30 days – 1 year |
| 🔴 Stale | pushed > 1 year |
| 🍴 Forks | `isFork: true` (triage separately, usually quick) |
| 📦 Archived | `isArchived: true` (already handled — list only) |

### Also classify each repo by completion status

Activity (when it was last touched) is not the same as how *finished* the work
is — a repo can be recently committed but half-built, or untouched for a year but
fully shipped. Independently tag each repo with a **status** so the user sees
what's done vs. what still needs work:

| Status | What it means | Signals to look for |
|--------|---------------|---------------------|
| ✅ Complete | Shipped / done, no further work planned | Has releases or tags, README present, no open TODO/issues, description reads finished |
| 🚧 In progress | Active build, unfinished | Recent commits, open issues/PRs, `TODO`/`WIP`/`FIXME` in code or last commit, README says "work in progress" |
| 🌱 Early / scaffold | Barely started, just a skeleton | Tiny `diskUsage`, no README, few commits, boilerplate only |
| 🪦 Abandoned | Started but dropped, never finished | Stale (>1yr) **and** lacks releases/README, open work left dangling |
| ❓ Unknown | Not enough signal — ask the user | Sparse metadata; confirm status with the user |

Cheap extra signals to pin down status (run only as needed, e.g. for repos that
aren't obviously throwaways):

```bash
gh release list --repo "$OWNER/<repo>" --limit 1             # any shipped releases?
gh api "repos/$OWNER/<repo>/contents/README.md" >/dev/null 2>&1 \
  && echo "has README" || echo "no README"                   # README present?
gh issue list --repo "$OWNER/<repo>" --state open --limit 1  # open work left?
```

Use status to sharpen the recommendation: 🪦 abandoned + stale → strong archive
candidate (delete only if a throwaway); ✅ complete → keep, or archive if the
user is truly done with it; 🚧 in progress and 🌱 early → keep and carry into
Phase 3 next-steps.

Present a compact summary table (name · last push · language · stars ·
visibility · **status** · one-line recommendation) grouped by activity bucket so
the user sees both how stale each repo is *and* whether it's finished before
deciding anything.

## Phase 2 — Triage decisions

Go bucket by bucket, stalest first. For each small batch use the
**AskUserQuestion** tool — one question per repo, options:

1. **Keep** — still needed → go to Phase 3 for this repo.
2. **Archive** — done with it but keep the history (`gh repo archive`).
3. **Delete** — gone for good (requires the confirmation step below).
4. **Decide later** — skip for now.

Carry the repo's recommendation into the option labels (e.g. mark the
recommended action first). For obvious keepers (active, has stars, recent work)
you can note the recommendation and move quickly.

### Acting on decisions

```bash
# Archive (reversible)
gh repo archive "$OWNER/<repo>" --yes

# Delete (IRREVERSIBLE — only after explicit per-repo confirmation)
gh repo delete "$OWNER/<repo>" --yes
```

Batch the archives at the end and report what was done. Run each delete
individually, immediately after its confirmation.

## Phase 3 — Next steps for keepers

For every repo the user chose to **Keep**, this is where the value is: don't just
move on — figure out what the repo needs and offer to start. Pull deeper signals:

```bash
gh repo view "$OWNER/<repo>" --json description,defaultBranchRef,licenseInfo,hasIssuesEnabled
gh issue list  --repo "$OWNER/<repo>" --state open --limit 20
gh pr list     --repo "$OWNER/<repo>" --state open --limit 20
gh api "repos/$OWNER/<repo>/branches" --jq '.[].name'   # stale branches?
```

Look for concrete, actionable next steps and surface them as a short list:

- Open issues / stale open PRs that could be closed or finished
- Missing README, license, or description
- Stale branches to prune; failing CI
- A `TODO`/roadmap the user mentioned (check memory / existing project notes)

Then **initiate the conversation**: summarize the repo's state in a sentence and
ask the user which next step they want to tackle now — and if they pick one, dive
straight into it (clone if needed, open the files, start the work). The goal is to
turn the audit into momentum, not just a cleanup checklist.

If the user has many keepers, offer to record the agreed next steps (e.g. as a
memory note or a tracking issue) so nothing is lost.

## Boundaries

**Will:** list repos, assess staleness/usefulness, archive on request, delete only
with explicit per-repo confirmation, and kick off next-step work on keepers.

**Will not:** delete anything without confirmation, batch deletes, or touch repos
the user owns under an org without first confirming the owner.
