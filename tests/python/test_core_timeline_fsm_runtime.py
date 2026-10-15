"""Unit tests for TimelineFSMRuntime — the renderer-agnostic Timeline FSM player.

Exercises state entry, playback advance, looping vs play-once, exit-time gating,
parameter/trigger-driven transitions and timeline-end auto-advance. Runs with a
``transform=None`` target so it tests pure state-machine logic (no renderer).
"""
from __future__ import annotations

import uuid

import pytest

import infernux.core.timeline_fsm_runtime as timeline_runtime_module
from infernux.core.anim_state_machine import (
    AnimCondition,
    AnimParameter,
    AnimState,
    AnimStateMachine,
    AnimTransition,
)
from infernux.core.animation_timeline import AnimationTimeline, TimelineKeyframe
from infernux.core.timeline_fsm_runtime import TimelineFSMRuntime
from infernux.graph import TypeRef, ValueType


_TIMELINE_PATHS = {}


@pytest.fixture(autouse=True)
def _asset_database(monkeypatch):
    _TIMELINE_PATHS.clear()

    class Database:
        @staticmethod
        def get_path_from_guid(guid):
            return _TIMELINE_PATHS.get(guid, "")

    monkeypatch.setattr(timeline_runtime_module, "_get_asset_database", lambda: Database())


# ── helpers ─────────────────────────────────────────────────────────────────

def _timeline_file(tmp_path, name, duration=1.0):
    tl = AnimationTimeline(duration=duration, keyframes=[
        TimelineKeyframe(time=0.0, position=[0, 0, 0]),
        TimelineKeyframe(time=duration, position=[1, 0, 0]),
    ])
    path = str(tmp_path / f"{name}.animtimeline")
    tl.save(path)
    guid = uuid.uuid5(uuid.NAMESPACE_URL, path).hex
    _TIMELINE_PATHS[guid] = path
    return guid, path


def _state(tmp_path, name, *, loop=True, exit_time=1.0, dur=1.0, trans=None):
    s = AnimState(name=name, kind="timeline", loop=loop, exit_time_normalized=exit_time)
    s.timeline_guid, s.timeline_path = _timeline_file(tmp_path, name, dur)
    for target, cond in (trans or []):
        conditions = []
        if cond:
            parts = cond.split()
            parameter_name = parts[0]
            operator = parts[1] if len(parts) == 3 else "=="
            threshold = float(parts[2]) if len(parts) == 3 else 1.0
            conditions.append(
                AnimCondition(
                    parameter_id=f"parameter:{parameter_name}",
                    operator=operator,
                    threshold=threshold,
                )
            )
        s.transitions.append(AnimTransition(target_state=target, conditions=conditions))
    return s


def _fsm(states, default=None):
    fsm = AnimStateMachine(mode="timeline")
    fsm.states = states
    fsm.default_state = default or (states[0].name if states else "")
    parameter_ids = {
        condition.parameter_id
        for state in states
        for transition in state.transitions
        for condition in transition.conditions
    }
    fsm.parameters = [
        AnimParameter(stable_id=parameter_id, name=parameter_id.removeprefix("parameter:"))
        for parameter_id in sorted(parameter_ids)
    ]
    return fsm


# ── setup / parameters ──────────────────────────────────────────────────────


# ── play / state entry ──────────────────────────────────────────────────────


def test_runtime_play_unknown_state_false(tmp_path):
    rt = TimelineFSMRuntime()
    rt.set_fsm(_fsm([_state(tmp_path, "A")]))
    assert rt.play("ghost") is False


def test_runtime_rejects_timeline_state_without_guid(tmp_path):
    state = _state(tmp_path, "A")
    state.timeline_guid = ""
    rt = TimelineFSMRuntime()
    rt.set_fsm(_fsm([state]))

    with pytest.raises(ValueError, match="has no asset GUID"):
        rt.play()


def test_runtime_rejects_unregistered_timeline_guid(tmp_path):
    state = _state(tmp_path, "A")
    state.timeline_guid = "missing-guid"
    rt = TimelineFSMRuntime()
    rt.set_fsm(_fsm([state]))

    with pytest.raises(FileNotFoundError, match="missing-guid"):
        rt.play()


def test_runtime_play_no_default_false(tmp_path):
    fsm = _fsm([_state(tmp_path, "A")])
    fsm.default_state = ""
    rt = TimelineFSMRuntime()
    rt.set_fsm(fsm)
    assert rt.play() is False


# ── playback advance ────────────────────────────────────────────────────────


def test_first_native_handle_does_not_compare_against_none(tmp_path):
    class StrictHandle:
        def __eq__(self, other):
            if other is None:
                raise TypeError("native handle cannot compare with None")
            return self is other

    class Transform:
        def __init__(self):
            self.handle = StrictHandle()
            self.calls = []
            self.position = self.local_position = type("V", (), {"x": 0.0, "y": 0.0, "z": 0.0})()
            self.euler_angles = self.local_euler_angles = self.position
            self.local_scale = type("V", (), {"x": 1.0, "y": 1.0, "z": 1.0})()

        def set_local_trs(self, *values):
            self.calls.append(values)

    rt = TimelineFSMRuntime()
    rt.set_fsm(_fsm([_state(tmp_path, "A")]))
    transform = Transform()

    assert rt.play(transform=transform)
    rt.update(0.1, transform=transform)

    assert transform.calls


