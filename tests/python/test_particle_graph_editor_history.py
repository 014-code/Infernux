from __future__ import annotations

from test_particle_graph_editor import (
    DocumentRegistry,
    PARTICLE_EVENT_TRIGGER_TYPE_ID,
    ParticleEmitterGraphAuthoringModel,
    ParticleGraphAsset,
    SimpleNamespace,
    TypeRef,
    ValueType,
    _finish_particle_graph_save,
    _particle_panel_with_command_core,
    _particle_panel_with_history,
    _save_particle_graph,
    json,
    particle_event_payload_port_id,
    particle_graph_node_definitions,
    pytest,
    replace,
    _isolate_particle_graph_panel_dirty_tracking,
)

def test_particle_graph_editor_dragged_event_root_uses_canvas_position():
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    panel = ParticleGraphEditorPanel()
    panel._record = lambda *_args: None
    event_type = panel.add_event_type("Pulse", 8, [])
    event_id = event_type["stable_id"]

    created = panel.add_authoring_event_flow(event_id, 420.0, 760.0)

    assert created["event_id"] == event_id
    assert created["created"] is True
    flow_id = created["flow_id"]
    root = next(
        node
        for node in panel.authoring_snapshot()["nodes"]
        if node["uid"] == f"event.{flow_id}::root.event"
    )
    assert root["position"] == pytest.approx([420.0, 760.0])
    root_definition = panel._model.get_node_type(
        panel._model.find_node(root["uid"])
    )
    assert root_definition.visual_style == "context"
    assert root_definition.deletable is False
    strict_definition = panel._definition_for_node(
        panel._model.find_node(root["uid"])
    )
    assert all(port.direction.value == "output" for port in strict_definition.ports)
    assert [port.id for port in strict_definition.ports][0] == "out"
    duplicate = panel.add_authoring_event_flow(event_id, 20.0, 30.0)
    assert duplicate["created"] is True
    assert duplicate["flow_id"] != flow_id
    duplicate_root = next(
        node
        for node in panel.authoring_snapshot()["nodes"]
        if node["uid"] == f"event.{duplicate['flow_id']}::root.event"
    )
    assert duplicate_root["position"] == pytest.approx([20.0, 30.0])
    assert panel._selected_node_uid == duplicate_root["uid"]
    assert panel._view.selected_nodes == [duplicate_root["uid"]]


def test_particle_graph_uses_the_shared_editor_shell_and_node_graph_model():
    from infernux.engine.ui.graph_document_authoring import (
        ParticleEmitterGraphAuthoringModel,
    )
    from infernux.engine.ui.node_graph_editor_panel import NodeGraphEditorPanel
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    panel = ParticleGraphEditorPanel()

    assert isinstance(panel, NodeGraphEditorPanel)
    assert isinstance(panel._model, ParticleEmitterGraphAuthoringModel)
    assert panel._view.on_link_created == panel._on_link_created
    assert panel._view.on_nodes_deleted == panel._on_nodes_deleted
    assert panel._view.on_node_data_changed == panel._on_node_data_changed


def test_particle_event_canvas_drop_creates_an_independent_event_flow():
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    panel = ParticleGraphEditorPanel()
    panel._record = lambda *_args: None
    event_type = panel.add_event_type("Pulse", 8, [])
    event_id = event_type["stable_id"]

    panel._on_canvas_drop("PARTICLE_EVENT", event_id, 420.0, 760.0)

    assert [flow.event_id for flow in panel.asset.emitters[0].event_flows] == [
        event_id
    ]
    flow_id = panel.asset.emitters[0].event_flows[0].stable_id
    root = next(
        node
        for node in panel.authoring_snapshot()["nodes"]
        if node["uid"] == f"event.{flow_id}::root.event"
    )
    assert root["position"] == pytest.approx([420.0, 760.0])


def test_particle_event_row_binds_drag_source_before_context_menu(monkeypatch):
    import infernux.engine.ui.particle_graph_editor_panel as module
    import infernux.engine.ui.node_graph_editor_panel as shared_module

    panel, _core, _manager = _particle_panel_with_command_core()
    panel._record = lambda *_args: None
    event_type = panel.add_event_type("Pulse", 8, [])
    calls = []

    monkeypatch.setattr(
        shared_module, "render_workspace_add_header", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        shared_module,
        "begin_workspace_entry",
        lambda *_a, **_k: (False, (0.0, 0.0, 100.0, 28.0)),
    )
    monkeypatch.setattr(
        shared_module, "paint_workspace_entry", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        shared_module, "finish_workspace_entry", lambda *_a, **_k: None
    )

    class Context:
        semantic_capture_enabled = False

        @staticmethod
        def begin_drag_drop_source():
            calls.append("drag")
            return True

        @staticmethod
        def set_drag_drop_payload_str(payload_type, payload):
            calls.append((payload_type, payload))

        @staticmethod
        def label(_value):
            pass

        @staticmethod
        def end_drag_drop_source():
            pass

        @staticmethod
        def begin_popup_context_item(_item_id):
            calls.append("context")
            return False

    panel._render_event_page(Context())

    assert calls == [
        "drag",
        ("PARTICLE_EVENT", event_type["stable_id"]),
        "context",
    ]


