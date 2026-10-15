"""Imported animation clips share Apply, stable references and binary playback."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from infernux.core.animation_clip3d import AnimationClip3D, embedded_take_descriptors
from infernux.core.asset_types import MeshImportSettings, read_mesh_import_settings, read_meta_file
from infernux.core.assets import AssetManager
from infernux.lib import AssetRegistry
from model_test_support import remove_model_test_folder


def spec(name="Middle", identity="a" * 32):
    return {"id": identity, "name": name, "source_take": "Take", "start": .25, "end": .75}


@pytest.mark.parametrize("change", [
    {"id": "bad"}, {"name": ""}, {"source_take": ""}, {"start": -1},
    {"end": .1}, {"end": float("inf")}, {"start": True}, {"other": 1},
])
def test_invalid_clip_specs_are_not_accepted(change):
    settings = MeshImportSettings().to_dict()
    settings["animation_clips"] = [spec() | change]
    with pytest.raises(ValueError, match="animation_clips"):
        MeshImportSettings.from_dict(settings)


@pytest.mark.parametrize("durations", [[0], [0, 2]])
def test_add_clip_uses_a_nonzero_duration_source(monkeypatch, durations):
    from infernux.engine.ui import asset_details_renderer as renderer

    sources = [{"name": f"Take {i}", "duration": duration} for i, duration in enumerate(durations)]
    settings = MeshImportSettings(custom_animation_clips=True)
    state = SimpleNamespace(settings=settings, meta={"source_animations": json.dumps(sources)})
    buttons = []

    def button(label):
        buttons.append(label)
        return True

    ctx = SimpleNamespace(text_wrapped=lambda _: None, button=button,
                          record_semantic_item=lambda *_: None)
    monkeypatch.setattr(renderer, "_edit_import_settings", lambda state, key, edit, label: edit(state.settings))
    renderer._render_model_animation_clips(ctx, state)
    timed_sources = [source for source in sources if source["duration"] > 0]
    assert len(settings.animation_clips) == len(buttons) == bool(timed_sources)
    if timed_sources:
        assert settings.animation_clips[0]["source_take"] == timed_sources[0]["name"]
        assert settings.animation_clips[0]["end"] == timed_sources[0]["duration"]
        assert MeshImportSettings.from_dict(settings.to_dict()) == settings
        settings.custom_animation_clips = False
        state.meta = {"model_animations": json.dumps([{"id": "source-57616c6b", "name": "Walk", "duration": 1.0}])}
        ctx.separator = lambda: None
        ctx.label = lambda _text: None
        ctx.button = lambda label: "##extras_add_" in label
        renderer._render_model_animation_clips(ctx, state)
        assert settings.animation_clip_extras == [{"clip_id": "source-57616c6b", "curves": [], "events": [], "bone_mask": []}]


@pytest.fixture
def model(engine, tmp_path, monkeypatch):
    database = engine.get_asset_database()
    monkeypatch.setattr(AssetManager, "_engine", engine)
    monkeypatch.setattr(AssetManager, "_asset_database", database)
    source = Path(database.assets_root) / tmp_path.name / "Clips.fbx"
    source.parent.mkdir()
    source.write_bytes((Path(__file__).resolve().parents[2] /
                        "external/assimp/test/models/FBX/animation_with_skeleton.fbx").read_bytes())
    imported = AssetManager.import_asset(str(source), database=database)
    assert imported, imported.error
    try:
        yield database, source, imported.guid
    finally:
        remove_model_test_folder(database, source.parent)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_clip_apply_rename_reorder_and_failure_are_identity_safe(model, mode):
    database, source, guid = model
    registry = AssetRegistry.instance()
    settings = read_mesh_import_settings(str(source))
    metadata = read_meta_file(str(source))
    source_inventory = metadata["source_animations"]
    take = json.loads(source_inventory)[0]
    seconds = take["duration"]
    first = spec() | {"source_take": take["name"], "start": seconds * .25, "end": seconds * .75}
    second = spec("Opening", "b" * 32) | {"source_take": take["name"], "start": 0, "end": seconds * .25}
    source_descriptor = embedded_take_descriptors(metadata)[0]
    source_clip = AnimationClip3D.from_embedded_take_virtual_path(
        f"{source}::subanim:{source_descriptor['id']}"
    )
    assert source_clip and source_clip.name == take["name"]
    assert AnimationClip3D.from_embedded_take_virtual_path(f"{source}::subanim:0") is None
    settings.custom_animation_clips = True
    settings.animation_clips = [first, second]

    def apply():
        if mode == "sync":
            return AssetManager.reimport_asset(str(source), import_settings=settings.to_dict(), database=database)
        owner = AssetManager.begin_model_reimport(str(source), settings)
        until = time.monotonic() + 30
        while (result := AssetManager.poll_model_reimport(owner)) is None:
            assert time.monotonic() < until
            time.sleep(.002)
        return result

    result = apply()
    assert result, result.error
    virtual = f"{source}::subanim:{first['id']}"
    clip = AnimationClip3D.from_embedded_take_virtual_path(virtual)
    assert clip and clip.name == "Middle" and clip.take_name == first["id"]
    assert clip.duration_hint == pytest.approx(seconds * .5)
    assert clip.source_model_guid == guid
    assert AnimationClip3D.from_embedded_take_virtual_path(f"{source}::subanim:0") is None
    registry.invalidate_asset(guid)
    mesh = registry.load_mesh(str(source))
    assert mesh.skinned_animation_names == ["Middle", "Opening"]
    settings.animation_clips.reverse()
    settings.animation_clips[1]["name"] = "Renamed"
    result = apply()
    assert result, result.error
    clip = AnimationClip3D.from_embedded_take_virtual_path(virtual)
    assert clip.name == "Renamed" and clip.take_name == first["id"]
    assert read_meta_file(str(source))["source_animations"] == source_inventory
    before = Path(str(source) + ".meta").read_bytes()
    settings.animation_clips[0]["end"] = seconds + 1
    result = apply()
    assert not result and "exceeds source take duration" in result.error
    assert Path(str(source) + ".meta").read_bytes() == before
    assert mesh.skinned_animation_names == ["Opening", "Renamed"]
    assert AssetManager.reimport_asset(str(source), database=database)
    assert AnimationClip3D.from_embedded_take_virtual_path(virtual).take_name == first["id"]
    settings.animation_clips = []
    assert apply()
    assert registry.load_mesh(str(source)).skinned_animation_names == []
    assert embedded_take_descriptors(read_meta_file(str(source))) == []


def test_clip_extras_reject_missing_clip_without_partial_publication(model):
    database, source, _guid = model
    before = Path(str(source) + ".meta").read_bytes()
    settings = read_mesh_import_settings(str(source))
    settings.animation_clip_extras = [{
        "clip_id": "missing",
        "curves": [], "events": [], "bone_mask": [],
    }]

    result = AssetManager.reimport_asset(str(source), import_settings=settings.to_dict(), database=database)

    assert not result and "missing clip" in result.error
    assert Path(str(source) + ".meta").read_bytes() == before


def test_stable_source_clip_reference_survives_inventory_reordering(tmp_path, monkeypatch):
    from infernux.core import asset_types
    source = tmp_path / "clips.gltf"
    source.write_text("{}")
    published = [
        {"id": "source-" + name.encode().hex(), "guid": "d" * 32,
         "name": name, "duration": 1.0, "default_loop": True,
         "apply_root_motion": False, "reference_pose": "bind_pose",
         "curves": [], "events": [], "bone_mask": []}
        for name in ("Second", "First")
    ]
    metadata = {"guid": "c" * 32, "animation_names_csv": "Second,First",
                "model_animations": json.dumps(published)}
    monkeypatch.setattr(asset_types, "read_meta_file", lambda _: metadata)
    monkeypatch.setattr(asset_types, "read_meta_guid", lambda _: "c" * 32)
    reference = str(source) + "::subanim:source-" + b"First".hex()
    clip = AnimationClip3D.from_embedded_take_virtual_path(reference)
    assert clip.name == "First"
    metadata["model_animations"] = json.dumps(list(reversed(json.loads(metadata["model_animations"]))))
    assert AnimationClip3D.from_embedded_take_virtual_path(reference).take_name == clip.take_name
    published[1].pop("default_loop")
    metadata["model_animations"] = json.dumps(published)
    assert AnimationClip3D.from_embedded_take_virtual_path(reference) is None
    published[1]["default_loop"] = True
    metadata["model_animations"] = json.dumps(published)
    monkeypatch.setattr(asset_types, "read_meta_guid", lambda _: "")
    assert AnimationClip3D.from_embedded_take_virtual_path(reference) is None


def test_model_animation_settings_configure_copy_and_validate_the_current_document():
    from infernux.core.asset_types import mesh_import_settings_schema

    extra = {"clip_id": "source-Walk", "curves": [{"name": "Speed", "keys": [
        {"time_normalized": 0.0, "value": 1.0}, {"time_normalized": 1.0, "value": 3.0}]}],
        "events": [{"time_normalized": .5, "function": "step", "string_arg": "L", "number_arg": 1.0}],
        "bone_mask": ["Spine"]}
    settings = MeshImportSettings(custom_animation_clips=True, animation_clips=[spec()], animation_clip_extras=[extra],
        animation_sample_rate=60, animation_position_error=.001, animation_rotation_error=.25,
        animation_scale_error=.002, animation_loop_time=False, animation_apply_root_motion=True,
        animation_reference_pose="first_frame")
    declared = {item["name"]: item for item in mesh_import_settings_schema()["fields"]}
    for name in ("animation_sample_rate", "animation_position_error", "animation_rotation_error",
                 "animation_scale_error", "animation_loop_time", "animation_apply_root_motion", "animation_reference_pose"):
        assert declared[name]["page"] == "animation" and "default" in declared[name]
    restored = MeshImportSettings.from_dict(settings.to_dict())
    assert restored == settings
    clone = restored.copy()
    clone.animation_clips[0]["name"] = "Other"
    clone.animation_clip_extras[0]["curves"][0]["keys"][0]["value"] = 99
    assert restored.animation_clips[0]["name"] == "Middle"
    assert restored.animation_clip_extras[0]["curves"][0]["keys"][0]["value"] == 1.0
    incomplete = settings.to_dict()
    del incomplete["custom_animation_clips"], incomplete["animation_clips"]
    with pytest.raises(ValueError, match="complete current field set"):
        MeshImportSettings.from_dict(incomplete)
    incomplete = {key: value for key, value in settings.to_dict().items() if not key.startswith("animation_") or key == "animation_clips"}
    with pytest.raises(ValueError, match="complete current field set"):
        MeshImportSettings.from_dict(incomplete)
    for name, value in {"animation_sample_rate": 241, "animation_position_error": -1,
                        "animation_rotation_error": 181, "animation_scale_error": float("inf"),
                        "animation_reference_pose": "unknown"}.items():
        invalid = settings.to_dict()
        invalid[name] = value
        with pytest.raises(ValueError, match=name):
            MeshImportSettings.from_dict(invalid)
    invalid = settings.to_dict()
    invalid["animation_clip_extras"][0]["curves"][0]["keys"].reverse()
    with pytest.raises(ValueError, match="animation"):
        MeshImportSettings.from_dict(invalid)
    invalid = settings.to_dict()
    invalid["animation_clip_extras"][0]["events"][0]["function"] = ""
    with pytest.raises(ValueError, match="animation"):
        MeshImportSettings.from_dict(invalid)
    invalid = settings.to_dict()
    invalid["animation_clip_extras"][0]["bone_mask"].append("Spine")
    with pytest.raises(ValueError, match="animation"):
        MeshImportSettings.from_dict(invalid)


def test_model_clip_import_publishes_playback_settings_curves_events_and_mask(model):
    database, source, _guid = model
    metadata = read_meta_file(str(source))
    descriptor = embedded_take_descriptors(metadata)[0]
    bone = str(metadata["bone_names_csv"]).split(",")[0].strip()
    assert bone
    settings = read_mesh_import_settings(str(source))
    settings.animation_loop_time = False
    settings.animation_apply_root_motion = True
    settings.animation_reference_pose = "first_frame"
    settings.animation_clip_extras = [{"clip_id": descriptor["id"],
        "curves": [{"name": "FootPlant", "keys": [{"time_normalized": 0.0, "value": 0.0}, {"time_normalized": 1.0, "value": 1.0}]}],
        "events": [{"time_normalized": .5, "function": "footstep", "string_arg": "L", "number_arg": 2.0}], "bone_mask": [bone]}]
    result = AssetManager.reimport_asset(str(source), import_settings=settings.to_dict(), database=database)
    assert result, result.error
    published = embedded_take_descriptors(read_meta_file(str(source)))[0]
    clip = AnimationClip3D.from_embedded_take_virtual_path(f"{source}::subanim:{published['id']}")
    assert clip is not None
    assert clip.default_loop is published["default_loop"] is False
    assert clip.apply_root_motion is published["apply_root_motion"] is True
    assert clip.reference_pose == published["reference_pose"] == "first_frame"
    assert clip.sample_curve("FootPlant", .25) == pytest.approx(.25)
    assert clip.events[0].to_dict() == {"time_normalized": .5, "function": "footstep", "string_arg": "L", "number_arg": 2.0}
    assert clip.bone_mask == published["bone_mask"] == [bone]
    assert published["curves"][0]["name"] == "FootPlant" and published["events"][0]["function"] == "footstep"
