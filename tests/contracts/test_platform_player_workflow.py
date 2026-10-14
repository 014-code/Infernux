from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import tempfile

import pytest

from tests.tool_discovery import find_executable
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "platform-player.yml"
ANDROID_DRIVER = ROOT / "tests" / "acceptance" / "android_emulator_ci.sh"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _android_driver_text() -> str:
    return ANDROID_DRIVER.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="Android CI driver runs on Linux")
@pytest.mark.parametrize("explicit", (False, True))
def test_android_driver_shares_tool_state_between_player_and_instrumentation(
    tmp_path, explicit
):
    driver = tmp_path / "android-driver.sh"
    driver.write_text(_android_driver_text(), encoding="utf-8")
    (tmp_path / "tests/fixtures/multiplatform_player").mkdir(parents=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    invocation_log = tmp_path / "invocations.txt"
    recorder = (
        '#!/bin/sh\n'
        'printf "%s|%s|%s\\n" "$0" "${GRADLE_USER_HOME:-}" '
        '"${ANDROID_USER_HOME:-}" >> "$PROBE_LOG"\n'
    )
    for name in ("python", "gradle"):
        executable = tools / name
        executable.write_text(recorder, encoding="utf-8")
        executable.chmod(0o755)
    environment = dict(os.environ)
    for name in ("GRADLE_USER_HOME", "ANDROID_USER_HOME"):
        environment.pop(name, None)
    environment.update(
        PATH=str(tools) + os.pathsep + environment["PATH"],
        PROBE_LOG=str(invocation_log),
        INFERNUX_ANDROID_RUNTIME=str(tmp_path / "runtime"),
        INFERNUX_ANDROID_BUILD_CACHE=str(tmp_path / "build-cache"),
    )
    expected_cache = str(tmp_path / "out/cache/gradle")
    expected_state = str(tmp_path / "out/state/android")
    if explicit:
        expected_cache = environment["GRADLE_USER_HOME"] = str(tmp_path / "author cache")
        expected_state = environment["ANDROID_USER_HOME"] = str(tmp_path / "author state")
    completed = subprocess.run(
        [find_executable("bash"), str(driver), str(tools / "python"), "build"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    invocations = [line.split("|") for line in invocation_log.read_text().splitlines()]
    assert [Path(row[0]).name for row in invocations] == ["python", "gradle", "python", "python"]
    assert [row[1:] for row in invocations] == [[expected_cache, expected_state]] * 4
