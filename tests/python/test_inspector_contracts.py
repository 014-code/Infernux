"""Stable semantic contracts for scene and asset Inspector surfaces."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from infernux.core.asset_types import TextureImportSettings
from infernux.engine.ui import inspector_utils


def test_generic_native_inspector_uses_declared_schema(scene):
    from infernux.engine.ui import inspector_components
    from infernux.components.fields import FieldType

    owner = scene.create_game_object("DeclaredInspectorCamera")
    camera = owner.add_component("Camera")

    fields = inspector_components._declared_native_fields(camera)
    by_name = {name: (schema, metadata, value) for name, schema, metadata, value in fields}

    # Sensor Type is a derived Inspector preset and therefore is deliberately
    # absent from the serialized-document surface returned by this helper.
    assert len(by_name) == 26
    assert "sensorType" not in by_name
    assert by_name["targetTextureGuid"][1].asset_type == "RenderTexture"
    assert by_name["targetTextureGuid"][2] is None
    assert by_name["fov"][0].attributes["range"] == (1.0, 179.0)
    assert by_name["fov"][1].field_type is FieldType.FLOAT
    assert by_name["projectionMode"][1].field_type is FieldType.ENUM


class _FakeSemanticContext:
    def __init__(self):
        self.semantic_items = []

    def record_semantic_item(self, kind, label, enabled, semantic_id):
        self.semantic_items.append((kind, label, enabled, semantic_id))


class _FakeTextContext(_FakeSemanticContext):
    def input_text_multiline(self, _widget_id, value, *_args):
        return value

    def drag_float(self, _widget_id, value, *_args):
        return value


class _FakeThemeContext(_FakeSemanticContext):
    def get_content_region_avail_width(self):
        return 240.0

    def push_style_color(self, *_args):
        pass

    def pop_style_color(self, *_args):
        pass

    def button(self, _label, _callback, **_kwargs):
        return False

    def same_line(self, *_args):
        pass


class _FakeVectorContext(_FakeSemanticContext):
    def __init__(self):
        super().__init__()
        self.vector_semantics = []

    def vector2(self, _label, x, y, *_args, semantic_id=""):
        self.vector_semantics.append(semantic_id)
        return x, y


class _StableFieldIdContext:
    def __init__(self):
        self.id_stack = []
        self.vector_calls = []

    def push_id_str(self, value):
        self.id_stack.append(value)

    def pop_id(self):
        self.id_stack.pop()

    def vector2(self, label, x, y, *_args):
        self.vector_calls.append((tuple(self.id_stack), label))
        return x, y


def test_inline_material_binding_and_preview_follow_live_document_generation(monkeypatch):
    import infernux.engine.interaction as interaction
    import infernux.engine.ui.inspector_material as module
    from infernux.engine.ui import asset_resource_preview

    bindings, serializations, queries = [], [], []

    class NativeMaterial:
        file_path = "C:/project/Assets/Test.mat"
        guid = "test-material-guid"
        is_builtin = False
        version = 4

        def get_version(self):
            return self.version

        def serialize_document(self):
            serializations.append(self.version)
            return {"properties": {}, "shaders": {}}

    controller = SimpleNamespace(document_id="material-document")
    monkeypatch.setattr(
        interaction, "ensure_editable_resource_document", lambda **kwargs: bindings.append(kwargs) or controller
    )
    panel, native = SimpleNamespace(), NativeMaterial()
    first = module._build_inline_state(panel, native)
    serialize_count = len(serializations)
    second = module._build_inline_state(panel, native)
    assert first is second and first.exec_layer is second.exec_layer
    assert serialize_count > 0 and len(serializations) == serialize_count
    assert len(bindings) == 1

    native.version = 5
    current = module._build_inline_state(panel, native)
    assert current is not first
    assert len(serializations) > serialize_count
    current.extra["cached_json"] = '{"color":"B"}'
    responses = iter(((41, "material-key"), (42, "material-key")))
    readiness = iter((False, True))

    def query(*args):
        queries.append(args)
        return next(responses)

    preview = SimpleNamespace(
        get_material_preview_texture_id=lambda key: 42 if key == "material-key" else 0,
        request_full_speed_frame=lambda: None,
    )
    monkeypatch.setattr(asset_resource_preview, "_resolve_native_engine", lambda _panel: preview)
    monkeypatch.setattr(module, "_query_material_preview_tex", query)
    monkeypatch.setattr(module, "_is_material_preview_ready", lambda *_args: next(readiness))
    args = (panel, native, {}, current, '{"color":"A"}', native.file_path)
    assert module._get_cached_material_preview_tex(*args) == 41
    assert module._get_cached_material_preview_tex(*args) == 42
    assert module._get_cached_material_preview_tex(*args) == 42
    assert len(queries) == 2
    assert queries[0][4] == '{"color":"B"}'
    assert current.extra["_material_cache_tag"] == '{"color":"B"}'


class _FakeObjectFieldContext(_FakeSemanticContext):
    def __init__(self, interaction=2):
        super().__init__()
        self.opened_popups = []
        self.interaction = int(interaction)

    def push_id_str(self, _value):
        pass

    def pop_id(self):
        pass

    def get_content_region_avail_width(self):
        return 240.0

    def push_style_var_vec2(self, *_args):
        pass

    def push_style_var_float(self, *_args):
        pass

    def pop_style_var(self, *_args):
        pass

    def push_style_color(self, *_args):
        pass

    def pop_style_color(self, *_args):
        pass

    def begin_group(self):
        pass

    def end_group(self):
        pass

    def set_next_item_allow_overlap(self):
        pass

    def selectable(self, *_args):
        return True

    def same_line(self, *_args):
        pass

    def get_cursor_pos_x(self):
        return 0.0

    def set_cursor_pos_x(self, _value):
        pass

    def open_popup(self, popup_id):
        self.opened_popups.append(popup_id)

    def render_object_field_chrome(
        self,
        _field_id,
        display_text,
        _type_hint,
        _selected,
        clickable,
        has_picker,
        _picker_texture_id,
        semantic_id,
        _fixed_width,
    ):
        if semantic_id:
            self.record_semantic_item("object_field", display_text, clickable, semantic_id)
        return self.interaction


def _text_component():
    return SimpleNamespace(
        game_object=SimpleNamespace(id=55),
        component_id=186,
        text="Race complete",
        font_size=24.0,
        line_height=1.2,
        letter_spacing=0.0,
    )


def test_scalar_batch_descriptor_preserves_full_int32_range_as_integers():
    from infernux.components.fields import FieldType
    from infernux.engine.ui.inspector_utils import build_scalar_desc

    metadata = SimpleNamespace(
        field_type=FieldType.INT,
        range=(0, 2147483647),
        drag_speed=None,
        slider=True,
        multiline=False,
        tooltip="",
    )

    desc = build_scalar_desc("##seed", "Random Seed", metadata, 0)

    assert desc is not None
    assert type(desc["mn"]) is int
    assert type(desc["mx"]) is int
    assert desc["mn"] == 0
    assert desc["mx"] == 2147483647


def test_ui_size_edit_keeps_position_set_after_rect_was_cached(scene):
    from infernux.engine.interaction import EditorInteractionCore
    from infernux.engine.ui.inspector_ui_components import (
        _apply_size_preserve_top_left,
        _apply_visual_position,
    )
    from infernux.ui import UIButton, UICanvas
    from infernux.ui.inx_ui_screen_component import clear_rect_cache
    from infernux.engine.undo import UndoManager

    previous_manager = UndoManager._instance
    manager = UndoManager()
    core = EditorInteractionCore()
    canvas_object = scene.create_game_object("Resize cache Canvas")
    canvas = canvas_object.add_py_component(UICanvas())
    button_object = scene.create_game_object("Resize cache Button")
    button_object.set_parent(canvas_object)
    button = button_object.add_py_component(UIButton())
    clear_rect_cache(1)

    try:
        initial_rect = button.get_visual_rect(1920, 1080)
        assert initial_rect[:2] == (880.0, 520.0)
        _apply_visual_position(button, 820.0, 620.0, canvas)
        assert len(manager.action_journal.applied_entries()) == 1
        _apply_size_preserve_top_left(button, 280.0, 72.0, canvas)

        assert button.get_visual_rect(1920, 1080) == (820.0, 620.0, 280.0, 72.0)
        position = button_object.transform.local_position
        assert (position.x, position.y) == (0.0, -116.0)
        assert len(manager.action_journal.applied_entries()) == 2

        manager.undo()
        assert button.get_visual_rect(1920, 1080) == (
            820.0,
            620.0,
            initial_rect[2],
            initial_rect[3],
        )
        manager.undo()
        assert button.get_visual_rect(1920, 1080) == initial_rect
    finally:
        core.shutdown()
        manager.clear()
        UndoManager._instance = previous_manager


@pytest.mark.parametrize("rotation,world", [(37.0, False), (90.0, True)])
def test_ui_resize_undo_restores_geometry_and_transform_together(scene, rotation, world):
    from infernux.engine.interaction import EditorInteractionCore
    from infernux.engine.ui.inspector_ui_components import _apply_size_preserve_top_left
    from infernux.engine.undo import UndoManager
    from infernux.lib import Vector3
    from infernux.ui import UIButton, UICanvas

    canvas = None
    owner = scene.create_game_object("Resize target")
    if not world:
        root = scene.create_game_object("Resize Canvas")
        canvas = root.add_py_component(UICanvas())
        owner.set_parent(root)
    button = owner.add_py_component(UIButton())
    owner.transform.local_position = Vector3(30.0, -50.0, 7.0)
    owner.transform.local_euler_angles = Vector3(0.0, 0.0, rotation)

    def position():
        value = owner.transform.local_position
        return value.x, value.y, value.z

    old_position = position()
    old_size = (button.width, button.height)
    old_corners = button.get_rotated_corners(1920.0, 1080.0)
    previous_manager = UndoManager._instance
    manager = UndoManager()
    core = EditorInteractionCore()
    try:
        _apply_size_preserve_top_left(button, 280.0, 72.0, canvas)
        new_position = position()
        assert (button.width, button.height) == (280.0, 72.0)
        assert len(manager.action_journal.applied_entries()) == 1
        if world:
            assert new_position == old_position
        else:
            assert button.get_rotated_corners(1920.0, 1080.0)[0] == pytest.approx(old_corners[0], abs=1e-4)
        assert new_position[2] == 7.0
        manager.undo()
        assert position() == old_position
        assert (button.width, button.height) == old_size
        for actual, expected in zip(button.get_rotated_corners(1920.0, 1080.0), old_corners):
            assert actual == pytest.approx(expected)
        manager.redo()
        assert position() == new_position
        assert (button.width, button.height) == (280.0, 72.0)
    finally:
        core.shutdown()
        manager.clear()
        UndoManager._instance = previous_manager


def test_ui_position_and_quarter_turn_edit_the_native_transform(scene):
    from infernux.components.value_codec import VALUE_CODECS
    from infernux.engine.interaction import EditorInteractionCore
    from infernux.engine.ui.inspector_ui_components import (
        _apply_visual_position,
        _rotate_component_90,
    )
    from infernux.engine.undo import UndoManager
    from infernux.ui import UIButton, UICanvas

    canvas_object = scene.create_game_object("TransformUICanvas")
    canvas = canvas_object.add_py_component(UICanvas())
    button_object = scene.create_game_object("TransformUIButton")
    button_object.set_parent(canvas_object, False)
    button = button_object.add_py_component(UIButton())
    canvas.reference_width = 1920.0
    canvas.reference_height = 1080.0

    previous_manager = UndoManager._instance
    manager = UndoManager()
    core = EditorInteractionCore()
    initial_position = VALUE_CODECS.encode(button_object.transform.local_position)
    try:
        _apply_visual_position(button, 820.0, 620.0, canvas)
        assert button.get_visual_rect(1920.0, 1080.0)[:2] == pytest.approx((820.0, 620.0))
        assert len(manager.action_journal.applied_entries()) == 1

        _rotate_component_90(button)
        assert VALUE_CODECS.encode(button_object.transform.local_euler_angles)[2] == pytest.approx(90.0)
        assert len(manager.action_journal.applied_entries()) == 2

        manager.undo()
        assert VALUE_CODECS.encode(button_object.transform.local_euler_angles)[2] == pytest.approx(0.0)
        manager.undo()
        assert VALUE_CODECS.encode(button_object.transform.local_position) == pytest.approx(initial_position)
    finally:
        core.shutdown()
        manager.clear()
        UndoManager._instance = previous_manager


def test_object_picker_executes_mutation_after_imgui_scopes_close(monkeypatch):
    from infernux.engine.ui.igui import IGUI

    events = []

    class _PickerContext(_FakeObjectFieldContext):
        def __init__(self):
            super().__init__(interaction=0)

        def push_id_str(self, value):
            events.append(("push", value))

        def pop_id(self):
            events.append(("pop", None))

        def begin_popup(self, _popup_id):
            return True

        def end_popup(self):
            events.append(("end_popup", None))

        def input_text_with_hint(self, *_args):
            return ""

        def separator(self):
            pass

        def begin_child(self, *_args):
            return True

        def end_child(self):
            events.append(("end_child", None))

        def selectable_list_clipped(self, labels):
            assert len(labels) == 1
            assert labels[0] == "Smoke.png"
            return 0

        def close_current_popup(self):
            events.append(("close_popup", None))

    monkeypatch.setattr(IGUI, "_draw_item_outline", lambda *_args, **_kwargs: None)
    IGUI.object_field(
        _PickerContext(),
        "texture",
        "Smoke.png",
        "Texture",
        picker_asset_items=lambda _filter: [("Smoke.png", "Assets/Smoke.png")],
        on_pick=lambda value: events.append(("callback", value)),
    )

    assert events[-3:] == [
        ("end_popup", None),
        ("pop", None),
        ("callback", "Assets/Smoke.png"),
    ]


def test_inspector_property_edit_fails_closed_without_transaction_authority():
    from infernux.engine.interaction import EditorInteractionCore
    from infernux.engine.ui._inspector_undo import _record_property
    from infernux.engine.undo import UndoManager

    previous = UndoManager._instance
    core = EditorInteractionCore()
    UndoManager._instance = None
    target = SimpleNamespace(speed=1.0)
    try:
        with pytest.raises(RuntimeError, match="active UndoManager"):
            _record_property(target, "speed", 1.0, 2.0, "Set Speed")
        assert target.speed == 1.0
    finally:
        core.shutdown()
        UndoManager._instance = previous


def test_multi_builtin_inspector_edit_is_one_global_action():
    from infernux.components.fields import FieldType
    from infernux.engine.ui.inspector_components import _apply_multi_builtin_change
    from infernux.engine.undo import UndoManager

    first = SimpleNamespace(value=1.0)
    second = SimpleNamespace(value=2.0)
    metadata = SimpleNamespace(field_type=FieldType.FLOAT, readonly=False)
    previous = UndoManager._instance
    manager = UndoManager()
    try:
        _apply_multi_builtin_change((first, second), ("value", "value"), metadata, 5.0)

        assert (first.value, second.value) == (5.0, 5.0)
        assert len(manager.action_journal.applied_entries()) == 1
        manager.undo()
        assert (first.value, second.value) == (1.0, 2.0)
    finally:
        UndoManager._instance = previous


def test_builtin_asset_reference_field_records_native_property(monkeypatch, tmp_path):
    import infernux.engine.ui._inspector_references as module
    from infernux.components.fields import FieldType
    from infernux.core.asset_ref import PhysicMaterialRef
    from infernux.core.assets import AssetManager
    from infernux.engine import project_context

    project_root = tmp_path / "project"
    material_path = str(project_root / "Assets" / "Bouncy.physicMaterial")
    monkeypatch.setattr(project_context._context(), "root", str(project_root))

    class _AssetDatabase:
        @staticmethod
        def get_guid_from_path(path):
            return "bouncy-guid" if Path(path) == Path(material_path) else ""

        @staticmethod
        def get_path_from_guid(guid):
            return material_path if guid == "bouncy-guid" else ""

    monkeypatch.setattr(AssetManager, "_asset_database", _AssetDatabase())

    captured = {}
    monkeypatch.setattr(module, "field_label", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        module,
        "render_asset_reference_field",
        lambda *_args, **kwargs: captured.update(kwargs),
    )
    component = SimpleNamespace(
        game_object=SimpleNamespace(id=21),
        component_id=34,
        physic_material=PhysicMaterialRef(),
    )
    # A scene-addressed edit must resolve the captured ID through its owner.
    # This fixture previously depended on editing an unresolvable stale ref.
    from infernux.engine.undo import _helpers

    owner = component.game_object
    owner.get_components = lambda: [component]
    owner.get_py_components = lambda: []
    monkeypatch.setattr(_helpers, "_find_runtime_object", lambda identity: owner if identity == 21 else None)
    metadata = SimpleNamespace(asset_type="PhysicMaterial")

    from infernux.engine.undo import UndoManager

    previous = UndoManager._instance
    manager = UndoManager()
    try:
        module._render_asset_reference_field(
            SimpleNamespace(),
            component,
            "physic_material",
            metadata,
            component.physic_material,
            FieldType.ASSET,
            120.0,
            builtin_attr="physic_material",
        )
        transaction = captured["transaction"]
        errors = []
        transaction.on_rejected = errors.append
        assert transaction.commit(material_path).value == "applied", errors

        assert isinstance(component.physic_material, PhysicMaterialRef)
        assert component.physic_material.guid == "bouncy-guid"
        assert component.physic_material.path_hint.endswith("Bouncy.physicMaterial")
        assert len(manager.action_journal.applied_entries()) == 1
        manager.undo()
        assert not component.physic_material.guid
        manager.redo()
        assert component.physic_material.guid == "bouncy-guid"
    finally:
        UndoManager._instance = previous


def test_initial_material_preview_uses_the_in_memory_document(monkeypatch, tmp_path):
    import infernux.engine.ui.inspector_material as module
    from infernux.engine.ui import asset_resource_preview

    path = tmp_path / "Fresh.mat"
    path.write_text('{"name":"Fresh"}', encoding="utf-8")
    calls = []
    native = object()
    monkeypatch.setattr(asset_resource_preview, "_resolve_native_engine", lambda _panel: native)
    monkeypatch.setattr(
        asset_resource_preview,
        "_try_get_cpp_material_preview_texture",
        lambda native, preview_path, **kwargs: calls.append((native, preview_path, kwargs)) or 17,
    )

    document = {"name": "Fresh"}
    tex = module._query_material_preview_tex(
        SimpleNamespace(),
        None,
        document,
        SimpleNamespace(extra={"cached_json": json.dumps(document)}),
        "",
        str(path),
    )

    assert tex == (17, f"mat|{path}")
    assert calls == [
        (
            native,
            str(path),
            {
                "material_json": json.dumps(document),
                "file_mtime_hint": 0,
            },
        )
    ]


def test_texture_preview_settings_generation_and_authoring_release(tmp_path, monkeypatch):
    from infernux.engine.ui import asset_resource_preview

    path = tmp_path / "Live.png"
    path.write_bytes(b"preview-source")

    class _Native:
        def __init__(self):
            self.calls = []
            self.released = []

        def query_or_schedule_texture_preview(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return 7, 64, 64

        def release_preview_authoring(self, key):
            self.released.append(key)

    native = _Native()
    monkeypatch.setattr(asset_resource_preview, "_resolve_native_engine", lambda _panel: native)
    settings = TextureImportSettings()
    asset_resource_preview._try_get_cpp_texture_preview(native, str(path), settings)
    settings.generate_mipmaps = False
    asset_resource_preview._try_get_cpp_texture_preview(native, str(path), settings)

    assert all(call[0][0].startswith("tex|") for call in native.calls)
    assert native.calls[0][0][2] != native.calls[1][0][2]
    assert native.calls[0][1]["max_size"] == 2048
    assert native.calls[0][1]["texture_format"] == "auto"
    assert native.calls[0][1]["texture_type"] == "default"
    assert native.calls[0][1]["authoring"] is True

    asset_resource_preview.invalidate_live_texture_preview(str(path))
    assert native.released == [f"tex|{os.path.normpath(path)}"]


def test_texture_preview_query_failure_is_not_suppressed(tmp_path):
    from infernux.engine.ui import asset_resource_preview

    path = tmp_path / "Broken.png"
    path.write_bytes(b"preview-source")

    class _Native:
        @staticmethod
        def query_or_schedule_texture_preview(*_args, **_kwargs):
            raise RuntimeError("preview device failure")

    with pytest.raises(RuntimeError, match="preview device failure"):
        asset_resource_preview._try_get_cpp_texture_preview(_Native(), str(path), TextureImportSettings())


def test_preview_authoring_release_failure_preserves_owner(monkeypatch):
    from infernux.engine.ui import asset_resource_preview

    class _Native:
        @staticmethod
        def release_preview_authoring(_key):
            raise RuntimeError("preview release failure")

    key = "mat|Assets/Broken.mat"
    asset_resource_preview._AUTHORING_PREVIEW_KEYS.add(key)
    monkeypatch.setattr(asset_resource_preview, "_resolve_native_engine", lambda _panel: _Native())
    try:
        with pytest.raises(RuntimeError, match="preview release failure"):
            asset_resource_preview.release_all_preview_authoring()
        assert key in asset_resource_preview._AUTHORING_PREVIEW_KEYS
    finally:
        asset_resource_preview._AUTHORING_PREVIEW_KEYS.discard(key)


def test_material_surface_edit_reuses_and_invalidates_the_native_batch_plan(monkeypatch):
    import infernux.engine.ui.inspector_material as module

    locale = ["zh"]
    monkeypatch.setattr(module, "get_locale", lambda: locale[0])
    monkeypatch.setattr(module, "t", lambda _key: "xxxx")

    class Context:
        def __init__(self):
            self.plans, self.values = [], []
            self.edits = {}

        def create_property_batch_plan(self, descriptors):
            required_by_type = {0: ("f",), 1: ("i",), 2: ("b",), 7: ("ei", "en")}
            for descriptor in descriptors:
                assert all(key in descriptor for key in required_by_type[descriptor["t"]])
            self.plans.append(tuple(descriptors))
            return self.plans[-1]

        def render_property_batch_plan_values(self, plan, values, label_width):
            assert label_width == 144.0
            self.values.append((plan, tuple(values)))
            edits, self.edits = self.edits, {}
            return edits

    ctx, cache = Context(), {}
    render_state = {
        "blendEnable": False,
        "depthWriteEnable": True,
        "depthTestEnable": True,
        "depthCompareOp": 1,
        "cullMode": 2,
        "renderQueue": 2000,
        "srcColorBlendFactor": 6,
        "dstColorBlendFactor": 7,
        "alphaClipEnabled": False,
        "alphaClipThreshold": 0.5,
    }
    document = {"renderState": render_state, "renderStateOverrides": 0}
    module._render_surface_options_batch(ctx, render_state, document, 0, 144.0, cache=cache)
    assert len(ctx.plans[0]) == 6
    render_state["renderQueue"] = 1200
    module._render_surface_options_batch(ctx, render_state, document, 0, 144.0, cache=cache)
    assert len(ctx.plans) == 1
    assert ctx.values[0][0] is ctx.values[1][0]
    assert ctx.values[0][1] != ctx.values[1][1]

    ctx.edits = {0: 1}
    overrides, change_key = module._render_surface_options_batch(ctx, render_state, document, 0, 144.0, cache=cache)
    assert render_state["blendEnable"] is True
    assert render_state["renderQueue"] == 3000
    assert overrides == document["renderStateOverrides"]
    assert change_key == "render_state.surface"
    render_state["alphaClipEnabled"] = True
    module._render_surface_options_batch(ctx, render_state, document, 0, 144.0, cache=cache)
    assert len(ctx.plans) == 2
    descriptors = ctx.plans[-1]
    enums = [descriptor for descriptor in descriptors if descriptor["t"] == 7]
    assert enums and all(0 <= item["ei"] < len(item["en"]) for item in enums)
    assert any(item["t"] == 0 and "f" in item for item in descriptors)
    assert any(item["t"] == 1 and "i" in item for item in descriptors)

    render_state["srcColorBlendFactor"] = 1
    render_state["dstColorBlendFactor"] = 1
    module._render_surface_options_batch(ctx, render_state, document, 0, 144.0, cache=cache)
    assert len(ctx.plans) == 2
    locale[0] = "en"
    module._render_surface_options_batch(ctx, render_state, document, 0, 144.0, cache=cache)
    assert len(ctx.plans) == 3
    cache["entries"][0][1].pop("ei")
    module._render_surface_options_batch(ctx, render_state, document, 0, 144.0, cache=cache)
    assert len(ctx.plans) == 4
    assert all("ei" in descriptor for _, descriptor in cache["entries"] if descriptor["t"] == 7)


class _ImportContext:
    def __init__(self):
        self.semantics = []

    def begin_disabled(self, _disabled):
        pass

    def end_disabled(self):
        pass

    def combo(self, _label, current, _items):
        return current

    def drag_float(self, _label, value, _speed, _minimum, _maximum):
        return value

    def record_semantic_item(self, *args):
        self.semantics.append(args)


class _SpriteContext(_ImportContext):
    def separator(self):
        pass

    def label(self, _text):
        pass

    def set_next_item_width(self, _width):
        pass

    def input_int(self, label, value, _step, _step_fast):
        return 3 if label == "##sprite_cols" else value

    def button(self, _label, callback=None):
        if callback is not None:
            callback()
        return callback is not None

    def dummy(self, _width, _height):
        pass


class _ApplyContext(_ImportContext):
    def __init__(self):
        super().__init__()
        self.buttons = []

    def separator(self):
        pass

    def push_style_color(self, *_args):
        pass

    def pop_style_color(self, _count):
        pass

    def same_line(self):
        pass

    def button(self, label, _callback):
        self.buttons.append(label)


def test_animation_curve_serialized_field_uses_shared_popup_editor(monkeypatch):
    from infernux.components.fields import FieldMetadata, FieldType
    from infernux.engine.ui import curve_editor
    from infernux.graph.ramp import AnimationCurve, Keyframe

    captured = {}
    monkeypatch.setattr(inspector_utils, "_label_or_fullwidth", lambda *_args: None)

    def render_curve(_ctx, widget_id, document, **kwargs):
        captured.update(widget_id=widget_id, document=document, **kwargs)
        return AnimationCurve((Keyframe(0.0, 1.0), Keyframe(1.0, 2.0))).to_dict()

    monkeypatch.setattr(curve_editor, "render_curve_property", render_curve)
    metadata = FieldMetadata(
        name="curve",
        field_type=FieldType.ANIMATION_CURVE,
        default=AnimationCurve(),
        curve_non_negative=True,
    )

    result = inspector_utils.render_serialized_field(
        SimpleNamespace(),
        "##curve",
        "Curve",
        metadata,
        AnimationCurve(),
        80.0,
    )

    assert isinstance(result, AnimationCurve)
    assert result.keys[-1].value == 2.0
    assert captured["widget_id"] == "##curve"
    assert captured["non_negative"] is True


def test_builtin_list_field_is_planned_and_committed_generically(monkeypatch):
    from infernux.components.fields import FieldMetadata, FieldType
    from infernux.engine.ui import inspector_components as module

    metadata = FieldMetadata(
        name="points",
        field_type=FieldType.LIST,
        default=[],
        element_type=FieldType.VEC3,
    )
    prop = SimpleNamespace(metadata=metadata, cpp_attr="points")
    comp = SimpleNamespace(points=[(0.0, 0.0, 0.0)])
    ctx = SimpleNamespace(create_property_batch_plan=lambda descriptors: descriptors)
    cache = {"values": {}, "field_revisions": {}}

    plan = module._build_builtin_cached_plan(ctx, comp, [("points", prop)], 80.0, None, cache, True)

    assert [op["kind"] for op in plan["ops"]] == ["list"]

    recorded = []
    monkeypatch.setattr(
        module,
        "_render_list_field",
        lambda _ctx, target, field, _meta, current, _lw, **kwargs: kwargs["on_change"](
            target, field, current, [(1.0, 2.0, 3.0)]
        ),
    )
    monkeypatch.setattr(module, "_tooltip_and_info", lambda *_args: None)
    monkeypatch.setattr(
        module,
        "_record_builtin_property",
        lambda target, field, old, new, description: recorded.append((target, field, old, new, description)),
    )

    assert module._replay_builtin_cached_plan(ctx, comp, plan, cache) is True
    assert recorded == [
        (
            comp,
            "points",
            [(0.0, 0.0, 0.0)],
            [(1.0, 2.0, 3.0)],
            "Set points",
        )
    ]


def test_builtin_field_visibility_failure_is_not_treated_as_visible():
    from infernux.components.fields import FieldMetadata, FieldType
    from infernux.engine.ui import inspector_components as module

    def invalid_visibility(_component):
        raise RuntimeError("invalid visibility dependency")

    metadata = FieldMetadata(
        name="value",
        field_type=FieldType.FLOAT,
        default=0.0,
        visible_when=invalid_visibility,
    )
    prop = SimpleNamespace(metadata=metadata, cpp_attr="value")
    ctx = SimpleNamespace(create_property_batch_plan=lambda descriptors: descriptors)

    with pytest.raises(RuntimeError, match="invalid visibility dependency"):
        module._build_builtin_cached_plan(
            ctx,
            SimpleNamespace(value=1.0),
            [("value", prop)],
            80.0,
            None,
            {"values": {}, "field_revisions": {}},
            True,
        )