def test_particle_graph_editor_event_schema_edit_prunes_only_invalid_payload_links():
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    panel = ParticleGraphEditorPanel()
    panel._record = lambda *_args: None
    event_type = panel.add_event_type(
        "Impact",
        8,
        [{"name": "Weight", "type": TypeRef(ValueType.F32).to_dict(), "default": 1.0}],
    )
    event_id = event_type["stable_id"]
    field_id = event_type["fields"][0]["stable_id"]
    flow = panel.add_authoring_event_flow(event_id)
    event_stage = f"event.{flow['flow_id']}"
    size = panel.add_authoring_node(event_stage, "particle.attribute.size", 360.0, 0.0)
    root_uid = f"{event_stage}::root.event"
    panel.connect_exec(root_uid, size["uid"])
    panel.connect_value(
        root_uid, particle_event_payload_port_id(field_id), size["uid"], "value"
    )

    updated = panel.update_event_type(
        event_id,
        "Impact Renamed",
        16,
        [{
            "stable_id": field_id,
            "name": "Impulse",
            "type": TypeRef(ValueType.F32).to_dict(),
            "default": 2.0,
        }],
    )
    assert updated["stable_id"] == event_id
    assert updated["queue_capacity"] == 16
    assert any(
        link.source_port == particle_event_payload_port_id(field_id)
        for link in panel.asset.emitters[0].event_flows[0].graph.links
    )

    panel.update_event_type(
        event_id,
        "Impact Renamed",
        16,
        [{
            "stable_id": field_id,
            "name": "Impulse",
            "type": TypeRef(ValueType.VEC3).to_dict(),
            "default": [0.0, 0.0, 0.0],
        }],
    )
    links = panel.asset.emitters[0].event_flows[0].graph.links
    assert any(link.kind.value == "exec" for link in links)
    assert any(
        link.source_port == particle_event_payload_port_id(field_id) for link in links
    )


def test_particle_graph_editor_event_schema_edit_migrates_trigger_literals_atomically():
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    panel = ParticleGraphEditorPanel()
    panel._record = lambda *_args: None
    event_type = panel.add_event_type(
        "Impact",
        8,
        [
            {
                "name": "Direction",
                "type": TypeRef(ValueType.VEC3).to_dict(),
                "default": [0.0, 1.0, 0.0],
            }
        ],
    )
    event_id = event_type["stable_id"]
    field_id = event_type["fields"][0]["stable_id"]
    panel.add_authoring_event_flow(event_id)
    trigger = panel.add_authoring_node(
        "update", PARTICLE_EVENT_TRIGGER_TYPE_ID, 260.0, 230.0
    )
    panel.set_node_property(trigger["uid"], "event", event_id)
    payload_port = particle_event_payload_port_id(field_id)
    trigger_node = panel._model.find_node(trigger["uid"])
    assert trigger_node.data[payload_port] == [0.0, 1.0, 0.0]

    panel.update_event_type(
        event_id,
        "Impact",
        8,
        [
            {
                "stable_id": field_id,
                "name": "Weight",
                "type": TypeRef(ValueType.F32).to_dict(),
                "default": 2.5,
            },
            {
                "stable_id": "new-color-field",
                "name": "Tint",
                "type": TypeRef(ValueType.COLOR).to_dict(),
                "default": [1.0, 0.25, 0.0, 1.0],
            },
        ],
    )

    node = next(
        item
        for item in panel.asset.emitters[0].update.nodes
        if item.type_id == PARTICLE_EVENT_TRIGGER_TYPE_ID
        and item.properties.get("event") == event_id
    )
    assert node.properties[payload_port] == 2.5
    assert node.properties[particle_event_payload_port_id("new-color-field")] == [
        1.0,
        0.25,
        0.0,
        1.0,
    ]
    canvas_node = panel._model.find_node(f"update::{node.uid}")
    canvas_type = panel._model.get_node_type(canvas_node)
    inline_types = {field.id: field.data_type for field in canvas_type.inline_fields}
    assert inline_types[payload_port] == "f32"
    assert inline_types[particle_event_payload_port_id("new-color-field")] == "color"


def test_particle_graph_editor_removing_event_clears_reusable_event_nodes():
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    panel = ParticleGraphEditorPanel()
    panel._record = lambda *_args: None
    event_type = panel.add_event_type("Death", 4, [])
    event_id = event_type["stable_id"]
    panel.add_authoring_event_flow(event_id)
    trigger = panel.add_authoring_node(
        "update", PARTICLE_EVENT_TRIGGER_TYPE_ID, 260.0, 230.0
    )
    panel.set_node_property(trigger["uid"], "event", event_id)
    panel.connect_exec("update::root.update", trigger["uid"])

    removed = panel.remove_event_type(event_id)
    assert removed["stable_id"] == event_id
    assert panel.asset.event_types == ()
    assert len(panel.asset.emitters[0].event_flows) == 1
    assert panel.asset.emitters[0].event_flows[0].event_id == ""
    trigger_node = next(
        node
        for node in panel.asset.emitters[0].update.nodes
        if node.type_id == PARTICLE_EVENT_TRIGGER_TYPE_ID
    )
    assert trigger_node.properties["event"] == ""

    replacement = panel.add_event_type("Respawn", 8, [])
    replacement_id = replacement["stable_id"]
    flow = panel.asset.emitters[0].event_flows[0]
    trigger_uid = f"update::{trigger_node.uid}"
    active_uid = f"event.{flow.stable_id}::root.event"
    panel.set_node_property(trigger_uid, "event", replacement_id)
    panel.set_node_property(active_uid, "event", replacement_id)
    assert panel._model.get_node_type(panel._model.find_node(trigger_uid)).label == (
        "Trigger Event: Respawn"
    )
    assert panel._model.get_node_type(panel._model.find_node(active_uid)).label == (
        "Active Event: Respawn"
    )


