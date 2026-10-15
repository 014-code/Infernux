from __future__ import annotations

from test_inxpackage_plugins import (
    InxPackage,
    Path,
    PluginManager,
    PluginRegistry,
    SimpleNamespace,
    _export,
    _meta,
    _project,
    _source,
    json,
    localized_intro,
    os,
    parse_markdown_blocks,
    path_key,
    player_file_exported,
    preload_module,
    project_guid_paths,
    pytest,
    split_markdown_images,
    sys,
    _fake_inxpack as _fake_inxpack,
)

def test_package_preload_supports_relative_imports(tmp_path):
    source = _source(tmp_path / "source", "vendor/relative-preload")
    package_module = source / "editor" / "infernux_relative_preload"
    package_module.mkdir(parents=True)
    (package_module / "__init__.py").write_text("", encoding="utf-8")
    (package_module / "service.py").write_text(
        "VALUE = 'relative-import-ready'\n", encoding="utf-8"
    )
    (package_module / "lifecycle.py").write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "from .service import VALUE\n"
        "class RelativePreload(InxPreload):\n"
        "    def preload(self, context):\n"
        "        Path(context.project_root, 'relative-preload.txt').write_text(VALUE)\n",
        encoding="utf-8",
    )
    package = _export(source, tmp_path / "relative-preload.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))

    state = manager.install_package(str(package), install_dependencies=False)

    assert state.loaded
    assert state.error == ""
    assert (project / "relative-preload.txt").read_text(encoding="utf-8") == (
        "relative-import-ready"
    )


def test_editor_translation_catalog_loads_before_plugin_preload_and_unloads_with_package(
    tmp_path, monkeypatch
):
    from infernux.engine import i18n

    monkeypatch.setattr(i18n, "_current_locale", "en")
    monkeypatch.setattr(i18n, "_translation_owners", {})
    monkeypatch.setattr(i18n, "_contributed_tables", {})
    source = _source(tmp_path / "source", "vendor/localized-panel")
    editor = source / "editor"
    editor.mkdir()
    (editor / "translations.json").write_text(
        json.dumps(
            {
                "$schema": "infernux.editor_translations",
                "locales": {
                    "en": {"vendor.localized_panel.menu": "Localized Tools"},
                    "zh": {"vendor.localized_panel.menu": "本地化工具"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (editor / "lifecycle.py").write_text(
        "from pathlib import Path\n"
        "from infernux.engine.i18n import t\n"
        "from infernux.lifecycle import InxPreload\n"
        "class LocalizedPreload(InxPreload):\n"
        "    def preload(self, context):\n"
        "        Path(context.project_root, 'translation-ready.txt').write_text(\n"
        "            t('vendor.localized_panel.menu'), encoding='utf-8')\n",
        encoding="utf-8",
    )
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))

    state = manager.install_package(
        str(_export(source, tmp_path / "localized-panel.inxpkg")),
        install_dependencies=False,
    )
    assert state.loaded, state.error
    assert (project / "translation-ready.txt").read_text(encoding="utf-8") == "Localized Tools"
    assert i18n.t("vendor.localized_panel.menu") == "Localized Tools"

    manager.set_enabled("vendor/localized-panel", False)
    assert i18n.t("vendor.localized_panel.menu") == "vendor.localized_panel.menu"


def test_editor_translation_catalog_hot_reload_is_atomic(tmp_path, monkeypatch):
    from infernux.engine import i18n

    monkeypatch.setattr(i18n, "_current_locale", "en")
    monkeypatch.setattr(i18n, "_translation_owners", {})
    monkeypatch.setattr(i18n, "_contributed_tables", {})
    source = _source(tmp_path / "source", "vendor/live-translations")
    editor = source / "editor"
    editor.mkdir()
    catalog = editor / "translations.json"

    def write_catalog(value: str) -> None:
        catalog.write_text(
            json.dumps(
                {
                    "$schema": "infernux.editor_translations",
                    "locales": {
                        "en": {"vendor.live.menu": value},
                        "zh": {"vendor.live.menu": f"zh-{value}"},
                    },
                }
            ),
            encoding="utf-8",
        )

    write_catalog("First")
    (editor / "lifecycle.py").write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class LiveTranslationsPreload(InxPreload):\n"
        "    def preload(self, context):\n"
        "        pass\n",
        encoding="utf-8",
    )
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    state = manager.install_package(
        str(_export(source, tmp_path / "live-translations.inxpkg")),
        install_dependencies=False,
    )
    assert state.loaded, state.error
    installed_catalog = project / "Packages/vendor/live-translations/editor/translations.json"
    assert i18n.t("vendor.live.menu") == "First"
    live_initial = next(
        item
        for item in manager.preloads.states.values()
        if item.package_reference == "vendor/live-translations"
    )

    document = json.loads(installed_catalog.read_text(encoding="utf-8"))
    document["locales"]["en"]["vendor.live.menu"] = "Second"
    document["locales"]["zh"]["vendor.live.menu"] = "zh-Second"
    installed_catalog.write_text(json.dumps(document), encoding="utf-8")
    manager._on_script_catalog_changed(str(installed_catalog), "modified")
    assert i18n.t("vendor.live.menu") == "Second"

    live_before = next(
        item
        for item in manager.preloads.states.values()
        if item.package_reference == "vendor/live-translations"
    )
    assert live_before is live_initial
    installed_catalog.write_text("{", encoding="utf-8")
    manager._on_script_catalog_changed(str(installed_catalog), "modified")
    live_after = next(
        item
        for item in manager.preloads.states.values()
        if item.package_reference == "vendor/live-translations"
    )
    assert live_after is live_before
    assert live_after.loaded
    assert i18n.t("vendor.live.menu") == "Second"
    assert "JSONDecodeError" in manager.preloads.failures[path_key(installed_catalog)]


@pytest.mark.parametrize('role,extract_only', [('editor', False), ('runtime', False), ('runtime', True)])
def test_import_reuses_authored_role_root_without_moving_user_files(tmp_path, role, extract_only):
    source = _source(tmp_path / "source", "vendor/authored-role")
    module = source / role / "authored_role_probe"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "service.py").write_text("VALUE = 'current'\n", encoding="utf-8")
    (module / "lifecycle.py").write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "class AuthoredRolePreload(InxPreload):\n"
        "    def preload(self, context):\n"
        "        from authored_role_probe.service import VALUE\n"
        "        Path(context.project_root, 'authored-role.txt').write_text(VALUE)\n",
        encoding="utf-8",
    )
    package = _export(source, tmp_path / "authored-role.inxpkg")
    project = _project(tmp_path / "project")
    authored_root = project / "Packages/vendor/authored-role" / role.title()
    authored_root.mkdir(parents=True)
    author = authored_root / "UserKeep.py"
    author.write_text("VALUE = 'author'\n", encoding="utf-8")
    if extract_only:
        InxPackage.extract(str(package), str(project))
        manager = PluginManager.startup(str(project))
    else:
        manager = PluginManager.startup(str(project))
        manager.install_package(str(package), install_dependencies=False)
    try:
        assert [p.name for p in authored_root.parent.iterdir() if p.is_dir() and p.name.casefold() == role] == [role.title()]
        assert author.read_text(encoding="utf-8") == "VALUE = 'author'\n"
        assert (authored_root / "authored_role_probe/service.py").is_file()
        state = next(state for state in manager.preloads.states.values() if state.type_name == "AuthoredRolePreload")
        assert state.loaded, state.error
        assert (project / "authored-role.txt").read_text(encoding="utf-8") == "current"
    finally:
        manager.shutdown()


@pytest.mark.skipif(os.path.normcase("Editor") == os.path.normcase("editor"), reason="case-sensitive filesystem boundary")
@pytest.mark.parametrize("extract_only", [False, True])
def test_import_rejects_ambiguous_authored_role_before_writing(tmp_path, extract_only):
    source = _source(tmp_path / "source", "vendor/ambiguous")
    (source / "editor").mkdir()
    (source / "editor/new.py").write_text("VALUE = 1\n", encoding="utf-8")
    package = _export(source, tmp_path / "ambiguous.inxpkg")
    project = _project(tmp_path / "project")
    root = project / "Packages/vendor/ambiguous"
    for role in ("Editor", "editor"):
        (root / role).mkdir(parents=True)
        (root / role / "keep.txt").write_text(role, encoding="utf-8")
    manager = PluginManager(str(project))
    with pytest.raises(ValueError, match="Ambiguous package role 'editor'"):
        if extract_only:
            InxPackage.extract(str(package), str(project))
        else:
            manager.install_package(str(package), install_dependencies=False)
    assert manager.registry.installed_record("vendor/ambiguous") is None
    for role in ("Editor", "editor"):
        assert (root / role / "keep.txt").read_text(encoding="utf-8") == role
        assert not (root / role / "new.py").exists()


@pytest.mark.parametrize("outside_package", [False, True])
def test_project_package_destination_rejects_redirected_role(tmp_path, outside_package, directory_junction):
    from infernux.plugins.package import package_destination

    project = _project(tmp_path / "project")
    root = project / "Packages/vendor/redirected"
    root.mkdir(parents=True)
    target = tmp_path / "outside" if outside_package else root / "another_directory"
    target.mkdir()
    if os.name == "nt":
        # Exercise Windows directory redirection without requiring symbolic-link
        # privileges. This checks the destination boundary, not symlink APIs.
        directory_junction(root / "Editor", target)
    else:
        (root / "Editor").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        package_destination("vendor/redirected", "editor/new.py", project_root=str(project))
    assert not list(target.iterdir())


@pytest.mark.parametrize("role", ["editor", "Editor", "runtime", "Runtime"])
def test_package_preload_absolute_imports_use_authored_role_spelling(tmp_path, role):
    import sys

    project = _project(tmp_path / "project")
    role_root = project / "Packages" / "case_probe" / role
    module = role_root / "infernux_case_probe"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "service.py").write_text("VALUE = 'case-ready'\n", encoding="utf-8")
    lifecycle = module / "lifecycle.py"
    lifecycle.write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "class CasePreload(InxPreload):\n"
        "    def preload(self, context):\n"
        "        from infernux_case_probe.service import VALUE\n"
        "        Path(context.project_root, 'case-ready.txt').write_text(VALUE)\n",
        encoding="utf-8",
    )
    # Assert the physical spelling even on case-insensitive Windows, where
    # importing from a fabricated lowercase directory would hide this bug.
    with preload_module._temporary_import_paths(str(lifecycle), str(project), "case_probe"):
        assert str(role_root.resolve()) in sys.path

    manager = PluginManager.startup(str(project))
    try:
        state = next(iter(manager.preloads.states.values()))
        assert state.loaded, state.error
        assert (project / "case-ready.txt").read_text(encoding="utf-8") == "case-ready"
    finally:
        manager.shutdown()


