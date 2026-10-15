import pytest

from tests.acceptance.line_bounds import verify_line_bounds


@pytest.mark.parametrize('style,space,shape', [
    ('constant', 'world', 'plain'), ('broad', 'local', 'rounded'),
    ('narrow', 'world', 'plain'), ('overshoot', 'affine', 'rounded'),
    ('repeat', 'local', 'loop'), ('ping_pong', 'affine', 'loop'),
    ('single', 'world', 'plain'), ('zero', 'affine', 'plain'),
    ('narrow', 'affine', 'rounded'), ('overshoot', 'world', 'loop'),
])
def test_authored_ribbon_vertices_fit_world_bounds(scene, style, space, shape):
    verify_line_bounds(scene, style, space, shape)
