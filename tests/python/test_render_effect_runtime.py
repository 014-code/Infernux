import json
from pathlib import Path

import pytest

from infernux.core.asset_ref import RenderEffectRef
from infernux.core.assets import AssetManager
from infernux.renderstack.effect_slot import EffectSlot
from infernux.renderstack.render_effect import RenderEffect
from infernux.renderstack.render_effect_asset import (
    EffectAssetReference,
    RenderEffectAsset,
    RenderEffectGroupAsset,
    RenderEffectGroupEntry,
    dump_render_effect_document,
)
from infernux.renderstack.render_effect_compiler import (
    RenderEffectArtifactRegistry,
    RenderEffectCompileError,
    expand_render_effect_reference,
    publish_live_effect_group_document,
)
from infernux.renderstack.render_stack import RenderStack
from infernux.renderstack.render_pipeline import RenderPipeline


@pytest.fixture
def effect_catalog(monkeypatch):
    """Small GUID catalog for managed RenderEffect references in unit tests."""
    paths_by_guid = {}
    guids_by_path = {}

    class Catalog:
        @staticmethod
        def register(path):
            resolved = str(Path(path).resolve())
            existing = guids_by_path.get(resolved)
            if existing:
                return existing
            guid = f"effect-{len(paths_by_guid) + 1:024x}"
            paths_by_guid[guid] = resolved
            guids_by_path[resolved] = guid
            return guid

        @staticmethod
        def get_path_from_guid(guid):
            return paths_by_guid.get(str(guid), "")

        @staticmethod
        def get_guid_from_path(path):
            return guids_by_path.get(str(Path(path).resolve()), "")

        @classmethod
        def ref(cls, path):
            return RenderEffectRef(guid=cls.register(path), path_hint=str(path))

        @classmethod
        def asset(cls, path):
            return EffectAssetReference(guid=cls.register(path))

    monkeypatch.setattr(AssetManager, "_asset_database", Catalog())
    return Catalog


class _SingleCameraPipeline(RenderPipeline):
    name = "Single Camera Contract"

    def define_topology(self, graph):
        graph.create_texture("color", camera_target=True)
        with graph.add_pass("Opaque") as render_pass:
            render_pass.write_color("color")
            render_pass.draw_renderers()
        graph.set_output("color")


class _BufferedSingleCameraPipeline(RenderPipeline):
    name = "Buffered Single Camera Contract"

    def define_topology(self, graph):
        from infernux.rendergraph.graph import Format

        color = graph.create_texture("color", camera_target=True)
        depth = graph.create_texture("depth", format=Format.D32_SFLOAT)
        normal = graph.create_texture("normal", format=Format.RGBA16_SFLOAT)
        motion = graph.create_texture("motion", format=Format.RG16_SFLOAT)
        with graph.add_pass("Opaque") as render_pass:
            render_pass.write_color(color)
            render_pass.write_depth(depth)
            render_pass.draw_renderers()
        result = graph.publish_pass_result(
            "opaque",
            {
                "color": color,
                "depth": depth,
                "normal": normal,
                "motion": motion,
            },
        )
        with graph.pass_result(result):
            graph.injection_point(
                "after_opaque",
                resources={"color", "depth", "normal", "motion"},
            )
        graph.set_output(color)


class _SingleCameraContext:
    output_samples = 0

    def __init__(self):
        self.calls = []
        self.current_revision = 0

    def setup_camera_properties(self, camera):
        self.calls.append(("setup", camera))

    def cull(self, camera):
        self.calls.append(("cull", camera))
        return "culling"

    def is_graph_revision_current(self, revision):
        self.calls.append(("is_current", revision))
        return self.current_revision == revision

    def apply_graph(self, description):
        self.calls.append(("apply", description.source_revision))
        self.current_revision = description.source_revision

    def submit_culling(self, culling):
        self.calls.append(("submit", culling))


def test_render_pipeline_callback_owns_exactly_one_camera_per_context():
    pipeline = _SingleCameraPipeline()
    context = _SingleCameraContext()
    camera = object()

    pipeline.render(context, camera)

    assert context.calls[0:2] == [("setup", camera), ("cull", camera)]
    assert context.calls[-1] == ("submit", "culling")
    assert sum(call[0] == "setup" for call in context.calls) == 1
    assert sum(call[0] == "cull" for call in context.calls) == 1
    assert sum(call[0] == "submit" for call in context.calls) == 1


