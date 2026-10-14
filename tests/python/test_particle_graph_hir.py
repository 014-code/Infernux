from __future__ import annotations
import copy
import json
from dataclasses import replace
from pathlib import Path
import threading
import pytest
from infernux.graph import GraphDocument, GraphLinkRecord, GraphNodeRecord, PortKind
from infernux.particle import (
    EmitterSettings,
    EmitterShape,
    GpuParticleGlslLowerer,
    KernelCompileError,
    MeshEmissionMode,
    ParticleCompileError,
    ParticleEmitterAsset,
    ParticleAttribute,
    ParticleEventField,
    ParticleEventFlow,
    ParticleEventType,
    ParticleGraphAsset,
    ParticleGraphCompiler,
    ParticleGraphSchemaError,
    ParticleParameter,
    ParticleKernelLowerer,
    ParticleStage,
    ParticleScriptCompiler,
    ParticleScriptError,
    ParticleArtifactError,
    ParticleArtifactSuperseded,
    ParticleArtifactRegistry,
    ParticleBurst,
    ParticleRuntimeMetadataError,
    SdfVolume,
    SimulationSpace,
    VectorField,
    decode_particle_runtime_metadata,
    compile_gpu_particle_spirv,
    default_event_graph,
)
from infernux.graph import AssetReference, CoordinateSpace, TypeRef, ValueType
from infernux.particle.nodes import (
    PARTICLE_EVENT_ACTIVE_TYPE_ID,
    PARTICLE_EVENT_TRIGGER_TYPE_ID,
    particle_event_payload_port_id,
    particle_graph_node_definitions,
)
from infernux.particle.asset import (
    particle_attribute_cache_id,
    particle_attribute_capture_id,
    particle_cache_attributes,
)


def _operations(stage):
    return tuple(stage.flow.iter_operations())


def _collision_lifecycle_graph(stage: str, size: float):
    operation_uid = f"{stage.removeprefix('collision_')}.size"
    return GraphDocument(
        f"particle.{stage}",
        nodes=(
            GraphNodeRecord(f"root.{stage}", f"particle.root.{stage}"),
            GraphNodeRecord(
                operation_uid,
                "particle.attribute.size",
                properties={"value": size},
            ),
        ),
        links=(
            GraphLinkRecord(
                f"{stage}.exec",
                f"root.{stage}",
                "out",
                operation_uid,
                "in",
                PortKind.EXEC,
            ),
        ),
    )


def _collision_lifecycle_stages():
    return {
        "collision_enter": _collision_lifecycle_graph("collision_enter", 2.0),
        "collision_stay": _collision_lifecycle_graph("collision_stay", 3.0),
        "collision_exit": _collision_lifecycle_graph("collision_exit", 1.0),
    }


def _event_trigger_stage(event_id: str) -> GraphDocument:
    return GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "event.trigger",
                PARTICLE_EVENT_TRIGGER_TYPE_ID,
                properties={"event": event_id, "condition": True},
            ),
        ),
        links=(
            GraphLinkRecord(
                "event.stream",
                "root.update",
                "out",
                "event.trigger",
                "in",
                PortKind.EXEC,
            ),
        ),
    )


PARTICLE_SCRIPT_SOURCE = '''\
from infernux.particle import AssetReference, ParticleScript, ParticleEmitter, EmitterSettings, VectorField

class SmokeGraph(ParticleScript):
    stable_id = "smoke-graph"

    class Smoke(ParticleEmitter):
        stable_id = "smoke"
        settings = EmitterSettings(
            capacity=100000,
            simulation_space="world",
            spawn_rate=20000.0,
            spawn_rate_over_distance=3.0,
            duration=4.0,
            loop=False,
            start_delay=0.25,
        )
        data_interfaces = (
            VectorField(
                stable_id="wind-field",
                name="Wind",
                texture=AssetReference(path_hint="Assets/Fields/Wind.vectorfield"),
                space="world",
                boundary="repeat",
            ),
        )

        def init(self, ctx, particles):
            particles.set_velocity((0.0, 1.0, 0.0))
            particles.set_lifetime(6.0)
            particles.set_rotation(0.25)

        def update(self, ctx, particles):
            particles .add_velocity((0.0, -0.2, 0.0))
            particles .add_rotation(180.0)

        def rendering(self, ctx, particles):
            particles.set_lifetime(8.0)
            particles.set_flipbook_frame(particles.normalized_age * 63.0)
            particles.sprite(
                shader="Particle Six-Way Smoke",
                receive_scene_lighting=True,
                receive_shadows=True,
                sort="back_to_front",
                alignment="velocity",
            )
'''


PARTICLE_SCRIPT_EVENT_SOURCE = '''\
from infernux.particle import (
    ParticleScript, ParticleEmitter, EmitterSettings,
    EventField, EventType, event,
)

class ImpactGraph(ParticleScript):
    stable_id = "impact-graph"
    event_types = (
        EventType(
            stable_id="impact",
            name="Impact",
            queue_capacity=32,
            fields=(
                EventField("energy", "Energy", "f32", 1.0),
                EventField("tint", "Tint", "color", (1.0, 1.0, 1.0, 1.0)),
            ),
        ),
    )
    class Source(ParticleEmitter):
        stable_id = "source"
        settings = EmitterSettings(capacity=1024)

        def init(self, ctx, particles):
            particles.set_size(2.0)

        def update(self, ctx, particles):
            particles.trigger_event(
                event="impact",
                condition=particles.age >= particles.lifetime,
                payload={
                    "energy": particles.size,
                    "tint": particles.color,
                },
            )

        def rendering(self, ctx, particles):
            particles.sprite()

        @event("impact")
        def on_impact(self, ctx, particles):
            particles.set_size(ctx.event_payload(
                field="energy"
            ))
            particles.set_color(ctx.event_payload(
                field="tint"
            ))
'''


def test_default_particle_graph_has_three_immutable_stage_roots_and_output():
    asset = ParticleGraphAsset()
    emitter = asset.emitters[0]

    assert emitter.init.nodes[0].uid == "root.init"
    assert emitter.update.nodes[0].uid == "root.update"
    assert emitter.rendering.nodes[0].uid == "root.rendering"
    assert emitter.rendering.nodes[1].type_id == "particle.output.sprite"
    assert [node.type_id for node in emitter.init.nodes[1:]] == [
        "particle.attribute.lifetime",
        "particle.attribute.velocity",
    ]
    assert emitter.init.nodes[1].properties["composition"] == "set"
    assert emitter.init.nodes[2].properties["composition"] == "set"
    assert [node.type_id for node in emitter.update.nodes[1:]] == [
        "particle.attribute.velocity"
    ]
    assert emitter.update.nodes[1].properties["composition"] == "add"

    restored = ParticleGraphAsset.from_json(asset.canonical_json())
    assert restored == asset
    assert restored.semantic_hash() == asset.semantic_hash()
    hir = ParticleGraphCompiler().compile(asset)
    assert "builtin.orientation" not in {
        attribute.stable_id for attribute in hir.emitters[0].attributes
    }


def test_particle_exec_fan_out_is_preserved_in_hir_and_allows_disjoint_writes():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "move",
                "particle.attribute.position",
                properties={"value": [1.0, 2.0, 3.0]},
            ),
            GraphNodeRecord(
                "grow",
                "particle.attribute.size",
                properties={"value": 2.0},
            ),
        ),
        links=(
            GraphLinkRecord(
                "exec-move",
                "root.update",
                "out",
                "move",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "exec-grow",
                "root.update",
                "out",
                "grow",
                "in",
                PortKind.EXEC,
            ),
        ),
    )

    stage = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    ).emitters[0].update

    assert {edge.link_uid for edge in stage.flow.edges} == {"exec-move", "exec-grow"}
    root = next(block for block in stage.flow.blocks if block.node_uid == "root.update")
    assert root.operations == ()
    assert set(root.outgoing_edges) == {"exec-move", "exec-grow"}
    assert [
        block.operations[0].source_node_uid
        for block in stage.flow.blocks
        if block.operations
    ] == [
        "grow",
        "move",
    ]
    assert len(stage.flow.lanes) == 3
    branch_lanes = {
        edge.link_uid: stage.flow.lanes[edge.lane_index].stable_id
        for edge in stage.flow.edges
    }
    assert branch_lanes["exec-grow"] != branch_lanes["exec-move"]


def test_burst_node_queues_a_spawn_request_for_the_target_emitter():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "burst-target",
                "particle.emitter.burst",
                properties={"emitter": "target", "count": 7},
            ),
        ),
        links=(
            GraphLinkRecord(
                "exec-burst",
                "root.update",
                "out",
                "burst-target",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    asset = ParticleGraphAsset(
        emitters=(
            ParticleEmitterAsset(stable_id="source", name="Source", update=update),
            ParticleEmitterAsset(
                stable_id="target",
                name="Target",
                settings=EmitterSettings(capacity=321),
            ),
        )
    )

    hir = ParticleGraphCompiler().compile(asset)
    operation = next(
        operation
        for operation in _operations(hir.emitters[0].update)
        if operation.opcode == "emitter.burst"
    )
    assert operation.parameter_dict()["target_emitter_index"] == 1
    assert operation.parameter_dict()["target_capacity"] == 321

    kernel = ParticleKernelLowerer().lower(hir)
    instruction = next(
        instruction
        for instruction in kernel.emitters[0].update.instructions
        if instruction.opcode == "burst_enqueue"
    )
    assert instruction.immediate_dict() == {
        "target_capacity": 321,
        "target_emitter_index": 1,
    }

    from infernux.particle import GpuParticleGlslLowerer

    gpu = GpuParticleGlslLowerer().lower(kernel)
    assert "inx_enqueue_burst(1u," in gpu.emitters[0].update
    assert "emitter_burst_request_counts[target_emitter_index]" in gpu.emitters[0].update
    assert (
        "atomicAdd(emitter_burst_request_counts[target_emitter_index], 0u)"
        in gpu.emitters[0].update
    )
    assert "emitter_spawn.spawn_count" in gpu.emitters[1].init


def test_particle_script_burst_accepts_emitter_name_and_dynamic_count():
    source = '''
class BurstScript(ParticleScript):
    class Source(ParticleEmitter):
        stable_id = "source"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(1.0)

        def update(self, ctx, particles):
            particles.burst(emitter="Target", count=particles.id)

        def rendering(self, ctx, particles):
            particles.sprite()

    class Target(ParticleEmitter):
        stable_id = "target"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_lifetime(1.0)

        def update(self, ctx, particles):
            particles.add_velocity((0.0, 1.0, 0.0))

        def rendering(self, ctx, particles):
            particles.sprite()
'''

    hir = ParticleScriptCompiler().compile(
        source, source_name="burst_test.particle.py"
    )
    operation = next(
        operation
        for operation in _operations(hir.emitters[0].update)
        if operation.opcode == "emitter.burst"
    )
    assert operation.parameter_dict()["emitter"] == "target"
    assert operation.parameter_dict()["target_emitter_index"] == 1


def test_particle_execution_lane_identity_ignores_link_order_and_link_uid():
    nodes = (
        GraphNodeRecord("root.update", "particle.root.update"),
        GraphNodeRecord("move", "particle.attribute.position"),
        GraphNodeRecord("grow", "particle.attribute.size"),
    )

    def compile_links(links):
        asset = ParticleGraphAsset(
            stable_id="stable-lane-asset",
            emitters=(
                ParticleEmitterAsset(
                    stable_id="stable-lane-emitter",
                    update=GraphDocument("particle.update", nodes=nodes, links=links),
                ),
            ),
        )
        program = ParticleGraphCompiler().compile(asset)
        flow = program.emitters[0].update.flow
        semantic_lanes = {
            (
                edge.source_node_uid,
                edge.source_port_id,
                edge.target_node_uid,
                edge.target_port_id,
            ): flow.lanes[edge.lane_index].stable_id
            for edge in flow.edges
        }
        return program.behavior_hash, semantic_lanes

    first = (
        GraphLinkRecord("link-a", "root.update", "out", "move", "in", PortKind.EXEC),
        GraphLinkRecord("link-b", "root.update", "out", "grow", "in", PortKind.EXEC),
    )
    reordered_and_renamed = (
        GraphLinkRecord("unrelated-z", "root.update", "out", "grow", "in", PortKind.EXEC),
        GraphLinkRecord("unrelated-y", "root.update", "out", "move", "in", PortKind.EXEC),
    )

    first_hash, first_lanes = compile_links(first)
    second_hash, second_lanes = compile_links(reordered_and_renamed)

    assert first_lanes == second_lanes
    assert first_hash == second_hash


def test_particle_exec_parallel_writes_require_explicit_order_or_merge():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("first", "particle.attribute.size"),
            GraphNodeRecord("second", "particle.attribute.size"),
        ),
        links=(
            GraphLinkRecord(
                "exec-first",
                "root.update",
                "out",
                "first",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "exec-second",
                "root.update",
                "out",
                "second",
                "in",
                PortKind.EXEC,
            ),
        ),
    )

    with pytest.raises(
        ParticleCompileError,
        match="parallel particle Exec branches have an unordered state dependency.*builtin.size",
    ):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )


def test_particle_exec_parallel_read_write_dependency_requires_order_or_join():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("write-position", "particle.attribute.position"),
            GraphNodeRecord(
                "read-position",
                "particle.attribute.get",
                properties={"attribute": "builtin.position"},
            ),
            GraphNodeRecord("accelerate", "particle.attribute.velocity"),
        ),
        links=(
            GraphLinkRecord(
                "exec-write",
                "root.update",
                "out",
                "write-position",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "exec-read",
                "root.update",
                "out",
                "accelerate",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "value-read",
                "read-position",
                "value",
                "accelerate",
                "value",
                PortKind.VALUE,
            ),
        ),
    )

    with pytest.raises(
        ParticleCompileError,
        match="unordered state dependency.*builtin.position",
    ):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )


def test_exec_bound_get_attribute_samples_before_later_attribute_writes():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "sample-lifetime",
                "particle.attribute.get",
                properties={"attribute": "builtin.lifetime"},
            ),
            GraphNodeRecord(
                "set-lifetime",
                "particle.attribute.lifetime",
                properties={"composition": "set", "value": 8.0},
            ),
            GraphNodeRecord(
                "set-velocity",
                "particle.attribute.velocity",
                properties={"composition": "set", "value": [0.0, 0.0, 0.0]},
            ),
            GraphNodeRecord(
                "two", "common.constant.vec3", properties={"value": [2.0, 2.0, 2.0]}
            ),
            GraphNodeRecord("double-lifetime", "common.math.multiply"),
        ),
        links=(
            GraphLinkRecord(
                "root-sample", "root.update", "out", "sample-lifetime", "in", PortKind.EXEC
            ),
            GraphLinkRecord(
                "sample-lifetime-write",
                "sample-lifetime",
                "out",
                "set-lifetime",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "lifetime-velocity",
                "set-lifetime",
                "out",
                "set-velocity",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "sample-value",
                "sample-lifetime",
                "value",
                "double-lifetime",
                "a",
                PortKind.VALUE,
            ),
            GraphLinkRecord(
                "two-value",
                "two",
                "value",
                "double-lifetime",
                "b",
                PortKind.VALUE,
            ),
            GraphLinkRecord(
                "velocity-value",
                "double-lifetime",
                "result",
                "set-velocity",
                "value",
                PortKind.VALUE,
            ),
        ),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    emitter = program.emitters[0]
    capture_id = particle_attribute_capture_id("update", "sample-lifetime")

    assert [operation.opcode for operation in _operations(emitter.update)] == [
        "attribute.capture",
        "attribute.modify_lifetime",
        "attribute.modify_velocity",
    ]
    assert any(attribute.stable_id == capture_id for attribute in emitter.attributes)
    sampled_load = next(
        instruction
        for instruction in emitter.update.expressions.instructions
        if instruction.source_node_uid == "sample-lifetime"
    )
    assert sampled_load.immediate_dict()["attribute"] == capture_id

    kernel = ParticleKernelLowerer().lower(program).emitters[0].update
    capture_store = next(
        index
        for index, instruction in enumerate(kernel.instructions)
        if instruction.opcode == "store_attribute"
        and instruction.immediate_dict()["attribute"] == capture_id
    )
    lifetime_store = next(
        index
        for index, instruction in enumerate(kernel.instructions)
        if instruction.opcode == "store_attribute"
        and instruction.immediate_dict()["attribute"] == "builtin.lifetime"
    )
    sampled_read = next(
        index
        for index, instruction in enumerate(kernel.instructions)
        if instruction.opcode == "load_attribute"
        and instruction.immediate_dict()["attribute"] == capture_id
    )
    assert capture_store < lifetime_store < sampled_read


def test_exec_bound_get_attribute_allocates_capture_without_value_reuse():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "sample-position",
                "particle.attribute.get",
                properties={"attribute": "builtin.position"},
            ),
            GraphNodeRecord(
                "seek",
                "particle.motion.target_position",
                properties={
                    "target": [0.0, 4.4, 0.0],
                    "speed": 0.48,
                    "responsiveness": 0.18,
                    "arrival_radius": 1.4,
                },
            ),
        ),
        links=(
            GraphLinkRecord(
                "root-sample",
                "root.update",
                "out",
                "sample-position",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "sample-seek",
                "sample-position",
                "out",
                "seek",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    capture_id = particle_attribute_capture_id("update", "sample-position")
    emitter = program.emitters[0]

    assert any(attribute.stable_id == capture_id for attribute in emitter.attributes)
    assert [operation.opcode for operation in _operations(emitter.update)] == [
        "attribute.capture",
        "motion.target_position",
    ]
    kernel = ParticleKernelLowerer().lower(program)
    capture_store = next(
        instruction
        for instruction in kernel.emitters[0].update.instructions
        if instruction.opcode == "store_attribute"
        and instruction.immediate_dict()["attribute"] == capture_id
    )
    assert capture_store is not None


def test_unbound_get_attribute_reads_live_at_its_consumer():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "set-lifetime",
                "particle.attribute.lifetime",
                properties={"composition": "set", "value": 8.0},
            ),
            GraphNodeRecord(
                "live-lifetime",
                "particle.attribute.get",
                properties={"attribute": "builtin.lifetime"},
            ),
            GraphNodeRecord(
                "set-velocity",
                "particle.attribute.velocity",
                properties={"composition": "set", "value": [0.0, 0.0, 0.0]},
            ),
            GraphNodeRecord(
                "two", "common.constant.vec3", properties={"value": [2.0, 2.0, 2.0]}
            ),
            GraphNodeRecord("double-lifetime", "common.math.multiply"),
        ),
        links=(
            GraphLinkRecord(
                "root-lifetime", "root.update", "out", "set-lifetime", "in", PortKind.EXEC
            ),
            GraphLinkRecord(
                "lifetime-velocity",
                "set-lifetime",
                "out",
                "set-velocity",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "live-value",
                "live-lifetime",
                "value",
                "double-lifetime",
                "a",
                PortKind.VALUE,
            ),
            GraphLinkRecord(
                "two-value",
                "two",
                "value",
                "double-lifetime",
                "b",
                PortKind.VALUE,
            ),
            GraphLinkRecord(
                "velocity-value",
                "double-lifetime",
                "result",
                "set-velocity",
                "value",
                PortKind.VALUE,
            ),
        ),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    emitter = program.emitters[0]
    live_load = next(
        instruction
        for instruction in emitter.update.expressions.instructions
        if instruction.source_node_uid == "live-lifetime"
    )

    assert live_load.immediate_dict()["attribute"] == "builtin.lifetime"
    assert not any(
        attribute.stable_id == particle_attribute_capture_id("update", "live-lifetime")
        for attribute in emitter.attributes
    )
    kernel = ParticleKernelLowerer().lower(program).emitters[0].update
    lifetime_store = next(
        index
        for index, instruction in enumerate(kernel.instructions)
        if instruction.opcode == "store_attribute"
        and instruction.immediate_dict()["attribute"] == "builtin.lifetime"
    )
    live_read = max(
        index
        for index, instruction in enumerate(kernel.instructions)
        if instruction.opcode == "load_attribute"
        and instruction.immediate_dict()["attribute"] == "builtin.lifetime"
    )
    assert lifetime_store < live_read


def test_get_attribute_exec_output_requires_its_exec_input():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "sample", "particle.attribute.get", properties={"attribute": "builtin.lifetime"}
            ),
            GraphNodeRecord("tail", "particle.attribute.size"),
        ),
        links=(
            GraphLinkRecord("sample-tail", "sample", "out", "tail", "in", PortKind.EXEC),
        ),
    )
    with pytest.raises(
        ParticleCompileError,
        match="cannot use its Exec output until its Exec input is connected",
    ):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )


def test_particle_exec_multiple_inputs_require_an_explicit_join():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("left", "particle.attribute.size"),
            GraphNodeRecord("right", "particle.attribute.position"),
            GraphNodeRecord("tail", "particle.lifecycle.kill_if"),
        ),
        links=(
            GraphLinkRecord("root-left", "root.update", "out", "left", "in", PortKind.EXEC),
            GraphLinkRecord("root-right", "root.update", "out", "right", "in", PortKind.EXEC),
            GraphLinkRecord("left-tail", "left", "out", "tail", "in", PortKind.EXEC),
            GraphLinkRecord("right-tail", "right", "out", "tail", "in", PortKind.EXEC),
        ),
    )

    with pytest.raises(ParticleCompileError, match="use an explicit Join All node"):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )


def test_particle_join_rejects_an_input_from_an_unreachable_exec_branch():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("reachable", "particle.attribute.size"),
            GraphNodeRecord("orphan", "particle.attribute.position"),
            GraphNodeRecord("join", "particle.control.join_all"),
            GraphNodeRecord("tail", "particle.lifecycle.kill_if"),
        ),
        links=(
            GraphLinkRecord(
                "root-reachable",
                "root.update",
                "out",
                "reachable",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "reachable-join",
                "reachable",
                "out",
                "join",
                "in0",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "orphan-join",
                "orphan",
                "out",
                "join",
                "in1",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "join-tail", "join", "out", "tail", "in", PortKind.EXEC
            ),
        ),
    )

    with pytest.raises(
        ParticleCompileError,
        match="Join All node 'join' has an input from unreachable Exec node 'orphan'",
    ):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )


def test_particle_exec_join_all_continues_after_every_parallel_branch():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("left", "particle.attribute.size"),
            GraphNodeRecord("right", "particle.attribute.position"),
            GraphNodeRecord("join", "particle.control.join_all"),
            GraphNodeRecord("tail", "particle.lifecycle.kill_if"),
        ),
        links=(
            GraphLinkRecord("root-left", "root.update", "out", "left", "in", PortKind.EXEC),
            GraphLinkRecord("root-right", "root.update", "out", "right", "in", PortKind.EXEC),
            GraphLinkRecord("left-join", "left", "out", "join", "in0", PortKind.EXEC),
            GraphLinkRecord("right-join", "right", "out", "join", "in1", PortKind.EXEC),
            GraphLinkRecord("join-tail", "join", "out", "tail", "in", PortKind.EXEC),
        ),
    )

    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    stage = program.emitters[0].update
    schedule = [
        block.node_uid
        for block in stage.flow.blocks
        if block.node_uid != stage.flow.entry_node_uid
    ]

    assert schedule.index("join") > schedule.index("left")
    assert schedule.index("join") > schedule.index("right")
    assert schedule.index("tail") > schedule.index("join")
    assert len(stage.flow.joins) == 1
    join = stage.flow.joins[0]
    assert join.node_uid == "join"
    assert len(join.input_lane_indices) == 2
    assert join.output_lane_index not in join.input_lane_indices
    kernel_program = ParticleKernelLowerer().lower(program)
    kernel_emitter = kernel_program.emitters[0]
    kernel = kernel_emitter.update
    assert all(
        instruction.source.node_uid != "join"
        for instruction in kernel.instructions
    )
    update_flow = next(
        flow
        for flow in kernel_emitter.flows
        if flow.lifecycle_stage is ParticleStage.UPDATE
    )
    assert [lane.index for lane in update_flow.lanes] == list(
        range(len(update_flow.lanes))
    )
    assert update_flow.lanes[0].parent_index == -1
    assert all(
        0 <= lane.parent_index < lane.index
        for lane in update_flow.lanes[1:]
    )
    blocks = {block.source_node_uid: block for block in update_flow.blocks}
    assert blocks["left"].lane_index != blocks["right"].lane_index
    assert blocks["join"].instruction_begin == blocks["join"].instruction_end
    assert blocks["tail"].instruction_begin >= blocks["join"].instruction_end
    assert [block.source_node_uid for block in update_flow.blocks] == [
        "root.update",
        "left",
        "right",
        "join",
        "tail",
    ]
    assert len(update_flow.joins) == 1
    kernel_join = update_flow.joins[0]
    assert kernel_join.source_node_uid == "join"
    assert set(kernel_join.input_lane_indices) == {
        blocks["left"].lane_index,
        blocks["right"].lane_index,
    }
    assert kernel_join.output_lane_index == blocks["join"].lane_index

    restored = type(kernel_program).from_dict(kernel_program.to_dict())
    assert restored == kernel_program
    assert restored.kernel_hash == kernel_program.kernel_hash

    stale = kernel_program.to_dict()
    stale["emitters"][0].pop("flows")
    with pytest.raises(KernelCompileError, match="kernel emitter keys"):
        type(kernel_program).from_dict(stale)

    changed_topology = kernel_program.to_dict()
    serialized_update_flow = next(
        flow
        for flow in changed_topology["emitters"][0]["flows"]
        if flow["lifecycle_stage"] == "update"
    )
    branch_lane_indices = sorted(
        (blocks["left"].lane_index, blocks["right"].lane_index)
    )
    serialized_update_flow["lanes"][branch_lane_indices[1]][
        "parent_index"
    ] = branch_lane_indices[0]
    with pytest.raises(KernelCompileError, match="kernel hash"):
        type(kernel_program).from_dict(changed_topology)


def test_particle_if_activates_only_the_selected_exec_branch():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("condition", "common.constant.bool", properties={"value": True}),
            GraphNodeRecord("if", "particle.control.if"),
            GraphNodeRecord("true-size", "particle.attribute.size", properties={"value": 2.0}),
            GraphNodeRecord("false-size", "particle.attribute.size", properties={"value": 0.5}),
        ),
        links=(
            GraphLinkRecord("root-if", "root.update", "out", "if", "in", PortKind.EXEC),
            GraphLinkRecord("condition-if", "condition", "value", "if", "condition"),
            GraphLinkRecord("if-true", "if", "true", "true-size", "in", PortKind.EXEC),
            GraphLinkRecord("if-false", "if", "false", "false-size", "in", PortKind.EXEC),
        ),
    )

    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    stage = program.emitters[0].update
    by_uid = {
        operation.source_node_uid: operation
        for operation in stage.flow.iter_operations()
    }

    assert by_uid["if"].opcode == "control.if"
    assert by_uid["true-size"].execution_predicates[0].expected is True
    assert by_uid["false-size"].execution_predicates[0].expected is False
    assert next(edge for edge in stage.flow.edges if edge.link_uid == "if-true").predicate_expected is True
    assert next(edge for edge in stage.flow.edges if edge.link_uid == "if-false").predicate_expected is False

    kernel = ParticleKernelLowerer().lower(program).emitters[0].update
    opcodes = [instruction.opcode for instruction in kernel.instructions]
    assert opcodes.count("begin_if") == 2
    assert opcodes.count("end_if") == 2
    assert "logical_not" in opcodes


def test_particle_join_all_rejects_mutually_exclusive_if_branches():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "condition",
                "common.constant.bool",
                properties={"value": True},
            ),
            GraphNodeRecord("if", "particle.control.if"),
            GraphNodeRecord("true-size", "particle.attribute.size"),
            GraphNodeRecord("false-position", "particle.attribute.position"),
            GraphNodeRecord("join", "particle.control.join_all"),
            GraphNodeRecord("tail", "particle.attribute.color"),
        ),
        links=(
            GraphLinkRecord(
                "root-if", "root.update", "out", "if", "in", PortKind.EXEC
            ),
            GraphLinkRecord(
                "condition-if", "condition", "value", "if", "condition"
            ),
            GraphLinkRecord(
                "if-true", "if", "true", "true-size", "in", PortKind.EXEC
            ),
            GraphLinkRecord(
                "if-false",
                "if",
                "false",
                "false-position",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "true-join",
                "true-size",
                "out",
                "join",
                "in0",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "false-join",
                "false-position",
                "out",
                "join",
                "in1",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "join-tail", "join", "out", "tail", "in", PortKind.EXEC
            ),
        ),
    )

    with pytest.raises(
        ParticleCompileError,
        match="cannot join mutually exclusive or differently predicated branches",
    ):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )


def test_particle_waits_emit_stable_suspension_resume_descriptors():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("frame-count", "common.constant.i32", properties={"value": 3}),
            GraphNodeRecord("seconds", "common.constant.f32", properties={"value": 0.25}),
            GraphNodeRecord("wait.frames", "particle.control.wait_frames"),
            GraphNodeRecord("wait.seconds", "particle.control.wait_seconds"),
            GraphNodeRecord("tail", "particle.attribute.size"),
        ),
        links=(
            GraphLinkRecord(
                "root-wait-frames",
                "root.update",
                "out",
                "wait.frames",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "frames-value",
                "frame-count",
                "value",
                "wait.frames",
                "frames",
                PortKind.VALUE,
            ),
            GraphLinkRecord(
                "wait-frames-seconds",
                "wait.frames",
                "out",
                "wait.seconds",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "seconds-value",
                "seconds",
                "value",
                "wait.seconds",
                "seconds",
                PortKind.VALUE,
            ),
            GraphLinkRecord(
                "wait-seconds-tail",
                "wait.seconds",
                "out",
                "tail",
                "in",
                PortKind.EXEC,
            ),
        ),
    )

    stage = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    ).emitters[0].update

    assert [item.kind.value for item in stage.flow.suspensions] == [
        "frames",
        "seconds",
    ]
    frame_wait, second_wait = stage.flow.suspensions
    assert frame_wait.resume_program_counter == 1
    assert frame_wait.resume_node_uid == "wait.seconds"
    assert frame_wait.value_id
    assert frame_wait.literal == 1
    assert second_wait.resume_program_counter == 2
    assert second_wait.resume_node_uid == "tail"
    assert second_wait.value_id
    assert second_wait.literal == pytest.approx(0.1)
    assert frame_wait.lane_stable_id == stage.flow.lanes[frame_wait.lane_index].stable_id
    assert second_wait.lane_stable_id == stage.flow.lanes[second_wait.lane_index].stable_id

    from infernux.particle.artifact import _program_to_dict

    serialized = _program_to_dict(
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )
    )
    suspension_payload = serialized["emitters"][0]["update"]["flow"]["suspensions"]
    assert [item["resume_program_counter"] for item in suspension_payload] == [1, 2]
    assert [item["resume_node_uid"] for item in suspension_payload] == [
        "wait.seconds",
        "tail",
    ]

    kernel_program = ParticleKernelLowerer().lower(
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
        )
    )
    kernel_emitter = kernel_program.emitters[0]
    assert [item.resume_program_counter for item in kernel_emitter.suspensions] == [
        1,
        2,
    ]
    assert [item.stage_resume_program_counter for item in kernel_emitter.suspensions] == [
        1,
        2,
    ]
    assert [item.lifecycle_stage for item in kernel_emitter.suspensions] == [
        ParticleStage.UPDATE,
        ParticleStage.UPDATE,
    ]
    for item in kernel_emitter.suspensions:
        update_flow = next(
            flow
            for flow in kernel_emitter.flows
            if flow.lifecycle_stage is ParticleStage.UPDATE
        )
        blocks = {block.source_node_uid: block for block in update_flow.blocks}
        instruction = kernel_emitter.update.instructions[
            item.suspend_instruction_index
        ]
        assert instruction.opcode == f"suspend_{item.kind.value}"
        assert instruction.immediate_dict()["resume_program_counter"] == (
            item.resume_program_counter
        )
        assert blocks[item.source_node_uid].instruction_begin <= (
            item.suspend_instruction_index
        ) < blocks[item.source_node_uid].instruction_end
        assert item.resume_instruction_index == blocks[
            item.resume_node_uid
        ].instruction_begin

    restored_kernel = type(kernel_program).from_dict(kernel_program.to_dict())
    assert restored_kernel == kernel_program


def test_particle_wait_can_finish_update_and_rendering_lifecycle_continuations():
    no_continuation = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("wait", "particle.control.wait_frames"),
        ),
        links=(
            GraphLinkRecord(
                "root-wait",
                "root.update",
                "out",
                "wait",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            emitters=(ParticleEmitterAsset(update=no_continuation),)
        )
    )
    suspension = program.emitters[0].update.flow.suspensions[0]
    assert suspension.resume_node_uid == ""

    kernel = ParticleKernelLowerer().lower(program)
    kernel_suspension = kernel.emitters[0].suspensions[0]
    assert kernel_suspension.resume_node_uid == ""
    assert kernel_suspension.resume_instruction_index == -1
    assert type(kernel).from_dict(kernel.to_dict()) == kernel

    rendering = GraphDocument(
        "particle.rendering",
        nodes=(
            GraphNodeRecord("root.rendering", "particle.root.rendering"),
            GraphNodeRecord("wait", "particle.control.wait_seconds"),
            GraphNodeRecord("output", "particle.output.sprite"),
        ),
        links=(
            GraphLinkRecord(
                "root-wait",
                "root.rendering",
                "out",
                "wait",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "wait-output",
                "wait",
                "out",
                "output",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    rendering_program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            emitters=(ParticleEmitterAsset(rendering=rendering),)
        )
    )
    rendering_suspension = rendering_program.emitters[0].rendering.flow.suspensions[0]
    assert rendering_suspension.resume_node_uid == "output"
    rendering_kernel = ParticleKernelLowerer().lower(rendering_program)
    kernel_suspension = rendering_kernel.emitters[0].suspensions[0]
    assert kernel_suspension.lifecycle_stage is ParticleStage.RENDERING
    assert kernel_suspension.resume_node_uid == "output"


def test_particle_until_repeats_the_preceding_operation_and_keeps_wait_distinct():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "velocity.down",
                "particle.attribute.velocity",
                properties={
                    "composition": "add",
                    "value": [0.0, -1.0, 0.0],
                },
            ),
            GraphNodeRecord(
                "until.down",
                "particle.control.until_seconds",
                properties={"seconds": 3.0},
            ),
            GraphNodeRecord(
                "velocity.side",
                "particle.attribute.velocity",
                properties={
                    "composition": "add",
                    "value": [1.0, 0.0, 0.0],
                },
            ),
            GraphNodeRecord(
                "until.side",
                "particle.control.until_frames",
                properties={"frames": 5},
            ),
        ),
        links=(
            GraphLinkRecord(
                "root-down", "root.update", "out", "velocity.down", "in", PortKind.EXEC
            ),
            GraphLinkRecord(
                "down-until", "velocity.down", "out", "until.down", "in", PortKind.EXEC
            ),
            GraphLinkRecord(
                "until-side", "until.down", "out", "velocity.side", "in", PortKind.EXEC
            ),
            GraphLinkRecord(
                "side-until", "velocity.side", "out", "until.side", "in", PortKind.EXEC
            ),
        ),
    )

    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    stage = program.emitters[0].update
    assert [item.kind.value for item in stage.flow.suspensions] == [
        "until_seconds",
        "until_frames",
    ]
    assert [item.resume_node_uid for item in stage.flow.suspensions] == [
        "velocity.down",
        "velocity.side",
    ]
    kernel = ParticleKernelLowerer().lower(program).emitters[0]
    assert [
        kernel.update.instructions[item.suspend_instruction_index].opcode
        for item in kernel.suspensions
    ] == ["until_seconds", "until_frames"]
    assert [item.resume_node_uid for item in kernel.suspensions] == [
        "velocity.down",
        "velocity.side",
    ]