def test_particle_node_inspector_edits_unconnected_value_input_defaults():
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    class Context:
        semantic_capture_enabled = True

        def __init__(self):
            self.items = []

        def label(self, _label):
            pass

        def separator(self):
            pass

        def drag_float(self, label, value, _speed, _minimum, _maximum):
            return 1.5 if label.startswith("B##") else value

        def record_semantic_item(self, *args, **kwargs):
            self.items.append((args, kwargs))

    panel = ParticleGraphEditorPanel()
    panel._record = lambda *_args: None
    panel._on_node_creation_requested({"source_node": "", "gy": 230.0})
    node = panel._on_node_add("common.compare.greater_than", 400.0, 230.0)
    panel._selected_node_uid = node.uid
    ctx = Context()

    panel._render_node_properties(ctx)

    assert node.data["b"] == 1.5
    semantic_ids = {args[3] for args, _kwargs in ctx.items}
    assert semantic_ids == {
        f"particle_graph.node.{node.uid}.property.a",
        f"particle_graph.node.{node.uid}.property.b",
    }


def test_particle_node_numeric_semantics_are_bound_during_widget_submission():
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    class Context:
        semantic_capture_enabled = True

        def __init__(self):
            self.semantic_id = ""

        @staticmethod
        def label(_label):
            pass

        @staticmethod
        def separator():
            pass

        def input_int_semantic(self, _label, _value, semantic_id):
            self.semantic_id = semantic_id
            return 5

        @staticmethod
        def record_semantic_item(*_args, **_kwargs):
            raise AssertionError("numeric semantic aliases must not be recorded after the widget")

    panel = ParticleGraphEditorPanel()
    panel._record = lambda *_args: None
    panel._on_node_creation_requested({"source_node": "", "gy": 230.0})
    node = panel._on_node_add("particle.control.wait_frames", 400.0, 230.0)
    panel._selected_node_uid = node.uid
    ctx = Context()

    panel._render_node_properties(ctx)

    assert node.data["frames"] == 5
    assert ctx.semantic_id == f"particle_graph.node.{node.uid}.property.frames"


def test_particle_node_color_inputs_use_shared_color_bar(monkeypatch):
    import infernux.engine.ui.particle_graph_editor_panel as particle_editor

    class Context:
        semantic_capture_enabled = False

        @staticmethod
        def label(_label):
            pass

        @staticmethod
        def separator():
            pass

        @staticmethod
        def combo(_label, value, _options, _popup_height):
            return value

        @staticmethod
        def drag_float(*_args, **_kwargs):
            raise AssertionError("Color inputs must not render as XYZW drag fields")

    calls = []

    def render_color(_ctx, widget_id, value, **kwargs):
        calls.append((widget_id, list(value), kwargs))
        return list(value)

    monkeypatch.setattr(particle_editor, "render_color_value_bar", render_color)
    panel = particle_editor.ParticleGraphEditorPanel()
    panel._record = lambda *_args: None
    node = panel._on_node_add("particle.attribute.color", 400.0, 230.0)
    panel._selected_node_uid = node.uid

    panel._render_node_properties(Context())

    assert calls == [
        (
            f"##particle_node_{node.uid}_value",
            [1.0, 1.0, 1.0, 1.0],
            {"allow_hdr": True, "default_hdr_enabled": True},
        )
    ]


def test_color_node_can_drive_sprite_output_and_compile_to_gpu_glsl():
    from dataclasses import replace

    from infernux.engine.ui.graph_document_authoring import (
        ParticleEmitterGraphAuthoringModel,
    )
    from infernux.particle import (
        GpuParticleGlslLowerer,
        ParticleGraphCompiler,
        ParticleKernelLowerer,
    )
    from infernux.particle.asset import ParticleGraphAsset
    from infernux.particle.nodes import particle_graph_node_definitions

    asset = ParticleGraphAsset()
    emitter = asset.emitters[0]
    definitions = particle_graph_node_definitions(asset)
    model = ParticleEmitterGraphAuthoringModel(
        emitter,
        definition_set=definitions,
    )
    model.set_authoring_stage("rendering")
    model.prepare_node_creation("rendering")
    color = model.add_node(
        "common.constant.color",
        180.0,
        model._STAGE_Y["rendering"] + 100.0,
        value=[2.0, 0.5, 0.25, 1.0],
    )
    link = model.add_link(
        color.uid,
        "value",
        "rendering::output.sprite",
        "shader.baseColor",
    )
    assert link is not None

    documents = model.to_documents()
    emitter = replace(emitter, rendering=documents["rendering"])
    asset = replace(asset, emitters=(emitter,))
    compiled = ParticleGraphCompiler().compile(asset)
    output = compiled.emitters[0].render_plan.outputs[0]
    base_color = next(
        item for item in output.shader_properties if item.name == "baseColor"
    )

    assert base_color.default == [2.0, 0.5, 0.25, 1.0]
    assert base_color.parameter_id == ""
    kernel = ParticleKernelLowerer().lower(compiled)
    gpu_program = GpuParticleGlslLowerer().lower(kernel)
    assert gpu_program.emitters


def test_particle_gradient_editor_uses_shared_hdr_color_bar_and_channel_semantics(monkeypatch):
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel
    from infernux.engine.ui import inspector_utils
    from infernux.graph.ramp import Gradient

    color_calls = []

    def render_color_bar(_ctx, widget_id, value, **kwargs):
        color_calls.append((widget_id, list(value), kwargs))
        return list(value)

    monkeypatch.setattr(inspector_utils, "render_color_value_bar", render_color_bar)

    class Context:
        def __init__(self):
            self.items = []

        def get_dpi_scale(self):
            return 1.0

        def combo(self, _label, value, _options, _popup_height):
            return value

        def separator(self):
            pass

        def label(self, _label):
            pass

        def align_text_to_frame_padding(self):
            pass

        def same_line(self, _offset):
            pass

        def set_next_item_width(self, _width):
            pass

        def drag_float(self, _label, value, _speed, _minimum, _maximum):
            return value

        def button(self, _label):
            return False

        def record_semantic_item(self, *args, **kwargs):
            self.items.append((args, kwargs))

    ctx = Context()
    result = ParticleGraphEditorPanel._render_gradient_property(
        ctx,
        "update::sample",
        "gradient",
        Gradient().to_dict(),
    )

    assert result == Gradient().to_dict()
    assert len(color_calls) == 2
    assert all(call[2]["allow_hdr"] is True for call in color_calls)
    assert all(call[2]["default_hdr_enabled"] is True for call in color_calls)
    semantic_ids = {args[3] for args, _kwargs in ctx.items}
    assert "particle_graph.node.update::sample.property.gradient.key.0.time" in semantic_ids
    assert (
        "particle_graph.node.update::sample.property.gradient.key.0.color.r"
        in semantic_ids
    )
    assert (
        "particle_graph.node.update::sample.property.gradient.key.1.color.a"
        in semantic_ids
    )


