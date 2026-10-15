from __future__ import annotations

from test_game_builder import (
    BuildCancelled,
    GameBuilder,
    NuitkaBuilder,
    ParticleGraphAsset,
    Path,
    RuntimeArtifactError,
    SimpleNamespace,
    _FakeNativeInxPack,
    _asset_index_entry,
    _install_runtime_identity_bindings,
    _make_builder,
    _make_license_distribution,
    _make_project,
    _particle_source_hash,
    _player_executable_name,
    _prepare_runtime_catalog_inputs,
    _reference_particle_graph,
    _scene_with_component,
    _stage_document_inputs,
    _write_asset_index,
    _write_particle_artifact,
    _write_player_executable,
    _write_player_native_contract,
    _write_scene_material_audio_reachability_fixture,
    _write_texture_artifact,
    _write_texture_asset_index,
    game_builder_module,
    hashlib,
    json,
    load_asset_index,
    nuitka_builder_module,
    os,
    pytest,
    read_entry,
    read_manifest,
    sys,
    threading,
    validate_artifact,
    write_pack,
    _native_package_backend as _native_package_backend,
)

def test_runtime_pack_rejects_unsafe_archive_paths(tmp_path, monkeypatch):
    cache_root = tmp_path / "runtime-packs"
    builder = object.__new__(NuitkaBuilder)
    builder._runtime_pack_dir = str(cache_root)
    builder._staging_dir = str(tmp_path / "staging")
    builder._engine_fingerprint_cache = "engine-content"
    builder.console_mode = "disable"
    builder.lto = True
    os.makedirs(builder._staging_dir)
    dist = tmp_path / "original.dist"
    dist.mkdir()
    (dist / "InfernuxPlayer.exe").write_bytes(b"runtime")
    fingerprint = "a" * 64
    builder._store_runtime_pack(fingerprint, str(dist))

    pack_root = cache_root / fingerprint
    archive = pack_root / "Runtime.inxrt"
    archive_key = _FakeNativeInxPack._key(archive)
    manifest = read_manifest(archive)
    original_path = str(manifest["files"][0]["path"])
    payload = _FakeNativeInxPack.entries.pop((archive_key, original_path))
    manifest["files"][0]["path"] = "../escape.txt"
    _FakeNativeInxPack.entries[(archive_key, "../escape.txt")] = payload

    assert builder._restore_runtime_pack_root(str(pack_root)) is None
    assert not (tmp_path / "escape.txt").exists()


def test_raw_runtime_package_sources_are_replaced_with_adjacent_bytecode(tmp_path):
    package_root = tmp_path / "numba"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    nested = package_root / "nested.py"
    nested.write_text("def value():\n    return 2\n", encoding="utf-8")

    NuitkaBuilder._compile_raw_python_sources(package_root)

    assert not (package_root / "__init__.py").exists()
    assert not nested.exists()
    assert (package_root / "__init__.pyc").is_file()
    assert (package_root / "nested.pyc").is_file()


def test_raw_runtime_package_injection_requires_one_complete_environment(
    tmp_path, monkeypatch
):
    first = tmp_path / "first-site-packages"
    second = tmp_path / "second-site-packages"
    (first / "numba").mkdir(parents=True)
    (second / "llvmlite").mkdir(parents=True)
    monkeypatch.setattr(
        nuitka_builder_module,
        "_run_python",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps([str(first), str(second)])
        ),
    )
    builder = object.__new__(NuitkaBuilder)
    builder._builder_python = sys.executable
    builder.raw_copy_packages = ["numba", "llvmlite"]

    with pytest.raises(RuntimeError, match="does not contain every raw runtime package"):
        builder._inject_jit_packages(str(tmp_path / "dist"))


@pytest.mark.parametrize("modern", [False, True])
def test_raw_dependency_licenses_come_from_selected_builder_distribution(tmp_path, modern):
    site = tmp_path / "site"
    notice, relative = _make_license_distribution(site, modern=modern)
    _make_license_distribution(site, package="unrelated", modern=modern)
    dist = tmp_path / "dist"

    NuitkaBuilder._copy_raw_dependency_licenses(site, dist, ["llvmlite"])

    assert (dist / relative).read_bytes() == notice.read_bytes()
    assert not (dist / "unrelated").exists()
    assert not list(dist.rglob("METADATA"))
    assert not list(dist.rglob("RECORD"))
    # Runtime cleanup must not discard the retained license with dist-info.
    builder = object.__new__(GameBuilder)
    builder._cleanup_dist(str(dist))
    assert (dist / relative).read_bytes() == notice.read_bytes()


def test_raw_package_injection_retains_external_wheel_licenses(tmp_path, monkeypatch):
    site = tmp_path / "site"
    notice, relative = _make_license_distribution(site)
    builder = object.__new__(NuitkaBuilder)
    builder._builder_python = sys.executable
    builder.raw_copy_packages = ["llvmlite"]
    monkeypatch.setattr(nuitka_builder_module, "_run_python", lambda *a, **kw:
                        SimpleNamespace(stdout=json.dumps([str(site)])))
    dist = tmp_path / "dist"

    builder._inject_jit_packages(str(dist))

    assert (dist / "llvmlite/__init__.pyc").is_file()
    assert not (dist / "llvmlite/__init__.py").exists()
    assert (dist / relative).read_bytes() == notice.read_bytes()


def test_missing_recorded_license_fails_publication(tmp_path):
    site = tmp_path / "site"
    notice, _ = _make_license_distribution(site)
    notice.unlink()
    with pytest.raises(FileNotFoundError):
        NuitkaBuilder._copy_raw_dependency_licenses(site, tmp_path / "dist", ["llvmlite"])


def test_recorded_license_cannot_escape_its_distribution(tmp_path):
    site = tmp_path / "site"
    _make_license_distribution(site)
    record = site / "vendor_llvmlite-1.0.dist-info/RECORD"
    record.write_text(
        "llvmlite/__init__.py,,\nvendor_llvmlite-1.0.dist-info/licenses/../../outside,,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invalid dependency license path"):
        NuitkaBuilder._copy_raw_dependency_licenses(site, tmp_path / "dist", ["llvmlite"])


def test_packaged_parallel_runtime_module_round_trip(tmp_path, monkeypatch):
    module_root = tmp_path / "wheel" / "_runtime_modules"
    builder = object.__new__(NuitkaBuilder)
    builder.last_runtime_compatibility_key = "b" * 64
    builder._engine_fingerprint_cache = "engine-content"

    def fake_inject(dist_dir, packages=None):
        for package in packages or []:
            package_dir = Path(dist_dir) / package
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / "runtime.pyc").write_bytes(package.encode("utf-8"))
            (package_dir / "runtime.pyi").write_text(package, encoding="utf-8")
            (package_dir / "runtime.c").write_text(package, encoding="utf-8")
            (package_dir / "runtime.h").write_text(package, encoding="utf-8")
            license_dir = package_dir / "_licenses" / package
            license_dir.mkdir(parents=True)
            (license_dir / "LICENSE").write_text(f"Copyright {package}", encoding="utf-8")

    monkeypatch.setattr(builder, "_inject_jit_packages", fake_inject)
    exported = builder.export_runtime_module(str(module_root))
    monkeypatch.setenv("INFERNUX_PREBUILT_RUNTIME_MODULE_DIR", str(module_root))

    dist = tmp_path / "dist"
    dist.mkdir()
    assert builder.install_runtime_module(str(dist)) is True
    assert (dist / "numba" / "runtime.pyc").read_bytes() == b"numba"
    assert (dist / "llvmlite" / "runtime.pyc").read_bytes() == b"llvmlite"
    assert (dist / "llvmlite/_licenses/llvmlite/LICENSE").read_text() == "Copyright llvmlite"
    manifest = json.loads(
        (Path(exported) / "Player.inxmanifest").read_text(encoding="utf-8")
    )
    assert manifest["archive"] == "Parallel.inxmod"
    assert manifest["compression"] == "store"
    assert manifest["compression_profile"] == "development"
    assert manifest["packages"] == ["llvmlite", "numba"]
    module_manifest = read_manifest(Path(exported) / "Parallel.inxmod")
    assert all(
        not entry["path"].endswith((".pyi", ".c", ".h"))
        for entry in module_manifest["files"]
    )

    # Re-exporting the same compatibility payload is idempotent. This is the
    # normal CMake retry/concurrent-build case on Windows and must not require
    # replacing an already published directory.
    assert builder.export_runtime_module(str(module_root)) == exported

    compressed_dist = tmp_path / "compressed-dist"
    compressed_dist.mkdir()
    assert builder.install_runtime_module(
        str(compressed_dist), archive_only=True
    ) is True
    staged = compressed_dist / "Parallel.inxmod"
    assert staged.read_bytes() == (Path(exported) / "Parallel.inxmod").read_bytes()
    assert not (compressed_dist / "numba").exists()
    assert not (compressed_dist / "llvmlite").exists()

    release_dist = tmp_path / "release-dist"
    release_dist.mkdir()
    assert builder.install_runtime_module(
        str(release_dist),
        archive_only=True,
        profile="release",
    ) is True
    assert read_manifest(release_dist / "Parallel.inxmod")["compression_profile"] == "release"
    assert read_entry(
        release_dist / "Parallel.inxmod", "llvmlite/_licenses/llvmlite/LICENSE",
    ) == b"Copyright llvmlite"


def test_runtime_engine_fingerprint_ignores_generated_meta(tmp_path, monkeypatch):
    import infernux

    package_root = tmp_path / "infernux"
    package_root.mkdir()
    package_init = package_root / "__init__.py"
    package_init.write_text("VALUE = 1\n", encoding="utf-8")
    metadata = package_root / "shader.frag.meta"
    metadata.write_text("first", encoding="utf-8")
    package_lib = package_root / "lib"
    package_lib.mkdir()
    monkeypatch.setattr(infernux, "__file__", str(package_init))
    monkeypatch.setattr(
        NuitkaBuilder,
        "_native_payload_dir",
        staticmethod(lambda: package_lib),
    )

    builder = object.__new__(NuitkaBuilder)
    builder._runtime_pack_dir = str(tmp_path / "runtime-packs")
    builder._engine_fingerprint_cache = ""
    first = builder._player_compile_input_fingerprint()
    metadata.write_text("changed", encoding="utf-8")
    builder._engine_fingerprint_cache = ""

    assert builder._player_compile_input_fingerprint() == first


def test_runtime_engine_fingerprint_ignores_editor_backups(tmp_path, monkeypatch):
    package_root = tmp_path / "infernux"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    backup = package_root / "bindings.pyi.bak"
    backup.write_text("old", encoding="utf-8")
    package_lib = package_root / "lib"
    package_lib.mkdir()
    fake_package = SimpleNamespace(__file__=str(package_root / "__init__.py"))
    monkeypatch.setitem(sys.modules, "infernux", fake_package)
    monkeypatch.setattr(
        NuitkaBuilder,
        "_native_payload_dir",
        staticmethod(lambda: package_lib),
    )

    builder = object.__new__(NuitkaBuilder)
    builder._runtime_pack_dir = str(tmp_path / "runtime-packs")
    builder._engine_fingerprint_cache = ""
    first = builder._player_compile_input_fingerprint()
    backup.write_text("changed", encoding="utf-8")
    builder._engine_fingerprint_cache = ""

    assert builder._player_compile_input_fingerprint() == first


def test_player_compile_fingerprint_ignores_post_build_packaging_code(tmp_path, monkeypatch):
    package_root = tmp_path / "infernux"
    (package_root / "engine").mkdir(parents=True)
    package_init = package_root / "__init__.py"
    package_init.write_text("", encoding="utf-8")
    runtime_source = package_root / "engine" / "player_bootstrap.py"
    runtime_source.write_text("RUNTIME = 1\n", encoding="utf-8")
    audit_source = package_root / "engine" / "player_package_audit.py"
    audit_source.write_text("AUDIT = 1\n", encoding="utf-8")
    builder_source = package_root / "engine" / "game_builder.py"
    builder_source.write_text("BUILDER = 1\n", encoding="utf-8")
    compile_builder_source = package_root / "engine" / "nuitka_builder.py"
    compile_builder_source.write_text("COMPILE_RULE = 1\n", encoding="utf-8")
    fake_package = SimpleNamespace(__file__=str(package_init))
    monkeypatch.setitem(sys.modules, "infernux", fake_package)

    builder = object.__new__(NuitkaBuilder)
    builder._runtime_pack_dir = str(tmp_path / "runtime-packs")
    builder._engine_fingerprint_cache = ""
    native_dir = package_root / "lib"
    native_dir.mkdir()
    builder._native_payload_dir = lambda: native_dir
    first = builder._player_compile_input_fingerprint()
    audit_source.write_text("AUDIT = 2\n", encoding="utf-8")
    builder_source.write_text("BUILDER = 2\n", encoding="utf-8")
    compile_builder_source.write_text("COMPILE_RULE = 2\n", encoding="utf-8")
    builder._engine_fingerprint_cache = ""

    assert builder._player_compile_input_fingerprint() != first

    compile_builder_source.write_text("COMPILE_RULE = 1\n", encoding="utf-8")
    builder._engine_fingerprint_cache = ""
    assert builder._player_compile_input_fingerprint() == first

    runtime_source.write_text("RUNTIME = 2\n", encoding="utf-8")
    builder._engine_fingerprint_cache = ""
    assert builder._player_compile_input_fingerprint() != first


