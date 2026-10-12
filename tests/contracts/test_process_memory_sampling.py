"""The leak sampler must continue to count allocations that remain alive."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path


def test_live_allocations_remain_visible_after_allocator_reclamation():
    # An isolated process prevents earlier tests' spare heap capacity from
    # satisfying this allocation without any growth in resident pages.
    path = Path(__file__).resolve().parents[1] / "acceptance/_process_memory.py"
    size = 8 * 1024 * 1024
    script = textwrap.dedent('''
        import ctypes
        import importlib.util
        import json
        import sys

        spec = importlib.util.spec_from_file_location("residency_memory", sys.argv[1])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        before = module.process_memory_bytes()
        allocation = (ctypes.c_ubyte * (8 * 1024 * 1024))()
        for offset in range(0, len(allocation), 4096):
            allocation[offset] = 1
        after = module.process_memory_bytes()
        assert allocation[0] == 1
        print(json.dumps({"before": before, "after": after}))
    ''')
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    values = json.loads(result.stdout)
    for before, after in zip(values["before"], values["after"], strict=True):
        assert after >= before + size - 512 * 1024
