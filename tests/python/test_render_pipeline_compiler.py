import pytest

from infernux.core.asset_ref import RenderEffectRef
from infernux.rendergraph.graph import RenderGraph
from infernux.renderstack.effect_stage import EffectScope
from infernux.renderstack.pipeline_dsl import Path, PipelineBuilder, Queue
from infernux.renderstack.pipeline_compiler import compile_pipeline_definition
from infernux.renderstack.render_effect import RenderEffect
from infernux.renderstack.render_effect_asset import RenderEffectAsset
from infernux.renderstack.render_pipeline import RenderPipeline
from infernux.renderstack.render_stack import RenderStack
from infernux.renderstack.route_policy import RoutePolicy


def _forward_mixed_definition():
    pipeline = PipelineBuilder()
    pipeline.frame(hdr=True)
    pipeline.shadows(resolution=1024)

    with pipeline.opaque() as opaque:
        with opaque.layer("Stylized") as layer:
            layer.forward(Queue(1, 100)).effects("low_queue")
            layer.forward(Queue(101, 200)).effects("middle_queue")
            layer.effects("stylized_combined")
        opaque.otherwise().forward()
        opaque.effects("opaque_only")

    pipeline.effects("after_opaque")
    pipeline.sky()
    pipeline.effects("after_sky")
    with pipeline.transparent() as transparent:
        transparent.otherwise().forward()
        transparent.effects("transparent_only")
    pipeline.effects("final")
    pipeline.screen_ui()
    return pipeline.build()


def test_compiler_creates_distinct_route_layer_stage_and_scene_images():
    graph = RenderGraph("Mixed")
    resources = {}

    def capture(stage):
        resources[stage.stable_id] = {name: handle.name for name, handle in graph.current_effect_resources.items()}

    graph._effect_stage_callback = capture
    compile_pipeline_definition(_forward_mixed_definition(), graph)

    assert resources["low_queue"]["color"] != resources["middle_queue"]["color"]
    assert resources["stylized_combined"]["color"].startswith("layer/")
    assert resources["opaque_only"]["color"].startswith("domain/opaque/")
    assert resources["after_opaque"]["color"] in {"color", "_scene_composite"}
    assert resources["transparent_only"]["color"].startswith("domain/transparent/")
    assert resources["final"]["color"] in {"color", "_scene_composite"}
    assert resources["low_queue"]["depth"] == "depth"

    stages = {stage.stable_id: stage for stage in graph.effect_stages}
    assert stages["low_queue"].scope is EffectScope.ROUTE
    assert stages["stylized_combined"].scope is EffectScope.LAYER
    assert stages["opaque_only"].scope is EffectScope.STAGE
    assert stages["after_opaque"].scope is EffectScope.COMPOSITE

    description = graph.build()
    assert description.output_texture == "color"
    assert len(description.passes) == graph.pass_count


def test_msaa_pipeline_resolves_isolated_routes_and_sky_before_effects():
    pipeline = PipelineBuilder()
    pipeline.frame(hdr=True, msaa=4)
    with pipeline.opaque() as opaque:
        opaque.forward(Queue(1, 100)).effects("route_fx")
        opaque.otherwise().forward()
    pipeline.sky()

    graph = RenderGraph("MSAA Routes")
    compile_pipeline_definition(pipeline.build(), graph)
    description = graph.build()
    textures = {texture.name: texture for texture in description.textures}
    route_draws = [
        render_pass
        for render_pass in description.passes
        if render_pass.name.startswith("route/opaque.") and render_pass.name.split("/")[-1].startswith("Draw_")
    ]
    sky = next(render_pass for render_pass in description.passes if render_pass.name == "sky/SkyboxPass")

    assert route_draws
    assert all(render_pass.resolve_color.endswith("/color") for render_pass in route_draws)
    assert all(textures[render_pass.write_colors[0][1]].samples == 4 for render_pass in route_draws)
    assert all(textures[render_pass.resolve_color].samples == 1 for render_pass in route_draws)
    assert sky.resolve_color == "sky/color"
    assert textures[sky.write_colors[0][1]].samples == 4

    frame_clear = next(render_pass for render_pass in description.passes if render_pass.name == "FrameClear")
    sky_under = next(render_pass for render_pass in description.passes if render_pass.name.startswith("under/scene/"))
    assert frame_clear.clear_color_a == 0.0
    assert sky.clear_color_a == 1.0
    assert sky_under.commands[0].shader_name == "Route Alpha Composite"
    assert sky_under.commands[0].input_bindings[0] == ("_BaseTex", "sky/color")