def test_render_pipeline_camera_filter_does_not_touch_rejected_context():
    class _RejectedPipeline(_SingleCameraPipeline):
        def should_render_camera(self, camera):
            return False

    context = _SingleCameraContext()
    _RejectedPipeline().render(context, object())

    assert context.calls == []


def test_render_stack_forces_after_screen_ui_for_custom_pipeline():
    stack = RenderStack()
    stack._pipeline = _BufferedSingleCameraPipeline()
    stack._pipeline._render_stack = stack
    stack.effect_slots = [
        EffectSlot(
            stage_id="after_screen_ui",
            effect=RenderEffectRef(
                RenderEffect(
                    RenderEffectAsset(
                        feature_type="infernux.post.motion_blur",
                        parameters={
                            "intensity": 0.5,
                            "max_blur_pixels": 8.0,
                            "depth_rejection": 1.0,
                        },
                    )
                )
            ),
        )
    ]
    stack.invalidate_graph()

    probe = stack._build_full_topology_probe()
    sequence = list(probe.topology_sequence)
    stage = next(value for value in probe.effect_stages if value.stable_id == "after_screen_ui")

    assert {"color", "depth", "normal", "motion"} <= set(stage.contract.inputs)
    assert sequence.index(("pass", "_DisplayEncode")) < sequence.index(("pass", "_ScreenUI_Overlay"))
    assert sequence.index(("pass", "_ScreenUI_Overlay")) < sequence.index(("effect_stage", "after_screen_ui"))

    description = stack.build_graph()
    pass_names = [render_pass.name for render_pass in description.passes]
    assert "_DisplayEncode" in pass_names
    assert "_ScreenUI_Overlay" in pass_names
    assert any(name.endswith("MotionBlur_Apply") for name in pass_names)
    assert stack.effect_compile_errors == ()


def test_render_effect_clone_is_runtime_only_and_parameter_isolated():
    shared = RenderEffect(
        RenderEffectAsset(
            feature_type="infernux.post.bloom",
            parameters={"intensity": 0.5},
        ),
        file_path="Assets/Effects/Bloom.effect",
        guid="bloom-guid",
    )

    instance = shared.clone()
    instance.set_float("intensity", 2.0)

    assert shared.get_float("intensity") == pytest.approx(0.5)
    assert instance.get_float("intensity") == pytest.approx(2.0)
    assert instance.guid == ""
    assert instance.file_path == ""


def test_render_effect_save_and_load_round_trip(tmp_path):
    path = tmp_path / "Bloom.effect"
    effect = RenderEffect(
        RenderEffectAsset(
            feature_type="infernux.post.bloom",
            parameters={"threshold": 1.0},
        )
    )
    assert effect.save(str(path))

    loaded = RenderEffect.load(str(path))

    assert loaded is not None
    assert loaded.feature_type == "infernux.post.bloom"
    assert loaded.get_float("threshold") == pytest.approx(1.0)


def _write_effect(path: Path, *, intensity=0.5, max_iterations=3, extra=None):
    parameters = {
        "intensity": intensity,
        "max_iterations": max_iterations,
    }
    parameters.update(extra or {})
    path.write_text(
        dump_render_effect_document(
            RenderEffectAsset(
                feature_type="infernux.post.bloom",
                parameters=parameters,
            )
        ),
        encoding="utf-8",
    )


