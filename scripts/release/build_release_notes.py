"""Generate an English GitHub Release body from the root English changelog."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[2]
SIGNING_CREDIT = "Free code signing provided by [SignPath.io](https://signpath.io/), certificate by [SignPath Foundation](https://signpath.org/)."
SIGNING_POLICY = "https://infernux-engine.com/code-signing-policy.html"


def release_block(path: Path, version: str) -> str:
    source = path.read_text(encoding="utf-8")
    block = re.split(r"^\s*---\s*$", source, maxsplit=1, flags=re.M)[0].strip()
    if not block.startswith(f"# Infernux v{version} · "):
        raise ValueError(f"{path.name} must begin with the release being published: {version}")
    # Repository-relative language links do not resolve in GitHub Release bodies.
    block = re.sub(r"^\[.*\]\(UpdateLog(?:-zh)?\.md\)\s*$", "", block, flags=re.M)
    return block.strip()


def build_notes(root: Path, *, signed: bool) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    english = release_block(root / "UpdateLog.md", version)
    notice = (
        "The Windows Hub installer and full Hub archive have been signed through SignPath.\n\n" + SIGNING_CREDIT
        if signed else
        "**Windows signing: this release is unsigned.** SignPath approval is still pending; signing was explicitly disabled for this publication."
    )
    return f"{english}\n\n---\n\n### Windows code signing\n\n{notice}\n\n[Code signing policy]({SIGNING_POLICY})\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signed", choices=("true", "false"), required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_notes(ROOT, signed=args.signed == "true"), encoding="utf-8", newline="\n")
    print(f"Wrote English release notes to {args.output}")


if __name__ == "__main__":
    main()
