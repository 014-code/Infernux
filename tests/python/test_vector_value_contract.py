"""Mutable vector value semantics and the public axis-bearing angle API."""
from collections.abc import Hashable

import pytest

from infernux import Vector2, Vector3, vec4f, vector3


@pytest.mark.parametrize('ctor,size', [(Vector2, 2), (Vector3, 3), (vec4f, 4)])
def test_mutable_approximately_equal_vectors_are_unhashable(ctor, size):
    a, b = ctor(*([1.] * size)), ctor(*([1.0000005] * size))
    assert a == b and not (a != b)
    with pytest.raises(TypeError, match='unhashable'):
        hash(a)
    assert not isinstance(a, Hashable)
    assert type(a).__hash__ is None


@pytest.mark.parametrize('ctor,size', [(Vector2, 2), (Vector3, 3), (vec4f, 4)])
def test_vector_can_be_snapshotted_as_an_immutable_exact_key(ctor, size):
    value = ctor(*([1.] * size))
    key = tuple(value)
    mapping = {key: 'original'}
    value.x = 2
    assert mapping[key] == 'original'
    assert tuple(value) not in mapping
    assert value != ctor(*key)


@pytest.mark.parametrize('api,a,b,axis,scale', [
    (Vector3, (1, 0, 0), (0, 1, 0), (0, 0, 1), 7.),
    (vector3, (0, 1, 0), (0, 0, 1), (1, 0, 0), -7.),
    (Vector3, (0, 0, 1), (1, 0, 0), (0, 1, 0), -1.),
    (vector3, (1, 0, 0), (0, 1, 0), (0, 0, 1), 1.),
])
def test_signed_angle_uses_the_authored_axis(api, a, b, axis, scale):
    a, b = Vector3(*a), Vector3(*b)
    axis = Vector3(*(coordinate * scale for coordinate in axis))
    expected = 90. if scale > 0 else -90.
    assert api.signed_angle(a, b, axis) == pytest.approx(expected)
    assert api.signed_angle(b, a, axis) == pytest.approx(-expected)
    assert api.angle(a, b) == pytest.approx(90.)


@pytest.mark.parametrize('a,b,axis,expected', [
    ((0, 0, 0), (0, 1, 0), (1, 0, 0), 0.),
    ((2, 0, 0), (-5, 0, 0), (0, 0, 1), 180.),
    ((2, 0, 0), (0, 5, 0), (0, 0, 0), 90.),
])
def test_signed_angle_degenerate_orientation_retains_unsigned_angle(a, b, axis, expected):
    assert Vector3.signed_angle(Vector3(*a), Vector3(*b), Vector3(*axis)) == pytest.approx(expected)


@pytest.mark.parametrize('scale,sign', [(1e-30, 1.), (1e30, -1.)])
def test_angles_are_invariant_under_finite_vector_scaling(scale, sign):
    a, b, axis = Vector3(scale, 0, 0), Vector3(0, scale, 0), Vector3(0, 0, sign * scale)
    assert Vector3.angle(a, b) == pytest.approx(90.)
    assert Vector3.signed_angle(a, b, axis) == pytest.approx(sign * 90.)
