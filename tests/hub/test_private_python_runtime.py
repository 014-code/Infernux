from __future__ import annotations

from contextlib import nullcontext

import io
import os
import sys
import subprocess
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


PACKAGING_ROOT = (Path(__file__).resolve().parents[2] / "packaging")

import embed_runtime_manager
import stage_bundled_python_runtime
from private_python_runtime import (
    extract_runtime_archive,
    is_private_runtime_root,
    prune_runtime_staging_cache,
    runtime_archive_for_machine,
)


def _write_runtime_archive(path: Path, executable: str = "python.exe") -> None:
    payload = b"private python"
    info = tarfile.TarInfo("python/" + executable)
    info.size = len(payload)
    with tarfile.open(path, mode="w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))


def test_runtime_discovery_agrees_before_and_after_interpreter_relocation(tmp_path, monkeypatch):
    relative = Path("python.exe") if sys.platform == "win32" else Path("bin/python")
    nested = tmp_path / "unrelated/tool" / relative
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"test interpreter")
    monkeypatch.setattr(stage_bundled_python_runtime, "_is_target_python", lambda path: Path(path).is_file())
    assert embed_runtime_manager._find_python_in_root(str(tmp_path)) is None
    assert stage_bundled_python_runtime._find_python_in_root(str(tmp_path)) is None
    executable = tmp_path / relative
    executable.parent.mkdir(parents=True, exist_ok=True)
    nested.rename(executable)
    assert embed_runtime_manager._find_python_in_root(str(tmp_path)) == str(executable)
    assert stage_bundled_python_runtime._find_python_in_root(str(tmp_path)) == str(executable)