def test_opaque_route_effect_overflow_is_composited_after_ordinary_background():
    pipeline = PipelineBuilder()
    with pipeline.opaque() as opaque:
        with opaque.layer("Effects") as layer:
            layer.forward(Queue(1, 100)).effects("blurred")
        opaque.otherwise().forward()
    pipeline.effects("empty_after_opaque")
    pipeline.sky()

    graph = RenderGraph("Deferred Route Overflow")
    graph._effect_route_policy_resolver = lambda stage_ids: (
        RoutePolicy.ISOLATE_AND_COMPOSITE if "blurred" in stage_ids else RoutePolicy.INLINE
    )
    compile_pipeline_definition(pipeline.build(), graph)
    description = graph.build()
    names = [render_pass.name for render_pass in description.passes]

    ordinary_draw = next(
        index for index, name in enumerate(names) if name.startswith("route/opaque.route_2/") and "/Draw_" in name
    )
    overflow_composite = next(
        index
        for index, render_pass in enumerate(description.passes)
        if any(
            command.shader_name == "Route Alpha Composite"
            and dict(command.input_bindings).get("_LayerTex", "").endswith("route/opaque.route_1/color")
            for command in render_pass.commands
        )
    )
    sky_under = next(index for index, name in enumerate(names) if name.startswith("under/scene/"))

    assert ordinary_draw < overflow_composite
    assert sky_under < overflow_composite


def test_msaa_inline_background_is_not_deferred_over_effect_overflow():
    class MsaaOverflowPipeline(RenderPipeline):
        name = "MSAA Overflow Ordering"

        def define(self, pipeline):
            pipeline.frame(hdr=True, msaa=4)
            with pipeline.opaque() as opaque:
                with opaque.layer("Effects") as layer:
                    layer.forward(Queue(1, 100)).effects("blurred")
                opaque.otherwise().forward()
            pipeline.sky()

    stack = RenderStack()
    stack._pipeline = MsaaOverflowPipeline()
    stack._pipeline._render_stack = stack
    stack.add_effect_slot(
        "blurred",
        RenderEffectRef(effect=_effect("infernux.route.pixelation", pixel_size=18)),
    )

    description = stack.build_graph()
    passes = list(description.passes)
    otherwise_composite = next(
        index
        for index, render_pass in enumerate(passes)
        if any(
            command.shader_name == "Route Alpha Composite"
            and dict(command.input_bindings).get("_LayerTex", "").endswith("route/opaque.route_2/color")
            for command in render_pass.commands
        )
    )
    overflow_composite = next(
        index
        for index, render_pass in enumerate(passes)
        if any(
            command.shader_name == "Route Alpha Composite"
            and dict(command.input_bindings).get("_LayerTex", "").endswith("route/opaque.route_1/color")
            for command in render_pass.commands
        )
    )
    sky_under = next(index for index, render_pass in enumerate(passes) if render_pass.name.startswith("under/scene/"))

    assert otherwise_composite < sky_under < overflow_composite


@pytest.mark.parametrize("with_shadows", [False, True])
def test_deferred_only_route_publishes_its_view_shadow_image(with_shadows):
    from infernux.lib import MaterialPassType

    pipeline = PipelineBuilder()
    if with_shadows:
        pipeline.shadows(resolution=1024)
    pipeline.opaque().deferred(fallback=Path.FORWARD_PLUS)

    graph = RenderGraph("Deferred shadow contract")
    compile_pipeline_definition(pipeline.build(), graph)
    geometry = [render_pass for render_pass in graph._passes if render_pass._material_pass == "gbuffer"]
    assert geometry
    has_shadow_caster = any(render_pass.name == "ShadowCasterPass" for render_pass in graph._passes)
    assert has_shadow_caster == with_shadows
    for render_pass in geometry:
        assert render_pass._input_bindings.get("shadowMap") == ("shadow_map" if with_shadows else None)
        assert ("shadow_map" in render_pass._reads) == with_shadows

    description = graph.build()
    geometry_names = {render_pass.name for render_pass in geometry}
    native_geometry = [
        render_pass for render_pass in description.passes if render_pass.commands and render_pass.name in geometry_names
    ]
    assert len(native_geometry) == len(geometry)
    for render_pass in native_geometry:
        assert render_pass.commands[0].material_pass == MaterialPassType.GBUFFER
        binding_published = ("shadowMap", "shadow_map") in render_pass.commands[0].input_bindings
        assert binding_published == with_shadows