def test_manifestless_local_package_preload_supports_relative_imports(tmp_path):
    project = _project(tmp_path / "project")
    package_module = project / "Packages" / "abc" / "Editor" / "local_tool"
    package_module.mkdir(parents=True)
    (package_module / "service.py").write_text(
        "VALUE = 'local-package-ready'\n", encoding="utf-8"
    )
    (package_module / "lifecycle.py").write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "from .service import VALUE\n"
        "class LocalPreload(InxPreload):\n"
        "    def preload(self, context):\n"
        "        Path(context.project_root, 'local-package.txt').write_text(VALUE)\n",
        encoding="utf-8",
    )

    manager = PluginManager.startup(str(project))

    state = next(iter(manager.preloads.states.values()))
    assert state.loaded is True
    assert state.package_reference == "abc"
    assert state.module_name == (
        "_infernux_packages.abc.editor.local_tool.lifecycle"
    )
    assert (project / "local-package.txt").read_text(encoding="utf-8") == (
        "local-package-ready"
    )

    manager.shutdown()
    (project / "local-package.txt").unlink()
    runtime_preloads = preload_module.PreloadManager(str(project), runtime=True)
    assert runtime_preloads.reload_all() == ()
    assert not (project / "local-package.txt").exists()


@pytest.mark.parametrize("fail_preload", [False, True])
def test_preload_library_ownership_follows_success_and_failure(tmp_path, fail_preload):
    from infernux.engine.project_context import is_project_component_script

    project = _project(tmp_path / "project")
    package = _source(project / "Packages/vendor/compiler", "vendor/compiler")
    library = package / "runtime/provider"
    library.mkdir(parents=True)
    implementation = library / "backend.py"
    implementation.write_text("from contextlib import contextmanager\n", encoding="utf-8")
    author = package / "runtime/AuthorComponent.py"
    author.write_text("", encoding="utf-8")
    (library / "lifecycle.py").write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class LibraryPreload(InxPreload):\n"
        "    def preload(self, context):\n"
        "        context.own_python_library('runtime/provider')\n"
        + ("        raise RuntimeError('initialization failed')\n" if fail_preload else "")
        + "    def unload(self): pass\n",
        encoding="utf-8",
    )
    manager = PluginManager.startup(str(project))
    try:
        state = next(iter(manager.preloads.states.values()))
        assert state.loaded is not fail_preload, state.error
        assert is_project_component_script(str(implementation), str(project)) is fail_preload
        assert is_project_component_script(str(author), str(project))
    finally:
        manager.shutdown()
    assert is_project_component_script(str(implementation), str(project))


def test_package_preloads_with_matching_module_paths_are_isolated(tmp_path):
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))

    for reference, marker in (("vendor/first", "first"), ("vendor/second", "second")):
        source = _source(tmp_path / marker, reference)
        package_module = source / "editor" / "shared_name"
        package_module.mkdir(parents=True)
        (package_module / "__init__.py").write_text("", encoding="utf-8")
        (package_module / "service.py").write_text(
            f"VALUE = {marker!r}\n", encoding="utf-8"
        )
        (package_module / "lifecycle.py").write_text(
            "from pathlib import Path\n"
            "from infernux.lifecycle import InxPreload\n"
            "from .service import VALUE\n"
            "class SharedNamePreload(InxPreload):\n"
            "    def preload(self, context):\n"
            "        Path(context.project_root, VALUE + '.txt').write_text(VALUE)\n",
            encoding="utf-8",
        )
        package = _export(source, tmp_path / f"{marker}.inxpkg")
        state = manager.install_package(str(package), install_dependencies=False)
        assert state.loaded

    assert (project / "first.txt").read_text(encoding="utf-8") == "first"
    assert (project / "second.txt").read_text(encoding="utf-8") == "second"
    module_names = {
        state.module_name
        for state in manager.preloads.states.values()
        if state.type_name == "SharedNamePreload"
    }
    assert len(module_names) == 2
    assert all(name.startswith("_infernux_packages.") for name in module_names)