def test_managed_runtime_extraction_and_provenance_preserve_external_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "hub-owned-runtime"
    private_root = runtime_dir / "python313"
    archive = tmp_path / "private-runtime.tar.gz"
    executable = "python.exe" if sys.platform == "win32" else "bin/python"
    _write_runtime_archive(archive, executable)

    external_python = tmp_path / "user-python" / executable
    external_python.parent.mkdir(parents=True)
    external_python.write_bytes(b"user installation")

    monkeypatch.setattr(embed_runtime_manager, "get_hub_shared_data_dir", lambda: str(tmp_path / "HubData"))
    monkeypatch.setattr(stage_bundled_python_runtime, "get_hub_shared_data_dir", lambda: str(tmp_path / "HubData"))
    assert embed_runtime_manager._default_runtime_dir() == str(tmp_path / "HubData/Runtimes")
    assert stage_bundled_python_runtime._bootstrap_root() == str(tmp_path / "HubData/Downloads/RuntimeBootstrap")
    manager = embed_runtime_manager.PythonRuntimeManager(runtime_dir=str(runtime_dir))
    monkeypatch.setattr(
        manager, "_ensure_runtime_archive", lambda *_args, **_kwargs: str(archive)
    )
    monkeypatch.setattr(
        embed_runtime_manager,
        "extract_runtime_archive",
        lambda source, destination, **kwargs: extract_runtime_archive(
            source,
            destination,
            runtime=kwargs["runtime"],
            validate=kwargs["validate"],
        ),
    )
    monkeypatch.setattr(
        embed_runtime_manager,
        "_is_python_version",
        lambda path, _version: Path(path).name == Path(executable).name and Path(path).is_file(),
    )
    monkeypatch.setattr(embed_runtime_manager, "_is_embedded_root", lambda _path: False)
    monkeypatch.setattr(manager, "_prepare_managed_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "_relocate_runtime_scripts", lambda *a: None)

    installed = manager._extract_runtime_to_root(str(private_root))

    assert Path(installed) == private_root / executable
    assert Path(installed).read_bytes() == b"private python"
    assert external_python.read_bytes() == b"user installation"

    assert is_private_runtime_root(private_root)
    marker = (private_root / ".infernux-private-python-runtime.json").read_text(encoding="utf-8")
    assert archive.name in marker


def test_runtime_catalog_versions_map_to_distinct_managed_roots(tmp_path) -> None:
    artifact = runtime_archive_for_machine(system="win32", machine="AMD64")

    assert artifact.name == (
        "cpython-3.13.15+20260825-x86_64-pc-windows-msvc-"
        "install_only.tar.gz"
    )
    assert artifact.url.startswith(
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
    )
    assert artifact.url.endswith(artifact.name.replace("+", "%2B"))
    assert not artifact.name.endswith((".exe", ".msi", ".pkg"))

    legacy = runtime_archive_for_machine(system="win32", machine="AMD64", runtime="3.12")
    assert legacy.name.startswith("cpython-3.12.13+20260805-")
    manager = embed_runtime_manager.PythonRuntimeManager(runtime_dir=str(tmp_path / "hub-runtime"))
    assert Path(manager.private_runtime_root("3.12")).name == "python312"
    assert Path(manager.private_runtime_root("3.13")).name == "python313"
    assert manager.default_version == "3.13"


def test_runtime_archive_download_uses_the_configured_ca_context(
    tmp_path, monkeypatch
):
    artifact = SimpleNamespace(
        name="python-runtime.tar.gz",
        url="https://example.test/python-runtime.tar.gz",
    )
    context = object()
    ca_bundle = str(tmp_path / "proxy-ca.pem")
    manager = embed_runtime_manager.PythonRuntimeManager(
        runtime_dir=str(tmp_path / "Runtimes"),
    )
    requests = []

    monkeypatch.setattr(
        embed_runtime_manager,
        "runtime_archive_for_machine",
        lambda **_kwargs: artifact,
    )
    monkeypatch.setattr(
        embed_runtime_manager,
        "create_download_ssl_context",
        lambda path: requests.append(("context", path)) or context,
    )
    monkeypatch.setattr(
        embed_runtime_manager.urllib.request,
        "urlopen",
        lambda request, **kwargs: (
            requests.append((request.full_url, kwargs["context"])),
            io.BytesIO(b"runtime archive"),
        )[1],
    )

    archive_path = manager._ensure_runtime_archive(download_ca_bundle=ca_bundle)

    assert Path(archive_path).read_bytes() == b"runtime archive"
    assert requests == [
        ("context", ca_bundle),
        (artifact.url, context),
    ]


def test_runtime_archive_download_uses_the_default_context_without_a_ca(
    tmp_path, monkeypatch
):
    artifact = SimpleNamespace(
        name="python-runtime.tar.gz",
        url="https://example.test/python-runtime.tar.gz",
    )
    manager = embed_runtime_manager.PythonRuntimeManager(
        runtime_dir=str(tmp_path / "Runtimes"),
    )
    contexts = []

    monkeypatch.setattr(
        embed_runtime_manager,
        "runtime_archive_for_machine",
        lambda **_kwargs: artifact,
    )
    monkeypatch.setattr(
        embed_runtime_manager,
        "create_download_ssl_context",
        lambda path: pytest.fail("no CA is configured"),
    )
    monkeypatch.setattr(
        embed_runtime_manager.urllib.request,
        "urlopen",
        lambda request, **kwargs: (
            contexts.append(kwargs["context"]),
            io.BytesIO(b"runtime archive"),
        )[1],
    )

    manager._ensure_runtime_archive()

    assert contexts == [None]


@pytest.mark.parametrize(
    ("platform_name", "header_path", "library_directory", "library_name"),
    (
        ("win32", Path("include/Python.h"), "libs", "python313.lib"),
        ("linux", Path("include/python3.13/Python.h"), "lib", "libpython3.13.so.1.0"),
        ("darwin", Path("include/python3.13/Python.h"), "lib", "libpython3.13.dylib"),
    ),
)
def test_target_runtime_layout_is_prepared_and_reused_at_its_install_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
    header_path: Path,
    library_directory: str,
    library_name: str,
) -> None:
    monkeypatch.setattr(stage_bundled_python_runtime.sys, "platform", platform_name)
    manager = embed_runtime_manager.PythonRuntimeManager(runtime_dir=str(tmp_path))
    prefix = Path(manager.private_runtime_root("3.13"))
    (prefix / header_path).parent.mkdir(parents=True)
    (prefix / header_path).write_bytes(b"")
    (prefix / library_directory).mkdir()
    (prefix / library_directory / library_name).write_bytes(b"")

    assert stage_bundled_python_runtime._has_dev_support(str(prefix))
    assert embed_runtime_manager._has_build_support(str(prefix), "3.13")

    python = manager.private_runtime_python("3.13")
    observed = []
    monkeypatch.setattr(manager, "_ensure_runtime_build_support", lambda root, *a, **kw: observed.append(root))
    monkeypatch.setattr(manager, "_ensure_pip", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "_ensure_runtime_packages", lambda *a, **kw: None)
    manager._prepare_managed_runtime(python, "3.13")
    assert observed == [manager.private_runtime_root("3.13")]
    monkeypatch.setattr(manager, "get_runtime_path", lambda runtime: python)
    monkeypatch.setattr(embed_runtime_manager, "is_frozen", lambda: True)
    monkeypatch.setattr(embed_runtime_manager, "_has_build_support", lambda root, runtime: root == observed[0])
    monkeypatch.setattr(manager, "_has_modules", lambda *a: True)
    monkeypatch.setattr(manager, "_runtime_lock", lambda *a: nullcontext())
    assert manager.ensure_runtime(version="3.13") == python


