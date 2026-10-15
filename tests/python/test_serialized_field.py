"""Annotation-driven Unity-style serialized field declarations.

Covers:
- bare typed defaults (``health: int = 100``)
- Annotated[] markers (Range/Tooltip/Header/HideInInspector/NonSerialized/...)
- Optional[X] and ``X | None`` unwrapping
- enum annotations
- Color fields
- asset/component reference annotations
- annotation + serialized_field() composition
- CDS backing for annotated numeric fields
"""
from __future__ import annotations

import enum
from typing import Annotated, Optional, List

import pytest

from infernux.components import (
    InxComponent, serialized_field, FieldType, get_serialized_fields,
    Range, Tooltip, Header, Space, Group, InfoText, DragSpeed,
    Multiline, ReadOnly, HideInInspector, NonSerialized, HDR, Color,
    VALUE_CODECS,
)
from infernux.components.fields import (
    build_field_from_annotation, coerce_serialized_field_input,
    _unwrap_annotation, _UNSET,
)
from infernux.components.ref_wrappers import GameObjectRef, PrefabRef
from infernux.core.asset_ref import AudioClipRef, ParticleGraphRef
from infernux.graph.ramp import AnimationCurve, Gradient, Keyframe


# ── annotation unwrapping ────────────────────────────────────────────────

class TestUnwrapAnnotation:


    def test_pep604_union_none_unwraps(self):
        base, _ = _unwrap_annotation(int | None)
        assert base is int

    def test_annotated_inside_optional(self):
        base, markers = _unwrap_annotation(Optional[Annotated[int, Range(1, 5)]])
        assert base is int
        assert any(isinstance(m, Range) for m in markers)


# ── build_field_from_annotation ──────────────────────────────────────────

class TestBuildField:


    def test_range_marker(self):
        meta = build_field_from_annotation(Annotated[float, Range(0, 100, slider=False)], default=5.0)
        assert meta.range == (0, 100) and meta.slider is False

    def test_all_cosmetic_markers(self):
        ann = Annotated[str, Tooltip("tip"), Header("H"), Space(12.0),
                        Group("G"), InfoText("info"), Multiline, ReadOnly]
        meta = build_field_from_annotation(ann, default="x")
        assert meta.tooltip == "tip" and meta.header == "H"
        assert meta.space == 12.0 and meta.group == "G"
        assert meta.info_text == "info" and meta.multiline and meta.readonly

    def test_drag_speed_marker(self):
        meta = build_field_from_annotation(Annotated[int, DragSpeed(0.25)], default=1)
        assert meta.drag_speed == 0.25


    def test_enum_annotation(self):
        class Mode(enum.Enum):
            A = 1
            B = 2
        meta = build_field_from_annotation(Mode, default=_UNSET)
        assert meta.field_type == FieldType.ENUM
        assert meta.enum_type is Mode and meta.default == Mode.A

    def test_enum_editor_input_accepts_member_name_and_integer(self):
        class Mode(enum.Enum):
            A = 1
            B = 2

        meta = build_field_from_annotation(Mode, default=Mode.A)

        assert coerce_serialized_field_input("B", meta, "Probe.mode") is Mode.B
        assert coerce_serialized_field_input("b", meta, "Probe.mode") is Mode.B
        assert coerce_serialized_field_input(1, meta, "Probe.mode") is Mode.A

    def test_color_annotation(self):
        meta = build_field_from_annotation(Color, default=_UNSET)
        assert meta.field_type == FieldType.COLOR
        assert tuple(meta.default) == (1.0, 1.0, 1.0, 1.0)
        assert meta.hdr is False


    def test_color_default_coercion(self):
        meta = build_field_from_annotation(Color, default=(0.5, 0.25, 0.0))
        assert meta.field_type == FieldType.COLOR
        assert meta.default == [0.5, 0.25, 0.0, 1.0]


    @pytest.mark.parametrize(
        ("annotation", "field_type"),
        [
            (AnimationCurve, FieldType.ANIMATION_CURVE),
            (Gradient, FieldType.GRADIENT),
        ],
    )
    def test_ramp_annotations_are_first_class_serialized_fields(
        self, annotation, field_type
    ):
        meta = build_field_from_annotation(annotation, default=_UNSET)

        assert meta.field_type == field_type
        assert isinstance(meta.default, annotation)

    def test_curve_editor_document_coerces_to_animation_curve(self):
        meta = build_field_from_annotation(AnimationCurve, default=_UNSET)
        document = AnimationCurve(
            (Keyframe(0.0, 2.0), Keyframe(1.0, 3.0)),
            "repeat",
            "ping_pong",
        ).to_dict()

        value = coerce_serialized_field_input(document, meta, "Probe.width")

        assert isinstance(value, AnimationCurve)
        assert value.pre_wrap == "repeat"
        assert value.post_wrap == "ping_pong"

    def test_unsupported_annotation_falls_back_to_value(self):
        class Weird:
            pass
        meta = build_field_from_annotation(Weird, default=2.5)
        assert meta is not None and meta.field_type == FieldType.FLOAT

    def test_audio_asset_annotation_carries_explicit_contract(self):
        meta = build_field_from_annotation(AudioClipRef, default=_UNSET)

        assert meta.field_type == FieldType.ASSET
        assert meta.asset_type == "AudioClip"

    def test_registered_asset_ref_annotation_infers_contract(self):
        meta = build_field_from_annotation(ParticleGraphRef, default=_UNSET)

        assert meta.field_type == FieldType.ASSET
        assert meta.asset_type == "ParticleGraph"

    def test_prefab_ref_annotation_is_a_game_object_asset_field(self):
        meta = build_field_from_annotation(PrefabRef, default=_UNSET)

        assert meta.field_type == FieldType.GAME_OBJECT
        assert isinstance(meta.default, PrefabRef)

    def test_prefab_path_coerces_to_guid_backed_prefab_ref(self, monkeypatch):
        meta = build_field_from_annotation(PrefabRef, default=_UNSET)
        from infernux.core.assets import AssetManager

        class Database:
            def get_guid_from_path(self, path):
                return "prefab-guid" if path == "Assets/Prefabs/Coin.prefab" else ""

            def get_path_from_guid(self, guid):
                return "Assets/Prefabs/Coin.prefab" if guid == "prefab-guid" else ""

        monkeypatch.setattr(AssetManager, "_asset_database", Database())

        value = coerce_serialized_field_input(
            "Assets/Prefabs/Coin.prefab",
            meta,
            "Spawner.coin_prefab",
        )

        assert isinstance(value, PrefabRef)
        assert value.guid == "prefab-guid"
        assert value.path_hint == "Assets/Prefabs/Coin.prefab"

    def test_asset_list_annotation_propagates_element_contract(self):
        meta = build_field_from_annotation(list[AudioClipRef], default=_UNSET)

        assert meta.field_type == FieldType.LIST
        assert meta.element_type == FieldType.ASSET
        assert meta.asset_type == "AudioClip"

    def test_untyped_asset_field_is_rejected_at_schema_construction(self):
        with pytest.raises(ValueError, match="explicit asset_type"):
            serialized_field(default=None, field_type=FieldType.ASSET)

        with pytest.raises(ValueError, match="explicit asset_type"):
            serialized_field(
                default=[],
                field_type=FieldType.LIST,
                element_type=FieldType.ASSET,
            )