def test_stubborn_plugin_does_not_block_unrelated_plugin_lifecycle(
    tmp_path, monkeypatch
):
    stubborn = _source(tmp_path / "stubborn", "vendor/stubborn-slice")
    (stubborn / "runtime").mkdir()
    (stubborn / "runtime" / "service.py").write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class Stubborn(InxPreload):\n"
        "    def preload(self, context): pass\n"
        "    def unload(self): raise RuntimeError('restart only me')\n",
        encoding="utf-8",
    )
    safe = _source(tmp_path / "safe", "vendor/safe-slice")
    (safe / "runtime").mkdir()
    (safe / "runtime" / "service.py").write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "class Safe(InxPreload):\n"
        "    def preload(self, context): self.root=context.project_root\n"
        "    def unload(self): Path(self.root, 'safe-unloaded.txt').write_text('yes')\n",
        encoding="utf-8",
    )
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(
        str(_export(stubborn, tmp_path / "stubborn-slice.inxpkg")),
        install_dependencies=False,
    )
    manager.install_package(
        str(_export(safe, tmp_path / "safe-slice.inxpkg")),
        install_dependencies=False,
    )
    stubborn_state = next(
        state
        for state in manager.preloads.states.values()
        if state.package_reference == "vendor/stubborn-slice"
    )
    stubborn_instance = stubborn_state.instance
    monkeypatch.setattr(
        manager.preloads,
        "_source_paths",
        lambda: pytest.fail("targeted lifecycle performed a full AST scan"),
    )

    manager.set_enabled("vendor/safe-slice", False)
    assert stubborn_state.instance is stubborn_instance
    manager.set_enabled("vendor/safe-slice", True)
    manager.uninstall("vendor/safe-slice")
    assert stubborn_state.instance is stubborn_instance
    assert (project / "safe-unloaded.txt").is_file()

    stubborn_state.instance.unload = lambda: None
    manager.shutdown()


def test_direct_manager_construction_does_not_replace_active_singleton(tmp_path):
    active_project = _project(tmp_path / "active")
    detached_project = _project(tmp_path / "detached")
    active = PluginManager.startup(str(active_project))
    detached = PluginManager(str(detached_project))
    assert PluginManager.instance() is active
    detached.shutdown()
    assert PluginManager.instance() is active


def test_native_guid_index_avoids_meta_walk_and_drives_preload_catalog(
    tmp_path, monkeypatch
):
    project = _project(tmp_path / "project")
    script = project / "Assets" / "indexed.py"
    script.write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class Indexed(InxPreload):\n"
        "    def preload(self, context): pass\n",
        encoding="utf-8",
    )
    guid = "1234567890abcdef1234567890abcdef"
    Path(str(script) + ".meta").write_text(_meta(guid), encoding="utf-8")

    class Database:
        project_root = str(project)

        @staticmethod
        def get_all_guids():
            return [guid]

        @staticmethod
        def get_path_from_guid(value):
            return str(script) if value == guid else ""

    class Engine:
        @staticmethod
        def get_asset_database():
            return Database()

    monkeypatch.setattr(
        "infernux.plugins.project_index._scan_guid_paths",
        lambda _root: pytest.fail("native GUID catalog fell back to os.walk"),
    )
    paths, native = project_guid_paths(str(project), engine=Engine())
    assert native is True
    assert paths == {guid: str(script.resolve())}
    manager = PluginManager(str(project), engine=Engine())
    manager.reload_all()
    assert [state.type_name for state in manager.preloads.states.values()] == [
        "Indexed"
    ]


def test_plugin_refresh_does_not_hide_the_project_database_error(tmp_path):
    project = _project(tmp_path / "project")

    def refresh():
        raise RuntimeError("asset publication failed")

    database = SimpleNamespace(project_root=str(project), refresh=refresh)
    engine = SimpleNamespace(get_asset_database=lambda: database)
    manager = PluginManager(str(project), engine=engine)
    with pytest.raises(RuntimeError, match="asset publication failed"):
        manager._refresh_editor_assets()


def test_detached_plugin_refresh_does_not_touch_another_project(tmp_path, monkeypatch):
    from infernux.core.assets import AssetManager

    project = _project(tmp_path / "project")
    refreshed = []
    database = SimpleNamespace(
        project_root=str(tmp_path / "other"), refresh=lambda: refreshed.append(True)
    )
    monkeypatch.setattr(AssetManager, "_asset_database", database)
    monkeypatch.setattr(
        "infernux.lib.AssetRegistry",
        SimpleNamespace(instance=lambda: SimpleNamespace(get_asset_database=lambda: database)),
    )
    PluginManager(str(project))._refresh_editor_assets()
    assert refreshed == []


def test_native_guid_index_failure_does_not_probe_another_database(
    tmp_path, monkeypatch
):
    project = _project(tmp_path / "project")

    class Engine:
        @staticmethod
        def get_asset_database():
            raise RuntimeError("native catalog unavailable")

    monkeypatch.setattr(
        "infernux.plugins.project_index._scan_guid_paths",
        lambda _root: pytest.fail("native GUID failure fell back to os.walk"),
    )
    with pytest.raises(RuntimeError, match="native catalog unavailable"):
        project_guid_paths(str(project), engine=Engine())


def test_static_preload_discovery_imports_only_lifecycle_candidates(tmp_path):
    project = _project(tmp_path / "project")
    marker = project / "ordinary-imported.txt"
    (project / "Assets" / "ordinary.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('wrong')\n",
        encoding="utf-8",
    )
    lifecycle = project / "Assets" / "startup.py"
    lifecycle.write_text(
        "from abc import abstractmethod\n"
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload as Lifecycle\n"
        "class Foundation(Lifecycle):\n"
        "    @abstractmethod\n"
        "    def configure(self): ...\n"
        "    def preload(self, context): ...\n"
        "class Startup(Foundation):\n"
        "    def configure(self): return None\n"
        "    def preload(self, context):\n"
        "        Path(context.project_root, 'preloaded.txt').write_text(context.script_guid)\n"
        "    def unload(self):\n"
        "        Path(__file__).with_name('unloaded.txt').write_text('yes')\n",
        encoding="utf-8",
    )
    manager = PluginManager.startup(str(project))
    states = manager.preloads.snapshots()
    assert len(states) == 1
    assert states[0]["type_name"] == "Startup"
    assert states[0]["loaded"] is True
    assert not marker.exists()
    first_guid = (project / "preloaded.txt").read_text(encoding="utf-8")
    manager.reload_all()
    assert (project / "Assets" / "unloaded.txt").is_file()
    assert (project / "preloaded.txt").read_text(encoding="utf-8") == first_guid