def test_particle_graph_editor_save_aot_compiles_and_reopens(tmp_path, monkeypatch):
    from infernux.core.assets import AssetManager
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel
    from infernux.particle.artifact import ParticleArtifactRegistry

    ParticleArtifactRegistry.clear()
    monkeypatch.setattr(AssetManager, "reimport_asset", classmethod(lambda cls, _path: None))

    path = tmp_path / "Smoke.particlegraph"
    panel = ParticleGraphEditorPanel()
    assert _save_particle_graph(panel, path) is True
    artifact = ParticleArtifactRegistry.get(str(path))
    assert artifact is not None
    assert artifact.source_kind == "graph"
    assert artifact.hir["name"] == "Smoke"
    assert panel._document_is_dirty() is False

    reopened = ParticleGraphEditorPanel()
    assert reopened.open_document_resource_immediate(str(path)) is True
    assert reopened.asset.name == "Smoke"
    assert reopened._document_is_dirty() is False
    assert reopened.reload_from_disk() is True
    assert reopened._document_is_dirty() is False


def test_particle_graph_close_reopen_preserves_direct_save_target(tmp_path, monkeypatch):
    from infernux.core.assets import AssetManager
    from infernux.engine.interaction import DocumentRegistry
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    DocumentRegistry()
    monkeypatch.setattr(AssetManager, "reimport_asset", classmethod(lambda cls, _path: None))
    path = tmp_path / "New_Particle_Graph.particlegraph"
    panel = ParticleGraphEditorPanel()
    panel.on_enable()
    assert _save_particle_graph(panel, path) is True
    original_document_id = panel.document_id

    panel.unbind_document()
    assert panel.document_id == ""
    panel.open()

    assert panel.document_id == original_document_id
    assert panel._file_path == str(path.resolve())
    panel.add_authoring_parameter("Intensity")
    assert panel._document_is_dirty() is True
    monkeypatch.setattr(
        panel,
        "_show_save_as_dialog",
        lambda: (_ for _ in ()).throw(AssertionError("Save unexpectedly became Save As")),
    )

    assert DocumentRegistry.instance().request_save(panel.document_id).accepted
    _finish_particle_graph_save(panel, path)
    assert panel._file_path == str(path.resolve())
    assert panel._document_is_dirty() is False
    assert DocumentRegistry.instance().require(panel.document_id).is_dirty is False
    panel.on_disable()


def test_particle_graph_deferred_save_stays_clean_after_same_frame_edit(
    tmp_path, monkeypatch
):
    from infernux.core.assets import AssetManager
    from infernux.engine.interaction import DocumentRegistry
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    registry = DocumentRegistry()
    monkeypatch.setattr(AssetManager, "reimport_asset", classmethod(lambda cls, _path: None))
    path = tmp_path / "Deferred.particlegraph"
    panel = ParticleGraphEditorPanel()
    panel.on_enable()
    assert _save_particle_graph(panel, path) is True
    panel.add_authoring_parameter("BeforeSave")

    result = registry.defer_save(panel.document_id)
    panel.add_authoring_parameter("CommittedLaterInFrame")
    assert result.accepted
    registry.process_deferred_saves()
    _finish_particle_graph_save(panel, path)
    document = registry.require(panel.document_id)
    assert document.is_dirty is False
    assert panel._document_is_dirty() is False
    reopened = ParticleGraphAsset.load(str(path))
    assert [parameter.name for parameter in reopened.parameters] == [
        "BeforeSave",
        "CommittedLaterInFrame",
    ]
    panel.on_disable()


def test_particle_graph_pending_save_as_clears_the_serialized_revision(
    tmp_path, monkeypatch
):
    from infernux.core.assets import AssetManager
    from infernux.engine.interaction import DocumentRegistry
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    registry = DocumentRegistry()
    monkeypatch.setattr(AssetManager, "reimport_asset", classmethod(lambda cls, _path: None))
    path = tmp_path / "PendingSaveAs.particlegraph"
    panel = ParticleGraphEditorPanel()
    panel.on_enable()
    document = registry.require(panel.document_id)
    ticket = registry.begin_save(document.document_id, save_as=True)
    panel._pending_save_ticket_id = ticket.ticket_id
    panel.add_authoring_parameter("CommittedWhileDialogWasOpen")

    assert panel._save_to(str(path), ticket_id=ticket.ticket_id) is True
    assert document.resource_path == ""
    _finish_particle_graph_save(panel, path)

    document = registry.require(panel.document_id)
    assert document.resource_path == str(path.resolve())
    assert document.is_dirty is False
    assert panel._document_is_dirty() is False
    assert panel._window_title_suffix() == ""
    panel.on_disable()


