from __future__ import annotations

import importlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from infernux.version import ENGINE_VERSION

from infernux.engine.build import (
    BuildConfiguration,
    BuildExporterRegistry,
    BuildProfile,
    BuildRequest,
)


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_EDITOR = (
    ROOT / "external" / "plugins" / "infernux_android" / "package" / "editor"
)


def _write_android_numpy_wheel(prefix: Path, *, abi: str = "x86_64") -> Path:
    architecture = "x86_64" if abi == "x86_64" else "arm64_v8a"
    wheel = (
        prefix
        / "wheels"
        / f"numpy-2.5.2-cp313-cp313-android_26_{architecture}.whl"
    )
    wheel.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("numpy/__init__.py", "__version__ = '2.5.2'\n")
        archive.writestr(
            "numpy/random/_examples/numba/extending.py",
            "raise RuntimeError('runtime package must not ship NumPy examples')\n",
        )
        archive.writestr("numpy/_core/tests/test_runtime.py", "raise AssertionError\n")
        archive.writestr("numpy/testing/__init__.py", "raise AssertionError\n")
        archive.writestr("numpy/typing/tests/test_typing.py", "raise AssertionError\n")
        archive.writestr("numpy/f2py/__init__.py", "raise AssertionError\n")
        archive.writestr(
            "numpy-2.5.2.dist-info/WHEEL",
            "Wheel-Version: 1.0\n"
            "Root-Is-Purelib: false\n"
            f"Tag: cp313-cp313-android_26_{architecture}\n",
        )
    return wheel


def _stamp_android_python_prefix(
    exporter_module,
    prefix: Path,
    *,
    abi: str = "x86_64",
    python_version: str = "3.13.15",
    cpython_api: int = 21,
    minimum_api: int = 26,
) -> Path:
    runtime_manifest = importlib.import_module("infernux_android.runtime_manifest")
    (prefix / f"include/python{python_version.rsplit('.', 1)[0]}").mkdir(
        parents=True, exist_ok=True
    )
    (prefix / f"lib/python{python_version.rsplit('.', 1)[0]}").mkdir(
        parents=True, exist_ok=True
    )
    runtime_manifest.create_runtime_manifest(
        prefix,
        abi=abi,
        python_version=python_version,
        cpython_android_api=cpython_api,
        minimum_android_api=minimum_api,
        ndk_version="27.3.13750724",
        source_url="https://www.python.org/ftp/python/3.13.15/Python-3.13.15.tar.xz",
    )
    return prefix / runtime_manifest.MANIFEST_NAME


def _android_module(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN_EDITOR))
    for name in tuple(sys.modules):
        if name == "infernux_android" or name.startswith("infernux_android."):
            sys.modules.pop(name)
    return importlib.import_module("infernux_android")


