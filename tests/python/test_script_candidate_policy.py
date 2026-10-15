from __future__ import annotations

import textwrap

import pytest

from infernux.engine.script_candidate_policy import analyze_script_candidate


def _report(source: str):
    return analyze_script_candidate(textwrap.dedent(source).encode("utf-8"), filename="candidate.py")


@pytest.mark.parametrize(
    "source",
    [
    "from infernux import *\n",
    "from numpy import dtype, asarray\nDEFAULT = asarray([1, 2], dtype=dtype('f4'))\n",
    "def update(self, delta_time):\n    open('out.txt', 'w')\n    import subprocess\n    subprocess.run(['tool'])\n",
    "class Component:\n    def update(self):\n        import os\n        os.environ['X'] = '1'\n",
],
)
def test_common_imports_declarations_and_function_bodies_are_not_blocked(source):
    report = _report(source)

    assert report.blocked == ()


def test_builtin_compile_call_is_not_confused_with_jit_declaration():
    report = _report("factory = compile('value = 1', '<generated>', 'exec')\n")
    assert len(report.blocked) == 1
    assert report.blocked[0].operation == "compile"


def test_annotated_field_markers_are_allowed_inside_component_declarations():
    report = _report(
        """
        from typing import Annotated
        import infernux as inx

        @inx.disallow_multiple
        @inx.require_component(inx.Rigidbody)
        class TargetReporter(inx.InxComponent):
            speed: Annotated[
                float,
                inx.components.Header("Movement"),
                inx.components.Range(0.0, 12.0),
                inx.components.Tooltip("Maximum movement speed."),
            ] = 3.0
            target: Annotated[
                inx.GameObject,
                inx.components.Header("References"),
                inx.components.RequiredComponent("MeshRenderer"),
            ]
        """
    )

    assert report.blocked == ()
    assert report.runtime_guard_required == ()


@pytest.mark.parametrize(
    ("source", "code", "operation"),
    [
    ("import helper\nhelper.VALUE = 2\n", "NX-R1-STATIC-MODULE-WRITE", "helper.VALUE"),
    ("from helper import VALUE as current\ncurrent += 1\n", "NX-R1-STATIC-MODULE-WRITE", "current"),
    ("import helper\ndel helper.VALUE\n", "NX-R1-STATIC-MODULE-WRITE", "helper.VALUE"),
    ("import os\nos.environ['MODE'] = 'x'\n", "NX-R1-STATIC-ENVIRONMENT-WRITE", "os.environ"),
    ("from os import environ as env\nenv['MODE'] = 'x'\n", "NX-R1-STATIC-ENVIRONMENT-WRITE", "env"),
    ("import sys\nsys.path.append('Assets')\n", "NX-R1-STATIC-MODULE-WRITE", "sys.path.append"),
    ("import sys\nsys.modules['helper'] = None\n", "NX-R1-STATIC-MODULE-WRITE", "sys.modules"),
    ("open('out.txt', 'wb')\n", "NX-R1-STATIC-FILE-WRITE", "open"),
    ("from io import open as write\nwrite('out.txt', mode='a')\n", "NX-R1-STATIC-FILE-WRITE", "open"),
    ("from pathlib import Path\nPath('out.txt').write_text('x')\n", "NX-R1-STATIC-FILE-WRITE", "Path.write_text"),
    ("from pathlib import Path\np = Path('out.txt')\np.unlink()\n", "NX-R1-STATIC-FILE-WRITE", "p.unlink"),
    ("import os\nos.remove('out.txt')\n", "NX-R1-STATIC-FILE-WRITE", "os.remove"),
    ("import os\nos.spawnv(os.P_WAIT, 'tool', ())\n", "NX-R1-STATIC-PROCESS", "os.spawnv"),
    ("import subprocess\nsubprocess.run(['tool'])\n", "NX-R1-STATIC-PROCESS", "subprocess.run"),
    ("from threading import Thread as Worker\nWorker(target=lambda: None)\n", "NX-R1-STATIC-PROCESS", "threading.Thread"),
    ("from importlib import reload as reload_module\nreload_module(helper)\n", "NX-R1-STATIC-DYNAMIC-CODE", "importlib.reload"),
    ("exec('value = 1')\n", "NX-R1-STATIC-DYNAMIC-CODE", "exec"),
],
)
def test_obvious_top_level_side_effects_are_blocked(source, code, operation):
    report = _report(source)

    assert report.is_blocked
    assert report.blocked[0].code == code
    assert report.blocked[0].operation == operation
    assert report.blocked[0].line >= 1
    assert report.blocked[0].column >= 0


@pytest.mark.parametrize("source", [
    "class Probe:\n    field = field_factory(default=make_value())\n",
    "import importlib\nmodule = importlib.import_module('helper')\n",
])
def test_ecosystem_declarations_need_no_class_or_call_allowlist(source):
    report = _report(source)
    assert not report.is_rejected
    assert report.runtime_guard_required == ()


@pytest.mark.parametrize("source", [
    "import subprocess\nVALUE = subprocess.list2cmdline(['hello world'])\n",
    "from threading import RLock\nLOCK = RLock()\n",
    "import socket\nVALUE = socket.inet_aton('127.0.0.1')\n",
])
def test_stdlib_declarations_are_not_rejected_by_module_name(source):
    assert not _report(source).is_rejected


@pytest.mark.parametrize("source", [
    "import asyncio\nasyncio.run(main())\n",
    "from socket import socketpair\nsocketpair()\n",
])
def test_explicit_external_stdlib_operations_remain_rejected(source):
    report = _report(source)
    assert report.is_rejected
    assert report.blocked[0].code == "NX-R1-STATIC-PROCESS"


@pytest.mark.parametrize("source", [
    "import numpy as np\nnp.save('state.npy', np.zeros(1))\n",
    "import numpy as np\nnp.seterr(all='ignore')\n",
])
def test_explicit_external_mutations_still_have_actionable_diagnostics(source):
    report = _report(source)
    assert report.is_blocked
    assert report.is_rejected
    assert report.runtime_guard_required == ()


def test_report_is_immutable_and_preserves_source_locations():
    report = _report("import helper\nhelper.VALUE = 2\n")

    with pytest.raises(AttributeError):
        report.blocked = ()
    assert report.blocked[0].line == 2
    assert report.blocked[0].column == 0
