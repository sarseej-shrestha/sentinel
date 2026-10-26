"""Run whole-repository release checks without installing or downloading anything."""

import argparse
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true", help="Use an already installed Chromium")
    args = parser.parse_args()
    commands = [
        [sys.executable, "-m", "pytest", "-q"],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [sys.executable, "-m", "pip", "check"],
        [sys.executable, "-m", "compileall", "-q", "src", "scripts", "tests"],
        ["npm", "--prefix", "frontend", "run", "typecheck"],
        ["npm", "--prefix", "frontend", "run", "lint"],
        ["npm", "--prefix", "frontend", "run", "format:check"],
        ["npm", "--prefix", "frontend", "run", "build"],
        ["git", "diff", "--check"],
    ]
    if args.browser:
        commands.append(["npm", "--prefix", "frontend", "run", "test:e2e"])
    failed = []
    for command in commands:
        started = time.perf_counter()
        print("\n$", " ".join(command), flush=True)
        result = subprocess.run(command, check=False)
        elapsed = time.perf_counter() - started
        print(f"{'PASS' if result.returncode == 0 else 'FAIL'} ({elapsed:.2f}s)", flush=True)
        if result.returncode:
            failed.append(command)
    if failed:
        raise SystemExit(f"{len(failed)} release checks failed")


if __name__ == "__main__":
    main()
