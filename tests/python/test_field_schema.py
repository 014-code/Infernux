"""Field schema values must not depend on a live editor or mutable metadata."""

import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from infernux.field_schema import FieldSchema


def test_existing_editor_surface_reexports_the_same_runtime_type():
    from infernux.engine.interaction import FieldSchema as EditorFieldSchema

    assert EditorFieldSchema is FieldSchema


def test_schema_owns_a_deeply_immutable_snapshot():
    attributes = {"range": [0.0, 10.0], "enum": {"members": ["a", "b"]}}
    schema = FieldSchema("Probe.value", "float", attributes=attributes)
    attributes["range"][1] = 200.0
    attributes["enum"]["members"].append("c")
    assert schema.attributes["range"] == (0.0, 10.0)
    assert schema.attributes["enum"]["members"] == ("a", "b")
    with pytest.raises(TypeError):
        schema.attributes["range"] = (0.0, 2.0)
    with pytest.raises(TypeError):
        schema.attributes["enum"]["members"] = ()
    with pytest.raises(FrozenInstanceError):
        schema.read_only = True
    assert copy.deepcopy(schema) is schema


def test_schema_document_roundtrip_and_detached_exports():
    schema = FieldSchema("Probe.value", "float", True, {"default": 2.0, "range": [0.0, 3.0]})
    document = schema.to_document()
    assert FieldSchema.from_document(json.loads(json.dumps(document))) == schema
    document["attributes"]["range"][0] = -100.0
    assert schema.attributes["range"] == (0.0, 3.0)


@pytest.mark.parametrize("value", [object(), lambda: True, float("nan"), float("inf"), {1: "invalid"}])
def test_schema_rejects_non_data_attributes(value):
    with pytest.raises((TypeError, ValueError), match="Probe.value"):
        FieldSchema("Probe.value", "float", attributes={"nested": [value]})


@pytest.mark.parametrize("read_only", ["false", 0, None])
def test_read_only_cannot_be_ambiguous(read_only):
    with pytest.raises(TypeError, match="boolean"):
        FieldSchema("Probe.value", "float", read_only)


def test_schema_document_rejects_unknown_keys():
    document = FieldSchema("Probe.value", "float").to_document()
    document["live_component"] = "not a schema field"
    with pytest.raises(ValueError, match="document"):
        FieldSchema.from_document(document)


def test_declaration_schema_carries_constraints_without_executing_callbacks():
    from infernux.components.fields import FieldMetadata, FieldType
    from infernux.components.field_schema_compiler import compile_field_schema

    def forbidden(*_args):
        raise AssertionError("schema compilation must not execute Inspector callbacks")

    metadata = FieldMetadata(
        "speed", FieldType.FLOAT, 8.0, range=(0.0, 5.0), readonly=True,
        hidden=True, tooltip="Speed", group="Movement", former_names=("velocity",),
        visible_when=forbidden, getter=forbidden, setter=forbidden,
    )
    schema = compile_field_schema(metadata, "Probe.speed")
    assert schema.read_only
    assert schema.attributes["field_id"] == "speed"
    assert schema.attributes["default"] == 5.0
    assert schema.attributes["range"] == (0.0, 5.0)
    assert schema.attributes["former_names"] == ("velocity",)
    assert schema.attributes["hidden"]
    assert schema.attributes["inspector_conditional_visibility"]
    assert not {"getter", "setter", "visible_when"} & schema.attributes.keys()
    assert FieldSchema.from_document(json.loads(json.dumps(schema.to_document()))) == schema


def test_enum_and_nested_data_declarations_preserve_current_codec_identities():
    from enum import Enum
    from infernux.components import SerializableObject
    from infernux.components.fields import FieldMetadata, FieldType
    from infernux.components.field_schema_compiler import compile_field_schema

    class Mode(Enum):
        WALK = 1
        RUN = 2

    class Stats(SerializableObject):
        __serialized_type_id__ = "schema-test:Stats"
        health: int = 100

    enum = compile_field_schema(FieldMetadata("mode", FieldType.ENUM, Mode.WALK, enum_type=Mode), "Probe.mode")
    assert enum.attributes["enum"]["members"] == (
        {"name": "WALK", "value": 1}, {"name": "RUN", "value": 2},
    )
    nested = compile_field_schema(FieldMetadata(
        "stats", FieldType.LIST, [Stats()], element_type=FieldType.SERIALIZABLE_OBJECT,
        element_class=Stats,
    ), "Probe.stats")
    assert nested.attributes["serializable_type"] == "schema-test:Stats"
    assert nested.attributes["element_type"] == "FieldType.SERIALIZABLE_OBJECT"
    assert FieldSchema.from_document(json.loads(json.dumps(nested.to_document()))) == nested


