"""Tests for infernux.components.serializable_object — SerializableObject base class."""

from infernux.components.serializable_object import (
    SerializableObject,
    get_serializable_type_id,
)
from infernux.components.fields import serialized_field, FieldType
from infernux.components.value_document import make_serializable_object
import pytest
from infernux.components.fields import (
    get_serialized_fields,
)


def test_inherited_data_field_uses_the_same_runtime_constraint():
    class Base(SerializableObject):
        speed = serialized_field(2.0, range=(0, 10))

    class Derived(Base):
        label = "child"

    value = Derived(speed=20.0)
    assert value.speed == 10.0
    value.speed = -20.0
    assert value.speed == 0.0
    restored = SerializableObject._deserialize(value._serialize())
    assert type(restored) is Derived and restored.speed == 0.0


def test_inherited_reference_serializes_raw_identity_not_resolved_object():
    import copy
    from infernux.components.ref_wrappers import GameObjectRef
    from infernux.components.fields import get_raw_field_value

    class Base(SerializableObject):
        target: GameObjectRef
        _target = serialized_field(None, field_type=FieldType.GAME_OBJECT, hidden=True)

    class Derived(Base):
        label = "reference owner"

    original = Derived(target=GameObjectRef(persistent_id=42), _target=GameObjectRef(persistent_id=43))
    document = original._serialize()
    for value in (copy.deepcopy(original), SerializableObject._deserialize(document)):
        assert get_raw_field_value(value, "target").persistent_id == 42
        assert get_raw_field_value(value, "_target").persistent_id == 43
        assert value._serialize() == document


def test_data_annotations_cover_nested_lists_enums_and_string_annotations():
    from enum import Enum

    class Mode(Enum):
        idle = 0
        moving = 1

    class Composite(SerializableObject):
        stats: Stats
        members: list[Stats]
        mode: Mode = Mode.moving

    first, second = Composite(), Composite()
    fields = get_serialized_fields(Composite)
    assert fields["stats"].serializable_class is Stats
    assert fields["members"].element_class is Stats
    assert fields["mode"].enum_type is Mode
    first.stats.hp = 7
    first.members.append(Stats(hp=3))
    assert second.stats.hp == 100 and second.members == []
    restored = SerializableObject._deserialize(first._serialize())
    assert restored.stats.hp == 7 and restored.members[0].hp == 3
    assert restored.mode is Mode.moving

    resolved = type(
        "StringAnnotationData",
        (SerializableObject,),
        {
            "__module__": __name__,
            "__annotations__": {"stats": "Stats", "values": "list[int]", "hidden": "int"},
            "hidden": serialized_field(default=4, hidden=True),
        },
    )
    fields = get_serialized_fields(resolved)
    assert fields["stats"].serializable_class is Stats
    assert fields["values"].element_type == FieldType.INT
    assert fields["hidden"].hidden
    assert resolved()._serialize()["fields"]["hidden"] == 4


# ── Test data classes ──


class Stats(SerializableObject):
    hp: int = serialized_field(default=100)
    mp: float = serialized_field(default=50.0)
    name: str = serialized_field(default="default")


class Nested(SerializableObject):
    inner: int = serialized_field(default=0)


class TestSerialization:
    def test_validate_document_does_not_construct_instance(self):
        class ValidateOnly(SerializableObject):
            value: int = serialized_field(default=0)

            def __new__(cls, *args, **kwargs):
                raise AssertionError("validation must not construct SerializableObject")

        data = make_serializable_object(
            get_serializable_type_id(ValidateOnly),
            {"value": 7},
        )

        actual_cls, fields = SerializableObject._validate_document(data, "Root.stats")

        assert actual_cls is ValidateOnly
        assert set(fields) == {"value"}

        data["fields"]["value"] = "invalid"
        with pytest.raises(TypeError, match="INT field requires an integer"):
            SerializableObject._deserialize(data)

    def test_extra_and_removed_document_fields_are_ignored(self, tmp_path):
        import json

        class EvolvingStats(Stats):
            tags: list[str] = serialized_field(default=[])

        authored = EvolvingStats(hp=6, mp=9.0, name="current", tags=["saved"])
        data = authored._serialize()
        assert data["$type"] == "serializable_object"
        data["schema_version"] = 99
        data["removed_envelope_value"] = True
        data["fields"].pop("name")
        data["fields"].pop("tags")
        data["fields"]["old_name"] = "ignored"
        path = tmp_path / "stats.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        restored = SerializableObject._deserialize(json.loads(path.read_text(encoding="utf-8")))
        assert type(restored) is EvolvingStats
        assert restored._serialize() == {
            "$type": "serializable_object",
            "type_id": get_serializable_type_id(EvolvingStats),
            "fields": {"hp": 6, "mp": 9.0, "name": "default", "tags": []},
        }
        restored.tags.append("local edit")
        assert EvolvingStats().tags == [] and authored.tags == ["saved"]

    def test_stable_type_id_rejects_an_old_type_name(self):
        class RenamedStats(SerializableObject):
            __serialized_type_id__ = "gameplay:CharacterStats"

            health: int = 100

        data = RenamedStats(health=42)._serialize()
        assert data["type_id"] == "gameplay:CharacterStats"
        data["type_id"] = "legacy.stats:Stats"

        with pytest.raises(ValueError, match="unknown SerializableObject type_id"):
            SerializableObject._deserialize(data)
