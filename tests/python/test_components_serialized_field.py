"""Tests for infernux.components.fields — field types, metadata, descriptors.

Merges tests from test_component_annotation_defaults.py.
"""

import weakref

from infernux.components import InxComponent
from infernux.components.fields import (
    FieldType,
    FieldMetadata,
    SerializedFieldDescriptor,
    get_serialized_fields,
    serialized_field,
)


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