@pytest.mark.parametrize("kind,default,options,code", [
    ("UNKNOWN", object(), {}, "unsupported_value_kind"),
    ("LIST", [], {}, "invalid_element_type"),
    ("ENUM", 0, {}, "invalid_enum_type"),
    ("SERIALIZABLE_OBJECT", None, {}, "invalid_nested_type"),
    ("FLOAT", float("nan"), {}, "invalid_default"),
])
def test_incomplete_declarations_are_not_exported_as_semantic_fields(kind, default, options, code):
    from infernux.components.fields import FieldMetadata, FieldType
    from infernux.components.field_schema_compiler import FieldSchemaError, compile_field_schema

    with pytest.raises(FieldSchemaError) as error:
        compile_field_schema(FieldMetadata("value", FieldType[kind], default, **options), "Probe.value")
    assert error.value.code == code
    assert error.value.path == "Probe.value"


def test_schema_read_and_metadata_cache_invalidation_never_recompile(monkeypatch):
    from infernux.components import SerializableObject
    from infernux.components.fields import clear_serialized_fields_cache, get_field_schema, get_serialized_fields

    class Data(SerializableObject):
        speed: float = 2.0

    from infernux.components import field_schema_compiler
    def forbidden(*_args):
        raise AssertionError("a schema read must not compile declarations")
    monkeypatch.setattr(field_schema_compiler, "compile_field_schema", forbidden)
    first = get_field_schema(Data, "speed")
    get_serialized_fields(Data)["speed"].tooltip = "Updated declaration"
    assert get_field_schema(Data, "speed") is first
    assert first.attributes["tooltip"] == ""
    clear_serialized_fields_cache(Data)
    updated = get_field_schema(Data, "speed")
    assert updated is first
    assert updated.attributes["tooltip"] == ""
    with pytest.raises(TypeError):
        Data._field_schemas_["speed"] = first


@pytest.mark.parametrize("native_storage", [False, True])
def test_compiled_default_matches_the_actual_component_default(native_storage):
    from infernux.components import InxComponent, serialized_field
    from infernux.components.fields import FieldType, get_field_schema, list_field
    from infernux.math import Vector3

    class Defaults(InxComponent):
        _uses_component_data_store = native_storage
        speed: float = serialized_field(default=8.0, range=(0.0, 5.0))
        offset: Vector3 = serialized_field(default=Vector3(1, 2, 3))
        samples: list = list_field(element_type=FieldType.INT, default=[2, 4])

    component = Defaults()
    try:
        assert component.speed == get_field_schema(Defaults, "speed").attributes["default"] == 5.0
        offset_default = get_field_schema(Defaults, "offset").attributes["default"]
        samples_default = get_field_schema(Defaults, "samples").attributes["default"]
        assert tuple(component.offset) == offset_default == (1, 2, 3)
        assert tuple(component.samples) == samples_default == (2, 4)
        component.offset = Vector3(9, 8, 7)
        component.samples.append(6)
        assert offset_default == (1, 2, 3)
        assert samples_default == (2, 4)
    finally:
        component._call_on_destroy()


def test_engine_python_component_catalog_has_compilable_field_declarations():
    from infernux.components.registry import ensure_engine_component_catalog_loaded, get_all_types
    from infernux.components.fields import get_field_schema, get_serialized_fields

    ensure_engine_component_catalog_loaded()
    component_types = {
        cls for cls in get_all_types().values()
        if cls.__module__.startswith("infernux.") and getattr(cls, "_uses_component_data_store", True)
    }
    assert any(cls.__name__ == "UIText" for cls in component_types)
    for cls in component_types:
        for name in get_serialized_fields(cls):
            schema = get_field_schema(cls, name)
            assert schema is get_field_schema(cls, name)
            assert schema.attributes["field_id"] == name
            assert FieldSchema.from_document(json.loads(json.dumps(schema.to_document()))) == schema
def test_python_field_using_native_enum_shares_the_camera_enum_identity():
    from infernux.lib import CameraProjection, _Infernux
    from infernux.components.fields import FieldMetadata, FieldType
    from infernux.components.field_schema_compiler import compile_field_schema

    metadata = FieldMetadata("projection", FieldType.ENUM, CameraProjection.Perspective, enum_type=CameraProjection)
    schema = compile_field_schema(metadata, "Controller.projection")
    camera = _Infernux._semantic_catalog_snapshot().type_document("native:infernux.Camera")
    projection = next(field for field in camera["fields"] if field["attributes"]["field_id"] == "projection_mode")
    enum = schema.to_document()["attributes"]["enum"]
    assert enum["type_id"] == projection["attributes"]["enum"]["type_id"]
    assert enum["members"] == projection["attributes"]["enum"]["members"]
    assert schema.to_document()["attributes"]["default"] == projection["attributes"]["default"]