def test_static_preload_discovery_accepts_lowercase_public_namespace(tmp_path):
    project = _project(tmp_path / "project")
    lifecycle = project / "Assets" / "startup.py"
    lifecycle.write_text(
        "from pathlib import Path\n"
        "import infernux as inx\n"
        "class Startup(inx.InxPreload):\n"
        "    def preload(self, context):\n"
        "        Path(context.project_root, 'lowercase-preloaded.txt').write_text(context.script_guid)\n",
        encoding="utf-8",
    )

    manager = PluginManager.startup(str(project))

    states = manager.preloads.snapshots()
    assert len(states) == 1
    assert states[0]["type_name"] == "Startup"
    assert states[0]["loaded"] is True
    assert (project / "lowercase-preloaded.txt").read_text(encoding="utf-8")


def test_preload_hot_reload_keeps_public_namespace_identity_and_last_good_state(tmp_path):
    import infernux as public_namespace

    project = _project(tmp_path / "project")
    lifecycle = project / "Assets" / "public_api.py"

    def write_source(value: str) -> None:
        lifecycle.write_text(
            "import infernux as inx\n"
            "class PublicApi(inx.InxPreload):\n"
            "    def preload(self, context):\n"
            f"        inx._community_reload_probe = {value!r}\n"
            "        context.add_cleanup(lambda: delattr(inx, '_community_reload_probe'))\n",
            encoding="utf-8",
        )

    write_source("first")
    manager = PluginManager.startup(str(project))
    assert public_namespace._community_reload_probe == "first"
    assert sys.modules["infernux"] is public_namespace

    write_source("second")
    states = manager.preloads.reload_path(str(lifecycle))
    assert len(states) == 1 and states[0].loaded
    assert public_namespace._community_reload_probe == "second"
    assert sys.modules["infernux"] is public_namespace

    lifecycle.write_text("class PublicApi(\n", encoding="utf-8")
    retained = manager.preloads.reload_path(str(lifecycle))
    assert retained == states
    assert public_namespace._community_reload_probe == "second"
    assert sys.modules["infernux"] is public_namespace
    manager.shutdown()


def test_preload_partial_failure_runs_owned_cleanups_in_reverse_order(tmp_path):
    project = _project(tmp_path / "project")
    lifecycle = project / "Assets" / "partial_service.py"
    lifecycle.write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "class PartialService(InxPreload):\n"
        "    def preload(self, context):\n"
        "        output = Path(context.project_root, 'cleanup-order.txt')\n"
        "        def record(value):\n"
        "            output.write_text(output.read_text() + value if output.exists() else value)\n"
        "        context.add_cleanup(lambda: record('A'))\n"
        "        context.add_cleanup(lambda: record('B'))\n"
        "        raise RuntimeError('candidate failed after acquisition')\n",
        encoding="utf-8",
    )

    manager = PluginManager.startup(str(project))
    state = next(iter(manager.preloads.states.values()))
    assert state.loaded is False
    assert "candidate failed after acquisition" in state.error
    assert (project / "cleanup-order.txt").read_text(encoding="utf-8") == "BA"
    assert state.cleanup_callbacks == []
    manager.shutdown()


def test_non_identifier_preload_module_uses_its_asset_guid(tmp_path):
    project = _project(tmp_path / "project")
    lifecycle = project / "Assets" / "startup script.py"
    lifecycle.write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class Startup(InxPreload):\n"
        "    def preload(self, context): pass\n",
        encoding="utf-8",
    )
    guid = "1234567890abcdef1234567890abcdef"
    Path(str(lifecycle) + ".meta").write_text(_meta(guid), encoding="utf-8")

    assert preload_module._module_name(str(lifecycle), str(project)) == (
        f"infernux_project_script_{guid}"
    )
    manager = PluginManager.startup(str(project))

    state = next(iter(manager.preloads.states.values()))
    assert state.module_name == f"_infernux_preload_{guid}"


def test_preload_rejects_invalid_script_identity_metadata(tmp_path):
    project = _project(tmp_path / "project")
    lifecycle = project / "Assets" / "startup.py"
    lifecycle.write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class Startup(InxPreload):\n"
        "    def preload(self, context): pass\n",
        encoding="utf-8",
    )
    Path(str(lifecycle) + ".meta").write_text(
        '{"metadata":{"guid":{"type":"string","value":"broken"}}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid script identity metadata"):
        PluginManager.startup(str(project))


def test_reload_all_reuses_unchanged_ast_catalog_and_parses_only_changes(
    tmp_path, monkeypatch
):
    project = _project(tmp_path / "project")
    lifecycle = project / "Assets" / "startup.py"
    lifecycle.write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class Startup(InxPreload):\n"
        "    def preload(self, context): pass\n",
        encoding="utf-8",
    )
    ordinary = project / "Assets" / "gameplay.py"
    ordinary.write_text("VALUE = 1\n", encoding="utf-8")
    manager = PluginManager.startup(str(project))
    original = preload_module._read_declarations
    parsed: list[str] = []

    def record(path, project_root):
        parsed.append(path)
        return original(path, project_root)

    monkeypatch.setattr(preload_module, "_read_declarations", record)
    manager.reload_all()
    assert parsed == []

    ordinary.write_text("VALUE = 200\n", encoding="utf-8")
    manager.reload_all()
    assert parsed == [str(ordinary.resolve())]


def test_ordinary_script_change_does_not_reload_preloads_or_rescan_catalog(
    tmp_path, monkeypatch
):
    project = _project(tmp_path / "project")
    lifecycle = project / "Assets" / "startup.py"
    lifecycle.write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "class Startup(InxPreload):\n"
        "    def preload(self, context): Path(context.project_root, 'loaded.txt').write_text('yes')\n"
        "    def unload(self): Path(__file__).with_name('unloaded.txt').write_text('yes')\n",
        encoding="utf-8",
    )
    ordinary = project / "Assets" / "gameplay.py"
    ordinary.write_text("VALUE = 1\n", encoding="utf-8")
    manager = PluginManager.startup(str(project))
    state = next(iter(manager.preloads.states.values()))
    original_instance = state.instance
    monkeypatch.setattr(
        manager.preloads,
        "_source_paths",
        lambda: pytest.fail("incremental reload performed a full source walk"),
    )

    ordinary.write_text("VALUE = 2\n", encoding="utf-8")
    manager._on_script_catalog_changed(str(ordinary), "modified")

    assert next(iter(manager.preloads.states.values())).instance is original_instance
    assert not (project / "Assets" / "unloaded.txt").exists()


