"""Deterministic MPM-style material kernels for the current JIT authoring API.

The fixture deliberately keeps the three material laws separate.  This makes
the parallel contract explicit and prevents a runtime material switch from
introducing a string or object branch into a compiled kernel.
"""

import numpy as np

import infernux as inx


@inx.jit.compile(parallel_policy="required")
def advance_snow(position, velocity, delta_time, gravity, floor_y, damping):
    for index in range(len(position)):
        velocity[index, 1] -= gravity * delta_time
        position[index, 0] += velocity[index, 0] * delta_time
        position[index, 1] += velocity[index, 1] * delta_time
        position[index, 2] += velocity[index, 2] * delta_time
        if position[index, 1] < floor_y:
            position[index, 1] = floor_y
            velocity[index, 1] = -velocity[index, 1] * damping


@inx.jit.compile(parallel_policy="required")
def advance_fluid(position, velocity, delta_time, gravity, floor_y, viscosity):
    for index in range(len(position)):
        velocity[index, 1] -= gravity * delta_time
        velocity[index, 0] *= viscosity
        velocity[index, 2] *= viscosity
        position[index, 0] += velocity[index, 0] * delta_time
        position[index, 1] += velocity[index, 1] * delta_time
        position[index, 2] += velocity[index, 2] * delta_time
        if position[index, 1] < floor_y:
            position[index, 1] = floor_y
            velocity[index, 1] = 0.0


@inx.jit.compile(parallel_policy="required")
def advance_viscoelastic(position, velocity, rest_position, delta_time, gravity, stiffness):
    for index in range(len(position)):
        velocity[index, 0] += (rest_position[index, 0] - position[index, 0]) * stiffness * delta_time
        velocity[index, 1] += (rest_position[index, 1] - position[index, 1]) * stiffness * delta_time
        velocity[index, 2] += (rest_position[index, 2] - position[index, 2]) * stiffness * delta_time
        velocity[index, 1] -= gravity * delta_time
        position[index, 0] += velocity[index, 0] * delta_time
        position[index, 1] += velocity[index, 1] * delta_time
        position[index, 2] += velocity[index, 2] * delta_time


def create_state(sample_count=256):
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    indices = np.arange(sample_count, dtype=np.float32)
    position = np.empty((sample_count, 3), dtype=np.float32)
    position[:, 0] = (indices % 16.0) * 0.08
    position[:, 1] = 1.05 + (indices // 16.0) * 0.03
    position[:, 2] = (indices % 8.0) * 0.07
    velocity = np.zeros_like(position)
    velocity[:, 0] = 0.15
    velocity[:, 1] = 0.4
    velocity[:, 2] = -0.08
    rest_position = position.copy()
    rest_position[:, 1] += 0.02 * ((indices % 5.0) - 2.0)
    return position, velocity, rest_position


def run_material_contract(sample_count=256):
    snow_position, snow_velocity, _ = create_state(sample_count)
    fluid_position, fluid_velocity, _ = create_state(sample_count)
    gel_position, gel_velocity, gel_rest = create_state(sample_count)

    for function, args in (
        (advance_snow, (snow_position, snow_velocity, 1.0 / 60.0, 9.81, 1.0, 0.35)),
        (advance_fluid, (fluid_position, fluid_velocity, 1.0 / 60.0, 9.81, 1.0, 0.94)),
        (advance_viscoelastic, (gel_position, gel_velocity, gel_rest, 1.0 / 60.0, 9.81, 7.5)),
    ):
        before = tuple(value.copy() for value in args if isinstance(value, np.ndarray))
        inx.jit.warmup(function, *args)
        after = tuple(value for value in args if isinstance(value, np.ndarray))
        for expected, actual in zip(before, after):
            np.testing.assert_array_equal(expected, actual)
        function(*args)
        assert function.selected_mode == "parallel"
        assert inx.jit.statistics(function).specializations

    if not np.isfinite(snow_position).all():
        raise RuntimeError("snow kernel produced a non-finite position")
    if not np.isfinite(fluid_position).all():
        raise RuntimeError("fluid kernel produced a non-finite position")
    if not np.isfinite(gel_position).all():
        raise RuntimeError("viscoelastic kernel produced a non-finite position")
    return {
        "mode": "parallel",
        "sample_count": int(sample_count),
        "specializations": sum(
            len(inx.jit.statistics(function).specializations)
            for function in (advance_snow, advance_fluid, advance_viscoelastic)
        ),
    }


class MpmJitDemo(inx.InxComponent):
    """Play-mode entry point for the fixture's material contract."""

    sample_count: int = 256

    def start(self):
        result = run_material_contract(self.sample_count)
        inx.Debug.log(
            "INFERNUX_MPM_JIT_DEMO_READY "
            f"mode={result['mode']} samples={result['sample_count']} "
            f"specializations={result['specializations']}",
            self,
        )
