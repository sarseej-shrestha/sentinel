"""Check the exact staged snapshot before a milestone commit."""

import re
import subprocess
from pathlib import Path


def git(*args):
    return subprocess.check_output(["git", *args])


def check():
    assert git("config", "user.name").decode().strip() == "Sarseej Shrestha"
    email = git("config", "user.email").decode().strip()
    identity = git("var", "GIT_AUTHOR_IDENT").decode()
    assert identity.startswith(f"Sarseej Shrestha <{email}>")
    assert email, "An existing configured email is required"
    forbidden = (
        "data/raw/",
        "data/processed/",
        "data/profiles/",
        "data/training/",
        ".venv/",
        "models/",
        "artifacts/",
    )
    secrets = re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:ghp_|github_pat_|hf_)[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}"
    )
    paths = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode().split("\0")
    for name in filter(None, paths):
        assert not name.startswith(forbidden), f"Excluded artifact: {name}"
        assert Path(name).name not in {".env", "kaggle.json"}, f"Credential file: {name}"
        assert Path(name).suffix.lower() != ".md" or name in {
            "README.md",
            "docs/technical_spike.md",
        }, name
        blob = git("show", f":{name}")
        assert len(blob) < 2_000_000, f"Review large file: {name}"
        assert not secrets.search(blob), f"Possible credential: {name}"
    subprocess.run(["git", "diff", "--cached", "--check"], check=True)
    print("Staged files, credentials, file sizes, documentation scope and author checks passed.")


if __name__ == "__main__":
    check()
