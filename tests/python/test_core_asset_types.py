"""Tests for infernux.core.asset_types — enums, dataclasses, meta file helpers."""

from __future__ import annotations

import builtins
import json

import pytest

from infernux.core.asset_types import (
    AudioCompressionFormat,
    AudioImportSettings,
    FilterMode,
    FontAssetInfo,
    MeshImportSettings,
    ShaderAssetInfo,
    SpriteFrame,
    TextureImportSettings,
    TextureCompression,
    TextureCompressionQuality,
    TextureFormat,
    TextureType,
    WrapMode,
    _load_strict_meta_root,
    asset_category_from_extension,
    read_texture_import_settings,
)



def test_import_settings_preserve_a_concurrent_sidecar_update(tmp_path, monkeypatch):
    from infernux.core import asset_types

    asset = tmp_path / "texture.png"
    sidecar = tmp_path / "texture.png.meta"
    document = {"metadata": {
        "guid": {"type": "string", "value": "1" * 32},
        "file_path": {"type": "string", "value": "Assets/texture.png"},
        "srgb": {"type": "bool", "value": True},
    }}
    sidecar.write_text(json.dumps(document), encoding="utf-8")
    read_root = asset_types._load_strict_meta_root
    external_bytes = b'{"metadata":{"guid":{"type":"string","value":"22222222222222222222222222222222"}}}\n'

    def concurrent_checkout(path):
        original = read_root(path)
        sidecar.write_bytes(external_bytes)
        return original

    monkeypatch.setattr(asset_types, "_load_strict_meta_root", concurrent_checkout)
    assert asset_types.write_meta_fields(str(asset), {"srgb": False}) is False
    assert sidecar.read_bytes() == external_bytes

    monkeypatch.setattr(asset_types, "_load_strict_meta_root", read_root)
    assert asset_types.write_meta_fields(str(asset), {"srgb": False}) is True
    updated = json.loads(sidecar.read_text(encoding="utf-8"))["metadata"]
    assert updated["guid"]["value"] == "2" * 32
    assert updated["srgb"]["value"] is False


def test_queued_sidecar_edits_preserve_all_fields(tmp_path):
    from infernux.core import asset_types

    asset = tmp_path / "texture.png"
    sidecar = tmp_path / "texture.png.meta"
    sidecar.write_text('{"metadata":{}}', encoding="utf-8")
    edits = [
        asset_types.write_meta_fields_async(str(asset), {f"setting_{index}": index})
        for index in range(16)
    ]
    assert all(edit.result(timeout=5) is True for edit in edits)
    fields = json.loads(sidecar.read_text(encoding="utf-8"))["metadata"]
    assert {name: field["value"] for name, field in fields.items()} == {
        f"setting_{index}": index for index in range(16)
    }


