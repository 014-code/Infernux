"""Permanent Windows installations must not retain tempfile's protected ACL."""

import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import tempfile

import pytest

from installer.install_application import HubInstallTransaction
from installer.payload import HUB_EXECUTABLE
from installer import install_python_runtime
from installer_safety import install_marker_path, write_install_marker
from private_python_runtime import runtime_publication


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows inherited access control")


def protected_dacl(path):
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_security = advapi.GetNamedSecurityInfoW
    get_security.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    get_security.restype = wintypes.DWORD
    control = wintypes.WORD()
    revision = wintypes.DWORD()
    descriptor = ctypes.c_void_p()
    advapi.GetSecurityDescriptorControl.argtypes = [ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    result = get_security(str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor))
    if result:
        raise ctypes.WinError(result)
    try:
        if not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(control.value & 0x1000)
    finally:
        kernel.LocalFree(descriptor)


def test_published_hub_inherits_install_parent_access(tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / HUB_EXECUTABLE).write_bytes(b"test payload")
    target = tmp_path / "Installed Hub"
    with HubInstallTransaction(payload, target) as transaction:
        transaction.prepare()
        assert not protected_dacl(transaction.staged_dir)
        transaction.activate()
        transaction.commit()
    assert not protected_dacl(target)
    assert (target / HUB_EXECUTABLE).read_bytes() == b"test payload"


def test_runtime_candidate_inherits_shared_storage_access(tmp_path):
    target = tmp_path / "Shared/Runtimes/python313"
    with runtime_publication(target) as candidate:
        assert not protected_dacl(candidate.parent)
        candidate.mkdir()
        (candidate / "python.exe").write_bytes(b"runtime")
        assert not protected_dacl(candidate)
    assert not protected_dacl(target)
    (target / "write-probe").write_text("ordinary user writable")


def test_legacy_update_repairs_native_acl_once(tmp_path, monkeypatch):
    old_install = Path(tempfile.mkdtemp(prefix="legacy-hub-", dir=tmp_path))
    assert protected_dacl(old_install)
    write_install_marker(str(old_install))
    marker = Path(install_marker_path(str(old_install)))
    document = json.loads(marker.read_text(encoding="utf-8"))
    del document["access_layout"]
    marker.write_text(json.dumps(document), encoding="utf-8")
    shared = old_install / "InfernuxHubData/Shared"
    retained = shared / "Runtimes/python313/keep.txt"
    retained.parent.mkdir(parents=True)
    retained.write_text("existing runtime", encoding="utf-8")
    # The fixture is owned by this test user, allowing real ACL changes without
    # a UAC prompt. The production migration runs only in the elevated updater.
    monkeypatch.setattr(install_python_runtime.ctypes.windll.shell32, "IsUserAnAdmin", lambda: True)
    assert install_python_runtime.repair_legacy_windows_permissions(str(old_install))
    assert not protected_dacl(old_install)
    assert retained.read_text(encoding="utf-8") == "existing runtime"
    assert json.loads(marker.read_text(encoding="utf-8"))["access_layout"] == 1
    stamp = marker.stat().st_mtime_ns
    assert not install_python_runtime.repair_legacy_windows_permissions(str(old_install))
    assert marker.stat().st_mtime_ns == stamp


def test_ordinary_launch_does_not_attempt_legacy_elevation(tmp_path, monkeypatch):
    write_install_marker(str(tmp_path))
    marker = Path(install_marker_path(str(tmp_path)))
    document = json.loads(marker.read_text(encoding="utf-8"))
    document.pop("access_layout")
    marker.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(install_python_runtime.ctypes.windll.shell32, "IsUserAnAdmin", lambda: False)
    assert not install_python_runtime.repair_legacy_windows_permissions(str(tmp_path))
    assert json.loads(marker.read_text(encoding="utf-8")) == document