def test_deferred_msaa_requires_and_uses_explicit_forward_plus_fallback():
    pipeline = PipelineBuilder()
    pipeline.frame(msaa=4)
    pipeline.opaque().deferred(fallback=Path.FORWARD_PLUS)
    graph = RenderGraph("Deferred MSAA Fallback")
    compile_pipeline_definition(pipeline.build(), graph)

    material_passes = [
        render_pass._material_pass
        for render_pass in graph._passes
        if render_pass._action == "draw_renderers" and render_pass._material_pass != "motion"
    ]
    assert material_passes
    assert set(material_passes) == {"forward_plus"}

    unsupported = PipelineBuilder()
    unsupported.frame(msaa=4)
    unsupported.opaque().deferred()
    with pytest.raises(ValueError, match="explicit Forward or Forward\\+ fallback"):
        compile_pipeline_definition(unsupported.build(), RenderGraph("Unsupported MSAA"))


class _RouteEffectPipeline(RenderPipeline):
    name = "Route Policy Test"

    def define(self, pipeline):
        with pipeline.opaque() as opaque:
            opaque.forward(Queue(1, 100)).effects("route_fx")
            opaque.otherwise().forward()


def _route_effect_stack(*effects: RenderEffect) -> RenderStack:
    stack = RenderStack()
    stack._pipeline = _RouteEffectPipeline()
    stack._pipeline._render_stack = stack
    for effect in effects:
        stack.add_effect_slot("route_fx", RenderEffectRef(effect=effect))
    return stack


def _effect(feature_type: str, **parameters) -> RenderEffect:
    return RenderEffect(RenderEffectAsset(feature_type=feature_type, parameters=parameters))


def test_pixelation_route_uses_isolation_and_alpha_composite():
    description = _route_effect_stack(
        _effect(
            "infernux.route.pixelation",
            pixel_size=24,
            pixel_aspect=1.5,
            sampling=2,
            preserve_alpha_coverage=True,
        )
    ).build_graph()
    pixelation = next(
        render_pass for render_pass in description.passes if render_pass.name.endswith("Pixelation_Apply")
    )
    command = pixelation.commands[0]
    bindings = dict(command.input_bindings)
    constants = dict(command.push_constants)

    assert bindings["_SourceTex"].startswith("route/opaque.route_1/")
    assert command.shader_name == "Pixelation"
    assert constants["pixelSize"] == 24.0
    assert constants["pixelAspect"] == 1.5
    assert constants["samplingMode"] == 2.0
    assert constants["preserveAlphaCoverage"] == 1.0
    assert any(
        command.shader_name == "Route Alpha Composite"
        for render_pass in description.passes
        for command in render_pass.commands
    )
    assert description.output_texture == "color"


def test_bloom_route_extracts_only_additive_energy_and_handles_one_mip():
    description = _route_effect_stack(_effect("infernux.post.bloom", max_iterations=1)).build_graph()
    names = [render_pass.name for render_pass in description.passes]
    composite = next(render_pass for render_pass in description.passes if render_pass.name.endswith("Bloom_Composite"))
    bindings = dict(composite.commands[0].input_bindings)

    assert "route/opaque.route_1/PreserveOriginal" in names
    assert "route/opaque.route_1/ExtractAdditiveDelta" in names
    assert any(
        command.shader_name == "Route Additive Composite"
        for render_pass in description.passes
        for command in render_pass.commands
    )
    assert bindings["_BloomTex"].endswith("/_bloom_mip0")


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_additive_and_replacement_route_effects_compile_in_slot_order(reverse):
    effects = [
        _effect("infernux.post.bloom", max_iterations=2),
        _effect("infernux.route.pixelation", pixel_size=16),
    ]
    if reverse:
        effects.reverse()
    stack = _route_effect_stack(
        *effects,
    )
    description = stack.build_graph()
    assert stack.effect_compile_errors == ()
    names = [render_pass.name for render_pass in description.passes]
    bloom = next(i for i, name in enumerate(names) if name.endswith("Bloom_Composite"))
    pixelation = next(i for i, name in enumerate(names) if "Pixelation" in name)
    assert (pixelation < bloom) == reverse
