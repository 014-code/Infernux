from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from infernux.engine.build import BuildProfile, BuildRequest
from infernux.plugins import player_file_exported
from infernux.version import ENGINE_VERSION


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_EDITOR = (
    ROOT / "external" / "plugins" / "infernux_web" / "package" / "editor"
)


def _web_python_runtime_manifest():
    return {
        "implementation": "cpython",
        "version": "3.13.15",
        "abi": "cp313",
        "platform": "emscripten",
        "platform_version": "4.0.10",
        "architecture": "wasm32",
        "extension_linkage": "static-built-in",
        "required_imports": ["numpy"],
        "packages": [
            {
                "name": "numpy",
                "version": "2.2.5",
                "imports": ["numpy"],
                "python_abi": "cp313",
                "platform": "emscripten",
                "platform_version": "4.0.10",
                "architecture": "wasm32",
                "linkage": "static-built-in",
                "source_url": "https://files.pythonhosted.org/packages/source/n/numpy/numpy-2.2.5.tar.gz",
                "source_sha256": "a9c0d994680cd991b1cb772e8b297340085466a6fe964bc9d4e80f5e2f43c291",
                "payload_hash_algorithm": "infernux-tree-sha256-v1",
                "payload_sha256": "1" * 64,
                "payload_bytes": 1,
                "payload_files": 1,
                "license": "BSD-3-Clause",
            }
        ],
    }


def _web_module(monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN_EDITOR))
    for name in tuple(sys.modules):
        if name == "infernux_web" or name.startswith("infernux_web."):
            sys.modules.pop(name)
    return importlib.import_module("infernux_web")


def _request(tmp_path: Path) -> BuildRequest:
    return BuildRequest(
        str(tmp_path / "project"),
        "web-wasm32",
        str(tmp_path / "output"),
        BuildProfile(options={
            "display_mode": "fullscreen_borderless",
            "window_width": 1280,
            "window_height": 720,
            "window_resizable": False,
            "build_settings": {"scene_guids": []},
        }),
    )


def test_web_exporter_contributes_only_webgpu_target(monkeypatch):
    module = _web_module(monkeypatch)
    targets = module.WebPlatformExporter().targets()

    assert [target.id for target in targets] == ["web-wasm32"]
    capabilities = targets[0].capabilities
    assert capabilities.graphics_api == "webgpu"
    assert not capabilities.threads
    assert not capabilities.dynamic_loading
    assert not capabilities.python_native_modules
    assert not capabilities.cpu_jit
    assert "gpu-jit" not in capabilities.features
    assert not capabilities.network
    assert capabilities.audio
    assert capabilities.text_input
    assert capabilities.gamepad_input
    assert not capabilities.persistent_storage
    assert {
        "dom-text-input",
        "gesture-gated-webaudio",
        "multi-pointer",
        "safe-area",
        "webgpu-capability-inventory",
    } <= capabilities.features


def test_web_dependency_gate_accepts_public_cpu_jit_fixture(monkeypatch):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    script = (
        ROOT / "tests/fixtures/multiplatform_player/Assets/Scripts/Bootstrap.py"
    )
    source = script.read_text(encoding="utf-8")
    assert "@inx.jit.compile" in source
    assert "inx.jit.warmup(" in source
    exporter._reject_unshipped_web_dependencies((script,))


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    (
        ("import numba\n", "numba"),
        ("from llvmlite import binding\n", "llvmlite"),
    ),
)
def test_web_dependency_gate_rejects_unshipped_compiler_imports(
    monkeypatch, tmp_path, source, diagnostic
):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    script = tmp_path / "invalid.py"
    script.write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match=diagnostic):
        exporter._reject_unshipped_web_dependencies((script,))


def test_web_dependency_gate_accepts_compute_for_build_time_cpu_lowering(
    monkeypatch, tmp_path
):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    script = tmp_path / "compute.py"
    script.write_text(
        "import infernux as inx\n"
        "@inx.compute.function\n"
        "def helper(value):\n"
        "    return value\n"
        "@inx.compute.kernel\n"
        "def sample(values):\n"
        "    i = inx.compute.index(values)\n"
        "    values[i] = helper(values[i])\n",
        encoding="utf-8",
    )

    exporter._reject_unshipped_web_dependencies((script,))


def test_web_build_cache_is_project_owned_by_default(monkeypatch, tmp_path):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    request = _request(tmp_path)

    assert exporter._web_staging_directory(request) == (
        (tmp_path / "project").resolve() / "Cache/Build/WebHost/web-wasm32"
    )


def test_web_build_cache_accepts_an_explicit_shared_root(monkeypatch, tmp_path):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    shared = tmp_path / "fast-build-disk"
    request = BuildRequest(
        str(tmp_path / "project"),
        "web-wasm32",
        str(tmp_path / "output"),
        BuildProfile(options={"build_cache_root": str(shared)}),
    )

    assert exporter._web_staging_directory(request) == (
        shared.resolve() / "WebHost/web-wasm32"
    )


def test_webgpu_capability_inventory_is_explicit_and_fire_forced(monkeypatch):
    _web_module(monkeypatch)
    capability_module = importlib.import_module("infernux_web.capabilities")
    document = capability_module.webgpu_capability_inventory()

    capability_module.validate_webgpu_capability_inventory(document)
    features = {feature["id"]: feature for feature in document["features"]}
    assert document["policy"] == "fire-forced"
    assert document["reference_backend"] == "vulkan"
    assert set(document["required_feature_ids"]) == {
        feature_id
        for feature_id, feature in features.items()
        if feature["required_for_040"]
    }
    assert all(
        features[feature_id]["status"] != "unsupported"
        for feature_id in document["required_feature_ids"]
    )
    assert features["material.custom-surface"]["status"] == "unsupported"
    assert features["material.custom-surface"]["required_for_040"] is False
    fixed_frame_features = {
        "render.pbr-material",
        "animation.skeletal",
        "shadow.directional",
        "render.transparent",
        "render.line-renderer",
        "particles.gpu-sprite",
        "post.bloom-hdr",
        "post.aces",
    }
    assert all(
        "web.smoke.vulkan-webgpu-fixed-frame" in features[feature_id]["validation"]
        for feature_id in fixed_frame_features
    )
    assert all(
        features[feature_id]["parity_gate"] == "closed"
        for feature_id in fixed_frame_features
    )
    assert features["lighting.point-lights"]["required_for_040"] is True
    assert features["lighting.point-lights"]["parity_gate"] == "closed"
    assert features["lighting.spot-lights"]["status"] == "unsupported"


