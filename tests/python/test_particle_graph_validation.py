from __future__ import annotations

from test_particle_graph_hir import (
    AssetReference,
    EmitterSettings,
    EmitterShape,
    GraphDocument,
    GraphLinkRecord,
    GraphNodeRecord,
    KernelCompileError,
    MeshEmissionMode,
    PARTICLE_SCRIPT_EVENT_SOURCE,
    PARTICLE_SCRIPT_SOURCE,
    ParticleArtifactError,
    ParticleArtifactRegistry,
    ParticleArtifactSuperseded,
    ParticleAttribute,
    ParticleBurst,
    ParticleCompileError,
    ParticleEmitterAsset,
    ParticleGraphAsset,
    ParticleGraphCompiler,
    ParticleGraphSchemaError,
    ParticleKernelLowerer,
    ParticleParameter,
    ParticleRuntimeMetadataError,
    ParticleScriptCompiler,
    ParticleScriptError,
    Path,
    PortKind,
    TypeRef,
    ValueType,
    VectorField,
    _operations,
    copy,
    decode_particle_runtime_metadata,
    json,
    particle_cache_attributes,
    pytest,
    replace,
    threading,
)

def test_transform_direction_treats_untyped_vector_as_emitter_local():
    init = GraphDocument(
        "particle.init",
        nodes=(
            GraphNodeRecord("root.init", "particle.root.init"),
            GraphNodeRecord(
                "local-velocity",
                "common.vector.compose3",
                properties={"x": 0.0, "y": 0.0, "z": 1.25},
            ),
            GraphNodeRecord(
                "to-simulation",
                "common.space.transform_direction",
                properties={"target_space": "simulation"},
            ),
            GraphNodeRecord("set-velocity", "particle.attribute.velocity"),
        ),
        links=(
            GraphLinkRecord(
                "stream",
                "root.init",
                "out",
                "set-velocity",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "compose-transform",
                "local-velocity",
                "value",
                "to-simulation",
                "input",
            ),
            GraphLinkRecord(
                "transform-set",
                "to-simulation",
                "value",
                "set-velocity",
                "value",
            ),
        ),
    )

    kernel = ParticleKernelLowerer().lower(
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(init=init),))
        )
    ).emitters[0]
    conversions = [
        instruction.immediate_dict()
        for instruction in kernel.init.instructions
        if instruction.opcode == "convert_space"
        and instruction.immediate_dict().get("semantic") == "direction"
    ]
    assert {
        "from": "none",
        "to": "emitter_local",
        "semantic": "direction",
    } in conversions
    assert {
        "from": "emitter_local",
        "to": "simulation",
        "semantic": "direction",
    } in conversions
    assert "builtin.velocity" in kernel.init.written_attributes


def test_unused_broken_value_graph_does_not_drop_chained_attribute_writes():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("set-velocity", "particle.attribute.velocity"),
            GraphNodeRecord("set-size", "particle.attribute.size"),
            GraphNodeRecord("orphan-size", "particle.attribute.size"),
            GraphNodeRecord("length", "common.vector.length"),
            GraphNodeRecord(
                "speed",
                "common.constant.vec3",
                properties={"value": [0.0, 2.0, 0.0]},
            ),
            GraphNodeRecord(
                "size",
                "common.constant.f32",
                properties={"value": 3.5},
            ),
        ),
        links=(
            GraphLinkRecord(
                "exec-velocity",
                "root.update",
                "out",
                "set-velocity",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "exec-size",
                "set-velocity",
                "out",
                "set-size",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord("bind-velocity", "speed", "value", "set-velocity", "value"),
            GraphLinkRecord("bind-size", "size", "value", "set-size", "value"),
            GraphLinkRecord(
                "orphan-value",
                "length",
                "result",
                "orphan-size",
                "value",
            ),
        ),
    )

    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    hir = program.emitters[0]
    opcodes = [operation.opcode for operation in _operations(hir.update)]
    assert opcodes.count("attribute.modify_size") == 1
    assert "attribute.modify_velocity" in opcodes
    kernel = ParticleKernelLowerer().lower(program).emitters[0]
    assert {"builtin.velocity", "builtin.size"} <= set(kernel.update.written_attributes)


def test_set_velocity_then_set_size_keeps_both_attribute_writes():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("set-velocity", "particle.attribute.velocity"),
            GraphNodeRecord("set-size", "particle.attribute.size"),
            GraphNodeRecord(
                "speed",
                "common.constant.vec3",
                properties={"value": [0.0, 2.0, 0.0]},
            ),
            GraphNodeRecord(
                "size",
                "common.constant.f32",
                properties={"value": 3.5},
            ),
        ),
        links=(
            GraphLinkRecord(
                "exec-velocity",
                "root.update",
                "out",
                "set-velocity",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "exec-size",
                "set-velocity",
                "out",
                "set-size",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord("bind-velocity", "speed", "value", "set-velocity", "value"),
            GraphLinkRecord("bind-size", "size", "value", "set-size", "value"),
        ),
    )

    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    hir = program.emitters[0]
    kernel = ParticleKernelLowerer().lower(program).emitters[0]
    opcodes = [operation.opcode for operation in _operations(hir.update)]
    velocity_index = opcodes.index("attribute.modify_velocity")
    size_index = opcodes.index("attribute.modify_size")
    assert velocity_index < size_index
    stored = [
        instruction.immediate_dict()["attribute"]
        for instruction in kernel.update.instructions
        if instruction.opcode == "store_attribute"
    ]
    assert stored.index("builtin.velocity") < stored.index("builtin.size")
    assert {"builtin.velocity", "builtin.size"} <= set(kernel.update.written_attributes)
    literals = [
        instruction.immediate_dict().get("value")
        for instruction in kernel.update.instructions
        if instruction.opcode == "constant"
    ]
    assert [0.0, 2.0, 0.0] in literals
    assert 3.5 in literals


def test_particle_behavior_hash_ignores_graph_identity_layout_and_record_order():
    def make_update(prefix: str, offset: float, *, reverse: bool = False) -> GraphDocument:
        nodes = (
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(f"{prefix}.gravity", "particle.attribute.velocity"),
            GraphNodeRecord(
                f"{prefix}.value",
                "common.constant.vec3",
                (offset, offset),
                {"value": [0.0, -1.0, 0.0]},
            ),
        )
        links = (
            GraphLinkRecord(
                f"{prefix}.stream",
                "root.update",
                "out",
                f"{prefix}.gravity",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                f"{prefix}.value-link",
                f"{prefix}.value",
                "value",
                f"{prefix}.gravity",
                "value",
            ),
        )
        return GraphDocument(
            "particle.update",
            nodes=tuple(reversed(nodes)) if reverse else nodes,
            links=tuple(reversed(links)) if reverse else links,
        )

    first = ParticleGraphAsset(
        stable_id="graph",
        emitters=(ParticleEmitterAsset(stable_id="emitter", update=make_update("first", 0.0)),),
    )
    second = ParticleGraphAsset(
        stable_id="graph",
        name="Renamed Graph",
        emitters=(
            ParticleEmitterAsset(
                stable_id="emitter",
                name="Renamed Emitter",
                update=make_update("second", 500.0, reverse=True),
            ),
        ),
    )
    first_hir = ParticleGraphCompiler().compile(first)
    second_hir = ParticleGraphCompiler().compile(second)

    assert first_hir.semantic_hash != second_hir.semantic_hash
    assert first_hir.behavior_hash == second_hir.behavior_hash
    assert first_hir.emitters[0].render_plan == second_hir.emitters[0].render_plan


def test_particle_graph_schema_is_strict_and_semantic_hash_ignores_positions():
    asset = ParticleGraphAsset()
    value = asset.to_dict()
    value["future"] = True
    with pytest.raises(ParticleGraphSchemaError, match="keys mismatch"):
        ParticleGraphAsset.from_dict(value)

    moved = copy.deepcopy(asset.to_dict())
    moved["emitters"][0]["stages"]["rendering"]["nodes"][0]["position"] = [500.0, 200.0]
    restored = ParticleGraphAsset.from_dict(moved)
    assert restored.semantic_hash() == asset.semantic_hash()

    obsolete = copy.deepcopy(asset.to_dict())
    del obsolete["emitters"][0]["settings"]["duration"]
    with pytest.raises(ParticleGraphSchemaError, match="keys mismatch"):
        ParticleGraphAsset.from_dict(obsolete)

    obsolete_lifecycle = copy.deepcopy(asset.to_dict())
    obsolete_lifecycle["emitters"][0]["enabled"] = True
    with pytest.raises(ParticleGraphSchemaError, match="keys mismatch"):
        ParticleGraphAsset.from_dict(obsolete_lifecycle)

    obsolete_collision = copy.deepcopy(asset.to_dict())
    del obsolete_collision["emitters"][0]["settings"]["collision_layer_mask"]
    with pytest.raises(ParticleGraphSchemaError, match="keys mismatch"):
        ParticleGraphAsset.from_dict(obsolete_collision)


def test_particle_emitter_instance_policy_is_not_graph_or_runtime_metadata():
    source = ParticleGraphAsset(
        emitters=(ParticleEmitterAsset(stable_id="manual"),)
    )
    restored = ParticleGraphAsset.from_json(source.canonical_json())
    hir = ParticleGraphCompiler().compile(restored)
    metadata = decode_particle_runtime_metadata(hir)

    assert restored.emitters[0].settings.to_dict().keys() == EmitterSettings().to_dict().keys()
    assert "enabled" not in source.to_dict()["emitters"][0]
    assert "play_on_start" not in source.to_dict()["emitters"][0]
    assert not hasattr(hir.emitters[0], "enabled")
    assert not hasattr(hir.emitters[0], "play_on_start")
    assert not hasattr(metadata.emitters[0], "enabled")
    assert not hasattr(metadata.emitters[0], "play_on_start")
    assert metadata.emitters[0].name == "Emitter"


