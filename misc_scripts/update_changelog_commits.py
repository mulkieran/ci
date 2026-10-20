#!/usr/bin/env python3
"""Update changelog with cherry-picked and new commits since a tag.

Usage: update-changelog-commits.py <tag> <changelog_file>
Example: update-changelog-commits.py v3.9.1 CHANGES.txt
"""

import argparse
import subprocess
import sys
from os import linesep
from pathlib import Path


def run_git_command(args: list[str]) -> str | None:
    """Run a git command and return the output."""
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return None


def verify_tag(tag: str) -> bool:
    """Verify that a git tag exists."""
    result = run_git_command(["rev-parse", tag])
    return result is not None


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
    if output:
        return [line for line in output.split("\n") if line.strip()]
    return []


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update changelog with cherry-picked and new commits since a tag"
    )
    parser.add_argument("tag", help="Git tag to compare against")
    parser.add_argument("changelog", help="Path to changelog file")

    args = parser.parse_args()

    # Verify tag exists
    if not verify_tag(args.tag):
        print(f"Error: Tag '{args.tag}' not found", file=sys.stderr)
        return 1

    # Verify changelog file exists
    changelog_path = Path(args.changelog)
    if not changelog_path.exists():
        print(f"Error: Changelog file '{args.changelog}' not found", file=sys.stderr)
        return 1

    # Get cherry-picked commits
    cherry_picked = get_commits(args.tag, grep_pattern="cherry picked from commit")

    # Get new commits
    new_commits = get_commits(
        args.tag, grep_pattern="cherry picked from commit", invert_grep=True
    )

    # Build the formatted section
    output = format_changelog_section(cherry_picked, new_commits)

    # Read existing changelog
    existing_content = changelog_path.read_text(encoding="utf-8")

    # Prepend new section to changelog
    new_content = output + linesep + existing_content

    # Write updated changelog
    changelog_path.write_text(new_content, encoding="utf-8")

    print(
        f"Updated {args.changelog} with {len(cherry_picked)} cherry-picked and {len(new_commits)} new commits"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
