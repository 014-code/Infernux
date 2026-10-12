"""Linux wheel publication must not introduce a second Vulkan loader."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("loader", [None, "libvulkan.so.1", "libvulkan-abcdef.so.1.3.204"])
def test_repaired_wheel_rejects_vendored_vulkan_before_replacing_source(tmp_path, monkeypatch, loader):
    spec = importlib.util.spec_from_file_location("repair_linux_wheel", ROOT / "cmake/repair_linux_wheel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = tmp_path / "infernux-linux_x86_64.whl"
    original.write_bytes(b"original unaudited wheel")
    output_name = "infernux-manylinux_2_35_x86_64.whl"

    def audit(command, *, check):
        assert check is True
        assert command[command.index("--exclude") + 1] == "libvulkan.so.1"
        output = Path(command[command.index("--wheel-dir") + 1]) / output_name
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("infernux/lib/libSDL3.so.0", b"SDL uses the system loader")
            if loader:
                archive.writestr(f"infernux.libs/{loader}", b"unwanted second Vulkan loader")

    monkeypatch.setattr(module.subprocess, "run", audit)
    if loader:
        with pytest.raises(RuntimeError, match="system Vulkan loader"):
            module.repair(tmp_path)
        assert original.read_bytes() == b"original unaudited wheel"
        assert not (tmp_path / output_name).exists()
    else:
        assert module.repair(tmp_path) == tmp_path / output_name
        assert not original.exists()
        assert not (tmp_path / "auditwheel").exists()
        with zipfile.ZipFile(tmp_path / output_name) as archive:
            assert archive.namelist() == ["infernux/lib/libSDL3.so.0"]