def test_render_effect_aot_artifact_distinguishes_dynamic_and_structural_edits(
    tmp_path,
    monkeypatch,
):
    from infernux.engine import project_context

    RenderEffectArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Bloom.effect"
    path.parent.mkdir()
    _write_effect(path, intensity=0.5, max_iterations=3)

    first, _ = RenderEffectArtifactRegistry.compile_and_publish(str(path), guid="bloom-guid")
    first_generation = RenderEffectArtifactRegistry.topology_generation()
    assert Path(first.artifact_path).is_file()

    _write_effect(path, intensity=1.5, max_iterations=3)
    dynamic, _ = RenderEffectArtifactRegistry.compile_and_publish(str(path), guid="bloom-guid")
    assert dynamic.revision > first.revision
    assert dynamic.structural_hash == first.structural_hash
    assert RenderEffectArtifactRegistry.topology_generation() == first_generation

    _write_effect(path, intensity=1.5, max_iterations=5)
    structural, _ = RenderEffectArtifactRegistry.compile_and_publish(str(path), guid="bloom-guid")
    assert structural.structural_hash != dynamic.structural_hash
    assert RenderEffectArtifactRegistry.topology_generation() == first_generation + 1


def test_render_effect_failed_compile_preserves_last_known_good_artifact(
    tmp_path,
    monkeypatch,
):
    from infernux.engine import project_context

    RenderEffectArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Bloom.effect"
    path.parent.mkdir()
    _write_effect(path)
    published, _ = RenderEffectArtifactRegistry.compile_and_publish(str(path), guid="bloom-guid")
    artifact_text = Path(published.artifact_path).read_text(encoding="utf-8")

    _write_effect(path, extra={"not_a_bloom_parameter": 1.0})
    with pytest.raises(RenderEffectCompileError, match="unknown parameters"):
        RenderEffectArtifactRegistry.compile_and_publish(str(path), guid="bloom-guid")

    assert RenderEffectArtifactRegistry.get(str(path), "bloom-guid") == published
    assert Path(published.artifact_path).read_text(encoding="utf-8") == artifact_text


def test_unresolved_effect_dependency_preserves_accepted_artifact(tmp_path, monkeypatch, effect_catalog):
    from infernux.engine import project_context

    RenderEffectArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Dependency.effect"
    path.parent.mkdir()
    _write_effect(path)
    guid = effect_catalog.register(path)
    accepted, _ = RenderEffectArtifactRegistry.compile_and_publish(str(path), guid=guid)
    artifact_bytes = Path(accepted.artifact_path).read_bytes()
    source = json.loads(path.read_text(encoding="utf-8"))
    source["dependencies"] = [{"guid": "unresolved-shader-guid"}]
    path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(RenderEffectCompileError, match="effect dependency GUID is unavailable"):
        RenderEffectArtifactRegistry.compile_and_publish(str(path), guid=guid)
    assert RenderEffectArtifactRegistry.get(str(path), guid) is accepted
    assert Path(accepted.artifact_path).read_bytes() == artifact_bytes


@pytest.mark.parametrize("clear_memory_cache", [False])
def test_group_cache_cannot_hide_removed_leaf_dependency(tmp_path, monkeypatch, effect_catalog, clear_memory_cache):
    from infernux.engine import project_context

    RenderEffectArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    shader = tmp_path / "Dependency.frag"
    shader.write_text("void main() {}", encoding="utf-8")
    dependency_guid = effect_catalog.register(shader)
    effect_path = tmp_path / "Leaf.effect"
    _write_effect(effect_path)
    source = json.loads(effect_path.read_text(encoding="utf-8"))
    source["dependencies"] = [{"guid": dependency_guid}]
    effect_path.write_text(json.dumps(source), encoding="utf-8")
    effect_guid = effect_catalog.register(effect_path)
    group_path = tmp_path / "Root.effectgroup"
    group_path.write_text(
        dump_render_effect_document(
            RenderEffectGroupAsset(entries=(RenderEffectGroupEntry("leaf", EffectAssetReference(guid=effect_guid)),))
        ),
        encoding="utf-8",
    )
    group_guid = effect_catalog.register(group_path)
    accepted, _ = RenderEffectArtifactRegistry.compile_and_publish(str(group_path), guid=group_guid)
    artifact_bytes = Path(accepted.artifact_path).read_bytes()
    if clear_memory_cache:
        RenderEffectArtifactRegistry.clear()
    catalog_lookup = effect_catalog.get_path_from_guid
    monkeypatch.setattr(
        effect_catalog,
        "get_path_from_guid",
        staticmethod(lambda guid: "" if guid == dependency_guid else catalog_lookup(guid)),
    )

    with pytest.raises(RenderEffectCompileError, match="effect dependency GUID is unavailable"):
        RenderEffectArtifactRegistry.compile_and_publish(str(group_path), guid=group_guid)
    assert Path(accepted.artifact_path).read_bytes() == artifact_bytes
    if not clear_memory_cache:
        assert RenderEffectArtifactRegistry.get(str(group_path), group_guid) is accepted


