"""Standalone official releases survive offline catalog regeneration."""

import importlib.util
import json
from pathlib import Path

import pytest

from infernux.plugins.official import OfficialCatalogError


@pytest.fixture
def catalog_build(tmp_path):
    script = Path(__file__).resolve().parents[2] / "external/plugins/build_official_plugins.py"
    spec = importlib.util.spec_from_file_location("official_plugin_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / "sources"
    package = source / "fixture/package"
    package.mkdir(parents=True)
    (package / "inx_package.json").write_text(json.dumps({
        "reference": "fixture/editor", "version": "1.0.0", "name": "Fixture",
        "engine": ">=0.4.3",
    }), encoding="utf-8")
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({
        "$schema": "infernux.official_plugin_sources", "plugins": [{
            "path": "fixture", "category": "editor_tools",
            "repository": "https://github.com/InfernuxEngine/fixture",
        }],
    }), encoding="utf-8")
    standalone = {
        "reference": "fixture/animation", "name": "Animation", "version": "1.0.0",
        "engine": ">=0.4.3", "category": "animation", "artifact": "fixture.animation.inxpkg",
        "repository": "https://github.com/InfernuxEngine/animation",
        "source": {"type": "url", "location": (
            "https://downloads.infernux-engine.com/plugins/fixture.animation/1.0.0/fixture.animation.inxpkg"
        )},
    }
    releases = tmp_path / "releases.json"
    output = tmp_path / "output"

    def build(entries):
        releases.write_text(json.dumps({
            "$schema": "infernux.official_plugin_registry", "packages": entries,
        }), encoding="utf-8")
        builder.build(source, output, sources, releases)
        return output / "official-registry.json"

    return build, standalone, output


def test_offline_build_retains_standalone_releases(catalog_build):
    build, release, output = catalog_build
    first = build([release]).read_bytes()
    assert build([release]).read_bytes() == first
    document = json.loads(first)
    assert document["packages"][0] == release
    assert [item["reference"] for item in document["packages"]] == ["fixture/animation", "fixture/editor"]
    assert list(output.glob("*.inxpkg")) == []
    assert json.loads((output / "default-libraries.json").read_text())["libraries"] == []


@pytest.mark.parametrize("failure", ["duplicate", "invalid_source"])
def test_bad_release_metadata_preserves_previous_catalog(catalog_build, failure):
    build, release, _ = catalog_build
    path = build([release])
    previous = path.read_bytes()
    if failure == "duplicate":
        release["reference"] = "fixture/editor"
    else:
        release["source"] = {"type": "local", "location": "unpublished.inxpkg"}
    with pytest.raises((RuntimeError, OfficialCatalogError)):
        build([release])
    assert path.read_bytes() == previous
