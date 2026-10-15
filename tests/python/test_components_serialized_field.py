"""Tests for infernux.components.fields — field types, metadata, descriptors.

Merges tests from test_component_annotation_defaults.py.
"""

import weakref
from typing import ClassVar

from infernux.components import InxComponent
from infernux.components.fields import (
    FieldType,
    FieldMetadata,
    SerializedFieldDescriptor,
    get_serialized_fields,
    serialized_field,
)


def test_component_class_constants_do_not_block_lifecycle_publication():
    class DemoComponent(InxComponent):
        SCENES = (("Jelly", "01_XPBD_Jelly"),)
        HINTS = {"01_XPBD_Jelly": "Press Space"}
        SETTINGS: ClassVar[dict]
        count: int
        active_flag: bool
        label: str
        speed = 5.0
        EXPLICIT = serialized_field(default=7)
        _counter: int
        _items: list[int]
        _backing = serialized_field("{}", hidden=True)

        def start(self):
            self.started = True

    fields = get_serialized_fields(DemoComponent)
    assert set(fields) == {"count", "active_flag", "label", "speed", "EXPLICIT", "_backing"}
    component = DemoComponent()
    try:
        assert (component.count, component.active_flag, component.label, component.speed) == (0, False, "", 5.0)
        assert component._counter == 0 and component._items == []
        component.count, component.active_flag, component.label = 1, True, "authored"
        component._counter += 1
        component._items.append(3)
        component._backing = '{"value":2}'
        document = component._serialize_fields_document()
        assert document["count"] == 1 and document["active_flag"] is True and document["label"] == "authored"
        assert document["_backing"] == '{"value":2}' and fields["_backing"].hidden
        assert "_counter" not in document and "_items" not in document
        assert DemoComponent.SCENES == (("Jelly", "01_XPBD_Jelly"),)
        assert DemoComponent.HINTS["01_XPBD_Jelly"] == "Press Space"
        from infernux.engine.runtime_dispatch import current_runtime_epoch

        assert "start" in current_runtime_epoch().require_descriptor(DemoComponent).methods
    finally:
        component._call_on_destroy()


def test_null_serialized_reference_uses_annotation_and_retains_constraints():
    from infernux.lib import GameObject
    from infernux.components.ref_wrappers import GameObjectRef

    class Reporter(InxComponent):
        target: GameObject = serialized_field(
            default=None,
            header="References",
            tooltip="Select a renderer",
            required_component="MeshRenderer",
        )

    metadata = get_serialized_fields(Reporter)["target"]
    assert metadata.field_type == FieldType.GAME_OBJECT
    assert isinstance(metadata.default, GameObjectRef)
    assert metadata.header == "References"
    assert metadata.tooltip == "Select a renderer"
    assert metadata.required_component == "MeshRenderer"
    assert Reporter().target is None
    assert Reporter._field_schemas_["target"].value_type == "FieldType.GAME_OBJECT"


def test_readonly_serialized_field_is_runtime_writable_but_remains_authoring_readonly():
    class Telemetry(InxComponent):
        frames: int = serialized_field(default=0, readonly=True)

    component = Telemetry()
    component.frames = 7

    assert component.frames == 7
    assert get_serialized_fields(Telemetry)["frames"].readonly is True


def test_unknown_raw_path_does_not_create_path_only_reference(monkeypatch):
    from infernux.components import fields as serialized_field_module
    from infernux.core.assets import AssetManager

    class Database:
        def get_guid_from_path(self, _path):
            return ""

    monkeypatch.setattr(AssetManager, "_asset_database", Database())

    texture = serialized_field_module._ensure_texture_ref("Assets/Textures/missing.png")
    shader = serialized_field_module._ensure_shader_ref("Assets/Shaders/missing.frag")
    material = serialized_field_module._ensure_material_ref("Assets/Materials/missing.mat")
    from infernux.core.asset_ref import MaterialRef

    direct_material = MaterialRef("Assets/Materials/missing.mat")
    asset = serialized_field_module._ensure_asset_ref("Assets/VFX/missing.particlegraph", "ParticleGraph")

    for reference in (texture, shader, material, direct_material, asset):
        assert reference.guid == ""
        assert reference.path_hint == ""
        assert not reference


class TestSerializedFieldFactory:
    def test_stale_weakref_callback_cannot_remove_reused_instance_slot(self):
        descriptor = SerializedFieldDescriptor(FieldMetadata(name="value", field_type=FieldType.STRING, default=""))

        class Owner:
            pass

        stale_owner = Owner()
        current_owner = Owner()
        stale_ref = weakref.ref(stale_owner)
        current_ref = weakref.ref(current_owner)
        slot = 17
        descriptor._weak_refs[slot] = current_ref
        descriptor._values[slot] = "current"

        with descriptor._lock:
            descriptor._make_ref_callback(slot)(stale_ref)

        assert descriptor._weak_refs[slot] is current_ref
        assert descriptor._values[slot] == "current"