def test_render_effect_load_reuses_matching_persisted_artifact(tmp_path, monkeypatch):
    from infernux.engine import project_context

    RenderEffectArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Bloom.effect"
    path.parent.mkdir()
    _write_effect(path)
    published, _ = RenderEffectArtifactRegistry.compile_and_publish(str(path), guid="bloom-guid")
    RenderEffectArtifactRegistry.clear()

    def fail_compile(_cls, _document, _source_path, _guid):
        raise AssertionError("matching AOT artifact should avoid feature recompilation")

    monkeypatch.setattr(
        RenderEffectArtifactRegistry,
        "_compile_document",
        classmethod(fail_compile),
    )
    restored, _ = RenderEffectArtifactRegistry.compile_and_publish(str(path), guid="bloom-guid")

    assert restored.source_hash == published.source_hash
    assert restored.structural_hash == published.structural_hash
    assert restored.artifact_path == published.artifact_path


def test_asset_publish_updates_loaded_render_effect_in_place(tmp_path, monkeypatch):
    from infernux.core.assets import AssetManager
    from infernux.engine import project_context

    RenderEffectArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Bloom.effect"
    path.parent.mkdir()
    _write_effect(path, intensity=0.5)
    loaded = RenderEffect.load(str(path))
    assert loaded is not None
    monkeypatch.setattr(
        AssetManager,
        "_get_cached",
        classmethod(lambda _cls, guid: loaded if guid == "bloom-guid" else None),
    )

    assert AssetManager._compile_render_effect_runtime(str(path), "bloom-guid") == ""
    first_revision = loaded.artifact_revision
    _write_effect(path, intensity=2.0)
    assert AssetManager._compile_render_effect_runtime(str(path), "bloom-guid") == ""

    assert loaded.get_float("intensity") == pytest.approx(2.0)
    assert loaded.artifact_revision > first_revision


def test_render_stack_structured_slots_round_trip_without_hidden_json():
    stack = RenderStack()
    stack.add_effect_slot(
        "after_sky",
        RenderEffectRef(guid="fog-guid", path_hint="Assets/Effects/Fog.effect"),
        enabled=False,
    )

    restored = RenderStack()
    restored._deserialize_fields_document(stack._serialize_fields_document())

    slots = restored.get_effect_stage_slots("after_sky")
    assert len(slots) == 1
    assert slots[0].enabled is False
    assert slots[0].effect_ref.guid == "fog-guid"
    assert not hasattr(restored, "effect_stage_bindings_json")


def test_render_stack_compiles_effect_stage_to_scoped_dynamic_blocks():
    effect = RenderEffect(
        RenderEffectAsset(
            feature_type="infernux.post.tonemapping",
            parameters={"exposure": 1.25},
        )
    )
    stack = RenderStack()
    slot = stack.add_effect_slot("final", RenderEffectRef(effect=effect))

    description = stack.build_graph()
    effect_pass = next(render_pass for render_pass in description.passes if render_pass.name.endswith("ToneMap_Apply"))
    command = effect_pass.commands[0]

    assert f"final/{slot.slot_id}/0" in effect_pass.name
    assert command.parameter_block.startswith(f"effect/final/{slot.slot_id}/0/")
    assert dict(command.push_constants)["exposure"] == pytest.approx(1.25)
    assert any(render_pass.name.endswith("final/Commit") for render_pass in description.passes)
    assert stack.effect_compile_errors == ()


