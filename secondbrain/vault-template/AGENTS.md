# AGENTS.md: rules for agents working in this vault

## Output
- Every deliverable is a markdown file. No HTML pages, no sidecar files.
- One file per deliverable unless asked for more.

## Where files go (PARA)
- `Projects/` active work with an end · `Areas/` ongoing responsibilities · `Research/` reference · `Archives/` done.
- Actionability decides the bucket, not topic. Resolve shorthand like `project-foo-bar` to `Projects/foo/bar/` against the real tree; do not create a literal dashed top-level folder.
- Filenames are lowercase kebab-case; no dates unless the file is inherently dated.

## Research notes
- Title, one-line subtitle, `Researched:` date, `Goal:`, `Scope:`.
- Open with a TL;DR of about five bolded claims; flag the one most likely to change the plan.
- Tables for comparisons; prose for caveats. Close with `## What's confirmed vs. inferred` and sources grouped by theme.
- Never invent a figure; mark it unknown.

## Secrets
- API keys live in `.env` at the vault root, which is gitignored. Never copy a value into a note or a commit.