def test_broken_matching_bundle_does_not_fall_back_to_network(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    with zipfile.ZipFile(bundle_dir / "runtime_bundle.zip", "w") as bundle:
        bundle.writestr("python313/broken.txt", "broken runtime")
    manager = embed_runtime_manager.PythonRuntimeManager(runtime_dir=str(tmp_path / "managed"))
    monkeypatch.setattr(manager, "bundled_runtime_dirs", lambda: [str(bundle_dir)])
    downloads = []
    monkeypatch.setattr(manager, "_extract_runtime_to_root", lambda *a, **kw: downloads.append(True))
    with pytest.raises(embed_runtime_manager.PythonRuntimeError, match="bundled Python 3.13"):
        manager._provision_managed_runtime("3.13")
    assert downloads == []


@pytest.mark.parametrize("success", [True, False])
def test_missing_pip_uses_bundled_ensurepip_without_network(tmp_path, monkeypatch, success):
    manager = embed_runtime_manager.PythonRuntimeManager(runtime_dir=str(tmp_path))
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0 if success and args[3] == "ensurepip" else 1,
                                           stdout="", stderr="bootstrap failed")
    monkeypatch.setattr(embed_runtime_manager, "_run_command", run)
    monkeypatch.setattr(embed_runtime_manager, "_download_file", lambda *a, **kw: pytest.fail("pip bootstrap must be offline"))
    if success:
        manager._ensure_pip("python")
    else:
        with pytest.raises(embed_runtime_manager.PythonRuntimeError, match="bootstrap failed"):
            manager._ensure_pip("python")
    assert commands == [["python", "-I", "-m", "pip", "--version"], ["python", "-I", "-m", "ensurepip", "--upgrade"]]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable permissions")