def test_particle_delta_time_is_an_explicit_lifecycle_value_not_attribute_semantics():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "gravity",
                "common.constant.vec3",
                properties={"value": [0.0, -9.8, 0.0]},
            ),
            GraphNodeRecord("delta", "particle.context.delta_time"),
            GraphNodeRecord("step", "common.math.multiply"),
            GraphNodeRecord(
                "velocity",
                "particle.attribute.velocity",
                properties={"composition": "add"},
            ),
        ),
        links=(
            GraphLinkRecord(
                "gravity-step", "gravity", "value", "step", "a", PortKind.VALUE
            ),
            GraphLinkRecord(
                "delta-step", "delta", "value", "step", "b", PortKind.VALUE
            ),
            GraphLinkRecord(
                "step-velocity", "step", "result", "velocity", "value", PortKind.VALUE
            ),
            GraphLinkRecord(
                "root-velocity", "root.update", "out", "velocity", "in", PortKind.EXEC
            ),
        ),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    stage = program.emitters[0].update
    assert any(
        instruction.opcode == "delta_time"
        for instruction in stage.expressions.instructions
    )
    operation = next(
        item
        for item in stage.flow.iter_operations()
        if item.source_node_uid == "velocity"
    )
    assert operation.parameter_dict()["composition"] == "add"

    kernel = ParticleKernelLowerer().lower(program).emitters[0].update
    explicit_delta = next(
        instruction
        for instruction in kernel.instructions
        if instruction.source.node_uid == "delta"
    )
    assert explicit_delta.opcode == "load_uniform"
    assert explicit_delta.immediate_dict() == {"name": "delta_time"}

    init = GraphDocument(
        "particle.init",
        nodes=(
            GraphNodeRecord("root.init", "particle.root.init"),
            GraphNodeRecord("delta", "particle.context.delta_time"),
            GraphNodeRecord("size", "particle.attribute.size"),
        ),
        links=(
            GraphLinkRecord(
                "delta-size", "delta", "value", "size", "value", PortKind.VALUE
            ),
            GraphLinkRecord(
                "root-size", "root.init", "out", "size", "in", PortKind.EXEC
            ),
        ),
    )
    init_program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(init=init),))
    )
    init_kernel = ParticleKernelLowerer().lower(init_program).emitters[0].init
    init_delta = next(
        instruction
        for instruction in init_kernel.instructions
        if instruction.source.node_uid == "delta"
    )
    assert init_delta.opcode == "load_uniform"
    assert init_delta.immediate_dict() == {"name": "delta_time"}


def test_kernel_continuation_program_counters_are_unique_across_lifecycle_stages():
    def waiting_stage(stage: str):
        return GraphDocument(
            f"particle.{stage}",
            nodes=(
                GraphNodeRecord(f"root.{stage}", f"particle.root.{stage}"),
                GraphNodeRecord(f"wait.{stage}", "particle.control.wait_frames"),
                GraphNodeRecord(f"tail.{stage}", "particle.attribute.size"),
            ),
            links=(
                GraphLinkRecord(
                    f"root-wait.{stage}",
                    f"root.{stage}",
                    "out",
                    f"wait.{stage}",
                    "in",
                    PortKind.EXEC,
                ),
                GraphLinkRecord(
                    f"wait-tail.{stage}",
                    f"wait.{stage}",
                    "out",
                    f"tail.{stage}",
                    "in",
                    PortKind.EXEC,
                ),
            ),
        )

    emitter = ParticleEmitterAsset(
        settings=EmitterSettings(collision_enabled=True),
        init=waiting_stage("init"),
        collision_enter=waiting_stage("collision_enter"),
    )
    kernel_emitter = ParticleKernelLowerer().lower(
        ParticleGraphCompiler().compile(ParticleGraphAsset(emitters=(emitter,)))
    ).emitters[0]

    assert [item.lifecycle_stage for item in kernel_emitter.suspensions] == [
        ParticleStage.INIT,
        ParticleStage.COLLISION_ENTER,
    ]
    assert [item.stage_resume_program_counter for item in kernel_emitter.suspensions] == [
        1,
        1,
    ]
    assert [item.resume_program_counter for item in kernel_emitter.suspensions] == [
        1,
        2,
    ]


def test_collision_lifecycle_roots_are_first_class_emitter_stages():
    emitter = ParticleEmitterAsset(
        settings=EmitterSettings(collision_enabled=True),
        **_collision_lifecycle_stages(),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(emitter,))
    )
    hir = program.emitters[0]

    assert hir.collision_enter.stage is ParticleStage.COLLISION_ENTER
    assert hir.collision_stay.stage is ParticleStage.COLLISION_STAY
    assert hir.collision_exit.stage is ParticleStage.COLLISION_EXIT
    runtime_conditions = {
        operation.source_node_uid: tuple(
            predicate.runtime_condition for predicate in operation.execution_predicates
        )
        for stage in (hir.collision_enter, hir.collision_stay, hir.collision_exit)
        if stage is not None
        for operation in stage.flow.iter_operations()
    }
    assert runtime_conditions == {
        "enter.size": (),
        "stay.size": (),
        "exit.size": (),
    }

    kernel_program = ParticleKernelLowerer().lower(program)
    kernel_emitter = kernel_program.emitters[0]
    update_opcodes = [instruction.opcode for instruction in kernel_emitter.update.instructions]
    contact_opcodes = [instruction.opcode for instruction in kernel_emitter.contact.instructions]
    assert update_opcodes.count("collide_scene") == 1
    assert "store_attribute" in contact_opcodes
    assert "builtin.collision_active" not in {
        stable_id for stable_id, _type, _default in kernel_emitter.attributes
    }

    flow_by_stage = {
        flow.lifecycle_stage: flow for flow in kernel_emitter.flows
    }
    assert set(flow_by_stage) == {
        ParticleStage.INIT,
        ParticleStage.UPDATE,
        ParticleStage.COLLISION_ENTER,
        ParticleStage.COLLISION_STAY,
        ParticleStage.COLLISION_EXIT,
        ParticleStage.RENDERING,
    }
    expected_nodes = {
        ParticleStage.COLLISION_ENTER: "enter.size",
        ParticleStage.COLLISION_STAY: "stay.size",
        ParticleStage.COLLISION_EXIT: "exit.size",
    }
    for lifecycle_stage, source_node_uid in expected_nodes.items():
        flow = flow_by_stage[lifecycle_stage]
        assert flow.kernel_stage.value == "contact"
        assert flow.entry_node_uid == f"root.{lifecycle_stage.value}"
        assert [lane.index for lane in flow.lanes] == list(range(len(flow.lanes)))
        block = next(
            block
            for block in flow.blocks
            if block.source_node_uid == source_node_uid
        )
        assert block.instruction_begin < block.instruction_end
        assert all(
            instruction.source.node_uid == source_node_uid
            for instruction in kernel_emitter.contact.instructions[
                block.instruction_begin : block.instruction_end
            ]
        )

    restored = type(kernel_program).from_dict(kernel_program.to_dict())
    assert restored == kernel_program
    assert restored.kernel_hash == kernel_program.kernel_hash


def test_disabled_collision_keeps_authored_roots_dormant():
    emitter = ParticleEmitterAsset(
        settings=EmitterSettings(collision_enabled=False),
        **_collision_lifecycle_stages(),
    )
    hir = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(emitter,))
    ).emitters[0]

    assert hir.collision_enter is None
    assert hir.collision_stay is None
    assert hir.collision_exit is None
    assert not any(
        operation.source_node_uid.endswith(".size")
        for operation in hir.update.flow.iter_operations()
    )
    assert "builtin.collision_active" not in {
        attribute.stable_id for attribute in hir.attributes
    }


def test_particle_script_collision_methods_compile_as_lifecycle_stages():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class CollisionGraph(ParticleScript):
    class Sparks(ParticleEmitter):
        stable_id = "sparks"
        settings = EmitterSettings(collision_enabled=True)

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            pass

        def collision_enter(self, ctx, particles):
            particles.set_position(particles.collision_point)
            particles.set_velocity(particles.collision_relative_velocity)
            particles.set_size(particles.collision_penetration)
            particles.set_color(particles.collision_material)
            particles.set_attribute("collision-id-low", particles.collision_collider_id_low)
            particles.set_attribute("collision-id-high", particles.collision_collider_id_high)

        def collision_stay(self, ctx, particles):
            particles.set_size(3.0)

        def collision_exit(self, ctx, particles):
            particles.set_size(1.0)

        def rendering(self, ctx, particles):
            particles.sprite()
'''

    program = ParticleScriptCompiler().compile(
        source, source_name="Collision.particle.py"
    )

    emitter = program.emitters[0]
    assert emitter.collision_enter.stage is ParticleStage.COLLISION_ENTER
    assert emitter.collision_stay.stage is ParticleStage.COLLISION_STAY
    assert emitter.collision_exit.stage is ParticleStage.COLLISION_EXIT
    enter_reads = {
        instruction.immediate_dict()["attribute"]
        for instruction in emitter.collision_enter.expressions.instructions
        if instruction.opcode == "load_attribute"
    }
    assert {
        "builtin.collision_point",
        "builtin.collision_relative_velocity",
        "builtin.collision_penetration",
        "builtin.collision_material",
        "builtin.collision_collider_id_low",
        "builtin.collision_collider_id_high",
    } <= enter_reads


def test_particle_script_collision_methods_require_collision_setting():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class CollisionGraph(ParticleScript):
    class Sparks(ParticleEmitter):
        stable_id = "sparks"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            pass

        def update(self, ctx, particles):
            pass

        def collision_enter(self, ctx, particles):
            particles.set_size(2.0)

        def rendering(self, ctx, particles):
            particles.sprite()
'''

    with pytest.raises(
        ParticleScriptError,
        match="collision lifecycle methods require",
    ):
        ParticleScriptCompiler().parse(
            source, source_name="CollisionDisabled.particle.py"
        )