def test_particle_python_construction_cannot_bypass_schema_invariants():
    with pytest.raises(ParticleGraphSchemaError, match="exactly 3"):
        ParticleAttribute("custom.wind", "wind", TypeRef(ValueType.VEC3), [1.0, 2.0])
    with pytest.raises(ParticleGraphSchemaError, match="bursts"):
        EmitterSettings(bursts=(object(),))
    with pytest.raises(ParticleGraphSchemaError, match="unsigned 32-bit"):
        EmitterSettings(collision_layer_mask=-1)
    with pytest.raises(ParticleGraphSchemaError, match="bounce_scale"):
        EmitterSettings(collision_bounce_scale=-0.1)
    with pytest.raises(ParticleGraphSchemaError, match="friction_scale"):
        EmitterSettings(collision_friction_scale=float("nan"))
    with pytest.raises(ParticleGraphSchemaError, match="within the emitter duration"):
        EmitterSettings(
            duration=1.0,
            bursts=(ParticleBurst(0.75, 1, cycles=2, interval=0.5),),
        )
    with pytest.raises(ParticleGraphSchemaError, match="emitters are invalid"):
        ParticleGraphAsset(emitters=(object(),))


def test_particle_output_rejects_removed_material_property():
    value = ParticleGraphAsset().to_dict()
    output = value["emitters"][0]["stages"]["rendering"]["nodes"][1]["properties"]
    output["material"] = AssetReference(guid="legacy-material").to_dict()

    restored = ParticleGraphAsset.from_dict(value)
    with pytest.raises(ParticleCompileError, match="unknown properties.*material"):
        ParticleGraphCompiler().compile(restored)


def test_mesh_emitter_shape_requires_an_asset_at_aot_compile_time():
    emitter = ParticleEmitterAsset(
        settings=EmitterSettings(shape=EmitterShape(kind="mesh"))
    )

    hir = ParticleGraphCompiler().compile(ParticleGraphAsset(emitters=(emitter,)))
    with pytest.raises(KernelCompileError, match="mesh shape requires a mesh asset"):
        ParticleKernelLowerer().lower(hir)


def test_sdf_emitter_shape_requires_an_owned_sdf_data_interface():
    with pytest.raises(ParticleGraphSchemaError, match="Signed Distance Volume"):
        ParticleEmitterAsset(
            settings=EmitterSettings(
                shape=EmitterShape(kind="sdf", sdf_interface="missing")
            )
        )

    with pytest.raises(ParticleGraphSchemaError, match="Signed Distance Volume"):
        ParticleEmitterAsset(
            settings=EmitterSettings(
                shape=EmitterShape(kind="sdf", sdf_interface="field")
            ),
            data_interfaces=(VectorField(stable_id="field"),),
        )


def test_particle_graph_rejects_pre_sdf_emitter_shape_schema():
    payload = ParticleGraphAsset().to_dict()
    shape = payload["emitters"][0]["settings"]["shape"]
    shape.pop("sdf_interface")
    shape.pop("sdf_mode")

    with pytest.raises(ParticleGraphSchemaError, match="keys mismatch"):
        ParticleGraphAsset.from_dict(payload)


def test_particle_script_mesh_shape_matches_graph_asset_contract():
    source = '''\
from infernux.particle import AssetReference, EmitterShape, ParticleScript, ParticleEmitter, EmitterSettings

class MeshShapeGraph(ParticleScript):
    class SurfaceEmitter(ParticleEmitter):
        stable_id = "surface-emitter"
        settings = EmitterSettings(
            shape=EmitterShape(
                kind="mesh",
                mesh=AssetReference(path_hint="Assets/Models/source.fbx"),
                mesh_mode="surface",
            ),
        )

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            pass

        def rendering(self, ctx, particles):
            particles.sprite()
'''

    asset = ParticleScriptCompiler().parse(
        source, source_name="MeshShape.particle.py"
    )

    shape = asset.emitters[0].settings.shape
    assert shape.kind.value == "mesh"
    assert shape.mesh == AssetReference(path_hint="Assets/Models/source.fbx")
    assert shape.mesh_mode is MeshEmissionMode.SURFACE


def test_particle_script_rejects_sdf_shape_authoring():
    source = '''\
from infernux.particle import AssetReference, EmitterShape, ParticleScript, ParticleEmitter, EmitterSettings, SdfVolume

class SdfShapeGraph(ParticleScript):
    class VolumeEmitter(ParticleEmitter):
        stable_id = "volume-emitter"
        settings = EmitterSettings(
            shape=EmitterShape(
                kind="sdf",
                sdf_interface="shape-field",
                sdf_mode="volume",
            ),
        )
        data_interfaces = (
            SdfVolume(
                stable_id="shape-field",
                texture=AssetReference(path_hint="Assets/VFX/Shape.inxsdf"),
            ),
        )

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            pass

        def rendering(self, ctx, particles):
            particles.sprite()
'''

    with pytest.raises(ParticleScriptError, match="SDF authoring is not available"):
        ParticleScriptCompiler().parse(source, source_name="SdfShape.particle.py")


def test_particle_script_compiles_without_execution_to_same_hir_contract():
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(PARTICLE_SCRIPT_SOURCE, source_name="Smoke.particle.py")
    program = compiler.compile(PARTICLE_SCRIPT_SOURCE, source_name="Smoke.particle.py")
    emitter = program.emitters[0]

    assert asset.stable_id == "smoke-graph"
    assert program.schedule.emitter_ids == ("smoke",)
    assert emitter.settings.spawn_rate_over_distance == 3.0
    assert emitter.settings.duration == 4.0
    assert emitter.settings.loop is False
    assert emitter.settings.start_delay == 0.25
    assert not hasattr(emitter, "enabled")
    assert not hasattr(emitter, "play_on_start")
    assert [operation.opcode for operation in _operations(emitter.init)] == [
        "emitter.sample_shape",
        "attribute.modify_velocity",
        "attribute.modify_lifetime",
        "attribute.modify_rotation",
    ]
    assert [operation.opcode for operation in _operations(emitter.update)[-2:]] == [
        "attribute.modify_velocity",
        "attribute.modify_rotation",
    ]
    assert [operation.opcode for operation in _operations(emitter.rendering)[:2]] == [
        "attribute.modify_lifetime",
        "attribute.modify_flipbook_frame",
    ]
    assert any(
        instruction.opcode == "normalized_age"
        for instruction in emitter.rendering.expressions.instructions
    )
    assert emitter.render_plan.outputs[0].receive_scene_lighting is True
    assert emitter.render_plan.outputs[0].receive_shadows is True
    assert emitter.render_plan.outputs[0].sprite_alignment == "velocity"
    assert [interface.stable_id for interface in emitter.data_interfaces] == [
        "wind-field",
    ]
    assert program.behavior_hash == ParticleGraphCompiler().compile(asset).behavior_hash


def test_particle_script_wait_and_until_share_the_graph_continuation_contract():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class TimedMotion(ParticleScript):
    stable_id = "timed-motion"

    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(8.0)
            ctx.wait_seconds(0.25)
            particles.set_size(2.0)
            ctx.wait_frames(2)

        def update(self, ctx, particles):
            particles.add_velocity((0.0, -1.0, 0.0))
            ctx.until_seconds(3.0)
            particles.add_velocity((1.0, 1.0, 0.0))
            ctx.until_frames(5)

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="TimedMotion.particle.py")
    emitter_asset = asset.emitters[0]

    assert [node.type_id for node in emitter_asset.init.nodes[1:]] == [
        "particle.attribute.lifetime",
        "particle.control.wait_seconds",
        "particle.attribute.size",
        "particle.control.wait_frames",
    ]
    assert [node.type_id for node in emitter_asset.update.nodes[1:]] == [
        "particle.attribute.velocity",
        "particle.control.until_seconds",
        "particle.attribute.velocity",
        "particle.control.until_frames",
    ]
    assert emitter_asset.update.nodes[1].properties["composition"] == "add"
    assert emitter_asset.update.nodes[3].properties["composition"] == "add"

    program = compiler.compile(source, source_name="TimedMotion.particle.py")
    emitter = program.emitters[0]
    assert [item.kind.value for item in emitter.init.flow.suspensions] == [
        "seconds",
        "frames",
    ]
    assert [item.resume_node_uid for item in emitter.init.flow.suspensions] == [
        "init.2.set_size",
        "",
    ]
    assert [item.kind.value for item in emitter.update.flow.suspensions] == [
        "until_seconds",
        "until_frames",
    ]
    assert [item.resume_node_uid for item in emitter.update.flow.suspensions] == [
        "update.0.add_velocity",
        "update.2.add_velocity",
    ]


def test_particle_script_source_locations_reach_hir_and_kernel_without_changing_hashes():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class Located(ParticleScript):
    stable_id = "located"

    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(4.0)

        def update(self, ctx, particles):
            particles.add_velocity((0.0, -1.0, 0.0))

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    first_asset = compiler.parse(source, source_name="Located.particle.py")
    moved_asset = compiler.parse(source, source_name="Moved.particle.py")
    program = ParticleGraphCompiler().compile(first_asset)
    update = program.emitters[0].update
    source_uid = "update.0.add_velocity"
    expected_line = source.splitlines().index(
        "            particles.add_velocity((0.0, -1.0, 0.0))"
    ) + 1

    location = update.source_location(source_uid)
    assert (location.source_name, location.line, location.column) == (
        "Located.particle.py",
        expected_line,
        13,
    )
    kernel = ParticleKernelLowerer().lower(program).emitters[0].update
    authored = next(
        instruction
        for instruction in kernel.instructions
        if instruction.source.node_uid == source_uid
    )
    assert authored.source.describe() == (
        f"Located.particle.py:{expected_line}:13 [{source_uid}]"
    )
    assert first_asset.semantic_hash() == moved_asset.semantic_hash()
    assert program.behavior_hash == ParticleGraphCompiler().compile(moved_asset).behavior_hash


def test_particle_graph_expression_error_names_source_file_and_node():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("sample", "common.texture.sample2d"),
            GraphNodeRecord("size", "particle.attribute.size"),
        ),
        links=(
            GraphLinkRecord(
                "sample-size",
                "sample",
                "color",
                "size",
                "value",
                PortKind.VALUE,
            ),
            GraphLinkRecord(
                "root-size", "root.update", "out", "size", "in", PortKind.EXEC
            ),
        ),
    )
    with pytest.raises(
        ParticleCompileError,
        match=r"Broken\.particlegraph \[sample\].*required input",
    ):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(
                emitters=(ParticleEmitterAsset(update=update),)
            ),
            source_name="Broken.particlegraph",
        )


