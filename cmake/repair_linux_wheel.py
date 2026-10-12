"""Bundle Linux wheel dependencies and apply the compatible PEP 600 tags."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def repair(wheel_dir: Path) -> Path:
    wheels = tuple(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"Expected one unaudited Linux wheel in {wheel_dir}, found {len(wheels)}"
        )
    staging = wheel_dir / "auditwheel"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "auditwheel",
            "repair",
            # SDL opens the system Vulkan loader by its normal soname. A
            # renamed, vendored loader would create a second instance registry
            # and make SDL reject surfaces created by the native engine.
            "--exclude",
            "libvulkan.so.1",
            "--wheel-dir",
            str(staging),
            str(wheels[0]),
        ],
        check=True,
    )
    repaired = tuple(staging.glob("*.whl"))
    if len(repaired) != 1:
        raise RuntimeError(
            f"Expected one audited Linux wheel in {staging}, found {len(repaired)}"
        )
    with zipfile.ZipFile(repaired[0]) as archive:
        if any(Path(name).name.startswith("libvulkan") and ".so" in Path(name).name
               for name in archive.namelist()):
            raise RuntimeError("Linux wheels must use the system Vulkan loader, not bundle another loader")
    destination = wheel_dir / repaired[0].name
    wheels[0].unlink()
    shutil.move(str(repaired[0]), destination)
    staging.rmdir()
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", required=True, type=Path)
    output = repair(parser.parse_args().wheel_dir.resolve())
    print(f"Audited Linux wheel: {output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
