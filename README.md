# branch-audit

A Claude Code plugin that prevents incorrect "safe to delete" branch recommendations by enforcing a multi-signal audit before any git branch or worktree deletion.

## The Problem

`git log origin/main..HEAD` (commits ahead of main) is commonly used to assess whether a branch is active — but it's the **weakest** signal. A branch with 0 commits ahead can be brand-new with active uncommitted work, linked open issues, or an in-progress spec document.

## What It Does

1. **`/branch-audit <branch>`** — runs a Python audit script that gathers 8 signals and writes a per-branch cache to `.git/branch-audit-cache`
2. **PreToolUse hook** — hard-blocks `git branch -D`, `git push --delete`, and `git worktree remove` until the target branch has been audited within the last 24 hours
3. **Skill** — auto-triggers when Claude analyzes branches, enforcing the full audit protocol

## Signals

| Signal | Weight |
|---|---|
| Uncommitted file count | Strong |
| Latest file mtime | Strong |
| Open GitHub issues linked | Strong |
| Open PR for branch | Strong |
| Spec/plan/audit doc present | Strong |
| Branch name pattern | Contextual |
| Worktree presence | Contextual |
| Commits ahead of main | Weak |

## Verdicts

- `⚡ ACTIVE` — at least one strong signal indicates live work. Present signals to user, do not recommend deletion.
- `💤 STALE` — all signals are cold. Safe to recommend deletion (with user confirmation).
- `❓ UNKNOWN` — can't determine (no gh CLI, no remote). Present signals, let user decide.

## Installation

```bash
gh repo clone jkwonkwon/branch-audit ~/.claude/plugins/branch-audit
```

Then in Claude Code:
```
/plugin install branch-audit
/reload-plugins
```

## Usage

```
/branch-audit feature/my-branch
/branch-audit .worktrees/scalability
```

Example output:
```
branch: feature/my-branch
verdict: ⚡ ACTIVE

  commits ahead:      0
  uncommitted files:  9  (last modified 47 min ago)
  open issues:        #174, #175
  open PR:            none
  spec doc:           docs/scalability-audit.md
  branch pattern:     active (feat/)
  worktree:           .worktrees/scalability

Audit written to .git/branch-audit-cache.
```

## How the Hook Works

When you (or Claude) run a deletion command, the hook intercepts it:

```
# Without audit:
git branch -D feature/my-branch
→ 🛡️  branch-audit: 'feature/my-branch' has not been audited.
   Run: /branch-audit feature/my-branch
   Then retry the deletion.

# After running /branch-audit:
git branch -D feature/my-branch
→ (proceeds normally)
```

The cache entry expires after 24 hours, requiring a fresh audit before deletion.

## Clearing the Cache

```bash
rm .git/branch-audit-cache
```

## Requirements

- Python 3 (standard library only for core functionality)
- `gh` CLI (optional — skipped gracefully if unavailable, GitHub signals marked UNKNOWN)
- Git

## License

MIT