def test_particle_graph_async_save_keeps_edits_after_capture_dirty(
    tmp_path, monkeypatch
):
    from infernux.core.assets import AssetManager
    from infernux.engine.interaction import DocumentRegistry
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    registry = DocumentRegistry()
    monkeypatch.setattr(AssetManager, "reimport_asset", classmethod(lambda cls, _path: None))
    path = tmp_path / "SnapshotIsolation.particlegraph"
    panel = ParticleGraphEditorPanel()
    panel.on_enable()
    panel.add_authoring_parameter("Captured")
    document = registry.require(panel.document_id)
    ticket = registry.begin_save(document.document_id, save_as=True)

    assert panel._save_to(str(path), ticket_id=ticket.ticket_id) is True
    panel.add_authoring_parameter("AfterCapture")
    _finish_particle_graph_save(panel, path)

    first_snapshot = ParticleGraphAsset.load(str(path))
    assert [parameter.name for parameter in first_snapshot.parameters] == ["Captured"]
    assert document.is_dirty is True

    assert _save_particle_graph(panel, path) is True
    second_snapshot = ParticleGraphAsset.load(str(path))
    assert [parameter.name for parameter in second_snapshot.parameters] == [
        "Captured",
        "AfterCapture",
    ]
    assert document.is_dirty is False
    panel.on_disable()


def test_particle_graph_save_absorbs_synchronous_reimport_bookkeeping_revision(
    tmp_path, monkeypatch
):
    from infernux.core.assets import AssetManager
    from infernux.engine.interaction import DocumentRegistry
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    registry = DocumentRegistry()
    path = tmp_path / "ReimportBookkeeping.particlegraph"
    panel = ParticleGraphEditorPanel()
    panel.on_enable()
    panel.add_authoring_parameter("Intensity")
    document = registry.require(panel.document_id)

    def reimport_asset(_cls, _path):
        registry.mark_changed(document.document_id)
        return True

    monkeypatch.setattr(AssetManager, "reimport_asset", classmethod(reimport_asset))

    ticket = registry.begin_save(document.document_id, save_as=True)
    assert panel._save_to(str(path), ticket_id=ticket.ticket_id) is True
    _finish_particle_graph_save(panel, path)

    assert document.revision == document.saved_revision
    assert document.is_dirty is False
    assert panel._document_is_dirty() is False
    assert panel._window_title_suffix() == ""
    panel.on_disable()


def test_particle_graph_document_revision_and_selection_are_globally_authoritative(
    tmp_path, monkeypatch
):
    from infernux.core.assets import AssetManager
    from infernux.engine.interaction import (
        DocumentRegistry,
        GraphElementKind,
        SelectionService,
    )
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    DocumentRegistry()
    selection = SelectionService()
    monkeypatch.setattr(AssetManager, "reimport_asset", classmethod(lambda cls, _path: None))
    panel = ParticleGraphEditorPanel()
    panel.on_enable()
    path = tmp_path / "Authority.particlegraph"

    assert _save_particle_graph(panel, path) is True
    document = DocumentRegistry.instance().require(panel.document_id)
    assert document.is_dirty is False

    parameter = panel.add_authoring_parameter("Intensity")

    assert document.is_dirty is True
    assert panel._document_is_dirty() is True
    assert selection.snapshot.primary.document_id == document.document_id
    assert selection.snapshot.primary.sub_kind == GraphElementKind.PARAMETER.value
    assert selection.snapshot.primary.target_id == parameter["stable_id"]

    assert _save_particle_graph(panel, path) is True
    assert document.is_dirty is False
    assert panel._document_is_dirty() is False
    panel.on_disable()


def test_particle_parameter_insert_and_update_use_precise_graph_diffs():
    from infernux.engine.interaction import GraphElementKind

    panel, manager = _particle_panel_with_history()
    document = panel._particle_document()
    initial_revision = document.revision

    created = panel.add_authoring_parameter("Intensity")
    parameter_id = created["stable_id"]
    insert_revision = document.revision

    assert insert_revision > initial_revision
    assert panel._graph_selection.primary_id(GraphElementKind.PARAMETER) == parameter_id

    updated = panel.update_authoring_parameter(
        parameter_id,
        {"name": "Smoke Intensity", "default": 2.0},
    )
    update_revision = document.revision
    assert updated["name"] == "Smoke Intensity"
    assert update_revision > insert_revision

    manager.undo()
    parameter = next(item for item in panel.asset.parameters if item.stable_id == parameter_id)
    assert parameter.name == "Intensity"
    assert document.revision == insert_revision

    manager.undo()
    assert all(item.stable_id != parameter_id for item in panel.asset.parameters)
    assert document.revision == initial_revision

    manager.redo()
    manager.redo()
    parameter = next(item for item in panel.asset.parameters if item.stable_id == parameter_id)
    assert parameter.name == "Smoke Intensity"
    assert document.revision == update_revision


def test_particle_structural_parameter_edit_restores_affected_graph_in_one_undo():
    panel, manager = _particle_panel_with_history()
    parameter = panel.add_authoring_parameter("Scale", "f32", 1.0)
    source = panel.add_authoring_parameter_node(
        parameter["stable_id"], 220.0, 40.0, stage="init"
    )
    target = panel.add_authoring_node(
        "init", "particle.attribute.size", 480.0, 40.0
    )
    link = panel.connect_value(source["uid"], "value", target["uid"], "value")
    manager.clear()

    panel.update_authoring_parameter(
        parameter["stable_id"],
        {"type": TypeRef(ValueType.VEC3).to_dict()},
    )

    assert panel._model.find_link(link["link_uid"]) is not None
    manager.undo()
    restored = next(
        item
        for item in panel.asset.parameters
        if item.stable_id == parameter["stable_id"]
    )
    assert restored.value_type == TypeRef(ValueType.F32)
    assert panel._model.find_link(link["link_uid"]) is not None
    manager.redo()
    assert panel._model.find_link(link["link_uid"]) is not None


