import json

import pytest

from infernux.core.assets import AssetManager
from infernux.renderstack.render_effect_asset import (
    EffectAssetReference,
    RenderEffectAsset,
    direct_effect_dependencies,
    dump_render_effect_document,
    parse_render_effect_document,
)


def test_render_effect_documents_consume_only_current_guid_fields():
    effect = parse_render_effect_document(
        {
            "$schema": "infernux.render_effect",
            "feature_type": "infernux.post.bloom",
            "parameters": {},
            "dependencies": [
                {
                    "guid": "shader-guid",
                    "path_hint": "Assets/Shaders/Stale.frag",
                    "legacy_path": "Shaders/Stale.frag",
                }
            ],
            "scope": "final",
            "mount": "route",
        }
    )
    group = parse_render_effect_document(
        {
            "$schema": "infernux.render_effect_group",
            "entries": [
                {
                    "entry_id": "bloom",
                    "asset": {
                        "guid": "effect-guid",
                        "path_hint": "Assets/Effects/Stale.effect",
                        "path": "Effects/Stale.effect",
                    },
                    "enabled": True,
                    "overrides": {},
                    "scope": "final",
                }
            ],
            "legacy_version": 1,
        }
    )

    assert json.loads(dump_render_effect_document(effect))["dependencies"] == [{"guid": "shader-guid"}]
    assert json.loads(dump_render_effect_document(group))["entries"][0]["asset"] == {"guid": "effect-guid"}

    assert set(json.loads(dump_render_effect_document(effect))) == {
        "$schema",
        "feature_type",
        "parameters",
        "dependencies",
    }
    assert set(json.loads(dump_render_effect_document(group))) == {"$schema", "entries"}


def test_path_only_document_references_do_not_query_catalog_or_create_edges(monkeypatch):
    class Database:
        @staticmethod
        def get_guid_from_path(_path):
            pytest.fail("document parsing must not resolve a path-only field")

    monkeypatch.setattr(AssetManager, "_asset_database", Database())
    effect = parse_render_effect_document(
        {
            "$schema": "infernux.render_effect",
            "feature_type": "infernux.post.bloom",
            "parameters": {},
            "dependencies": [
                {"guid": "", "path_hint": "Assets/Shaders/Legacy.frag"},
                {"path_hint": "Assets/Shaders/AlsoLegacy.frag"},
            ],
        }
    )
    group = parse_render_effect_document(
        {
            "$schema": "infernux.render_effect_group",
            "entries": [
                {
                    "entry_id": "legacy",
                    "asset": {"path_hint": "Assets/Rendering/Legacy.effect"},
                    "enabled": True,
                    "overrides": {},
                }
            ],
        }
    )

    assert direct_effect_dependencies(effect) == ()
    assert direct_effect_dependencies(group) == ()


def test_effect_reference_dump_is_stable_when_asset_path_moves(monkeypatch):
    current_path = ["Assets/Effects/Before.effect"]

    class Database:
        @staticmethod
        def get_guid_from_path(path):
            return "stable-guid" if str(path).replace("\\", "/") == current_path[0] else ""

    monkeypatch.setattr(AssetManager, "_asset_database", Database())
    before_reference = EffectAssetReference.from_author_path(current_path[0])
    before = dump_render_effect_document(RenderEffectAsset("infernux.post.bloom", dependencies=(before_reference,)))

    current_path[0] = "Assets/Effects/After.effect"
    after_reference = EffectAssetReference.from_author_path(current_path[0])
    after = dump_render_effect_document(RenderEffectAsset("infernux.post.bloom", dependencies=(after_reference,)))

    assert after == before