def test_webgpu_capability_inventory_rejects_required_unsupported_feature(monkeypatch):
    _web_module(monkeypatch)
    capability_module = importlib.import_module("infernux_web.capabilities")
    document = capability_module.webgpu_capability_inventory()
    pbr = next(
        feature
        for feature in document["features"]
        if feature["id"] == "render.pbr-material"
    )
    pbr["status"] = "unsupported"
    pbr["reason"] = "fixture"

    with pytest.raises(ValueError, match="render.pbr-material is unsupported"):
        capability_module.validate_webgpu_capability_inventory(document)


def test_webgpu_capability_inventory_rejects_required_open_parity_gate(monkeypatch):
    _web_module(monkeypatch)
    capability_module = importlib.import_module("infernux_web.capabilities")
    document = capability_module.webgpu_capability_inventory()
    pbr = next(
        feature
        for feature in document["features"]
        if feature["id"] == "render.pbr-material"
    )
    pbr["parity_gate"] = "open"

    with pytest.raises(ValueError, match="open parity gate"):
        capability_module.validate_webgpu_capability_inventory(document)


def _installed_web_payload(tmp_path):
    root = tmp_path / "installed-plugin"
    player = root / "player"
    player.mkdir(parents=True)
    manifest = {"$schema": "infernux.web_player", "engine": ENGINE_VERSION,
                "platform": "web", "architecture": "wasm32", "python_abi": "cp313",
                "configuration": "Release",
                "python_runtime": _web_python_runtime_manifest()}
    (player / "Player.inxmanifest").write_text(json.dumps(manifest), encoding="utf-8")
    for suffix in ("js", "wasm", "data"):
        (player / f"infernux-runtime.{suffix}").write_bytes(suffix.encode())
    for host, suffix in (("windows-x64", ".exe"), ("linux-x64", "")):
        tools = root / "tools" / host
        tools.mkdir(parents=True)
        for name in ("glslangValidator", "tint"):
            (tools / f"{name}{suffix}").write_bytes(b"native tool")
    return root


def test_web_doctor_accepts_installed_payload_without_compilers(monkeypatch, tmp_path):
    module = _web_module(monkeypatch)
    doctor = importlib.import_module("infernux_web.doctor")
    root = _installed_web_payload(tmp_path)
    monkeypatch.setattr(doctor, "__file__", str(root / "doctor.py"))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("INFERNUX_SOURCE_ROOT", raising=False)
    report = module.inspect_web_toolchain("web-wasm32")
    assert report.available
    assert report.diagnostics == ()
    assert report.details["player"]["python_abi"] == "cp313"
    assert Path(report.details["tint"]).is_relative_to(root)


def test_web_python_payload_identity_is_content_and_path_stable(monkeypatch, tmp_path):
    _web_module(monkeypatch)
    native_payload = importlib.import_module("infernux_web.native_payload")
    payload = tmp_path / "numpy-payload"
    (payload / "numpy").mkdir(parents=True)
    (payload / "numpy/__init__.py").write_bytes(b"version = 'fixture'\n")
    (payload / "static").mkdir()
    (payload / "static/multiarray.a").write_bytes(b"wasm archive")

    first = native_payload.python_package_payload_identity(payload)
    second = native_payload.python_package_payload_identity(payload)

    assert first == second
    assert first["algorithm"] == "infernux-tree-sha256-v1"
    assert first["files"] == 2
    assert first["bytes"] == len(b"version = 'fixture'\nwasm archive")
    (payload / "numpy/__init__.py").write_bytes(b"version = 'changed'\n")
    assert native_payload.python_package_payload_identity(payload)["sha256"] != first["sha256"]


def test_web_python_payload_verification_rehashes_exact_preload_tree(monkeypatch, tmp_path):
    _web_module(monkeypatch)
    native_payload = importlib.import_module("infernux_web.native_payload")
    payload = tmp_path / "numpy-payload"
    (payload / "site-packages/numpy").mkdir(parents=True)
    package_file = payload / "site-packages/numpy/__init__.py"
    package_file.write_bytes(b"__version__ = '2.2.5'\n")
    (payload / "archives").mkdir()
    archives = []
    for module in native_payload.WEB_NUMPY_BUILTIN_MODULES:
        relative = f"archives/{module}.a"
        (payload / relative).write_bytes(module.encode())
        archives.append(relative)
    (payload / "numpy_builtin_registry.c").write_bytes(b"int fixture;\n")
    (payload / "numpy_static_link.json").write_text(
        json.dumps({
            "schema": "infernux.numpy_static_payload.v1",
            "modules": list(native_payload.WEB_NUMPY_BUILTIN_MODULES),
            "archives": archives,
            "registry": "numpy_builtin_registry.c",
            "preload": "site-packages",
        }),
        encoding="utf-8",
    )
    identity = native_payload.python_package_payload_identity(payload)
    runtime = _web_python_runtime_manifest()
    runtime["packages"][0].update(
        payload_hash_algorithm=identity["algorithm"],
        payload_sha256=identity["sha256"],
        payload_bytes=identity["bytes"],
        payload_files=identity["files"],
    )

    assert native_payload.verify_python_package_payload(
        payload, runtime, package_name="numpy"
    ) == identity
    for field in ("payload_sha256", "payload_bytes", "payload_files"):
        stale = json.loads(json.dumps(runtime))
        value = stale["packages"][0][field]
        stale["packages"][0][field] = ("0" * 64) if isinstance(value, str) else value + 1
        with pytest.raises(ValueError, match="does not match manifest"):
            native_payload.verify_python_package_payload(
                payload, stale, package_name="numpy"
            )
    package_file.write_bytes(b"__version__ = 'stale'\n")
    with pytest.raises(ValueError, match="does not match manifest.*sha256"):
        native_payload.verify_python_package_payload(
            payload, runtime, package_name="numpy"
        )


