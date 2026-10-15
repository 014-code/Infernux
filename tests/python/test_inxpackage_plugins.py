from __future__ import annotations
import io
import json
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace
import pytest
from infernux.version import ENGINE_VERSION
import infernux.plugins.manager as plugin_manager_module
import infernux.plugins.preload as preload_module
import infernux.plugins.github_releases as github_releases_module
import infernux.plugins.cache as plugin_cache_module
from infernux.engine import player_package_native
from infernux.application import Application
from infernux.engine.project_context import (
    set_project_root,
    set_runtime_asset_query,
    set_runtime_asset_resolver,
)
from infernux.plugins import (
    InxPackage,
    SharedPackageCache,
    PackageConflictError,
    PackageUpdateConflict,
    PluginManager,
    PluginRegistry,
    player_file_exported,
)
from infernux.plugins.official import (
    OfficialCatalogError,
    bootstrap_new_project,
    install_bundled_packages,
    sync_official_registry,
)
from infernux.plugins.content import normalize_page_descriptor
from infernux.plugins.github_releases import (
    RELEASE_MANIFEST_NAME,
    download_github_source,
    release_manifest_name,
    resolve_github_release,
)
from infernux.plugins.cli import main as package_cli_main
from infernux.ui import UIButton


class _FakeInxPack:
    archives: dict[str, dict[str, bytes]] = {}

    @classmethod
    def _write(cls, sources, destination, compression_level=None, profile="development"):
        entries = {
            str(logical).replace("\\", "/"): Path(source).read_bytes()
            for logical, source in sorted(sources)
        }
        path = os.path.abspath(destination)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"INXPKG-test")
        cls.archives[path] = entries
        return cls._manifest(path)

    @classmethod
    def _manifest(cls, path):
        entries = cls.archives[os.path.abspath(path)]
        return {
            "format": "infernux-native-inxpack",
            "file_count": len(entries),
            "files": [
                {
                    "path": name,
                    "raw_bytes": len(payload),
                    "stored_bytes": len(payload),
                }
                for name, payload in entries.items()
            ],
        }

    @classmethod
    def _read_manifest(cls, path):
        return cls._manifest(path)

    @classmethod
    def _read_entry(cls, path, entry):
        return cls.archives[os.path.abspath(path)][entry]

    _inxpack_write = _write
    _inxpack_read_manifest = _read_manifest
    _inxpack_read_entry = _read_entry


@pytest.fixture(autouse=True)
def _fake_inxpack(tmp_path, monkeypatch):
    from infernux.plugins.cache import SharedPackageCache
    original_store = SharedPackageCache.store

    def store(cache, source, **kwargs):
        destination = original_store(cache, source, **kwargs)
        if os.path.abspath(source) in _FakeInxPack.archives:
            _FakeInxPack.archives[os.path.abspath(destination)] = dict(
                _FakeInxPack.archives[os.path.abspath(source)]
            )
        return destination

    monkeypatch.setattr(SharedPackageCache, "store", store)
    monkeypatch.setenv(
        "INFERNUX_PACKAGE_CACHE_ROOT",
        str(tmp_path / "hub-package-cache"),
    )
    engine_resources = tmp_path / "engine-resources"
    engine_resources.mkdir()
    monkeypatch.setattr("infernux.resources._package_dir", str(engine_resources))
    _FakeInxPack.archives.clear()
    player_package_native.set_test_backend(_FakeInxPack)
    yield
    manager = PluginManager.instance()
    if manager is not None:
        manager.shutdown()
    player_package_native.set_test_backend(None)


def _project(path: Path) -> Path:
    (path / "Assets").mkdir(parents=True)
    (path / "ProjectSettings").mkdir(parents=True)
    return path


def _meta(guid: str) -> str:
    return json.dumps({"metadata": {"guid": {"type": "string", "value": guid}}})


def _source(
    path: Path, reference: str, *, version: str = "1.0.0", engine: str = ""
) -> Path:
    path.mkdir(parents=True)
    (path / "inx_package.json").write_text(
        json.dumps(
            {
                "reference": reference,
                "name": reference,
                "version": version,
                "engine": engine,
            }
        ),
        encoding="utf-8",
    )
    return path


def _export(source: Path, package: Path) -> Path:
    InxPackage.export_source(str(source), str(package))
    return package


def test_manifestless_authoring_preserves_identity_and_expands_selected_roots(tmp_path):
    project = _project(tmp_path / "project")
    plugin_root = project / "Packages" / "abc"
    materials = plugin_root / "materials"
    materials.mkdir(parents=True)
    (materials / "Neon.mat").write_text("material", encoding="utf-8")
    guid = "0123456789abcdef0123456789abcdef"
    (materials / "Neon.mat.meta").write_text(_meta(guid), encoding="utf-8")
    package = tmp_path / "abc.inxpkg"
    preview = InxPackage.export(str(project), [str(plugin_root)], str(package))
    assert preview.metadata["reference"] == preview.metadata["name"] == "abc"
    assert preview.logical_entries == ("materials/Neon.mat",)
    assert preview.project_entries == ("Assets/Plugins/materials/Neon.mat",)
    assert preview.file_records[0]["guid"] == guid
    archived_meta = json.loads(player_package_native.read_entry(
        str(package), preview.file_records[0]["meta_archive_path"]
    ).decode("utf-8"))
    assert "content_hash" not in archived_meta["metadata"]
    assert archived_meta["metadata"]["guid"]["value"] == guid

    nested = plugin_root / "package"
    nested.mkdir()
    (nested / "inx_package.json").write_text("{}", encoding="utf-8")
    (nested / "payload.bin").write_bytes(b"payload")
    nested_preview = InxPackage.export(str(project), [str(plugin_root)], str(package))
    assert nested_preview.metadata["reference"] == "abc"
    assert nested_preview.logical_entries == (
        "materials/Neon.mat", "package/inx_package.json", "package/payload.bin",
    )

    second = project / "Assets" / "Second"
    second.mkdir()
    (second / "two.txt").write_text("two", encoding="utf-8")
    selection = InxPackage.export(
        str(project), [str(materials), str(second)], str(tmp_path / "Selection.inxpkg")
    )
    assert selection.logical_entries == ("Second/two.txt", "materials/Neon.mat")
    assert selection.project_entries == (
        "Assets/Plugins/Second/two.txt", "Assets/Plugins/materials/Neon.mat",
    )


