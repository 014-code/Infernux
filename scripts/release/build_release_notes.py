"""Generate a GitHub Release body from the authoritative bilingual changelogs."""
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
    # The release contains both languages; repository-relative links would be
    # broken when GitHub renders the same Markdown outside the source tree.
    block = re.sub(r"^\[.*\]\(UpdateLog(?:-zh)?\.md\)\s*$", "", block, flags=re.M)
    return block.strip()


def build_notes(root: Path, *, signed: bool) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    english = release_block(root / "UpdateLog.md", version)
    chinese = release_block(root / "UpdateLog-zh.md", version)
    notice = (
        "The Windows Hub installer and full Hub archive have been signed through SignPath.\n\n" + SIGNING_CREDIT
        if signed else
        "**Windows signing: this release is unsigned.** SignPath approval is still pending; signing was explicitly disabled for this publication.\n\n"
        "**Windows 签名：本期为未签名发行包。** SignPath 尚在审核，本次发布明确跳过签名。"
    )
    return f"{english}\n\n---\n\n{chinese}\n\n---\n\n### Windows code signing / Windows 代码签名\n\n{notice}\n\n[Code signing policy / 签名政策]({SIGNING_POLICY})\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signed", choices=("true", "false"), required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_notes(ROOT, signed=args.signed == "true"), encoding="utf-8", newline="\n")
    print(f"Wrote bilingual release notes to {args.output}")


if __name__ == "__main__":
    main()