def test_runtime_metadata_replaces_omitted_meta_sidecar(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from infernux.core import asset_types

    document = {
        "metadata": {
            "guid": {"type": "string", "value": "texture-guid"},
            "width": {"type": "int", "value": 256},
            "height": {"type": "int", "value": 128},
            "texture_type": {"type": "string", "value": "sprite"},
            "sprite_frames": {"type": "json_array", "value": []},
        }
    }
    database = SimpleNamespace(
        get_meta_by_guid=lambda guid: SimpleNamespace(
            serialize_document=lambda: document
        ) if guid == "texture-guid" else None,
        get_meta_by_path=lambda _path: None,
    )
    monkeypatch.setattr(asset_types, "_published_asset_database", lambda: database)

    metadata = asset_types.read_asset_metadata(
        str(tmp_path / "Assets" / "sheet.png"),
        guid="texture-guid",
    )

    assert metadata["width"] == 256
    assert metadata["height"] == 128
    assert metadata["texture_type"] == "sprite"
    assert metadata["sprite_frames"] == []


def test_defaults_and_inspector_project_native_schema():
    from infernux.core.asset_types import mesh_import_settings_schema
    from infernux.engine.ui import asset_details_renderer as inspector

    schema = mesh_import_settings_schema()
    defaults = MeshImportSettings().to_dict()
    assert defaults == {item["name"]: item["default"] for item in schema["fields"]}
    inspector._ensure_categories()
    fields = inspector._categories["mesh"].editable_fields
    custom_fields = {
        "rig_root_node", "skeleton_definition_guid", "skeleton_definition_id",
        "exposed_bones", "humanoid_bone_overrides",
    }
    model_fields = [item for item in schema["fields"] if item["type"] not in {
        "material_remaps", "animation_clips", "animation_clip_extras"
    } and item["name"] not in custom_fields]
    assert [item.key for item in fields] == [item["name"] for item in model_fields]
    for actual, declared in zip(fields, model_fields):
        assert actual.label == declared["label"]
        assert actual.field_type.value == {"bool": "checkbox", "float": "float", "int": "int", "enum": "combo"}[declared["type"]]
    for page in ("model", "rig", "animation"):
        assert [item.key for item in inspector._model_page_fields(page)] == [
            item["name"] for item in schema["fields"]
            if item["page"] == page
            and item["type"] not in {"animation_clips", "animation_clip_extras"}
            and item["name"] not in custom_fields
        ]
    # Authoring clients cannot change the next client's contract or defaults.
    schema["fields"][0]["default"] = -7
    assert mesh_import_settings_schema()["fields"][0]["default"] == defaults["scale_factor"]
    assert MeshImportSettings().to_dict() == defaults


def test_missing_thread_executor_does_not_block_asset_model_imports(
    monkeypatch
):
    from infernux.core import asset_types

    original_import = builtins.__import__

    def import_without_thread_executor(name, *args, **kwargs):
        if name == "concurrent.futures":
            raise ImportError("thread executor is unavailable")
        return original_import(name, *args, **kwargs)

    previous_pool = asset_types._io_pool
    asset_types._io_pool = None
    monkeypatch.setattr(builtins, "__import__", import_without_thread_executor)
    try:
        with pytest.raises(
            RuntimeError,
            match="Asynchronous asset writes are unavailable",
        ):
            asset_types._asset_io_pool()
    finally:
        asset_types._io_pool = previous_pool


def test_document_is_strict_and_transactional():
    from infernux.lib import ResourceMeta

    valid = {
        "metadata": {
            "guid": {"type": "string", "value": "strict-guid"},
            "resource_type": {
                "type": "enum infernux::ResourceType",
                "value": "DefaultText",
            },
        },
    }
    meta = ResourceMeta()
    meta.deserialize_document(valid)
    assert meta.serialize_document() == valid

    invalid_documents = []
    unknown_field = json.loads(json.dumps(valid))
    unknown_field["legacy_guid"] = "strict-guid"
    invalid_documents.append(unknown_field)
    wrong_value_type = json.loads(json.dumps(valid))
    wrong_value_type["metadata"]["guid"]["value"] = ["strict-guid"]
    invalid_documents.append(wrong_value_type)

    for invalid in invalid_documents:
        with pytest.raises(ValueError):
            meta.deserialize_document(invalid)
        assert meta.serialize_document() == valid


def test_import_enum_storage_and_lookup_contract():
    assert [int(value) for value in TextureType] == list(range(7))
    assert [int(value) for value in AudioCompressionFormat] == [0, 1, 2]
    for enum_type in (TextureCompression, TextureCompressionQuality, TextureFormat):
        assert {enum_type.from_string(value.to_string()) for value in enum_type} == set(enum_type)
    assert WrapMode.from_string("mirror") == WrapMode.MIRROR
    assert WrapMode.from_string("unknown") == WrapMode.REPEAT
    assert WrapMode.CLAMP.to_string() == "clamp"
    assert FilterMode.from_string("nearest") == FilterMode.POINT
    assert FilterMode.from_string("trilinear") == FilterMode.TRILINEAR
    assert FilterMode.from_string("unknown") == FilterMode.BILINEAR
    assert FilterMode.BILINEAR.to_string() == "linear"


def test_texture_import_settings_roundtrip_copy_and_format_precedence():
    original = TextureImportSettings()
    assert (original.max_size, original.aniso_level, original.srgb) == (2048, -1, True)
    settings = TextureImportSettings(
        texture_type=TextureType.NORMAL_MAP, wrap_mode=WrapMode.CLAMP,
        filter_mode=FilterMode.TRILINEAR, generate_mipmaps=False, srgb=False,
        max_size=1024, aniso_level=4, compression=TextureCompression.BC5,
        compression_quality=TextureCompressionQuality.HIGH,
    )
    assert TextureImportSettings.from_dict(settings.to_dict()) == settings
    copied = settings.copy()
    copied.max_size = 512
    assert settings.max_size == 1024 and copied != settings
    assert TextureImportSettings().__eq__("other") is NotImplemented
    assert TextureImportSettings.from_dict(original.to_dict()).aniso_level == -1
    document = original.to_dict()
    document.update(aniso_level=1, texture_format="rgba4444", texture_compression="bc1")
    loaded = TextureImportSettings.from_dict(document)
    assert loaded.aniso_level == 1
    assert loaded.format == TextureFormat.RGBA4444
    assert loaded.compression == TextureCompression.NONE


def test_sprite_import_requires_unique_persisted_frame_identities(tmp_path):
    frame = SpriteFrame(name="idle", x=0, y=0, w=64, h=64)
    assert len(frame.stable_id) == 32
    assert SpriteFrame.from_dict(frame.to_dict()) == frame
    with pytest.raises(ValueError, match="at least one sprite frame"):
        TextureImportSettings(texture_type=TextureType.SPRITE).to_dict()
    settings = TextureImportSettings(texture_type=TextureType.SPRITE, sprite_frames=[
        frame, SpriteFrame(stable_id=frame.stable_id, name="duplicate", x=64, w=64, h=64),
    ])
    with pytest.raises(ValueError, match="must be unique"):
        settings.to_dict()
    meta = tmp_path / "sprite.png.meta"
    meta.write_text(json.dumps({"metadata": {
        "sprite_frames": {"type": "string", "value": "[]"},
    }}), encoding="utf-8")
    with pytest.raises(TypeError, match="sprite_frames must use json_array"):
        _load_strict_meta_root(str(meta))


@pytest.mark.parametrize("extension,kind", [
    (".inxvfield", TextureType.VECTOR_FIELD), (".inxsdf", TextureType.SDF),
])
def test_volume_extensions_publish_linear_uncompressed_settings(tmp_path, extension, kind):
    settings = read_texture_import_settings(str(tmp_path / ("volume" + extension)))
    assert settings.texture_type == kind and not settings.srgb
    assert settings.format == TextureFormat.RGBA16_FLOAT
    assert settings.compression == TextureCompression.NONE
    if kind == TextureType.SDF:
        assert not settings.generate_mipmaps and settings.wrap_mode == WrapMode.CLAMP
    assert TextureImportSettings.from_dict(settings.to_dict()) == settings


def test_audio_import_settings_roundtrip_copy_and_load_policy():
    defaults = AudioImportSettings()
    assert not defaults.force_mono and defaults.quality == 1
    assert defaults.compression_format == AudioCompressionFormat.PCM
    settings = AudioImportSettings(
        force_mono=True, quality=0.5, compression_format=AudioCompressionFormat.VORBIS,
        load_type="streaming",
    )
    assert AudioImportSettings.from_dict(settings.to_dict()) == settings == settings.copy()
    copied = settings.copy()
    copied.force_mono = False
    assert settings.force_mono and copied != settings
    document = settings.to_dict()
    del document["load_type"]
    assert AudioImportSettings.from_dict(document).load_type == "decompress_on_load"
    document["load_type"] = "automatic_guess"
    with pytest.raises(ValueError, match="load_type"):
        AudioImportSettings.from_dict(document)


def test_mesh_import_settings_roundtrip_copy_and_current_schema():
    settings = MeshImportSettings(
        normal_smoothing_angle=30, weld_vertices=False, sort_hierarchy_by_name=True,
        import_visibility=False, import_blend_shapes=False, mesh_compression="high",
        index_format="uint32", normal_mode="calculate", tangent_mode="source_only",
        normal_smoothing_source="source", flip_uvs=True, optimize_mesh=False,
    )
    assert MeshImportSettings.from_dict(settings.to_dict()) == settings
    copied = settings.copy()
    copied.optimize_mesh = True
    assert not settings.optimize_mesh and copied != settings
    document = settings.to_dict()
    document.update(guid="mesh-guid", bone_count=14, animation_names_csv="Armature|Action")
    assert MeshImportSettings.from_dict(document) == settings
    del document["weld_vertices"]
    with pytest.raises(ValueError, match="complete current field set"):
        MeshImportSettings.from_dict(document)


@pytest.mark.parametrize("key,value,error", [
    ("normal_smoothing_angle", -1, ValueError),
    ("normal_smoothing_angle", 176, ValueError),
    ("normal_smoothing_angle", float("nan"), ValueError),
    ("normal_smoothing_angle", True, ValueError),
    ("sort_hierarchy_by_name", 1, TypeError),
    ("import_visibility", None, TypeError),
    ("weld_vertices", "false", TypeError),
    ("normal_smoothing_source", "fallback", ValueError),
    ("normal_mode", True, ValueError),
    ("normal_weighting", "auto", ValueError),
    ("tangent_algorithm", 4, ValueError),
    ("mesh_compression", "maximum", ValueError),
    ("index_format", "uint8", ValueError),
    ("rig_type", "legacy", ValueError),
])
def test_mesh_document_rejects_distinct_invalid_setting_categories(key, value, error):
    document = MeshImportSettings().to_dict()
    document[key] = value
    with pytest.raises(error, match=key):
        MeshImportSettings.from_dict(document)


def test_asset_extensions_share_published_categories_and_supported_shader_stages():
    expected = {
        ".mat": "material", ".png": "texture", ".PNG": "texture", ".jpe": "texture",
        ".vert": "shader", ".frag": "shader", ".wav": "audio", ".ogg": "audio",
        ".mp3": "audio", ".flac": "audio", ".ttf": "font", ".fbx": "mesh",
        ".gltf": "mesh", ".blend": "mesh", ".prefab": "prefab",
        ".particlegraph": "particle_graph", ".xyz": None,
    }
    assert {ext: asset_category_from_extension(ext) for ext in expected} == expected
    vertex = ShaderAssetInfo.from_path("test.vert", guid="vertex-guid")
    assert vertex.shader_type == "vertex" and vertex.guid == "vertex-guid"
    assert ShaderAssetInfo.from_path("test.frag").shader_type == "fragment"
    assert ShaderAssetInfo.from_path("test.comp").shader_type == "unknown"
    assert ShaderAssetInfo.from_path("test.txt").shader_type == "unknown"
    assert FontAssetInfo.from_path("font.ttf").font_type == "truetype"
    assert FontAssetInfo.from_path("font.otf").font_type == "opentype"
    assert FontAssetInfo.from_path("font.woff").font_type == "unknown"


def test_public_meta_write_preserves_typed_values_and_rejects_partial_invalid_updates(tmp_path):
    from infernux.core.asset_types import read_meta_guid, write_meta_fields

    asset = tmp_path / "asset.png"
    meta = tmp_path / "asset.png.meta"
    meta.write_text('{"metadata":{}}', encoding="utf-8")
    assert write_meta_fields(str(asset), {
        "guid": "abc123", "srgb": True, "width": 42, "scale": 0.5, "label": "authored",
    })
    fields = json.loads(meta.read_text(encoding="utf-8"))["metadata"]
    assert {key: value["type"] for key, value in fields.items()} == {
        "guid": "string", "srgb": "bool", "width": "int", "scale": "float", "label": "string",
    }
    assert read_meta_guid(str(asset)) == "abc123"
    saved = meta.read_bytes()
    assert not write_meta_fields(str(asset), {"label": "must not publish", "bad": None})
    assert meta.read_bytes() == saved
    meta.write_text('{"guid":"legacy-root-guid"}', encoding="utf-8")
    assert read_meta_guid(str(asset)) == ""
