---
name: mdp
description: Open a markdown file as a rendered, live-reloading preview in a separate Herdr pane (Glow, split to the right). Use when the user invokes /mdp, or asks to preview, render, open, or show a markdown/.md file "in a pane", "side by side", or "like the Cursor preview". Requires running inside Herdr (HERDR_ENV=1).
argument-hint: "[file.md | directory]"
allowed-tools: Bash(mdp:*), Bash(test:*)
---

# mdp — markdown preview pane

The bundled `mdp` command splits the current Herdr pane to the right and runs Glow's viewer
in the new pane. The preview live-reloads when the file changes, and `q` closes the
pane. The command returns immediately, so it does not block this session.

Arguments: `$ARGUMENTS`

## Workflow

1. **Check for Herdr.** Run `test "${HERDR_ENV:-}" = 1`. If it fails, tell the user
   the preview needs Herdr and stop. Never run `mdp` or `glow` outside Herdr: there
   `mdp` falls back to starting Glow's full-screen viewer in the current terminal,
   which would hang the Bash tool.

2. **Resolve the target.**
   - If arguments were given, use them as the path (relative to the current working
     directory). A directory is fine; `mdp` opens Glow's file browser for it.
   - If there are no arguments, use the markdown file this conversation most recently
     created, edited, or discussed. If there is no clear single candidate, ask the
     user which file rather than guessing.
   - If the path does not exist, say so and stop.

3. **Open it.** Run `mdp "<path>"` once. Quote the path, since filenames may contain
   spaces. Do not open a second preview of the same file unless asked.

4. **Confirm in one line**, e.g. "Opened `notes.md` in the pane on the right. It
   updates as the file changes; press `q` there to close it." Nothing more.

## Notes

- The new pane takes focus. To return to this session, the user switches panes in
  Herdr.
- Glow cannot render images or Mermaid diagrams. For files that depend on them,
  mention that opening the file in a browser or an editor with a Mermaid-capable
  preview is the fallback.
