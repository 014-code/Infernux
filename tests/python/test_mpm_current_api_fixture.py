"""Acceptance checks for the deterministic MPM-style JIT fixture."""

import importlib.util
from pathlib import Path

import numpy as np


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tests/fixtures/mpm_current_api/Assets/Scripts/MpmJitDemo.py"


def _load_demo():
    spec = importlib.util.spec_from_file_location("infernux_mpm_jit_demo", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_material_kernels_warmup_without_mutation_and_run_parallel():
    demo = _load_demo()
    result = demo.run_material_contract(sample_count=128)

    assert result["mode"] == "parallel"
    assert result["sample_count"] == 128
    assert result["specializations"] >= 3


def test_material_kernels_keep_positions_finite_and_separate():
    demo = _load_demo()
    position, velocity, rest = demo.create_state(32)
    original = position.copy()
    rest_original = rest.copy()

    demo.advance_snow(position, velocity, 1.0 / 60.0, 9.81, 1.0, 0.35)

    assert np.isfinite(position).all()
    assert np.isfinite(velocity).all()
    assert not np.array_equal(position, original)
    np.testing.assert_array_equal(rest, rest_original)
