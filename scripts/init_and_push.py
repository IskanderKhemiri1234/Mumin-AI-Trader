#!/usr/bin/env python3
"""Safely push this project to an existing GitHub repository using gh auth.

This script never reads or stores a personal access token. It does not create
or overwrite a repository, and refuses to push if the target is not empty or
the remote does not match the requested owner/repository.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path, *, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=capture)
    if result.returncode:
        detail = result.stderr.strip() if capture else ""
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)} {detail}")
    return result.stdout.strip() if capture else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Push Mumin AI Trader to an existing GitHub repository")
    parser.add_argument("--repo", required=True, help="Existing repository in owner/name form")
    args = parser.parse_args()
    if "/" not in args.repo or args.repo.startswith("/") or args.repo.endswith("/"):
        raise ValueError("--repo must be owner/name")
    root = Path(__file__).resolve().parents[1]
    if not (root / "pyproject.toml").is_file():
        raise RuntimeError("Run this script from the Mumin AI Trader project")
    if not shutil.which("git") or not shutil.which("gh"):
        raise RuntimeError("Both git and GitHub CLI (gh) must be installed")
    run(["gh", "auth", "status"], root, capture=True)
    run(["gh", "repo", "view", args.repo], root, capture=True)

    if not (root / ".git").exists():
        run(["git", "init", "-b", "main"], root)
    remotes = run(["git", "remote"], root, capture=True).splitlines()
    if "origin" in remotes:
        current = run(["git", "remote", "get-url", "origin"], root, capture=True)
        expected = f"https://github.com/{args.repo}.git"
        if current.rstrip("/") not in {expected, expected.removesuffix(".git")}:
            raise RuntimeError(f"Refusing to push: origin points to {current}, not {expected}")
    else:
        run(["git", "remote", "add", "origin", f"https://github.com/{args.repo}.git"], root)

    run(["git", "add", "-A"], root)
    status = run(["git", "status", "--porcelain"], root, capture=True)
    if status:
        run(["git", "-c", "user.name=Mumin AI Trader", "-c",
             "user.email=mumin-ai-trader@users.noreply.github.com",
             "commit", "-m", "Update Mumin AI Trader"], root)
    run(["git", "push", "-u", "origin", "main"], root)
    print(f"Successfully pushed to https://github.com/{args.repo}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