def _toolchain(tmp_path: Path) -> dict[str, str]:
    sdk = tmp_path / "sdk"
    java = tmp_path / "jdk"
    avd = tmp_path / "avd"
    for directory in (
        sdk / "platforms" / "android-36",
        sdk / "build-tools" / "36.0.0",
        sdk / "cmake" / "3.30.5",
        sdk / "ndk" / "29.0.14206865" / "build" / "cmake",
        sdk / "platform-tools",
        sdk / "emulator",
        java / "bin",
        avd,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    for path in (
        sdk / "platform-tools" / f"adb{suffix}",
        sdk / "emulator" / f"emulator{suffix}",
        java / "bin" / f"java{suffix}",
        sdk
        / "ndk"
        / "29.0.14206865"
        / "build"
        / "cmake"
        / "android.toolchain.cmake",
    ):
        path.write_text("fixture\n", encoding="utf-8")
    (java / "release").write_text('JAVA_VERSION="17.0.20"\n', encoding="utf-8")
    (avd / "Infernux_API_36.ini").write_text("target=android-36\n", encoding="utf-8")
    gradle = tmp_path / "gradle"
    (gradle / "bin").mkdir(parents=True)
    launcher = gradle / "bin" / ("gradle.bat" if sys.platform == "win32" else "gradle")
    launcher.write_text("fixture\n", encoding="utf-8")
    return {
        "ANDROID_SDK_ROOT": str(sdk),
        "ANDROID_AVD_HOME": str(avd),
        "JAVA_HOME": str(java),
        "INFERNUX_GRADLE_HOME": str(gradle),
    }


def _write_native_payload(root: Path, *, abi: str) -> Path:
    payload = importlib.import_module("infernux_android.native_payload")
    native = root / abi / "jniLibs"
    native.mkdir(parents=True)
    for name in payload.NATIVE_LIBRARIES:
        (native / name).write_bytes(b"\\x7fELFfixture")
    (root / abi / "Player.inxmanifest").write_text(json.dumps({
        "engine_version": ENGINE_VERSION, "platform": "android", "abi": abi,
        "python_abi": "cp313", "minimum_api": 26, "configuration": "Release",
        "native_libraries": list(payload.NATIVE_LIBRARIES),
    }), encoding="utf-8")
    java = root / "java/org/libsdl/app/SDLActivity.java"
    java.parent.mkdir(parents=True)
    sdl_java = (
        ROOT
        / "external"
        / "sdl_for_infernux"
        / "android-project"
        / "app"
        / "src"
        / "main"
        / "java"
        / "org"
        / "libsdl"
        / "app"
    )
    shutil.copy2(sdl_java / "SDLActivity.java", java)
    shutil.copy2(
        sdl_java / "SDLInputConnection.java",
        java.parent / "SDLInputConnection.java",
    )
    return root


def test_android_native_payload_stages_without_build_tools(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    module = importlib.import_module("infernux_android.native_payload")
    source = _write_native_payload(tmp_path / "player", abi="x86_64")
    manifest = module.inspect_native_payload(source, abi="x86_64")
    assert manifest["configuration"] == "Release"
    staging = tmp_path / "host"
    native = staging / "app/src/main/jniLibs/x86_64"
    native.mkdir(parents=True)
    (native / "libInfernuxOld.so").write_bytes(b"stale")
    (native / "libpython3.13.so").write_bytes(b"Hub-owned")
    (source / "x86_64/jniLibs/libInfernuxOld.so").write_bytes(b"stale source")
    (source / "x86_64/jniLibs/libmain.so.meta").write_bytes(b"editor-only")
    module.stage_native_payload(source, staging, abi="x86_64")
    assert {p.name for p in native.iterdir()} == set(module.NATIVE_LIBRARIES) | {"libpython3.13.so"}
    assert (native / "libpython3.13.so").read_bytes() == b"Hub-owned"
    assert (staging / "app/src/main/java/org/libsdl/app/SDLActivity.java").is_file()
    input_connection = staging / "app/src/main/java/org/libsdl/app/SDLInputConnection.java"
    input_text = input_connection.read_text(encoding="utf-8")
    assert "if (keyCode == KeyEvent.KEYCODE_DEL)" in input_text
    assert "SDLActivity.onNativeKeyDown(keyCode)" in input_text
    activity_text = (staging / "app/src/main/java/org/libsdl/app/SDLActivity.java").read_text(encoding="utf-8")
    assert "imm.restartInput(mTextEdit);" in activity_text
    assert "mTextEdit.post(() ->" in activity_text


@pytest.mark.parametrize("failure", [
    "abi", "engine", "missing_library", "missing_asset_runtime",
    "missing_manifest_asset_runtime", "missing_java",
])
def test_android_native_payload_rejects_incomplete_or_incompatible_plugin(monkeypatch, tmp_path, failure):
    _android_module(monkeypatch)
    module = importlib.import_module("infernux_android.native_payload")
    source = _write_native_payload(tmp_path / "player", abi="x86_64")
    manifest = source / "x86_64/Player.inxmanifest"
    document = json.loads(manifest.read_bytes())
    if failure == "abi":
        document["abi"] = "arm64-v8a"
    elif failure == "engine":
        document["engine_version"] = "0.3.7"
    elif failure == "missing_library":
        (source / "x86_64/jniLibs/libmain.so").unlink()
    elif failure == "missing_asset_runtime":
        (source / "x86_64/jniLibs/libInfernuxAssetRuntime.so").unlink()
    elif failure == "missing_manifest_asset_runtime":
        document["native_libraries"].remove("libInfernuxAssetRuntime.so")
    else:
        (source / "java/org/libsdl/app/SDLActivity.java").unlink()
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        module.inspect_native_payload(source, abi="x86_64")


def test_android_exporter_registration_plan_and_removal(monkeypatch, tmp_path):
    module = _android_module(monkeypatch)
    exporter = module.AndroidPlatformExporter()
    registry = BuildExporterRegistry()
    registration = registry.register("package:infernux/platform-android", exporter)
    targets = exporter.targets()
    assert [target.id for target in targets] == ["android-x64-emulator", "android-arm64"]
    assert {target.capabilities.graphics_api for target in targets} == {"vulkan"}
    assert all(not target.capabilities.cpu_jit for target in targets)
    assert {target.id for target in registry.targets()} == {target.id for target in targets}
    request = BuildRequest(str(tmp_path / "project"), "android-x64-emulator", str(tmp_path / "output"), BuildProfile())
    plan = exporter.create_plan(request)
    assert [step.id for step in plan.steps] == ["cook", "imports", "native", "package", "audit"]
    assert plan.metadata["graphics_api"] == "vulkan"
    assert registry.unregister(registration)
    assert registry.targets() == ()


def test_android_build_storage_moves_from_project_to_shared_and_authored_environment(monkeypatch, tmp_path):
    from dataclasses import replace

    _android_module(monkeypatch)
    exporter = importlib.import_module("infernux_android.exporter")
    monkeypatch.delenv("INFERNUX_BUILD_CACHE_ROOT", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    request = BuildRequest(str(project), "android-arm64", str(tmp_path / "output"), BuildProfile())
    assert exporter._android_staging_directory(request) == project.resolve() / "Cache/Build/AndroidHost/android-arm64"
    assert exporter._android_build_cache_root(request) / "AndroidEngine" == project.resolve() / "Cache/Build/AndroidEngine"
    environment = dict(exporter.os.environ)
    for name in ("INFERNUX_SHARED_DATA_ROOT", "GRADLE_USER_HOME", "ANDROID_USER_HOME"):
        environment.pop(name, None)
    before = environment.copy()
    configured = exporter._android_gradle_environment(request, environment)
    assert environment == before
    assert configured["GRADLE_USER_HOME"] == str(project / "Cache/Gradle")
    assert configured["ANDROID_USER_HOME"] == str(project / "State/Android")

    owner = tmp_path / "Hub Shared"
    environment["INFERNUX_SHARED_DATA_ROOT"] = str(owner)
    shared = tmp_path / "fast-build-disk"
    request = replace(request, profile=BuildProfile(options={"build_cache_root": str(shared)}))
    assert exporter._android_staging_directory(request) == shared.resolve() / "AndroidHost/android-arm64"
    configured = exporter._android_gradle_environment(request, environment)
    assert configured["GRADLE_USER_HOME"] == str(owner / "Cache/Gradle")
    assert configured["ANDROID_USER_HOME"] == str(owner / "State/Android")
    environment["GRADLE_USER_HOME"] = str(tmp_path / "author-gradle")
    environment["ANDROID_USER_HOME"] = str(tmp_path / "author-android")
    before = environment.copy()
    configured = exporter._android_gradle_environment(request, environment)
    assert environment == before
    assert configured["GRADLE_USER_HOME"] == before["GRADLE_USER_HOME"]
    assert configured["ANDROID_USER_HOME"] == before["ANDROID_USER_HOME"]
    code, output = exporter._run_command(request, [
        sys.executable, "-c",
        "import os,json;print(json.dumps([os.environ['GRADLE_USER_HOME'],os.environ['ANDROID_USER_HOME']]))",
    ], project, configured, source="gradle")
    assert code == 0
    assert json.loads(output[-1]) == [before["GRADLE_USER_HOME"], before["ANDROID_USER_HOME"]]


def test_android_toolchain_remains_buildable_after_device_tools_are_removed(monkeypatch, tmp_path):
    module = _android_module(monkeypatch)
    toolchain = _toolchain(tmp_path)
    report = module.inspect_android_toolchain(
        "android-x64-emulator",
        toolchain,
    )

    assert report.available
    assert report.diagnostics == ()
    assert report.details["avds"] == ["Infernux_API_36"]
    assert report.details["adb_available"] is True
    assert report.details["emulator_available"] is True

    sdk = Path(toolchain["ANDROID_SDK_ROOT"])
    suffix = ".exe" if sys.platform == "win32" else ""
    (sdk / "platform-tools" / f"adb{suffix}").unlink()
    (sdk / "emulator" / f"emulator{suffix}").unlink()
    Path(toolchain["ANDROID_AVD_HOME"], "Infernux_API_36.ini").unlink()
    report = module.inspect_android_toolchain("android-x64-emulator", toolchain)
    assert report.available
    assert report.diagnostics == ()
    assert report.details["avds"] == []
    assert report.details["adb_available"] is False
    assert report.details["emulator_available"] is False


def test_android_doctor_reports_every_missing_root(monkeypatch):
    module = _android_module(monkeypatch)
    report = module.inspect_android_toolchain("android-arm64", {})

    assert not report.available
    assert {item.code for item in report.diagnostics} == {
        "android.sdk.environment",
        "android.jdk.environment",
        "android.gradle.environment",
    }


def test_android_exporter_doctor_requires_target_python_runtime(
    monkeypatch, tmp_path
):
    module = _android_module(monkeypatch)
    for name, value in _toolchain(tmp_path).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("INFERNUX_ANDROID_PYTHON_PREFIX_ARM64", raising=False)
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-arm64",
        str(tmp_path / "output"),
        BuildProfile(),
    )

    report = module.AndroidPlatformExporter().doctor(request)

    assert not report.available
    assert [item.code for item in report.diagnostics] == [
        "android.python.runtime-missing"
    ]
    assert "INFERNUX_ANDROID_PYTHON_PREFIX_ARM64" in report.diagnostics[0].message


def test_android_exporter_doctor_validates_target_python_runtime(
    monkeypatch, tmp_path
):
    module = _android_module(monkeypatch)
    for name, value in _toolchain(tmp_path).items():
        monkeypatch.setenv(name, value)
    prefix = tmp_path / "python-arm64"
    prefix.mkdir()
    _stamp_android_python_prefix(module, prefix, abi="arm64-v8a")
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-arm64",
        str(tmp_path / "output"),
        BuildProfile(options={"android_python_prefix": str(prefix)}),
    )

    exporter = importlib.import_module("infernux_android.exporter")
    _write_native_payload(tmp_path / "player", abi="arm64-v8a")
    monkeypatch.setattr(exporter, "__file__", str(tmp_path / "exporter.py"))
    report = module.AndroidPlatformExporter().doctor(request)

    assert report.available
    assert report.details["python_prefix"] == str(prefix.resolve())


def test_android_compute_aot_is_requested_from_shared_cook(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    platform_cook = importlib.import_module("infernux.engine.platform_content_cook")
    project = tmp_path / "project"
    captured = {}
    request = BuildRequest(
        str(project),
        "android-arm64",
        str(tmp_path / "output"),
        BuildProfile(options={
            "display_mode": "fullscreen_borderless",
            "window_width": 1280,
            "window_height": 720,
            "window_resizable": False,
            "android_orientation": "landscape",
            "build_settings": {},
        }),
    )
    cooked = platform_cook.PlatformContentCookResult(
        "TestGame",
        tmp_path / "cooked",
        {},
    )
    cooked.data_directory.mkdir(parents=True)
    (cooked.data_directory / "Content.inxpkg").write_bytes(b"content")
    (cooked.data_directory / "AssetCatalog.inxcat").write_bytes(b"catalog")
    (cooked.data_directory / "PackageIndex.inxmanifest").write_text(
        "INFERNUX_PLAYER_PACKAGE_INDEX\n", encoding="ascii"
    )
    def cook(*args, **kwargs):
        captured.update(kwargs)
        return cooked
    monkeypatch.setattr(platform_cook, "cook_platform_content", cook)
    monkeypatch.setattr(platform_cook, "read_cooked_player_icon", lambda *args, **kwargs: b"icon")
    monkeypatch.setattr(exporter_module, "_stage_android_launcher_icons", lambda *args: None)

    exporter_module._cook_player_content(
        request,
        tmp_path / "staging",
        tmp_path / "engine-package",
        "arm64-v8a",
    )

    assert captured["gpu_compute_aot"] is True


@pytest.mark.parametrize(
    "import_source, missing_package",
    [
        ("import numba as nb\n", "numba"),
        ("from llvmlite import binding\n", "llvmlite"),
    ],
)
def test_android_rejects_direct_jit_imports_in_selected_sources(
    monkeypatch, tmp_path, import_source, missing_package
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    platform_cook = importlib.import_module("infernux.engine.platform_content_cook")
    selected = tmp_path / "Assets" / "Scripts" / "Gameplay.py"
    selected.parent.mkdir(parents=True)
    selected.write_text(import_source, encoding="utf-8")
    cooked = platform_cook.PlatformContentCookResult(
        "TestGame", tmp_path / "cooked", {}, (selected,)
    )
    cooked.data_directory.mkdir()
    monkeypatch.setattr(platform_cook, "cook_platform_content", lambda *args, **kwargs: cooked)
    request = BuildRequest(
        str(tmp_path / "project"), "android-arm64", str(tmp_path / "output"),
        BuildProfile(options={
            "display_mode": "fullscreen_borderless",
            "window_width": 1280,
            "window_height": 720,
            "window_resizable": False,
            "android_orientation": "landscape",
            "build_settings": {},
        }),
    )

    with pytest.raises(ValueError, match=rf"{missing_package}.*Android runtime has no CPU JIT"):
        exporter_module._cook_player_content(
            request, tmp_path / "staging", tmp_path / "engine-package", "arm64-v8a"
        )
    assert not (tmp_path / "staging/app/src/main/assets/player").exists()


def test_android_jit_import_scan_uses_only_selected_sources(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    selected = tmp_path / "Assets" / "Scripts" / "Gameplay.py"
    selected.parent.mkdir(parents=True)
    selected.write_text(
        "import infernux as inx\n"
        "@inx.jit.compile(parallel_policy='required')\n"
        "def update(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    editor_only = tmp_path / "Assets" / "Editor" / "Bake.py"
    editor_only.parent.mkdir(parents=True)
    editor_only.write_text("import numba\n", encoding="utf-8")

    exporter_module._reject_android_jit_imports((selected,))


def test_android_exporter_doctor_rejects_wrong_runtime_abi(monkeypatch, tmp_path):
    module = _android_module(monkeypatch)
    for name, value in _toolchain(tmp_path).items():
        monkeypatch.setenv(name, value)
    prefix = tmp_path / "python-x64"
    prefix.mkdir()
    _stamp_android_python_prefix(module, prefix, abi="x86_64")
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-arm64",
        str(tmp_path / "output"),
        BuildProfile(options={"android_python_prefix": str(prefix)}),
    )

    report = module.AndroidPlatformExporter().doctor(request)

    assert not report.available
    assert [item.code for item in report.diagnostics] == [
        "android.python.runtime-invalid"
    ]
    assert "targets x86_64" in report.diagnostics[0].message


def test_android_development_host_uses_landscape_defaults_and_debug_apk(monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET

    _android_module(monkeypatch)
    exporter = importlib.import_module("infernux_android.exporter")
    project = tmp_path / "generated"
    shutil.copytree(PLUGIN_EDITOR / "infernux_android/templates/host", project)
    request = BuildRequest(str(tmp_path / "project"), "android-x64-emulator", str(tmp_path / "output"), BuildProfile())
    orientations = exporter._android_orientation_contract(request, {})
    resolution = exporter._android_resolution_contract(request)
    assert orientations == ("LandscapeLeft LandscapeRight", "sensorLandscape")
    assert resolution == ("fixed_dpi", 320)
    exporter._configure_project(project, tmp_path / "sdk", "x86_64",
                                sdl_orientations=orientations[0], android_orientation=orientations[1],
                                resolution_scaling=resolution[0], target_dpi=resolution[1],
                                game_name="Infernux041Labv2")
    android = "{http://schemas.android.com/apk/res/android}"
    manifest = ET.parse(project / "app/src/main/AndroidManifest.xml").getroot()
    application = manifest.find("application")
    activity = application.find("activity")
    metadata = {item.get(android + "name"): item.get(android + "value")
                for item in application.findall("meta-data") + activity.findall("meta-data")}
    features = {item.get(android + "name") for item in manifest.findall("uses-feature")}
    assert "android.hardware.vulkan.version" in features
    assert "android.hardware.opengles" not in features
    assert activity.get(android + "screenOrientation") == "sensorLandscape"
    assert activity.get(android + "resizeableActivity") == "false"
    assert activity.get(android + "windowSoftInputMode") == "adjustNothing"
    assert metadata["infernux.orientations"] == "LandscapeLeft LandscapeRight"
    assert metadata["infernux.resolution_scaling"] == "fixed_dpi"
    assert metadata["infernux.target_dpi"] == "320"
    assert application.get(android + "appCategory") == "game"
    assert application.get(android + "icon") == "@mipmap/infernux_launcher"
    assert "android.intent.category.GAME" in {
        item.get(android + "name") for item in activity.findall("intent-filter/category")}
    doctor = importlib.import_module("infernux_android.doctor")
    gradle = (project / "app/build.gradle").read_text(encoding="utf-8")
    assert f'buildToolsVersion "{doctor.ANDROID_BUILD_TOOLS}"' in gradle
    assert 'abiFilters "x86_64"' in gradle
    assert not (project / "app/src/main/cpp/CMakeLists.txt").exists()
    assert not list(project.rglob("*.in"))
    assert "@INFERNUX_" not in gradle and "@ANDROID_" not in gradle

    assert 'applicationId "com.infernux.infernux041labv2"' in gradle
    assert exporter._android_artifact_plan(request, project) == (
        "apk", "Debug", project / "app/build/outputs/apk/debug/app-debug.apk",
    )


def test_android_portrait_release_host_uses_authored_dpi_and_bundle(monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET

    _android_module(monkeypatch)
    exporter = importlib.import_module("infernux_android.exporter")
    request = BuildRequest(str(tmp_path / "project"), "android-arm64", str(tmp_path / "output"),
                           BuildProfile(configuration=BuildConfiguration.RELEASE, options={
                               "android_orientation": "portrait", "android_resolution_scaling": "disabled",
                               "android_target_dpi": 480,
                           }))
    orientations = exporter._android_orientation_contract(request, {"window_width": 720, "window_height": 1280})
    resolution = exporter._android_resolution_contract(request)
    assert orientations == ("Portrait PortraitUpsideDown", "sensorPortrait")
    assert resolution == ("disabled", 480)
    host = tmp_path / "host"
    shutil.copytree(PLUGIN_EDITOR / "infernux_android/templates/host", host)
    exporter._configure_project(host, tmp_path / "sdk", "arm64-v8a", game_name="Runner Long",
                                sdl_orientations=orientations[0], android_orientation=orientations[1],
                                resolution_scaling=resolution[0], target_dpi=resolution[1])
    android = "{http://schemas.android.com/apk/res/android}"
    application = ET.parse(host / "app/src/main/AndroidManifest.xml").getroot().find("application")
    activity = application.find("activity")
    metadata = {item.get(android + "name"): item.get(android + "value")
                for item in application.findall("meta-data") + activity.findall("meta-data")}
    assert activity.get(android + "screenOrientation") == "sensorPortrait"
    assert metadata["infernux.orientations"] == orientations[0]
    assert metadata["infernux.resolution_scaling"] == "disabled"
    assert metadata["infernux.target_dpi"] == "480"
    assert 'applicationId "com.infernux.runnerlong"' in (host / "app/build.gradle").read_text(encoding="utf-8")
    assert exporter._android_artifact_plan(request, host) == (
        "aab", "Release", host / "app/build/outputs/bundle/release/app-release.aab",
    )


def test_android_sensor_release_host_selects_unsigned_then_signed_apk(monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET

    _android_module(monkeypatch)
    exporter = importlib.import_module("infernux_android.exporter")
    request = BuildRequest(str(tmp_path / "project"), "android-arm64", str(tmp_path / "output"),
                           BuildProfile(configuration=BuildConfiguration.RELEASE, options={
                               "android_artifact": "apk", "android_orientation": "sensor",
                               "android_resolution_scaling": "fixed_dpi", "android_target_dpi": "360",
                           }))
    orientations = exporter._android_orientation_contract(request, {"window_width": 1280, "window_height": 720})
    resolution = exporter._android_resolution_contract(request)
    assert orientations == ("LandscapeLeft LandscapeRight Portrait PortraitUpsideDown", "fullUser")
    assert resolution == ("fixed_dpi", 360)
    host = tmp_path / "host"
    shutil.copytree(PLUGIN_EDITOR / "infernux_android/templates/host", host)
    exporter._configure_project(host, tmp_path / "sdk", "arm64-v8a", game_name="123 Demo",
                                sdl_orientations=orientations[0], android_orientation=orientations[1],
                                resolution_scaling=resolution[0], target_dpi=resolution[1])
    android = "{http://schemas.android.com/apk/res/android}"
    activity = ET.parse(host / "app/src/main/AndroidManifest.xml").getroot().find("application/activity")
    assert activity.get(android + "screenOrientation") == "fullUser"
    manifest = (host / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert 'android:value="360"' in manifest
    assert 'applicationId "com.infernux.game123demo"' in (host / "app/build.gradle").read_text(encoding="utf-8")
    assert exporter._android_artifact_plan(request, host) == (
        "apk", "Release", host / "app/build/outputs/apk/release/app-release-unsigned.apk",
    )
    assert exporter._android_artifact_plan(request, host, release_signed=True) == (
        "apk", "Release", host / "app/build/outputs/apk/release/app-release.apk",
    )


def test_android_launcher_icons_are_generated_from_the_cooked_project_icon(
    monkeypatch, tmp_path
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    staging = tmp_path / "host"
    source_icon = ROOT / "python/infernux/resources/icons/icon.png"

    exporter_module._stage_android_launcher_icons(staging, source_icon.read_bytes())

    from PIL import Image

    resources = staging / "app/src/main/res"
    for density, size in {
        "mdpi": 48,
        "hdpi": 72,
        "xhdpi": 96,
        "xxhdpi": 144,
        "xxxhdpi": 192,
    }.items():
        with Image.open(resources / f"mipmap-{density}/infernux_launcher.png") as icon:
            assert icon.size == (size, size)
            assert icon.mode == "RGBA"
    with Image.open(
        resources / "drawable-nodpi/infernux_launcher_foreground.png"
    ) as foreground:
        assert foreground.size == (432, 432)


def test_android_host_template_excludes_asset_database_sidecars(
    monkeypatch, tmp_path
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    source = tmp_path / "installed-plugin-template"
    values = source / "app/src/main/res/values"
    values.mkdir(parents=True)
    (values / "strings.xml").write_text("<resources />\n", encoding="utf-8")
    (values / "strings.xml.meta").write_text("{}\n", encoding="utf-8")
    staging = tmp_path / "host-cache"
    stale = staging / "app/src/main/res/values/styles.xml.meta"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}\n", encoding="utf-8")

    exporter_module._stage_host_template(source, staging)

    assert (staging / "app/src/main/res/values/strings.xml").is_file()
    assert not list(staging.rglob("*.meta"))


def test_android_orientation_contract_rejects_unknown_policy(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-x64-emulator",
        str(tmp_path / "output"),
        BuildProfile(options={"android_orientation": "diagonal"}),
    )

    with pytest.raises(ValueError, match="android_orientation"):
        exporter_module._android_orientation_contract(request, {})


@pytest.mark.parametrize(
    "options",
    (
        {"android_resolution_scaling": "dynamic"},
        {"android_target_dpi": "not-a-number"},
        {"android_target_dpi": 60},
        {"android_target_dpi": 1200},
    ),
)
def test_android_resolution_contract_rejects_invalid_values(
    monkeypatch, tmp_path, options
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-arm64",
        str(tmp_path / "output"),
        BuildProfile(options=options),
    )

    with pytest.raises(ValueError, match="android_"):
        exporter_module._android_resolution_contract(request)


def test_android_signing_environment_keeps_passwords_out_of_profile(
    monkeypatch, tmp_path
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    keystore = tmp_path / "release.jks"
    keystore.write_bytes(b"fixture")
    monkeypatch.setenv("TEST_ANDROID_STORE_PASSWORD", "store-secret")
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-arm64",
        str(tmp_path / "output"),
        BuildProfile(
            configuration=BuildConfiguration.RELEASE,
            options={
                "android_keystore": str(keystore),
                "android_key_alias": "release",
                "android_keystore_password_env": "TEST_ANDROID_STORE_PASSWORD",
            },
        ),
    )

    environment = exporter_module._android_signing_environment(request)

    assert environment == {
        "INFERNUX_ANDROID_KEYSTORE": str(keystore.resolve()),
        "INFERNUX_ANDROID_KEY_ALIAS": "release",
        "INFERNUX_ANDROID_KEYSTORE_PASSWORD": "store-secret",
        "INFERNUX_ANDROID_KEY_PASSWORD": "store-secret",
    }
    assert "store-secret" not in repr(request.profile.options)


def test_android_signing_environment_rejects_partial_configuration(
    monkeypatch, tmp_path
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-arm64",
        str(tmp_path / "output"),
        BuildProfile(
            configuration=BuildConfiguration.RELEASE,
            options={"android_key_alias": "release"},
        ),
    )

    with pytest.raises(ValueError, match="partially configured"):
        exporter_module._android_signing_environment(request)


def test_android_python_runtime_staging_is_exact_and_versioned(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    prefix = tmp_path / "python-prefix"
    include = prefix / "include" / "python3.13"
    stdlib = prefix / "lib" / "python3.13"
    include.mkdir(parents=True)
    (stdlib / "encodings").mkdir(parents=True)
    (stdlib / "__pycache__").mkdir()
    (include / "Python.h").write_text("fixture header\n", encoding="utf-8")
    (stdlib / "encodings" / "__init__.py").write_text("fixture\n", encoding="utf-8")
    (stdlib / "removed.py").write_text("removed later\n", encoding="utf-8")
    (stdlib / "__pycache__" / "ignored.pyc").write_bytes(b"ignored")
    (stdlib / "pydoc_data").mkdir()
    (stdlib / "pydoc_data" / "topics.py").write_text("ignored\n", encoding="utf-8")
    (stdlib / "lib-dynload").mkdir()
    (stdlib / "lib-dynload" / "_testcapi.so").write_bytes(b"ignored")
    (prefix / "lib" / "libpython3.13.so").write_bytes(b"python")
    (prefix / "lib" / "libssl_python.so").write_bytes(b"ssl")
    _write_android_numpy_wheel(prefix)
    _stamp_android_python_prefix(exporter_module, prefix)

    staging = tmp_path / "staging"
    stale_include = staging / "app/src/main/python/include/python3.13"
    stale_include.mkdir(parents=True)
    (stale_include / "Python.h").write_text("stale\n", encoding="utf-8")
    stale_assets = staging / "app/src/main/assets/python"
    stale_assets.mkdir(parents=True)
    (stale_assets / "stale.py").write_text("stale\n", encoding="utf-8")
    stale_native = staging / "app/src/main/jniLibs/x86_64"
    stale_native.mkdir(parents=True)
    (stale_native / "libpython3.13.so").write_bytes(b"stale")
    (stale_native / "libengine.so").write_bytes(b"keep")

    request = BuildRequest(
        str(tmp_path / "project"),
        "android-x64-emulator",
        str(tmp_path / "output"),
        BuildProfile(),
    )
    version = exporter_module._stage_python_runtime(
        request, staging, prefix, "x86_64"
    )

    runtime_id = stale_assets / "infernux-runtime.id"
    first_identity = runtime_id.read_text(encoding="utf-8")
    assert version == "3.13"
    assert not stale_include.exists()
    assert not (stale_assets / "stale.py").exists()
    assert not (stale_assets / "lib/python3.13/__pycache__").exists()
    assert (stale_native / "libpython3.13.so").is_file()
    assert (stale_native / "libssl_python.so").is_file()
    assert (stale_native / "libengine.so").is_file()
    assert (stale_assets / "site-packages/numpy/__init__.py").is_file()
    assert not (stale_assets / "site-packages/numpy/random/_examples").exists()
    assert not (stale_assets / "site-packages/numpy/_core/tests").exists()
    assert not (stale_assets / "site-packages/numpy/testing").exists()
    assert not (stale_assets / "site-packages/numpy/typing/tests").exists()
    assert not (stale_assets / "site-packages/numpy/f2py").exists()
    assert not (stale_assets / "lib/python3.13/pydoc_data").exists()
    assert not (stale_assets / "lib/python3.13/lib-dynload/_testcapi.so").exists()
    assert len(first_identity.strip()) == 64

    (stdlib / "encodings" / "__init__.py").write_text(
        "changed runtime fixture\n", encoding="utf-8"
    )
    _stamp_android_python_prefix(exporter_module, prefix)
    exporter_module._stage_python_runtime(request, staging, prefix, "x86_64")
    assert runtime_id.read_text(encoding="utf-8") != first_identity


def test_android_package_audit_accepts_one_exact_abi(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    artifact = tmp_path / "player.aab"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("base/manifest/AndroidManifest.xml", b"manifest")
        archive.writestr("base/lib/arm64-v8a/libinfernux.so", b"native")
        archive.writestr(
            "base/assets/python/site-packages/numpy/__init__.py",
            b"",
        )

    audit = exporter_module._audit_android_archive(
        artifact,
        abi="arm64-v8a",
        artifact_kind="aab",
    )

    assert audit == {
        "native_library_count": 1,
        "packaged_abis": ["arm64-v8a"],
        "forbidden_distribution_count": 0,
    }


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (
            "base/assets/python/site-packages/torch/__init__.py",
            "host-only Python distributions",
        ),
        ("base/lib/x86_64/libinfernux.so", "unexpected ABIs"),
    ],
)
def test_android_package_audit_rejects_scope_violations(
    monkeypatch,
    tmp_path,
    entry,
    message,
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    artifact = tmp_path / "player.aab"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("base/manifest/AndroidManifest.xml", b"manifest")
        archive.writestr("base/lib/arm64-v8a/libinfernux.so", b"native")
        archive.writestr(entry, b"scope violation")

    with pytest.raises(ValueError, match=message):
        exporter_module._audit_android_archive(
            artifact,
            abi="arm64-v8a",
            artifact_kind="aab",
        )


def test_android_engine_staging_excludes_desktop_runtime_payloads(
    monkeypatch,
    tmp_path,
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    source_root = tmp_path / "source"
    package = source_root / "python/infernux"
    bootstrap = package / "engine/platform_player_bootstrap.py"
    bootstrap.parent.mkdir(parents=True)
    bootstrap.write_text("def run_platform_player(): pass\n", encoding="utf-8")
    public_api = package / "__init__.py"
    public_api.write_text("__version__ = '0.4.1'\n", encoding="utf-8")
    shader = package / "resources/shaders/standard.vert"
    shader.parent.mkdir(parents=True)
    shader.write_text("void main() {}\n", encoding="utf-8")
    shader_template = package / "resources/shaders/_templates/surface_main.glsl"
    shader_template.parent.mkdir()
    shader_template.write_text("void main() {}\n", encoding="utf-8")
    excluded = (
        package / "_runtime_packs/Runtime.inxrt",
        package / "_runtime_modules/Parallel.inxmod",
        package / "resources/official_packages/default.inxpkg",
        package / "resources/player_runtime/InfernuxPlayerHost.exe",
        package / "templates/project/EmptyProject.json",
        package / "test/test_runtime.py",
        package / "engine/platform_player_bootstrap.pyi",
    )
    for path in excluded:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"desktop-only")
    vendor = package / "_compiler/taichi/_vendor/taichi/__init__.py"
    vendor.parent.mkdir(parents=True)
    vendor.write_text("raise AssertionError('compiler must not ship')\n", encoding="utf-8")
    staging = tmp_path / "staging"
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-x64-emulator",
        str(tmp_path / "output"),
        BuildProfile(),
    )

    exporter_module._stage_engine_python_package(request, staging, package)

    destination = staging / "app/src/main/assets/python/site-packages/infernux"
    assert (destination / "__init__.py").read_text(encoding="utf-8") == public_api.read_text(
        encoding="utf-8"
    )
    assert (destination / "engine/platform_player_bootstrap.py").is_file()
    assert Path(
        importlib.util.cache_from_source(
            str(destination / "engine/platform_player_bootstrap.py")
        )
    ).is_file()
    assert (destination / "resources/shaders/standard.vert").is_file()
    assert (destination / "resources/shaders/_templates/surface_main.glsl").is_file()
    assert not (destination / "_runtime_packs").exists()
    assert not (destination / "_runtime_modules").exists()
    assert not (destination / "resources/official_packages").exists()
    assert not (destination / "resources/player_runtime").exists()
    assert not (destination / "templates/project").exists()
    assert not (destination / "test").exists()
    assert not (destination / "engine/platform_player_bootstrap.pyi").exists()
    assert not (destination / "_compiler/taichi/_vendor").exists()


def test_android_python_runtime_identity_tracks_packaging_policy(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    prefix = tmp_path / "python-prefix"
    stdlib = prefix / "lib/python3.13"
    stdlib.mkdir(parents=True)
    (stdlib / "os.py").write_text("fixture\n", encoding="utf-8")
    runtime_library = prefix / "lib/libpython3.13.so"
    runtime_library.write_bytes(b"python")

    first = exporter_module._python_runtime_identity(
        prefix,
        stdlib,
        (runtime_library,),
        "3.13",
        "x86_64",
    )
    monkeypatch.setattr(exporter_module, "_ANDROID_STDLIB_IGNORES", ("changed-policy",))
    second = exporter_module._python_runtime_identity(
        prefix,
        stdlib,
        (runtime_library,),
        "3.13",
        "x86_64",
    )

    assert second != first


def test_android_python_runtime_identity_includes_engine_modules(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    staging = tmp_path / "staging"
    python_assets = staging / "app/src/main/assets/python"
    site_packages = python_assets / "site-packages"
    (site_packages / "infernux").mkdir(parents=True)
    (site_packages / "packaging").mkdir()
    (python_assets / "infernux-runtime.id").write_text(
        "a" * 64 + "\n", encoding="ascii"
    )
    engine_module = site_packages / "infernux/__init__.py"
    engine_module.write_text("ENGINE = 1\n", encoding="utf-8")
    (site_packages / "packaging/__init__.py").write_text(
        "VERSION = 1\n", encoding="utf-8"
    )

    first = exporter_module._finalize_python_runtime_identity(staging)
    (python_assets / "infernux-runtime.id").write_text(
        "a" * 64 + "\n", encoding="ascii"
    )
    engine_module.write_text("ENGINE = 2\n", encoding="utf-8")
    second = exporter_module._finalize_python_runtime_identity(staging)

    assert first != second
    assert (python_assets / "infernux-runtime.id").read_text(
        encoding="ascii"
    ) == second + "\n"


def test_android_python_runtime_rejects_unsafe_numpy_wheel(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    prefix = tmp_path / "python-prefix"
    include = prefix / "include" / "python3.13"
    stdlib = prefix / "lib" / "python3.13"
    include.mkdir(parents=True)
    (stdlib / "encodings").mkdir(parents=True)
    (include / "Python.h").write_text("fixture header\n", encoding="utf-8")
    (stdlib / "encodings" / "__init__.py").write_text(
        "fixture\n", encoding="utf-8"
    )
    (prefix / "lib" / "libpython3.13.so").write_bytes(b"python")
    wheel = _write_android_numpy_wheel(prefix)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("../outside.py", "unsafe\n")
    _stamp_android_python_prefix(exporter_module, prefix)
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-x64-emulator",
        str(tmp_path / "output"),
        BuildProfile(),
    )

    with pytest.raises(ValueError, match="unsafe entry"):
        exporter_module._stage_python_runtime(
            request,
            tmp_path / "staging",
            prefix,
            "x86_64",
        )


def test_android_python_runtime_rejects_non_313_prefix(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    prefix = tmp_path / "python-prefix"
    include = prefix / "include" / "python3.11"
    stdlib = prefix / "lib" / "python3.11"
    include.mkdir(parents=True)
    (stdlib / "encodings").mkdir(parents=True)
    (include / "Python.h").write_text("fixture header\n", encoding="utf-8")
    (prefix / "lib" / "libpython3.11.so").write_bytes(b"python")
    manifest_path = _stamp_android_python_prefix(exporter_module, prefix)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cpython"]["version"] = "3.11.9"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-x64-emulator",
        str(tmp_path / "output"),
        BuildProfile(),
    )

    with pytest.raises(ValueError, match="requires CPython 3.13"):
        exporter_module._stage_python_runtime(
            request,
            tmp_path / "staging",
            prefix,
            "x86_64",
        )


def test_android_python_runtime_rejects_missing_extension_sidecars(
    monkeypatch, tmp_path
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    prefix = tmp_path / "python-prefix"
    include = prefix / "include" / "python3.13"
    stdlib = prefix / "lib" / "python3.13"
    extension_dir = stdlib / "lib-dynload"
    include.mkdir(parents=True)
    (stdlib / "encodings").mkdir(parents=True)
    extension_dir.mkdir()
    (include / "Python.h").write_text("fixture header\n", encoding="utf-8")
    (prefix / "lib" / "libpython3.13.so").write_bytes(b"python")
    (extension_dir / "_ssl.cpython-313-x86_64-linux-android.so").write_bytes(
        b"ELF payload libssl_python.so libcrypto_python.so"
    )
    _write_android_numpy_wheel(prefix)
    _stamp_android_python_prefix(exporter_module, prefix)
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-x64-emulator",
        str(tmp_path / "output"),
        BuildProfile(),
    )

    with pytest.raises(
        ValueError,
        match="libcrypto_python.so, libssl_python.so",
    ):
        exporter_module._stage_python_runtime(
            request,
            tmp_path / "staging",
            prefix,
            "x86_64",
        )


def test_android_python_runtime_rejects_unstamped_prefix(monkeypatch, tmp_path):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    prefix = tmp_path / "python-prefix"
    (prefix / "include/python3.13").mkdir(parents=True)
    (prefix / "lib/python3.13/encodings").mkdir(parents=True)
    request = BuildRequest(
        str(tmp_path / "project"),
        "android-x64-emulator",
        str(tmp_path / "output"),
        BuildProfile(),
    )

    with pytest.raises(ValueError, match="android_python_runtime.py"):
        exporter_module._stage_python_runtime(
            request,
            tmp_path / "staging",
            prefix,
            "x86_64",
        )


def test_android_python_runtime_manifest_rejects_incomplete_prefix(
    monkeypatch, tmp_path
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    runtime_manifest = importlib.import_module("infernux_android.runtime_manifest")
    prefix = tmp_path / "python-prefix"
    payload = prefix / "lib/python3.13/os.py"
    payload.parent.mkdir(parents=True)
    payload.write_text("fixture = 1\n", encoding="utf-8")
    _stamp_android_python_prefix(exporter_module, prefix)

    runtime_manifest.validate_runtime_manifest(
        prefix,
        expected_abi="x86_64",
        expected_python_series="3.13",
        application_minimum_android_api=26,
    )
    shutil.rmtree(prefix / "include/python3.13")

    with pytest.raises(ValueError, match="prefix is incomplete"):
        runtime_manifest.validate_runtime_manifest(
            prefix,
            expected_abi="x86_64",
            expected_python_series="3.13",
            application_minimum_android_api=26,
        )


def test_android_python_runtime_manifest_rejects_incompatible_target(
    monkeypatch, tmp_path
):
    _android_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_android.exporter")
    runtime_manifest = importlib.import_module("infernux_android.runtime_manifest")
    prefix = tmp_path / "python-prefix"
    (prefix / "lib").mkdir(parents=True)
    _stamp_android_python_prefix(exporter_module, prefix, minimum_api=27)

    with pytest.raises(ValueError, match="requires API 27"):
        runtime_manifest.validate_runtime_manifest(
            prefix,
            expected_abi="x86_64",
            expected_python_series="3.13",
            application_minimum_android_api=26,
        )
    with pytest.raises(ValueError, match="targets x86_64"):
        runtime_manifest.validate_runtime_manifest(
            prefix,
            expected_abi="arm64-v8a",
            expected_python_series="3.13",
            application_minimum_android_api=27,
        )
