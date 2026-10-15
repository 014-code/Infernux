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
    clear_serialized_fields_cache,
    get_serialized_fields,
    serialized_field,
)


def test_component_class_constants_do_not_block_lifecycle_publication():
    class DemoComponent(InxComponent):
        SCENES = (("Jelly", "01_XPBD_Jelly"),)
        HINTS = {"01_XPBD_Jelly": "Press Space"}
        SETTINGS: ClassVar[dict] = {"speed": 1.0}
        speed = 5.0
        EXPLICIT = serialized_field(default=7)

        def start(self):
            self.started = True

    fields = get_serialized_fields(DemoComponent)
    assert set(fields) == {"speed", "EXPLICIT"}
    assert DemoComponent.SCENES == (("Jelly", "01_XPBD_Jelly"),)
    assert DemoComponent.HINTS["01_XPBD_Jelly"] == "Press Space"

    from infernux.engine.runtime_dispatch import current_runtime_epoch

    descriptor = current_runtime_epoch().require_descriptor(DemoComponent)
    assert "start" in descriptor.methods


def test_annotation_only_class_var_is_not_a_serialized_field():
    class ClassVarComponent(InxComponent):
        SETTINGS: ClassVar[dict]
        speed = 3.0

    assert set(get_serialized_fields(ClassVarComponent)) == {"speed"}


def test_null_serialized_reference_uses_annotation_and_retains_constraints():
    from infernux.lib import GameObject
    from infernux.components.ref_wrappers import GameObjectRef

    class Reporter(InxComponent):
        target: GameObject = serialized_field(
            default=None, header="References", tooltip="Select a renderer",
            required_component="MeshRenderer",
        )

    metadata = get_serialized_fields(Reporter)['target']
    assert metadata.field_type == FieldType.GAME_OBJECT
    assert isinstance(metadata.default, GameObjectRef)
    assert metadata.header == "References"
    assert metadata.tooltip == "Select a renderer"
    assert metadata.required_component == "MeshRenderer"
    assert Reporter().target is None
    assert Reporter._field_schemas_['target'].value_type == 'FieldType.GAME_OBJECT'


def test_raw_path_assignment_resolves_guid_at_editor_boundary(monkeypatch):
    from infernux.components import fields as serialized_field_module
    from infernux.core.assets import AssetManager

    class Database:
        def get_guid_from_path(self, path):
            return "particle-guid" if path == "Assets/VFX/Ribbon.particlegraph" else ""

        def get_path_from_guid(self, guid):
            return "Assets/VFX/Ribbon.particlegraph" if guid == "particle-guid" else ""

    monkeypatch.setattr(AssetManager, "_asset_database", Database())

    guid, path_hint = serialized_field_module._extract_guid_and_path(
        "Assets/VFX/Ribbon.particlegraph", ()
    )

    assert guid == "particle-guid"
    assert path_hint == "Assets/VFX/Ribbon.particlegraph"


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
    asset = serialized_field_module._ensure_asset_ref(
        "Assets/VFX/missing.particlegraph", "ParticleGraph"
    )

    for reference in (texture, shader, material, direct_material, asset):
        assert reference.guid == ""
        assert reference.path_hint == ""
        assert not reference


class TestSerializedFieldFactory:
    def test_returns_descriptor(self):
        class Counter(InxComponent):
            _uses_component_data_store = False
            count: int = serialized_field(default=7)

        descriptor = Counter.count
        assert isinstance(descriptor, SerializedFieldDescriptor)
        assert descriptor.default == 7
        assert Counter().count == 7
        assert descriptor != 7
        assert {descriptor: "field"}[Counter.count] == "field"


    def test_stale_weakref_callback_cannot_remove_reused_instance_slot(self):
        descriptor = SerializedFieldDescriptor(
            FieldMetadata(name="value", field_type=FieldType.STRING, default="")
        )

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


class TestComponentFieldCollection:


    def test_annotation_only_gets_zero_defaults(self):
        """Ported from test_component_annotation_defaults.py."""

        class Comp(InxComponent):
            c: int
            speed: float
            enabled_flag: bool
            label: str

        comp = Comp()
        assert comp.c == 0
        assert comp.speed == 0.0
        assert comp.enabled_flag is False
        assert comp.label == ""

        comp.c += 1
        comp.speed += 2.5
        comp.enabled_flag = True
        comp.label = "ok"

        assert comp.c == 1
        assert comp.speed == 2.5
        assert comp.enabled_flag is True
        assert comp.label == "ok"

    def test_private_fields_hidden_but_initialized(self):
        """Ported from test_component_annotation_defaults.py."""

        class Comp(InxComponent):
            _c: int
            _items: list[int]

        comp = Comp()
        assert comp._c == 0
        assert comp._items == []

        comp._c += 1
        comp._items.append("x")

        assert comp._c == 1
        assert comp._items == ["x"]
        assert "_c" not in get_serialized_fields(Comp)
        assert "_items" not in get_serialized_fields(Comp)


    def test_class_field_table_is_authoritative_after_cache_clear(self):
        class Comp(InxComponent):
            speed: float = serialized_field(default=10.0)

        assert "speed" in get_serialized_fields(Comp)

        clear_serialized_fields_cache(Comp)
        Comp._serialized_fields_ = {}

        assert get_serialized_fields(Comp) == {}