# ── full component declarations ──────────────────────────────────────────

class _Phase(enum.Enum):
    IDLE = 0
    RUN = 1


class SerializedFieldShowcase(InxComponent):
    # Unity-style: annotation + plain default
    health: int = 100
    speed: Annotated[float, Range(0, 20), Tooltip("m/s")] = 5.0
    name_tag: Annotated[str, Multiline] = "hello"
    armor: float = 3            # int literal, float annotation → coerced
    phase: _Phase = _Phase.RUN
    tint: Color = Color(1, 0, 0)
    secret: Annotated[int, HideInInspector] = 9
    scratch: Annotated[int, NonSerialized] = 0
    opt_count: Optional[int] = 4
    # references
    mat: 'Material' = None  # noqa: F821  (string annotation; resolves via registry)
    target: Optional[GameObjectRef] = None
    grouped_value: Annotated[float, Group("Advanced")] = serialized_field(default=1.5)


class TestSerializedFieldShowcase:
    def test_declared_fields_preserve_metadata_and_authored_document(self):
        fields = get_serialized_fields(SerializedFieldShowcase)
        assert fields['armor'].field_type == FieldType.FLOAT
        assert isinstance(fields['armor'].default, float)
        assert fields['health'].field_type == FieldType.INT and fields['health'].default == 100
        assert fields['speed'].range == (0, 20) and fields['speed'].tooltip == 'm/s'
        assert fields['name_tag'].multiline
        assert fields['phase'].field_type == FieldType.ENUM
        assert fields['phase'].enum_type is _Phase and fields['phase'].default == _Phase.RUN
        assert fields['tint'].field_type == FieldType.COLOR
        assert fields['tint'].default == [1., 0., 0., 1.] and not fields['tint'].hdr
        assert fields['secret'].hidden and 'scratch' not in fields
        assert fields['opt_count'].field_type == FieldType.INT and fields['opt_count'].default == 4
        assert fields['mat'].field_type == FieldType.MATERIAL
        assert fields['grouped_value'].group == 'Advanced'
        assert fields['grouped_value'].default == 1.5
        authored, restored = SerializedFieldShowcase(), SerializedFieldShowcase()
        try:
            assert authored.health == 100 and authored.speed == pytest.approx(5.)
            authored.health, authored.speed, authored.secret = 55, 9.5, 1234
            document = authored._serialize_fields_document()
            assert document['secret'] == 1234 and 'scratch' not in document
            restored._deserialize_fields_document(document)
            assert restored._serialize_fields_document() == document
            assert restored.health == 55 and restored.speed == pytest.approx(9.5)
            assert restored.phase is _Phase.RUN and restored.secret == 1234
        finally:
            authored._call_on_destroy()
            restored._call_on_destroy()

    def test_annotated_numeric_fields_share_scalar_and_batch_storage(self):
        import numpy as np
        from infernux.batch import batch_read, batch_write

        components = [SerializedFieldShowcase(), SerializedFieldShowcase()]
        try:
            np.testing.assert_array_equal(batch_read(components, 'health'), [100, 100])
            components[0].health = 55
            np.testing.assert_array_equal(batch_read(components, 'health'), [55, 100])
            batch_write(components, np.array([7., 9.5]), 'speed')
            assert [component.speed for component in components] == pytest.approx([7., 9.5])
            np.testing.assert_allclose(batch_read(components, 'speed'), [7., 9.5])
        finally:
            for component in components:
                component._call_on_destroy()


