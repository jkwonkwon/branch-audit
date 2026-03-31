#!/usr/bin/env python3
"""
branch-audit: Multi-signal branch/worktree activity analysis.

Usage: python3 audit.py <branch-name-or-worktree-path>

Writes results to .git/branch-audit-cache (JSON).
Exit code: always 0 (errors are reported in output, not exit code).
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd, cwd=None, check=False):
    """Run a shell command, return (stdout, stderr, returncode)."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, cwd=cwd
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def gh_available():
    _, _, rc = run("gh --version")
    return rc == 0


def find_git_root(start=None):
    """Walk up from start (or CWD) to find the .git directory."""
    path = os.path.abspath(start or os.getcwd())
    while path != os.path.dirname(path):
        if os.path.exists(os.path.join(path, ".git")):
            return path
        path = os.path.dirname(path)
    return None


def humanize_age(mtime_iso):
    """Return human-friendly age string like '47 min ago' or '3 days ago'."""
    if not mtime_iso:
        return "unknown"
    dt = datetime.fromisoformat(mtime_iso)
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs} sec ago"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} hr ago"
    return f"{secs // 86400} days ago"


# ---------------------------------------------------------------------------
# Signal collectors
# ---------------------------------------------------------------------------

def resolve_branch(target, git_root):
    """Resolve a worktree path or branch name to a canonical branch name."""
    stdout, _, _ = run("git worktree list --porcelain", cwd=git_root)
    worktrees = {}
    current_wt = {}
    for line in stdout.splitlines():
        if line.startswith("worktree "):
            current_wt = {"path": line[len("worktree "):]}
        elif line.startswith("branch "):
            current_wt["branch"] = line[len("branch refs/heads/"):]
            worktrees[current_wt["path"]] = current_wt.get("branch", "HEAD")
        elif line == "" and current_wt:
            current_wt = {}

    # Normalize target: strip trailing slash, expand ~
    target_abs = os.path.abspath(os.path.expanduser(target))

    # Check if it's a known worktree path
    if target_abs in worktrees:
        return worktrees[target_abs], target_abs

    # Check if it's relative to git_root
    for path, branch in worktrees.items():
        if os.path.abspath(path) == target_abs:
            return branch, path

    # Assume it's already a branch name — find its worktree if any
    for path, branch in worktrees.items():
        if branch == target:
            return branch, path

    return target, None


def get_commits_ahead(branch, git_root):
    stdout, _, rc = run(
        f"git log origin/main..{branch} --oneline", cwd=git_root
    )
    if rc != 0:
        # Try without origin/main (no remote)
        stdout, _, rc = run(
            f"git log main..{branch} --oneline", cwd=git_root
        )
    return len([l for l in stdout.splitlines() if l]) if rc == 0 else 0


def get_uncommitted_files(worktree_path, git_root):
    """Return list of uncommitted file paths (modified, added, deleted)."""
    cwd = worktree_path or git_root
    stdout, _, rc = run("git status --porcelain", cwd=cwd)
    if rc != 0:
        return []
    files = []
    for line in stdout.splitlines():
        if line.strip():
            # Format: XY filename (XY = status codes)
            fname = line[3:].strip()
            files.append(os.path.join(cwd, fname))
    return files


def get_latest_mtime(file_paths):
    """Return ISO timestamp of the most recently modified file."""
    latest = None
    latest_file = None
    for fp in file_paths:
        try:
            mtime = os.stat(fp).st_mtime
            if latest is None or mtime > latest:
                latest = mtime
                latest_file = fp
        except OSError:
            continue
    if latest is None:
        return None, None
    dt = datetime.fromtimestamp(latest, tz=timezone.utc)
    return dt.isoformat(), latest_file


def find_spec_doc(branch, git_root):
    """Search docs/ for .md files referencing the branch name."""
    docs_dir = os.path.join(git_root, "docs")
    if not os.path.isdir(docs_dir):
        return None
    branch_short = branch.split("/")[-1]  # e.g. scalability-architecture
    for root, _, files in os.walk(docs_dir):
        for f in files:
            if not f.endswith(".md"):
                continue
            path = os.path.join(root, f)
            try:
                content = open(path).read()
                if branch in content or branch_short in content:
                    return os.path.relpath(path, git_root)
            except OSError:
                continue
    return None


def get_open_pr(branch):
    """Return PR info dict or None. Requires gh CLI."""
    stdout, _, rc = run(
        f'gh pr list --head "{branch}" --state open --json number,title --limit 1'
    )
    if rc != 0 or not stdout:
        return None
    try:
        prs = json.loads(stdout)
        return prs[0] if prs else None
    except (json.JSONDecodeError, IndexError):
        return None


def get_linked_issues(branch, git_root):
    """Find open GitHub issues referenced in docs/ or commit messages."""
    # Collect all #NNN references from docs/
    issue_numbers = set()
    docs_dir = os.path.join(git_root, "docs")
    if os.path.isdir(docs_dir):
        for root, _, files in os.walk(docs_dir):
            for f in files:
                if not f.endswith(".md"):
                    continue
                try:
                    content = open(os.path.join(root, f)).read()
                    for m in re.findall(r"#(\d+)", content):
                        issue_numbers.add(int(m))
                except OSError:
                    continue

    # Also check recent commit messages on the branch
    stdout, _, _ = run(
        f'git log origin/main..{branch} --pretty=format:"%s %b"'
    )
    for m in re.findall(r"#(\d+)", stdout):
        issue_numbers.add(int(m))

    if not issue_numbers:
        return []

    # Filter to only open issues
    stdout, _, rc = run(
        'gh issue list --state open --json number --limit 200'
    )
    if rc != 0:
        return list(issue_numbers)  # Can't verify, assume all referenced

    try:
        open_issues = {i["number"] for i in json.loads(stdout)}
        return sorted(issue_numbers & open_issues)
    except (json.JSONDecodeError, KeyError):
        return list(issue_numbers)