def test_graph_parameters_have_stable_slots_and_default_only_hot_updates():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "wind",
                "particle.parameter",
                properties={"parameter": "wind"},
            ),
            GraphNodeRecord("accelerate", "particle.attribute.velocity"),
        ),
        links=(
            GraphLinkRecord(
                "stream",
                "root.update",
                "out",
                "accelerate",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "wind-value",
                "wind",
                "value",
                "accelerate",
                "value",
                PortKind.VALUE,
            ),
        ),
    )
    parameter = ParticleParameter(
        "wind",
        "Wind",
        TypeRef(ValueType.VEC3),
        [1.0, 0.0, 0.0],
    )
    asset = ParticleGraphAsset(
        parameters=(parameter,),
        emitters=(ParticleEmitterAsset(update=update),),
    )

    program = ParticleGraphCompiler().compile(asset)
    kernel = ParticleKernelLowerer().lower(program)
    changed = ParticleGraphCompiler().compile(
        replace(asset, parameters=(replace(parameter, default=[3.0, 2.0, 1.0]),))
    )

    assert program.parameters[0].slot == 0
    assert program.parameters[0].stable_id == "wind"
    assert program.behavior_hash == changed.behavior_hash
    assert program.semantic_hash != changed.semantic_hash
    load = next(
        instruction
        for instruction in kernel.emitters[0].update.instructions
        if instruction.opcode == "load_parameter"
    )
    assert load.result_type == TypeRef(ValueType.VEC3)
    assert load.immediate_dict() == {"parameter": "wind"}
    assert kernel.to_dict() == type(kernel).from_dict(kernel.to_dict()).to_dict()


def test_color_parameter_hdr_is_runtime_metadata_not_behavior():
    from infernux.graph.parameters import GRAPH_PARAMETER_HDR_ATTRIBUTE
    from infernux.particle.artifact import _program_to_dict

    parameter = ParticleParameter(
        "tint",
        "Tint",
        TypeRef(ValueType.COLOR),
        [1.0, 1.0, 1.0, 1.0],
    )
    hdr_parameter = replace(parameter, attributes=(GRAPH_PARAMETER_HDR_ATTRIBUTE,))
    asset = ParticleGraphAsset(
        parameters=(parameter,),
        emitters=(ParticleEmitterAsset(),),
    )
    program = ParticleGraphCompiler().compile(asset)
    hdr_program = ParticleGraphCompiler().compile(
        replace(asset, parameters=(hdr_parameter,))
    )
    metadata = decode_particle_runtime_metadata(program)
    hdr_metadata = decode_particle_runtime_metadata(hdr_program)
    encoded = _program_to_dict(hdr_program)

    assert program.parameters[0].hdr is False
    assert hdr_program.parameters[0].hdr is True
    assert program.behavior_hash == hdr_program.behavior_hash
    assert metadata.parameters[0].hdr is False
    assert hdr_metadata.parameters[0].hdr is True
    assert encoded["parameters"][0]["hdr"] is True
    assert decode_particle_runtime_metadata(encoded).parameters[0].hdr is True

    incomplete = copy.deepcopy(encoded)
    del incomplete["parameters"][0]["hdr"]
    with pytest.raises(ParticleRuntimeMetadataError, match="does not match the schema"):
        decode_particle_runtime_metadata(incomplete)


def test_writable_graph_parameter_lowers_to_one_typed_shared_store():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "set-intensity",
                "particle.parameter.set",
                properties={"parameter": "intensity", "value": 2.5},
            ),
        ),
        links=(
            GraphLinkRecord(
                "stream",
                "root.update",
                "out",
                "set-intensity",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    parameter = ParticleParameter(
        "intensity",
        "Intensity",
        TypeRef(ValueType.F32),
        1.0,
        writable=True,
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            parameters=(parameter,),
            emitters=(ParticleEmitterAsset(update=update),),
        )
    )
    operations = _operations(program.emitters[0].update)
    assert [operation.opcode for operation in operations] == ["parameter.store"]
    kernel = ParticleKernelLowerer().lower(program)
    stores = [
        instruction
        for instruction in kernel.emitters[0].update.instructions
        if instruction.opcode == "store_parameter"
    ]
    assert len(stores) == 1
    assert stores[0].immediate_dict() == {"parameter": "intensity"}
    assert stores[0].operands[0].value_type == TypeRef(ValueType.F32)


def test_set_parameter_rejects_read_only_graph_parameter():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "set-intensity",
                "particle.parameter.set",
                properties={"parameter": "intensity", "value": 2.5},
            ),
        ),
        links=(
            GraphLinkRecord(
                "stream",
                "root.update",
                "out",
                "set-intensity",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    with pytest.raises(ParticleCompileError, match="read-only parameter"):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(
                parameters=(
                    ParticleParameter(
                        "intensity",
                        "Intensity",
                        TypeRef(ValueType.F32),
                        1.0,
                    ),
                ),
                emitters=(ParticleEmitterAsset(update=update),),
            )
        )


def test_set_emitter_playing_lowers_to_graph_owned_target_state():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "start-trail",
                "particle.emitter.playing",
                properties={"emitter": "trail", "playing": True},
            ),
        ),
        links=(
            GraphLinkRecord(
                "stream",
                "root.update",
                "out",
                "start-trail",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    asset = ParticleGraphAsset(
        emitters=(
            ParticleEmitterAsset(stable_id="meteor", update=update),
            ParticleEmitterAsset(stable_id="trail"),
        )
    )
    program = ParticleGraphCompiler().compile(asset)
    operation = _operations(program.emitters[0].update)[0]
    assert operation.opcode == "emitter.set_playing"
    assert operation.parameter_dict()["target_emitter_index"] == 1
    instruction = next(
        item
        for item in ParticleKernelLowerer().lower(program).emitters[0].update.instructions
        if item.opcode == "emitter_set_playing"
    )
    assert instruction.immediate_dict() == {"target_emitter_index": 1}
    assert instruction.operands[0].value_type == TypeRef(ValueType.BOOL)


def test_shared_parameter_and_playing_state_form_one_cross_emitter_contract():
    meteor_init = GraphDocument(
        "particle.init",
        nodes=(
            GraphNodeRecord("root.init", "particle.root.init"),
            GraphNodeRecord(
                "start-trail",
                "particle.emitter.playing",
                properties={"emitter": "trail", "playing": True},
            ),
        ),
        links=(
            GraphLinkRecord(
                "start-trail-exec",
                "root.init",
                "out",
                "start-trail",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    meteor_update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "position",
                "particle.attribute.get",
                properties={"attribute": "builtin.position"},
            ),
            GraphNodeRecord(
                "publish-position",
                "particle.parameter.set",
                properties={"parameter": "meteor-position"},
            ),
        ),
        links=(
            GraphLinkRecord(
                "publish-exec",
                "root.update",
                "out",
                "publish-position",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "publish-value",
                "position",
                "value",
                "publish-position",
                "value",
                PortKind.VALUE,
            ),
        ),
    )
    trail_init = GraphDocument(
        "particle.init",
        nodes=(
            GraphNodeRecord("root.init", "particle.root.init"),
            GraphNodeRecord(
                "meteor-position",
                "particle.parameter",
                properties={"parameter": "meteor-position"},
            ),
            GraphNodeRecord("set-position", "particle.attribute.position"),
        ),
        links=(
            GraphLinkRecord(
                "set-position-exec",
                "root.init",
                "out",
                "set-position",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "set-position-value",
                "meteor-position",
                "value",
                "set-position",
                "value",
                PortKind.VALUE,
            ),
        ),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            parameters=(
                ParticleParameter(
                    "meteor-position",
                    "Meteor Position",
                    TypeRef(ValueType.VEC3),
                    [0.0, 0.0, 0.0],
                    writable=True,
                ),
            ),
            emitters=(
                ParticleEmitterAsset(
                    stable_id="meteor",
                    init=meteor_init,
                    update=meteor_update,
                ),
                ParticleEmitterAsset(stable_id="trail", init=trail_init),
            ),
        )
    )
    kernel = ParticleKernelLowerer().lower(program)

    meteor_init_opcodes = {
        instruction.opcode for instruction in kernel.emitters[0].init.instructions
    }
    meteor_update_opcodes = {
        instruction.opcode for instruction in kernel.emitters[0].update.instructions
    }
    trail_init_opcodes = {
        instruction.opcode for instruction in kernel.emitters[1].init.instructions
    }
    assert "emitter_set_playing" in meteor_init_opcodes
    assert {"load_attribute", "store_parameter"} <= meteor_update_opcodes
    assert {"load_parameter", "store_attribute"} <= trail_init_opcodes


def test_set_emitter_playing_rejects_its_own_emitter():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "self-play",
                "particle.emitter.playing",
                properties={"emitter": "meteor", "playing": False},
            ),
        ),
        links=(
            GraphLinkRecord(
                "stream", "root.update", "out", "self-play", "in", PortKind.EXEC
            ),
        ),
    )
    with pytest.raises(ParticleCompileError, match="cannot target its owning emitter"):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(
                emitters=(
                    ParticleEmitterAsset(stable_id="meteor", update=update),
                    ParticleEmitterAsset(stable_id="trail"),
                )
            )
        )


def test_particle_graph_rejects_removed_cpu_execution_target():
    document = ParticleGraphAsset().to_dict()
    settings = document["emitters"][0]["settings"]
    assert "target" not in settings

    settings["target"] = "cpu"
    with pytest.raises(ParticleGraphSchemaError, match=r"unknown=\['target'\]"):
        ParticleGraphAsset.from_dict(document)


def test_particle_event_flows_compile_to_stable_typed_queue_abi():
    impact = ParticleEventType(
        "impact",
        "Impact",
        32,
        (
            ParticleEventField(
                "position",
                "Position",
                TypeRef(ValueType.VEC3, CoordinateSpace.WORLD),
                [0.0, 0.0, 0.0],
            ),
            ParticleEventField(
                "color",
                "Color",
                TypeRef(ValueType.COLOR),
                [1.0, 1.0, 1.0, 1.0],
            ),
        ),
    )
    emitter = ParticleEmitterAsset(
        stable_id="source",
        name="Source",
        event_flows=(ParticleEventFlow("impact", default_event_graph("impact")),),
    )
    asset = ParticleGraphAsset(
        stable_id="event-graph",
        emitters=(emitter,),
        event_types=(impact,),
    )

    restored = ParticleGraphAsset.from_json(asset.canonical_json())
    assert restored == asset
    program = ParticleGraphCompiler().compile(asset)
    assert len(program.events.event_types) == 1
    event_type = program.events.event_types[0]
    assert event_type.payload_stride_words == 7
    assert [(field.word_offset, field.word_count) for field in event_type.fields] == [
        (0, 3),
        (3, 4),
    ]
    assert event_type.stable_type_hash != 0
    assert program.events.event_abi_u64 != 0
    assert program.emitters[0].event_flows[0].flow_id == "impact"
    kernel = ParticleKernelLowerer().lower(program)
    opcodes = [item.opcode for item in kernel.emitters[0].update.instructions]
    assert "event_begin" in opcodes
    assert "event_complete" in opcodes

    changed = replace(
        asset,
        event_types=(replace(impact, queue_capacity=64),),
    )
    assert (
        ParticleGraphCompiler().compile(changed).events.event_abi_hash
        != program.events.event_abi_hash
    )


def test_particle_event_flows_keep_distinct_per_event_continuation_domains():
    emitter = ParticleEmitterAsset(
        stable_id="source",
        name="Source",
        event_flows=(
            ParticleEventFlow("impact", default_event_graph("impact")),
            ParticleEventFlow("expire", default_event_graph("expire")),
        ),
    )
    hir = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            emitters=(emitter,),
            event_types=(
                ParticleEventType("impact", "Impact", 8),
                ParticleEventType("expire", "Expire", 16),
            ),
        )
    )
    kernel = ParticleKernelLowerer().lower(hir)

    assert [flow.flow_id for flow in hir.emitters[0].event_flows] == [
        "impact",
        "expire",
    ]
    begin = [
        item.immediate_dict()
        for item in kernel.emitters[0].update.instructions
        if item.opcode == "event_begin"
    ]
    assert [item["queue_capacity"] for item in begin] == [8, 16]