def test_motion_blur_consumes_color_depth_and_resolved_motion():
    effect = RenderEffect(
        RenderEffectAsset(
            feature_type="infernux.post.motion_blur",
            parameters={
                "intensity": 1.25,
                "max_blur_pixels": 48.0,
                "depth_rejection": 2.0,
            },
        )
    )
    stack = RenderStack()
    stack.add_effect_slot("final", RenderEffectRef(effect=effect))

    description = stack.build_graph()
    motion_blur = next(
        render_pass for render_pass in description.passes if render_pass.name.endswith("MotionBlur_Apply")
    )
    command = motion_blur.commands[0]
    bindings = dict(command.input_bindings)
    constants = dict(command.push_constants)

    assert command.shader_name == "Motion Blur"
    assert bindings["_SourceTex"]
    assert bindings["_DepthTex"] == "depth"
    assert bindings["_MotionTex"] == "_result/opaque/motion"
    assert set(motion_blur.read_textures) >= {
        bindings["_SourceTex"],
        "depth",
        bindings["_MotionTex"],
    }
    assert constants == {
        "intensity": pytest.approx(1.25),
        "maxBlurPixels": pytest.approx(48.0),
        "depthRejection": pytest.approx(2.0),
        "_pad0": pytest.approx(0.0),
    }
    assert stack.effect_compile_errors == ()


def test_temporal_aa_consumes_motion_and_commits_typed_history():
    effect = RenderEffect(
        RenderEffectAsset(
            feature_type="infernux.post.temporal_aa",
            parameters={
                "feedback": 0.92,
                "motion_rejection": 0.1,
                "depth_rejection": 128.0,
            },
        )
    )
    stack = RenderStack()
    stack.add_effect_slot("final", RenderEffectRef(effect=effect))

    description = stack.build_graph()
    resolve = next(render_pass for render_pass in description.passes if render_pass.name.endswith("TAA_Resolve"))
    commit = next(render_pass for render_pass in description.passes if render_pass.name.endswith("TAA_CommitHistory"))
    command = resolve.commands[0]
    bindings = dict(command.input_bindings)
    textures = {texture.name: texture for texture in description.textures}
    history_read = textures[bindings["_HistoryTex"]]
    history_write = textures[commit.commands[0].destination_resource]

    assert command.shader_name == "Temporal Anti-Aliasing"
    assert description.temporal_jitter is True
    assert bindings["_MotionTex"] == "_result/opaque/motion"
    assert bindings["_DepthTex"] == "depth"
    assert history_read.temporal_key == history_write.temporal_key
    assert history_read.role.name == "TEMPORAL_READ"
    assert history_write.role.name == "TEMPORAL_WRITE"
    assert commit.side_effect is True
    assert dict(command.push_constants) == {
        "feedback": pytest.approx(0.92),
        "motionRejection": pytest.approx(0.1),
        "depthRejection": pytest.approx(128.0),
        "_InfernuxHistoryValid": pytest.approx(0.0),
    }
    assert stack.effect_compile_errors == ()


def test_effect_group_expands_in_order_with_non_destructive_overrides(tmp_path, effect_catalog):
    bloom_path = tmp_path / "Bloom.effect"
    tone_path = tmp_path / "Tone.effect"
    bloom_path.write_text(
        dump_render_effect_document(
            RenderEffectAsset(
                feature_type="infernux.post.bloom",
                parameters={"intensity": 0.5, "max_iterations": 2},
            )
        ),
        encoding="utf-8",
    )
    tone_path.write_text(
        dump_render_effect_document(
            RenderEffectAsset(
                feature_type="infernux.post.tonemapping",
                parameters={"exposure": 1.0},
            )
        ),
        encoding="utf-8",
    )
    group_path = tmp_path / "Post.effectgroup"
    group_path.write_text(
        dump_render_effect_document(
            RenderEffectGroupAsset(
                entries=(
                    RenderEffectGroupEntry(
                        "bloom",
                        effect_catalog.asset(bloom_path),
                        overrides={"intensity": 0.9},
                    ),
                    RenderEffectGroupEntry(
                        "tone",
                        effect_catalog.asset(tone_path),
                    ),
                )
            )
        ),
        encoding="utf-8",
    )

    effects = expand_render_effect_reference(effect_catalog.ref(group_path))

    assert [effect.feature_type for effect in effects] == [
        "infernux.post.bloom",
        "infernux.post.tonemapping",
    ]
    assert effects[0].get_float("intensity") == pytest.approx(0.9)


