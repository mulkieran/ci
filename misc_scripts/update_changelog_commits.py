#!/usr/bin/env python3
"""Update changelog with cherry-picked and new commits since a tag.

Usage: update-changelog-commits.py <tag> <changelog_file>
Example: update-changelog-commits.py v3.9.1 CHANGES.txt
"""

import argparse
import subprocess
import sys
from pathlib import Path


def run_git_command(args: list[str]) -> str:
    """Run a git command and return the output."""
    return subprocess.run(
        ["git"] + args, capture_output=True, text=True, check=True
    ).stdout.strip()


def verify_tag(tag: str):
    """Verify that a git tag exists."""
    return run_git_command(["rev-parse", tag])


def get_commits(
    tag: str, *, grep_pattern: str | None = None, invert_grep: bool = False
) -> list[str]:
    """Get commits since a tag, optionally filtering by grep pattern."""
    args = ["log", f"{tag}..HEAD", "--reverse", "--pretty=format:%s"]

    if grep_pattern:
        args.append(f"--grep={grep_pattern}")
        if invert_grep:
            args.append("--invert-grep")

    output = run_git_command(args)
    if output is None:
        raise RuntimeError(f"No result on stdout for command: '{args}'")

    return [line for line in output.split("\n") if line.strip()]


def format_changelog_section(cherry_picked: list[str], new_commits: list[str]) -> str:
    """Format the changelog section with cherry-picked and new commits."""
    lines = ["* Cherry-picked commits:"]

    for commit in cherry_picked:
        lines.append(f"  * {commit}")

    lines.append("")
    lines.append("* New commits:")

    for commit in new_commits:
        lines.append(f"  * {commit}")

    lines.append("")

    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:

    try:
        verify_tag(args.tag)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Could not verify tag '{args.tag}'") from e

    changelog_path = Path(args.changelog)
    if not changelog_path.exists():
        raise RuntimeError(f"Error: Changelog file '{args.changelog}' not found")

    cherry_picked = get_commits(args.tag, grep_pattern="cherry picked from commit")

    new_commits = get_commits(
        args.tag, grep_pattern="cherry picked from commit", invert_grep=True
    )

    output = format_changelog_section(cherry_picked, new_commits)

    existing_content = changelog_path.read_text()

    new_content = output + existing_content

    changelog_path.write_text(new_content)

    print(
        f"Updated {args.changelog} with {len(cherry_picked)} cherry-picked and {len(new_commits)} new commits"
    )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update changelog with cherry-picked and new commits since a tag"
    )
    parser.add_argument("tag", help="Git tag to compare against")
    parser.add_argument("changelog", help="Path to changelog file")

    args = parser.parse_args()

    try:
        return run(args)
    except Exception as e:
        sys.exit(str(e))


if __name__ == "__main__":
    sys.exit(main())
