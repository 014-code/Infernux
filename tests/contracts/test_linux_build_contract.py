import json
from pathlib import Path
import shlex

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_linux_presets_use_the_system_pkg_config() -> None:
    presets = json.loads(
        (ROOT / "cmake/presets/Linux.json").read_text(encoding="utf-8")
    )
    base = next(
        preset
        for preset in presets["configurePresets"]
        if preset["name"] == "linux-base"
    )

    assert base["cacheVariables"]["PKG_CONFIG_EXECUTABLE"] == "/usr/bin/pkg-config"


def test_linux_ci_reuses_the_repository_dependency_installer() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    linux_job = workflow["jobs"]["linux-desktop"]
    steps = linux_job["steps"]
    commands = [step.get("run", "") for step in steps]

    assert "scripts/setup/install_linux_dependencies.sh" in commands
    assert all("sudo apt-get install" not in command for command in commands)
    assert linux_job["env"]["QT_QPA_PLATFORM"] == "offscreen"
    assert linux_job["env"]["VK_ICD_FILENAMES"] == "/usr/share/vulkan/icd.d/lvp_icd.x86_64.json"
    native_index = next(index for index, step in enumerate(steps) if step.get("id") == "native-python")
    python_step = commands[native_index]
    command, = [line.strip() for line in python_step.splitlines() if line.strip().startswith("xvfb-run ")]
    arguments = shlex.split(command)
    assert arguments[:5] == ["xvfb-run", "--auto-servernum", "python", "-m", "pytest"]
    assert arguments[arguments.index("-o") + 1] == "pythonpath="
    assert "tests/python" in arguments
    assert "tests/contracts" not in arguments
    assert "unset PYTHONPATH INFERNUX_NATIVE_MODULE_DIR" in python_step
    assert next(
        index for index, command in enumerate(commands)
        if "cmake --build --preset linux-clang-install-wheel" in command
    ) < native_index

    # Runtime-neutral contracts run before native builds and are not repeated
    # by the installed-wheel suite. Human-readable step names are not identity.
    needs = linux_job["needs"]
    needs = {needs} if isinstance(needs, str) else set(needs)
    assert {"portable-hub", "python-correctness"} <= needs
    portable_steps = workflow["jobs"]["portable-hub"]["steps"]
    assert any("tests/contracts" in step.get("run", "") for step in portable_steps)

    hub_index = next(
        index for index, command in enumerate(commands)
        if "python -m pytest tests/hub" in command
    )
    hub_step = commands[hub_index]
    assert (
        "xvfb-run --auto-servernum env QT_QPA_PLATFORM=xcb PYTHONPATH=packaging "
        "python -m pytest tests/hub"
    ) in hub_step
    assert "pytest tests/hub -q --junitxml" in hub_step
    assert hub_index < next(
        index for index, command in enumerate(commands)
        if "cmake --preset linux-clang-release" in command
    )
    assert any(
        'conda install --yes --channel conda-forge "libpython-static=3.13.15"' in command
        for command in commands
    )


def test_global_shader_declarations_run_once_in_their_own_installed_wheel_process() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    module = "tests/python/test_shader_declaration_discovery.py"
    for job_name, preset in (("windows-desktop", "windows-msvc"), ("linux-desktop", "linux-clang")):
        steps = workflow["jobs"][job_name]["steps"]
        isolated_index = next(index for index, step in enumerate(steps) if step.get("id") == "shader-declarations")
        native_index = next(index for index, step in enumerate(steps) if step.get("id") == "native-python")
        install_index = next(index for index, step in enumerate(steps)
                             if f"cmake --build --preset {preset}-install-wheel" in step.get("run", ""))
        isolated = steps[isolated_index]["run"].replace("\\", "/")
        native = steps[native_index]["run"].replace("\\", "/")
        assert install_index < isolated_index < native_index
        assert f"python -m pytest -o pythonpath= {module} " in isolated
        assert "--junitxml out/test-results/pytest-shader-declarations-" in isolated
        assert f"--ignore={module}" in native
        assert native.count(module) == 1
        assert "PYTHONPATH" in isolated and "INFERNUX_NATIVE_MODULE_DIR" in isolated