def test_multiple_active_event_roots_share_one_queued_invocation():
    def active_flow(flow_id: str, node_id: str, node_type: str) -> ParticleEventFlow:
        return ParticleEventFlow(
            flow_id,
            GraphDocument(
                "particle.event",
                nodes=(
                    GraphNodeRecord(
                        "root.event",
                        PARTICLE_EVENT_ACTIVE_TYPE_ID,
                        properties={"event": "impact"},
                    ),
                    GraphNodeRecord(node_id, node_type),
                ),
                links=(
                    GraphLinkRecord(
                        "exec",
                        "root.event",
                        "out",
                        node_id,
                        "in",
                        PortKind.EXEC,
                    ),
                ),
            ),
        )

    emitter = ParticleEmitterAsset(
        update=_event_trigger_stage("impact"),
        event_flows=(
            active_flow("grow", "size", "particle.attribute.size"),
            active_flow("tint", "color", "particle.attribute.color"),
        ),
    )
    hir = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            emitters=(emitter,),
            event_types=(ParticleEventType("impact", "Impact", 16),),
        )
    )
    assert len(hir.emitters[0].event_flows) == 1
    assert [
        operation.opcode
        for operation in hir.emitters[0].event_flows[0].flow.iter_operations()
    ] == ["attribute.modify_size", "attribute.modify_color"]

    instructions = ParticleKernelLowerer().lower(hir).emitters[0].update.instructions
    assert sum(item.opcode == "event_enqueue" for item in instructions) == 1
    assert sum(item.opcode == "event_begin" for item in instructions) == 1
    assert sum(item.opcode == "event_complete" for item in instructions) == 1


def test_particle_event_flow_requires_a_graph_level_event_definition():
    emitter = ParticleEmitterAsset(
        event_flows=(ParticleEventFlow("missing", default_event_graph("missing")),)
    )
    with pytest.raises(ParticleGraphSchemaError, match="flows for unknown events"):
        ParticleGraphAsset(emitters=(emitter,))


def test_particle_event_trigger_requires_a_matching_event_flow_on_the_emitter():
    source = ParticleEmitterAsset(
        stable_id="source",
        update=_event_trigger_stage("event"),
    )
    asset = ParticleGraphAsset(
        emitters=(source,),
        event_types=(ParticleEventType("event", "Event", 16),),
    )

    with pytest.raises(ParticleCompileError, match="dragged into emitter"):
        ParticleGraphCompiler().compile(asset)


def test_particle_event_trigger_compiles_for_an_implemented_event():
    source = ParticleEmitterAsset(
        stable_id="source",
        update=_event_trigger_stage("event"),
        event_flows=(ParticleEventFlow("event", default_event_graph("event")),),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            emitters=(source,),
            event_types=(ParticleEventType("event", "Event", 16),),
        )
    )
    kernel = ParticleKernelLowerer().lower(program)
    instructions = kernel.emitters[0].update.instructions
    assert sum(item.opcode == "event_enqueue" for item in instructions) == 1


def test_scene_collision_is_not_a_public_authoring_node():
    definitions = particle_graph_node_definitions(ParticleGraphAsset())
    assert definitions.registry.get("particle.collision.scene") is None
    assert all(
        "scene collision" not in definition.type_id.lower()
        and "scene collision" not in definition.display_name.lower()
        for definition in definitions.registry.definitions()
    )


def test_particle_event_schema_fingerprint_invalidates_event_expression_programs():
    source = ParticleEmitterAsset(
        stable_id="source",
        update=_event_trigger_stage("event"),
        event_flows=(ParticleEventFlow("event", default_event_graph("event")),),
    )

    def compile_default(default: float):
        event_type = ParticleEventType(
            "event",
            "Event",
            16,
            (ParticleEventField("weight", "Weight", TypeRef(ValueType.F32), default),),
        )
        return ParticleGraphCompiler().compile(
            ParticleGraphAsset(
                emitters=(source,),
                event_types=(event_type,),
            )
        )

    first = compile_default(1.0)
    second = compile_default(2.0)
    assert first.semantic_hash != second.semantic_hash


def test_particle_graph_persists_builtin_defaults_and_node_owned_attribute_cache():
    attributes = list(ParticleEmitterAsset().attributes)
    color_index = next(
        index
        for index, attribute in enumerate(attributes)
        if attribute.stable_id == "builtin.color"
    )
    color = attributes[color_index]
    attributes[color_index] = ParticleAttribute(
        color.stable_id,
        color.name,
        color.value_type,
        [1.0, 0.25, 0.1, 1.0],
    )
    init = GraphDocument(
        "particle.init",
        nodes=(
            GraphNodeRecord("root.init", "particle.root.init"),
            GraphNodeRecord(
                "cache.temperature",
                "particle.attribute.cache",
                properties={
                    "name": "Temperature",
                    "value_type": "f32",
                    "value_space": "none",
                    "composition": "set",
                    "value": 3.5,
                },
            ),
        ),
        links=(
            GraphLinkRecord(
                "init-cache",
                "root.init",
                "out",
                "cache.temperature",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    asset = ParticleGraphAsset(
        emitters=(ParticleEmitterAsset(attributes=tuple(attributes), init=init),)
    )

    document = asset.to_dict()
    emitter_document = document["emitters"][0]
    assert "attributes" not in emitter_document
    assert emitter_document["attribute_defaults"] == {
        "builtin.color": [1.0, 0.25, 0.1, 1.0]
    }
    assert "attribute_cache" not in emitter_document
    cache_document = emitter_document["stages"]["init"]["nodes"][1]
    assert cache_document["properties"]["name"] == "Temperature"
    assert cache_document["properties"]["value_type"] == "f32"
    assert ParticleGraphAsset.from_dict(document) == asset

    stale = copy.deepcopy(document)
    stale["emitters"][0]["attribute_cache"] = []
    with pytest.raises(ParticleGraphSchemaError, match="keys mismatch"):
        ParticleGraphAsset.from_dict(stale)


def test_attribute_cache_rejects_an_empty_authoring_name():
    init = GraphDocument(
        "particle.init",
        nodes=(
            GraphNodeRecord("root.init", "particle.root.init"),
            GraphNodeRecord(
                "cache.empty",
                "particle.attribute.cache",
                properties={
                    "name": "   ",
                    "value_type": "f32",
                    "value_space": "none",
                    "composition": "set",
                    "value": 0.5,
                },
            ),
        ),
        links=(
            GraphLinkRecord(
                "init-cache",
                "root.init",
                "out",
                "cache.empty",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    emitter = ParticleEmitterAsset(init=init)

    with pytest.raises(ParticleGraphSchemaError, match="name must not be empty"):
        particle_cache_attributes(emitter)
    with pytest.raises(ParticleGraphSchemaError, match="name must not be empty"):
        ParticleGraphCompiler().compile(ParticleGraphAsset(emitters=(emitter,)))


def test_attribute_cache_is_written_in_init_and_read_live_from_update():
    cache_id = particle_attribute_cache_id("init", "cache.set")
    init = GraphDocument(
        "particle.init",
        nodes=(
            GraphNodeRecord("root.init", "particle.root.init"),
            GraphNodeRecord(
                "cache.set",
                    "particle.attribute.cache",
                    properties={
                        "name": "Temperature",
                        "value_type": "f32",
                        "value_space": "none",
                        "composition": "set",
                        "value": 3.5,
                },
            ),
        ),
        links=(
            GraphLinkRecord(
                "init-cache",
                "root.init",
                "out",
                "cache.set",
                "in",
                PortKind.EXEC,
            ),
        ),
    )
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "cache.get",
                "particle.attribute.get",
                properties={"attribute": cache_id},
            ),
            GraphNodeRecord(
                "size.set",
                "particle.attribute.size",
                properties={"composition": "set", "value": 1.0},
            ),
        ),
        links=(
            GraphLinkRecord(
                "update-size",
                "root.update",
                "out",
                "size.set",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "cache-size",
                "cache.get",
                "value",
                "size.set",
                "value",
                PortKind.VALUE,
            ),
        ),
    )
    emitter = ParticleEmitterAsset(init=init, update=update)

    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(emitter,))
    )
    init_operation = next(
        operation
        for operation in program.emitters[0].init.flow.iter_operations()
        if operation.source_node_uid == "cache.set"
    )
    assert init_operation.opcode == "attribute.modify_cache"
    assert init_operation.parameter_dict()["attribute"] == cache_id
    kernel = ParticleKernelLowerer().lower(program).emitters[0]
    assert cache_id in kernel.init.written_attributes
    assert cache_id in kernel.update.read_attributes
    assert any(
        instruction.opcode == "store_attribute"
        and instruction.immediate_dict()["attribute"] == cache_id
        for instruction in kernel.init.instructions
    )
    assert any(
        instruction.opcode == "load_attribute"
        and instruction.immediate_dict()["attribute"] == cache_id
        for instruction in kernel.update.instructions
    )


def test_particle_script_attribute_cache_uses_graph_nodes_and_shared_hir():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class CachedMotion(ParticleScript):
    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()
        def init(self, ctx, particles):
            particles.set_attribute("Force", (0.0, 1.0, 0.0))

        def update(self, ctx, particles):
            particles.add_velocity(particles.get_attribute("Force"))

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="CachedMotion.particle.py")
    emitter = asset.emitters[0]

    cache = particle_cache_attributes(emitter)[0]
    assert cache.value_type == TypeRef(ValueType.VEC3)
    assert emitter.init.nodes[1].type_id == "particle.attribute.cache"
    assert emitter.init.nodes[1].properties == {
        "name": "Force",
        "value_type": "vec3",
        "value_space": "none",
        "composition": "set",
        "value": [0.0, 1.0, 0.0],
    }
    assert any(
        node.type_id == "particle.attribute.get"
        and node.properties["attribute"] == cache.stable_id
        for node in emitter.update.nodes
    )

    program = compiler.compile(source, source_name="CachedMotion.particle.py")
    update_operations = tuple(program.emitters[0].update.flow.iter_operations())
    assert [operation.opcode for operation in update_operations] == [
        "attribute.modify_velocity",
    ]


def test_particle_script_cache_infers_expression_type_and_reads_zero_before_writer():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class DeferredCache(ParticleScript):
    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()

        def init(self, ctx, particles):
            particles.set_velocity(particles.get_attribute("Later Force"))

        def update(self, ctx, particles):
            particles.set_attribute("Later Force", particles.position * 2.0)

        def rendering(self, ctx, particles):
            particles.sprite()
'''
    compiler = ParticleScriptCompiler()
    asset = compiler.parse(source, source_name="DeferredCache.particle.py")
    emitter = asset.emitters[0]
    cache = particle_cache_attributes(emitter)[0]

    assert cache.value_type == TypeRef(
        ValueType.VEC3, CoordinateSpace.SIMULATION
    )
    assert cache.default == [0.0, 0.0, 0.0]

    kernel = ParticleKernelLowerer().lower(
        ParticleGraphCompiler().compile(asset)
    ).emitters[0]
    init_load = next(
        instruction
        for instruction in kernel.init.instructions
        if instruction.opcode == "load_attribute"
        and instruction.immediate_dict()["attribute"] == cache.stable_id
    )
    default_store = next(
        instruction
        for instruction in kernel.init.instructions
        if instruction.opcode == "store_attribute"
        and instruction.immediate_dict()["attribute"] == cache.stable_id
    )
    assert kernel.init.instructions.index(default_store) < kernel.init.instructions.index(
        init_load
    )


def test_particle_script_rejects_multiple_owners_for_one_attribute_cache():
    source = '''\
from infernux.particle import ParticleScript, ParticleEmitter, EmitterSettings

