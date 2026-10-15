"""Tests for infernux.input — Input class, KeyCode constants, focus gating (real C++ backend)."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

import infernux.input as input_module
from infernux.input import AccelerationEvent, Input, KeyCode, Touch, TouchPhase
from infernux.lib import InputManager
from infernux.runtime_services import install_runtime_service, remove_runtime_service


def test_visibility_confinement_and_lock_are_independent():
    Input.set_cursor_visible(False)
    Input.set_cursor_confined(True)
    Input.set_cursor_locked(True)
    try:
        assert Input.is_cursor_visible() is False
        assert Input.is_cursor_confined() is True
        assert Input.is_cursor_locked() is True

        Input.set_cursor_locked(False)
        assert Input.is_cursor_visible() is False
        assert Input.is_cursor_confined() is True
        assert Input.warp_cursor(10.0, 20.0) is False
    finally:
        Input.set_cursor_locked(False)
        Input.set_cursor_confined(False)
        Input.set_cursor_visible(True)


def test_touch_public_types():
    touch = Touch(
        touch_id=1,
        finger_id=2,
        timestamp_ns=3,
        window_id=4,
        position=(480.0, 270.0),
        raw_position=(480.0, 270.0),
        delta_position=(12.0, -8.0),
        normalized_position=(0.25, 0.25),
        normalized_delta_position=(0.01, -0.01),
        delta_time=1.0 / 60.0,
        pressure=0.5,
        contact_size=(0.02, 0.03),
        is_primary=True,
        began_this_frame=True,
        begin_normalized_position=(0.2, 0.2),
        cancel_reason="",
        phase=TouchPhase.MOVED,
    )
    assert touch.phase is TouchPhase.MOVED
    assert touch.began_this_frame
    assert touch.begin_normalized_position == pytest.approx((0.2, 0.2))
    assert touch.position == (480.0, 270.0)
    assert touch.normalized_position == (0.25, 0.25)
    assert touch.contact_size == (0.02, 0.03)
    assert touch.is_primary


def test_motion_sensor_properties_publish_native_samples(monkeypatch):
    manager = SimpleNamespace(
        accelerometer_supported=True,
        gyroscope_supported=True,
        acceleration=(0.25, -0.5, 1.0),
        gyroscope_rotation_rate=(0.1, 0.2, -0.3),
        acceleration_events=(
            SimpleNamespace(acceleration=(0.0, 1.0, 0.0), delta_time=0.0),
            SimpleNamespace(acceleration=(0.1, 0.9, 0.0), delta_time=0.02),
        ),
    )
    monkeypatch.setattr(
        input_module,
        "_NativeInputManager",
        SimpleNamespace(instance=lambda: manager),
    )

    assert Input.accelerometer_supported
    assert Input.gyroscope_supported
    assert Input.acceleration == pytest.approx((0.25, -0.5, 1.0))
    assert Input.gyroscope_rotation_rate == pytest.approx((0.1, 0.2, -0.3))
    assert Input.acceleration_event_count == 2
    assert Input.acceleration_events == (
        AccelerationEvent((0.0, 1.0, 0.0), 0.0),
        AccelerationEvent((0.1, 0.9, 0.0), 0.02),
    )


def test_native_touch_is_published_in_unity_screen_pixels(monkeypatch):
    native_touch = SimpleNamespace(
        touch_id=7,
        finger_id=11,
        timestamp_ns=2_000_000_000,
        window_id=3,
        x=0.25,
        y=0.75,
        delta_x=0.1,
        delta_y=-0.2,
        delta_time=0.016,
        pressure=0.6,
        contact_width=0.02,
        contact_height=0.03,
        is_primary=True,
        began_this_frame=True,
        begin_x=0.2,
        begin_y=0.8,
        cancel_reason="",
        phase="moved",
    )
    manager = SimpleNamespace(
        screen_state=SimpleNamespace(logical_width=1920, logical_height=1080)
    )
    monkeypatch.setattr(
        input_module,
        "_NativeInputManager",
        SimpleNamespace(instance=lambda: manager),
    )

    touch = Input._from_native_touch(native_touch)

    assert touch.position == pytest.approx((480.0, 270.0))
    assert touch.raw_position == touch.position
    assert touch.delta_position == pytest.approx((192.0, 216.0))
    assert touch.normalized_position == pytest.approx((0.25, 0.25))
    assert touch.normalized_delta_position == pytest.approx((0.1, 0.2))
    assert touch.contact_size == pytest.approx((38.4, 32.4))
    assert touch.delta_time == pytest.approx(0.016)
    assert touch.phase is TouchPhase.MOVED
    assert touch.began_this_frame
    assert touch.begin_normalized_position == pytest.approx((0.2, 0.2))


def test_platform_text_input_runtime_service():
    class _TextInputService:
        def __init__(self):
            self.active = False
            self.request = None

        def begin_text_input(self, initial_value, input_type):
            self.request = (initial_value, input_type)
            self.active = True
            return True

        def end_text_input(self):
            self.active = False

        def is_text_input_active(self):
            return self.active

    service = _TextInputService()
    install_runtime_service("text-input", service)
    try:
        assert Input.begin_text_input("hello", "email")
        assert service.request == ("hello", "email")
        assert Input.is_text_input_active()
        Input.end_text_input()
        assert not Input.is_text_input_active()
    finally:
        remove_runtime_service("text-input", service)


def test_scancode_names_match_public_keys():
    expected = {
        "space": KeyCode.SPACE, "escape": KeyCode.ESCAPE, "a": KeyCode.A,
        "z": KeyCode.Z, "left": KeyCode.LEFT_ARROW, "right": KeyCode.RIGHT_ARROW,
        "up": KeyCode.UP_ARROW, "down": KeyCode.DOWN_ARROW, "f1": KeyCode.F1,
        "f12": KeyCode.F12,
    }
    assert {name: InputManager.name_to_scancode(name) for name in expected} == expected
    assert (KeyCode.SPACE, KeyCode.ESCAPE, KeyCode.A, KeyCode.Z) == (44, 41, 4, 29)
    assert Input._resolve_key(44) == 44
    assert Input._resolve_key("space") == 44
    assert Input._resolve_key("nonexistent_key") == -1


def test_focus_and_viewport_gate_idle_keyboard_mouse_text_and_touch_queries():
    Input.set_game_focused(True)
    Input.set_game_viewport_origin(100, 200)
    Input.set_game_viewport_size(640, 360)
    assert Input.is_game_focused()
    assert Input.game_viewport_size == (640, 360)
    assert not Input.get_key("w") and not Input.get_key_down(KeyCode.SPACE)
    assert not Input.get_key_up(KeyCode.SPACE) and not Input.any_key
    assert not any(Input.get_mouse_button(button) for button in (0, 1, 2))
    x, y, *_ = Input.get_game_mouse_frame_state(0)
    assert (x, y) == pytest.approx((-100, -200))
    assert isinstance(Input.mouse_position, tuple) and len(Input.mouse_position) == 2
    assert isinstance(Input.input_string, str)
    assert [Input.get_axis(axis) for axis in ("Horizontal", "Vertical", "Mouse X", "Unknown")] == [0] * 4

    Input.set_game_focused(False)
    assert not Input.is_game_focused()
    assert not Input.get_key(KeyCode.W) and not Input.get_mouse_button(0)
    assert Input.get_mouse_frame_state(0) == (0, 0, 0, 0, False, False, False)
    assert Input.mouse_scroll_delta == (0, 0)
    assert not Input.any_key and not Input.any_key_down and Input.input_string == ""
    assert Input.touch_count == 0 and Input.touches == ()
    assert Input.get_axis("Horizontal") == 0


def test_nested_automation_input_does_not_claim_focus_and_cleans_up():
    Input.set_game_focused(False)
    Input._begin_automation_game_input()
    try:
        assert Input._accepts_game_input() and not Input.is_game_focused()
        Input._begin_automation_game_input()
        Input._end_automation_game_input()
        assert Input._accepts_game_input()
    finally:
        Input._end_automation_game_input()
    Input._end_automation_game_input()
    assert not Input._accepts_game_input() and not Input.is_game_focused()
    assert Input._automation_game_input_depth == 0