def test_effect_group_parameter_publication_reaches_compiled_render_stack(tmp_path, effect_catalog):
    RenderEffectArtifactRegistry.clear()
    bloom_path = tmp_path / "Bloom.effect"
    bloom_path.write_text(
        dump_render_effect_document(
            RenderEffectAsset(
                feature_type="infernux.post.bloom",
                parameters={"intensity": 0.5, "max_iterations": 2},
            )
        ),
        encoding="utf-8",
    )
    group_path = tmp_path / "World.effectgroup"
    original = RenderEffectGroupAsset(
        entries=(
            RenderEffectGroupEntry(
                "bloom",
                effect_catalog.asset(bloom_path),
                overrides={"intensity": 0.75},
            ),
        )
    )
    group_path.write_text(
        dump_render_effect_document(original),
        encoding="utf-8",
    )

    reference = effect_catalog.ref(group_path)
    projection = expand_render_effect_reference(reference)[0]
    previous_revision = projection.revision
    stack = RenderStack()
    stack.add_effect_slot(
        "final",
        effect_catalog.ref(group_path),
    )
    stack._graph_state.description = stack.build_graph()

    class Context:
        graph_instance_id = 41

    requires_rebuild, initial = stack._collect_effect_parameter_updates(Context())
    assert requires_rebuild is False
    assert any(dict(update.values).get("intensity") == pytest.approx(0.75) for update in initial)
    assert stack._collect_effect_parameter_updates(Context()) == (False, [])

    edited = RenderEffectGroupAsset(
        entries=(
            RenderEffectGroupEntry(
                "bloom",
                effect_catalog.asset(bloom_path),
                overrides={"intensity": 1.5},
            ),
        )
    )
    publish_live_effect_group_document(str(group_path), edited)
    assert expand_render_effect_reference(reference)[0] is projection
    assert projection.revision > previous_revision
    requires_rebuild, updates = stack._collect_effect_parameter_updates(Context())

    assert requires_rebuild is False
    assert any(dict(update.values).get("intensity") == pytest.approx(1.5) for update in updates)


@pytest.mark.parametrize("nested", [True])
def test_group_reimport_updates_every_mounted_projection_without_rebuilding(tmp_path, effect_catalog, nested):
    RenderEffectArtifactRegistry.clear()
    effect_path = tmp_path / "Bloom.effect"
    effect_path.write_text(
        dump_render_effect_document(
            RenderEffectAsset(
                feature_type="infernux.post.bloom",
                parameters={"intensity": 0.5, "max_iterations": 2},
            )
        ),
        encoding="utf-8",
    )
    group_path = tmp_path / "Shared.effectgroup"

    def document(overrides):
        return RenderEffectGroupAsset(
            entries=(
                RenderEffectGroupEntry(
                    "bloom",
                    effect_catalog.asset(effect_path),
                    overrides=overrides,
                ),
            )
        )

    original = document({"intensity": 0.75})
    group_path.write_text(dump_render_effect_document(original), encoding="utf-8")
    group_guid = effect_catalog.register(group_path)
    RenderEffectArtifactRegistry.compile_and_publish(str(group_path), guid=group_guid)
    mounted_path = group_path
    if nested:
        mounted_path = tmp_path / "Outer.effectgroup"
        mounted_path.write_text(
            dump_render_effect_document(
                RenderEffectGroupAsset(entries=(RenderEffectGroupEntry("shared", effect_catalog.asset(group_path)),))
            ),
            encoding="utf-8",
        )

    stack = RenderStack()
    stack.add_effect_slot("after_opaque", effect_catalog.ref(mounted_path))
    stack.add_effect_slot("final", effect_catalog.ref(mounted_path))
    stack._graph_state.description = stack.build_graph()
    accepted_graph = stack._graph_state.description
    mounted_sources = tuple(binding.source for binding in stack._graph_state.bindings)
    assert len(mounted_sources) == 2

    class Context:
        graph_instance_id = 41

    assert stack._collect_effect_parameter_updates(Context())[0] is False
    assert stack._collect_effect_parameter_updates(Context()) == (False, [])

    # A rejected candidate must not mutate either live projection.
    group_path.write_text(dump_render_effect_document(document({"unknown": 1.5})), encoding="utf-8")
    with pytest.raises(RenderEffectCompileError, match="unknown parameter"):
        RenderEffectArtifactRegistry.compile_and_publish(str(group_path), guid=group_guid)
    assert all(source.get_float("intensity") == pytest.approx(0.75) for source in mounted_sources)
    assert stack._collect_effect_parameter_updates(Context()) == (False, [])

    edited = document({"intensity": 1.5})
    group_path.write_text(dump_render_effect_document(edited), encoding="utf-8")
    RenderEffectArtifactRegistry.compile_and_publish(str(group_path), guid=group_guid)
    requires_rebuild, updates = stack._collect_effect_parameter_updates(Context())

    assert requires_rebuild is False
    assert stack._graph_state.description is accepted_graph
    assert all(source.get_float("intensity") == pytest.approx(1.5) for source in mounted_sources)
    intensity_blocks = [update for update in updates if "intensity" in dict(update.values)]
    assert len(intensity_blocks) == 2
    assert all(dict(update.values)["intensity"] == pytest.approx(1.5) for update in intensity_blocks)
    for source in mounted_sources:
        group_view = source._source if nested else source
        assert group_view.group_resource.to_asset().entries[0].overrides["intensity"] == 1.5
    assert stack._collect_effect_parameter_updates(Context()) == (False, [])
    stack.on_destroy()