def test_transform_contract_failure_propagates(tmp_path):
    class InvalidTransform:
        handle = object()

    rt = TimelineFSMRuntime()
    rt.set_fsm(_fsm([_state(tmp_path, "A")]))

    with pytest.raises(AttributeError):
        rt.play(transform=InvalidTransform())


# ── transitions ─────────────────────────────────────────────────────────────


def test_runtime_trigger_consumed_after_transition(tmp_path):
    a = _state(tmp_path, "A", loop=True, exit_time=0.0, trans=[("B", "fire")])
    b = _state(tmp_path, "B")
    rt = TimelineFSMRuntime()
    rt.set_fsm(_fsm([a, b]))
    rt.play()
    rt.set_trigger("fire")
    rt.update(0.1, transform=None)
    assert rt.current_state == "B"
    assert rt.get_bool("fire") is False


def test_runtime_timeline_end_auto_advance(tmp_path):
    # Empty condition + non-looping → advance when the timeline finishes.
    a = _state(tmp_path, "A", loop=False, exit_time=1.0, dur=1.0, trans=[("B", "")])
    b = _state(tmp_path, "B")
    rt = TimelineFSMRuntime()
    rt.set_fsm(_fsm([a, b]))
    rt.play()
    rt.update(1.1, transform=None)
    assert rt.current_state == "B"


def test_runtime_float_condition_transition(tmp_path):
    a = _state(tmp_path, "A", loop=True, exit_time=0.0, trans=[("B", "speed > 0.5")])
    b = _state(tmp_path, "B")
    rt = TimelineFSMRuntime()
    rt.set_fsm(_fsm([a, b]))
    rt.play()
    rt.set_float("speed", 1.0)
    rt.update(0.1, transform=None)
    assert rt.current_state == "B"


def test_timeline_player_configures_plays_loops_and_resets(tmp_path):
    runtime = TimelineFSMRuntime()
    assert not runtime.play()
    assert runtime.get_parameter("missing", "fallback") == "fallback"
    fsm = _fsm([_state(tmp_path, "A", dur=2.0), _state(tmp_path, "B")])
    fsm.parameters = [
        AnimParameter(name="speed", value_type=TypeRef(ValueType.F32), default=2.0),
        AnimParameter(name="ready", value_type=TypeRef(ValueType.BOOL), default=True),
        AnimParameter(name="count", value_type=TypeRef(ValueType.I32), default=3),
    ]
    runtime.set_fsm(fsm)
    assert (runtime.get_float("speed"), runtime.get_bool("ready"), runtime.get_int("count")) == (2.0, True, 3)
    runtime.set_float("speed", 1.5)
    runtime.set_bool("ready", False)
    runtime.set_int("count", 4)
    runtime.set_trigger("fire")
    assert (runtime.get_float("speed"), runtime.get_bool("ready"), runtime.get_int("count")) == (1.5, False, 4)
    assert runtime.get_bool("fire")
    runtime.update(1.0, transform=None)
    assert runtime.current_state == ""
    assert runtime.play() and runtime.current_state == "A" and runtime.is_playing
    assert runtime.normalized_time == 0.0
    runtime.update(.5, transform=None)
    assert runtime.normalized_time == pytest.approx(.25)
    runtime.play("A")
    assert runtime.normalized_time == pytest.approx(.25)
    runtime.playback_speed = 2.0
    runtime.update(1.25, transform=None)
    assert runtime.is_playing and runtime.normalized_time == pytest.approx(.5)
    assert runtime.play("B") and runtime.current_state == "B"
    assert runtime.normalized_time == 0.0
    runtime.stop()
    runtime.update(1.0, transform=None)
    assert not runtime.is_playing and runtime.normalized_time == 0.0
    runtime.set_fsm(None)
    assert runtime.fsm is None and not runtime.is_playing and runtime.current_state == ""


def test_non_looping_timeline_stops_at_its_clamped_end(tmp_path):
    runtime = TimelineFSMRuntime()
    runtime.set_fsm(_fsm([_state(tmp_path, "A", loop=False)]))
    runtime.play()
    runtime.update(1.5, transform=None)
    assert not runtime.is_playing and runtime.normalized_time == 1.0
    runtime.update(5.0, transform=None)
    assert not runtime.is_playing and runtime.normalized_time == 1.0


def test_condition_transition_waits_for_parameter_and_exit_time(tmp_path):
    a = _state(tmp_path, "A", exit_time=.6, trans=[("B", "go")])
    runtime = TimelineFSMRuntime()
    runtime.set_fsm(_fsm([a, _state(tmp_path, "B")]))
    runtime.play()
    runtime.update(.2, transform=None)
    assert runtime.current_state == "A"
    runtime.set_bool("go", True)
    runtime.update(.2, transform=None)
    assert runtime.current_state == "A"
    runtime.update(.3, transform=None)
    assert runtime.current_state == "B"
