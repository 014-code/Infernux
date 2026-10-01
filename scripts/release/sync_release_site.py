"""Synchronize public website metadata after the release artifacts are published.

Run after build_release_catalog.py. Historic downloads and API snapshots are
retained. Current download URLs always come from the measured release catalog.
"""
from __future__ import annotations

import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main():
    os.chdir(ROOT)
    release = read_json(DOCS / "release.json")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if release["version"] != version or not release.get("published_at") or not release.get("assets"):
        raise SystemExit("Public website synchronization requires the published, populated release catalog")
    manifest = read_json(DOCS / "docs-manifest.json")
    previous = manifest["documented_release"]
    manifest.update(documented_release=version, last_verified=release["published_at"][:10], release_status="stable")
    manifest["indexes"]["changelog"] = "/changelog.html"
    write_json(DOCS / "docs-manifest.json", manifest)
    support = read_json(DOCS / "platform-support.json")
    support["released_version"] = version
    write_json(DOCS / "platform-support.json", support)

    # Maintain publication dates when release.json moves on to a later version.
    history_path = DOCS / "changelog/history.json"
    history = read_json(history_path)
    record = {"tag_name": f"v{version}", "published_at": release["published_at"], "html_url": release["release_url"],
              "body": re.split(r"^\s*---\s*$", (ROOT / "UpdateLog.md").read_text(encoding="utf-8"), maxsplit=1, flags=re.M)[0].strip()}
    history = [record] + [item for item in history if item["tag_name"] != record["tag_name"]]
    write_json(history_path, history)

    for path in [ROOT / "README.md", ROOT / "README-zh.md", *DOCS.glob("*.html"),
                 DOCS / "wiki/docs/en/api/index.md", DOCS / "wiki/docs/zh/api/index.md"]:
        if path.name in {"download.html", "changelog.html"}:
            continue
        source = path.read_text(encoding="utf-8")
        source = source.replace(previous, version)
        path.write_text(source, encoding="utf-8", newline="\n")
    i18n_path = DOCS / "tools/i18n-source.json"
    translations = read_json(i18n_path)
    for lang, dictionary in translations.items():
        for key, value in dictionary.items():
            dictionary[key] = value.replace(previous, version)
        authored = (ROOT / ("UpdateLog.md" if lang == "en" else "UpdateLog-zh.md")).read_text(encoding="utf-8")
        title = authored.split("\n", 1)[0].partition(" · ")[2]
        dictionary["roadmap.release.next.title"] = title
        dictionary["roadmap.release.next.tag"] = f"{version} · " + ("PUBLISHED" if lang == "en" else "已发布")
        dictionary["roadmap.release.next.item1"] = authored.split("\n\n")[1]
        dictionary["roadmap.release.next.item2"] = ("Read the Changelog for features, fixes, platform boundaries and upgrade notes." if lang == "en" else "详细能力、问题修复、平台边界与升级须知请查看更新日志。")
    i18n_path.write_text(json.dumps(translations, ensure_ascii=False, indent=4) + "\n", encoding="utf-8", newline="\n")

    download_path = DOCS / "download.html"
    download = download_path.read_text(encoding="utf-8")
    download = "\n".join(line if "<option " in line else line.replace(previous, version) for line in download.split("\n"))
    options = []
    download = re.sub(
        rf'<option\b[^>]*>{re.escape(version)} · (?:Windows|Linux) x64 · CPython 3\.13</option>',
        "", download,
    )
    for asset in release["assets"]:
        if asset["kind"] != "python-wheel":
            continue
        platform = "Windows" if "win_amd64" in asset["name"] else "Linux"
        options.append(f'<option value="{html.escape(asset["url"], quote=True)}">{version} · {platform} x64 · CPython 3.13</option>')
    for option in options:
        if option not in download:
            download = re.sub(r'(<select [^>]*data-version-select>)', lambda match: match[1] + "\n                                " + option, download)
    download_path.write_text(download, encoding="utf-8", newline="\n")

    def run(*args):
        subprocess.run(args, check=True, cwd=ROOT)

    run("node", "docs/tools/apply-api-curation.mjs")
    run("node", "docs/tools/build-api-index.mjs")
    run("node", "docs/tools/build-api-diff.mjs", "--record-current")
    run("node", "docs/tools/build-release-notes.mjs")
    run("node", "docs/tools/normalize-i18n-fallbacks.mjs")
    run(sys.executable, "docs/tools/build-learning-guides.py")
    run(sys.executable, "docs/tools/build-changelog.py")
    run("node", "docs/tools/build-i18n.mjs")
    os.environ["DOCS_SOURCE_COMMIT"] = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    run("node", "docs/tools/stamp-docs-manifest.mjs")
    os.environ["SOURCE_DATE_EPOCH"] = subprocess.check_output(["node", "docs/tools/docs-source-date-epoch.mjs"], text=True).strip()
    run(sys.executable, "-m", "mkdocs", "build", "--strict", "--clean", "-f", "docs/wiki/mkdocs.yml")
    run("node", "docs/tools/optimize-static-site.mjs")
    run("node", "docs/tools/build-sitemap.mjs")
    run("node", "docs/tools/build-service-worker.mjs")
    run("node", "docs/tools/verify-site.mjs")
    run("node", "docs/tools/check-static-budget.mjs")


if __name__ == "__main__":
    main()