def test_particle_emitter_settings_use_one_precise_undo_action():
    panel, manager = _particle_panel_with_history()
    emitter = panel.asset.emitters[0]
    settings = emitter.settings.to_dict()
    settings["spawn_rate"] = 73.0
    manager.clear()

    panel.set_authoring_emitter_settings(emitter.stable_id, settings)

    assert panel.asset.emitters[0].settings.spawn_rate == 73.0
    manager.undo()
    assert panel.asset.emitters[0].settings.spawn_rate == emitter.settings.spawn_rate
    manager.redo()
    assert panel.asset.emitters[0].settings.spawn_rate == 73.0


def test_particle_data_interface_edit_uses_one_precise_undo_action():
    from infernux.particle.data_interface import VectorField

    panel, manager = _particle_panel_with_history()
    emitter = panel.asset.emitters[0]
    interface = VectorField(stable_id="wind-field", name="Wind Field")
    panel._replace_emitter(
        replace(emitter, data_interfaces=(interface,))
    )
    panel._bind_stage()
    manager.clear()

    updated = panel.patch_authoring_data_interface(
        emitter.stable_id,
        interface.stable_id,
        {"vector_scale": 2.5},
    )

    assert updated["vector_scale"] == 2.5
    assert manager.undo_description == "Edit Particle Graph Data Interface"
    manager.undo()
    assert panel.asset.emitters[0].data_interfaces[0].vector_scale == 1.0
    manager.redo()
    assert panel.asset.emitters[0].data_interfaces[0].vector_scale == 2.5


def test_particle_event_type_and_flow_use_precise_domain_diffs():
    panel, manager = _particle_panel_with_history()

    event_type = panel.add_event_type("Impact", 8, [])
    event_id = event_type["stable_id"]
    manager.undo()
    assert panel.asset.event_types == ()


    manager.redo()
    assert panel.asset.event_types[0].stable_id == event_id

    manager.clear()
    flow = panel.add_authoring_event_flow(event_id, 420.0, 760.0)
    flow_id = flow["flow_id"]
    assert panel.asset.emitters[0].event_flows[0].stable_id == flow_id
    manager.undo()
    assert panel.asset.emitters[0].event_flows == ()
    manager.redo()
    assert panel.asset.emitters[0].event_flows[0].stable_id == flow_id
    assert panel._model.find_node(f"event.{flow_id}::root.event") is not None

    manager.clear()
    panel.remove_event_type(event_id)
    assert panel.asset.event_types == ()
    assert panel.asset.emitters[0].event_flows[0].event_id == ""
    manager.undo()
    assert panel.asset.event_types[0].stable_id == event_id
    assert panel.asset.emitters[0].event_flows[0].event_id == event_id
    manager.redo()
    assert panel.asset.event_types == ()
    assert panel.asset.emitters[0].event_flows[0].event_id == ""


def test_particle_graph_disk_discard_reconciles_document_revision(tmp_path):
    from infernux.engine.interaction import DocumentActionStatus, DocumentRegistry
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel
    from infernux.particle import ParticleGraphAsset

    target = tmp_path / "Saved.particlegraph"
    ParticleGraphAsset(stable_id="saved-graph", name="Saved").save(str(target))
    panel = ParticleGraphEditorPanel()
    assert panel.open_document_resource_immediate(str(target))
    registry = DocumentRegistry.instance()
    document = registry.require(panel.document_id)
    registry.mark_changed(document.document_id)

    result = registry.request_discard(document.document_id)

    assert result.status is DocumentActionStatus.APPLIED
    assert registry.require(panel.document_id).is_dirty is False
    assert panel._document_is_dirty() is False


def test_particle_node_drag_records_only_stable_node_positions():
    panel, manager = _particle_panel_with_history()
    node_data = panel.add_authoring_node(
        "update",
        "common.noise.vector3d",
        240.0,
        460.0,
    )
    node_uid = node_data["uid"]
    node = panel._model.find_node(node_uid)
    before = (node.pos_x, node.pos_y)
    manager.clear()

    panel._view.project_selection((node_uid,), "")
    panel._on_node_drag_start(node_uid)
    node.pos_x += 37.0
    node.pos_y -= 19.0
    after = (node.pos_x, node.pos_y)
    panel._on_node_drag_end(node_uid)

    assert (panel._model.find_node(node_uid).pos_x, panel._model.find_node(node_uid).pos_y) == after
    manager.undo()
    assert (
        panel._model.find_node(node_uid).pos_x,
        panel._model.find_node(node_uid).pos_y,
    ) == before
    manager.redo()
    assert (
        panel._model.find_node(node_uid).pos_x,
        panel._model.find_node(node_uid).pos_y,
    ) == after


def test_connect_vector3_to_set_velocity_never_moves_nodes_through_panel_history():
    """The real panel transaction must preserve layout across connect/undo/redo."""
    panel, manager = _particle_panel_with_history()
    model = panel._model
    model.prepare_node_creation("init")
    vector = model.add_node("common.vector.compose3", 619.0, 287.0)
    velocity = model.find_node("init::init.velocity")
    assert velocity is not None

    def positions_by_raw_id():
        return {
            model._document_uid(node.uid): (node.pos_x, node.pos_y)
            for node in model.nodes
        }

    expected = positions_by_raw_id()
    manager.clear()
    panel._on_link_created(vector.uid, "value", velocity.uid, "value")

    assert positions_by_raw_id() == expected
    assert any(
        link.source_node == vector.uid
        and link.source_pin == "value"
        and link.target_node == velocity.uid
        and link.target_pin == "value"
        for link in model.links
    )
    manager.undo()
    assert positions_by_raw_id() == expected
    manager.redo()
    assert positions_by_raw_id() == expected