def test_pixelation_group_parameter_publication_reaches_compiled_render_stack(tmp_path, effect_catalog):
    RenderEffectArtifactRegistry.clear()
    pixelation_path = tmp_path / "Pixelation.effect"
    pixelation_path.write_text(
        dump_render_effect_document(
            RenderEffectAsset(
                feature_type="infernux.route.pixelation",
                parameters={"intensity": 0.0, "pixel_size": 4},
            )
        ),
        encoding="utf-8",
    )
    group_path = tmp_path / "World.effectgroup"
    group_path.write_text(
        dump_render_effect_document(
            RenderEffectGroupAsset(
                entries=(
                    RenderEffectGroupEntry(
                        "pixel",
                        effect_catalog.asset(pixelation_path),
                        overrides={"intensity": 0.0, "pixel_size": 4},
                    ),
                )
            )
        ),
        encoding="utf-8",
    )

    stack = RenderStack()
    stack.add_effect_slot("final", effect_catalog.ref(group_path))
    stack._graph_state.description = stack.build_graph()

    class Context:
        graph_instance_id = 42

    requires_rebuild, initial = stack._collect_effect_parameter_updates(Context())
    assert requires_rebuild is False
    assert any(
        dict(update.values).get("intensity") == pytest.approx(0.0)
        and dict(update.values).get("pixelSize") == pytest.approx(4.0)
        for update in initial
    )
    assert stack._collect_effect_parameter_updates(Context()) == (False, [])

    edited = RenderEffectGroupAsset(
        entries=(
            RenderEffectGroupEntry(
                "pixel",
                effect_catalog.asset(pixelation_path),
                overrides={"intensity": 0.65, "pixel_size": 24},
            ),
        )
    )
    publish_live_effect_group_document(str(group_path), edited)
    requires_rebuild, updates = stack._collect_effect_parameter_updates(Context())

    assert requires_rebuild is False
    assert any(
        dict(update.values).get("intensity") == pytest.approx(0.65)
        and dict(update.values).get("pixelSize") == pytest.approx(24.0)
        for update in updates
    )