def test_particle_script_exposes_delta_time_as_a_pure_common_graph_value():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class Gravity(ParticleScript):
    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(8.0)

        def update(self, ctx, particles):
            particles.add_velocity((0.0, -9.8, 0.0) * ctx.delta_time)

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="Gravity.particle.py")
    update = asset.emitters[0].update
    assert "particle.context.delta_time" in {
        node.type_id for node in update.nodes
    }
    assert "common.constant.vec3" in {node.type_id for node in update.nodes}
    assert "common.math.multiply" in {node.type_id for node in update.nodes}

    kernel = ParticleKernelLowerer().lower(
        compiler.compile(source, source_name="Gravity.particle.py")
    ).emitters[0].update
    assert any(
        instruction.opcode == "load_uniform"
        and instruction.source.node_uid.endswith(".delta_time")
        for instruction in kernel.instructions
    )


def test_particle_script_if_else_expands_continuations_into_mutually_exclusive_lanes():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class ConditionalMotion(ParticleScript):
    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(8.0)

        def update(self, ctx, particles):
            if particles.age < 1.0:
                particles.add_velocity((0.0, 1.0, 0.0))
                ctx.wait_frames(2)
            else:
                particles.multiply_size(0.5)
            particles.add_color((0.1, 0.2, 0.3, 0.0))

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="ConditionalMotion.particle.py")
    update = asset.emitters[0].update

    assert [node.type_id for node in update.nodes[1:]] == [
        "particle.attribute.get",
        "common.constant.f32",
        "common.compare.less_than",
        "particle.control.if",
        "particle.attribute.velocity",
        "particle.control.wait_frames",
        "particle.attribute.color",
        "particle.attribute.size",
        "particle.attribute.color",
    ]
    assert sum(node.type_id == "particle.attribute.color" for node in update.nodes) == 2

    emitter = compiler.compile(
        source, source_name="ConditionalMotion.particle.py"
    ).emitters[0]
    operations = tuple(emitter.update.flow.iter_operations())
    colors = [
        operation
        for operation in operations
        if operation.opcode == "attribute.modify_color"
    ]
    assert len(colors) == 2
    assert {color.execution_predicates[-1].expected for color in colors} == {
        False,
        True,
    }
    assert {color.execution_predicates[-1].source_node_uid for color in colors} == {
        "update.0.if"
    }
    assert emitter.update.flow.suspensions[0].resume_node_uid == "update.3.add_color"


def test_particle_script_rejects_imperative_loops_in_stage_control_flow():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class InvalidLoop(ParticleScript):
    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            while particles.age < 1.0:
                particles.add_size(0.1)

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    with pytest.raises(
        ParticleScriptError,
        match="stage bodies only allow particle operation calls and if/else",
    ):
        ParticleScriptCompiler().parse(
            source,
            source_name="InvalidLoop.particle.py",
        )


def test_particle_script_allows_wait_and_until_in_rendering_timeline():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class InvalidRenderingWait(ParticleScript):
    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            pass

        def rendering(self, ctx, particles):
            particles.set_size(0.5)
            ctx.until_seconds(1.0)
            ctx.wait_frames(2)
            particles.sprite()
'''
    program = ParticleScriptCompiler().compile(
        source,
        source_name="RenderingTimeline.particle.py",
    )
    suspensions = program.emitters[0].rendering.flow.suspensions
    assert [item.kind.value for item in suspensions] == ["until_seconds", "frames"]
    assert suspensions[-1].resume_node_uid == "rendering.3.sprite"


def test_particle_script_parameters_share_graph_hir_and_gpu_abi():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings, Parameter

class ParameterGraph(ParticleScript):
    stable_id = "parameter-graph"
    parameters = (
        Parameter(
            "wind-id",
            "Wind",
            "vec3",
            (1.0, 2.0, 3.0),
            category="Motion",
            tooltip="World-space wind velocity",
        ),
    )

    class Smoke(ParticleEmitter):
        stable_id = "smoke"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_velocity(ctx.parameter("Wind"))

        def update(self, ctx, particles):
            particles .add_velocity(ctx.parameter("wind-id"))

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="Parameters.particle.py")
    program = compiler.compile(source, source_name="Parameters.particle.py")
    graph_program = ParticleGraphCompiler().compile(asset)
    kernel = ParticleKernelLowerer().lower(program)

    assert asset.parameters == (
        ParticleParameter(
            "wind-id",
            "Wind",
            TypeRef(ValueType.VEC3),
            [1.0, 2.0, 3.0],
            category="Motion",
            tooltip="World-space wind velocity",
        ),
    )
    assert program.behavior_hash == graph_program.behavior_hash
    assert program.parameters[0].stable_id == "wind-id"
    assert program.parameters[0].slot == 0
    assert kernel.parameters[0].stable_id == "wind-id"
    assert any(
        instruction.opcode == "load_parameter"
        and instruction.immediate_dict()["parameter"] == "wind-id"
        for stage in (program.emitters[0].init, program.emitters[0].update)
        for instruction in stage.expressions.instructions
    )


def test_particle_script_rejects_unknown_parameter_reads():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class InvalidParameterGraph(ParticleScript):
    class Smoke(ParticleEmitter):
        stable_id = "smoke"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_velocity(ctx.parameter("missing"))

        def update(self, ctx, particles):
            pass

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    with pytest.raises(ParticleScriptError, match="unknown particle parameter 'missing'"):
        ParticleScriptCompiler().parse(source, source_name="InvalidParameter.particle.py")


def test_particle_script_texture2d_sample_shares_graph_hir_and_gpu_resource_abi():
    source = '''\
from infernux.particle import AssetReference, ParticleScript, ParticleEmitter, EmitterSettings, Parameter

class TextureParameterGraph(ParticleScript):
    stable_id = "texture-parameter-graph"
    parameters = (
        Parameter(
            "texture-id",
            "Particle Texture",
            "texture2d",
            AssetReference(path_hint="Assets/Smoke.tga"),
        ),
        Parameter("uv-id", "Sample UV", "vec2", (0.5, 0.5)),
    )

    class Smoke(ParticleEmitter):
        stable_id = "smoke"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_color(ctx.sample_texture2d(
                ctx.parameter("Particle Texture"),
                ctx.parameter("Sample UV"),
            ))

        def update(self, ctx, particles):
            pass

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="TextureParameter.particle.py")
    program = compiler.compile(source, source_name="TextureParameter.particle.py")
    kernel = ParticleKernelLowerer().lower(program)

    assert asset.parameters[0].value_type == TypeRef(ValueType.TEXTURE2D)
    assert asset.parameters[0].default == {
        "guid": "",
        "path_hint": "Assets/Smoke.tga",
    }
    sample = next(
        instruction
        for instruction in kernel.emitters[0].init.instructions
        if instruction.opcode == "sample_texture2d"
    )
    assert len(sample.operands) == 2
    assert sample.result_type == TypeRef(ValueType.COLOR)


def test_particle_script_typed_events_lower_through_the_graph_event_abi():
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(
        PARTICLE_SCRIPT_EVENT_SOURCE, source_name="Impact.particle.py"
    )
    hir = ParticleGraphCompiler().compile(asset)
    kernel = ParticleKernelLowerer().lower(hir)

    assert [value.stable_id for value in asset.event_types] == ["impact"]
    assert [value.event_id for value in asset.emitters[0].event_flows] == ["impact"]
    assert [value.flow_id for value in hir.emitters[0].event_flows] == ["impact"]
    assert any(
        instruction.opcode == "event_enqueue"
        for instruction in kernel.emitters[0].update.instructions
    )
    payloads = [
        instruction
        for instruction in kernel.emitters[0].update.instructions
        if instruction.opcode == "event_payload"
    ]
    assert [value.result_type.value_type.value for value in payloads] == [
        "f32",
        "color",
    ]


def test_particle_script_event_flow_can_write_and_read_its_attribute_cache():
    source = '''\
from infernux.particle import (
    ParticleScript, ParticleEmitter, EmitterSettings, EventType, event,
)

class EventCache(ParticleScript):
    event_types = (
        EventType(stable_id="pulse", name="Pulse", queue_capacity=8),
    )

    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(8.0)

        def update(self, ctx, particles):
            particles.trigger_event(event="pulse")

        def rendering(self, ctx, particles):
            particles.sprite()

        @event("pulse")
        def on_pulse(self, ctx, particles):
            particles.add_attribute("Invocation Count", 1.0)
            ctx.wait_frames(3)
            particles.kill_if(
                particles.get_attribute("Invocation Count") >= 3.0
            )
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="EventCache.particle.py")
    emitter = asset.emitters[0]
    event_flow = emitter.event_flows[0]
    cache = particle_cache_attributes(emitter)[0]

    assert cache.stable_id.startswith("cache.event.pulse.event.pulse.cache.")
    assert any(
        node.type_id == "particle.attribute.get"
        and node.properties.get("attribute") == cache.stable_id
        for node in event_flow.graph.nodes
    )

    hir = ParticleGraphCompiler().compile(asset)
    kernel = ParticleKernelLowerer().lower(hir)
    assert any(
        instruction.opcode == "store_attribute"
        and instruction.immediate_dict().get("attribute") == cache.stable_id
        for instruction in kernel.emitters[0].update.instructions
    )
    assert any(
        instruction.opcode == "kill_if"
        for instruction in kernel.emitters[0].update.instructions
    )
    assert any(
        suspension.flow_id == "pulse"
        and suspension.lifecycle_stage == "event"
        for suspension in kernel.emitters[0].suspensions
    )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            PARTICLE_SCRIPT_EVENT_SOURCE.replace(
                '@event("impact")', '@event("missing")'
            ),
            "unknown particle event",
        ),
        (
            PARTICLE_SCRIPT_EVENT_SOURCE.replace(
                '"energy": particles.size,', '"missing": particles.size,'
            ),
            "unknown event payload field",
        ),
        (
            PARTICLE_SCRIPT_EVENT_SOURCE.replace(
                "particles.set_size(2.0)",
                'particles.set_size(ctx.event_payload(field="energy"))',
            ),
            "event_payload is only available in an event flow",
        ),
    ],
)
def test_particle_script_typed_events_reject_invalid_events_and_payloads(
    source, message
):
    with pytest.raises(ParticleScriptError, match=message):
        ParticleScriptCompiler().parse(source, source_name="InvalidEvent.particle.py")


def test_particle_script_static_mesh_output_matches_graph_contract():
    source = PARTICLE_SCRIPT_SOURCE.replace(
        '''particles.sprite(
                shader="Particle Six-Way Smoke",
                receive_scene_lighting=True,
                receive_shadows=True,
                sort="back_to_front",
                alignment="velocity",
            )''',
        '''particles.mesh(
                mesh=AssetReference(guid="mesh-guid", path_hint="Assets/Models/Debris.fbx"),
                shader="Particle Unlit",
                cast_shadows=True,
                sort="none",
            )''',
    )
    source = source.replace(
        "particles.set_rotation(0.25)",
        "particles.set_orientation((10.0, 20.0, 30.0))\n            particles.set_scale((2.0, 0.5, 1.5))",
    ).replace(
        "particles .add_rotation(180.0)",
        "particles .add_orientation((90.0, 180.0, 270.0))",
    )

    emitter = ParticleScriptCompiler().compile(
        source, source_name="MeshOutput.particle.py"
    ).emitters[0]
    output = emitter.render_plan.outputs[0]

    assert output.output_type == "mesh"
    assert output.mesh.guid == "mesh-guid"
    assert output.cast_shadows is True
    default_output = ParticleScriptCompiler().compile(
        source.replace("                cast_shadows=True,\n", ""),
        source_name="MeshOutputDefault.particle.py",
    ).emitters[0].render_plan.outputs[0]
    assert default_output.cast_shadows is False
    graph_program = ParticleGraphCompiler().compile(
        ParticleScriptCompiler().parse(source, source_name="MeshOutput.particle.py")
    )
    assert graph_program.emitters[0].render_plan.outputs[0] == output
    assert output.shader == "Particle Unlit"
    assert output.sort_mode == "none"
    assert _operations(emitter.init)[-2].opcode == "attribute.modify_orientation"
    assert _operations(emitter.init)[-1].opcode == "attribute.modify_scale"
    assert _operations(emitter.update)[-1].opcode == "attribute.modify_orientation"
    assert "builtin.orientation" in {
        attribute.stable_id for attribute in emitter.attributes
    }
    assert "builtin.scale" in {
        attribute.stable_id for attribute in emitter.attributes
    }


def test_particle_script_mesh_parameter_connects_to_output_mesh_port():
    source = '''\