class TestAnnotationOnlyDeclarations:
    def test_bare_annotation_creates_field(self):
        class Bare(InxComponent):
            counter: int
            ratio: Annotated[float, Range(0, 1)]

        fields = get_serialized_fields(Bare)
        assert fields['counter'].field_type == FieldType.INT
        assert fields['counter'].default == 0
        assert fields['ratio'].range == (0, 1)

    def test_private_annotation_not_serialized(self):
        class Priv(InxComponent):
            _hidden_counter: int = 3
            shown: int = 1

        fields = get_serialized_fields(Priv)
        assert '_hidden_counter' not in fields and 'shown' in fields

    def test_explicit_private_hidden_field_is_serialized(self):
        class PrivateBacking(InxComponent):
            _state: str = serialized_field(default="{}", hidden=True)

        component = PrivateBacking()
        component._state = '{"value":2}'
        fields = get_serialized_fields(PrivateBacking)

        assert fields["_state"].hidden is True
        assert component._serialize_fields_document()["_state"] == '{"value":2}'

    def test_list_annotation(self):
        class WithList(InxComponent):
            tags: List[str] = serialized_field(default=[], field_type=FieldType.LIST,
                                               element_type=FieldType.STRING)
        fields = get_serialized_fields(WithList)
        assert fields['tags'].field_type == FieldType.LIST