@pytest.mark.parametrize("missing", ["Player.inxmanifest", "infernux-runtime.wasm",
                                     "infernux-runtime.data"])
def test_web_doctor_reports_missing_payload(monkeypatch, tmp_path, missing):
    module = _web_module(monkeypatch)
    doctor = importlib.import_module("infernux_web.doctor")
    root = _installed_web_payload(tmp_path)
    (root / "player" / missing).unlink()
    monkeypatch.setattr(doctor, "__file__", str(root / "doctor.py"))
    report = module.inspect_web_toolchain("web-wasm32")
    assert not report.available
    assert [item.code for item in report.diagnostics] == ["web.player.payload"]


def test_web_doctor_rejects_incompatible_engine(monkeypatch, tmp_path):
    module = _web_module(monkeypatch)
    doctor = importlib.import_module("infernux_web.doctor")
    root = _installed_web_payload(tmp_path)
    manifest = root / "player/Player.inxmanifest"
    manifest.write_text(manifest.read_text().replace(ENGINE_VERSION, "0.3.7"))
    monkeypatch.setattr(doctor, "__file__", str(root / "doctor.py"))
    report = module.inspect_web_toolchain("web-wasm32")
    assert not report.available
    assert "does not match" in report.diagnostics[0].message


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda runtime: runtime.update(platform_version="4.0.9"), "ABI does not match"),
        (lambda runtime: runtime.update(packages=[]), "package closure is missing"),
        (
            lambda runtime: runtime["packages"][0].update(linkage="dynamic-side-module"),
            "package ABI is incompatible",
        ),
        (
            lambda runtime: runtime["packages"][0].update(source_sha256="2" * 64),
            "source provenance does not match",
        ),
        (
            lambda runtime: runtime["packages"][0].update(payload_sha256="not-a-hash"),
            "payload hash is invalid",
        ),
    ],
)
def test_web_doctor_rejects_missing_or_wrong_abi_python_runtime(
    monkeypatch, tmp_path, mutation, message
):
    module = _web_module(monkeypatch)
    doctor = importlib.import_module("infernux_web.doctor")
    root = _installed_web_payload(tmp_path)
    manifest_path = root / "player/Player.inxmanifest"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutation(manifest["python_runtime"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(doctor, "__file__", str(root / "doctor.py"))

    report = module.inspect_web_toolchain("web-wasm32")

    assert not report.available
    assert message in report.diagnostics[0].message


@pytest.mark.parametrize("mode", ["windowed", "fullscreen_borderless"])
def test_web_assembly_reuses_runtime_and_packs_project_without_compilation(
    monkeypatch, tmp_path, mode
):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    pipeline = importlib.import_module("infernux_web.shader_pipeline")
    from infernux.engine.player_package_native import read_entry

    root = _installed_web_payload(tmp_path)
    request = _request(tmp_path)
    staging = tmp_path / "staging"
    assets = staging / "player-assets"
    assets.mkdir(parents=True)
    (assets / "包内文本.txt").write_text("button reads this content", encoding="utf-8")
    branding = assets / "web-branding"
    branding.mkdir()
    for name in ("infernux-favicon.png", "infernux-icon-192.png", "infernux-icon-512.png",
                 "infernux.webmanifest", "infernux-branding.js"):
        (branding / name).write_bytes(b"branding")
    exporter._stage_webgpu_capability_inventory(assets)
    (staging / "shader-cook").mkdir()
    compiler_calls = []

    def compile_shaders(manifest, output, **tools):
        compiler_calls.append(tools)
        output.mkdir()
        (output / "catalog.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pipeline, "compile_shader_manifest", compile_shaders)
    details = {"player_root": str(root / "player"),
               "glslang": str(root / "tools/windows-x64/glslangValidator.exe"),
               "tint": str(root / "tools/windows-x64/tint.exe")}
    presentation = {"display_mode": mode, "window_width": 960, "window_height": 540}
    exporter._assemble_web_host(
        request, staging, assets, details, ROOT / "python/infernux", presentation,
        PLUGIN_EDITOR / "infernux_web/templates/host/shell.html", None,
    )
    artifacts, revision = exporter._publish_web_player(request, staging / "host-build", None)
    output = Path(request.output_dir)
    package = output / f"infernux-player.{revision}.inxpkg"
    assert read_entry(package, "包内文本.txt").decode() == "button reads this content"
    assert read_entry(package, "web-shaders/catalog.json") == b"{}"
    assert not (output / "player-assets").exists()
    assert not list(output.rglob("CMakeCache.txt"))
    for suffix in ("js", "wasm", "data"):
        assert (output / f"infernux-player.{revision}.{suffix}").read_bytes() == (
            root / "player" / f"infernux-runtime.{suffix}"
        ).read_bytes()
    html = (output / "infernux-player.html").read_text(encoding="utf-8")
    assert 'Module.infernuxPresentation = ' in html
    assert f'"mode": "{mode}"' in html
    assert f"fetch('infernux-player.{revision}.inxpkg')" in html
    assert "addRunDependency('infernux-project-content')" in html
    assert "Module.FS.writeFile" in html
    assert "Module.abort(error.message)" in html
    assert "@INFERNUX_WEB_" not in html
    assert "{{{ SCRIPT }}}" not in html
    assert len(compiler_calls) == 1
    assert all(Path(item.path).is_relative_to(output) for item in artifacts)


def test_web_publish_html_switch_failure_keeps_previous_generation_complete(
    monkeypatch, tmp_path
):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    capabilities = importlib.import_module("infernux_web.capabilities")
    request = _request(tmp_path)
    output = Path(request.output_dir)
    output.mkdir(parents=True)
    host = tmp_path / "host-build"
    host.mkdir()
    old_revision = "a" * 24
    new_revision = "b" * 24
    old_names = [
        "infernux-player.html",
        *(
            f"infernux-player.{old_revision}.{suffix}"
            for suffix in ("js", "wasm", "data", "inxpkg")
        ),
    ]
    for name in old_names:
        (output / name).write_bytes(f"old:{name}".encode())
    html = (
        f"<script>const assetRevision = '{new_revision}';"
        "const paths = {'infernux-runtime.wasm': "
        "`infernux-player.${assetRevision}.wasm`, 'infernux-runtime.data': "
        "`infernux-player.${assetRevision}.data`};</script>"
        f'<script src="infernux-player.{new_revision}.js"></script>'
    )
    (host / "infernux-player.html").write_text(html, encoding="utf-8")
    for suffix in ("js", "wasm", "data", "inxpkg"):
        (host / f"infernux-player.{new_revision}.{suffix}").write_bytes(
            f"new:{suffix}".encode()
        )
    for name in (
        "infernux-logo.png",
        "infernux-favicon.png",
        "infernux-icon-192.png",
        "infernux-icon-512.png",
        "infernux.webmanifest",
        "infernux-branding.js",
    ):
        (host / name).write_bytes(f"new:{name}".encode())
    (host / exporter.WEBGPU_CAPABILITY_FILENAME).write_text(
        json.dumps(capabilities.webgpu_capability_inventory()), encoding="utf-8"
    )
    original_replace = os.replace
    replaced = []

    def fail_html_switch(source, destination, *args, **kwargs):
        replaced.append(Path(destination).name)
        if Path(destination).name == "infernux-player.html":
            raise OSError("injected HTML switch failure")
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(exporter.os, "replace", fail_html_switch)

    with pytest.raises(OSError, match="injected HTML switch failure"):
        exporter._publish_web_player(request, host, None)

    for name in old_names:
        assert (output / name).read_bytes() == f"old:{name}".encode()
    for suffix in ("js", "wasm", "data", "inxpkg"):
        assert (output / f"infernux-player.{new_revision}.{suffix}").is_file()
    assert replaced[-1] == "infernux-player.html"
    assert not list(output.glob(".*.tmp"))


@pytest.mark.parametrize("had_template,failure", [
    (had_template, failure)
    for had_template in (False, True)
    for failure in ("template_copy", "template_scan", "template_publish", "html_switch",
                    "interrupt", "none", "remove_template", "remove_template_failure")
] + [(True, "rollback")])
def test_web_custom_template_failure_preserves_previous_publication(monkeypatch, tmp_path, had_template, failure):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    capabilities = importlib.import_module("infernux_web.capabilities")
    request = _request(tmp_path)
    output = Path(request.output_dir)
    output.mkdir(parents=True)
    host = tmp_path / "host"
    host.mkdir()
    old_revision, revision = "a" * 24, "b" * 24
    old_html = b"old publication"
    (output / "infernux-player.html").write_bytes(old_html)
    for suffix in ("js", "wasm", "data", "inxpkg"):
        (output / f"infernux-player.{old_revision}.{suffix}").write_bytes(b"old resource")
        (host / f"infernux-player.{revision}.{suffix}").write_bytes(b"new resource")
    html = (
        f"const assetRevision = '{revision}'; infernux-player.{revision}.js;"
        "'infernux-runtime.wasm': `infernux-player.${assetRevision}.wasm`,"
        "'infernux-runtime.data': `infernux-player.${assetRevision}.data`;"
        '<link href="web-template/theme/site.css">'
    )
    if failure.startswith("remove_template"):
        html = html.replace('<link href="web-template/theme/site.css">', '')
    (host / "infernux-player.html").write_text(html, encoding="utf-8")
    for name in ("infernux-logo.png", "infernux-favicon.png", "infernux-icon-192.png",
                 "infernux-icon-512.png", "infernux.webmanifest", "infernux-branding.js"):
        (host / name).write_bytes(b"branding")
    (host / exporter.WEBGPU_CAPABILITY_FILENAME).write_text(
        json.dumps(capabilities.webgpu_capability_inventory()), encoding="utf-8"
    )
    template = tmp_path / "custom-template"
    (template / "theme").mkdir(parents=True)
    (template / "shell.html").write_text("input shell", encoding="utf-8")
    (template / "theme/site.css").write_bytes(b"new css")
    published_template = output / "web-template"
    if had_template:
        (published_template / "theme").mkdir(parents=True)
        (published_template / "theme/site.css").write_bytes(b"old css")
    # This directory belongs to somebody else; publication must never sweep
    # generic staging names it did not allocate.
    foreign = output / ".web-template.tmp"
    foreign.mkdir()
    (foreign / "sentinel").write_bytes(b"unrelated")
    copy = exporter.shutil.copy2
    replace = exporter.os.replace
    scandir = exporter.os.scandir

    def fail_copy(source, destination, *args, **kwargs):
        if failure == "template_copy" and Path(source).name == "site.css":
            Path(destination).write_bytes(b"partial")
            raise OSError("owned template copy failure")
        return copy(source, destination, *args, **kwargs)

    def fail_replace(source, destination, *args, **kwargs):
        target, origin = Path(destination), Path(source)
        if failure == "template_publish" and target == published_template and origin.name != "previous":
            raise OSError("owned template publish failure")
        if failure in {"html_switch", "rollback", "remove_template_failure"} and target == output / "infernux-player.html":
            raise OSError("owned html switch failure")
        if failure == "interrupt" and target == output / "infernux-player.html":
            raise KeyboardInterrupt("owned build interruption")
        if failure == "rollback" and origin.name == "previous":
            raise OSError("owned rollback failure")
        return replace(source, destination, *args, **kwargs)

    def fail_scan(path):
        # POSIX rmtree passes directory descriptors to the same os.scandir.
        # Inject only the authored-path failure, preserving real cleanup.
        if failure == "template_scan" and not isinstance(path, int) and Path(path) == template / "theme":
            raise PermissionError("owned template scan failure")
        return scandir(path)

    monkeypatch.setattr(exporter.shutil, "copy2", fail_copy)
    monkeypatch.setattr(exporter.os, "replace", fail_replace)
    if failure == "template_scan":
        monkeypatch.setattr(exporter.os, "scandir", fail_scan)
    selected_template = None if failure.startswith("remove_template") else template
    if failure in {"none", "remove_template"}:
        artifacts, actual_revision = exporter._publish_web_player(request, host, selected_template)
        assert actual_revision == revision
        assert all(Path(artifact.path).is_file() for artifact in artifacts)
        assert (output / "infernux-player.html").read_text(encoding="utf-8") == html
        if failure == "none":
            assert (published_template / "theme/site.css").read_bytes() == b"new css"
            assert not (published_template / "shell.html").exists()
        else:
            assert not published_template.exists()
        assert not list(output.glob(f"infernux-player.{old_revision}.*"))
        assert (foreign / "sentinel").read_bytes() == b"unrelated"
        assert not [path for path in output.glob(".web-template.*") if path != foreign]
        return
    error_type = {"rollback": RuntimeError, "interrupt": KeyboardInterrupt}.get(failure, OSError)
    error_message = "retained template workspace" if failure == "rollback" else "owned"
    with pytest.raises(error_type, match=error_message):
        exporter._publish_web_player(request, host, selected_template)
    assert (output / "infernux-player.html").read_bytes() == old_html
    for suffix in ("js", "wasm", "data", "inxpkg"):
        assert (output / f"infernux-player.{old_revision}.{suffix}").read_bytes() == b"old resource"
    if failure == "rollback":
        workspaces = [path for path in output.glob(".web-template.*") if path != foreign]
        assert len(workspaces) == 1
        assert (workspaces[0] / "previous/theme/site.css").read_bytes() == b"old css"
        assert (foreign / "sentinel").read_bytes() == b"unrelated"
        return
    if had_template:
        assert (published_template / "theme/site.css").read_bytes() == b"old css"
    else:
        assert not published_template.exists()
    assert (foreign / "sentinel").read_bytes() == b"unrelated"
    assert not [path for path in output.glob(".web-template.*") if path != foreign]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows template junction boundaries")
@pytest.mark.parametrize("placement", ["root", "child"])
def test_web_template_staging_rejects_junctions_without_copying_external_files(monkeypatch, tmp_path, placement):
    import _winapi
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    source = tmp_path / "source"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "private.txt"
    sentinel.write_bytes(b"external content")
    if placement == "child":
        source.mkdir()
    junction = source if placement == "root" else source / "linked"
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        with pytest.raises(RuntimeError, match="link.*junction"):
            exporter._stage_project_web_template(source, tmp_path / "staged")
        assert sentinel.read_bytes() == b"external content"
        assert not list((tmp_path / "staged").rglob("private.txt"))
    finally:
        if os.path.isjunction(junction):
            os.rmdir(junction)


def test_web_publish_copy_failure_keeps_previous_generation_complete(
    monkeypatch, tmp_path
):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    capabilities = importlib.import_module("infernux_web.capabilities")
    request = _request(tmp_path)
    output = Path(request.output_dir)
    output.mkdir(parents=True)
    host = tmp_path / "host-build"
    host.mkdir()
    old_revision = "c" * 24
    new_revision = "d" * 24
    old_names = [
        "infernux-player.html",
        *(
            f"infernux-player.{old_revision}.{suffix}"
            for suffix in ("js", "wasm", "data", "inxpkg")
        ),
    ]
    old_bytes = {}
    for name in old_names:
        old_bytes[name] = f"old:{name}".encode()
        (output / name).write_bytes(old_bytes[name])
    html = (
        f"<script>const assetRevision = '{new_revision}';"
        "const paths = {'infernux-runtime.wasm': "
        "`infernux-player.${assetRevision}.wasm`, 'infernux-runtime.data': "
        "`infernux-player.${assetRevision}.data`};</script>"
        f'<script src="infernux-player.{new_revision}.js"></script>'
    )
    (host / "infernux-player.html").write_text(html, encoding="utf-8")
    for suffix in ("js", "wasm", "data", "inxpkg"):
        (host / f"infernux-player.{new_revision}.{suffix}").write_bytes(
            f"new:{suffix}".encode()
        )
    for name in (
        "infernux-logo.png",
        "infernux-favicon.png",
        "infernux-icon-192.png",
        "infernux-icon-512.png",
        "infernux.webmanifest",
        "infernux-branding.js",
    ):
        (host / name).write_bytes(f"new:{name}".encode())
    (host / exporter.WEBGPU_CAPABILITY_FILENAME).write_text(
        json.dumps(capabilities.webgpu_capability_inventory()), encoding="utf-8"
    )
    original_copy2 = exporter.shutil.copy2

    def fail_resource_copy(source, destination, *args, **kwargs):
        if Path(source).name == f"infernux-player.{new_revision}.wasm":
            Path(destination).write_bytes(b"partial")
            raise OSError("injected resource copy failure")
        return original_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(exporter.shutil, "copy2", fail_resource_copy)

    with pytest.raises(OSError, match="injected resource copy failure"):
        exporter._publish_web_player(request, host, None)

    for name, expected in old_bytes.items():
        assert (output / name).read_bytes() == expected
    assert not list(output.glob(f"infernux-player.{new_revision}.*"))
    assert not list(output.glob(".*.tmp"))


def test_web_exporter_plan_exposes_real_runtime_stages(monkeypatch, tmp_path):
    module = _web_module(monkeypatch)
    plan = module.WebPlatformExporter().create_plan(_request(tmp_path))

    assert [step.id for step in plan.steps] == [
        "cook",
        "shaders",
        "imports",
        "runtime",
        "package",
        "audit",
    ]
    assert plan.metadata == {
        "architecture": "wasm32",
        "graphics_api": "webgpu",
        "python": "3.13",
    }


def test_web_template_uses_default_or_requires_complete_project_shell(
    monkeypatch, tmp_path
):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    request = _request(tmp_path)
    default_shell = tmp_path / "default-shell.html"
    default_shell.write_text("default", encoding="utf-8")

    assert exporter._resolve_web_template(request, default_shell) == (
        default_shell.resolve(),
        None,
    )

    template = Path(request.project_root) / "ProjectSettings" / "WebTemplate"
    template.mkdir(parents=True)
    with pytest.raises(ValueError, match="missing shell.html"):
        exporter._resolve_web_template(request, default_shell)

    (template / "shell.html").write_text(
        '<canvas id="canvas"></canvas>\n{{{ SCRIPT }}}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required markers"):
        exporter._resolve_web_template(request, default_shell)

    (template / "shell.html").write_text(
        "\n".join(
            (
                '<canvas id="canvas"></canvas>',
                "@INFERNUX_WEB_ASSET_REVISION@",
                "@INFERNUX_WEB_DISPLAY_MODE@",
                "@INFERNUX_WEB_CANVAS_WIDTH@",
                "@INFERNUX_WEB_CANVAS_HEIGHT@",
                "{{{ SCRIPT }}}",
            )
        ),
        encoding="utf-8",
    )
    assert exporter._resolve_web_template(request, default_shell) == (
        (template / "shell.html").resolve(),
        template.resolve(),
    )


def test_web_staging_refresh_removes_obsolete_native_build_cache(monkeypatch, tmp_path):
    _web_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_web.exporter")
    staging = tmp_path / "web-staging"
    host_marker = staging / "host-build" / "CMakeCache.txt"
    host_marker.parent.mkdir(parents=True)
    host_marker.write_text("cache", encoding="utf-8")
    for name in (".infernux-player-cook", "player-assets", "shader-cook"):
        path = staging / name
        path.mkdir()
        (path / "stale.txt").write_text("stale", encoding="utf-8")

    exporter_module._prepare_web_staging(staging)

    assert not host_marker.exists()
    assert not (staging / ".infernux-player-cook").exists()
    assert not (staging / "player-assets").exists()
    assert not (staging / "shader-cook").exists()


def test_web_export_publishes_versioned_cooked_player(monkeypatch, tmp_path):
    module = _web_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_web.exporter")
    exporter = module.WebPlatformExporter()
    monkeypatch.setattr(
        exporter,
        "doctor",
        lambda _request: importlib.import_module("infernux.engine.build").CapabilityReport(
            True,
            details={
                "distribution": "fixture",
                "emsdk_root": "/fixture/emsdk",
                "cpython_root": "/fixture/cpython",
                "source_root": str(ROOT),
                "player": {"python_runtime": _web_python_runtime_manifest()},
            },
        ),
    )
    request = _request(tmp_path)
    project_template = (
        Path(request.project_root) / "ProjectSettings" / "WebTemplate"
    )
    project_template.mkdir(parents=True)
    (project_template / "shell.html").write_text(
        "\n".join(
            (
                '<canvas id="canvas"></canvas>',
                "@INFERNUX_WEB_ASSET_REVISION@",
                "@INFERNUX_WEB_DISPLAY_MODE@",
                "@INFERNUX_WEB_CANVAS_WIDTH@",
                "@INFERNUX_WEB_CANVAS_HEIGHT@",
                "{{{ SCRIPT }}}",
            )
        ),
        encoding="utf-8",
    )
    (project_template / "theme").mkdir()
    (project_template / "theme" / "site.css").write_text(
        "body { background: black; }\n",
        encoding="utf-8",
    )
    player_assets = tmp_path / "player-assets"
    player_assets.mkdir()
    monkeypatch.setattr(
        exporter_module,
        "_cook_web_player_assets",
        lambda _request, _staging: ("Balance", player_assets, ()),
    )
    monkeypatch.setattr(
        exporter_module,
        "_stage_engine_python_package",
        lambda _request, _assets, _source_root: None,
    )
    monkeypatch.setattr(
        exporter_module,
        "_stage_web_branding",
        lambda _assets, _source_root, _game_name: None,
    )
    monkeypatch.setattr(
        exporter_module,
        "_stage_web_shader_sources",
        lambda _request, _staging, _assets, _source_root: None,
    )
    revision = "1234567890abcdef12345678"

    captured_presentation = {}
    captured_template = {}

    def _build_host(
        _request,
        staging,
        _assets,
        _details,
        _source_root,
        presentation,
        web_shell,
        web_template,
    ):
        captured_presentation.update(presentation)
        captured_template.update(shell=web_shell, root=web_template)
        host_build = staging / "host-build"
        host_build.mkdir(parents=True)
        (host_build / "infernux-player.html").write_text(
            "\n".join(
                (
                    f"const assetRevision = '{revision}';",
                    f"infernux-player.{revision}.js",
                    "'infernux-runtime.wasm': `infernux-player.${assetRevision}.wasm`,",
                    "'infernux-runtime.data': `infernux-player.${assetRevision}.data`,",
                )
            ),
            encoding="utf-8",
        )
        for suffix in ("js", "wasm", "data", "inxpkg"):
            (host_build / f"infernux-player.{revision}.{suffix}").write_bytes(
                suffix.encode("ascii")
            )
        (host_build / "infernux-logo.png").write_bytes(b"png")
        for name in (
            "infernux-favicon.png",
            "infernux-icon-192.png",
            "infernux-icon-512.png",
            "infernux.webmanifest",
            "infernux-branding.js",
        ):
            (host_build / name).write_bytes(name.encode("ascii"))
        shutil.copy2(
            player_assets / "infernux-webgpu-capabilities.json",
            host_build / "infernux-webgpu-capabilities.json",
        )
        return ("host built",)

    monkeypatch.setattr(exporter_module, "_assemble_web_host", _build_host)

    result = exporter.execute(request, exporter.create_plan(request))

    assert result.success
    assert result.diagnostics == ()
    assert [item.kind for item in result.artifacts] == [
        "html",
        "js",
        "wasm",
        "data",
        "inxpkg",
        "png",
        "png",
        "png",
        "png",
        "webmanifest",
        "js",
        "json",
        "css",
    ]
    assert all(Path(item.path).is_file() for item in result.artifacts)
    assert result.manifest["scope"] == "cooked-player"
    assert result.manifest["game"] == "Balance"
    assert result.manifest["asset_revision"] == revision
    assert result.manifest["entry_point"] == "infernux-player.html"
    assert result.manifest["python_runtime"] == _web_python_runtime_manifest()
    assert result.manifest["web_template"] == "project"
    assert result.manifest["presentation"] == {
        "display_mode": "fullscreen_borderless",
        "window_width": 1280,
        "window_height": 720,
    }
    capability_manifest = result.manifest["webgpu_capabilities"]
    assert capability_manifest["path"] == "infernux-webgpu-capabilities.json"
    assert capability_manifest["open_parity_gates"] == []
    published_capabilities = json.loads(
        (
            Path(request.output_dir) / "infernux-webgpu-capabilities.json"
        ).read_text(encoding="utf-8")
    )
    assert published_capabilities["backend"] == "webgpu"
    assert captured_presentation == result.manifest["presentation"]
    assert captured_template == {
        "shell": (project_template / "shell.html").resolve(),
        "root": project_template.resolve(),
    }
    assert (
        Path(request.output_dir) / "web-template" / "theme" / "site.css"
    ).read_text(encoding="utf-8") == "body { background: black; }\n"
    assert result.logs == ("host built",)


def test_web_branding_uses_cooked_project_icon_and_game_name(monkeypatch, tmp_path):
    _web_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_web.exporter")
    from PIL import Image

    player_assets = tmp_path / "player-assets"
    data_root = player_assets / "BrandingGame_Data"
    icon = data_root / "Branding" / "project-icon.png"
    icon.parent.mkdir(parents=True)
    Image.new("RGBA", (96, 48), (240, 96, 48, 255)).save(icon)
    from infernux.engine.player_package_native import read_manifest, write_pack

    write_pack(
        (("Branding/project-icon.png", icon),),
        data_root / "Content.inxpkg",
    )
    build_manifest = tmp_path / "BuildManifest.json"
    build_manifest.write_text(
        json.dumps({"icon_path": "Branding/project-icon.png"}),
        encoding="utf-8",
    )
    runtime_catalog = tmp_path / "RuntimeAssetCatalog.json"
    runtime_catalog.write_text(
        json.dumps(
            {
                "$schema": "infernux.runtime_asset_catalog",
                "player_host": {},
                "packages": [],
                "artifacts": [],
            }
        ),
        encoding="utf-8",
    )
    catalog = data_root / "AssetCatalog.inxcat"
    write_pack(
        (
            ("RuntimeAssetCatalog.json", runtime_catalog),
            ("BuildManifest.json", build_manifest),
        ),
        catalog,
    )
    catalog_manifest = read_manifest(catalog)
    (data_root / "PackageIndex.inxmanifest").write_text(
        "INFERNUX_PLAYER_PACKAGE_INDEX\n"
        f"catalog\t{catalog_manifest['archive_sha256']}\t"
        f"{catalog_manifest['archive_bytes']}\n",
        encoding="ascii",
    )

    branding = exporter_module._stage_web_branding(
        player_assets,
        ROOT / "python/infernux",
        "Branding Game",
    )

    for name, size in (
        ("infernux-favicon.png", 32),
        ("infernux-icon-192.png", 192),
        ("infernux-icon-512.png", 512),
    ):
        with Image.open(branding / name) as generated:
            assert generated.mode == "RGBA"
            assert generated.size == (size, size)
    manifest = json.loads(
        (branding / "infernux.webmanifest").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "Branding Game"
    assert [item["sizes"] for item in manifest["icons"]] == ["192x192", "512x512"]
    branding_script = (branding / "infernux-branding.js").read_text(
        encoding="utf-8"
    )
    assert '"game_name":"Branding Game"' in branding_script
    assert "document.title" in branding_script


def test_web_scene_shader_passes_pinned_tint_validation():
    renderer = (
        ROOT / "external" / "plugins" / "infernux_web" / "native" / "WebSceneRenderer.cpp"
    ).read_text(encoding="utf-8")
    match = re.search(
        r'constexpr char kSceneShader\[\] = R"wgsl\((.*?)\)wgsl";',
        renderer,
        re.DOTALL,
    )
    assert match is not None
    platform_name = "windows-x64" if sys.platform == "win32" else "linux-x64"
    executable = "tint.exe" if sys.platform == "win32" else "tint"
    configured = os.environ.get("INFERNUX_TEST_TINT")
    tint = (Path(configured) if configured else
            PLUGIN_EDITOR / "infernux_web" / "tools" / platform_name / executable)
    if configured:
        assert tint.is_file(), f"Configured pinned Tint does not exist: {tint}"
    if not tint.is_file():
        pytest.skip(f"pinned Tint is unavailable for {platform_name}; set INFERNUX_TEST_TINT to the built tool")
    with tempfile.TemporaryDirectory(prefix="web-wgsl-", dir=ROOT / "out") as temp_dir:
        shader = Path(temp_dir) / "WebSceneRenderer.wgsl"
        shader.write_text(match.group(1), encoding="utf-8")
        completed = subprocess.run(
            [str(tint), "--input-format", "wgsl", "--format", "wgsl", str(shader)],
            capture_output=True,
            text=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_web_host_build_templates_are_editor_only():
    plugin_root = ROOT / "external" / "plugins" / "infernux_web"
    host_templates = (
        plugin_root
        / "package"
        / "editor"
        / "infernux_web"
        / "templates"
        / "host"
    )
    assert not (plugin_root / "package" / "runtime" / "web").exists()
    assert (plugin_root / "native/CMakeLists.txt").is_file()
    assert (plugin_root / "native/main.cpp").is_file()
    assert not list((plugin_root / "package").rglob("CMakeLists.txt"))
    assert not list((plugin_root / "package").rglob("*.cpp"))
    assert (PLUGIN_EDITOR / "infernux_web/templates/host/shell.html").is_file()
    for path in host_templates.rglob("*"):
        if path.is_file():
            relative = path.relative_to(plugin_root / "package").as_posix()
            assert player_file_exported({}, relative) is False


def test_web_shader_stage_deduplicates_shared_particle_kernel(monkeypatch, tmp_path):
    _web_module(monkeypatch)
    exporter = importlib.import_module("infernux_web.exporter")
    native = importlib.import_module("infernux.lib")._Infernux
    monkeypatch.setattr(
        native,
        "_prepare_authored_shader_glsl",
        lambda source, _path: source,
    )
    request = _request(tmp_path)
    source_root = tmp_path / "source"
    shader_dir = source_root / "resources" / "shaders"
    shader_dir.mkdir(parents=True)
    (shader_dir / "fullscreen_triangle.vert").write_text(
        "#version 450\nvoid main() {}\n", encoding="utf-8"
    )
    player_assets = tmp_path / "player-assets"
    (player_assets / "Balance_Data").mkdir(parents=True)
    # Shader staging consumes the actual Player catalog even when this
    # fixture has no UI materials. Keep its package boundary valid.
    from infernux.engine.player_package_native import write_pack
    runtime_catalog = tmp_path / "RuntimeAssetCatalog.json"
    asset_records = tmp_path / "RuntimeAssetRecords.json"
    asset_records.write_text(json.dumps({"entries": []}), encoding="utf-8")
    record_path = "Library/RuntimeAssetRecords.json"
    runtime_catalog.write_text(json.dumps({"artifacts": [{
        "runtime_artifact_id": "records", "runtime_path": record_path,
        "package": "Balance_Data/Content.inxpkg", "dependencies": [],
    }]}), encoding="utf-8")
    write_pack(((record_path, asset_records),),
               player_assets / "Balance_Data" / "Content.inxpkg")
    write_pack((("RuntimeAssetCatalog.json", runtime_catalog),),
               player_assets / "Balance_Data" / "AssetCatalog.inxcat")
    particle_dir = (
        Path(request.project_root) / "Library" / "Artifacts" / "Particle"
    )
    particle_dir.mkdir(parents=True)
    kernel_hash = "a" * 64
    stage_names = {
        "bootstrap",
        "init",
        "update",
        "update_rendering_fused",
        "contact_prepare",
        "contact_solve",
        "contact_dispatch",
        "render_reset",
        "rendering",
    }
    emitters = []
    for stable_id in ("emitter-b", "emitter-a"):
        emitters.append(
            {
                "stable_id": stable_id,
                "kernel_hash": kernel_hash,
                "state_stride": 80,
                "event_type_count": 0,
                "collision_enabled": False,
                "continuation": None,
                "update_render_fusion": {"eligible": True},
                "data_interface_layout": {
                    "mesh_interfaces": [],
                    "texture2d_parameters": [],
                    "volume_interfaces": [],
                },
                "stages": {
                    name: "#version 450\nlayout(local_size_x=1) in; void main() {}\n"
                    for name in stage_names
                },
            }
        )
    (particle_dir / "shared.inxparticle").write_text(
        json.dumps({"gpu_glsl": {"emitters": emitters}}), encoding="utf-8"
    )

    exporter._stage_web_shader_sources(
        request, tmp_path / "staging", player_assets, source_root
    )

    catalog = json.loads(
        (player_assets / "web-particles" / "catalog.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(catalog["kernels"]) == 1
    assert catalog["kernels"][0]["stable_ids"] == ["emitter-a", "emitter-b"]
    assert len(catalog["kernels"][0]["stages"]) == len(stage_names)

def test_web_asset_revision_covers_content_runtime_and_shader_inputs(
    monkeypatch, tmp_path
):
    _web_module(monkeypatch)
    exporter_module = importlib.import_module("infernux_web.exporter")
    staging = tmp_path / "staging"
    player = staging / "player-assets"
    runtime = tmp_path / "runtime"
    shaders = staging / "shader-cook"
    for root in (player, runtime, shaders):
        root.mkdir(parents=True)
    (player / "Content.inxpkg").write_bytes(b"content")
    (runtime / "main.cpp").write_bytes(b"runtime")
    (shaders / "manifest.json").write_bytes(b"shader")
    project_template = tmp_path / "project-template"
    project_template.mkdir()
    (project_template / "shell.html").write_bytes(b"custom shell")

    fullscreen = {
        "display_mode": "fullscreen_borderless",
        "window_width": 1280,
        "window_height": 720,
    }
    first = exporter_module._web_asset_revision(
        staging, player, runtime, presentation=fullscreen
    )
    (runtime / "main.cpp").write_bytes(b"runtime changed")
    second = exporter_module._web_asset_revision(
        staging, player, runtime, presentation=fullscreen
    )
    bytecode = player / "python" / "site-packages" / "infernux" / "__pycache__"
    bytecode.mkdir(parents=True)
    (bytecode / "runtime.cpython-313.opt-1.pyc").write_bytes(b"compiled")
    third = exporter_module._web_asset_revision(
        staging, player, runtime, presentation=fullscreen
    )
    windowed = exporter_module._web_asset_revision(
        staging,
        player,
        runtime,
        presentation={**fullscreen, "display_mode": "windowed"},
    )
    custom = exporter_module._web_asset_revision(
        staging,
        player,
        runtime,
        presentation=fullscreen,
        project_template_root=project_template,
    )
    (project_template / "shell.html").write_bytes(b"custom shell changed")
    custom_changed = exporter_module._web_asset_revision(
        staging,
        player,
        runtime,
        presentation=fullscreen,
        project_template_root=project_template,
    )

    assert re.fullmatch(r"[0-9a-f]{24}", first)
    assert first != second
    assert second != third
    assert third != windowed
    assert custom != custom_changed
