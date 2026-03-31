---
description: Audit a branch or worktree for activity signals before deletion. Gathers commits ahead, uncommitted files, file timestamps, open PRs, linked issues, and spec docs. Writes result to .git/branch-audit-cache.
---

Run the audit script for the given branch or worktree path:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/audit.py" $ARGUMENTS
```

Present the output directly to the user. The audit result is cached to `.git/branch-audit-cache` and will be checked by the deletion hook for 24 hours.

If no argument is provided, ask the user which branch or worktree to audit.
