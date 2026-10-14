from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DAWN_LOCK = ROOT / "scripts" / "setup" / "dawn_dependencies.lock.json"


def test_dawn_dependency_lock_pins_exact_sources_commits_and_trees():
    payload = json.loads(DAWN_LOCK.read_text(encoding="utf-8"))
    dependencies = payload["dependencies"]

    assert payload["schema"] == "infernux.dawn_dependencies"
    assert re.fullmatch(r"[0-9a-f]{40}", payload["dawn_revision"])
    assert payload["dependency_count"] == len(dependencies)
    assert len({dependency["path"] for dependency in dependencies}) == len(dependencies)
    assert sum(
        dependency["transport"] == "official_gitiles_snapshot"
        for dependency in dependencies
    ) == payload["snapshot_dependency_count"]
    for dependency in dependencies:
        assert re.fullmatch(r"[0-9a-f]{40}", dependency["commit"])
        assert re.fullmatch(r"[0-9a-f]{40}", dependency["tree"])
        if dependency["transport"] == "github":
            assert dependency["source_url"].startswith("https://github.com/")
            assert dependency["deps_url"].startswith(
                "https://chromium.googlesource.com/external/github.com/"
            )
        else:
            assert dependency["source_url"] == dependency["deps_url"]
            assert dependency["source_url"].startswith(
                "https://chromium.googlesource.com/chromium/src/third_party/"
            )
