#!/usr/bin/env python3
import subprocess
import sys


def find_multiclaude_worktrees() -> list[str]:
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        line[len("worktree "):]
        for line in result.stdout.splitlines()
        if line.startswith("worktree ") and "/multiclaude-" in line
    ]


def remove_worktrees(paths: list[str]) -> None:
    if not paths:
        print("No worktrees to remove.", file=sys.stderr)
        return
    print(f"Removing {len(paths)} worktree(s):", file=sys.stderr)
    for p in paths:
        print(f"  {p}", file=sys.stderr)
        subprocess.run(
            ["git", "worktree", "remove", "-f", "-f", p],
            check=False,
        )


def main():
    remove_worktrees(find_multiclaude_worktrees())


if __name__ == "__main__":
    main()