from infernux.particle import AssetReference, ParticleScript, ParticleEmitter, EmitterSettings, Parameter

class MeshParameterOutput(ParticleScript):
    parameters = (
        Parameter(
            "output-mesh",
            "Output Mesh",
            "mesh",
            AssetReference(guid="mesh-guid", path_hint="Assets/Models/Debris.fbx"),
        ),
    )

    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            pass

        def rendering(self, ctx, particles):
            particles.mesh(mesh=ctx.parameter("Output Mesh"))
'''
    asset = ParticleScriptCompiler().parse(
        source,
        source_name="MeshParameterOutput.particle.py",
    )
    output_node = next(
        node
        for node in asset.emitters[0].rendering.nodes
        if node.type_id == "particle.output.mesh"
    )
    assert "mesh" not in output_node.properties
    assert any(
        link.source_node.startswith("rendering.expr.")
        and link.target_node == output_node.uid
        and link.target_port == "mesh"
        for link in asset.emitters[0].rendering.links
    )

    output = ParticleGraphCompiler().compile(asset).emitters[0].render_plan.outputs[0]
    assert output.mesh_parameter == "output-mesh"


def test_ribbon_output_has_stable_topology_attributes_and_script_parity():
    source = '''
from infernux.particle import AssetReference, ParticleScript, ParticleEmitter, EmitterSettings

class TrailGraph(ParticleScript):
    class Trail(ParticleEmitter):
        stable_id = "trail"
        settings = EmitterSettings(capacity=4096)

        def init(self, ctx, particles):
            particles.set_strip_id(7)
            particles.set_ribbon_order(11)
            particles.break_ribbon(False)

        def update(self, ctx, particles):
            particles.set_ribbon_order(12)

        def rendering(self, ctx, particles):
            particles.ribbon(
                shader="Particle Unlit",
                uv_mode="repeat",
                uv_scale=2.5,
            )
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="Trail.particle.py")
    program = compiler.compile(source, source_name="Trail.particle.py")
    graph_program = ParticleGraphCompiler().compile(asset)
    emitter = program.emitters[0]
    output = emitter.render_plan.outputs[0]

    assert program.behavior_hash == graph_program.behavior_hash
    assert output.output_type == "ribbon"
    assert output.ribbon_uv_mode == "repeat"
    assert output.ribbon_uv_scale == pytest.approx(2.5)
    assert output.sort_mode == "none"
    assert [operation.opcode for operation in _operations(emitter.init)[-3:]] == [
        "attribute.modify_strip_id",
        "attribute.modify_ribbon_order",
        "attribute.modify_ribbon_break",
    ]
    assert {
        "builtin.ribbon_strip_id",
        "builtin.ribbon_order",
        "builtin.ribbon_break",
    }.issubset({attribute.stable_id for attribute in emitter.attributes})


def test_plane_collision_graph_and_script_share_terminal_update_contract():
    source = '''
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class CollisionGraph(ParticleScript):
    class Sparks(ParticleEmitter):
        stable_id = "sparks"
        settings = EmitterSettings(capacity=1024)

        def init(self, ctx, particles):
            particles.set_velocity((1.0, -2.0, 0.0))

        def update(self, ctx, particles):
            particles .add_velocity((0.0, -9.81, 0.0))
            particles.collide_plane(
                point=(0.0, 0.0, 0.0),
                normal=(0.0, 1.0, 0.0),
                radius=0.1,
                restitution=0.6,
                friction=0.2,
            )

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="Collision.particle.py")
    emitter = compiler.compile(source, source_name="Collision.particle.py").emitters[0]

    assert _operations(emitter.update)[-1].opcode == "collision.plane"
    assert _operations(emitter.update)[-1].parameter_dict() == {
        "friction": 0.2,
        "normal": [0.0, 1.0, 0.0],
        "point": [0.0, 0.0, 0.0],
        "radius": 0.1,
        "restitution": 0.6,
    }
    assert emitter == ParticleGraphCompiler().compile(asset).emitters[0]


@pytest.mark.parametrize(
    "properties, message",
    [
        ({"normal": [0.0, 0.0, 0.0]}, "normal must be non-zero"),
        ({"radius": -0.1}, "radius must be non-negative"),
        ({"restitution": 1.1}, "restitution must be between 0 and 1"),
        ({"friction": -0.1}, "friction must be between 0 and 1"),
    ],
)
def test_plane_collision_rejects_invalid_static_parameters(properties, message):
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "collision",
                "particle.collision.plane",
                properties=properties,
            ),
        ),
        links=(
            GraphLinkRecord(
                "stream", "root.update", "out", "collision", "in", PortKind.EXEC
            ),
        ),
    )
    with pytest.raises(ParticleCompileError, match=message):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )


def test_sphere_collision_graph_and_script_share_terminal_update_contract():
    source = '''
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class CollisionGraph(ParticleScript):
    class Sparks(ParticleEmitter):
        stable_id = "sparks"
        settings = EmitterSettings(capacity=1024)

        def init(self, ctx, particles):
            particles.set_velocity((0.0, 0.0, 0.0))

        def update(self, ctx, particles):
            particles.collide_sphere(
                center=(1.0, 2.0, 3.0),
                sphere_radius=2.0,
                particle_radius=0.1,
                restitution=0.7,
                friction=0.25,
            )

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="SphereCollision.particle.py")
    emitter = compiler.compile(
        source, source_name="SphereCollision.particle.py"
    ).emitters[0]

    assert _operations(emitter.update)[-1].opcode == "collision.sphere"
    assert _operations(emitter.update)[-1].parameter_dict() == {
        "center": [1.0, 2.0, 3.0],
        "friction": 0.25,
        "particle_radius": 0.1,
        "restitution": 0.7,
        "sphere_radius": 2.0,
    }
    assert emitter == ParticleGraphCompiler().compile(asset).emitters[0]


def test_scene_collision_graph_and_script_share_gpu_scene_contract():
    source = '''
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class CollisionGraph(ParticleScript):
    class Sparks(ParticleEmitter):
        stable_id = "sparks"
        settings = EmitterSettings(
            capacity=1024,
            collision_enabled=True,
            collision_layer_mask=21,
            collision_include_triggers=False,
            collision_bounce_scale=1.5,
            collision_friction_scale=0.25,
        )

        def init(self, ctx, particles):
            particles.set_velocity((0.0, 0.0, 0.0))

        def update(self, ctx, particles):
            pass

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    script_hir = compiler.compile(
        source, source_name="SceneCollision.particle.py"
    )
    graph_hir = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            emitters=(
                ParticleEmitterAsset(
                    stable_id="sparks",
                    settings=EmitterSettings(
                        capacity=1024,
                        collision_enabled=True,
                        collision_layer_mask=21,
                        collision_include_triggers=False,
                        collision_bounce_scale=1.5,
                        collision_friction_scale=0.25,
                    ),
                ),
            ),
        )
    )

    assert not any(
        operation.opcode == "collision.scene"
        for program in (script_hir, graph_hir)
        for operation in program.emitters[0].update.flow.iter_operations()
    )
    collision_instructions = []
    for program in (script_hir, graph_hir):
        instructions = ParticleKernelLowerer().lower(program).emitters[0].update.instructions
        collisions = [item for item in instructions if item.opcode == "collide_scene"]
        assert len(collisions) == 1
        collision_instructions.append(collisions[0])
    assert [operand.value_type for operand in collision_instructions[0].operands] == [
        operand.value_type for operand in collision_instructions[1].operands
    ]
    assert collision_instructions[0].immediate_dict() == (
        collision_instructions[1].immediate_dict()
    )
    for program, collision in zip((script_hir, graph_hir), collision_instructions):
        instructions = ParticleKernelLowerer().lower(program).emitters[0].update.instructions
        constants = {
            instruction.result_id: instruction.immediate_dict()["value"]
            for instruction in instructions
            if instruction.opcode == "constant"
        }
        assert [constants[operand.value_id] for operand in collision.operands[3:]] == [
            21,
            False,
            1.5,
            0.25,
        ]


def test_particle_script_scene_collision_events_read_current_hit_and_normal():
    source = '''
from infernux.particle import (
    ParticleScript, ParticleEmitter, EmitterSettings,
    EventField, EventType, event,
)