@pytest.mark.parametrize('partial,role,background', [(False, 'editor', False), (True, 'runtime', False), (True, 'editor', True)])
def test_install_script_events_are_owned_but_later_author_edits_refresh(
    tmp_path, partial, role, background
):
    from infernux.core.assets import AssetManager
    from concurrent.futures import ThreadPoolExecutor

    source = _source(tmp_path / "source", "vendor/echo")
    (source / role).mkdir()
    (source / role / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "bare.py").write_text("BARE = 1\n", encoding="utf-8")
    (source / "message.txt").write_text("payload", encoding="utf-8")
    package = tmp_path / "echo.inxpkg"
    InxPackage.export_source(str(source), str(package))
    project = _project(tmp_path / "project")
    manager = PluginManager.startup(str(project))
    if partial:
        manager.install_package(str(package), selected=("message.txt",))
    if background:
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(manager.install_package, str(package)).result()
        manager.finalize_background_install("vendor/echo")
    else:
        manager.install_package(str(package))
    script = project / "Packages/vendor/echo" / role / "helper.py"
    for event in ("created", "modified", "deleted"):
        assert AssetManager.is_watcher_echo_suppressed(event, str(script))
        assert AssetManager.is_watcher_echo_suppressed(event, str(script))
        assert not AssetManager.is_watcher_echo_suppressed(
            event, str(project / "Assets/Plugins/bare.py")
        )
        assert not AssetManager.is_watcher_echo_suppressed(
            event, str(project / "Assets/Plugins/message.txt")
        )
    script.write_text("VALUE = 2\n", encoding="utf-8")
    assert not AssetManager.is_watcher_echo_suppressed("modified", str(script))
    assert not AssetManager.is_watcher_echo_suppressed("created", str(script))
    assert not AssetManager.is_watcher_echo_suppressed("deleted", str(script))
    # Reinstallation acknowledges stale deletes, not a subsequent real delete.
    manager.uninstall("vendor/echo")
    manager.install_package(str(package))
    assert AssetManager.is_watcher_echo_suppressed("deleted", str(script))
    script.unlink()
    assert not AssetManager.is_watcher_echo_suppressed("deleted", str(script))


def test_preload_change_reloads_only_its_dependency_slice(tmp_path, monkeypatch):
    project = _project(tmp_path / "project")

    def write_preload(path: Path, label: str, revision: int) -> None:
        path.write_text(
            "from pathlib import Path\n"
            "from infernux.lifecycle import InxPreload\n"
            f"class {label}(InxPreload):\n"
            f"    revision = {revision}\n"
            "    def preload(self, context): pass\n"
            f"    def unload(self): Path(__file__).with_name('{label}.unloaded').write_text('yes')\n",
            encoding="utf-8",
        )

    first = project / "Assets" / "first.py"
    second = project / "Assets" / "second.py"
    write_preload(first, "First", 1)
    write_preload(second, "Second", 1)
    manager = PluginManager.startup(str(project))
    before = {
        state.type_name: state.instance for state in manager.preloads.states.values()
    }
    monkeypatch.setattr(
        manager.preloads,
        "_source_paths",
        lambda: pytest.fail("incremental reload performed a full source walk"),
    )

    write_preload(first, "First", 2)
    manager._on_script_catalog_changed(str(first), "modified")
    after = {
        state.type_name: state.instance for state in manager.preloads.states.values()
    }

    assert after["First"] is not before["First"]
    assert after["Second"] is before["Second"]
    assert (project / "Assets" / "First.unloaded").is_file()
    assert not (project / "Assets" / "Second.unloaded").exists()