def test_runtime_pack_fingerprint_tracks_generated_boot_and_keeps_environment_inputs(
    tmp_path,
):
    staged_entry = tmp_path / "boot.py"
    staged_entry.write_text("BOOT = 1\n", encoding="utf-8")
    builder = object.__new__(NuitkaBuilder)
    builder._staged_entry = str(staged_entry)
    builder._staging_dir = str(tmp_path / "staging")
    builder.extra_requirements_files = []
    builder._builder_environment_fingerprint = lambda: b"builder-env"
    builder._player_compile_input_fingerprint = lambda: "player-code"

    first = builder._runtime_pack_fingerprint(
        ["python", str(staged_entry), "--jobs=8"]
    )
    original_layout = builder._RUNTIME_PACK_LAYOUT
    builder._RUNTIME_PACK_LAYOUT = "different-layout"
    assert builder._runtime_pack_fingerprint(
        ["python", str(staged_entry), "--jobs=8"]
    ) != first
    builder._RUNTIME_PACK_LAYOUT = original_layout
    staged_entry.write_text("BOOT = 2\n", encoding="utf-8")

    assert builder._runtime_pack_fingerprint(
        ["python", str(staged_entry), "--jobs=8"]
    ) != first


def test_runtime_engine_fingerprint_tracks_loaded_native_payload(tmp_path, monkeypatch):
    import infernux

    package_root = tmp_path / "infernux"
    package_root.mkdir()
    package_init = package_root / "__init__.py"
    package_init.write_text("", encoding="utf-8")
    native_root = tmp_path / "native"
    native_root.mkdir()
    native_module = (
        "_Infernux.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_Infernux.so"
    )
    (native_root / native_module).write_bytes(b"module")
    bootstrap_module = (
        "_InfernuxBootstrap.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_InfernuxBootstrap.so"
    )
    (native_root / bootstrap_module).write_bytes(b"bootstrap")
    _write_player_native_contract(native_root)
    companion = native_root / (
        "InfernuxRendererRuntime.dll"
        if sys.platform == "win32"
        else "libInfernuxRendererRuntime.so"
    )
    companion.write_bytes(b"first")
    monkeypatch.setattr(infernux, "__file__", str(package_init))
    monkeypatch.setenv("INFERNUX_NATIVE_MODULE_DIR", str(native_root))

    builder = object.__new__(NuitkaBuilder)
    builder._runtime_pack_dir = str(tmp_path / "runtime-packs")
    builder._engine_fingerprint_cache = ""
    first = builder._player_compile_input_fingerprint()
    companion.write_bytes(b"second")
    builder._engine_fingerprint_cache = ""

    assert builder._player_compile_input_fingerprint() != first


def test_native_payload_injection_uses_one_override_and_overwrites_stale_files(
    tmp_path, monkeypatch
):
    native_root = tmp_path / "native"
    native_root.mkdir()
    native_module = (
        "_Infernux.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_Infernux.so"
    )
    companion = (
        "InfernuxRendererRuntime.dll"
        if sys.platform == "win32"
        else "libInfernuxRendererRuntime.so"
    )
    (native_root / native_module).write_bytes(b"current-module")
    bootstrap_module = (
        "_InfernuxBootstrap.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_InfernuxBootstrap.so"
    )
    (native_root / bootstrap_module).write_bytes(b"bootstrap-module")
    _write_player_native_contract(native_root)
    if sys.platform == "win32":
        # A stale short-name module can be left by Nuitka discovery. The
        # ABI-tagged build output must win and replace it atomically.
        (native_root / "_Infernux.pyd").write_bytes(b"stale-source-module")
    (native_root / companion).write_bytes(b"current-runtime")
    foundation = (
        native_root / "InfernuxFoundation.dll"
        if sys.platform == "win32"
        else native_root / "libInfernuxFoundation.so"
    )
    foundation.write_bytes(b"foundation")
    python_runtime = native_root / "python313.dll" if sys.platform == "win32" else None
    if python_runtime is not None:
        python_runtime.write_bytes(b"python-runtime")
        (native_root / "zlib.dll").write_bytes(b"runtime-zlib")
    monkeypatch.setenv("INFERNUX_NATIVE_MODULE_DIR", str(native_root))

    dist = tmp_path / "boot.dist"
    package_lib = dist / "infernux" / "lib"
    package_lib.mkdir(parents=True)
    (package_lib / native_module).write_bytes(b"stale-module")
    canonical_module = "_Infernux.pyd" if sys.platform == "win32" else "_Infernux.so"
    (package_lib / canonical_module).write_bytes(b"stale-canonical-module")
    (package_lib / companion).write_bytes(b"stale-runtime")
    if sys.platform == "win32":
        (dist / companion).write_bytes(b"stale-root-runtime")

    builder = object.__new__(NuitkaBuilder)
    builder._inject_native_libs(str(dist))

    assert (package_lib / native_module).read_bytes() == b"current-module"
    assert (package_lib / canonical_module).read_bytes() == b"current-module"
    assert (package_lib / companion).read_bytes() == b"current-runtime"
    if sys.platform == "win32":
        assert not (dist / companion).exists()
        assert (dist / "_InfernuxBootstrap.pyd").read_bytes() == b"bootstrap-module"
        assert (dist / "InfernuxFoundation.dll").read_bytes() == b"foundation"
        assert (dist / "python313.dll").read_bytes() == b"python-runtime"
        assert not (package_lib / "python313.dll").exists()
        assert (package_lib / "zlib.dll").read_bytes() == b"runtime-zlib"
        assert not (dist / "zlib.dll").exists()


def test_player_native_payload_requires_current_contract(tmp_path, monkeypatch):
    native_root = tmp_path / "native"
    native_root.mkdir()
    native_module = (
        "_Infernux.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_Infernux.so"
    )
    bootstrap_module = (
        "_InfernuxBootstrap.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_InfernuxBootstrap.so"
    )
    (native_root / native_module).write_bytes(b"editor-module")
    (native_root / bootstrap_module).write_bytes(b"bootstrap-module")
    monkeypatch.setenv("INFERNUX_NATIVE_MODULE_DIR", str(native_root))

    with pytest.raises(RuntimeError, match="static Release Player runtime"):
        NuitkaBuilder._native_payload_dir()


def test_player_native_payload_requires_complete_current_closure(tmp_path, monkeypatch):
    native_root = tmp_path / "native"
    native_root.mkdir()
    native_module = (
        "_Infernux.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_Infernux.so"
    )
    bootstrap_module = (
        "_InfernuxBootstrap.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_InfernuxBootstrap.so"
    )
    (native_root / native_module).write_bytes(b"player-module")
    (native_root / bootstrap_module).write_bytes(b"bootstrap-module")
    _write_player_native_contract(native_root)
    missing = sorted(nuitka_builder_module.player_native_library_filenames())[0]
    (native_root / missing).unlink()
    monkeypatch.setenv("INFERNUX_NATIVE_MODULE_DIR", str(native_root))

    with pytest.raises(RuntimeError, match="static Release Player runtime"):
        NuitkaBuilder._native_payload_dir()


def test_player_native_payload_selects_static_source_build_sibling(
    tmp_path, monkeypatch
):
    repository = tmp_path / "repository"
    package_root = repository / "python" / "infernux"
    package_root.mkdir(parents=True)
    package_init = package_root / "__init__.py"
    package_init.write_text("", encoding="utf-8")
    build_root = repository / "out" / "build"
    editor_root = build_root / "windows-msvc-dev" / "python-sync"
    player_root = build_root / "windows-msvc-release" / "python-sync"
    editor_root.mkdir(parents=True)
    player_root.mkdir(parents=True)
    native_module = (
        "_Infernux.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_Infernux.so"
    )
    bootstrap_module = (
        "_InfernuxBootstrap.cp313-win_amd64.pyd"
        if sys.platform == "win32"
        else "_InfernuxBootstrap.so"
    )
    for root in (editor_root, player_root):
        (root / native_module).write_bytes(root.name.encode("utf-8"))
        (root / bootstrap_module).write_bytes(b"bootstrap-module")
    _write_player_native_contract(player_root)

    import infernux

    monkeypatch.delenv("INFERNUX_NATIVE_MODULE_DIR", raising=False)
    monkeypatch.setattr(infernux, "__file__", str(package_init))
    monkeypatch.setattr(
        nuitka_builder_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(__file__=str(editor_root / native_module)),
    )

    assert NuitkaBuilder._native_payload_dir() == player_root