def test_repository_package_export_install_reexport_and_player_inventory(tmp_path):
    repository = tmp_path / "repository"
    source = _source(repository / "package", "vendor/physics/jolt")
    (repository / "README.md").write_text("repository only", encoding="utf-8")
    (repository / "CMakeLists.txt").write_text("project(plugin)", encoding="utf-8")
    (repository / "build.gradle").write_text("plugins {}", encoding="utf-8")
    payloads = {
        "runtime/backend.py": b"BACKEND = 'jolt'\n",
        "runtime/native_backend.pyd": b"pyd",
        "runtime/web_backend.wasm": b"wasm",
        "editor/compiler.dll": b"dll",
        "editor/vendor/panel.py": b"PANEL_ID = 'vendor.jolt'\n",
        "editor/translations.json": b'{"$schema":"infernux.editor_translations","locales":{"en":{},"zh":{}}}',
        "plugin_pages/usage.md": b"# Jolt\n",
        "README.md": b"Physics backend.\n",
        "LICENSE": b"license",
        "Scenes/Demo.scene": b"scene",
        "Variants/Alternative.inxpkg": b"opaque nested package",
        "materials/neon.mat": b"material",
        "shaders/neon.frag": b"shader",
        "web/index.html": b"<button>plugin page</button>",
        "build/generated_backend.wasm": b"generated-wasm",
        ".well-known/plugin.json": b"{}",
    }
    for logical, payload in payloads.items():
        path = source.joinpath(*logical.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    guid = "0123456789abcdef0123456789abcdef"
    (source / "materials/neon.mat.meta").write_text(_meta(guid), encoding="utf-8")
    package = tmp_path / "Jolt.inxpkg"
    first = InxPackage.export_source(str(repository), str(package))
    repeated = InxPackage.inspect(str(_export(source, tmp_path / "repeated.inxpkg")))
    assert first.metadata["$schema"] == "infernux.inxpackage"
    assert set(first.logical_entries) == set(payloads)
    assert "CMakeLists.txt" not in first.logical_entries
    assert "build.gradle" not in first.logical_entries
    records = {item["logical_path"]: item for item in first.file_records}
    assert len({item["guid"] for item in first.file_records}) == len(first.file_records)
    assert records["materials/neon.mat"]["guid"] == guid
    assert first.file_records == repeated.file_records
    assert first.metadata["control_guid"] == repeated.metadata["control_guid"]
    assert records["runtime/native_backend.pyd"]["role"] == "runtime"
    assert records["editor/translations.json"]["role"] == "editor"
    assert records["plugin_pages/usage.md"]["role"] == "control"
    for logical in (
        "runtime/backend.py", "runtime/native_backend.pyd", "runtime/web_backend.wasm",
        "materials/neon.mat", "shaders/neon.frag", "web/index.html",
    ):
        assert player_file_exported(first.metadata, logical)
        assert player_file_exported(records[logical], logical)
    for logical in (
        "editor/compiler.dll", "editor/translations.json", "editor/vendor/panel.py",
        "plugin_pages/usage.md",
    ):
        assert not player_file_exported(first.metadata, logical)
        assert not player_file_exported(records[logical], logical)
    assert "Packages/vendor/physics/jolt/runtime/backend.py" in first.project_entries
    assert "Packages/vendor/physics/jolt/editor/vendor/panel.py" in first.project_entries
    assert "Assets/Plugins/Scenes/Demo.scene" in first.project_entries
    assert "Assets/Plugins/Variants/Alternative.inxpkg" in first.project_entries

    (source / "runtime/backend.py").write_text("BACKEND = 'updated'\n", encoding="utf-8")
    changed = InxPackage.inspect(str(_export(source, tmp_path / "changed.inxpkg")))
    assert changed.file_records == first.file_records
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    installed = manager.install_package(str(package), install_dependencies=False)
    assert installed.loaded, installed.error
    root = project / "Packages/vendor/physics/jolt"
    assert (root / "runtime/backend.py").read_bytes() == payloads["runtime/backend.py"]
    assert (root / "runtime/native_backend.pyd").read_bytes() == b"pyd"
    assert (project / "Assets/Plugins/Scenes/Demo.scene").is_file()
    assert {item["reference"] for item in manager.registry.installed()} == {"vendor/physics/jolt"}
    for path in (root / "runtime/backend.py", root / "runtime/native_backend.pyd",
                 root / "inx_package.json"):
        metadata = json.loads(Path(str(path) + ".meta").read_text(encoding="utf-8"))["metadata"]
        assert "content_hash" not in metadata
        assert len(metadata["guid"]["value"]) == 32
    destination = tmp_path / "reexported.inxpkg"
    exported = InxPackage.export(str(project), [str(root)], str(destination))
    assert destination.is_file()
    assert exported.metadata["reference"] == "vendor/physics/jolt"
    assert "runtime/backend.py" in exported.logical_entries
    assert InxPackage.inspect(str(destination)).logical_entries == exported.logical_entries

    class UrlEngine:
        def __init__(self):
            self.urls = []

        def open_url(self, url):
            self.urls.append(url)
            return True

    engine = UrlEngine()
    page = project / "Assets/Plugins/web/index.html"
    assert page.read_bytes() == payloads["web/index.html"]
    set_project_root(str(project))
    Application._bind_engine(engine, "player")
    try:
        set_runtime_asset_query(lambda path, *, exact=False: (guid,)
                                if path == "Assets/Plugins/web/index.html" else ())
        set_runtime_asset_resolver(lambda value: str(page.resolve()) if value == guid else None)
        button = UIButton()
        button.on_click.add_listener(lambda: Application.open_url(
            Application.asset_path("Assets/Plugins/web/index.html")))
        button.on_pointer_click(None)
        assert engine.urls == [page.resolve().as_uri()]
    finally:
        Application._unbind_engine(engine)
        set_project_root(None)


def test_selective_reimport_adds_missing_files_without_replacing_existing_ownership(tmp_path, monkeypatch):
    source = _source(tmp_path / "source", "vendor/partial")
    (source / "first.txt").write_text("first", encoding="utf-8")
    (source / "second.txt").write_text("second", encoding="utf-8")
    package = _export(source, tmp_path / "partial.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), selected=("first.txt",))
    before = manager.registry.installed_record("vendor/partial")
    assert before is not None
    first = project / "Assets/Plugins/first.txt"
    first.write_text("user edit", encoding="utf-8")
    manager.set_enabled("vendor/partial", False)
    monkeypatch.setattr(manager, "_install_requirements", lambda *_args, **_kwargs: pytest.fail("Reimport must not reinstall dependencies"))

    manager.install_package(str(package), selected=("second.txt",))

    after = manager.registry.installed_record("vendor/partial")
    assert after["enabled"] is False
    assert first.read_text() == "user edit"
    assert (project / "Assets/Plugins/second.txt").read_text() == "second"
    assert after["files"][0] == before["files"][0]
    assert after["control"] == before["control"]
    assert {item["logical_path"] for item in after["files"]} == {"first.txt", "second.txt"}


@pytest.mark.parametrize("change", ["version", "members"])
def test_selective_reimport_does_not_treat_another_release_as_missing_members(tmp_path, change):
    source = _source(tmp_path / "source", "vendor/partial-release")
    (source / "first.txt").write_text("first", encoding="utf-8")
    (source / "second.txt").write_text("second", encoding="utf-8")
    package = _export(source, tmp_path / "original.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), selected=("first.txt",))
    before = manager.registry.load()
    if change == "version":
        manifest = source / "inx_package.json"
        document = json.loads(manifest.read_text())
        document["version"] = "99.0.0"
        manifest.write_text(json.dumps(document), encoding="utf-8")
    else:
        (source / "third.txt").write_text("new release", encoding="utf-8")
    incoming = _export(source, tmp_path / "different.inxpkg")
    with pytest.raises(RuntimeError, match="different content"):
        manager.install_package(str(incoming))
    assert manager.registry.load() == before
    assert not (project / "Assets/Plugins/second.txt").exists()


def test_selective_reimport_conflict_keeps_the_existing_install(tmp_path):
    source = _source(tmp_path / "source", "vendor/partial-conflict")
    (source / "first.txt").write_text("first", encoding="utf-8")
    (source / "second.txt").write_text("second", encoding="utf-8")
    package = _export(source, tmp_path / "partial-conflict.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), selected=("first.txt",))
    before = manager.registry.load()
    blocked = project / "Assets/Plugins/second.txt"
    blocked.write_text("another asset", encoding="utf-8")
    with pytest.raises(PackageConflictError, match="another GUID"):
        manager.install_package(str(package), selected=("second.txt",))
    assert manager.registry.load() == before
    assert blocked.read_text() == "another asset"
    assert (project / "Assets/Plugins/first.txt").read_text() == "first"


def test_selective_reimport_rollback_retains_previous_files_and_registry(tmp_path, monkeypatch):
    source = _source(tmp_path / "source", "vendor/partial-rollback")
    for name in ("first.txt", "second.txt", "third.txt"):
        (source / name).write_text(name, encoding="utf-8")
    package = _export(source, tmp_path / "partial-rollback.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), selected=("first.txt",))
    before = manager.registry.load()
    write = plugin_manager_module._InstallTransaction.write

    def fail_third(transaction, destination, payload):
        if destination.endswith("third.txt"):
            raise OSError("simulated write failure")
        return write(transaction, destination, payload)

    monkeypatch.setattr(plugin_manager_module._InstallTransaction, "write", fail_third)
    with pytest.raises(OSError, match="simulated write failure"):
        manager.install_package(str(package))
    assert manager.registry.load() == before
    assert (project / "Assets/Plugins/first.txt").read_text() == "first.txt"
    assert not (project / "Assets/Plugins/second.txt").exists()
    assert not (project / "Assets/Plugins/second.txt.meta").exists()
    assert not (project / "Assets/Plugins/third.txt").exists()


def test_runtime_script_publication_tracks_install_and_live_topology_lifetime(tmp_path, monkeypatch, request):
    source = _source(tmp_path / "source", "vendor/mutable-component")
    (source / "runtime").mkdir()
    (source / "runtime/component.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "seed.txt").write_text("initial non-script selection", encoding="utf-8")
    package = _export(source, tmp_path / "MutableComponent.inxpkg")
    project = _project(tmp_path / "project")
    events = []

    class Resources:
        _project_path = str(project.resolve())

        def begin_script_transaction(self, paths):
            events.append(("begin", tuple(Path(path) for path in paths)))
            return "mutable-component"

        def submit_script_change(self, path, **kwargs):
            events.append((Path(path), kwargs))
            return object()

        def process_pending_reloads(self, *, force=False):
            events.append(("drain", force))
            return 0

        def retire_script_paths(self, paths):
            events.append(("retire", tuple(Path(path) for path in paths)))

    monkeypatch.setattr("infernux.engine.resources_manager.ResourcesManager.instance",
                        classmethod(lambda _cls: Resources()))
    manager = PluginManager(str(project))
    request.addfinalizer(manager.preloads.unload_all)
    reference = "vendor/mutable-component"
    root = project / "Packages" / reference
    runtime = root / "runtime/component.py"
    manager.install_package(str(package), selected=("seed.txt",), install_dependencies=False)
    assert events == []
    manager.install_package(str(package), install_dependencies=False)
    assert events[0] == ("begin", (runtime,))
    assert events[1][0] == runtime
    assert events[1][1]["force"] is True
    assert events[1][1]["transaction_id"] == "mutable-component"
    assert events[2] == ("drain", True)
    assert not (project / "Library/InxPackageStaging").exists()
    before = manager.registry.installed_record(reference)["files"]
    events.clear()
    manager.set_enabled(reference, False)
    assert set(events[0][1]) == {runtime}
    manager.set_enabled(reference, True)

    source_bytes = runtime.read_bytes()
    meta_bytes = runtime.with_suffix(".py.meta").read_bytes()
    runtime.unlink()
    runtime.with_suffix(".py.meta").unlink()
    events.clear()
    manager.set_enabled(reference, False)
    assert events[0] == ("retire", (runtime,))
    events.clear()
    manager.set_enabled(reference, True)
    assert events == []
    manager.set_enabled(reference, False)
    runtime.write_bytes(source_bytes)
    runtime.with_suffix(".py.meta").write_bytes(meta_bytes)
    events.clear()
    manager.set_enabled(reference, True)
    assert events[0] == ("begin", (runtime,))

    added = runtime.with_name("added.py")
    added.write_text("ADDED = True\n", encoding="utf-8")
    added.with_suffix(".py.meta").write_text(_meta("c" * 32), encoding="utf-8")
    editor = root / "editor/panel.py"
    editor.parent.mkdir()
    editor.write_text("EDITOR = True\n", encoding="utf-8")
    editor.with_suffix(".py.meta").write_text(_meta("d" * 32), encoding="utf-8")
    events.clear()
    manager.set_enabled(reference, False)
    assert events[0][0] == "retire"
    assert set(events[0][1]) == {runtime, added}
    events.clear()
    manager.set_enabled(reference, True)
    assert events[0][0] == "begin"
    assert set(events[0][1]) == {runtime, added}

    renamed = runtime.with_name("renamed.py")
    runtime.rename(renamed)
    runtime.with_suffix(".py.meta").rename(renamed.with_suffix(".py.meta"))
    events.clear()
    manager.set_enabled(reference, False)
    assert set(events[0][1]) == {runtime, renamed, added}
    events.clear()
    manager.set_enabled(reference, True)
    assert set(events[0][1]) == {renamed, added}

    events.clear()
    manager.set_enabled(reference, False)
    assert set(events[0][1]) == {runtime, renamed, added}
    renamed.rename(runtime)
    renamed.with_suffix(".py.meta").rename(runtime.with_suffix(".py.meta"))
    events.clear()
    manager.set_enabled(reference, True)
    assert set(events[0][1]) == {runtime, added}
    moved_editor = root / "editor/component.py"
    runtime.rename(moved_editor)
    runtime.with_suffix(".py.meta").rename(moved_editor.with_suffix(".py.meta"))
    events.clear()
    manager.set_enabled(reference, False)
    assert set(events[0][1]) == {runtime, added}
    events.clear()
    manager.set_enabled(reference, True)
    assert events[0] == ("begin", (added,))

    assert manager.registry.installed_record(reference)["files"] == before
    author = root / "runtime/author.py"
    author.write_text("AUTHOR = True\n", encoding="utf-8")
    author.with_suffix(".py.meta").write_text(_meta("e" * 32), encoding="utf-8")
    events.clear()
    manager.uninstall(reference)
    assert events[0][0] == "retire"
    assert set(events[0][1]) == {runtime, added, author}
    assert author.is_file() and added.is_file() and editor.is_file()
    events.clear()
    manager.install_package(str(package), install_dependencies=False)
    assert events[0][0] == "begin"
    assert set(events[0][1]) == {runtime, added, author}
    assert runtime.is_file()


def test_player_export_uses_persisted_role_for_pre_pythonic_installed_record():
    assert not player_file_exported(
        {"role": "editor"}, "Editor/legacy_plugin/compiler.py"
    )
    assert not player_file_exported(
        {"role": "control"}, "InxPluginPages/README.md"
    )
    assert player_file_exported(
        {"role": "runtime"}, "Runtime/legacy_plugin/server.jar"
    )


def test_package_metadata_schema_rejects_unknown_fields(tmp_path):
    source = _source(tmp_path / "source", "vendor/current-format")
    (source / "Data.bin").write_bytes(b"payload")
    package = _export(source, tmp_path / "current.inxpkg")
    archive = _FakeInxPack.archives[str(package.resolve())]
    document = json.loads(archive["inx_package.json"].decode("utf-8"))
    document["unexpected"] = True
    archive["inx_package.json"] = (
        json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")

    with pytest.raises(ValueError, match="Unsupported InxPackage metadata schema"):
        InxPackage.inspect(str(package))


def test_plugin_source_descriptor_rejects_unknown_fields(tmp_path):
    manager = PluginManager(str(_project(tmp_path / "project")))

    with pytest.raises(ValueError, match="unknown fields: unexpected"):
        manager.install_source(
            {"type": "local", "location": "plugin.inxpkg", "unexpected": True},
            install_dependencies=False,
        )


def test_engine_compatibility_is_validated_and_enforced_before_install(tmp_path):
    invalid = _source(tmp_path / "invalid", "vendor/invalid-engine", engine=">=oops")
    (invalid / "Data.bin").write_bytes(b"payload")
    with pytest.raises(ValueError, match="engine compatibility is invalid"):
        _export(invalid, tmp_path / "invalid.inxpkg")

    incompatible = _source(
        tmp_path / "incompatible", "vendor/future-engine", engine=">=99"
    )
    (incompatible / "Data.bin").write_bytes(b"payload")
    package = _export(incompatible, tmp_path / "future.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    with pytest.raises(RuntimeError, match=f"current engine is {ENGINE_VERSION}"):
        manager.install_package(str(package), install_dependencies=False)
    assert manager.registry.installed() == ()
    assert not (project / "Assets/Plugins/Data.bin").exists()


@pytest.mark.parametrize("directory", ["Runtime", "RUNTIME", "Editor"])
def test_reserved_layout_requires_canonical_casing(tmp_path, directory):
    source = _source(tmp_path / "source", "vendor/case")
    (source / directory).mkdir()
    (source / directory / "entry.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="canonical casing"):
        _export(source, tmp_path / "bad.inxpkg")


def test_docs_has_no_special_layout_role(tmp_path):
    source = _source(tmp_path / "source", "vendor/docs")
    (source / "Docs").mkdir()
    (source / "Docs" / "Guide.md").write_text("ordinary asset", encoding="utf-8")
    package = _export(source, tmp_path / "docs.inxpkg")
    preview = InxPackage.inspect(str(package))
    assert "Assets/Plugins/Docs/Guide.md" in preview.project_entries
    assert all(page["path"] != "Docs/Guide.md" for page in preview.metadata["pages"])


def test_manifest_cannot_register_information_pages_outside_fixed_layout(tmp_path):
    source = _source(tmp_path / "source", "vendor/strict-pages")
    (source / "Docs").mkdir()
    (source / "Docs" / "Guide.md").write_text("not a plugin page", encoding="utf-8")
    manifest_path = source / "inx_package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pages"] = [
        {"id": "guide", "title": "Guide", "path": "Docs/Guide.md"}
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="page path is invalid"):
        _export(source, tmp_path / "invalid-pages.inxpkg")


@pytest.mark.parametrize("locale", ["zh", "zh-cn", "en", "en-US"])
def test_plugin_page_descriptor_rejects_locale_aliases(locale):
    with pytest.raises(ValueError, match="page locale must be zh-CN"):
        normalize_page_descriptor(
            {
                "id": "intro",
                "title": "Description",
                "path": "plugin_pages/intro.zh-CN.md",
                "locale": locale,
            }
        )


def test_guid_reuse_transfers_ownership_on_uninstall(tmp_path):
    guid = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    packages = []
    for reference in ("aabbc/physics", "aabbc/physics/jolt"):
        source = _source(tmp_path / reference.replace("/", "-"), reference)
        asset = source / "Shared.bin"
        asset.write_bytes(b"same bytes")
        (source / "Shared.bin.meta").write_text(_meta(guid), encoding="utf-8")
        packages.append(_export(source, tmp_path / f"{reference.replace('/', '-')}.inxpkg"))

    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(packages[0]), install_dependencies=False)
    manager.install_package(str(packages[1]), install_dependencies=False)
    original = project / "Assets/Plugins/Shared.bin"
    child_record = manager.registry.installed_record("aabbc/physics/jolt")
    shared = next(item for item in child_record["files"] if item["guid"] == guid)
    assert shared["owned"] is False
    assert Path(project, *shared["path_hint"].split("/")).resolve() == original.resolve()

    manager.uninstall("aabbc/physics")
    assert original.is_file()
    child_record = manager.registry.installed_record("aabbc/physics/jolt")
    shared = next(item for item in child_record["files"] if item["guid"] == guid)
    assert shared["owned"] is True
    manager.uninstall("aabbc/physics/jolt")


def test_uninstall_dependency_blocker_is_case_insensitive(tmp_path):
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.registry.record_install(
        {"reference": "vendor/x"},
        files=[],
        control={
            "guid": "dependency-control-guid",
            "path_hint": "Packages/vendor/x/inx_package.json",
            "owned": False,
        },
    )
    manager.registry.record_install(
        {"reference": "vendor/consumer"},
        files=[],
        control={
            "guid": "consumer-control-guid",
            "path_hint": "Packages/vendor/consumer/inx_package.json",
            "owned": False,
        },
        dependencies=["Vendor/X"],
    )

    with pytest.raises(RuntimeError, match="vendor/consumer"):
        manager.uninstall("VENDOR/X")

    assert manager.registry.installed_record("vendor/x") is not None


def test_invalid_official_catalog_does_not_mutate_project_registry(tmp_path):
    project = _project(tmp_path / "project")
    registry = PluginRegistry(str(project))
    registry.add_package(
        "vendor/local",
        source={"type": "git", "location": "https://example.invalid/local.git"},
    )
    registry.add_package(
        "infernux/previous",
        source={"type": "local", "location": "old.inxpkg", "official": True},
    )
    resources = tmp_path / "resources"
    official = resources / "official_packages"
    official.mkdir(parents=True)
    (official / "broken.inxpkg").write_bytes(b"broken")
    (official / "official-registry.json").write_text(
        json.dumps(
            {
                "$schema": "infernux.official_plugin_registry",
                "packages": [
                    {
                        "reference": "infernux/broken",
                        "version": "1.0.0",
                        "engine": ">=0.4,<0.5",
                        "artifact": "broken.inxpkg",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = registry.load()

    with pytest.raises(OfficialCatalogError, match="artifact is invalid"):
        sync_official_registry(str(project), resources_root=str(resources))

    assert registry.load() == before


def test_official_registry_publishes_remote_entry_without_bundled_artifact(tmp_path):
    project = _project(tmp_path / "project")
    resources = tmp_path / "resources"
    official = resources / "official_packages"
    official.mkdir(parents=True)
    (official / "official-registry.json").write_text(
        json.dumps(
            {
                "$schema": "infernux.official_plugin_registry",
                "packages": [
                    {
                        "reference": "infernux/platform-web",
                        "name": "Infernux Web Platform",
                        "version": "0.1.0",
                        "engine": ">=0.4,<0.5",
                        "artifact": "infernux.platform-web.inxpkg",
                        "dependencies": [],
                        "pages": [],
                        "intros": {},
                        "category": "platform_build",
                        "targets": ["web-wasm32"],
                        "source": {
                            "type": "url",
                            "location": "https://downloads.example/infernux.platform-web.inxpkg",
                        },
                        "repository": "https://github.com/example/infernux-web",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    added = sync_official_registry(str(project), resources_root=str(resources))

    assert len(added) == 1
    entry = PluginRegistry(str(project)).find("infernux/platform-web")
    assert entry["category"] == "platform_build"
    assert entry["targets"] == ["web-wasm32"]
    assert entry["source"] == {
        "type": "url",
        "location": "https://downloads.example/infernux.platform-web.inxpkg",
        "official": True,
        "reference": "infernux/platform-web",
        "repository": "https://github.com/example/infernux-web",
        "release_tag": "v0.1.0",
    }


def test_startup_degrades_when_official_catalog_is_unavailable(tmp_path):
    project = _project(tmp_path / "project")
    registry = PluginRegistry(str(project))
    registry.add_package(
        "vendor/local",
        source={"type": "git", "location": "https://example.invalid/local.git"},
    )

    manager = PluginManager.startup(str(project))

    assert "Official plugin catalog is unavailable" in manager.official_catalog_error
    assert manager.registry.find("vendor/local") is not None


def test_startup_restores_requirements_before_single_preload_catchup(
    tmp_path, monkeypatch
):
    project = _project(tmp_path / "project")
    events: list[str] = []
    monkeypatch.setattr(
        "infernux.plugins.official.sync_official_registry",
        lambda _project, *, resources_root=None: events.append("sync"),
    )
    monkeypatch.setattr(
        "infernux.plugins.official.install_bundled_packages",
        lambda _project, *, manager=None: events.append("bundled") or (),
    )
    monkeypatch.setattr(
        PluginManager,
        "_reconcile_python_requirements_for_startup",
        lambda self: events.append("requirements") or (),
    )
    monkeypatch.setattr(
        preload_module.PreloadManager,
        "catch_up",
        lambda self: events.append("catchup") or (),
    )

    manager = PluginManager.startup(str(project), runtime=False)
    try:
        assert events == [
            "sync",
            "requirements",
            "bundled",
            "catchup",
        ]
    finally:
        manager.shutdown()


def test_bundled_package_startup_idempotence_and_successive_releases(tmp_path, monkeypatch):
    source = _source(tmp_path / "source", "infernux/platform-fixture")
    (source / "runtime").mkdir()
    fixture = source / "runtime/fixture.py"
    fixture.write_text("RELEASE = 'stale-cache'\n", encoding="utf-8")
    stale = _export(source, tmp_path / "stale.inxpkg")
    SharedPackageCache().store(str(stale), reference="infernux/platform-fixture", version="1.0.0")
    fixture.write_text("RELEASE = 'bundled'\n", encoding="utf-8")
    resources = tmp_path / "resources"
    resources.mkdir()
    package = _export(source, resources / "infernux.platform-fixture.inxpkg")
    project = _project(tmp_path / "project")
    monkeypatch.setattr("infernux.resources.get_package_resources_path", lambda: str(resources))
    manager = PluginManager.startup(str(project), runtime=False)
    assert "Official plugin catalog is unavailable" in manager.official_catalog_error
    installed = project / "Packages/infernux/platform-fixture"
    assert (installed / "runtime/fixture.py").read_text(encoding="utf-8") == "RELEASE = 'bundled'\n"
    assert install_bundled_packages(str(project), resources_root=str(resources), manager=manager) == ()
    record = manager.registry.installed_record("infernux/platform-fixture")
    assert record["source"]["location"] == record["source"]["cache_location"]
    assert record["source"]["builtin"] is True
    cached = Path(manager._installed_archive_path(record))
    _FakeInxPack.archives[str(cached.resolve())] = dict(_FakeInxPack.archives[str(package.resolve())])

    fixture.write_text("RELEASE = 'second'\n", encoding="utf-8")
    manifest_path = source / "inx_package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "1.0.1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _export(source, package)
    states = install_bundled_packages(str(project), resources_root=str(resources), manager=manager)
    assert [state.reference for state in states] == ["infernux/platform-fixture"]
    assert (installed / "runtime/fixture.py").read_text(encoding="utf-8") == "RELEASE = 'second'\n"
    record = manager.registry.installed_record("infernux/platform-fixture")
    cached = Path(manager._installed_archive_path(record))
    _FakeInxPack.archives[str(cached.resolve())] = dict(_FakeInxPack.archives[str(package.resolve())])

    manifest["version"] = "1.0.2"
    manifest["intro"] = "Updated built-in description"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _export(source, package)
    states = install_bundled_packages(str(project), resources_root=str(resources), manager=manager)
    assert [state.reference for state in states] == ["infernux/platform-fixture"]
    assert json.loads((installed / "inx_package.json").read_text(encoding="utf-8"))["intro"] == manifest["intro"]
    assert install_bundled_packages(str(project), resources_root=str(resources), manager=manager) == ()


def test_startup_never_force_overwrites_an_edited_builtin_package(tmp_path):
    source = _source(tmp_path / "source", "infernux/platform-fixture")
    runtime = source / "runtime"
    runtime.mkdir()
    (runtime / "fixture.py").write_text("VALUE = 'engine'\n", encoding="utf-8")
    resources = tmp_path / "resources"
    resources.mkdir()
    _export(source, resources / "infernux.platform-fixture.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project), runtime=False)
    install_bundled_packages(str(project), resources_root=str(resources), manager=manager)
    authored = project / "Packages/infernux/platform-fixture/runtime/fixture.py"
    original_meta = Path(str(authored) + ".meta").read_bytes()
    edited = b"VALUE = 'team edit'\n"
    authored.write_bytes(edited)

    with pytest.raises(PackageUpdateConflict):
        install_bundled_packages(str(project), resources_root=str(resources), manager=manager)
    assert authored.read_bytes() == edited
    assert Path(str(authored) + ".meta").read_bytes() == original_meta


def test_package_update_preserves_mesh_read_write_and_strips_legacy_statistics(tmp_path):
    from infernux.plugins.package import portable_meta_bytes

    source = _source(tmp_path / "source", "vendor/mesh-settings")
    mesh = source / "Ship.obj"
    mesh.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    metadata = {
        "guid": {"type": "string", "value": "a" * 32},
        "resource_type": {"type": "enum infernux::ResourceType", "value": "Mesh"},
        "is_readable": {"type": "bool", "value": False},
        "file_size": {"type": "size_t", "value": 42},
        "character_count": {"type": "size_t", "value": 42},
    }
    Path(str(mesh) + ".meta").write_text(json.dumps({"metadata": metadata}), encoding="utf-8")
    package = _export(source, tmp_path / "ship.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project), runtime=False)
    manager.install_package(str(package), install_dependencies=False)
    authored = project / "Assets/Plugins/Ship.obj.meta"
    document = json.loads(authored.read_bytes())
    assert "file_size" not in document["metadata"]
    assert "character_count" not in document["metadata"]
    document["metadata"]["is_readable"]["value"] = True
    authored.write_text(json.dumps(document), encoding="utf-8")

    manifest_path = source / "inx_package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "1.0.1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    mesh.write_text(mesh.read_text(encoding="utf-8") + "# new package revision\n", encoding="utf-8")
    _export(source, package)
    manager.install_package(str(package), install_dependencies=False, update=True)
    assert json.loads(authored.read_bytes())["metadata"]["is_readable"]["value"] is True

    metadata["resource_type"]["value"] = "DefaultText"
    plain = json.loads(portable_meta_bytes(json.dumps({"metadata": metadata}).encode("utf-8"), "Assets/Text.txt"))
    assert "is_readable" not in plain["metadata"]


def test_resources_root_preserves_existing_project_plugin_release(tmp_path):
    bundled_source = _source(tmp_path / "bundled", "infernux/platform-fixture")
    (bundled_source / "runtime").mkdir()
    (bundled_source / "runtime" / "fixture.py").write_text(
        "RELEASE = 'bundled'\n", encoding="utf-8"
    )
    resources = tmp_path / "resources"
    resources.mkdir()
    _export(bundled_source, resources / "infernux.platform-fixture.inxpkg")

    project_source = _source(tmp_path / "project-release", "infernux/platform-fixture")
    (project_source / "runtime").mkdir()
    (project_source / "runtime" / "fixture.py").write_text(
        "RELEASE = 'project'\n", encoding="utf-8"
    )
    project_package = _export(project_source, tmp_path / "project-release.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project), runtime=False)
    manager.install_package(str(project_package), install_dependencies=False)

    states = install_bundled_packages(
        str(project), resources_root=str(resources), manager=manager
    )

    assert states == ()
    assert (
        project / "Packages/infernux/platform-fixture/runtime/fixture.py"
    ).read_text(encoding="utf-8") == "RELEASE = 'project'\n"
    record = manager.registry.installed_record("infernux/platform-fixture")
    assert record is not None
    assert record["source"]["location"] == record["source"]["cache_location"]
    assert record["source"].get("builtin") is not True


def test_new_project_installs_builtins_before_resolving_default_registry(
    tmp_path, monkeypatch
):
    source = _source(tmp_path / "source", "infernux/default-fixture")
    (source / "runtime").mkdir()
    (source / "runtime" / "fixture.py").write_text(
        "DEFAULT_FIXTURE = True\n", encoding="utf-8"
    )
    resources = tmp_path / "resources"
    resources.mkdir()
    package = _export(source, resources / "infernux.default-fixture.inxpkg")
    project = _project(tmp_path / "project")
    official = resources / "official_packages"
    official.mkdir(parents=True)
    (official / "official-registry.json").write_text(
        json.dumps(
            {
                "$schema": "infernux.official_plugin_registry",
                "packages": [
                    {
                        "reference": "infernux/default-fixture",
                        "name": "Default Fixture",
                        "version": "1.0.0",
                        "artifact": "infernux.default-fixture.inxpkg",
                        "dependencies": [],
                        "pages": [],
                        "intros": {},
                        "source": {
                            "type": "git",
                            "location": "https://example.invalid/must-not-clone.git",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (official / "default-libraries.json").write_text(
        json.dumps(
            {
                "$schema": "infernux.default_libraries",
                "libraries": ["infernux/default-fixture"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "infernux.resources.get_package_resources_path", lambda: str(resources)
    )
    monkeypatch.setattr("infernux.engine.library_sync.sync_resources", lambda _root: None)
    monkeypatch.setattr(
        PluginManager,
        "_run_process",
        staticmethod(lambda *_args, **_kwargs: pytest.fail("default cloned remotely")),
    )

    states = bootstrap_new_project(str(project))

    assert [state.reference for state in states] == ["infernux/default-fixture"]
    assert PluginRegistry(str(project)).installed_record(
        "infernux/default-fixture"
    ) is not None


def test_duplicate_resources_root_package_reference_is_rejected_before_install(
    tmp_path,
):
    source = _source(tmp_path / "source", "infernux/platform-fixture")
    (source / "runtime").mkdir()
    (source / "runtime" / "fixture.py").write_text(
        "PLATFORM_FIXTURE = True\n", encoding="utf-8"
    )
    resources = tmp_path / "resources"
    resources.mkdir()
    _export(source, resources / "first.inxpkg")
    _export(source, resources / "second.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project), runtime=False)

    with pytest.raises(OfficialCatalogError, match="duplicated"):
        install_bundled_packages(
            str(project), resources_root=str(resources), manager=manager
        )

    assert manager.registry.installed() == ()


def test_shared_guid_reuses_existing_asset_without_duplicate_import(tmp_path):
    first = _source(tmp_path / "first", "vendor/first")
    (first / "Shared.bin").write_bytes(b"one")
    (first / "Shared.bin.meta").write_text(
        _meta("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), encoding="utf-8"
    )
    second = _source(tmp_path / "second", "vendor/second")
    (second / "Shared.bin").write_bytes(b"two")
    (second / "Shared.bin.meta").write_text(
        _meta("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), encoding="utf-8"
    )
    first_package = _export(first, tmp_path / "first.inxpkg")
    second_package = _export(second, tmp_path / "second.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(first_package), install_dependencies=False)
    manager.install_package(str(second_package), install_dependencies=False)
    second_record = manager.registry.installed_record("vendor/second")
    assert second_record is not None
    assert second_record["files"][0]["owned"] is False
    assert (project / "Assets/Plugins/Shared.bin").read_bytes() == b"one"


def test_target_path_with_different_guid_is_rejected_without_overwrite(tmp_path):
    source = _source(tmp_path / "source", "vendor/path-conflict")
    payload = source / "Data.bin"
    payload.write_bytes(b"package bytes")
    (source / "Data.bin.meta").write_text(
        _meta("11111111111111111111111111111111"), encoding="utf-8"
    )
    package = _export(source, tmp_path / "path-conflict.inxpkg")
    project = _project(tmp_path / "project")
    occupied = project / "Assets/Plugins/Data.bin"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(b"user bytes")
    Path(str(occupied) + ".meta").write_text(
        _meta("22222222222222222222222222222222"), encoding="utf-8"
    )
    manager = PluginManager(str(project))

    with pytest.raises(PackageConflictError, match="occupied by another GUID"):
        manager.install_package(str(package), install_dependencies=False)

    assert occupied.read_bytes() == b"user bytes"
    assert manager.registry.installed() == ()


def test_identical_bytes_with_different_guid_are_not_adopted(tmp_path):
    source = _source(tmp_path / "source", "vendor/adopt-identical")
    payload = source / "runtime" / "backend.py"
    payload.parent.mkdir()
    payload.write_text("VALUE = 7\n", encoding="utf-8")
    package = _export(source, tmp_path / "adopt-identical.inxpkg")

    project = _project(tmp_path / "project")
    occupied = project / "Packages/vendor/adopt-identical/runtime/backend.py"
    occupied.parent.mkdir(parents=True)
    occupied.write_bytes(payload.read_bytes())
    previous_guid = "22222222222222222222222222222222"
    Path(str(occupied) + ".meta").write_text(
        _meta(previous_guid), encoding="utf-8"
    )
    manager = PluginManager(str(project))

    with pytest.raises(PackageConflictError, match="occupied by another GUID"):
        manager.install_package(str(package), install_dependencies=False)

    document = json.loads(
        Path(str(occupied) + ".meta").read_text(encoding="utf-8")
    )
    assert document["metadata"]["guid"]["value"] == previous_guid
    assert manager.registry.installed_record("vendor/adopt-identical") is None


def test_install_rolls_back_files_and_registry_if_registration_fails(tmp_path, monkeypatch):
    source = _source(tmp_path / "source", "vendor/rollback")
    (source / "Data.bin").write_bytes(b"payload")
    package = _export(source, tmp_path / "rollback.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    original = manager.registry.record_install

    def fail_after_save(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated post-save failure")

    monkeypatch.setattr(manager.registry, "record_install", fail_after_save)
    with pytest.raises(RuntimeError, match="post-save"):
        manager.install_package(str(package), install_dependencies=False)
    assert manager.registry.installed() == ()
    assert not (project / "Assets/Plugins/Data.bin").exists()
    assert not (project / "Packages/vendor/rollback/inx_package.json").exists()


def test_rejected_parent_install_removes_new_plugin_dependencies(tmp_path, monkeypatch):
    project = _project(tmp_path / "project")
    child_source = _source(tmp_path / "child", "vendor/child")
    (child_source / "Child.bin").write_bytes(b"child")
    child_package = _export(child_source, tmp_path / "child.inxpkg")
    parent_source = _source(tmp_path / "parent", "vendor/parent")
    (parent_source / "requirements.txt").write_text(
        "vendor/child\n", encoding="utf-8"
    )
    (parent_source / "Parent.bin").write_bytes(b"parent")
    parent_package = _export(parent_source, tmp_path / "parent.inxpkg")

    manager = PluginManager(str(project))
    manager.registry.add_package(
        "vendor/child",
        source={"type": "local", "location": str(child_package)},
    )
    original_record_install = manager.registry.record_install

    def reject_parent(metadata, **kwargs):
        if metadata.get("reference") == "vendor/parent":
            raise RuntimeError("parent registry failure")
        return original_record_install(metadata, **kwargs)

    monkeypatch.setattr(manager.registry, "record_install", reject_parent)
    with pytest.raises(RuntimeError, match="parent registry failure"):
        manager.install_package(str(parent_package))

    assert manager.registry.installed() == ()
    assert not (project / "Assets/Plugins/Child.bin").exists()
    assert not (project / "Assets/Plugins/Parent.bin").exists()


def test_shared_hub_package_download_install_reinstall_and_reference_cleanup(tmp_path):
    source = _source(tmp_path / "source", "vendor/shared")
    (source / "Data.bin").write_bytes(b"shared offline payload")
    package = _export(source, tmp_path / "shared.inxpkg")
    first_project = _project(tmp_path / "first-project")
    second_project = _project(tmp_path / "second-project")
    first = PluginManager(str(first_project))
    second = PluginManager(str(second_project))
    first.registry.add_package(
        "vendor/shared", source={"type": "local", "location": str(package)}, version="1.0.0",
    )
    second.registry.add_package(
        "vendor/shared", source={"type": "local", "location": str(package)}, version="1.0.0",
    )
    cache = first._package_cache()
    assert plugin_cache_module.package_cache_root() == str((tmp_path / "hub-package-cache").resolve())
    assert cache.root == str((tmp_path / "hub-package-cache").resolve())
    assert cache.staging_root == str((first_project / "Cache/Plugins/.staging").resolve())

    downloaded = first.download_reference("vendor/shared")
    assert downloaded["cached"] is False
    assert first.registry.installed() == ()
    cached = Path(downloaded["path"])
    assert cached.is_file()
    installed = first.install_package(str(package), install_dependencies=False)
    assert installed.loaded is True
    record = first.registry.installed_record("vendor/shared")
    assert record["transaction_id"]
    lock = json.loads((first_project / "ProjectSettings/InxPackages.lock.json").read_text(encoding="utf-8"))
    assert set(lock) == {"$schema", "packages", "python", "python_dependencies"}

    package.unlink()
    assert second.download_reference("vendor/shared") == {
        "reference": "vendor/shared", "path": str(cached), "cached": True,
    }
    installed = second.install_reference("vendor/shared", install_dependencies=False)
    assert installed.reference == "vendor/shared"
    first_source = first.registry.installed_record("vendor/shared")["source"]
    second_source = second.registry.installed_record("vendor/shared")["source"]
    assert first_source["cache_scope"] == second_source["cache_scope"] == "hub"
    assert first_source["cache_location"] == second_source["cache_location"]
    assert first_source["cache_location"] == cache.relative_path("vendor/shared", str(record["version"]))
    hub_library = Path(cache.root)
    assert len(list((hub_library / "packages").rglob("*.inxpkg"))) == 1
    assert not (first_project / "Cache/Plugins/packages").exists()
    assert not (second_project / "Cache/Plugins/packages").exists()

    first.uninstall("vendor/shared")
    assert cached.is_file()
    assert second.registry.installed_record("vendor/shared") is not None
    reinstalled = first.install_reference("vendor/shared", install_dependencies=False)
    assert reinstalled.loaded is True
    assert first.registry.installed_record("vendor/shared")["source"]["cache_location"] == first_source["cache_location"]
    assert first.registry.installed_record("vendor/shared")["source"]["cache_scope"] == "hub"

    first.uninstall("vendor/shared")
    second.uninstall("vendor/shared")
    assert cache.prune_unreferenced((first_project, second_project)) == ()
    assert cached.is_file()
    first_document = first.registry.load()
    first_document["packages"] = []
    first.registry.save(first_document)
    second_document = second.registry.load()
    second_document["packages"] = []
    second.registry.save(second_document)
    assert cache.prune_unreferenced((first_project, second_project)) == (str(cached),)
    assert not cached.exists()


def test_cache_cleanup_fails_closed_when_registered_project_is_unavailable(tmp_path):
    cache = SharedPackageCache(tmp_path / "project/Cache/Plugins")
    blob = tmp_path / "unused.inxpkg"
    blob.write_bytes(b"unused")
    destination = Path(
        cache.store(str(blob), reference="vendor/unused", version="1.0.0")
    )

    with pytest.raises(FileNotFoundError, match="registered project is unavailable"):
        cache.prune_unreferenced((tmp_path / "missing-project",))

    assert destination.is_file()


def test_plugin_staging_is_owned_by_shared_cache_and_reaps_dead_processes(
    tmp_path,
    monkeypatch,
):
    cache = SharedPackageCache(tmp_path / "packages")
    stale = Path(cache.staging_root) / "download-41-dead"
    stale.mkdir(parents=True)
    (stale / "partial.inxpkg").write_bytes(b"partial")
    monkeypatch.setattr(plugin_cache_module, "_process_is_alive", lambda _pid: False)

    with cache.workspace("download") as workspace:
        active = Path(workspace)
        assert active.parent == Path(cache.staging_root)
        assert not stale.exists()
        (active / "package.inxpkg").write_bytes(b"package")

    assert not active.exists()


def test_explicit_data_root_owns_default_plugin_library(tmp_path, monkeypatch):
    root = tmp_path / "HubData"
    monkeypatch.delenv("INFERNUX_PACKAGE_CACHE_ROOT", raising=False)
    monkeypatch.setenv("INFERNUX_DATA_ROOT", str(root))

    assert plugin_cache_module.package_cache_root() == str(
        (root / "Library" / "Plugins").resolve()
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows Hub data layout")
def test_windows_default_plugin_library_lives_under_local_hub_data(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("INFERNUX_PACKAGE_CACHE_ROOT")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    assert plugin_cache_module.package_cache_root() == str(
        (tmp_path / "local/InfernuxHub/Library/Plugins").resolve()
    )


@pytest.mark.skipif(os.name == "nt", reason="XDG Hub data layout")
def test_linux_default_plugin_library_lives_under_xdg_hub_data(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("INFERNUX_PACKAGE_CACHE_ROOT")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

    assert plugin_cache_module.package_cache_root() == str(
        (tmp_path / "xdg-data/InfernuxHub/Library/Plugins").resolve()
    )


def test_github_source_prefers_highest_compatible_protocol_release(
    tmp_path, monkeypatch
):
    artifact = b"released inxpackage"
    manifest = {
        "$schema": "infernux.plugin_release",
        "reference": "vendor/released",
        "version": "2.0.0",
        "engine": ">=0.4,<0.5",
        "artifact": {
            "name": "vendor.released.inxpkg",
        },
        "generator": {"name": "Infernux", "version": "0.4.0"},
    }
    releases = [
        {
            "tag_name": "v2.0.0",
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/vendor/released/releases/tag/v2.0.0",
            "assets": [
                {
                    "name": RELEASE_MANIFEST_NAME,
                    "browser_download_url": "https://downloads.invalid/manifest",
                },
                {
                    "name": "vendor.released.inxpkg",
                    "browser_download_url": "https://downloads.invalid/package",
                },
            ],
        }
    ]

    class Response:
        def __init__(self, payload):
            self.payload = payload
            self.offset = 0
            self.headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            if size < 0:
                return self.payload
            chunk = self.payload[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    def open_request(request, *, timeout):
        assert timeout == 30
        url = request.full_url
        if url.startswith("https://api.github.com/"):
            return Response(json.dumps(releases).encode("utf-8"))
        if url.endswith("/manifest"):
            return Response(json.dumps(manifest).encode("utf-8"))
        if url.endswith("/package"):
            return Response(artifact)
        raise AssertionError(url)

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    monkeypatch.setattr(
        "infernux.plugins.github_releases.InxPackage.inspect",
        lambda _path: SimpleNamespace(
            metadata={
                "reference": "vendor/released",
                "version": "2.0.0",
                "engine": ">=0.4,<0.5",
            }
        ),
    )

    result = resolve_github_release(
        "https://github.com/vendor/released",
        str(tmp_path / "downloads"),
        expected_reference="vendor/released",
    )

    assert Path(result.path).read_bytes() == artifact
    assert result.source["type"] == "github-release"
    assert result.source["release_tag"] == "v2.0.0"


def test_release_protocol_names_support_multiple_plugins_in_one_repository():
    manifest = release_manifest_name("infernux/platform-web")
    assert manifest == "infernux.platform-web.release.json"
    assert release_manifest_name() == RELEASE_MANIFEST_NAME


def test_github_source_snapshot_downloads_only_selected_subdirectory(
    tmp_path,
    monkeypatch,
):
    commit = "a" * 40
    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        for name, payload in (
            (
                "Infernux-a/external/plugins/infernux_linux/package/inx_package.json",
                b"{}",
            ),
            (
                "Infernux-a/external/plugins/infernux_linux/README.md",
                b"Linux plugin",
            ),
            ("Infernux-a/README.md", b"repository root"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    archive_payload = archive_buffer.getvalue()

    class Response:
        def __init__(self, payload):
            self.payload = payload
            self.offset = 0
            self.headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            if size < 0:
                return self.payload
            chunk = self.payload[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    def open_request(request, *, timeout):
        assert timeout == 30
        if request.full_url.startswith("https://api.github.com/"):
            return Response(json.dumps({"sha": commit}).encode("utf-8"))
        if request.full_url.startswith("https://codeload.github.com/"):
            return Response(archive_payload)
        raise AssertionError(request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    progress = []
    result = download_github_source(
        "https://github.com/vendor/Infernux",
        str(tmp_path / "download"),
        revision="040/multiplatform_build",
        subdirectory="external/plugins/infernux_linux",
        progress=lambda stage, value, detail: progress.append(
            (stage, value, detail)
        ),
    )

    root = Path(result.root)
    assert result.commit == commit
    assert (root / "package" / "inx_package.json").read_bytes() == b"{}"
    assert (root / "README.md").read_bytes() == b"Linux plugin"
    assert not (root / "external").exists()
    assert any(stage == "download_source" and detail for stage, _, detail in progress)


def test_github_source_selects_reference_scoped_manifest_from_shared_release(
    tmp_path, monkeypatch
):
    artifact = b"web platform package"
    web_manifest_name = release_manifest_name("infernux/platform-web")
    releases = [
        {
            "tag_name": "v0.4.0",
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/vendor/engine/releases/tag/v0.1.0",
            "assets": [
                {
                    "name": web_manifest_name,
                    "browser_download_url": "https://downloads.invalid/web-manifest",
                },
                {
                    "name": release_manifest_name("infernux/platform-android"),
                    "browser_download_url": "https://downloads.invalid/android-manifest",
                },
                {
                    "name": "infernux.platform-web.inxpkg",
                    "browser_download_url": "https://downloads.invalid/web-package",
                },
                {
                    "name": "infernux.platform-android.inxpkg",
                    "browser_download_url": "https://downloads.invalid/android-package",
                },
            ],
        }
    ]
    manifest = {
        "$schema": "infernux.plugin_release",
        "reference": "infernux/platform-web",
        "version": "0.1.0",
        "release_tag": "v0.4.0",
        "engine": ">=0.4,<0.5",
        "artifact": {
            "name": "infernux.platform-web.inxpkg",
        },
        "generator": {"name": "Infernux", "version": "0.4.0"},
    }

    class Response:
        def __init__(self, payload):
            self.payload = payload
            self.offset = 0
            self.headers = {"Content-Length": str(len(payload))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            if size < 0:
                return self.payload
            chunk = self.payload[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    def open_request(request, *, timeout):
        assert timeout == 30
        url = request.full_url
        if url.startswith("https://api.github.com/"):
            return Response(json.dumps(releases).encode("utf-8"))
        if url.endswith("/web-manifest"):
            return Response(json.dumps(manifest).encode("utf-8"))
        if url.endswith("/web-package"):
            return Response(artifact)
        raise AssertionError(url)

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    monkeypatch.setattr(
        "infernux.plugins.github_releases.InxPackage.inspect",
        lambda _path: SimpleNamespace(metadata={
            "reference": "infernux/platform-web",
            "version": "0.1.0",
            "engine": ">=0.4,<0.5",
        }),
    )

    result = resolve_github_release(
        "https://github.com/vendor/engine",
        str(tmp_path / "downloads"),
        expected_reference="infernux/platform-web",
    )
    assert Path(result.path).read_bytes() == artifact


def test_official_release_downloads_to_project_cache_then_imports(
    tmp_path,
    monkeypatch,
):
    player_package_native.set_test_backend(None)
    try:
        source = _source(
            tmp_path / "source",
            "vendor/released-platform",
            version="0.1.0",
            engine=">=0.4,<0.5",
        )
        (source / "runtime").mkdir()
        (source / "runtime" / "api.py").write_text(
            "VALUE = 1\n",
            encoding="utf-8",
        )
        package = _export(
            source,
            tmp_path / "vendor.released-platform.inxpkg",
        )
        artifact = package.read_bytes()

        resources = tmp_path / "resources"
        official = resources / "official_packages"
        official.mkdir(parents=True)
        (official / "official-registry.json").write_text(
            json.dumps(
                {
                    "$schema": "infernux.official_plugin_registry",
                    "packages": [
                        {
                            "reference": "vendor/released-platform",
                            "name": "Released Platform",
                            "version": "0.1.0",
                            "intro": "Release-first test package",
                            "intros": {},
                            "artifact": "vendor.released-platform.inxpkg",
                            "engine": ">=0.4,<0.5",
                            "dependencies": [],
                            "repository": "https://github.com/vendor/engine",
                            "source": {
                                "type": "github",
                                "location": "https://github.com/vendor/engine",
                                "revision": "040/multiplatform_build",
                            },
                            "category": "platform_build",
                            "targets": ["released-test"],
                            "pages": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        manifest_name = release_manifest_name("vendor/released-platform")
        release_manifest = {
            "$schema": "infernux.plugin_release",
            "reference": "vendor/released-platform",
            "version": "0.1.0",
            "release_tag": "v0.4.0",
            "engine": ">=0.4,<0.5",
            "artifact": {"name": "vendor.released-platform.inxpkg"},
            "generator": "Infernux official plugin release assets",
        }
        releases = [
            {
                "tag_name": "v0.4.0",
                "draft": False,
                "prerelease": False,
                "html_url": "https://github.com/vendor/engine/releases/tag/v0.4.0",
                "assets": [
                    {
                        "name": manifest_name,
                        "browser_download_url": "https://downloads.invalid/manifest",
                    },
                    {
                        "name": "vendor.released-platform.inxpkg",
                        "browser_download_url": "https://downloads.invalid/package",
                    },
                ],
            }
        ]

        class Response:
            def __init__(self, payload):
                self.payload = payload
                self.offset = 0
                self.headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size=-1):
                if size < 0:
                    return self.payload
                chunk = self.payload[self.offset : self.offset + size]
                self.offset += len(chunk)
                return chunk

        def open_request(request, *, timeout):
            assert timeout == 30
            url = request.full_url
            if url.startswith("https://api.github.com/"):
                return Response(json.dumps(releases).encode("utf-8"))
            if url.endswith("/manifest"):
                return Response(json.dumps(release_manifest).encode("utf-8"))
            if url.endswith("/package"):
                return Response(artifact)
            raise AssertionError(url)

        monkeypatch.setattr(urllib.request, "urlopen", open_request)
        project = _project(tmp_path / "project")
        sync_official_registry(str(project), resources_root=str(resources))
        manager = PluginManager(str(project))

        downloaded = manager.download_reference("vendor/released-platform")

        cached = Path(downloaded["path"])
        assert cached.is_file()
        assert cached.read_bytes() == artifact
        assert manager.registry.installed() == ()
        registry_entry = manager.registry.find("vendor/released-platform")
        assert registry_entry["source"]["acquisition"] == "github-release"
        assert registry_entry["source"]["release_tag"] == "v0.4.0"
        assert registry_entry["source"]["cache_scope"] == "hub"
        assert registry_entry["source"]["official"] is True
        assert registry_entry["category"] == "platform_build"
        assert registry_entry["targets"] == ["released-test"]

        installed = manager.install_reference(
            "vendor/released-platform",
            install_dependencies=False,
        )
        assert installed.reference == "vendor/released-platform"
        assert manager.registry.installed_record("vendor/released-platform") is not None

        manager.uninstall("vendor/released-platform")
        assert manager.registry.installed() == ()
        assert cached.is_file()
    finally:
        player_package_native.set_test_backend(_FakeInxPack)


def test_incompatible_github_protocol_release_never_falls_back_to_head(
    tmp_path, monkeypatch
):
    releases = [
        {
            "tag_name": "v1.0.0",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": RELEASE_MANIFEST_NAME,
                    "browser_download_url": "https://downloads.invalid/manifest",
                },
                {
                    "name": "vendor.strict.inxpkg",
                    "browser_download_url": "https://downloads.invalid/package",
                },
            ],
        }
    ]
    monkeypatch.setattr(
        github_releases_module,
        "_request_bytes",
        lambda *_args, **_kwargs: json.dumps(releases).encode("utf-8"),
    )
    monkeypatch.setattr(
        github_releases_module,
        "_release_manifest",
        lambda _asset: {
            "$schema": "infernux.plugin_release",
            "reference": "vendor/strict",
            "version": "1.0.0",
            "engine": ">=9",
            "artifact": {
                "name": "vendor.strict.inxpkg",
            },
        },
    )

    with pytest.raises(RuntimeError, match="No compatible Infernux plugin release"):
        resolve_github_release(
            "https://github.com/vendor/strict",
            str(tmp_path / "downloads"),
            expected_reference="vendor/strict",
        )


def test_package_cli_build_verify_and_release_manifest(tmp_path):
    source = _source(tmp_path / "source", "vendor/cli", version="1.2.3")
    (source / "runtime").mkdir()
    (source / "runtime" / "api.py").write_text("VALUE = 3\n", encoding="utf-8")
    package = tmp_path / "vendor.cli.inxpkg"
    manifest = tmp_path / RELEASE_MANIFEST_NAME

    assert package_cli_main(
        ["package", "build", str(source), str(package), "--profile", "release"]
    ) == 0
    assert package_cli_main(
        ["package", "verify", str(package), "--engine", "0.4.0"]
    ) == 0
    assert package_cli_main(
        [
            "package",
            "release-manifest",
            str(package),
            "--output",
            str(manifest),
            "--tag",
            "v1.2.3",
        ]
    ) == 0

    document = json.loads(manifest.read_text(encoding="utf-8"))
    assert document["$schema"] == "infernux.plugin_release"
    assert document["reference"] == "vendor/cli"
    assert document["version"] == "1.2.3"
    assert document["artifact"]["name"] == package.name
    assert document["artifact"] == {"name": package.name}
    assert package_cli_main(
        [
            "package",
            "release-manifest",
            str(package),
            "--output",
            str(manifest),
            "--tag",
            "v9.9.9",
        ]
    ) == 1

    shared_manifest = tmp_path / "vendor.cli.release.json"
    assert package_cli_main(
        [
            "package",
            "release-manifest",
            str(package),
            "--output",
            str(shared_manifest),
            "--tag",
            "v0.4.0",
            "--shared-release",
        ]
    ) == 0
    assert json.loads(shared_manifest.read_text(encoding="utf-8"))[
        "release_tag"
    ] == "v0.4.0"


def test_arbitrary_pip_syntax_is_project_scoped_and_written_to_lock(tmp_path, monkeypatch):
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    monkeypatch.setattr(manager, "_project_python_executable", lambda: "project-python")
    calls = []
    environment = {}

    def run(command, cwd=None):
        calls.append((command, cwd))
        if command[2:5] == ["pip", "list", "--format=json"]:
            return type("Result", (), {"stdout": json.dumps([
                {"name": name, "version": version}
                for name, version in environment.items()
            ])})()
        environment["demo"] = "2.0"
        return type("Result", (), {"stdout": "installed wheel"})()

    monkeypatch.setattr(manager, "_run_process", run)
    result = manager.install_pip(
        'pip install --index-url https://packages.example/simple "demo[vision]>=2"'
    )
    assert result["command"] == [
        "project-python",
        "-m",
        "pip",
        "install",
        "--index-url",
        "https://packages.example/simple",
        "demo[vision]>=2",
    ]
    lock = json.loads(
        (project / "ProjectSettings/InxPackages.lock.json").read_text(encoding="utf-8")
    )
    assert lock["python"] == ["demo[vision]>=2"]
    assert result["output"] == "installed wheel"
    assert lock["python_dependencies"][0]["name"] == "demo"
    assert lock["python_dependencies"][0]["owners"][0]["reference"] == "@project"


def test_direct_local_github_git_and_http_sources_converge_on_same_inventory(
    tmp_path, monkeypatch
):
    source = _source(tmp_path / "source", "vendor/distributed")
    (source / "runtime").mkdir()
    (source / "runtime" / "api.py").write_text("VALUE = 7\n", encoding="utf-8")
    (source / "Scene.scene").write_text("scene", encoding="utf-8")
    package = _export(source, tmp_path / "distributed.inxpkg")
    expected_archive = dict(_FakeInxPack.archives[str(package.resolve())])
    repository_source = tmp_path / "repository-source"
    shutil.copytree(source, repository_source / "package")

    inventories = []
    cases = (
        ("direct", str(package)),
        ("local", {"type": "local", "location": str(source)}),
        ("github", {"type": "github", "location": "https://github.com/vendor/repo.git"}),
        ("git", {"type": "git", "location": "ssh://git.internal/vendor/repo.git"}),
        ("http", {"type": "url", "location": "https://packages.internal/distributed.inxpkg"}),
    )
    for label, descriptor in cases:
        project = _project(tmp_path / f"project-{label}")
        manager = PluginManager(str(project))

        def run_process(command, cwd=None):
            if command[:2] == ["git", "clone"]:
                checkout = Path(command[-1])
                shutil.copytree(repository_source, checkout, dirs_exist_ok=True)
            output = "a" * 40 if command[:3] == ["git", "rev-parse", "HEAD"] else ""
            return type("Result", (), {"stdout": output})()

        monkeypatch.setattr(manager, "_run_process", run_process)
        monkeypatch.setattr(
            "infernux.plugins.github_releases.resolve_github_release",
            lambda *_args, **_kwargs: None,
        )

        def download_source(_location, destination, **_kwargs):
            checkout = Path(destination) / "checkout"
            shutil.copytree(repository_source, checkout, dirs_exist_ok=True)
            return SimpleNamespace(root=str(checkout), commit="a" * 40)

        monkeypatch.setattr(
            "infernux.plugins.github_releases.download_github_source",
            download_source,
        )

        def retrieve(_url, destination):
            shutil.copy2(package, destination)
            _FakeInxPack.archives[str(Path(destination).resolve())] = dict(expected_archive)

        monkeypatch.setattr(plugin_manager_module, "_download_url_package", retrieve)
        state = manager.install_source(descriptor, install_dependencies=False)
        assert state.reference == "vendor/distributed"
        record = manager.registry.installed_record("vendor/distributed")
        inventories.append(
            tuple(
                sorted(
                    (item["logical_path"], item["guid"])
                    for item in record["files"]
                )
            )
        )
        manager.shutdown()
    assert all(inventory == inventories[0] for inventory in inventories)


def test_http_package_download_streams_and_preserves_destination_on_failure(
    tmp_path, monkeypatch
):
    class Response:
        def __init__(self, chunks, content_length=""):
            self.headers = {"Content-Length": content_length} if content_length else {}
            self._chunks = iter(chunks)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return next(self._chunks, b"")

    observed = {}

    def urlopen(request, *, timeout):
        observed["url"] = request.full_url
        observed["timeout"] = timeout
        return Response((b"1234", b"5678", b"9"), content_length="999999999999")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    destination = tmp_path / "download.inxpkg"
    plugin_manager_module._download_url_package(
        "https://packages.example/plugin.inxpkg",
        str(destination),
    )
    assert observed == {"url": "https://packages.example/plugin.inxpkg", "timeout": 30}
    assert destination.read_bytes() == b"123456789"

    class FailedResponse(Response):
        def read(self, _size):
            chunk = next(self._chunks, None)
            if chunk is None:
                raise OSError("connection lost")
            return chunk

    def interrupted(_request, *, timeout):
        assert timeout == 30
        return FailedResponse((b"partial",))

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        interrupted,
    )
    with pytest.raises(OSError, match="connection lost"):
        plugin_manager_module._download_url_package(
            "https://packages.example/interrupted.inxpkg",
            str(destination),
        )
    assert destination.read_bytes() == b"123456789"


def test_official_object_download_uses_primary_channel_without_github(
    tmp_path, monkeypatch
):
    manager = PluginManager(str(_project(tmp_path / "project")))
    downloaded = b"cloudflare package"
    workspace = tmp_path / "download"
    workspace.mkdir()

    def retrieve(url, destination, **_kwargs):
        assert url == "https://downloads.example/plugin.inxpkg"
        Path(destination).write_bytes(downloaded)

    monkeypatch.setattr(plugin_manager_module, "_download_url_package", retrieve)
    monkeypatch.setattr(
        "infernux.plugins.github_releases.resolve_github_release",
        lambda *_args, **_kwargs: pytest.fail("GitHub fallback must remain idle"),
    )

    path, source = manager._materialize_source(
        {
            "type": "url",
            "location": "https://downloads.example/plugin.inxpkg",
            "official": True,
            "reference": "infernux/example",
            "repository": "https://github.com/example/plugin",
            "release_tag": "v1.0.0",
        },
        str(workspace),
    )

    assert Path(path).read_bytes() == downloaded
    assert source["location"] == "https://downloads.example/plugin.inxpkg"


def test_official_object_download_falls_back_to_exact_github_release_on_network_error(
    tmp_path, monkeypatch
):
    manager = PluginManager(str(_project(tmp_path / "project")))
    workspace = tmp_path / "download"
    workspace.mkdir()
    fallback = workspace / "github.inxpkg"
    fallback.write_bytes(b"github package")

    monkeypatch.setattr(
        plugin_manager_module,
        "_download_url_package",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    def resolve(repository, destination, **kwargs):
        assert repository == "https://github.com/example/plugin"
        assert destination == str(workspace)
        assert kwargs["expected_reference"] == "infernux/example"
        assert kwargs["release_tag"] == "v1.0.0"
        return SimpleNamespace(
            path=str(fallback),
            source={
                "version": "1.0.0",
                "release_tag": "v1.0.0",
                "release_url": "https://github.com/example/plugin/releases/tag/v1.0.0",
            },
        )

    monkeypatch.setattr(
        "infernux.plugins.github_releases.resolve_github_release", resolve
    )

    path, source = manager._materialize_source(
        {
            "type": "url",
            "location": "https://downloads.example/plugin.inxpkg",
            "official": True,
            "reference": "infernux/example",
            "repository": "https://github.com/example/plugin",
            "release_tag": "v1.0.0",
        },
        str(workspace),
    )

    assert path == str(fallback)
    assert source["type"] == "url"
    assert source["location"] == "https://downloads.example/plugin.inxpkg"
    assert source["acquisition"] == "github-release"


def test_uninstall_follows_guid_and_removes_plugin_owned_modification(tmp_path):
    source = _source(tmp_path / "source", "vendor/movable")
    (source / "Data.bin").write_bytes(b"payload")
    package = _export(source, tmp_path / "movable.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), install_dependencies=False)
    old = project / "Assets/Plugins/Data.bin"
    moved = project / "Assets/UserLayout/Moved.bin"
    moved.parent.mkdir(parents=True)
    old.replace(moved)
    Path(str(old) + ".meta").replace(Path(str(moved) + ".meta"))
    manager.uninstall("vendor/movable")
    assert not moved.exists()

    manager.install_package(str(package), install_dependencies=False)
    old.write_bytes(b"user modified")
    result = manager.uninstall("vendor/movable")
    assert not old.exists()
    assert result["preserved_shared_files"] == []


def test_uninstall_file_failure_rolls_back_payload_meta_and_registry(tmp_path, monkeypatch):
    source = _source(tmp_path / "source", "vendor/uninstall-rollback")
    (source / "Data.bin").write_bytes(b"payload")
    package = _export(source, tmp_path / "uninstall-rollback.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), install_dependencies=False)
    payload = project / "Assets/Plugins/Data.bin"
    meta = Path(str(payload) + ".meta")
    original_remove = os.remove
    failed = False

    def fail_once(path):
        nonlocal failed
        if not failed and Path(path).resolve() == meta.resolve():
            failed = True
            raise PermissionError("simulated locked metadata")
        return original_remove(path)

    monkeypatch.setattr("infernux.plugins.manager.os.remove", fail_once)
    with pytest.raises(PermissionError, match="locked metadata"):
        manager.uninstall("vendor/uninstall-rollback")

    assert payload.read_bytes() == b"payload"
    assert meta.is_file()
    assert manager.registry.installed_record("vendor/uninstall-rollback") is not None


def test_requirements_failure_rolls_back_new_plugin_dependencies(tmp_path, monkeypatch):
    dependency = _source(tmp_path / "dependency", "vendor/requirement-child")
    (dependency / "Child.bin").write_bytes(b"child")
    dependency_package = _export(dependency, tmp_path / "requirement-child.inxpkg")
    parent = _source(tmp_path / "parent", "vendor/requirement-parent")
    (parent / "requirements.txt").write_text(
        "vendor/requirement-child\nbroken-wheel==1\n", encoding="utf-8"
    )
    (parent / "Parent.bin").write_bytes(b"parent")
    parent_package = _export(parent, tmp_path / "requirement-parent.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.registry.add_package(
        "vendor/requirement-child",
        source={"type": "local", "location": str(dependency_package)},
    )
    monkeypatch.setattr(manager, "_project_python_executable", lambda: "project-python")
    monkeypatch.setattr(
        manager,
        "_run_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated pip resolution failure")
        ),
    )

    with pytest.raises(RuntimeError, match="pip resolution failure"):
        manager.install_package(str(parent_package))

    assert manager.registry.installed() == ()
    assert not (project / "Assets/Plugins/Child.bin").exists()
    assert not (project / "Assets/Plugins/Parent.bin").exists()


def test_pip_side_effect_rolls_back_when_file_registry_transaction_fails(
    tmp_path, monkeypatch
):
    source = _source(tmp_path / "source", "vendor/pip-rollback")
    (source / "requirements.txt").write_text(
        "shared-wheel==1.0\n", encoding="utf-8"
    )
    (source / "Data.bin").write_bytes(b"payload")
    package = _export(source, tmp_path / "pip-rollback.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    environment: dict[str, str] = {}
    commands: list[list[str]] = []

    def run(command, cwd=None):
        commands.append(list(command))
        if command[2:5] == ["pip", "list", "--format=json"]:
            return type("Result", (), {"stdout": json.dumps([
                {"name": name, "version": version}
                for name, version in environment.items()
            ])})()
        if command[2:4] == ["pip", "install"]:
            environment["shared-wheel"] = "1.0"
        elif command[2:4] == ["pip", "uninstall"]:
            for name in command[5:]:
                environment.pop(name, None)
        return type("Result", (), {"stdout": "ok"})()

    monkeypatch.setattr(manager, "_project_python_executable", lambda: "project-python")
    monkeypatch.setattr(manager, "_run_process", run)
    monkeypatch.setattr(
        manager.registry,
        "record_install",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated registry rejection")
        ),
    )

    with pytest.raises(RuntimeError, match="registry rejection"):
        manager.install_package(str(package))

    assert environment == {}
    assert manager.registry.installed() == ()
    assert any(command[2:4] == ["pip", "uninstall"] for command in commands)
    assert not (project / "Assets/Plugins/Data.bin").exists()


def test_shared_pip_distribution_is_removed_only_after_last_plugin_owner(
    tmp_path, monkeypatch
):
    packages = []
    for label in ("first", "second"):
        source = _source(tmp_path / label, f"vendor/{label}")
        (source / "requirements.txt").write_text(
            "shared-wheel==1.0\n", encoding="utf-8"
        )
        (source / f"{label}.bin").write_bytes(label.encode("utf-8"))
        packages.append(_export(source, tmp_path / f"{label}.inxpkg"))
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    environment: dict[str, str] = {}
    commands: list[list[str]] = []

    def run(command, cwd=None):
        commands.append(list(command))
        if command[2:5] == ["pip", "list", "--format=json"]:
            return type("Result", (), {"stdout": json.dumps([
                {"name": name, "version": version}
                for name, version in environment.items()
            ])})()
        if command[2:4] == ["pip", "install"]:
            environment["shared-wheel"] = "1.0"
        elif command[2:4] == ["pip", "uninstall"]:
            for name in command[5:]:
                environment.pop(name, None)
        return type("Result", (), {"stdout": "ok"})()

    monkeypatch.setattr(manager, "_project_python_executable", lambda: "project-python")
    monkeypatch.setattr(manager, "_run_process", run)
    manager.install_package(str(packages[0]))
    manager.install_package(str(packages[1]))
    ledger = manager.registry.load()["python_dependencies"]
    assert [owner["reference"] for owner in ledger[0]["owners"]] == [
        "vendor/first",
        "vendor/second",
    ]

    manager.uninstall("vendor/first")
    assert environment == {"shared-wheel": "1.0"}
    assert not any(
        command[2:4] == ["pip", "uninstall"] for command in commands
    )
    manager.uninstall("vendor/second")
    assert environment == {}
    assert sum(
        command[2:4] == ["pip", "uninstall"] for command in commands
    ) == 1
    assert manager.registry.load()["python_dependencies"] == []


@pytest.mark.parametrize("preinstalled", (False, True))
def test_startup_restores_installed_plugin_requirements_in_new_environment(
    tmp_path, monkeypatch, preinstalled
):
    source = _source(tmp_path / "source", "vendor/portable-python")
    (source / "requirements.txt").write_text(
        "shared-wheel>=1,<2\n", encoding="utf-8"
    )
    (source / "Data.bin").write_bytes(b"payload")
    package = _export(source, tmp_path / "portable-python.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    environment: dict[str, str] = {"shared-wheel": "1.5"} if preinstalled else {}
    commands: list[list[str]] = []
    restored_files = []
    next_available_version = "1.5"

    def run(command, cwd=None):
        commands.append(list(command))
        if command[2:5] == ["pip", "list", "--format=json"]:
            return type(
                "Result",
                (),
                {
                    "stdout": json.dumps(
                        [
                            {"name": name, "version": version}
                            for name, version in environment.items()
                        ]
                    )
                },
            )()
        if command[2:4] == ["pip", "install"]:
            text = Path(command[-1]).read_text(encoding="utf-8")
            restored_files.append(text)
            environment["shared-wheel"] = "1.5" if "shared-wheel==1.5" in text else next_available_version
        return type("Result", (), {"stdout": "ok"})()

    monkeypatch.setattr(manager, "_project_python_executable", lambda: "project-python")
    monkeypatch.setattr(manager, "_run_process", run)
    manager.install_package(str(package))
    installed_dependency = manager.registry.load()["python_dependencies"][0]
    assert installed_dependency["installed_version"] == "1.5"
    assert installed_dependency["managed"] is not preinstalled
    if preinstalled:
        assert not any(command[2:4] == ["pip", "install"] for command in commands)
    shared_registry = Path(manager.registry.path).read_bytes()
    shared_lock = Path(manager.registry.lock_path).read_bytes()
    next_available_version = "1.8"
    environment.clear()
    commands.clear()

    assert manager._reconcile_python_requirements_for_startup() == (
        "vendor/portable-python",
    )
    assert environment == {"shared-wheel": "1.5"}
    assert "shared-wheel==1.5" in restored_files[-1]
    assert Path(manager.registry.path).read_bytes() == shared_registry
    assert Path(manager.registry.lock_path).read_bytes() == shared_lock
    assert sum(command[2:4] == ["pip", "install"] for command in commands) == 1
    ledger = manager.registry.load()["python_dependencies"]
    assert ledger[0]["owners"] == [
        {
            "reference": "vendor/portable-python",
            "requirements": ["shared-wheel>=1,<2"],
        }
    ]
    assert ledger[0]["baseline_version"] == ""
    assert ledger[0]["installed_version"] == "1.5"
    assert manager.registry.installed_record("vendor/portable-python")[
        "python_requirements"
    ] == [{"name": "shared-wheel", "requirement": "shared-wheel>=1,<2"}]

    commands.clear()
    assert manager._reconcile_python_requirements_for_startup() == ()
    assert not any(command[2:4] == ["pip", "install"] for command in commands)

    environment["shared-wheel"] = "1.8"
    assert manager._reconcile_python_requirements_for_startup() == ("vendor/portable-python",)
    assert environment == {"shared-wheel": "1.5"}


@pytest.mark.parametrize("observed", ({}, {"shared-wheel": "2.0"}))
@pytest.mark.parametrize("project_install", (False, True))
def test_pip_success_without_a_satisfied_requirement_cannot_publish(tmp_path, monkeypatch, observed, project_install):
    manager = PluginManager(str(_project(tmp_path / "project")))
    monkeypatch.setattr(manager, "_project_python_executable", lambda: "project-python")
    snapshots = iter(({}, observed))
    monkeypatch.setattr(manager, "_python_environment_snapshot", lambda _executable=None: next(snapshots))
    monkeypatch.setattr(manager, "_run_process", lambda *_args, **_kwargs: SimpleNamespace(stdout="ok"))
    restored = []
    monkeypatch.setattr(manager, "_restore_python_environment", lambda before, **_kwargs: restored.append(before))
    with pytest.raises(RuntimeError, match="requested Python requirements remain unresolved"):
        if project_install:
            manager.install_pip("pip install shared-wheel>=1,<2")
        else:
            manager._install_pip_lines(("shared-wheel>=1,<2",))
    assert restored == [{}]
    assert manager.registry.load()["python_dependencies"] == []


def test_install_reuses_satisfied_project_python_requirement_without_running_pip(
    tmp_path, monkeypatch
):
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    monkeypatch.setattr(manager, "_project_python_executable", lambda: "project-python")
    monkeypatch.setattr(
        manager,
        "_python_environment_snapshot",
        lambda _executable=None: {"shared-wheel": "1.5"},
    )
    monkeypatch.setattr(
        manager,
        "_run_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("satisfied requirements must not invoke pip")
        ),
    )

    effect = manager._install_pip_lines(("shared-wheel>=1,<2",))

    assert effect.before == {"shared-wheel": "1.5"}
    assert effect.after == effect.before
    assert effect.requirements == (
        {"name": "shared-wheel", "requirement": "shared-wheel>=1,<2"},
    )
    assert effect.command == ()
    assert effect.changes == ()


def test_reference_identity_is_casefolded_across_platforms(tmp_path):
    lower = _source(tmp_path / "lower", "Vendor/CaseIdentity")
    upper = _source(tmp_path / "upper", "vendor/caseidentity")
    (lower / "Data.bin").write_bytes(b"one")
    (upper / "Data.bin").write_bytes(b"two")
    lower_package = _export(lower, tmp_path / "lower.inxpkg")
    upper_package = _export(upper, tmp_path / "upper.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(lower_package), install_dependencies=False)

    with pytest.raises(RuntimeError, match="already installed with different content"):
        manager.install_package(str(upper_package), install_dependencies=False)

    installed = manager.registry.installed()
    assert len(installed) == 1
    assert installed[0]["reference"] == "Vendor/CaseIdentity"


def test_failed_preload_unload_aborts_uninstall_and_requires_restart(tmp_path):
    source = _source(tmp_path / "source", "vendor/stubborn")
    runtime = source / "runtime"
    runtime.mkdir()
    (runtime / "service.py").write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class StubbornService(InxPreload):\n"
        "    def preload(self, context): pass\n"
        "    def unload(self): raise RuntimeError('service still running')\n",
        encoding="utf-8",
    )
    package = _export(source, tmp_path / "stubborn.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), install_dependencies=False)
    script = project / "Packages/vendor/stubborn/runtime/service.py"

    with pytest.raises(RuntimeError, match="uninstall aborted"):
        manager.uninstall("vendor/stubborn")

    assert script.is_file()
    assert manager.registry.installed_record("vendor/stubborn") is not None
    failed_state = next(iter(manager.preloads.states.values()))
    assert failed_state.loaded is True
    assert failed_state.restart_required is True
    assert "service still running" in failed_state.error

    failed_state.instance.unload = lambda: None
    manager.uninstall("vendor/stubborn")
    assert not script.exists()


@pytest.mark.parametrize("keep_author", [False, True])
@pytest.mark.parametrize("fail_uninstall", [False, True])
def test_uninstall_removes_only_owned_script_caches_transactionally(
    tmp_path, monkeypatch, keep_author, fail_uninstall
):
    import py_compile
    from infernux.core.assets import AssetManager

    source = _source(tmp_path / "source", "vendor/cached")
    (source / "editor").mkdir()
    (source / "editor" / "owned.py").write_text("VALUE = 1\n", encoding="utf-8")
    package = _export(source, tmp_path / "cached.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), install_dependencies=False)
    role = project / "Packages/vendor/cached/editor"
    # Reproduce a legacy authored spelling on both case-sensitive and
    # case-insensitive hosts. GUID-based uninstall must follow the moved file.
    intermediate = role.with_name("role-moving")
    role.rename(intermediate)
    role = role.with_name("Editor")
    intermediate.rename(role)
    owned = role / "owned.py"
    caches = [Path(py_compile.compile(str(owned), optimize=level, doraise=True)) for level in (0, 1, 2)]
    author = role / "author.py"
    if keep_author:
        author.write_text("VALUE = 2\n", encoding="utf-8")
        author_cache = Path(py_compile.compile(str(author), doraise=True))
        author_bytes = author_cache.read_bytes()
    if fail_uninstall:
        echo_before = AssetManager.is_watcher_echo_suppressed("deleted", str(owned))
        original_remove = plugin_manager_module._InstallTransaction.remove

        def fail_after_cache(self, path):
            if path == str(owned):
                raise OSError("injected source removal failure")
            return original_remove(self, path)

        monkeypatch.setattr(plugin_manager_module._InstallTransaction, "remove", fail_after_cache)
        with pytest.raises(OSError, match="injected source removal failure"):
            manager.uninstall("vendor/cached")
        assert owned.is_file()
        assert all(cache.is_file() for cache in caches)
        assert manager.registry.installed_record("vendor/cached") is not None
        assert AssetManager.is_watcher_echo_suppressed("deleted", str(owned)) == echo_before
    else:
        manager.uninstall("vendor/cached")
        assert not owned.exists()
        assert not any(cache.exists() for cache in caches)
        assert AssetManager.is_watcher_echo_suppressed("deleted", str(owned))
        assert role.exists() == keep_author
        manager.install_package(str(package), install_dependencies=False)
        installed_role = role if keep_author else role.with_name("editor")
        assert (installed_role / "owned.py").is_file()
    if keep_author:
        assert author.read_text(encoding="utf-8") == "VALUE = 2\n"
        assert author_cache.read_bytes() == author_bytes
