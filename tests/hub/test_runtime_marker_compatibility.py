"""Old installer bundles survive auto-update and must remain installable."""
import json
import zipfile
from pathlib import Path

import pytest

import build_hub
from embed_runtime_manager import PythonRuntimeError, PythonRuntimeManager
from private_python_runtime import (
    PRIVATE_RUNTIME_MARKER, is_current_private_runtime_root,
    is_private_runtime_root, runtime_archive_for_machine, write_private_runtime_marker,
)


def _bundle(tmp_path, *, legacy, mutation=None, directory=False):
    source = tmp_path / "installer" / "python313"
    source.mkdir(parents=True)
    (source / "python.exe").write_bytes(b"runtime witness")
    write_private_runtime_marker(source, runtime_archive_for_machine().name)
    marker = source / PRIVATE_RUNTIME_MARKER
    payload = json.loads(marker.read_text())
    if legacy:
        payload["source_archive_sha256"] = "legacy provenance, not a runtime identity"
    if legacy == "extended":
        payload["extra_metadata"] = {"build_notes": ["future extension"]}
    if mutation:
        mutation(payload)
    marker.write_text(json.dumps(payload), encoding="utf-8")
    if directory:
        return source.parent
    bundle = source.parent / "runtime_bundle.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_LZMA) as archive:
        for file in source.iterdir():
            archive.write(file, "python313/" + file.name)
    return bundle


@pytest.mark.parametrize("legacy", [False, True, "extended"])
@pytest.mark.parametrize("directory", [False, True])
def test_old_and_current_installer_markers_seed_without_downloading(tmp_path, monkeypatch, legacy, directory):
    bundle = _bundle(tmp_path, legacy=legacy, directory=directory)
    manager = PythonRuntimeManager(runtime_dir=str(tmp_path / "managed"))
    monkeypatch.setattr(manager, "bundled_runtime_dirs", lambda: [str(bundle)] if directory else [])
    monkeypatch.setattr(manager, "bundled_runtime_bundle_paths", lambda: [] if directory else [str(bundle)])
    prepared = []
    monkeypatch.setattr(manager, "_prepare_candidate", lambda path, *a, **kw: prepared.append(path))
    monkeypatch.setattr(manager, "_extract_runtime_to_root", lambda *a, **kw: pytest.fail("bundle must not download"))
    if not directory:
        build_hub._validate_runtime_bundle(bundle)
    assert manager._provision_managed_runtime(manager._runtime_id("3.13")) == manager.private_runtime_python("3.13")
    root = Path(manager.private_runtime_root("3.13"))
    assert is_private_runtime_root(root)
    assert is_current_private_runtime_root(root)
    assert (root / "python.exe").read_bytes() == b"runtime witness"
    assert len(prepared) == 1


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(python_version="3.13.0"),
    lambda p: p.update(python_series="3.12"),
    lambda p: p.update(source_archive="wrong-platform.tar.gz"),
    lambda p: p.update(owner="another application"),
    lambda p: p.update(kind="not-a-runtime"),
    lambda p: p.pop("written_at"),
])
def test_invalid_legacy_markers_fail_build_and_install_without_replacing_live_runtime(tmp_path, monkeypatch, mutation):
    bundle = _bundle(tmp_path, legacy=True, mutation=mutation)
    with pytest.raises(RuntimeError, match="stale"):
        build_hub._validate_runtime_bundle(bundle)
    manager = PythonRuntimeManager(runtime_dir=str(tmp_path / "managed"))
    root = Path(manager.private_runtime_root("3.13"))
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("existing runtime")
    monkeypatch.setattr(manager, "bundled_runtime_dirs", lambda: [])
    monkeypatch.setattr(manager, "bundled_runtime_bundle_paths", lambda: [str(bundle)])
    monkeypatch.setattr(manager, "_extract_runtime_to_root", lambda *a, **kw: pytest.fail("must not download"))
    with pytest.raises(PythonRuntimeError, match="invalid release marker"):
        manager._provision_managed_runtime(manager._runtime_id("3.13"))
    assert (root / "keep.txt").read_text() == "existing runtime"
    assert not (root / "python.exe").exists()