class TestStrictSerializationFailures:
    def test_unsupported_field_value_raises_with_field_path(self):
        with pytest.raises(ValueError, match=r"UnsupportedField\.payload.*UNKNOWN"):
            class UnsupportedField(InxComponent):
                payload = serialized_field(default=None, field_type=FieldType.UNKNOWN)

    def test_non_finite_vector_rejects_save_and_preserves_authored_fields(self):
        from infernux.math import Vector3

        class CameraRig(InxComponent):
            _uses_component_data_store = False
            speed: float = serialized_field(default=2.4)
            sway: Vector3 = serialized_field(default=Vector3(0.1, 0.2, 0.3))

        component = CameraRig()
        component.speed = 5.0
        # Inject stale payload into the existing Python store; native Vector3
        # rejects NaN at construction before it can reach serialization.
        type(component).__dict__["sway"]._values[id(component)] = [
            float("nan"),
            0.0,
            0.0,
        ]

        with pytest.raises(ValueError, match=r"CameraRig\.sway"):
            component._serialize_fields_document()
        assert type(component).__dict__["speed"]._values[id(component)] == 5.0
        assert type(component).__dict__["sway"]._values[id(component)][1:] == [0.0, 0.0]

    def test_repair_keeps_editor_defaults_for_stale_scene_data(self):
        class ShowcaseDirector(InxComponent):
            style_interval: float = serialized_field(default=3.5)
            camera_name: str = serialized_field(default="Main Camera")

        component = ShowcaseDirector()
        component._deserialize_fields_document(
            {
                "__type_name__": "ShowcaseDirector",
                "style_interval": "broken",
                "camera_name": "Hero Camera",
                "camera_position_sway": [0.1, 0.2, 0.3],
            },
            repair=True,
        )

        assert component.style_interval == 3.5
        assert component.camera_name == "Hero Camera"
        healed = component._serialize_fields_document()
        assert "camera_position_sway" not in healed
        assert healed["style_interval"] == 3.5
        assert healed["camera_name"] == "Hero Camera"

    @pytest.mark.parametrize(
        "mutate, error",
        [
            (lambda data: data.__setitem__("__type_name__", "Other"), "type mismatch"),
            (lambda data: data.__setitem__("health", "bad"), "requires an integer"),
        ],
    )
    def test_invalid_field_document_is_rejected_without_partial_assignment(self, mutate, error):
        import json

        class StrictFields(InxComponent):
            health: int = 10
            title: str = "ready"

        source = StrictFields()
        document = json.loads(source._serialize_fields())
        mutate(document)

        target = StrictFields()
        target.health = 77
        target.title = "unchanged"
        with pytest.raises((TypeError, ValueError), match=error):
            target._deserialize_fields(json.dumps(document))
        assert target.health == 77
        assert target.title == "unchanged"

    def test_missing_new_component_field_uses_declared_default(self):
        import json

        class AdditiveFields(InxComponent):
            health: int = 10
            tags: list = serialized_field(
                default=[],
                field_type=FieldType.LIST,
                element_type=FieldType.STRING,
            )

        source = AdditiveFields()
        source.health = 42
        document = json.loads(source._serialize_fields())
        document.pop("tags")

        restored = AdditiveFields()
        restored._deserialize_fields(json.dumps(document))

        assert restored.health == 42
        assert restored.tags == []

    def test_removed_and_renamed_fields_are_ignored(self):
        import json

        class EvolvingFields(InxComponent):
            health: int = serialized_field(default=10)

        document = json.loads(EvolvingFields()._serialize_fields())
        document.pop("health")
        document["hp"] = 42
        document["removed_debug_value"] = True

        restored = EvolvingFields()
        restored._deserialize_fields(json.dumps(document))
        assert restored.health == 10
        assert restored._serialize_fields_document()["health"] == 10

    def test_unknown_metadata_is_ignored(self):
        import json

        class CurrentFields(InxComponent):
            value: int = 3

        document = json.loads(CurrentFields()._serialize_fields())
        document["__unknown__"] = 1
        restored = CurrentFields()
        restored._deserialize_fields(json.dumps(document))
        assert restored.value == 3
        assert "__unknown__" not in restored._serialize_fields_document()

    def test_all_fields_validate_before_any_decode(self, monkeypatch):
        class ValidateFirst(InxComponent):
            count: int = 1
            label: str = "ready"

        document = ValidateFirst()._serialize_fields_document()
        document["label"] = 42
        decode_calls = []
        original_decode = VALUE_CODECS.decode

        def track_decode(value, field_meta_or_type, path="value"):
            decode_calls.append(path)
            return original_decode(value, field_meta_or_type, path)

        monkeypatch.setattr(VALUE_CODECS, "decode", track_decode)

        with pytest.raises(TypeError, match="STRING field requires a string"):
            ValidateFirst()._deserialize_fields_document(document)

        assert decode_calls == []


class TestColorInference:

    def test_color_value_without_annotation(self):
        class TintHolder(InxComponent):
            tint = Color(0.2, 0.4, 0.6)
        fields = get_serialized_fields(TintHolder)
        assert fields['tint'].field_type == FieldType.COLOR
        assert fields['tint'].default == [0.2, 0.4, 0.6, 1.0]

    def test_future_annotations_color(self):
        import textwrap
        import tempfile
        import os
        script = textwrap.dedent('''
            from __future__ import annotations
            from infernux.components import InxComponent, Color
            class FutureColor(InxComponent):
                tint: Color
        ''')
        td = tempfile.mkdtemp()
        path = os.path.join(td, 'future_color.py')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(script)
        from infernux.components.script_loader import load_component_from_file
        cls = load_component_from_file(path)
        fields = get_serialized_fields(cls)
        assert fields['tint'].field_type == FieldType.COLOR


class TestColorClass:
    def test_factory_returns_material_list(self):
        c = Color(0.1, 0.2, 0.3)
        assert isinstance(c, list)
        assert c == [0.1, 0.2, 0.3, 1.0]

    def test_construct_from_sequence(self):
        c = Color((0.5, 0.6, 0.7, 0.8))
        assert c == [0.5, 0.6, 0.7, 0.8]

    def test_undo_snapshot_rgba(self):
        from infernux.engine.undo._base import _snapshot_value
        src = Color(0.2, 0.4, 0.6)
        snap = _snapshot_value(src)
        assert isinstance(snap, list)
        assert snap == [0.2, 0.4, 0.6, 1.0]
        assert snap is not src


class TestEffectColorFieldsAllowHdr:
    def test_mixin_color_annotation_keeps_hdr(self):
        from infernux.renderstack._serialized_field_mixin import (
            SerializedFieldCollectorMixin,
        )

        class BloomTint(SerializedFieldCollectorMixin):
            tint: Annotated[Color, HDR] = Color(1.0, 1.0, 1.0, 1.0)

        meta = get_serialized_fields(BloomTint)["tint"]
        assert meta.field_type == FieldType.COLOR
        assert meta.hdr is True
