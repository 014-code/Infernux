"""Tests for infernux.components.builtin.rigidbody — Rigidbody wrapper (real C++ backend)."""

from __future__ import annotations

import pytest

from infernux.lib import ForceMode, SceneManager, quatf
from infernux.components.builtin.rigidbody import (
    CollisionDetectionMode,
    Rigidbody,
    RigidbodyConstraints,
    RigidbodyInterpolation,
)


class TestCollisionDetectionModeMapping:
    def test_unsupported_mode_is_rejected(self, cpp_rigidbody):
        with pytest.raises(ValueError):
            cpp_rigidbody.collision_detection_mode = 3


class TestPropertyRoundTrip:
    def test_enum_properties_round_trip(self, cpp_rigidbody):
        rb = cpp_rigidbody
        native = rb._cpp_component

        rb.collision_detection_mode = CollisionDetectionMode.ContinuousDynamic
        rb.interpolation = RigidbodyInterpolation.None_

        assert native.collision_detection_mode == int(CollisionDetectionMode.ContinuousDynamic)
        assert native.interpolation == int(RigidbodyInterpolation.None_)

        native.collision_detection_mode = 2
        native.interpolation = 1
        assert rb.collision_detection_mode is CollisionDetectionMode.ContinuousDynamic
        assert rb.interpolation is RigidbodyInterpolation.Interpolate


class TestConstraintsHelpers:
    def test_add_remove_has(self, cpp_rigidbody):
        rb = cpp_rigidbody

        rb.constraints_flags = (
            RigidbodyConstraints.FreezePositionX | RigidbodyConstraints.FreezeRotationY
        )
        assert rb.has_constraint(RigidbodyConstraints.FreezePositionX)
        assert rb.has_constraint(RigidbodyConstraints.FreezeRotationY)
        assert not rb.has_constraint(RigidbodyConstraints.FreezePositionZ)

        rb.add_constraint(RigidbodyConstraints.FreezeRotationZ)
        assert rb.has_constraint(RigidbodyConstraints.FreezeRotationZ)

        rb.remove_constraint(RigidbodyConstraints.FreezePositionX)
        assert not rb.has_constraint(RigidbodyConstraints.FreezePositionX)

    def test_freeze_rotation(self, cpp_rigidbody):
        rb = cpp_rigidbody
        assert rb.freeze_rotation is False
        rb.freeze_rotation = True
        assert rb._cpp_component.freeze_rotation is True


class TestVelocityAndForce:
    def test_bound_wrapper_configures_moves_and_sleeps_a_dynamic_body(self, scene):
        owner = scene.create_game_object("Wrapper dynamics")
        owner.add_component("BoxCollider")
        rb = owner.add_component(Rigidbody)
        native = rb._cpp_component
        rb.mass = 2.0
        rb.drag = rb.angular_drag = 0.0
        rb.use_gravity = False
        assert native.mass == pytest.approx(2.0)
        assert native.drag == native.angular_drag == 0.0
        assert native.use_gravity is False

        manager = SceneManager.instance()
        manager.play()
        manager.pause()
        manager.step()
        rb.velocity = (1, 2, 3)
        rb.angular_velocity = (0, 0, 0)
        assert tuple(native.velocity) == pytest.approx((1, 2, 3))
        assert tuple(native.angular_velocity) == pytest.approx((0, 0, 0))
        rb.add_force((2, 0, 0))
        rb.add_torque((0, 0, 1), ForceMode.VelocityChange)
        rb.add_force_at_position((0, 2, 0), tuple(rb.world_center_of_mass), ForceMode.Impulse)
        manager.step()
        assert tuple(native.velocity) == pytest.approx((1 + manager.get_fixed_time_step(), 3, 3), abs=1e-4)
        assert tuple(native.angular_velocity) == pytest.approx((0, 0, 1), abs=1e-4)
        assert tuple(rb.position) == pytest.approx(tuple(native.position))
        assert tuple(rb.rotation) == pytest.approx(tuple(native.rotation))

        rb.velocity = rb.angular_velocity = (0, 0, 0)
        rb.sleep()
        assert native.is_sleeping() and rb.is_sleeping()
        rb.wake_up()
        assert not native.is_sleeping() and not rb.is_sleeping()

    def test_kinematic_moves_reject_dynamic_rigidbody(self, cpp_rigidbody):
        rb = cpp_rigidbody
        with pytest.raises(RuntimeError, match="requires a kinematic Rigidbody"):
            rb.move_position((3, 2, 1))
        with pytest.raises(RuntimeError, match="requires a kinematic Rigidbody"):
            rb.move_rotation(quatf(0.0, 0.0, 0.0, 1.0))


class TestDetachedWrapperFailures:
    def test_native_state_access_requires_binding(self):
        rb = Rigidbody()
        operations = (
            lambda: rb.mass,
            lambda: setattr(rb, "mass", 2.0),
            lambda: rb.freeze_rotation,
            lambda: rb.is_sleeping(),
            lambda: rb.rotation,
            lambda: rb.add_force((1, 2, 3)),
            lambda: rb.add_torque((1, 2, 3)),
            lambda: rb.add_force_at_position((1, 2, 3), (0, 0, 0)),
            lambda: rb.move_position((1, 2, 3)),
            lambda: rb.move_rotation((0.0, 0.0, 0.0, 1.0)),
            lambda: rb.wake_up(),
            lambda: rb.sleep(),
        )
        for operation in operations:
            with pytest.raises(ReferenceError):
                operation()
