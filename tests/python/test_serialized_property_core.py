import time
from types import SimpleNamespace

import pytest


def _property_handle(targets, *, validate=lambda _value: "", publish=None):
    from infernux.engine.interaction import (
        FieldSchema,
        SerializedObjectView,
        SerializedPropertyBinding,
        SerializedPropertyHandle,
    )
    from infernux.engine.undo import SetPropertyCommand

    bindings = []
    ids = []
    for index, target in enumerate(targets):
        target_id = f"target:{index}"
        ids.append(target_id)
        bindings.append(
            SerializedPropertyBinding(
                target_id,
                read=lambda target=target: target.value,
                command_factory=lambda old, new, description, target=target: SetPropertyCommand(
                    target, "value", old, new, description
                ),
                validate=validate,
            )
        )
    return SerializedPropertyHandle(
        FieldSchema("Probe.value", "float"),
        SerializedObjectView(tuple(ids)),
        tuple(bindings),
        publish=publish,
    )


def test_snapshot_property_gesture_merges_until_pointer_release_boundary():
    from infernux.engine.interaction import SnapshotPropertyTransaction
    from infernux.engine.undo import UndoManager

    state = {"value": 1.0}
    previous = UndoManager._instance
    manager = UndoManager()
    try:

        def commit(value, gesture_id):
            SnapshotPropertyTransaction(
                "Transform:17",
                lambda: state["value"],
                lambda candidate: state.__setitem__("value", candidate),
                "Edit Transform",
                gesture_id=gesture_id,
            ).commit_or_raise(value)

        commit(2.0, "pointer-drag:1")
        # Gesture identity, rather than an arbitrary timing window, owns the
        # edit. Simulate a drag held longer than MERGE_WINDOW.
        manager.action_journal.entries[0].action.timestamp -= 10.0
        commit(3.0, "pointer-drag:1")
        assert len(manager.action_journal.applied_entries()) == 1

        # Releasing and pressing again creates a new undo step even when the
        # second drag starts immediately.
        manager.action_journal.entries[0].action.timestamp = time.monotonic()
        commit(4.0, "pointer-drag:2")
        assert len(manager.action_journal.applied_entries()) == 2
        manager.undo()
        assert state["value"] == 3.0
        manager.undo()
        assert state["value"] == 1.0
    finally:
        UndoManager._instance = previous


def test_snapshot_property_transaction_normalizes_and_clears_aggregate():
    from infernux.engine.interaction import SnapshotPropertyTransaction
    from infernux.engine.undo import UndoManager

    state = {"value": {"asset": "old", "subresource": "old-frame"}}
    previous = UndoManager._instance
    manager = UndoManager()
    try:
        transaction = SnapshotPropertyTransaction(
            "SpriteRenderer:17:sprite",
            lambda: state["value"],
            lambda value: state.__setitem__("value", value),
            "Set Sprite",
            normalize=lambda candidate: (
                {"asset": candidate, "subresource": f"{candidate}-frame"}
                if candidate
                else {"asset": "", "subresource": ""}
            ),
            clear_value="",
        )
        transaction.commit_or_raise("new")
        assert state["value"] == {"asset": "new", "subresource": "new-frame"}

        transaction.clear_or_raise()
        assert state["value"] == {"asset": "", "subresource": ""}
        manager.undo()
        assert state["value"] == {"asset": "new", "subresource": "new-frame"}
    finally:
        UndoManager._instance = previous


def test_attribute_property_transactions_merge_continuous_edits():
    from infernux.engine.interaction import make_attribute_property_transaction
    from infernux.engine.undo import UndoManager

    target = SimpleNamespace(value=1.0)
    previous = UndoManager._instance
    manager = UndoManager()
    try:
        first = make_attribute_property_transaction((target,), "value", description="Set Value")
        second = make_attribute_property_transaction((target,), "value", description="Set Value")
        first.commit_or_raise(2.0)
        second.commit_or_raise(3.0)

        assert target.value == 3.0
        assert len(manager.action_journal.applied_entries()) == 1
        manager.undo()
        assert target.value == 1.0
    finally:
        UndoManager._instance = previous


def test_python_reference_transaction_enforces_required_component(monkeypatch):
    from infernux.components import InxComponent, serialized_field
    from infernux.lib import GameObject
    from infernux.components import ref_wrappers
    from infernux.components.fields import get_raw_field_value
    from infernux.engine.interaction import make_python_component_property_transaction
    from infernux.engine.undo import UndoManager

    class Probe(InxComponent):
        target: GameObject = serialized_field(default=None, required_component="MeshRenderer")

    component = Probe()
    monkeypatch.setattr(ref_wrappers.GameObjectRef, "resolve", lambda self: self.persistent_id)
    monkeypatch.setattr(
        ref_wrappers,
        "_resolve_component_on_game_object",
        lambda target, required: object() if target == 13 and required == "MeshRenderer" else None,
    )
    previous = UndoManager._instance
    manager = UndoManager()
    try:
        transaction = make_python_component_property_transaction((component,), "target")
        assert transaction.commit({"object_id": 14}).value == "rejected"
        assert get_raw_field_value(component, "target").persistent_id == 0
        assert manager.action_journal.applied_entries() == ()
        transaction.commit_or_raise({"object_id": 13})
        assert get_raw_field_value(component, "target").persistent_id == 13
        assert transaction.commit({"object_id": 14}).value == "rejected"
        assert get_raw_field_value(component, "target").persistent_id == 13
        manager.undo()
        assert get_raw_field_value(component, "target").persistent_id == 0
    finally:
        UndoManager._instance = previous


