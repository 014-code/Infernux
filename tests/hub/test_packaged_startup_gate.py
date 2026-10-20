"""Release qualification must execute the Hub, not only inspect its payload."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import build_hub


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_hub_startup", ROOT / "scripts/release/verify_hub_startup.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


@pytest.mark.parametrize("compiler,qt", [("4.3", "6.11.2"), ("4.2", "6.12.0")])
def test_unqualified_compiler_or_qt_pair_is_rejected(monkeypatch, compiler, qt):
    monkeypatch.setattr(build_hub.importlib.metadata, "version", lambda name: {
        "nuitka": compiler, "PySide6": qt,
    }[name])
    with pytest.raises(RuntimeError, match="hub-build-requirements.txt"):
        build_hub._validate_hub_toolchain()


@pytest.mark.parametrize("generator,explicit_instance", [
    ("Visual Studio 17 2022", False), ("Visual Studio 18 2026", True),
])
def test_msvc_environment_keeps_unicode_and_single_windows_key_identity(tmp_path, monkeypatch, generator, explicit_instance):
    vs = tmp_path / "Visual Studio"
    for relative in ("VC/Auxiliary/Build/vcvars64.bat", "MSBuild/Current/Bin/MSBuild.exe"):
        path = vs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    if explicit_instance:
        monkeypatch.setenv("CMAKE_GENERATOR_INSTANCE", str(vs))
    else:
        monkeypatch.delenv("CMAKE_GENERATOR_INSTANCE", raising=False)
        monkeypatch.setattr(build_hub, "_find_visual_studio", lambda _: vs)
    monkeypatch.setattr(build_hub.shutil, "which", lambda name, **_: str(vs / name))
    monkeypatch.setenv("LOCALAPPDATA", "previous")
    monkeypatch.setenv("SYSTEMROOT", "previous")

    def capture(command, **kwargs):
        assert "cmd.exe /u /d /c" in command
        assert kwargs["encoding"] == "utf-16-le"
        assert "errors" not in kwargs
        return SimpleNamespace(stdout="LocalAppData=C:\\Users\\测试用户\\AppData\\Local\nSystemRoot=C:\\Windows\nPath=toolchain\n")

    monkeypatch.setattr(build_hub.subprocess, "run", capture)
    env, _ = build_hub._msvc_build_environment(generator)
    assert env["LOCALAPPDATA"] == "C:\\Users\\测试用户\\AppData\\Local"
    assert env["SYSTEMROOT"] == "C:\\Windows"
    assert "SystemRoot" not in env and "LocalAppData" not in env


@pytest.mark.parametrize("exit_code,receipt", [
    (3, {"version": "0.4.2-2", "pages": [0, 1, 2, 3], "error": ""}),
    (0, {"version": "0.4.1-3", "pages": [0, 1, 2, 3], "error": ""}),
    (0, {"version": "0.4.2-2", "pages": [0], "error": ""}),
])
def test_gate_rejects_crash_wrong_binary_and_incomplete_startup(tmp_path, monkeypatch, exit_code, receipt):
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / ("Infernux Hub.exe" if os.name == "nt" else "Infernux Hub")).touch()

    def run(command, *, cwd, env, **kwargs):
        assert command[-1] == "--smoke-test"
        assert "PYTHONPATH" not in env and "QT_PLUGIN_PATH" not in env
        data = Path(env["INFERNUX_DATA_ROOT"])
        assert data.is_relative_to(cwd)
        logs = data / "Logs"
        logs.mkdir(parents=True)
        (logs / "hub-native-crash.log").write_text("crash evidence")
        (data / "startup-probe.json").write_text(json.dumps(receipt))
        return subprocess.CompletedProcess(command, exit_code, b"stdout", b"stderr")

    monkeypatch.setattr(gate.subprocess, "run", run)
    output = tmp_path / "evidence"
    with pytest.raises(RuntimeError):
        gate.verify_startup(hub, "0.4.2-2", output=output)
    assert (output / "hub-native-crash.log").read_text() == "crash evidence"


def test_probe_exercises_real_window_pages_and_clean_teardown(tmp_path):
    env = dict(os.environ)
    env.update(
        QT_QPA_PLATFORM="offscreen", INFERNUX_DATA_ROOT=str(tmp_path / "user"),
        INFERNUX_SHARED_DATA_ROOT=str(tmp_path / "shared"),
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "packaging/launcher.py"), "--smoke-test"],
        cwd=tmp_path, env=env, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((tmp_path / "user/startup-probe.json").read_text())
    assert receipt["pages"] == [0, 1, 2, 3]
    assert receipt["error"] == ""