class DuplicateCache(ParticleScript):
    class Emitter(ParticleEmitter):
        stable_id = "emitter"
        settings = EmitterSettings()
        def init(self, ctx, particles):
            particles.set_attribute("Shared", 1.0)
        def update(self, ctx, particles):
            particles.set_attribute("Shared", 2.0)
'''
    with pytest.raises(
        ParticleScriptError, match="already has an owning write node"
    ):
        ParticleScriptCompiler().parse(
            source, source_name="DuplicateCache.particle.py"
        )


@pytest.mark.parametrize(
    "value_type, default",
    [
        (ValueType.STRING, "text"),
        (ValueType.ASSET_REF, {"guid": "asset-guid"}),
        (
            ValueType.CURVE,
            {
                "keys": [
                    {
                        "time": 0.0,
                        "value": 1.0,
                        "in_tangent": 0.0,
                        "out_tangent": 0.0,
                    }
                ],
                "pre_wrap": "clamp",
                "post_wrap": "clamp",
            },
        ),
        (
            ValueType.GRADIENT,
            {
                "keys": [{"time": 0.0, "color": [1.0, 1.0, 1.0, 1.0]}],
                "mode": "linear",
            },
        ),
    ],
)
def test_particle_attributes_reject_property_only_types(value_type, default):
    with pytest.raises(ParticleGraphSchemaError, match="GPU-storable type"):
        ParticleAttribute("custom.invalid", "invalid", TypeRef(value_type), default)


def test_particle_graph_rejects_unknown_field():
    value = ParticleGraphAsset(stable_id="current-particle").to_dict()
    value["unknown"] = 1

    with pytest.raises(ParticleGraphSchemaError, match="keys mismatch"):
        ParticleGraphAsset.from_dict(value)


def test_particle_data_interfaces_round_trip_with_stable_identity_and_space():
    emitter = ParticleEmitterAsset(
        stable_id="data-emitter",
        data_interfaces=(
            VectorField(
                stable_id="wind-field",
                name="Wind",
                texture=AssetReference(path_hint="Assets/Fields/Wind.vectorfield"),
                space="world",
                boundary="repeat",
                filtering="linear",
                vector_scale=2.5,
            ),
            SdfVolume(
                stable_id="collision-field",
                name="Collision",
                texture=AssetReference(path_hint="Assets/Fields/Collision.inxsdf"),
                distance_scale=1.5,
            ),
        ),
    )
    asset = ParticleGraphAsset(stable_id="data-graph", emitters=(emitter,))

    restored = ParticleGraphAsset.from_json(asset.canonical_json())
    hir = ParticleGraphCompiler().compile(restored)

    assert restored == asset
    assert [interface.stable_id for interface in hir.emitters[0].data_interfaces] == [
        "wind-field",
        "collision-field",
    ]
    assert hir.emitters[0].data_interfaces[0].boundary.value == "repeat"

    with pytest.raises(ParticleGraphSchemaError, match="stable ids must be unique"):
        ParticleEmitterAsset(
            data_interfaces=(
                VectorField(stable_id="duplicate"),
                SdfVolume(stable_id="duplicate"),
            )
        )


@pytest.mark.parametrize("kind", ["mesh_resource"])
def test_mesh_resource_bindings_are_not_public_data_interfaces(kind):
    from infernux.particle.data_interface import (
        ParticleDataInterfaceError,
        particle_data_interface_from_dict,
    )

    value = {
        "kind": kind,
        "stable_id": "surface-mesh",
        "name": "Surface Mesh",
        "mesh": AssetReference(guid="mesh-guid").to_dict(),
        "mesh_parameter": "surface-mesh",
        "space": "emitter_local",
        "mesh_to_space": [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ],
    }

    with pytest.raises(ParticleDataInterfaceError, match="unsupported"):
        particle_data_interface_from_dict(value)


def test_runtime_mesh_resource_parser_accepts_only_current_internal_schema():
    from infernux.particle.data_interface import (
        MeshResourceBinding,
        ParticleDataInterfaceError,
        particle_runtime_resource_from_dict,
    )

    current = MeshResourceBinding(
        stable_id="surface-mesh",
        name="Surface Mesh",
        mesh=AssetReference(guid="mesh-guid"),
        mesh_parameter="surface-mesh",
    ).to_dict()

    restored = particle_runtime_resource_from_dict(current)
    assert restored == MeshResourceBinding(
        stable_id="surface-mesh",
        name="Surface Mesh",
        mesh=AssetReference(guid="mesh-guid"),
        mesh_parameter="surface-mesh",
    )


@pytest.mark.parametrize("stage", ["init", "update", "rendering"])
def test_particle_graph_rejects_deleted_or_replaced_stage_root(stage):
    value = ParticleGraphAsset().to_dict()
    stage_document = value["emitters"][0]["stages"][stage]
    stage_document["nodes"] = [
        node for node in stage_document["nodes"] if not node["type_id"].startswith("particle.root.")
    ]
    stage_document["links"] = [
        link
        for link in stage_document["links"]
        if not link["source_node"].startswith("root.")
        and not link["target_node"].startswith("root.")
    ]

    with pytest.raises(ParticleGraphSchemaError, match="mandatory root"):
        ParticleGraphAsset.from_dict(value)


def test_particle_graph_compiler_builds_multi_emitter_schedule_and_render_plan():
    first = ParticleEmitterAsset(
        stable_id="smoke",
        name="Smoke",
        settings=EmitterSettings(
            capacity=100_000,
            simulation_space=SimulationSpace.WORLD,
            spawn_rate=20_000.0,
        ),
    )
    rendering = first.rendering
    sprite = rendering.nodes[1]
    first = ParticleEmitterAsset(
        stable_id=first.stable_id,
        name=first.name,
        settings=first.settings,
        attributes=first.attributes,
        init=first.init,
        update=first.update,
        rendering=GraphDocument(
            rendering.domain,
            (
                rendering.nodes[0],
                GraphNodeRecord(
                    sprite.uid,
                    sprite.type_id,
                    properties={
                        "shader": "Particle Six-Way Smoke",
                        "receive_scene_lighting": True,
                        "receive_shadows": True,
                        "soft_particles": True,
                        "soft_distance": 0.35,
                        "sort": "back_to_front",
                    },
                ),
            ),
            rendering.links,
        ),
    )
    sparks = ParticleEmitterAsset(stable_id="sparks", name="Sparks")
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(stable_id="fire", name="Fire", emitters=(first, sparks))
    )

    assert program.schedule.emitter_ids == ("smoke", "sparks")
    smoke = program.emitters[0]
    assert smoke.init.stage is ParticleStage.INIT
    assert _operations(smoke.init)[0].opcode == "emitter.sample_shape"
    assert _operations(smoke.update)[0].opcode == "attribute.modify_velocity"
    assert smoke.render_plan.outputs[0].shader == "Particle Six-Way Smoke"
    assert {item.name for item in smoke.render_plan.outputs[0].shader_properties} >= {
        "baseColor",
        "positiveAxesMap",
        "negativeAxesMap",
    }
    assert smoke.render_plan.outputs[0].receive_scene_lighting is True
    assert smoke.render_plan.outputs[0].receive_shadows is True
    assert smoke.render_plan.outputs[0].cast_shadows is False
    assert smoke.render_plan.outputs[0].soft_particles is True
    assert smoke.render_plan.outputs[0].soft_distance == pytest.approx(0.35)


def test_particle_graph_compiler_rejects_rendering_without_output():
    emitter = ParticleEmitterAsset(
        rendering=GraphDocument(
            "particle.rendering",
            (GraphNodeRecord("root.rendering", "particle.root.rendering"),),
        )
    )
    asset = ParticleGraphAsset(emitters=(emitter,))

    with pytest.raises(ParticleCompileError, match="at least one output"):
        ParticleGraphCompiler().compile(asset)


def test_particle_sprite_output_rejects_static_mesh_shadow_property():
    rendering = GraphDocument(
        "particle.rendering",
        nodes=(
            GraphNodeRecord("root.rendering", "particle.root.rendering"),
            GraphNodeRecord(
                "output.sprite",
                "particle.output.sprite",
                properties={"cast_shadows": True},
            ),
        ),
        links=(
            GraphLinkRecord(
                "root-to-sprite",
                "root.rendering",
                "out",
                "output.sprite",
                "in",
                PortKind.EXEC,
            ),
        ),
    )

    with pytest.raises(ParticleCompileError, match="unknown properties"):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(rendering=rendering),))
        )


def test_particle_sprite_output_compiles_valid_flipbook_grid_and_rejects_invalid_grid():
    def compile_grid(columns, rows):
        rendering = GraphDocument(
            "particle.rendering",
            nodes=(
                GraphNodeRecord("root.rendering", "particle.root.rendering"),
                GraphNodeRecord(
                    "output.sprite",
                    "particle.output.sprite",
                    properties={"flipbook_columns": columns, "flipbook_rows": rows},
                ),
            ),
            links=(
                GraphLinkRecord(
                    "root-to-sprite",
                    "root.rendering",
                    "out",
                    "output.sprite",
                    "in",
                    PortKind.EXEC,
                ),
            ),
        )
        return ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(rendering=rendering),))
        )

    output = compile_grid(8, 4).emitters[0].render_plan.outputs[0]
    assert output.flipbook_columns == 8
    assert output.flipbook_rows == 4

    with pytest.raises(ParticleCompileError, match="flipbook grid"):
        compile_grid(0, 4)
    with pytest.raises(ParticleCompileError, match="flipbook grid"):
        compile_grid(4096, 4096)


def test_particle_sprite_output_compiles_alignment_and_rejects_invalid_axis():
    def compile_alignment(alignment, axis=(0.0, 1.0, 0.0)):
        rendering = GraphDocument(
            "particle.rendering",
            nodes=(
                GraphNodeRecord("root.rendering", "particle.root.rendering"),
                GraphNodeRecord(
                    "output.sprite",
                    "particle.output.sprite",
                    properties={"alignment": alignment, "alignment_axis": list(axis)},
                ),
            ),
            links=(
                GraphLinkRecord(
                    "root-to-sprite",
                    "root.rendering",
                    "out",
                    "output.sprite",
                    "in",
                    PortKind.EXEC,
                ),
            ),
        )
        return ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(rendering=rendering),))
        )

    output = compile_alignment("axis", (0.0, 0.0, 1.0)).emitters[0].render_plan.outputs[0]
    assert output.sprite_alignment == "axis"
    assert output.alignment_axis == (0.0, 0.0, 1.0)

    with pytest.raises(ParticleCompileError, match="alignment must be"):
        compile_alignment("screen")
    with pytest.raises(ParticleCompileError, match="alignment axis"):
        compile_alignment("axis", (0.0, 0.0, 0.0))


def test_particle_graph_compiles_static_mesh_output_with_explicit_asset():
    rendering = GraphDocument(
        "particle.rendering",
        nodes=(
            GraphNodeRecord("root.rendering", "particle.root.rendering"),
            GraphNodeRecord(
                "output.mesh",
                "particle.output.mesh",
                properties={
                    "mesh": AssetReference(
                        guid="mesh-guid", path_hint="Assets/Models/Debris.fbx"
                    ).to_dict(),
                    "shader": "Particle Unlit",
                    "receive_scene_lighting": False,
                    "receive_shadows": False,
                    "sort": "none",
                },
            ),
        ),
        links=(
            GraphLinkRecord(
                "root-to-mesh",
                "root.rendering",
                "out",
                "output.mesh",
                "in",
                PortKind.EXEC,
            ),
        ),
    )

    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(rendering=rendering),))
    )
    compiled_emitter = program.emitters[0]
    output = compiled_emitter.render_plan.outputs[0]

    assert output.output_type == "mesh"
    assert output.mesh == AssetReference(
        guid="mesh-guid", path_hint="Assets/Models/Debris.fbx"
    )
    assert output.shader == "Particle Unlit"
    assert {item.name for item in output.shader_properties} == {
        "baseColor",
        "glow",
        "rainbow",
        "texSampler",
    }
    assert output.soft_particles is False
    assert output.cast_shadows is False
    assert output.sort_mode == "none"
    orientation = next(
        attribute
        for attribute in compiled_emitter.attributes
        if attribute.stable_id == "builtin.orientation"
    )
    assert orientation.value_type == TypeRef(ValueType.VEC3)
    assert orientation.default == [0.0, 0.0, 0.0]


def test_particle_graph_mesh_parameter_connects_directly_to_static_mesh_output():
    default_mesh = AssetReference(
        guid="mesh-guid",
        path_hint="Assets/Models/Debris.fbx",
    )
    rendering = GraphDocument(
        "particle.rendering",
        nodes=(
            GraphNodeRecord("root.rendering", "particle.root.rendering"),
            GraphNodeRecord(
                "mesh.parameter",
                "particle.parameter",
                properties={"parameter": "output-mesh"},
            ),
            GraphNodeRecord("output.mesh", "particle.output.mesh"),
        ),
        links=(
            GraphLinkRecord(
                "root-to-mesh",
                "root.rendering",
                "out",
                "output.mesh",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord(
                "parameter-to-mesh",
                "mesh.parameter",
                "value",
                "output.mesh",
                "mesh",
                PortKind.VALUE,
            ),
        ),
    )
    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(
            parameters=(
                ParticleParameter(
                    "output-mesh",
                    "Output Mesh",
                    TypeRef(ValueType.MESH),
                    default_mesh.to_dict(),
                ),
            ),
            emitters=(ParticleEmitterAsset(rendering=rendering),),
        )
    )

    output = program.emitters[0].render_plan.outputs[0]
    assert output.mesh_parameter == "output-mesh"
    assert output.mesh == AssetReference()
    kernel = ParticleKernelLowerer().lower(program)
    assert not any(
        instruction.opcode == "load_parameter"
        and instruction.immediate_dict().get("parameter") == "output-mesh"
        for instruction in kernel.emitters[0].rendering.instructions
    )
    compile_gpu_particle_spirv(GpuParticleGlslLowerer().lower(kernel))


def test_particle_graph_rejects_static_mesh_output_without_mesh_asset():
    rendering = GraphDocument(
        "particle.rendering",
        nodes=(
            GraphNodeRecord("root.rendering", "particle.root.rendering"),
            GraphNodeRecord("output.mesh", "particle.output.mesh"),
        ),
        links=(
            GraphLinkRecord(
                "root-to-mesh",
                "root.rendering",
                "out",
                "output.mesh",
                "in",
                PortKind.EXEC,
            ),
        ),
    )

    with pytest.raises(ParticleCompileError, match="requires a mesh asset"):
        ParticleGraphCompiler().compile(
            ParticleGraphAsset(emitters=(ParticleEmitterAsset(rendering=rendering),))
        )


def test_particle_graph_compiles_lit_shadow_receiving_static_mesh_output():
    rendering = GraphDocument(
        "particle.rendering",
        nodes=(
            GraphNodeRecord("root.rendering", "particle.root.rendering"),
            GraphNodeRecord(
                "output.mesh",
                "particle.output.mesh",
                properties={
                    "mesh": AssetReference(guid="mesh-guid").to_dict(),
                    "receive_scene_lighting": True,
                    "receive_shadows": True,
                    "cast_shadows": True,
                },
            ),
        ),
        links=(
            GraphLinkRecord(
                "root-to-mesh",
                "root.rendering",
                "out",
                "output.mesh",
                "in",
                PortKind.EXEC,
            ),
        ),
    )

    graph_asset = ParticleGraphAsset(
        emitters=(ParticleEmitterAsset(rendering=rendering),)
    )
    restored_graph = ParticleGraphAsset.from_json(graph_asset.canonical_json())
    output = ParticleGraphCompiler().compile(restored_graph).emitters[0].render_plan.outputs[0]

    assert output.receive_scene_lighting is True
    assert output.receive_shadows is True
    assert output.cast_shadows is True


@pytest.mark.parametrize("sort_mode", ["back_to_front", "front_to_back"])
def test_particle_graph_compiles_sorted_static_mesh_output(sort_mode):
    properties = {
        "mesh": AssetReference(guid="mesh-guid").to_dict(),
        "sort": sort_mode,
    }
    rendering = GraphDocument(
        "particle.rendering",
        nodes=(
            GraphNodeRecord("root.rendering", "particle.root.rendering"),
            GraphNodeRecord(
                "output.mesh", "particle.output.mesh", properties=properties
            ),
        ),
        links=(
            GraphLinkRecord(
                "root-to-mesh",
                "root.rendering",
                "out",
                "output.mesh",
                "in",
                PortKind.EXEC,
            ),
        ),
    )

    output = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(rendering=rendering),))
    ).emitters[0].render_plan.outputs[0]

    assert output.output_type == "mesh"
    assert output.sort_mode == sort_mode


def test_particle_graph_exec_order_lowers_to_stage_operations():
    init = GraphDocument(
        "particle.init",
        nodes=(
            GraphNodeRecord("root.init", "particle.root.init"),
            GraphNodeRecord(
                "velocity",
                "particle.attribute.velocity",
                properties={"value": [0.0, 2.0, 0.0]},
            ),
            GraphNodeRecord(
                "lifetime",
                "particle.attribute.lifetime",
                properties={"value": 3.0},
            ),
        ),
        links=(
            GraphLinkRecord("l1", "root.init", "out", "velocity", "in", PortKind.EXEC),
            GraphLinkRecord("l2", "velocity", "out", "lifetime", "in", PortKind.EXEC),
        ),
    )
    emitter = ParticleEmitterAsset(init=init)
    hir = ParticleGraphCompiler().compile(ParticleGraphAsset(emitters=(emitter,))).emitters[0]

    assert [operation.opcode for operation in _operations(hir.init)] == [
        "emitter.sample_shape",
        "attribute.modify_velocity",
        "attribute.modify_lifetime",
    ]


def test_particle_stage_value_links_use_common_typed_expression_ir():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("gravity", "particle.attribute.velocity"),
            GraphNodeRecord(
                "a",
                "common.constant.vec3",
                properties={"value": [0.0, -4.0, 0.0]},
            ),
            GraphNodeRecord(
                "b",
                "common.constant.vec3",
                properties={"value": [1.0, 0.0, 0.0]},
            ),
            GraphNodeRecord("add", "common.math.add"),
            GraphNodeRecord("normalize", "common.vector.normalize"),
        ),
        links=(
            GraphLinkRecord("s1", "root.update", "out", "gravity", "in", PortKind.EXEC),
            GraphLinkRecord("v1", "a", "value", "add", "a"),
            GraphLinkRecord("link_b", "b", "value", "add", "b"),
            GraphLinkRecord("v3", "add", "result", "normalize", "value"),
            GraphLinkRecord("v4", "normalize", "result", "gravity", "value"),
        ),
    )
    emitter = ParticleEmitterAsset(update=update)
    hir = ParticleGraphCompiler().compile(ParticleGraphAsset(emitters=(emitter,))).emitters[0]

    assert [instruction.opcode for instruction in hir.update.expressions.instructions] == [
        "constant",
        "constant",
        "add",
        "normalize",
    ]
    assert _operations(hir.update)[-1].value_bindings == (("value", "normalize.result"),)


def test_particle_update_can_author_color_and_size_over_lifetime():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord("set-color", "particle.attribute.color"),
            GraphNodeRecord("set-size", "particle.attribute.size"),
            GraphNodeRecord(
                "age",
                "particle.attribute.get",
                properties={"attribute": "builtin.age"},
            ),
            GraphNodeRecord(
                "lifetime",
                "particle.attribute.get",
                properties={"attribute": "builtin.lifetime"},
            ),
            GraphNodeRecord("normalized-age", "common.math.divide"),
            GraphNodeRecord(
                "start-color",
                "common.constant.color",
                properties={"value": [1.0, 0.5, 0.0, 1.0]},
            ),
            GraphNodeRecord(
                "end-color",
                "common.constant.color",
                properties={"value": [0.1, 0.0, 0.0, 0.0]},
            ),
            GraphNodeRecord("color-over-life", "common.math.lerp"),
            GraphNodeRecord("size-over-life", "common.math.lerp", properties={"a": 1.0, "b": 0.0}),
        ),
        links=(
            GraphLinkRecord("stream-color", "root.update", "out", "set-color", "in", PortKind.EXEC),
            GraphLinkRecord("stream-size", "set-color", "out", "set-size", "in", PortKind.EXEC),
            GraphLinkRecord("age-divide", "age", "value", "normalized-age", "a"),
            GraphLinkRecord("life-divide", "lifetime", "value", "normalized-age", "b"),
            GraphLinkRecord("color-a", "start-color", "value", "color-over-life", "a"),
            GraphLinkRecord("color-b", "end-color", "value", "color-over-life", "b"),
            GraphLinkRecord("color-t", "normalized-age", "result", "color-over-life", "t"),
            GraphLinkRecord("size-t", "normalized-age", "result", "size-over-life", "t"),
            GraphLinkRecord("set-color-value", "color-over-life", "result", "set-color", "value"),
            GraphLinkRecord("set-size-value", "size-over-life", "result", "set-size", "value"),
        ),
    )

    emitter = ParticleEmitterAsset(update=update)
    program = ParticleGraphCompiler().compile(ParticleGraphAsset(emitters=(emitter,)))
    hir = program.emitters[0]
    kernel = ParticleKernelLowerer().lower(program).emitters[0]

    assert [operation.opcode for operation in _operations(hir.update)[-2:]] == [
        "attribute.modify_color",
        "attribute.modify_size",
    ]
    assert {instruction.opcode for instruction in kernel.update.instructions} >= {
        "divide",
        "lerp",
        "store_attribute",
    }
    assert {"builtin.color", "builtin.size"} <= set(kernel.update.written_attributes)


def test_transform_position_to_set_position_inserts_position_space_conversion():
    update = GraphDocument(
        "particle.update",
        nodes=(
            GraphNodeRecord("root.update", "particle.root.update"),
            GraphNodeRecord(
                "get-position",
                "particle.attribute.get",
                properties={"attribute": "builtin.position"},
            ),
            GraphNodeRecord(
                "to-world",
                "common.space.transform_position",
                properties={"target_space": "world"},
            ),
            GraphNodeRecord("set-position", "particle.attribute.position"),
        ),
        links=(
            GraphLinkRecord(
                "stream",
                "root.update",
                "out",
                "set-position",
                "in",
                PortKind.EXEC,
            ),
            GraphLinkRecord("get-transform", "get-position", "value", "to-world", "input"),
            GraphLinkRecord("transform-set", "to-world", "value", "set-position", "value"),
        ),
    )

    program = ParticleGraphCompiler().compile(
        ParticleGraphAsset(emitters=(ParticleEmitterAsset(update=update),))
    )
    kernel = ParticleKernelLowerer().lower(program).emitters[0]
    conversion = next(
        instruction
        for instruction in kernel.update.instructions
        if instruction.opcode == "convert_space"
        and instruction.immediate_dict().get("to") == "simulation"
    )
    assert conversion.immediate_dict() == {
        "from": "world",
        "to": "simulation",
        "semantic": "position",
    }
    assert conversion.result_type == TypeRef(ValueType.VEC3, CoordinateSpace.SIMULATION)
    assert "builtin.position" in kernel.update.written_attributes
