def test_source_checkout_gpu_vendor_follows_the_loaded_native_directory(tmp_path, monkeypatch):
    from infernux import lib as engine_lib
    from infernux._compiler import taichi as compiler_loader

    native_dir = tmp_path / "Release"
    vendor = tmp_path / "gpu-jit-wheel/infernux/_compiler/taichi/_vendor/taichi"
    native_dir.mkdir()
    vendor.mkdir(parents=True)
    monkeypatch.delenv("INFERNUX_GPU_JIT_VENDOR_DIR", raising=False)
    monkeypatch.delenv("INFERNUX_NATIVE_MODULE_DIR", raising=False)
    monkeypatch.setattr(engine_lib, "native_dir", str(native_dir))
    monkeypatch.setattr(compiler_loader, "__file__", str(tmp_path / "source/infernux/_compiler/taichi/__init__.py"))

    assert compiler_loader._vendor_dir() == vendor