def test_python_component_multi_edit_is_one_atomic_document_action():
    from infernux.components import InxComponent
    from infernux.engine.interaction import make_python_component_property_transaction
    from infernux.engine.undo import UndoManager

    class ProbeComponent(InxComponent):
        speed: float = 1.0
        _validate_count: int = 0

        def on_validate(self):
            self._validate_count += 1

    first, second = ProbeComponent(), ProbeComponent()
    second.speed = 2.0
    previous = UndoManager._instance
    manager = UndoManager()
    try:
        first.speed = 3.0
        assert manager.action_journal.applied_entries() == ()
        transaction = make_python_component_property_transaction((first, second), "speed", description="Set Speed")
        assert transaction.handle.mixed
        transaction.commit_or_raise(4.0)
        assert (first.speed, second.speed) == (4.0, 4.0)
        assert (first._validate_count, second._validate_count) == (1, 1)
        assert len(manager.action_journal.applied_entries()) == 1
        manager.undo()
        assert (first.speed, second.speed) == (3.0, 2.0)
        assert (first._validate_count, second._validate_count) == (2, 2)
        manager.redo()
        assert (first.speed, second.speed) == (4.0, 4.0)
        assert (first._validate_count, second._validate_count) == (3, 3)
    finally:
        first._call_on_destroy()
        second._call_on_destroy()
        UndoManager._instance = previous


def test_python_property_transaction_normalizes_declared_enum_and_undo():
    from enum import Enum
    from infernux.components import InxComponent, serialized_field
    from infernux.engine.interaction import make_python_component_property_transaction
    from infernux.engine.undo import UndoManager

    class Mode(Enum):
        WALK = 1
        RUN = 2

    class Probe(InxComponent):
        mode = serialized_field(Mode.WALK)

    component = Probe()
    previous = UndoManager._instance
    manager = UndoManager()
    try:
        transaction = make_python_component_property_transaction((component,), "mode")
        transaction.commit_or_raise("RUN")
        assert component.mode is Mode.RUN
        assert len(manager.action_journal.applied_entries()) == 1
        manager.undo()
        assert component.mode is Mode.WALK
        manager.redo()
        assert component.mode is Mode.RUN
        assert transaction.commit("missing").value == "rejected"
        assert component.mode is Mode.RUN
    finally:
        UndoManager._instance = previous


@pytest.mark.parametrize("surface", ["command", "inspector"])
def test_python_field_edit_surfaces_share_range_and_no_change_semantics(surface):
    from infernux.components import InxComponent, get_serialized_fields, serialized_field
    from infernux.engine.interaction import (
        ComponentCommandService,
        make_python_component_property_transaction,
    )
    from infernux.engine.ui.inspector_components import _commit_python_component_field
    from infernux.engine.undo import UndoManager

    class RangeProbe(InxComponent):
        speed = serialized_field(1.0, range=(0.0, 5.0))

    component = RangeProbe()
    previous = UndoManager._instance
    manager = UndoManager()
    previous_service = ComponentCommandService._instance
    service = ComponentCommandService()
    try:

        def edit(value):
            if surface == "transaction":
                return make_python_component_property_transaction((component,), "speed").commit_or_raise(value)
            if surface == "command":
                return service.set_field(component, "speed", value)
            return _commit_python_component_field(
                (component,), "speed", get_serialized_fields(RangeProbe)["speed"], value
            )

        edit(50.0)
        assert component.speed == 5.0
        assert len(manager.action_journal.applied_entries()) == 1
        journal_entry = manager.action_journal.applied_entries()[0]
        action_time = journal_entry.action.timestamp
        edit(99.0)
        assert manager.action_journal.applied_entries()[0].action.timestamp == action_time
        for invalid in (True, "3", float("nan")):
            with pytest.raises(RuntimeError):
                edit(invalid)
            assert component.speed == 5.0
        manager.undo()
        assert component.speed == 1.0
        manager.redo()
        assert component.speed == 5.0
    finally:
        ComponentCommandService._instance = previous_service
        UndoManager._instance = previous


def test_python_multi_edit_respects_readonly_on_every_target():
    from infernux.components import InxComponent, serialized_field
    from infernux.engine.interaction import make_python_component_property_transaction

    class Writable(InxComponent):
        speed = serialized_field(1.0)

    class ReadOnly(InxComponent):
        speed = serialized_field(2.0, readonly=True)

    first, second = Writable(), ReadOnly()
    transaction = make_python_component_property_transaction((first, second), "speed")
    assert transaction.read_only
    assert transaction.commit(4.0).value == "rejected"
    assert (first.speed, second.speed) == (1.0, 2.0)


def test_multi_target_property_write_rolls_back_when_one_target_rejects():
    from infernux.engine.interaction import make_attribute_property_transaction
    from infernux.engine.undo import UndoManager

    class Probe:
        def __init__(self, value, reject=False):
            self._value = value
            self.reject = reject

        @property
        def value(self):
            return self._value

        @value.setter
        def value(self, candidate):
            if self.reject and candidate == 3.0:
                raise ValueError("rejected by target")
            self._value = candidate

    first = Probe(1.0)
    second = Probe(2.0, reject=True)
    previous = UndoManager._instance
    manager = UndoManager()
    try:
        transaction = make_attribute_property_transaction((first, second), "value", description="Set Value")
        with pytest.raises(RuntimeError, match="rejected"):
            transaction.commit_or_raise(3.0)

        assert (first.value, second.value) == (1.0, 2.0)
        assert len(manager.action_journal.applied_entries()) == 0
    finally:
        UndoManager._instance = previous