def test_runtime_compatibility_key_ignores_branding_and_managed_dependencies(
    tmp_path,
):
    entry = tmp_path / "boot.py"
    entry.write_text("pass\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("numba==1\nfastmcp==2\n", encoding="utf-8")

    def make_builder(*, icon: str, custom: list[str] | None = None):
        builder = object.__new__(NuitkaBuilder)
        builder.entry_script = str(entry)
        builder.extra_requirements_files = [str(requirements)]
        builder.extra_include_packages = list(custom or [])
        builder.extra_include_data = [str(tmp_path / "project-specific-data")]
        builder.raw_copy_packages = ["numpy"]
        builder.runtime_support_packages = ["numba", "llvmlite", "mcp", "fastmcp"]
        builder.icon_path = icon
        builder.console_mode = "disable"
        builder.lto = True
        builder.player_module = True
        builder._player_compile_input_fingerprint = lambda: "core-runtime"
        return builder

    first = make_builder(icon=str(tmp_path / "first.ico"))
    second = make_builder(icon=str(tmp_path / "second.ico"))
    assert first._runtime_pack_compatibility_key() == second._runtime_pack_compatibility_key()

    custom = make_builder(icon="", custom=["project_runtime_dependency"])
    custom_key = custom._runtime_pack_compatibility_key()
    assert custom_key != first._runtime_pack_compatibility_key()

    requirements.write_text(
        "numba==99\nfastmcp==99\nproject-runtime-dependency==3\n",
        encoding="utf-8",
    )
    assert custom._runtime_pack_compatibility_key() != custom_key


def test_player_compile_fingerprint_is_source_install_layout_equivalent(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "source" / "infernux"
    installed_root = tmp_path / "site-packages" / "infernux"
    for package_root in (source_root, installed_root):
        (package_root / "engine").mkdir(parents=True)
        (package_root / "lib").mkdir()
        (package_root / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
        (package_root / "engine" / "player_runtime.py").write_text(
            "RUNTIME = 1\n", encoding="utf-8"
        )
    (installed_root / "engine" / "player_runtime.pyi").write_text(
        "RUNTIME: int\n", encoding="utf-8"
    )
    (installed_root / "engine" / "__pycache__").mkdir()
    (installed_root / "engine" / "__pycache__" / "player_runtime.pyc").write_bytes(
        b"layout-specific-bytecode"
    )
    generated = installed_root / "_runtime_packs" / ("a" * 64)
    generated.mkdir(parents=True)
    (generated / "Runtime.inxrt").write_bytes(b"generated cache")

    builder = object.__new__(NuitkaBuilder)
    builder._engine_fingerprint_cache = ""
    monkeypatch.setattr(builder, "_load_runtime_hash_state", lambda: {})
    monkeypatch.setattr(builder, "_store_runtime_hash_state", lambda _state: None)
    active_root = source_root
    monkeypatch.setattr(builder, "_native_payload_dir", lambda: active_root / "lib")

    fake_package = SimpleNamespace(__file__=str(source_root / "__init__.py"))
    monkeypatch.setitem(sys.modules, "infernux", fake_package)
    source_fingerprint = builder._player_compile_input_fingerprint()

    active_root = installed_root
    fake_package.__file__ = str(installed_root / "__init__.py")
    builder._engine_fingerprint_cache = ""
    installed_fingerprint = builder._player_compile_input_fingerprint()

    assert installed_fingerprint == source_fingerprint


def test_cleanup_temp_removes_boot_directory_synchronously(tmp_path):
    boot_dir = tmp_path / "_build_temp"
    boot_dir.mkdir()
    boot_script = boot_dir / "boot.py"
    boot_script.write_text("print('temporary')", encoding="utf-8")

    GameBuilder._cleanup_temp(str(boot_script))

    assert not boot_dir.exists()


def test_player_build_tree_removal_uses_boot_required_stdlib(tmp_path):
    tree = tmp_path / "cache-tree"
    (tree / "nested").mkdir(parents=True)
    (tree / "nested" / "payload.bin").write_bytes(b"payload")
    game_builder_module._remove_player_path(str(tree))
    assert not tree.exists()


def test_player_output_transaction_keeps_previous_product_until_commit(tmp_path):
    output = tmp_path / "已发布游戏"
    output.mkdir()
    previous = output / "TestGame.exe"
    previous.write_bytes(b"previous")
    builder = _make_builder(tmp_path, output)

    builder._begin_output_transaction()
    staging = Path(builder.output_dir)
    assert previous.read_bytes() == b"previous"
    (staging / "TestGame.exe").write_bytes(b"current")
    (staging / "TestGame_Data").mkdir()

    published = Path(builder._commit_output_transaction(str(staging)))

    assert published == output.resolve()
    assert (published / "TestGame.exe").read_bytes() == b"current"
    assert not Path(str(output) + ".infernux-build.lock").exists()


def test_player_output_transaction_abort_removes_only_private_staging(tmp_path):
    output = tmp_path / "Player"
    output.mkdir()
    (output / "TestGame.exe").write_bytes(b"previous")
    builder = _make_builder(tmp_path, output)

    builder._begin_output_transaction()
    staging = Path(builder.output_dir)
    (staging / "partial.bin").write_bytes(b"partial")
    builder._abort_output_transaction()

    assert (output / "TestGame.exe").read_bytes() == b"previous"
    assert not staging.exists()
    assert builder.output_dir == str(output.resolve())


def test_player_output_transaction_restores_previous_product_on_publish_failure(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "Player"
    output.mkdir()
    (output / "TestGame.exe").write_bytes(b"previous")
    builder = _make_builder(tmp_path, output)
    builder._begin_output_transaction()
    staging = Path(builder.output_dir)
    (staging / "TestGame.exe").write_bytes(b"current")
    native_replace = game_builder_module.os.replace

    def fail_staging_publish(source, destination):
        if Path(source) == staging and Path(destination) == output:
            raise PermissionError("read-only publication target")
        return native_replace(source, destination)

    monkeypatch.setattr(game_builder_module.os, "replace", fail_staging_publish)

    with pytest.raises(PermissionError, match="read-only"):
        builder._commit_output_transaction(str(staging))

    assert (output / "TestGame.exe").read_bytes() == b"previous"
    builder._abort_output_transaction()


def test_player_output_transaction_rejects_live_concurrent_owner(tmp_path, monkeypatch):
    output = tmp_path / "Player"
    lock = Path(str(output) + ".infernux-build.lock")
    lock.write_text(json.dumps({"pid": 123, "staging": ""}), encoding="utf-8")
    builder = _make_builder(tmp_path, output)
    monkeypatch.setattr(
        game_builder_module,
        "_player_builder_process_is_alive",
        lambda pid: pid == 123,
    )

    with pytest.raises(RuntimeError, match="Another Player build owns"):
        builder._begin_output_transaction()

    assert lock.is_file()


def test_player_output_transaction_recovers_stale_interrupted_staging(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "Player"
    stale = tmp_path / ".Player.infernux-staging-44-55"
    stale.mkdir()
    (stale / "partial.bin").write_bytes(b"partial")
    lock = Path(str(output) + ".infernux-build.lock")
    lock.write_text(
        json.dumps({"pid": 44, "staging": str(stale)}),
        encoding="utf-8",
    )
    builder = _make_builder(tmp_path, output)
    monkeypatch.setattr(
        game_builder_module,
        "_player_builder_process_is_alive",
        lambda _pid: False,
    )

    builder._begin_output_transaction()

    assert not stale.exists()
    builder._abort_output_transaction()


def test_player_output_transaction_restores_stale_previous_product(
    tmp_path,
    monkeypatch,
):
    output = tmp_path / "Player"
    stale = tmp_path / ".Player.infernux-staging-44-55"
    backup = tmp_path / ".Player.infernux-previous-44-55"
    stale.mkdir()
    (stale / "partial.bin").write_bytes(b"partial")
    backup.mkdir()
    (backup / "TestGame.exe").write_bytes(b"previous")
    lock = Path(str(output) + ".infernux-build.lock")
    lock.write_text(
        json.dumps(
            {
                "pid": 44,
                "staging": str(stale),
                "backup": str(backup),
            }
        ),
        encoding="utf-8",
    )
    builder = _make_builder(tmp_path, output)
    monkeypatch.setattr(
        game_builder_module,
        "_player_builder_process_is_alive",
        lambda _pid: False,
    )

    builder._begin_output_transaction()

    assert (output / "TestGame.exe").read_bytes() == b"previous"
    assert not stale.exists()
    assert not backup.exists()
    builder._abort_output_transaction()


def test_release_output_copies_player_host_and_keeps_module(tmp_path, monkeypatch):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    host = tmp_path / "InfernuxPlayerHost.exe"
    host.write_bytes(b"host")
    monkeypatch.setattr(builder, "_player_host_path", lambda: str(host))
    builder.debug_mode = False
    dist = tmp_path / "staging" / "generic.dist"
    dist.mkdir(parents=True)
    module_name = "_InfernuxPlayer.pyd" if sys.platform == "win32" else "_InfernuxPlayer.so"
    (dist / module_name).write_bytes(b"player module")

    service = dist / "infernux" / "Application.pyc"
    service.parent.mkdir()
    service.write_bytes(b"case-sensitive archive identity")

    final_dir = Path(builder._organize_output(str(dist)))

    game_name = "TestGame.exe" if sys.platform == "win32" else "TestGame"
    assert (final_dir / game_name).read_bytes() == b"host"
    assert (final_dir / module_name).read_bytes() == b"player module"
    assert "infernux" in [p.name for p in final_dir.iterdir()]
    assert "Application.pyc" in [p.name for p in (final_dir / "infernux").iterdir()]
    assert not dist.parent.exists()


def test_player_host_resolves_from_selected_platform_plugin(tmp_path, monkeypatch):
    monkeypatch.delenv("INFERNUX_PLAYER_HOST_PATH", raising=False)
    package = tmp_path / "infernux"
    engine = package / "engine"
    runtime = package / "resources" / "player_runtime"
    engine.mkdir(parents=True)
    runtime.mkdir(parents=True)
    host = runtime / ("InfernuxPlayerHost.exe" if sys.platform == "win32" else "InfernuxPlayerHost")
    host.write_bytes(b"host")
    monkeypatch.setattr(game_builder_module, "__file__", str(engine / "game_builder.py"))

    builder = _make_builder(tmp_path, tmp_path / "build_output")
    builder.player_runtime_root = str(runtime)
    assert Path(builder._player_host_path()) == host


def test_current_player_layout_uses_one_renamed_executable(tmp_path, monkeypatch):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    host = tmp_path / "InfernuxPlayerHost.exe"
    host.write_bytes(b"host")
    monkeypatch.setattr(builder, "_player_host_path", lambda: str(host))

    dist = tmp_path / "staging" / "generic.dist"
    dist.mkdir(parents=True)
    module_name = "_InfernuxPlayer.pyd" if sys.platform == "win32" else "_InfernuxPlayer.so"
    (dist / module_name).write_bytes(b"player module")
    (dist / "python313.dll").write_bytes(b"python")
    final_dir = Path(builder._organize_output(str(dist)))
    (final_dir / "Data").mkdir()
    (final_dir / "Data" / "BuildManifest.json").write_text("{}", encoding="utf-8")

    builder._organize_player_layout(str(final_dir))
    builder._write_output_marker(str(final_dir))

    data_root = final_dir / "TestGame_Data"
    executable_name = _player_executable_name()
    assert (final_dir / executable_name).read_bytes() == b"host"
    assert [path.name for path in final_dir.iterdir() if path.name == executable_name] == [
        executable_name
    ]
    assert (final_dir / module_name).read_bytes() == b"player module"
    assert (final_dir / "python313.dll").read_bytes() == b"python"
    assert (final_dir / GameBuilder.OUTPUT_MARKER_FILENAME).is_file()
    assert (data_root / "BuildManifest.json").is_file()
    assert not (data_root / "Runtime").exists()


def test_build_branding_assets_are_manifested_and_packed(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    icon = Path(builder.project_path) / "Assets" / "project-icon.png"
    splash = Path(builder.project_path) / "Assets" / "opening.png"
    icon.write_bytes(b"icon")
    splash.write_bytes(b"splash")
    entries = load_asset_index(builder.project_path)
    entries.extend(
        (
            _asset_index_entry(Path(builder.project_path), icon, "icon-guid", "", "Texture"),
            _asset_index_entry(Path(builder.project_path), splash, "splash-guid", "", "Texture"),
        )
    )
    builder.freeze_asset_index_entries(entries)
    builder.icon_guid = "icon-guid"
    builder.splash_items = [
        {
            "type": "image",
            "asset_guid": "splash-guid",
            "duration": 2.0,
            "fade_in": 0.25,
            "fade_out": 0.5,
        }
    ]
    final_dir = tmp_path / "dist"
    settings = final_dir / "Data" / "ProjectSettings"
    settings.mkdir(parents=True)
    (settings / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}), encoding="utf-8"
    )

    builder._process_build_icon(str(final_dir))
    builder._process_splash_items(str(final_dir))
    builder._generate_manifest(str(final_dir))
    _write_player_executable(final_dir, b"player")
    builder._organize_player_layout(str(final_dir))

    manifest = json.loads(
        (final_dir / "TestGame_Data" / "BuildManifest.json").read_text(encoding="utf-8")
    )
    assert manifest["icon_path"] == "Branding/icon.png"
    assert manifest["splash_items"][0]["path"] == "Splash/opening.png"
    assert manifest["splash_items"][0]["layout"] == "contain"
    builder._pack_content_archive(str(final_dir))
    header = read_manifest(final_dir / "TestGame_Data" / "Content.inxpkg")
    names = {entry["path"] for entry in header["files"]}
    assert "Branding/icon.png" in names
    assert "Splash/opening.png" in names


def test_build_branding_reuses_identical_icon_for_splash(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    branding = Path(builder.project_path) / "Assets" / "branding.png"
    branding.write_bytes(b"one-branding-payload")
    entries = load_asset_index(builder.project_path)
    entries.append(
        _asset_index_entry(
            Path(builder.project_path), branding, "branding-guid", "", "Texture"
        )
    )
    builder.freeze_asset_index_entries(entries)
    builder.icon_guid = "branding-guid"
    builder.splash_items = [
        {
            "type": "image",
            "asset_guid": "branding-guid",
            "duration": 2.0,
            "fade_in": 0.25,
            "fade_out": 0.5,
        }
    ]
    final_dir = tmp_path / "dist"
    settings = final_dir / "Data" / "ProjectSettings"
    settings.mkdir(parents=True)
    (settings / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": []}), encoding="utf-8"
    )

    builder._process_build_icon(str(final_dir))
    builder._process_splash_items(str(final_dir))
    builder._generate_manifest(str(final_dir))

    manifest = json.loads(
        (final_dir / "Data" / "BuildManifest.json").read_text(encoding="utf-8")
    )
    assert manifest["icon_path"] == "Branding/icon.png"
    assert manifest["splash_items"][0]["path"] == "Branding/icon.png"
    assert manifest["splash_items"][0]["layout"] == "logo"
    assert not (final_dir / "Data" / "Splash" / "branding.png").exists()


def test_requirements_install_is_skipped_when_content_is_unchanged(tmp_path, monkeypatch):
    state_root = tmp_path / "requirements-state"
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("requests==2.32.0\n", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(nuitka_builder_module.subprocess, "check_call", lambda command, **kwargs: calls.append(command))

    nuitka_builder_module._install_requirements_files(
        sys.executable,
        [str(requirements)],
        str(state_root),
    )
    nuitka_builder_module._install_requirements_files(
        sys.executable,
        [str(requirements)],
        str(state_root),
    )

    assert len(calls) == 1


def test_player_cleanup_preserves_runtime_closure_and_removes_authoring_state(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    retained = {
        "infernux/lib/keep.dll": b"runtime",
        "infernux/lib/_Infernux.pyd": b"native module",
        "_InfernuxBootstrap.pyd": b"bootstrap module",
        "infernux/resources/icons/gizmo_camera.png": b"camera",
        "infernux/resources/icons/gizmo_light.png": b"light",
        "infernux/resources/fonts/engine.otf": b"package-font",
        "Data/Assets/Scripts/player.py.meta": b"project",
        "numpy/__init__.pyc": b"runtime package",
        "numpy/_core/__init__.pyc": b"runtime core",
    }
    removed = {
        "infernux/lib/_Infernux.pyi": b"build-time stubs",
        "_Infernux.pyd": b"native module",
        "infernux/resources/shaders/lit.frag.meta": b"engine",
        "numpy/__pycache__/stale.pyc": b"cache",
        "Data/Library/Resources/fonts/engine.otf": b"duplicate-font",
    }
    for relative, content in (retained | removed).items():
        path = final_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    # One actual cleanup of one prepared product, rather than six independent
    # products. On Windows the mixed-case _Infernux.pyi still reaches both
    # cleanup discovery paths and must be removed only once.
    builder._cleanup_dist(str(final_dir))

    assert {relative: (final_dir / relative).read_bytes() for relative in retained} == retained
    assert all(not (final_dir / relative).exists() for relative in removed)
    assert not (final_dir / "numpy/__pycache__").exists()
    assert not (final_dir / "Data/Library/Resources").exists()

















def test_game_data_includes_render_effect_artifacts(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    source_effect = project / "Assets" / "Rendering" / "Bloom.effect"
    source_effect.parent.mkdir(parents=True)
    source_effect.write_text(
        '{"$schema":"infernux.render_effect","name":"Bloom"}',
        encoding="utf-8",
    )
    scene = project / "Assets" / "Main.scene"
    scene.write_text(json.dumps(_scene_with_component({"effect": {
        "$type": "asset_ref", "guid": "effect-guid", "path_hint": "Assets/Rendering/Bloom.effect",
    }})), encoding="utf-8")
    artifact = (
        project
        / "Library"
        / "Artifacts"
        / "RenderEffect"
        / "bloom.inxeffect"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        json.dumps(
            {
                "$schema": "infernux.render_effect_artifact",
                "source_hash": hashlib.sha256(source_effect.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene, "scene-guid", "", "Scene"),
            _asset_index_entry(
                project,
                source_effect,
                "effect-guid",
                "Library/Artifacts/RenderEffect/bloom.inxeffect",
                "RenderEffect",
            ),
        ],
    )
    final_dir = tmp_path / "dist"

    builder._copy_game_data(str(final_dir))
    shipped = (
        final_dir
        / "Data"
        / "Library"
        / "Artifacts"
        / "RenderEffect"
        / artifact.name
    )

    assert shipped.read_text(encoding="utf-8") == artifact.read_text(encoding="utf-8")

    _write_player_executable(final_dir)
    builder._organize_player_layout(str(final_dir))
    builder._pack_content_archive(str(final_dir))
    header = read_manifest(
        final_dir / "TestGame_Data" / builder._CONTENT_ARCHIVE_FILENAME
    )
    assert "Library/Artifacts/RenderEffect/bloom.inxeffect" in {
        entry["path"] for entry in header["files"]
    }


def test_full_build_cook_rejects_an_empty_runtime_asset_selection(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    _write_asset_index(Path(builder.project_path), [])
    builder._full_build_validated = True
    data_dir = tmp_path / "dist" / "Data"
    data_dir.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="selected no runtime assets"):
        builder._copy_cooked_assets(str(data_dir))


def test_player_cook_rejects_raw_model_source_without_compiled_artifact(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    scene.write_text("{}", encoding="utf-8")
    source = project / "Assets" / "Models" / "Raw.blend"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"authoring source")
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene, "scene-guid", "", "Scene"),
            _asset_index_entry(project, source, "model-guid", "", "Mesh"),
        ],
    )

    with pytest.raises(RuntimeError, match="raw model source without a compiled artifact"):
        builder._copy_cooked_assets(str(tmp_path / "dist" / "Data"))


def test_player_cook_uses_asset_index_snapshot_after_live_index_invalidation(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    builder._asset_index_entries()
    (project / "Library" / "AssetIndex.json").unlink()
    data_dir = tmp_path / "dist" / "Data"

    builder._copy_cooked_assets(str(data_dir))

    assert (data_dir / "Assets" / "Main.scene").is_file()


def test_player_stages_project_shader_as_packed_runtime_glsl(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    shader = project / "Assets" / "Shaders" / "Surface.frag"
    shader.parent.mkdir(parents=True, exist_ok=True)
    shader.write_text('ShaderInfo { Name "Test/Surface" Type Fragment }\n', encoding="utf-8")
    entry = _asset_index_entry(project, shader, "shader-guid", "", "Shader")
    entry["metadata"]["metadata"]["type"] = {"value": "fragment"}
    builder._cooked_asset_entries = {"shader-guid": entry}
    builder._runtime_artifact_bindings = {}
    builder._runtime_artifact_source_paths = set()
    data_dir = tmp_path / "dist" / "Data"

    _stage_document_inputs(builder, data_dir)
    builder._stage_library_runtime_documents(str(data_dir))

    artifact = (
        data_dir
        / "Library"
        / "Artifacts"
        / "Blob"
        / "shader-guid.frag"
    )
    assert artifact.read_bytes() == shader.read_bytes()
    assert builder._runtime_artifact_bindings[
        "Library/Artifacts/Blob/shader-guid.frag"
    ]["source_path"] == "Assets/Shaders/Surface.frag"


def test_player_stages_font_as_guid_owned_runtime_blob(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    font = project / "Assets" / "Fonts" / "ProjectFont.ttf"
    font.parent.mkdir(parents=True, exist_ok=True)
    font.write_bytes(b"project font payload")
    entry = _asset_index_entry(project, font, "font-guid", "", "Font")
    builder._cooked_asset_entries = {"font-guid": entry}
    builder._runtime_artifact_bindings = {}
    builder._runtime_artifact_source_paths = set()
    data_dir = tmp_path / "dist" / "Data"

    _stage_document_inputs(builder, data_dir)
    builder._stage_library_runtime_documents(str(data_dir))

    runtime_path = "Library/Artifacts/Blob/font-guid.ttf"
    artifact = data_dir / Path(runtime_path)
    assert artifact.read_bytes() == b"project font payload"
    assert builder._runtime_artifact_bindings[runtime_path]["source_path"] == (
        "Assets/Fonts/ProjectFont.ttf"
    )
    assert "assets/fonts/projectfont.ttf" in builder._runtime_artifact_source_paths


def test_player_cooks_data_asset_to_binary_infernux_artifact(tmp_path):
    from infernux.core.data_asset import decode_data_asset_artifact

    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    source = project / "Assets" / "Data" / "Settings.inxdata"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps(
            {
                "$type": "data_asset",
                "type_id": "infernux.data_asset",
                "fields": {},
            }
        ),
        encoding="utf-8",
    )
    entry = _asset_index_entry(
        project, source, "data-guid", "", "DataAsset"
    )
    builder._cooked_asset_entries = {"data-guid": entry}
    builder._runtime_artifact_bindings = {}
    builder._runtime_artifact_source_paths = set()
    data_dir = tmp_path / "dist" / "Data"

    _stage_document_inputs(builder, data_dir)
    builder._stage_library_runtime_documents(str(data_dir))

    runtime_path = "Library/Artifacts/Data/data-guid.inxasset"
    artifact = data_dir / Path(runtime_path)
    assert artifact.read_bytes().startswith(b"INXDATA\0")
    cooked = decode_data_asset_artifact(artifact.read_bytes())
    assert cooked == {
        "$type": "data_asset",
        "type_id": "infernux.data_asset",
        "fields": {},
    }
    # Cook normalizes runtime values without rewriting the authored source.
    assert "schema_version" not in json.loads(source.read_text(encoding="utf-8"))
    assert builder._runtime_artifact_bindings[runtime_path]["source_path"] == (
        "Assets/Data/Settings.inxdata"
    )
    assert "assets/data/settings.inxdata" in builder._runtime_artifact_source_paths


@pytest.mark.parametrize("depth", [1, 2, 4])
@pytest.mark.parametrize("source_change", ["none", "base-edit", "source-removal"])
def test_player_cooks_variant_from_current_base_without_authoring_metadata(tmp_path, depth, source_change):
    import copy
    from infernux.engine.prefab_variant import create_variant_definition, variant_document
    from infernux.engine.prefab_manager import _make_prefab_baseline

    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    base_path = project / "Assets/Base.prefab"
    variant_path = project / "Assets/Variant.prefab"
    base = dict(root_object=dict(local_id=1, name="Base", active=True, is_static=False,
                tag="Untagged", layer=0, children=[], components=[],
                transform=dict(type="Transform", enabled=True, execution_order=0,
                               position=[0, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1])),
                next_local_id=3, next_component_id=1)
    child = copy.deepcopy(base["root_object"])
    child.update(local_id=2, name="Child")
    base["root_object"]["children"] = [child]
    own = copy.deepcopy(base)
    own["root_object"]["name"] = "Authored Variant"
    variant = variant_document(create_variant_definition("base-guid", base, own))
    ancestors = [("base-guid", base_path)]
    # Leave every derived file stale after changing the base below. Cook must
    # resolve the whole chain, including ancestors that are staged later.
    for level in range(1, depth):
        ancestor_path = project / f"Assets/Ancestor{level}.prefab"
        ancestor_path.write_text(json.dumps(variant), encoding="utf8")
        ancestor_guid = f"ancestor-{level}"
        ancestors.append((ancestor_guid, ancestor_path))
        own = copy.deepcopy(variant)
        own.pop("variant")
        own["root_object"]["tag"] = f"Level {level}"
        variant = variant_document(create_variant_definition(ancestor_guid, variant, own))
    variant_path.write_text(json.dumps(variant), encoding="utf8")
    instance = copy.deepcopy(variant["root_object"])
    for node in (instance, instance["children"][0]):
        local = node.pop("local_id")
        node.update(id=20 + local, prefab_guid="variant-guid", prefab_root=local == 1, prefab_source_id=local)
        node["transform"]["component_id"] = 100 + local
    instance["prefab_source"] = _make_prefab_baseline(variant["root_object"])
    instance["children"][0]["name"] = "Instance override"
    scene_path = project / "Assets/Scene.scene"
    from infernux.engine.scene_authoring import encode_scene_document
    snapshot = {"name": "VariantScene", "isPlaying": False, "objects": [instance], "authoring_identity": {
        "objects": {str(node["id"]): f'{node["id"]:032x}' for node in (instance, instance["children"][0])},
        "components": {str(node["transform"]["component_id"]): f'{node["transform"]["component_id"]:032x}'
                       for node in (instance, instance["children"][0])},
    }}
    scene_path.write_text(json.dumps(encode_scene_document(snapshot)), encoding="utf8")
    base["root_object"]["layer"] = 7
    base["root_object"]["children"][0]["layer"] = 7
    addition = copy.deepcopy(child)
    addition.update(local_id=3, name="Added in current base")
    base["root_object"]["children"].append(addition)
    base["next_local_id"] = 4
    base_path.write_text(json.dumps(base), encoding="utf8")
    source_paths = [path for _, path in ancestors] + [variant_path]
    before = [path.read_bytes() for path in source_paths]
    builder._cooked_asset_entries = {
        guid: _asset_index_entry(project, path, guid, "", "Prefab")
        for guid, path in [("variant-guid", variant_path), *reversed(ancestors)]
    }
    builder._cooked_asset_entries["scene-guid"] = _asset_index_entry(project, scene_path, "scene-guid", "", "Scene")
    builder._runtime_artifact_bindings = {}
    builder._runtime_artifact_source_paths = set()
    data_dir = tmp_path / "dist/Data"
    _stage_document_inputs(builder, data_dir)
    if source_change == "base-edit":
        base["root_object"]["layer"] = 12
        base["root_object"]["children"][0]["layer"] = 12
        base_path.write_text(json.dumps(base), encoding="utf8")
        before = [path.read_bytes() for path in source_paths]
    elif source_change == "source-removal":
        for path in [*source_paths, scene_path]:
            path.unlink()
    builder._stage_library_runtime_documents(str(data_dir))
    cooked = json.loads((data_dir / "Library/Artifacts/Document/variant-guid.prefab").read_text(encoding="utf8"))
    assert cooked["root_object"]["name"] == "Authored Variant"
    assert cooked["root_object"]["layer"] == 7
    assert cooked["root_object"]["tag"] == (f"Level {depth - 1}" if depth > 1 else "Untagged")
    assert "variant" not in cooked
    if source_change == "source-removal":
        assert not any(path.exists() for path in [*source_paths, scene_path])
    else:
        assert [path.read_bytes() for path in source_paths] == before
    for guid, _ in ancestors:
        ancestor = json.loads((data_dir / f"Library/Artifacts/Document/{guid}.prefab").read_text(encoding="utf8"))
        assert "variant" not in ancestor
        assert ancestor["root_object"]["children"][0]["layer"] == 7
    cooked_scene = json.loads((data_dir / "Library/Artifacts/Document/scene-guid.scene").read_text(encoding="utf8"))
    result = cooked_scene["objects"][0]
    assert result["id"] == 1 and result["children"][0]["id"] == 2
    assert result["children"][0]["layer"] == 7
    assert result["children"][0]["name"] == "Instance override"
    assert result["children"][1]["name"] == "Added in current base"
    assert result["children"][1]["id"] not in (result["id"], result["children"][0]["id"])
    assert "prefab_source" not in result
    assert cooked_scene["identity_format"] == "runtime-v1"
    assert "authoring_identity" not in cooked_scene
    artifact = data_dir / "Library/Artifacts/Document/scene-guid.scene"
    first = artifact.read_bytes()
    builder._stage_library_runtime_documents(str(data_dir))
    assert artifact.read_bytes() == first


@pytest.mark.parametrize("imported", [False, True])
def test_scene_cook_preserves_native_joint_references_and_loads(scene, tmp_path, imported):
    from infernux.lib import SceneManager
    from infernux.engine.runtime_scene_transaction import SceneDocumentTransaction

    target = scene.create_game_object("JointTarget")
    body = target.add_component("Rigidbody")
    owner = scene.create_game_object("JointOwner")
    owner.add_component("Rigidbody")
    joint = owner.add_component("HingeJoint")
    joint.connected_body = body
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    source = project / "Assets/Main.scene"
    assert scene.save_to_file(str(source))
    authored = source.read_bytes()
    if imported:
        source = project / "Assets/Generated.custom"
        source.write_bytes(b"custom importer source")
    entry = _asset_index_entry(project, source, "scene-guid", "", "Scene")
    if imported:
        entry["metadata"]["metadata"].update({
            "import_document": {"value": authored.decode("utf-8")},
            "file_extension": {"value": ".scene"},
        })
    source_before = source.read_bytes()
    builder._cooked_asset_entries = {
        "scene-guid": entry,
    }
    builder._runtime_artifact_bindings = {}
    builder._runtime_artifact_source_paths = set()
    data_dir = tmp_path / "dist/Data"
    artifact = data_dir / "Library/Artifacts/Document/scene-guid.scene"
    _stage_document_inputs(builder, data_dir)
    builder._stage_library_runtime_documents(str(data_dir))
    first = artifact.read_bytes()
    builder._stage_library_runtime_documents(str(data_dir))
    assert artifact.read_bytes() == first
    assert source.read_bytes() == source_before
    cooked = json.loads(first)
    assert cooked["identity_format"] == "runtime-v1"
    assert not {"authoring_identity", "nextObjectId", "nextComponentId"}.intersection(cooked)
    loaded = SceneManager.instance().create_scene("CookedWorld")
    transaction = SceneDocumentTransaction(loaded, path=str(artifact), clear_registries=False)
    assert transaction.run_to_completion(), transaction.error
    assert loaded.find("JointTarget").id != target.id
    connected = loaded.find("JointOwner").get_component("HingeJoint").connected_body
    assert connected is loaded.find("JointTarget").get_component("Rigidbody")


def test_player_catalog_excludes_editor_assets_and_rejects_runtime_dependencies(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    entries = []
    for guid, relative, kind in (
        ("runtime", "Assets/Scripts/EditorHelper.py", "PythonScript"),
        ("tool", "Assets/Editor/Author.py", "PythonScript"),
        ("nested-tool", "Assets/Tools/eDiToR/Author.py", "PythonScript"),
        ("tool-data", "Assets/Tools/Editor/Settings.json", "Text"),
        ("scene", "Assets/Main.scene", "Scene"),
    ):
        source = project / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("", encoding="utf8")
        entries.append(_asset_index_entry(project, source, guid, "", kind))
    assert set(builder._collect_library_asset_entries(entries)) == {"runtime", "scene"}
    entries[0]["dependencies"] = ["nested-tool"]
    with pytest.raises(RuntimeError, match="dependency references editor-only content"):
        builder._collect_library_asset_entries(entries)
    entries[0]["dependencies"] = []
    with pytest.raises(RuntimeError, match="dependency references editor-only content"):
        builder._collect_library_asset_entries(entries, extra_roots=("tool",))
    assert builder._is_player_editor_path("Assets/Editor/Author.pyc")
    assert not builder._is_player_editor_path("Assets/Scripts/EditorHelper.pyc")
    preview = project / "Assets/Editor/Preview.scene"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text("{}", encoding="utf8")
    entries.append(
        _asset_index_entry(project, preview, "preview-guid", "", "Scene")
    )
    (project / "ProjectSettings/BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["preview-guid"]}), encoding="utf8")
    # A build freezes its scene list; changing author settings starts a new build.
    builder = GameBuilder(str(project), str(tmp_path / "preview_output"), game_name="TestGame")
    builder.freeze_asset_index_entries(entries)
    with pytest.raises(RuntimeError, match="BuildSettings scene is editor-only"):
        builder._collect_library_asset_entries(entries)


def test_cooked_python_sources_follow_the_frozen_player_closure(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    runtime = project / "Assets" / "Scripts" / "Gameplay.py"
    editor = project / "Assets" / "Editor" / "Bake.py"
    package = project / "Packages" / "effects" / "Runtime" / "Bloom.py"
    for source in (runtime, editor, package):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("value = 1\n", encoding="utf-8")

    builder._player_python_source_paths = {
        str(runtime.resolve()),
        str(package.resolve()),
    }

    assert builder.cooked_python_source_paths() == tuple(
        sorted((str(runtime.resolve()), str(package.resolve())), key=str.casefold)
    )
    assert str(editor.resolve()) not in builder.cooked_python_source_paths()


def test_content_archive_keeps_only_catalog_staged_project_glsl(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    data = tmp_path / "dist" / "TestGame_Data"
    runtime_shader = data / "Library" / "Artifacts" / "Blob" / "shader-guid.frag"
    runtime_shader.parent.mkdir(parents=True)
    runtime_shader.write_text(
        'ShaderInfo { Name "Test/Surface" Type Fragment }\n',
        encoding="utf-8",
    )

    builder._pack_content_archive(str(tmp_path / "dist"))

    archive = data / builder._CONTENT_ARCHIVE_FILENAME
    names = {entry["path"] for entry in read_manifest(archive)["files"]}
    assert "Library/Artifacts/Blob/shader-guid.frag" in names


def test_content_archive_rejects_project_glsl_outside_runtime_catalog_area(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    shader = tmp_path / "dist" / "TestGame_Data" / "Assets" / "Shaders" / "Loose.frag"
    shader.parent.mkdir(parents=True)
    shader.write_text("#version 450\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="authoring/source files"):
        builder._pack_content_archive(str(tmp_path / "dist"))


def test_game_data_replaces_current_texture_source_with_library_artifact(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    source = project / "Assets" / "Art" / "Smoke.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"texture source")
    artifact_relative = "Library/Artifacts/Texture/texture-guid.inxtex"
    artifact = project / artifact_relative
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(
        b"INXTEXTURE" + b"\x04\x03\x02\x01" + (16).to_bytes(4, "little") + b"a" * 16 + b"payload"
    )
    _write_texture_asset_index(project, source, "texture-guid", artifact_relative)

    final_dir = tmp_path / "dist"
    builder._copy_game_data(str(final_dir))
    _write_player_executable(final_dir)
    builder._organize_player_layout(str(final_dir))
    builder._pack_content_archive(str(final_dir))

    archive = final_dir / "TestGame_Data" / builder._CONTENT_ARCHIVE_FILENAME
    names = {entry["path"] for entry in read_manifest(archive)["files"]}
    assert artifact_relative in names
    assert "Assets/Art/Smoke.png" not in names


def test_game_data_rejects_missing_current_library_artifact(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    source = project / "Assets" / "Art" / "Missing.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"texture source")
    _write_texture_asset_index(
        project,
        source,
        "missing-guid",
        "Library/Artifacts/Texture/missing-guid.inxtex",
    )

    with pytest.raises(RuntimeError, match="Library artifact selection failed"):
        builder._copy_game_data(str(tmp_path / "dist"))


def test_game_data_includes_particle_artifacts(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    _reference_particle_graph(Path(builder.project_path), "smoke")
    guid = hashlib.md5(b"smoke").hexdigest()
    artifact = (
        Path(builder.project_path)
        / "Library"
        / "Artifacts"
        / "Particle"
        / f"{guid}.inxparticle"
    )
    final_dir = tmp_path / "dist"

    builder._copy_game_data(str(final_dir))
    shipped = (
        final_dir
        / "Data"
        / "Library"
        / "Artifacts"
        / "Particle"
        / artifact.name
    )
    assert shipped.read_text(encoding="utf-8") == artifact.read_text(encoding="utf-8")
    assert json.loads(shipped.read_text(encoding="utf-8"))["source_hash"]
    runtime_index = json.loads(
        (shipped.parent / builder._PARTICLE_RUNTIME_INDEX_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert runtime_index == {
        "$schema": "infernux.particle_runtime_index",
        "entries": [
            {
                "guid": hashlib.md5(b"smoke").hexdigest(),
                "path_hint": "Assets/VFX/smoke.particlegraph",
                "stable_id": "smoke",
            }
        ],
    }

    _write_player_executable(final_dir)
    builder._organize_player_layout(str(final_dir))
    builder._pack_content_archive(str(final_dir))
    header = read_manifest(
        final_dir / "TestGame_Data" / builder._CONTENT_ARCHIVE_FILENAME
    )
    names = {entry["path"] for entry in header["files"]}
    assert f"Library/Artifacts/Particle/{guid}.inxparticle" in names
    assert "Library/Artifacts/Particle/RuntimeIndex.json" in names


def test_particle_reference_uses_guid_when_serialized_path_belongs_to_old_project(
    tmp_path,
):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    _reference_particle_graph(project, "portable")
    scene_path = project / "Assets" / "Main.scene"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    reference = scene["objects"][0]["components"][0]["data"]["graph"]
    reference["path_hint"] = "C:/retired/project/Assets/VFX/portable.particlegraph"
    scene_path.write_text(json.dumps(scene), encoding="utf-8")

    assert builder._collect_reachable_particle_artifacts() == [
        {
            "guid": hashlib.md5(b"portable").hexdigest(),
            "path_hint": "Assets/VFX/portable.particlegraph",
            "stable_id": "portable",
        }
    ]


def test_particle_reference_without_guid_is_ignored(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    _reference_particle_graph(project, "path-only")
    scene_path = project / "Assets" / "Main.scene"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    reference = scene["objects"][0]["components"][0]["data"]["graph"]
    reference["guid"] = ""
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    _write_asset_index(
        project,
        [
            entry
            for entry in load_asset_index(project)
            if entry["guid"] != hashlib.md5(b"path-only").hexdigest()
        ],
    )

    assert builder._collect_reachable_particle_artifacts() == []


def test_game_data_excludes_unreachable_particle_artifacts(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    _reference_particle_graph(project, "reachable")
    artifact_root = project / "Library" / "Artifacts" / "Particle"
    artifact_root.mkdir(parents=True, exist_ok=True)
    guid = hashlib.md5(b"reachable").hexdigest()
    (artifact_root / "unreachable.inxparticle").write_text(
        '{"$schema":"infernux.particle_artifact"}',
        encoding="utf-8",
    )

    final_dir = tmp_path / "dist"
    builder._copy_game_data(str(final_dir))

    shipped = final_dir / "Data" / "Library" / "Artifacts" / "Particle"
    assert (shipped / f"{guid}.inxparticle").is_file()
    assert not (shipped / "unreachable.inxparticle").exists()


def test_game_data_recompiles_instead_of_shipping_stable_id_particle_artifact(
    tmp_path,
):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    _reference_particle_graph(project, "retired-name")
    artifact_root = project / "Library" / "Artifacts" / "Particle"
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "retired-name.inxparticle").write_text(
        '{"$schema":"retired-particle-artifact"}',
        encoding="utf-8",
    )
    guid = hashlib.md5(b"retired-name").hexdigest()

    builder._copy_game_data(str(tmp_path / "dist"))

    shipped = tmp_path / "dist" / "Data" / "Library" / "Artifacts" / "Particle"
    assert (shipped / f"{guid}.inxparticle").is_file()
    assert not (shipped / "retired-name.inxparticle").exists()


def test_particle_runtime_index_ignores_path_only_entry(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    index_path = (
        Path(builder.project_path)
        / "Library"
        / "Artifacts"
        / "Particle"
        / "RuntimeIndex.json"
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(
            {
                "$schema": "infernux.particle_runtime_index",
                "entries": [
                    {
                        "guid": "",
                        "path_hint": "Assets/VFX/path-only.particlegraph",
                        "stable_id": "path-only",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert builder._particle_library_artifacts() == {}


def test_game_data_compiles_missing_particle_artifact(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    _reference_particle_graph(project, "missing")
    (project / "Library" / "Artifacts" / "Particle").mkdir(parents=True, exist_ok=True)
    guid = hashlib.md5(b"missing").hexdigest()

    builder._copy_game_data(str(tmp_path / "dist"))

    shipped = (
        tmp_path
        / "dist"
        / "Data"
        / "Library"
        / "Artifacts"
        / "Particle"
        / f"{guid}.inxparticle"
    )
    assert shipped.is_file()
    payload = json.loads(shipped.read_text(encoding="utf-8"))
    assert payload["$schema"] == "infernux.particle_artifact"
    assert payload["source_hash"]


def test_game_data_fails_when_particle_source_cannot_compile(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    graph_path = _reference_particle_graph(project, "broken")
    graph_path.write_text("{not-json", encoding="utf-8")
    guid = hashlib.md5(b"broken").hexdigest()
    scene_path = project / "Assets" / "Main.scene"
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene_path, "scene-guid", "", "Scene"),
            _asset_index_entry(project, graph_path, guid, "", "ParticleGraph"),
        ],
    )
    (project / "Library" / "Artifacts" / "Particle").mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match="Library particle artifact compile failed"):
        builder._copy_game_data(str(tmp_path / "dist"))


def test_game_data_keeps_particle_artifacts_that_share_graph_stable_id(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    first = _reference_particle_graph(project, "shared")
    second = project / "Assets" / "VFX" / "copy.particlegraph"
    second.write_text(
        ParticleGraphAsset(stable_id="shared", name="copy").canonical_json(),
        encoding="utf-8",
    )
    guid1 = hashlib.md5(b"shared").hexdigest()
    guid2 = hashlib.md5(b"copy").hexdigest()
    scene_path = project / "Assets" / "Main.scene"
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, scene_path, "scene-guid", "", "Scene"),
            _asset_index_entry(project, first, guid1, "", "ParticleGraph"),
            _asset_index_entry(project, second, guid2, "", "ParticleGraph"),
        ],
    )
    artifact_root = project / "Library" / "Artifacts" / "Particle"
    _write_particle_artifact(artifact_root / f"{guid1}.inxparticle", first)
    _write_particle_artifact(artifact_root / f"{guid2}.inxparticle", second)
    (artifact_root / "RuntimeIndex.json").write_text(
        json.dumps(
            {
                "$schema": "infernux.particle_runtime_index",
                "entries": [
                    {
                        "guid": guid1,
                        "path_hint": "Assets/VFX/shared.particlegraph",
                        "stable_id": "shared",
                    },
                    {
                        "guid": guid2,
                        "path_hint": "Assets/VFX/copy.particlegraph",
                        "stable_id": "shared",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    builder._copy_game_data(str(tmp_path / "dist"))
    shipped = tmp_path / "dist" / "Data" / "Library" / "Artifacts" / "Particle"
    assert (shipped / f"{guid1}.inxparticle").is_file()
    assert (shipped / f"{guid2}.inxparticle").is_file()


def test_validate_artifact_rejects_particle_owned_by_another_guid(tmp_path):
    project = _make_project(tmp_path)
    source = project / "Assets" / "VFX" / "owned.particlegraph"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        ParticleGraphAsset(stable_id="owned", name="owned").canonical_json(),
        encoding="utf-8",
    )
    guid = hashlib.md5(b"owned").hexdigest()
    artifact = project / "Library" / "Artifacts" / "Particle" / "owned.inxparticle"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "$schema": "infernux.particle_artifact",
                "source_key": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "source_hash": _particle_source_hash(source),
                "kernel_ir": {"emitters": []},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeArtifactError, match="belongs to"):
        validate_artifact(
            project,
            _asset_index_entry(project, source, guid, "", "ParticleGraph"),
            artifact,
        )


def test_particle_runtime_index_requires_current_asset_index_for_scene_identity(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    (project / "Library" / "AssetIndex.json").unlink()
    data_dir = tmp_path / "dist" / "Data"

    with pytest.raises(RuntimeArtifactError, match="AssetIndex is unreadable"):
        builder._write_particle_runtime_index(str(data_dir))


def test_particle_runtime_index_remains_required_for_reachable_graph(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    _reference_particle_graph(project, "smoke")
    (project / "Library" / "AssetIndex.json").unlink()

    with pytest.raises(RuntimeArtifactError, match="AssetIndex is unreadable"):
        builder._write_particle_runtime_index(str(tmp_path / "dist" / "Data"))


def test_particle_script_is_not_a_player_build_source(tmp_path):
    source = tmp_path / "Smoke.particle.py"
    source.write_text(
        "from infernux.particle import ParticleScript\n"
        "class Smoke(ParticleScript):\n"
        "    stable_id = 'smoke-script'\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="ParticleScript is Preview/Future"):
        GameBuilder._particle_source_stable_id(str(source))


def test_game_data_collects_all_imported_particle_interface_artifacts(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    _reference_particle_graph(project, "interfaces")
    particle_artifact = (
        project / "Library" / "Artifacts" / "Particle" / "interfaces.inxparticle"
    )
    particle_artifact.parent.mkdir(parents=True, exist_ok=True)

    def sample(opcode: str, stable_id: str) -> dict:
        return {"opcode": opcode, "immediates": [["interface", stable_id]]}

    emitters = [
        {
            "data_interfaces": [
                {
                    "kind": "vector_field",
                    "stable_id": "wind",
                    "texture": {"guid": "field-guid", "path_hint": ""},
                },
                {
                    "kind": "vector_field",
                    "stable_id": "unused",
                    "texture": {"guid": "unused-guid", "path_hint": ""},
                },
                {
                    "kind": "sdf_volume",
                    "stable_id": "collision",
                    "texture": {"guid": "sdf-guid", "path_hint": ""},
                },
            ],
            "init": {"instructions": []},
            "update": {
                "instructions": [
                    sample("sample_vector_field", "wind"),
                    sample("collide_sdf_position", "collision"),
                    sample("collide_sdf_velocity", "collision"),
                ]
            },
            "rendering": {"instructions": []},
        }
    ]
    graph_path = project / "Assets" / "VFX" / "interfaces.particlegraph"
    _write_particle_artifact(particle_artifact, graph_path, emitters=emitters)
    texture_artifact = (
        project / "Library" / "Artifacts" / "Texture" / "field-guid.inxtex"
    )
    unused_artifact = (
        project / "Library" / "Artifacts" / "Texture" / "unused-guid.inxtex"
    )
    sdf_artifact = (
        project / "Library" / "Artifacts" / "Texture" / "sdf-guid.inxtex"
    )
    texture_sources = {
        "field-guid": (project / "Assets" / "VFX" / "field.png", texture_artifact, "a" * 16),
        "sdf-guid": (project / "Assets" / "VFX" / "sdf.png", sdf_artifact, "b" * 16),
        "unused-guid": (project / "Assets" / "VFX" / "unused.png", unused_artifact, "c" * 16),
    }
    texture_entries = []
    for guid, (source, path, content_hash) in texture_sources.items():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(guid.encode("ascii"))
        _write_texture_artifact(path, guid.encode("ascii"), content_hash)
        texture_entries.append(
            _asset_index_entry(project, source, guid, f"Library/Artifacts/Texture/{path.name}", "Texture", content_hash)
        )
    particle_guid = hashlib.md5(b"interfaces").hexdigest()
    _write_asset_index(
        project,
        [
            _asset_index_entry(project, project / "Assets" / "Main.scene", "scene-guid", "", "Scene"),
            _asset_index_entry(project, graph_path, particle_guid, "", "ParticleGraph"),
            *texture_entries,
        ],
    )

    final_dir = tmp_path / "dist"
    builder._copy_game_data(str(final_dir))

    shipped = final_dir / "Data" / "Library" / "Artifacts"
    assert (shipped / "Texture" / texture_artifact.name).read_bytes() == texture_artifact.read_bytes()
    assert (shipped / "Texture" / sdf_artifact.name).read_bytes() == sdf_artifact.read_bytes()
    assert (shipped / "Texture" / unused_artifact.name).read_bytes() == unused_artifact.read_bytes()


def test_payload_manifest_rejects_missing_native_packages(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    _prepare_runtime_catalog_inputs(
        builder,
        final_dir,
        include_runtime=False,
        include_content=False,
    )

    with pytest.raises(RuntimeError, match="required native package is missing"):
        builder._write_payload_manifest(str(final_dir))


def test_content_archive_replaces_loose_project_files(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data = final_dir / "TestGame_Data"
    runtime_assets = data / "RuntimeAssets"
    settings = data / "ProjectSettings"
    runtime_assets.mkdir(parents=True)
    settings.mkdir(parents=True)
    (runtime_assets / "Main.inxscene").write_bytes(b"compiled scene")
    (runtime_assets / "Player.inxscript").write_bytes(b"compiled script")
    build_manifest = data / "BuildManifest.json"
    build_manifest.write_text('{"game_name": "TestGame"}', encoding="utf-8")
    (settings / "BuildSettings.json").write_text(
        '{"scene_guids": ["scene-guid"]}', encoding="utf-8"
    )
    (settings / "mcp_capabilities.json").write_text('{"enabled": true}', encoding="utf-8")
    (settings / "agent_tools.json").write_text('{"tools": []}', encoding="utf-8")

    builder._pack_content_archive(str(final_dir))

    archive_path = data / "Content.inxpkg"
    assert archive_path.is_file()
    assert build_manifest.is_file()
    assert not runtime_assets.exists()
    assert not settings.exists()
    header = read_manifest(archive_path)
    assert {entry["path"] for entry in header["files"]} == {
        "ProjectSettings/BuildSettings.json",
        "RuntimeAssets/Main.inxscene",
        "RuntimeAssets/Player.inxscript",
    }


def test_player_cook_excludes_editor_project_settings_before_archive(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    settings = project / "ProjectSettings"
    editor_files = (
        ".infernux-engine-lock.json",
        ".infernux-engine-lock.interrupted.tmp",
        "agent_tools.json",
        "mcp_capabilities.json",
        "requirements.txt",
        "EditorSettings.json",
        "GameView.ini",
    )
    for filename in editor_files:
        (settings / filename).write_text("editor-only", encoding="utf-8")
    (settings / "PhysicsSettings.json").write_text("{}", encoding="utf-8")
    (settings / "TagLayerSettings.json").write_text("{}", encoding="utf-8")
    (settings / "FutureEditorService.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (project / ".infernux-engine-lock.guard").touch()

    final_dir = tmp_path / "dist"
    builder._copy_game_data(str(final_dir))

    staged_settings = final_dir / "Data" / "ProjectSettings"
    assert (staged_settings / "BuildSettings.json").is_file()
    assert (staged_settings / "PhysicsSettings.json").is_file()
    assert (staged_settings / "TagLayerSettings.json").is_file()
    assert not (staged_settings / "FutureEditorService.json").exists()
    assert not (final_dir / "Data/.infernux-engine-lock.guard").exists()
    assert all(not (staged_settings / filename).exists() for filename in editor_files)


def test_content_archive_excludes_editor_settings_and_metadata(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data = final_dir / "TestGame_Data"
    settings = data / "ProjectSettings"
    assets = data / "Assets"
    settings.mkdir(parents=True)
    assets.mkdir(parents=True)
    (settings / "BuildSettings.json").write_text(
        '{"scene_guids": []}', encoding="utf-8"
    )
    editor_files = (
        ".infernux-engine-lock.json",
        ".infernux-engine-lock.interrupted.tmp",
        "agent_tools.json",
        "mcp_capabilities.json",
        "requirements.txt",
        "EditorSettings.json",
        "GameView.ini",
    )
    for filename in editor_files:
        (settings / filename).write_text("editor-only", encoding="utf-8")
    (assets / "editor-only.meta").write_text("metadata", encoding="utf-8")
    (data / ".infernux-engine-lock.guard").touch()

    builder._pack_content_archive(str(final_dir))

    archive = data / builder._CONTENT_ARCHIVE_FILENAME
    assert {entry["path"] for entry in read_manifest(archive)["files"]} == {
        "ProjectSettings/BuildSettings.json",
    }
    assert not (assets / "editor-only.meta").exists()
    assert not (data / ".infernux-engine-lock.guard").exists()
    assert all(not (settings / filename).exists() for filename in editor_files)


def test_content_archive_preserves_native_packages_and_player_controls(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    data = tmp_path / "dist" / "TestGame_Data"
    ordinary = data / "RuntimeAssets" / "Main.inxscene"
    runtime = data / builder._RUNTIME_ARCHIVE_FILENAME
    parallel = data / "Modules" / builder._PARALLEL_ARCHIVE_FILENAME
    extra_native = data / "Nested" / "Other.inxpkg"
    build_manifest = data / "BuildManifest.json"
    player_manifest = data / builder._PLAYER_MANIFEST_FILENAME
    ordinary.parent.mkdir(parents=True)
    parallel.parent.mkdir(parents=True)
    extra_native.parent.mkdir(parents=True)
    ordinary.write_bytes(b"compiled scene")
    runtime.write_bytes(b"runtime package")
    parallel.write_bytes(b"parallel module")
    extra_native.write_bytes(b"another native package")
    build_manifest.write_text('{"game_name":"TestGame"}', encoding="utf-8")
    player_manifest.write_text('{}', encoding="utf-8")

    builder._pack_content_archive(str(tmp_path / "dist"))

    header = read_manifest(data / builder._CONTENT_ARCHIVE_FILENAME)
    assert header["compression_profile"] == "release"
    names = {entry["path"] for entry in header["files"]}
    assert names == {"RuntimeAssets/Main.inxscene"}
    assert ordinary.exists() is False
    assert runtime.read_bytes() == b"runtime package"
    assert parallel.read_bytes() == b"parallel module"
    assert extra_native.read_bytes() == b"another native package"
    assert build_manifest.read_text(encoding="utf-8") == '{"game_name":"TestGame"}'
    assert player_manifest.read_text(encoding="utf-8") == "{}"


def test_content_archive_excludes_build_inputs_and_rewrites_project_paths(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    data = tmp_path / "dist" / "TestGame_Data"
    scene = data / "Assets" / "Main.scene"
    settings = data / "ProjectSettings"
    referenced = Path(builder.project_path) / "Assets" / "VFX" / "Smoke.particlegraph"
    scene.parent.mkdir(parents=True)
    settings.mkdir(parents=True)
    scene.write_text(
        json.dumps(
            {
                "graph": str(referenced),
                "external": "D:/External/Shared.asset",
            }
        ),
        encoding="utf-8",
    )
    (settings / "requirements.txt").write_text("numpy", encoding="utf-8")
    (settings / ".infernux-engine-lock.json").write_text(
        json.dumps({"project_root": builder.project_path}), encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="direct or serialized runtime payloads"):
        builder._pack_content_archive(str(tmp_path / "dist"))


def test_content_archive_excludes_particle_authoring_and_keeps_aot(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    data = tmp_path / "dist" / "TestGame_Data"
    graph = data / "Assets" / "VFX" / "Smoke.particlegraph"
    script = data / "Assets" / "VFX" / "Future.particle.py"
    artifact = data / "Library" / "Artifacts" / "Particle" / "smoke.inxparticle"
    runtime_index = artifact.parent / builder._PARTICLE_RUNTIME_INDEX_FILENAME
    graph.parent.mkdir(parents=True)
    artifact.parent.mkdir(parents=True)
    graph.write_text('{"$schema":"infernux.particle_graph"}', encoding="utf-8")
    graph.with_suffix(graph.suffix + ".meta").write_text("graph-meta", encoding="utf-8")
    script.write_text("# Preview ParticleScript", encoding="utf-8")
    script.with_suffix(".pyc").write_bytes(b"preview-bytecode")
    script.with_suffix(script.suffix + ".meta").write_text("script-meta", encoding="utf-8")
    cache = script.parent / "__pycache__"
    cache.mkdir()
    (cache / "Future.particle.cpython-312.pyc").write_bytes(b"preview-bytecode")
    (cache / "Future.particle.cpython-312.opt-2.pyc").write_bytes(b"optimized-preview")
    artifact.write_text('{"$schema":"infernux.particle_artifact"}', encoding="utf-8")
    runtime_index.write_text(
        '{"$schema":"infernux.particle_runtime_index","entries":[]}',
        encoding="utf-8",
    )

    builder._pack_content_archive(str(tmp_path / "dist"))

    header = read_manifest(data / builder._CONTENT_ARCHIVE_FILENAME)
    names = {entry["path"] for entry in header["files"]}
    assert "Library/Artifacts/Particle/smoke.inxparticle" in names
    assert "Library/Artifacts/Particle/RuntimeIndex.json" in names
    assert not any(name.casefold().endswith(".particlegraph") for name in names)
    assert not any(".particle." in name.casefold() for name in names)
    assert not graph.exists()
    assert not script.exists()


def test_content_archive_rejects_plaintext_project_scripts(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    script = tmp_path / "dist" / "TestGame_Data" / "RuntimeAssets" / "Player.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('source')", encoding="utf-8")

    with pytest.raises(RuntimeError, match="authoring/source files"):
        builder._pack_content_archive(str(tmp_path / "dist"))


def test_content_archive_keeps_compiled_scripts_and_cooked_runtime_documents(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    data = tmp_path / "dist" / "TestGame_Data"
    assets = data / "Assets" / "Scripts"
    assets.mkdir(parents=True)
    (assets / "Player.pyc").write_bytes(b"compiled-player")
    (assets / "Player.py.meta").write_text("editor metadata", encoding="utf-8")
    document = data / "Library" / "Artifacts" / "Document" / "scene-guid.scene"
    document.parent.mkdir(parents=True)
    document.write_text(
        '{"name":"Main","objects":[]}', encoding="utf-8"
    )
    (data / "BuildManifest.json").write_text(
        '{"game_name":"TestGame"}', encoding="utf-8"
    )

    builder._pack_content_archive(str(tmp_path / "dist"))

    header = read_manifest(data / builder._CONTENT_ARCHIVE_FILENAME)
    names = {entry["path"] for entry in header["files"]}
    assert "Assets/Scripts/Player.pyc" in names
    assert "Library/Artifacts/Document/scene-guid.scene" in names
    assert not any(name.endswith(".meta") for name in names)
    assert not (assets / "Player.pyc").exists()
    assert not document.exists()


def test_core_runtime_archive_replaces_loose_numpy_and_resources(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    numpy_file = final_dir / "numpy" / "core.py"
    numpy_init = final_dir / "numpy" / "__init__.pyc"
    numpy_core_init = final_dir / "numpy" / "_core" / "__init__.pyc"
    numpy_dll = final_dir / "numpy.libs" / "openblas.dll"
    numpy_header = final_dir / "numpy" / "_core" / "include" / "numpy" / "arrayobject.h"
    numpy_example = final_dir / "numpy" / "random" / "_examples" / "extending.pyx"
    numpy_stub = final_dir / "numpy" / "typing" / "_array_like.pyi"
    numpy_tests_extension = (
        final_dir / "numpy" / "_core" / "_multiarray_tests.cp313-win_amd64.pyd"
    )
    numpy_api_changes = final_dir / "numpy" / "ma" / "API_CHANGES.txt"
    numpy_license = final_dir / "numpy" / "LICENSE.txt"
    font = final_dir / "infernux" / "resources" / "fonts" / "engine.otf"
    gizmo_icon = final_dir / "infernux" / "resources" / "icons" / "gizmo_camera.png"
    light_icon = final_dir / "infernux" / "resources" / "icons" / "gizmo_light.png"
    editor_icon = final_dir / "infernux" / "resources" / "icons" / "file.png"
    numpy_file.parent.mkdir(parents=True)
    numpy_core_init.parent.mkdir(parents=True, exist_ok=True)
    numpy_dll.parent.mkdir(parents=True)
    numpy_header.parent.mkdir(parents=True)
    numpy_example.parent.mkdir(parents=True)
    numpy_stub.parent.mkdir(parents=True)
    numpy_tests_extension.parent.mkdir(parents=True, exist_ok=True)
    numpy_api_changes.parent.mkdir(parents=True, exist_ok=True)
    numpy_license.parent.mkdir(parents=True, exist_ok=True)
    font.parent.mkdir(parents=True)
    gizmo_icon.parent.mkdir(parents=True)
    light_icon.parent.mkdir(parents=True, exist_ok=True)
    numpy_file.write_text("VALUE = 1", encoding="utf-8")
    numpy_init.write_bytes(b"numpy package")
    numpy_core_init.write_bytes(b"numpy core package")
    numpy_dll.write_bytes(b"dll")
    numpy_header.write_text("header", encoding="utf-8")
    numpy_example.write_text("source", encoding="utf-8")
    numpy_stub.write_text("stub", encoding="utf-8")
    numpy_tests_extension.write_bytes(b"test extension")
    numpy_api_changes.write_text("history", encoding="utf-8")
    numpy_license.write_text("license", encoding="utf-8")
    font.write_bytes(b"font")
    gizmo_icon.write_bytes(b"gizmo")
    light_icon.write_bytes(b"light")
    editor_icon.write_bytes(b"editor")
    (final_dir / "TestGame_Data").mkdir(parents=True)

    builder._pack_core_runtime_archive(str(final_dir))

    archive = final_dir / "TestGame_Data" / "Runtime.inxrt"
    assert archive.is_file()
    assert not (final_dir / "numpy").exists()
    assert not (final_dir / "numpy.libs").exists()
    assert not (final_dir / "infernux" / "resources").exists()
    header = read_manifest(archive)
    assert header["compression_profile"] == "release"
    assert {entry["path"] for entry in header["files"]} == {
        "infernux/resources/fonts/engine.otf",
        "infernux/resources/icons/gizmo_camera.png",
        "infernux/resources/icons/gizmo_light.png",
        "numpy.libs/openblas.dll",
        "numpy/__init__.pyc",
        "numpy/_core/__init__.pyc",
        "numpy/core.py",
        "numpy/LICENSE.txt",
    }


def test_core_runtime_archive_excludes_editor_icon_payloads(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data_root = final_dir / "TestGame_Data"
    data_root.mkdir(parents=True)
    icons = final_dir / "infernux" / "resources" / "icons"
    icons.mkdir(parents=True)
    for name in ("icon.png", "gizmo_camera.png", "file.png", "scene.png"):
        (icons / name).write_bytes(name.encode("ascii"))

    builder._pack_core_runtime_archive(str(final_dir))

    names = {entry["path"] for entry in read_manifest(data_root / "Runtime.inxrt")["files"]}
    assert "infernux/resources/icons/icon.png" in names
    assert "infernux/resources/icons/gizmo_camera.png" in names
    assert "infernux/resources/icons/file.png" not in names
    assert "infernux/resources/icons/scene.png" not in names


def test_core_runtime_archive_omits_generic_icon_for_configured_project_icon(
    tmp_path,
):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project_icon = Path(builder.project_path) / "Assets" / "project-icon.png"
    project_icon.write_bytes(b"project-icon")
    builder.icon_guid = "project-icon-guid"
    final_dir = tmp_path / "dist"
    data_root = final_dir / "TestGame_Data"
    data_root.mkdir(parents=True)
    icons = final_dir / "infernux" / "resources" / "icons"
    icons.mkdir(parents=True)
    for name in ("icon.png", "gizmo_camera.png"):
        (icons / name).write_bytes(name.encode("ascii"))

    builder._pack_core_runtime_archive(str(final_dir))

    names = {
        entry["path"]
        for entry in read_manifest(data_root / "Runtime.inxrt")["files"]
    }
    assert "infernux/resources/icons/icon.png" not in names
    assert "infernux/resources/icons/gizmo_camera.png" in names


@pytest.mark.parametrize(
    ("debug_mode", "expected_profile"),
    ((False, "release"), (True, "development")),
)
def test_player_runtime_and_content_archives_share_build_profile(
    tmp_path,
    monkeypatch,
    debug_mode,
    expected_profile,
):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    builder.debug_mode = debug_mode
    final_dir = tmp_path / "dist"
    data_root = final_dir / "TestGame_Data"
    runtime_source = final_dir / "infernux" / "resources" / "runtime.bin"
    content_source = data_root / "RuntimeAssets" / "Main.inxscene"
    runtime_source.parent.mkdir(parents=True)
    content_source.parent.mkdir(parents=True)
    runtime_source.write_bytes(b"runtime")
    content_source.write_text("{}", encoding="utf-8")
    observed: dict[str, str] = {}
    native_write_pack = game_builder_module.write_pack_isolated

    def record_profile(files, destination, **kwargs):
        observed[Path(destination).name] = kwargs.get("profile", "development")
        return native_write_pack(files, destination, **kwargs)

    monkeypatch.setattr(game_builder_module, "write_pack_isolated", record_profile)

    builder._pack_core_runtime_archive(str(final_dir))
    builder._pack_content_archive(str(final_dir))

    assert observed == {
        "Runtime.inxrt": expected_profile,
        "Content.inxpkg": expected_profile,
    }


def test_pack_content_archive_yields_the_editor_thread(tmp_path, monkeypatch):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data_root = final_dir / "TestGame_Data" / "RuntimeAssets"
    data_root.mkdir(parents=True)
    for index in range(12):
        (data_root / f"Item{index}.bin").write_bytes(b"payload")
    yields: list[int] = []
    reports: list[tuple[str, float]] = []
    monkeypatch.setattr(
        game_builder_module,
        "_yield_editor_thread",
        lambda: yields.append(1),
    )

    builder._pack_content_archive(
        str(final_dir),
        on_progress=lambda message, fraction: reports.append((message, fraction)),
    )

    assert len(yields) >= 12
    assert any("Packing project content" in message for message, _fraction in reports)
    assert any(message == "Compressing project content" for message, _fraction in reports)
    assert any(
        message.startswith("Finalizing packed project content")
        for message, _fraction in reports
    )
    assert (final_dir / "TestGame_Data" / "Content.inxpkg").is_file()


def test_pack_content_archive_rejects_raw_model_sources(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    data_root = tmp_path / "dist" / "TestGame_Data" / "Assets" / "Models"
    data_root.mkdir(parents=True)
    (data_root / "Imported.blend").write_bytes(b"authoring source")

    with pytest.raises(RuntimeError, match="raw model sources"):
        builder._pack_content_archive(str(tmp_path / "dist"))


def test_pack_content_archive_finalizes_staged_trees_in_bulk(tmp_path, monkeypatch):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data = final_dir / "TestGame_Data"
    assets = data / "Assets" / "Art"
    library = data / "Library" / "Artifacts"
    nested_logs = data / "Assets" / "Logs"
    assets.mkdir(parents=True)
    library.mkdir(parents=True)
    nested_logs.mkdir(parents=True)
    (data / "Logs").mkdir()
    (data / "Modules").mkdir()
    (assets / "a.inxtex").write_bytes(b"asset")
    (library / "b.inxtex").write_bytes(b"library")
    (library / "keep.inxpkg").write_bytes(b"nested-pack")
    (nested_logs / "keep.txt").write_text("nested-log", encoding="utf-8")
    (data / "Logs" / "build.log").write_text("ok", encoding="utf-8")
    (data / "Modules" / "Parallel.inxmod").write_bytes(b"mod")
    (data / "Modules" / "leftover.bin").write_bytes(b"drop")
    (data / "Runtime.inxrt").write_bytes(b"runtime")
    (data / "BuildManifest.json").write_text("{}", encoding="utf-8")
    (data / builder.OUTPUT_MARKER_FILENAME).write_text("marker", encoding="utf-8")

    removed: list[str] = []
    real_remove = os.remove

    def track_remove(path):
        removed.append(os.fspath(path))
        return real_remove(path)

    monkeypatch.setattr(os, "remove", track_remove)
    reports: list[str] = []

    builder._pack_content_archive(
        str(final_dir),
        on_progress=lambda message, _fraction: reports.append(message),
    )

    assert (data / "Content.inxpkg").is_file()
    assert not (data / "Assets" / "Art").exists()
    assert not (library / "b.bin").exists()
    assert (data / "Assets" / "Logs" / "keep.txt").read_text(encoding="utf-8") == "nested-log"
    assert (data / "Library" / "Artifacts" / "keep.inxpkg").read_bytes() == b"nested-pack"
    assert (data / "Logs" / "build.log").read_text(encoding="utf-8") == "ok"
    assert (data / "Modules" / "Parallel.inxmod").read_bytes() == b"mod"
    assert not (data / "Modules" / "leftover.bin").exists()
    assert (data / "Runtime.inxrt").read_bytes() == b"runtime"
    assert (data / "BuildManifest.json").read_text(encoding="utf-8") == "{}"
    assert (data / builder.OUTPUT_MARKER_FILENAME).read_text(encoding="utf-8") == "marker"
    assert any(message == "Finalizing packed project content (Assets)" for message in reports)
    assert any(message == "Finalizing packed project content (Library)" for message in reports)
    packed_unlinks = [
        path
        for path in removed
        if path.endswith(
            (os.path.join("Art", "a.inxtex"), os.path.join("Artifacts", "b.inxtex"))
        )
    ]
    assert packed_unlinks == []


def test_pack_content_archive_finalize_honors_cancel(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    final_dir = tmp_path / "dist"
    data = final_dir / "TestGame_Data"
    (data / "Assets").mkdir(parents=True)
    (data / "Library").mkdir(parents=True)
    (data / "Assets" / "a.inxtex").write_bytes(b"asset")
    (data / "Library" / "b.inxtex").write_bytes(b"library")
    cancel_event = threading.Event()

    def _progress(message, _fraction):
        if message == "Finalizing packed project content":
            cancel_event.set()

    with pytest.raises(BuildCancelled):
        builder._pack_content_archive(
            str(final_dir),
            on_progress=_progress,
            cancel_event=cancel_event,
        )

    assert (data / "Content.inxpkg").is_file()
    assert (data / "Assets" / "a.inxtex").is_file()
    assert (data / "Library" / "b.inxtex").is_file()


def test_payload_manifest_rejects_reachable_direct_scene_material_and_audio(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    sources = _write_scene_material_audio_reachability_fixture(
        builder,
        include_unreachable=False,
    )
    final_dir = tmp_path / "dist"
    data_root = _prepare_runtime_catalog_inputs(builder, final_dir)
    write_pack(
        (
            ("Assets/Main.scene", sources["scene"]),
            ("Assets/Materials/Bird.mat", sources["material"]),
            ("Assets/Audio/Wing.wav", sources["audio"]),
        ),
        data_root / builder._CONTENT_ARCHIVE_FILENAME,
    )
    _install_runtime_identity_bindings(
        builder,
        {
            "Assets/Main.scene": (
                "scene-guid",
                "runtime_loader_requires_serialized_document",
            ),
            "Assets/Materials/Bird.mat": (
                "material-guid",
                "runtime_loader_requires_serialized_document",
            ),
            "Assets/Audio/Wing.wav": (
                "audio-guid",
                "runtime_audio_backend_requires_encoded_stream",
            ),
        },
    )

    with pytest.raises(
        RuntimeError,
        match="direct or serialized runtime payloads",
    ):
        builder._write_payload_manifest(str(final_dir))


def test_project_render_scripts_make_declared_shader_ids_reachable(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    script = project / "Assets" / "Scripts" / "CustomPipeline.py"
    declared_shader = project / "Assets" / "Shaders" / "Declared.frag"
    fullscreen_shader = project / "Assets" / "Shaders" / "Fullscreen.frag"
    unused_shader = project / "Assets" / "Shaders" / "Unused.frag"
    script.parent.mkdir(parents=True, exist_ok=True)
    declared_shader.parent.mkdir(parents=True, exist_ok=True)
    scene.write_text(
        json.dumps(
            {
                "objects": [
                    {
                        "components": [
                            {"type_id": "python:script-guid:type-guid:Scripts.CustomPipeline:Pipeline"}
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    script.write_text(
        "class Effect:\n"
        "    def get_shader_list(self):\n"
        "        return ['Project/Declared']\n"
        "    def setup(self, render_pass):\n"
        "        render_pass.fullscreen_quad('Project/Fullscreen')\n",
        encoding="utf-8",
    )
    declared_shader.write_text("void main() {}\n", encoding="utf-8")
    fullscreen_shader.write_text("void main() {}\n", encoding="utf-8")
    unused_shader.write_text("void main() {}\n", encoding="utf-8")

    scene_entry = _asset_index_entry(project, scene, "scene-guid", "", "Scene")
    script_entry = _asset_index_entry(project, script, "script-guid", "", "PythonScript")
    declared_entry = _asset_index_entry(
        project, declared_shader, "declared-shader-guid", "", "Shader"
    )
    declared_entry["metadata"]["metadata"]["shader_id"] = {
        "value": "Project/Declared"
    }
    fullscreen_entry = _asset_index_entry(
        project, fullscreen_shader, "fullscreen-shader-guid", "", "Shader"
    )
    fullscreen_entry["metadata"]["metadata"]["shader_id"] = {
        "value": "Project/Fullscreen"
    }
    unused_entry = _asset_index_entry(
        project, unused_shader, "unused-shader-guid", "", "Shader"
    )
    unused_entry["metadata"]["metadata"]["shader_id"] = {
        "value": "Project/Unused"
    }
    entries = [
        scene_entry,
        script_entry,
        declared_entry,
        fullscreen_entry,
        unused_entry,
    ]
    _write_asset_index(project, entries)
    (project / "ProjectSettings" / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )

    selected = builder._collect_library_asset_entries(entries)

    assert set(selected) == {
        "scene-guid",
        "script-guid",
        "declared-shader-guid",
        "fullscreen-shader-guid",
        "unused-shader-guid",
    }


def test_copy_cooked_assets_catalogs_builtin_shaders_without_duplicating_them(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    shader = project / "Library" / "Resources" / "shaders" / "standard.vert"
    shader.parent.mkdir(parents=True, exist_ok=True)
    shader.write_text("void main() {}\n", encoding="utf-8")
    scene.write_text(json.dumps({"shader": "builtin-shader-guid"}), encoding="utf-8")
    scene_entry = _asset_index_entry(
        project,
        scene,
        "scene-guid",
        "",
        "Scene",
    )
    shader_entry = _asset_index_entry(
        project,
        shader,
        "builtin-shader-guid",
        "",
        "Shader",
    )
    shader_entry["read_only"] = True
    _write_asset_index(project, [scene_entry, shader_entry])
    data_dir = tmp_path / "dist" / "Data"

    builder._copy_cooked_assets(str(data_dir))

    assert (data_dir / "Assets" / "Main.scene").is_file()
    assert not (data_dir / "Library" / "Resources" / "shaders" / "standard.vert").exists()
    assert "builtin-shader-guid" in builder._cooked_asset_entries

    builder._cooked_asset_entries = {"builtin-shader-guid": shader_entry}
    builder._runtime_artifact_bindings = {}
    builder._write_runtime_asset_records(str(tmp_path / "dist"))
    records = json.loads(
        (data_dir / "Library" / "RuntimeAssetRecords.json").read_text(encoding="utf-8")
    )
    shader_record = next(
        item for item in records["entries"] if item["guid"] == "builtin-shader-guid"
    )
    assert shader_record["runtime_artifacts"] == [
        {
            "package": "Runtime.inxrt",
            "runtime_path": "infernux/resources/shaders/standard.vert",
            "runtime_artifact_id": game_builder_module.runtime_artifact_id(
                "Runtime.inxrt", "infernux/resources/shaders/standard.vert"
            ),
        }
    ]


def test_platform_cook_packages_reachable_builtin_resources_in_content(tmp_path):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    shader = project / "Library" / "Resources" / "shaders" / "standard.vert"
    shader.parent.mkdir(parents=True, exist_ok=True)
    shader.write_text("void main() {}\n", encoding="utf-8")
    scene.write_text(json.dumps({"shader": "builtin-shader-guid"}), encoding="utf-8")
    scene_entry = _asset_index_entry(project, scene, "scene-guid", "", "Scene")
    shader_entry = _asset_index_entry(
        project,
        shader,
        "builtin-shader-guid",
        "",
        "Shader",
    )
    shader_entry["read_only"] = True
    _write_asset_index(project, [scene_entry, shader_entry])
    final_dir = tmp_path / "dist"
    data_dir = final_dir / "Data"

    builder._copy_cooked_assets(
        str(data_dir),
        package_builtin_resources=True,
    )

    packaged_shader = data_dir / "infernux/resources/shaders/standard.vert"
    assert packaged_shader.read_text(encoding="utf-8") == "void main() {}\n"

    builder._cooked_asset_entries = {"builtin-shader-guid": shader_entry}
    builder._runtime_artifact_bindings = {}
    builder._write_runtime_asset_records(
        str(final_dir),
        package_builtin_resources=True,
    )
    records = json.loads(
        (data_dir / "Library" / "RuntimeAssetRecords.json").read_text(
            encoding="utf-8"
        )
    )
    shader_record = next(
        item for item in records["entries"] if item["guid"] == "builtin-shader-guid"
    )
    assert shader_record["runtime_artifacts"] == [
        {
            "package": "Content.inxpkg",
            "runtime_path": "infernux/resources/shaders/standard.vert",
            "runtime_artifact_id": game_builder_module.runtime_artifact_id(
                "Content.inxpkg", "infernux/resources/shaders/standard.vert"
            ),
        }
    ]

    data_root = final_dir / "TestGame_Data"
    data_root.parent.mkdir(parents=True, exist_ok=True)
    data_dir.rename(data_root)
    (data_root / "Assets/Main.scene").unlink()
    (data_root / "Assets").rmdir()
    builder._pack_content_archive(
        str(final_dir),
        package_builtin_resources=True,
    )
    manifest = read_manifest(data_root / "Content.inxpkg")
    assert "infernux/resources/shaders/standard.vert" in {
        entry["path"] for entry in manifest["files"]
    }


def test_package_resource_shader_keeps_guid_identity_in_headless_build(
    tmp_path, monkeypatch
):
    builder = _make_builder(tmp_path, tmp_path / "build_output")
    project = Path(builder.project_path)
    scene = project / "Assets" / "Main.scene"
    package_resources = tmp_path / "installed-engine" / "infernux" / "resources"
    shader = package_resources / "shaders" / "particle_sprite.vert"
    shader.parent.mkdir(parents=True, exist_ok=True)
    shader.write_text("void main() {}\n", encoding="utf-8")
    scene.write_text(json.dumps({"shader": "particle-shader-guid"}), encoding="utf-8")
    scene_entry = _asset_index_entry(project, scene, "scene-guid", "", "Scene")
    shader_entry = _asset_index_entry(
        project,
        shader,
        "particle-shader-guid",
        "",
        "Shader",
    )
    shader_entry["read_only"] = True
    shader_entry["metadata"]["metadata"]["shader_id"] = {
        "type": "string",
        "value": "Particle Sprite",
    }
    monkeypatch.setattr(
        game_builder_module._resources,
        "get_package_resources_path",
        lambda: str(package_resources),
    )
    monkeypatch.setattr(
        game_builder_module._resources,
        "resources_path",
        str(package_resources),
    )
    entries = [scene_entry, shader_entry]
    _write_asset_index(project, entries)
    (project / "ProjectSettings" / "BuildSettings.json").write_text(
        json.dumps({"scene_guids": ["scene-guid"]}),
        encoding="utf-8",
    )

    selected = builder._collect_library_asset_entries(entries)

    assert set(selected) == {"scene-guid", "particle-shader-guid"}
    builder._copy_cooked_assets(str(tmp_path / "copy" / "Data"))
    assert (tmp_path / "copy" / "Data" / "Assets" / "Main.scene").is_file()
    assert not (
        tmp_path
        / "copy"
        / "Data"
        / "Library"
        / "Resources"
        / "shaders"
        / "particle_sprite.vert"
    ).exists()
    builder._cooked_asset_entries = {
        "particle-shader-guid": selected["particle-shader-guid"]
    }
    builder._runtime_artifact_bindings = {}
    final_dir = tmp_path / "dist"
    builder._write_runtime_asset_records(str(final_dir))
    records = json.loads(
        (final_dir / "Data" / "Library" / "RuntimeAssetRecords.json").read_text(
            encoding="utf-8"
        )
    )
    shader_record = next(
        item for item in records["entries"] if item["guid"] == "particle-shader-guid"
    )
    assert shader_record["runtime_path"] == (
        "Library/Resources/shaders/particle_sprite.vert"
    )
    assert shader_record["runtime_artifacts"][0]["runtime_path"] == (
        "infernux/resources/shaders/particle_sprite.vert"
    )
    assert shader_record["metadata"]["metadata"]["shader_id"]["value"] == (
        "Particle Sprite"
    )
