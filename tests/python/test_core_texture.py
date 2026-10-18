"""Tests for infernux.core.texture — Texture wrapper (real C++ backend)."""

from __future__ import annotations

import pytest

from infernux.core.texture import Texture
from infernux.lib import TextureLoader


# ═══════════════════════════════════════════════════════════════════════════
# Texture creation via real C++ TextureLoader
# ═══════════════════════════════════════════════════════════════════════════

class TestTextureSolidColor:
    def test_create_solid_color(self):
        tex = Texture.solid_color(4, 4, 255, 0, 0, 255)
        assert tex is not None
        assert tex.width == 4
        assert tex.height == 4

    def test_create_solid_color_channels(self):
        tex = Texture.solid_color(2, 2, 128, 64, 32, 255)
        assert tex is not None
        assert tex.channels == 4


class TestTextureCheckerboard:
    def test_create_checkerboard(self):
        tex = Texture.checkerboard(8, 8)
        assert tex is not None
        assert tex.width == 8
        assert tex.height == 8


# ═══════════════════════════════════════════════════════════════════════════
# Texture.__init__ validation
# ═══════════════════════════════════════════════════════════════════════════

class TestTextureInit:
    def test_none_raises(self):
        with pytest.raises(ValueError):
            Texture(None)

    def test_wraps_native(self):
        native = TextureLoader.create_solid_color(8, 8, 255, 255, 255, 255)
        tex = Texture(native)
        assert tex.width == 8
        assert tex.height == 8


# ═══════════════════════════════════════════════════════════════════════════
# Texture properties
# ═══════════════════════════════════════════════════════════════════════════

class TestTextureProperties:
    def test_width_height_channels(self):
        tex = Texture.solid_color(16, 32, 0, 0, 0, 255)
        assert tex.width == 16
        assert tex.height == 32
        assert tex.channels == 4

    def test_name_is_string(self):
        tex = Texture.solid_color(4, 4, 255, 0, 0, 255)
        assert isinstance(tex.name, str)


def test_managed_texture_pixels_use_imported_artifacts_without_source_files(engine, monkeypatch):
    from pathlib import Path
    from infernux.core.assets import AssetManager
    from infernux.lib import ResourceType

    for name in ("_engine", "_asset_database", "_registry"):
        monkeypatch.setattr(AssetManager, name, getattr(AssetManager, name))
    AssetManager.initialize(engine)
    database = engine.get_asset_database()
    source = Path(database.assets_root) / "managed-pixels.ppm"
    source.write_bytes(b"P3\n2 1\n255\n255 0 0 0 0 255\n")
    imported = database.import_asset(str(source))
    assert imported, imported.error
    artifact = database.get_runtime_artifact_path(imported.guid, ResourceType.Texture)
    assert artifact.endswith(".inxtex")
    source.unlink()
    try:
        texture = AssetManager.load_by_guid(imported.guid, Texture)
        assert texture.size == (2, 1) and texture.channels == 4
        assert texture.guid == imported.guid
        assert texture.pixels_as_bytes() == bytes(texture.pixels_as_list())
        assert len(texture.pixels_as_bytes()) == 8
        assert AssetManager.load_by_guid(imported.guid, Texture) is texture
    finally:
        database.delete_asset(str(source))
        AssetManager.flush()