def classify_branch_name(branch):
    """Return 'active', 'stale-hint', or 'neutral' based on naming pattern."""
    active_patterns = [r"^(investigate|fix|feat|feature|wip|hotfix|dev)/"]
    stale_patterns = [r"^old/", r"^\d{4}-", r"^(tmp|temp|test)-"]
    for p in active_patterns:
        if re.match(p, branch, re.IGNORECASE):
            return "active"
    for p in stale_patterns:
        if re.match(p, branch, re.IGNORECASE):
            return "stale-hint"
    return "neutral"


def get_worktree_path(branch, git_root):
    """Return the worktree path for this branch, if any."""
    stdout, _, _ = run("git worktree list --porcelain", cwd=git_root)
    current_wt = {}
    for line in stdout.splitlines():
        if line.startswith("worktree "):
            current_wt = {"path": line[len("worktree "):]}
        elif line.startswith("branch "):
            current_wt["branch"] = line[len("branch refs/heads/"):]
        elif line == "" and current_wt:
            if current_wt.get("branch") == branch:
                return current_wt["path"]
            current_wt = {}
    return None


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

TWENTY_FOUR_HOURS = 86400
SEVEN_DAYS = 7 * 86400


def compute_verdict(signals):
    now = datetime.now(timezone.utc).timestamp()

    uncommitted = signals["uncommitted_files"] > 0
    has_pr = signals["open_pr"] is not None
    has_issues = len(signals["open_issues"]) > 0
    has_spec = signals["has_spec_doc"] is not None

    mtime_iso = signals["latest_mtime"]
    recent = False
    old = True
    if mtime_iso:
        try:
            dt = datetime.fromisoformat(mtime_iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = now - dt.timestamp()
            recent = age < TWENTY_FOUR_HOURS
            old = age > SEVEN_DAYS
        except ValueError:
            pass

    if uncommitted or has_pr or has_issues or has_spec or recent:
        return "ACTIVE"
    if not uncommitted and not has_pr and not has_issues and not has_spec and old:
        return "STALE"
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def write_cache(git_root, branch, signals, verdict):
    cache_path = os.path.join(git_root, ".git", "branch-audit-cache")
    cache = {}
    if os.path.exists(cache_path):
        try:
            cache = json.loads(open(cache_path).read())
        except (json.JSONDecodeError, OSError):
            pass

    cache[branch] = {
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "signals": signals,
        "verdict": verdict,
    }

    with open(cache_path, "w") as f:
        json.dump(cache, f, indent=2)

    return cache_path


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(branch, worktree_path, signals, verdict, cache_path):
    uncommitted = signals["uncommitted_files"]
    mtime = signals["latest_mtime"]
    age = f"  (last modified {humanize_age(mtime)})" if mtime and uncommitted > 0 else ""
    pr = signals["open_pr"]
    pr_str = f"#{pr['number']} {pr['title']}" if pr else "none"
    issues_str = ", ".join(f"#{n}" for n in signals["open_issues"]) or "none"
    spec = signals["has_spec_doc"] or "none"
    pattern = signals["branch_pattern"]
    wt = worktree_path or "none"

    verdict_color = {"ACTIVE": "⚡", "STALE": "💤", "UNKNOWN": "❓"}.get(verdict, "")

    print(f"branch: {branch}")
    print(f"verdict: {verdict_color} {verdict}")
    print()
    print(f"  commits ahead:      {signals['commits_ahead']}")
    print(f"  uncommitted files:  {uncommitted}{age}")
    print(f"  open issues:        {issues_str}")
    print(f"  open PR:            {pr_str}")
    print(f"  spec doc:           {spec}")
    print(f"  branch pattern:     {pattern}")
    print(f"  worktree:           {wt}")
    print()
    print(f"Audit written to {cache_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: audit.py <branch-name-or-worktree-path>", file=sys.stderr)
        sys.exit(1)

    target = sys.argv[1]
    git_root = find_git_root()

    if not git_root:
        print(f"ERROR: Not inside a git repository.", file=sys.stderr)
        sys.exit(1)

    branch, worktree_path = resolve_branch(target, git_root)

    use_gh = gh_available()
    if not use_gh:
        print("Note: gh CLI not available — GitHub signals (PR, issues) will be skipped.\n")

    # Gather signals
    uncommitted_files = get_uncommitted_files(worktree_path, git_root)
    latest_mtime, _ = get_latest_mtime(uncommitted_files)

    signals = {
        "commits_ahead": get_commits_ahead(branch, git_root),
        "uncommitted_files": len(uncommitted_files),
        "latest_mtime": latest_mtime,
        "has_spec_doc": find_spec_doc(branch, git_root),
        "open_pr": get_open_pr(branch) if use_gh else None,
        "open_issues": get_linked_issues(branch, git_root) if use_gh else [],
        "branch_pattern": classify_branch_name(branch),
        "worktree": worktree_path,
    }

    verdict = compute_verdict(signals)
    cache_path = write_cache(git_root, branch, signals, verdict)
    print_report(branch, worktree_path, signals, verdict, cache_path)


if __name__ == "__main__":
    main()