def test_particle_node_drag_keeps_exact_cross_panel_history_order():
    """A graph drag must remain between the two Scene editing sessions."""
    from infernux.engine._bootstrap_selection import BootstrapSelectionMixin
    from infernux.engine.interaction import (
        ContextRestoreStatus,
        EditorInteractionCore,
        PanelInteractionDescriptor,
        SelectionDomain,
        SelectionTarget,
    )
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel
    from infernux.engine.undo import SetPropertyCommand, UndoManager

    core = EditorInteractionCore()
    manager = UndoManager(core.action_journal)
    manager.set_context_hooks(
        core.capture_context,
        lambda context, _phase: (
            core.focus.apply_snapshot(context.focus, record_history=False),
            core.selection.apply_snapshot(
                context.selection,
                reason="cross_panel_test_restore",
                record_history=False,
            ),
            ContextRestoreStatus.READY,
        )[-1],
    )
    bootstrap = BootstrapSelectionMixin()
    bootstrap.interaction_core = core
    bootstrap._present_selection_snapshot = lambda _snapshot: None
    core.focus.add_change_listener(bootstrap._on_global_focus_changed)
    core.selection.add_listener(bootstrap._on_global_selection_changed)

    panel = ParticleGraphEditorPanel()
    core.panels.register_type(
        panel.panel_type_id,
        panel.PANEL_INTERACTION,
    )
    core.panels.bind_view(panel.window_id, panel.panel_type_id, panel)
    core.panels.register_type(
        "scene_view",
        PanelInteractionDescriptor(
            owned_selection_domains=frozenset({SelectionDomain.SCENE_OBJECT}),
        ),
    )
    panel._graph_selection.bind(core.selection)
    node_data = panel.add_authoring_node(
        "update",
        "common.noise.vector3d",
        240.0,
        460.0,
    )
    node_uid = node_data["uid"]
    manager.clear()
    camera = SimpleNamespace(position=0.0)

    core.focus.activate_panel("scene_view", view_id="scene_view")
    core.selection.select(
        SelectionTarget.scene_object(5),
        owner_id="scene_view",
    )
    manager.execute(SetPropertyCommand(camera, "position", 0.0, 1.0, "Move camera"))

    core.focus.activate_panel(
        panel.panel_type_id,
        view_id=panel.window_id,
        document_id=panel.document_id,
        child_context_id=panel.current_child_context_id(),
    )
    panel._select_canvas_node(node_uid, record_history=True)
    panel._on_node_drag_start(node_uid)
    node = panel._model.find_node(node_uid)
    node.pos_x += 37.0
    panel._on_node_drag_end(node_uid)

    core.focus.activate_panel(
        "scene_view",
        view_id="scene_view",
        document_id="",
        child_context_id="",
    )
    core.selection.select(
        SelectionTarget.scene_object(5),
        owner_id="scene_view",
    )
    manager.execute(SetPropertyCommand(camera, "position", 1.0, 2.0, "Move camera"))
    core.selection.clear(reason="scene_pick_clear")

    assert [entry.action.description for entry in manager.action_journal.entries] == [
        "Focus scene_view",
        "Change Selection",
        "Move camera",
        f"Focus {panel.panel_type_id}",
        "Change Selection",
        "Move Particle Graph node",
        "Focus scene_view",
        "Change Selection",
        "Move camera",
        "Change Selection",
    ]

    for _ in range(4):
        manager.undo()
    assert manager.undo_description == "Move Particle Graph node"
    manager.undo()
    assert panel._model.find_node(node_uid).pos_x == 240.0


def test_particle_node_and_link_deletion_is_one_precise_undo_action():
    panel, manager = _particle_panel_with_history()
    panel._stage = "update"
    panel._model.set_authoring_stage("update")
    panel._model.prepare_node_creation("update")
    node = panel._on_node_add("particle.attribute.velocity", 260.0, 230.0)
    assert node is not None
    panel._on_link_created("update::root.update", "out", node.uid, "in")
    link = next(
        item
        for item in panel._model.links
        if item.target_node == node.uid and item.target_pin == "in"
    )
    node_uid = node.uid
    link_uid = link.uid
    manager.clear()

    panel._on_nodes_deleted((node_uid,))

    assert panel._model.find_node(node_uid) is None
    assert panel._model.find_link(link_uid) is None

    manager.undo()
    assert panel._model.find_node(node_uid) is not None
    assert panel._model.find_link(link_uid) is not None

    manager.redo()
    assert panel._model.find_node(node_uid) is None
    assert panel._model.find_link(link_uid) is None


def test_particle_graph_uses_shared_typed_subgraph_clipboard_and_undo():
    from infernux.engine.interaction import (
        ClipboardDomain,
        ClipboardService,
        GraphElementKind,
    )

    panel, manager = _particle_panel_with_history()
    panel._model.set_authoring_stage("update")
    panel._model.prepare_node_creation("update")
    first = panel._on_node_add("particle.control.wait_frames", 260.0, 230.0)
    panel._model.prepare_node_creation("update")
    second = panel._on_node_add("particle.control.wait_seconds", 520.0, 230.0)
    assert first is not None and second is not None
    panel._on_link_created(first.uid, "out", second.uid, "in")
    assert any(
        link.source_node == first.uid and link.target_node == second.uid
        for link in panel._model.links
    )
    panel._graph_selection.select(
        (
            panel._graph_element_from_view(GraphElementKind.NODE, first.uid),
            panel._graph_element_from_view(GraphElementKind.NODE, second.uid),
        ),
        record_history=False,
    )
    manager.clear()

    panel._on_graph_copy()
    payload = ClipboardService.instance().peek(ClipboardDomain.GRAPH_ELEMENT)
    assert payload is not None
    assert payload.items[0].sub_kind == "node_graph_subgraph"
    assert len(payload.items[0].data.state.nodes) == 2
    assert len(payload.items[0].data.state.links) == 1

    panel._on_graph_paste()
    pasted = panel._graph_selection.selected_ids(GraphElementKind.NODE)
    assert len(pasted) == 2
    assert all(stable_id not in {first.uid, second.uid} for stable_id in pasted)
    assert manager.undo_description == "Paste graph nodes"
    copied_count = len(panel._model.nodes)

    manager.undo()
    assert len(panel._model.nodes) == copied_count - 2
    manager.redo()
    assert len(panel._model.nodes) == copied_count