def test_effect_group_expansion_is_published_in_memory_until_asset_reimport(
    tmp_path,
    monkeypatch,
    effect_catalog,
):
    RenderEffectArtifactRegistry.clear()
    bloom_path = tmp_path / "Bloom.effect"
    bloom_path.write_text(
        dump_render_effect_document(
            RenderEffectAsset(
                feature_type="infernux.post.bloom",
                parameters={"intensity": 0.5, "max_iterations": 2},
            )
        ),
        encoding="utf-8",
    )
    group_path = tmp_path / "Post.effectgroup"

    def write_group(intensity):
        group_path.write_text(
            dump_render_effect_document(
                RenderEffectGroupAsset(
                    entries=(
                        RenderEffectGroupEntry(
                            "bloom",
                            effect_catalog.asset(bloom_path),
                            overrides={"intensity": intensity},
                        ),
                    )
                )
            ),
            encoding="utf-8",
        )

    write_group(0.75)
    original_read_text = Path.read_text
    group_reads = []

    def count_group_reads(path, *args, **kwargs):
        if path.resolve() == group_path.resolve():
            group_reads.append(str(path))
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", count_group_reads)

    first = expand_render_effect_reference(effect_catalog.ref(group_path))
    second = expand_render_effect_reference(effect_catalog.ref(group_path))

    assert first[0] is second[0]
    assert first[0].get_float("intensity") == pytest.approx(0.75)
    assert len(group_reads) == 1

    write_group(1.25)
    RenderEffectArtifactRegistry.compile_and_publish(str(group_path))
    refreshed = expand_render_effect_reference(effect_catalog.ref(group_path))

    assert refreshed[0].get_float("intensity") == pytest.approx(1.25)
    assert len(group_reads) == 2


def test_effect_group_compiles_without_reentering_its_own_asset_load(tmp_path, monkeypatch, effect_catalog):
    from infernux.engine import project_context

    RenderEffectArtifactRegistry.clear()
    monkeypatch.setattr(project_context._context(), "root", str(tmp_path))
    rendering_dir = tmp_path / "Assets" / "Rendering"
    rendering_dir.mkdir(parents=True)
    bloom_path = rendering_dir / "Bloom.effect"
    bloom_path.write_text(
        dump_render_effect_document(
            RenderEffectAsset(
                feature_type="infernux.post.bloom",
                parameters={"intensity": 0.5, "max_iterations": 2},
            )
        ),
        encoding="utf-8",
    )
    group_path = rendering_dir / "Post.effectgroup"
    group_path.write_text(
        dump_render_effect_document(
            RenderEffectGroupAsset(
                entries=(
                    RenderEffectGroupEntry(
                        "bloom",
                        effect_catalog.asset(bloom_path),
                    ),
                )
            )
        ),
        encoding="utf-8",
    )

    artifact, document = RenderEffectArtifactRegistry.compile_and_publish(str(group_path))

    assert isinstance(document, RenderEffectGroupAsset)
    assert artifact.kind == "group"
    assert [feature["feature_type"] for feature in artifact.features] == ["infernux.post.bloom"]


def test_effect_group_cycle_is_rejected(tmp_path, effect_catalog):
    first = tmp_path / "First.effectgroup"
    second = tmp_path / "Second.effectgroup"
    first.write_text(
        dump_render_effect_document(
            RenderEffectGroupAsset(
                entries=(
                    RenderEffectGroupEntry(
                        "second",
                        effect_catalog.asset(second),
                    ),
                )
            )
        ),
        encoding="utf-8",
    )
    second.write_text(
        dump_render_effect_document(
            RenderEffectGroupAsset(
                entries=(
                    RenderEffectGroupEntry(
                        "first",
                        effect_catalog.asset(first),
                    ),
                )
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(RenderEffectCompileError, match="cycle"):
        expand_render_effect_reference(effect_catalog.ref(first))


def test_failed_effect_compile_rolls_back_partial_graph_mutation():
    from infernux.renderstack.fullscreen_effect import FullScreenEffect
    from infernux.renderstack.render_effect_compiler import register_render_effect_feature

    class BrokenEffect(FullScreenEffect):
        name = "Broken Test Effect"
        injection_point = "after_post_process"
        default_order = 1

        def setup_passes(self, graph, bus):
            graph.create_texture("partial")
            with graph.add_pass("Partial") as render_pass:
                render_pass.write_color("partial")
                render_pass.fullscreen_quad("fullscreen_blit")
            raise ValueError("intentional compile failure")

    register_render_effect_feature("tests.post.broken", BrokenEffect)
    stack = RenderStack()
    stack.add_effect_slot(
        "final",
        RenderEffectRef(effect=RenderEffect(RenderEffectAsset("tests.post.broken"))),
    )

    description = stack.build_graph()

    assert not any("Partial" in render_pass.name for render_pass in description.passes)
    assert any("intentional compile failure" in error for error in stack.effect_compile_errors)
