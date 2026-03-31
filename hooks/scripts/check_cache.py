#!/usr/bin/env python3
"""
branch-audit PreToolUse hook.

Hard-blocks git branch/worktree deletion commands until the target
branch has been audited (and audit is < 24h old).

Reads JSON from stdin, writes JSON to stdout.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

TWENTY_FOUR_HOURS = 86400

# Patterns to detect deletion commands and extract the target name
DELETION_PATTERNS = [
    # git branch -d <branch> or git branch -D <branch>
    (re.compile(r"\bgit\s+branch\s+-[dD]\s+(\S+)"), "branch"),
    # git push <remote> --delete <branch>
    (re.compile(r"\bgit\s+push\s+\S+\s+--delete\s+(\S+)"), "branch"),
    # git push <remote> :<branch>  (shorthand delete)
    (re.compile(r"\bgit\s+push\s+\S+\s+:(\S+)"), "branch"),
    # git worktree remove <path>
    (re.compile(r"\bgit\s+worktree\s+remove\s+(\S+)"), "worktree"),
]


def find_git_root(start=None):
    path = os.path.abspath(start or os.getcwd())
    while path != os.path.dirname(path):
        if os.path.exists(os.path.join(path, ".git")):
            return path
        path = os.path.dirname(path)
    return None


def load_cache(git_root):
    cache_path = os.path.join(git_root, ".git", "branch-audit-cache")
    if not os.path.exists(cache_path):
        return None, cache_path
    try:
        return json.loads(open(cache_path).read()), cache_path
    except (json.JSONDecodeError, OSError):
        return None, cache_path


def worktree_path_to_branch(path, git_root):
    """Resolve a worktree path to its branch name."""
    import subprocess
    result = subprocess.run(
        "git worktree list --porcelain",
        shell=True, capture_output=True, text=True, cwd=git_root
    )
    target_abs = os.path.abspath(os.path.expanduser(path))
    current = {}
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):]}
        elif line.startswith("branch "):
            current["branch"] = line[len("branch refs/heads/"):]
        elif line == "" and current:
            if os.path.abspath(current["path"]) == target_abs:
                return current.get("branch", path)
            current = {}
    return path  # Fall back to the path itself


def deny(name, reason):
    return {
        "permissionDecision": "deny",
        "systemMessage": reason,
    }


def allow():
    return {"permissionDecision": "allow"}


def allow_with_warning(msg):
    return {"permissionDecision": "allow", "systemMessage": msg}


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        print(json.dumps(allow()))
        return

    command = input_data.get("tool_input", {}).get("command", "")
    if not command:
        print(json.dumps(allow()))
        return

    # Check if command matches any deletion pattern
    matched_name = None
    match_type = None
    for pattern, kind in DELETION_PATTERNS:
        m = pattern.search(command)
        if m:
            matched_name = m.group(1)
            match_type = kind
            break

    if not matched_name:
        print(json.dumps(allow()))
        return

    # Find git root
    git_root = find_git_root()
    if not git_root:
        print(json.dumps(allow_with_warning(
            "branch-audit: not in a git repo, skipping audit check"
        )))
        return

    # Resolve worktree path to branch name
    branch_name = matched_name
    if match_type == "worktree":
        branch_name = worktree_path_to_branch(matched_name, git_root)

    # Load cache
    cache, cache_path = load_cache(git_root)

    if cache is None:
        print(json.dumps(deny(
            branch_name,
            f"🛡️  branch-audit: '{branch_name}' has not been audited.\n"
            f"Run: /branch-audit {branch_name}\n"
            f"Then retry the deletion."
        )))
        return

    entry = cache.get(branch_name)
    if entry is None:
        print(json.dumps(deny(
            branch_name,
            f"🛡️  branch-audit: '{branch_name}' has not been audited.\n"
            f"Run: /branch-audit {branch_name}\n"
            f"Then retry the deletion."
        )))
        return

    # Check cache freshness (24h TTL)
    try:
        audited_at = datetime.fromisoformat(entry["audited_at"])
        if audited_at.tzinfo is None:
            audited_at = audited_at.replace(tzinfo=timezone.utc)
        age_secs = (datetime.now(timezone.utc) - audited_at).total_seconds()
        if age_secs > TWENTY_FOUR_HOURS:
            print(json.dumps(deny(
                branch_name,
                f"🛡️  branch-audit: audit for '{branch_name}' is stale "
                f"({int(age_secs // 3600)}h old).\n"
                f"Run: /branch-audit {branch_name}\n"
                f"Then retry the deletion."
            )))
            return
    except (KeyError, ValueError):
        # Can't parse timestamp — allow but warn
        print(json.dumps(allow_with_warning(
            f"branch-audit: could not verify audit freshness for '{branch_name}'"
        )))
        return

    # All good — audit exists and is fresh
    print(json.dumps(allow()))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Hook errors must never block Claude — fail open
        print(json.dumps(allow_with_warning(f"branch-audit hook error: {e}")))