class CollisionEvents(ParticleScript):
    event_types = (
        EventType(
            stable_id="impact",
            name="Impact",
            queue_capacity=64,
            fields=(
                EventField(
                    "normal", "Normal", "vec3", (0.0, 1.0, 0.0),
                    space="simulation",
                ),
            ),
        ),
    )
    class Source(ParticleEmitter):
        stable_id = "source"
        settings = EmitterSettings(capacity=1024, collision_enabled=True)

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            pass

        def collision_enter(self, ctx, particles):
            particles.trigger_event(
                event="impact",
                condition=particles.collision_hit,
                payload={"normal": particles.collision_normal},
            )

        @event("impact")
        def on_impact(self, ctx, particles):
            particles.set_velocity(ctx.event_payload(field="normal"))

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    program = ParticleScriptCompiler().compile(
        source, source_name="CollisionEvents.particle.py"
    )
    assert [
        operation.opcode
        for operation in program.emitters[0].collision_enter.flow.iter_operations()
    ] == ["event.trigger"]

    kernel = ParticleKernelLowerer().lower(program)
    update_instructions = kernel.emitters[0].update.instructions
    contact_instructions = kernel.emitters[0].contact.instructions
    collision_index = next(
        index for index, instruction in enumerate(update_instructions)
        if instruction.opcode == "collide_scene"
    )
    event_index = next(
        index for index, instruction in enumerate(contact_instructions)
        if instruction.opcode == "event_enqueue"
    )
    collision = update_instructions[collision_index]
    assert collision.immediate_dict()["hit_attribute"] == "builtin.collision_hit"
    assert collision.immediate_dict()["normal_attribute"] == "builtin.collision_normal"
    assert any(
        index < event_index
        and instruction.opcode == "load_attribute"
        and instruction.immediate_dict()["attribute"] == "builtin.collision_hit"
        for index, instruction in enumerate(contact_instructions)
    )
    assert any(
        index < event_index
        and instruction.opcode == "load_attribute"
        and instruction.immediate_dict()["attribute"] == "builtin.collision_normal"
        for index, instruction in enumerate(contact_instructions)
    )


def test_particle_script_rejects_removed_scene_collision_operation():
    source = '''
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class RemovedCollisionNode(ParticleScript):
    class Sparks(ParticleEmitter):
        stable_id = "sparks"
        settings = EmitterSettings(collision_enabled=True)

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            particles.collide_scene()

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    with pytest.raises(ParticleScriptError, match="collide_scene"):
        ParticleScriptCompiler().compile(
            source, source_name="RemovedCollisionNode.particle.py"
        )


def test_particle_script_rejects_sdf_collision_authoring():
    source = '''
from infernux.particle import AssetReference, ParticleScript, ParticleEmitter, EmitterSettings, SdfVolume

class CollisionGraph(ParticleScript):
    class Sparks(ParticleEmitter):
        stable_id = "sparks"
        settings = EmitterSettings(capacity=1024)
        data_interfaces = (
            SdfVolume(
                stable_id="collision-field",
                texture=AssetReference(guid="sdf-texture"),
            ),
        )

        def init(self, ctx, particles):
            particles.set_velocity((0.0, 0.0, 0.0))

        def update(self, ctx, particles):
            particles.collide_sdf(
                interface="collision-field",
                particle_radius=0.1,
                restitution=0.7,
                friction=0.25,
                inverted=True,
            )

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    with pytest.raises(ParticleScriptError, match="SDF authoring is not available"):
        ParticleScriptCompiler().parse(source, source_name="SdfCollision.particle.py")


@pytest.mark.parametrize(
    "properties, message",
    [
        ({"sphere_radius": -0.1}, "sphere_radius must be non-negative"),
        ({"particle_radius": -0.1}, "particle_radius must be non-negative"),
        ({"restitution": 1.1}, "restitution must be between 0 and 1"),
        ({"friction": -0.1}, "friction must be between 0 and 1"),
    ],
)
def test_sphere_collision_rejects_invalid_static_parameters(properties, message):
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "collision",
                "particle.collision.sphere",
                properties=properties,
            ),
        ),
        links=(
            GraphLinkRecord(
                "stream", "root.update", "out", "collision", "in", PortKind.EXEC
            ),
        ),
    )
    with pytest.raises(ParticleCompileError, match=message):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )


@pytest.mark.parametrize(
    "properties, message",
    [
        ({"sort": "back_to_front"}, "requires sort='none'"),
        ({"uv_mode": "projected"}, "requires uv_mode"),
        ({"uv_scale": 0.0}, "finite positive uv_scale"),
    ],
)
def test_ribbon_output_rejects_ambiguous_topology_and_uv_settings(properties, message):
    rendering = GraphDocument(
        "particle.rendering",
        nodes=(
            GraphNodeRecord("root.rendering", "particle.root.rendering"),
            GraphNodeRecord("ribbon", "particle.output.ribbon", properties=properties),
        ),
        links=(
            GraphLinkRecord(
                "render-stream", "root.rendering", "out", "ribbon", "in", PortKind.EXEC
            ),
        ),
    )
    asset = ParticleGraphAsset(
        emitters=(ParticleEmitterAsset(rendering=rendering),)
    )
    with pytest.raises(ParticleCompileError, match=message):
        ParticleGraphCompiler().compile(asset)


def test_particle_script_vector_field_expression_matches_graph_kernel_contract():
    source = PARTICLE_SCRIPT_SOURCE.replace(
        "particles .add_velocity((0.0, -0.2, 0.0))",
        'particles .add_velocity(ctx.sample_vector_field("wind-field", particles.position))',
    )
    asset = ParticleScriptCompiler().parse(source, source_name="Wind.particle.py")
    update = asset.emitters[0].update

    assert [node.type_id for node in update.nodes] == [
        "particle.root.update",
        "particle.attribute.get",
        "particle.vector_field.sample",
        "particle.attribute.velocity",
        "particle.attribute.rotation",
    ]
    assert any(
        link.source_node.endswith("sample_vector_field")
        and link.target_node.endswith("add_velocity")
        and link.kind is PortKind.VALUE
        for link in update.links
    )

    hir = ParticleGraphCompiler().compile(asset)
    kernel = ParticleKernelLowerer().lower(hir)
    sample = next(
        instruction
        for instruction in kernel.emitters[0].update.instructions
        if instruction.opcode == "sample_vector_field"
    )
    assert sample.immediate_dict() == {"interface": "wind-field"}


def test_particle_script_rejects_sdf_sampling_authoring():
    source = '''\
from infernux.particle import AssetReference, ParticleScript, ParticleEmitter, EmitterSettings, SdfVolume

class SdfSampleGraph(ParticleScript):
    stable_id = "sdf-sample-graph"

    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()
        data_interfaces = (
            SdfVolume(
                stable_id="shape",
                texture=AssetReference(path_hint="Assets/Fields/Shape.inxsdf"),
            ),
        )

        def init(self, ctx, particles):
            particles.set_lifetime(1.0)

        def update(self, ctx, particles):
            particles.set_size(ctx.sample_sdf_distance("shape", particles.position))
            particles.set_velocity(ctx.sample_sdf_gradient("shape", particles.position))

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    with pytest.raises(ParticleScriptError, match="SDF authoring is not available"):
        ParticleScriptCompiler().parse(source, source_name="SdfSample.particle.py")


def test_particle_script_target_position_is_a_stateless_motion_operation():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class TargetMotion(ParticleScript):
    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(4.0)

        def update(self, ctx, particles):
            particles.target_position(
                (2.0, 3.0, 4.0),
                speed=6.0,
                responsiveness=10.0,
                arrival_radius=0.25,
            )

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    asset = ParticleScriptCompiler().parse(source, source_name="TargetMotion.particle.py")
    update = asset.emitters[0].update
    target_node = next(
        node for node in update.nodes if node.type_id == "particle.motion.target_position"
    )
    assert target_node.properties == {
        "target": [2.0, 3.0, 4.0],
        "speed": 6.0,
        "responsiveness": 10.0,
        "arrival_radius": 0.25,
    }

    hir = ParticleGraphCompiler().compile(asset)
    operation = next(
        operation
        for operation in hir.emitters[0].update.flow.iter_operations()
        if operation.opcode == "motion.target_position"
    )
    assert operation.parameter_dict()["target"] == [2.0, 3.0, 4.0]
    kernel = ParticleKernelLowerer().lower(hir).emitters[0]
    opcodes = [instruction.opcode for instruction in kernel.update.instructions]
    assert "target_position_velocity" in opcodes
    assert "target" not in {
        attribute.stable_id for attribute in hir.emitters[0].attributes
    }


def test_particle_script_curve_and_gradient_compile_to_shared_kernel_operations():
    source = PARTICLE_SCRIPT_SOURCE.replace(
        "from infernux.particle import AssetReference, ParticleScript, ParticleEmitter, EmitterSettings, VectorField",
        "from infernux.particle import AssetReference, ParticleScript, ParticleEmitter, EmitterSettings, VectorField, AnimationCurve, Keyframe, Gradient, GradientKey",
    ).replace(
        "particles .add_velocity((0.0, -0.2, 0.0))",
        """particles.set_size(ctx.sample_curve(
                AnimationCurve(keys=(Keyframe(0.0, 0.0, 1.0, 1.0), Keyframe(1.0, 1.0, 1.0, 1.0))),
                particles.age / particles.lifetime,
            ))
            particles.set_color(ctx.sample_gradient(
                Gradient(keys=(GradientKey(0.0, (1.0, 0.0, 0.0, 1.0)), GradientKey(1.0, (0.0, 0.0, 1.0, 0.0)))),
                particles.age / particles.lifetime,
            ))""",
    )
    asset = ParticleScriptCompiler().parse(source, source_name="Ramp.particle.py")
    kernel = ParticleKernelLowerer().lower(ParticleGraphCompiler().compile(asset))
    opcodes = [instruction.opcode for instruction in kernel.emitters[0].update.instructions]

    assert "sample_curve" in opcodes
    assert "sample_gradient" in opcodes
    assert opcodes.count("divide") == 2
    assert opcodes.count("store_attribute") >= 4


def test_particle_script_curve_and_gradient_parameters_share_dynamic_kernel_path():
    source = '''\
from infernux.particle import (
    AnimationCurve, Gradient, Parameter, ParticleScript, ParticleEmitter, EmitterSettings,
)

