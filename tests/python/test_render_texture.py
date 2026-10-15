"""Public RenderTexture argument and ownership contract; real GPU coverage is native."""

from types import SimpleNamespace

import pytest
from infernux.application import Application
from infernux.core import RenderTexture
from infernux.lib import PixelFormat
from infernux.rendergraph.graph import RenderGraph


@pytest.mark.parametrize("size", [(0, 1)])
def test_size_requires_positive_integer_pixels(size):
    with pytest.raises(ValueError, match="positive integers"):
        RenderTexture(*size)


@pytest.mark.parametrize("samples", [3])
def test_sample_count_is_explicit(samples):
    with pytest.raises(ValueError, match="samples"):
        RenderTexture(64, 32, samples=samples)


def test_graphical_engine_required(monkeypatch):
    monkeypatch.setattr(Application, "_current_engine", staticmethod(lambda: None))
    with pytest.raises(RuntimeError, match="graphical engine"):
        RenderTexture(64, 32)


@pytest.fixture
def graphical_application(engine, monkeypatch):
    """Use the live Vulkan test engine for public resource allocation."""
    monkeypatch.setattr(
        Application, "_current_engine", staticmethod(lambda: SimpleNamespace(get_native_engine=lambda: engine))
    )


def test_relative_target_tracks_game_pixels_not_scene_size(graphical_application, engine):
    snapshot = engine.renderer_frame_snapshot
    old_game = snapshot["game_target_width"], snapshot["game_target_height"]
    old_scene = snapshot["scene_target_width"], snapshot["scene_target_height"]
    try:
        engine.resize_game_render_target(101, 99)
        target = RenderTexture(scale=(0.5, 0.25), depth_format=PixelFormat.D32_SFLOAT, samples=4)
        other = RenderTexture(scale=(0.25, 0.5))
        fixed = RenderTexture(19, 11)
        assert target.scale == (0.5, 0.25) and fixed.scale is None
        assert (target.width, target.height) == (51, 25)
        revision = target.revision
        engine.resize_scene_render_target(213, 115)
        assert target.revision == revision
        engine.resize_game_render_target(203, 107)
        assert (target.width, target.height) == (102, 27)
        assert (other.width, other.height) == (51, 54)
        assert (fixed.width, fixed.height, fixed.revision) == (19, 11, 1)
        assert target.revision == revision + 1
        engine.resize_game_render_target(203, 107)
        engine.resize_game_render_target(0, 0)
        assert target.revision == revision + 1
        with pytest.raises(ValueError, match="Game output"):
            target.resize(45, 23)
        del target, other
        engine.resize_game_render_target(111, 73)  # expired registrations do not retain resources
    finally:
        engine.resize_scene_render_target(*old_scene)
        engine.resize_game_render_target(*old_game)


def test_import_retains_native_owner_and_observes_resize(graphical_application):
    target = RenderTexture(37, 23, format=PixelFormat.RGBA16_SFLOAT)
    revision = target.revision
    assert not target.resize(37, 23) and target.revision == revision
    graph = RenderGraph("Persistent target")
    output = graph.import_texture("target", target)
    assert graph.import_texture("another_label", target) is output
    with graph.add_pass("Clear") as render_pass:
        render_pass.write_color(output).set_clear(color=(0.2, 0.4, 0.8, 1.0))
    graph.set_output(output)
    assert target.resize(53, 29)
    assert target.revision == revision + 1
    with pytest.raises(ValueError):
        target.resize(0, 32)
    assert target.revision == revision + 1
    description = graph.build()
    assert len(description.textures) == 1
    texture = description.textures[0]
    assert texture.render_texture is target._native
    assert (texture.width, texture.height) == (53, 29)
    assert texture.role.name == "PERSISTENT"


def test_import_does_not_shadow_existing_resource(graphical_application):
    target = RenderTexture(16, 16)
    other = RenderTexture(16, 16)
    graph = RenderGraph()
    graph.create_texture("occupied")
    graph.create_buffer("buffer", 16)
    output = graph.import_texture("target", target)
    assert graph.import_texture("target", target) is output
    for name in ("occupied", "buffer"):
        with pytest.raises(ValueError, match="already exists"):
            graph.import_texture(name, target)
    with pytest.raises(ValueError, match="already exists"):
        graph.import_texture("target", other)
    with pytest.raises(TypeError, match="RenderTexture"):
        graph.import_texture("not_a_target", "asset-guid")