def test_preload_cross_module_inheritance_move_disable_and_restart_diagnostic(tmp_path):
    source = _source(tmp_path / "source", "vendor/lifecycle")
    runtime = source / "runtime"
    runtime.mkdir()
    (runtime / "foundation.py").write_text(
        "from abc import abstractmethod\n"
        "from infernux.lifecycle import InxPreload\n"
        "class Foundation(InxPreload):\n"
        "    @abstractmethod\n"
        "    def configure(self): ...\n"
        "    def preload(self, context): ...\n",
        encoding="utf-8",
    )
    (runtime / "startup.py").write_text(
        "from pathlib import Path\n"
            "from .foundation import Foundation\n"
        "class Startup(Foundation):\n"
        "    def configure(self): return None\n"
        "    def preload(self, context):\n"
        "        Path(context.project_root, 'package-loaded.txt').write_text(context.script_guid)\n"
        "        context.require_restart('native test state')\n"
        "    def unload(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    package = _export(source, tmp_path / "lifecycle.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    state = manager.install_package(str(package), install_dependencies=False)
    assert state.loaded is True
    assert state.restart_required is True
    assert state.lifecycle[0]["restart_reason"] == "native test state"
    original_identity = state.lifecycle[0]["identity"]

    old = project / "Packages/vendor/lifecycle/runtime/startup.py"
    moved = project / "Packages/vendor/lifecycle/runtime/boot.py"
    old.replace(moved)
    Path(str(old) + ".meta").replace(Path(str(moved) + ".meta"))
    state = manager.reload("vendor/lifecycle")
    assert state.lifecycle[0]["identity"] == original_identity

    disabled = manager.set_enabled("vendor/lifecycle", False)
    assert disabled.enabled is False
    assert disabled.loaded is False
    assert disabled.lifecycle == ()
    manager.set_enabled("vendor/lifecycle", True)
    assert (project / "package-loaded.txt").is_file()


def test_package_reload_preflights_every_source_before_unloading_live_state(tmp_path):
    source = _source(tmp_path / "source", "vendor/transactional-reload")
    runtime = source / "runtime"
    runtime.mkdir()
    (runtime / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (runtime / "startup.py").write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "class Startup(InxPreload):\n"
        "    def preload(self, context):\n"
        "        self.root = Path(context.project_root)\n"
        "    def unload(self):\n"
        "        (self.root / 'unexpected-unload.txt').write_text('unloaded')\n",
        encoding="utf-8",
    )
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(
        str(_export(source, tmp_path / "transactional-reload.inxpkg")),
        install_dependencies=False,
    )
    before = tuple(manager.preloads.states.values())
    installed_helper = (
        project
        / "Packages/vendor/transactional-reload/runtime/helper.py"
    )
    installed_helper.write_text("def broken(:\n", encoding="utf-8")

    result = manager.preloads.reload_package("vendor/transactional-reload")

    assert result == before
    assert tuple(manager.preloads.states.values()) == before
    assert not (project / "unexpected-unload.txt").exists()
    assert "SyntaxError" in manager.preloads.failures[path_key(installed_helper)]
    manager.shutdown()


@pytest.mark.parametrize('refresh,native_index', [(refresh, True) for refresh in ('reload_all', 'catch_up', 'reload_path', 'reload_package')]
    + [('reload_all', False)])
def test_new_package_preload_obeys_disable_and_enable_without_taking_ownership(
    tmp_path, monkeypatch, request, refresh, native_index
):
    source = _source(tmp_path / "source", "vendor/mutable")
    (source / "runtime").mkdir()
    (source / "runtime" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    package = _export(source, tmp_path / "mutable.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    request.addfinalizer(manager.preloads.unload_all)
    manager.install_package(str(package), install_dependencies=False)
    manager.set_enabled("vendor/mutable", False)
    before = manager.registry.installed_record("vendor/mutable")
    new_script = project / "Packages/vendor/mutable/runtime/added.py"
    new_script.write_text(
        "from pathlib import Path\n"
        "from infernux.lifecycle import InxPreload\n"
        "class Added(InxPreload):\n"
        "    def preload(self, context):\n"
        "        Path(context.project_root, 'added-ran.txt').write_text('yes')\n",
        encoding="utf-8",
    )
    new_script.with_suffix(".py.meta").write_text(_meta("a" * 32), encoding="utf-8")
    if native_index:
        def native_paths(project_root, *, engine=None):
            paths, _native = project_guid_paths(project_root)
            return paths, True
        monkeypatch.setattr(preload_module, "project_guid_paths", native_paths)

    if refresh == "reload_path":
        manager.preloads.reload_path(str(new_script))
    elif refresh == "reload_package":
        manager.preloads.reload_package("vendor/mutable")
    else:
        getattr(manager.preloads, refresh)()
    assert not (project / "added-ran.txt").exists()
    assert not manager.preloads.states
    assert not manager.preloads.failures
    assert manager.registry.installed_record("vendor/mutable") == before

    enabled = manager.set_enabled("vendor/mutable", True)
    assert enabled.enabled and enabled.loaded
    assert (project / "added-ran.txt").read_text(encoding="utf-8") == "yes"
    assert len(enabled.lifecycle) == 1
    assert enabled.lifecycle[0]["package_reference"] == "vendor/mutable"
    assert manager.registry.installed_record("vendor/mutable")["files"] == before["files"]
    manager.set_enabled("vendor/mutable", False)
    assert not manager.preloads.states


@pytest.mark.parametrize('role,refresh', [('runtime', refresh) for refresh in ('reload_all', 'reload_path', 'reload_package')]
    + [('editor', 'reload_all')])
def test_player_preload_refresh_respects_current_role_and_package_boundary(
    tmp_path, request, role, refresh
):
    # A reference containing "editor" is not an Editor role directory.
    reference = "vendor/editor/tool"
    source = _source(tmp_path / "source", reference)
    (source / "runtime").mkdir()
    (source / "runtime" / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    package = _export(source, tmp_path / "role.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project), runtime=True)
    request.addfinalizer(manager.preloads.unload_all)
    manager.install_package(str(package), install_dependencies=False)
    script = project / "Packages" / reference / role / "added.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "from infernux.lifecycle import InxPreload\n"
        "class Added(InxPreload):\n"
        "    def preload(self, context): pass\n",
        encoding="utf-8",
    )
    script.with_suffix(".py.meta").write_text(_meta("b" * 32), encoding="utf-8")
    if refresh == "reload_path":
        manager.preloads.reload_path(str(script))
    elif refresh == "reload_package":
        manager.preloads.reload_package(reference)
    else:
        manager.preloads.reload_all()
    assert not manager.preloads.failures
    assert len(manager.preloads.states) == (1 if role == "runtime" else 0)


def test_package_dependency_preload_and_reverse_unload_order(tmp_path):
    def lifecycle_source(reference: str, label: str) -> Path:
        source = _source(tmp_path / label, reference)
        runtime = source / "runtime"
        runtime.mkdir()
        (runtime / "startup.py").write_text(
            "from pathlib import Path\n"
            "from infernux.lifecycle import InxPreload\n"
            f"LABEL = {label!r}\n"
            "class Startup(InxPreload):\n"
            "    def preload(self, context):\n"
            "        path=Path(context.project_root, 'order.log')\n"
            "        with path.open('a', encoding='utf-8') as stream: stream.write('preload:' + LABEL + '\\n')\n"
            "    def unload(self):\n"
            "        path=Path(__file__).parents[4] / 'order.log'\n"
            "        with path.open('a', encoding='utf-8') as stream: stream.write('unload:' + LABEL + '\\n')\n",
            encoding="utf-8",
        )
        return source

    dependency = lifecycle_source("vendor/dependency", "dependency")
    parent = lifecycle_source("vendor/parent", "parent")
    (parent / "requirements.txt").write_text("vendor/dependency\n", encoding="utf-8")
    dependency_package = _export(dependency, tmp_path / "dependency.inxpkg")
    parent_package = _export(parent, tmp_path / "parent.inxpkg")
    project = _project(tmp_path / "project")
    registry = PluginRegistry(str(project))
    registry.add_package(
        "vendor/dependency",
        source={"type": "local", "location": str(dependency_package)},
    )
    manager = PluginManager(str(project))
    manager.install_package(str(parent_package))
    with pytest.raises(RuntimeError, match="required by enabled plugins"):
        manager.set_enabled("vendor/dependency", False)
    (project / "order.log").write_text("", encoding="utf-8")
    manager.reload_all()
    assert (project / "order.log").read_text(encoding="utf-8").splitlines() == [
        "unload:parent",
        "unload:dependency",
        "preload:dependency",
        "preload:parent",
    ]


@pytest.mark.parametrize("field,value", [("dependencies", []), ("requirements", "requirements.txt")])
def test_source_manifest_rejects_unimplemented_dependency_fields(tmp_path, field, value):
    source = _source(tmp_path / "source", "vendor/current-schema")
    manifest_path = source / "inx_package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (source / "payload.bin").write_bytes(b"payload")

    with pytest.raises(ValueError, match=f"Unsupported inx_package.json fields: {field}"):
        _export(source, tmp_path / "invalid.inxpkg")


def test_runtime_plugin_panel_is_registered_and_removed_with_package(tmp_path):
    from infernux.engine.interaction import PanelInteractionRegistry
    from infernux.engine.ui.panel_registry import PanelRegistry

    class WindowManagerStub:
        def __init__(self, panel_interactions):
            self.panel_interactions = panel_interactions
            self.registered = []
            self.removed = []
            self.restored = []

        def register_window_type(self, **kwargs):
            self.registered.append(kwargs["type_id"])

        def unregister_window_type(self, type_id):
            self.removed.append(type_id)
            return True

        def capture_open_views_for_types(self, type_ids):
            return tuple((type_id, type_id) for type_id in type_ids)

        def restore_reloaded_views(self, views):
            self.restored.extend(views)

    source = _source(tmp_path / "source", "vendor/live-panel")
    runtime = source / "runtime"
    runtime.mkdir()
    (runtime / "startup.py").write_text(
        "from infernux.engine.interaction import PanelInteractionDescriptor\n"
        "from infernux.engine.ui.panel_registry import editor_panel\n"
        "from infernux.lifecycle import InxPreload\n"
        "@editor_panel('Live Tool', type_id='vendor.live_tool', "
        "interaction=PanelInteractionDescriptor())\n"
        "class LiveTool: pass\n"
        "class Startup(InxPreload):\n"
        "    def preload(self, context): pass\n",
        encoding="utf-8",
    )
    package = _export(source, tmp_path / "live-panel.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    interactions = PanelInteractionRegistry()
    windows = WindowManagerStub(interactions)
    PanelRegistry.bind_live(windows)
    try:
        manager.install_package(str(package), install_dependencies=False)
        assert windows.registered == ["vendor.live_tool"]
        assert interactions.descriptor("vendor.live_tool") is not None

        state = next(iter(manager.preloads.states.values()))
        lifecycle_path = Path(state.source_path)
        lifecycle_path.write_text(
            lifecycle_path.read_text(encoding="utf-8") + "\n# live reload\n",
            encoding="utf-8",
        )
        manager.preloads.reload_path(str(lifecycle_path))
        assert windows.registered == ["vendor.live_tool", "vendor.live_tool"]
        assert windows.removed == ["vendor.live_tool"]
        assert windows.restored == [("vendor.live_tool", "vendor.live_tool")]

        manager.uninstall("vendor/live-panel")
        assert windows.removed == ["vendor.live_tool", "vendor.live_tool"]
        assert interactions.descriptor("vendor.live_tool") is None
    finally:
        owner = next(
            (
                item.owner
                for item in PanelRegistry.get_registrations()
                if item.type_id == "vendor.live_tool"
            ),
            "",
        )
        if owner:
            PanelRegistry.remove_owner(owner)
        PanelRegistry.unbind_live()


@pytest.mark.parametrize("teardown", ["uninstall", "disable", "shutdown"])
def test_plugin_commands_and_shortcuts_follow_preload_lifetime(tmp_path, monkeypatch, teardown):
    from infernux.engine.interaction.commands import EditorCommandRegistry
    from infernux.engine.interaction.shortcuts import ShortcutRouter

    monkeypatch.setattr(EditorCommandRegistry, "_instance", None)
    monkeypatch.setattr(ShortcutRouter, "_instance", None)
    commands = EditorCommandRegistry.instance()
    shortcuts = ShortcutRouter.instance()
    source = _source(tmp_path / "source", "vendor/authoring")
    editor = source / "editor"
    editor.mkdir()
    (editor / "startup.py").write_text(
        "from infernux.lifecycle import InxPreload\n"
        "from infernux.engine.interaction.commands import EditorCommand, EditorCommandRegistry\n"
        "from infernux.engine.interaction.shortcuts import KeyChord, ShortcutBinding, ShortcutRouter\n"
        "EditorCommandRegistry.instance().register(EditorCommand('vendor.create', lambda ctx: 'created', display_name='创建关卡'))\n"
        "class Startup(InxPreload):\n"
        "    def preload(self, context):\n"
        "        ShortcutRouter.instance().register(ShortcutBinding('vendor.create', KeyChord.parse('Ctrl+F9'), binding_id='vendor.create.key'))\n",
        encoding="utf-8",
    )
    package = _export(source, tmp_path / "authoring.inxpkg")
    manager = PluginManager(str(_project(tmp_path / "project")))
    try:
        manager.install_package(str(package), install_dependencies=False)
        assert commands.get("vendor.create").display_name == "创建关卡"
        assert len(shortcuts.bindings) == 1
        for _ in range(2):
            manager.reload("vendor/authoring")
            assert commands.get("vendor.create") is not None
            assert len(shortcuts.bindings) == 1
            assert not manager.preloads.failures
        if teardown == "uninstall":
            manager.uninstall("vendor/authoring")
        elif teardown == "disable":
            manager.set_enabled("vendor/authoring", False)
        else:
            manager.shutdown()
        assert commands.get("vendor.create") is None
        assert shortcuts.bindings == ()
        if teardown == "disable":
            manager.set_enabled("vendor/authoring", True)
            assert commands.get("vendor.create") is not None
            assert len(shortcuts.bindings) == 1
    finally:
        manager.shutdown()


@pytest.mark.parametrize("teardown", ["uninstall", "disable", "shutdown"])
def test_plugin_handles_cancel_capture_and_retire_with_preload_lifetime(
    tmp_path, monkeypatch, teardown
):
    from infernux.engine.interaction.handles import EditorHandleRegistry

    monkeypatch.setattr(EditorHandleRegistry, "_instance", None)
    handles = EditorHandleRegistry.instance()
    source = _source(tmp_path / "source", "vendor/handles")
    editor = source / "editor"
    editor.mkdir()
    (editor / "startup.py").write_text(
        "from pathlib import Path\n"
        "from infernux.editor import register_handle_provider\n"
        "from infernux.lifecycle import InxPreload\n"
        "ROOT = ''\n"
        "def record(value):\n"
        "    with Path(ROOT, 'handles.log').open('a', encoding='utf-8') as stream: stream.write(str(value) + '\\n')\n"
        "def draw(handles):\n"
        "    handles.position('pivot', (0, 0, 0), lambda value: record(('change', tuple(value))), on_cancel=lambda value: record(('cancel', tuple(value))))\n"
        "class Startup(InxPreload):\n"
        "    def preload(self, context):\n"
        "        global ROOT\n"
        "        ROOT = context.project_root\n"
        "        register_handle_provider('vendor.handles', draw, on_retire=lambda: record(('retire',)))\n",
        encoding="utf-8",
    )
    package = _export(source, tmp_path / "handles.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    try:
        manager.install_package(str(package), install_dependencies=False)
        handles.collect(object())
        assert handles.begin_capture(
            "vendor.handles:pivot", (0, 0, 5), (0, 0, -1)
        )
        handles.update_capture((1, 0, 5), (0, 0, -1))

        if teardown == "uninstall":
            manager.uninstall("vendor/handles")
        elif teardown == "disable":
            manager.set_enabled("vendor/handles", False)
        else:
            manager.shutdown()

        assert handles.active_capture_id == ""
        assert handles.frame_handles == ()
        assert handles.providers == ()
        assert (project / "handles.log").read_text(encoding="utf-8").splitlines()[-3:] == [
            "('change', (0.0, 0.0, 0.0))",
            "('cancel', (0.0, 0.0, 0.0))",
            "('retire',)",
        ]
    finally:
        manager.shutdown()


def test_requirements_choose_official_inxpackage_before_pip(tmp_path, monkeypatch):
    dependency = _source(tmp_path / "dependency", "vendor/dependency", version="2.1.0")
    (dependency / "runtime").mkdir()
    (dependency / "runtime" / "api.py").write_text("VALUE = 1\n", encoding="utf-8")
    dep_package = _export(dependency, tmp_path / "dependency.inxpkg")
    parent = _source(tmp_path / "parent", "vendor/parent")
    (parent / "requirements.txt").write_text(
        "vendor/dependency>=2\nthird-party-wheel==1.0\n", encoding="utf-8"
    )
    (parent / "Data.bin").write_bytes(b"parent")
    parent_package = _export(parent, tmp_path / "parent.inxpkg")
    project = _project(tmp_path / "project")
    registry = PluginRegistry(str(project))
    registry.add_package(
        "vendor/dependency",
        version="2.1.0",
        source={"type": "local", "location": str(dep_package)},
    )
    manager = PluginManager(str(project))
    calls = []
    monkeypatch.setattr(manager, "_project_python_executable", lambda: "project-python")
    environment = {}

    def run(command, cwd=None):
        calls.append((command, cwd))
        if command[2:4] == ["pip", "install"]:
            environment["third-party-wheel"] = "1.0"
        return SimpleNamespace(stdout=json.dumps([
            {"name": name, "version": version} for name, version in environment.items()
        ]) if command[2:5] == ["pip", "list", "--format=json"] else "")

    monkeypatch.setattr(manager, "_run_process", run)
    manager.install_package(str(parent_package))
    assert {item["reference"] for item in manager.registry.installed()} == {
        "vendor/dependency",
        "vendor/parent",
    }
    install_calls = [
        command for command, _cwd in calls
        if command[2:4] == ["pip", "install"]
    ]
    assert len(install_calls) == 1
    assert install_calls[0][:4] == ["project-python", "-m", "pip", "install"]


def test_only_plugin_pages_are_displayed_and_resolve_relative_images(tmp_path):
    source = _source(tmp_path / "source", "vendor/documented")
    (source / "README.md").write_text(
        "# Plugin\n\nIntro paragraph.\n\n## Gallery\n\n![Preview](Images/preview.png)\n",
        encoding="utf-8",
    )
    (source / "LICENSE").write_text("License text", encoding="utf-8")
    (source / "plugin_pages").mkdir()
    (source / "plugin_pages" / "media").mkdir()
    (source / "plugin_pages" / "media" / "preview.png").write_bytes(b"image")
    (source / "plugin_pages" / "guide.md").write_text(
        "# Guide\n\nSteps\n\n## Gallery\n\n![Preview](media/preview.png)\n",
        encoding="utf-8",
    )
    package = _export(source, tmp_path / "documented.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), install_dependencies=False)
    record = manager.registry.installed_record("vendor/documented")
    pages = manager.content_pages(record)
    assert [page["id"] for page in pages] == ["guide"]
    blocks = parse_markdown_blocks(pages[0]["content"])
    assert [block["level"] for block in blocks if block["kind"] == "heading"] == [1, 2]
    image = next(block for block in split_markdown_images(pages[0]["content"]) if block["kind"] == "image")
    assert Path(manager.content_asset_path(record, pages[0], image["source"])).is_file()
    assert (project / "Assets/Plugins/README.md").is_file()
    assert (project / "Assets/Plugins/LICENSE").is_file()


def test_downloaded_plugin_pages_are_available_before_import(tmp_path, monkeypatch):
    source = _source(tmp_path / "source", "vendor/preview")
    pages_root = source / "plugin_pages"
    pages_root.mkdir()
    (source / "images").mkdir()
    (source / "images" / "preview.png").write_bytes(b"preview-image")
    (source / "runtime").mkdir()
    (source / "runtime" / "payload.bin").write_bytes(b"do-not-extract")
    (pages_root / "guide.md").write_text(
        "# Guide\n![Preview](../images/preview.png)\n", encoding="utf-8"
    )
    (pages_root / "guide.zh-CN.md").write_text("# 指南\n", encoding="utf-8")
    package = _export(source, tmp_path / "preview.inxpkg")
    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    monkeypatch.setattr(manager, "cached_reference_path", lambda reference: str(package))
    record = manager.registry.add_package(
        "vendor/preview", source={"type": "local", "location": str(package)},
        pages=InxPackage.inspect(str(package)).metadata["pages"],
    )
    try:
        english = manager.content_pages(record, locale="en")
        chinese = manager.content_pages(record, locale="zh")
        assert english[0]["content"].startswith("# Guide")
        assert chinese[0]["content"] == "# 指南\n"
        image = Path(manager.content_asset_path(record, english[0], "../images/preview.png"))
        assert image.read_bytes() == b"preview-image"
        root = image.parent.parent
        assert not (root / "runtime").exists()
        assert manager.registry.installed() == ()
        assert not (project / "Packages/vendor/preview").exists()
        assert not manager.content_asset_path(record, english[0], "../../../outside.png")
        assert not manager.content_asset_path(record, english[0], "https://example.com/image.png")
        assert manager.content_pages(record, locale="en") == english
        assert Path(manager.content_asset_path(record, chinese[0], "../images/preview.png")) == image

        # Import switches to authored project docs; uninstall restores archive docs.
        manager.install_package(str(package), install_dependencies=False)
        authored = project / "Packages/vendor/preview/plugin_pages/guide.md"
        authored.write_text("# Local edits\n", encoding="utf-8")
        assert manager.content_pages(record, locale="en")[0]["content"] == "# Local edits\n"
        manager.uninstall("vendor/preview")
        assert manager.content_pages(record, locale="en") == english
    finally:
        manager.shutdown()
    assert not root.exists()


def test_plugin_content_uses_one_strict_zh_cn_suffix_layout(tmp_path):
    source = _source(tmp_path / "source", "vendor/localized")
    (source / "README.md").write_text(
        "# Localized\n\nEnglish summary.\n", encoding="utf-8"
    )
    (source / "README.zh-CN.md").write_text(
        "# 本地化插件\n\n中文摘要。\n", encoding="utf-8"
    )
    (source / "README.zh.md").write_text(
        "# Unsupported\n\nMust not become a translation.\n", encoding="utf-8"
    )
    (source / "LICENSE").write_text("English license", encoding="utf-8")
    (source / "LICENSE.zh-CN.md").write_text("中文许可证", encoding="utf-8")
    pages_root = source / "plugin_pages"
    pages_root.mkdir()
    (pages_root / "Guide.md").write_text(
        "# Guide\n\nEnglish guide.", encoding="utf-8"
    )
    (pages_root / "Guide.zh-CN.md").write_text(
        "# 指南\n\n中文指南。", encoding="utf-8"
    )
    manifest_path = source / "inx_package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["intro"] = "English summary."
    manifest["intros"] = {"zh-CN": "中文摘要。"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    package = _export(source, tmp_path / "localized.inxpkg")
    preview = InxPackage.inspect(str(package))
    page_keys = [
        (page["id"], page.get("locale", ""))
        for page in preview.metadata["pages"]
    ]
    assert page_keys == [
        ("guide", ""),
        ("guide", "zh-CN"),
    ]
    assert all(page["path"] != "README.zh.md" for page in preview.metadata["pages"])
    assert preview.metadata["intros"] == {"zh-CN": "中文摘要。"}
    assert localized_intro(preview.metadata, "zh") == "中文摘要。"
    assert localized_intro(preview.metadata, "en") == "English summary."

    project = _project(tmp_path / "project")
    manager = PluginManager(str(project))
    manager.install_package(str(package), install_dependencies=False)
    record = manager.registry.installed_record("vendor/localized")
    chinese = manager.content_pages(record, locale="zh")
    english = manager.content_pages(record, locale="en")
    assert [(page["id"], page.get("locale", "")) for page in chinese] == [
        ("guide", "zh-CN"),
    ]
    assert [page["content"] for page in chinese] == [
        "# 指南\n\n中文指南。",
    ]
    assert [page.get("locale", "") for page in english] == [""]


def test_player_policy_is_structural_and_custom_rules_are_rejected():
    assert player_file_exported({}, "runtime/game.py") is True
    assert player_file_exported({}, "editor/tool.py") is False
    assert player_file_exported({}, "README.md") is True
    assert player_file_exported({}, "Scenes/Demo.scene") is True