class RampParameters(ParticleScript):
    parameters = (
        Parameter("size", "Size", "curve", AnimationCurve()),
        Parameter("color", "Color", "gradient", Gradient()),
    )

    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(2.0)

        def update(self, ctx, particles):
            particles.set_size(ctx.sample_curve(
                ctx.parameter("size"), particles.normalized_age
            ))
            particles.set_color(ctx.sample_gradient(
                ctx.parameter("color"), particles.normalized_age
            ))

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    asset = ParticleScriptCompiler().parse(
        source,
        source_name="RampParameters.particle.py",
    )
    update = asset.emitters[0].update
    assert any(
        link.target_port == "curve"
        and next(node for node in update.nodes if node.uid == link.source_node).type_id
        == "particle.parameter"
        for link in update.links
    )
    assert any(
        link.target_port == "gradient"
        and next(node for node in update.nodes if node.uid == link.source_node).type_id
        == "particle.parameter"
        for link in update.links
    )

    kernel = ParticleKernelLowerer().lower(ParticleGraphCompiler().compile(asset))
    opcodes = [item.opcode for item in kernel.emitters[0].update.instructions]
    assert "sample_curve_parameter" in opcodes
    assert "sample_gradient_parameter" in opcodes


def test_particle_script_comparisons_and_kill_if_lower_to_composable_lifecycle_ops():
    source = PARTICLE_SCRIPT_SOURCE.replace(
        "particles .add_velocity((0.0, -0.2, 0.0))",
        "particles.kill_if(particles.age >= 0.5)",
    )

    asset = ParticleScriptCompiler().parse(source, source_name="Kill.particle.py")
    update = asset.emitters[0].update
    kernel = ParticleKernelLowerer().lower(ParticleGraphCompiler().compile(asset))
    opcodes = [instruction.opcode for instruction in kernel.emitters[0].update.instructions]

    assert any(node.type_id == "common.compare.greater_equal" for node in update.nodes)
    assert any(node.type_id == "particle.lifecycle.kill_if" for node in update.nodes)
    assert "greater_equal" in opcodes
    assert opcodes.count("kill_if") == 2


def test_particle_script_boolean_primitives_lower_to_common_graph_ops():
    source = PARTICLE_SCRIPT_SOURCE.replace(
        "particles .add_velocity((0.0, -0.2, 0.0))",
        "particles.kill_if(not ((particles.age == 0.5) or (particles.lifetime != 1.0 and False)))",
    )

    asset = ParticleScriptCompiler().parse(source, source_name="Boolean.particle.py")
    update = asset.emitters[0].update
    kernel = ParticleKernelLowerer().lower(ParticleGraphCompiler().compile(asset))
    node_types = {node.type_id for node in update.nodes}
    opcodes = {instruction.opcode for instruction in kernel.emitters[0].update.instructions}

    assert {
        "common.constant.bool",
        "common.compare.equal",
        "common.compare.not_equal",
        "common.logic.and",
        "common.logic.or",
        "common.logic.not",
    } <= node_types
    assert {
        "equal",
        "not_equal",
        "logical_and",
        "logical_or",
        "logical_not",
    } <= opcodes


def test_particle_script_noise_compiles_to_the_shared_portable_kernel_ops():
    source = PARTICLE_SCRIPT_SOURCE.replace(
        "particles .add_velocity((0.0, -0.2, 0.0))",
        "particles .add_velocity(ctx.vector_noise_3d(particles.position, frequency=2.5, seed=17))",
    ).replace(
        "particles .add_rotation(180.0)",
        "particles.set_size(ctx.value_noise_3d(particles.position, 4.0, 23))",
    )

    asset = ParticleScriptCompiler().parse(source, source_name="Noise.particle.py")
    kernel = ParticleKernelLowerer().lower(ParticleGraphCompiler().compile(asset))
    opcodes = [instruction.opcode for instruction in kernel.emitters[0].update.instructions]

    assert "vector_noise_3d" in opcodes
    assert "value_noise_3d" in opcodes
    vector_noise = next(
        node
        for node in asset.emitters[0].update.nodes
        if node.type_id == "common.noise.vector3d"
    )
    assert vector_noise.properties == {"frequency": 2.5, "seed": 17}


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (
            'ctx.sample_vector_field("missing", particles.position)',
            "unknown data interface",
        ),
        (
            'ctx.read_internal_wheel(particles.position)',
            "unsupported particle context expression",
        ),
        (
            'ctx.sample_vector_field("wind-field", particles.private_state)',
            "unsupported particle attribute",
        ),
    ],
)
def test_particle_script_vector_field_expression_rejects_unknown_or_private_access(replacement, message):
    source = PARTICLE_SCRIPT_SOURCE.replace(
        "particles .add_velocity((0.0, -0.2, 0.0))",
        f"particles .add_velocity({replacement})",
    )

    if message == "unknown data interface":
        asset = ParticleScriptCompiler().parse(source, source_name="InvalidWind.particle.py")
        with pytest.raises(KernelCompileError, match=message):
            ParticleKernelLowerer().lower(ParticleGraphCompiler().compile(asset))
    else:
        with pytest.raises(ParticleScriptError, match=message):
            ParticleScriptCompiler().parse(source, source_name="InvalidWind.particle.py")


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ('sort="distance"', "unsupported sort mode"),
        (
            "receive_scene_lighting=False,\n                receive_shadows=True,\n                sort=\"back_to_front\"",
            "cannot receive shadows",
        ),
    ],
)
def test_particle_output_rejects_invalid_render_semantics(replacement, message):
    source = PARTICLE_SCRIPT_SOURCE.replace(
        'receive_scene_lighting=True,\n                receive_shadows=True,\n                sort="back_to_front"',
        replacement,
    )

    with pytest.raises(ParticleCompileError, match=message):
        ParticleScriptCompiler().compile(source, source_name="InvalidOutput.particle.py")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("open('side-effect.txt', 'w')\n" + PARTICLE_SCRIPT_SOURCE, "unsupported top-level"),
        (
            PARTICLE_SCRIPT_SOURCE.replace("particles.set_lifetime(6.0)", "particles.set_lifetime(get_lifetime())"),
            "literal data",
        ),
        (
            PARTICLE_SCRIPT_SOURCE.replace(
                "        def update(self, ctx, particles):\n            particles .add_velocity((0.0, -0.2, 0.0))\n            particles .add_rotation(180.0)\n\n",
                "",
            ),
            "missing=['update']",
        ),
    ],
)
def test_particle_script_rejects_executable_or_incomplete_python(source, message):
    with pytest.raises(ParticleScriptError, match=message.replace("[", r"\[").replace("]", r"\]")):
        ParticleScriptCompiler().parse(source, source_name="Invalid.particle.py")


def test_particle_graph_and_script_save_to_equivalent_aot_artifacts(tmp_path, monkeypatch):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    graph_path = tmp_path / "Assets" / "Smoke.particlegraph"
    script_path = tmp_path / "Assets" / "Smoke.particle.py"
    graph_path.parent.mkdir()
    script_path.write_text(PARTICLE_SCRIPT_SOURCE, encoding="utf-8")
    graph_asset = ParticleScriptCompiler().parse(
        PARTICLE_SCRIPT_SOURCE,
        source_name=str(script_path),
    )
    graph_asset.save(str(graph_path))

    graph_artifact = ParticleArtifactRegistry.get(str(graph_path))
    script_artifact = ParticleArtifactRegistry.compile_path(str(script_path))

    assert graph_artifact is not None
    assert graph_artifact.behavior_hash == script_artifact.behavior_hash
    assert graph_artifact.hir["schedule"] == ["smoke"]
    assert graph_artifact.kernel_ir["$schema"] == "infernux.particle_kernel_ir"
    assert graph_artifact.kernel_ir["source_behavior_hash"] == graph_artifact.behavior_hash
    assert graph_artifact.kernel_ir["kernel_hash"] == script_artifact.kernel_ir["kernel_hash"]
    assert graph_artifact.gpu_glsl["$schema"] == "infernux.particle_gpu_glsl"
    assert graph_artifact.gpu_glsl["kernel_hash"] == graph_artifact.kernel_ir["kernel_hash"]
    assert [
        value["stable_id"]
        for value in graph_artifact.gpu_glsl["emitters"][0]["data_interfaces"]
    ] == ["wind-field"]
    assert set(graph_artifact.gpu_glsl["emitters"][0]["stages"]) == {
            "bootstrap",
            "init",
            "update",
            "contact_prepare",
            "contact_solve",
            "contact_dispatch",
            "render_reset",
        "rendering",
    }
    assert graph_artifact.gpu_spirv["target"] == "vulkan1.2-spirv1.5"
    assert graph_artifact.gpu_spirv["kernel_hash"] == graph_artifact.kernel_ir["kernel_hash"]
    assert set(graph_artifact.gpu_spirv["emitters"][0]["stages"]) == set(
        graph_artifact.gpu_glsl["emitters"][0]["stages"]
    )
    assert set(graph_artifact.gpu_spirv["billboard"]) == {
        "vertex",
        "picking_fragment",
        "motion_vertex",
        "motion_fragment",
    }
    assert set(graph_artifact.gpu_spirv["mesh"]) == {
        "vertex",
        "shadow_fragment",
        "picking_fragment",
        "motion_vertex",
        "motion_fragment",
    }
    from infernux.particle import decode_gpu_particle_spirv

    decoded = decode_gpu_particle_spirv(graph_artifact.gpu_spirv, 0)
    assert decoded["stable_id"] == "smoke"
    assert set(decoded["stages"]) == set(graph_artifact.gpu_glsl["emitters"][0]["stages"])
    assert set(decoded["billboard"]) == {
        "vertex",
        "picking_fragment",
        "motion_vertex",
        "motion_fragment",
    }
    assert set(decoded["mesh"]) == {
        "vertex",
        "shadow_fragment",
        "picking_fragment",
        "motion_vertex",
        "motion_fragment",
    }
    assert all(binary[:4] == b"\x03\x02#\x07" for binary in decoded["stages"].values())
    assert all(binary[:4] == b"\x03\x02#\x07" for binary in decoded["billboard"].values())
    assert all(binary[:4] == b"\x03\x02#\x07" for binary in decoded["mesh"].values())
    assert script_artifact.hir["emitters"][0]["render_plan"][0][
        "receive_scene_lighting"
    ] is True
    assert script_artifact.hir["emitters"][0]["render_plan"][0]["cast_shadows"] is False
    runtime_metadata = decode_particle_runtime_metadata(script_artifact.hir)
    assert runtime_metadata.emitters[0].outputs[0].cast_shadows is False
    stale_hir = copy.deepcopy(script_artifact.hir)
    stale_hir["emitters"][0]["render_plan"][0].pop("sprite_alignment")
    with pytest.raises(ParticleRuntimeMetadataError, match="is invalid"):
        decode_particle_runtime_metadata(stale_hir)
    assert graph_artifact.artifact_path.endswith("smoke-graph.inxparticle")