def test_particle_graph_shared_commands_duplicate_and_cut_atomically():
    from infernux.engine.interaction import GraphElementKind

    panel, manager = _particle_panel_with_history()
    panel._model.set_authoring_stage("update")
    panel._model.prepare_node_creation("update")
    first = panel._on_node_add("particle.control.wait_frames", 260.0, 230.0)
    panel._model.prepare_node_creation("update")
    second = panel._on_node_add("particle.control.wait_seconds", 520.0, 230.0)
    assert first is not None and second is not None
    panel._on_link_created(first.uid, "out", second.uid, "in")
    panel._graph_selection.select(
        (
            panel._graph_element_from_view(GraphElementKind.NODE, first.uid),
            panel._graph_element_from_view(GraphElementKind.NODE, second.uid),
        ),
        record_history=False,
    )
    manager.clear()

    assert panel.command_edit_duplicate()
    duplicated_count = len(panel._model.nodes)
    assert manager.undo_description == "Duplicate graph nodes"
    manager.undo()
    assert len(panel._model.nodes) == duplicated_count - 2

    panel._graph_selection.select(
        (
            panel._graph_element_from_view(GraphElementKind.NODE, first.uid),
            panel._graph_element_from_view(GraphElementKind.NODE, second.uid),
        ),
        record_history=False,
    )
    manager.clear()
    assert panel.command_edit_cut()
    assert panel._model.find_node(first.uid) is None
    assert panel._model.find_node(second.uid) is None
    assert manager.undo_description == "Delete Particle Graph nodes"
    manager.undo()
    assert panel._model.find_node(first.uid) is not None
    assert panel._model.find_node(second.uid) is not None


def test_project_create_particlegraph_writes_loadable_asset(tmp_path, monkeypatch):
    from infernux.engine.ui.project_file_ops import create_particlegraph
    from infernux.particle.artifact import ParticleArtifactRegistry

    ParticleArtifactRegistry.clear()

    ok, error = create_particlegraph(str(tmp_path), "Fire")

    assert ok is True, error
    path = tmp_path / "Fire.particlegraph"
    graph = ParticleGraphAsset.load(str(path))
    assert graph.name == "Fire"
    assert len(graph.emitters) == 1
    emitter = graph.emitters[0]
    assert [node.type_id for node in emitter.init.nodes[1:]] == [
        "particle.attribute.lifetime",
        "particle.attribute.velocity",
    ]
    assert [node.type_id for node in emitter.update.nodes[1:]] == [
        "particle.attribute.velocity"
    ]
    assert emitter.update.nodes[1].properties["composition"] == "add"
    artifact = ParticleArtifactRegistry.get(str(path))
    assert artifact is not None
    assert artifact.source_kind == "graph"
    assert artifact.hir["name"] == "Fire"
    assert json.loads(path.read_text(encoding="utf-8"))["$schema"] == "infernux.particle_graph"


def test_particle_graph_document_state_does_not_serialize_stale_model(monkeypatch):
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    panel = ParticleGraphEditorPanel()
    panel._file_path = "Assets/VFX/Legacy.particlegraph"
    monkeypatch.setattr(
        panel,
        "_sync_model_to_asset",
        lambda: (_ for _ in ()).throw(AssertionError("must not serialize the graph")),
    )

    assert panel.authoring_document_state() == {
        "file_path": "Assets/VFX/Legacy.particlegraph",
        "dirty": False,
    }


def test_particle_system_inspector_metadata_is_localizable_and_backend_is_emitter_owned():
    from infernux.components.particle_system import ParticleSystem
    from infernux.components.fields import get_serialized_fields

    fields = get_serialized_fields(ParticleSystem)
    assert {
        name for name, metadata in fields.items() if not metadata.hidden
    } == {
        "graph",
        "simulation_speed",
        "play_on_awake",
        "random_seed",
        "prewarm",
        "offscreen_policy",
        "bounds_mode",
        "manual_bounds_center",
        "manual_bounds_size",
    }
    assert {
        name for name, metadata in fields.items() if metadata.hidden
    } == {
        "_parameter_overrides_json",
        "_emitter_overrides_json",
    }
    assert fields["graph"].display_name_key == "particle_system.graph"
    assert fields["simulation_speed"].display_name_key == "particle_system.simulation_speed"
    assert fields["play_on_awake"].display_name_key == "particle_system.play_on_awake"
    assert fields["random_seed"].display_name_key == "particle_system.random_seed"
    assert fields["prewarm"].display_name_key == "particle_system.prewarm"
    assert (
        fields["offscreen_policy"].display_name_key
        == "particle_system.offscreen_policy"
    )
    assert fields["bounds_mode"].display_name_key == "particle_system.bounds_mode"
    assert (
        fields["manual_bounds_center"].display_name_key
        == "particle_system.manual_bounds_center"
    )
    assert (
        fields["manual_bounds_size"].display_name_key
        == "particle_system.manual_bounds_size"
    )


def test_particle_graph_workspace_child_context_is_stable_and_restorable():
    from infernux.engine.ui.particle_graph_editor_panel import ParticleGraphEditorPanel

    panel = ParticleGraphEditorPanel()
    panel._workspace_tab_index = 1
    assert panel.current_child_context_id() == "particle_graph.workspace.parameters"

    assert panel.restore_child_context("particle_graph.workspace.events")
    assert panel._workspace_tab_index == 2
    assert not panel.restore_child_context("particle_graph.workspace.unknown")