@pytest.mark.parametrize(
    "samples",
    [
        1,
        4,
    ],
)
def test_import_msaa_depth_and_resolve_descriptions(graphical_application, samples):
    try:
        target = RenderTexture(37, 23, samples=samples, depth_format=PixelFormat.D32_SFLOAT)
    except ValueError as exc:
        if samples == 1 or "does not support the requested sample count" not in str(exc):
            raise
        pytest.skip(f"GPU does not support {samples} samples for this RenderTexture format")
    graph = RenderGraph()
    color = graph.import_texture("color", target)
    depth = graph.import_texture("depth", target, attachment="depth")
    resolve = graph.import_texture("resolve", target, attachment="resolve")
    assert graph.import_texture("depth_alias", target, attachment="depth") is depth
    with pytest.raises(ValueError, match="already exists"):
        graph.import_texture("color", target, attachment="depth")
    with graph.add_pass("Clear") as p:
        p.write_color(color).write_depth(depth).set_clear(color=(0.1, 0.2, 0.3, 1), depth=0.25)
        if samples > 1:
            p.write_resolve(resolve)
    graph.set_output(resolve)
    description = graph.build()
    assert len(description.textures) == (2 if samples == 1 else 3)
    assert color.samples == depth.samples == samples
    assert depth.is_depth and resolve.samples == 1
    assert description.textures[0].attachment.name == "COLOR"
    assert description.textures[1].attachment.name == "DEPTH"
    assert description.passes[0].write_depth == depth.name
    if samples > 1:
        assert description.textures[2].attachment.name == "RESOLVE"
        assert description.passes[0].resolve_color == resolve.name


def test_sampled_depth_requires_a_depth_attachment(graphical_application):
    with pytest.raises(ValueError, match="depth"):
        RenderTexture(16, 16, sampled_depth=True)


def test_camera_output_owns_target_without_fabricating_asset_identity(graphical_application, scene):
    camera = scene.create_game_object("OffscreenCamera").add_component("Camera")
    before = camera.serialize_document()
    assert camera.target_texture is None
    target = RenderTexture(37, 23, samples=4, depth_format=PixelFormat.D32_SFLOAT)
    try:
        camera.target_texture = target
        native = target._native
        assert camera.target_texture._native is native
        assert camera.serialize_document() == before
        target.resize(53, 29)
        assert (camera.target_texture.width, camera.target_texture.height) == (53, 29)
        del target
        assert camera.target_texture.is_valid
        with pytest.raises(TypeError, match="RenderTexture"):
            camera.target_texture = "not-an-asset-guid"
        with pytest.raises(ValueError, match="depth"):
            camera.target_texture = RenderTexture(8, 8)
        assert camera.target_texture._native is native
        assert camera.serialize_document() == before
    finally:
        camera.target_texture = None
    assert camera.target_texture is None


def test_scene_and_game_target_replacement_uses_rhi_attachments(engine):
    previous_samples = engine.get_msaa_samples()
    before = engine.renderer_frame_snapshot
    previous_scene = (before["scene_target_width"], before["scene_target_height"])
    previous_game = (before["game_target_width"], before["game_target_height"])
    try:
        engine.resize_scene_render_target(79, 43)
        engine.resize_game_render_target(113, 67)
        generation = engine.get_game_render_target_generation()
        for samples in engine.msaa_state["supported_samples"]:
            engine.set_msaa_samples(samples)
            state = engine.msaa_state
            assert state["active_samples"] == samples
            assert state["scene_target_aligned"] and state["game_target_aligned"]
            assert state["material_pipelines_aligned"]
            assert state["scene_msaa_color_bytes"] == (0 if samples == 1 else 79 * 43 * 8 * samples)
            assert state["game_msaa_color_bytes"] == (0 if samples == 1 else 113 * 67 * 8 * samples)
        engine.resize_game_render_target(157, 89)
        after = engine.renderer_frame_snapshot
        assert (after["game_target_width"], after["game_target_height"]) == (157, 89)
        assert engine.get_game_render_target_generation() > generation
    finally:
        engine.set_msaa_samples(previous_samples)
        if all(previous_scene):
            engine.resize_scene_render_target(*previous_scene)
        if all(previous_game):
            engine.resize_game_render_target(*previous_game)


def test_camera_cache_observes_additive_unload_without_a_render_frame(engine, scene):
    from infernux.lib import SceneManager

    manager = SceneManager.instance()
    baseline = set(engine.renderer_frame_snapshot["game_camera_ids"])
    # Reuse allocations repeatedly so stale borrowed camera pointers cannot
    # accidentally pass because their old memory still looks like a Camera.
    for index in range(12):
        extra = manager.create_scene(f"CameraLifetime{index}")
        camera = extra.create_game_object("TemporaryCamera").add_component("Camera")
        camera_id = camera.component_id
        assert camera_id in engine.renderer_frame_snapshot["game_camera_ids"]
        manager.unload_scene(extra)
        del camera, extra
        assert set(engine.renderer_frame_snapshot["game_camera_ids"]) == baseline


def test_camera_cache_observes_enabled_and_order_edits_in_same_frame(engine, scene):
    camera_a = scene.create_game_object("CameraA").add_component("Camera")
    camera_b = scene.create_game_object("CameraB").add_component("Camera")
    a, b = camera_a.component_id, camera_b.component_id
    before = engine.renderer_frame_snapshot["game_camera_ids"]
    assert a in before and b in before
    camera_a.enabled = False
    assert a not in engine.renderer_frame_snapshot["game_camera_ids"]
    camera_a.enabled = True
    camera_a.depth = 20
    ordered = engine.renderer_frame_snapshot["game_camera_ids"]
    assert ordered.index(b) < ordered.index(a)