def test_particle_runtime_index_loads_aot_without_authoring_source(tmp_path, monkeypatch):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    source_path = tmp_path / "Assets" / "Smoke.particle.py"
    source_path.parent.mkdir()
    source_path.write_text(PARTICLE_SCRIPT_SOURCE, encoding="utf-8")
    compiled = ParticleArtifactRegistry.compile_path(str(source_path), guid="smoke-guid")
    assert Path(compiled.artifact_path).is_file()

    runtime_index = Path(compiled.artifact_path).parent / "RuntimeIndex.json"
    runtime_index.write_text(
        json.dumps(
            {
                "$schema": "infernux.particle_runtime_index",
                "entries": [
                    {
                        "guid": "smoke-guid",
                        "path_hint": "Assets/Smoke.particle.py",
                        "stable_id": Path(compiled.artifact_path).stem,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source_path.unlink()
    ParticleArtifactRegistry.clear()

    restored = ParticleArtifactRegistry.load_runtime_reference(guid="smoke-guid")

    assert restored is not None
    assert restored.source_kind == "script"
    assert restored.behavior_hash == compiled.behavior_hash
    assert restored.artifact_path == compiled.artifact_path


def test_particle_force_recompile_replaces_registered_artifact(
    tmp_path, monkeypatch
):
    from infernux.engine import project_context

    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    source_path = tmp_path / "Assets" / "ForceRecovery.particlegraph"
    source_path.parent.mkdir()
    ParticleGraphAsset(stable_id="force-recovery").save(str(source_path))
    current = ParticleArtifactRegistry.get(str(source_path))
    assert current is not None

    stale = replace(current, gpu_glsl={"emitters": []}, gpu_spirv={})
    source_key = ParticleArtifactRegistry._source_key(str(source_path))
    with ParticleArtifactRegistry._lock:
        ParticleArtifactRegistry._register_unlocked(stale, source_key, source_key)

    rebuilt = ParticleArtifactRegistry.compile_path(
        str(source_path), force_recompile=True
    )

    assert rebuilt is not stale
    assert rebuilt.gpu_glsl["$schema"] == "infernux.particle_gpu_glsl"
    assert ParticleArtifactRegistry.get(str(source_path)) is rebuilt


def test_particle_graph_save_compiles_the_in_memory_snapshot_once(tmp_path, monkeypatch):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "SingleSnapshot.particlegraph"
    asset = ParticleGraphAsset(stable_id="single-snapshot")
    original_compile = ParticleArtifactRegistry._compile_graph_asset
    compiled_assets = []

    def compile_once(cls, candidate, source_path):
        compiled_assets.append(candidate)
        return original_compile(candidate, source_path)

    monkeypatch.setattr(
        ParticleArtifactRegistry,
        "_compile_graph_asset",
        classmethod(compile_once),
    )
    monkeypatch.setattr(
        ParticleArtifactRegistry,
        "compile_path",
        classmethod(
            lambda cls, *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("graph save must not re-read and compile its disk file")
            )
        ),
    )

    asset.save(str(path))

    assert compiled_assets == [asset]
    assert ParticleGraphAsset.load(str(path)) == asset
    persisted = ParticleArtifactRegistry.get(str(path))
    assert persisted is not None

    runtime_index = json.loads(
        (tmp_path / "Library" / "Artifacts" / "Particle" / "RuntimeIndex.json")
        .read_text(encoding="utf-8")
    )
    assert runtime_index == {
        "$schema": "infernux.particle_runtime_index",
        "entries": [
            {
                "guid": "",
                "path_hint": "Assets/SingleSnapshot.particlegraph",
                "stable_id": "single-snapshot",
            }
        ],
    }

    path.unlink()
    ParticleArtifactRegistry.clear()
    assert (
        ParticleArtifactRegistry.load_runtime_reference(
            guid="scene-reference-guid"
        )
        is None
    )


def test_particle_runtime_reference_never_uses_path_or_stable_id_fallback(
    tmp_path, monkeypatch
):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    source_path = tmp_path / "Assets" / "PathFallback.particlegraph"
    source_path.parent.mkdir()
    ParticleGraphAsset(stable_id="path-fallback").save(str(source_path))
    fallback_artifact = (
        tmp_path
        / "Library"
        / "Artifacts"
        / "Particle"
        / "path-fallback.inxparticle"
    )
    assert fallback_artifact.is_file()

    runtime_index = fallback_artifact.parent / "RuntimeIndex.json"
    runtime_index.write_text(
        json.dumps(
            {
                "$schema": "infernux.particle_runtime_index",
                "entries": [
                    {
                        "guid": "different-guid",
                        "path_hint": "Assets/PathFallback.particlegraph",
                        "stable_id": "path-fallback",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source_path.unlink()
    ParticleArtifactRegistry.clear()

    assert (
        ParticleArtifactRegistry.load_runtime_reference(guid="requested-guid")
        is None
    )


def test_particle_artifacts_are_indexed_by_asset_guid(tmp_path, monkeypatch):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    assets = tmp_path / "Assets"
    assets.mkdir()
    portal_guid = "a" * 32
    trail_guid = "b" * 32
    ParticleArtifactRegistry.save_graph_asset(
        ParticleGraphAsset(stable_id="shared-graph", name="portal"),
        str(assets / "Portal.particlegraph"),
        guid=portal_guid,
    )
    ParticleArtifactRegistry.save_graph_asset(
        ParticleGraphAsset(stable_id="shared-graph", name="trail"),
        str(assets / "Trail.particlegraph"),
        guid=trail_guid,
    )

    artifact_root = tmp_path / "Library" / "Artifacts" / "Particle"
    assert (artifact_root / f"{portal_guid}.inxparticle").is_file()
    assert (artifact_root / f"{trail_guid}.inxparticle").is_file()
    index = json.loads((artifact_root / "RuntimeIndex.json").read_text(encoding="utf-8"))
    assert {entry["guid"] for entry in index["entries"]} == {portal_guid, trail_guid}


def test_particle_artifact_uses_meta_guid_when_compile_omits_guid(tmp_path, monkeypatch):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "MetaOwned.particlegraph"
    path.parent.mkdir()
    guid = "c" * 32
    Path(str(path) + ".meta").write_text(
        json.dumps(
            {
                "metadata": {
                    "guid": {"type": "string", "value": guid},
                }
            }
        ),
        encoding="utf-8",
    )
    ParticleGraphAsset(stable_id="meta-owned").save(str(path))

    assert (
        tmp_path / "Library" / "Artifacts" / "Particle" / f"{guid}.inxparticle"
    ).is_file()


def test_particle_graph_artifact_hash_ignores_json_formatting(tmp_path, monkeypatch):
    from infernux.engine import project_context
    from infernux.particle import artifact as artifact_module

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Formatting.particlegraph"
    asset = ParticleGraphAsset(stable_id="formatting-independent")
    asset.save(str(path))
    current = ParticleArtifactRegistry.get(str(path))
    assert current is not None

    path.write_text(
        json.dumps(asset.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    ParticleArtifactRegistry.clear()

    def fail_compile(_self, _asset, *, source_name=""):
        raise AssertionError("format-only edits must reuse the persisted graph artifact")

    monkeypatch.setattr(artifact_module.ParticleGraphCompiler, "compile", fail_compile)
    restored = ParticleArtifactRegistry.compile_path(str(path))

    assert restored.revision == current.revision
    assert restored.source_hash == current.source_hash
    assert restored.behavior_hash == current.behavior_hash


def test_particle_graph_rebuilds_deterministically_after_library_cleanup(
    tmp_path, monkeypatch
):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Rebuild.particlegraph"
    asset = ParticleGraphAsset(stable_id="deterministic-rebuild")
    asset.save(str(path))
    first = ParticleArtifactRegistry.get(str(path))
    assert first is not None

    artifact_path = Path(first.artifact_path)
    artifact_path.unlink()
    runtime_index = artifact_path.parent / "RuntimeIndex.json"
    runtime_index.unlink()
    ParticleArtifactRegistry.clear()

    rebuilt = ParticleArtifactRegistry.compile_path(str(path))

    assert Path(rebuilt.artifact_path).is_file()
    assert runtime_index.is_file()
    assert rebuilt.source_hash == first.source_hash
    assert rebuilt.semantic_hash == first.semantic_hash
    assert rebuilt.behavior_hash == first.behavior_hash
    assert rebuilt.hir == first.hir
    assert rebuilt.kernel_ir == first.kernel_ir
    assert rebuilt.gpu_glsl == first.gpu_glsl
    assert rebuilt.gpu_spirv == first.gpu_spirv


def test_ensure_project_compiled_rebuilds_missing_library_artifacts(tmp_path, monkeypatch):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    assets = tmp_path / "Assets" / "VFX"
    assets.mkdir(parents=True)
    path = assets / "Portal.particlegraph"
    guid = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    Path(str(path) + ".meta").write_text(
        json.dumps(
            {
                "metadata": {
                    "guid": {"type": "string", "value": guid},
                }
            }
        ),
        encoding="utf-8",
    )
    ParticleGraphAsset(stable_id="portal-ensure", name="Portal").save(str(path))
    artifact_path = (
        tmp_path / "Library" / "Artifacts" / "Particle" / f"{guid}.inxparticle"
    )
    assert artifact_path.is_file()
    artifact_path.unlink()
    ParticleArtifactRegistry.clear()

    summary = ParticleArtifactRegistry.ensure_project_compiled(str(tmp_path))

    compiled = {Path(item).resolve() for item in summary["compiled"]}
    assert path.resolve() in compiled
    assert artifact_path.is_file()
    assert not summary["failed"]


def test_ensure_project_compiled_raises_when_source_is_invalid(tmp_path, monkeypatch):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    assets = tmp_path / "Assets" / "VFX"
    assets.mkdir(parents=True)
    path = assets / "Broken.particlegraph"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ParticleArtifactError, match="Particle artifact compile failed"):
        ParticleArtifactRegistry.ensure_project_compiled(
            str(tmp_path),
            raise_on_error=True,
        )


def test_source_needs_compile_when_library_artifact_is_stale(tmp_path, monkeypatch):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Stale.particlegraph"
    path.parent.mkdir(parents=True)
    ParticleGraphAsset(stable_id="stale-graph", name="Before").save(str(path))
    assert not ParticleArtifactRegistry.source_needs_compile(str(path))

    path.write_text(
        ParticleGraphAsset(stable_id="stale-graph", name="After").canonical_json(),
        encoding="utf-8",
    )

    assert ParticleArtifactRegistry.source_needs_compile(str(path))
    rebuilt = ParticleArtifactRegistry.ensure_source_compiled(str(path))
    assert rebuilt is not None
    assert not ParticleArtifactRegistry.source_needs_compile(str(path))


def test_source_needs_compile_when_generated_gpu_contract_is_stale(
    tmp_path, monkeypatch
):
    from infernux.engine import project_context

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "CompilerUpgrade.particlegraph"
    path.parent.mkdir(parents=True)
    ParticleGraphAsset(stable_id="compiler-upgrade", name="Compiler Upgrade").save(
        str(path)
    )
    current = ParticleArtifactRegistry.get(str(path))
    assert current is not None
    artifact_path = Path(current.artifact_path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["gpu_glsl"]["emitters"][0]["stages"]["init"] += "\n// stale"
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    ParticleArtifactRegistry.clear()

    assert ParticleArtifactRegistry.source_needs_compile(str(path))
    rebuilt = ParticleArtifactRegistry.ensure_source_compiled(str(path))

    assert rebuilt is not None
    assert "// stale" not in rebuilt.gpu_glsl["emitters"][0]["stages"]["init"]
    assert not ParticleArtifactRegistry.source_needs_compile(str(path))


def test_particle_graph_latest_request_wins_out_of_order_compilation(
    tmp_path, monkeypatch
):
    from infernux.engine import project_context

    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = str(tmp_path / "Assets" / "LatestWins.particlegraph")
    older = ParticleGraphAsset(stable_id="latest-wins", name="Older")
    newer = replace(older, name="Newer")
    old_started = threading.Event()
    release_old = threading.Event()
    old_results = []
    original_compile = ParticleArtifactRegistry._compile_graph_asset

    def controlled_compile(cls, asset, source_path):
        if asset.name == "Older":
            old_started.set()
            assert release_old.wait(timeout=5.0)
        return original_compile(asset, source_path)

    monkeypatch.setattr(
        ParticleArtifactRegistry,
        "_compile_graph_asset",
        classmethod(controlled_compile),
    )
    ParticleArtifactRegistry.clear()
    def save_older():
        with pytest.raises(ParticleArtifactSuperseded, match="superseded") as rejected:
            ParticleArtifactRegistry.save_graph_asset(older, path)
        old_results.append(rejected.value)

    old_thread = threading.Thread(target=save_older)
    old_thread.start()
    assert old_started.wait(timeout=5.0)
    latest = ParticleArtifactRegistry.save_graph_asset(newer, path)
    release_old.set()
    old_thread.join(timeout=5.0)

    assert not old_thread.is_alive()
    assert len(old_results) == 1
    assert ParticleArtifactRegistry.get(path) is latest
    assert latest.hir["name"] == "Newer"
    assert ParticleGraphAsset.load(path).name == "Newer"
    assert json.loads(Path(latest.artifact_path).read_text(encoding="utf-8"))["hir"]["name"] == "Newer"


def test_failed_newer_particle_compile_invalidates_older_in_flight_result(
    tmp_path, monkeypatch
):
    from infernux.engine import project_context

    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = str(tmp_path / "Assets" / "FailedLatest.particlegraph")
    baseline = ParticleGraphAsset(stable_id="failed-latest", name="Baseline")
    older = replace(baseline, name="Older In Flight")
    broken = replace(baseline, name="Broken Newer")
    ParticleArtifactRegistry.clear()
    published = ParticleArtifactRegistry.save_graph_asset(baseline, path)
    old_started = threading.Event()
    release_old = threading.Event()
    old_results = []
    original_compile = ParticleArtifactRegistry._compile_graph_asset

    def controlled_compile(cls, asset, source_path):
        if asset.name == "Older In Flight":
            old_started.set()
            assert release_old.wait(timeout=5.0)
        if asset.name == "Broken Newer":
            raise ValueError("intentional latest revision failure")
        return original_compile(asset, source_path)

    monkeypatch.setattr(
        ParticleArtifactRegistry,
        "_compile_graph_asset",
        classmethod(controlled_compile),
    )
    def save_older():
        with pytest.raises(ParticleArtifactSuperseded, match="superseded") as rejected:
            ParticleArtifactRegistry.save_graph_asset(older, path)
        old_results.append(rejected.value)

    old_thread = threading.Thread(target=save_older)
    old_thread.start()
    assert old_started.wait(timeout=5.0)
    with pytest.raises(ParticleArtifactError, match="intentional latest revision failure"):
        ParticleArtifactRegistry.save_graph_asset(broken, path)
    release_old.set()
    old_thread.join(timeout=5.0)

    assert not old_thread.is_alive()
    assert len(old_results) == 1
    assert ParticleArtifactRegistry.get(path) is published
    assert published.hir["name"] == "Baseline"
    assert ParticleGraphAsset.load(path).name == "Baseline"
    assert json.loads(Path(published.artifact_path).read_text(encoding="utf-8"))["hir"]["name"] == "Baseline"


def test_particle_aot_failure_preserves_last_known_good_and_cache_hit(tmp_path, monkeypatch):
    from infernux.engine import project_context
    from infernux.particle import artifact as artifact_module

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(project_context, "get_project_root", lambda: str(tmp_path))
    path = tmp_path / "Assets" / "Smoke.particlegraph"
    path.parent.mkdir()
    asset = ParticleGraphAsset(stable_id="smoke-cache")
    asset.save(str(path))
    published = ParticleArtifactRegistry.get(str(path))
    assert published is not None

    path.write_text('{"broken": true}', encoding="utf-8")
    with pytest.raises(ParticleArtifactError, match="AOT compile failed"):
        ParticleArtifactRegistry.compile_path(str(path))
    assert ParticleArtifactRegistry.get(str(path)) == published

    asset.save(str(path))
    current = ParticleArtifactRegistry.get(str(path))
    ParticleArtifactRegistry.clear()

    def fail_compile(_self, _asset):
        raise AssertionError("matching particle artifact should bypass HIR compilation")

    monkeypatch.setattr(artifact_module.ParticleGraphCompiler, "compile", fail_compile)
    restored = ParticleArtifactRegistry.compile_path(str(path))

    assert restored.source_hash == current.source_hash
    assert restored.behavior_hash == current.behavior_hash


def test_particle_graph_save_does_not_replace_valid_source_with_invalid_draft(tmp_path):
    path = tmp_path / "Smoke.particlegraph"
    valid = ParticleGraphAsset(stable_id="atomic-particle-save")
    valid.save(str(path))
    source_before = path.read_bytes()

    emitter = valid.emitters[0]
    update = emitter.update
    invalid_update = GraphDocument(
        update.domain,
        update.nodes
        + (
            GraphNodeRecord("noise", "common.noise.vector3d", properties={}),
                GraphNodeRecord("invalid.acceleration", "particle.attribute.velocity"),
        ),
        update.links
        + (
            GraphLinkRecord(
                    "root-to-invalid-acceleration",
                    "root.update",
                    "out",
                    "invalid.acceleration",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "noise-to-acceleration",
                "noise",
                "value",
                    "invalid.acceleration",
                "value",
                PortKind.VALUE,
            ),
        ),
        update.metadata,
    )
    invalid = replace(valid, emitters=(replace(emitter, update=invalid_update),))

    with pytest.raises(ParticleArtifactError, match="required input noise.position"):
        invalid.save(str(path))

    assert path.read_bytes() == source_before


def test_particle_aot_rebuilds_persisted_artifact_with_stale_hir_contract(
    tmp_path, monkeypatch
):
    project = tmp_path / "Project"
    source = project / "Assets" / "Smoke.particlegraph"
    source.parent.mkdir(parents=True)
    graph = ParticleGraphAsset(stable_id="stale-hir-smoke")
    source.write_text(
        json.dumps(graph.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "infernux.engine.project_context.get_project_root", lambda: str(project)
    )

    ParticleArtifactRegistry.clear()
    current = ParticleArtifactRegistry.compile_path(str(source))
    artifact_path = Path(current.artifact_path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    output = payload["hir"]["emitters"][0]["render_plan"][0]
    output.pop("soft_particles")
    output.pop("soft_distance")
    artifact_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    ParticleArtifactRegistry.clear()
    rebuilt = ParticleArtifactRegistry.compile_path(str(source))

    rebuilt_output = rebuilt.hir["emitters"][0]["render_plan"][0]
    assert rebuilt_output["soft_particles"] is False
    assert rebuilt_output["soft_distance"] == 1.0
    persisted_output = json.loads(artifact_path.read_text(encoding="utf-8"))["hir"][
        "emitters"
    ][0]["render_plan"][0]
    assert persisted_output["soft_particles"] is False
    assert persisted_output["soft_distance"] == 1.0


def test_particle_aot_rebuilds_persisted_artifact_with_stale_gpu_layout(
    tmp_path, monkeypatch
):
    project = tmp_path / "Project"
    source = project / "Assets" / "Smoke.particlegraph"
    source.parent.mkdir(parents=True)
    graph = ParticleGraphAsset(stable_id="stale-gpu-layout-smoke")
    source.write_text(
        json.dumps(graph.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "infernux.engine.project_context.get_project_root", lambda: str(project)
    )

    ParticleArtifactRegistry.clear()
    current = ParticleArtifactRegistry.compile_path(str(source))
    artifact_path = Path(current.artifact_path)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    layout = payload["gpu_glsl"]["emitters"][0]["data_interface_layout"]
    layout["obsolete_interface_table"] = []
    artifact_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    ParticleArtifactRegistry.clear()
    rebuilt = ParticleArtifactRegistry.compile_path(str(source))

    rebuilt_layout = rebuilt.gpu_glsl["emitters"][0]["data_interface_layout"]
    assert "obsolete_interface_table" not in rebuilt_layout
    persisted_layout = json.loads(artifact_path.read_text(encoding="utf-8"))["gpu_glsl"][
        "emitters"
    ][0]["data_interface_layout"]
    assert "obsolete_interface_table" not in persisted_layout