def test_runtime_zip_preserves_executable_permissions(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    with zipfile.ZipFile(bundle_dir / "runtime_bundle.zip", "w") as bundle:
        info = zipfile.ZipInfo("python313/bin/python")
        info.create_system = 3
        info.external_attr = 0o100755 << 16
        bundle.writestr(info, "#!/bin/sh\nprintf '3.13\\n'\n")
    manager = embed_runtime_manager.PythonRuntimeManager(runtime_dir=str(tmp_path / "managed"))
    monkeypatch.setattr(manager, "bundled_runtime_dirs", lambda: [str(bundle_dir)])
    monkeypatch.setattr(embed_runtime_manager, "is_current_private_runtime_root", lambda root, **kw: Path(root).exists())
    monkeypatch.setattr(manager, "_prepare_managed_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "_relocate_runtime_scripts", lambda *a: None)
    python = manager._seed_runtime_from_bundle(version="3.13")
    assert python == manager.private_runtime_python("3.13")
    assert os.stat(python).st_mode & 0o111 == 0o111


def test_runtime_archive_rejects_paths_outside_destination(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    payload = b"do not write"
    info = tarfile.TarInfo("../outside.txt")
    info.size = len(payload)
    with tarfile.open(archive_path, mode="w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(RuntimeError, match="Invalid private Python runtime archive"):
        extract_runtime_archive(archive_path, tmp_path / "python313")

    assert not (tmp_path / "outside.txt").exists()


def test_stale_packaging_runtime_is_removed_from_current_installer(tmp_path: Path) -> None:
    legacy_installer = tmp_path / "python-3.12.8-amd64.exe"
    legacy_installer.write_bytes(b"installer")
    legacy_archive = (
        tmp_path
        / "cpython-3.12.13+20260805-x86_64-pc-windows-msvc-install_only.tar.gz"
    )
    legacy_archive.write_bytes(b"old archive")
    current_archive = tmp_path / runtime_archive_for_machine().name
    current_archive.write_bytes(b"current archive")
    runtime_python = tmp_path / "python312" / "python.exe"
    runtime_python.parent.mkdir()
    runtime_python.write_bytes(b"runtime")

    prune_runtime_staging_cache(tmp_path)

    assert not legacy_installer.exists()
    assert not legacy_archive.exists()
    assert current_archive.read_bytes() == b"current archive"
    assert not runtime_python.parent.exists()


def test_runtime_manager_refuses_non_hub_python_destination(tmp_path: Path) -> None:
    manager = embed_runtime_manager.PythonRuntimeManager(
        runtime_dir=str(tmp_path / "hub-runtime")
    )

    with pytest.raises(
        embed_runtime_manager.PythonRuntimeError,
        match="Hub-owned python313 directory",
    ):
        manager._extract_runtime_to_root(str(tmp_path / "user-python"))


def test_runtime_reinstall_does_not_prepare_after_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = embed_runtime_manager.PythonRuntimeManager(
        runtime_dir=str(tmp_path / "hub-runtime")
    )
    expected_python = str(tmp_path / "hub-runtime" / "python313" / "python.exe")
    observed: dict[str, object] = {}

    def seed_runtime(**kwargs):
        observed.update(kwargs)
        return expected_python

    monkeypatch.setattr(manager, "_seed_runtime_from_bundle", seed_runtime)
    monkeypatch.setattr(
        manager,
        "_prepare_managed_runtime",
        lambda *_args, **_kwargs: pytest.fail("published runtime must already be prepared"),
    )

    assert manager.reinstall_runtime() == expected_python
    assert observed["version"].series == "3.13"


def test_runtime_manager_contains_no_python_installer_execution_path() -> None:
    forbidden = (
        "InstallAllUsers=",
        "TargetDir=",
        "Include_launcher=",
        "InstallLauncherAllUsers=",
        "python.org/ftp/python",
        '"installer", "-pkg"',
    )
    sources = [
        path
        for path in PACKAGING_ROOT.rglob("*.py")
        if "tests" not in path.parts and "runtime" not in path.parts
    ]

    for source_path in sources:
        source = source_path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, (
                f"{source_path.relative_to(PACKAGING_ROOT)} reintroduced a system "
                f"Python installer path: {token}"
            )


def test_project_runtime_requires_explicitly_installed_target_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = embed_runtime_manager.PythonRuntimeManager(
        runtime_dir=str(tmp_path / "hub-runtime")
    )
    provisioned = []
    monkeypatch.setattr(
        manager,
        "_provision_managed_runtime",
        lambda *_args, **_kwargs: provisioned.append(True),
    )

    with pytest.raises(
        embed_runtime_manager.PythonRuntimeError,
        match="Python 3.12 is not installed",
    ):
        manager.create_project_runtime(
            str(tmp_path / "project" / ".runtime" / "python312"),
            version="3.12",
        )

    assert provisioned == []


def test_runtime_bundle_only_extracts_the_requested_python_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    runtime_bundle = bundle_dir / "runtime_bundle.zip"
    with zipfile.ZipFile(runtime_bundle, "w") as bundle:
        bundle.writestr("python313/python.exe", b"python 313")

    managed_root = tmp_path / "managed"
    existing_python313 = managed_root / "python313" / "keep.txt"
    existing_python313.parent.mkdir(parents=True)
    existing_python313.write_text("untouched", encoding="utf-8")
    manager = embed_runtime_manager.PythonRuntimeManager(
        runtime_dir=str(managed_root),
        bundle_runtime_dir=str(bundle_dir),
    )
    monkeypatch.setattr(
        manager,
        "bundled_runtime_dirs",
        lambda: [],
    )

    assert manager._seed_runtime_from_bundle(version="3.12") is None
    assert existing_python313.read_text(encoding="utf-8") == "untouched"
    assert not (managed_root / "python312").exists()
