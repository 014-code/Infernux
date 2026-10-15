"""Public numeric utilities and native vector value boundaries."""
import math

import pytest

from infernux.mathf import Mathf
from infernux.lib import Vector2, Vector3, quatf, vec4f


@pytest.mark.parametrize('value,expected', [
    (Vector2(1, 2), (1., 2.)), (Vector3(1, 2, 3), (1., 2., 3.)),
    (vec4f(1, 2, 3, 4), (1., 2., 3., 4.)), (quatf(1, 2, 3, 4), (1., 2., 3., 4.)),
])
def test_native_math_values_are_independent_iterables(value, expected):
    assert tuple(value) == expected


@pytest.mark.parametrize('constructor,coordinates', [
    (Vector2, (1, 2)), (Vector3, (1, 2, 3)), (vec4f, (1, 2, 3, 4)),
])
def test_native_vectors_reject_nonfinite_construction_and_mutation(constructor, coordinates):
    with pytest.raises(ValueError, match='finite'):
        constructor(float('nan'), *coordinates[1:])
    value = constructor(*coordinates)
    with pytest.raises(ValueError, match='finite'):
        value.x = float('inf')
    assert tuple(value) == coordinates


def test_smooth_damp_rejects_invalid_timing():
    with pytest.raises(ValueError, match='smooth_time'):
        Vector3.smooth_damp(Vector3(), Vector3(1, 0, 0), Vector3(), 0., float('inf'), .02)


def test_scalar_interpolation_clamps_and_preserves_endpoints():
    assert Mathf.clamp(-1, 0, 10) == 0
    assert Mathf.clamp(15, 0, 10) == 10
    assert Mathf.clamp01(.5) == .5
    assert Mathf.lerp(0, 10, -1) == 0
    assert Mathf.lerp(0, 10, 2) == 10
    position = Mathf.lerp(0, 10, .5)
    assert position == 5
    assert Mathf.inverse_lerp(0, 10, position) == .5
    assert Mathf.inverse_lerp(5, 5, 5) == 0
    assert Mathf.lerp_unclamped(0, 10, 2) == 20
    assert Mathf.move_towards(0, 10, 3) == 3
    assert Mathf.move_towards(0, 10, 100) == 10
    assert Mathf.smooth_step(0, 1, 0) == 0
    assert Mathf.smooth_step(0, 1, .25) < Mathf.smooth_step(0, 1, .75)
    assert Mathf.smooth_step(0, 1, 1) == 1


def test_scalar_damping_converges_with_velocity_feedback():
    position, velocity = Mathf.smooth_damp(0, 10, 0, .3, delta_time=.016)
    assert position > 0 and velocity > 0
    for _ in range(300):
        position, velocity = Mathf.smooth_damp(position, 10, velocity, .3, delta_time=.016)
    assert position == pytest.approx(10, abs=.01)


def test_rotation_units_and_shortest_arc():
    assert Mathf.TAU == pytest.approx(2 * Mathf.PI)
    assert 180 * Mathf.Deg2Rad == pytest.approx(math.pi)
    assert math.pi * Mathf.Rad2Deg == pytest.approx(180)
    assert Mathf.delta_angle(350, 10) == pytest.approx(20)
    assert Mathf.delta_angle(10, 350) == pytest.approx(-20)
    assert Mathf.lerp_angle(350, 10, .5) == pytest.approx(360)
    assert Mathf.move_towards_angle(0, 90, 30) == pytest.approx(30)


def test_repeating_motion_and_numeric_domain_boundaries():
    assert Mathf.repeat(3.5, 2) == pytest.approx(1.5)
    assert Mathf.repeat(5, 0) == 0
    assert Mathf.ping_pong(3, 2) == 1
    assert Mathf.sqrt(-1) == 0
    assert Mathf.log(0) == Mathf.NegativeInfinity
    assert Mathf.log10(0) == -Mathf.Infinity
    assert Mathf.approximately(1, 1 + 1e-8)
    assert not Mathf.approximately(1, 2)
    assert Mathf.sign(0) == 1


def test_integer_capacity_helpers():
    assert Mathf.is_power_of_two(4) and not Mathf.is_power_of_two(3)
    assert not Mathf.is_power_of_two(0)
    assert Mathf.next_power_of_two(5) == 8
    assert Mathf.next_power_of_two(0) == 1
    assert Mathf.closest_power_of_two(5) == 4
    assert Mathf.floor_to_int(1.9) == 1
    assert Mathf.ceil_to_int(1.1) == 2
    assert Mathf.round_to_int(2.7) == 3
