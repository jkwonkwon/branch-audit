---
name: branch-audit
description: Use when analyzing branch or worktree status, recommending cleanup, or before any deletion. Runs /branch-audit to gather all signals before drawing conclusions. Never assess a branch as stale or safe to delete without running the audit first.
---

# Branch Audit Protocol

## The Core Rule

**Never recommend deleting a branch or worktree without running `/branch-audit <branch>` first.**

`git log origin/main..HEAD` (commits ahead of main) is the **weakest** signal for whether a branch is active. A branch with 0 commits ahead can be:
- Brand new, created minutes ago with active uncommitted work
- An in-progress investigation with no commits yet
- Waiting for its first commit while a spec is being written

## Required Protocol

For every branch or worktree under consideration:

1. **Run `/branch-audit <branch>`** — no exceptions, no shortcuts
2. **Read ALL signals** in the output:
   - `uncommitted files` — the strongest signal of active work
   - `last modified` — when files were last touched (minutes ago = active)
   - `open issues` — linked GitHub issues still open = work planned
   - `open PR` — branch has an open PR = definitely active
   - `spec doc` — a design document references this branch = work scoped
   - `branch pattern` — naming conventions carry signal
   - `worktree` — deliberate worktree setup = intentional active work
3. **Apply the verdict**:
   - `ACTIVE` — do NOT recommend deletion. Present signals to user and ask what they want to do.
   - `STALE` — safe to recommend deletion, but always confirm with user first
   - `UNKNOWN` — present signals, explain uncertainty, let user decide

## Why Commits Ahead Is Weak

A developer can have days of active work that is intentionally uncommitted:
- In-progress investigation with files modified but not committed
- Active worktree with WIP changes being iterated
- Work paused mid-session before committing

The file mtime, uncommitted file count, open issues, and PR status are far more reliable indicators of whether a branch represents active work.

## What the Hook Does

A PreToolUse hook automatically blocks `git branch -D`, `git push --delete`, and `git worktree remove` if the target branch has not been audited in the last 24 hours. The hook will tell Claude to run `/branch-audit <branch>` before proceeding.

This means the audit is enforced mechanically — but the skill ensures Claude interprets the results correctly before making a recommendation.
